"""v0.2 integration boundaries; all audio streams and Windows mutations are fakes."""
import os
from pathlib import Path
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PySide6.QtWidgets import QApplication
import soundfile as sf

from niulai_player.controller import Controller
from niulai_player.models import DeviceInfo
from niulai_player.preview import PreviewEngine
from niulai_player.system_settings import TemporarySystemSettings
from niulai_player.windows_audio import CablePair, Endpoint, WindowsAudioBackend


PAIR = CablePair(
    Endpoint("render-guid", "CABLE Input (VB-Audio Virtual Cable)", 1, "render"),
    Endpoint("capture-guid", "CABLE Output (VB-Audio Virtual Cable)", 1, "capture"),
)


class DelayedCallEngine(PreviewEngine):
    def devices(self):
        return [DeviceInfo("physical-mic", "Physical microphone", "input", is_default=True),
                DeviceInfo("speakers", "Speakers", "output", is_default=True),
                DeviceInfo("cable-output", PAIR.render.name, "output", is_virtual=True)]

    def connect_call(self, microphone, output):
        self.calls.append(("connect_call", microphone, output))

    def acknowledge(self, output="cable-output"):
        self.status.update(call_connected=True, call_output_id=output)
        self.on_event("call_connected", {"output_id": output})

    def disconnect_call(self):
        self.status.update(call_connected=False, call_output_id="")
        self.on_event("call_disconnected", {})


class FakeSystemSettings:
    def __init__(self):
        self.calls = []

    def recover(self):
        self.calls.append("recover")
        return {"ok": True, "status": "idle", "message": ""}

    def apply(self, identity):
        self.calls.append(("apply", identity))
        return {"ok": True, "status": "active", "message": "configured"}

    def restore(self, reason):
        self.calls.append(("restore", reason))
        return {"ok": True, "status": "restored", "message": "restored"}

    def close(self):
        self.calls.append("close")
        return {"ok": True, "status": "idle", "message": ""}


class FakeWindows:
    def __init__(self):
        self.default = "original-mic"
        self.available = {"original-mic", "capture-guid", "user-choice"}
        self.calls = []
        self.on_availability = None

    def get_default_communications_input(self):
        return self.default

    def endpoint_available(self, identity):
        if self.on_availability:
            self.on_availability(identity)
        return identity in self.available

    def set_default_communications_input(self, identity):
        self.calls.append(identity)
        self.default = identity


@pytest.fixture(scope="session")
def integration_qt_app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def integration_controller(tmp_path, monkeypatch, integration_qt_app):
    # Even the shared-user journal and discovery are isolated from real Windows.
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local-app-data"))
    monkeypatch.setattr(WindowsAudioBackend, "discover_standard_cable_pair", lambda _self: PAIR)
    controllers = []

    def create(system=None, engine_factory=DelayedCallEngine, factory=None):
        index = len(controllers)
        folder = tmp_path / f"audio-{index}"
        folder.mkdir()
        sf.write(folder / "sample.wav", np.zeros((96000, 2), dtype=np.float32), 48000, subtype="PCM_16")
        service = system or FakeSystemSettings()
        controller = Controller(tmp_path / f"profile-{index}", engine_factory=engine_factory,
                                folder=str(folder), no_hotkeys=True,
                                system_settings_factory=factory or (lambda _directory: service))
        controllers.append(controller)
        deadline = time.monotonic() + 5
        while controller._jobs or len(controller.items) != 1:
            integration_qt_app.processEvents()
            if time.monotonic() >= deadline:
                pytest.fail("Controller initialization did not finish")
            time.sleep(0.002)
        controller._poll.stop()
        controller._folder_poll.stop()
        controller._debounce.stop()
        return controller, service

    yield create
    for controller in reversed(controllers):
        controller.shutdown()
    integration_qt_app.processEvents()


def applied(service):
    return [entry for entry in service.calls if isinstance(entry, tuple) and entry[0] == "apply"]


@pytest.mark.parametrize("device", ["mic_device", "local_device", "system_device", "call_device"])
def test_pending_call_freezes_device_settings_and_persisted_selection(integration_controller, device):
    controller, service = integration_controller()
    original = controller.settings[device]
    persisted = controller.library.get_setting(device)
    controller.configure_call_temporarily()
    assert controller._call_connect_pending
    controller.save_settings({device: "different-device", "local_volume": 20})
    assert controller.settings[device] == original
    assert controller.library.get_setting(device) == persisted
    assert not applied(service)
    controller.engine.acknowledge()
    assert applied(service) == [("apply", PAIR.capture.id)]


def test_volume_changes_remain_available_while_connecting(integration_controller):
    controller, service = integration_controller()
    controller.configure_call_temporarily()
    controller.save_settings({"local_volume": 20, "send_volume": 35})
    assert controller.settings["local_volume"] == 20
    assert controller.library.get_setting("send_volume") == 35
    assert not applied(service)
    controller.engine.acknowledge()
    assert applied(service) == [("apply", PAIR.capture.id)]


def test_stale_ack_for_another_output_never_changes_system_input(integration_controller):
    controller, service = integration_controller()
    messages = []
    controller.message.connect(lambda text, error: messages.append((text, error)))
    controller.configure_call_temporarily()
    controller.engine.acknowledge("old-unrelated-output")
    assert not applied(service)
    assert not controller._temporary_call_requested
    assert any(error for _text, error in messages)


def test_mismatched_connected_snapshot_before_ack_never_changes_system_input(integration_controller):
    controller, service = integration_controller()
    controller.configure_call_temporarily()
    controller.engine.status.update(call_connected=True, call_output_id="old-unrelated-output")
    controller._poll_state()
    assert not applied(service)
    controller.engine.on_event("call_connected", {"output_id": "old-unrelated-output"})
    assert not applied(service)


def test_connection_uses_frozen_capture_endpoint_when_discovery_changes(integration_controller):
    controller, service = integration_controller()
    controller.configure_call_temporarily()
    controller._cable_pair = CablePair(PAIR.render, Endpoint("replacement-guid", PAIR.capture.name, 1, "capture"))
    controller._update_call_pair_state()
    assert controller.state["call_endpoint_id"] == "replacement-guid"
    controller.engine.acknowledge()
    assert applied(service) == [("apply", PAIR.capture.id)]


def test_other_profile_cannot_recover_a_live_owners_system_setting(integration_controller, tmp_path):
    backend = FakeWindows()
    shared = tmp_path / "local-app-data" / "NiuLaiPlayerPython"
    owner = TemporarySystemSettings(shared, backend)
    try:
        assert owner.apply(PAIR.capture.id)["ok"]
        created_paths = []

        def factory(directory):
            created_paths.append(Path(directory))
            return TemporarySystemSettings(directory, backend)

        controller, _ = integration_controller(factory=factory)
        assert created_paths == [shared]
        assert controller.state["temporary_config_status"] == "busy"
        assert backend.calls == [PAIR.capture.id]
        controller.shutdown()
        assert backend.default == PAIR.capture.id
        assert owner.journal.exists()
    finally:
        owner.close()
    assert backend.default == "original-mic"


def test_startup_restores_abandoned_journal_before_audio_engine_creation(integration_controller, tmp_path):
    backend = FakeWindows()
    shared = tmp_path / "local-app-data" / "NiuLaiPlayerPython"
    abandoned = TemporarySystemSettings(shared, backend)
    assert abandoned.apply(PAIR.capture.id)["ok"]
    # Closing only the handle simulates OS lock release after process death.
    abandoned._owner.close()
    abandoned._owner = None
    engine_start_defaults = []

    def engine_factory(callback):
        engine_start_defaults.append(backend.default)
        return DelayedCallEngine(callback)

    controller, _ = integration_controller(engine_factory=engine_factory,
                                           factory=lambda directory: TemporarySystemSettings(directory, backend))
    assert engine_start_defaults == ["original-mic"]
    assert controller.state["temporary_config_status"] == "restored"
    assert backend.calls == [PAIR.capture.id, "original-mic"]
    assert not abandoned.journal.exists()
    abandoned.close()


def test_recording_save_failure_still_attempts_restore_and_retains_recovery_journal(integration_controller, monkeypatch):
    backend = FakeWindows()
    controller, _ = integration_controller(factory=lambda directory: TemporarySystemSettings(directory, backend))
    controller.configure_call_temporarily()
    controller.engine.acknowledge()
    backend.available.remove("original-mic")
    monkeypatch.setattr(controller.engine, "close", lambda: (_ for _ in ()).throw(OSError("recording disk full")))
    errors = controller.shutdown()
    assert any("recording disk full" in error for error in errors)
    assert controller.system_settings.journal.exists()
    assert backend.default == PAIR.capture.id
    assert controller.state["temporary_config_status"] == "pending"
    backend.available.add("original-mic")
    recovered = TemporarySystemSettings(controller.system_settings.directory, backend)
    try:
        assert recovered.recover()["status"] == "restored"
        assert backend.default == "original-mic"
    finally:
        recovered.close()


def test_external_change_during_restore_availability_check_is_preserved(tmp_path):
    backend = FakeWindows()
    service = TemporarySystemSettings(tmp_path, backend)
    assert service.apply(PAIR.capture.id)["ok"]

    def user_selects_another_microphone(identity):
        if identity == "original-mic":
            backend.default = "user-choice"

    backend.on_availability = user_selects_another_microphone
    try:
        result = service.restore()
        assert result["status"] == "external_change"
        assert backend.default == "user-choice"
        assert backend.calls == [PAIR.capture.id]
        assert not service.journal.exists()
    finally:
        service.close()
