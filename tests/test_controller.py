"""Shared playback/recording behavior without audio hardware or native hotkeys."""
import os
from pathlib import Path
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PySide6.QtWidgets import QApplication
import soundfile as sf

from niulai_player.controller import Controller
from niulai_player.models import path_key
from niulai_player.preview import PreviewEngine
from niulai_player.windows_audio import Endpoint
from niulai_player.models import DeviceInfo
from types import SimpleNamespace


@pytest.fixture(scope="session")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


def settle(qt_app, predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while not predicate():
        qt_app.processEvents()
        if time.monotonic() >= deadline:
            pytest.fail("Qt controller did not reach the expected state")
        time.sleep(0.005)
    qt_app.processEvents()


def audio_file(folder, name, seconds=4):
    samples = np.zeros((round(48000 * seconds), 2), dtype=np.float32)
    path = folder / name
    sf.write(path, samples, 48000, subtype="PCM_16")
    return path


@pytest.fixture
def make_controller(tmp_path, qt_app):
    created = []

    def create(engine_factory=PreviewEngine, **kwargs):
        folder = tmp_path / f"music-{len(created)}"
        folder.mkdir()
        audio_file(folder, "a.wav")
        audio_file(folder, "b.wav")
        controller = Controller(tmp_path / f"data-{len(created)}", engine_factory=engine_factory,
                                folder=str(folder), no_hotkeys=True, **kwargs)
        created.append(controller)
        settle(qt_app, lambda: len(controller.items) == 2 and not controller._jobs)
        # Tests explicitly advance playback; background scans must not add timing noise.
        controller._folder_poll.stop()
        controller._debounce.stop()
        return controller

    yield create
    for controller in created:
        controller.shutdown()
    qt_app.processEvents()


def play_calls(controller):
    return [call for call in controller.engine.calls if call[0] == "play"]


def test_live_appearance_does_not_reset_running_audio(make_controller):
    controller = make_controller()
    controller.state.update(playing=True, recording=True, call_connected=True)
    before = list(controller.engine.calls)
    assert controller.set_theme("light")
    assert controller.set_mini_on_top(False)
    assert controller.library.get_setting("theme", None) == "light"
    assert controller.library.get_setting("mini_on_top", None) is False
    assert controller.engine.calls == before
    assert all(controller.state[key] for key in ("playing", "recording", "call_connected"))
    assert not controller.set_theme("unknown")
    assert controller.state["theme"] == "light"


def test_failed_appearance_write_keeps_actual_state(make_controller, monkeypatch):
    controller = make_controller()
    before = dict(controller.settings)
    audio_calls = list(controller.engine.calls)
    messages, states = [], []
    controller.message.connect(lambda text, error: messages.append((text, error)))
    controller.state_changed.connect(lambda: states.append(dict(controller.state)))
    def fail(*args):
        raise OSError("disk write failed")
    monkeypatch.setattr(controller.library, "set_setting", fail)
    assert not controller.set_theme("light")
    assert not controller.set_mini_on_top(False)
    assert controller.settings == before
    assert controller.engine.calls == audio_calls
    assert len(states) == 2
    assert all(error and "disk write failed" in text for text, error in messages)


def test_system_defaults_follow_external_change_and_avoid_virtual_input(make_controller, qt_app):
    controller = make_controller()
    controller._system_follow.stop()
    selected = {"render": Endpoint("out", "Headphones", 1, "render"),
                "capture": Endpoint("in", "Physical microphone", 1, "capture")}
    devices = [DeviceInfo("hp", "Headphones", "output"),
               DeviceInfo("mic", "Physical microphone", "input"),
               DeviceInfo("loop", "Headphones [Loopback]", "loopback"),
               DeviceInfo("virtual", "CABLE Output", "input", is_virtual=True)]
    controller.engine.devices = lambda: devices
    calls = []
    controller.engine.follow_devices = lambda *args: calls.append(args)
    controller.system_settings = SimpleNamespace(backend=SimpleNamespace(get_default_endpoint=lambda flow: selected[flow]),
                                                restore=lambda _: {}, close=lambda: {})
    controller.follow_system_devices()
    settle(qt_app, lambda: not controller._system_follow_busy)
    assert calls == [("hp", "mic", "loop")]
    selected["capture"] = Endpoint("cable", "CABLE Output", 1, "capture")
    controller.follow_system_devices()
    settle(qt_app, lambda: not controller._system_follow_busy)
    assert controller.settings["mic_device"] == "mic"
    assert "声音回路" in controller.state["audio_follow_note"]
    assert len(calls) == 1


def test_player_changes_real_system_device_and_reports_failure(make_controller, qt_app):
    controller = make_controller()
    controller._system_follow.stop()
    endpoints = [Endpoint("stable-id", "Headphones", 1, "render")]
    controller._devices = [DeviceInfo("pa-id", "Headphones", "output")]
    writes = []
    backend = SimpleNamespace(render_endpoints=lambda: endpoints,
                              set_default_endpoint=lambda *args: writes.append(args))
    controller.system_settings = SimpleNamespace(backend=backend, restore=lambda _: {}, close=lambda: {})
    controller.change_system_device("local_device", "pa-id")
    settle(qt_app, lambda: not controller._jobs)
    assert writes == [("stable-id", "render")]
    endpoints.append(Endpoint("duplicate", "Headphones", 1, "render"))
    messages = []
    controller.message.connect(lambda text, error: messages.append((text, error)))
    controller.change_system_device("local_device", "pa-id")
    settle(qt_app, lambda: not controller._jobs)
    assert len(writes) == 1 and any(error and "唯一匹配" in text for text, error in messages)


def complete(controller, item=None, token=None):
    item = item or controller.get_item(controller.state["path"])
    token = controller._token if token is None else token
    controller.engine.status.update(playing=False, paused=False, position=item.end)
    controller.engine.on_event("playback_finished", {"path": item.path, "token": token})
    controller._poll_state()


def test_two_immediate_clicks_stop_current_audio_without_waiting_for_poll(make_controller):
    controller = make_controller()
    item = controller.items[0]
    controller.trigger(item.path)
    controller.trigger(item.path)
    assert len(play_calls(controller)) == 1
    assert controller.engine.calls[-1] == ("stop",)
    assert not controller.engine.status["playing"]


def test_seek_moves_active_clip_without_changing_range_or_microphone(make_controller):
    controller = make_controller()
    item = controller.items[0]
    controller.set_range(item.path, 1, 3)
    controller.trigger(item.path)
    controller._poll_state()
    controller.engine.status["call_connected"] = True
    controller._poll_state()
    previous_token = controller._token
    assert controller.seek(2.25)
    assert controller.engine.status["position"] == 2.25
    assert (item.start, item.end) == (1, 3)
    assert controller.engine.status["call_connected"]
    assert controller._token > previous_token
    # A completion already queued before the seek must not advance the list.
    controller.set_mode(2)
    controller._on_audio_event("playback_finished", {"path": item.path, "token": previous_token})
    assert controller.state["path"] == item.path
    assert controller.state["playing"]


def test_seek_ignores_idle_and_nonfinite_positions(make_controller):
    controller = make_controller()
    assert controller.seek(2) is False
    item = controller.items[0]
    controller.trigger(item.path)
    assert controller.seek(float("nan")) is False
    assert controller.seek(float("inf")) is False
    assert controller.seek(-10)
    assert controller.state["position"] == item.start


def test_share_switch_does_not_disconnect_existing_microphone_route(make_controller, monkeypatch):
    controller = make_controller()
    controller.state["call_connected"] = True
    controller.engine.status["call_connected"] = True
    sent = []
    monkeypatch.setattr(controller.engine, "set_send", sent.append)
    monkeypatch.setattr(controller.engine, "disconnect_call", lambda: pytest.fail("share is not disconnect"))
    assert controller.set_share(True)
    assert controller.set_share(False)
    assert sent == [True, False]
    assert controller.engine.status["call_connected"]


def test_batch_delete_keeps_microphone_and_reports_individual_failures(make_controller, qt_app, monkeypatch):
    controller = make_controller()
    first, second = controller.items
    controller.trigger(first.path)
    controller.engine.status["call_connected"] = True
    controller._poll_state()
    messages = []
    controller.message.connect(lambda text, error: messages.append((text, error)))

    def stage_batch(items):
        # Simulate one staged file and one locked file, keeping the assertion
        # about routing independent of the Windows Recycle Bin backend.
        assert {item.key for item in items} == {first.key, second.key}
        Path(first.path).unlink()
        return {"batch_id": "test-batch", "folder": controller.folder,
                "deleted": [first.path], "failed": [{"path": second.path, "error": "locked"}], "count": 1}

    monkeypatch.setattr(controller.library, "stage_delete_batch", stage_batch)
    controller.delete_items([first.path, second.path, first.path])
    settle(qt_app, lambda: not controller._jobs and not controller._scan_busy)
    assert not Path(first.path).exists()
    assert Path(second.path).exists()
    assert not controller.engine.status["playing"]
    assert controller.engine.status["call_connected"]
    assert any(error and "locked" in text and "1" in text for text, error in messages)


def test_clicking_another_item_switches_and_updates_completion_token(make_controller):
    controller = make_controller()
    first, second = controller.items
    controller.trigger(first.path)
    first_token = controller._token
    controller._poll_state()
    controller.trigger(second.path)
    controller._poll_state()
    assert controller.state["path"] == second.path
    assert controller.state["playing"]
    assert play_calls(controller)[-1][1] == second.path
    assert controller._token > first_token


def test_range_edit_keeps_playback_position_inside_and_jumps_if_outside(make_controller):
    controller = make_controller()
    item = controller.items[0]
    controller.trigger(item.path)
    controller.engine.status["position"] = 1.5
    controller._poll_state()
    token = controller._token
    controller.set_range(item.path, 1, 3)
    controller._poll_state()
    assert controller.state["playing"] and controller.state["position"] == 1.5
    assert controller._token == token and len(play_calls(controller)) == 1
    controller.set_range(item.path, 2, 3.5)
    controller._poll_state()
    assert controller.state["playing"] and controller.state["position"] == 2
    saved = controller.library.scan(controller.folder)[0]
    assert (saved.start, saved.end) == (2, 3.5)


@pytest.mark.parametrize("mode", [0, 1, 2, 3])
def test_completion_obeys_each_play_mode(make_controller, mode):
    controller = make_controller()
    first, second = controller.items
    controller.set_mode(mode)
    controller.trigger(first.path)
    controller._poll_state()
    complete(controller, first)
    if mode == 0:
        assert len(play_calls(controller)) == 1
        assert not controller.state["playing"]
    elif mode == 1:
        assert len(play_calls(controller)) == 2
        assert play_calls(controller)[-1][1] == first.path
        assert controller.state["playing"]
    else:
        assert len(play_calls(controller)) == 2
        assert play_calls(controller)[-1][1] == second.path
        complete(controller, second)
        if mode == 2:
            assert len(play_calls(controller)) == 2
            assert not controller.state["playing"]
        else:
            assert len(play_calls(controller)) == 3
            assert play_calls(controller)[-1][1] == first.path
            assert controller.state["playing"]


@pytest.mark.parametrize("mode", [1, 2, 3])
def test_stop_invalidates_completion_and_cannot_restart_loop(make_controller, mode):
    controller = make_controller()
    item = controller.items[0]
    controller.set_mode(mode)
    controller.trigger(item.path)
    controller._poll_state()
    stale_token = controller._token
    controller.stop()
    complete(controller, item, token=stale_token)
    assert len(play_calls(controller)) == 1
    assert not controller.state["playing"]


def test_old_item_completion_cannot_replace_new_item(make_controller):
    controller = make_controller()
    first, second = controller.items
    controller.set_mode(1)
    controller.trigger(first.path)
    old_token = controller._token
    controller.trigger(second.path)
    controller._poll_state()
    controller._on_audio_event("playback_finished", {"path": first.path, "token": old_token})
    assert len(play_calls(controller)) == 2
    assert controller.state["path"] == second.path
    assert controller.state["playing"]


def test_pause_resume_keeps_position_and_does_not_restart_clip(make_controller):
    controller = make_controller()
    item = controller.items[0]
    controller.trigger(item.path)
    controller.engine.status["position"] = 1.25
    controller._poll_state()
    controller.pause()
    controller._poll_state()
    assert controller.state["paused"]
    controller.pause()
    controller._poll_state()
    assert not controller.state["paused"]
    assert controller.state["position"] == 1.25
    assert len(play_calls(controller)) == 1


def test_folder_switch_changes_hotkey_context_and_preserves_mic_call(make_controller, tmp_path, qt_app):
    controller = make_controller()
    original_folder = controller.folder
    original = controller.items[0]
    controller.set_hotkey(original.path, "Ctrl+Alt+1")
    controller.set_range(original.path, 1, 2.5)
    controller.engine.status["call_connected"] = True
    controller._poll_state()
    controller.trigger(original.path)
    controller._poll_state()
    other = tmp_path / "other-folder"
    other.mkdir()
    other_file = audio_file(other, "a.wav")
    controller.set_folder(str(other))
    assert "item:" + original.path not in controller.hotkeys.bindings
    settle(qt_app, lambda: len(controller.items) == 1 and not controller._scan_busy)
    assert controller.items[0].key == path_key(other_file)
    assert controller.items[0].hotkey == ""
    assert not controller.engine.status["playing"]
    assert controller.engine.status["call_connected"]
    controller.set_hotkey(str(other_file), "Ctrl+Alt+1")
    assert controller.hotkeys.bindings["item:" + str(other_file)] == "Ctrl+Alt+1"
    controller.set_folder(original_folder)
    settle(qt_app, lambda: len(controller.items) == 2 and not controller._scan_busy)
    restored = controller.get_item(original.path)
    assert (restored.start, restored.end, restored.hotkey) == (1, 2.5, "Ctrl+Alt+1")
    assert "item:" + str(other_file) not in controller.hotkeys.bindings
    assert controller.hotkeys.bindings["item:" + original.path] == "Ctrl+Alt+1"


def test_stale_scan_result_cannot_replace_new_folder(make_controller, tmp_path, monkeypatch):
    controller = make_controller()
    old_items = controller.items.copy()
    queued = []
    monkeypatch.setattr(controller, "_submit", lambda function, success=None, failure=None: queued.append((function, success, failure)))
    controller.reload()
    old_success = queued[-1][1]
    other = tmp_path / "new-folder"
    other.mkdir()
    audio_file(other, "new.wav")
    controller.set_folder(str(other))
    old_success(old_items)
    assert controller.folder == str(other)
    assert controller.items == []
    current_job = queued[-1]
    current_job[1](current_job[0]())
    assert [item.name for item in controller.items] == ["new"]


class RecordingPreview(PreviewEngine):
    """Controllable recording acknowledgement with no microphone access."""

    def start_recording(self, folder, source, microphone, system):
        self.calls.append(("start_recording", folder, source, microphone, system))

    def acknowledge_recording(self):
        self.status["recording"] = True
        self.on_event("state", {})

    def stop_recording(self):
        self.calls.append(("stop_recording",))
        self.status["recording"] = False
        self.on_event("recording_stopped", {})


def test_recording_pending_and_active_states_lock_folder_and_source(make_controller, tmp_path, qt_app):
    controller = make_controller(RecordingPreview)
    original_folder = controller.folder
    other = tmp_path / "record-target"
    other.mkdir()
    audio_file(other, "one.wav")
    messages = []
    controller.message.connect(lambda message, error: messages.append((message, error)))
    controller.start_recording("都录制")
    assert controller._record_command_pending
    assert controller.library.get_setting("recording_source") == "都录制"
    controller.set_folder(str(other))
    assert controller.folder == original_folder
    controller.start_recording("电脑")
    assert len([call for call in controller.engine.calls if call[0] == "start_recording"]) == 1
    assert controller.settings["recording_source"] == "都录制"
    controller.engine.acknowledge_recording()
    assert controller.state["recording"] and not controller._record_command_pending
    controller.set_folder(str(other))
    assert controller.folder == original_folder
    assert any("先停止录音" in message for message, error in messages if error)
    controller.stop_recording()
    assert not controller.state["recording"]
    controller.set_folder(str(other))
    settle(qt_app, lambda: len(controller.items) == 1 and not controller._scan_busy)
    assert controller.folder == str(other)


def test_recording_error_releases_pending_folder_lock(make_controller, tmp_path, qt_app):
    controller = make_controller(RecordingPreview)
    other = tmp_path / "after-error"
    other.mkdir()
    audio_file(other, "one.wav")
    controller.start_recording("麦克风")
    controller.engine.on_event("error", {"message": "simulated capture failure"})
    assert not controller._record_command_pending
    controller.set_folder(str(other))
    settle(qt_app, lambda: len(controller.items) == 1 and not controller._scan_busy)
    assert controller.folder == str(other)


def test_conflicting_hotkey_change_keeps_old_binding_and_saved_metadata(make_controller):
    controller = make_controller()
    first, second = controller.items
    assert controller.set_hotkey(first.path, "Ctrl+Alt+1") is True
    assert controller.set_hotkey(second.path, "Ctrl+Alt+2") is True
    assert controller.set_hotkey(second.path, "Ctrl+Alt+1") is False
    assert second.hotkey == "Ctrl+Alt+2"
    assert controller.hotkeys.bindings["item:" + second.path] == "Ctrl+Alt+2"
    stored = next(item for item in controller.library.scan(controller.folder) if item.key == second.key)
    assert stored.hotkey == "Ctrl+Alt+2"


def test_range_save_error_returns_false_for_editor_and_keeps_previous_range(make_controller, monkeypatch):
    controller = make_controller()
    item = controller.items[0]
    assert controller.set_range(item.path, 1, 2.5) is True
    errors = []
    controller.message.connect(lambda text, error: errors.append((text, error)))
    def cannot_write(_):
        raise PermissionError("disk write denied")
    monkeypatch.setattr(controller.library, "save_item", cannot_write)
    assert controller.set_range(item.path, 0, 2) is False
    assert (item.start, item.end) == (1, 2.5)
    assert controller.reset_range(item.path) is False
    assert (item.start, item.end) == (1, 2.5)
    assert any(error and "disk write denied" in text for text, error in errors)


class HardwareSemanticsPreview(PreviewEngine):
    def pause(self):
        self.status["paused"] = not self.status["paused"]
        self.status["playing"] = not self.status["paused"]


def test_resume_honors_real_engine_paused_snapshot_without_restarting(make_controller):
    controller = make_controller(HardwareSemanticsPreview)
    item = controller.items[0]
    controller.trigger(item.path)
    controller.engine.status["position"] = 1.75
    controller._poll_state()
    controller.pause()
    controller._poll_state()
    assert controller.state["paused"] and not controller.state["playing"]
    controller.pause()
    controller._poll_state()
    assert controller.state["playing"] and not controller.state["paused"]
    assert controller.state["position"] == 1.75
    assert len(play_calls(controller)) == 1


class DecodingPreview(PreviewEngine):
    def play(self, path, start, end, send=False, token=0):
        self.calls.append(("play", path, start, end, token))
        # Match a real background decoder: the selected path can be present
        # before the playback cursor is ready.
        self.status.update(path=path, playing=False, paused=False, position=start)


def test_second_click_during_background_decode_cancels_requested_clip(make_controller):
    controller = make_controller(DecodingPreview)
    item = controller.items[0]
    controller.trigger(item.path)
    controller._poll_state()
    controller.trigger(item.path)
    assert len(play_calls(controller)) == 1
    assert controller.engine.calls[-1] == ("stop",)


def test_pending_record_start_blocks_device_reconfiguration(make_controller):
    controller = make_controller(RecordingPreview)
    old_device = controller.settings["mic_device"]
    controller.start_recording("麦克风")
    controller.save_settings({"mic_device": "different-input"})
    assert controller.settings["mic_device"] == old_device
    assert controller.library.get_setting("mic_device", old_device) == old_device


def test_failed_appearance_save_rolls_back_in_memory_item(make_controller, monkeypatch):
    controller = make_controller()
    item = controller.items[0]
    old_color, old_avatar = item.color, item.avatar
    monkeypatch.setattr(controller.library, "save_item", lambda _: (_ for _ in ()).throw(OSError("disk full")))
    controller.update_appearance(item.path, color="#112233", avatar="🎷")
    assert (item.color, item.avatar) == (old_color, old_avatar)


def test_dialog_settings_failure_rolls_back_all_database_and_memory_values(make_controller):
    controller = make_controller()
    controller.library.set_settings(controller.settings)
    controller.library._db.executescript("""
        CREATE TRIGGER fail_send_setting BEFORE UPDATE ON settings
        WHEN NEW.key = 'send_volume' AND NEW.value = '42'
        BEGIN SELECT RAISE(ABORT, 'simulated failure'); END;
    """)
    old = controller.settings.copy()
    controller.save_settings({"local_volume": 33, "send_volume": 42})
    assert controller.settings == old
    assert controller.library.get_setting("local_volume") == old["local_volume"]
    assert controller.library.get_setting("send_volume") == old["send_volume"]


class StaleSnapshotPreview(PreviewEngine):
    def play(self, path, start, end, send=False, token=0):
        if self.status["playing"]:
            # A queued switch has not reached the audio thread yet: the snapshot
            # still describes the previous clip for one poll cycle.
            self.calls.append(("play", path, start, end, token))
        else:
            super().play(path, start, end, send, token)


def test_old_playing_snapshot_cannot_override_new_pending_clip(make_controller):
    controller = make_controller(StaleSnapshotPreview)
    first, second = controller.items
    controller.trigger(first.path)
    controller._poll_state()
    controller.trigger(second.path)
    controller._poll_state()
    assert controller.state["path"] == second.path
    controller.trigger(second.path)
    assert len(play_calls(controller)) == 2
    assert controller.engine.calls[-1] == ("stop",)


class CallPreview(PreviewEngine):
    def devices(self):
        from niulai_player.models import DeviceInfo
        return [DeviceInfo("mic", "Physical microphone", "input", is_default=True),
                DeviceInfo("cable", "CABLE Input (VB-Audio Virtual Cable)", "output", is_virtual=True)]

    def set_send(self, value):
        self.calls.append(("set_send", value))

    def connect_call(self, microphone, output):
        self.calls.append(("connect_call", microphone, output))

    def acknowledge_call(self):
        self.status["call_connected"] = True
        self.on_event("call_connected", {"output_id": "cable"})

    def disconnect_call(self):
        super().disconnect_call()
        self.on_event("call_disconnected", {})

    def test_call_output(self):
        self.calls.append(("test_call_output",))


class TemporarySettingsPreview:
    def __init__(self):
        self.calls = []

    def recover(self):
        self.calls.append("recover")
        return {"ok": True, "status": "idle", "message": ""}

    def apply(self, endpoint):
        self.calls.append(("apply", endpoint))
        return {"ok": True, "status": "active", "message": "temporary input enabled"}

    def restore(self, reason):
        self.calls.append(("restore", reason))
        return {"ok": True, "status": "restored", "message": "original input restored"}

    def close(self):
        self.calls.append("close")


def temporary_controller(make_controller, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(Controller, "_refresh_system_pair", lambda self: None)
    system = TemporarySettingsPreview()
    controller = make_controller(CallPreview, system_settings_factory=lambda directory: system)
    controller._cable_pair = SimpleNamespace(capture=SimpleNamespace(id="stable-cable-guid", name="CABLE Output"))
    controller._update_call_pair_state()
    return controller, system


def test_call_connect_enables_clip_send_and_test_tone_keeps_current_item(make_controller):
    controller = make_controller(CallPreview)
    controller.connect_call()
    assert controller.settings["send_to_call"] is True
    controller.engine.acknowledge_call()
    controller.trigger(controller.items[0].path)
    selected, token = controller.state["path"], controller._token
    controller.test_call_output()
    assert controller.engine.calls[-1][0] != "test_call_output"
    assert (controller.state["path"], controller._token) == (selected, token)
    controller.stop()
    controller.test_call_output()
    assert controller.engine.calls[-1] == ("test_call_output",)
    controller.set_send(False)
    assert controller.state["call_diagnostic"] == "仅麦克风送入通话"
    assert controller.state["call_connected"]


def test_temporary_config_changes_only_after_route_ack_and_restores_on_disconnect(make_controller, monkeypatch):
    controller, system = temporary_controller(make_controller, monkeypatch)
    assert system.calls == ["recover"]
    controller.configure_call_temporarily()
    assert not any(isinstance(call, tuple) and call[0] == "apply" for call in system.calls)
    controller.engine.acknowledge_call()
    assert ("apply", "stable-cable-guid") in system.calls
    assert controller.state["temporary_config_status"] == "active"
    controller.disconnect_call()
    assert ("restore", "disconnect") in system.calls


def test_failed_call_route_cannot_apply_temporary_input(make_controller, monkeypatch):
    controller, system = temporary_controller(make_controller, monkeypatch)
    controller.configure_call_temporarily()
    controller.engine.on_event("error", {"message": "device could not open"})
    assert not controller._temporary_call_requested
    assert not controller._call_connect_pending
    assert not any(isinstance(call, tuple) and call[0] == "apply" for call in system.calls)


def test_cancel_pending_temporary_config_does_not_apply_late_ack(make_controller, monkeypatch):
    controller, system = temporary_controller(make_controller, monkeypatch)
    controller.configure_call_temporarily()
    controller.disconnect_call()
    controller.engine.acknowledge_call()
    assert not any(isinstance(call, tuple) and call[0] == "apply" for call in system.calls)


def test_shutdown_restores_settings_even_when_record_save_raises(make_controller, monkeypatch):
    controller, system = temporary_controller(make_controller, monkeypatch)
    controller.configure_call_temporarily()
    controller.engine.acknowledge_call()
    monkeypatch.setattr(controller.engine, "close", lambda: (_ for _ in ()).throw(OSError("disk full")))
    errors = controller.shutdown()
    assert any("disk full" in error for error in errors)
    assert ("restore", "exit") in system.calls and "close" in system.calls
    assert controller.shutdown() == errors


def test_language_persists_without_restarting_audio(make_controller):
    controller = make_controller()
    controller.trigger(controller.items[0].path)
    calls = controller.engine.calls.copy()
    assert controller.set_language("en")
    assert controller.state["ui_language"] == "en"
    assert controller.library.get_setting("ui_language") == "en"
    assert controller.engine.calls == calls
    assert not controller.set_language("unknown")
