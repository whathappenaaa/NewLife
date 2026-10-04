import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QObject, QPoint, QPointF, Qt, Signal, QMimeData, QUrl, QEvent
from PySide6.QtGui import QWheelEvent, QImage, QColor, QKeySequence, QFontDatabase, QFont, QDragEnterEvent, QDragMoveEvent, QDropEvent
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QToolButton, QMessageBox

from niulai_player.models import AudioItem, DeviceInfo
from niulai_player.ui import MainWindow, SettingsDialog
from niulai_player.widgets import VolumeButton, WaveformWidget, time_text
from niulai_player.avatar_crop import AvatarCropDialog, CropCanvas
from niulai_player import i18n
from niulai_player.style import set_theme, style_for


class FakeController(QObject):
    items_changed = Signal()
    state_changed = Signal()
    message = Signal(str, bool)
    waveform_ready = Signal(str, list)
    request_expand = Signal()

    def __init__(self):
        super().__init__()
        self.folder = r"C:\音效\常用"
        self.settings = {"recording_source": "电脑", "global_hotkeys": {}, "mini_on_top": True,
                         "theme": "system", "mic_device": "mic", "local_device": "speaker"}
        self.state = {"path": "", "playing": False, "paused": False, "position": 0.0, "recording": False, "record_seconds": 0.0, "mark_pending": False, "call_connected": False, "call_ready": False, "local_volume": 70, "mode": 0, "send_to_call": False}
        self.items = [AudioItem(r"C:\音效\常用\牛来了.wav", "牛来了", 10, 2, 7, peaks=[.25, .6, .3] * 30)]
        self.calls = []
        self.waveform_requests = []

    def resolve_asset(self, path):
        return path

    def trigger(self, path):
        self.calls.append(("trigger", path))
        active = self.state["playing"] and self.state["path"] == path
        self.state.update(path=path, playing=not active, paused=False)
        self.state_changed.emit()

    def set_range(self, path, start, end):
        self.calls.append(("range", path, start, end))

    def set_volume(self, kind, value):
        self.calls.append(("volume", kind, value))
        self.state[kind + "_volume"] = value
        self.state_changed.emit()

    def set_mode(self, value):
        self.state["mode"] = value

    def set_send(self, value):
        self.state["send_to_call"] = value

    def save_settings(self, values):
        self.calls.append(("save_settings", values.copy()))
        self.settings.update(values)
        self.state_changed.emit()
        self.message.emit("设置已保存", False)

    def set_theme(self, value):
        self.calls.append(("theme", value))
        self.settings["theme"] = value
        self.state_changed.emit()
        return True

    def set_mini_on_top(self, value):
        self.calls.append(("mini_on_top", value))
        self.settings["mini_on_top"] = value
        self.state_changed.emit()
        return True

    def change_system_device(self, key, device_id):
        self.calls.append(("system_device", key, device_id))
        self.settings[key] = device_id
        self.state_changed.emit()

    def available_devices(self):
        return [DeviceInfo("mic", "测试麦克风", "input"), DeviceInfo("loop", "测试电脑声音", "loopback"), DeviceInfo("speaker", "测试耳机", "output")]

    def refresh_devices(self):
        self.calls.append(("refresh",))

    def request_waveform(self, path):
        self.waveform_requests.append(path)

    def suspend_hotkeys(self, value):
        self.calls.append(("suspend", value))

    def previous(self):
        self.calls.append(("previous",))

    def next(self):
        self.calls.append(("next",))

    def pause(self):
        pass

    def stop(self):
        self.calls.append(("stop",))

    def shutdown(self):
        pass

    def reorder(self, paths):
        self.calls.append(("reorder", paths))

    def mark_recording(self):
        pass

    def import_files(self, paths):
        self.calls.append(("import", paths))

    def delete_items(self, paths):
        self.calls.append(("delete_items", paths))

    def seek(self, seconds):
        self.calls.append(("seek", seconds))

    def set_share(self, enabled):
        self.calls.append(("share", enabled))
        self.state["send_to_call"] = enabled
        if enabled:
            self.state["call_connected"] = True
        self.state_changed.emit()

    def set_background(self, path, image_path, crop):
        self.calls.append(("background", path, image_path, crop))

    def set_avatar(self, path, image_path, crop):
        self.calls.append(("avatar", path, image_path, crop))

    def set_audio_stream(self, path, index):
        self.calls.append(("audio_stream", path, index))

    def set_language(self, code):
        self.settings["ui_language"] = code
        self.state["ui_language"] = code
        self.state_changed.emit()

    def configure_call_temporarily(self):
        self.calls.append(("configure_temporary",))

    def test_call_output(self):
        self.calls.append(("test_call",))

    def disconnect_call(self):
        self.calls.append(("disconnect_call",))
        self.state["call_connected"] = False
        self.state_changed.emit()


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    if not QFontDatabase.families():
        from pathlib import Path
        fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
        for name in ("msyh.ttc", "msyhbd.ttc", "segoeui.ttf", "seguisym.ttf", "seguiemj.ttf"):
            if (fonts / name).is_file():
                QFontDatabase.addApplicationFont(str(fonts / name))
    app.setFont(QFont("Microsoft YaHei UI", 10))
    app.setQuitOnLastWindowClosed(False)
    return app


def test_theme_round_trip_changes_styles_and_preserves_play_state(main):
    window, controller = main, main.controller
    controller.trigger(controller.items[0].path)
    before = controller.state.copy()
    set_theme("light")
    assert "#f5f6f8" in window.styleSheet()
    assert style_for("color: #edf4f5;") == "color: #232b33;"
    set_theme("dark")
    assert "#191c21" in window.styleSheet()
    assert controller.state == before
    set_theme("system")


def test_selection_entire_hit_region_and_folder_name_only(main, qapp):
    window, controller = main, main.controller
    row = window.rows[controller.items[0].key]
    QTest.mouseClick(row.check, Qt.MouseButton.LeftButton, pos=QPoint(row.check.width() - 2, 30))
    assert controller.items[0].key in window._checked
    assert window.folder_path.text() == "常用"
    assert window.call_button.isCheckable() and window.call_button.height() >= 38


def test_seek_large_hit_area_does_not_change_range(qapp):
    waveform = WaveformWidget()
    waveform.resize(400, 90)
    waveform.set_audio(10, 2, 7)
    waveform.set_playhead(3)
    waveform.show()
    seek = QSignalSpy(waveform.seekRequested)
    edit = QSignalSpy(waveform.range_changed)
    point = QPoint(round(waveform._seek_x_for(5)), round(waveform._wave_rect().center().y()))
    QTest.mouseClick(waveform, Qt.MouseButton.LeftButton, pos=point)
    assert seek.count() == 1 and edit.count() == 0
    assert (waveform.start, waveform.end) == (2, 7)
    waveform.close()


@pytest.fixture
def main(qapp):
    controller = FakeController()
    window = MainWindow(controller)
    window.show()
    qapp.processEvents()
    yield window
    window._quitting = True
    window.tray.hide()
    window.mini.hide()
    window.close()
    window.mini.deleteLater()
    window.deleteLater()
    qapp.processEvents()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_waveform_click_never_triggers_playback_but_avatar_toggles(main, qapp):
    row = next(iter(main.rows.values()))
    QTest.mouseClick(row.waveform, Qt.MouseButton.LeftButton, pos=QPoint(row.waveform.width() // 2, 40))
    assert main.controller.calls == []
    QTest.mouseClick(row.play_button, Qt.MouseButton.LeftButton)
    assert main.controller.state["playing"]
    QTest.mouseClick(row.play_button, Qt.MouseButton.LeftButton)
    assert not main.controller.state["playing"]
    assert [call[0] for call in main.controller.calls] == ["trigger", "trigger"]


def test_drag_range_clamps_to_one_second_and_only_emits_on_release(qapp):
    waveform = WaveformWidget()
    waveform.resize(420, 68)
    waveform.set_audio(10, 2, 7, [.2] * 80)
    waveform.show()
    qapp.processEvents()
    changed = QSignalSpy(waveform.range_changed)
    start = QPoint(round(waveform.x_for(2)), round(waveform._wave_rect().center().y()))
    QTest.mousePress(waveform, Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(waveform, QPoint(round(waveform.x_for(9)), start.y()))
    assert waveform.end - waveform.start == pytest.approx(1)
    assert changed.count() == 0
    QTest.mouseRelease(waveform, Qt.MouseButton.LeftButton, pos=QPoint(round(waveform.x_for(9)), 44))
    assert changed.count() == 1
    assert changed.at(0) == pytest.approx([6, 7])
    waveform.close()


def test_short_source_has_no_editable_range(qapp):
    waveform = WaveformWidget()
    waveform.resize(300, 64)
    waveform.set_audio(.8, 0, .8)
    changed = QSignalSpy(waveform.range_changed)
    QTest.mousePress(waveform, Qt.MouseButton.LeftButton, pos=QPoint(9, 40))
    QTest.mouseMove(waveform, QPoint(100, 40))
    QTest.mouseRelease(waveform, Qt.MouseButton.LeftButton, pos=QPoint(100, 40))
    assert changed.count() == 0
    assert waveform.end == .8


def test_volume_wheel_and_popup_is_below_button(qapp):
    button = VolumeButton()
    button.resize(36, 34)
    button.move(300, 200)
    button.show()
    button.set_value(80)
    changed = QSignalSpy(button.volume_changed)
    event = QWheelEvent(QPointF(15, 15), QPointF(315, 215), QPoint(), QPoint(0, 120), Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(button, event)
    assert button.value == 85
    assert changed.at(0) == [85]
    button.open_popup()
    qapp.processEvents()
    assert button.popup.y() >= button.mapToGlobal(QPoint(0, button.height())).y()
    button.popup.hide()
    button.close()


def test_recording_prevents_mini_and_folder_change(main, qapp):
    main.controller.state["recording"] = True
    main.controller.state_changed.emit()
    main.show_mini()
    assert main.isVisible()
    assert not main.mini.isVisible()
    assert not main.mini_button.isEnabled()
    assert not main.folder_button.isEnabled()


def test_mini_has_only_avatar_toggle_no_transport_pause_stop_buttons(main, qapp):
    main.show_mini()
    assert main.mini.isVisible()
    roles = [button.property("iconName") for button in main.mini.findChildren(QToolButton)]
    assert "stop" not in roles
    assert "pause" not in roles
    assert "play" not in roles
    assert "previous" in roles and "next" in roles
    main.show_main()


def test_signature_persists_across_inline_sections_and_missing_driver_is_local_only(main, qapp):
    dialog = main.open_settings(tab=1)
    for index in (1, 2, 1):
        assert main.open_settings(tab=index) is dialog
        qapp.processEvents()
        assert main.signature.isVisible()
        assert main.signature.text() == "Bilibili那年松江"
    assert "尚未检测到" in dialog.call_heading.text()
    assert main.controller.state["call_connected"] is False
    dialog.reject()
    assert main.signature.isVisible()


def test_millisecond_format_carries_seconds():
    assert time_text(1.9996, True) == "00:02.000"


def test_state_change_updates_range_and_duration_without_rebuilding_row(main):
    row = next(iter(main.rows.values()))
    item = main.controller.items[0]
    item.start, item.end = 3, 5.5
    main.controller.state_changed.emit()
    assert row.waveform.start == 3
    assert row.waveform.end == 5.5
    assert row.duration_label.text() == "2.50 秒"


def test_settings_reports_controller_error_without_false_success(main, qapp):
    dialog = SettingsDialog(main.controller, main)
    def fail(values):
        main.controller.message.emit("快捷键已占用", True)
    main.controller.save_settings = fail
    dialog.save()
    assert dialog.feedback.text() == "快捷键已占用"
    dialog.reject()


def test_narrow_window_keeps_recording_and_transport_controls_visible(main, qapp):
    main.resize(780, 500)
    qapp.processEvents()
    for widget in (main.record_button, main.play_pause, main.mode, main.local_volume, main.call_button):
        assert widget.isVisible()
        rect = widget.rect()
        bottom_right = widget.mapTo(main, rect.bottomRight())
        assert bottom_right.x() < main.width()
        assert bottom_right.y() < main.height()


def test_live_language_preserves_recording_rows_common_settings_and_shortcut_drafts(main, qapp):
    dialog = main.open_settings(tab=2)
    row = next(iter(main.rows.values()))
    QTest.mouseClick(main.always_on_top, Qt.MouseButton.LeftButton)
    dialog._hotkey_fields["stop"].setKeySequence(QKeySequence("Ctrl+Alt+9"))
    main.controller.state.update(recording=True, record_seconds=42.5, playing=True, path=main.controller.items[0].path)
    main.controller.set_language("en")
    qapp.processEvents()
    assert main.record_button.text() == "Finish & save"
    assert dialog.heading.text() == "Shortcuts"
    assert main.rows[row.item.key] is row
    assert main.controller.state["recording"] and main.controller.state["playing"]
    assert not main.always_on_top.isChecked()
    assert dialog._hotkey_fields["stop"].keySequence().toString() == "Ctrl+Alt+9"
    assert row.play_button.title == "牛来了"
    assert main._device_combos["mic_device"].itemText(1) == "测试麦克风"
    main.controller.set_language("zh_CN")
    assert main.record_button.text() == "结束并保存"
    assert dialog.heading.text() == "快捷键"
    assert main.signature.text() == "Bilibili那年松江"
    dialog.reject()


def test_english_error_templates_preserve_user_paths_and_nested_diagnostics(main):
    main.controller.set_language("en")
    path = r"C:\音效\录音.wav"
    main.show_message("已保存：" + path)
    assert main.status.text() == "Saved: " + path
    main.show_message("临时配置失败：原通信输入无法确认，未修改系统", True)
    assert main.status.text() == "!  Temporary setup failed: The original input could not be confirmed. No system change was made."
    main.controller.set_language("zh_CN")
    assert main.status.text() == "!  临时配置失败：原通信输入无法确认，未修改系统"


def test_mini_exact_geometry_and_all_eight_controls_fit(main, qapp):
    main.show_mini()
    qapp.processEvents()
    mini = main.mini
    assert (mini.width(), mini.height()) == (380, 48)
    assert (mini.play_button.width(), mini.play_button.height()) == (120, 36)
    controls = [mini.layout().itemAt(i).widget() for i in range(1, mini.layout().count())]
    assert len(controls) == 8
    for button in controls:
        assert button.width() == 28
        assert button.geometry().right() < mini.width()
    assert [button.property("iconName") for button in controls[-3:]] == ["expand", "hide", "close"]


def test_hide_preserves_audio_and_exit_surfaces_restore_errors(main, qapp, monkeypatch):
    dialog = main.open_settings(tab=2)
    dialog._hotkey_fields["stop"].setKeySequence(QKeySequence("Ctrl+Alt+9"))
    main.controller.state.update(recording=True, call_connected=True)
    events = []
    main.controller.shutdown = lambda: events.append("shutdown") or ["原麦克风当前不可用，已保留恢复记录"]
    monkeypatch.setattr(QMessageBox, "warning", lambda parent, title, body: events.append(body))
    monkeypatch.setattr(qapp, "quit", lambda: events.append("quit"))
    main.hide_to_tray()
    assert not main.isVisible()
    assert not events
    assert main.controller.state["recording"] and main.controller.state["call_connected"]
    main.show_main()
    assert main._settings_dialog is dialog
    assert dialog._hotkey_fields["stop"].keySequence().toString() == "Ctrl+Alt+9"
    main.quit_app()
    assert events[0] == "shutdown" and events[-1] == "quit"
    assert "恢复记录" in events[1]
    dialog.reject()


def test_call_settings_routes_actions_and_updates_live_meters(main, qapp):
    dialog = main.open_settings(tab=1)
    assert not dialog.temporary_button.isEnabled()
    assert not dialog.test_button.isEnabled()
    main.controller.state.update(call_pair_ready=True, call_ready=True, call_connected=True, call_input_name="CABLE Output (custom name)", mic_peak=.35, call_peak=.6, local_peak=.12, call_diagnostic="片段和麦克风送入通话")
    main.controller.state_changed.emit()
    assert dialog.expected_input.text() == "CABLE Output (custom name)"
    assert {key: meter.value() for key, meter in dialog.meters.items()} == {"mic_peak": 35, "call_peak": 60, "local_peak": 12}
    QTest.mouseClick(dialog.temporary_button, Qt.MouseButton.LeftButton)
    QTest.mouseClick(dialog.test_button, Qt.MouseButton.LeftButton)
    assert ("share", True) in main.controller.calls
    assert ("test_call",) in main.controller.calls
    for key in ("playing", "paused"):
        main.controller.state[key] = True
        main.controller.state_changed.emit()
        assert not dialog.test_button.isEnabled()
        main.controller.state[key] = False
    dialog.reject()


def test_avatar_crop_drag_zoom_and_reset_keep_square_within_original(qapp):
    image = QImage(800, 400, QImage.Format.Format_RGB32)
    image.fill(QColor("#44aa88"))
    canvas = CropCanvas(image)
    canvas.resize(360, 360)
    canvas.show()
    assert canvas.crop == (200, 0, 400, 400)
    canvas.set_zoom(200)
    assert canvas.crop == (300, 100, 200, 200)
    QTest.mousePress(canvas, Qt.MouseButton.LeftButton, pos=QPoint(180, 180))
    QTest.mouseMove(canvas, QPoint(2000, 2000))
    QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=QPoint(2000, 2000))
    assert canvas.crop == (0, 0, 200, 200)
    event = QWheelEvent(QPointF(180, 180), QPointF(180, 180), QPoint(), QPoint(0, 120), Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(canvas, event)
    assert canvas.zoom == 210
    x, y, w, h = canvas.crop
    assert w == h and x >= 0 and y >= 0 and x + w <= 800 and y + h <= 400
    canvas.reset()
    assert canvas.crop == (200, 0, 400, 400)
    canvas.close()


def test_avatar_dialog_preserves_original_and_restores_existing_crop(qapp, tmp_path):
    path = tmp_path / "avatar.png"
    image = QImage(500, 300, QImage.Format.Format_RGB32)
    image.fill(QColor("#33aabb"))
    assert image.save(str(path))
    original = path.read_bytes()
    dialog = AvatarCropDialog(str(path), (10, 20, 100, 100))
    assert dialog.crop == (10, 20, 100, 100)
    dialog.canvas.set_zoom(400)
    dialog.reject()
    assert path.read_bytes() == original
    assert list(tmp_path.iterdir()) == [path]


def test_avatar_crop_uses_exif_oriented_coordinates(qapp, tmp_path):
    path = tmp_path / "rotated.jpg"
    image = QImage(400, 200, QImage.Format.Format_RGB32)
    image.fill(QColor("teal"))
    assert image.save(str(path))
    # A minimal EXIF IFD with orientation 6 (90 degrees clockwise).
    exif = bytes.fromhex("45786966000049492a0008000000010012010300010000000600000000000000")
    jpeg = path.read_bytes()
    path.write_bytes(jpeg[:2] + b"\xff\xe1" + (len(exif) + 2).to_bytes(2, "big") + exif + jpeg[2:])
    dialog = AvatarCropDialog(str(path))
    assert (dialog.canvas.image.width(), dialog.canvas.image.height()) == (200, 400)
    assert dialog.crop == (0, 100, 200, 200)
    dialog.reject()


def test_english_narrow_window_keeps_bottom_controls_inside(main, qapp):
    main.controller.set_language("en")
    main.resize(780, 500)
    qapp.processEvents()
    for widget in (main.record_button, main.play_pause, main.mode, main.local_volume, main.call_button):
        assert widget.isVisible()
        assert widget.mapTo(main, widget.rect().bottomRight()).x() < main.width()
        assert widget.mapTo(main, widget.rect().bottomRight()).y() < main.height()
    dialog = main.open_settings(tab=1)
    assert dialog.call_heading.text() == "!  VB-CABLE was not detected"
    dialog.reject()


def test_diagnostics_expand_counts_buffers_and_live_language(main, qapp):
    dialog = main.open_settings(tab=1)
    assert dialog.diagnostics_panel.isHidden()
    main.controller.state["diagnostics"] = {
        "capture_dropped_frames": 240, "capture_overflows": 2,
        "output_dropped_frames": 96, "output_underflows": 3,
        "output_underrun_frames": 128, "local_buffer_ms": 12.34, "call_buffer_ms": 8.76,
    }
    main.controller.state_changed.emit()
    dialog.diagnostics_toggle.click()
    assert not dialog.diagnostics_panel.isHidden()
    assert [label.text() for label in dialog.diagnostics_labels] == [
        "采集：丢失 240 帧 · 溢出 2 次", "输出：丢失 96 帧 · 欠载 3 次（128 帧）", "当前缓冲：本地 12.3 ms · 通话 8.8 ms",
    ]
    main.controller.set_language("en")
    assert dialog.diagnostics_toggle.text() == "Hide audio diagnostics"
    assert [label.text() for label in dialog.diagnostics_labels] == [
        "Capture: 240 frames lost · 2 overflows", "Output: 96 frames lost · 3 underruns (128 frames)", "Buffer: local 12.3 ms · call 8.8 ms",
    ]
    main.controller.state["diagnostics"]["local_buffer_ms"] = 0
    main.controller.state_changed.emit()
    assert dialog.diagnostics_labels[2].text() == "Buffer: local 0.0 ms · call 8.8 ms"
    dialog.diagnostics_toggle.click()
    assert dialog.diagnostics_panel.isHidden()
    dialog.reject()


def test_seek_track_only_changes_active_playhead_on_release(qapp):
    wave = WaveformWidget()
    wave.resize(420, 68)
    wave.set_audio(10, 2, 7)
    wave.show()
    seek = QSignalSpy(wave.seekRequested)
    ranges = QSignalSpy(wave.range_changed)
    target = QPoint(round(wave._seek_x_for(5)), round(wave._wave_rect().center().y()))
    QTest.mouseClick(wave, Qt.MouseButton.LeftButton, pos=target)
    assert seek.count() == 0
    wave.set_playhead(3)
    QTest.mousePress(wave, Qt.MouseButton.LeftButton, pos=target)
    assert seek.count() == 0
    QTest.mouseRelease(wave, Qt.MouseButton.LeftButton, pos=target)
    assert seek.at(0) == pytest.approx([5], abs=.03)
    assert ranges.count() == 0 and (wave.start, wave.end) == (2, 7)
    wave.close()


def test_checkbox_batch_delete_and_search_clear_do_not_play(main, monkeypatch):
    row = next(iter(main.rows.values()))
    row.check.click()
    assert main._checked == {row.item.key}
    assert main.controller.calls == []
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    main.delete_selected()
    assert main.controller.calls == [("delete_items", [row.item.path])]
    main.search.setText("不存在")
    assert not main._checked


def test_external_media_drop_routes_to_copy_import_and_share_is_single_switch(main):
    paths = [r"C:\Other\sound.wav", r"C:\Other\music.mp3"]
    main.handle_drop(paths)
    assert main.controller.calls == [("import", paths)]
    main.call_button.click()
    assert main.controller.calls[-1] == ("share", True)


def test_rectangular_crop_stays_inside_original(qapp):
    image = QImage(800, 500, QImage.Format.Format_RGB32)
    canvas = CropCanvas(image, aspect=10)
    assert canvas.crop == (0, 210, 800, 80)
    canvas.set_zoom(200)
    assert canvas.crop == (200, 230, 400, 40)


def test_shift_checkbox_range_and_ctrl_a_only_affect_visible_list(main, qapp):
    main.controller.items = [AudioItem(fr"C:\音效\常用\{index}.wav", "声音" + str(index), 5) for index in range(4)]
    main.refresh_items()
    qapp.processEvents()
    rows = list(main.rows.values())
    QTest.mouseClick(rows[0].check, Qt.MouseButton.LeftButton)
    QTest.mouseClick(rows[2].check, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ShiftModifier)
    assert main._checked == {row.item.key for row in rows[:3]}
    assert not main.controller.calls
    main.check_all(False)
    main.search.setText("声音")
    QTest.keyClick(main.search, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    assert not main._checked
    QTest.keyClick(main.list, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    assert main._checked == set(main.rows)
    main.search.setText("声音1")
    assert not main._checked
    QTest.keyClick(main.list, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    assert main._checked == {main.controller.items[1].key}
    QTest.keyClick(main.list, Qt.Key.Key_Escape)
    assert not main._checked


def _drop_events(widget, position, paths):
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(path) for path in paths])
    actions = Qt.DropAction.CopyAction | Qt.DropAction.MoveAction
    enter = QDragEnterEvent(position, actions, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(widget, enter)
    move = QDragMoveEvent(position, actions, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(widget, move)
    drop = QDropEvent(QPointF(position), actions, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(widget, drop)
    return enter, drop


def test_actual_external_drop_is_copy_on_main_row_and_mini_directory(main):
    paths = [r"C:\外部\音乐.m4a", r"C:\外部\视频.mp4"]
    row = next(iter(main.rows.values()))
    for target, point in ((main, QPoint(10, 10)), (row, QPoint(row.width() // 2, 40)), (main.mini, main.mini.directory_button.geometry().center())):
        enter, drop = _drop_events(target, point, paths)
        assert enter.isAccepted() and drop.isAccepted()
        assert drop.dropAction() == Qt.DropAction.CopyAction
        kind, copied = main.controller.calls[-1]
        assert kind == "import" and list(map(os.path.normpath, copied)) == list(map(os.path.normpath, paths))
    assert [call[0] for call in main.controller.calls] == ["import"] * 3


def test_actual_image_drop_opens_target_crop_and_does_not_play(main, monkeypatch, tmp_path):
    image_path = str(tmp_path / "原图片.png")
    image = QImage(1000, 600, QImage.Format.Format_RGB32)
    image.fill(QColor("#83ccb1"))
    assert image.save(image_path)
    monkeypatch.setattr(AvatarCropDialog, "exec", lambda self: self.DialogCode.Accepted)
    row = next(iter(main.rows.values()))
    avatar_point = row.play_button.geometry().topLeft() + QPoint(20, 20)
    _, drop = _drop_events(row, avatar_point, [image_path])
    assert drop.isAccepted()
    kind, path, source, crop = main.controller.calls[-1]
    assert (kind, path, crop) == ("avatar", row.item.path, (200, 0, 600, 600))
    assert os.path.normpath(source) == os.path.normpath(image_path)
    background_point = row.waveform.geometry().center()
    _, drop = _drop_events(row, background_point, [image_path])
    kind, path, source, crop = main.controller.calls[-1]
    assert drop.isAccepted() and (kind, path) == ("background", row.item.path)
    assert os.path.normpath(source) == os.path.normpath(image_path)
    assert crop[2] == 1000 and crop[3] < 100
    assert all(call[0] != "trigger" for call in main.controller.calls)
    _, drop = _drop_events(main.mini, main.mini.play_button.geometry().topLeft() + QPoint(10, 15), [image_path])
    assert drop.isAccepted() and main.controller.calls[-1][0] == "avatar"


def test_mixed_images_and_media_are_rejected_without_partial_import(main):
    row = next(iter(main.rows.values()))
    assert not main.handle_drop(["a.png", "a.mp3"], row.item, True)
    assert not main.handle_drop(["a.png", "b.jpg"], row.item)
    assert not main.handle_drop(["a.png"])
    assert not main.controller.calls


def test_seek_drag_is_cancelled_when_playback_moves_to_another_row(main):
    row = next(iter(main.rows.values()))
    main.controller.state.update(path=row.item.path, playing=True, position=3)
    main.refresh_state()
    target = QPoint(round(row.waveform._seek_x_for(5)), round(row.waveform._wave_rect().center().y()))
    QTest.mousePress(row.waveform, Qt.MouseButton.LeftButton, pos=target)
    assert row.waveform._drag == "seek"
    main.controller.state.update(path="other.wav")
    main.refresh_state()
    QTest.mouseRelease(row.waveform, Qt.MouseButton.LeftButton, pos=target)
    assert not any(call[0] in ("trigger", "seek", "range") for call in main.controller.calls)


def test_call_share_handles_connecting_and_off_keeps_microphone(main):
    main.controller.state.update(call_connecting=True, call_connected=False, send_to_call=True)
    main.refresh_state()
    assert main.call_button.isChecked()
    assert "正在连接" in main.output_label.text()
    main.controller.state.update(call_connecting=False, call_connected=True)
    main.refresh_state()
    main.call_button.click()
    assert main.controller.calls[-1] == ("share", False)
    assert main.controller.state["call_connected"] and not main.call_button.isChecked()
    assert main.output_label.text() == "仅麦克风"


def test_audio_track_menu_uses_real_stream_indices_and_preserves_titles(main):
    item = main.controller.items[0]
    item.audio_stream = 3
    item.audio_streams = [dict(index=1, codec="aac", channels=2, language="chi", title="设置"),
                          dict(index=3, codec_name="opus", channels=6, tags=dict(language="eng", title="Original"))]
    menu = main.build_item_menu(item)
    submenu = next(action.menu() for action in menu.actions() if action.menu())
    tracks = submenu.actions()
    assert "chi" in tracks[0].text() and "aac" in tracks[0].text() and tracks[0].text().endswith("设置")
    assert tracks[1].isChecked() and not tracks[0].isChecked()
    tracks[0].trigger()
    assert main.controller.calls[-1] == ("audio_stream", item.path, 1)
    menu.deleteLater()


def test_waveforms_are_loaded_on_demand_and_per_audio_track(main, qapp):
    main.hide()
    main.controller.items = [AudioItem(r"C:\音效\常用\video.mp4", "长视频", 3600, audio_stream=1),
                             AudioItem(r"C:\音效\常用\video2.mp4", "另一长视频", 3600)]
    main.refresh_items()
    qapp.processEvents()
    assert main.controller.waveform_requests == []
    item = main.controller.items[0]
    main.request_row_waveform(item)
    main.request_row_waveform(item)
    assert main.controller.waveform_requests == [item.path]
    item.audio_stream = 3
    main.request_row_waveform(item)
    assert main.controller.waveform_requests == [item.path, item.path]


def test_config_error_is_visible_but_does_not_disable_audio_and_translates(main):
    item = main.controller.items[0]
    item.portable_status = "配置包不可用：配置包损坏，已保留原包"
    main.refresh_items()
    row = main.rows[item.key]
    assert row.play_button.isEnabled() and "⚠" in row.duration_label.text()
    assert "配置包损坏" in row.toolTip()
    main.controller.set_language("en")
    assert row.play_button.isEnabled() and "Config" in row.duration_label.text()
    assert "Damaged configuration package" in row.toolTip()
    main.controller.set_language("zh_CN")


def test_rectangular_crop_reopens_exact_original_pixel_coordinates(qapp, tmp_path):
    path = tmp_path / "background.png"
    image = QImage(1000, 600, QImage.Format.Format_RGB32)
    image.fill(QColor("#bbd5ef"))
    image.save(str(path))
    dialog = AvatarCropDialog(str(path), (130, 230, 600, 50), aspect=13)
    assert dialog.crop == (130, 230, 600, 50)
    assert dialog.preview.width() > dialog.preview.height()
    dialog.canvas.reset()
    assert dialog.crop[2] == 1000 and dialog.crop[3] == 83
    dialog.close()


def test_top_devices_follow_controller_state_without_writing_system_defaults(main):
    controller = main.controller
    long_name = "USB 耳机／扬声器 " + "很长的设备名称" * 12
    devices = [DeviceInfo("mic2", "真实麦克风二", "input"),
               DeviceInfo("long-output", long_name, "output"),
               DeviceInfo("cable-in", "CABLE Output", "input", is_virtual=True),
               DeviceInfo("cable-out", "CABLE Input", "output", is_virtual=True)]
    controller.available_devices = lambda: devices
    controller.settings.update(mic_device="mic2", local_device="long-output")
    controller.state_changed.emit()
    mic, local = (main._device_combos[key] for key in ("mic_device", "local_device"))
    assert mic.currentData() == "mic2" and local.currentData() == "long-output"
    assert local.currentText() == long_name and long_name in local.toolTip()
    assert mic.findData("cable-in") < 0 and local.findData("cable-out") < 0
    assert not any(call[0] == "system_device" for call in controller.calls)
    controller.settings["mic_device"] = "unplugged"
    controller.state_changed.emit()
    assert mic.currentData() == "unplugged"
    assert mic.currentText() == "已保存的设备当前不可用"
    controller.set_language("en")
    assert mic.currentText() == "Saved device is currently unavailable"
    assert mic.itemText(0) == "Microphone"
    assert local.currentText() == long_name
    controller.set_language("zh_CN")
    mic.setCurrentIndex(mic.findData("mic2"))
    mic.activated.emit(mic.currentIndex())
    assert controller.calls[-1] == ("system_device", "mic_device", "mic2")
    assert not any(call[0] == "save_settings" for call in controller.calls)


def test_top_theme_and_mini_priority_save_immediately_without_changing_audio(main, qapp):
    controller = main.controller
    controller.state.update(recording=True, call_connected=True, playing=True)
    before = controller.state.copy()
    theme = main.theme_choice
    theme.setCurrentIndex(theme.findData("light"))
    theme.activated.emit(theme.currentIndex())
    QTest.mouseClick(main.always_on_top, Qt.MouseButton.LeftButton)
    assert controller.settings["theme"] == "light"
    assert controller.settings["mini_on_top"] is False
    assert not main.mini.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    assert controller.state == before
    assert [call[0] for call in controller.calls] == ["theme", "mini_on_top"]
    set_theme("system")


def test_failed_top_setting_save_restores_actual_values(main, qapp):
    controller = main.controller
    def failed(_):
        controller.message.emit("设置保存失败：磁盘已满", True)
        controller.state_changed.emit()
        return False
    controller.set_theme = failed
    controller.set_mini_on_top = failed
    main.theme_choice.setCurrentIndex(main.theme_choice.findData("light"))
    main.theme_choice.activated.emit(main.theme_choice.currentIndex())
    QTest.mouseClick(main.always_on_top, Qt.MouseButton.LeftButton)
    assert main.theme_choice.currentData() == "system"
    assert main.always_on_top.isChecked()
    assert main.mini.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    assert "磁盘已满" in main.status.text()


def test_inline_entries_switch_one_panel_and_keep_shortcut_drafts(main, qapp):
    QTest.mouseClick(main.hotkeys_button, Qt.MouseButton.LeftButton)
    panel = main._settings_dialog
    assert panel.isWindow() and not panel.isModal() and panel.section == "hotkeys"
    assert main.hotkeys_button.isChecked() and not main.call_help_button.isChecked()
    panel._hotkey_fields["stop"].setKeySequence(QKeySequence("Ctrl+Alt+9"))
    QTest.mouseClick(main.call_help_button, Qt.MouseButton.LeftButton)
    assert main._settings_dialog is panel and panel.section == "call"
    assert main.call_help_button.isChecked() and not main.hotkeys_button.isChecked()
    assert not panel.save_button.isVisible()
    QTest.mouseClick(main.call_help_button, Qt.MouseButton.LeftButton)
    assert panel.isHidden()
    QTest.mouseClick(main.hotkeys_button, Qt.MouseButton.LeftButton)
    assert panel._hotkey_fields["stop"].keySequence().toString() == "Ctrl+Alt+9"
    assert not hasattr(panel, "theme_choice") and not hasattr(panel, "_device_combos")
    assert main.signature.isVisible()


def test_shortcut_save_only_writes_shortcuts_and_keeps_top_settings(main, qapp):
    panel = main.open_settings(tab=2)
    panel._hotkey_fields["stop"].setKeySequence(QKeySequence("Ctrl+Alt+9"))
    main.controller.set_theme("dark")
    main.controller.set_mini_on_top(False)
    main.controller.change_system_device("local_device", "speaker")
    panel.save()
    values = next(call[1] for call in reversed(main.controller.calls) if call[0] == "save_settings")
    assert values == {"global_hotkeys": {"stop": "Ctrl+Alt+9"}}
    assert main.controller.settings["theme"] == "dark"
    assert main.controller.settings["mini_on_top"] is False
    assert main.controller.settings["local_device"] == "speaker"
    panel._hotkey_fields["stop"].clear()
    panel.save()
    assert main.controller.settings["global_hotkeys"] == {}
    set_theme("system")


def test_inline_shortcut_focus_suspends_and_every_hide_path_resumes(main, qapp):
    panel = main.open_settings(tab=2)
    field = panel._hotkey_fields["stop"]
    panel.hotkeys_page.ensureWidgetVisible(field)
    field.setFocus()
    qapp.processEvents()
    assert panel._capturing
    assert main.controller.calls[-1] == ("suspend", True)
    main.open_settings(tab=1)
    qapp.processEvents()
    assert not panel._capturing and main.controller.calls[-1] == ("suspend", False)
    main.open_settings(tab=2)
    field.setFocus()
    qapp.processEvents()
    assert panel._capturing
    main.hide_to_tray()
    qapp.processEvents()
    assert not panel._capturing
    main.show_main()
    qapp.processEvents()
    assert panel.isVisible()
    field.setFocus()
    qapp.processEvents()
    main.close_settings()
    qapp.processEvents()
    assert not panel._capturing and main.controller.calls[-1] == ("suspend", False)
    main.open_settings(tab=2)
    field.setFocus()
    qapp.processEvents()
    main.show_mini()
    qapp.processEvents()
    assert not panel._capturing and panel.isHidden()
    assert panel._hotkey_fields["stop"] is field


@pytest.mark.parametrize("language", ["zh_CN", "en"])
@pytest.mark.parametrize("tab", [1, 2])
def test_narrow_helper_layout_keeps_playlist_size_and_controls_in_own_windows(main, qapp, language, tab):
    name = "Physical headset " + "very long device name " * 15
    main.controller.available_devices = lambda: [DeviceInfo("mic", name, "input"), DeviceInfo("speaker", name, "output")]
    main.controller.set_language(language)
    main.resize(780, 500)
    before = main.list_stack.height()
    panel = main.open_settings(tab=tab)
    for _ in range(4):
        qapp.processEvents()
    assert main.size().width() == 780 and main.size().height() == 500
    assert main.list.viewport().height() >= 92
    assert main.list_stack.height() == before
    assert panel.isWindow() and not panel.isModal()
    assert panel.tabs.currentWidget().viewport().height() > 20
    controls = [main.call_help_button, main.hotkeys_button, main.theme_choice, main.always_on_top,
                *main._device_combos.values(), main.record_button, main.play_pause, main.mode,
                main.local_volume, main.call_button, main.signature]
    if tab == 2:
        assert panel.save_button.isVisible()
        assert panel.save_button.mapTo(panel, panel.save_button.rect().bottomRight()).x() < panel.width()
        first_field = panel._hotkey_fields["play_pause"]
        viewport = panel.hotkeys_page.viewport()
        field_bottom = first_field.mapTo(viewport, first_field.rect().bottomRight())
        assert field_bottom.y() < viewport.height()
    for widget in controls:
        assert widget.isVisible()
        point = widget.mapTo(main, widget.rect().bottomRight())
        assert 0 <= point.x() < main.width() and 0 <= point.y() < main.height()
    common = [*main._device_combos.values()]
    for left, right in zip(common, common[1:]):
        assert left.geometry().right() < right.geometry().left()
    assert main.theme_choice.width() == 2 * main.language_button.width()
    assert all(combo.width() >= 72 for combo in main._device_combos.values())
    main.controller.set_language("zh_CN")


def test_inline_panel_reuse_and_disposal_do_not_duplicate_controller_connections(main, qapp):
    panel = main.open_settings(tab=2)
    for _ in range(5):
        main.close_settings()
        assert main.open_settings(tab=2) is panel
    assert main.controller.receivers("2state_changed()") == 2
    assert main.controller.receivers("2message(QString,bool)") == 2
    panel.dispose()
    panel.dispose()
    assert main.controller.receivers("2state_changed()") == 1
    assert main.controller.receivers("2message(QString,bool)") == 1


def test_item_shortcut_editor_does_not_lose_capture_to_helper_focus_checks(main, qapp):
    panel = main.open_settings(tab=2)
    panel._hotkey_fields["stop"].setFocus()
    qapp.processEvents()
    assert panel._capturing
    editor = main.change_hotkey(main.controller.items[0])
    assert main._hotkey_dialog_active and editor.isVisible() and not editor.isModal()
    assert main.controller.calls[-1] == ("suspend", True)
    panel._check_capture_focus()
    assert main.controller.calls[-1] == ("suspend", True)
    main.change_hotkey(main.controller.items[0])
    assert not main._hotkey_dialog_active
    assert panel.isHidden()
    main.open_settings(tab=2)
    panel._hotkey_fields["stop"].setFocus()
    qapp.processEvents()
    assert panel._capturing
