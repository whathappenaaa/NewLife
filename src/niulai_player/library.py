from __future__ import annotations

from contextlib import closing, suppress
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
import threading
from typing import Any
import uuid

import numpy as np
from send2trash import send2trash

from .models import AudioItem, MIN_SEGMENT_SECONDS, SUPPORTED_EXTENSIONS, path_key, valid_range
from .media_decode import default_decoder
from .music_folder import check_folder_writable
from . import portable
from . import file_undo


class Library:
    """Folder-backed audio files with independent, durable per-file UI metadata.

    Scanning/decoding belongs on a worker thread. SQLite access is serialized, but
    slow file operations do not hold the metadata lock. Missing files retain their
    settings so a temporarily disconnected directory does not destroy user edits.
    """

    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir).absolute()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "assets").mkdir(exist_ok=True)
        (self.data_dir / "cache").mkdir(exist_ok=True)
        self._lock = threading.RLock()
        self._closed = False
        self._pending: set[str] = set()
        self._edit_versions: dict[str, int] = {}
        self._writing: str | None = None
        self._undo_lock = threading.RLock()
        self._asset_revisions: dict[tuple[str, str], int] = {}
        self._portable_states: dict[str, portable.PackageData | None] = {}
        self._portable_errors: dict[str, str] = {}
        self._package_warnings: dict[str, str] = {}
        self._package_cache: dict[str, tuple[tuple, portable.PackageData]] = {}
        self._condition = threading.Condition(self._lock)
        self._worker_stop = False
        self._db = sqlite3.connect(self.data_dir / "library.sqlite3", check_same_thread=False, timeout=10)
        self._db.row_factory = sqlite3.Row
        try:
            self._initialize_database()
        except Exception:
            self._db.close()
            raise
        self._writer = threading.Thread(target=self._package_worker, name="newlife-config-writer", daemon=True)
        self._writer.start()

    def _initialize_database(self) -> None:
        version = self._db.execute("PRAGMA user_version").fetchone()[0]
        if version > 3:
            raise ValueError("此媒体库由更新版本创建，请使用匹配的播放器版本")
        existing = self._db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='items'").fetchone()
        if existing and version < 3:
            # sqlite.backup includes committed WAL pages; copying only the main
            # database file can silently omit the user's latest saved settings.
            directory = self.data_dir / "backups"
            directory.mkdir(exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
            target = directory / f"library-v{version}-before-v3-{stamp}-{uuid.uuid4().hex[:8]}.sqlite3"
            temporary = self._temporary(directory)
            try:
                with closing(sqlite3.connect(temporary)) as backup:
                    self._db.backup(backup)
                    if backup.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                        raise sqlite3.DatabaseError("数据库备份校验失败，已取消升级")
                    backup.execute("PRAGMA journal_mode=DELETE")
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=FULL")
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            self._db.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            self._db.execute("""CREATE TABLE IF NOT EXISTS items (
                path TEXT PRIMARY KEY, folder TEXT NOT NULL, display_path TEXT NOT NULL,
                name TEXT NOT NULL, duration REAL NOT NULL DEFAULT 0,
                start REAL NOT NULL DEFAULT 0, end REAL NOT NULL DEFAULT 0,
                color TEXT NOT NULL DEFAULT '#202832', avatar TEXT NOT NULL DEFAULT '🐮',
                background TEXT NOT NULL DEFAULT '', hotkey TEXT NOT NULL DEFAULT '',
                sort_order INTEGER NOT NULL DEFAULT 0,
                size INTEGER NOT NULL DEFAULT -1, mtime_ns INTEGER NOT NULL DEFAULT -1,
                probe_ok INTEGER NOT NULL DEFAULT 0, present INTEGER NOT NULL DEFAULT 1,
                avatar_source TEXT NOT NULL DEFAULT '', avatar_crop TEXT
            )""")
            columns = {row[1] for row in self._db.execute("PRAGMA table_info(items)")}
            if "avatar_source" not in columns:
                self._db.execute("ALTER TABLE items ADD COLUMN avatar_source TEXT NOT NULL DEFAULT ''")
            if "avatar_crop" not in columns:
                self._db.execute("ALTER TABLE items ADD COLUMN avatar_crop TEXT")
            for name, declaration in {
                "background_source": "TEXT NOT NULL DEFAULT ''", "background_crop": "TEXT",
                "audio_stream": "INTEGER", "audio_streams": "TEXT NOT NULL DEFAULT '[]'",
            }.items():
                if name not in columns:
                    self._db.execute(f"ALTER TABLE items ADD COLUMN {name} {declaration}")
            self._db.execute("CREATE INDEX IF NOT EXISTS folder_items ON items(folder, sort_order)")
            self._db.execute("PRAGMA user_version=3")

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("媒体库已经关闭")

    def get_setting(self, key: str, default: Any = None) -> Any:
        with self._lock:
            self._ensure_open()
            row = self._db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            return json.loads(row[0]) if row is not None else default

    def set_setting(self, key: str, value: Any) -> None:
        self.set_settings({key: value})

    def set_settings(self, values: dict[str, Any]) -> None:
        """Commit a settings dialog as one transaction, never partially."""
        encoded = [(key, json.dumps(value, ensure_ascii=False, allow_nan=False)) for key, value in values.items()]
        with self._lock, self._db:
            self._ensure_open()
            self._db.executemany("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", encoded)

    @staticmethod
    def _probe(path: str | Path, extension: str | None = None) -> tuple[float, int, int]:
        extension = (extension or Path(path).suffix).lower()
        if extension not in SUPPORTED_EXTENSIONS:
            raise ValueError("此媒体扩展名不受支持")
        info = default_decoder().probe(path)
        return info.duration, info.sample_rate, info.frames

    @staticmethod
    def _file_stat(path: str | Path) -> tuple[int, int]:
        status = os.stat(path)
        return status.st_size, status.st_mtime_ns

    @staticmethod
    def _row_item(row: sqlite3.Row, *, error: str = "") -> AudioItem:
        crop = None
        if row["avatar_crop"]:
            try:
                candidate = json.loads(row["avatar_crop"])
                crop = Library._validate_crop(candidate)
            except (ValueError, TypeError):
                pass  # Existing avatar remains usable even if optional recrop metadata is damaged.
        return AudioItem(path=row["display_path"], name=row["name"], duration=row["duration"],
                         start=row["start"], end=row["end"], color=row["color"], avatar=row["avatar"],
                         background=row["background"], hotkey=row["hotkey"], order=row["sort_order"], error=error,
                         avatar_source=row["avatar_source"], avatar_crop=crop,
                         background_source=row["background_source"],
                         background_crop=tuple(json.loads(row["background_crop"])) if row["background_crop"] and json.loads(row["background_crop"]) else None,
                         audio_stream=row["audio_stream"], audio_streams=json.loads(row["audio_streams"]))

    def _properties(self, item: AudioItem) -> dict:
        return {"name": item.name, "start": item.start, "end": item.end,
                "color": item.color, "avatar_emoji": item.avatar if not item.avatar.startswith("assets/") else "🐮",
                "hotkey": item.hotkey, "avatar_crop": item.avatar_crop,
                "background_crop": item.background_crop, "audio_stream": item.audio_stream}

    def _assets(self, item: AudioItem) -> dict:
        return {slot: self.data_dir / getattr(item, slot) for slot in portable.ASSET_SLOTS
                if getattr(item, slot).replace("\\", "/").startswith("assets/")}

    def _enqueue_package(self, key: str) -> None:
        with self._condition:
            self._edit_versions[key] = self._edit_versions.get(key, 0) + 1
            self._pending.add(key)
            self._portable_errors.pop(key, None)
            self._condition.notify_all()

    def _package_worker(self) -> None:
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._pending or self._worker_stop)
                if self._worker_stop and not self._pending:
                    return
                key = self._pending.pop()
                self._writing = key
                state = self._portable_states.get(key)
                item = None
            try:
                with self._lock:
                    row = self._db.execute("SELECT * FROM items WHERE path=?", (key,)).fetchone()
                    item = self._row_item(row) if row is not None else None
                if item is not None:
                    saved = portable.write_package(item.path, self._properties(item), self._assets(item),
                                                   identifier=state.identifier if state else None,
                                                   expected_revision=state.revision if state else None)
                    with self._condition:
                        self._portable_states[key] = saved
                        self._package_cache.pop(key, None)
                        self._portable_errors.pop(key, None)
            except Exception as exc:
                with self._condition:
                    detail = ("当前音频文件夹不可写，录音和配置保存不可用，请选择可写文件夹"
                              if isinstance(exc, PermissionError) else str(exc))
                    self._portable_errors[key] = f"{Path(item.path).name if item else key}：配置保存失败：{detail}"
            finally:
                with self._condition:
                    self._writing = None
                    self._condition.notify_all()

    def flush_pending(self, timeout: float | None = None) -> list[str]:
        """Wait for queued commits and report durable-write failures without hiding them."""
        with self._condition:
            completed = self._condition.wait_for(lambda: not self._pending and self._writing is None, timeout)
            errors = list(self._portable_errors.values())
            if not completed:
                errors.append("配置保存仍在进行，请稍后再退出或复制文件")
            return errors

    def _portable_write_status(self, key: str, default: str = "配置已保存") -> str:
        """Call with the metadata lock held; only a writer can confirm a save."""
        if key in self._pending or self._writing == key:
            return "配置保存中"
        return self._portable_errors.get(key, default)

    def _restore_latest_portable_item(self, item: AudioItem) -> None:
        """Discard a stale scan's fields without losing probe or write failures."""
        latest = self._db.execute("SELECT * FROM items WHERE path=?", (item.key,)).fetchone()
        if latest:
            item.__dict__.update(self._row_item(latest, error=item.error).__dict__)
        state = self._portable_states.get(item.key)
        if state is not None:
            item.portable_id = state.identifier
        item.portable_status = self._portable_write_status(item.key, "配置已保存" if state else "")

    def _load_portable(self, item: AudioItem) -> None:
        key, target = item.key, portable.package_path(item.path)
        with self._lock:
            edit_version = self._edit_versions.get(key, 0)
            if key in self._pending or self._writing == key:
                self._restore_latest_portable_item(item)
                return
        if not target.exists():
            self._recover_orphan(item)
        if not target.exists():
            with self._lock:
                if (self._edit_versions.get(key, 0) != edit_version
                        or key in self._pending or self._writing == key):
                    self._restore_latest_portable_item(item)
                    return
                self._portable_states[key] = None
                if key in self._portable_errors:
                    # Refresh is not a retry. Preserve the failure until a new
                    # explicit edit or a successful durable write resolves it.
                    item.portable_status = self._portable_errors[key]
                    return
                # Untouched new media need not create a companion file. Existing edits
                # are migrated after the database backup made during initialization.
                if (item.name != Path(item.path).stem or item.start != 0 or abs(item.end - item.duration) > 1e-6
                        or item.color != "#202832" or item.avatar != "🐮" or item.background or item.hotkey
                        or item.avatar_source or item.background_source or item.audio_stream is not None):
                    self._enqueue_package(key)
                    item.portable_status = "配置保存中"
            return
        try:
            signature = (*self._file_stat(item.path), *self._file_stat(target))
            cached = self._package_cache.get(key)
            state = cached[1] if cached and cached[0] == signature else portable.read_package(item.path, self.data_dir / "assets")
            with self._lock:
                # A field edit made while ZIP verification ran wins until saved.
                if (self._edit_versions.get(key, 0) != edit_version
                        or key in self._pending or self._writing == key):
                    self._restore_latest_portable_item(item)
                    return
            values = state.properties
            item.name = values.get("name", item.name)
            if item.name == Path(state.media["name"]).stem:
                item.name = Path(item.path).stem
            item.color = values.get("color", "#202832")
            if not re.fullmatch(r"#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?", item.color):
                raise portable.PackageError("配置包背景色无效，已保留原包")
            item.hotkey = values.get("hotkey", "")
            item.avatar = values.get("avatar_emoji", "🐮")
            item.background = item.avatar_source = item.background_source = ""
            for slot, asset in state.assets.items():
                setattr(item, slot, asset.relative_to(self.data_dir).as_posix())
            item.avatar_crop = tuple(values["avatar_crop"]) if values.get("avatar_crop") else None
            item.background_crop = tuple(values["background_crop"]) if values.get("background_crop") else None
            item.audio_stream = values.get("audio_stream")
            selected = next((track for track in item.audio_streams if track["index"] == item.audio_stream), None)
            if item.audio_stream is not None and selected is None:
                item.audio_stream = None
                item.portable_status = "配置的音轨不存在，当前使用默认音轨；可右键重新选择"
            elif selected:
                item.duration = selected.get("duration", item.duration)
            if item.duration >= MIN_SEGMENT_SECONDS:
                item.start, item.end = valid_range(item.duration, values.get("start", 0), values.get("end", item.duration))
            item.portable_id = state.identifier
            with self._lock, self._db:
                if self._edit_versions.get(key, 0) != edit_version:
                    self._restore_latest_portable_item(item)
                    return
                # Publishing verified read state must be atomic with the revision
                # check. Reading an old package never acknowledges a failed write.
                self._portable_states[key] = state
                self._package_cache[key] = (signature, state)
                self._package_warnings.pop(key, None)
                item.portable_status = self._portable_write_status(key, item.portable_status or "配置已保存")
                self._db.execute("""UPDATE items SET name=?,duration=?,start=?,end=?,color=?,avatar=?,background=?,hotkey=?,
                    avatar_source=?,avatar_crop=?,background_source=?,background_crop=?,audio_stream=? WHERE path=?""",
                    (item.name, item.duration, item.start, item.end, item.color, item.avatar, item.background, item.hotkey,
                     item.avatar_source, json.dumps(item.avatar_crop), item.background_source, json.dumps(item.background_crop),
                     item.audio_stream, key))
        except (OSError, ValueError, RuntimeError) as exc:
            with self._lock:
                if self._edit_versions.get(key, 0) != edit_version:
                    self._restore_latest_portable_item(item)
                    return
                item.portable_status = f"配置包不可用：{exc}"
                self._package_warnings[key] = item.portable_status

    def _recover_orphan(self, item: AudioItem) -> None:
        """Reassociate a unique verified companion after an external media rename."""
        media = Path(item.path)
        candidates = []
        for package in media.parent.glob("*.newlife"):
            original = Path(str(package)[:-len(".newlife")])
            if original.exists():
                continue
            try:
                metadata = portable.inspect_package(package)["media"]
                if metadata["size"] == media.stat().st_size:
                    candidates.append((package, metadata["sha256"]))
            except (OSError, ValueError, RuntimeError):
                continue
        if not candidates:
            return
        digest = portable.file_hash(media)
        matches = [package for package, expected in candidates if digest == expected]
        if len(matches) != 1:
            return  # Identical audio may intentionally have different configurations.
        destination = portable.package_path(media)
        try:
            if os.name == "nt":
                os.rename(matches[0], destination)
            else:
                os.link(matches[0], destination)
                matches[0].unlink()
        except OSError:
            pass

    def scan(self, folder: str) -> list[AudioItem]:
        directory = Path(folder).absolute()
        folder_key = path_key(directory)
        # Do not mark anything missing if enumeration itself fails (USB offline,
        # permissions, or inaccessible network shares).
        with os.scandir(directory) as entries:
            paths = []
            for entry in entries:
                if Path(entry.name).suffix.lower() not in SUPPORTED_EXTENSIONS or entry.is_symlink():
                    continue
                try:
                    if not entry.is_file(follow_symlinks=False):
                        continue
                except OSError:
                    pass  # Keep a disabled entry if an enumerated file is temporarily inaccessible.
                paths.append(Path(entry.path))
        paths.sort(key=lambda path: (path.name.casefold(), path.name))
        with self._lock:
            self._ensure_open()
            known = {row["path"]: row for row in self._db.execute("SELECT * FROM items WHERE folder=?", (folder_key,))}

        probes: list[tuple[Path, int, int, float, bool, str]] = []
        tracks = {}
        for path in paths:
            previous = known.get(path_key(path))
            size, modified = -1, -1
            duration = previous["duration"] if previous else 0.0
            try:
                size, modified = self._file_stat(path)
                if previous and previous["probe_ok"] and (size, modified) == (previous["size"], previous["mtime_ns"]):
                    with open(path, "rb") as check_access:
                        check_access.read(1)
                    duration = previous["duration"]
                else:
                    duration, _, _ = self._probe(path)
                    info = default_decoder().probe(path)
                    tracks[path_key(path)] = [asdict(track) for track in info.tracks]
                error = "音频不足 1 秒，无法播放片段" if duration < MIN_SEGMENT_SECONDS else ""
                probes.append((path, size, modified, duration, True, error))
            except FileNotFoundError:
                continue  # It was removed after enumeration; retain its stored metadata.
            except (OSError, ValueError, RuntimeError) as exc:
                probes.append((path, size, modified, duration, False, f"音频暂不可用：{exc}"))

        items: list[AudioItem] = []
        with self._lock, self._db:
            self._ensure_open()
            self._db.execute("UPDATE items SET present=0 WHERE folder=?", (folder_key,))
            next_order = self._db.execute("SELECT COALESCE(MAX(sort_order),-1)+1 FROM items WHERE folder=?", (folder_key,)).fetchone()[0]
            for path, size, modified, duration, ok, error in probes:
                key = path_key(path)
                if not path.is_file():
                    continue  # A deletion worker may have moved it while this scan probed.
                # Re-read after probing: an edit made while this worker decoded
                # a file must not be overwritten by a stale metadata snapshot.
                previous = self._db.execute("SELECT * FROM items WHERE path=?", (key,)).fetchone()
                if previous:
                    item = self._row_item(previous, error=error)
                    item.path = str(path)
                    old_duration = item.duration
                    item.duration = duration
                    if ok and duration >= MIN_SEGMENT_SECONDS:
                        # Full-file ranges continue to mean the full file after a replacement.
                        end = duration if abs(item.end - old_duration) < 1e-6 else item.end
                        item.start, item.end = valid_range(duration, item.start, end)
                    elif ok:
                        item.start, item.end = 0.0, duration
                    self._db.execute("UPDATE items SET display_path=?,duration=?,start=?,end=?,size=?,mtime_ns=?,probe_ok=?,present=1 WHERE path=?",
                                     (str(path), duration, item.start, item.end, size, modified, int(ok), key))
                else:
                    item = AudioItem(str(path), path.stem, duration, order=next_order, error=error)
                    next_order += 1
                    self._db.execute("INSERT INTO items(path,folder,display_path,name,duration,start,end,sort_order,size,mtime_ns,probe_ok) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                                     (key, folder_key, str(path), item.name, duration, item.start, item.end, item.order, size, modified, int(ok)))
                if key in tracks:
                    item.audio_streams = tracks[key]
                    self._db.execute("UPDATE items SET audio_streams=? WHERE path=?", (json.dumps(item.audio_streams), key))
                items.append(item)
        for item in items:
            self._load_portable(item)
        return sorted(items, key=lambda item: (item.order, item.name.casefold(), item.key))

    @staticmethod
    def _check_asset(value: str) -> None:
        # Emoji avatars are allowed. File-backed assets are owned relative paths.
        if value and ("/" in value or "\\" in value or Path(value).is_absolute()):
            normalized = value.replace("\\", "/")
            if not normalized.startswith("assets/") or ".." in Path(normalized).parts or ":" in normalized:
                raise ValueError("图片资源必须通过图片选择器复制到媒体库")

    def save_item(self, item: AudioItem) -> None:
        self._require_media_folder_writable(item)
        if item.key in self._package_warnings:
            raise portable.PackageError(self._package_warnings[item.key])
        if not all(math.isfinite(value) for value in (item.duration, item.start, item.end)):
            raise ValueError("音频范围必须是有效数值")
        if item.duration >= MIN_SEGMENT_SECONDS:
            start, end = valid_range(item.duration, item.start, item.end)
        else:
            start, end = 0.0, max(0.0, item.duration)
        self._check_asset(item.avatar)
        self._check_asset(item.background)
        self._check_asset(item.avatar_source)
        self._check_asset(item.background_source)
        if item.avatar_crop is not None:
            self._validate_crop(item.avatar_crop)
            if not item.avatar_source:
                raise ValueError("裁切坐标必须关联保留的头像原图")
        if not re.fullmatch(r"#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?", item.color):
            raise ValueError("背景色必须为有效的十六进制颜色")
        if item.audio_stream is not None and (type(item.audio_stream) is not int or item.audio_stream < 0):
            raise ValueError("音轨编号无效")
        if item.background_crop is not None:
            self._validate_background_crop(item.background_crop)
            if not item.background_source:
                raise ValueError("背景裁切坐标必须关联保留的原图")
        with self._lock, self._db:
            self._ensure_open()
            existing = self._db.execute("SELECT path FROM items WHERE path=? AND present=1", (item.key,)).fetchone()
            if existing is None:
                raise KeyError("音频不在媒体库中，请先刷新文件夹")
            self._db.execute("UPDATE items SET name=?,start=?,end=?,color=?,avatar=?,background=?,hotkey=?,sort_order=?,avatar_source=?,avatar_crop=?,background_source=?,background_crop=?,audio_stream=? WHERE path=?",
                             (item.name, start, end, item.color, item.avatar, item.background, item.hotkey, item.order,
                              item.avatar_source, json.dumps(item.avatar_crop) if item.avatar_crop is not None else None,
                              item.background_source, json.dumps(item.background_crop) if item.background_crop else None,
                              item.audio_stream, item.key))
            item.start, item.end = start, end
            item.portable_status = "配置保存中"
            self._enqueue_package(item.key)

    @staticmethod
    def _require_media_folder_writable(item: AudioItem) -> None:
        try:
            check_folder_writable(Path(item.path).parent)
        except OSError as error:
            raise OSError("当前音频文件夹不可写，录音和配置保存不可用，请选择可写文件夹") from error

    def save_order(self, items: list[AudioItem]) -> None:
        keys = [item.key for item in items]
        if len(set(keys)) != len(keys):
            raise ValueError("播放顺序不能包含重复音频")
        if len({path_key(Path(item.path).parent) for item in items}) > 1:
            raise ValueError("不能混合排列不同文件夹中的音频")
        with self._lock, self._db:
            self._ensure_open()
            for order, item in enumerate(items):
                cursor = self._db.execute("UPDATE items SET sort_order=? WHERE path=?", (order, item.key))
                if cursor.rowcount != 1:
                    raise KeyError("音频列表已改变，请刷新后重试")
        for order, item in enumerate(items):
            item.order = order

    @staticmethod
    def _temporary(folder: Path) -> Path:
        handle, name = tempfile.mkstemp(prefix=".niulai-", suffix=".pending", dir=folder)
        os.close(handle)
        return Path(name)

    @staticmethod
    def _publish_unique(temporary: Path, folder: Path, stem: str, suffix: str) -> Path:
        for index in range(1, 10001):
            destination = folder / f"{stem}{'' if index == 1 else f' ({index})'}{suffix}"
            if portable.package_path(destination).exists():
                continue
            try:
                if os.name == "nt":
                    # Windows rename fails if destination exists: no check/write race.
                    os.rename(temporary, destination)
                else:
                    os.link(temporary, destination)
                    temporary.unlink()
                return destination
            except FileExistsError:
                continue
        raise FileExistsError("同名音频过多，请使用另一个名称")

    @staticmethod
    def _copy_bytes(source: str | Path, target: Path) -> None:
        with open(source, "rb") as reader, open(target, "wb") as writer:
            shutil.copyfileobj(reader, writer, length=1024 * 1024)
            writer.flush()
            os.fsync(writer.fileno())

    def _import_one(self, value: str, directory: Path) -> str | None:
        source = Path(value).absolute()
        self._probe(source)
        if path_key(source.parent) == path_key(directory):
            return None
        before = self._file_stat(source)
        state = None
        if portable.package_path(source).exists():
            state = portable.read_package(source, self.data_dir / "assets")
        temporary = self._temporary(directory)
        destination = None
        try:
            self._copy_bytes(source, temporary)
            if self._file_stat(source) != before:
                raise OSError("来源文件在复制时发生变化，请重试")
            self._probe(temporary, source.suffix)
            destination = self._publish_unique(temporary, directory, source.stem, source.suffix.lower())
            if state is not None:
                props = dict(state.properties)
                if props.get("name") == source.stem:
                    props["name"] = destination.stem
                portable.write_package(destination, props, state.assets)
            return str(destination)
        except Exception:
            if destination is not None:
                portable.package_path(destination).unlink(missing_ok=True)
                destination.unlink(missing_ok=True)
            raise
        finally:
            temporary.unlink(missing_ok=True)

    def import_batch(self, paths: list[str], folder: str) -> dict:
        """Copy independent media/config pairs; one failure does not undo other imports."""
        directory = Path(folder).absolute()
        if not directory.is_dir():
            raise NotADirectoryError("目标音频文件夹不可用")
        result = {"imported": [], "skipped": [], "failed": []}
        seen = set()
        for value in paths:
            if path_key(value) in seen:
                result["skipped"].append(value)
                continue
            seen.add(path_key(value))
            try:
                copied = self._import_one(value, directory)
                result["imported"].append(copied) if copied else result["skipped"].append(value)
            except Exception as exc:
                result["failed"].append({"path": value, "error": str(exc)})
        return result

    def import_audio(self, paths: list[str], folder: str) -> list[str]:
        """Compatibility API with atomic batch behavior; UI uses import_batch."""
        directory = Path(folder).absolute()
        if not directory.is_dir():
            raise NotADirectoryError("目标音频文件夹不可用")
        created = []
        try:
            for value in paths:
                copied = self._import_one(value, directory)
                if copied:
                    created.append(copied)
            return created
        except Exception:
            for copied in created:
                portable.package_path(copied).unlink(missing_ok=True)
                Path(copied).unlink(missing_ok=True)
            raise

    def export_segment(self, item: AudioItem, folder: str) -> str:
        info = default_decoder().probe(item.path)
        track = next((value for value in info.tracks if value.index == item.audio_stream), None)
        duration, rate = track.duration if track else info.duration, info.sample_rate
        if not math.isfinite(item.start) or not math.isfinite(item.end):
            raise ValueError("片段范围无效")
        start, end = valid_range(duration, item.start, item.end)
        if abs(start - item.start) > 1 / rate or abs(end - item.end) > 1 / rate:
            raise ValueError("音源已改变或片段不足 1 秒，请刷新并重新标记范围")
        directory = Path(folder).absolute()
        temporary = self._temporary(directory)
        # Decoder's destination is create-only; remove only our empty reservation.
        temporary.unlink()
        try:
            default_decoder().export_segment(item.path, temporary, start, end, stream_index=item.audio_stream)
            with open(temporary, "rb+") as ready:
                os.fsync(ready.fileno())
            return str(self._publish_unique(temporary, directory, Path(item.path).stem + "_片段", ".wav"))
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _safe_name(value: str) -> str:
        value = value.strip()
        if not value or len(value) > 180 or re.search(r'[<>:"/\\|?*\x00-\x1f]', value) or value.endswith((" ", ".")):
            raise ValueError("文件名不能为空，不能包含路径、特殊字符或末尾句点")
        if value.split(".", 1)[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
            raise ValueError("不能使用 Windows 保留的设备名称")
        return value

    def rename(self, item: AudioItem, new_name: str) -> str:
        source = Path(item.path)
        stem = self._safe_name(new_name)
        if stem.lower().endswith(source.suffix.lower()):
            stem = self._safe_name(stem[:-len(source.suffix)])
        if stem == source.stem:
            return item.path
        self.flush_pending()
        if item.key in self._portable_errors:
            raise OSError(self._portable_errors[item.key])
        old_key = item.key
        source_package = portable.package_path(source)
        moved_package = None
        with self._lock:
            self._ensure_open()
            if self._db.execute("SELECT path FROM items WHERE path=?", (old_key,)).fetchone() is None:
                raise KeyError("音频不在媒体库中，请先刷新")
            destination = self._publish_unique(source, source.parent, stem, source.suffix)
            try:
                if source_package.exists():
                    moved_package = portable.package_path(destination)
                    os.rename(source_package, moved_package)
                with self._db:
                    # Remove only obsolete metadata at a previously deleted target pathname.
                    if path_key(destination) != old_key:
                        self._db.execute("DELETE FROM items WHERE path=?", (path_key(destination),))
                    self._db.execute("UPDATE items SET path=?,display_path=?,name=? WHERE path=?",
                                     (path_key(destination), str(destination), destination.stem, old_key))
            except Exception:
                # Restore the file if the metadata transaction fails, without overwriting anything.
                if not source.exists():
                    if os.name == "nt":
                        os.rename(destination, source)
                    else:
                        os.link(destination, source)
                        destination.unlink()
                if moved_package and moved_package.exists() and not source_package.exists():
                    os.rename(moved_package, source_package)
                raise
        item.path, item.name = str(destination), destination.stem
        state = self._portable_states.pop(old_key, None)
        self._portable_states[item.key] = state
        self._package_cache.pop(old_key, None)
        self._enqueue_package(item.key)
        return item.path

    def delete(self, item: AudioItem) -> None:
        # send2trash owns Windows recycle-bin behavior; never fall back to permanent deletion.
        self.flush_pending()
        media, package = Path(item.path).absolute(), portable.package_path(item.path)
        backup = None
        # The last complete bundle is also retained in app recovery storage before
        # a potentially partial two-file recycle operation. This is not deletion.
        if package.exists():
            recovery = self.data_dir / "recovery"
            recovery.mkdir(exist_ok=True)
            backup = recovery / f"{uuid.uuid4().hex}-{package.name}"
            self._copy_bytes(package, backup)
        try:
            send2trash(str(media))
            if package.exists():
                send2trash(str(package))
        except OSError as exc:
            if not media.exists():
                with self._lock, self._db:
                    self._db.execute("UPDATE items SET present=0 WHERE path=?", (item.key,))
                raise OSError(f"音频已进入回收站，配置包回收失败；可从回收站还原音频：{exc}") from exc
            if backup is not None:
                backup.unlink(missing_ok=True)
            raise
        with self._lock, self._db:
            self._ensure_open()
            self._db.execute("UPDATE items SET present=0 WHERE path=?", (item.key,))
        if backup is not None:
            backup.unlink(missing_ok=True)

    def deletion_batches(self) -> dict:
        """Find recoverable batches from previous sessions without expiring them."""
        folders = self.get_setting("delete_undo_folders", [])
        batches, errors = file_undo.discover(folders if isinstance(folders, list) else [])
        return {"batches": batches, "errors": errors}

    def stage_delete_batch(self, items: list[AudioItem]) -> dict:
        """Temporarily remove pairs in place; durable journal precedes every move."""
        if not items:
            return {"batch_id": "", "folder": "", "deleted": [], "failed": [], "count": 0}
        folder = str(Path(items[0].path).absolute().parent)
        if any(path_key(Path(item.path).parent) != path_key(folder) for item in items):
            raise ValueError("不能一起删除不同文件夹中的音频")
        self._require_media_folder_writable(items[0])
        with self._undo_lock:
            registry = self.get_setting("delete_undo_folders", [])
            registry = registry if isinstance(registry, list) else []
            if folder not in registry:
                # Register before moving so startup always knows where to look.
                self.set_setting("delete_undo_folders", registry + [folder])
            while True:
                self.flush_pending()
                with self._condition:
                    if self._pending or self._writing is not None:
                        continue
                    self._ensure_open()
                    rows, rejected = [], []
                    for item in items:
                        row = self._db.execute("SELECT * FROM items WHERE path=? AND present=1", (item.key,)).fetchone()
                        if row is None:
                            rejected.append({"path": item.path, "error": "音频列表已改变，请刷新后重试"})
                        elif item.key in self._portable_errors:
                            rejected.append({"path": item.path, "error": self._portable_errors[item.key]})
                        else:
                            rows.append(dict(row))

                    def removed(row):
                        with self._db:
                            self._db.execute("UPDATE items SET present=0 WHERE path=?", (row["path"],))

                    if rows:
                        result = file_undo.stage(folder, rows, removed)
                    else:
                        result = {"batch_id": "", "folder": folder, "deleted": [], "failed": [], "count": 0}
                    result["failed"].extend(rejected)
                    return result

    def undo_delete_batch(self, batch_id: str) -> dict:
        """Restore originals and their exact metadata; no name conflict is overwritten."""
        with self._undo_lock:
            with self._lock:
                columns = {row[1] for row in self._db.execute("PRAGMA table_info(items)")}

            def restored(row):
                if (set(row) != columns or row["path"] != path_key(row["display_path"])
                        or row["folder"] != path_key(Path(row["display_path"]).parent)):
                    raise ValueError("删除恢复记录的配置字段无效，音频已保留")
                values = dict(row, present=1)
                names = list(values)
                with self._lock, self._db:
                    self._ensure_open()
                    existing = self._db.execute(
                        "SELECT path FROM items WHERE folder=? AND present=1 AND path<>? ORDER BY sort_order,path",
                        (row["folder"], row["path"])).fetchall()
                    self._db.execute(
                        f"INSERT INTO items({','.join(names)}) VALUES({','.join('?' for _ in names)}) "
                        "ON CONFLICT(path) DO UPDATE SET " + ','.join(f"{key}=excluded.{key}" for key in names if key != "path"),
                        list(values.values()))
                    order = [entry["path"] for entry in existing]
                    order.insert(min(max(0, int(row["sort_order"])), len(order)), row["path"])
                    self._db.executemany("UPDATE items SET sort_order=? WHERE path=?", enumerate(order))
                    # The restored package is authoritative; stale revisions or
                    # prior write failures must not replace it on the next scan.
                    self._package_cache.pop(row["path"], None)
                    self._portable_states.pop(row["path"], None)
                    self._portable_errors.pop(row["path"], None)
                    self._package_warnings.pop(row["path"], None)

            while True:
                self.flush_pending()
                with self._condition:
                    if self._pending or self._writing is not None:
                        continue
                    self._ensure_open()
                    # Scans and portable writers must not observe the interval
                    # between restoring audio and its companion configuration.
                    return file_undo.restore(batch_id, restored)

    def recycle_delete_batch(self, batch_id: str) -> None:
        with self._undo_lock:
            file_undo.recycle(batch_id)

    def begin_asset_edit(self, path: str, kind: str) -> int:
        """Invalidate older crop workers as soon as the user makes a new choice."""
        if kind not in ("avatar", "background"):
            raise ValueError("外观类型无效")
        with self._lock:
            key = (path_key(path), kind)
            self._asset_revisions[key] = self._asset_revisions.get(key, 0) + 1
            return self._asset_revisions[key]

    def asset_edit_current(self, path: str, kind: str, revision: int) -> bool:
        with self._lock:
            return self._asset_revisions.get((path_key(path), kind)) == revision

    def reset_appearance(self, item: AudioItem, kind: str) -> AudioItem:
        """Reset only one appearance group using the latest database fields."""
        self.begin_asset_edit(item.path, kind)
        with self._lock:
            row = self._db.execute("SELECT * FROM items WHERE path=? AND present=1", (item.key,)).fetchone()
            if row is None:
                raise KeyError("音频不在媒体库中，请先刷新文件夹")
            latest = self._row_item(row)
            if kind == "avatar":
                latest.avatar, latest.avatar_source, latest.avatar_crop = "🐮", "", None
            elif kind == "background":
                latest.color = "#202832"
                latest.background, latest.background_source, latest.background_crop = "", "", None
            self.save_item(latest)
            return latest

    def copy_asset(self, path: str) -> str:
        source = Path(path)
        extension = source.suffix.lower()
        if extension not in {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"}:
            raise ValueError("图片支持 PNG、JPG、BMP、GIF 和 WebP")
        if not 0 < source.stat().st_size <= 25 * 1024 * 1024:
            raise ValueError("图片必须非空且不超过 25 MiB")
        directory = self.data_dir / "assets"
        temporary = self._temporary(directory)
        try:
            self._copy_bytes(source, temporary)
            with open(temporary, "rb") as copied:
                digest = hashlib.file_digest(copied, "sha256").hexdigest()
            target = directory / f"{digest}{extension}"
            if not target.exists():
                os.replace(temporary, target)
            return target.relative_to(self.data_dir).as_posix()
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _validate_crop(crop, width: int | None = None, height: int | None = None) -> tuple[int, int, int, int]:
        if not isinstance(crop, (tuple, list)) or len(crop) != 4 or any(type(value) is not int for value in crop):
            raise ValueError("头像裁切范围必须是整数像素 (x, y, 宽, 高)")
        x, y, side, other_side = crop
        if x < 0 or y < 0 or side < 1 or side != other_side:
            raise ValueError("头像裁切必须是图片内的正方形")
        if width is not None and height is not None and (x + side > width or y + side > height):
            raise ValueError("头像裁切范围超出原图")
        return x, y, side, side

    def apply_avatar_crop(self, item: AudioItem, source_path: str, crop: tuple[int, int, int, int], *,
                          expected_revision: int | None = None) -> bool:
        """Retain the original and atomically apply a 256px square PNG avatar.

        Crop coordinates are integer pixels in the EXIF-oriented original. The
        original is never overwritten; cancellation belongs before this method.
        Metadata failure leaves the previous avatar and the in-memory item intact.
        """
        self._require_media_folder_writable(item)
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QImageReader

        if item.key in self._package_warnings:
            raise portable.PackageError(self._package_warnings[item.key])

        source = Path(source_path)
        if not source.is_absolute():
            self._check_asset(source_path)
            if not source_path.replace("\\", "/").startswith("assets/"):
                raise ValueError("头像原图必须来自所选图片或已保存的图片资源")
            source = self.data_dir / source
        original = self.copy_asset(str(source))
        reader = QImageReader(str(self.data_dir / original))
        reader.setAutoTransform(True)
        size = reader.size()
        if size.width() <= 0 or size.height() <= 0 or size.width() * size.height() > 40_000_000:
            raise ValueError("无法读取图片，或图片超过 4000 万像素")
        image = reader.read()
        if image.isNull():
            raise ValueError("无法读取头像图片：" + reader.errorString())
        actual_crop = self._validate_crop(crop, image.width(), image.height())
        cropped = image.copy(*actual_crop).scaled(256, 256, Qt.AspectRatioMode.IgnoreAspectRatio,
                                                  Qt.TransformationMode.SmoothTransformation)
        directory = self.data_dir / "assets"
        temporary = self._temporary(directory)
        try:
            if not cropped.save(str(temporary), "PNG"):
                raise OSError("无法保存裁切头像，请检查磁盘空间及文件夹权限")
            with open(temporary, "rb+") as image_file:
                os.fsync(image_file.fileno())
                digest = hashlib.file_digest(image_file, "sha256").hexdigest()
            destination = directory / f"avatar-{digest}.png"
            if not destination.exists():
                os.replace(temporary, destination)
            avatar = destination.relative_to(self.data_dir).as_posix()
            # A crop may be computed in a worker from an earlier AudioItem
            # snapshot. Update only avatar fields so concurrent range, color,
            # shortcut and ordering edits are never overwritten.
            with self._lock, self._db:
                self._ensure_open()
                if (expected_revision is not None
                        and self._asset_revisions.get((item.key, "avatar")) != expected_revision):
                    return False
                cursor = self._db.execute("UPDATE items SET avatar=?,avatar_source=?,avatar_crop=? WHERE path=? AND present=1",
                                          (avatar, original, json.dumps(actual_crop), item.key))
                if cursor.rowcount != 1:
                    raise KeyError("音频不在媒体库中，请先刷新文件夹")
                item.avatar, item.avatar_source, item.avatar_crop = avatar, original, actual_crop
                item.portable_status = "配置保存中"
                self._enqueue_package(item.key)
                return True
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _validate_background_crop(crop, width=None, height=None):
        if (not isinstance(crop, (tuple, list)) or len(crop) != 4
                or any(type(value) is not int for value in crop)
                or min(crop[:2]) < 0 or min(crop[2:]) < 1):
            raise ValueError("背景裁切范围必须是图片内的整数像素矩形")
        x, y, w, h = crop
        if width is not None and (x + w > width or y + h > height):
            raise ValueError("背景裁切范围超出原图")
        return tuple(crop)

    def apply_background_crop(self, item: AudioItem, source_path: str, crop: tuple[int, int, int, int], *,
                              expected_revision: int | None = None) -> bool:
        self._require_media_folder_writable(item)
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QImageReader
        if item.key in self._package_warnings:
            raise portable.PackageError(self._package_warnings[item.key])
        source = Path(source_path)
        if not source.is_absolute():
            self._check_asset(source_path)
            source = self.data_dir / source
        original = self.copy_asset(str(source))
        reader = QImageReader(str(self.data_dir / original))
        reader.setAutoTransform(True)
        size = reader.size()
        if size.width() <= 0 or size.height() <= 0 or size.width() * size.height() > 40_000_000:
            raise ValueError("无法读取图片，或图片超过 4000 万像素")
        image = reader.read()
        if image.isNull():
            raise ValueError("无法读取背景图片：" + reader.errorString())
        actual_crop = self._validate_background_crop(crop, image.width(), image.height())
        cropped = image.copy(*actual_crop)
        if cropped.width() > 1600:
            cropped = cropped.scaledToWidth(1600, Qt.TransformationMode.SmoothTransformation)
        temporary = self._temporary(self.data_dir / "assets")
        try:
            if not cropped.save(str(temporary), "PNG"):
                raise OSError("无法保存裁切背景，请检查磁盘空间及文件夹权限")
            with temporary.open("rb+") as image_file:
                os.fsync(image_file.fileno())
                digest = hashlib.file_digest(image_file, "sha256").hexdigest()
            destination = self.data_dir / "assets" / f"background-{digest}.png"
            if not destination.exists():
                os.replace(temporary, destination)
            background = destination.relative_to(self.data_dir).as_posix()
            with self._lock, self._db:
                self._ensure_open()
                if (expected_revision is not None
                        and self._asset_revisions.get((item.key, "background")) != expected_revision):
                    return False
                cursor = self._db.execute("UPDATE items SET background=?,background_source=?,background_crop=? WHERE path=? AND present=1",
                                          (background, original, json.dumps(actual_crop), item.key))
                if cursor.rowcount != 1:
                    raise KeyError("音频不在媒体库中，请先刷新文件夹")
                item.background, item.background_source, item.background_crop = background, original, actual_crop
                item.portable_status = "配置保存中"
                self._enqueue_package(item.key)
                return True
        finally:
            temporary.unlink(missing_ok=True)

    def waveform(self, path: str, bins: int = 320, *, stream_index: int | None = None) -> list[float]:
        if not 1 <= bins <= 16384:
            raise ValueError("波形采样点数必须为 1 至 16384")
        size, modified = self._file_stat(path)
        fingerprint = f"ffmpeg-v1|{path_key(path)}|{size}|{modified}|{stream_index}|{bins}"
        key = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()
        target = self.data_dir / "cache" / f"{key}.json"
        try:
            cached = json.loads(target.read_text(encoding="utf-8"))
            if len(cached) == bins and all(isinstance(value, (int, float)) and math.isfinite(value) and 0 <= value <= 1 for value in cached):
                return cached
        except (OSError, ValueError, TypeError):
            pass
        result = default_decoder().waveform(path, bins, stream_index=stream_index)
        if self._file_stat(path) != (size, modified):
            raise OSError("音频在生成波形时发生变化，请重试")
        temporary = self._temporary(target.parent)
        try:
            temporary.write_text(json.dumps(result, allow_nan=False), encoding="utf-8")
            os.replace(temporary, target)
        except OSError:
            pass  # A disposable visualization cache is optional.
        finally:
            temporary.unlink(missing_ok=True)
        return result

    def close(self) -> None:
        errors = self.flush_pending()
        with self._condition:
            if self._closed:
                return
            self._worker_stop = True
            self._condition.notify_all()
        self._writer.join()
        with self._lock:
            self._db.close()
            self._closed = True
        if errors:
            raise OSError("\n".join(errors))
