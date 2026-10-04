"""Transactional, per-user ownership of a temporary communication input setting."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import threading
import time
import uuid

from .windows_audio import WindowsAudioBackend


class TemporarySystemSettings:
    """Call from a worker. Construction does not recover or change any OS setting.

    All application instances must pass the canonical user data directory. A
    process-held byte lock serializes owners; an atomic journal survives crashes.
    No watchdog is used: recovery after a force-kill happens at the next launch.
    """

    def __init__(self, data_dir, backend=None):
        self.directory = Path(data_dir).resolve()
        self.journal = self.directory / "temporary-communications.json"
        self.lock_path = self.directory / "temporary-communications.lock"
        self.backend = backend if backend is not None else WindowsAudioBackend()
        self._mutex = threading.RLock()
        self._owner = None
        self._token = uuid.uuid4().hex
        self._closed = False
        self._last = self._result("idle", "尚未临时配置通信输入")

    @staticmethod
    def _result(status, message, entry=None, **extra):
        entry = entry or {}
        result = dict(status=status, message=message, original_id=entry.get("original_id", ""),
                      target_id=entry.get("target_id", ""),
                      ok=status in ("active", "restored", "unchanged", "idle", "external_change"))
        result.update(extra)
        return result

    def status(self):
        with self._mutex:
            return self._last.copy()

    def _remember(self, status, message, entry=None, **extra):
        self._last = self._result(status, message, entry, **extra)
        return self._last.copy()

    def _acquire(self):
        if self._closed:
            raise RuntimeError("系统设置服务已关闭")
        if self._owner:
            return True
        self.directory.mkdir(parents=True, exist_ok=True)
        handle = self.lock_path.open("a+b")
        try:
            if handle.seek(0, os.SEEK_END) == 0:
                handle.write(b"0"); handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        self._owner = handle
        return True

    def _load(self):
        if not self.journal.exists():
            return None
        entry = json.loads(self.journal.read_text(encoding="utf-8"))
        if (entry.get("version") != 1 or entry.get("role") != 2 or entry.get("flow") != "capture"
                or not all(isinstance(entry.get(key), str) and entry[key] for key in ("original_id", "target_id", "token"))):
            raise ValueError("恢复记录无效，未修改系统；请手动检查默认通信输入")
        return entry

    def _write(self, entry):
        name = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.directory,
                                             prefix=".communications-", suffix=".tmp", delete=False) as file:
                name = file.name
                json.dump(entry, file, ensure_ascii=False)
                file.flush()
                os.fsync(file.fileno())
            os.replace(name, self.journal)
        finally:
            if name and Path(name).exists():
                Path(name).unlink()

    def _clear(self):
        self.journal.unlink(missing_ok=True)

    def apply(self, target_endpoint_id):
        with self._mutex:
            entry = None
            try:
                if not self._acquire():
                    return self._remember("busy", "另一份 New Life 正在管理通信输入，请先退出另一份软件")
                if not self.backend.endpoint_available(target_endpoint_id):
                    return self._remember("failed", "CABLE Output 当前不可用，未修改系统")
                entry = self._load()
                current = self.backend.get_default_communications_input()
                if entry:
                    if current == entry["target_id"] and target_endpoint_id == entry["target_id"]:
                        return self._remember("active", "临时通信输入已配置，原设备记录保持不变", entry)
                    result = self._restore("before_apply")
                    if result["status"] in ("pending", "failed"):
                        return result
                    current = self.backend.get_default_communications_input()
                if current == target_endpoint_id:
                    return self._remember("unchanged", "通信输入原本就是 CABLE Output，无需恢复", target_id=target_endpoint_id)
                if not current or not self.backend.endpoint_available(current):
                    return self._remember("failed", "原通信输入无法确认，未修改系统")
                entry = dict(version=1, role=2, flow="capture", original_id=current, target_id=target_endpoint_id,
                             token=self._token, pid=os.getpid(), created_at=time.time(), phase="prepared")
                self._write(entry)  # Must succeed before the only system mutation.
                if self.backend.get_default_communications_input() != current:
                    self._clear()
                    return self._remember("external_change", "通信输入已被其他操作更改，本次未接管", ok=False)
                self.backend.set_default_communications_input(target_endpoint_id)
                if self.backend.get_default_communications_input() != target_endpoint_id:
                    raise RuntimeError("Windows 未确认通信输入切换")
                entry["phase"] = "active"
                self._write(entry)
                return self._remember("active", "已临时配置 CABLE Output；断开或退出时恢复", entry)
            except Exception as error:
                # Even a setter that reports failure may have changed the default.
                # The prepared journal supplies the original for safe rollback.
                if entry and self.journal.exists():
                    result = self._restore("apply_failed")
                    if result["status"] == "pending":
                        return self._remember("pending", f"配置失败，恢复尚未完成：{error}；{result['message']}", entry)
                return self._remember("failed", f"临时配置失败：{error}", entry)

    def _restore(self, reason):
        entry = None
        try:
            entry = self._load()
            if not entry:
                return self._remember("idle", "没有待恢复的通信设置")
            current = self.backend.get_default_communications_input()
            if current != entry["target_id"]:
                self._clear()
                return self._remember("external_change", "通信输入已改变，保留当前选择", entry)
            if not self.backend.endpoint_available(entry["original_id"]):
                return self._remember("pending", "原麦克风当前不可用，已保留恢复记录", entry)
            # Enumeration may be slow. Recheck directly before mutation so a
            # user/device change during it does not get overwritten.
            if self.backend.get_default_communications_input() != entry["target_id"]:
                self._clear()
                return self._remember("external_change", "通信输入已改变，保留当前选择", entry)
            self.backend.set_default_communications_input(entry["original_id"])
            if self.backend.get_default_communications_input() != entry["original_id"]:
                raise RuntimeError("Windows 未确认恢复原通信输入")
            self._clear()
            return self._remember("restored", "已恢复原默认通信输入", entry)
        except Exception as error:
            return self._remember("pending", f"恢复未完成：{error}；可在 Windows 声音设置中手动恢复", entry)

    def restore(self, reason="disconnect"):
        with self._mutex:
            try:
                if not self._acquire():
                    return self._remember("busy", "通信设置由另一份软件管理")
                return self._restore(reason)
            except Exception as error:
                return self._remember("pending", str(error))

    def recover(self):
        return self.restore("startup")

    def close(self):
        with self._mutex:
            if self._closed:
                return self._last.copy()
            try:
                result = self._restore("exit") if self._owner else self._last.copy()
            finally:
                if self._owner:
                    self._owner.close()  # OS releases the exclusive byte lock.
                    self._owner = None
                self._closed = True
            return result


class PersistentSystemSettings(TemporarySystemSettings):
    """User-selected system defaults persist. Legacy journals are retired."""

    def recover(self):
        with self._mutex:
            if not self._acquire():
                return self._remember("pending", "其他实例正在使用系统设置，请先退出旧版")
            if self.journal.exists():
                self.journal.replace(self.journal.with_name("communications-legacy-" + uuid.uuid4().hex + ".json"))
            return self._remember("idle", "系统设备修改会保留，退出不会恢复")

    def apply(self, target_endpoint_id):
        with self._mutex:
            if not self._acquire():
                return self._remember("pending", "其他实例正在使用系统设置，请先退出旧版")
            self.backend.set_default_communications_input(target_endpoint_id)
            return self._remember("active", "已将通话麦克风设为 CABLE Output；退出后保留。通话软件仍需选择该设备。")

    def restore(self, reason="disconnect"):
        return self._remember("idle", "系统设备设置保持不变")

    def close(self):
        with self._mutex:
            if self._owner:
                self._owner.close()
                self._owner = None
            self._closed = True
            return self._last.copy()
