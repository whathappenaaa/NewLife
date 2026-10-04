"""Opt-in Windows audio smoke test; NEVER opens a physical microphone.

Run explicitly: .venv/Scripts/python.exe tools/hardware_smoke.py --run
Plays a quiet generated tone on the default physical output, records its system
loopback, then mixes synthetic speech/tone into VB-CABLE and captures that virtual
endpoint. It does not change Windows defaults or install drivers.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import threading
import time

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from niulai_player.audio import AudioEngine, _PortAudioBackend
from niulai_player.models import DeviceInfo


RATE = 48000


def wait_for(predicate, timeout=8):
    until = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= until:
            raise TimeoutError("Audio smoke test timed out")
        time.sleep(.02)


def tone(frequency, seconds, amplitude=.04):
    return np.repeat((amplitude * np.sin(np.arange(round(seconds * RATE)) * 2 * np.pi * frequency / RATE))[:, None], 2, axis=1).astype(np.float32)


def amplitude(data, rate, frequency):
    data = np.asarray(data)
    if data.ndim == 2:
        data = data.mean(axis=1)
    if not len(data):
        return 0.0
    window = np.hanning(len(data))
    axis = np.arange(len(data)) / rate
    return float(abs(np.sum(data * window * np.exp(-2j * np.pi * frequency * axis))) * 2 / window.sum())


class SyntheticMicrophone:
    """A fake capture clock feeding a generated 440 Hz tone; no OS input handle."""
    def __init__(self, callback):
        self.callback = callback
        self.stop_event = threading.Event()
        self.thread = None
        self.active = False

    def get_time(self):
        return time.monotonic()

    def start_stream(self):
        self.active = True
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        origin = time.monotonic()
        frame = 0
        while not self.stop_event.is_set():
            start = origin + frame / RATE
            samples = np.repeat((.025 * np.sin(np.arange(frame, frame + 480) * 2 * np.pi * 440 / RATE))[:, None], 2, axis=1).astype(np.float32)
            self.callback(samples.tobytes(), 480, {"input_buffer_adc_time": start}, 0)
            frame += 480
            self.stop_event.wait(max(0, origin + frame / RATE - time.monotonic()))

    def stop_stream(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join()
        self.active = False

    def close(self):
        self.stop_stream()

    def is_active(self):
        return self.active


class SyntheticMicBackend:
    def __init__(self):
        self.native = _PortAudioBackend()
        self.mic = DeviceInfo("smoke-synthetic-mic", "Generated 440 Hz (no physical microphone)", "input", RATE, 2)

    def devices(self):
        return self.native.devices() + [self.mic]

    def open(self, device, callback, output=False):
        if device.id == self.mic.id:
            return SyntheticMicrophone(callback)
        if not output and not device.is_virtual:
            raise RuntimeError("Smoke test refuses every physical input")
        return self.native.open(device, callback, output)

    def close(self):
        self.native.close()


def local_test(folder, generated):
    events = []
    engine = AudioEngine(lambda name, payload: events.append((name, payload)))
    try:
        devices = engine.devices()
        output = next(device for device in devices if device.kind == "output" and device.is_default and not device.is_virtual)
        loopback = next(device for device in devices if device.kind == "loopback" and device.name.startswith(output.name) and not device.is_virtual)
        engine.set_local_device(output.id)
        engine.set_volumes(.25, .1, 1)
        engine.start_recording(str(folder), "system", system_id=loopback.id)
        wait_for(lambda: engine.snapshot()["recording"] or any(name == "error" for name, _ in events))
        if not engine.snapshot()["recording"]:
            raise RuntimeError(str(events))
        time.sleep(.2)
        engine.play(str(generated), 0, 2, token=100)
        wait_for(lambda: any(name == "playback_finished" for name, _ in events))
        engine.stop_recording()
        wait_for(lambda: any(name == "recording_saved" for name, _ in events))
        saved = next(payload["path"] for name, payload in events if name == "recording_saved")
        data, rate = sf.read(saved)
        # Tone may start after endpoint wake-up. Find the strongest half-second.
        strength = max(amplitude(data[start:start + rate // 2], rate, 880) for start in range(0, max(1, len(data) - rate // 2), rate // 4))
        return {"output": output.name, "loopback": loopback.name, "recorded_path": saved,
                "seconds": len(data) / rate, "tone_880_amplitude": strength,
                "passed": strength > .001, "events": events}
    finally:
        engine.close()


def cable_test(folder, generated):
    events, captured = [], []
    backend = SyntheticMicBackend()
    engine = AudioEngine(lambda name, payload: events.append((name, payload)), backend=backend)
    observer = None
    try:
        devices = engine.devices()
        cable_output = next(device for device in devices if device.kind == "output" and "cable input" in device.name.lower() and "16ch" not in device.name.lower())
        cable_capture = next(device for device in devices if device.kind == "input" and "cable output" in device.name.lower() and "16ch" not in device.name.lower())
        local = next(device for device in devices if device.kind == "output" and device.is_default and not device.is_virtual)

        def capture(data, frames, timing, flags):
            captured.append(np.frombuffer(data, np.float32).reshape(-1, cable_capture.channels).copy())
            return None, 0

        observer = backend.open(cable_capture, capture)
        observer.start_stream()
        engine.set_local_device(local.id)
        engine.set_volumes(0, .5, 1)
        engine.connect_call(backend.mic.id, cable_output.id)
        wait_for(lambda: engine.snapshot()["call_connected"] or any(name == "error" for name, _ in events))
        if not engine.snapshot()["call_connected"]:
            raise RuntimeError(str(events))
        time.sleep(.8)
        before = sum(len(block) for block in captured)
        engine.play(str(generated), 0, 2, True, 200)
        wait_for(lambda: any(name == "playback_started" for name, _ in events))
        time.sleep(1)
        middle = sum(len(block) for block in captured)
        engine.stop()
        time.sleep(.8)
        after = sum(len(block) for block in captured)
        observer.stop_stream()
        data = np.concatenate(captured) if captured else np.zeros((0, 2))
        rate = round(cable_capture.rate)
        sf.write(folder / "virtual-line-capture.wav", data, rate, subtype="PCM_16")
        mixed = data[max(before, middle - rate // 3):middle]
        stopped = data[max(middle, after - rate // 3):after]
        metrics = {"mix_440": amplitude(mixed, rate, 440), "mix_880": amplitude(mixed, rate, 880),
                   "after_stop_440": amplitude(stopped, rate, 440), "after_stop_880": amplitude(stopped, rate, 880)}
        metrics["passed"] = metrics["mix_440"] > .005 and metrics["mix_880"] > .004 and metrics["after_stop_440"] > .005 and metrics["after_stop_880"] < .001
        metrics.update({"output": cable_output.name, "capture": cable_capture.name, "events": events,
                        "mic_is_generated": True, "call_connected_after_stop": engine.snapshot()["call_connected"]})
        return metrics
    finally:
        if observer:
            observer.close()
        engine.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="Explicitly play quiet test tones and capture system/virtual audio; never physical microphone")
    parser.add_argument("--output", default="artifacts/hardware-smoke")
    args = parser.parse_args()
    if not args.run:
        parser.error("No hardware access without --run")
    folder = Path(args.output).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    generated = folder / "generated-880hz.wav"
    sf.write(generated, tone(880, 2), RATE, subtype="PCM_16")
    result = {"physical_microphone_opened": False}
    for name, operation in (("local_and_system", local_test), ("virtual_call", cable_test)):
        try:
            result[name] = operation(folder, generated)
        except Exception as error:
            result[name] = {"passed": False, "error": str(error)}
    (folder / "metrics.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if all(result[key].get("passed") for key in ("local_and_system", "virtual_call")) else 1


if __name__ == "__main__":
    raise SystemExit(main())
