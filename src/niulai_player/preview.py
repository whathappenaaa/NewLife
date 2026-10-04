"""无硬件的界面预览后端，仅 --no-audio 与自动化测试使用。"""
from __future__ import annotations


class PreviewEngine:
    def __init__(self, on_event):
        self.on_event = on_event
        self.status = dict(path="", playing=False, paused=False, position=0.0,
                           recording=False, record_seconds=0.0, mark_pending=False,
                           call_connected=False, mic_peak=0.0)
        self.calls = []

    def devices(self):
        return []

    def set_local_device(self, device_id):
        pass

    def play(self, path, start, end, send=False, token=0, stream_index=None):
        self.calls.append(("play", path, start, end, token))
        self.status.update(path=path, playing=True, paused=False, position=start)

    def stop(self):
        self.calls.append(("stop",))
        self.status.update(playing=False, paused=False)

    def pause(self):
        self.status["paused"] = not self.status["paused"]

    def set_range(self, start, end):
        if not start <= self.status["position"] < end:
            self.status["position"] = start

    def seek(self, seconds, token=None):
        self.calls.append(("seek", seconds, token))
        self.status["position"] = seconds

    def set_send(self, value):
        pass

    def set_volumes(self, local, send, mic):
        self.volumes = (local, send, mic)

    def snapshot(self):
        return self.status.copy()

    def start_recording(self, *args):
        self.on_event("error", {"message": "当前为无音频预览模式，录音请使用正常启动方式"})

    def stop_recording(self):
        pass

    def mark_recording(self):
        pass

    def connect_call(self, *args):
        self.on_event("error", {"message": "当前为无音频预览模式，无法连接通话"})

    def disconnect_call(self):
        self.status["call_connected"] = False

    def close(self):
        pass
