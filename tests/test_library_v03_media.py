"""Portable-library regressions; fixtures never use personal recordings."""
from dataclasses import replace
from pathlib import Path
import shutil
import threading

import numpy as np
import pytest
import soundfile as sf
from PySide6.QtGui import QImage

from niulai_player.library import Library
from niulai_player.media_decode import default_decoder
from niulai_player.portable import PackageError, package_path, read_package, write_package


def audio(path, seconds=4):
    sf.write(path, np.sin(np.arange(48000 * seconds) / 48000 * 440 * 2 * np.pi) * .1, 48000)
    return path


def picture(path):
    image = QImage(200, 100, QImage.Format.Format_RGB32)
    image.fill(0x335566)
    assert image.save(str(path))
    return path


@pytest.fixture
def setup(tmp_path):
    folder = tmp_path / "music"
    folder.mkdir()
    instance = Library(tmp_path / "profile")
    yield instance, folder
    instance.close()


def test_aac_mp4_content_named_wav_can_play_edit_waveform_and_export(setup):
    library, folder = setup
    media = folder / "实际MP4但叫WAV.wav"
    process = default_decoder()._spawn("ffmpeg", ["-v", "error", "-f", "lavfi", "-i",
        "sine=frequency=440:sample_rate=48000", "-t", "3", "-c:a", "aac", "-f", "mp4", str(media)])
    _, error = process.communicate(timeout=20)
    assert process.returncode == 0, error
    assert b"ftyp" in media.read_bytes()[:32]
    item = library.scan(str(folder))[0]
    assert item.playable and 2.9 < item.duration < 3.1
    assert item.audio_streams[0]["codec"] == "aac"
    item.start, item.end = .5, 1.75
    library.save_item(item)
    assert library.flush_pending() == []
    assert max(library.waveform(item.path, bins=16)) > .05
    result = library.export_segment(item, str(folder))
    assert sf.info(result).duration == pytest.approx(1.25, abs=1 / 48000)


def test_import_batch_keeps_success_and_copies_unique_companion(setup, tmp_path):
    library, folder = setup
    source = audio(tmp_path / "声音.wav")
    config = write_package(source, dict(name="我的标题", start=1, end=3, color="#123456", hotkey="Ctrl+F9"), {})
    audio(folder / source.name)
    invalid = tmp_path / "broken.mp3"
    invalid.write_bytes(b"broken")
    result = library.import_batch([str(source), str(invalid), str(source)], str(folder))
    assert len(result["imported"]) == len(result["failed"]) == len(result["skipped"]) == 1
    imported = Path(result["imported"][0])
    assert imported.name == "声音 (2).wav"
    assert package_path(imported).is_file()
    restored = read_package(imported, tmp_path / "assets")
    assert restored.identifier != config.identifier
    assert restored.properties["name"] == "我的标题"
    assert imported.read_bytes() == source.read_bytes()
    assert not list(folder.glob("*.pending"))


def test_same_folder_drag_is_skipped_and_orphan_package_name_is_reserved(setup, tmp_path):
    library, folder = setup
    existing = audio(folder / "one.wav")
    assert library.import_batch([str(existing)], str(folder))["skipped"] == [str(existing)]
    package_path(folder / "other.wav").write_bytes(b"preserved-orphan")
    source = audio(tmp_path / "other.wav")
    result = library.import_batch([str(source)], str(folder))
    assert Path(result["imported"][0]).name == "other (2).wav"
    assert package_path(folder / "other.wav").read_bytes() == b"preserved-orphan"


def test_stale_background_crop_does_not_overwrite_range_or_shortcut(setup, tmp_path):
    library, folder = setup
    media = audio(folder / "one.wav")
    current = library.scan(str(folder))[0]
    old = replace(current)
    current.start, current.end, current.hotkey = 1, 3, "Ctrl+F8"
    library.save_item(current)
    source = picture(tmp_path / "background.png")
    library.apply_background_crop(old, str(source), (10, 5, 160, 60))
    assert library.flush_pending() == []
    restored = read_package(media, tmp_path / "new-assets")
    assert (restored.properties["start"], restored.properties["end"], restored.properties["hotkey"]) == (1, 3, "Ctrl+F8")
    assert restored.properties["background_crop"] == [10, 5, 160, 60]
    assert set(restored.assets) == {"background", "background_source"}


@pytest.mark.parametrize("content", [b"broken-zip", b"future"])
def test_invalid_companion_preserved_and_does_not_disable_media(setup, content):
    library, folder = setup
    media = audio(folder / "one.wav")
    package_path(media).write_bytes(content)
    item = library.scan(str(folder))[0]
    assert item.playable and "不可用" in item.portable_status
    item.start, item.end = 1, 3
    with pytest.raises(PackageError):
        library.save_item(item)
    assert library.flush_pending() == []
    assert package_path(media).read_bytes() == content


def test_missing_audio_track_uses_default_without_overwriting_package(setup):
    library, folder = setup
    media = audio(folder / "one.wav")
    write_package(media, dict(name="one", start=1, end=3, audio_stream=999), {})
    original = package_path(media).read_bytes()
    item = library.scan(str(folder))[0]
    assert item.playable and item.audio_stream is None
    assert "默认音轨" in item.portable_status
    assert package_path(media).read_bytes() == original


def test_pair_rename_preserves_package_and_complete_metadata(setup):
    library, folder = setup
    media = audio(folder / "one.wav")
    item = library.scan(str(folder))[0]
    item.start, item.end, item.hotkey = 1, 3, "Alt+F8"
    library.save_item(item)
    assert library.flush_pending() == []
    renamed = Path(library.rename(item, "新名字"))
    assert library.flush_pending() == []
    assert not media.exists() and not package_path(media).exists()
    restored = read_package(renamed, library.data_dir / "assets")
    assert restored.media["name"] == renamed.name
    assert restored.properties["hotkey"] == "Alt+F8"
    assert restored.properties["name"] == "新名字"


def test_partial_recycle_keeps_config_and_recovery_copy(setup, tmp_path, monkeypatch):
    library, folder = setup
    media = audio(folder / "one.wav")
    item = library.scan(str(folder))[0]
    item.start, item.end = 1, 3
    library.save_item(item)
    assert library.flush_pending() == []
    original = package_path(media).read_bytes()
    calls = []

    def recycle(value):
        calls.append(value)
        if value.endswith(".newlife"):
            raise OSError("recycle unavailable")
        Path(value).rename(tmp_path / "recycled.wav")

    monkeypatch.setattr("niulai_player.library.send2trash", recycle)
    with pytest.raises(OSError, match="配置包回收失败"):
        library.delete(item)
    assert len(calls) == 2 and not media.exists()
    assert package_path(media).read_bytes() == original
    assert next((library.data_dir / "recovery").glob("*.newlife")).read_bytes() == original


def test_bad_index_json_is_reported_without_killing_writer_or_hanging_close(setup):
    library, folder = setup
    media = audio(folder / "one.wav")
    item = library.scan(str(folder))[0]
    with library._lock, library._db:
        library._db.execute("UPDATE items SET audio_streams='broken-json' WHERE path=?", (item.key,))
    library._enqueue_package(item.key)
    assert library.flush_pending(timeout=3)
    assert library._writer.is_alive()
    with pytest.raises(OSError, match="配置保存失败"):
        library.close()


def test_concurrent_package_verification_does_not_overwrite_completed_new_edit(setup, monkeypatch):
    library, folder = setup
    media = audio(folder / "one.wav")
    item = library.scan(str(folder))[0]
    item.start, item.end = 0, 2
    library.save_item(item)
    assert library.flush_pending() == []
    original = read_package
    paused, resume = threading.Event(), threading.Event()

    def slow_read(*args, **kwargs):
        state = original(*args, **kwargs)
        paused.set()
        assert resume.wait(5)
        return state

    monkeypatch.setattr("niulai_player.library.portable.read_package", slow_read)
    result = []
    worker = threading.Thread(target=lambda: result.extend(library.scan(str(folder))))
    worker.start()
    assert paused.wait(5)
    item.start, item.end = 1, 3
    library.save_item(item)
    assert library.flush_pending() == []
    resume.set()
    worker.join(5)
    assert not worker.is_alive()
    assert (result[0].start, result[0].end) == (1, 3)
    assert (original(media, library.data_dir / "assets").properties["start"], original(media, library.data_dir / "assets").properties["end"]) == (1, 3)
    # A stale read must not replace the writer's new revision either, or the
    # next explicit edit would fail despite having the correct current fields.
    item.start, item.end = .25, 2.25
    library.save_item(item)
    assert library.flush_pending() == []
    assert original(media, library.data_dir / "assets").properties["start"] == .25


@pytest.mark.parametrize("failure", [PermissionError("write denied"), OSError("disk full")])
def test_verification_cannot_clear_completed_write_failure_or_report_saved(setup, monkeypatch, failure):
    library, folder = setup
    media = audio(folder / "one.wav")
    item = library.scan(str(folder))[0]
    item.start, item.end = 0, 2
    library.save_item(item)
    assert library.flush_pending() == []
    original_bytes = package_path(media).read_bytes()
    original_read = read_package
    paused, resume = threading.Event(), threading.Event()
    result, read_errors = [], []

    def paused_read(*args, **kwargs):
        state = original_read(*args, **kwargs)
        paused.set()
        assert resume.wait(5)
        return state

    def denied_write(*args, **kwargs):
        raise failure

    def scan():
        try:
            result.extend(library.scan(str(folder)))
        except Exception as error:
            read_errors.append(error)

    with monkeypatch.context() as patches:
        patches.setattr("niulai_player.library.portable.read_package", paused_read)
        patches.setattr("niulai_player.library.portable.write_package", denied_write)
        reader = threading.Thread(target=scan)
        reader.start()
        try:
            assert paused.wait(5)
            item.start, item.end = 1, 3
            library.save_item(item)
            failed = library.flush_pending(timeout=3)
            assert len(failed) == 1 and "配置保存失败" in failed[0]
        finally:
            resume.set()
            reader.join(5)
        assert not reader.is_alive() and not read_errors
        assert (result[0].start, result[0].end) == (1, 3)
        assert library.flush_pending() == failed
        assert result[0].portable_status == failed[0]
        assert "配置已保存" not in result[0].portable_status
        assert package_path(media).read_bytes() == original_bytes

    # Successful refresh of the still-valid old package is only a read, and
    # must retain the write failure instead of causing a false confirmation.
    refreshed = library.scan(str(folder))[0]
    assert refreshed.playable
    assert refreshed.portable_status == failed[0]
    assert library.flush_pending() == failed
    assert package_path(media).read_bytes() == original_bytes

    # A new user edit explicitly retries the write. Only its durable success
    # resolves the error and allows the saved status to be shown again.
    library.save_item(result[0])
    assert library.flush_pending() == []
    saved = original_read(media, library.data_dir / "assets")
    assert (saved.properties["start"], saved.properties["end"]) == (1, 3)
    assert library.scan(str(folder))[0].portable_status == "配置已保存"


def test_refresh_missing_sidecar_preserves_failed_first_write_without_automatic_retry(setup, monkeypatch):
    library, folder = setup
    media = audio(folder / "one.wav")
    item = library.scan(str(folder))[0]
    writes = []

    def denied_write(*args, **kwargs):
        writes.append(args)
        raise PermissionError("write denied")

    with monkeypatch.context() as patches:
        patches.setattr("niulai_player.library.portable.write_package", denied_write)
        item.start, item.end = 1, 3
        library.save_item(item)
        failed = library.flush_pending(timeout=3)
        version = library._edit_versions[item.key]
        assert len(failed) == 1
        refreshed = library.scan(str(folder))[0]
        assert refreshed.portable_status == failed[0]
        assert library.flush_pending() == failed
        assert library._edit_versions[item.key] == version
        assert len(writes) == 1
        assert not package_path(media).exists()
    library.save_item(refreshed)
    assert library.flush_pending() == []
    assert read_package(media, library.data_dir / "assets").properties["start"] == 1
