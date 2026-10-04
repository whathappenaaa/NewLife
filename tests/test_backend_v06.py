"""v0.6 recovery and feedback contracts using only temporary user files."""
from dataclasses import replace
import json
import os
from pathlib import Path
import shutil
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication
import soundfile as sf

from niulai_player import file_undo
from niulai_player.controller import Controller
from niulai_player.library import Library
from niulai_player.models import path_key
from niulai_player.portable import package_path, read_package
from niulai_player.preview import PreviewEngine


@pytest.fixture(scope="session")
def app():
    return QApplication.instance() or QApplication([])


def audio(path):
    sf.write(path, np.zeros((48000 * 4, 2), dtype=np.float32), 48000, subtype="PCM_16")
    return path


def picture(path):
    image = QImage(160, 120, QImage.Format.Format_RGB32)
    image.fill(QColor("#336699"))
    assert image.save(str(path))
    return path


def settle(app, controller, condition=lambda: True):
    deadline = time.monotonic() + 12
    while controller._jobs or controller._scan_busy or not condition():
        app.processEvents()
        if time.monotonic() >= deadline:
            pytest.fail("v0.6 controller did not settle")
        time.sleep(.005)
    app.processEvents()


@pytest.fixture
def library(tmp_path):
    folder = tmp_path / "music"
    folder.mkdir()
    instance = Library(tmp_path / "profile")
    yield instance, folder
    instance.close()


@pytest.fixture
def controller(tmp_path, app, monkeypatch):
    folder = tmp_path / "music"
    folder.mkdir()
    audio(folder / "first.wav")
    audio(folder / "second.wav")
    # No test sends real files into the Windows recycle bin.
    recycle = tmp_path / "recycle"
    recycle.mkdir()
    monkeypatch.setattr(file_undo, "send2trash", lambda path: shutil.move(path, recycle / Path(path).name))
    instance = Controller(tmp_path / "profile", folder=str(folder), engine_factory=PreviewEngine, no_hotkeys=True)
    settle(app, instance)
    instance._folder_poll.stop()
    instance._system_follow.stop()
    yield instance
    instance.shutdown()
    app.processEvents()


def configured(library, folder, tmp_path):
    media = audio(folder / "my.wav")
    item = library.scan(str(folder))[0]
    image = picture(tmp_path / "picture.png")
    item.name, item.start, item.end, item.hotkey = "Custom title", .8, 3.5, "Ctrl+Alt+9"
    item.color = "#346789"
    library.save_item(item)
    library.apply_avatar_crop(item, str(image), (20, 10, 100, 100))
    library.apply_background_crop(item, str(image), (0, 10, 160, 80))
    assert library.flush_pending() == []
    return item


def test_stage_and_undo_preserve_audio_package_assets_and_position(library, tmp_path):
    instance, folder = library
    item = configured(instance, folder, tmp_path)
    other = audio(folder / "other.wav")
    items = instance.scan(str(folder))
    instance.save_order([next(value for value in items if value.path == str(other)),
                         next(value for value in items if value.key == item.key)])
    item = instance.scan(str(folder))[1]
    media_bytes, package_bytes = Path(item.path).read_bytes(), package_path(item.path).read_bytes()
    batch = instance.stage_delete_batch([item])
    assert batch["count"] == 1 and not batch["failed"]
    assert not Path(item.path).exists() and not package_path(item.path).exists()
    assert [value.path for value in instance.scan(str(folder))] == [str(other)]
    assert instance.deletion_batches()["batches"][0]["batch_id"] == batch["batch_id"]
    result = instance.undo_delete_batch(batch["batch_id"])
    assert result["restored"] == [item.path] and result["count"] == 0
    restored = instance.scan(str(folder))[1]
    assert restored.name == "Custom title" and restored.hotkey == "Ctrl+Alt+9"
    assert (restored.start, restored.end) == (.8, 3.5)
    assert restored.avatar_crop == (20, 10, 100, 100)
    assert restored.background_crop == (0, 10, 160, 80)
    assert restored.color == "#346789"
    assert Path(item.path).read_bytes() == media_bytes
    assert package_path(item.path).read_bytes() == package_bytes
    assert not instance.deletion_batches()["batches"]


def test_undo_conflict_preserves_both_and_is_retryable(library, tmp_path):
    instance, folder = library
    item = configured(instance, folder, tmp_path)
    original = Path(item.path).read_bytes()
    batch = instance.stage_delete_batch([item])
    # A new same-named file must remain untouched by undo.
    Path(item.path).write_bytes(b"new user's file")
    result = instance.undo_delete_batch(batch["batch_id"])
    assert result["count"] == 1 and "未覆盖" in result["failed"][0]["error"]
    assert Path(item.path).read_bytes() == b"new user's file"
    assert (Path(batch["batch_id"]) / "0" / "audio").read_bytes() == original
    Path(item.path).rename(folder / "new-user-file.wav")
    assert instance.undo_delete_batch(batch["batch_id"])["count"] == 0
    assert Path(item.path).read_bytes() == original


def test_config_name_conflict_prevents_audio_half_restore(library, tmp_path):
    instance, folder = library
    item = configured(instance, folder, tmp_path)
    batch = instance.stage_delete_batch([item])
    package_path(item.path).write_bytes(b"a new configuration")
    result = instance.undo_delete_batch(batch["batch_id"])
    assert result["failed"] and result["count"] == 1
    assert not Path(item.path).exists()
    assert package_path(item.path).read_bytes() == b"a new configuration"


def test_recycle_failure_keeps_pair_and_recovery_record(library, tmp_path, monkeypatch):
    instance, folder = library
    item = configured(instance, folder, tmp_path)
    original_config = package_path(item.path).read_bytes()
    batch = instance.stage_delete_batch([item])
    monkeypatch.setattr(file_undo, "send2trash", lambda _: (_ for _ in ()).throw(OSError("recycle unavailable")))
    with pytest.raises(OSError, match="recycle unavailable"):
        instance.recycle_delete_batch(batch["batch_id"])
    directory = Path(batch["batch_id"])
    assert (directory / "0" / "audio").is_file()
    assert (directory / "0" / "config.newlife").read_bytes() == original_config
    assert instance.undo_delete_batch(batch["batch_id"])["count"] == 0


def test_cleanup_failure_reports_successful_restore_without_stale_undo(controller, app, monkeypatch):
    item = controller.items[0]
    controller.set_range(item.path, 1, 3)
    settle(app, controller)
    original = Path(item.path).read_bytes()
    controller.delete_items([item.path])
    settle(app, controller)
    batch = controller._undo_batches[-1]
    journal = Path(batch["batch_id"]) / "journal.json"
    messages = []
    controller.message.connect(lambda text, error: messages.append((text, error)))
    original_unlink = Path.unlink

    def denied_unlink(path, *args, **kwargs):
        if path == journal:
            raise PermissionError("journal cleanup locked")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", denied_unlink)
    assert controller.undo_delete()
    settle(app, controller)
    assert Path(item.path).read_bytes() == original and package_path(item.path).is_file()
    assert not controller.state["delete_undo_available"] and controller.state["delete_undo_count"] == 0
    assert not controller.library.deletion_batches()["batches"]
    assert journal.exists()
    assert any("已恢复 1" in text and "清理未完成" in text and not error for text, error in messages)


def test_batch_partial_failure_rolls_back_pair_and_keeps_other_success(library, tmp_path, monkeypatch):
    instance, folder = library
    item = configured(instance, folder, tmp_path)
    other = audio(folder / "other.wav")
    items = instance.scan(str(folder))
    original = file_undo._move
    original_config = package_path(item.path).read_bytes()

    def denied(source, destination):
        if source == package_path(item.path):
            raise PermissionError("configuration locked")
        original(source, destination)

    monkeypatch.setattr(file_undo, "_move", denied)
    result = instance.stage_delete_batch(items)
    assert result["deleted"] == [str(other)] and result["count"] == 1
    assert "configuration locked" in result["failed"][0]["error"]
    assert Path(item.path).is_file() and package_path(item.path).read_bytes() == original_config
    assert instance.undo_delete_batch(result["batch_id"])["restored"] == [str(other)]


def test_previous_process_journal_restores_without_original_library_object(tmp_path):
    folder = tmp_path / "music"
    folder.mkdir()
    path = audio(folder / "clip.wav")
    profile = tmp_path / "profile"
    first = Library(profile)
    item = first.scan(str(folder))[0]
    item.start, item.end, item.hotkey = 1, 3, "Alt+F8"
    first.save_item(item)
    first.flush_pending()
    batch = first.stage_delete_batch([item])
    first.close()  # Library close does not implicitly throw away undo storage.
    second = Library(profile)
    try:
        recovered = second.deletion_batches()["batches"]
        assert recovered[0]["count"] == 1
        assert second.undo_delete_batch(batch["batch_id"])["count"] == 0
        restored = second.scan(str(folder))[0]
        assert (restored.start, restored.end, restored.hotkey) == (1, 3, "Alt+F8")
        assert path.exists()
    finally:
        second.close()


def test_crash_during_restore_recognizes_already_moved_identical_audio(library, tmp_path):
    instance, folder = library
    item = configured(instance, folder, tmp_path)
    batch = instance.stage_delete_batch([item])
    directory = Path(batch["batch_id"])
    # A process ended after moving the media but before moving the config.
    file_undo._move(directory / "0" / "audio", Path(item.path))
    result = instance.undo_delete_batch(batch["batch_id"])
    assert result["restored"] == [item.path] and not result["failed"]
    assert package_path(item.path).exists()


def test_scan_cannot_publish_configuration_between_pair_restore_moves(library, tmp_path, monkeypatch):
    instance, folder = library
    item = configured(instance, folder, tmp_path)
    original_package = package_path(item.path).read_bytes()
    batch = instance.stage_delete_batch([item])
    media_restored, resume, scan_started, scan_finished = (threading.Event() for _ in range(4))
    original_move = file_undo._move
    results, errors = [], []

    def paused_move(source, destination):
        original_move(source, destination)
        if destination == Path(item.path):
            media_restored.set()
            assert resume.wait(5)

    def restore():
        try:
            results.append(instance.undo_delete_batch(batch["batch_id"]))
        except Exception as error:
            errors.append(error)

    def scan():
        scan_started.set()
        try:
            instance.scan(str(folder))
        except Exception as error:
            errors.append(error)
        finally:
            scan_finished.set()

    monkeypatch.setattr(file_undo, "_move", paused_move)
    restorer = threading.Thread(target=restore)
    scanner = threading.Thread(target=scan)
    restorer.start()
    try:
        assert media_restored.wait(5)
        assert Path(item.path).exists() and not package_path(item.path).exists()
        scanner.start()
        assert scan_started.wait(5)
        assert not scan_finished.wait(.08)
        assert not package_path(item.path).exists()
    finally:
        resume.set()
        restorer.join(5)
        if scanner.ident is not None:
            scanner.join(5)
    assert not errors and not restorer.is_alive() and not scanner.is_alive()
    assert results[0]["count"] == 0 and not results[0]["failed"]
    assert instance.flush_pending() == []
    assert package_path(item.path).read_bytes() == original_package
    restored = instance.scan(str(folder))[0]
    assert (restored.start, restored.end, restored.hotkey) == (.8, 3.5, "Ctrl+Alt+9")


def test_historical_recovery_survives_start_and_normal_exit(tmp_path, app, monkeypatch):
    folder = tmp_path / "music"
    folder.mkdir()
    path = audio(folder / "saved.wav")
    profile = tmp_path / "profile"
    old = Library(profile)
    batch = old.stage_delete_batch(old.scan(str(folder)))
    old.close()
    recycled = []
    monkeypatch.setattr(file_undo, "send2trash", recycled.append)
    first = Controller(profile, folder=str(folder), engine_factory=PreviewEngine, no_hotkeys=True)
    settle(app, first)
    assert first.state["delete_undo_available"] and first.state["delete_undo_count"] == 1
    first._expire_delete_undo()
    settle(app, first)
    assert first.shutdown() == []
    app.processEvents()
    assert not recycled and (Path(batch["batch_id"]) / "0" / "audio").exists()
    second = Controller(profile, folder=str(folder), engine_factory=PreviewEngine, no_hotkeys=True)
    try:
        settle(app, second)
        assert second.undo_delete()
        settle(app, second)
        assert path.exists() and not second.state["delete_undo_available"]
    finally:
        second.shutdown()
        app.processEvents()
    assert not recycled


def test_reset_appearance_is_independent_and_survives_fresh_profile(library, tmp_path):
    instance, folder = library
    item = configured(instance, folder, tmp_path)
    background = item.background
    reset = instance.reset_appearance(item, "avatar")
    assert (reset.avatar, reset.avatar_source, reset.avatar_crop) == ("🐮", "", None)
    assert reset.background == background and reset.hotkey == item.hotkey
    assert instance.flush_pending() == []
    state = read_package(item.path, tmp_path / "fresh-assets")
    assert "avatar" not in state.assets and "avatar_source" not in state.assets
    assert state.properties["avatar_crop"] is None
    assert set(state.assets) == {"background", "background_source"}
    reset = instance.reset_appearance(item, "background")
    assert (reset.color, reset.background, reset.background_source, reset.background_crop) == ("#202832", "", "", None)
    assert (reset.start, reset.end, reset.hotkey) == (.8, 3.5, "Ctrl+Alt+9")
    assert instance.flush_pending() == []
    state = read_package(item.path, tmp_path / "fresh-assets-2")
    assert not state.assets and state.properties["background_crop"] is None


@pytest.mark.parametrize("kind", ["avatar", "background"])
def test_old_crop_cannot_reappear_after_reset(library, tmp_path, kind):
    instance, folder = library
    item = configured(instance, folder, tmp_path)
    revision = instance.begin_asset_edit(item.path, kind)
    old = replace(item)
    instance.reset_appearance(item, kind)
    method = getattr(instance, "apply_" + kind + "_crop")
    crop = (0, 0, 100, 100) if kind == "avatar" else (0, 0, 160, 100)
    assert method(old, str(tmp_path / "picture.png"), crop, expected_revision=revision) is False
    assert instance.flush_pending() == []
    restored = instance.scan(str(folder))[0]
    assert getattr(restored, kind + "_source") == ""
    assert getattr(restored, kind + "_crop") is None


def test_import_summary_short_and_details_keep_actual_results(controller, app, tmp_path):
    outside = audio(tmp_path / "wrong-name.wav")
    # The content is WAV but the user changed its extension to MP3.
    renamed = outside.with_suffix(".mp3")
    outside.rename(renamed)
    bad = tmp_path / "corrupt.mp3"
    bad.write_bytes(b"not an audio file")
    reports, messages = [], []
    controller.import_finished.connect(reports.append)
    controller.message.connect(lambda text, error: messages.append((text, error)))
    controller.import_files([str(renamed), str(bad), str(renamed)])
    settle(app, controller)
    result = reports[-1]
    assert result["folder"] == controller.folder
    assert len(result["imported"]) == len(result["skipped"]) == len(result["failed"]) == 1
    assert result["failed"][0]["path"] == str(bad) and result["failed"][0]["error"]
    assert Path(result["imported"][0]).read_bytes() == renamed.read_bytes()
    summary = next(text for text, _ in messages if text.startswith("已导入"))
    assert "\n" not in summary and "corrupt.mp3" not in summary and "失败 1" in summary


def test_controller_delete_undo_preserves_microphone_and_does_not_play(controller, app):
    first, second = controller.items
    controller.trigger(first.path)
    controller.engine.status["call_connected"] = True
    controller._poll_state()
    controller.delete_items([first.path, first.path])
    settle(app, controller)
    assert controller.state["delete_undo_available"] and controller.state["delete_undo_count"] == 1
    assert not controller.state["delete_busy"] and controller.engine.status["call_connected"]
    assert not controller.engine.status["playing"] and not Path(first.path).exists()
    plays = [call for call in controller.engine.calls if call[0] == "play"]
    assert controller.undo_delete()
    settle(app, controller)
    assert not controller.state["delete_undo_available"] and Path(first.path).exists()
    assert controller.engine.status["call_connected"]
    assert [call for call in controller.engine.calls if call[0] == "play"] == plays


def test_controller_expiration_recycles_whole_pair(controller, app):
    item = controller.items[0]
    controller.set_range(item.path, 1, 3)
    settle(app, controller)
    controller.delete_items([item.path])
    settle(app, controller)
    batch = controller._undo_batches[-1]
    original_dir = Path(batch["batch_id"])
    controller._undo_timer.stop()
    controller._expire_delete_undo()
    settle(app, controller)
    assert not controller.state["delete_undo_available"] and not original_dir.exists()
    recycled = Path(controller.folder).parent / "recycle" / original_dir.name
    assert (recycled / "0" / "audio").exists()
    assert (recycled / "0" / "config.newlife").exists()


def test_recycle_failure_remains_undoable_and_shutdown_records_failure(controller, app, monkeypatch):
    item = controller.items[0]
    controller.delete_items([item.path])
    settle(app, controller)
    batch = Path(controller._undo_batches[-1]["batch_id"])
    monkeypatch.setattr(file_undo, "send2trash", lambda _: (_ for _ in ()).throw(OSError("recycle locked")))
    controller._undo_timer.stop()
    controller._expire_delete_undo()
    settle(app, controller)
    assert controller.state["delete_undo_available"] and batch.exists()
    errors = controller.shutdown()
    assert any("recycle locked" in error for error in errors)
    assert (batch / "0" / "audio").exists() and (batch / "journal.json").exists()


def test_saved_recording_notification_waits_for_actual_item_and_never_plays(controller, app):
    path = audio(Path(controller.folder) / "recording.wav")
    notifications = []
    controller.recording_completed.connect(notifications.append)
    controller._on_audio_event("recording_saved", {"path": str(path)})
    settle(app, controller)
    assert controller.state["recent_recording_path"] == str(path)
    assert controller.state["recent_recording_revision"] == 1
    assert controller.get_item(str(path)) is not None
    controller._on_audio_event("clip_saved", {"path": str(path)})
    settle(app, controller)
    assert controller.state["recent_recording_revision"] == 2
    assert notifications == [str(path), str(path)]
    assert not [call for call in controller.engine.calls if call[0] == "play"]


def test_controller_reset_range_while_playing_keeps_other_configuration(controller, app):
    item = controller.items[0]
    controller.set_range(item.path, 1, 3)
    controller.update_appearance(item.path, color="#225577")
    settle(app, controller)
    controller.trigger(item.path)
    assert controller.reset_range(item.path)
    settle(app, controller)
    assert (item.start, item.end, item.color) == (0, 4, "#225577")
    assert controller.engine.status["playing"]
    state = read_package(item.path, Path(controller.data_dir) / "fresh-assets")
    assert (state.properties["start"], state.properties["end"]) == (0, 4)
