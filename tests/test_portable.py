import json
from pathlib import Path
import shutil
import uuid
import zipfile

import pytest
from PySide6.QtGui import QImage

from niulai_player.portable import (PackageCancelled, PackageError, file_hash,
                                    package_path, read_package, write_package)


@pytest.fixture
def media(tmp_path):
    path = tmp_path / "声音.wav"
    path.write_bytes(b"unchanged-original-media" * 100)
    return path


@pytest.fixture
def picture(tmp_path):
    path = tmp_path / "original.png"
    image = QImage(16, 8, QImage.Format.Format_RGB32)
    image.fill(0x225544)
    assert image.save(str(path))
    return path


def properties(**changes):
    return dict(name="声音{原名}", start=.5, end=2, color="#102030", avatar_emoji="🐮",
                hotkey="Ctrl+F8", audio_stream=2, background_crop=[1, 2, 5, 4], **changes)


def rewrite_config(media, change):
    package = package_path(media)
    with zipfile.ZipFile(package) as archive:
        content = {entry.filename: archive.read(entry) for entry in archive.infolist()}
    config = json.loads(content["config.json"])
    change(config)
    content["config.json"] = json.dumps(config).encode()
    with zipfile.ZipFile(package, "w") as archive:
        for name, payload in content.items():
            archive.writestr(name, payload)


def test_two_file_copy_restores_properties_and_embedded_original_in_new_directory(media, picture, tmp_path):
    original = media.read_bytes()
    saved = write_package(media, properties(), {"background_source": picture})
    destination = tmp_path / "another-computer"
    destination.mkdir()
    copied = destination / media.name
    shutil.copyfile(media, copied)
    shutil.copyfile(package_path(media), package_path(copied))
    picture.unlink()
    restored = read_package(copied, tmp_path / "empty-appdata" / "assets")
    assert restored.identifier == saved.identifier
    assert restored.properties == properties()
    assert restored.assets["background_source"].exists()
    assert media.read_bytes() == original
    assert len(list(destination.iterdir())) == 2


def test_cancelled_write_keeps_existing_package_and_removes_temporary_files(media, picture, tmp_path):
    saved = write_package(media, properties(), {"avatar_source": picture})
    previous = package_path(media).read_bytes()
    with pytest.raises(PackageCancelled):
        write_package(media, properties(), {"avatar_source": picture},
                      identifier=saved.identifier, expected_revision=saved.revision, cancelled=lambda: True)
    assert package_path(media).read_bytes() == previous
    assert not list(tmp_path.glob("*.pending"))


def test_publish_failure_keeps_previous_complete_package(media, tmp_path, monkeypatch):
    saved = write_package(media, properties(), {})
    previous = package_path(media).read_bytes()
    monkeypatch.setattr("niulai_player.portable.os.replace", lambda *_: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError, match="disk full"):
        write_package(media, properties(), {}, identifier=saved.identifier, expected_revision=saved.revision)
    assert package_path(media).read_bytes() == previous
    assert not list(tmp_path.glob("*.pending"))


def test_stale_revision_cannot_overwrite_another_edit(media):
    first = write_package(media, properties(), {})
    second = write_package(media, properties(), {}, expected_revision=first.revision, identifier=first.identifier)
    previous = package_path(media).read_bytes()
    with pytest.raises(PackageError, match="其他操作"):
        write_package(media, properties(), {}, expected_revision=first.revision)
    assert package_path(media).read_bytes() == previous
    assert first.revision != second.revision


@pytest.mark.parametrize("kind", ["future", "corrupt", "mismatch"])
def test_invalid_package_is_preserved_and_never_replaced_with_defaults(media, tmp_path, kind):
    saved = write_package(media, properties(), {})
    if kind == "future":
        rewrite_config(media, lambda config: config.update(version=999))
    elif kind == "corrupt":
        package_path(media).write_bytes(b"not-a-zip")
    else:
        media.write_bytes(b"different-media")
    previous = package_path(media).read_bytes()
    with pytest.raises(PackageError):
        read_package(media, tmp_path / "cache")
    with pytest.raises(PackageError):
        write_package(media, properties(), {}, expected_revision=saved.revision)
    assert package_path(media).read_bytes() == previous


def test_unsafe_member_cannot_escape_extraction_directory(media, tmp_path):
    write_package(media, properties(), {})
    with zipfile.ZipFile(package_path(media), "a") as archive:
        archive.writestr("../escaped.png", b"bad")
    with pytest.raises(PackageError):
        read_package(media, tmp_path / "cache")
    assert not (tmp_path / "escaped.png").exists()


def test_identical_media_have_independent_configuration_ids(media, tmp_path):
    copy = tmp_path / "independent.wav"
    shutil.copyfile(media, copy)
    first = write_package(media, properties(), {})
    second = write_package(copy, properties(), {})
    assert first.identifier != second.identifier
    assert first.media["sha256"] == second.media["sha256"] == file_hash(media)


def test_missing_asset_cannot_publish_partial_package(media, tmp_path):
    with pytest.raises(OSError):
        write_package(media, properties(), {"background": tmp_path / "missing.png"})
    assert not package_path(media).exists()
    assert not list(tmp_path.glob("*.pending"))


def test_invalid_crop_and_invalid_stream_are_rejected_before_writing(media):
    for update in ({"background_crop": [0, 0, 0, 4]}, {"avatar_crop": [0, 0, 8, 4]}, {"audio_stream": -1}):
        config = properties()
        config.update(update)
        with pytest.raises(PackageError):
            write_package(media, config, {})
    assert not package_path(media).exists()
