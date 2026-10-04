"""Recoverable, same-volume deletion staging; user files are never erased.

The journal is committed before any move. A batch can be restored after a
process crash, and recycling operates on the whole batch directory so audio and
portable configuration remain together even if Windows rejects the operation.
All entry points perform blocking I/O and belong on a worker thread.
"""
from __future__ import annotations

from contextlib import suppress
import json
import os
from pathlib import Path
import time
import uuid

from send2trash import send2trash

from .models import SUPPORTED_EXTENSIONS, path_key
from .portable import package_path

DIRECTORY = ".newlife-undo"


def _move(source: Path, destination: Path) -> None:
    """Create-only move: existing destinations are never overwritten."""
    if os.name == "nt":
        os.rename(source, destination)
    else:
        os.link(source, destination)
        source.unlink()


def _identity(path: Path) -> dict:
    stat = path.stat()
    return {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns,
            "ino": stat.st_ino, "dev": stat.st_dev}


def _matches(path: Path, identity: dict) -> bool:
    try:
        return not path.is_symlink() and _identity(path) == identity
    except OSError:
        return False


def _write(batch: Path, journal: dict) -> None:
    temporary = batch / ".journal.pending"
    with temporary.open("w", encoding="utf-8") as writer:
        json.dump(journal, writer, ensure_ascii=False, allow_nan=False)
        writer.flush()
        os.fsync(writer.fileno())
    os.replace(temporary, batch / "journal.json")


def _read(batch: Path) -> dict:
    if batch.is_symlink() or batch.parent.is_symlink() or batch.parent.name != DIRECTORY:
        raise ValueError("删除恢复目录无效")
    uuid.UUID(batch.name)
    path = batch / "journal.json"
    if path.is_symlink() or path.stat().st_size > 4 * 1024 * 1024:
        raise ValueError("删除恢复记录无效")
    journal = json.loads(path.read_text(encoding="utf-8"))
    if (journal.get("version") != 1 or not isinstance(journal.get("entries"), list)
            or path_key(journal.get("folder", "")) != path_key(batch.parent.parent)):
        raise ValueError("删除恢复记录无效")
    for index, entry in enumerate(journal["entries"]):
        source = Path(entry["row"]["display_path"])
        if (path_key(source.parent) != path_key(batch.parent.parent)
                or source.suffix.lower() not in SUPPORTED_EXTENSIONS
                or entry.get("slot") != str(index)):
            raise ValueError("删除恢复记录的音频位置无效")
    return journal


def _files(batch: Path, entry: dict):
    source = Path(entry["row"]["display_path"])
    slot = batch / entry["slot"]
    if slot.is_symlink():
        raise ValueError("删除恢复目录无效")
    yield source, slot / "audio", entry["media_identity"]
    if entry.get("package_identity") is not None:
        yield package_path(source), slot / "config.newlife", entry["package_identity"]


def count_pending(batch: str | Path) -> int:
    return sum(entry["status"] not in ("restored", "ignored") and entry.get("media_identity") is not None
               for entry in _read(Path(batch))["entries"])


def discover(folders: list[str]) -> tuple[list[dict], list[str]]:
    batches, errors = [], []
    for folder in dict.fromkeys(folders):
        directory = Path(folder) / DIRECTORY
        if not directory.exists():
            continue
        try:
            if directory.is_symlink():
                raise ValueError("删除恢复目录不能是链接")
            for batch in directory.iterdir():
                if not batch.is_dir():
                    continue
                try:
                    journal = _read(batch)
                    count = sum(entry["status"] not in ("restored", "ignored") and entry.get("media_identity") is not None
                                for entry in journal["entries"])
                    if count:
                        batches.append({"batch_id": str(batch), "folder": folder,
                                        "count": count, "created": journal["created"]})
                except (OSError, ValueError, KeyError, TypeError) as error:
                    errors.append(f"{folder}：删除恢复记录不可用，暂存文件已保留：{error}")
        except OSError as error:
            errors.append(f"{folder}：无法检查删除恢复记录，文件已保留：{error}")
    return sorted(batches, key=lambda batch: batch["created"]), errors


def stage(folder: str, rows: list[dict], on_removed) -> dict:
    directory = Path(folder).absolute() / DIRECTORY
    if directory.is_symlink():
        raise ValueError("删除暂存目录不能是链接")
    directory.mkdir(exist_ok=True)
    batch = directory / str(uuid.uuid4())
    batch.mkdir()
    entries = [{"slot": str(index), "row": row, "status": "prepared",
                "media_identity": None, "package_identity": None}
               for index, row in enumerate(rows)]
    journal = {"version": 1, "folder": str(Path(folder).absolute()),
               "created": time.time(), "entries": entries}
    _write(batch, journal)
    deleted, failed = [], []
    for entry in entries:
        source = Path(entry["row"]["display_path"])
        try:
            if (source.is_symlink() or path_key(source.parent) != path_key(folder)
                    or source.suffix.lower() not in SUPPORTED_EXTENSIONS):
                raise ValueError("只能删除当前文件夹内的真实音频文件")
            entry["media_identity"] = _identity(source)
            companion = package_path(source)
            if companion.exists():
                if companion.is_symlink():
                    raise ValueError("配置包不能是链接")
                entry["package_identity"] = _identity(companion)
            slot = batch / entry["slot"]
            slot.mkdir()
            _write(batch, journal)
            for original, staged, _ in _files(batch, entry):
                _move(original, staged)
            on_removed(entry["row"])
            entry["status"] = "staged"
            _write(batch, journal)
            deleted.append(str(source))
        except Exception as error:
            rollback_errors = []
            for original, staged, _ in _files(batch, entry):
                if staged.exists():
                    try:
                        _move(staged, original)
                    except OSError as rollback_error:
                        rollback_errors.append(str(rollback_error))
            entry["status"] = "staged" if rollback_errors else "ignored"
            with suppress(OSError):
                _write(batch, journal)
            detail = str(error)
            if rollback_errors:
                detail += "；部分文件已保存在删除暂存目录，可撤销恢复：" + "; ".join(rollback_errors)
            failed.append({"path": str(source), "error": detail})
    count = sum(entry["status"] not in ("restored", "ignored") for entry in entries)
    cleanup_warning = ""
    if not count:
        cleanup_warning = _clean_empty(batch, journal)
    return {"batch_id": str(batch) if count else "", "folder": folder,
            "deleted": deleted, "failed": failed, "count": count, "cleanup_warning": cleanup_warning}


def restore(batch_id: str, on_restored) -> dict:
    batch = Path(batch_id)
    journal = _read(batch)
    restored, failed = [], []
    for entry in journal["entries"]:
        if entry["status"] in ("restored", "ignored"):
            continue
        if entry.get("media_identity") is None:
            entry["status"] = "ignored"
            continue  # The process stopped before any of this entry's files moved.
        source = Path(entry["row"]["display_path"])
        try:
            pairs = list(_files(batch, entry))
            # Preflight both destinations before moving either file. A partial
            # crash recovery accepts only the identical file previously moved.
            for original, staged, identity in pairs:
                if staged.is_symlink():
                    raise ValueError("删除暂存文件不能是链接")
                if original.exists() or original.is_symlink():
                    if staged.exists() or not _matches(original, identity):
                        raise FileExistsError("原位置已有同名文件，未覆盖；请移开该文件后再次撤销")
                elif not staged.is_file():
                    raise FileNotFoundError("删除暂存文件缺失，请检查回收站或暂存目录")
                elif not _matches(staged, identity):
                    raise ValueError("删除暂存文件已被修改，已保留供手动恢复")
            entry["status"] = "restoring"
            _write(batch, journal)
            for original, staged, _ in pairs:
                if staged.exists():
                    _move(staged, original)
            on_restored(entry["row"])
            entry["status"] = "restored"
            _write(batch, journal)
            restored.append(str(source))
        except Exception as error:
            failed.append({"path": str(source), "error": str(error)})
    count = sum(entry["status"] not in ("restored", "ignored") for entry in journal["entries"])
    cleanup_warning = ""
    if not count:
        cleanup_warning = _clean_empty(batch, journal)
    return {"batch_id": batch_id if count else "", "folder": journal["folder"],
            "restored": restored, "failed": failed, "count": count, "cleanup_warning": cleanup_warning}


def _clean_empty(batch: Path, journal: dict) -> str:
    """Remove only our journal and empty directories, never audio/config files."""
    for entry in journal["entries"]:
        with suppress(OSError):
            (batch / entry["slot"]).rmdir()
    try:
        # Unknown files keep the complete journal available for inspection.
        if all(path.name in ("journal.json", ".journal.pending") for path in batch.iterdir()):
            (batch / "journal.json").unlink(missing_ok=True)
            (batch / ".journal.pending").unlink(missing_ok=True)
            with suppress(OSError):
                batch.rmdir()
    except OSError as error:
        return str(error)  # Audio/config restoration has already completed.
    with suppress(OSError):
        batch.parent.rmdir()
    return ""


def recycle(batch_id: str) -> None:
    batch = Path(batch_id)
    _read(batch)  # Reject paths outside the registered staging structure.
    # A single recycle operation preserves the pair and its recovery metadata.
    # Errors deliberately leave the complete batch available for another try.
    send2trash(str(batch))
    with suppress(OSError):
        batch.parent.rmdir()
