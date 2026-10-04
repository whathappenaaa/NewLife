import os
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEvent, QMimeData, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QMouseEvent, QWheelEvent
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QToolButton

from niulai_player import i18n
from niulai_player.models import AudioItem, PLAY_MODES
from niulai_player.ui import MiniWindow


class MiniMain:
    """UI-only owner: every action is recorded, with no audio or device access."""

    def __init__(self):
        self.calls = []
        self.controller = SimpleNamespace(
            settings={"mini_on_top": True},
            folder="C:/Sounds/My sounds",
            items=[AudioItem("C:/Sounds/My sounds/a.wav", "这是完整而且很长的音频名称", 10, 0, 10)],
            state={"path": "", "playing": False, "paused": False, "local_volume": 70, "mode": 0},
            resolve_asset=lambda path: path,
            trigger=lambda path: self.calls.append(("trigger", path)),
            previous=lambda: self.calls.append(("previous",)),
            next=lambda: self.calls.append(("next",)),
            set_volume=lambda kind, value: self.calls.append(("volume", kind, value)),
            set_mode=lambda value: self.calls.append(("mode", value)),
        )

    def invoke(self, function, *args):
        function(*args)

    def show_main(self):
        self.calls.append(("expand",))

    def hide_to_tray(self):
        self.calls.append(("hide",))

    def quit_app(self):
        self.calls.append(("quit",))

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()

    def dragLeaveEvent(self, event):
        event.accept()

    def dropped_paths(self, event):
        return [url.toLocalFile() for url in event.mimeData().urls()]

    def handle_drop(self, paths, target, avatar):
        self.calls.append(("drop", paths, target, avatar))
        return True


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def mini(qapp):
    old_language = i18n.language()
    i18n.set_language("zh_CN")
    owner = MiniMain()
    widget = MiniWindow(owner)
    widget.move(200, 200)
    widget.show()
    qapp.processEvents()
    yield widget
    widget.hide()
    widget.deleteLater()
    qapp.processEvents()
    i18n.set_language(old_language)


def send_mouse(widget, event_type, global_point, button=Qt.MouseButton.NoButton, buttons=Qt.MouseButton.NoButton):
    event = QMouseEvent(
        event_type, QPointF(widget.mapFromGlobal(global_point)), QPointF(global_point),
        button, buttons, Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(widget, event)


@pytest.mark.parametrize("local_point", [QPoint(15, 18), QPoint(62, 18)])
def test_drag_avatar_or_title_moves_without_playing_and_releases(mini, local_point):
    avatar = mini.play_button
    pressed = avatar.mapToGlobal(local_point)
    before = mini.pos()
    movement = QPoint(QApplication.startDragDistance() + 15, 11)
    clicks = QSignalSpy(avatar.clicked)
    send_mouse(avatar, QEvent.Type.MouseButtonPress, pressed, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton)
    send_mouse(avatar, QEvent.Type.MouseMove, pressed + movement, buttons=Qt.MouseButton.LeftButton)
    assert mini.pos() == before + movement
    assert not avatar.isDown()
    send_mouse(avatar, QEvent.Type.MouseButtonRelease, pressed + movement, Qt.MouseButton.LeftButton)
    assert clicks.count() == 0
    assert mini.main.calls == []
    assert mini._drag_offset is None and mini._drag_origin is None and not mini._dragging
    send_mouse(avatar, QEvent.Type.MouseMove, pressed + movement + QPoint(50, 0))
    assert mini.pos() == before + movement
    QTest.mouseClick(avatar, Qt.MouseButton.LeftButton, pos=local_point)
    assert clicks.count() == 1
    assert mini.main.calls == [("trigger", mini.main.controller.items[0].path)]


def test_small_pointer_movement_is_one_click_without_window_motion(mini):
    avatar = mini.play_button
    pressed = avatar.mapToGlobal(QPoint(62, 18))
    before = mini.pos()
    clicks = QSignalSpy(avatar.clicked)
    movement = QPoint(max(0, QApplication.startDragDistance() - 1), 0)
    send_mouse(avatar, QEvent.Type.MouseButtonPress, pressed, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton)
    send_mouse(avatar, QEvent.Type.MouseMove, pressed + movement, buttons=Qt.MouseButton.LeftButton)
    send_mouse(avatar, QEvent.Type.MouseButtonRelease, pressed + movement, Qt.MouseButton.LeftButton)
    assert mini.pos() == before
    assert clicks.count() == 1
    assert len(mini.main.calls) == 1


def test_blank_margin_drag_uses_threshold_and_hiding_clears_drag(mini):
    pressed = mini.mapToGlobal(QPoint(2, 2))
    before = mini.pos()
    send_mouse(mini, QEvent.Type.MouseButtonPress, pressed, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton)
    send_mouse(mini, QEvent.Type.MouseMove, pressed + QPoint(1, 0), buttons=Qt.MouseButton.LeftButton)
    assert mini.pos() == before
    movement = QPoint(QApplication.startDragDistance() + 10, 8)
    send_mouse(mini, QEvent.Type.MouseMove, pressed + movement, buttons=Qt.MouseButton.LeftButton)
    assert mini.pos() == before + movement
    mini.hide()
    assert mini._drag_offset is None and mini._drag_origin is None and not mini._dragging
    assert mini.main.calls == []


def test_empty_list_still_allows_dragging_title(mini):
    mini.main.controller.items = []
    mini.refresh()
    assert not mini.play_button.isEnabled()
    pressed = mini.play_button.mapToGlobal(QPoint(62, 18))
    before = mini.pos()
    movement = QPoint(QApplication.startDragDistance() + 10, 0)
    send_mouse(mini.play_button, QEvent.Type.MouseButtonPress, pressed, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton)
    send_mouse(mini.play_button, QEvent.Type.MouseMove, pressed + movement, buttons=Qt.MouseButton.LeftButton)
    send_mouse(mini.play_button, QEvent.Type.MouseButtonRelease, pressed + movement, Qt.MouseButton.LeftButton)
    assert mini.pos() == before + movement
    assert mini.main.calls == []


def test_icon_click_and_geometry_are_preserved(mini):
    assert (mini.width(), mini.height()) == (380, 48)
    assert (mini.play_button.width(), mini.play_button.height()) == (120, 36)
    controls = [mini.layout().itemAt(index).widget() for index in range(1, mini.layout().count())]
    assert len(controls) == 8
    assert all(control.width() == 28 and control.geometry().right() < mini.width() for control in controls)
    before = mini.pos()
    next_button = next(button for button in mini.findChildren(QToolButton) if button.property("iconName") == "next")
    QTest.mouseClick(next_button, Qt.MouseButton.LeftButton)
    assert mini.pos() == before
    assert mini.main.calls == [("next",)]


def test_tooltips_translate_round_trip_and_update_play_mode_volume(mini):
    item = mini.main.controller.items[0]
    for button in mini.findChildren(QToolButton):
        assert button.toolTip() and button.accessibleName()
    assert item.name in mini.play_button.toolTip()
    assert "点击播放" in mini.play_button.toolTip() and "拖动" in mini.play_button.toolTip()
    assert "My sounds" in mini.directory_button.toolTip() and "拖入音频或视频" in mini.directory_button.toolTip()
    i18n.set_language("en")
    assert item.name in mini.play_button.toolTip()
    assert "Click to play" in mini.play_button.toolTip() and "Drag the avatar" in mini.play_button.toolTip()
    assert "My sounds" in mini.directory_button.toolTip() and "drop audio or video" in mini.directory_button.toolTip()
    assert mini.volume_button.accessibleName() == "Volume"
    mini.main.controller.state.update(path=item.path, playing=True, mode=2, local_volume=35)
    mini.refresh()
    assert "Click again to stop" in mini.play_button.toolTip()
    assert i18n.tr(PLAY_MODES[2]) in mini.mode_button.toolTip()
    assert "35%" in mini.volume_button.toolTip() and "Click to expand" in mini.volume_button.toolTip()
    wheel = QWheelEvent(QPointF(10, 10), QPointF(mini.volume_button.mapToGlobal(QPoint(10, 10))), QPoint(), QPoint(0, 120), Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(mini.volume_button, wheel)
    assert "40%" in mini.volume_button.toolTip() and "Click to expand" in mini.volume_button.toolTip()
    i18n.set_language("zh_CN")
    assert "再点停止" in mini.play_button.toolTip() and "拖动" in mini.play_button.toolTip()
    assert "拖入音频或视频" in mini.directory_button.toolTip()
    assert mini.volume_button.accessibleName() == "音量"
    assert "40%" in mini.volume_button.toolTip() and "点击展开" in mini.volume_button.toolTip()


@pytest.mark.parametrize("suffix,avatar", [(".png", True), (".mp4", False)])
def test_external_drop_remains_copy_with_correct_target(mini, tmp_path, suffix, avatar):
    paths = [str(tmp_path / ("drop" + suffix))]
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(paths[0])])
    point = mini.play_button.geometry().topLeft() + QPoint(10, 15) if avatar else mini.directory_button.geometry().center()
    enter = QDragEnterEvent(point, Qt.DropAction.CopyAction | Qt.DropAction.MoveAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(mini, enter)
    assert enter.isAccepted()
    drop = QDropEvent(QPointF(point), Qt.DropAction.CopyAction | Qt.DropAction.MoveAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(mini, drop)
    assert drop.isAccepted() and drop.dropAction() == Qt.DropAction.CopyAction
    action, received, target, was_avatar = mini.main.calls[-1]
    assert action == "drop" and Path(received[0]) == Path(paths[0])
    assert target is (mini.main.controller.items[0] if avatar else None)
    assert was_avatar is avatar
