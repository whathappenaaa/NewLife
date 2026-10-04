"""Windows audio I/O. Device callbacks never decode files, write files or call Qt."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import ctypes
import logging
import os
from pathlib import Path
import queue
import re
import struct
import tempfile
import threading
import time
import uuid
import wave

import numpy as np
import soundfile as sf

from .models import DeviceInfo
from .media_decode import DecodeCancelled, ProgressiveSamples, default_decoder
from .audio_processing import RATE, AudioTimeline, CaptureConverter, CaptureTimestampClock, ClipCursor, RoutedBuffer, SmoothLimiter, StreamingResampler, pcm16, stereo

log = logging.getLogger(__name__)
BLOCK = 480
SYNC_DELAY = .15
MAX_WAV_FRAMES = (2**32 - 16 * 1024 * 1024) // 4


def _hidden(path: Path, value: bool) -> None:
    if os.name == "nt":
        attributes = ctypes.windll.kernel32.GetFileAttributesW(str(path))
        if attributes != -1:
            ctypes.windll.kernel32.SetFileAttributesW(str(path), attributes | 2 if value else attributes & ~2)


def _new_path(folder: Path, prefix: str) -> Path:
    return folder / f"{prefix}_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}.wav"


class WavRecording:
    """Recoverable standard PCM16 WAV, with frame-addressable concurrent exports."""

    def __init__(self, folder: str):
        self.folder = Path(folder).resolve()
        self.folder.mkdir(parents=True, exist_ok=True)
        self.path = self.folder / f".niulai-{uuid.uuid4().hex}.wav.part"
        self._file = self.path.open("xb")
        self._wave = wave.open(self._file, "wb")
        self._wave.setparams((2, 2, RATE, 0, "NONE", "not compressed"))
        self._wave.writeframes(b"")
        self._file.flush()
        _hidden(self.path, True)
        self.frames = 0
        self.closed = False
        self._lock = threading.RLock()

    def write(self, samples: np.ndarray) -> None:
        with self._lock:
            if self.closed:
                raise RuntimeError("Recording is already closed")
            n = min(len(samples), MAX_WAV_FRAMES - self.frames)
            if n:
                # writeframes patches RIFF/data lengths, unlike writeframesraw.
                self._wave.writeframes(pcm16(samples[:n]))
                self.frames += n

    def flush(self) -> int:
        with self._lock:
            if not self.closed:
                self._file.flush()
            return self.frames

    def finish(self) -> str:
        with self._lock:
            if self.closed:
                return str(self.path)
            try:
                self._wave.close()
                self._file.flush()
                os.fsync(self._file.fileno())
            finally:
                self._file.close()
                self.closed = True
            destination = _new_path(self.folder, "录音")
            self.path.replace(destination)
            self.path = destination
            _hidden(destination, False)
            return str(destination)

    def export(self, start: int, end: int) -> str:
        if start < 0 or end > self.frames or end - start < RATE:
            raise ValueError("标记片段必须至少 1 秒，且在已经录制的范围内")
        self.flush()
        destination = _new_path(self.folder, "片段")
        temporary = destination.with_suffix(".wav.part")
        try:
            with wave.open(str(temporary), "wb") as output:
                output.setparams((2, 2, RATE, 0, "NONE", "not compressed"))
                position = start
                while position < end:
                    n = min(65_536, end - position)
                    # Keep locks short. finish() may rename the source between chunks.
                    with self._lock:
                        with self.path.open("rb") as source:
                            source.seek(44 + position * 4)
                            chunk = source.read(n * 4)
                    if len(chunk) != n * 4:
                        raise IOError("录音数据不完整，原始文件仍然保留")
                    output.writeframes(chunk)
                    position += n
            temporary.replace(destination)
            return str(destination)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise


def recover_recording_parts(folder: str) -> list[str]:
    """Only recover files bearing this engine's exact private recording name."""
    recovered: list[str] = []
    directory = Path(folder)
    if not directory.is_dir():
        return recovered
    for path in directory.glob(".niulai-*.wav.part"):
        if not re.fullmatch(r"\.niulai-[a-f0-9]{32}\.wav\.part", path.name):
            continue
        with path.open("r+b") as file:
            header = file.read(44)
            if (len(header) != 44 or header[:4] != b"RIFF" or header[8:16] != b"WAVEfmt "
                    or header[36:40] != b"data" or struct.unpack_from("<HHI", header, 20) != (1, 2, RATE)
                    or struct.unpack_from("<HH", header, 32) != (4, 16)):
                continue
            size = min((os.fstat(file.fileno()).st_size - 44) // 4, MAX_WAV_FRAMES) * 4
            file.truncate(44 + size)
            file.seek(4); file.write(struct.pack("<I", size + 36))
            file.seek(40); file.write(struct.pack("<I", size))
            file.flush(); os.fsync(file.fileno())
        destination = _new_path(directory, "恢复录音")
        path.replace(destination)
        _hidden(destination, False)
        recovered.append(str(destination))
    return recovered


class _Decoded:
    def __init__(self, file, frames: int):
        self.file = file
        self.samples = np.memmap(file, dtype=np.float32, mode="r", shape=(frames, 2))

    def close(self):
        self.samples._mmap.close()
        self.file.close()


class _ProgressiveDecoded:
    def __init__(self, samples):
        self.samples = samples

    @property
    def error(self):
        return self.samples.error

    def close(self):
        self.samples.close()

    def wait_closed(self):
        return self.samples.wait_closed()


def _decode(path: str, cancelled: threading.Event, stream_index=None, *, start=None) -> _Decoded | None:
    if start is not None:
        samples = ProgressiveSamples(default_decoder(), path, stream_index, cancelled, start)
        while not samples.first_ready.wait(.05):
            if cancelled.is_set():
                samples.close()
                samples.wait_closed()
                return None
        if cancelled.is_set():
            samples.close()
            samples.wait_closed()
            return None
        if samples.error:
            samples.close()
            samples.wait_closed()
            raise samples.error
        return _ProgressiveDecoded(samples)
    file = tempfile.TemporaryFile()
    try:
        frames = 0
        for converted in default_decoder().iter_pcm(path, stream_index=stream_index, cancelled=cancelled):
            file.write(converted.tobytes())
            frames += len(converted)
        if frames == 0:
            raise ValueError("音频文件没有可播放内容")
        file.flush()
        return _Decoded(file, frames)
    except DecodeCancelled:
        file.close()
        return None
    except Exception:
        file.close()
        raise


class _PortAudioBackend:
    def __init__(self):
        import pyaudiowpatch as pa
        self.module = pa
        self._pa = pa.PyAudio()
        self._lock = threading.RLock()
        self._known: dict[str, tuple[DeviceInfo, int]] = {}
        self._closed = False
        self._streams = 0
        self._last_enumeration = time.monotonic()

    def devices(self) -> list[DeviceInfo]:
        with self._lock:
            if self._closed:
                return []
            # PortAudio's WASAPI endpoint table is a snapshot taken at initialize.
            # Rebuild only with no open streams; active devices must never be reset.
            if self._streams == 0 and time.monotonic() - self._last_enumeration >= 1:
                self._pa.terminate()
                self._pa = self.module.PyAudio()
                self._last_enumeration = time.monotonic()
            pa = self.module
            host = self._pa.get_host_api_info_by_type(pa.paWASAPI)
            default_in = int(host.get("defaultInputDevice", -1))
            default_out = int(host.get("defaultOutputDevice", -1))
            default_output_name = ""
            if default_out >= 0:
                default_output_name = self._pa.get_device_info_by_index(default_out)["name"]
            result = []
            known = {}
            for index in range(self._pa.get_device_count()):
                raw = self._pa.get_device_info_by_index(index)
                if int(raw["hostApi"]) != int(host["index"]):
                    continue
                loopback = bool(raw.get("isLoopbackDevice", False))
                kind = "loopback" if loopback else "input" if raw["maxInputChannels"] > 0 else "output"
                channels = int(raw["maxInputChannels"] if kind != "output" else raw["maxOutputChannels"])
                if channels <= 0:
                    continue
                name = str(raw["name"])
                # Include index and exact name. A reordered/replaced endpoint is not silently reused.
                identity = f"wasapi:{kind}:{index}:{name}"
                default = index == (default_out if kind == "output" else default_in)
                if loopback:
                    default = bool(default_output_name and name.startswith(default_output_name))
                device = DeviceInfo(identity, name, kind, float(raw["defaultSampleRate"]), channels,
                                    default, "cable" in name.lower() or "vb-audio" in name.lower())
                result.append(device)
                known[identity] = (device, index)
            self._known = known
            return result

    def open(self, device: DeviceInfo, callback, output: bool = False):
        with self._lock:
            if device.id not in self._known:
                self.devices()
            entry = self._known.get(device.id)
            if not entry:
                raise ValueError("所选音频设备不存在，请重新选择")
            index = entry[1]
            actual = self._pa.get_device_info_by_index(index)
            if actual["name"] != device.name:
                raise ValueError("音频设备已改变，请重新选择")
            channels = device.channels
            last_error = None
            for sample_format in (self.module.paFloat32, self.module.paInt16):
                is_pcm = sample_format == self.module.paInt16

                def native_callback(data, frames, timing, flags, use_pcm=is_pcm):
                    if not output:
                        if use_pcm:
                            data = (np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768).tobytes()
                        return callback(data, frames, timing, flags)
                    result, flag = callback(data, frames, timing, flags)
                    samples = np.frombuffer(result, dtype=np.float32).reshape(frames, min(2, channels))
                    if channels > 2:
                        expanded = np.zeros((frames, channels), np.float32)
                        expanded[:, :2] = samples
                        samples = expanded
                    return (pcm16(samples) if use_pcm else samples.tobytes(), flag)

                kwargs = {"output_device": index, "output_channels": channels, "output_format": sample_format} if output else {
                    "input_device": index, "input_channels": channels, "input_format": sample_format}
                try:
                    self._pa.is_format_supported(device.rate, **kwargs)
                    stream = self._pa.open(format=sample_format, channels=channels, rate=round(device.rate),
                                           input=not output, output=output, input_device_index=None if output else index,
                                           output_device_index=index if output else None,
                                           frames_per_buffer=max(64, round(device.rate / 100)), stream_callback=native_callback, start=False)
                    self._streams += 1
                    return _TrackedStream(self, stream)
                except (ValueError, OSError) as error:
                    last_error = error
            raise RuntimeError(f"设备无法按其原生格式打开：{device.name}；{last_error}")

    def close(self):
        with self._lock:
            if not self._closed:
                self._closed = True
                self._pa.terminate()


class _TrackedStream:
    def __init__(self, backend, stream):
        self.backend, self.stream = backend, stream
        self.closed = False

    def __getattr__(self, name):
        return getattr(self.stream, name)

    def close(self):
        with self.backend._lock:
            if not self.closed:
                self.closed = True
                try:
                    self.stream.close()
                finally:
                    self.backend._streams -= 1


class _Capture:
    def __init__(self, backend, device: DeviceInfo, clock):
        self.device = device
        self.converter = CaptureConverter(device.rate)
        self.packets: queue.Queue = queue.Queue(maxsize=256)
        self.clock = clock
        self.offset = None
        self.overflow = False
        self.error = None
        self.peak = 0.0
        self.timestamp_clock = CaptureTimestampClock(device.rate)
        self.dropped_frames = self.overflow_count = 0
        self.stream = backend.open(device, self.callback)
        try:
            # Every PortAudio stream has its own time base; never equate it to Python's clock.
            before = clock()
            native_now = self.stream.get_time()
            after = clock()
            if native_now > 0:
                self.offset = (before + after) / 2 - native_now
            self.stream.start_stream()
            if self.offset is None:
                before = clock()
                native_now = self.stream.get_time()
                after = clock()
                if native_now > 0:
                    self.offset = (before + after) / 2 - native_now
        except Exception:
            self.stream.close()
            raise

    def callback(self, data, frames, timing, flags):
        try:
            if flags & 2:  # paInputOverflow
                self.overflow = True
                self.overflow_count += 1
            adc = timing.get("input_buffer_adc_time", 0)
            mapped_adc = adc + self.offset if adc > 0 and self.offset is not None else None
            stamp = self.timestamp_clock.stamp(frames, self.clock(), mapped_adc)
            samples = np.frombuffer(data, dtype=np.float32).reshape(-1, self.device.channels).copy()
            try:
                self.packets.put_nowait((samples, stamp))
            except queue.Full:
                self.overflow = True
                self.dropped_frames += frames
        except Exception as error:
            self.error = error
        return (None, 0)  # paContinue

    def drain(self):
        while True:
            try:
                data, timestamp = self.packets.get_nowait()
            except queue.Empty:
                break
            self.peak = float(np.clip(np.max(np.nan_to_num(np.abs(data), nan=0.0, posinf=0.0), initial=0), 0, 1))
            self.converter.feed(data, timestamp)

    def close(self):
        try:
            self.stream.stop_stream()
        except Exception:
            pass
        finally:
            try:
                self.stream.close()
            except Exception:
                log.exception("Closing capture device")


class _Output:
    def __init__(self, backend, device: DeviceInfo):
        self.device = device
        self.channels = min(device.channels, 2)
        self.buffer = RoutedBuffer(round(device.rate * .25), self.channels)
        self.mic_resampler = StreamingResampler(RATE, device.rate, self.channels)
        self.clip_resampler = StreamingResampler(RATE, device.rate, self.channels)
        self.limiter = SmoothLimiter()
        self.mic_gain, self.clip_gain = 0.0, 1.0
        self.peak = 0.0
        self.underflow_count = 0
        self.buffer.levels(1, 1)
        self.stream = backend.open(device, self.callback, output=True)
        try:
            self.stream.start_stream()
        except Exception:
            self.stream.close()
            raise

    def callback(self, data, frames, timing, flags):
        if flags & 4:  # paOutputUnderflow
            self.underflow_count += 1
        return (self.buffer.read(frames).tobytes(), 0)

    def levels(self, mic, clip):
        self.mic_gain, self.clip_gain = mic, clip

    def push(self, mic: np.ndarray, clip: np.ndarray, clip_gain_override=None):
        mic = mic * self.mic_gain
        clip = clip * (self.clip_gain if clip_gain_override is None else clip_gain_override)
        gain = self.limiter.gains(mic + clip)
        mic, clip = mic * gain, clip * gain
        self.peak = float(np.max(np.abs(mic + clip), initial=0))
        if self.channels == 1:
            mic, clip = mic.mean(axis=1, keepdims=True), clip.mean(axis=1, keepdims=True)
        # Bound independent output-clock drift; native buffers keep at most 250 ms.
        error = self.buffer.frames / self.device.rate - .03
        ppm = float(np.clip(error * 10_000, -1500, 1500))
        self.mic_resampler.correct(ppm)
        self.clip_resampler.correct(ppm)
        mic = self.mic_resampler.process(mic)
        clip = self.clip_resampler.process(clip)
        self.buffer.add(mic, clip)

    def clear_clip(self):
        self.buffer.clear_clip()
        self.clip_resampler = StreamingResampler(RATE, self.device.rate, self.channels)

    @property
    def delay(self):
        try:
            hardware_latency = self.stream.get_output_latency()
        except (AttributeError, OSError):
            hardware_latency = .06
        return (self.buffer.frames + self.clip_resampler.delay) / self.device.rate + max(.02, hardware_latency)

    def close(self):
        try:
            self.stream.stop_stream()
        except Exception:
            pass
        finally:
            try:
                self.stream.close()
            except Exception:
                log.exception("Closing output device")


class AudioEngine:
    """Commands are queued; failures and completion are delivered through on_event.

    on_event is called on a background thread. The UI must dispatch it to Qt.
    The optional backend and clock arguments support device-free integration tests.
    """

    def __init__(self, on_event, *, backend=None, clock=time.monotonic):
        self._on_event = on_event
        self._backend = backend
        self._backend_lock = threading.RLock()
        self._clock = clock
        self._commands: queue.Queue = queue.Queue()
        self._lock = threading.RLock()
        self._request_lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="niulai-media")
        self._cancel = threading.Event()
        self._request = 0
        self._active_request = 0
        self._play_loading = False
        self._requested_range = None
        self._requested_seek = None
        self._closed = False
        self._shutdown_errors = []
        self._retired_samples = []
        self._quit = threading.Event()
        self._mic = self._system = self._local = self._call = None
        self._local_id = ""
        self._asset = self._cursor = None
        self._path = ""
        self._token = 0
        self._last_position = 0.0
        self._send = False
        self._volumes = (.8, .8, 1.0)
        self._record = None
        self._record_source = ""
        self._record_start = 0
        self._record_stop = None
        self._mark = None
        self._last_record_seconds = 0.0
        self._record_limiter = SmoothLimiter()
        self._test_position = None
        self._test_end = 0
        self._test_local = False
        self._end_deadline = None
        self._clock_frame = round(clock() * RATE)
        self._last_flush = self._last_check = clock()
        self._published_state = self._capture_state()
        self._thread = threading.Thread(target=self._run, name="niulai-audio", daemon=True)
        self._thread.start()

    def _backend_instance(self):
        with self._backend_lock:
            if self._closed:
                raise RuntimeError("音频引擎已关闭")
            if self._backend is None:
                self._backend = _PortAudioBackend()
            return self._backend

    def devices(self) -> list[DeviceInfo]:
        try:
            return self._backend_instance().devices()
        except Exception as error:
            self._error(error)
            return []

    def _device(self, identity: str, kind: str) -> DeviceInfo:
        if not identity:
            raise ValueError("请先选择" + {"input": "真实麦克风", "output": "输出设备", "loopback": "电脑声音设备"}[kind])
        device = next((d for d in self._backend_instance().devices() if d.id == identity and d.kind == kind), None)
        if device is None:
            raise ValueError("所选音频设备已断开或改变，请重新选择")
        return device

    def _post(self, function, *args):
        if not self._closed:
            self._commands.put((function, args))
            return True
        return False

    def _emit(self, name: str, **payload):
        try:
            self._on_event(name, payload)
        except Exception:
            log.exception("Audio event consumer failed")

    def _error(self, error):
        if self._closed:
            self._shutdown_errors.append(str(error))
        detail = (type(error), error, error.__traceback__) if isinstance(error, BaseException) else None
        log.warning("Audio error: %s", error, exc_info=detail)
        self._emit("error", message=str(error))

    def set_local_device(self, device_id: str):
        self._post(self._set_local, device_id)

    def _set_local(self, device_id):
        device = self._device(device_id, "output")
        if device.is_virtual:
            raise ValueError("本地监听请选择耳机或扬声器，不能选择虚拟通话线")
        if device_id == self._local_id:
            return
        if self._local:
            self._local.close(); self._local = None
        self._local_id = device_id
        if self._cursor:
            self._ensure_local()

    def _ensure_local(self):
        if self._local is None:
            device = self._device(self._local_id, "output")
            if device.is_virtual:
                raise ValueError("不能用虚拟通话线作为本地监听")
            self._local = _Output(self._backend_instance(), device)
            self._levels()

    def play(self, path: str, start: float, end: float, send: bool = False, token: int = 0, stream_index=None):
        with self._request_lock:
            if self._closed:
                return
            self._cancel.set()
            self._cancel = threading.Event()
            cancel = self._cancel
            self._request += 1
            request = self._request
        path = os.path.abspath(path)
        self._post(self._begin_play, request, path, start, end, send)

        def decode_job():
            try:
                asset = _decode(path, cancel, stream_index, start=start)
                if asset is not None:
                    with self._request_lock:
                        if cancel.is_set() or not self._post(self._accept_play, asset, request, path, start, end, send, token):
                            asset.close()
                            if hasattr(asset, "wait_closed"):
                                asset.wait_closed()
            except Exception as error:
                if not cancel.is_set():
                    self._post(self._decode_failed, request, error)
        try:
            self._executor.submit(decode_job)
        except RuntimeError:
            if not self._closed:
                raise

    def _begin_play(self, request, path, start, end, send):
        if request == self._request:
            self._stop_playback()
            self._play_loading = True
            self._requested_range = (start, end)
            self._path = path
            self._send = bool(send)
            self._levels()

    def _decode_failed(self, request, error):
        if request == self._request:
            self._play_loading = False
            self._error(error)

    def _accept_play(self, asset, request, path, start, end, send, token):
        if request != self._request or self._closed:
            asset.close()
            if hasattr(asset, "wait_closed"):
                self._retired_samples.append(asset.samples)
            return
        try:
            self._play_loading = False
            if self._requested_range is not None:
                start, end = self._requested_range
            if not np.isfinite(start) or not np.isfinite(end) or end <= start:
                raise ValueError("播放范围无效")
            self._ensure_local()
            pending_seek = self._requested_seek
            self._stop_playback()
            self._asset = asset
            self._cursor = ClipCursor(asset.samples, round(start * RATE), round(end * RATE))
            self._path, self._token = path, token
            if pending_seek is not None:
                position, seek_token = pending_seek
                self._cursor.position = max(self._cursor.start, min(round(position * RATE), self._cursor.end - 1))
                if hasattr(asset.samples, "seek"):
                    asset.samples.seek(self._cursor.position)
                if seek_token is not None:
                    self._token = seek_token
            self._active_request = request
            self._last_position = self._cursor.position / RATE
            self._end_deadline = None
            self._levels()
            self._emit("playback_started", token=self._token, path=path)
        except Exception:
            asset.close()
            raise

    def stop(self):
        with self._request_lock:
            self._request += 1
            self._cancel.set()
        self._post(self._stop_playback, True)

    def _stop_playback(self, release_local=False):
        self._play_loading = False
        self._requested_range = None
        self._requested_seek = None
        self._test_position = None
        self._cursor = None
        self._end_deadline = None
        if self._asset:
            if hasattr(self._asset, "wait_closed"):
                self._retired_samples = [samples for samples in self._retired_samples if samples._thread.is_alive()]
                self._retired_samples.append(self._asset.samples)
            self._asset.close(); self._asset = None
        for output in (self._local, self._call):
            if output:
                output.clear_clip()
        if release_local and self._local:
            self._local.close()
            self._local = None

    def pause(self):
        self._post(self._pause)

    def seek(self, seconds, token=None):
        self._post(self._seek, float(seconds), token)

    def _seek(self, seconds, token):
        if not np.isfinite(seconds):
            raise ValueError("跳转位置无效")
        if self._play_loading:
            # Only the latest position/token is applied after first PCM arrives.
            self._requested_seek = (seconds, token)
            return
        if self._cursor is None:
            return
        with self._request_lock:
            self._request += 1
            self._active_request = self._request
        self._cursor.position = max(self._cursor.start, min(round(seconds * RATE), self._cursor.end - 1))
        if hasattr(self._asset.samples, "seek"):
            self._asset.samples.seek(self._cursor.position)
        if token is not None:
            self._token = token
        self._last_position = self._cursor.position / RATE
        self._end_deadline = None
        for output in (self._local, self._call):
            if output:
                output.clear_clip()

    def _pause(self):
        if self._cursor:
            self._cursor.paused = not self._cursor.paused
            self._end_deadline = None
            for output in (self._local, self._call):
                if output:
                    output.clear_clip()

    def set_range(self, start: float, end: float):
        self._post(self._set_range, start, end)

    def _set_range(self, start, end):
        if not np.isfinite(start) or not np.isfinite(end) or end <= start:
            raise ValueError("播放范围无效")
        if self._play_loading:
            self._requested_range = (start, end)
        if self._cursor:
            jumped = self._cursor.set_range(round(start * RATE), round(end * RATE))
            if jumped and hasattr(self._asset.samples, "seek"):
                self._asset.samples.seek(self._cursor.position)
            self._end_deadline = None
            for output in (self._local, self._call):
                if output:
                    output.clear_clip()

    def set_send(self, enabled: bool):
        self._post(self._set_send, bool(enabled))

    def _set_send(self, enabled):
        if enabled != self._send and self._call:
            self._call.clear_clip()
        self._send = enabled
        self._levels()

    def set_volumes(self, local: float, send: float, mic: float):
        self._post(self._set_volumes, local, send, mic)

    def _set_volumes(self, local, send, mic):
        self._volumes = tuple(float(np.clip(v, 0, 1)) if np.isfinite(v) else 0.0 for v in (local, send, mic))
        self._levels()

    def _levels(self):
        local, send, mic = self._volumes
        if self._local:
            self._local.levels(0, local)
        if self._call:
            self._call.levels(mic, send if self._send else 0)

    def _ensure_mic(self, mic_id):
        if self._mic:
            if self._mic.device.id != mic_id:
                raise ValueError("录音与通话必须使用同一个真实麦克风；请先结束当前使用再更换")
            return
        device = self._device(mic_id, "input")
        if device.is_virtual:
            raise ValueError("真实麦克风不能选择虚拟线，否则会形成声音回路")
        self._mic = _Capture(self._backend_instance(), device, self._clock)

    def follow_devices(self, local_id, mic_id, system_id):
        self._post(self._follow_devices, local_id, mic_id, system_id)

    def _follow_devices(self, local_id, mic_id, system_id):
        if local_id:
            self._set_local(local_id)
        for name, identity, kind in (("_mic", mic_id, "input"), ("_system", system_id, "loopback")):
            current = getattr(self, name)
            if current is None or not identity or current.device.id == identity:
                continue
            device = self._device(identity, kind)
            if device.is_virtual:
                raise ValueError("本人麦克风不能使用 CABLE Output，请选择真实麦克风")
            replacement = _Capture(self._backend_instance(), device, self._clock)
            current.close()
            setattr(self, name, replacement)

    def _release_mic(self):
        if self._mic and not self._call and not (self._record and self._record_source in ("mic", "both")):
            self._mic.close(); self._mic = None

    def connect_call(self, mic_id: str, output_id: str):
        self._post(self._connect_call, mic_id, output_id)

    def _connect_call(self, mic_id, output_id):
        if self._call:
            if self._call.device.id == output_id and self._mic and self._mic.device.id == mic_id:
                return
            raise ValueError("请先断开通话，再更换通话设备")
        output = self._device(output_id, "output")
        if not output.is_virtual:
            raise ValueError("通话输出必须选择虚拟音频线 CABLE Input，不会回退到普通扬声器")
        try:
            self._ensure_mic(mic_id)
            self._call = _Output(self._backend_instance(), output)
            self._levels()
            self._emit("call_connected", output_id=output.id, output_name=output.name)
        except Exception:
            self._release_mic()
            raise

    def disconnect_call(self):
        self._post(self._disconnect_call)

    def _disconnect_call(self):
        self._test_position = None
        if self._call:
            self._call.close(); self._call = None
            self._emit("call_disconnected")
        self._release_mic()

    def start_recording(self, folder: str, source: str, mic_id: str = "", system_id: str = ""):
        self._post(self._start_recording, folder, source, mic_id, system_id)

    def _start_recording(self, folder, source, mic_id, system_id):
        source = {"电脑": "system", "系统": "system", "麦克风": "mic", "microphone": "mic", "都录制": "both", "系统＋麦克风": "both"}.get(source, source)
        if source not in ("system", "mic", "both"):
            raise ValueError("未知录音来源")
        if self._record:
            raise ValueError("已经在录音")
        try:
            if source in ("mic", "both"):
                self._ensure_mic(mic_id)
            if source in ("system", "both"):
                device = self._device(system_id, "loopback")
                if device.is_virtual:
                    raise ValueError("电脑声音请选择真实输出设备的 loopback，不能选择通话线")
                self._system = _Capture(self._backend_instance(), device, self._clock)
            self._record = WavRecording(folder)
            self._record_source = source
            self._record_start = round(self._clock() * RATE)
            self._record_stop = self._mark = None
            self._last_record_seconds = 0.0
            self._record_limiter = SmoothLimiter()
        except Exception:
            if self._system:
                self._system.close(); self._system = None
            self._release_mic()
            raise

    def stop_recording(self):
        self._post(self._request_recording_stop)

    def _request_recording_stop(self):
        if self._record and self._record_stop is None:
            self._record_stop = self._clock()

    def mark_recording(self):
        self._post(self._mark_recording)

    def _mark_recording(self):
        if self._record is None:
            raise ValueError("开始录音后才能标记片段")
        frames = self._record.flush()
        if self._mark is None:
            self._mark = frames
        elif frames - self._mark < RATE:
            raise ValueError("片段不足 1 秒，起点仍保留，请稍后再标记终点")
        else:
            self._export(self._record, self._mark, frames)
            self._mark = None

    def _export(self, recording, start, end):
        def export_job():
            try:
                self._emit("clip_saved", path=recording.export(start, end))
            except Exception as error:
                self._error(error)
        self._executor.submit(export_job)

    def _finish_recording(self):
        recording = self._record
        if recording is None:
            return
        try:
            # Normal stop waits for input delivery first. Device loss/close still
            # saves every already delivered packet, including the latency tail.
            try:
                for capture in (self._mic, self._system):
                    if capture:
                        capture.drain()
                stop = self._record_stop if self._record_stop is not None else self._clock()
                self._write_recording_until(round(stop * RATE))
            except Exception as error:
                self._error(f"部分录音无法继续写入，正在保留已有内容：{error}")
            path = recording.finish()
            self._last_record_seconds = recording.frames / RATE
            self._emit("recording_saved", path=path)
            if self._mark is not None and recording.frames - self._mark >= RATE:
                self._export(recording, self._mark, recording.frames)
        finally:
            self._last_record_seconds = recording.frames / RATE
            self._record = None
            self._mark = self._record_stop = None
            if self._system:
                self._system.close(); self._system = None
            self._release_mic()
            self._emit("recording_stopped")

    def recover_recordings(self, folder: str) -> list[str]:
        """Call off the UI thread, before starting a recording in this folder."""
        with self._lock:
            if self._record is not None:
                return []
            try:
                result = recover_recording_parts(folder)
                for path in result:
                    self._emit("recording_saved", path=path)
                return result
            except Exception as error:
                self._error(error)
                return []

    def snapshot(self) -> dict:
        # Publish by replacing a dict reference; never take the device/storage
        # worker lock from a UI timer. Callers get their own mutable copy.
        result = self._published_state.copy()
        result["diagnostics"] = result["diagnostics"].copy()
        return result

    def _capture_state(self):
        cursor = self._cursor
        return {"path": self._path, "playing": cursor is not None and not cursor.paused,
                "play_loading": self._play_loading,
                "play_buffering": bool(cursor and not cursor.paused and getattr(cursor.samples, "buffering", False)),
                "paused": cursor is not None and cursor.paused,
                "position": cursor.position / RATE if cursor else self._last_position,
                "recording": self._record is not None,
                "record_seconds": self._record.frames / RATE if self._record else self._last_record_seconds,
                "mark_pending": self._mark is not None, "call_connected": self._call is not None,
                "call_output_id": self._call.device.id if self._call else "",
                "mic_peak": self._mic.peak if self._mic else 0.0,
                "call_peak": self._call.peak if self._call else 0.0,
                "local_peak": self._local.peak if self._local else 0.0,
                "test_output_active": self._test_position is not None,
                "diagnostics": {
                    "capture_dropped_frames": sum(p.dropped_frames for p in (self._mic, self._system) if p),
                    "capture_overflows": sum(p.overflow_count for p in (self._mic, self._system) if p),
                    "timestamp_fallback_packets": sum(p.timestamp_clock.fallback_packets for p in (self._mic, self._system) if p),
                    "capture_gap_resets": sum(p.converter.gap_resets for p in (self._mic, self._system) if p),
                    "output_dropped_frames": sum(p.buffer.dropped_frames for p in (self._local, self._call) if p),
                    "output_underrun_frames": sum(p.buffer.underrun_frames for p in (self._local, self._call) if p),
                    "output_underflows": sum(p.underflow_count for p in (self._local, self._call) if p),
                    "call_buffer_ms": self._call.buffer.frames / self._call.device.rate * 1000 if self._call else 0,
                    "local_buffer_ms": self._local.buffer.frames / self._local.device.rate * 1000 if self._local else 0,
                    "mic_clock_ppm": self._mic.converter.correction_ppm if self._mic else 0,
                    "record_limited_frames": self._record_limiter.limited_frames,
                    "call_limited_frames": self._call.limiter.limited_frames if self._call else 0,
                }}

    def test_call_output(self, local=False, duration=1.0):
        self._post(self._start_test_output, bool(local), float(duration))

    def _start_test_output(self, local, duration):
        if not self._call:
            raise ValueError("请先连接通话设备，再发送测试音")
        if self._cursor or self._play_loading:
            raise ValueError("请先停止片段播放，再发送测试音")
        if not np.isfinite(duration):
            raise ValueError("测试时长无效")
        if local:
            self._ensure_local()
        self._test_position = 0
        self._test_end = round(np.clip(duration, .2, 2.0) * RATE)
        self._test_local = local
        self._emit("test_output_started")

    def _publish_state(self):
        self._published_state = self._capture_state()

    def _run(self):
        # WASAPI opens on this worker, which needs its own COM apartment even
        # when PortAudio was initialized by a separate enumeration thread.
        initialized_com = False
        if os.name == "nt":
            initialized_com = ctypes.windll.ole32.CoInitializeEx(None, 0) in (0, 1)
        try:
            self._run_loop()
        finally:
            if initialized_com:
                ctypes.windll.ole32.CoUninitialize()

    def _run_loop(self):
        while not self._quit.wait(.005):
            with self._lock:
                while True:
                    try:
                        command, args = self._commands.get_nowait()
                    except queue.Empty:
                        break
                    try:
                        command(*args)
                    except Exception as error:
                        self._error(error)
                    finally:
                        self._publish_state()
                try:
                    self._tick()
                except Exception as error:
                    self._error(error)
                    # Fail closed: no apparently healthy stream after a DSP/storage failure.
                    self._stop_playback()
                    try:
                        self._finish_recording()
                    except Exception as save_error:
                        self._error(f"已保留临时录音供下次恢复：{save_error}")
                    self._disconnect_call()
                finally:
                    self._publish_state()

    def _tick(self):
        now = self._clock()
        if self._cursor and self._active_request != self._request:
            self._stop_playback()
        if self._asset and getattr(self._asset, "error", None):
            # A damaged media track must not disconnect a healthy microphone
            # or abort an independent recording.
            error = self._asset.error
            self._stop_playback(True)
            self._error(error)
        for capture in (self._mic, self._system):
            if capture:
                capture.drain()
                if capture.overflow:
                    capture.overflow = False
                    self._error("采集缓冲暂时溢出，缺失时段将保留为空白，请降低系统负载")
        if self._record:
            end = round((min(now - SYNC_DELAY, self._record_stop) if self._record_stop is not None else now - SYNC_DELAY) * RATE)
            self._write_recording_until(end)
            if now - self._last_flush >= 1:
                self._record.flush(); self._last_flush = now
            if self._record.frames >= MAX_WAV_FRAMES or (self._record_stop is not None and now >= self._record_stop + SYNC_DELAY + .04):
                if self._record.frames >= MAX_WAV_FRAMES:
                    self._error("单个 WAV 已接近 4 GB，录音已安全保存；请开始新的录音")
                self._finish_recording()
        target = round(now * RATE)
        if target - self._clock_frame > RATE // 2:
            self._clock_frame = target - BLOCK
            for output in (self._local, self._call):
                if output:
                    output.buffer.clear()
        while self._clock_frame + BLOCK <= target:
            clip = self._cursor.read(BLOCK) if self._cursor else np.zeros((BLOCK, 2), np.float32)
            silence = np.zeros_like(clip)
            test = None
            if self._test_position is not None:
                count = min(BLOCK, self._test_end - self._test_position)
                test = np.zeros_like(clip)
                positions = np.arange(self._test_position, self._test_position + count)
                # Short fades avoid clicks. This bypasses the fragment-send toggle.
                envelope = np.minimum(1, np.minimum(positions / 240, (self._test_end - positions) / 240))
                test[:count] = (.08 * envelope * np.sin(2 * np.pi * 660 * positions / RATE))[:, None]
                self._test_position += count
            if self._local:
                self._local.push(silence, test if test is not None and self._test_local else clip)
            if self._call:
                mic = self._mic.converter.timeline.read(self._clock_frame - round(SYNC_DELAY * RATE), BLOCK) if self._mic else silence
                self._call.push(mic, test if test is not None else clip, clip_gain_override=1.0 if test is not None else None)
            if self._test_position is not None and self._test_position >= self._test_end:
                self._test_position = None
                self._emit("test_output_finished")
            self._clock_frame += BLOCK
        if self._cursor and self._cursor.finished and not self._cursor.paused:
            if self._end_deadline is None:
                self._end_deadline = now + max((route.delay for route in (self._local, self._call) if route), default=.06)
            elif now >= self._end_deadline:
                token, path = self._token, self._path
                self._last_position = self._cursor.position / RATE
                self._stop_playback(True)
                if self._active_request == self._request:
                    self._emit("playback_finished", token=token, path=path)
        if now - self._last_check > .5:
            self._last_check = now
            self._check_devices()

    def _write_recording_until(self, end):
        end = min(end, self._record_start + MAX_WAV_FRAMES)
        while self._record_start + self._record.frames < end:
            first = self._record_start + self._record.frames
            count = min(BLOCK, end - first)
            audio = np.zeros((count, 2), np.float32)
            if self._record_source in ("mic", "both") and self._mic:
                audio += self._mic.converter.timeline.read(first, count)
            if self._record_source in ("system", "both") and self._system:
                audio += self._system.converter.timeline.read(first, count)
            self._record.write(self._record_limiter.process(audio))

    def _check_devices(self):
        for name in ("_mic", "_system", "_local", "_call"):
            route = getattr(self, name)
            if route is None:
                continue
            try:
                failed = not route.stream.is_active() or getattr(route, "error", None) is not None
            except Exception:
                failed = True
            if not failed:
                continue
            self._error(f"设备已断开：{route.device.name}；请重新选择后手动恢复")
            if name in ("_mic", "_system"):
                if self._record and (name == "_system" or self._record_source in ("mic", "both")):
                    try:
                        self._finish_recording()
                    except Exception as error:
                        self._error(error)
                if name == "_mic":
                    self._disconnect_call()
                remaining = getattr(self, name)
                if remaining:
                    remaining.close(); setattr(self, name, None)
            elif name == "_call":
                self._disconnect_call()
            else:
                self._stop_playback()
                route.close(); self._local = None

    def close(self):
        with self._request_lock:
            if self._closed:
                return list(self._shutdown_errors)
            self._closed = True
            self._request += 1
            self._cancel.set()
        self._quit.set()
        self._thread.join()
        with self._lock:
            self._stop_playback()
            try:
                self._finish_recording()
            except Exception as error:
                self._error(f"录音保留在临时文件中，可下次恢复：{error}")
            for name in ("_local", "_call", "_mic", "_system"):
                route = getattr(self, name)
                if route:
                    try:
                        route.close()
                    except Exception as error:
                        log.exception("Closing audio device")
                        self._shutdown_errors.append(str(error))
                    setattr(self, name, None)
            while not self._commands.empty():
                command, args = self._commands.get_nowait()
                if command == self._accept_play:
                    args[0].close()
                    if hasattr(args[0], "wait_closed"):
                        self._retired_samples.append(args[0].samples)
            self._publish_state()
        self._executor.shutdown(wait=True, cancel_futures=True)
        for samples in self._retired_samples:
            if not samples.wait_closed():
                self._shutdown_errors.append("媒体解码退出超时")
        self._retired_samples.clear()
        with self._backend_lock:
            if self._backend:
                try:
                    self._backend.close()
                except Exception as error:
                    self._shutdown_errors.append(str(error))
        return list(self._shutdown_errors)
