"""One audio-only FFmpeg boundary for metadata, PCM, waveforms and export.

All work belongs on background workers. No Qt or audio devices are imported.
"""
from __future__ import annotations

from dataclasses import dataclass
from contextlib import contextmanager
import ctypes
import hashlib
import json
import math
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import uuid
import wave

import numpy as np

from .audio_processing import RATE, pcm16

CHUNK_FRAMES = RATE * 30
CACHE_LIMIT = 2 * 1024 ** 3
_spawn_lock = threading.RLock()


@contextmanager
def isolated_dll_search():
    """Do not let PyInstaller's process DLL directory leak into FFmpeg.

    Windows inherits SetDllDirectory state at CreateProcess, independently of
    PATH. Restore the exact application directory immediately after spawning.
    """
    if os.name != "nt":
        yield
        return
    with _spawn_lock:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        getter, setter = kernel.GetDllDirectoryW, kernel.SetDllDirectoryW
        getter.argtypes, getter.restype = [ctypes.c_uint32, ctypes.c_wchar_p], ctypes.c_uint32
        setter.argtypes, setter.restype = [ctypes.c_wchar_p], ctypes.c_int
        size = getter(0, None)
        previous = ctypes.create_unicode_buffer(size + 1)
        getter(len(previous), previous)
        if not setter(None):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            yield
        finally:
            if not setter(previous.value if size else None):
                raise ctypes.WinError(ctypes.get_last_error())


class PcmChunkCache:
    """Atomic 30 s PCM chunks with a bounded disk LRU; never used on callbacks."""

    def __init__(self, directory, limit=CACHE_LIMIT):
        self.directory = Path(directory)
        self.limit = max(0, int(limit))
        self._lock = threading.RLock()

    @staticmethod
    def identity(path, stream, decoder_version):
        path = Path(path).resolve(strict=True)
        stat = path.stat()
        return hashlib.sha256(json.dumps([str(path), stat.st_size, stat.st_mtime_ns,
                                          stream, decoder_version, RATE, 2]).encode()).hexdigest()

    def _path(self, identity, chunk):
        if len(identity) != 64 or any(c not in "0123456789abcdef" for c in identity) or chunk < 0:
            raise ValueError("Invalid chunk identity")
        return self.directory / f"{identity}-{chunk}.pcm"

    def load(self, identity, chunk):
        target = self._path(identity, chunk)
        with self._lock:
            try:
                size = target.stat().st_size
                if size == 0 or size % 8 or size > CHUNK_FRAMES * 8:
                    target.unlink(missing_ok=True)
                    return None
                data = np.fromfile(target, dtype="<f4").reshape(-1, 2)
                if not np.isfinite(data).all():
                    target.unlink(missing_ok=True)
                    return None
                os.utime(target, None)
                return data
            except FileNotFoundError:
                return None
            except OSError:
                # A cache is expendable; a read-only cache must not block playback.
                return None

    def store(self, identity, chunk, samples):
        samples = np.asarray(samples, dtype="<f4")
        if samples.shape != (len(samples), 2) or len(samples) > CHUNK_FRAMES or not len(samples):
            raise ValueError("Invalid PCM cache chunk")
        if samples.nbytes > self.limit:
            return
        target = self._path(identity, chunk)
        temporary = target.with_suffix("." + uuid.uuid4().hex + ".part")
        with self._lock:
            try:
                self.directory.mkdir(parents=True, exist_ok=True)
                with temporary.open("xb") as output:
                    output.write(samples.tobytes()); output.flush()
                    os.fsync(output.fileno())
                temporary.replace(target)
                self._prune()
            except OSError:
                pass
            finally:
                temporary.unlink(missing_ok=True)

    def _prune(self):
        files = []
        for path in self.directory.glob("*.pcm"):
            try:
                stat = path.stat()
                files.append((stat.st_mtime_ns, stat.st_size, path))
            except OSError:
                continue
        total = sum(size for _, size, _ in files)
        for _, size, path in sorted(files):
            if total <= self.limit:
                break
            try:
                path.unlink(); total -= size
            except OSError:
                continue


class MediaDecodeError(RuntimeError):
    pass


class DecodeCancelled(MediaDecodeError):
    pass


class _BackgroundPreempted(DecodeCancelled):
    pass


@dataclass(frozen=True)
class AudioTrack:
    index: int
    codec: str
    channels: int
    sample_rate: int
    language: str
    title: str
    is_default: bool
    duration: float


@dataclass(frozen=True)
class MediaInfo:
    duration: float
    sample_rate: int
    frames: int
    tracks: tuple[AudioTrack, ...]
    default_stream: int


def _number(value, default=0.0):
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (ValueError, TypeError):
        return default


class MediaDecoder:
    def __init__(self, cache_dir=None, tool_dir=None):
        self.cache_dir = Path(cache_dir) if cache_dir else Path(os.environ.get("LOCALAPPDATA", Path.home())) / "NiuLaiPlayerPython" / "cache" / "pcm"
        self.cache = PcmChunkCache(self.cache_dir)
        self._priority = threading.Condition()
        self._foreground = 0
        self._slots = threading.BoundedSemaphore(2)
        self._probes = {}
        self._probe_lock = threading.RLock()
        root = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[2]
        self.tool_dir = Path(tool_dir) if tool_dir else root / "vendor" / "ffmpeg" / "bin"

    def _command(self, executable, args):
        binary = self.tool_dir / (executable + (".exe" if os.name == "nt" else ""))
        if not binary.is_file():
            raise MediaDecodeError("媒体解码组件缺失，请使用包含 FFmpeg 的完整发布包")
        return [str(binary.resolve()), *map(str, args)]

    def _spawn(self, executable, args):
        # Keep the complete vendor bin together. Never add its DLLs to Qt's
        # directory or mutate the application's PATH/DLL search configuration.
        environment = os.environ.copy()
        environment["PATH"] = os.pathsep.join([str(self.tool_dir.resolve()),
                                               str(Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32")])
        with isolated_dll_search():
            return subprocess.Popen(self._command(executable, args), stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment,
                                    cwd=str(self.tool_dir.resolve()),
                                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)

    def probe(self, path):
        path = Path(path).resolve(strict=True)
        stat = path.stat()
        key = (str(path), stat.st_size, stat.st_mtime_ns)
        with self._probe_lock:
            if key in self._probes:
                return self._probes[key]
        process = self._spawn("ffprobe", ["-v", "error", "-select_streams", "a", "-show_streams",
                                          "-show_format", "-of", "json", str(path)])
        try:
            output, error = process.communicate(timeout=20)
        except subprocess.TimeoutExpired as exc:
            process.kill(); process.communicate()
            raise MediaDecodeError("媒体信息读取超时") from exc
        if process.returncode:
            raise MediaDecodeError("无法识别媒体：" + error.decode("utf-8", "replace")[-2000:])
        try:
            document = json.loads(output)
            overall = _number(document.get("format", {}).get("duration"))
            tracks = tuple(AudioTrack(int(row["index"]), row.get("codec_name", "unknown"),
                                      int(row.get("channels", 0)), int(row.get("sample_rate", 0)),
                                      row.get("tags", {}).get("language", ""), row.get("tags", {}).get("title", ""),
                                      bool(row.get("disposition", {}).get("default", 0)),
                                      _number(row.get("duration"), overall)) for row in document.get("streams", []))
        except (ValueError, TypeError, KeyError) as exc:
            raise MediaDecodeError("媒体信息不完整") from exc
        if not tracks:
            raise MediaDecodeError("此文件没有可播放的音轨")
        selected = next((track for track in tracks if track.is_default), tracks[0])
        if selected.duration <= 0:
            raise MediaDecodeError("无法确定音轨时长，请检查文件是否完整")
        result = MediaInfo(selected.duration, RATE, round(selected.duration * RATE), tracks, selected.index)
        with self._probe_lock:
            # A small metadata cache avoids ffprobe launches on every chunk.
            if len(self._probes) >= 256:
                self._probes.pop(next(iter(self._probes)))
            self._probes[key] = result
        return result

    def selected_track(self, path, stream_index=None):
        info = self.probe(path)
        stream_index = info.default_stream if stream_index is None else stream_index
        track = next((track for track in info.tracks if track.index == stream_index), None)
        if track is None:
            raise MediaDecodeError("选定音轨已不存在，请重新选择")
        return track

    def cache_identity(self, path, stream_index):
        binary = Path(self._command("ffmpeg", [])[0])
        stat = binary.stat()
        version = f"{binary.name}:{stat.st_size}:{stat.st_mtime_ns}:pcm-v2-timeline"
        return self.cache.identity(path, stream_index, version)

    @contextmanager
    def foreground(self):
        with self._priority:
            self._foreground += 1
            self._priority.notify_all()
        try:
            yield
        finally:
            with self._priority:
                self._foreground -= 1
                self._priority.notify_all()

    def _background_wait(self, cancelled):
        with self._priority:
            while self._foreground:
                if cancelled is not None and cancelled.is_set():
                    raise DecodeCancelled("媒体处理已取消")
                self._priority.wait(.05)

    def iter_pcm(self, path, *, stream_index=None, start=0.0, end=None, cancelled=None, block_frames=65536, background=False):
        if background:
            self._background_wait(cancelled)
        while not self._slots.acquire(timeout=.05):
            if cancelled is not None and cancelled.is_set():
                raise DecodeCancelled("媒体处理已取消")
        try:
            yield from self._iter_pcm(path, stream_index=stream_index, start=start, end=end,
                                      cancelled=cancelled, block_frames=block_frames, background=background)
        finally:
            self._slots.release()

    def _iter_pcm(self, path, *, stream_index=None, start=0.0, end=None, cancelled=None, block_frames=65536, background=False):
        if not math.isfinite(start) or start < 0 or (end is not None and (not math.isfinite(end) or end <= start)):
            raise ValueError("解码范围无效")
        if not 1 <= block_frames <= RATE * 2:
            raise ValueError("解码缓冲大小无效")
        path = Path(path).resolve(strict=True)
        info = self.probe(path)
        if stream_index is None:
            stream_index = info.default_stream
        if not isinstance(stream_index, int) or stream_index < 0:
            raise ValueError("音轨编号无效")
        track = next((track for track in info.tracks if track.index == stream_index), None)
        if track is None:
            raise MediaDecodeError("选定音轨已不存在，请重新选择")
        if cancelled is not None and cancelled.is_set():
            raise DecodeCancelled("媒体处理已取消")
        # Warm codec state and align the resampler's rational phase with a
        # decode from zero. Direct -ss at an arbitrary sample starts a fresh
        # filter phase and compressed formats may need decoder preroll.
        quantum = 1 / math.gcd(max(1, track.sample_rate), RATE)
        seek_base = math.floor(max(0, start - 1.0) / quantum) * quantum
        trim_start = round(start * RATE) - round(seek_base * RATE)
        filters = []
        if track.channels == 1:
            filters.append("pan=stereo|c0=c0|c1=c0")
        filters.append(f"aresample={RATE}:async=1:first_pts={round(seek_base * track.sample_rate)}")
        trim = f"atrim=start_sample={trim_start}"
        if end is not None:
            trim += f":end_sample={trim_start + round(end * RATE) - round(start * RATE)}"
        filters += [trim, "asetpts=PTS-STARTPTS"]
        args = ["-v", "error", "-nostdin", "-threads", "1", "-copyts", "-noaccurate_seek", "-ss", format(seek_base, ".9f"),
                "-i", str(path), "-map", f"0:{stream_index}", "-vn", "-sn", "-dn",
                "-filter_threads", "1", "-threads", "1", "-ar", str(RATE), "-ac", "2",
                "-af", ",".join(filters)]
        if end is not None:
            args += ["-t", format((round(end * RATE) - round(start * RATE)) / RATE, ".9f")]
        args += ["-c:a", "pcm_f32le", "-f", "f32le", "pipe:1"]
        process = self._spawn("ffmpeg", args)
        blocks = queue.Queue(maxsize=4)
        stop = threading.Event()
        errors = bytearray()

        def reader():
            try:
                while not stop.is_set():
                    block = process.stdout.read(block_frames * 8)
                    while not stop.is_set():
                        try:
                            blocks.put(block, timeout=.05)
                            break
                        except queue.Full:
                            pass
                    if not block:
                        break
            finally:
                process.stdout.close()

        def error_reader():
            try:
                while data := process.stderr.read(2048):
                    errors.extend(data)
                    if len(errors) > 8192:
                        del errors[:-8192]
            finally:
                process.stderr.close()

        threads = [threading.Thread(target=reader, daemon=True), threading.Thread(target=error_reader, daemon=True)]
        for thread in threads:
            thread.start()
        last_data = time.monotonic()
        try:
            while True:
                if cancelled is not None and cancelled.is_set():
                    raise DecodeCancelled("媒体处理已取消")
                if background:
                    with self._priority:
                        if self._foreground:
                            # Restart this bounded chunk after playback has
                            # obtained its first PCM. Incomplete cache is unused.
                            raise _BackgroundPreempted()
                try:
                    block = blocks.get(timeout=.05)
                except queue.Empty:
                    if time.monotonic() - last_data > 30:
                        raise MediaDecodeError("媒体解码超时")
                    continue
                last_data = time.monotonic()
                if not block:
                    break
                if len(block) % 8:
                    raise MediaDecodeError("解码器返回了不完整的音频帧")
                yield np.frombuffer(block, dtype="<f4").reshape(-1, 2).copy()
            process.wait(timeout=5)
            threads[1].join(timeout=1)
            if process.returncode:
                raise MediaDecodeError("媒体解码失败：" + errors.decode("utf-8", "replace")[-2000:])
        finally:
            stop.set()
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
            for thread in threads:
                thread.join(timeout=1)

    def waveform(self, path, bins=320, *, stream_index=None, cancelled=None):
        if not 1 <= bins <= 16384:
            raise ValueError("波形采样点数无效")
        track = self.selected_track(path, stream_index)
        total = max(1, round(track.duration * RATE))
        identity = self.cache_identity(path, track.index)
        peaks, offset = np.zeros(bins, np.float32), 0
        for chunk in range(math.ceil(total / CHUNK_FRAMES)):
            if cancelled is not None and cancelled.is_set():
                raise DecodeCancelled("媒体处理已取消")
            block = self.cache.load(identity, chunk)
            while block is None:
                try:
                    parts = list(self.iter_pcm(path, stream_index=track.index,
                                start=chunk * 30.0, end=min(track.duration, (chunk + 1) * 30.0),
                                cancelled=cancelled, background=True))
                    block = np.concatenate(parts) if parts else np.empty((0, 2), np.float32)
                    self.cache.store(identity, chunk, block)
                except _BackgroundPreempted:
                    self._background_wait(cancelled)
                    block = self.cache.load(identity, chunk)
            indices = np.minimum(bins - 1, (offset + np.arange(len(block))) * bins // total)
            amplitudes = np.nan_to_num(np.max(np.abs(block), axis=1), nan=0, posinf=1)
            np.maximum.at(peaks, indices, np.clip(amplitudes, 0, 1))
            offset += len(block)
        return peaks.tolist()

    def export_segment(self, path, destination, start, end, *, stream_index=None, cancelled=None):
        destination, source = Path(destination).resolve(), Path(path).resolve()
        if destination == source or destination.exists():
            raise FileExistsError("导出目标已存在，原文件保持不变")
        temporary = destination.with_name(destination.name + "." + uuid.uuid4().hex + ".part")
        frames = 0
        try:
            with wave.open(str(temporary), "wb") as output:
                output.setparams((2, 2, RATE, 0, "NONE", "not compressed"))
                for block in self.iter_pcm(source, stream_index=stream_index, start=start, end=end, cancelled=cancelled):
                    output.writeframes(pcm16(block)); frames += len(block)
            if frames == 0:
                raise MediaDecodeError("选定范围没有可导出的声音")
            with temporary.open("rb+") as completed:
                os.fsync(completed.fileno())
            # Hard-link publication fails rather than overwriting another file.
            os.link(temporary, destination)
            return str(destination)
        finally:
            temporary.unlink(missing_ok=True)


class _CombinedCancel:
    def __init__(self, *events):
        self.events = events

    def is_set(self):
        return any(event.is_set() for event in self.events)


class ProgressiveSamples:
    """Two-chunk in-memory playback window; take() never waits or touches disk.

    Missing PCM returns a short read. The cursor keeps its position while the
    microphone/output workers continue. A seek invalidates the decoder epoch,
    kills its subprocess, and retains only already complete useful chunks.
    """

    def __init__(self, decoder, path, stream_index, cancelled, start=0.0):
        self.decoder, self.path = decoder, str(path)
        self.track = decoder.selected_track(path, stream_index)
        self.identity = decoder.cache_identity(path, self.track.index)
        self.frames = round(self.track.duration * RATE)
        self.cancelled = cancelled
        self._closed = threading.Event()
        self._epoch_cancel = threading.Event()
        self._condition = threading.Condition()
        self._chunks = {}
        self._position = max(0, min(round(start * RATE), self.frames - 1))
        self._epoch = 0
        self.error = None
        self.first_ready = threading.Event()
        self._thread = threading.Thread(target=self._produce, name="newlife-decode", daemon=True)
        self._thread.start()

    def __len__(self):
        return self.frames

    @property
    def shape(self):
        return (self.frames, 2)

    @property
    def buffering(self):
        with self._condition:
            chunk, offset = divmod(self._position, CHUNK_FRAMES)
            state = self._chunks.get(chunk)
            return self._position < self.frames and (state is None or offset >= state[1])

    def seek(self, frame):
        with self._condition:
            self._position = max(0, min(int(frame), self.frames - 1))
            self._epoch += 1
            self._epoch_cancel.set()
            self._epoch_cancel = threading.Event()
            # Incomplete chunks are invalidated; no old packet can reappear.
            self._chunks = {key: state for key, state in self._chunks.items()
                            if state[2] and abs(key - self._position // CHUNK_FRAMES) <= 1}
            self._condition.notify_all()

    def take(self, frame, count):
        parts = []
        with self._condition:
            self._position = frame
            while count > 0 and frame < self.frames:
                chunk, offset = divmod(frame, CHUNK_FRAMES)
                state = self._chunks.get(chunk)
                if state is None or offset >= state[1]:
                    break
                n = min(count, state[1] - offset)
                parts.append(state[0][offset:offset + n].copy())
                frame += n; count -= n
            self._position = frame
            current = frame // CHUNK_FRAMES
            self._chunks = {key: state for key, state in self._chunks.items() if current <= key <= current + 1}
            self._condition.notify_all()
        return np.concatenate(parts) if parts else np.empty((0, 2), np.float32)

    def close(self):
        self._closed.set()
        with self._condition:
            self._epoch_cancel.set()
            self._chunks.clear()
            self._condition.notify_all()

    def wait_closed(self, timeout=5):
        """Join only from shutdown/decode workers, never an audio callback."""
        self._thread.join(timeout)
        return not self._thread.is_alive()

    def _produce(self):
        try:
            while not self._closed.is_set() and not self.cancelled.is_set():
                with self._condition:
                    current = self._position // CHUNK_FRAMES
                    chunk = next((key for key in (current, current + 1)
                                  if key * CHUNK_FRAMES < self.frames and
                                  (key not in self._chunks or not self._chunks[key][2])), None)
                    if chunk is None:
                        self._condition.wait(.05)
                        continue
                    epoch, epoch_cancel = self._epoch, self._epoch_cancel
                cancel = _CombinedCancel(self._closed, self.cancelled, epoch_cancel)
                cached = self.decoder.cache.load(self.identity, chunk)
                if cached is not None:
                    self._publish(chunk, cached, len(cached), True, epoch)
                    continue
                capacity = min(CHUNK_FRAMES, self.frames - chunk * CHUNK_FRAMES)
                samples = np.empty((capacity, 2), np.float32)
                received = 0
                try:
                    with self.decoder.foreground():
                        for block in self.decoder.iter_pcm(self.path, stream_index=self.track.index,
                                    start=chunk * 30.0, end=min(self.track.duration, (chunk + 1) * 30.0),
                                    cancelled=cancel, block_frames=RATE // 5):
                            n = min(len(block), capacity - received)
                            if n:
                                samples[received:received + n] = block[:n]
                                received += n
                                self._publish(chunk, samples, received, False, epoch)
                    if cancel.is_set():
                        continue
                    if not received:
                        raise MediaDecodeError("选定音轨没有可播放内容")
                    self.decoder.cache.store(self.identity, chunk, samples[:received])
                    self._publish(chunk, samples[:received], received, True, epoch)
                except DecodeCancelled:
                    continue
        except Exception as error:
            if not self._closed.is_set() and not self.cancelled.is_set():
                self.error = error
        finally:
            self.first_ready.set()

    def _publish(self, chunk, samples, ready, complete, epoch):
        with self._condition:
            if epoch != self._epoch or self._closed.is_set():
                return
            self._chunks[chunk] = [samples, ready, complete]
            if complete and ready < min(CHUNK_FRAMES, self.frames - chunk * CHUNK_FRAMES):
                if (chunk + 1) * CHUNK_FRAMES < self.frames:
                    raise MediaDecodeError("媒体中间段不完整，请检查原文件")
                # Container durations can include codec padding. Natural end
                # follows decoded audio, rather than waiting forever at EOF.
                self.frames = chunk * CHUNK_FRAMES + ready
            if chunk * CHUNK_FRAMES <= self._position < chunk * CHUNK_FRAMES + ready:
                self.first_ready.set()
            self._condition.notify_all()


_default = None


def default_decoder():
    global _default
    if _default is None:
        _default = MediaDecoder()
    return _default
