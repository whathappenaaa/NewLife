"""Deterministic audio processing; no device or UI side effects."""
from __future__ import annotations

import threading
from collections import deque
import math
from statistics import median
import numpy as np
import soxr

RATE = 48_000
CHANNELS = 2


def stereo(data: np.ndarray) -> np.ndarray:
    data = np.asarray(data, dtype=np.float32)
    if data.ndim == 1:
        data = data[:, None]
    if data.ndim != 2 or data.shape[1] < 1:
        raise ValueError("Invalid audio channel layout")
    if data.shape[1] == 1:
        data = np.repeat(data, 2, axis=1)
    elif data.shape[1] > 2:
        # PortAudio does not supply a speaker mask. Preserve all channels with a
        # conservative mono downmix rather than guessing a surround layout.
        data = np.repeat(data.mean(axis=1, keepdims=True), 2, axis=1)
    return np.ascontiguousarray(np.nan_to_num(data, nan=0.0, posinf=0.0, neginf=0.0))


def limit(data: np.ndarray) -> np.ndarray:
    return np.clip(np.nan_to_num(data, nan=0.0, posinf=0.0, neginf=0.0), -1.0, 1.0).astype(np.float32)


def pcm16(data: np.ndarray) -> bytes:
    values = limit(data)
    return np.where(values <= -1, -32768, np.rint(values * 32767)).astype("<i2").tobytes()


class SmoothLimiter:
    """Linked-channel peak limiter: instant protection, exponential 80 ms release.

    Unlike hard clipping, overload reduces the complete waveform by one shared
    gain. State survives blocks. Runs on the worker, never the device callback.
    """

    def __init__(self, rate=RATE, ceiling=.98, release_seconds=.08):
        self.ceiling = ceiling
        self.release = float(np.exp(-1 / (rate * release_seconds)))
        self.gain = 1.0
        self.limited_frames = 0

    def gains(self, samples):
        peaks = np.max(np.abs(np.nan_to_num(samples, nan=0.0, posinf=0.0, neginf=0.0)), axis=1)
        result = np.ones((len(samples), 1), np.float32)
        gain = self.gain
        for index, peak in enumerate(peaks):
            target = min(1.0, self.ceiling / max(float(peak), 1e-12))
            gain = target if target < gain else self.release * gain + (1 - self.release) * target
            result[index, 0] = gain
        self.gain = gain
        self.limited_frames += int(np.count_nonzero(result < .999))
        return result

    def process(self, samples):
        samples = stereo(samples)
        return samples * self.gains(samples)


class CaptureTimestampClock:
    """Continuous sample clock with slow, robust fallback frequency correction.

    Callback arrival supplies frequency only, never packet position. One minimum
    latency observation per second rejects late callbacks; a bounded Theil-Sen
    slope over 45 seconds rejects outlier windows. This work is bounded, uses no
    locks/I/O, and occurs only once per second in the capture callback.
    """

    def __init__(self, rate):
        self.rate = rate
        self.next_stamp = None
        self.fallback_packets = 0
        self.correction_ppm = 0.0
        self._frames = 0
        self._bucket_end = 1.0
        self._candidate = None
        self._observations = deque(maxlen=45)
        self._using_adc = False
        self._adc_offset = 0.0

    def _observe_arrival(self, arrival):
        nominal = self._frames / self.rate
        candidate = (arrival - nominal, nominal, arrival)
        if self._candidate is None or candidate[0] < self._candidate[0]:
            self._candidate = candidate
        if nominal < self._bucket_end:
            return
        _, x, y = self._candidate
        self._observations.append((x, y))
        self._candidate = None
        self._bucket_end = math.floor(nominal) + 1.0
        points = list(self._observations)
        slopes = [(right[1] - left[1]) / (right[0] - left[0])
                  for index, left in enumerate(points) for right in points[index + 1:]
                  if right[0] - left[0] >= 8.0]
        if slopes:
            slope = median(slopes)
            observed = (1 / slope - 1) * 1_000_000 if slope > 0 else float("inf")
            if abs(observed) <= 2000:
                self.correction_ppm += .12 * (observed - self.correction_ppm)

    def stamp(self, frame_count, arrival, adc=None):
        duration = frame_count / self.rate
        self._frames += frame_count
        self._observe_arrival(arrival)
        valid_adc = adc is not None and math.isfinite(adc) and arrival - 5 <= adc <= arrival + .1
        if valid_adc:
            if not self._using_adc and self.next_stamp is not None:
                # ADC and fallback can have different fixed latency. Preserve
                # continuity on source changes, then slew phase at <=1000 ppm.
                self._adc_offset = self.next_stamp - float(adc)
            first = float(adc) + self._adc_offset
            step = duration * .001
            self._adc_offset -= max(-step, min(step, self._adc_offset))
        else:
            first = arrival - duration if self.next_stamp is None else self.next_stamp
            self.fallback_packets += 1
            duration /= 1 + self.correction_ppm / 1_000_000
        self._using_adc = valid_adc
        self.next_stamp = first + duration
        return first


class StreamingResampler:
    """Continuous band-limited conversion with bounded, smoothly changing ratio."""

    def __init__(self, input_rate: float, output_rate: float = RATE, channels: int = 2):
        self.input_rate = float(input_rate)
        self.output_rate = float(output_rate)
        self.channels = channels
        self._stream = soxr.ResampleStream(
            input_rate * 1.005, output_rate, channels, dtype="float32", quality="HQ", vr=True
        )
        self._stream.set_io_ratio(input_rate, output_rate)
        self._finished = False

    def correct(self, ppm: float) -> None:
        ppm = float(np.clip(ppm, -2000, 2000))
        self._stream.set_io_ratio(self.input_rate * (1 + ppm / 1_000_000), self.output_rate, slew_len=4096)

    def process(self, data: np.ndarray, final: bool = False) -> np.ndarray:
        if self._finished:
            raise RuntimeError("Resampler has already finished")
        self._finished = final
        return self._stream.resample_chunk(np.ascontiguousarray(data, dtype=np.float32), last=final)

    @property
    def delay(self) -> float:
        return float(self._stream.delay())


class AudioTimeline:
    """Timestamped, non-consuming stereo ring: recording and call may both read it."""

    def __init__(self, capacity: int = RATE * 10):
        self.capacity = capacity
        self._audio = np.zeros((capacity, 2), np.float32)
        self._stamp = np.full(capacity, np.iinfo(np.int64).min, np.int64)
        self._lock = threading.Lock()

    def add(self, first_frame: int, samples: np.ndarray) -> None:
        samples = stereo(samples)
        if len(samples) > self.capacity:
            first_frame += len(samples) - self.capacity
            samples = samples[-self.capacity:]
        ids = first_frame + np.arange(len(samples), dtype=np.int64)
        slots = ids % self.capacity
        with self._lock:
            self._audio[slots] = samples
            self._stamp[slots] = ids

    def read(self, first_frame: int, count: int) -> np.ndarray:
        ids = first_frame + np.arange(count, dtype=np.int64)
        slots = ids % self.capacity
        with self._lock:
            result = self._audio[slots].copy()
            result[self._stamp[slots] != ids] = 0
        return result


class CaptureConverter:
    """Maps a native capture clock to the shared 48 kHz timeline.

    Timestamps are monotonic seconds for the first input frame, calibrated by
    the device adapter. Missing intervals stay silence; they are never collapsed.
    """

    def __init__(self, rate: float, timeline: AudioTimeline | None = None):
        self.rate = rate
        self.timeline = timeline or AudioTimeline()
        self.resampler = StreamingResampler(rate)
        self.origin: float | None = None
        self.previous_end: float | None = None
        self.input_frames = 0
        self.next_frame = 0
        self.correction_ppm = 0.0
        self.gap_resets = 0

    def feed(self, data: np.ndarray, timestamp: float) -> None:
        data = stereo(data)
        if not len(data):
            return
        if self.origin is None or (self.previous_end is not None and abs(timestamp - self.previous_end) > .003):
            if self.origin is not None:
                self.gap_resets += 1
                tail = self.resampler.process(np.empty((0, 2), np.float32), final=True)
                self.timeline.add(self.next_frame, tail)
            self.resampler = StreamingResampler(self.rate)
            self.origin = timestamp
            self.next_frame = round(timestamp * RATE)
            self.input_frames = 0
            self.correction_ppm = 0
        elapsed = timestamp - self.origin
        if elapsed >= 2 and self.input_frames:
            observed = self.input_frames / elapsed
            desired = np.clip((observed / self.rate - 1) * 1_000_000, -2000, 2000)
            self.correction_ppm += .03 * (desired - self.correction_ppm)
            self.resampler.correct(self.correction_ppm)
        result = self.resampler.process(data)
        self.timeline.add(self.next_frame, result)
        self.next_frame += len(result)
        self.input_frames += len(data)
        self.previous_end = timestamp + len(data) / self.rate


class RoutedBuffer:
    """Separate microphone/clip lanes, independently consumed by each output."""

    def __init__(self, capacity: int, channels: int = 2):
        self.capacity = capacity
        self.channels = channels
        self._mic = np.zeros((capacity, channels), np.float32)
        self._clip = np.zeros_like(self._mic)
        self._read = [0, 0]
        self._count = [0, 0]
        self._lock = threading.Lock()
        self.mic_gain = 0.0
        self.clip_gain = 1.0
        self.dropped_frames = 0
        self.underrun_frames = 0

    @property
    def frames(self) -> int:
        with self._lock:
            return max(self._count)

    def levels(self, mic: float, clip: float) -> None:
        with self._lock:
            self.mic_gain, self.clip_gain = float(np.clip(mic, 0, 1)), float(np.clip(clip, 0, 1))

    def add(self, mic: np.ndarray, clip: np.ndarray) -> None:
        if mic.shape[1] != self.channels or clip.shape[1] != self.channels:
            raise ValueError("Audio lanes must have the expected channel layout")
        with self._lock:
            for lane, (data, ring) in enumerate(((mic, self._mic), (clip, self._clip))):
                n = len(data)
                if n >= self.capacity:
                    self.dropped_frames += self._count[lane] + n - self.capacity
                    data = data[-self.capacity:]
                    self._read[lane] = self._count[lane] = 0
                    n = self.capacity
                overflow = max(0, self._count[lane] + n - self.capacity)
                self._read[lane] = (self._read[lane] + overflow) % self.capacity
                self._count[lane] -= overflow
                self.dropped_frames += overflow
                slots = (self._read[lane] + self._count[lane] + np.arange(n)) % self.capacity
                ring[slots] = data
                self._count[lane] += n

    def read(self, count: int) -> np.ndarray:
        result = np.zeros((count, self.channels), np.float32)
        with self._lock:
            self.underrun_frames += max(0, count - max(self._count))
            for lane, (ring, gain) in enumerate(((self._mic, self.mic_gain), (self._clip, self.clip_gain))):
                available = min(count, self._count[lane])
                slots = (self._read[lane] + np.arange(available)) % self.capacity
                result[:available] += ring[slots] * gain
                self._read[lane] = (self._read[lane] + available) % self.capacity
                self._count[lane] -= available
        return limit(result)

    def clear_clip(self) -> None:
        with self._lock:
            self._clip.fill(0)
            self._read[1] = self._count[1] = 0

    def clear(self) -> None:
        with self._lock:
            self._read = [0, 0]
            self._count = [0, 0]
            self._clip.fill(0)
            self._mic.fill(0)


class ClipCursor:
    """Frame-exact bounds and pause/range behavior independent of audio hardware."""

    def __init__(self, samples: np.ndarray, start: int, end: int):
        self.samples = samples
        self.start = max(0, min(start, len(samples) - 1))
        self.end = max(self.start + 1, min(end, len(samples)))
        self.position = self.start
        self.paused = False

    @property
    def finished(self) -> bool:
        return self.position >= min(self.end, len(self.samples))

    def read(self, count: int) -> np.ndarray:
        result = np.zeros((count, 2), np.float32)
        if self.paused:
            return result
        n = max(0, min(count, self.end - self.position, len(self.samples) - self.position))
        if not isinstance(self.samples, np.ndarray) and hasattr(self.samples, "take"):
            # A progressive source returns immediately. Buffering emits silence
            # without advancing the clip, while live microphone lanes continue.
            data = self.samples.take(self.position, n)
            n = len(data)
            result[:n] = data
        else:
            result[:n] = self.samples[self.position:self.position + n]
        self.position += n
        return result

    def set_range(self, start: int, end: int) -> bool:
        start = max(0, min(start, len(self.samples) - 1))
        end = max(start + 1, min(end, len(self.samples)))
        jump = not start <= self.position < end
        self.start, self.end = start, end
        if jump:
            self.position = start
        return jump
