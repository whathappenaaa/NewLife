from pathlib import Path
import queue
import struct
import threading
import time
import wave
from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf

import niulai_player.audio as audio
from niulai_player.audio import AudioEngine, WavRecording, recover_recording_parts
from niulai_player.audio_processing import RATE
from niulai_player.models import DeviceInfo


class Clock:
    now = 100.0

    def __call__(self):
        return self.now


class Stream:
    def __init__(self, device, callback, output, clock):
        self.device, self.callback, self.output, self.clock = device, callback, output, clock
        self.active = self.closed = False
        self.heard = []

    def get_time(self):
        return self.clock()

    def start_stream(self):
        self.active = True

    def stop_stream(self):
        self.active = False

    def close(self):
        self.active, self.closed = False, True

    def is_active(self):
        return self.active


class Backend:
    def __init__(self, clock):
        self.clock, self.streams = clock, []
        self.closed = False
        self.device_list = [
            DeviceInfo("mic", "Real microphone", "input", 44100, 1, True),
            DeviceInfo("headphones", "Headphones", "output", 48000, 2, True),
            DeviceInfo("system", "Headphones loopback", "loopback", 48000, 2, True),
            DeviceInfo("cable", "CABLE Input", "output", 48000, 2, False, True),
            DeviceInfo("cablemic", "CABLE Output", "input", 48000, 2, False, True),
        ]

    def devices(self):
        return self.device_list

    def open(self, device, callback, output=False):
        stream = Stream(device, callback, output, self.clock)
        self.streams.append(stream)
        return stream

    def close(self):
        self.closed = True


class Harness:
    def __init__(self):
        self.clock, self.events = Clock(), []
        self.backend = Backend(self.clock)
        self.engine = AudioEngine(lambda name, payload: self.events.append((name, payload)), backend=self.backend, clock=self.clock)
        # Tests advance the worker deterministically, without opening any devices.
        self.engine._quit.set()
        self.engine._thread.join()

    def commands(self):
        while True:
            try:
                method, args = self.engine._commands.get_nowait()
            except queue.Empty:
                break
            try:
                method(*args)
            except Exception as error:
                self.engine._error(error)
            finally:
                self.engine._publish_state()

    def advance(self, seconds=.01):
        self.commands()
        for _ in range(round(seconds * 100)):
            for stream in self.backend.streams:
                if not stream.output and stream.active:
                    count = round(stream.device.rate / 100)
                    value = .2 if stream.device.id == "mic" else .4
                    samples = np.full((count, stream.device.channels), value, np.float32)
                    stream.callback(samples.tobytes(), count, {"input_buffer_adc_time": self.clock()}, 0)
            self.clock.now += .01
            self.commands()
            self.engine._tick()
            self.engine._publish_state()
            for stream in self.backend.streams:
                if stream.output and stream.active:
                    count = round(stream.device.rate / 100)
                    data, _ = stream.callback(None, count, {}, 0)
                    stream.heard.append(np.frombuffer(data, np.float32).reshape(-1, min(2, stream.device.channels)))

    def wait(self, predicate):
        deadline = time.monotonic() + 3
        while not predicate():
            assert time.monotonic() < deadline
            self.commands()
            time.sleep(.005)

    def close(self):
        self.engine.close()


@pytest.fixture
def harness():
    harness = Harness()
    yield harness
    harness.close()


def test_constructor_and_device_enumeration_do_not_capture(harness):
    assert len(harness.engine.devices()) == 5
    assert harness.backend.streams == []


def test_system_device_change_keeps_recording_and_call(harness, tmp_path):
    engine = harness.engine
    harness.backend.device_list += [DeviceInfo("mic2", "New microphone", "input", 48000, 1),
                                   DeviceInfo("speakers2", "New speakers", "output", 48000, 2),
                                   DeviceInfo("loop2", "New speakers loopback", "loopback", 48000, 2)]
    engine.set_local_device("headphones")
    engine.connect_call("mic", "cable")
    engine.start_recording(str(tmp_path), "both", "mic", "system")
    harness.advance(.4)
    previous = engine._mic
    engine.follow_devices("speakers2", "mic2", "loop2")
    harness.advance(.4)
    assert previous.stream.closed
    assert engine._mic.device.id == "mic2" and engine._system.device.id == "loop2"
    assert engine.snapshot()["recording"] and engine.snapshot()["call_connected"]
    assert engine.snapshot()["record_seconds"] > .5
    assert not any(name == "error" for name, _ in harness.events)


def test_record_and_call_share_native_mic_system_is_never_forwarded(harness, tmp_path):
    engine = harness.engine
    engine.connect_call("mic", "cable")
    engine.start_recording(str(tmp_path), "both", "mic", "system")
    harness.advance(1.5)
    assert sum(s.device.id == "mic" for s in harness.backend.streams) == 1
    assert engine.snapshot()["record_seconds"] > 1.3
    call = next(s for s in harness.backend.streams if s.device.id == "cable")
    np.testing.assert_allclose(call.heard[-1], .2, atol=.005)
    engine.stop_recording()
    harness.advance(.25)
    saved = next(payload["path"] for name, payload in harness.events if name == "recording_saved")
    data, rate = sf.read(saved)
    assert rate == RATE
    np.testing.assert_allclose(data[RATE // 2:RATE], .6, atol=.005)
    assert engine.snapshot()["call_connected"]
    assert next(s for s in harness.backend.streams if s.device.id == "mic").active


def test_play_stop_pause_preserve_live_microphone_and_local_has_no_mic(harness, tmp_path):
    path = tmp_path / "tone.wav"
    sf.write(path, np.full((RATE * 3, 2), .3), RATE, subtype="FLOAT")
    engine = harness.engine
    engine.set_local_device("headphones")
    engine.connect_call("mic", "cable")
    engine.set_volumes(1, 1, 1)
    engine.play(str(path), 0, 3, True, 12)
    harness.wait(lambda: engine.snapshot()["playing"])
    harness.advance(.4)
    local = next(s for s in harness.backend.streams if s.device.id == "headphones")
    call = next(s for s in harness.backend.streams if s.device.id == "cable")
    np.testing.assert_allclose(local.heard[-1], .3, atol=.005)
    np.testing.assert_allclose(call.heard[-1], .5, atol=.005)
    engine.pause()
    harness.advance(.2)
    assert engine.snapshot()["paused"]
    np.testing.assert_allclose(call.heard[-1], .2, atol=.005)
    assert not local.heard[-1].any()
    engine.stop()
    harness.advance(.2)
    np.testing.assert_allclose(call.heard[-1], .2, atol=.005)
    assert engine.snapshot()["call_connected"]
    assert not any(name == "playback_finished" for name, _ in harness.events)


def test_natural_end_emits_only_matching_token(harness, tmp_path):
    path = tmp_path / "tone.wav"
    sf.write(path, np.full((RATE, 1), .2), RATE)
    engine = harness.engine
    engine.set_local_device("headphones")
    engine.play(str(path), .2, .4, token=73)
    harness.wait(lambda: engine.snapshot()["playing"])
    harness.advance(.7)
    completed = [event for event in harness.events if event[0] == "playback_finished"]
    assert completed == [("playback_finished", {"path": str(path), "token": 73})]
    assert engine.snapshot()["position"] == pytest.approx(.4)


def test_seek_while_paused_preserves_mic_and_clears_old_clip(harness, tmp_path):
    path = tmp_path / "seek.wav"
    data = np.full((RATE * 3, 2), .1, np.float32)
    data[RATE:] = .35
    sf.write(path, data, RATE, subtype="FLOAT")
    engine = harness.engine
    engine.set_local_device("headphones")
    engine.connect_call("mic", "cable")
    engine.set_volumes(1, 1, 1)
    engine.play(str(path), .2, 2.5, True, 21)
    harness.wait(lambda: engine.snapshot()["playing"])
    harness.advance(.15)
    engine.pause()
    engine.seek(1.5, token=22)
    harness.advance(.2)
    state = engine.snapshot()
    assert state["paused"] and state["call_connected"]
    assert state["position"] == 1.5
    call = next(s for s in harness.backend.streams if s.device.id == "cable")
    np.testing.assert_allclose(call.heard[-1], .2, atol=.005)
    # The simulated clock advances faster than a real decoder. Await PCM at
    # the new position while paused; buffering itself must leave the mic live.
    harness.wait(lambda: engine._asset.samples._chunks.get(0, [None, 0])[1] >= 2 * RATE)
    engine.pause()
    harness.advance(.15)
    np.testing.assert_allclose(call.heard[-1], .55, atol=.005)
    engine.seek(9, token=23)
    harness.commands()
    assert engine.snapshot()["position"] < 2.5
    engine.stop()
    harness.advance(.15)
    np.testing.assert_allclose(call.heard[-1], .2, atol=.005)
    assert not any(name == "playback_finished" for name, _ in harness.events)


def test_cancelled_decode_cannot_revive_playback(harness, monkeypatch):
    started, release = threading.Event(), threading.Event()
    closed = []

    class Asset:
        samples = np.zeros((RATE, 2), np.float32)
        def close(self):
            closed.append(True)

    def slow_decode(path, cancelled, stream_index=None, *, start=None):
        started.set()
        release.wait(2)
        return Asset()

    monkeypatch.setattr(audio, "_decode", slow_decode)
    engine = harness.engine
    engine.set_local_device("headphones")
    engine.play("fake.wav", 0, 1, token=5)
    assert started.wait(1)
    harness.commands()
    assert engine.snapshot()["play_loading"]
    engine.stop()
    harness.commands()
    release.set()
    harness.wait(lambda: bool(closed))
    assert not engine.snapshot()["playing"]
    assert not engine.snapshot()["play_loading"]
    assert harness.backend.streams == []


def test_missing_or_feedback_devices_do_not_fallback(harness):
    engine = harness.engine
    engine.connect_call("mic", "headphones")
    engine.connect_call("cablemic", "cable")
    engine.set_local_device("cable")
    engine.connect_call("missing", "cable")
    harness.commands()
    assert len([name for name, _ in harness.events if name == "error"]) == 4
    assert harness.backend.streams == []


def test_device_loss_saves_recording_and_drops_affected_call(harness, tmp_path):
    engine = harness.engine
    engine.connect_call("mic", "cable")
    engine.start_recording(str(tmp_path), "mic", "mic")
    harness.advance(.7)
    next(s for s in harness.backend.streams if s.device.id == "mic").active = False
    engine._check_devices()
    engine._publish_state()
    assert not engine.snapshot()["recording"]
    assert not engine.snapshot()["call_connected"]
    saved = next(payload["path"] for name, payload in harness.events if name == "recording_saved")
    assert sf.info(saved).frames > RATE // 2
    assert all(stream.closed for stream in harness.backend.streams)


def test_live_record_marks_and_stop_pending_mark_export(harness, tmp_path):
    engine = harness.engine
    engine.start_recording(str(tmp_path), "mic", "mic")
    harness.advance(.3)
    engine.mark_recording()
    harness.advance(1.2)
    engine.mark_recording()
    harness.commands()
    harness.wait(lambda: any(name == "clip_saved" for name, _ in harness.events))
    assert engine.snapshot()["recording"]
    engine.mark_recording()
    harness.advance(1.2)
    engine.stop_recording()
    harness.advance(.25)
    harness.wait(lambda: sum(name == "clip_saved" for name, _ in harness.events) == 2)
    for name, payload in harness.events:
        if name == "clip_saved":
            assert sf.info(payload["path"]).duration >= 1
    assert not list(tmp_path.glob("*.part"))


def test_flush_header_and_recovery_keep_exact_frames(tmp_path):
    recording = WavRecording(str(tmp_path))
    data = np.full((RATE + 7, 2), .25, np.float32)
    recording.write(data)
    assert recording.flush() == RATE + 7
    with wave.open(str(recording.path), "rb") as reader:
        assert reader.getnframes() == RATE + 7
    recording._wave.close()
    recording._file.close()
    recording.closed = True
    # Simulate a crash after raw PCM reached disk but before its header patch.
    with recording.path.open("r+b") as file:
        file.seek(4); file.write(struct.pack("<I", 36))
        file.seek(40); file.write(struct.pack("<I", 0))
        file.seek(0, 2); file.write(b"x")
    result = recover_recording_parts(str(tmp_path))
    assert len(result) == 1
    assert sf.info(result[0]).frames == RATE + 7
    assert Path(result[0]).stat().st_size == 44 + (RATE + 7) * 4


def test_decode_native_mono_to_stereo_48k(tmp_path):
    path = tmp_path / "native.wav"
    sf.write(path, np.full(44100, .25), 44100)
    asset = audio._decode(str(path), threading.Event())
    try:
        assert asset.samples.shape == (RATE, 2)
        np.testing.assert_allclose(asset.samples[100:-100], .25, atol=.001)
    finally:
        asset.close()


def test_snapshot_remains_fast_during_slow_hardware_open():
    clock = Clock()
    backend = Backend(clock)
    entered, release = threading.Event(), threading.Event()
    original_open = backend.open

    def slow_open(*args, **kwargs):
        entered.set()
        release.wait(2)
        return original_open(*args, **kwargs)

    backend.open = slow_open
    engine = AudioEngine(lambda *_: None, backend=backend, clock=clock)
    try:
        engine.connect_call("mic", "cable")
        assert entered.wait(1)
        start = time.monotonic()
        for _ in range(200):
            state = engine.snapshot()
        assert time.monotonic() - start < .1
        assert not state["call_connected"]
    finally:
        release.set()
        engine.close()


def test_native_pcm16_fallback_converts_both_directions_without_opening_hardware():
    backend = audio._PortAudioBackend.__new__(audio._PortAudioBackend)
    backend.module = SimpleNamespace(paFloat32=1, paInt16=8)
    backend._lock = threading.RLock()
    backend._streams = 0
    backend._known = {}
    opened = []
    current_device = None

    class Native:
        def get_device_info_by_index(self, index):
            return {"name": current_device.name}

        def is_format_supported(self, rate, **kwargs):
            if kwargs.get("input_format", kwargs.get("output_format")) == 1:
                raise ValueError("float unavailable")
            return True

        def open(self, **kwargs):
            opened.append(kwargs)
            return SimpleNamespace(close=lambda: None)

    backend._pa = Native()
    current_device = DeviceInfo("native-mic", "PCM16 mic", "input", 44100, 1)
    backend._known[current_device.id] = (current_device, 1)
    received = []
    stream = backend.open(current_device, lambda data, *_: (received.append(np.frombuffer(data, np.float32)) or None, 0))
    opened[-1]["stream_callback"](np.array([-32768, 16384], "<i2").tobytes(), 2, {}, 0)
    np.testing.assert_array_equal(received[0], [-1, .5])
    assert opened[-1]["rate"] == 44100
    stream.close()
    current_device = DeviceInfo("native-output", "6 channel PCM16 output", "output", 48000, 6)
    backend._known[current_device.id] = (current_device, 2)
    stream = backend.open(current_device, lambda *_: (np.full((2, 2), .5, np.float32).tobytes(), 0), True)
    data, flag = opened[-1]["stream_callback"](None, 2, {}, 0)
    samples = np.frombuffer(data, "<i2").reshape(2, 6)
    np.testing.assert_array_equal(samples[:, :2], 16384)
    assert not samples[:, 2:].any()
    assert flag == 0
    stream.close()
    assert backend._streams == 0


def test_recording_finish_failure_releases_hardware_and_keeps_recoverable_part(harness, tmp_path, monkeypatch):
    engine = harness.engine
    engine.start_recording(str(tmp_path), "mic", "mic")
    harness.advance(.5)
    recording = engine._record

    def failed_finish():
        recording._wave.close()
        recording._file.close()
        recording.closed = True
        raise OSError("simulated rename failure")

    monkeypatch.setattr(recording, "finish", failed_finish)
    with pytest.raises(OSError):
        engine._finish_recording()
    engine._publish_state()
    assert not engine.snapshot()["recording"]
    assert all(stream.closed for stream in harness.backend.streams)
    assert any(name == "recording_stopped" for name, _ in harness.events)
    assert len(recover_recording_parts(str(tmp_path))) == 1


def test_decode_error_clears_loading_without_touching_devices(harness, monkeypatch):
    def fail(*_):
        raise ValueError("corrupt audio")
    monkeypatch.setattr(audio, "_decode", fail)
    harness.engine.play("corrupt.wav", 0, 1)
    harness.wait(lambda: any(name == "error" for name, _ in harness.events))
    assert not harness.engine.snapshot()["play_loading"]
    assert not harness.engine.snapshot()["playing"]
    assert harness.backend.streams == []


def test_call_test_tone_ignores_send_toggle_and_keeps_microphone_running(harness):
    engine = harness.engine
    engine.connect_call("mic", "cable")
    harness.advance(.3)
    assert any(name == "call_connected" for name, _ in harness.events)
    engine.test_call_output(duration=.5)
    harness.advance(.3)
    call = next(stream for stream in harness.backend.streams if stream.device.id == "cable")
    assert np.std(call.heard[-1]) > .025
    harness.advance(.6)
    np.testing.assert_allclose(call.heard[-1], .2, atol=.005)
    assert engine.snapshot()["call_connected"]
    assert not engine.snapshot()["test_output_active"]
    assert any(name == "test_output_finished" for name, _ in harness.events)
    assert not any(name == "playback_finished" for name, _ in harness.events)
    assert "capture_gap_resets" in engine.snapshot()["diagnostics"]


def test_close_reports_storage_failure_for_ui(harness, tmp_path, monkeypatch):
    engine = harness.engine
    engine.start_recording(str(tmp_path), "mic", "mic")
    harness.advance(.3)
    recording = engine._record
    original_finish = recording.finish
    def fail_finish():
        original_finish()
        raise OSError("simulated storage finalization failure")
    monkeypatch.setattr(recording, "finish", fail_finish)
    errors = engine.close()
    assert any("simulated storage" in message for message in errors)
