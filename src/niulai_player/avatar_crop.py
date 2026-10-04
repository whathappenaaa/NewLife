"""Non-destructive avatar framing; the media library owns saving the original/crop."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QImageReader, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QSlider, QVBoxLayout, QWidget

from .i18n import tr
from .style import APP_STYLE, set_style, theme_color
from .widgets import app_icon


class CropCanvas(QWidget):
    changed = Signal()

    def __init__(self, image: QImage, initial_crop=None, parent=None, aspect=1.0):
        super().__init__(parent)
        if image.isNull():
            raise ValueError("图片无法读取，请选择其他图片。")
        self.image = image
        self.aspect = max(.01, float(aspect))
        if initial_crop and len(initial_crop) == 4 and aspect != 1 and initial_crop[3] > 0:
            self.aspect = initial_crop[2] / initial_crop[3]
        self.base_size = float(min(image.width(), image.height() * self.aspect))
        self.side = self.base_size
        self.center = QPointF(image.width() / 2, image.height() / 2)
        self._drag_point = None
        self._drag_center = None
        self.setMinimumSize(280, 280)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setAccessibleName("裁剪头像" if self.aspect == 1 else "裁剪背景")
        if initial_crop and len(initial_crop) == 4:
            x, y, width, height = map(float, initial_crop)
            if width > 0 and height > 0 and abs(width / height - self.aspect) < .01 and x >= 0 and y >= 0 and x + width <= image.width() and y + height <= image.height():
                self.side = width
                self.center = QPointF(x + width / 2, y + height / 2)

    @property
    def zoom(self):
        return max(100, round(self.base_size / self.side * 100))

    @property
    def crop(self) -> tuple[int, int, int, int]:
        side = max(1, min(round(self.side), self.image.width()))
        height = max(1, min(round(self.side / self.aspect), self.image.height()))
        x = max(0, min(self.image.width() - side, round(self.center.x() - self.side / 2)))
        y = max(0, min(self.image.height() - height, round(self.center.y() - self.side / self.aspect / 2)))
        return x, y, side, height

    def selection_rect(self):
        width = max(1, min(self.width() - 34, (self.height() - 34) * self.aspect))
        height = width / self.aspect
        return QRectF((self.width() - width) / 2, (self.height() - height) / 2, width, height)

    def _clamp(self):
        self.center.setX(max(self.side / 2, min(self.image.width() - self.side / 2, self.center.x())))
        half_height = self.side / self.aspect / 2
        self.center.setY(max(half_height, min(self.image.height() - half_height, self.center.y())))

    def set_zoom(self, value: int):
        self.side = self.base_size / max(1, value / 100)
        self._clamp()
        self.update()
        self.changed.emit()

    def reset(self):
        self.center = QPointF(self.image.width() / 2, self.image.height() / 2)
        self.side = self.base_size
        self.update()
        self.changed.emit()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(theme_color("#081118")))
        crop = self.selection_rect()
        scale = crop.width() / self.side
        destination = QRectF(crop.left() - (self.center.x() - self.side / 2) * scale,
                             crop.top() - (self.center.y() - self.side / self.aspect / 2) * scale,
                             self.image.width() * scale, self.image.height() * scale)
        painter.drawImage(destination, self.image)
        mask = QPainterPath()
        mask.addRect(QRectF(self.rect()))
        mask.addRect(crop)
        mask.setFillRule(Qt.FillRule.OddEvenFill)
        painter.fillPath(mask, QColor(0, 0, 0, 155))
        painter.setPen(QPen(QColor(theme_color("#b4f4e7")), 1.5))
        painter.drawRect(crop)
        painter.setPen(QPen(QColor(220, 255, 246, 90), 1, Qt.PenStyle.DashLine))
        for fraction in (1 / 3, 2 / 3):
            painter.drawLine(QPointF(crop.left() + crop.width() * fraction, crop.top()), QPointF(crop.left() + crop.width() * fraction, crop.bottom()))
            painter.drawLine(QPointF(crop.left(), crop.top() + crop.height() * fraction), QPointF(crop.right(), crop.top() + crop.height() * fraction))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_point = event.position()
            self._drag_center = QPointF(self.center)
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self.grabMouse()
            event.accept()

    def mouseMoveEvent(self, event):
        if self._drag_point is not None:
            delta = event.position() - self._drag_point
            scale = self.selection_rect().width() / self.side
            self.center = self._drag_center - delta / scale
            self._clamp()
            self.update()
            self.changed.emit()
            event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._drag_point is not None:
            self._drag_point = None
            self._drag_center = None
            self.releaseMouse()
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            event.accept()

    def wheelEvent(self, event):
        if event.angleDelta().y():
            self.set_zoom(min(800, max(100, self.zoom + (10 if event.angleDelta().y() > 0 else -10))))
        event.accept()


class AvatarCropDialog(QDialog):
    def __init__(self, image_path: str, initial_crop=None, parent=None, aspect=1.0):
        super().__init__(parent)
        reader = QImageReader(image_path)
        reader.setAutoTransform(True)
        if reader.size().width() * reader.size().height() > 40_000_000:
            raise ValueError("图片过大，请选择尺寸更小的图片。")
        image = reader.read()
        if image.isNull():
            raise ValueError("图片无法读取，请选择其他图片。")
        set_style(self, APP_STYLE)
        self.setWindowIcon(app_icon())
        self.setWindowTitle("裁剪头像" if aspect == 1 else "裁剪背景")
        self.resize(480, 530)
        self.setMinimumSize(390, 440)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        instructions = QLabel("拖动图片调整位置，滚轮或滑块缩放")
        instructions.setWordWrap(True)
        layout.addWidget(instructions)
        self.canvas = CropCanvas(image, initial_crop, self, aspect=aspect)
        layout.addWidget(self.canvas, 1)
        row = QHBoxLayout()
        row.addWidget(QLabel("缩放"))
        self.zoom = QSlider(Qt.Orientation.Horizontal)
        self.zoom.setRange(100, max(800, self.canvas.zoom))
        self.zoom.setValue(self.canvas.zoom)
        self.zoom.setSingleStep(10)
        self.zoom.valueChanged.connect(self.canvas.set_zoom)
        row.addWidget(self.zoom, 1)
        self.zoom_label = QLabel(f"{self.canvas.zoom}%")
        self.zoom_label.setFixedWidth(45)
        row.addWidget(self.zoom_label)
        layout.addLayout(row)
        bottom = QHBoxLayout()
        reset = QPushButton("重置")
        reset.clicked.connect(self.canvas.reset)
        bottom.addWidget(reset)
        self.preview = QLabel()
        self.preview.setFixedSize(36, 36) if aspect == 1 else self.preview.setFixedSize(140, 28)
        self.preview.setToolTip("应用后的预览")
        bottom.addWidget(self.preview)
        bottom.addStretch()
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        bottom.addWidget(cancel)
        apply = QPushButton("应用")
        apply.setObjectName("Primary")
        apply.clicked.connect(self.accept)
        bottom.addWidget(apply)
        layout.addLayout(bottom)
        self.canvas.changed.connect(self._refresh)
        self._refresh()

    @property
    def crop(self):
        return self.canvas.crop

    def _refresh(self):
        self.zoom.blockSignals(True)
        self.zoom.setValue(self.canvas.zoom)
        self.zoom.blockSignals(False)
        self.zoom_label.setText(f"{self.canvas.zoom}%")
        self.preview.setPixmap(QPixmap.fromImage(self.canvas.image.copy(*self.canvas.crop)).scaled(self.preview.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
