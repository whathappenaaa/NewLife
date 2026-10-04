from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
import os
import sqlite3
import struct

import numpy as np
import pytest
import soundfile as sf

from niulai_player.library import Library
from niulai_player.models import path_key


@pytest.fixture
def library(tmp_path):
    instance = Library(tmp_path / "data")
    yield instance
    instance.close()


@pytest.fixture
def folder(tmp_path):
    path = tmp_path / "music"
    path.mkdir()
    return path


def write_audio(path, seconds=3, amplitude=0.3, rate=48000, **kwargs):
    frames = round(seconds * rate)
    data = amplitude * np.sin(np.arange(frames) * (2 * np.pi * 440 / rate))
    sf.write(path, np.column_stack([data, -data]), rate, **kwargs)
    return path


def test_flat_folder_scan_ignores_other_directories_and_pending_files(library, folder):
    write_audio(folder / "one.wav")
    nested = folder / "nested"
    nested.mkdir()
    write_audio(nested / "hidden.wav")
    write_audio(folder.parent / "outside.wav")
    (folder / ".niulai-recording.pending").write_bytes(b"partial audio")
    (folder / "note.txt").write_text("ignored")
    items = library.scan(str(folder))
    assert [item.name for item in items] == ["one"]
    assert items[0].playable


def test_metadata_settings_and_folder_order_survive_restart(library, folder, tmp_path):
    write_audio(folder / "a.wav")
    write_audio(folder / "b.wav")
    items = library.scan(str(folder))
    item = items[0]
    item.start, item.end = 0.5, 2.0
    item.color, item.avatar, item.hotkey = "#112233", "🐟", "Ctrl+F8"
    item.background = "assets/background.png"
    image_source(library.data_dir / item.background)
    library.save_item(item)
    library.save_order(items[::-1])
    library.set_setting("routing", {"microphone": "test-device", "enabled": False})
    library.close()
    with_library = Library(tmp_path / "data")
    try:
        restored = with_library.scan(str(folder))
        assert [value.name for value in restored] == ["b", "a"]
        assert (restored[1].start, restored[1].end) == (0.5, 2)
        assert restored[1].color == "#112233"
        assert restored[1].avatar == "🐟"
        assert (with_library.data_dir / restored[1].background).is_file()
        assert restored[1].hotkey == "Ctrl+F8"
        assert with_library.get_setting("routing") == {"microphone": "test-device", "enabled": False}
        assert with_library.get_setting("not-set", 123) == 123
    finally:
        with_library.close()


def test_same_name_in_different_folder_has_independent_metadata(library, folder, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    write_audio(folder / "one.wav")
    write_audio(other / "one.wav")
    item = library.scan(str(folder))[0]
    item.color, item.hotkey = "#aabbcc", "Ctrl+A"
    library.save_item(item)
    other_item = library.scan(str(other))[0]
    assert other_item.color == "#202832"
    assert other_item.hotkey == ""
    assert library.scan(str(folder))[0].hotkey == "Ctrl+A"
    with pytest.raises(ValueError, match="不同文件夹"):
        library.save_order([item, other_item])


def test_short_and_bad_files_stay_as_disabled_entries(library, folder):
    write_audio(folder / "short.wav", seconds=0.3)
    (folder / "broken.mp3").write_bytes(b"not an audio file")
    items = library.scan(str(folder))
    assert len(items) == 2
    assert all(not item.playable and item.error for item in items)
    assert "1 秒" in next(item.error for item in items if item.name == "short")


def test_temporary_unreadable_preserves_entry_settings_then_recovers(library, folder, monkeypatch):
    source = write_audio(folder / "one.wav")
    item = library.scan(str(folder))[0]
    item.start, item.end, item.hotkey = 0.5, 2.0, "Alt+T"
    library.save_item(item)
    original_stat = library._file_stat
    with monkeypatch.context() as patch:
        patch.setattr(library, "_file_stat", lambda path: (_ for _ in ()).throw(PermissionError("temporarily locked")))
        disabled = library.scan(str(folder))[0]
        assert disabled.error and not disabled.playable
        assert (disabled.start, disabled.end, disabled.hotkey) == (0.5, 2.0, "Alt+T")
    assert original_stat(source)[0] > 0
    recovered = library.scan(str(folder))[0]
    assert recovered.playable and recovered.hotkey == "Alt+T"
    assert (recovered.start, recovered.end) == (0.5, 2)


def test_missing_file_disappears_without_erasing_metadata(library, folder):
    source = write_audio(folder / "one.wav")
    item = library.scan(str(folder))[0]
    item.hotkey = "F7"
    library.save_item(item)
    assert library.flush_pending() == []
    absent = source.with_suffix(".offline")
    source.rename(absent)
    assert library.scan(str(folder)) == []
    absent.rename(source)
    assert library.scan(str(folder))[0].hotkey == "F7"


def test_failed_folder_enumeration_does_not_change_metadata(library, folder):
    write_audio(folder / "one.wav")
    item = library.scan(str(folder))[0]
    item.avatar = "🎵"
    library.save_item(item)
    with pytest.raises(FileNotFoundError):
        library.scan(str(folder / "not-existent"))
    assert library.scan(str(folder))[0].avatar == "🎵"


def test_external_rename_recovers_unique_matching_package(library, folder):
    source = write_audio(folder / "old.wav")
    item = library.scan(str(folder))[0]
    item.hotkey = "F6"
    library.save_item(item)
    assert library.flush_pending() == []
    source.rename(folder / "new.wav")
    renamed = library.scan(str(folder))[0]
    assert renamed.name == "new"
    assert renamed.hotkey == "F6"


def test_replaced_audio_rereads_duration_clamps_range_and_invalidates_waveform(library, folder):
    source = write_audio(folder / "one.wav", seconds=4, amplitude=0.2)
    item = library.scan(str(folder))[0]
    item.start, item.end = 2, 3.5
    library.save_item(item)
    assert library.flush_pending() == []
    old_waveform = library.waveform(str(source), bins=32)
    old_stat = source.stat()
    write_audio(source, seconds=2, amplitude=0.8)
    os.utime(source, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns + 1_000_000_000))
    replaced = library.scan(str(folder))[0]
    assert replaced.duration == 2
    assert (replaced.start, replaced.end) == (1, 2)
    new_waveform = library.waveform(str(source), bins=32)
    assert max(new_waveform) > max(old_waveform) + 0.5
    assert len(list((library.data_dir / "cache").glob("*.json"))) == 2


def test_full_range_tracks_replaced_file_duration(library, folder):
    source = write_audio(folder / "one.wav", seconds=2)
    library.scan(str(folder))
    write_audio(source, seconds=4)
    item = library.scan(str(folder))[0]
    assert (item.start, item.end) == (0, 4)


@pytest.mark.parametrize("extension,format_name,subtype", [
    (".wav", "WAV", "PCM_16"),
    (".mp3", "MP3", "MPEG_LAYER_III"),
    (".flac", "FLAC", "PCM_16"),
    (".ogg", "OGG", "VORBIS"),
])
def test_actual_import_and_export_supported_codecs(library, folder, tmp_path, extension, format_name, subtype):
    source = write_audio(tmp_path / ("source" + extension), format=format_name, subtype=subtype)
    original = source.read_bytes()
    copied = library.import_audio([str(source)], str(folder))
    assert len(copied) == 1
    assert Path(copied[0]).read_bytes() == original
    item = library.scan(str(folder))[0]
    assert item.playable and 2.9 <= item.duration <= 3.1
    item.start, item.end = 0.5, 1.75
    exported = library.export_segment(item, str(folder))
    info = sf.info(exported)
    assert info.format == "WAV" and info.subtype == "PCM_16"
    assert abs(info.duration - 1.25) <= 1 / info.samplerate
    assert source.read_bytes() == original
    assert len(library.scan(str(folder))) == 2
    assert not list(folder.glob("*.pending"))


def test_accept_ogg_opus_and_probe_actual_content_despite_extension(library, folder, tmp_path):
    opus = write_audio(tmp_path / "opus.ogg", format="OGG", subtype="OPUS")
    renamed_wav = write_audio(tmp_path / "wrong.mp3", format="WAV")
    copied = library.import_audio([str(opus), str(renamed_wav)], str(folder))
    assert len(copied) == 2
    assert all(item.playable for item in library.scan(str(folder)))


def test_import_uses_unique_names_and_never_overwrites(library, folder, tmp_path):
    original = write_audio(folder / "one.wav", amplitude=0.1)
    before = original.read_bytes()
    source = write_audio(tmp_path / "one.wav", amplitude=0.9)
    copied = library.import_audio([str(source)], str(folder))
    assert Path(copied[0]).name == "one (2).wav"
    assert original.read_bytes() == before
    assert library.import_audio([str(original)], str(folder)) == []


def test_partial_copy_failure_leaves_no_visible_or_temporary_file(library, folder, tmp_path, monkeypatch):
    source = write_audio(tmp_path / "one.wav")
    original = source.read_bytes()

    def fail_copy(src, dst):
        dst.write_bytes(b"partially copied data")
        raise OSError("disk full")

    monkeypatch.setattr(library, "_copy_bytes", fail_copy)
    with pytest.raises(OSError, match="disk full"):
        library.import_audio([str(source)], str(folder))
    assert list(folder.iterdir()) == []
    assert source.read_bytes() == original


def test_batch_failure_rolls_back_only_files_created_by_batch(library, folder, tmp_path):
    existing = write_audio(folder / "existing.wav")
    before = existing.read_bytes()
    source = write_audio(tmp_path / "first.wav")
    invalid = tmp_path / "second.mp3"
    invalid.write_bytes(b"broken")
    with pytest.raises((ValueError, RuntimeError)):
        library.import_audio([str(source), str(invalid)], str(folder))
    assert [path.name for path in folder.iterdir()] == ["existing.wav"]
    assert existing.read_bytes() == before


def test_export_is_frame_exact_without_mutating_source(library, folder):
    source = write_audio(folder / "source.wav", rate=44100)
    original = source.read_bytes()
    item = library.scan(str(folder))[0]
    item.start, item.end = 0.125, 1.375
    first = library.export_segment(item, str(folder))
    second = library.export_segment(item, str(folder))
    assert first != second
    data, rate = sf.read(source, dtype="int16", always_2d=True)
    result, exported_rate = sf.read(first, dtype="int16", always_2d=True)
    assert exported_rate == 48000
    assert len(result) == round((item.end - item.start) * exported_rate)
    assert np.max(np.abs(result)) > 5000
    assert source.read_bytes() == original


def test_failed_export_publication_keeps_source_and_cleans_temporary(library, folder, monkeypatch):
    source = write_audio(folder / "source.wav")
    original = source.read_bytes()
    item = library.scan(str(folder))[0]
    monkeypatch.setattr(library, "_publish_unique", lambda *args: (_ for _ in ()).throw(PermissionError("read only")))
    with pytest.raises(PermissionError):
        library.export_segment(item, str(folder))
    assert [path.name for path in folder.iterdir()] == ["source.wav"]
    assert source.read_bytes() == original


def test_rename_preserves_metadata_and_never_overwrites(library, folder):
    source = write_audio(folder / "old.wav")
    existing = write_audio(folder / "new.wav", amplitude=0.1)
    existing_bytes = existing.read_bytes()
    item = next(item for item in library.scan(str(folder)) if item.name == "old")
    item.start, item.end, item.hotkey = 0.5, 2.0, "Ctrl+F9"
    library.save_item(item)
    new_path = library.rename(item, "new")
    assert Path(new_path).name == "new (2).wav"
    assert not source.exists() and existing.read_bytes() == existing_bytes
    restored = next(value for value in library.scan(str(folder)) if value.key == path_key(new_path))
    assert (restored.start, restored.end, restored.hotkey) == (0.5, 2, "Ctrl+F9")


@pytest.mark.parametrize("name", ["../escape", "sub/file", "CON", "NUL.wav", "bad:name", "trailing.", ""])
def test_rename_rejects_paths_and_windows_invalid_names(library, folder, name):
    source = write_audio(folder / "source.wav")
    item = library.scan(str(folder))[0]
    with pytest.raises(ValueError):
        library.rename(item, name)
    assert source.exists()


def test_delete_uses_recycle_service_and_failure_preserves_file(library, folder, monkeypatch):
    source = write_audio(folder / "source.wav")
    item = library.scan(str(folder))[0]
    with monkeypatch.context() as patch:
        patch.setattr("niulai_player.library.send2trash", lambda path: (_ for _ in ()).throw(OSError("recycle unavailable")))
        with pytest.raises(OSError):
            library.delete(item)
    assert source.exists() and library.scan(str(folder))[0].playable
    recycled = folder.parent / "recycled"
    calls = []

    def recycle(path):
        calls.append(path)
        Path(path).rename(recycled)

    monkeypatch.setattr("niulai_player.library.send2trash", recycle)
    library.delete(item)
    assert calls == [str(source)] and recycled.exists()
    assert library.scan(str(folder)) == []


def test_assets_are_owned_relative_copies(library, tmp_path):
    source = tmp_path / "background.png"
    payload = bytes.fromhex("89504e470d0a1a0a") + b"test payload"
    source.write_bytes(payload)
    relative = library.copy_asset(str(source))
    assert relative.startswith("assets/") and not Path(relative).is_absolute()
    assert library.copy_asset(str(source)) == relative
    source.unlink()
    assert (library.data_dir / relative).read_bytes() == payload


def test_settings_writes_and_reads_are_safe_from_multiple_threads(library):
    def update(index):
        library.set_setting(f"key-{index}", {"value": index})
        return library.get_setting(f"key-{index}")

    with ThreadPoolExecutor(max_workers=8) as executor:
        assert list(executor.map(update, range(40))) == [{"value": index} for index in range(40)]


def test_failed_metadata_write_keeps_database_readable(library, folder):
    write_audio(folder / "source.wav")
    item = library.scan(str(folder))[0]
    original = item.color
    library._db.execute("PRAGMA query_only=ON")
    item.color = "#abcdef"
    with pytest.raises(sqlite3.OperationalError):
        library.save_item(item)
    library._db.execute("PRAGMA query_only=OFF")
    assert library.scan(str(folder))[0].color == original


def test_scan_keeps_user_edit_made_while_worker_probes_file(library, folder, monkeypatch):
    source = write_audio(folder / "source.wav")
    item = library.scan(str(folder))[0]
    write_audio(source, seconds=4)
    original_probe = library._probe

    def probe_and_edit(path, extension=None):
        result = original_probe(path, extension)
        item.avatar, item.hotkey, item.start, item.end = "🎸", "Ctrl+Alt+Z", 1, 2.5
        library.save_item(item)
        return result

    monkeypatch.setattr(library, "_probe", probe_and_edit)
    refreshed = library.scan(str(folder))[0]
    assert refreshed.avatar == "🎸" and refreshed.hotkey == "Ctrl+Alt+Z"
    assert (refreshed.start, refreshed.end) == (1, 2.5)


def test_rename_metadata_failure_restores_original_filename(library, folder):
    source = write_audio(folder / "source.wav")
    item = library.scan(str(folder))[0]
    library._db.execute("PRAGMA query_only=ON")
    try:
        with pytest.raises(sqlite3.OperationalError):
            library.rename(item, "new-name")
    finally:
        library._db.execute("PRAGMA query_only=OFF")
    assert source.exists()
    assert not (folder / "new-name.wav").exists()
    assert item.path == str(source)


def test_invalid_reorder_rolls_back_earlier_updates(library, folder):
    write_audio(folder / "a.wav")
    write_audio(folder / "b.wav")
    a, b = library.scan(str(folder))
    from dataclasses import replace
    missing = replace(a, path=str(folder / "not-in-library.wav"))
    with pytest.raises(KeyError):
        library.save_order([b, missing])
    assert [item.name for item in library.scan(str(folder))] == ["a", "b"]


def test_multi_setting_write_rolls_back_all_values_on_database_failure(library):
    library.set_settings({"first": "old", "second": "old"})
    library._db.executescript("""
        CREATE TRIGGER fail_second BEFORE UPDATE ON settings
        WHEN NEW.key = 'second' AND NEW.value = '"new"'
        BEGIN SELECT RAISE(ABORT, 'simulated write failure'); END;
    """)
    with pytest.raises(sqlite3.IntegrityError, match="simulated"):
        library.set_settings({"first": "new", "second": "new"})
    assert library.get_setting("first") == "old"
    assert library.get_setting("second") == "old"


def test_multi_setting_serialization_error_cannot_commit_earlier_value(library):
    library.set_setting("first", "old")
    with pytest.raises(ValueError):
        library.set_settings({"first": "new", "invalid": float("nan")})
    assert library.get_setting("first") == "old"


def legacy_library(data_dir, folder):
    """The shipped v1 schema, with committed data still eligible to live in WAL."""
    data_dir.mkdir()
    connection = sqlite3.connect(data_dir / "library.sqlite3")
    connection.execute("PRAGMA journal_mode=WAL")
    connection.executescript("""
        CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE items (
            path TEXT PRIMARY KEY, folder TEXT NOT NULL, display_path TEXT NOT NULL,
            name TEXT NOT NULL, duration REAL NOT NULL DEFAULT 0,
            start REAL NOT NULL DEFAULT 0, end REAL NOT NULL DEFAULT 0,
            color TEXT NOT NULL DEFAULT '#202832', avatar TEXT NOT NULL DEFAULT '🐮',
            background TEXT NOT NULL DEFAULT '', hotkey TEXT NOT NULL DEFAULT '',
            sort_order INTEGER NOT NULL DEFAULT 0, size INTEGER NOT NULL DEFAULT -1,
            mtime_ns INTEGER NOT NULL DEFAULT -1, probe_ok INTEGER NOT NULL DEFAULT 0,
            present INTEGER NOT NULL DEFAULT 1
        );
        PRAGMA user_version=1;
    """)
    for index, name in enumerate(["a.wav", "b.wav"]):
        path = write_audio(folder / name)
        connection.execute("""INSERT INTO items
            (path,folder,display_path,name,duration,start,end,color,avatar,background,hotkey,sort_order)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (path_key(path), path_key(folder), str(path), path.stem, 3, .5, 2,
             "#123456", "assets/old-avatar.png", "assets/old-background.png", f"Ctrl+F{index + 1}", 1 - index))
    connection.execute("INSERT INTO settings VALUES('mic_device','\"saved-microphone\"')")
    connection.execute("INSERT INTO settings VALUES('mode','3')")
    connection.commit()
    return connection


def test_v1_migration_backs_up_wal_and_preserves_existing_data(tmp_path, folder):
    data = tmp_path / "old-data"
    legacy = legacy_library(data, folder)
    original_audio = (folder / "a.wav").read_bytes()
    try:
        migrated = Library(data)
        try:
            assert migrated._db.execute("PRAGMA user_version").fetchone()[0] == 3
            image_source(data / "assets" / "old-avatar.png")
            image_source(data / "assets" / "old-background.png")
            items = migrated.scan(str(folder))
            assert [item.name for item in items] == ["b", "a"]
            assert all((item.start, item.end, item.color, item.avatar) == (.5, 2, "#123456", "assets/old-avatar.png") for item in items)
            assert all(item.avatar_source == "" and item.avatar_crop is None for item in items)
            assert migrated.get_setting("mic_device") == "saved-microphone"
            assert migrated.get_setting("mode") == 3
            backups = list((data / "backups").glob("*.sqlite3"))
            assert len(backups) == 1
            with closing(sqlite3.connect(backups[0])) as backup:
                assert backup.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
                assert backup.execute("PRAGMA user_version").fetchone()[0] == 1
                assert backup.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 2
                assert backup.execute("SELECT value FROM settings WHERE key='mode'").fetchone()[0] == "3"
                assert "avatar_source" not in {row[1] for row in backup.execute("PRAGMA table_info(items)")}
        finally:
            migrated.close()
        second_open = Library(data)
        second_open.close()
        assert len(list((data / "backups").glob("*.sqlite3"))) == 1
        assert (folder / "a.wav").read_bytes() == original_audio
    finally:
        legacy.close()


def test_backup_failure_cancels_migration_before_schema_changes(tmp_path, folder, monkeypatch):
    data = tmp_path / "old-data"
    legacy = legacy_library(data, folder)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(Library, "_temporary", staticmethod(lambda _: (_ for _ in ()).throw(OSError("backup disk full"))))
            with pytest.raises(OSError, match="backup disk full"):
                Library(data)
        assert legacy.execute("PRAGMA user_version").fetchone()[0] == 1
        assert "avatar_source" not in {row[1] for row in legacy.execute("PRAGMA table_info(items)")}
        assert legacy.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 2
    finally:
        legacy.close()


def test_migration_schema_error_rolls_back_all_added_columns(tmp_path, folder, monkeypatch):
    data = tmp_path / "old-data"
    legacy = legacy_library(data, folder)
    real_connect = sqlite3.connect

    def guarded_connect(path, *args, **kwargs):
        connection = real_connect(path, *args, **kwargs)
        if Path(path).name == "library.sqlite3":
            alterations = []

            def authorize(action, *args):
                if action == sqlite3.SQLITE_ALTER_TABLE:
                    alterations.append(action)
                    if len(alterations) == 2:
                        return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK

            connection.set_authorizer(authorize)
        return connection

    try:
        with monkeypatch.context() as patch:
            patch.setattr(sqlite3, "connect", guarded_connect)
            with pytest.raises(sqlite3.DatabaseError):
                Library(data)
        assert legacy.execute("PRAGMA user_version").fetchone()[0] == 1
        assert "avatar_source" not in {row[1] for row in legacy.execute("PRAGMA table_info(items)")}
        assert len(list((data / "backups").glob("*.sqlite3"))) == 1
    finally:
        legacy.close()


def image_source(path, width=320, height=160):
    from PySide6.QtGui import QImage, QColor, QPainter
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(QColor("#2040e0"))
    painter = QPainter(image)
    painter.fillRect(0, 0, width // 2, height, QColor("#e02030"))
    painter.end()
    assert image.save(str(path))
    return path


def test_avatar_crop_keeps_original_and_saves_256_png_with_recrop_metadata(library, folder, tmp_path):
    from PySide6.QtGui import QImage
    audio = write_audio(folder / "one.wav")
    audio_before = audio.read_bytes()
    item = library.scan(str(folder))[0]
    item.start, item.end, item.hotkey = .25, 2.5, "Ctrl+F5"
    library.save_item(item)
    source = image_source(tmp_path / "original.png")
    original_bytes = source.read_bytes()
    library.apply_avatar_crop(item, str(source), (160, 0, 160, 160))
    assert item.avatar.startswith("assets/avatar-")
    assert item.avatar_source.startswith("assets/")
    assert item.avatar_crop == (160, 0, 160, 160)
    cropped = QImage(str(library.data_dir / item.avatar))
    assert (cropped.width(), cropped.height()) == (256, 256)
    assert cropped.pixelColor(128, 128).name() == "#2040e0"
    source.unlink()
    assert (library.data_dir / item.avatar_source).read_bytes() == original_bytes
    restored = library.scan(str(folder))[0]
    assert restored.avatar_crop == item.avatar_crop and restored.avatar_source == item.avatar_source
    assert (restored.start, restored.end, restored.hotkey) == (.25, 2.5, "Ctrl+F5")
    first_avatar = item.avatar
    library.apply_avatar_crop(item, item.avatar_source, (0, 0, 160, 160))
    assert item.avatar != first_avatar
    assert QImage(str(library.data_dir / item.avatar)).pixelColor(128, 128).name() == "#e02030"
    assert audio.read_bytes() == audio_before


def test_avatar_crop_uses_exif_oriented_source_coordinates(library, folder, tmp_path):
    from PySide6.QtGui import QImage
    write_audio(folder / "one.wav")
    item = library.scan(str(folder))[0]
    source = image_source(tmp_path / "rotated.jpg", width=300, height=150)
    # Insert an EXIF orientation=6 tag: the decoded image becomes 150×300,
    # with the originally blue right half becoming the bottom half.
    tiff = b"II\x2a\x00\x08\x00\x00\x00" + struct.pack("<H", 1)
    tiff += struct.pack("<HHI", 0x0112, 3, 1) + struct.pack("<H", 6) + b"\0\0" + b"\0" * 4
    payload = b"Exif\0\0" + tiff
    jpeg = source.read_bytes()
    source.write_bytes(jpeg[:2] + b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload + jpeg[2:])
    library.apply_avatar_crop(item, str(source), (0, 150, 150, 150))
    color = QImage(str(library.data_dir / item.avatar)).pixelColor(128, 128)
    assert color.blue() > 200 and color.red() < 60


@pytest.mark.parametrize("crop", [(-1, 0, 160, 160), (0, 0, 0, 0), (0, 0, 160, 120), (200, 0, 160, 160), (0.5, 0, 100, 100), (0, 0, 160)])
def test_invalid_crop_cannot_change_existing_avatar(library, folder, tmp_path, crop):
    write_audio(folder / "one.wav")
    item = library.scan(str(folder))[0]
    source = image_source(tmp_path / "original.png")
    previous = (item.avatar, item.avatar_source, item.avatar_crop)
    with pytest.raises(ValueError):
        library.apply_avatar_crop(item, str(source), crop)
    assert (item.avatar, item.avatar_source, item.avatar_crop) == previous
    assert library.scan(str(folder))[0].avatar == previous[0]


def test_crop_metadata_failure_keeps_previous_avatar_and_original(library, folder, tmp_path):
    write_audio(folder / "one.wav")
    item = library.scan(str(folder))[0]
    source = image_source(tmp_path / "original.png")
    original = source.read_bytes()
    library._db.execute("PRAGMA query_only=ON")
    try:
        with pytest.raises(sqlite3.OperationalError):
            library.apply_avatar_crop(item, str(source), (0, 0, 160, 160))
    finally:
        library._db.execute("PRAGMA query_only=OFF")
    assert item.avatar == "🐮" and item.avatar_source == "" and item.avatar_crop is None
    assert library.scan(str(folder))[0].avatar == "🐮"
    assert source.read_bytes() == original


def test_async_crop_snapshot_cannot_overwrite_newer_range_or_color(library, folder, tmp_path):
    from dataclasses import replace
    write_audio(folder / "one.wav")
    current = library.scan(str(folder))[0]
    snapshot = replace(current)
    current.start, current.end, current.color = 1, 2.5, "#abcdef"
    current.hotkey = "Ctrl+F8"
    library.save_item(current)
    source = image_source(tmp_path / "original.png")
    library.apply_avatar_crop(snapshot, str(source), (0, 0, 160, 160))
    saved = library.scan(str(folder))[0]
    assert (saved.start, saved.end, saved.color, saved.hotkey) == (1, 2.5, "#abcdef", "Ctrl+F8")
    assert saved.avatar == snapshot.avatar and saved.avatar_source == snapshot.avatar_source


def test_icon_is_deterministic_and_contains_all_windows_sizes(tmp_path):
    import importlib.util
    from PySide6.QtGui import QImageReader
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("niulai_icon_builder", root / "tools" / "generate_icon.py")
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    first, second = tmp_path / "one.ico", tmp_path / "two.ico"
    builder.make_ico(root / "assets" / "app.svg", first)
    builder.make_ico(root / "assets" / "app.svg", second)
    assert first.read_bytes() == second.read_bytes() == (root / "assets" / "app.ico").read_bytes()
    reader = QImageReader(str(first))
    assert reader.imageCount() == len(builder.SIZES)
    for index, size in enumerate(builder.SIZES):
        assert reader.jumpToImage(index)
        image = reader.read()
        assert (image.width(), image.height()) == (size, size)
        assert image.pixelColor(0, 0).alpha() == 0


def test_brand_asset_lookup_supports_source_and_frozen_bundle(tmp_path, monkeypatch):
    import sys
    from niulai_player.branding import asset_path
    assert asset_path("app.ico").is_file()
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert asset_path("app.svg") == tmp_path / "assets" / "app.svg"
    with pytest.raises(ValueError):
        asset_path("../other-file")
