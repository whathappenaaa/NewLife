"""Windows RegisterHotKey；原生快捷键与界面命令使用相同入口。"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import sys
from collections.abc import Callable

from PySide6.QtCore import QAbstractNativeEventFilter, QCoreApplication


MODIFIERS = {"ctrl": 2, "control": 2, "alt": 1, "shift": 4, "win": 8, "meta": 8}
KEYS = {"space": 0x20, "enter": 0x0D, "return": 0x0D, "esc": 0x1B,
        "escape": 0x1B, "tab": 9, "backspace": 8, "delete": 0x2E,
        "insert": 0x2D, "home": 0x24, "end": 0x23, "pageup": 0x21,
        "pagedown": 0x22, "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28}


def parse_hotkey(text: str) -> tuple[int, int]:
    parts = [x.strip().lower() for x in text.split("+") if x.strip()]
    if not parts:
        raise ValueError("快捷键不能为空")
    modifiers = 0
    for part in parts[:-1]:
        if part not in MODIFIERS:
            raise ValueError("请设置一个组合键，例如 Ctrl+Alt+1")
        modifiers |= MODIFIERS[part]
    key = parts[-1]
    if len(key) == 1 and key.isascii() and key.isalnum():
        code = ord(key.upper())
    elif key.startswith("f") and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
        code = 0x70 + int(key[1:]) - 1
    elif key in KEYS:
        code = KEYS[key]
    else:
        raise ValueError("暂不支持这个按键，请使用字母、数字、F1～F24 或常见控制键")
    if not modifiers and not (0x70 <= code <= 0x87):
        raise ValueError("请至少搭配 Ctrl、Alt 或 Shift，避免影响日常输入")
    return modifiers, code


class HotkeyService(QAbstractNativeEventFilter):
    def __init__(self, callback: Callable[[str], None], enabled: bool = True, backend=None):
        super().__init__()
        self.callback = callback
        self.user32 = backend or (ctypes.WinDLL("user32", use_last_error=True) if sys.platform == "win32" and enabled else None)
        self.bindings: dict[str, str] = {}
        self._ids: dict[int, str] = {}
        self.suspended = False
        if self.user32 is not None:
            if backend is None:
                self.user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
                self.user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
            app = QCoreApplication.instance()
            if app:
                app.installNativeEventFilter(self)

    def _unregister(self):
        if self.user32:
            for identifier in tuple(self._ids):
                self.user32.UnregisterHotKey(None, identifier)
        self._ids.clear()

    def _register(self, bindings: dict[str, str]):
        if not self.user32:
            return
        for identifier, (action, sequence) in enumerate(bindings.items(), 18000):
            modifiers, code = parse_hotkey(sequence)
            if not self.user32.RegisterHotKey(None, identifier, modifiers | 0x4000, code):
                raise ValueError(f"快捷键 {sequence} 已被系统或其他程序占用")
            self._ids[identifier] = action

    def replace(self, bindings: dict[str, str]):
        proposed = {key: value for key, value in bindings.items() if value.strip()}
        seen = {}
        for action, sequence in proposed.items():
            parsed = parse_hotkey(sequence)
            if parsed in seen:
                raise ValueError(f"快捷键 {sequence} 重复，请为每个操作设置不同的按键")
            seen[parsed] = action
        old = self.bindings.copy()
        self._unregister()
        try:
            if not self.suspended:
                self._register(proposed)
        except Exception:
            self._unregister()
            if not self.suspended:
                self._register(old)
            raise
        self.bindings = proposed

    def replace_available(self, bindings: dict[str, str]) -> dict[str, str]:
        """Restore portable bindings independently without changing their definitions."""
        accepted, disabled, seen = {}, {}, set()
        self._unregister()
        for identifier, (action, sequence) in enumerate(bindings.items(), 18000):
            if not sequence.strip():
                continue
            try:
                parsed = parse_hotkey(sequence)
                if parsed in seen:
                    raise ValueError(f"快捷键 {sequence} 重复，请为每个操作设置不同的按键")
                if self.user32 and not self.suspended:
                    modifiers, code = parsed
                    if not self.user32.RegisterHotKey(None, identifier, modifiers | 0x4000, code):
                        raise ValueError(f"快捷键 {sequence} 已被系统或其他程序占用")
                    self._ids[identifier] = action
                accepted[action] = sequence
                seen.add(parsed)
            except ValueError as error:
                disabled[action] = str(error)
        self.bindings = accepted
        return disabled

    def suspend(self, value: bool):
        if self.suspended == value:
            return
        self.suspended = value
        self._unregister()
        if not value:
            self._register(self.bindings)

    def nativeEventFilter(self, event_type, message):
        if self.user32 is not None:
            msg = ctypes.cast(int(message), ctypes.POINTER(wintypes.MSG)).contents
            if msg.message == 0x0312 and int(msg.wParam) in self._ids:
                self.callback(self._ids[int(msg.wParam)])
                return True, 0
        return False, 0

    def close(self):
        self._unregister()
        app = QCoreApplication.instance()
        if app:
            app.removeNativeEventFilter(self)
