"""v0.3 portable media flows, with no real audio capture or OS-setting writes."""
import os
from pathlib import Path
import shutil
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication
import soundfile as sf

from niulai_player.controller import Controller
from niulai_player.portable import package_path
from niulai_player.preview import PreviewEngine


@pytest.fixture(scope="session")
def v03_app():
    return QApplication.instance() or QApplication([])


def settle(app, controller, predicate=lambda: True):
    deadline = time.monotonic() + 15
    while controller._jobs or controller._scan_busy or not predicate():
        app.processEvents()
        if time.monotonic() >= deadline:
            pytest.fail("portable controller flow did not settle")
        time.sleep(.005)
    app.processEvents()


def write_audio(path):
    axis = np.arange(48000 * 4) / 48000
    sf.write(path, .025 * np.sin(axis * 2 * np.pi * 440), 48000, subtype="PCM_16")


def create(folder, profile, app):
    controller = Controller(profile, folder=str(folder), engine_factory=PreviewEngine, no_hotkeys=True)
    settle(app, controller)
    return controller


def test_two_files_restore_range_original_images_and_shortcut_in_fresh_profile(tmp_path, v03_app):
    first, second = tmp_path / "computer-a", tmp_path / "computer-b"
    first.mkdir(); second.mkdir()
    media = first / "声音.wav"
    write_audio(media)
    image = QImage(200, 100, QImage.Format.Format_RGB32)
    image.fill(QColor("#338899"))
    picture = tmp_path / "上传图片.png"
    assert image.save(str(picture))
    original = media.read_bytes()
    old = create(first, tmp_path / "profile-a", v03_app)
    new = None
    try:
        old.set_range(str(media), 1.25, 3.75)
        old.set_hotkey(str(media), "Ctrl+Alt+7")
        old.set_avatar(str(media), str(picture), (0, 0, 100, 100))
        old.set_background(str(media), str(picture), (20, 10, 160, 60))
        settle(v03_app, old)
        assert not old.library.flush_pending()
        assert package_path(media).is_file()
        assert media.read_bytes() == original
        shutil.copy2(media, second / media.name)
        shutil.copy2(package_path(media), package_path(second / media.name))
        old.shutdown()
        v03_app.processEvents()
        # Only the media and its package were copied, never the old app profile.
        new = create(second, tmp_path / "profile-b", v03_app)
        restored = new.items[0]
        assert (restored.start, restored.end, restored.hotkey) == (1.25, 3.75, "Ctrl+Alt+7")
        assert restored.avatar_crop == (0, 0, 100, 100)
        assert restored.background_crop == (20, 10, 160, 60)
        for source in (restored.avatar, restored.avatar_source, restored.background, restored.background_source):
            resolved = Path(new.resolve_asset(source))
            assert resolved.is_file()
            assert "profile-a" not in str(resolved)
        new.trigger(restored.path)
        new._poll_state()
        assert new.seek(2.5)
        assert new.state["position"] == 2.5
    finally:
        old.shutdown()
        if new:
            new.shutdown()
        v03_app.processEvents()


def test_drag_import_carries_package_and_disables_only_duplicate_shortcut(tmp_path, v03_app):
    source, target = tmp_path / "source", tmp_path / "target"
    source.mkdir(); target.mkdir()
    media = source / "声音.wav"
    write_audio(media)
    original = create(source, tmp_path / "source-profile", v03_app)
    imported = None
    try:
        original.set_range(str(media), 1, 3)
        original.set_hotkey(str(media), "Ctrl+Alt+8")
        settle(v03_app, original)
        imported = create(target, tmp_path / "target-profile", v03_app)
        imported.import_files([str(media)])
        settle(v03_app, imported, lambda: len(imported.items) == 1)
        imported.import_files([str(media)])
        settle(v03_app, imported, lambda: len(imported.items) == 2)
        assert len({item.path for item in imported.items}) == 2
        assert all((item.start, item.end) == (1, 3) for item in imported.items)
        assert all(package_path(item.path).is_file() for item in imported.items)
        assert all(item.hotkey == "Ctrl+Alt+8" for item in imported.items)
        assert len(imported.state["hotkey_conflicts"]) == 1
        assert len(imported.hotkeys.bindings) == 1
        imported.save_settings({"local_volume": 55})
        assert imported.library.get_setting("local_volume") == 55
        assert len(imported.hotkeys.bindings) == 1
        assert media.is_file() and package_path(media).is_file()
    finally:
        original.shutdown()
        if imported:
            imported.shutdown()
        v03_app.processEvents()


def test_shutdown_still_restores_system_settings_when_library_close_fails(tmp_path, v03_app, monkeypatch):
    folder = tmp_path / "music"
    folder.mkdir()
    write_audio(folder / "test.wav")
    calls = []

    class Settings:
        def recover(self):
            return {"ok": True, "status": "idle"}
        def restore(self, reason):
            calls.append(reason)
            return {"ok": True, "status": "restored"}
        def close(self):
            calls.append("close")
            return {"ok": True, "status": "idle"}

    controller = Controller(tmp_path / "profile", folder=str(folder), engine_factory=PreviewEngine,
                            no_hotkeys=True, system_settings_factory=lambda _: Settings())
    settle(v03_app, controller)
    original_close = controller.library.close

    def failed_close():
        original_close()
        raise OSError("simulated portable save failure")

    monkeypatch.setattr(controller.library, "close", failed_close)
    errors = controller.shutdown()
    assert "exit" in calls and "close" in calls
    assert any("simulated portable save failure" in error for error in errors)
    v03_app.processEvents()
