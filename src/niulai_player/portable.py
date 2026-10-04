"""One media file plus one atomic, self-contained .newlife ZIP package.

This module performs blocking I/O and must be called from a worker. It never
changes the media file and never extracts ZIP member paths supplied by a file.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile
from typing import Callable
import uuid
import zipfile


VERSION = 1
MAX_ASSET = 25 * 1024 * 1024
MAX_PACKAGE = 80 * 1024 * 1024
MAX_CONFIG = 256 * 1024
ASSET_SLOTS = frozenset({"avatar", "avatar_source", "background", "background_source"})
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"})


class PackageError(ValueError):
    """A package was preserved because it cannot safely be used or replaced."""


class PackageCancelled(RuntimeError):
    pass


@dataclass(frozen=True)
class PackageData:
    identifier: str
    revision: str
    properties: dict
    assets: dict[str, Path]
    media: dict


def package_path(media: str | Path) -> Path:
    return Path(str(media) + ".newlife")


def _cancel(check: Callable[[], bool] | None) -> None:
    if check and check():
        raise PackageCancelled("配置包操作已取消，原文件保持不变")


def file_hash(path: str | Path, cancelled=None) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        while True:
            _cancel(cancelled)
            block = file.read(1024 * 1024)
            if not block:
                return digest.hexdigest()
            digest.update(block)


def _crop(value, square=False):
    if value is None:
        return
    if (not isinstance(value, (list, tuple)) or len(value) != 4
            or any(type(part) is not int for part in value)
            or min(value[:2]) < 0 or min(value[2:]) < 1
            or (square and value[2] != value[3])):
        raise PackageError("配置包中的图片裁剪范围无效")


def _validate(config: dict) -> None:
    if not isinstance(config, dict) or type(config.get("version")) is not int or config.get("version") != VERSION:
        raise PackageError("配置包版本不受支持，已保留原包")
    try:
        uuid.UUID(config["id"])
        uuid.UUID(config["revision"])
        media, props, assets = config["media"], config["properties"], config["assets"]
        if not isinstance(props, dict) or not isinstance(assets, dict):
            raise ValueError()
        if not isinstance(media.get("name"), str) or Path(media["name"]).name != media["name"]:
            raise ValueError()
        if type(media.get("size")) is not int or media["size"] < 1:
            raise ValueError()
        if not re.fullmatch(r"[0-9a-f]{64}", media.get("sha256", "")):
            raise ValueError()
        for key in ("start", "end"):
            value = props.get(key, 0)
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                raise ValueError()
        if props.get("end", 0) < props.get("start", 0):
            raise ValueError()
        for key in ("name", "color", "hotkey", "avatar_emoji"):
            if key in props and not isinstance(props[key], str):
                raise ValueError()
        if "color" in props and not re.fullmatch(r"#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?", props["color"]):
            raise ValueError()
        if props.get("audio_stream") is not None and (type(props["audio_stream"]) is not int or props["audio_stream"] < 0):
            raise ValueError()
        _crop(props.get("avatar_crop"), square=True)
        _crop(props.get("background_crop"))
        if not set(assets) <= ASSET_SLOTS:
            raise ValueError()
        for name in assets.values():
            path = PurePosixPath(name)
            if (not isinstance(name, str) or path.is_absolute() or ".." in path.parts
                    or "\\" in name or ":" in name or len(path.parts) != 2
                    or path.parts[0] != "assets" or path.suffix.lower() not in IMAGE_SUFFIXES):
                raise ValueError()
    except (KeyError, TypeError, ValueError, AttributeError) as error:
        raise PackageError("配置包内容无效，已保留原包") from error


def _read_config(archive: zipfile.ZipFile) -> dict:
    entries = archive.infolist()
    if len(entries) > 8 or len({entry.filename for entry in entries}) != len(entries):
        raise PackageError("配置包文件列表无效")
    if sum(entry.file_size for entry in entries) > MAX_PACKAGE:
        raise PackageError("配置包解压大小超出限制")
    for entry in entries:
        mode = entry.external_attr >> 16
        path = PurePosixPath(entry.filename)
        if (entry.is_dir() or stat.S_ISLNK(mode) or path.is_absolute()
                or ".." in path.parts or "\\" in entry.filename or ":" in entry.filename
                or entry.flag_bits & 1 or entry.file_size > MAX_ASSET):
            raise PackageError("配置包含有不安全的文件条目")
    try:
        info = archive.getinfo("config.json")
        if info.file_size > MAX_CONFIG:
            raise PackageError("配置包说明文件过大")
        config = json.loads(archive.read(info))
        _validate(config)
        expected = {"config.json", *config["assets"].values()}
        if {entry.filename for entry in entries} != expected:
            raise PackageError("配置包图片缺失或含未知文件")
        return config
    except (KeyError, UnicodeError, json.JSONDecodeError) as error:
        raise PackageError("配置包损坏，已保留原包") from error


def revision(media: str | Path) -> str | None:
    target = package_path(media)
    if not target.exists():
        return None
    try:
        with zipfile.ZipFile(target) as archive:
            return _read_config(archive)["revision"]
    except (OSError, zipfile.BadZipFile) as error:
        raise PackageError("配置包损坏，已保留原包") from error


def inspect_package(path: str | Path) -> dict:
    """Read validated metadata only, without trusting or extracting member paths."""
    try:
        with zipfile.ZipFile(path) as archive:
            return _read_config(archive)
    except (OSError, zipfile.BadZipFile) as error:
        raise PackageError("配置包损坏，已保留原包") from error


def read_package(media: str | Path, cache: str | Path, *, cancelled=None, verify_media=True) -> PackageData:
    media, cache = Path(media), Path(cache)
    target = package_path(media)
    try:
        with zipfile.ZipFile(target) as archive:
            config = _read_config(archive)
            if verify_media and (media.stat().st_size != config["media"]["size"]
                                 or file_hash(media, cancelled) != config["media"]["sha256"]):
                raise PackageError("音频与配置包不匹配，已保留原包")
            cache.mkdir(parents=True, exist_ok=True)
            assets = {}
            for slot, name in config["assets"].items():
                _cancel(cancelled)
                fd, temp_name = tempfile.mkstemp(prefix=".newlife-", suffix=".pending", dir=cache)
                temporary = Path(temp_name)
                try:
                    digest = hashlib.sha256()
                    with os.fdopen(fd, "wb") as output, archive.open(name) as source:
                        while True:
                            _cancel(cancelled)
                            data = source.read(1024 * 1024)
                            if not data:
                                break
                            digest.update(data)
                            output.write(data)
                        output.flush()
                        os.fsync(output.fileno())
                    final = cache / (digest.hexdigest() + PurePosixPath(name).suffix.lower())
                    from PySide6.QtGui import QImageReader
                    reader = QImageReader(str(temporary))
                    dimensions = reader.size()
                    valid_size = dimensions.width() > 0 and dimensions.height() > 0 and dimensions.width() * dimensions.height() <= 40_000_000
                    image = reader.read() if valid_size else None
                    # QImageReader retains a file handle on Windows. Release
                    # it before atomically publishing or unlinking the cache.
                    del reader
                    if not valid_size:
                        raise PackageError("配置包图片无效或超过 4000 万像素")
                    if image.isNull():
                        raise PackageError("配置包图片损坏，已保留原包")
                    if not final.exists():
                        os.replace(temporary, final)
                    assets[slot] = final
                finally:
                    temporary.unlink(missing_ok=True)
            return PackageData(config["id"], config["revision"], config["properties"], assets, config["media"])
    except (zipfile.BadZipFile, EOFError) as error:
        raise PackageError("配置包损坏，已保留原包") from error


def write_package(media: str | Path, properties: dict, assets: dict[str, str | Path], *,
                  identifier: str | None = None, expected_revision: str | None = None,
                  cancelled=None) -> PackageData:
    """Commit a complete package only if its previous revision still matches.

    Callers must pass the currently loaded revision when replacing a package.
    None means create-only. An invalid existing package is never overwritten.
    """
    media = Path(media)
    target = package_path(media)
    if revision(media) != expected_revision:
        raise PackageError("配置包已被其他操作修改，请刷新后重试")
    before = media.stat()
    metadata = dict(name=media.name, size=before.st_size, sha256=file_hash(media, cancelled))
    if expected_revision is not None:
        with zipfile.ZipFile(target) as existing:
            previous_media = _read_config(existing)["media"]
        if (previous_media["size"], previous_media["sha256"]) != (metadata["size"], metadata["sha256"]):
            raise PackageError("音频与配置包不匹配，已保留原包")
    config = dict(version=VERSION, id=identifier or str(uuid.uuid4()), revision=str(uuid.uuid4()),
                  media=metadata, properties=properties,
                  assets={key: "assets/" + key + Path(path).suffix.lower() for key, path in assets.items()})
    _validate(config)
    payload = json.dumps(config, ensure_ascii=False, allow_nan=False).encode("utf-8")
    if len(payload) > MAX_CONFIG or sum(Path(path).stat().st_size for path in assets.values()) > MAX_PACKAGE:
        raise PackageError("配置包大小超出限制")
    fd, temp_name = tempfile.mkstemp(prefix=".newlife-", suffix=".pending", dir=media.parent)
    os.close(fd)
    temporary = Path(temp_name)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_STORED) as archive:
            archive.writestr("config.json", payload)
            for key, path in assets.items():
                path = Path(path)
                if not 0 < path.stat().st_size <= MAX_ASSET:
                    raise PackageError("配置包图片必须非空且不超过 25 MiB")
                with path.open("rb") as source, archive.open(config["assets"][key], "w") as output:
                    while True:
                        _cancel(cancelled)
                        chunk = source.read(1024 * 1024)
                        if not chunk:
                            break
                        output.write(chunk)
        with zipfile.ZipFile(temporary) as archive:
            _read_config(archive)
            if archive.testzip() is not None:
                raise PackageError("配置包校验失败")
        after = media.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise PackageError("媒体在保存配置期间发生变化，请刷新后重试")
        _cancel(cancelled)
        if revision(media) != expected_revision:
            raise PackageError("配置包已被其他操作修改，请刷新后重试")
        with temporary.open("rb+") as file:
            os.fsync(file.fileno())
        os.replace(temporary, target)
        return PackageData(config["id"], config["revision"], dict(properties),
                           {key: Path(value) for key, value in assets.items()}, metadata)
    finally:
        temporary.unlink(missing_ok=True)
