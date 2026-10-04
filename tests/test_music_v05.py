"""Portable MUSIC defaults and denied writes, with no real device/system access."""
from __future__ import annotations

import os
from pathlib import Path
import runpy
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

import niulai_player.controller as controller_module
from niulai_player.controller import Controller
from niulai_player.library import Library
import niulai_player.library as library_module
from niulai_player.models import AudioItem
import niulai_player.music_folder as music_folder
from niulai_player.preview import PreviewEngine


class FakeEngine(PreviewEngine):
    def recover_recordings(self, folder):
        self.calls.append(("recover_recordings", folder))

    def start_recording(self, *args):
        self.calls.append(("start_recording", *args))


@pytest.fixture(scope="module")
def qt_app():
    yield QApplication.instance() or QApplication([])


def settle(qt_app, controller):
    deadline = time.monotonic() + 3
    while controller._jobs:
        qt_app.processEvents()
        if time.monotonic() > deadline:
            pytest.fail("MUSIC controller jobs did not finish")
        time.sleep(.005)
    qt_app.processEvents()


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    root = tmp_path / "source-project"
    root.mkdir()
    unrelated = tmp_path / "other-cwd"
    unrelated.mkdir()
    monkeypatch.chdir(unrelated)
    monkeypatch.setattr(music_folder, "__file__", str(root / "src" / "niulai_player" / "music_folder.py"))
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "internal-assets"), raising=False)
    return root, unrelated


@pytest.fixture
def make_controller(tmp_path, qt_app, monkeypatch, sandbox):
    created = []
    # These tests exercise selection and writes; decoder/hardware tests live elsewhere.
    monkeypatch.setattr(Library, "scan", lambda self, folder: [])

    def create(*, folder="", saved=""):
        data = tmp_path / f"settings-{len(created)}"
        if saved:
            library = Library(data)
            library.set_setting("folder", str(saved))
            library.close()
        controller = Controller(data, engine_factory=FakeEngine, folder=str(folder), no_hotkeys=True,
                                enable_system_settings=False)
        created.append(controller)
        controller._folder_poll.stop()
        controller._system_follow.stop()
        messages = []
        controller.message.connect(lambda text, error: messages.append((text, error)))
        settle(qt_app, controller)
        return controller, messages

    yield create
    for controller in created:
        controller.shutdown()
    qt_app.processEvents()


def deny_write(_folder):
    raise PermissionError("simulated readonly MUSIC")


def test_source_root_ignores_working_directory_and_meipass(sandbox):
    root, unrelated = sandbox
    assert music_folder.application_root() == root
    assert music_folder.default_music_folder() == root / "MUSIC"
    assert music_folder.default_music_folder().parent != unrelated


def test_frozen_root_is_executable_parent(sandbox, tmp_path, monkeypatch):
    executable = tmp_path / "portable-folder" / "NewLife.exe"
    monkeypatch.setattr(sys, "frozen", True)
    monkeypatch.setattr(sys, "executable", str(executable))
    assert music_folder.application_root() == executable.parent
    assert music_folder.default_music_folder() == executable.parent / "MUSIC"


def test_first_source_start_creates_music_at_project_root(make_controller, sandbox):
    root, unrelated = sandbox
    controller, messages = make_controller()
    assert controller.folder == str(root / "MUSIC")
    assert controller.library.get_setting("folder") == controller.folder
    assert controller.state["folder_writable"]
    assert not (unrelated / "MUSIC").exists()
    assert not messages


def test_first_frozen_start_creates_music_beside_executable(make_controller, tmp_path, monkeypatch):
    portable_root = tmp_path / "portable-folder"
    monkeypatch.setattr(sys, "frozen", True)
    monkeypatch.setattr(sys, "executable", str(portable_root / "NewLife.exe"))
    controller, _ = make_controller()
    assert controller.folder == str(portable_root / "MUSIC")
    assert (portable_root / "MUSIC").is_dir()


def test_saved_custom_folder_is_kept_without_moving_files(make_controller, sandbox, tmp_path):
    chosen = tmp_path / "my-sounds"
    chosen.mkdir()
    original = chosen / "recording.wav"
    original.write_bytes(b"original user recording")
    controller, messages = make_controller(saved=chosen)
    assert controller.folder == str(chosen)
    assert original.read_bytes() == b"original user recording"
    assert not (sandbox[0] / "MUSIC").exists()
    assert not messages


def test_command_line_folder_overrides_valid_saved_folder(make_controller, tmp_path):
    saved, requested = tmp_path / "saved", tmp_path / "requested"
    saved.mkdir()
    requested.mkdir()
    controller, _ = make_controller(folder=requested, saved=saved)
    assert controller.folder == str(requested)
    assert controller.library.get_setting("folder") == str(requested)
    assert saved.is_dir()


@pytest.mark.parametrize("selection", ["saved", "requested"])
def test_missing_selection_reports_fallback_to_music(make_controller, sandbox, tmp_path, selection):
    kwargs = {"saved" if selection == "saved" else "folder": tmp_path / "disconnected-drive"}
    controller, messages = make_controller(**kwargs)
    assert controller.folder == str(sandbox[0] / "MUSIC")
    assert any(error and "不存在或无法访问" in text and "MUSIC" in text for text, error in messages)


def test_readonly_music_remains_playable_but_blocks_all_output(make_controller, sandbox, monkeypatch):
    monkeypatch.setattr(controller_module, "check_folder_writable", deny_write)
    controller, messages = make_controller()
    assert controller.folder == str(sandbox[0] / "MUSIC")
    assert not controller.state["folder_writable"]
    assert any(error and "请选择可写文件夹" in text for text, error in messages)
    assert not any(call[0] == "recover_recordings" for call in controller.engine.calls)
    media = Path(controller.folder) / "readable.wav"
    media.write_bytes(b"readable audio bytes")
    item = AudioItem(str(media), "readable", 2, peaks=[.1])
    controller.items = [item]
    controller.trigger(item.path)
    assert any(call[0] == "play" for call in controller.engine.calls)
    jobs = []
    monkeypatch.setattr(controller, "_submit", lambda *args: jobs.append(args))
    controller.start_recording("麦克风")
    controller.import_files(["source.wav"])
    controller.export(item.path)
    assert not jobs
    assert not any(call[0] == "start_recording" for call in controller.engine.calls)
    assert not controller._record_command_pending


def test_uncreatable_default_does_not_write_to_cwd(make_controller, sandbox, monkeypatch):
    root, unrelated = sandbox
    original_mkdir = Path.mkdir

    def guarded_mkdir(path, *args, **kwargs):
        if path == root / "MUSIC":
            raise PermissionError("simulated protected program directory")
        return original_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", guarded_mkdir)
    controller, messages = make_controller()
    assert not controller.folder
    assert not controller.state["folder_writable"]
    assert controller.library.get_setting("folder", "") == ""
    assert any(error and "无法创建程序旁的 MUSIC" in text for text, error in messages)
    jobs = []
    monkeypatch.setattr(controller, "_submit", lambda *args: jobs.append(args))
    controller.items = [AudioItem(str(unrelated / "selected.wav"), "selected", 2)]
    controller.start_recording("麦克风")
    controller.import_files(["source.wav"])
    controller.export(controller.items[0].path)
    assert not jobs
    assert not any(call[0] == "start_recording" for call in controller.engine.calls)
    assert list(unrelated.iterdir()) == []


def test_folder_choice_save_error_does_not_crash_or_claim_success(make_controller, tmp_path, monkeypatch):
    controller, messages = make_controller()
    selected = tmp_path / "second-folder"
    selected.mkdir()
    monkeypatch.setattr(controller.library, "set_setting", lambda *args: deny_write(None))
    controller.set_folder(str(selected))
    assert controller.folder == str(selected)
    assert any(error and "文件夹选择保存失败" in text for text, error in messages)


def test_metadata_write_denial_happens_before_any_saved_edit(make_controller, monkeypatch):
    controller, _ = make_controller()
    item = AudioItem(str(Path(controller.folder) / "sound.wav"), "sound", 2)
    monkeypatch.setattr(library_module, "check_folder_writable", deny_write)
    with pytest.raises(OSError, match="请选择可写文件夹"):
        controller.library.save_item(item)
    assert not controller.library._pending


def test_real_write_probe_cleans_up_and_propagates_denial(tmp_path, monkeypatch):
    music_folder.check_folder_writable(tmp_path)
    assert list(tmp_path.iterdir()) == []
    monkeypatch.setattr(music_folder.tempfile, "TemporaryFile", lambda **kwargs: deny_write(None))
    with pytest.raises(PermissionError):
        music_folder.check_folder_writable(tmp_path)


def test_packaging_copies_only_direct_designated_music_files(tmp_path):
    helper = Path(__file__).resolve().parents[1] / "tools" / "package_music.py"
    copy_music = runpy.run_path(str(helper))["copy_music"]
    source, destination = tmp_path / "MUSIC", tmp_path / "release" / "MUSIC"
    source.mkdir()
    names = ("说明.txt", "tone.WAV", "video.mp4", "tone.WAV.newlife", "logs.txt", "library.sqlite3",
             "recording.wav.part", "temporary.pending", "unused.png")
    for name in names:
        (source / name).write_bytes(name.encode("utf-8"))
    nested = source / "nested"
    nested.mkdir()
    (nested / "hidden.wav").write_bytes(b"do not copy subfolders")
    copied = copy_music(source, destination)
    expected = {"说明.txt", "tone.WAV", "video.mp4", "tone.WAV.newlife"}
    assert {entry.name for entry in copied} == expected
    assert {entry.name for entry in destination.iterdir()} == expected
    assert {entry.name for entry in source.iterdir()} == set(names) | {"nested"}
    (source / "tone.WAV").unlink()
    (source / "tone.WAV.newlife").unlink()
    copy_music(source, destination)
    assert {entry.name for entry in destination.iterdir()} == {"说明.txt", "video.mp4"}
    before = {entry.name for entry in source.iterdir()}
    with pytest.raises(ValueError, match="different directories"):
        copy_music(source, source / ".")
    assert {entry.name for entry in source.iterdir()} == before
