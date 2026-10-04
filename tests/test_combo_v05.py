import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QStyle

import niulai_player.widgets as widgets_module
from niulai_player.style import APP_STYLE
from niulai_player.widgets import DarkComboBox


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    if not QFontDatabase.families():
        fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
        for name in ("msyh.ttc", "msyhbd.ttc", "segoeui.ttf", "seguisym.ttf"):
            if (fonts / name).is_file():
                QFontDatabase.addApplicationFont(str(fonts / name))
    return app


@pytest.fixture
def painted_labels(monkeypatch):
    """Observe the text actually passed to Qt's label painter."""
    labels = []
    original = widgets_module.QStylePainter

    class RecordingPainter:
        def __init__(self, widget):
            self.painter = original(widget)

        def __getattr__(self, name):
            return getattr(self.painter, name)

        def drawControl(self, element, option):
            if element == QStyle.ControlElement.CE_ComboBoxLabel:
                labels.append(option.currentText)
            self.painter.drawControl(element, option)

    monkeypatch.setattr(widgets_module, "QStylePainter", RecordingPainter)
    return labels


@pytest.mark.parametrize("direction", [Qt.LayoutDirection.LeftToRight, Qt.LayoutDirection.RightToLeft])
def test_long_device_label_is_elided_only_while_painting(qapp, painted_labels, direction):
    combo = DarkComboBox()
    combo.setStyleSheet(APP_STYLE)
    combo.setFont(QFont("Segoe UI", 11))
    combo.setLayoutDirection(direction)
    full_name = "USB Headphones / 扬声器（Realtek USB Audio with a very long device name）"
    device_id = "wasapi-device-id-that-must-not-change"
    combo.addItem(full_name, device_id)
    combo.setToolTip(full_name)
    combo.resize(200, 36)
    combo.show()
    qapp.processEvents()
    combo.grab()
    shown = painted_labels[-1]
    assert "…" in shown and shown != full_name
    assert combo.currentText() == combo.itemText(0) == combo.toolTip() == full_name
    assert combo.currentData() == device_id
    combo.showPopup()
    qapp.processEvents()
    assert combo.view().isVisible()
    assert combo.model().index(0, 0).data() == full_name
    combo.hidePopup()
    combo.close()
    combo.deleteLater()


@pytest.mark.parametrize("text,width", [
    ("单次播放", 124), ("单段循环", 124), ("顺序播放", 124), ("列表循环", 124),
    ("Play once", 124), ("Repeat one", 124), ("In order", 124), ("Repeat all", 124),
    ("跟随系统", 140), ("Follow system", 140), ("Dark", 140), ("Light", 140),
])
def test_short_play_mode_and_theme_labels_remain_whole(qapp, painted_labels, text, width):
    combo = DarkComboBox()
    combo.setStyleSheet(APP_STYLE)
    combo.addItem(text, "unchanged-value")
    combo.resize(width, 36)
    combo.show()
    qapp.processEvents()
    combo.grab()
    assert painted_labels[-1] == text
    assert combo.currentData() == "unchanged-value"
    combo.close()
    combo.deleteLater()


def test_keyboard_and_popup_selection_keep_full_text_and_data(qapp, painted_labels):
    combo = DarkComboBox()
    combo.setStyleSheet(APP_STYLE)
    names = ["Speakers", "USB device with a very long endpoint name for headphones", "HDMI monitor audio"]
    for index, name in enumerate(names):
        combo.addItem(name, f"device-{index}")
    combo.resize(180, 36)
    combo.show()
    combo.setFocus()
    qapp.processEvents()
    QTest.keyClick(combo, Qt.Key.Key_Down)
    assert (combo.currentText(), combo.currentData()) == (names[1], "device-1")
    combo.grab()
    assert "…" in painted_labels[-1]
    combo.showPopup()
    qapp.processEvents()
    view = combo.view()
    index = combo.model().index(2, 0)
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=view.visualRect(index).center())
    assert (combo.currentText(), combo.currentData()) == (names[2], "device-2")
    assert [combo.itemText(index) for index in range(combo.count())] == names
    combo.close()
    combo.deleteLater()
