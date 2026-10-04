"""共享控制器：所有窗口、托盘和快捷键都经过这里改变状态。"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import logging
import math
import os
from pathlib import Path
import re

from PySide6.QtCore import QObject, Signal, QTimer, QFileSystemWatcher, QCoreApplication

from .hotkeys import HotkeyService
from .library import Library
from .models import AudioItem, path_key, valid_range, SUPPORTED_EXTENSIONS
from .music_folder import check_folder_writable, default_music_folder, is_readable_folder

log = logging.getLogger(__name__)

DEFAULTS = {
    "local_volume": 70, "send_volume": 70, "mic_volume": 100,
    "mode": 0, "send_to_call": False, "recording_source": "麦克风",
    "mic_device": "", "system_device": "", "local_device": "", "call_device": "",
    "mini_on_top": True, "global_hotkeys": {}, "ui_language": "zh_CN",
    "theme": "system",
}


class Controller(QObject):
    items_changed = Signal()
    state_changed = Signal()
    message = Signal(str, bool)
    import_finished = Signal(object)
    recording_completed = Signal(str)
    waveform_ready = Signal(str, list)
    request_expand = Signal()
    _event = Signal(str, object)
    _result = Signal(int, object, object)

    def __init__(self, data_dir: str | Path, engine_factory=None, folder: str = "", no_hotkeys: bool = False,
                 system_settings_factory=None, enable_system_settings: bool | None = None):
        # Qt owns this dispatcher on the GUI thread even when a worker releases
        # the last Python callback reference during a slow decoder shutdown.
        super().__init__(QCoreApplication.instance())
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.library = Library(self.data_dir)
        self.settings = {key: self.library.get_setting(key, value) for key, value in DEFAULTS.items()}
        self.items: list[AudioItem] = []
        self.folder = ""
        self.state = dict(self.settings, path="", playing=False, paused=False, position=0.0,
                          recording=False, record_seconds=0.0, mark_pending=False,
                          call_connected=False, call_connecting=False, call_ready=False, loading=False,
                          call_peak=0.0, local_peak=0.0, mic_peak=0.0, diagnostics={},
                          call_input_name="", call_endpoint_id="", call_diagnostic="",
                          folder_writable=False, folder_warning="",
                          delete_busy=False, delete_undo_available=False, delete_undo_count=0,
                          recent_recording_path="", recent_recording_revision=0,
                          temporary_config_status="idle", temporary_config_message="")
        if self.settings["ui_language"] not in ("zh_CN", "en"):
            self.settings["ui_language"] = self.state["ui_language"] = "zh_CN"
        self.system_settings = None
        self._startup_system_message = ""
        self._startup_system_error = False
        if enable_system_settings is None:
            enable_system_settings = engine_factory is None or system_settings_factory is not None
        if enable_system_settings:
            try:
                from .system_settings import PersistentSystemSettings
                factory = system_settings_factory or PersistentSystemSettings
                # This journal belongs to the Windows user, including isolated developer profiles.
                system_dir = Path(os.environ.get("LOCALAPPDATA", str(self.data_dir.parent))) / "NiuLaiPlayerPython"
                self.system_settings = factory(system_dir)
                recovered = self.system_settings.recover()
                self._accept_system_result(recovered, emit=False)
                if recovered.get("status") not in ("idle", "none", "unchanged"):
                    self._startup_system_message = recovered.get("message", "")
                    self._startup_system_error = not recovered.get("ok", True)
            except Exception as error:
                self._startup_system_message = f"临时系统配置不可用：{error}"
                self._startup_system_error = True
        self._cable_pair = None
        self._system_pair_busy = False
        self._temporary_call_requested = False
        self._temporary_call_endpoint = ""
        self._requested_call_output = ""
        self._call_connect_pending = False
        self._shutdown_errors = []
        self._closed = False
        self._devices = []
        self._generation = 0
        self._token = 0
        self._pending_play_path = ""
        self._next_job = 0
        self._jobs = {}
        self._wave_pending = set()
        self._signature = None
        self._scan_busy = False
        self._scan_again = False
        self._edit_revision = 0
        self._device_refresh_busy = False
        self._record_command_pending = False
        self._undo_batches = []
        self._session_deletion_ids = set()
        self._undo_timer = QTimer(self)
        self._undo_timer.setSingleShot(True)
        self._undo_timer.setInterval(30_000)
        self._undo_timer.timeout.connect(self._expire_delete_undo)
        self._executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="media-library")
        self._event.connect(self._on_audio_event)
        self._result.connect(self._on_result)
        if engine_factory is None:
            from .audio import AudioEngine
            engine_factory = AudioEngine
        self.engine = engine_factory(lambda name, payload: self._event.emit(name, payload))
        self.hotkeys = HotkeyService(self._on_hotkey, enabled=not no_hotkeys)
        self._apply_audio_settings()
        self.watcher = QFileSystemWatcher(self)
        self.watcher.directoryChanged.connect(self._folder_changed)
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(300)
        self._debounce.timeout.connect(self.reload)
        self._poll = QTimer(self)
        self._poll.setInterval(80)
        self._poll.timeout.connect(self._poll_state)
        self._poll.start()
        self._folder_poll = QTimer(self)
        self._folder_poll.setInterval(1800)
        self._folder_poll.timeout.connect(self._check_folder)
        self._folder_poll.start()
        self.refresh_devices()
        self._system_follow_busy = False
        self._system_follow = QTimer(self)
        self._system_follow.setInterval(1500)
        self._system_follow.timeout.connect(self.follow_system_devices)
        self._system_follow.start()
        self._initialize_folder(folder)
        self._submit(self.library.deletion_batches, self._accept_recovered_deletions)
        if self._startup_system_message:
            QTimer.singleShot(0, lambda: self.message.emit(self._startup_system_message, self._startup_system_error))

    def _submit(self, function, success=None, failure=None):
        if self._closed:
            return
        self._next_job += 1
        identifier = self._next_job
        self._jobs[identifier] = (success, failure)
        future = self._executor.submit(function)

        def completed(job):
            if self._closed:
                return
            try:
                result = job.result()
                self._result.emit(identifier, result, None)
            except Exception as error:
                log.exception("媒体任务失败", exc_info=error)
                self._result.emit(identifier, None, error)
        future.add_done_callback(completed)

    def _on_result(self, identifier, result, error):
        callbacks = self._jobs.pop(identifier, (None, None))
        if self._closed:
            return
        try:
            if error is not None:
                if callbacks[1]:
                    callbacks[1](error)
                else:
                    self.message.emit(str(error), True)
            elif callbacks[0]:
                callbacks[0](result)
        except Exception as error:
            log.exception("媒体结果处理失败")
            self.message.emit(str(error), True)

    def get_item(self, path: str) -> AudioItem | None:
        key = path_key(path)
        return next((item for item in self.items if item.key == key), None)

    def resolve_asset(self, path: str) -> str:
        if not path:
            return ""
        candidate = Path(path)
        return str(candidate if candidate.is_absolute() else self.data_dir / candidate)

    def _initialize_folder(self, requested: str):
        messages = []
        saved = self.library.get_setting("folder", "")
        chosen = requested or saved
        if chosen and not is_readable_folder(chosen):
            messages.append("启动指定的音频文件夹不存在或无法访问，已切换到程序旁的 MUSIC，请重新选择文件夹"
                            if requested else
                            "上次使用的音频文件夹不存在或无法访问，已切换到程序旁的 MUSIC，请重新选择文件夹")
            chosen = ""
        if not chosen:
            chosen = default_music_folder()
            try:
                Path(chosen).mkdir(parents=True, exist_ok=True)
            except OSError:
                # The fallback itself failed; do not claim that MUSIC was opened.
                messages = ["音频文件夹不存在或无法访问，请重新选择"] if messages else []
                self.state.update(folder_writable=False,
                                  folder_warning="无法创建程序旁的 MUSIC 文件夹，请选择可写的音频文件夹")
                messages.append(self.state["folder_warning"])
                for message in messages:
                    QTimer.singleShot(0, lambda text=message: self.message.emit(text, True))
                return
        self.set_folder(str(chosen), defer_errors=True)
        if self.state.get("folder_warning"):
            messages.append(self.state["folder_warning"])
        for message in messages:
            QTimer.singleShot(0, lambda text=message: self.message.emit(text, True))

    def _refresh_folder_write_access(self):
        try:
            if not self.folder:
                raise OSError("No audio folder is selected")
            check_folder_writable(self.folder)
        except OSError:
            self.state.update(folder_writable=False,
                              folder_warning="当前音频文件夹不可写，录音和配置保存不可用，请选择可写文件夹")
            return False
        self.state.update(folder_writable=True, folder_warning="")
        return True

    def set_folder(self, folder: str, *, defer_errors: bool = False):
        if self.state.get("delete_busy"):
            self.message.emit("文件操作尚未完成，请稍后再切换文件夹", True)
            return
        if self.state.get("recording") or self._record_command_pending:
            self.message.emit("请先停止录音，再切换文件夹", True)
            return
        candidate = Path(folder).expanduser().absolute()
        if not is_readable_folder(candidate):
            text = "音频文件夹不存在或无法访问，请重新选择"
            if defer_errors:
                QTimer.singleShot(0, lambda: self.message.emit(text, True))
            else:
                self.message.emit(text, True)
            return
        self.stop()
        self._generation += 1
        self._wave_pending.clear()
        self._scan_busy = False
        self._scan_again = False
        self.folder = str(candidate)
        self.items = []
        try:
            self.library.set_setting("folder", self.folder)
        except Exception as error:
            text = f"文件夹选择保存失败：{error}"
            if defer_errors:
                QTimer.singleShot(0, lambda: self.message.emit(text, True))
            else:
                self.message.emit(text, True)
        writable = self._refresh_folder_write_access()
        if not writable and not defer_errors:
            self.message.emit(self.state["folder_warning"], True)
        self.hotkeys.replace(self._bindings())
        if self.watcher.directories():
            self.watcher.removePaths(self.watcher.directories())
        self.watcher.addPath(self.folder)
        self._signature = None
        self.items_changed.emit()
        if writable and hasattr(self.engine, "recover_recordings"):
            current_folder = self.folder
            self._submit(lambda: self.engine.recover_recordings(current_folder), lambda _: self.reload())
        self.reload()

    def _folder_changed(self, _path):
        self._debounce.start()

    def _check_folder(self):
        if not self.folder or self._closed:
            return
        try:
            signature = tuple(sorted((entry.name, entry.stat().st_size, entry.stat().st_mtime_ns)
                                     for entry in Path(self.folder).iterdir()
                                     if entry.is_file() and (entry.suffix.lower() in SUPPORTED_EXTENSIONS
                                                             or entry.name.lower().endswith(".newlife"))))
        except OSError:
            if self.state.get("playing"):
                self.stop()
            if self._signature is not False:
                self.message.emit("当前文件夹无法访问，已停止片段播放，请重新选择文件夹", True)
                self._signature = False
            return
        if signature != self._signature:
            self._signature = signature
            self.reload()

    def reload(self):
        if self._closed or not self.folder:
            return
        if self.state.get("delete_busy"):
            self._scan_again = True
            return
        if self._scan_busy:
            self._scan_again = True
            return
        self._scan_busy = True
        folder, generation, revision = self.folder, self._generation, self._edit_revision
        self.state["loading"] = True
        self.state_changed.emit()

        def success(items):
            if generation != self._generation:
                return
            self._scan_busy = False
            if self.state.get("delete_busy"):
                self._scan_again = True
                self.state["loading"] = False
                return
            if revision != self._edit_revision:
                self._scan_again = False
                self.reload()
                return
            self.state["loading"] = False
            self.items = items
            path = self.state.get("path", "")
            if path and self.get_item(path) is None:
                self.stop()
                self.state["path"] = ""
            self._refresh_hotkeys()
            self.items_changed.emit()
            self.state_changed.emit()
            # Do not decode every long video merely because a folder was opened.
            selected = self.get_item(path) or next((entry for entry in self.items if entry.playable), None)
            if selected:
                self.request_waveform(selected.path)
            if self._scan_again:
                self._scan_again = False
                self.reload()

        def failure(error):
            if generation == self._generation:
                self._scan_busy = False
                self.state["loading"] = False
                self.state_changed.emit()
                self.message.emit(f"读取文件夹失败：{error}", True)
                if self._scan_again:
                    self._scan_again = False
                    self.reload()
        self._submit(lambda: self.library.scan(folder), success, failure)

    def request_waveform(self, path):
        item = self.get_item(path)
        if item is None or item.peaks or item.error or item.key in self._wave_pending:
            return
        key, generation = item.key, self._generation
        stream = getattr(item, "audio_stream", None)
        try:
            status = os.stat(path)
            fingerprint = (status.st_size, status.st_mtime_ns)
        except OSError:
            return
        self._wave_pending.add(key)

        def success(peaks):
            self._wave_pending.discard(key)
            current = self.get_item(path)
            if generation == self._generation and current and getattr(current, "audio_stream", None) == stream:
                try:
                    status = os.stat(path)
                    if fingerprint != (status.st_size, status.st_mtime_ns):
                        self.request_waveform(path)
                        return
                except OSError:
                    return
                current.peaks = peaks
                self.waveform_ready.emit(path, peaks)

        def failure(error):
            self._wave_pending.discard(key)
            log.warning("波形不可用 %s: %s", path, error)
        self._submit(lambda: self.library.waveform(path, stream_index=stream) if stream is not None
                     else self.library.waveform(path), success, failure)

    def trigger(self, path: str):
        item = self.get_item(path)
        if item is None:
            return
        if ((self.state.get("playing") and path_key(self.state.get("path", "")) == item.key)
                or (self._pending_play_path and path_key(self._pending_play_path) == item.key)):
            self.stop()
            return
        self._play(item)

    def _play(self, item):
        if not item.playable:
            self.message.emit(item.error or "音频不足 1 秒，无法播放片段", True)
            return
        self._token += 1
        self._pending_play_path = item.path
        self.state.update(path=item.path, position=item.start, paused=False, playing=True)
        options = dict(send=self.settings["send_to_call"], token=self._token)
        stream = getattr(item, "audio_stream", None)
        if stream is not None:
            options["stream_index"] = stream
        self.engine.play(item.path, item.start, item.end, **options)
        self.request_waveform(item.path)
        self.state_changed.emit()

    def seek(self, seconds):
        """Move only the active clip; its range and microphone route are unchanged."""
        item = self.get_item(self.state.get("path", ""))
        if item is None or not (self.state.get("playing") or self.state.get("paused")):
            return False
        try:
            seconds = float(seconds)
            if not math.isfinite(seconds):
                return False
            seconds = max(item.start, min(seconds, item.end - 1 / 48000))
            if not hasattr(self.engine, "seek"):
                self.message.emit("当前音频后端不支持播放定位", True)
                return False
            self._token += 1
            self.engine.seek(seconds, token=self._token)
            self.state["position"] = seconds
            self.state_changed.emit()
            return True
        except Exception as error:
            self.message.emit(f"播放定位失败：{error}", True)
            return False

    def stop(self):
        self._token += 1
        self._pending_play_path = ""
        self.engine.stop()
        self.state.update(playing=False, paused=False)
        self.state_changed.emit()

    def pause(self):
        if self.state.get("playing") or self.state.get("paused"):
            self.engine.pause()
        else:
            item = self.get_item(self.state.get("path", ""))
            if item is None:
                item = next((entry for entry in self.items if entry.playable), None)
            if item:
                self._play(item)

    def _step(self, delta):
        playable = [item for item in self.items if item.playable]
        if not playable:
            return
        current = self.get_item(self.state.get("path", ""))
        index = next((i for i, entry in enumerate(playable) if current and entry.key == current.key), -1)
        self._play(playable[(index + delta) % len(playable)])

    def previous(self):
        self._step(-1)

    def next(self):
        self._step(1)

    def set_range(self, path, start, end):
        if self.state.get("delete_busy"):
            return False
        item = self.get_item(path)
        if not item:
            return False
        try:
            start, end = valid_range(item.duration, start, end)
            previous = (item.start, item.end)
            item.start, item.end = start, end
            try:
                self.library.save_item(item)
            except Exception:
                item.start, item.end = previous
                raise
            self._edit_revision += 1
            if path_key(self.state.get("path", "")) == item.key:
                self.engine.set_range(start, end)
            self._confirm_metadata("范围已保存")
            self.state_changed.emit()
            return True
        except Exception as error:
            self.items_changed.emit()
            self.message.emit(str(error), True)
            return False

    def set_mode(self, value):
        if value not in range(4):
            return
        self._save_live_setting("mode", value)

    def set_theme(self, value):
        """Persist appearance without rebuilding any audio or hotkey state."""
        if value not in ("system", "dark", "light"):
            return False
        return self._save_live_setting("theme", value)

    def set_mini_on_top(self, value):
        """The view applies the flag after this write has actually succeeded."""
        return self._save_live_setting("mini_on_top", bool(value))

    def _save_live_setting(self, key, value):
        try:
            self.library.set_setting(key, value)
        except Exception as error:
            self.message.emit(f"设置保存失败：{error}", True)
            self.state_changed.emit()
            return False
        self.settings[key] = self.state[key] = value
        self.state_changed.emit()
        return True

    def set_volume(self, kind, value):
        if kind not in ("local", "send", "mic"):
            return
        key = kind + "_volume"
        if not self._save_live_setting(key, max(0, min(100, int(value)))):
            return
        self.engine.set_volumes(*(self.settings[name] / 100.0 for name in ("local_volume", "send_volume", "mic_volume")))
        self.state_changed.emit()

    def set_send(self, value):
        if not self._save_live_setting("send_to_call", bool(value)):
            return False
        self.engine.set_send(bool(value))
        self._update_call_pair_state()
        self.state_changed.emit()
        if value and not self.state.get("call_connected"):
            self.message.emit("片段已选择发送到通话，请先连接通话音频设备", False)
        elif not value and self.state.get("call_connected"):
            self.message.emit("片段分享已关闭，麦克风仍保持通话", False)
        return True

    def set_share(self, value):
        """One UI switch connects/shares; turning it off keeps microphone routing."""
        if not value:
            return self.set_send(False)
        if self.state.get("call_connected") or self._call_connect_pending:
            return self.set_send(True)
        return self._begin_call()

    def set_language(self, code):
        if code not in ("zh_CN", "en"):
            return False
        return self._save_live_setting("ui_language", code)

    def _apply_audio_settings(self):
        self.engine.set_volumes(*(self.settings[key] / 100.0 for key in ("local_volume", "send_volume", "mic_volume")))
        if self.settings["local_device"]:
            self.engine.set_local_device(self.settings["local_device"])

    def start_recording(self, source):
        if self.state.get("recording") or self._record_command_pending:
            return
        if not self._refresh_folder_write_access():
            self.message.emit(self.state["folder_warning"], True)
            self.state_changed.emit()
            return
        self.settings["recording_source"] = source
        self.library.set_setting("recording_source", source)
        self.request_expand.emit()
        self._record_command_pending = True
        self.state_changed.emit()
        self.engine.start_recording(self.folder, source, self.settings["mic_device"], self.settings["system_device"])

    def stop_recording(self):
        self.engine.stop_recording()

    def mark_recording(self):
        if self.state.get("recording"):
            self.engine.mark_recording()

    def available_devices(self):
        return list(self._devices)

    def refresh_devices(self):
        if self._device_refresh_busy:
            return
        self._device_refresh_busy = True

        def success(devices):
            self._device_refresh_busy = False
            self._devices = devices
            self.state["call_ready"] = any(device.kind == "output" and device.is_virtual for device in devices)
            # 第一次启动选择并保存当时的默认设备。已保存设备缺失时不替换。
            for key, kind in (("mic_device", "input"), ("system_device", "loopback"), ("local_device", "output")):
                if not self.settings[key]:
                    selected = next((device for device in devices if device.kind == kind and device.is_default and not device.is_virtual), None)
                    if selected:
                        self.settings[key] = selected.id
                        self.library.set_setting(key, selected.id)
            if not self.settings["call_device"]:
                selected = next((device for device in devices if device.kind == "output" and self._standard_cable_output(device.name)), None)
                if selected:
                    self.settings["call_device"] = selected.id
                    self.library.set_setting("call_device", selected.id)
            self._apply_audio_settings()
            self._update_call_pair_state()
            self._refresh_system_pair()
            self.state_changed.emit()

        def failure(error):
            self._device_refresh_busy = False
            self.message.emit(f"读取音频设备失败：{error}", True)
        self._submit(self.engine.devices, success, failure)

    @staticmethod
    def _standard_cable_output(name):
        return bool(re.match(r"^CABLE\s+Input\s*\(VB-Audio Virtual Cable\)$", name.strip(), re.I))

    def _refresh_system_pair(self):
        if self._system_pair_busy or self.system_settings is None:
            return
        self._system_pair_busy = True

        def discover():
            from .windows_audio import WindowsAudioBackend
            return WindowsAudioBackend().discover_standard_cable_pair()

        def success(pair):
            self._system_pair_busy = False
            self._cable_pair = pair
            self._update_call_pair_state()
            self.state_changed.emit()

        def failure(error):
            self._system_pair_busy = False
            self._cable_pair = None
            self.state["call_diagnostic"] = f"无法检查通话输入设备：{error}"
            self.state_changed.emit()
        self._submit(discover, success, failure)

    def _update_call_pair_state(self):
        output = next((device for device in self._devices if device.id == self.settings["call_device"]), None)
        pair = self._cable_pair
        valid = bool(pair and output and self._standard_cable_output(output.name))
        self.state["call_endpoint_id"] = pair.capture.id if valid else ""
        self.state["call_input_name"] = pair.capture.name if valid else "CABLE Output (VB-Audio Virtual Cable)"
        self.state["call_pair_ready"] = valid
        if self.state.get("call_connected"):
            self.state["call_diagnostic"] = ("片段和麦克风送入通话" if self.settings["send_to_call"] else "仅麦克风送入通话")
        elif output and not self._standard_cable_output(output.name):
            self.state["call_diagnostic"] = "请选择标准 CABLE Input；一键配置不支持其他虚拟线"
        elif not pair and self.system_settings is not None:
            self.state["call_diagnostic"] = "未检测到完整的 CABLE Input／CABLE Output 配对"
        else:
            self.state["call_diagnostic"] = "通话软件需要选择 CABLE Output"

    def connect_call(self):
        if self.state.get("call_connected"):
            self.disconnect_call()
            return
        self._begin_call()

    def _begin_call(self):
        if self._call_connect_pending:
            return
        device = self.settings["call_device"]
        if not device:
            device = next((entry.id for entry in self._devices if entry.kind == "output" and entry.is_virtual), "")
        if not device:
            self.message.emit("尚未检测到 VB-CABLE，请打开通话帮助查看安装指引", True)
            self._temporary_call_requested = False
            return False
        if not self.set_send(True):
            self._temporary_call_requested = False
            return False
        self._call_connect_pending = True
        self.state["call_connecting"] = True
        self._requested_call_output = device
        self.engine.connect_call(self.settings["mic_device"], device)
        self.state_changed.emit()
        return True

    def configure_call_temporarily(self):
        if self.system_settings is None:
            self.message.emit("临时系统配置不可用，请在 Windows 和通话软件中手动选择 CABLE Output", True)
            return
        if not self.state.get("call_endpoint_id"):
            self._refresh_system_pair()
            self.message.emit("尚未确认标准虚拟线配对，请刷新设备后重试，或手动选择 CABLE Output", True)
            return
        if not self.set_send(True):
            return
        self._temporary_call_endpoint = self.state["call_endpoint_id"]
        if self.state.get("call_connected"):
            self._apply_temporary_call()
        else:
            self._temporary_call_requested = True
            self._begin_call()

    def _accept_system_result(self, result, emit=True):
        result = result or {}
        self.state["temporary_config_status"] = result.get("status", "idle")
        self.state["temporary_config_message"] = result.get("message", "")
        if emit and result.get("message"):
            self.message.emit(result["message"], not result.get("ok", True))
            self.state_changed.emit()

    def _apply_temporary_call(self):
        self._temporary_call_requested = False
        actual = self.engine.snapshot().get("call_output_id", "")
        if actual and actual != self.settings["call_device"]:
            self.message.emit("通话设备已改变，本次未修改系统输入，请重新连接通话", True)
            return
        try:
            result = self.system_settings.apply(self._temporary_call_endpoint)
            self._accept_system_result(result)
        except Exception as error:
            self.message.emit(f"临时系统配置失败，请手动选择 CABLE Output：{error}", True)

    def _restore_temporary_call(self, reason="disconnect"):
        if self.system_settings is None:
            return
        try:
            result = self.system_settings.restore(reason)
            self._accept_system_result(result, emit=not self._closed)
            return result
        except Exception as error:
            message = f"系统设置恢复失败，请检查 Windows 默认通信麦克风：{error}"
            if not self._closed:
                self.message.emit(message, True)
            return {"ok": False, "status": "pending", "message": message}

    def test_call_output(self):
        if not self.state.get("call_connected"):
            self.message.emit("请先开启通话输出，再播放测试声音", True)
            return
        if self.state.get("playing") or self.state.get("paused") or self._pending_play_path:
            self.message.emit("请先停止当前片段，再播放测试声音", True)
            return
        if hasattr(self.engine, "test_call_output"):
            self.engine.test_call_output()
            self.message.emit("已请求测试声音；发送电平仅表示本机输出，请让对方确认是否听到", False)
        else:
            self.message.emit("当前预览模式不提供测试声音", True)

    def disconnect_call(self):
        self._temporary_call_requested = False
        self._call_connect_pending = False
        self.state["call_connecting"] = False
        self.engine.disconnect_call()

    def _bindings(self, changed=None, *, include_disabled=True):
        bindings = dict(self.settings.get("global_hotkeys", {}))
        for item in self.items:
            sequence = changed.get(item.key, item.hotkey) if changed else item.hotkey
            action = "item:" + item.path
            disabled = self.state.get("hotkey_conflicts", {})
            if action in disabled and ((changed and item.key not in changed) or not include_disabled):
                continue
            if sequence and item.playable:
                bindings["item:" + item.path] = sequence
        return bindings

    def _refresh_hotkeys(self):
        try:
            disabled = self.hotkeys.replace_available(self._bindings())
            previous = self.state.get("hotkey_conflicts", {})
            self.state["hotkey_conflicts"] = disabled
            if disabled and disabled != previous:
                details = "\n".join(str(value) for value in disabled.values())
                self.message.emit("部分快捷键暂未启用，原配置已保留：\n" + details, True)
        except ValueError as error:
            self.message.emit(str(error), True)

    def suspend_hotkeys(self, value):
        try:
            self.hotkeys.suspend(value)
        except ValueError as error:
            self.message.emit(str(error), True)

    def set_hotkey(self, path, sequence):
        if self.state.get("delete_busy"):
            return False
        item = self.get_item(path)
        if not item:
            return False
        old = item.hotkey
        try:
            self.hotkeys.replace(self._bindings({item.key: sequence}))
            item.hotkey = sequence
            try:
                self.library.save_item(item)
            except Exception:
                item.hotkey = old
                self._refresh_hotkeys()
                raise
            self._edit_revision += 1
            self.items_changed.emit()
            self._confirm_metadata("快捷键已保存")
            return True
        except Exception as error:
            self.message.emit(str(error), True)
            return False

    def _on_hotkey(self, action):
        if action.startswith("item:"):
            self.trigger(action[5:])
        elif action == "play_pause":
            self.pause()
        elif action == "stop":
            self.stop()
        elif action == "record":
            if self.state.get("recording"):
                self.stop_recording()
            else:
                self.start_recording(self.settings["recording_source"])
        elif action == "mark":
            self.mark_recording()

    def save_settings(self, values):
        if (self.state.get("recording") or self.state.get("call_connected") or self._record_command_pending
                or self._call_connect_pending):
            changed = any(values.get(key, self.settings[key]) != self.settings[key]
                          for key in ("mic_device", "system_device", "local_device", "call_device"))
            if changed:
                self.message.emit("请先停止录音并断开通话，再修改音频设备", True)
                return
        old = self.settings.copy()
        try:
            self.settings.update({key: value for key, value in values.items() if key in DEFAULTS})
            self.hotkeys.replace(self._bindings(include_disabled=False))
            self.library.set_settings(self.settings)
            self._apply_audio_settings()
            self.engine.set_send(self.settings["send_to_call"])
            self.state.update({key: self.settings[key] for key in ("mode", "send_to_call", "local_volume", "send_volume", "mic_volume", "ui_language")})
            self._update_call_pair_state()
            self.state_changed.emit()
            self.message.emit("设置已保存", False)
        except Exception as error:
            self.settings = old
            self._refresh_hotkeys()
            self.message.emit(str(error), True)

    def follow_system_devices(self):
        if self._closed or self._system_follow_busy or self.system_settings is None:
            return
        backend = self.system_settings.backend
        if not hasattr(backend, "get_default_endpoint"):
            return
        self._system_follow_busy = True
        def read():
            return self.engine.devices(), backend.get_default_endpoint("render"), backend.get_default_endpoint("capture")
        def ready(result):
            self._system_follow_busy = False
            devices, output, microphone = result
            self._devices = devices
            changed = {}
            notices = []
            for key, endpoint, kind in (("local_device", output, "output"), ("mic_device", microphone, "input")):
                candidates = [device for device in devices if device.kind == kind and not device.is_virtual
                              and device.name.strip().casefold() == endpoint.name.strip().casefold()]
                if len(candidates) == 1:
                    changed[key] = candidates[0].id
                elif "cable" in endpoint.name.casefold() or "vb-audio" in endpoint.name.casefold():
                    notices.append("系统当前选择了虚拟线；播放器保留真实耳机／麦克风，避免声音回路。")
                else:
                    notices.append("暂时无法跟随系统设备，请停止录音和通话后重新检测，或打开 Windows 声音设置检查。")
            notice = "\n".join(dict.fromkeys(notices))
            note_changed = self.state.get("audio_follow_note", "") != notice
            self.state["audio_follow_note"] = notice
            if "local_device" in changed:
                selected = next(device for device in devices if device.id == changed["local_device"])
                loops = [device for device in devices if device.kind == "loopback" and not device.is_virtual
                         and device.name.startswith(selected.name)]
                if len(loops) == 1:
                    changed["system_device"] = loops[0].id
            changed = {key: value for key, value in changed.items() if self.settings[key] != value}
            if changed:
                self.settings.update(changed)
                self.library.set_settings(changed)
                self.state.update(changed)
                if hasattr(self.engine, "follow_devices"):
                    self.engine.follow_devices(self.settings["local_device"], self.settings["mic_device"], self.settings["system_device"])
                else:
                    self._apply_audio_settings()
                self.state_changed.emit()
            elif note_changed:
                self.state_changed.emit()
        def failed(error):
            self._system_follow_busy = False
            log.debug("System audio defaults unavailable: %s", error)
        self._submit(read, ready, failed)

    def change_system_device(self, key, device_id):
        if key not in ("mic_device", "local_device") or self.system_settings is None:
            return
        device = next((entry for entry in self._devices if entry.id == device_id and not entry.is_virtual), None)
        if device is None:
            self.message.emit("请选择当前可用的真实耳机或麦克风", True)
            return
        backend = self.system_settings.backend
        flow = "capture" if key == "mic_device" else "render"
        def change():
            endpoints = backend.capture_endpoints() if flow == "capture" else backend.render_endpoints()
            matches = [endpoint for endpoint in endpoints if endpoint.state == 1 and endpoint.name.strip().casefold() == device.name.strip().casefold()]
            if len(matches) != 1:
                raise ValueError("设备名称无法唯一匹配，请使用 Windows 声音设置更换")
            backend.set_default_endpoint(matches[0].id, flow)
        def changed(_):
            self.follow_system_devices()
            self.message.emit("已更改系统声音设备，其他软件也会跟随；退出后保留", False)
        self._submit(change, changed)

    def import_files(self, paths):
        if not paths:
            return
        paths = [str(path) for path in paths]
        folder = self.folder
        if not self._refresh_folder_write_access():
            self.message.emit(self.state["folder_warning"], True)
            self.import_finished.emit({"folder": folder, "imported": [], "skipped": [],
                                       "failed": [{"path": path, "error": self.state["folder_warning"]} for path in paths]})
            self.state_changed.emit()
            return

        def imported(result):
            if isinstance(result, dict):
                report = {"folder": folder, "imported": list(result.get("imported", [])),
                          "skipped": list(result.get("skipped", [])), "failed": list(result.get("failed", []))}
            else:
                report = {"folder": folder, "imported": list(result), "skipped": [], "failed": []}
            text = f"已导入 {len(report['imported'])} 项，跳过 {len(report['skipped'])} 项，失败 {len(report['failed'])} 项"
            if report["failed"]:
                text += "；详情中可查看原因"
            self.message.emit(text, bool(report["failed"]))
            self.import_finished.emit(report)
            self.reload()

        def failed(error):
            self.message.emit(f"导入失败：{error}", True)
            self.import_finished.emit({"folder": folder, "imported": [], "skipped": [],
                                       "failed": [{"path": path, "error": str(error)} for path in paths]})

        operation = getattr(self.library, "import_batch", self.library.import_audio)
        self._submit(lambda: operation(paths, folder), imported, failed)

    def export(self, path):
        item = self.get_item(path)
        if item:
            if not self._refresh_folder_write_access():
                self.message.emit(self.state["folder_warning"], True)
                self.state_changed.emit()
                return
            # 复制范围快照，导出不受随后拖动影响。
            from dataclasses import replace
            snapshot, folder = replace(item), self.folder
            self._submit(lambda: self.library.export_segment(snapshot, folder),
                         lambda result: (self.message.emit(f"已另存：{Path(result).name}", False), self.reload()))

    def rename(self, path, name):
        if self.state.get("delete_busy"):
            self.message.emit("文件操作尚未完成，请稍后重试", True)
            return
        item = self.get_item(path)
        if item:
            if path_key(self.state.get("path", "")) == item.key:
                self.stop()
            self._submit(lambda: self.library.rename(item, name), lambda _: self.reload())

    def delete(self, path):
        self.delete_items([path])

    def delete_items(self, paths):
        """Stage a batch on a worker and offer a 30-second create-only undo."""
        if self.state.get("delete_busy"):
            self.message.emit("文件操作尚未完成，请稍后重试", True)
            return
        from dataclasses import replace
        selected = {path_key(path) for path in paths}
        items = [replace(item) for item in self.items if item.key in selected]
        if not items:
            return
        if path_key(self.state.get("path", "")) in selected:
            self.stop()
        previous = self._undo_batches[-1] if self._undo_batches else None
        if previous and previous.get("recovered"):
            previous = None  # A new deletion must not discard crash recovery.
        self._undo_timer.stop()
        self.state["delete_busy"] = True
        self.state_changed.emit()

        def stage():
            if previous:
                self.library.recycle_delete_batch(previous["batch_id"])
            try:
                result = self.library.stage_delete_batch(items)
                if result.get("batch_id"):
                    self._session_deletion_ids.add(result["batch_id"])
                return result
            except Exception as error:
                return {"batch_id": "", "folder": self.folder, "deleted": [],
                        "failed": [{"path": item.path, "error": str(error)} for item in items], "count": 0}

        def finished(result):
            if previous and previous in self._undo_batches:
                self._undo_batches.remove(previous)
            self.state["delete_busy"] = False
            if result.get("batch_id"):
                self._undo_batches.append(result)
                self._undo_timer.start()
            self._update_delete_undo_state()
            text = f"已删除 {len(result['deleted'])} 项，可在 30 秒内撤销"
            if result["failed"]:
                text += "；失败 " + str(len(result["failed"])) + " 项\n" + "\n".join(
                    f"{Path(entry['path']).name}：{entry['error']}" for entry in result["failed"])
            self.message.emit(text, bool(result["failed"]))
            self._edit_revision += 1
            self.reload()

        def failed(error):
            self.state["delete_busy"] = False
            self._update_delete_undo_state()
            self.message.emit(f"删除未完成，原文件或删除暂存记录已保留：{error}", True)
            self.reload()

        self._submit(stage, finished, failed)

    def _update_delete_undo_state(self):
        latest = self._undo_batches[-1] if self._undo_batches else None
        self.state["delete_undo_count"] = int(latest.get("count", 0)) if latest else 0
        self.state["delete_undo_available"] = bool(self.state["delete_undo_count"])
        self.state_changed.emit()

    def _accept_recovered_deletions(self, result):
        known = {entry["batch_id"] for entry in self._undo_batches}
        recovered = [dict(entry, recovered=entry["batch_id"] not in self._session_deletion_ids)
                     for entry in result["batches"] if entry["batch_id"] not in known]
        self._undo_batches = recovered + self._undo_batches
        self._update_delete_undo_state()
        if result["errors"]:
            self.message.emit("\n".join(result["errors"]), True)
        elif result["batches"]:
            self.message.emit("发现上次未完成的删除记录，可点击撤销恢复音频和配置", False)

    def undo_delete(self):
        if self.state.get("delete_busy") or not self._undo_batches:
            return False
        batch = self._undo_batches[-1]
        self._undo_timer.stop()
        self.state["delete_busy"] = True
        self.state_changed.emit()

        def finished(result):
            self.state["delete_busy"] = False
            if result["count"]:
                batch.update(result)
            elif batch in self._undo_batches:
                self._undo_batches.remove(batch)
            self._update_delete_undo_state()
            text = f"已恢复 {len(result['restored'])} 项音频及配置"
            if result["failed"]:
                text += "；仍可重试撤销\n" + "\n".join(
                    f"{Path(entry['path']).name}：{entry['error']}" for entry in result["failed"])
            if result.get("cleanup_warning"):
                text += "；音频及配置已恢复，暂存目录清理未完成：" + result["cleanup_warning"]
            self.message.emit(text, bool(result["failed"]))
            self._edit_revision += 1
            self.reload()

        def failed(error):
            self.state["delete_busy"] = False
            self._update_delete_undo_state()
            self.message.emit(f"撤销未完成，删除暂存文件已保留：{error}", True)
            self.reload()

        self._submit(lambda: self.library.undo_delete_batch(batch["batch_id"]), finished, failed)
        return True

    def _expire_delete_undo(self):
        if self._closed or self.state.get("delete_busy") or not self._undo_batches:
            return
        batch = self._undo_batches[-1]
        if batch.get("recovered"):
            return
        self.state["delete_busy"] = True
        self.state_changed.emit()

        def finished(_):
            if batch in self._undo_batches:
                self._undo_batches.remove(batch)
            self.state["delete_busy"] = False
            self._update_delete_undo_state()
            self.message.emit("撤销时间已结束，删除的音频和配置已进入系统回收站", False)
            if self._scan_again:
                self.reload()

        def failed(error):
            self.state["delete_busy"] = False
            self._update_delete_undo_state()
            self.message.emit(f"回收失败，音频和配置仍保存在删除暂存目录，可撤销恢复：{error}", True)
            if self._scan_again:
                self.reload()

        self._submit(lambda: self.library.recycle_delete_batch(batch["batch_id"]), finished, failed)

    def update_appearance(self, path, **values):
        if self.state.get("delete_busy"):
            return
        item = self.get_item(path)
        if not item:
            return
        old = {key: getattr(item, key) for key in ("color", "avatar", "background", "avatar_source",
                                                 "avatar_crop", "background_source", "background_crop")}
        try:
            for key in ("color", "avatar", "background"):
                if key not in values:
                    continue
                value = values[key]
                if key in ("avatar", "background") and value and Path(value).is_file():
                    value = self.library.copy_asset(value)
                if key in ("avatar", "background"):
                    self.library.begin_asset_edit(path, key)
                    setattr(item, key + "_source", "")
                    setattr(item, key + "_crop", None)
                setattr(item, key, value)
            self.library.save_item(item)
            self._edit_revision += 1
            self.items_changed.emit()
            self._confirm_metadata("外观已保存")
        except Exception as error:
            for key, value in old.items():
                setattr(item, key, value)
            self.items_changed.emit()
            self.message.emit(str(error), True)

    def reset_avatar(self, path):
        return self._reset_appearance(path, "avatar")

    def reset_background(self, path):
        return self._reset_appearance(path, "background")

    def _reset_appearance(self, path, kind):
        if self.state.get("delete_busy"):
            return False
        item = self.get_item(path)
        if item is None:
            return False
        try:
            result = self.library.reset_appearance(item, kind)
            fields = ("avatar", "avatar_source", "avatar_crop") if kind == "avatar" else (
                "color", "background", "background_source", "background_crop")
            for field in fields:
                setattr(item, field, getattr(result, field))
            self._edit_revision += 1
            self.items_changed.emit()
            self._confirm_metadata("头像已恢复默认" if kind == "avatar" else "背景已恢复默认")
            return True
        except Exception as error:
            self.message.emit(f"恢复默认失败：{error}", True)
            return False

    def reset_range(self, path):
        item = self.get_item(path)
        if item is None or self.state.get("delete_busy"):
            return False
        result = self.set_range(path, 0.0, item.duration)
        if result:
            self.items_changed.emit()
        return result

    def avatar_edit_source(self, path):
        item = self.get_item(path)
        if not item:
            return "", None
        source = getattr(item, "avatar_source", "") or item.avatar
        absolute = self.resolve_asset(source)
        return (absolute, getattr(item, "avatar_crop", None)) if Path(absolute).is_file() else ("", None)

    def _confirm_metadata(self, success_message):
        """A short index commit is provisional until the portable file is durable."""
        flush = getattr(self.library, "flush_pending", None)
        if flush is None:
            self.message.emit(success_message, False)
            return

        def finished(errors):
            if errors:
                self.message.emit("配置保存未完成：\n" + "\n".join(map(str, errors)), True)
                self.reload()
            else:
                self.message.emit(success_message, False)
        self._submit(flush, finished)

    def background_edit_source(self, path):
        item = self.get_item(path)
        if not item:
            return "", None
        source = getattr(item, "background_source", "") or item.background
        absolute = self.resolve_asset(source)
        return (absolute, getattr(item, "background_crop", None)) if Path(absolute).is_file() else ("", None)

    def set_background(self, path, image_path, crop):
        if self.state.get("delete_busy"):
            return
        item = self.get_item(path)
        if not item:
            return
        from dataclasses import replace
        snapshot, generation = replace(item), self._generation
        asset_revision = self.library.begin_asset_edit(path, "background")

        def apply():
            if not self.library.apply_background_crop(snapshot, image_path, crop, expected_revision=asset_revision):
                return None
            errors = self.library.flush_pending()
            if errors:
                raise OSError("\n".join(map(str, errors)))
            return snapshot

        def saved(result):
            if result is None or not self.library.asset_edit_current(path, "background", asset_revision):
                return
            self._edit_revision += 1
            if generation != self._generation:
                return
            current = self.get_item(path)
            if current:
                for field in ("background", "background_source", "background_crop"):
                    setattr(current, field, getattr(result, field))
                self.items_changed.emit()
                self.message.emit("背景已保存", False)
        self._submit(apply, saved)

    def set_audio_stream(self, path, stream_index):
        if self.state.get("delete_busy"):
            return False
        item = self.get_item(path)
        if not item:
            return False
        tracks = getattr(item, "audio_streams", [])
        selected = next((track for track in tracks if track.get("index") == stream_index), None)
        if selected is None:
            self.message.emit("所选音轨不可用，请刷新文件夹", True)
            return False
        from dataclasses import replace
        previous = replace(item)
        try:
            duration = float(selected.get("duration") or item.duration)
            start, end = valid_range(duration, item.start, item.end)
            item.audio_stream = stream_index
            item.duration, item.start, item.end = duration, start, end
            self.library.save_item(item)
        except Exception as error:
            item.audio_stream = getattr(previous, "audio_stream", None)
            item.duration, item.start, item.end = previous.duration, previous.start, previous.end
            self.message.emit(str(error), True)
            return False
        self._edit_revision += 1
        item.peaks = []
        self._wave_pending.discard(item.key)
        if path_key(self.state.get("path", "")) == item.key:
            resume = self.state.get("playing") and not self.state.get("paused")
            self.stop()
            if resume:
                self._play(item)
        self.request_waveform(path)
        self.items_changed.emit()
        self._confirm_metadata("音轨已保存")
        return True

    def set_avatar(self, path, image_path, crop):
        if self.state.get("delete_busy"):
            return
        item = self.get_item(path)
        if not item:
            return
        from dataclasses import replace
        snapshot, generation = replace(item), self._generation
        asset_revision = self.library.begin_asset_edit(path, "avatar")

        def apply():
            if not self.library.apply_avatar_crop(snapshot, image_path, crop, expected_revision=asset_revision):
                return None
            flush = getattr(self.library, "flush_pending", None)
            if flush:
                errors = flush()
                if errors:
                    raise OSError("\n".join(map(str, errors)))
            return snapshot

        def saved(result):
            if result is None or not self.library.asset_edit_current(path, "avatar", asset_revision):
                return
            self._edit_revision += 1
            if generation != self._generation:
                return
            current = self.get_item(path)
            if current:
                current.avatar = result.avatar
                current.avatar_source = result.avatar_source
                current.avatar_crop = result.avatar_crop
                self.items_changed.emit()
                self.message.emit("头像已保存", False)
        self._submit(apply, saved)

    def reorder(self, paths):
        if self.state.get("delete_busy"):
            return
        lookup = {entry.key: entry for entry in self.items}
        ordered = [lookup[path_key(path)] for path in paths if path_key(path) in lookup]
        if len(ordered) == len(self.items) and len({entry.key for entry in ordered}) == len(ordered):
            try:
                self.library.save_order(ordered)
                self._edit_revision += 1
                self.items = ordered
                self.items_changed.emit()
            except Exception as error:
                self.message.emit(str(error), True)

    def _poll_state(self):
        if self._closed:
            return
        latest = self.engine.snapshot()
        was_connected = self.state.get("call_connected", False)
        pending_matches = (self._pending_play_path and latest.get("path")
                           and path_key(latest["path"]) == path_key(self._pending_play_path))
        if (latest.get("playing") or latest.get("paused")) and (not self._pending_play_path or pending_matches):
            self._pending_play_path = ""
        elif self._pending_play_path:
            latest["playing"] = True
            latest["path"] = self._pending_play_path
            latest["paused"] = False
            current = self.get_item(self._pending_play_path)
            if current:
                latest["position"] = current.start
        if latest.get("recording"):
            self._record_command_pending = False
        changed = any(self.state.get(key) != value for key, value in latest.items())
        self.state.update(latest)
        connected = self.state.get("call_connected", False)
        if connected and not was_connected:
            self._call_connect_pending = False
            self.state["call_connecting"] = False
            if self._temporary_call_requested:
                self._apply_temporary_call()
        elif was_connected and not connected:
            self._restore_temporary_call()
        self._update_call_pair_state()
        if changed:
            self.state_changed.emit()

    def _on_audio_event(self, event, payload):
        if self._closed:
            return
        if event == "playback_finished":
            if payload.get("token") != self._token:
                return
            item = self.get_item(payload.get("path", ""))
            mode = self.settings["mode"]
            playable = [entry for entry in self.items if entry.playable]
            if item and mode == 1:
                self._play(item)
            elif item and mode in (2, 3):
                index = next((i for i, entry in enumerate(playable) if entry.key == item.key), -1)
                if index + 1 < len(playable):
                    self._play(playable[index + 1])
                elif mode == 3 and playable:
                    self._play(playable[0])
                else:
                    self.state.update(playing=False, paused=False)
            else:
                self.state.update(playing=False, paused=False)
            self.state_changed.emit()
        elif event == "error":
            self._record_command_pending = False
            self._pending_play_path = ""
            if not self.engine.snapshot().get("call_connected"):
                self._call_connect_pending = False
                self.state["call_connecting"] = False
                self._temporary_call_requested = False
            self.message.emit(payload.get("message", "音频处理失败"), True)
            self._poll_state()
        elif event in ("recording_saved", "clip_saved"):
            self.state["recent_recording_path"] = payload["path"]
            self.state["recent_recording_revision"] += 1
            self.message.emit(f"已保存：{Path(payload['path']).name}", False)
            self.recording_completed.emit(payload["path"])
            self.state_changed.emit()
            self.reload()
        elif event == "recording_stopped":
            self._record_command_pending = False
            self._poll_state()
            self.reload()
        elif event == "devices_changed":
            self.refresh_devices()
        elif event == "playback_started":
            if payload.get("token") == self._token:
                self._pending_play_path = ""
            self._poll_state()
        elif event == "call_connected":
            if payload.get("output_id") and self._requested_call_output and payload["output_id"] != self._requested_call_output:
                self._temporary_call_requested = False
                self.message.emit("通话设备已改变，本次未修改系统输入，请重新连接通话", True)
            self._call_connect_pending = False
            self.state["call_connecting"] = False
            self._poll_state()
        elif event == "call_disconnected":
            self._call_connect_pending = False
            self.state["call_connecting"] = False
            self._temporary_call_requested = False
            self._restore_temporary_call()
            self._poll_state()
        else:
            self._poll_state()

    def shutdown(self):
        if self._closed:
            return list(self._shutdown_errors)
        self._closed = True
        self._undo_timer.stop()
        self._poll.stop()
        self._system_follow.stop()
        self._folder_poll.stop()
        self._debounce.stop()
        # Every cleanup is attempted, including recovery after a disk/audio error.
        for label, cleanup in (("快捷键", self.hotkeys.close), ("音频和录音", self.engine.close)):
            try:
                result = cleanup()
                if isinstance(result, list):
                    self._shutdown_errors.extend(str(error) for error in result)
            except Exception as error:
                log.exception("退出时无法关闭%s", label)
                self._shutdown_errors.append(f"{label}关闭失败：{error}")
        restored = self._restore_temporary_call("exit")
        if self.system_settings is not None:
            try:
                result = self.system_settings.close()
                if isinstance(result, dict):
                    restored = result
            except Exception as error:
                self._shutdown_errors.append(f"系统配置清理失败：{error}")
        if restored and not restored.get("ok", True):
            self._shutdown_errors.append(restored.get("message", "系统设置恢复失败"))
        try:
            self._executor.shutdown(wait=True, cancel_futures=True)
        finally:
            try:
                pending = self.library.deletion_batches()
                self._shutdown_errors.extend(pending["errors"])
                for batch in pending["batches"]:
                    if batch["batch_id"] not in self._session_deletion_ids:
                        continue  # Previous-session recovery requires an explicit user action.
                    try:
                        self.library.recycle_delete_batch(batch["batch_id"])
                    except Exception as error:
                        self._shutdown_errors.append(f"删除文件未能送入回收站，暂存文件和恢复记录已保留：{error}")
            except Exception as error:
                self._shutdown_errors.append(f"删除恢复记录检查失败，暂存文件已保留：{error}")
            try:
                self.library.close()
            except Exception as error:
                self._shutdown_errors.append(f"配置保存失败：{error}")
        self._jobs.clear()
        self.deleteLater()
        self._shutdown_errors = list(dict.fromkeys(self._shutdown_errors))
        return list(self._shutdown_errors)
