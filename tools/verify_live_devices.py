"""Read-only real Windows discovery with a setter that always refuses mutation."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import time
import uuid

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from PySide6.QtWidgets import QApplication
from niulai_player.controller import Controller
from niulai_player.system_settings import TemporarySystemSettings
from niulai_player.windows_audio import WindowsAudioBackend


class ReadOnlyBackend(WindowsAudioBackend):
    def set_default_communications_input(self, identity):
        raise AssertionError("Read-only verification must never change Windows settings")


def main():
    root = Path(__file__).resolve().parents[1]
    output = root / "artifacts" / "device-verification" / uuid.uuid4().hex[:8]
    folder = output / "empty-audio-folder"
    folder.mkdir(parents=True)
    app = QApplication([])
    backend = ReadOnlyBackend()
    original = backend.get_default_communications_input()
    controller = Controller(output / "config", folder=str(folder), no_hotkeys=True,
                            system_settings_factory=lambda _: TemporarySystemSettings(output / "temporary-settings", backend))
    try:
        deadline = time.monotonic() + 10
        while controller._jobs or controller._device_refresh_busy or controller._system_pair_busy:
            app.processEvents()
            if time.monotonic() > deadline:
                raise TimeoutError("Device discovery did not finish")
            time.sleep(.005)
        assert controller.state["call_ready"]
        assert controller.state["call_pair_ready"]
        assert controller.state["call_endpoint_id"]
        assert not controller.state["recording"] and not controller.state["call_connected"]
        result = {"call_pair_ready": True, "expected_input": controller.state["call_input_name"],
                  "physical_microphone_opened": False, "capability": backend.capability()}
    finally:
        errors = controller.shutdown()
        assert not errors, errors
    assert backend.get_default_communications_input() == original
    result["default_communications_input_unchanged"] = True
    report = output / "results.json"
    report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
