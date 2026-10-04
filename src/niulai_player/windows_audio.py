"""Small Windows Core Audio adapter. Discovery never opens an audio stream.

Only set_default_communications_input changes Windows state. PolicyConfig is an
internal Windows interface, so every operation checks HRESULT and reads back.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import ctypes as c
from ctypes import wintypes as w
import os
import uuid


@dataclass(frozen=True)
class Endpoint:
    id: str
    name: str
    state: int
    flow: str


@dataclass(frozen=True)
class CablePair:
    render: Endpoint
    capture: Endpoint


class WindowsAudioError(RuntimeError):
    pass


class _GUID(c.Structure):
    _fields_ = [("a", w.DWORD), ("b", w.WORD), ("c", w.WORD), ("d", c.c_ubyte * 8)]

    @classmethod
    def of(cls, value):
        return cls.from_buffer_copy(uuid.UUID(value).bytes_le)


class _Key(c.Structure):
    _fields_ = [("fmtid", _GUID), ("pid", w.DWORD)]


class _Value(c.Union):
    _fields_ = [("pointer", c.c_void_p), ("space", c.c_byte * 16)]


class _Variant(c.Structure):
    _fields_ = [("vt", w.WORD), ("r1", w.WORD), ("r2", w.WORD), ("r3", w.WORD), ("value", _Value)]


def _call(obj, slot, result, argument_types, *args):
    table = c.cast(obj, c.POINTER(c.POINTER(c.c_void_p))).contents
    return c.WINFUNCTYPE(result, c.c_void_p, *argument_types)(table[slot])(obj, *args)


def _release(obj):
    if obj:
        _call(obj, 2, w.ULONG, [])


def _check(hr, action):
    if hr < 0:
        raise WindowsAudioError(f"{action}失败（0x{hr & 0xffffffff:08X}），可在 Windows 声音设置中手动选择 CABLE Output")


class WindowsAudioBackend:
    """Stateless adapter; each call owns COM references on its calling thread."""

    @contextmanager
    def _com(self):
        if os.name != "nt":
            raise WindowsAudioError("临时通话设备配置仅支持 Windows")
        ole = c.WinDLL("ole32")
        ole.CoInitializeEx.argtypes = [c.c_void_p, w.DWORD]
        ole.CoInitializeEx.restype = c.c_long
        ole.CoCreateInstance.argtypes = [c.POINTER(_GUID), c.c_void_p, w.DWORD, c.POINTER(_GUID), c.POINTER(c.c_void_p)]
        ole.CoCreateInstance.restype = c.c_long
        ole.CoTaskMemFree.argtypes = [c.c_void_p]
        ole.CoTaskMemFree.restype = None
        ole.PropVariantClear.argtypes = [c.POINTER(_Variant)]
        ole.PropVariantClear.restype = c.c_long
        ole.CoUninitialize.argtypes = []
        ole.CoUninitialize.restype = None
        hr = ole.CoInitializeEx(None, 0)
        # A Qt STA thread is already initialized; don't uninitialize its apartment.
        if hr < 0 and (hr & 0xffffffff) != 0x80010106:
            _check(hr, "初始化 Windows 音频接口")
        try:
            yield ole
        finally:
            if hr in (0, 1):
                ole.CoUninitialize()

    @contextmanager
    def _instance(self, ole, clsid, iid):
        clsid, iid, obj = _GUID.of(clsid), _GUID.of(iid), c.c_void_p()
        _check(ole.CoCreateInstance(c.byref(clsid), None, 1, c.byref(iid), c.byref(obj)), "创建 Windows 音频接口")
        try:
            yield obj
        finally:
            _release(obj)

    def _enumerator(self, ole):
        return self._instance(ole, "bcde0395-e52f-467c-8e3d-c4579291692e", "a95664d2-9614-4f35-a746-de8db63617e6")

    def _policy(self, ole):
        return self._instance(ole, "870af99c-171d-4f9e-af0d-e63df40c2bc9", "f8679f50-850a-41cf-9c72-430f290290c8")

    def _info(self, ole, device, flow):
        pointer = c.c_void_p()
        _check(_call(device, 5, c.c_long, [c.POINTER(c.c_void_p)], c.byref(pointer)), "读取设备标识")
        try:
            identity = c.wstring_at(pointer)
        finally:
            ole.CoTaskMemFree(pointer)
        state = w.DWORD()
        _check(_call(device, 6, c.c_long, [c.POINTER(w.DWORD)], c.byref(state)), "读取设备状态")
        store = c.c_void_p()
        _check(_call(device, 4, c.c_long, [w.DWORD, c.POINTER(c.c_void_p)], 0, c.byref(store)), "读取设备名称")
        value = _Variant()
        key = _Key(_GUID.of("a45c254e-df1c-4efd-8020-67d146a850e0"), 14)
        try:
            _check(_call(store, 5, c.c_long, [c.POINTER(_Key), c.POINTER(_Variant)], c.byref(key), c.byref(value)), "读取设备名称")
            name = c.wstring_at(value.value.pointer) if value.vt == 31 and value.value.pointer else identity
        finally:
            ole.PropVariantClear(c.byref(value))
            _release(store)
        return Endpoint(identity, name, state.value, flow)

    def _endpoints(self, flow):
        with self._com() as ole, self._enumerator(ole) as enumerator:
            collection, count = c.c_void_p(), w.UINT()
            _check(_call(enumerator, 3, c.c_long, [c.c_int, w.DWORD, c.POINTER(c.c_void_p)],
                         1 if flow == "capture" else 0, 15, c.byref(collection)), "枚举音频设备")
            try:
                _check(_call(collection, 3, c.c_long, [c.POINTER(w.UINT)], c.byref(count)), "读取设备数量")
                result = []
                for index in range(count.value):
                    device = c.c_void_p()
                    _check(_call(collection, 4, c.c_long, [w.UINT, c.POINTER(c.c_void_p)], index, c.byref(device)), "读取音频设备")
                    try:
                        result.append(self._info(ole, device, flow))
                    finally:
                        _release(device)
                return result
            finally:
                _release(collection)

    def capture_endpoints(self):
        return self._endpoints("capture")

    def render_endpoints(self):
        return self._endpoints("render")

    def get_default_communications_input(self):
        with self._com() as ole, self._enumerator(ole) as enumerator:
            device = c.c_void_p()
            _check(_call(enumerator, 4, c.c_long, [c.c_int, c.c_int, c.POINTER(c.c_void_p)], 1, 2, c.byref(device)), "读取默认通信输入")
            try:
                return self._info(ole, device, "capture").id
            finally:
                _release(device)

    def get_default_endpoint(self, flow, role=0):
        with self._com() as ole, self._enumerator(ole) as enumerator:
            device = c.c_void_p()
            _check(_call(enumerator, 4, c.c_long, [c.c_int, c.c_int, c.POINTER(c.c_void_p)],
                        1 if flow == "capture" else 0, role, c.byref(device)), "读取系统声音设备")
            try:
                return self._info(ole, device, flow)
            finally:
                _release(device)

    def set_default_endpoint(self, identity, flow):
        endpoints = self.capture_endpoints() if flow == "capture" else self.render_endpoints()
        if not any(item.id == identity and item.state == 1 for item in endpoints):
            raise WindowsAudioError("设备当前不可用，未修改系统设置")
        with self._com() as ole, self._policy(ole) as policy:
            for role in (0, 1):
                _check(_call(policy, 13, c.c_long, [w.LPCWSTR, w.DWORD], identity, role), "更换系统声音设备")
        if self.get_default_endpoint(flow).id != identity:
            raise WindowsAudioError("Windows 未确认设备切换，请打开系统声音设置检查")

    def endpoint_available(self, identity):
        return any(item.id == identity and item.state == 1 for item in self.capture_endpoints())

    def capability(self):
        try:
            with self._com() as ole, self._policy(ole):
                pass
            return {"available": True, "message": "支持临时配置默认通信输入"}
        except Exception as error:
            return {"available": False, "message": str(error)}

    def set_default_communications_input(self, identity):
        if not identity or not self.endpoint_available(identity):
            raise WindowsAudioError("目标输入设备当前不可用，未修改 Windows 设置")
        with self._com() as ole, self._policy(ole) as policy:
            # The sole mutating call in this module. Capture endpoint + role 2 only.
            _check(_call(policy, 13, c.c_long, [w.LPCWSTR, w.DWORD], identity, 2), "切换默认通信输入")
        if self.get_default_communications_input() != identity:
            raise WindowsAudioError("Windows 未确认通信输入切换，已保留恢复信息")

    def discover_standard_cable_pair(self):
        renders = [p for p in self.render_endpoints() if p.state == 1 and p.name.strip().casefold() == "cable input (vb-audio virtual cable)"]
        captures = [p for p in self.capture_endpoints() if p.state == 1 and p.name.strip().casefold() == "cable output (vb-audio virtual cable)"]
        # An ambiguous or renamed endpoint requires explicit configuration rather
        # than guessing across independent cables or the 16-channel variant.
        return CablePair(renders[0], captures[0]) if len(renders) == len(captures) == 1 else None

    def find_cable_capture(self, output_name):
        pair = self.discover_standard_cable_pair()
        name = output_name.split(":", 3)[-1] if output_name.startswith("wasapi:") else output_name
        return pair.capture if pair and pair.render.name.strip().casefold() == name.strip().casefold() else None
