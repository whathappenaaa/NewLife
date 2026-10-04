"""Native UI interactions and geometry from the approved v0.6 design."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEvent, QPoint, QRect, Qt, Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog, QMessageBox, QPushButton

from niulai_player.models import AudioItem, path_key
from niulai_player.ui import MainWindow
from niulai_player import i18n
from niulai_player.style import set_theme
import niulai_player.widgets as widgets_module
from test_ui import FakeController, qapp


class V06Controller(FakeController):
    import_finished = Signal(object)

    def set_hotkey(self, path, value):
        self.calls.append(("hotkey", path, value))
        return True

    def undo_delete(self):
        self.calls.append(("undo_delete",))
        self.state.update(delete_undo_available=False, delete_undo_count=0)
        self.state_changed.emit()

    def reset_avatar(self, path):
        self.calls.append(("reset_avatar", path))

    def reset_background(self, path):
        self.calls.append(("reset_background", path))

    def reset_range(self, path):
        self.calls.append(("reset_range", path))


@pytest.fixture
def window(qapp):
    controller = V06Controller()
    result = MainWindow(controller)
    result.show()
    qapp.processEvents()
    yield result
    result._quitting = True
    result.tray.hide()
    result.mini.hide()
    result.close()
    result.mini.deleteLater()
    result.deleteLater()
    qapp.processEvents()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    i18n.set_language("zh_CN")
    set_theme("system")


def center_in(widget, parent):
    return widget.mapTo(parent, widget.rect().center())


@pytest.mark.parametrize("width", [780, 960])
@pytest.mark.parametrize("language", ["zh_CN", "en"])
def test_two_toolbar_rows_share_sizes_and_table_columns(window, qapp, width, language):
    window.controller.set_language(language)
    window.resize(width, 650)
    qapp.processEvents()
    long = window._long_toolbar
    short = window._short_toolbar
    assert len({widget.width() for widget in long}) == 1
    assert len({widget.width() for widget in short}) == 1
    assert long[0].width() == short[0].width() * 2
    assert len({center_in(widget, window).y() for widget in (*long, *short)}) == 1
    assert len({center_in(widget, window).y() for widget in window._folder_controls}) == 1
    unit = window.delete_selected_button.width()
    for widget, ratio in zip(window._folder_controls, (2, 2, 4, 4, 4, 2, 1)):
        assert abs(widget.width() - unit * ratio) <= 3
        assert widget.isVisible()
        assert widget.mapTo(window, widget.rect().bottomRight()).x() < width
    row = next(iter(window.rows.values()))
    columns = ((window.header_labels["number"], row.number),
               (window.select_all, row.check),
               (window.header_labels["play"], row.play_button),
               (window.header_labels["wave"], row.waveform),
               (window.header_labels["duration"], row.duration_label),
               (window.header_labels["hotkey"], row.hotkey))
    for heading, cell in columns:
        assert abs(center_in(heading, window).x() - center_in(cell, window).x()) <= 1
    assert row.number.geometry().right() < row.check.geometry().left()
    assert row.number.width() + row.check.width() + row.layout().spacing() * 2 == 58
    assert row.play_button.width() == (120 if width < 870 else 150)
    assert row.waveform.width() > row.play_button.width()


def test_scrollbar_does_not_shift_header_centres(window, qapp):
    window.controller.items = [AudioItem(fr"C:\音效\常用\{i}.wav", str(i), 10) for i in range(15)]
    window.refresh_items()
    qapp.processEvents()
    assert window.list.verticalScrollBar().maximum() > 0
    row = next(iter(window.rows.values()))
    for key, cell in (("play", row.play_button), ("wave", row.waveform), ("duration", row.duration_label), ("hotkey", row.hotkey)):
        assert abs(center_in(window.header_labels[key], window).x() - center_in(cell, window).x()) <= 1


def test_helpers_toggle_and_keep_drafts_without_resizing_list(window, qapp):
    height = window.list_stack.height()
    window.call_help_button.click()
    helper = window._settings_dialog
    assert helper.isWindow() and not helper.isModal()
    assert window.call_help_button.isChecked()
    window.call_help_button.click()
    assert helper.isHidden() and not window.call_help_button.isChecked()
    window.hotkeys_button.click()
    helper._hotkey_fields["stop"].setKeySequence(QKeySequence("Ctrl+Alt+9"))
    window.call_help_button.click()
    assert helper.section == "call" and not window.hotkeys_button.isChecked()
    window.hotkeys_button.click()
    assert helper._hotkey_fields["stop"].keySequence().toString() == "Ctrl+Alt+9"
    qapp.processEvents()
    assert window.list_stack.height() == height
    assert helper.y() >= window.y() + 90
    window.hotkeys_button.click()
    assert helper.isHidden() and not helper._capturing


def test_language_button_directly_switches_while_audio_state_is_preserved(window):
    controller = window.controller
    controller.state.update(recording=True, playing=True, path=controller.items[0].path)
    before = controller.state.copy()
    window.language_button.click()
    assert controller.settings["ui_language"] == "en"
    assert window.language_button.text() == "中文"
    assert all(controller.state[key] == value for key, value in before.items())
    window.language_button.click()
    assert controller.settings["ui_language"] == "zh_CN"
    assert window.language_button.text() == "EN"


def test_range_editor_repeated_entry_keeps_draft_and_invalid_save_stays_open(window):
    item = window.controller.items[0]
    dialog = window.precise_range(item)
    assert dialog.isVisible() and not dialog.isModal()
    dialog.start_field.setValue(4.5)
    dialog.end_field.setValue(5)
    save = next(button for button in dialog.findChildren(QPushButton) if button.text() == "保存")
    save.click()
    assert dialog.isVisible() and not window.controller.calls
    assert window.precise_range(item) is dialog and dialog.isHidden()
    window.precise_range(item)
    assert dialog.start_field.value() == 4.5
    dialog.end_field.setValue(6)
    save.click()
    assert window.controller.calls[-1] == ("range", item.path, 4.5, 6)
    assert dialog.isHidden()


def test_clip_hotkey_editor_repeated_click_retains_draft_and_restores_background_keys(window):
    item = window.controller.items[0]
    dialog = window.change_hotkey(item)
    dialog.field.setKeySequence(QKeySequence("Ctrl+Alt+8"))
    assert window.controller.calls[-1] == ("suspend", True)
    window.change_hotkey(item)
    assert dialog.isHidden() and window.controller.calls[-1] == ("suspend", False)
    window.change_hotkey(item)
    assert dialog.field.keySequence().toString() == "Ctrl+Alt+8"
    save = next(button for button in dialog.findChildren(QPushButton) if button.text() == "保存")
    save.click()
    assert ("hotkey", item.path, "Ctrl+Alt+8") in window.controller.calls
    assert dialog.isHidden() and window.controller.calls[-1] == ("suspend", False)


def test_import_results_show_each_failed_reason_and_can_toggle(window):
    result = dict(folder=window.controller.folder, imported=[r"C:\音效\常用\成功.wav"], skipped=[r"C:\音效\常用\原有.wav"], failed=[dict(path=r"C:\外部\坏文件.mp3", error="文件损坏")])
    window.controller.import_finished.emit(result)
    dialog = window._import_result_dialog
    assert dialog.isVisible() and not dialog.isModal()
    assert window.import_details.count() == 3
    assert "坏文件.mp3" in window.import_details.item(2).text()
    assert "文件损坏" in window.import_details.item(2).text()
    assert window.import_details.item(2).toolTip().startswith(r"C:\外部\坏文件.mp3")
    window.import_button.click()
    assert dialog.isHidden()
    window.import_button.click()
    assert dialog.isVisible() and window.import_details.count() == 3


def test_hidden_import_result_retranslates_without_reopening_or_changing_names(window):
    window.show_import_result(dict(folder=window.controller.folder, imported=[], skipped=[], failed=[dict(path=r"C:\外部\录音.mp3", error="此文件没有可播放的音轨")]))
    window.import_button.click()
    assert window._import_result_dialog.isHidden()
    window.controller.set_language("en")
    assert window._import_result_dialog.isHidden()
    assert "录音.mp3" in window.import_details.item(0).text()
    assert "no playable audio track" in window.import_details.item(0).text()
    assert "failed" in window.import_summary.text().lower()


def test_changing_helper_entry_hides_other_panel_and_preserves_each_draft(window):
    item = window.controller.items[0]
    precision = window.precise_range(item)
    precision.start_field.setValue(3.5)
    window.hotkeys_button.click()
    assert precision.isHidden() and window._settings_dialog.isVisible()
    editor = window.change_hotkey(item)
    assert window._settings_dialog.isHidden() and editor.isVisible()
    editor.field.setKeySequence(QKeySequence("Ctrl+Alt+7"))
    window.precise_range(item)
    assert editor.isHidden() and precision.isVisible()
    assert precision.start_field.value() == 3.5
    window.change_hotkey(item)
    assert precision.isHidden() and editor.field.keySequence().toString() == "Ctrl+Alt+7"


def test_delete_and_undo_controls_follow_selection_and_actual_worker_state(window, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *args: pytest.fail("Deletion should offer undo rather than stack confirmation dialogs"))
    assert not window.delete_selected_button.isEnabled()
    row = next(iter(window.rows.values()))
    row.check.click()
    assert window.delete_selected_button.isEnabled()
    assert "1" in window.status.text()
    window.delete_selected_button.click()
    assert window.controller.calls[-1] == ("delete_items", [row.item.path])
    window.controller.state.update(delete_undo_available=True, delete_undo_count=1, delete_busy=True)
    window.refresh_state()
    assert window.undo_button.isVisible() and not window.undo_button.isEnabled()
    assert not window.delete_selected_button.isEnabled()
    window.controller.state["delete_busy"] = False
    window.refresh_state()
    window.undo_button.click()
    assert window.controller.calls[-1] == ("undo_delete",)
    assert window.undo_button.isHidden()


def test_bottom_action_buttons_and_left_frames_align_even_during_recording(window, qapp):
    for width in (780, 960):
        window.resize(width, 650)
        window.controller.state.update(recording=True, record_seconds=125.4)
        window.refresh_state()
        qapp.processEvents()
        assert center_in(window.record_button, window).x() == center_in(window.call_button, window).x()
        assert window.record_button.width() == window.call_button.width() == 176
        assert center_in(window.record_left, window).x() == center_in(window.transport_left, window).x()
        assert window.record_left.width() == window.transport_left.width()
        assert window.record_button.text() == "结束并保存"
        assert "02:05" in window.record_time.toolTip() and "电脑" in window.record_time.toolTip()
        assert all(not radio.isEnabled() for radio in window.record_radios)
        assert window.signature.text() == "Bilibili那年松江"


def test_completed_recording_is_highlighted_and_scrolled_without_playback(window, qapp):
    new = AudioItem(r"C:\音效\常用\刚录好.wav", "刚录好", 3)
    window.controller.items.extend(AudioItem(fr"C:\音效\常用\{i}.wav", str(i), 10) for i in range(9))
    window.controller.items.append(new)
    window.controller.state.update(recent_recording_path=new.path, recent_recording_revision=1)
    window.refresh_items()
    qapp.processEvents()
    assert window.rows[new.key].property("recentRecording")
    assert path_key(window.list.currentItem().data(Qt.ItemDataRole.UserRole)) == new.key
    assert not any(call[0] == "trigger" for call in window.controller.calls)


def test_seek_request_cannot_change_another_rows_playback(window):
    item = window.controller.items[0]
    row = window.rows[item.key]
    window.controller.state.update(path=r"C:\other.wav", playing=True)
    window.refresh_state()
    row.waveform.seekRequested.emit(4)
    assert not window.controller.calls
    window.controller.state["path"] = item.path
    window.refresh_state()
    row.waveform.seekRequested.emit(4)
    assert window.controller.calls == [("seek", 4)]


def test_right_click_resets_are_independent_actions(window):
    item = window.controller.items[0]
    menu = window.build_item_menu(item)
    for text, method in (("恢复默认头像", "reset_avatar"), ("恢复默认背景", "reset_background"), ("恢复完整播放范围", "reset_range")):
        next(action for action in menu.actions() if action.text() == text).trigger()
        assert window.controller.calls[-1] == (method, item.path)
    menu.deleteLater()


def test_empty_device_controls_have_distinct_labels_and_icons(window):
    window.controller.available_devices = lambda: []
    window.controller.settings.update(mic_device="", local_device="")
    window.refresh_state()
    mic, output = (window._device_combos[key] for key in ("mic_device", "local_device"))
    assert mic.currentText() == "麦克风" and output.currentText() == "耳机"
    assert not mic.itemIcon(0).isNull() and not output.itemIcon(0).isNull()
    assert "本人讲话" in mic.toolTip() and "你听声音" in output.toolTip()


@pytest.mark.parametrize("width", [780, 960])
def test_fixed_theme_and_play_modes_paint_complete_english_labels(window, qapp, monkeypatch, width):
    labels = []
    original = widgets_module.QStylePainter
    class RecordingPainter:
        def __init__(self, widget):
            self.painter = original(widget)
        def __getattr__(self, name):
            return getattr(self.painter, name)
        def drawControl(self, element, option):
            labels.append(option.currentText)
            self.painter.drawControl(element, option)
    monkeypatch.setattr(widgets_module, "QStylePainter", RecordingPainter)
    window.controller.set_language("en")
    window.resize(width, 650)
    qapp.processEvents()
    for combo in (window.theme_choice, window.mode):
        for index in range(combo.count()):
            combo.blockSignals(True)
            combo.setCurrentIndex(index)
            combo.blockSignals(False)
            combo.grab()
            assert labels[-1] == combo.currentText()


def test_helper_shrinks_to_high_scale_work_area_and_keeps_toolbar_reachable(window, monkeypatch):
    class SmallLogicalScreen:
        def availableGeometry(self):
            return QRect(0, 0, 960, 516)
    monkeypatch.setattr(window, "screen", lambda: SmallLogicalScreen())
    window.move(0, 0)
    helper = window.open_settings(tab=1)
    assert helper.height() <= 416
    assert helper.y() == 100
    assert helper.geometry().bottom() < 516
    assert helper.geometry().right() < 960
    assert helper.frameGeometry().bottom() < 516
    assert window.call_help_button.mapToGlobal(window.call_help_button.rect().bottomRight()).y() < helper.y()
    window.call_help_button.click()
    assert helper.isHidden()


def test_failed_clip_editor_save_keeps_dialog_and_draft_visible(window):
    item = window.controller.items[0]
    dialog = window.change_hotkey(item)
    dialog.field.setKeySequence(QKeySequence("Ctrl+Alt+6"))
    def fail(path, sequence):
        window.controller.message.emit("快捷键已占用", True)
        return False
    window.controller.set_hotkey = fail
    next(button for button in dialog.findChildren(QPushButton) if button.text() == "保存").click()
    assert dialog.isVisible() and dialog.feedback.text() == "快捷键已占用"
    assert dialog.field.keySequence().toString() == "Ctrl+Alt+6"
    dialog.hide()
    precise = window.precise_range(item)
    def failed_range(path, start, end):
        raise OSError("磁盘已满")
    window.controller.set_range = failed_range
    next(button for button in precise.findChildren(QPushButton) if button.text() == "保存").click()
    assert precise.isVisible() and "磁盘已满" in window.status.text()
