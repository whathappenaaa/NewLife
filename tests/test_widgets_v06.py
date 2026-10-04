import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication

from niulai_player.style import MINT, set_theme, theme_color
from niulai_player.widgets import AvatarButton, VolumeButton, WaveformWidget, control_icon


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def wave(qapp):
    widget = WaveformWidget()
    widget.resize(420, 90)
    widget.set_audio(10, 2, 7, [0.0] * 120)
    widget.show()
    qapp.processEvents()
    yield widget
    widget.close()
    widget.deleteLater()
    qapp.processEvents()


def seek_point(wave, seconds):
    return QPoint(round(wave._seek_x_for(seconds)), round(wave._wave_rect().center().y()))


@pytest.mark.parametrize("position", [-100, 2, 4.5, 7, 100])
def test_seek_circle_stays_entirely_within_range(wave, position):
    wave.set_playhead(position)
    left, right = wave._range_bounds()
    centre = wave._seek_x_for(wave.playhead)
    assert centre - wave.SEEK_RADIUS >= left + wave.HANDLE_HALF_WIDTH
    assert centre + wave.SEEK_RADIUS <= right - wave.HANDLE_HALF_WIDTH + 1e-9
    assert wave.start <= wave.playhead <= wave.end
    assert wave._seek_rect().center().y() == wave._wave_rect().center().y()


def test_one_second_range_in_long_recording_remains_operable(wave):
    wave.set_audio(3600, 1800, 1801)
    wave.set_playhead(1801)
    left, right = wave._range_bounds()
    assert wave._seek_x_for(1800) - wave.SEEK_RADIUS >= left
    assert wave._seek_x_for(1801) + wave.SEEK_RADIUS <= right
    assert wave._seek_rect().width() >= 8
    assert wave._handle_at(QPoint(round(left), round(wave._wave_rect().top() + 2))) == "start"
    assert wave._handle_at(QPoint(round(right), round(wave._wave_rect().top() + 2))) == "end"


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_horizontal_track_distinguishes_played_and_unplayed_without_vertical_line(wave, qapp, theme):
    set_theme(theme)
    wave.set_playhead(4.5)
    qapp.processEvents()
    image = wave.grab().toImage()
    r = wave._seek_rect()
    x = wave._seek_x_for(4.5)
    y = round(wave._wave_rect().center().y())
    played = image.pixelColor(round((r.left() + x) / 2), y)
    unplayed = image.pixelColor(round((x + r.right()) / 2), y)
    assert played.name() == theme_color(MINT).lower()
    assert unplayed.name() == theme_color("#3c515b").lower()
    # Above the circle there is no line extending towards the wave's top.
    assert image.pixelColor(round(x), y - 11).name() != played.name()
    set_theme("system")


def test_seek_drag_commits_once_and_clamps_both_edges(wave):
    wave.set_playhead(3)
    seeks = QSignalSpy(wave.seekRequested)
    ranges = QSignalSpy(wave.range_changed)
    target = seek_point(wave, 5)
    QTest.mousePress(wave, Qt.MouseButton.LeftButton, pos=target)
    QTest.mouseMove(wave, QPoint(-100, target.y()))
    assert seeks.count() == 0
    QTest.mouseRelease(wave, Qt.MouseButton.LeftButton, pos=QPoint(-100, target.y()))
    assert seeks.at(0) == [2.0]
    QTest.mouseClick(wave, Qt.MouseButton.LeftButton,
                     pos=QPoint(round(wave._wave_rect().right()), target.y()))
    assert seeks.at(1) == [7.0]
    assert ranges.count() == 0
    assert (wave.start, wave.end) == (2, 7)


def test_active_centre_lane_does_not_drag_range_boundary(wave):
    wave.set_playhead(3)
    ranges = QSignalSpy(wave.range_changed)
    seeks = QSignalSpy(wave.seekRequested)
    boundary = QPoint(round(wave.x_for(2)), round(wave._wave_rect().center().y()))
    QTest.mouseClick(wave, Qt.MouseButton.LeftButton, pos=boundary)
    assert seeks.at(0) == [2.0]
    assert ranges.count() == 0


def test_handle_cap_edits_while_playing_and_warns_once_at_minimum(wave):
    wave.set_playhead(3)
    ranges = QSignalSpy(wave.range_changed)
    seeks = QSignalSpy(wave.seekRequested)
    warnings = QSignalSpy(wave.minimumReached)
    target = QPoint(round(wave._range_bounds()[0]), round(wave._wave_rect().top() + 2))
    QTest.mousePress(wave, Qt.MouseButton.LeftButton, pos=target)
    finish = QPoint(round(wave.x_for(9)), target.y())
    QTest.mouseMove(wave, finish)
    QTest.mouseMove(wave, finish + QPoint(1, 0))
    assert ranges.count() == 0
    assert warnings.count() == 1
    assert wave.end - wave.start == 1
    QTest.mouseRelease(wave, Qt.MouseButton.LeftButton, pos=finish)
    assert ranges.at(0) == [6.0, 7.0]
    assert seeks.count() == 0


def test_range_escape_cancels_without_committing(wave):
    edits = QSignalSpy(wave.range_changed)
    target = QPoint(round(wave._range_bounds()[0]), round(wave._wave_rect().top() + 2))
    QTest.mousePress(wave, Qt.MouseButton.LeftButton, pos=target)
    QTest.mouseMove(wave, target + QPoint(50, 0))
    QTest.keyClick(wave, Qt.Key.Key_Escape)
    assert (wave.start, wave.end) == (2, 7)
    assert edits.count() == 0
    assert wave._drag is None


def test_inactive_wave_does_not_seek_or_play_and_time_opens_precise_editor(wave):
    seeks = QSignalSpy(wave.seekRequested)
    edits = QSignalSpy(wave.rangeEditRequested)
    QTest.mouseClick(wave, Qt.MouseButton.LeftButton, pos=seek_point(wave, 5))
    assert seeks.count() == 0
    header = QPoint(wave.width() // 2, 10)
    QTest.mousePress(wave, Qt.MouseButton.LeftButton, pos=header)
    assert edits.count() == 0
    QTest.mouseRelease(wave, Qt.MouseButton.LeftButton, pos=header)
    assert edits.count() == 1
    assert seeks.count() == 0


def test_seek_keyboard_respects_range(wave):
    wave.set_playhead(3)
    seeks = QSignalSpy(wave.seekRequested)
    QTest.keyClick(wave, Qt.Key.Key_Home)
    QTest.keyClick(wave, Qt.Key.Key_End)
    assert [seeks.at(i) for i in range(seeks.count())] == [[2.0], [7.0]]


@pytest.mark.parametrize("width", [72, 87, 120])
def test_avatar_visible_frame_matches_hit_region_and_icon_has_fixed_column(qapp, width):
    button = AvatarButton()
    button.setFixedWidth(width)
    button.set_item("这个音频名称很长很长", "🐮", lambda value: value)
    button.show()
    qapp.processEvents()
    clicked = QSignalSpy(button.clicked)
    QTest.mouseClick(button, Qt.MouseButton.LeftButton, pos=QPoint(width // 2, button.height() // 2))
    assert clicked.count() == 1
    QTest.mouseClick(button, Qt.MouseButton.LeftButton, pos=QPoint(1, 1))
    assert clicked.count() == 1
    assert button._play_icon_rect().right() == width - 9
    assert button._play_icon_rect().center().y() == button.height() // 2
    assert "这个音频名称很长很长" in button.toolTip()
    button.close()


def test_volume_popup_same_entry_toggles_and_hiding_clears_active_state(qapp):
    button = VolumeButton()
    button.resize(36, 34)
    button.show()
    button.click()
    assert button.popup.isVisible() and button.isChecked()
    button.click()
    assert not button.popup.isVisible() and not button.isChecked()
    button.click()
    button.popup.hide()
    assert not button.isChecked()
    assert button.popup.testAttribute(Qt.WidgetAttribute.WA_NoMouseReplay)
    button.close()


@pytest.mark.parametrize("name", ["delete", "headphones", "ear"])
def test_new_toolbar_icons_are_visible(qapp, name):
    image = control_icon(name, size=24).pixmap(24, 24).toImage()
    painted = sum(image.pixelColor(x, y).alpha() > 0
                  for y in range(image.height()) for x in range(image.width()))
    assert painted > 20
