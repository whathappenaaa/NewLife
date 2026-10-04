from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QRectF, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QActionGroup, QColor, QDesktopServices, QDrag, QFont, QKeySequence, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QButtonGroup, QCheckBox, QColorDialog, QComboBox,
    QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog, QFormLayout, QFrame,
    QGridLayout, QHBoxLayout, QInputDialog, QKeySequenceEdit, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox, QPushButton,
    QRadioButton, QScrollArea, QSizeGrip, QSizePolicy, QSlider, QStackedWidget,
    QSystemTrayIcon, QTabWidget, QToolButton, QVBoxLayout, QWidget,
    QProgressBar,
)

from .models import AudioItem, PLAY_MODES, RECORD_SOURCES, SUPPORTED_EXTENSIONS, path_key
from . import __version__
from .style import APP_STYLE, MINT, MUTED, theme_color, set_theme, set_style, is_light
from .widgets import HitCheckBox, AvatarButton, DarkComboBox, VolumeButton, VolumeSlider, WaveformWidget, app_icon, control_icon, time_text
from . import i18n
from .i18n import tr
from .avatar_crop import AvatarCropDialog

QComboBox = DarkComboBox
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"}

ICON_NAMES = {"|◀": "previous", "▶|": "next", "▶": "play", "Ⅱ": "pause", "■": "stop", "⚙": "settings", "−": "minimize", "×": "close", "⤢": "expand", "☷": "list", "↻": "repeat", "↺¹": "repeat_one", "→": "once"}


def set_control_icon(button, name: str):
    button.setText("")
    button.setIcon(control_icon(name))
    button.setIconSize(QSize(21, 21))
    button.setProperty("iconName", name)


def icon_button(text: str, tooltip: str, callback=None, width=30) -> QToolButton:
    button = QToolButton()
    button.setObjectName("Icon")
    set_control_icon(button, ICON_NAMES.get(text, text))
    button.setToolTip(tooltip)
    button.setAccessibleName(tooltip)
    button.setFixedSize(width, 32)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    if callback:
        button.clicked.connect(callback)
    return button


def muted(text: str, small=False) -> QLabel:
    label = QLabel(text)
    label.setObjectName("Hint" if small else "Muted")
    return label


def line() -> QFrame:
    frame = QFrame()
    frame.setObjectName("Line")
    frame.setFixedHeight(1)
    return frame


class WindowDragLabel(QLabel):
    def __init__(self, text: str, window: QWidget):
        super().__init__(text)
        self.target = window
        self.offset: QPoint | None = None

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.offset = event.globalPosition().toPoint() - self.target.frameGeometry().topLeft()

    def mouseMoveEvent(self, event):
        if self.offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.target.move(event.globalPosition().toPoint() - self.offset)

    def mouseReleaseEvent(self, event):
        self.offset = None


class DragNumber(QLabel):
    drag_requested = Signal()

    def __init__(self, text: str):
        super().__init__(text)
        self._origin = None
        self.setToolTip("拖动序号调整顺序")
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._origin = event.position().toPoint()

    def mouseMoveEvent(self, event):
        if self._origin is not None and event.buttons() & Qt.MouseButton.LeftButton:
            if (event.position().toPoint() - self._origin).manhattanLength() >= QApplication.startDragDistance():
                self._origin = None
                self.drag_requested.emit()

    def mouseReleaseEvent(self, event):
        self._origin = None


class MediaList(QListWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            self.window.dragEnterEvent(event)
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            self.window.dragEnterEvent(event)
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):
        if event.mimeData().hasUrls():
            self.window.dropEvent(event)
        else:
            super().dropEvent(event)

    def dragLeaveEvent(self, event):
        self.window.dragLeaveEvent(event)
        super().dragLeaveEvent(event)

    def keyPressEvent(self, event):
        if event.matches(QKeySequence.StandardKey.SelectAll):
            self.window.check_all(True)
            event.accept()
        elif event.key() == Qt.Key.Key_Escape:
            self.window.check_all(False)
            event.accept()
        else:
            super().keyPressEvent(event)


class ClipEditorDialog(QDialog):
    """Modeless editor reused on repeated entry clicks, with explicit draft saving."""
    visibility_changed = Signal()

    def __init__(self, main, hotkey=False):
        super().__init__(main, Qt.WindowType.Tool)
        self.main = main
        self.hotkey = hotkey
        self.setModal(False)
        set_style(self, APP_STYLE)
        main.controller.message.connect(self.receive_message)

    def receive_message(self, text, error=False):
        feedback = getattr(self, "feedback", None)
        if self.isVisible() and feedback is not None:
            feedback.setText(tr(text))
            feedback.setToolTip(tr(text))
            set_style(feedback, "color: #ffc09b;" if error else "color: #57dec8;")

    def showEvent(self, event):
        if self.hotkey:
            self.main._hotkey_dialog_active = True
            if self.main._settings_dialog:
                self.main._settings_dialog._set_capture(False)
            self.main.invoke(self.main.controller.suspend_hotkeys, True)
        super().showEvent(event)
        self.visibility_changed.emit()

    def hideEvent(self, event):
        if self.hotkey:
            self.main._hotkey_dialog_active = False
            self.main.invoke(self.main.controller.suspend_hotkeys, False)
            if self.main._settings_dialog:
                QTimer.singleShot(0, self.main._settings_dialog._check_capture_focus)
        super().hideEvent(event)
        self.visibility_changed.emit()


class ClipRow(QFrame):
    def __init__(self, item: AudioItem, index: int, window: "MainWindow"):
        super().__init__()
        self.item, self.window = item, window
        self.background_pixmap = QPixmap()
        if item.background:
            try:
                self.background_pixmap = QPixmap(window.controller.resolve_asset(item.background))
            except (OSError, ValueError):
                pass
        self.active = False
        self._drop_field = ""
        self.setAcceptDrops(True)
        self.setFixedHeight(92)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(lambda point: window.item_menu(item, self.mapToGlobal(point)))
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 7, 10, 7)
        layout.setSpacing(6)
        self.check = HitCheckBox()
        self.check.setObjectName("SelectAudio")
        self.check.setFixedSize(22, 36)
        self.check.setChecked(item.key in window._checked)
        self.check.setToolTip("选择此音频，可批量删除")
        self.check.clicked.connect(lambda checked: window.check_item(item.key, checked))
        self.number = DragNumber(f"{index + 1:02d}")
        self.number.setFixedWidth(24)
        self.number.drag_requested.connect(lambda: window.begin_drag(item.path))
        layout.addWidget(self.number)
        layout.addWidget(self.check)
        self.play_button = AvatarButton()
        self.play_button.setFixedWidth(window.play_column_width())
        self.play_button.setEnabled(item.playable)
        self.play_button.clicked.connect(lambda: window.invoke(window.controller.trigger, item.path))
        self.play_button.set_item(item.name, item.avatar, window.controller.resolve_asset)
        layout.addWidget(self.play_button)
        self.waveform = WaveformWidget()
        self.waveform.set_audio(item.duration, item.start, item.end, item.peaks)
        self.waveform.setEnabled(item.playable)
        self.waveform.range_changed.connect(lambda start, end: window.invoke(window.controller.set_range, item.path, start, end))
        self.waveform.seekRequested.connect(lambda seconds: window.invoke(window.controller.seek, seconds) if self.active else None)
        self.waveform.rangeEditRequested.connect(lambda: window.precise_range(item))
        self.waveform.minimumReached.connect(lambda boundary: window.show_message("片段至少保留 1 秒"))
        layout.addWidget(self.waveform, 1)
        self.duration_label = QLabel(f"{item.segment_duration:.2f} 秒")
        self.duration_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.duration_label.setFixedWidth(58)
        layout.addWidget(self.duration_label)
        self.hotkey = QPushButton(item.hotkey or "未设置")
        self.hotkey.setCheckable(True)
        self.hotkey.setFixedWidth(80)
        self.hotkey.setToolTip("设置这个声音的全局快捷键")
        self.hotkey.clicked.connect(lambda: window.change_hotkey(item))
        layout.addWidget(self.hotkey)
        if item.error:
            self.setToolTip(item.error)
            self.duration_label.setText("不可播放")
        elif getattr(item, "portable_status", "").startswith("配置包不可用"):
            self.setToolTip(item.portable_status)
            self.duration_label.setToolTip(item.portable_status)
        self.set_state(window.controller.state)

    def set_state(self, state: dict):
        selected = bool(state.get("path")) and path_key(state["path"]) == self.item.key
        playing, paused = selected and state.get("playing", False), selected and state.get("paused", False)
        self.active = playing or paused
        self.play_button.playing, self.play_button.paused = playing, paused
        self.play_button.setToolTip(self.item.name + ("\n再点停止" if self.active else "\n点击播放"))
        self.play_button.update()
        self.waveform.set_audio(self.item.duration, self.item.start, self.item.end)
        warning = getattr(self.item, "portable_status", "").startswith("配置包不可用")
        duration = tr("{value} 秒", value=f"{self.item.segment_duration:.2f}")
        self.duration_label.setText((duration + ("\n" + tr("⚠ 配置") if warning else "")) if self.item.playable else "不可播放")
        self.waveform.set_playhead(state.get("position", self.item.start) if self.active else None)
        self.update()

    def set_peaks(self, peaks: list[float]):
        self.waveform.peaks = peaks
        self.waveform.update()

    def enterEvent(self, event):
        self.window.request_row_waveform(self.item)
        super().enterEvent(event)

    def _image_target(self, point):
        return "avatar" if self.play_button.geometry().contains(point) and point.x() - self.play_button.x() < 51 else "background"

    def dragEnterEvent(self, event):
        self.window.dragEnterEvent(event)
        self.dragMoveEvent(event)

    def dragMoveEvent(self, event):
        self.window.dragEnterEvent(event)
        paths = self.window.dropped_paths(event)
        if paths and all(Path(path).suffix.lower() in IMAGE_EXTENSIONS for path in paths):
            point = event.position().toPoint()
            self._drop_field = self._image_target(point)
            self.update()

    def dragLeaveEvent(self, event):
        self._drop_field = ""
        self.update()
        self.window.dragLeaveEvent(event)

    def dropEvent(self, event):
        point = event.position().toPoint()
        avatar = self._image_target(point) == "avatar"
        self._drop_field = ""
        self.update()
        if self.window.handle_drop(self.window.dropped_paths(event), self.item, avatar):
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
        else:
            event.ignore()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(.5, .5, self.width() - 1, self.height() - 4)
        color = QColor(theme_color(self.item.color) if self.item.color == "#202832" else self.item.color)
        if not color.isValid():
            color = QColor(theme_color("#202c34"))
        p.setBrush(color)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(rect, 7, 7)
        p.save()
        clip = QPainterPath()
        clip.addRoundedRect(rect, 7, 7)
        p.setClipPath(clip)
        if not self.background_pixmap.isNull():
            scaled = self.background_pixmap.scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
            p.drawPixmap((self.width() - scaled.width()) // 2, (self.height() - scaled.height()) // 2, scaled)
            p.fillRect(rect, QColor(255, 255, 255, 185) if is_light() else QColor(8, 21, 28, 175))
        elif color.lightnessF() > .4:
            p.fillRect(rect, QColor(255, 255, 255, 185) if is_light() else QColor(8, 21, 28, 175))
        else:
            p.fillRect(rect, QColor(255, 255, 255, 50) if is_light() else QColor(8, 21, 28, 50))
        if self.active:
            p.fillRect(rect, QColor(20, 139, 119, 25))
        p.restore()
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(theme_color(MINT if self.active or self.property("recentRecording") else "#263d48")), 1))
        p.drawRoundedRect(rect, 7, 7)
        if self._drop_field:
            p.setPen(QPen(QColor(theme_color(MINT)), 2))
            p.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 5, 5)
            target = QRectF(self.play_button.geometry()).adjusted(0, 0, -self.play_button.width() + 51, 0) if self._drop_field == "avatar" else rect
            p.fillRect(target, QColor(8, 21, 28, 185))
            p.setPen(QColor(theme_color("#dcfff6")))
            p.drawText(target, Qt.AlignmentFlag.AlignCenter, tr("头像\n裁剪" if self._drop_field == "avatar" else "松开以裁剪背景"))


class MiniWindow(QWidget):
    def __init__(self, main: "MainWindow"):
        flags = Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
        if main.controller.settings.get("mini_on_top", True):
            flags |= Qt.WindowType.WindowStaysOnTopHint
        super().__init__(None, flags)
        self.main = main
        self.setObjectName("MiniShell")
        set_style(self, APP_STYLE)
        self.setWindowTitle("New Life · 迷你")
        self.setWindowIcon(app_icon())
        self.setFixedSize(380, 48)
        self.setAcceptDrops(True)
        self._drag_offset = None
        self._drag_origin = None
        self._dragging = False
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(3)
        self.play_button = AvatarButton(compact=True)
        self.play_button.setFixedSize(120, 36)
        self.play_button.installEventFilter(self)
        self.play_button.clicked.connect(self.trigger_current)
        layout.addWidget(self.play_button)
        layout.addWidget(icon_button("|◀", "上一段", lambda: main.invoke(main.controller.previous), 28))
        layout.addWidget(icon_button("▶|", "下一段", lambda: main.invoke(main.controller.next), 28))
        self.directory_button = icon_button("☷", "当前文件夹的声音", self.show_directory, 28)
        self.directory_button.setToolTip("当前文件夹的声音；可拖入音频或视频添加")
        layout.addWidget(self.directory_button)
        self.mode_button = icon_button("↻", "播放模式", self.show_modes, 28)
        layout.addWidget(self.mode_button)
        self.volume_button = VolumeButton()
        self.volume_button.setFixedSize(28, 32)
        self.volume_button.volume_changed.connect(lambda value: main.invoke(main.controller.set_volume, "local", value))
        layout.addWidget(self.volume_button)
        layout.addWidget(icon_button("⤢", "展开主窗口", main.show_main, 28))
        layout.addWidget(icon_button("hide", "隐藏到托盘，保持运行", main.hide_to_tray, 28))
        exit_button = icon_button("×", "退出 New Life", main.quit_app, 28)
        exit_button.setObjectName("Exit")
        layout.addWidget(exit_button)
        self.refresh()

    def dragEnterEvent(self, event):
        self.main.dragEnterEvent(event)

    def dragMoveEvent(self, event):
        self.main.dragEnterEvent(event)

    def dragLeaveEvent(self, event):
        self.main.dragLeaveEvent(event)

    def dropEvent(self, event):
        point = event.position().toPoint()
        avatar = self.play_button.geometry().contains(point) and point.x() - self.play_button.x() < 40
        paths = self.main.dropped_paths(event)
        image = any(Path(path).suffix.lower() in IMAGE_EXTENSIONS for path in paths)
        target = self.current_item() if avatar else None
        if self.main.handle_drop(paths, target, avatar=image and avatar):
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
        else:
            event.ignore()

    def current_item(self):
        state = self.main.controller.state
        key = path_key(state["path"]) if state.get("path") else ""
        return next((item for item in self.main.controller.items if item.key == key), next(iter(self.main.controller.items), None))

    def trigger_current(self):
        item = self.current_item()
        if item:
            self.main.invoke(self.main.controller.trigger, item.path)

    def refresh(self):
        state = self.main.controller.state
        item = self.current_item()
        if item:
            selected = bool(state.get("path")) and path_key(state["path"]) == item.key
            self.play_button.set_item(item.name, item.avatar, self.main.controller.resolve_asset, selected and state.get("playing", False), selected and state.get("paused", False))
        else:
            self.play_button.set_item(tr("选择声音"), "🐮", self.main.controller.resolve_asset)
        self.play_button.setEnabled(item is not None and item.playable)
        if item:
            active = selected and (state.get("playing", False) or state.get("paused", False))
            action = "再点停止" if active else "点击播放"
            self.play_button.setToolTip(f"{item.name}\n{action}\n拖动头像或名称移动迷你条")
        else:
            self.play_button.setToolTip("选择声音\n拖动头像或名称移动迷你条")
        self.play_button.setAccessibleName(self.play_button.toolTip())
        self.volume_button.set_value(state.get("local_volume", 80))
        mode = min(3, max(0, int(state.get("mode", 0))))
        set_control_icon(self.mode_button, ("once", "repeat_one", "list", "repeat")[mode])
        self.mode_button.setToolTip("播放模式：" + PLAY_MODES[mode])
        directory = "目录：" + Path(self.main.controller.folder).name if self.main.controller.folder else "未选择文件夹"
        self.directory_button.setToolTip(directory + "\n当前文件夹的声音；可拖入音频或视频添加")
        i18n.retranslate(self)

    def show_directory(self):
        menu = QMenu(self)
        header = menu.addAction(Path(self.main.controller.folder).name or "当前文件夹")
        header.setProperty("i18n_skip", bool(self.main.controller.folder))
        header.setEnabled(False)
        menu.addSeparator()
        for item in self.main.controller.items:
            action = menu.addAction(item.name)
            action.setProperty("i18n_skip", True)
            action.setEnabled(item.playable)
            action.triggered.connect(lambda checked=False, path=item.path: self.main.invoke(self.main.controller.trigger, path))
        if not self.main.controller.items:
            menu.addAction("暂无声音，请展开导入").setEnabled(False)
        menu.exec(self.directory_button.mapToGlobal(QPoint(0, self.directory_button.height() + 5)))

    def show_modes(self):
        menu = QMenu(self)
        for index, name in enumerate(PLAY_MODES):
            action = menu.addAction(name)
            action.setCheckable(True)
            action.setChecked(index == self.main.controller.state.get("mode", 0))
            action.triggered.connect(lambda checked=False, value=index: self.main.invoke(self.main.controller.set_mode, value))
        menu.exec(self.mode_button.mapToGlobal(QPoint(0, self.mode_button.height() + 5)))

    def _begin_window_drag(self, event):
        self._drag_origin = event.globalPosition().toPoint()
        self._drag_offset = self._drag_origin - self.frameGeometry().topLeft()
        self._dragging = False

    def _move_window_drag(self, event):
        if self._drag_origin is None or not event.buttons() & Qt.MouseButton.LeftButton:
            return False
        point = event.globalPosition().toPoint()
        if not self._dragging and (point - self._drag_origin).manhattanLength() < QApplication.startDragDistance():
            return False
        self._dragging = True
        self.play_button.setDown(False)
        self.move(point - self._drag_offset)
        return True

    def _end_window_drag(self):
        self._drag_origin = None
        self._drag_offset = None
        self._dragging = False

    def eventFilter(self, watched, event):
        if watched is self.play_button:
            if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                self._begin_window_drag(event)
            elif event.type() == QEvent.Type.MouseMove and self._move_window_drag(event):
                event.accept()
                return True
            elif event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
                dragged = self._dragging
                self._end_window_drag()
                if dragged:
                    self.play_button.setDown(False)
                    self.play_button.releaseMouse()
                    event.accept()
                    return True
        return super().eventFilter(watched, event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._begin_window_drag(event)
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._move_window_drag(event):
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._end_window_drag()
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def hideEvent(self, event):
        self._end_window_drag()
        self.play_button.setDown(False)
        super().hideEvent(event)

    def closeEvent(self, event):
        event.ignore()
        self.main.quit_app()


class MainWindow(QMainWindow):
    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        i18n.install_live_translation()
        i18n.set_language(controller.settings.get("ui_language", "zh_CN"))
        self._quitting = False
        self._rendering = False
        self._requested_waveforms: set[tuple[str, int | None]] = set()
        self.rows: dict[str, ClipRow] = {}
        self._checked = set()
        self._selection_context = None
        self._selection_anchor = None
        self.setAcceptDrops(True)
        self._settings_dialog = None
        self._settings_hidden = False
        self._hotkey_dialog_active = False
        self._detail_dialogs = {}
        self._recent_recording_revision = 0
        self._import_result = None
        self._import_result_dialog = None
        self._device_combos: dict[str, QComboBox] = {}
        self._devices_signature = None
        self._raw_message = "就绪 · 点击头像或名称播放，再点停止"
        self._message_error = False
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.setWindowTitle("New Life")
        self.setWindowIcon(app_icon())
        set_style(self, APP_STYLE)
        self.resize(960, 650)
        self.setMinimumSize(780, 500)
        if QApplication.platformName() != "offscreen":
            available = self.screen().availableGeometry()
            self.resize(max(780, min(960, available.width())), max(500, min(650, available.height())))
        self._build()
        self.mini = MiniWindow(self)
        self._mini_on_top = bool(controller.settings.get("mini_on_top", True))
        self._theme_watch = QTimer(self)
        self._theme_watch.setInterval(1000)
        self._theme_watch.timeout.connect(lambda: set_theme(self.controller.settings.get("theme", "system")))
        self._theme_watch.start()
        self._tray()
        controller.items_changed.connect(self.refresh_items)
        controller.state_changed.connect(self.refresh_state)
        controller.message.connect(self.show_message)
        controller.waveform_ready.connect(self.waveform_ready)
        controller.request_expand.connect(self.show_main)
        if hasattr(controller, "import_finished"):
            controller.import_finished.connect(self.show_import_result)
        self.refresh_items()
        self.refresh_state()

    def _build(self):
        shell = QWidget()
        shell.setObjectName("MainShell")
        self.setCentralWidget(shell)
        outer = QVBoxLayout(shell)
        outer.setContentsMargins(12, 10, 12, 8)
        outer.setSpacing(6)
        self._main_layout = outer
        title = QHBoxLayout()
        title.setSpacing(6)
        self.title_layout = title
        brand_icon = QLabel()
        brand_icon.setPixmap(app_icon().pixmap(26, 26))
        title.addWidget(brand_icon)
        self.brand = WindowDragLabel("New Life", self)
        self.brand.setObjectName("Brand")
        title.addWidget(self.brand, 1)
        self._build_common_settings(title)
        self.language_button = QPushButton("EN")
        self.language_button.setProperty("i18n_skip", True)
        self.language_button.setToolTip("Language / 语言")
        self.language_button.clicked.connect(self.language_menu)
        self.call_help_button = QPushButton("通话帮助")
        self.call_help_button.setObjectName("Toolbar")
        self.call_help_button.setCheckable(True)
        self.call_help_button.setToolTip("通话安装、输入选择与测试；再点收起")
        self.call_help_button.clicked.connect(lambda: self.toggle_settings(1))
        title.addWidget(self.call_help_button)
        self.hotkeys_button = QPushButton("快捷键")
        self.hotkeys_button.setObjectName("Toolbar")
        self.hotkeys_button.setCheckable(True)
        self.hotkeys_button.setToolTip("设置后台快捷键；再点收起，未保存输入会保留")
        self.hotkeys_button.clicked.connect(lambda: self.toggle_settings(2))
        title.addWidget(self.hotkeys_button)
        title.addWidget(self.language_button)
        self.mini_button = icon_button("mini", "收起为单行迷你条", width=40)
        self.mini_button.clicked.connect(self.show_mini)
        title.addWidget(self.mini_button)
        self.minimize_button = icon_button("−", "最小化", self.showMinimized)
        self.hide_button = icon_button("hide", "隐藏到托盘，保持运行", self.hide_to_tray)
        self.exit_button = icon_button("×", "退出 New Life", self.quit_app)
        for button in (self.minimize_button, self.hide_button, self.exit_button):
            title.addWidget(button)
        self._long_toolbar = (self.theme_choice, self.always_on_top, self.call_help_button, self.hotkeys_button)
        self._short_toolbar = (self.language_button, self.mini_button, self.minimize_button, self.hide_button, self.exit_button)
        for button in self._short_toolbar:
            button.setObjectName("Toolbar")
        outer.addLayout(title)
        self.title_separator = line()
        self.title_separator.hide()
        # The helper is an independent window. The hidden slot is retained for old callers.
        self._settings_slot = QWidget()
        self._settings_slot.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self._settings_layout = QVBoxLayout(self._settings_slot)
        self._settings_layout.setContentsMargins(0, 0, 0, 0)
        self._settings_layout.setSpacing(0)
        self._settings_slot.hide()
        top = QHBoxLayout()
        top.setSpacing(6)
        self.folder_toolbar = top
        self.folder_button = QPushButton("文件夹")
        self.folder_button.setIcon(control_icon("folder", "#e9ce57"))
        self.folder_button.setProperty("themeIcon", "folder")
        self.folder_button.setProperty("themeIconColor", "#e9ce57")
        self.folder_button.clicked.connect(self.choose_folder)
        top.addWidget(self.folder_button, 2)
        self.folder_path = QPushButton("未选择文件夹")
        self.folder_path.setProperty("i18n_skip", True)
        self.folder_path.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.folder_path.setToolTip("点击打开当前文件夹")
        self.folder_path.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(self.controller.folder)) if self.controller.folder else None)
        top.addWidget(self.folder_path, 2)
        for key, label in (("mic_device", "麦克风"), ("local_device", "耳机／扬声器")):
            combo = QComboBox()
            combo.setObjectName(key)
            combo.setAccessibleName(label)
            combo.setProperty("i18n_device_names", True)
            combo.setMinimumWidth(0)
            combo.setMinimumContentsLength(0)
            combo.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            combo.activated.connect(lambda index, name=key, field=combo: self.invoke(self.controller.change_system_device, name, field.itemData(index)))
            self._device_combos[key] = combo
            top.addWidget(combo, 4)
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索音频")
        self.search.addAction(control_icon("search", "#99adb8"), QLineEdit.ActionPosition.LeadingPosition)
        self.search.textChanged.connect(self.refresh_items)
        self.search.setMinimumWidth(0)
        self.search.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        top.addWidget(self.search, 4)
        self.import_button = QPushButton("导入")
        self.import_button.setCheckable(True)
        self.import_button.setIcon(control_icon("import"))
        self.import_button.setProperty("themeIcon", "import")
        self.import_button.setToolTip("复制音频或视频到当前文件夹；有导入结果时再点收起")
        self.import_button.clicked.connect(self.import_files)
        top.addWidget(self.import_button, 2)
        self.delete_selected_button = icon_button("delete", "删除选中的音频，可撤销", self.delete_selected)
        self.delete_selected_button.setObjectName("Toolbar")
        top.addWidget(self.delete_selected_button, 1)
        self._folder_controls = (self.folder_button, self.folder_path, *self._device_combos.values(), self.search, self.import_button, self.delete_selected_button)
        for control in self._folder_controls:
            control.setFixedHeight(34)
        outer.addLayout(top)

        self.header = QFrame()
        self.header.setObjectName("TableHeader")
        header = QHBoxLayout(self.header)
        header.setContentsMargins(7, 5, 9, 5)
        header.setSpacing(6)
        self.header_labels = {}
        number = QLabel("序号")
        number.setAlignment(Qt.AlignmentFlag.AlignCenter)
        number.setFixedWidth(24)
        header.addWidget(number)
        self.header_labels["number"] = number
        self.select_all = HitCheckBox()
        self.select_all.setObjectName("SelectAudio")
        self.select_all.setFixedSize(22, 36)
        self.select_all.setTristate(True)
        self.select_all.setToolTip("全选当前列表")
        self.select_all.clicked.connect(self.check_all)
        header.addWidget(self.select_all)
        for key, text, width in (("play", "播放列表", self.play_column_width()), ("wave", "播放范围", 0), ("duration", "时长", 58), ("hotkey", "快捷键", 80)):
            label = QLabel(text)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.header_labels[key] = label
            if width:
                label.setFixedWidth(width)
                header.addWidget(label)
            else:
                header.addWidget(label, 1)
        outer.addWidget(self.header)
        self.list = MediaList(self)
        self.list.setSpacing(0)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.list.setDropIndicatorShown(True)
        self.list.model().rowsMoved.connect(self.rows_moved)
        self.list.verticalScrollBar().rangeChanged.connect(lambda *_: self._fit_table())
        self.empty = QLabel("还没有声音\n选择文件夹，或拖入音频／视频（只播放声音）")
        self.empty.setObjectName("Muted")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.list_stack = QStackedWidget()
        self.list_stack.addWidget(self.list)
        self.list_stack.addWidget(self.empty)
        outer.addWidget(self.list_stack, 1)
        self.selection_bar = QWidget()
        selection_layout = QHBoxLayout(self.selection_bar)
        selection_layout.setContentsMargins(0, 0, 0, 0)
        self.selection_count = muted("")
        selection_layout.addWidget(self.selection_count)
        selection_layout.addStretch()
        self.undo_button = QPushButton("撤销删除")
        self.undo_button.clicked.connect(lambda: self.invoke(self.controller.undo_delete))
        selection_layout.addWidget(self.undo_button)
        clear_selection = QPushButton("取消选择")
        clear_selection.clicked.connect(lambda: self.check_all(False))
        selection_layout.addWidget(clear_selection)
        self.selection_bar.hide()
        # Selection feedback uses the existing bottom status area instead of another row.
        self.header.setToolTip("波形区域不触发播放 · 最短 1 秒 · 右键查看更多操作")
        self.record_bar = QFrame()
        record_outer = QHBoxLayout(self.record_bar)
        record_outer.setContentsMargins(0, 0, 0, 0)
        record_outer.setSpacing(14)
        self.record_left = QFrame()
        self.record_left.setObjectName("RecordBar")
        record = QHBoxLayout(self.record_left)
        record.setContentsMargins(8, 3, 8, 3)
        record.setSpacing(8)
        record.addWidget(QLabel("录音"))
        self.record_group = QButtonGroup(self)
        self.record_radios = []
        for index, source in enumerate(RECORD_SOURCES):
            radio = QRadioButton(source)
            radio.setChecked(source == self.controller.settings.get("recording_source", "电脑"))
            self.record_group.addButton(radio, index)
            self.record_radios.append(radio)
            record.addWidget(radio)
        self.record_group.idClicked.connect(lambda index: self.invoke(self.controller.save_settings, {"recording_source": RECORD_SOURCES[index]}))
        record.addStretch(1)
        self.record_time = QLabel("00:00")
        self.record_time.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.record_time.setToolTip("保存到当前文件夹")
        record.addWidget(self.record_time, 1)
        self.mark_button = QPushButton("标记起点")
        self.mark_button.setToolTip("录音中点两次标记起点与终点，生成可立即播放的片段")
        self.mark_button.clicked.connect(lambda: self.invoke(self.controller.mark_recording))
        record.addWidget(self.mark_button)
        self.record_button = QPushButton("开始录音")
        self.record_button.setIcon(control_icon("mic", "#ff797f"))
        self.record_button.setProperty("themeIcon", "mic")
        self.record_button.setProperty("themeIconColor", "#ff797f")
        self.record_button.setObjectName("Record")
        self.record_button.clicked.connect(self.toggle_recording)
        self.record_button.setFixedSize(176, 40)
        record_outer.addWidget(self.record_left, 1)
        record_outer.addWidget(self.record_button)
        self.save_hint = muted("保存到当前文件夹", True)
        self.save_hint.hide()
        outer.addWidget(self.record_bar)
        self.transport_separator = line()
        self.transport_separator.hide()
        transport_outer = QHBoxLayout()
        transport_outer.setSpacing(14)
        self.transport_left = QFrame()
        self.transport_left.setObjectName("RecordBar")
        transport = QHBoxLayout(self.transport_left)
        transport.setContentsMargins(6, 3, 6, 3)
        transport.setSpacing(5)
        transport.addWidget(icon_button("|◀", "上一段", lambda: self.invoke(self.controller.previous), 24))
        self.play_pause = icon_button("▶", "播放／暂停", self.pause_or_play, 26)
        transport.addWidget(self.play_pause)
        transport.addWidget(icon_button("▶|", "下一段", lambda: self.invoke(self.controller.next), 24))
        transport.addWidget(icon_button("■", "立即停止片段，麦克风通话继续", lambda: self.invoke(self.controller.stop), 24))
        self.mode = QComboBox()
        self.mode.addItems(PLAY_MODES)
        set_style(self.mode, "QComboBox { padding: 5px 9px 5px 5px; font-size: 11px; } QComboBox::drop-down { width: 12px; }")
        self.mode.setMinimumWidth(0)
        self.mode.setFixedWidth(105)
        self.mode.currentIndexChanged.connect(lambda index: self.invoke(self.controller.set_mode, index))
        transport.addWidget(self.mode)
        self.local_volume = VolumeSlider()
        self.local_volume.setFixedWidth(70)
        self.local_volume.valueChanged.connect(lambda value: self.invoke(self.controller.set_volume, "local", value))
        transport.addWidget(self.local_volume)
        self.output_label = muted("未连接通话", True)
        transport.addWidget(self.output_label)
        self.output_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.status = muted("就绪 · 点击头像或名称播放，再点停止", True)
        self.status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        transport.addWidget(self.status, 1)
        self.undo_button.setParent(self.transport_left)
        self.undo_button.setFixedWidth(75)
        self.undo_button.hide()
        transport.addWidget(self.undo_button)
        self.signature = QLabel("Bilibili那年松江")
        self.signature.setObjectName("Signature")
        self.signature.setProperty("i18n_skip", True)
        transport.addWidget(self.signature)
        self.call_button = QPushButton("分享给通话对方")
        self.call_button.setCheckable(True)
        self.call_button.setFixedSize(176, 40)
        self.call_button.clicked.connect(self.share_to_call)
        transport_outer.addWidget(self.transport_left, 1)
        transport_outer.addWidget(self.call_button)
        outer.addLayout(transport_outer)
        self.version_label = muted("v" + __version__, True)
        self.version_label.hide()
        self._size_toolbar()

    def _build_common_settings(self, outer):
        row = QHBoxLayout()
        row.setSpacing(6)
        self.theme_choice = QComboBox()
        self.theme_choice.setObjectName("theme_choice")
        set_style(self.theme_choice, "QComboBox { padding: 5px 9px 5px 5px; font-size: 11px; } QComboBox::drop-down { width: 12px; }")
        for title, value in (("系统", "system"), ("深色", "dark"), ("浅色", "light")):
            self.theme_choice.addItem(title, value)
        self.theme_choice.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.theme_choice.setMinimumWidth(0)
        self.theme_choice.setToolTip("界面配色：系统、深色或浅色，立即生效")
        self.theme_choice.activated.connect(lambda _: self.invoke(self.controller.set_theme, self.theme_choice.currentData()))
        outer.addWidget(self.theme_choice)
        self.always_on_top = QPushButton("置顶")
        self.always_on_top.setCheckable(True)
        self.always_on_top.setObjectName("Toolbar")
        self.always_on_top.setToolTip("迷你条保持在其他窗口上方")
        self.always_on_top.clicked.connect(lambda checked: self.invoke(self.controller.set_mini_on_top, checked))
        outer.addWidget(self.always_on_top)

    def _sync_common_settings(self):
        devices = self.controller.available_devices()
        signature = tuple((device.id, device.name, device.kind, device.is_virtual) for device in devices)
        if signature != self._devices_signature:
            self._devices_signature = signature
            for key, combo in self._device_combos.items():
                blocked = combo.blockSignals(True)
                combo.clear()
                combo.addItem(tr("麦克风" if key == "mic_device" else "耳机"), "")
                combo.setItemIcon(0, control_icon("mic" if key == "mic_device" else "headphones"))
                kind = "input" if key == "mic_device" else "output"
                for device in devices:
                    if device.kind == kind and not device.is_virtual:
                        combo.addItem(device.name, device.id)
                        combo.setItemIcon(combo.count() - 1, control_icon("mic" if key == "mic_device" else "headphones"))
                combo.setIconSize(QSize(14, 14))
                combo.blockSignals(blocked)
        for key, combo in self._device_combos.items():
            saved = self.controller.settings.get(key, "")
            blocked = combo.blockSignals(True)
            if saved and combo.findData(saved) < 0:
                combo.addItem(tr("已保存的设备当前不可用"), saved)
            kind = "input" if key == "mic_device" else "output"
            available_ids = {device.id for device in devices if device.kind == kind and not device.is_virtual}
            for index in range(combo.count()):
                if not combo.itemData(index):
                    combo.setItemText(index, tr("麦克风" if key == "mic_device" else "耳机"))
                elif combo.itemData(index) not in available_ids:
                    combo.setItemText(index, tr("已保存的设备当前不可用"))
            combo.setCurrentIndex(max(0, combo.findData(saved)))
            title = tr("麦克风：你本人讲话的真实设备" if key == "mic_device" else "耳机／扬声器：你听声音的设备")
            combo.setToolTip(title + "\n" + combo.currentText() + "\n" + tr("自动跟随系统；更换会影响其他软件，退出后保留。"))
            combo.blockSignals(blocked)
        blocked = self.theme_choice.blockSignals(True)
        self.theme_choice.setCurrentIndex(max(0, self.theme_choice.findData(self.controller.settings.get("theme", "system"))))
        self.theme_choice.blockSignals(blocked)
        blocked = self.always_on_top.blockSignals(True)
        on_top = bool(self.controller.settings.get("mini_on_top", True))
        self.always_on_top.setChecked(on_top)
        self.always_on_top.blockSignals(blocked)
        if hasattr(self, "mini") and self._mini_on_top != on_top:
            self._mini_on_top = on_top
            was_visible, position = self.mini.isVisible(), self.mini.pos()
            self.mini.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, on_top)
            self.mini.move(position)
            if was_visible:
                self.mini.show()

    def _sync_settings_entries(self):
        panel = self._settings_dialog
        visible = bool(panel is not None and panel.isVisible())
        for button, section in ((self.call_help_button, "call"), (self.hotkeys_button, "hotkeys")):
            blocked = button.blockSignals(True)
            button.setChecked(visible and panel.section == section)
            button.blockSignals(blocked)

    def _fit_settings_panel(self):
        # Helper windows never consume the playlist's layout space.
        self._settings_slot.hide()

    def play_column_width(self):
        return 120 if self.width() < 870 else 150

    def _size_toolbar(self):
        if not hasattr(self, "_long_toolbar"):
            return
        unit = max(72, int((self.width() - 24 - 36) / 9.5))
        short = unit // 2
        for widget in self._long_toolbar:
            widget.setFixedSize(short * 2, 34)
        for widget in self._short_toolbar:
            widget.setFixedSize(short, 34)
        if hasattr(self, "_folder_controls"):
            for widget, ratio in zip(self._folder_controls, (2, 2, 4, 4, 4, 2, 1)):
                widget.setMinimumWidth(0)
                widget.setFixedWidth(int((self.width() - 24 - 36) * ratio / 19))

    def _fit_table(self):
        if not hasattr(self, "header_labels"):
            return
        width = self.play_column_width()
        self.header_labels["play"].setFixedWidth(width)
        # QListWidget's scroll bar changes viewport width; share that exact allowance.
        reserve = max(0, self.list.width() - self.list.viewport().width())
        self.header.layout().setContentsMargins(7, 5, 9 + reserve, 5)
        for row in self.rows.values():
            row.play_button.setFixedWidth(width)

    def _place_helper(self, dialog):
        screen = self.screen() or QApplication.primaryScreen()
        available = screen.availableGeometry()
        top = max(available.top(), self.y() + 100)
        decoration_height = max(0, dialog.frameGeometry().height() - dialog.height())
        decoration_width = max(0, dialog.frameGeometry().width() - dialog.width())
        remaining = available.bottom() - top + 1 - decoration_height
        if remaining >= dialog.minimumHeight():
            dialog.resize(min(dialog.width(), available.width() - decoration_width), min(dialog.height(), remaining))
        frame_width = dialog.width() + decoration_width
        frame_height = dialog.height() + decoration_height
        right = self.frameGeometry().right() + 8
        x = right if right + frame_width <= available.right() else min(self.x() + self.width() - frame_width - 12, available.right() - frame_width + 1)
        x = max(available.left(), x)
        # Place below both toolbar rows so their toggles always remain reachable.
        y = max(available.top(), min(top, available.bottom() - frame_height + 1))
        dialog.move(x, y)

    def _hide_other_helpers(self, keep=None):
        if self._settings_dialog and self._settings_dialog is not keep:
            self._settings_dialog.hide()
            self._settings_hidden = False
            self._sync_settings_entries()
        for dialog in self._detail_dialogs.values():
            if dialog is not keep:
                dialog.hide()
        if self._import_result_dialog and self._import_result_dialog is not keep:
            self._import_result_dialog.hide()
        self._sync_detail_entries()

    def _sync_detail_entries(self):
        self.import_button.setChecked(bool(self._import_result_dialog and self._import_result_dialog.isVisible()))
        for key, row in self.rows.items():
            dialog = self._detail_dialogs.get(("hotkey", key))
            row.hotkey.setChecked(bool(dialog and dialog.isVisible()))

    def invoke(self, function, *args, **kwargs):
        try:
            return function(*args, **kwargs)
        except Exception as exc:
            self.show_message(str(exc), True)
            return False

    @staticmethod
    def dropped_paths(event):
        return [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]

    def dragEnterEvent(self, event):
        paths = self.dropped_paths(event)
        if paths:
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
            self.status.setText("松开以复制到当前播放文件夹" if not any(Path(path).suffix.lower() in IMAGE_EXTENSIONS for path in paths) else "将图片拖到头像或片段背景，可调整显示区域")
            i18n.retranslate(self.status)
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        self.dragEnterEvent(event)

    def dragLeaveEvent(self, event):
        self.show_message(self._raw_message, self._message_error)

    def dropEvent(self, event):
        if self.handle_drop(self.dropped_paths(event)):
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
        else:
            event.ignore()

    def handle_drop(self, paths, item=None, avatar=False):
        images = [path for path in paths if Path(path).suffix.lower() in IMAGE_EXTENSIONS]
        if images:
            if len(paths) != 1 or item is None:
                self.show_message("请将一张图片拖到目标头像或片段行", True)
                return False
            elif avatar:
                self.crop_avatar(item, images[0])
            else:
                self.crop_background(item, images[0])
            return True
        if paths:
            self.invoke(self.controller.import_files, paths)
            return True
        return False

    def request_row_waveform(self, item):
        stream = getattr(item, "audio_stream", None)
        identity = (item.key, stream)
        if item.playable and not item.peaks and identity not in self._requested_waveforms:
            self._requested_waveforms.add(identity)
            self.invoke(self.controller.request_waveform, item.path)

    def check_item(self, key, checked):
        keys = list(self.rows)
        if QApplication.keyboardModifiers() & Qt.KeyboardModifier.ShiftModifier and self._selection_anchor in keys:
            first, last = sorted((keys.index(self._selection_anchor), keys.index(key)))
            affected = keys[first:last + 1]
        else:
            affected = [key]
        for value in affected:
            self._checked.add(value) if checked else self._checked.discard(value)
        self._selection_anchor = key
        self.update_selection()

    def check_all(self, checked):
        self._checked = set(self.rows) if checked else set()
        self.update_selection()

    def update_selection(self):
        for key, row in self.rows.items():
            row.check.setChecked(key in self._checked)
        count = len(self._checked)
        self.select_all.blockSignals(True)
        self.select_all.setCheckState(Qt.CheckState.Checked if count and count == len(self.rows) else Qt.CheckState.PartiallyChecked if count else Qt.CheckState.Unchecked)
        self.select_all.blockSignals(False)
        self.selection_count.setText(tr("已选 {count} 项", count=count))
        self.selection_bar.hide()
        busy = bool(self.controller.state.get("delete_busy"))
        self.delete_selected_button.setEnabled(bool(count) and not busy)
        self.delete_selected_button.setToolTip(tr("删除选中的 {count} 个音频，可撤销", count=count) if count else tr("先勾选音频，再批量删除"))
        if count:
            self.status.setText(tr("已选 {count} 项", count=count))
            self.status.setToolTip(tr("按 Esc 取消选择；删除后可撤销"))
        elif not self.controller.state.get("play_loading") and not self.controller.state.get("play_buffering"):
            self.status.setText(("!  " if self._message_error else "") + tr(self._raw_message))
            self.status.setToolTip(tr(self._raw_message))
        undo = bool(self.controller.state.get("delete_undo_available"))
        self.undo_button.setVisible(undo)
        self.undo_button.setEnabled(undo and not busy)
        self.undo_button.setToolTip(tr("撤销最近删除的 {count} 项", count=self.controller.state.get("delete_undo_count", 0)))

    def delete_selected(self):
        items = [row.item for key, row in self.rows.items() if key in self._checked]
        if not items:
            return
        if not self.controller.state.get("delete_busy"):
            self.invoke(self.controller.delete_items, [item.path for item in items])

    def refresh_items(self, *args):
        self._rendering = True
        try:
            self.list.clear()
            self.rows.clear()
            query = self.search.text().strip().casefold()
            context = (self.controller.folder, query)
            if context != self._selection_context:
                self._checked.clear()
                self._selection_anchor = None
                self._selection_context = context
            items = [item for item in self.controller.items if query in item.name.casefold()]
            self._checked.intersection_update(item.key for item in items)
            for index, item in enumerate(items):
                entry = QListWidgetItem()
                entry.setData(Qt.ItemDataRole.UserRole, item.path)
                entry.setSizeHint(QSize(0, 92))
                self.list.addItem(entry)
                row = ClipRow(item, index, self)
                self.list.setItemWidget(entry, row)
                self.rows[item.key] = row
            self.list_stack.setCurrentIndex(0 if items else 1)
            self.empty.setText("没有匹配的声音" if query else "还没有声音\n选择文件夹，或拖入音频／视频（只播放声音）")
            self.folder_path.setText(Path(self.controller.folder).name or "当前文件夹")
            self.folder_path.setToolTip(tr("点击打开当前文件夹"))
            self.save_hint.setText("保存到当前文件夹：" + (Path(self.controller.folder).name or "当前文件夹") if self.controller.folder else "请先选择文件夹")
            self.save_hint.setToolTip(self.controller.folder)
        finally:
            self._rendering = False
        if hasattr(self, "mini"):
            set_theme(self.controller.settings.get("theme", "system"))
            self.mini.refresh()
        i18n.retranslate(self)
        self.update_selection()
        self._fit_table()
        QTimer.singleShot(0, self._fit_table)
        self._highlight_recording()
        self._sync_detail_entries()

    def _highlight_recording(self):
        revision = int(self.controller.state.get("recent_recording_revision", 0))
        path = self.controller.state.get("recent_recording_path", "")
        if revision <= self._recent_recording_revision or not path:
            return
        key = path_key(path)
        if key not in self.rows:
            return
        self._recent_recording_revision = revision
        row = self.rows[key]
        row.setProperty("recentRecording", True)
        row.update()
        for index in range(self.list.count()):
            entry = self.list.item(index)
            if path_key(entry.data(Qt.ItemDataRole.UserRole)) == key:
                self.list.setCurrentItem(entry)
                self.list.scrollToItem(entry, QAbstractItemView.ScrollHint.PositionAtCenter)
                break
        QTimer.singleShot(4500, lambda: self._clear_recording_highlight(key, revision))

    def _clear_recording_highlight(self, key, revision):
        if revision != self._recent_recording_revision:
            return
        row = self.rows.get(key)
        if row:
            row.setProperty("recentRecording", False)
            row.update()

    def refresh_state(self):
        state = self.controller.state
        code = state.get("ui_language", self.controller.settings.get("ui_language", "zh_CN"))
        if code != i18n.language():
            i18n.set_language(code)
        self._sync_common_settings()
        self.language_button.setText("中文" if i18n.language() == "en" else "EN")
        self.folder_path.setToolTip(tr("点击打开当前文件夹"))
        for row in self.rows.values():
            row.set_state(state)
        recording = bool(state.get("recording"))
        self.mini_button.setEnabled(not recording)
        self.mini_button.setToolTip("停止录音后才可收起为迷你条" if recording else "收起为单行迷你条")
        self.folder_button.setEnabled(not recording)
        self.folder_button.setToolTip("录音期间不能切换文件夹" if recording else "文件夹就是播放列表")
        for radio in self.record_radios:
            radio.setEnabled(not recording)
        self.mark_button.setEnabled(recording)
        self.mark_button.setText("标记终点" if state.get("mark_pending") else "标记起点")
        self.record_button.setText("结束并保存" if recording else "开始录音")
        self.record_button.setIcon(control_icon("stop" if recording else "mic", "#ff797f"))
        source = self.controller.settings.get("recording_source", "电脑")
        record_text = tr("● 正在录制{record_source} · {time}", record_source=tr(source), time=time_text(state.get("record_seconds", 0))) if recording else time_text(state.get("record_seconds", 0))
        self.record_time.setText(record_text)
        self.record_time.setToolTip(record_text + "\n" + tr("保存到当前文件夹"))
        set_style(self.record_time, "color: #ff797f;" if recording else "color: #99adb8;")
        set_control_icon(self.play_pause, "pause" if state.get("playing") and not state.get("paused") else "play")
        self.mode.blockSignals(True)
        self.mode.setCurrentIndex(max(0, min(3, int(state.get("mode", 0)))))
        self.mode.blockSignals(False)
        self.local_volume.blockSignals(True)
        self.local_volume.setValue(int(state.get("local_volume", 80)))
        self.local_volume.blockSignals(False)
        self.local_volume.setToolTip(f"本地音量 {self.local_volume.value()}% · 滚轮调节")
        connected, ready = state.get("call_connected", False), state.get("call_ready", False)
        connecting = state.get("call_connecting", False)
        self.call_button.blockSignals(True)
        sharing = bool((connected or connecting) and state.get("send_to_call"))
        self.call_button.setChecked(sharing)
        self.call_button.setText("正在连接…" if connecting else "正在分享 · 点击关闭" if sharing else "分享给通话对方")
        self.call_button.blockSignals(False)
        self.call_button.setToolTip("开启后连接麦克风并分享片段；关闭仅停止分享片段，本人讲话继续。")
        self.output_label.setText("正在连接通话…" if connecting else "麦克风＋片段" if connected and state.get("send_to_call") else "仅麦克风" if connected else "未连接通话")
        self.output_label.setToolTip("片段分享已关闭，麦克风仍保持通话" if connected and not state.get("send_to_call") else "发送电平只能证明音频已送入虚拟设备，请在通话软件中确认输入并让对方试听")
        name = "SharingActive" if sharing else "SharingOff"
        if self.call_button.objectName() != name:
            self.call_button.setObjectName(name)
            self.call_button.style().unpolish(self.call_button)
            self.call_button.style().polish(self.call_button)
        set_theme(self.controller.settings.get("theme", "system"))
        self.mini.refresh()
        if state.get("play_loading") or state.get("play_buffering"):
            self.status.setText("正在缓冲音频…")
        else:
            self.status.setText(("!  " if self._message_error else "") + self._raw_message)
        i18n.retranslate(self)
        self.update_selection()
        self._highlight_recording()
        if self._settings_dialog:
            self._settings_dialog.update_state()
        if self._import_result is not None and getattr(self, "_import_result_language", None) != i18n.language():
            self._render_import_result()

    def waveform_ready(self, path: str, peaks: list):
        row = self.rows.get(path_key(path))
        if row:
            row.set_peaks(peaks)

    def show_message(self, text: str, error: bool = False):
        self._raw_message, self._message_error = text, error
        self.status.setText(("!  " if error else "") + text)
        self.status.setToolTip(text)
        set_style(self.status, "color: #ffc09b;" if error else "color: #99adb8;")
        i18n.retranslate(self.status)

    def choose_folder(self):
        if self.controller.state.get("recording"):
            return
        path = QFileDialog.getExistingDirectory(self, tr("选择播放与录音文件夹"), self.controller.folder)
        if path:
            self._requested_waveforms.clear()
            self.invoke(self.controller.set_folder, path)

    def import_files(self):
        if self._import_result_dialog is not None:
            if self._import_result_dialog.isVisible():
                self._import_result_dialog.hide()
            else:
                self._hide_other_helpers(self._import_result_dialog)
                self._place_helper(self._import_result_dialog)
                self._import_result_dialog.show()
                self._place_helper(self._import_result_dialog)
                self._import_result_dialog.raise_()
            return
        self._choose_import_files()
        self._sync_detail_entries()

    def _choose_import_files(self):
        formats = " ".join("*" + extension for extension in sorted(SUPPORTED_EXTENSIONS))
        files, _ = QFileDialog.getOpenFileNames(self, tr("导入音频或视频到当前文件夹"), self.controller.folder, tr("媒体文件") + f" ({formats})")
        if files:
            self.invoke(self.controller.import_files, files)

    def show_import_result(self, result):
        self._import_result = result
        if self._import_result_dialog is None:
            dialog = ClipEditorDialog(self)
            dialog.setWindowTitle("导入结果")
            dialog.setModal(False)
            dialog.resize(500, 360)
            outer = QVBoxLayout(dialog)
            self.import_summary = QLabel()
            self.import_summary.setWordWrap(True)
            outer.addWidget(self.import_summary)
            self.import_details = QListWidget()
            outer.addWidget(self.import_details, 1)
            footer = QHBoxLayout()
            footer.addWidget(muted("再次点击导入按钮可收起此面板", True))
            footer.addStretch()
            again = QPushButton("导入新文件")
            again.clicked.connect(self._choose_import_files)
            footer.addWidget(again)
            outer.addLayout(footer)
            set_style(dialog, APP_STYLE)
            dialog.visibility_changed.connect(self._sync_detail_entries)
            self._import_result_dialog = dialog
        self._render_import_result()
        self._hide_other_helpers(self._import_result_dialog)
        self._place_helper(self._import_result_dialog)
        self._import_result_dialog.show()
        self._place_helper(self._import_result_dialog)
        self._import_result_dialog.raise_()

    def _render_import_result(self):
        result = self._import_result
        if result is None or self._import_result_dialog is None:
            return
        self._import_result_language = i18n.language()
        imported, skipped, failed = (result.get(key, []) for key in ("imported", "skipped", "failed"))
        self.import_summary.setText(tr("成功添加 {added} 个，跳过 {skipped} 个，失败 {failed} 个", added=len(imported), skipped=len(skipped), failed=len(failed)))
        self.import_details.clear()
        for path in imported:
            entry = QListWidgetItem(tr("已添加：{name}", name=Path(path).name))
            entry.setToolTip(path)
            self.import_details.addItem(entry)
        for path in skipped:
            entry = QListWidgetItem(tr("已跳过：{name}", name=Path(path).name))
            entry.setToolTip(path)
            self.import_details.addItem(entry)
        for value in failed:
            path, reason = value.get("path", ""), value.get("error", "")
            entry = QListWidgetItem(tr("添加失败：{name} · {reason}", name=Path(path).name, reason=tr(reason)))
            entry.setToolTip(path + "\n" + tr(reason))
            self.import_details.addItem(entry)
        i18n.retranslate(self._import_result_dialog)

    def toggle_recording(self):
        if self.controller.state.get("recording"):
            self.invoke(self.controller.stop_recording)
        else:
            index = self.record_group.checkedId()
            self.invoke(self.controller.start_recording, RECORD_SOURCES[max(0, index)])

    def pause_or_play(self):
        if self.controller.state.get("playing") or self.controller.state.get("paused"):
            self.invoke(self.controller.pause)
        else:
            path = self.controller.state.get("path")
            item = next((item for item in self.controller.items if item.path == path), next((item for item in self.controller.items if item.playable), None))
            if item:
                self.invoke(self.controller.trigger, item.path)

    def toggle_call(self):
        if self.controller.state.get("call_connected"):
            self.invoke(self.controller.disconnect_call)
        elif not self.controller.state.get("call_ready"):
            self.open_settings(tab=1)
        else:
            self.invoke(self.controller.connect_call)

    def share_to_call(self, enabled):
        result = self.invoke(self.controller.set_share, enabled)
        self.refresh_state()
        if enabled and result is False and not self.controller.state.get("call_ready"):
            self.open_settings(tab=1)

    def show_mini(self):
        if self.controller.state.get("recording"):
            self.show_message("停止录音后才可收起为迷你条", True)
            return
        self.close_settings()
        self._hide_other_helpers()
        set_theme(self.controller.settings.get("theme", "system"))
        self.mini.refresh()
        if not self.mini.isVisible():
            self.mini.move(self.frameGeometry().topLeft())
        self.mini.show()
        self.mini.raise_()
        self.hide()

    def show_main(self):
        self.mini.hide()
        self.showNormal()
        self.raise_()
        self.activateWindow()
        if self._settings_dialog is not None and self._settings_hidden:
            self._settings_hidden = False
            self._settings_dialog.show()
            self._place_helper(self._settings_dialog)
            self._fit_settings_panel()
            self._sync_settings_entries()

    def hide_to_tray(self):
        if self._settings_dialog is not None:
            self._settings_dialog._set_capture(False)
            self._settings_hidden = self._settings_dialog.isVisible()
            self._settings_dialog.hide()
            self._settings_slot.hide()
            self._sync_settings_entries()
        self.hide()
        self.mini.hide()
        for dialog in self._detail_dialogs.values():
            dialog.hide()
        if self._import_result_dialog:
            self._import_result_dialog.hide()
        if self.tray.isVisible():
            self.tray.showMessage(tr("New Life 仍在运行"), tr("录音、通话与快捷键继续。右键托盘图标可退出。"), QSystemTrayIcon.MessageIcon.Information, 1600)

    def language_menu(self):
        self.invoke(self.controller.set_language, "zh_CN" if i18n.language() == "en" else "en")

    def _tray(self):
        self.tray = QSystemTrayIcon(app_icon(), self)
        self.tray.setToolTip("New Life · 双击打开")
        menu = QMenu(self)
        menu.addAction("打开 New Life", self.show_main)
        menu.addAction("停止片段", lambda: self.invoke(self.controller.stop))
        menu.addSeparator()
        menu.addAction("退出 New Life", self.quit_app)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self.show_main() if reason in (QSystemTrayIcon.ActivationReason.DoubleClick, QSystemTrayIcon.ActivationReason.Trigger) else None)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()

    def quit_app(self):
        if self._quitting:
            return
        self._quitting = True
        try:
            errors = self.controller.shutdown()
            if errors is False:
                self._quitting = False
                return
        except Exception as exc:
            errors = [str(exc)]
        if errors:
            QMessageBox.warning(self, tr("退出前请注意"), tr("软件即将退出，但以下操作未完成，请按提示检查：") + "\n\n" + "\n".join(tr(str(error)) for error in errors))
        self.tray.hide()
        self.mini.hide()
        self.close()
        QApplication.instance().quit()

    def closeEvent(self, event):
        if self._quitting:
            self._theme_watch.stop()
            if self._settings_dialog is not None:
                self._settings_dialog.dispose()
                self._settings_dialog.hide()
            for dialog in self._detail_dialogs.values():
                dialog.hide()
            if self._import_result_dialog:
                self._import_result_dialog.hide()
            event.accept()
        else:
            event.ignore()
            self.quit_app()

    def begin_drag(self, path: str):
        if self.search.text().strip():
            self.show_message("清空搜索后，可以拖动序号调整列表顺序")
            return
        for index in range(self.list.count()):
            if self.list.item(index).data(Qt.ItemDataRole.UserRole) == path:
                self.list.setCurrentRow(index)
                self.list.startDrag(Qt.DropAction.MoveAction)
                break

    def rows_moved(self, *args):
        if not self._rendering:
            paths = [self.list.item(index).data(Qt.ItemDataRole.UserRole) for index in range(self.list.count())]
            # Rebuilding during the Qt model's move notification invalidates its item widgets.
            QTimer.singleShot(0, lambda: self.invoke(self.controller.reorder, paths))

    def open_settings(self, checked=False, tab=0):
        if self._settings_dialog is None:
            self._settings_dialog = SettingsDialog(self.controller, self, tab=tab)
        self._settings_hidden = False
        self._hide_other_helpers(self._settings_dialog)
        self._settings_dialog.show_section(tab)
        self._place_helper(self._settings_dialog)
        self._settings_dialog.show()
        self._place_helper(self._settings_dialog)
        self._fit_settings_panel()
        self._sync_settings_entries()
        return self._settings_dialog

    def toggle_settings(self, tab):
        section = "hotkeys" if tab == 2 else "call"
        if self._settings_dialog is not None and self._settings_dialog.isVisible() and self._settings_dialog.section == section:
            self.close_settings()
        else:
            self.open_settings(tab=tab)

    def close_settings(self):
        self._settings_hidden = False
        if self._settings_dialog is not None:
            self._settings_dialog._set_capture(False)
            self._settings_dialog.hide()
        self._settings_slot.hide()
        self._fit_settings_panel()
        self._sync_settings_entries()
        if self.isVisible():
            (self.hotkeys_button if self._settings_dialog and self._settings_dialog.section == "hotkeys" else self.call_help_button).setFocus()

    def change_hotkey(self, item: AudioItem):
        key = ("hotkey", item.key)
        dialog = self._detail_dialogs.get(key)
        if dialog and dialog.isVisible():
            dialog.hide()
            return dialog
        if dialog is None:
            dialog = ClipEditorDialog(self, hotkey=True)
            dialog.setWindowTitle(tr("片段快捷键 · ") + item.name)
            dialog.resize(410, 190)
            layout = QVBoxLayout(dialog)
            layout.addWidget(QLabel("点击输入框，按下一个快捷键组合"))
            field = QKeySequenceEdit(QKeySequence(item.hotkey))
            field.setMaximumSequenceLength(1)
            dialog.field = field
            layout.addWidget(field)
            layout.addWidget(muted("后台可用；按住不重复触发。留空可取消绑定。", True))
            layout.addWidget(muted("再次点击此音频的快捷键按钮可收起，未保存输入会保留", True))
            dialog.feedback = muted("", True)
            dialog.feedback.setWordWrap(True)
            layout.addWidget(dialog.feedback)
            save = QPushButton("保存")
            def accept():
                result = self.invoke(self.controller.set_hotkey, item.path, field.keySequence().toString(QKeySequence.SequenceFormat.PortableText))
                if result is not False:
                    dialog.hide()
            save.clicked.connect(accept)
            layout.addWidget(save)
            self._detail_dialogs[key] = dialog
            dialog.visibility_changed.connect(self._sync_detail_entries)
        self._hide_other_helpers(dialog)
        self._place_helper(dialog)
        dialog.show()
        self._place_helper(dialog)
        dialog.raise_()
        dialog.field.setFocus()
        return dialog

    def item_menu(self, item: AudioItem, position: QPoint):
        self.build_item_menu(item).exec(position)

    def build_item_menu(self, item: AudioItem):
        menu = QMenu(self)
        menu.addAction("精确设置播放范围…", lambda: self.precise_range(item))
        menu.addAction("恢复完整播放范围", lambda: self.invoke(self.controller.reset_range, item.path))
        menu.addAction("另存当前范围为新 WAV", lambda: self.invoke(self.controller.export, item.path))
        streams = getattr(item, "audio_streams", [])
        if streams:
            tracks = menu.addMenu("音轨")
            track_group = QActionGroup(tracks)
            track_group.setExclusive(True)
            for ordinal, stream in enumerate(streams, 1):
                index = int(stream.get("index", ordinal - 1))
                tags = stream.get("tags") or {}
                language = stream.get("language") or tags.get("language") or tr("未标注语言")
                title = stream.get("title") or tags.get("title") or ""
                codec = stream.get("codec_name") or stream.get("codec") or "?"
                channels = stream.get("channels", "?")
                label = tr("音轨 {number} · {track_language} · {codec} · {channels} 声道", number=ordinal, track_language=language, codec=codec, channels=channels)
                if title:
                    label += " · " + title
                action = tracks.addAction(label)
                action.setProperty("i18n_skip", True)
                action.setCheckable(True)
                track_group.addAction(action)
                action.setChecked(index == getattr(item, "audio_stream", None))
                action.triggered.connect(lambda checked=False, value=index: self.invoke(self.controller.set_audio_stream, item.path, value))
        menu.addSeparator()
        menu.addAction("重命名…", lambda: self.rename_item(item))
        menu.addAction("更换头像图片…", lambda: self.choose_appearance(item, "avatar"))
        if getattr(item, "avatar_source", "") or (item.avatar and ("/" in item.avatar or "\\" in item.avatar)):
            menu.addAction("重新裁剪头像…", lambda: self.recrop_avatar(item))
        menu.addAction("设置头像表情…", lambda: self.choose_emoji(item))
        menu.addAction("恢复默认头像", lambda: self.invoke(self.controller.reset_avatar, item.path))
        menu.addAction("背景颜色…", lambda: self.choose_color(item))
        menu.addAction("背景图片…", lambda: self.choose_appearance(item, "background"))
        if item.background:
            menu.addAction("重新裁剪背景…", lambda: self.recrop_background(item))
            menu.addAction("清除背景图片", lambda: self.invoke(self.controller.update_appearance, item.path, background=""))
        menu.addAction("恢复默认背景", lambda: self.invoke(self.controller.reset_background, item.path))
        menu.addAction("设置快捷键…", lambda: self.change_hotkey(item))
        menu.addSeparator()
        menu.addAction("打开所在文件夹", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(item.path).parent))))
        menu.addAction("删除音频…", lambda: self.delete_item(item))
        return menu

    def precise_range(self, item: AudioItem):
        if not item.playable:
            self.show_message(item.error or "音频不足 1 秒，无法设置片段", True)
            return
        key = ("range", item.key)
        dialog = self._detail_dialogs.get(key)
        if dialog is not None:
            if dialog.isVisible():
                dialog.hide()
            else:
                self._hide_other_helpers(dialog)
                self._place_helper(dialog)
                dialog.show()
                self._place_helper(dialog)
                dialog.raise_()
            return dialog
        dialog = ClipEditorDialog(self)
        dialog.setWindowTitle("播放范围 · " + item.name)
        dialog.resize(360, 190)
        layout = QVBoxLayout(dialog)
        form = QFormLayout()
        start, end = QDoubleSpinBox(), QDoubleSpinBox()
        dialog.start_field, dialog.end_field = start, end
        for control in (start, end):
            control.setDecimals(3)
            control.setSingleStep(.1)
            control.setSuffix(" 秒")
        start.setRange(0, max(0, item.duration - 1))
        end.setRange(1, item.duration)
        start.setValue(item.start)
        end.setValue(item.end)
        form.addRow("起点", start)
        form.addRow("终点", end)
        layout.addLayout(form)
        error = muted("至少选择 1 秒；保存后更新播放范围", True)
        dialog.feedback = error
        layout.addWidget(error)
        save = QPushButton("保存")
        def accept():
            if end.value() - start.value() < 1 - .000001:
                error.setText("范围不足 1 秒，请调整起点或终点")
                set_style(error, "color: #ffb694;")
                return
            result = self.invoke(self.controller.set_range, item.path, start.value(), end.value())
            if result is not False:
                dialog.hide()
        save.clicked.connect(accept)
        layout.addWidget(save)
        self._hide_other_helpers(dialog)
        self._detail_dialogs[key] = dialog
        self._place_helper(dialog)
        dialog.show()
        self._place_helper(dialog)
        dialog.raise_()
        return dialog

    def rename_item(self, item: AudioItem):
        name, ok = QInputDialog.getText(self, tr("重命名"), tr("音频名称（扩展名自动保留）"), text=item.name)
        if ok and name.strip():
            self.invoke(self.controller.rename, item.path, name.strip())

    def choose_color(self, item: AudioItem):
        color = QColorDialog.getColor(QColor(item.color), self, tr("片段背景颜色"))
        if color.isValid():
            self.invoke(self.controller.update_appearance, item.path, color=color.name())

    def choose_appearance(self, item: AudioItem, field: str):
        path, _ = QFileDialog.getOpenFileName(self, tr("选择头像图片" if field == "avatar" else "选择背景图片"), "", tr("图片 (*.png *.jpg *.jpeg *.bmp *.gif *.webp)"))
        if path:
            if field == "avatar":
                self.crop_avatar(item, path)
            else:
                self.crop_background(item, path)

    def recrop_avatar(self, item: AudioItem):
        try:
            path, crop = self.controller.avatar_edit_source(item.path)
            self.crop_avatar(item, path, crop)
        except Exception as exc:
            self.show_message(str(exc), True)

    def crop_avatar(self, item: AudioItem, path: str, crop=None):
        try:
            dialog = AvatarCropDialog(path, crop, self)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                self.invoke(self.controller.set_avatar, item.path, path, dialog.crop)
        except Exception as exc:
            self.show_message(str(exc), True)

    def recrop_background(self, item: AudioItem):
        try:
            path, crop = self.controller.background_edit_source(item.path)
            self.crop_background(item, path, crop)
        except Exception as exc:
            self.show_message(str(exc), True)

    def crop_background(self, item: AudioItem, path: str, crop=None):
        try:
            row = self.rows.get(item.key)
            aspect = max(1.1, (row.width() - 1) / (row.height() - 4)) if row else 12.0
            dialog = AvatarCropDialog(path, crop, self, aspect=aspect)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                self.invoke(self.controller.set_background, item.path, path, dialog.crop)
        except Exception as exc:
            self.show_message(str(exc), True)

    def choose_emoji(self, item: AudioItem):
        text, ok = QInputDialog.getText(self, tr("头像表情"), tr("输入一个表情，例如 🐮、👏、🎵"), text=item.avatar if len(item.avatar) < 8 else "🐮")
        if ok and text.strip():
            self.invoke(self.controller.update_appearance, item.path, avatar=text.strip())

    def delete_item(self, item: AudioItem):
        answer = QMessageBox.question(self, tr("删除音频"), tr("将“{name}”移到回收站？\n该文件对应的播放范围和外观设置也会移除。", name=item.name), QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if answer == QMessageBox.StandardButton.Yes:
            self.invoke(self.controller.delete, item.path)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._size_toolbar()
        self._fit_table()
        QTimer.singleShot(0, self._fit_table)
        if hasattr(self, "rows") and hasattr(self, "local_volume"):
            compact = self.width() < 870
            self.local_volume.setFixedWidth(50 if compact else 70)
            self.mode.setFixedWidth(90 if compact else 105)
            self.output_label.setMaximumWidth(65 if compact else 90)
            self.signature.setMaximumWidth(110)


class SettingsDialog(QDialog):
    """Reusable, nonmodal helper window; its toolbar entry also closes it."""
    def __init__(self, controller, parent: MainWindow | None = None, tab: int = 0):
        super().__init__(parent, Qt.WindowType.Tool)
        self.setModal(False)
        self.resize(520, 520)
        self.setMinimumSize(410, 320)
        self.controller = controller
        self.main = parent
        self._capturing = False
        self._disposed = False
        self._capture_widgets: set[QWidget] = set()
        self._hotkey_fields: dict[str, QKeySequenceEdit] = {}
        self.section = "call"
        self.setObjectName("SettingsPanel")
        set_style(self, APP_STYLE)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 4, 8, 4)
        outer.setSpacing(4)
        heading_row = QHBoxLayout()
        self.heading = QLabel("通话帮助")
        self.heading.setObjectName("Section")
        heading_row.addWidget(self.heading)
        self.feedback = muted("", True)
        self.feedback.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        heading_row.addWidget(self.feedback, 1)
        self.save_button = QPushButton("保存快捷键")
        self.save_button.setObjectName("Primary")
        self.save_button.clicked.connect(self.save)
        heading_row.addWidget(self.save_button)
        self.collapse_button = QPushButton("收起通话帮助")
        self.collapse_button.setObjectName("TextEntry")
        self.collapse_button.clicked.connect(self.reject)
        heading_row.addWidget(self.collapse_button)
        outer.addLayout(heading_row)
        self.tabs = QStackedWidget()
        self.call_page = self._call_tab()
        self.hotkeys_page = self._hotkeys_tab()
        self.tabs.addWidget(self.call_page)
        self.tabs.addWidget(self.hotkeys_page)
        outer.addWidget(self.tabs, 1)
        controller.state_changed.connect(self.update_state)
        controller.message.connect(self.receive_message)
        self._scroll_timer = QTimer(self)
        self._scroll_timer.setSingleShot(True)
        self._scroll_timer.timeout.connect(lambda: self.call_page.ensureWidgetVisible(self.diagnostics_panel, 0, 12))
        self.show_section(tab)
        self.update_state()

    def show_section(self, tab):
        self._set_capture(False)
        self.section = "hotkeys" if tab == 2 else "call"
        self.tabs.setCurrentIndex(1 if self.section == "hotkeys" else 0)
        self.heading.setText("快捷键" if self.section == "hotkeys" else "通话帮助")
        self.setWindowTitle(tr("快捷键" if self.section == "hotkeys" else "通话帮助"))
        self.collapse_button.setText("收起快捷键" if self.section == "hotkeys" else "收起通话帮助")
        self.save_button.setVisible(self.section == "hotkeys")
        i18n.retranslate(self)
        QTimer.singleShot(0, self._check_capture_focus)

    def _page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(4, 6, 4, 8)
        layout.setSpacing(8)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(page)
        set_style(scroll, "QScrollArea { background: #101920; } QScrollArea > QWidget > QWidget { background: #101920; }")
        return scroll, layout

    def _call_tab(self):
        page, layout = self._page()
        self._step(layout, "1", "检测虚拟音频设备", "让对方同时听见片段和你的讲话，需要安装 VB-CABLE。已安装时无需重复安装。")
        self.device_locked = muted("", True)
        self.device_locked.setWordWrap(True)
        layout.addWidget(self.device_locked)
        system_button = QPushButton("打开 Windows 声音设置")
        system_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("ms-settings:sound")))
        layout.addWidget(system_button, 0, Qt.AlignmentFlag.AlignLeft)
        warning = QFrame()
        warning.setObjectName("Warning")
        warn_layout = QVBoxLayout(warning)
        warn_layout.setContentsMargins(14, 12, 14, 12)
        self.call_heading = QLabel("!  尚未检测到 VB-CABLE")
        set_style(self.call_heading, "font-size: 16px; font-weight: 600; color: #edc55c;")
        self.call_heading.setWordWrap(True)
        warn_layout.addWidget(self.call_heading)
        detail = QLabel("让对方同时听见片段和你的讲话，\n需要先安装虚拟音频设备。")
        set_style(detail, "color: #d2d6c7; line-height: 1.5;")
        detail.setWordWrap(True)
        warn_layout.addWidget(detail)
        self.call_detail = detail
        self.install_quick = QPushButton("下载并安装 VB-CABLE")
        self.install_quick.setToolTip("从 VB-Audio 官网下载，以管理员身份运行安装程序。")
        self.install_quick.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://vb-audio.com/Cable/")))
        warn_layout.addWidget(self.install_quick)
        layout.addWidget(warning)
        detect = QPushButton("已安装，重新检测")
        detect.setIcon(control_icon("repeat"))
        detect.setProperty("themeIcon", "repeat")
        detect.clicked.connect(self.refresh_devices)
        layout.addWidget(detect)
        layout.addWidget(line())
        self._step(layout, "2", "在通话软件里选择麦克风", "打开微信、QQ、Discord 或游戏的音频设置，将输入设备改为下方 CABLE Output。播放器不能替你更改软件内部的选择。")
        self.temporary_button = QPushButton("连接并分享片段")
        self.temporary_button.setObjectName("Primary")
        self.temporary_button.clicked.connect(lambda: self.invoke(self.controller.set_share, True))
        self.temporary_status = muted("", True)
        self.temporary_status.setWordWrap(True)
        layout.addWidget(self.temporary_status)
        layout.addWidget(QLabel("应选的通话麦克风"))
        cable_row = QHBoxLayout()
        self.expected_input = QLineEdit("CABLE Output")
        self.expected_input.setReadOnly(True)
        cable_row.addWidget(self.expected_input)
        copy = QPushButton("复制")
        copy.clicked.connect(lambda: QApplication.clipboard().setText(self.expected_input.text()))
        cable_row.addWidget(copy)
        layout.addLayout(cable_row)
        layout.addWidget(line())
        self._step(layout, "3", "连接并试听测试声音", "连接后播放测试声音，请对方确认是否听见。发送电平只证明音频已送入虚拟设备。")
        layout.addWidget(self.temporary_button)
        self.test_button = QPushButton("测试通话声音")
        self.test_button.clicked.connect(lambda: self.invoke(self.controller.test_call_output))
        layout.addWidget(self.test_button)
        self.disconnect_button = QPushButton("完全断开通话（包括麦克风）")
        self.disconnect_button.clicked.connect(lambda: self.invoke(self.controller.disconnect_call))
        layout.addWidget(self.disconnect_button)
        self.call_diagnostic = muted("等待音频状态", True)
        self.call_diagnostic.setWordWrap(True)
        layout.addWidget(self.call_diagnostic)
        self.share_note = muted("片段分享关闭时，麦克风仍保持通话；完全断开请使用上方按钮。", True)
        self.share_note.setWordWrap(True)
        layout.addWidget(self.share_note)
        self.meters = {}
        for key, title in (("mic_peak", "真实麦克风电平"), ("call_peak", "通话发送电平"), ("local_peak", "本地播放电平")):
            row = QHBoxLayout()
            row.addWidget(muted(title, True))
            meter = QProgressBar()
            meter.setRange(0, 100)
            meter.setTextVisible(False)
            meter.setFixedHeight(7)
            row.addWidget(meter, 1)
            self.meters[key] = meter
            layout.addLayout(row)
        self.diagnostics_toggle = QToolButton()
        self.diagnostics_toggle.setText("查看音频诊断")
        self.diagnostics_toggle.setCheckable(True)
        self.diagnostics_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.diagnostics_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.diagnostics_toggle.toggled.connect(self.toggle_diagnostics)
        layout.addWidget(self.diagnostics_toggle, 0, Qt.AlignmentFlag.AlignLeft)
        self.diagnostics_panel = QWidget()
        diagnostics_layout = QVBoxLayout(self.diagnostics_panel)
        diagnostics_layout.setContentsMargins(4, 0, 4, 0)
        diagnostics_layout.setSpacing(5)
        self.diagnostics_labels = []
        for _ in range(3):
            diagnostic = muted("", True)
            diagnostic.setWordWrap(True)
            diagnostic.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            diagnostics_layout.addWidget(diagnostic)
            self.diagnostics_labels.append(diagnostic)
        self.diagnostics_panel.hide()
        layout.addWidget(self.diagnostics_panel)
        layout.addWidget(line())
        self._level_slider(layout, "发送片段音量", "send", "send_volume")
        self._level_slider(layout, "麦克风音量", "mic", "mic_volume")
        volume_note = muted("音量调整立即生效；停止片段不会关闭麦克风。", True)
        volume_note.setWordWrap(True)
        layout.addWidget(volume_note)
        note = QLabel("若音效被过滤，可调整通话软件的降噪与自动增益。本人声音有回声时，检查 Windows“侦听此设备”和声卡监听是否开启。")
        note.setWordWrap(True)
        set_style(note, "color: #57dec8;")
        layout.addWidget(note)
        layout.addStretch()
        return page

    def toggle_diagnostics(self, expanded: bool):
        self.diagnostics_panel.setVisible(expanded)
        self.diagnostics_toggle.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
        self.diagnostics_toggle.setText("收起音频诊断" if expanded else "查看音频诊断")
        i18n.retranslate(self.diagnostics_toggle)
        if expanded:
            self._scroll_timer.start(0)

    def _step(self, layout, number: str, title: str, description: str):
        row = QHBoxLayout()
        badge = QLabel(number)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setFixedSize(25, 25)
        set_style(badge, "background: #21dfc1; color: #082b22; border-radius: 12px; font-weight: 700;")
        row.addWidget(badge)
        content = QVBoxLayout()
        content.setSpacing(4)
        label = QLabel(title)
        label.setObjectName("Section")
        content.addWidget(label)
        explanation = muted(description)
        explanation.setWordWrap(True)
        content.addWidget(explanation)
        row.addLayout(content, 1)
        layout.addLayout(row)

    def _level_slider(self, layout, title: str, kind: str, key: str):
        row = QHBoxLayout()
        row.addWidget(QLabel(title))
        slider = VolumeSlider()
        slider.setValue(int(self.controller.state.get(key, 80)))
        value_label = QLabel(f"{slider.value()}%")
        value_label.setFixedWidth(40)
        row.addWidget(slider, 1)
        row.addWidget(value_label)
        def changed(value):
            value_label.setText(f"{value}%")
            self.invoke(self.controller.set_volume, kind, value)
        slider.valueChanged.connect(changed)
        if not hasattr(self, "_level_fields"):
            self._level_fields = {}
        self._level_fields[key] = (slider, value_label)
        layout.addLayout(row)

    def _hotkeys_tab(self):
        page, layout = self._page()
        hint = muted("点击输入框，再按下组合键。快捷键后台生效，按住不会重复触发。留空即可取消。")
        hint.setWordWrap(True)
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(8)
        for index, (action, title) in enumerate((("play_pause", "播放／暂停"), ("stop", "停止片段"), ("record", "开始／停止录音"), ("mark", "标记录音起点／终点"))):
            row = QHBoxLayout()
            row.addWidget(QLabel(title))
            field = QKeySequenceEdit(QKeySequence(self.controller.settings.get("global_hotkeys", {}).get(action, "")))
            field.setMinimumWidth(86)
            field.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            field.setMaximumSequenceLength(1)
            field.setObjectName("hotkey_" + action)
            self._hotkey_fields[action] = field
            self._capture_widgets.add(field)
            field.installEventFilter(self)
            for child in field.findChildren(QLineEdit):
                child.installEventFilter(self)
                self._capture_widgets.add(child)
            row.addWidget(field, 1)
            grid.addLayout(row, index // 2, index % 2)
        layout.addLayout(grid)
        layout.addWidget(hint)
        hint = muted("编辑快捷键时会暂停本软件的全局快捷键。保存时会检查重复与系统占用。", True)
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addStretch()
        return page

    def invoke(self, function, *args):
        try:
            return function(*args)
        except Exception as exc:
            if hasattr(self, "feedback"):
                self.feedback.setText(str(exc))
                set_style(self.feedback, "color: #ffc09b;")
            if self.main:
                self.main.show_message(str(exc), True)
            return None

    def refresh_devices(self):
        self.invoke(self.controller.refresh_devices)
        self.feedback.setText("正在检测音频设备…")

    def update_state(self):
        state = self.controller.state
        locked = state.get("recording", False) or state.get("call_connected", False)
        self.device_locked.setText(state.get("audio_follow_note", "") or ("设备更换可能短暂中断声音，录音会继续保存。" if locked else ""))
        self.device_locked.setVisible(bool(self.device_locked.text()))
        for key, (slider, label) in self._level_fields.items():
            blocked = slider.blockSignals(True)
            slider.setValue(int(state.get(key, self.controller.settings.get(key, 80))))
            label.setText(f"{slider.value()}%")
            slider.blockSignals(blocked)
        if state.get("call_connected"):
            self.call_heading.setText("通话音频已连接")
            self.call_detail.setText("麦克风正在送入虚拟设备。\n通话软件需选择 CABLE Output。")
        elif state.get("call_ready"):
            self.call_heading.setText("已检测到虚拟音频设备")
            self.call_detail.setText("请检查音频设备与通话软件设置，\n然后在主窗口开启“分享片段到通话”。")
        else:
            self.call_heading.setText("!  尚未检测到 VB-CABLE")
            self.call_detail.setText("让对方同时听见片段和你的讲话，\n需要先安装虚拟音频设备。")
        self.temporary_button.setEnabled(bool(state.get("call_ready")) and not state.get("call_connecting"))
        self.install_quick.setVisible(not state.get("call_ready"))
        self.temporary_button.setToolTip("将真实麦克风和片段送入虚拟设备；通话软件仍需选择 CABLE Output" if state.get("call_ready") else "先安装 VB-CABLE 并重新检测设备")
        self.temporary_status.setText(state.get("temporary_config_message", ""))
        self.temporary_status.setVisible(bool(state.get("temporary_config_message")))
        self.expected_input.setText(state.get("call_input_name") or "CABLE Output (VB-Audio Virtual Cable)")
        self.share_note.setText("片段分享已关闭，麦克风仍保持通话" if state.get("call_connected") and not state.get("send_to_call") else "片段分享关闭时，麦克风仍保持通话；完全断开请使用上方按钮。")
        playback_active = state.get("playing") or state.get("paused")
        self.disconnect_button.setEnabled(bool(state.get("call_connected")))
        self.test_button.setEnabled(bool(state.get("call_connected")) and not playback_active)
        self.test_button.setToolTip("请先停止当前片段，再播放测试声音" if playback_active else "先连接通话，再播放测试声音。")
        self.call_diagnostic.setText(state.get("call_diagnostic") or "等待音频状态")
        for key, meter in self.meters.items():
            meter.setValue(round(max(0, min(1, float(state.get(key, 0)))) * 100))
        diagnostics = state.get("diagnostics") or {}
        self.diagnostics_labels[0].setText("采集：丢失 {dropped} 帧 · 溢出 {overflows} 次".format(dropped=int(diagnostics.get("capture_dropped_frames", 0)), overflows=int(diagnostics.get("capture_overflows", 0))))
        self.diagnostics_labels[1].setText("输出：丢失 {dropped} 帧 · 欠载 {underflows} 次（{frames} 帧）".format(dropped=int(diagnostics.get("output_dropped_frames", 0)), underflows=int(diagnostics.get("output_underflows", 0)), frames=int(diagnostics.get("output_underrun_frames", 0))))
        self.diagnostics_labels[2].setText("当前缓冲：本地 {local} ms · 通话 {call} ms".format(local=f"{float(diagnostics.get('local_buffer_ms', 0)):.1f}", call=f"{float(diagnostics.get('call_buffer_ms', 0)):.1f}"))
        i18n.retranslate(self)

    def eventFilter(self, watched, event):
        if watched in self._capture_widgets:
            if event.type() == QEvent.Type.FocusIn:
                self._set_capture(True)
            elif event.type() == QEvent.Type.FocusOut:
                QTimer.singleShot(0, self._check_capture_focus)
        return super().eventFilter(watched, event)

    def _check_capture_focus(self):
        if not self._disposed:
            self._set_capture(self.isVisible() and self.section == "hotkeys"
                              and not (self.main and self.main._hotkey_dialog_active)
                              and QApplication.focusWidget() in self._capture_widgets)

    def _set_capture(self, active: bool):
        if active and (self._disposed or self.section != "hotkeys" or not self.isVisible()):
            active = False
        if active != self._capturing:
            self._capturing = active
            self.invoke(self.controller.suspend_hotkeys, active)

    def save(self):
        self._set_capture(False)
        values = {"global_hotkeys": {key: field.keySequence().toString(QKeySequence.SequenceFormat.PortableText) for key, field in self._hotkey_fields.items() if not field.keySequence().isEmpty()}}
        try:
            self.controller.save_settings(values)
        except Exception as exc:
            self.feedback.setText(str(exc))
            set_style(self.feedback, "color: #ffc09b;")
            return
        # Controller commands report asynchronous validation failures through message.

    def receive_message(self, text: str, error: bool = False):
        self.feedback.setText(text)
        self.feedback.setToolTip(text)
        set_style(self.feedback, "color: #ffc09b;" if error else "color: #57dec8;")
        i18n.retranslate(self.feedback)

    def reject(self):
        if self.main is not None and self.main._settings_dialog is self:
            self.main.close_settings()
        else:
            self.hide()

    def hideEvent(self, event):
        self._set_capture(False)
        self._scroll_timer.stop()
        super().hideEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self._check_capture_focus)

    def closeEvent(self, event):
        self.reject()
        event.accept()

    def done(self, result):
        self.reject()

    def dispose(self):
        if self._disposed:
            return
        self._set_capture(False)
        self._scroll_timer.stop()
        self._disposed = True
        try:
            self.controller.state_changed.disconnect(self.update_state)
            self.controller.message.disconnect(self.receive_message)
        except (RuntimeError, TypeError):
            pass
