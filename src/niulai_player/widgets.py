from __future__ import annotations

import math
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QEvent, QPoint, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QMouseEvent, QPainter, QPainterPath, QPen, QPixmap, QPolygonF, QWheelEvent
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QCheckBox, QComboBox, QLabel, QPushButton, QSlider, QStyle, QStyleOptionComboBox, QStylePainter, QToolButton, QToolTip, QVBoxLayout, QWidget

from .models import MIN_SEGMENT_SECONDS, valid_range
from .style import MINT, MUTED, theme_color, is_light
from .i18n import retranslate, tr


class HitCheckBox(QCheckBox):
    def hitButton(self, position):
        return self.rect().contains(position)

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.checkState() == Qt.CheckState.Unchecked:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor("#ffffff" if is_light() else "#092922"), 2.5))
        y = self.height() // 2
        if self.checkState() == Qt.CheckState.PartiallyChecked:
            painter.drawLine(6, y, 18, y)
        else:
            painter.drawLine(6, y, 10, y + 4)
            painter.drawLine(10, y + 4, 18, y - 5)


def time_text(seconds: float, precise: bool = False) -> str:
    milliseconds = max(0, round(seconds * 1000))
    minutes, remainder = divmod(milliseconds, 60000)
    seconds_value, millis = divmod(remainder, 1000)
    return f"{minutes:02d}:{seconds_value:02d}" + (f".{millis:03d}" if precise else "")


def app_icon(size: int = 64) -> QIcon:
    from .branding import app_icon as brand_icon
    return brand_icon()


def control_icon(name: str, color: str = "#dbe9ed", size: int = 24) -> QIcon:
    """Small resolution-independent shapes avoid platform font/emoji icon differences."""
    pixmap = QPixmap(size * 2, size * 2)
    pixmap.setDevicePixelRatio(2)
    pixmap.fill(Qt.GlobalColor.transparent)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.scale(size / 24, size / 24)
    ink = QColor(theme_color(color))
    p.setPen(QPen(ink, 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    def polygon(points):
        p.setBrush(ink)
        p.drawPolygon(QPolygonF([QPointF(x, y) for x, y in points]))
        p.setBrush(Qt.BrushStyle.NoBrush)
    if name == "play":
        polygon([(8, 5), (19, 12), (8, 19)])
    elif name == "pause":
        p.fillRect(QRectF(6, 5, 4, 14), ink)
        p.fillRect(QRectF(14, 5, 4, 14), ink)
    elif name == "stop":
        p.setBrush(ink)
        p.drawRoundedRect(QRectF(6, 6, 12, 12), 1, 1)
    elif name == "previous":
        p.drawLine(5, 5, 5, 19)
        polygon([(18, 5), (7, 12), (18, 19)])
    elif name == "next":
        p.drawLine(19, 5, 19, 19)
        polygon([(6, 5), (17, 12), (6, 19)])
    elif name == "close":
        p.drawLine(6, 6, 18, 18)
        p.drawLine(18, 6, 6, 18)
    elif name == "minimize":
        p.drawLine(5, 13, 19, 13)
    elif name == "hide":
        path = QPainterPath()
        path.moveTo(2, 12)
        path.cubicTo(7, 4, 17, 4, 22, 12)
        path.cubicTo(17, 20, 7, 20, 2, 12)
        p.drawPath(path)
        p.drawEllipse(QRectF(9, 9, 6, 6))
        p.drawLine(3, 3, 21, 21)
    elif name == "expand":
        p.drawLine(4, 20, 10, 14)
        p.drawLine(14, 10, 20, 4)
        p.drawLine(4, 15, 4, 20)
        p.drawLine(4, 20, 9, 20)
        p.drawLine(15, 4, 20, 4)
        p.drawLine(20, 4, 20, 9)
    elif name == "list":
        for y in (6, 12, 18):
            p.drawPoint(4, y)
            p.drawLine(9, y, 20, y)
    elif name in ("repeat", "repeat_one"):
        p.drawArc(QRectF(4, 5, 16, 14), 35 * 16, 260 * 16)
        polygon([(20, 5), (20, 11), (15, 8)])
        if name == "repeat_one":
            p.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
            p.drawText(QRectF(7, 5, 9, 14), Qt.AlignmentFlag.AlignCenter, "1")
    elif name == "once":
        p.drawLine(4, 12, 20, 12)
        p.drawLine(15, 7, 20, 12)
        p.drawLine(15, 17, 20, 12)
    elif name == "volume":
        polygon([(3, 9), (7, 9), (12, 5), (12, 19), (7, 15), (3, 15)])
        p.drawArc(QRectF(10, 6, 11, 12), -65 * 16, 130 * 16)
        p.drawArc(QRectF(10, 9, 7, 6), -65 * 16, 130 * 16)
    elif name == "settings":
        p.drawEllipse(QRectF(7, 7, 10, 10))
        p.drawEllipse(QRectF(10, 10, 4, 4))
        for i in range(8):
            angle = i * math.pi / 4
            p.drawLine(QPointF(12 + math.cos(angle) * 7, 12 + math.sin(angle) * 7), QPointF(12 + math.cos(angle) * 9.5, 12 + math.sin(angle) * 9.5))
    elif name == "mini":
        p.drawRoundedRect(QRectF(3, 5, 18, 14), 1, 1)
        p.setBrush(ink)
        p.drawRect(QRectF(13, 12, 6, 5))
    elif name == "folder":
        path = QPainterPath()
        path.moveTo(3, 6)
        for x, y in ((10, 6), (12, 8), (21, 8), (21, 19), (3, 19)):
            path.lineTo(x, y)
        path.closeSubpath()
        p.drawPath(path)
    elif name == "delete":
        p.drawLine(4, 6, 20, 6)
        p.drawLine(9, 3, 15, 3)
        p.drawLine(9, 3, 9, 6)
        p.drawLine(15, 3, 15, 6)
        path = QPainterPath()
        path.moveTo(6, 9)
        path.lineTo(7, 21)
        path.lineTo(17, 21)
        path.lineTo(18, 9)
        p.drawPath(path)
        p.drawLine(10, 10, 10, 17)
        p.drawLine(14, 10, 14, 17)
    elif name in ("headphones", "ear"):
        p.drawArc(QRectF(4, 3, 16, 17), 0, 180 * 16)
        p.drawLine(4, 11, 4, 17)
        p.drawLine(20, 11, 20, 17)
        p.drawRoundedRect(QRectF(3, 12, 5, 9), 2, 2)
        p.drawRoundedRect(QRectF(16, 12, 5, 9), 2, 2)
    elif name == "import":
        p.drawLine(12, 3, 12, 15)
        p.drawLine(8, 7, 12, 3)
        p.drawLine(16, 7, 12, 3)
        p.drawLine(3, 15, 3, 20)
        p.drawLine(3, 20, 21, 20)
        p.drawLine(21, 20, 21, 15)
    elif name == "search":
        p.drawEllipse(QRectF(4, 3, 12, 12))
        p.drawLine(15, 14, 21, 20)
    elif name == "mic":
        p.drawRoundedRect(QRectF(9, 3, 6, 12), 3, 3)
        p.drawArc(QRectF(6, 6, 12, 12), 180 * 16, 180 * 16)
        p.drawLine(12, 18, 12, 21)
        p.drawLine(8, 21, 16, 21)
    elif name == "phone":
        path = QPainterPath()
        path.moveTo(6, 3)
        path.lineTo(10, 7)
        path.lineTo(8, 10)
        path.cubicTo(9, 13, 11, 15, 14, 16)
        path.lineTo(17, 14)
        path.lineTo(21, 18)
        path.cubicTo(20, 24, 12, 20, 7, 15)
        path.cubicTo(2, 10, 1, 4, 6, 3)
        p.drawPath(path)
    p.end()
    return QIcon(pixmap)


class WaveformWidget(QWidget):
    """Independent range handles and a centred, bounded seek track.

    Editing never starts playback. The centre lane seeks only the active clip;
    the handle caps above and below it remain usable while that clip plays.
    """

    range_changed = Signal(float, float)
    seekRequested = Signal(float)
    rangeEditRequested = Signal()
    minimumReached = Signal(str)
    SEEK_RADIUS = 7.0
    SEEK_HIT_RADIUS = 11.0
    HANDLE_HALF_WIDTH = 2.0

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.duration = 0.0
        self.start = 0.0
        self.end = 0.0
        self.peaks: list[float] = []
        self.playhead: float | None = None
        self.seek_enabled = False
        self._preview_seek = None
        self._drag: str | None = None
        self._before = (0.0, 0.0)
        self._at_minimum = False
        self._range_drag_anchor = (0.0, 0.0)
        self._time_pressed = False
        self.setMinimumWidth(140)
        self.setMinimumHeight(78)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setToolTip(tr("拖动左右把手调整范围，松手保存；至少 1 秒。波形区域不会播放。"))
        self.setAccessibleName("片段播放范围")

    def set_audio(self, duration: float, start: float, end: float, peaks: list[float] | None = None):
        self.duration = max(0.0, duration)
        if not self._drag:
            self.start, self.end = (valid_range(duration, start, end) if duration >= MIN_SEGMENT_SECONDS else (0.0, duration))
        if peaks is not None:
            self.peaks = peaks
        self.update()

    def set_playhead(self, position: float | None):
        self.playhead = None if position is None else max(self.start, min(self.end, position))
        self.seek_enabled = position is not None
        if not self.seek_enabled and self._drag == "seek":
            self._drag = None
            self._preview_seek = None
            self.releaseMouse()
        self.update()

    def _wave_rect(self) -> QRectF:
        return QRectF(9, 22, max(1, self.width() - 18), max(24, self.height() - 42))

    def x_for(self, seconds: float) -> float:
        r = self._wave_rect()
        return r.left() + r.width() * seconds / max(self.duration, .001)

    def _seconds(self, x: float) -> float:
        r = self._wave_rect()
        return max(0.0, min(self.duration, (x - r.left()) / r.width() * self.duration))

    def _range_bounds(self) -> tuple[float, float]:
        """Keep even a one-second slice of a long recording operable.

        The narrow selection is drawn as a minimum-width capsule; labels and
        signals retain the exact times, and its central track maps those times.
        """
        r = self._wave_rect()
        left, right = self.x_for(self.start), self.x_for(self.end)
        minimum = 2 * (self.SEEK_RADIUS + self.HANDLE_HALF_WIDTH) + 8
        if right - left < minimum:
            centre = (left + right) / 2
            left = max(r.left(), min(centre - minimum / 2, r.right() - minimum))
            right = min(r.right(), left + minimum)
        return left, right

    def _seek_rect(self) -> QRectF:
        left, right = self._range_bounds()
        inset = self.SEEK_RADIUS + self.HANDLE_HALF_WIDTH
        centre = self._wave_rect().center().y()
        return QRectF(left + inset, centre - self.SEEK_HIT_RADIUS,
                      max(1.0, right - left - 2 * inset), 2 * self.SEEK_HIT_RADIUS)

    def _seek_x_for(self, seconds: float) -> float:
        r = self._seek_rect()
        fraction = (max(self.start, min(self.end, seconds)) - self.start) / max(.001, self.end - self.start)
        return r.left() + fraction * r.width()

    def _seek_seconds(self, x: float) -> float:
        r = self._seek_rect()
        fraction = max(0.0, min(1.0, (x - r.left()) / r.width()))
        return self.start + fraction * (self.end - self.start)

    def _handle_at(self, position: QPointF) -> str | None:
        r = self._wave_rect()
        if self.duration < MIN_SEGMENT_SECONDS or not r.adjusted(-8, -4, 8, 4).contains(position):
            return None
        # The centre lane belongs to seeking while playing. Handle caps have
        # their own larger targets on both sides of that lane.
        if self.seek_enabled and abs(position.y() - r.center().y()) <= self.SEEK_HIT_RADIUS:
            return None
        left, right = self._range_bounds()
        distances = abs(position.x() - left), abs(position.x() - right)
        if min(distances) > 12:
            return None
        return "start" if distances[0] <= distances[1] else "end"

    def _update_range_drag(self, x: float, global_position: QPoint | None = None):
        anchor_x, anchor_seconds = self._range_drag_anchor
        value = anchor_seconds + (x - anchor_x) * self.duration / self._wave_rect().width()
        if self._drag == "start":
            self.start = max(0.0, min(value, self.end - MIN_SEGMENT_SECONDS))
            limited = value > self.end - MIN_SEGMENT_SECONDS
        else:
            self.end = min(self.duration, max(value, self.start + MIN_SEGMENT_SECONDS))
            limited = value < self.start + MIN_SEGMENT_SECONDS
        if limited and not self._at_minimum:
            message = tr("片段至少保留 1 秒")
            self.minimumReached.emit(message)
            if global_position is not None:
                QToolTip.showText(global_position, message, self)
        self._at_minimum = limited
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self._wave_rect()
        p.setPen(QColor(theme_color("#d4e5e8")))
        p.setFont(QFont("Segoe UI", 9))
        p.drawText(QRectF(0, 0, self.width(), 21), Qt.AlignmentFlag.AlignCenter, f"{time_text(self.start, True)}  →  {time_text(self.end, True)}")
        if self.duration <= 0:
            p.setPen(QColor(theme_color(MUTED)))
            p.drawLine(r.left(), r.center().y(), r.right(), r.center().y())
            return
        left, right = self._range_bounds()
        selection = QColor(theme_color(MINT))
        selection.setAlpha(22)
        p.fillRect(QRectF(left, r.top(), right - left, r.height()), selection)
        count = max(1, int(r.width() / 3))
        for i in range(count):
            x = r.left() + i * r.width() / count
            index = min(len(self.peaks) - 1, int(i / count * len(self.peaks)))
            peak = abs(self.peaks[index]) if index >= 0 else 0.03
            height = max(1.0, min(1.0, peak) * (r.height() * .43))
            p.setPen(QPen(QColor(theme_color(MINT if left <= x <= right else "#627483")), 1.4))
            p.drawLine(QPoint(round(x), round(r.center().y() - height)), QPoint(round(x), round(r.center().y() + height)))
        if self.duration >= MIN_SEGMENT_SECONDS:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(theme_color(MUTED)))
            for x in (left, right):
                p.drawRoundedRect(QRectF(x - self.HANDLE_HALF_WIDTH, r.top(),
                                        self.HANDLE_HALF_WIDTH * 2, r.height()), 2, 2)
                for cap_y in (r.top(), r.bottom() - 4):
                    p.drawRoundedRect(QRectF(x - 5, cap_y, 10, 4), 2, 2)
        track = self._seek_rect()
        y = r.center().y()
        position = self._preview_seek if self._preview_seek is not None else self.playhead
        position = self.start if position is None else max(self.start, min(self.end, position))
        x = self._seek_x_for(position)
        # A single horizontal axis splits into played/unplayed colours. The
        # filled circle alone represents position; no vertical playhead exists.
        p.setPen(QPen(QColor(theme_color("#3c515b")), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
        p.drawLine(QPointF(track.left(), y), QPointF(track.right(), y))
        if self.seek_enabled:
            p.setPen(QPen(QColor(theme_color(MINT)), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
            p.drawLine(QPointF(track.left(), y), QPointF(x, y))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(theme_color(MINT if self.seek_enabled else MUTED)))
        p.drawEllipse(QPointF(x, y), self.SEEK_RADIUS, self.SEEK_RADIUS)
        p.setPen(QColor(theme_color(MUTED)))
        p.setFont(QFont("Segoe UI", 8))
        footer = f"{time_text(position, True)}  ·  {time_text(self.end - self.start, True)}"
        if self._at_minimum:
            footer = tr("片段至少保留 1 秒")
        p.drawText(QRectF(0, self.height() - 19, self.width(), 19), Qt.AlignmentFlag.AlignCenter, footer)

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        event.accept()
        if self.duration < MIN_SEGMENT_SECONDS:
            return
        if event.position().y() < self._wave_rect().top():
            self._time_pressed = True
            return
        x = event.position().x()
        if self.seek_enabled and abs(event.position().y() - self._wave_rect().center().y()) <= self.SEEK_HIT_RADIUS and self._wave_rect().adjusted(-8, 0, 8, 0).contains(event.position()):
            self._drag = "seek"
            self._preview_seek = self._seek_seconds(x)
            self.setFocus()
            self.grabMouse()
            self.update()
            return
        handle = self._handle_at(event.position())
        if not handle:
            return
        self._drag = handle
        self._before = self.start, self.end
        self._range_drag_anchor = (x, self.start if handle == "start" else self.end)
        self._at_minimum = False
        self.setCursor(Qt.CursorShape.SizeHorCursor)
        self.setFocus()
        self.grabMouse()

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._drag == "seek":
            self._preview_seek = self._seek_seconds(event.position().x())
            self.update()
            event.accept()
        elif self._drag:
            self._update_range_drag(event.position().x(), event.globalPosition().toPoint())
            event.accept()
        else:
            handle = self._handle_at(event.position())
            near_seek = abs(event.position().y() - self._wave_rect().center().y()) <= self.SEEK_HIT_RADIUS
            self.setCursor(Qt.CursorShape.SizeHorCursor if handle or (near_seek and self.seek_enabled) else Qt.CursorShape.ArrowCursor)
            if handle:
                self.setToolTip(tr("调整起点" if handle == "start" else "调整终点"))
            elif event.position().y() < self._wave_rect().top():
                self.setToolTip(tr("点击输入准确时间"))
            elif near_seek:
                self.setToolTip(tr("拖动播放位置" if self.seek_enabled else "先点击头像或名称播放，再拖动播放位置"))

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mouseReleaseEvent(event)
        event.accept()
        if self._time_pressed:
            self._time_pressed = False
            if self.rect().contains(event.position().toPoint()) and event.position().y() < self._wave_rect().top():
                self.rangeEditRequested.emit()
            return
        if self._drag == "seek":
            value = self._seek_seconds(event.position().x())
            self._drag = None
            self._preview_seek = None
            self.releaseMouse()
            if self.seek_enabled and value is not None:
                self.seekRequested.emit(value)
            self.update()
            return
        if self._drag:
            self._update_range_drag(event.position().x())
            self._drag = None
            self._at_minimum = False
            self.releaseMouse()
            self.unsetCursor()
            if (self.start, self.end) != self._before:
                self.range_changed.emit(self.start, self.end)
            self.update()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape and self._drag:
            if self._drag != "seek":
                self.start, self.end = self._before
            self._preview_seek = None
            self._drag = None
            self._at_minimum = False
            self.releaseMouse()
            self.unsetCursor()
            self.update()
            event.accept()
        elif self.seek_enabled and self.playhead is not None and event.key() in (Qt.Key.Key_Home, Qt.Key.Key_End, Qt.Key.Key_Left, Qt.Key.Key_Right):
            if event.key() == Qt.Key.Key_Home:
                position = self.start
            elif event.key() == Qt.Key.Key_End:
                position = self.end
            else:
                position = self.playhead + (-.1 if event.key() == Qt.Key.Key_Left else .1)
            self.seekRequested.emit(max(self.start, min(self.end, position)))
            event.accept()
        else:
            super().keyPressEvent(event)


class AvatarButton(QPushButton):
    def __init__(self, parent: QWidget | None = None, compact: bool = False):
        super().__init__(parent)
        self.compact = compact
        self.title = "选择声音"
        self.avatar = "🐮"
        self.avatar_pixmap = QPixmap()
        self.playing = False
        self.paused = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumWidth(105 if compact else 72)
        self.setFixedHeight(40 if compact else 57)
        self.setAccessibleName("点击头像或名称播放，再点停止")

    def set_item(self, name: str, avatar: str, resolve: Callable[[str], str], playing=False, paused=False):
        self.title, self.avatar, self.playing, self.paused = name, avatar or "🐮", playing, paused
        self.avatar_pixmap = QPixmap()
        if avatar and ("/" in avatar or "\\" in avatar or Path(avatar).suffix):
            try:
                self.avatar_pixmap = QPixmap(resolve(avatar))
            except (OSError, ValueError):
                pass
        self.setToolTip(name + "\n" + tr("再点停止" if playing or paused else "点击播放"))
        self.update()

    def _button_path(self) -> QPainterPath:
        path = QPainterPath()
        path.addRoundedRect(QRectF(.5, .5, self.width() - 1, self.height() - 1), 6, 6)
        return path

    def hitButton(self, position: QPoint) -> bool:
        return self._button_path().contains(QPointF(position))

    def _play_icon_rect(self) -> QRect:
        return QRect(self.width() - 23, (self.height() - 15) // 2, 15, 15)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        active = self.playing or self.paused
        r = QRectF(.5, .5, self.width() - 1, self.height() - 1)
        p.setPen(QPen(QColor(theme_color("#3bc8b4" if active else "#425965")), 1))
        p.setBrush(QColor(theme_color("#1e514e" if active else "#17252e")))
        p.drawPath(self._button_path())
        size = 28 if self.compact else 40
        ar = QRectF(6, (self.height() - size) / 2, size, size)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(theme_color("#263d46")))
        p.drawRoundedRect(ar, 5, 5)
        if not self.avatar_pixmap.isNull():
            p.save()
            clip = QPainterPath()
            clip.addRoundedRect(ar, 5, 5)
            p.setClipPath(clip)
            pixmap = self.avatar_pixmap.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
            p.drawPixmap(ar.toRect(), pixmap, QRect((pixmap.width() - size) // 2, (pixmap.height() - size) // 2, size, size))
            p.restore()
        else:
            p.setFont(QFont("Segoe UI Emoji", 18 if self.compact else 26))
            p.setPen(QColor(theme_color("#ffffff")))
            p.drawText(ar, Qt.AlignmentFlag.AlignCenter, self.avatar if len(self.avatar) < 8 else "🐮")
        text_left = size + (14 if self.compact else 13)
        p.setPen(QColor(theme_color("#eef9f7")))
        p.setFont(QFont("Microsoft YaHei UI", 10, QFont.Weight.DemiBold))
        text_width = max(0, self.width() - text_left - 27)
        title = p.fontMetrics().elidedText(self.title, Qt.TextElideMode.ElideRight, text_width)
        if self.compact:
            p.drawText(QRectF(text_left, 0, text_width, self.height()), Qt.AlignmentFlag.AlignVCenter, title)
        else:
            p.drawText(QRectF(text_left, 7, text_width, 23), Qt.AlignmentFlag.AlignCenter, title)
            p.setPen(QColor(theme_color(MINT if active else "#b8cbd3")))
            p.setFont(QFont("Microsoft YaHei UI", 8))
            cue = p.fontMetrics().elidedText(tr("再点停止" if active else "点击播放"), Qt.TextElideMode.ElideRight, text_width)
            p.drawText(QRectF(text_left, 31, text_width, 18), Qt.AlignmentFlag.AlignCenter, cue)
        icon = control_icon("stop" if active else "play", MINT if active else "#a8c3c9", 15)
        icon.paint(p, self._play_icon_rect())


class VolumeSlider(QSlider):
    """Wheel steps have the same predictable meaning in the main and mini UI."""
    def __init__(self, parent=None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.setRange(0, 100)
        self.setSingleStep(5)
        self.setPageStep(10)
        self.setAccessibleName("音量")

    def wheelEvent(self, event: QWheelEvent):
        delta = event.angleDelta().y()
        if delta:
            self.setValue(self.value() + (5 if delta > 0 else -5))
        event.accept()


class DarkComboBox(QComboBox):
    def paintEvent(self, event):
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        style = self.style()
        field = style.subControlRect(QStyle.ComplexControl.CC_ComboBox, option,
                                     QStyle.SubControl.SC_ComboBoxEditField, self)
        if not option.editable:
            # Paint a shortened label only; popup text and device identifiers
            # remain untouched. The style already reserves padding and the arrow.
            icon_width = option.iconSize.width() + 4 if not option.currentIcon.isNull() else 0
            width = max(0, field.width() - icon_width - 2)
            option.currentText = option.fontMetrics.elidedText(option.currentText, Qt.TextElideMode.ElideRight, width)
        p = QStylePainter(self)
        p.drawComplexControl(QStyle.ComplexControl.CC_ComboBox, option)
        p.drawControl(QStyle.ControlElement.CE_ComboBoxLabel, option)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor(theme_color("#a5c0cc")), 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        arrow = style.subControlRect(QStyle.ComplexControl.CC_ComboBox, option,
                                     QStyle.SubControl.SC_ComboBoxArrow, self)
        x, y = arrow.center().x(), arrow.center().y()
        p.drawLine(x - 3, y - 2, x, y + 1)
        p.drawLine(x, y + 1, x + 3, y - 2)


class VolumeButton(QToolButton):
    volume_changed = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Icon")
        self.setIcon(control_icon("volume"))
        self.setIconSize(QSize(22, 22))
        self.setToolTip("音量 · 点击展开，滚轮调节")
        self.setAccessibleName("音量")
        self.value = 80
        self.setCheckable(True)
        self.popup = QWidget(self, Qt.WindowType.Popup)
        # Clicking the opener while a Qt popup owns the mouse normally closes
        # it and replays that click, which would immediately reopen it.
        self.popup.setAttribute(Qt.WidgetAttribute.WA_NoMouseReplay, True)
        self.popup.installEventFilter(self)
        self.popup.setObjectName("VolumePopup")
        self.popup.setFixedSize(180, 72)
        layout = QVBoxLayout(self.popup)
        layout.setContentsMargins(13, 9, 13, 10)
        self.label = QLabel("本地音量 80%")
        self.slider = VolumeSlider()
        layout.addWidget(self.label)
        layout.addWidget(self.slider)
        self.slider.valueChanged.connect(self._changed)
        self.clicked.connect(self.open_popup)
        self.set_value(80)

    def open_popup(self):
        if self.popup.isVisible():
            self.popup.hide()
            return
        self.popup.move(self.mapToGlobal(QPoint(self.width() // 2 - self.popup.width() // 2, self.height() + 6)))
        self.popup.show()
        self.setChecked(True)

    def eventFilter(self, watched, event):
        if watched is self.popup and event.type() == QEvent.Type.Hide:
            self.setChecked(False)
        return super().eventFilter(watched, event)

    def set_value(self, value: int):
        self.value = max(0, min(100, int(value)))
        self.slider.blockSignals(True)
        self.slider.setValue(self.value)
        self.slider.blockSignals(False)
        self.label.setText(f"本地音量 {self.value}%")
        self.setToolTip(f"本地音量 {self.value}% · 点击展开，滚轮调节")
        retranslate(self)

    def _changed(self, value: int):
        self.set_value(value)
        self.volume_changed.emit(value)

    def wheelEvent(self, event: QWheelEvent):
        if event.angleDelta().y():
            self._changed(max(0, min(100, self.value + (5 if event.angleDelta().y() > 0 else -5))))
        event.accept()
