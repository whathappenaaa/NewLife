"""Shared desktop palette and styles for the native Qt interface."""

BACKGROUND = "#101920"
PANEL = "#17252e"
TEXT = "#edf4f5"
MUTED = "#99adb8"
MINT = "#21dfc1"
WARNING = "#edba4a"

APP_STYLE = """
QWidget { color: #edf4f5; font-family: 'Microsoft YaHei UI', 'Segoe UI'; font-size: 12px; }
QMainWindow, QDialog, QWidget#MainShell, QWidget#MiniShell { background: #101920; }
QWidget#MainShell, QWidget#MiniShell { border: 1px solid #30434e; border-radius: 10px; }
QLabel { border: none; background: transparent; }
QLabel#Brand { font-size: 19px; font-weight: 650; }
QLabel#Muted, QLabel#Hint { color: #99adb8; }
QLabel#Hint { font-size: 11px; }
QLabel#Section { font-size: 15px; font-weight: 600; }
QLabel#Signature { color: #ccd9df; font-size: 12px; }
QLabel#Error { color: #ffc09b; }
QPushButton, QToolButton { background: #1b2c36; border: 1px solid #3b515f; border-radius: 6px; padding: 7px 11px; min-height: 18px; }
QPushButton:hover, QToolButton:hover { background: #28434e; border-color: #6b939e; }
QPushButton:pressed, QToolButton:pressed { background: #1e514e; }
QPushButton:disabled, QToolButton:disabled { color: #647680; border-color: #293b45; background: #172630; }
QPushButton#TextEntry { background: transparent; border-color: transparent; padding: 5px 8px; }
QPushButton#TextEntry:hover { background: #28434e; border-color: #3b515f; }
QPushButton#TextEntry:checked { color: #21dfc1; background: #1b3038; border-color: #39a694; }
QFrame#SettingsPanel { background: #121f28; border: 1px solid #2a3e49; border-radius: 7px; }
QPushButton#Primary { color: #092922; background: #21dfc1; border-color: #21dfc1; font-weight: 600; }
QPushButton#Primary:hover { background: #73ecd6; }
QPushButton#Primary:disabled { background: #234b47; color: #8aa29f; border-color: #315955; }
QPushButton#Record { color: #ff797f; border-color: #ba4752; }
QPushButton#CallReady { color: #45e3cb; border-color: #39a694; }
QPushButton#CallMissing { color: #edba4a; border-color: #ad872d; }
QToolButton#Icon { padding: 3px; border: none; background: transparent; font-size: 17px; }
QToolButton#Icon:hover { background: #263f49; }
QToolButton#Exit { padding: 3px; border: none; background: transparent; }
QToolButton#Exit:hover { background: #803d47; }
QProgressBar { border: none; background: #304550; border-radius: 3px; min-height: 6px; max-height: 6px; }
QProgressBar::chunk { background: #21dfc1; border-radius: 3px; }
QLineEdit, QDoubleSpinBox, QSpinBox, QKeySequenceEdit { background: #172832; color: #edf4f5; border: 1px solid #3b5362; border-radius: 6px; padding: 7px 9px; selection-background-color: #287e73; }
QLineEdit:focus, QDoubleSpinBox:focus, QKeySequenceEdit:focus { border-color: #21dfc1; }
QComboBox { background: #192b35; border: 1px solid #3b5362; border-radius: 6px; min-height: 20px; padding: 6px 22px 6px 10px; }
QComboBox::drop-down { border: none; width: 19px; }
QComboBox::down-arrow { image: none; width: 0px; height: 0px; border: none; }
QComboBox QAbstractItemView { background: #1b2d37; color: #edf4f5; selection-background-color: #27564f; outline: none; border: 1px solid #415a67; }
QListWidget { background: transparent; border: none; outline: none; padding: 0; }
QListWidget::item { border: none; padding: 0; background: transparent; }
QListWidget::item:selected { background: transparent; }
QMenu { background: #1b2c36; color: #edf4f5; border: 1px solid #425c6a; border-radius: 7px; padding: 5px; }
QMenu::item { padding: 7px 18px; border-radius: 4px; }
QMenu::item:selected { background: #31534f; }
QMenu::item:disabled { color: #748995; }
QMenu::separator { height: 1px; background: #344b59; margin: 5px; }
QToolTip { background: #243c45; color: #eef8f7; border: 1px solid #548272; padding: 5px; }
QSlider::groove:horizontal { height: 5px; border-radius: 2px; background: #3b5060; }
QSlider::sub-page:horizontal { background: #21dfc1; border-radius: 2px; }
QSlider::handle:horizontal { background: #e2fff7; width: 12px; height: 12px; margin: -4px 0; border-radius: 6px; }
QScrollBar:vertical { background: #15232c; width: 7px; border: none; margin: 0; }
QScrollBar::handle:vertical { background: #425e6b; border-radius: 3px; min-height: 25px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QRadioButton { spacing: 7px; background: transparent; }
QRadioButton::indicator { width: 15px; height: 15px; border: 2px solid #a4bbc5; border-radius: 9px; background: #12212b; }
QRadioButton::indicator:checked { background: #21dfc1; border-color: #21dfc1; }
QCheckBox { spacing: 8px; }
QTabWidget::pane { border: none; border-top: 1px solid #354b57; }
QTabBar::tab { background: transparent; color: #aebfc8; padding: 13px 17px; border-bottom: 2px solid transparent; }
QTabBar::tab:selected { color: #21dfc1; border-bottom: 2px solid #21dfc1; }
QTabBar::tab:hover { background: #1b3038; }
QFrame#Line { background: #304651; border: none; max-height: 1px; }
QFrame#Warning { background: #302b1d; border: 1px solid #a88634; border-radius: 8px; }
QFrame#RecordBar { background: #121f28; border: 1px solid #2a3e49; border-radius: 7px; }
QFrame#TableHeader { background: #1b2b34; border: 1px solid #2a404c; border-radius: 6px; }
QWidget#VolumePopup { background: #1b2e37; border: 1px solid #4c6c77; border-radius: 7px; }
"""

# Colors are shared by Qt styles, custom painters and icon generation.
_theme = "system"
_last_light = None
_LIGHT = {
    "#101920": "#f4f7f9", "#17252e": "#ffffff", "#edf4f5": "#20343f",
    "#99adb8": "#536c79", "#21dfc1": "#008c78", "#30434e": "#c1d0d7",
    "#1b2c36": "#edf3f6", "#3b515f": "#b2c5cf", "#28434e": "#dcebeF",
    "#1e514e": "#c2e9df", "#172630": "#e9eef1", "#263f49": "#dcebef",
    "#12212b": "#ffffff", "#15232c": "#e5edf1", "#172832": "#ffffff",
    "#3b5362": "#aec3ce", "#1b3038": "#e4efF2", "#304651": "#c1d0d7",
    "#121f28": "#ffffff", "#2a3e49": "#c1d0d7", "#1b2b34": "#e6eef2",
    "#2a404c": "#c1d0d7", "#1b2e37": "#ffffff", "#4c6c77": "#aec3ce",
    "#ccd9df": "#405e6c", "#aebfc8": "#405e6c", "#dcfff6": "#006b5b",
    "#d4e5e8": "#405e6c", "#d5e6eb": "#405e6c", "#cde4ec": "#405e6c",
    "#a6ffea": "#006b5b", "#e2fff7": "#008c78", "#45e3cb": "#006b5b",
    "#202c34": "#ffffff", "#202832": "#ffffff", "#263d48": "#c1d0d7",
    "#ffffff": "#ffffff", "#302b1d": "#fff5dc", "#edc55c": "#765300",
    "#edba4a": "#765300", "#d2d6c7": "#685b38", "#ffc09b": "#a83216",
    "#57dec8": "#006b5b", "#ff797f": "#b52d41", "#3c515b": "#afc2cc",
    "#eef9f7": "#20343f", "#b8cbd3": "#536c79", "#263d46": "#e2edf1",
    "#425965": "#aec3ce", "#3bc8b4": "#008c78", "#a5c0cc": "#405e6c",
    "#dbe9ed": "#405e6c", "#a8c3c9": "#536c79", "#e9ce57": "#886600",
    "#192b35": "#ffffff", "#1b2d37": "#ffffff", "#27564f": "#c2e9df",
    "#415a67": "#aec3ce", "#234b47": "#d2e6df", "#315955": "#bdd7cd",
    "#8aa29f": "#647c74", "#092922": "#ffffff", "#082b22": "#ffffff",
    "#243c45": "#ffffff", "#287e73": "#c2e9df", "#293b45": "#cad7de",
    "#304550": "#d4e0e6", "#31534f": "#c2e9df", "#344b59": "#c1d0d7",
    "#354b57": "#c1d0d7", "#39a694": "#008c78", "#3b5060": "#aec3ce",
    "#425c6a": "#aec3ce", "#425e6b": "#93aebc", "#548272": "#008c78",
    "#647680": "#7d929d", "#6b939e": "#577787", "#73ecd6": "#006b5b",
    "#748995": "#7d929d", "#803d47": "#fbdde2", "#a4bbc5": "#859da9",
    "#a88634": "#b1904f", "#ad872d": "#b1904f", "#ba4752": "#b52d41",
    "#eef8f7": "#20343f",
}

def is_light():
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    if _theme != "system":
        return _theme == "light"
    scheme = app.styleHints().colorScheme() if app else Qt.ColorScheme.Unknown
    if scheme != Qt.ColorScheme.Unknown:
        return scheme == Qt.ColorScheme.Light
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            return bool(winreg.QueryValueEx(key, "AppsUseLightTheme")[0])
    except (OSError, ImportError):
        return False

def theme_color(value):
    if not isinstance(value, str):
        return value
    return (_LIGHT if is_light() else _DARK).get(value.lower(), value)

def style_for(value):
    import re
    return re.sub(r"#[0-9a-fA-F]{6}", lambda match: theme_color(match.group()), value)

def set_theme(value):
    global _theme, _last_light
    previous = _theme
    _theme = value if value in ("dark", "light", "system") else "system"
    light = bool(is_light())
    if previous == _theme and _last_light == light:
        return
    _last_light = light
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    if app:
        for widget in app.allWidgets():
            base = widget.property("theme_base_style")
            if base is None and widget.styleSheet():
                base = widget.styleSheet()
                widget.setProperty("theme_base_style", base)
            if base:
                widget.setStyleSheet(style_for(base))
            name = widget.property("iconName") or widget.property("themeIcon")
            if name and hasattr(widget, "setIcon"):
                from .widgets import control_icon
                widget.setIcon(control_icon(name, widget.property("themeIconColor") or "#dbe9ed"))
            widget.update()


def set_style(widget, source):
    widget.setProperty("theme_base_style", source)
    widget.setStyleSheet(style_for(source))

APP_STYLE += """
QCheckBox::indicator { width: 20px; height: 20px; border: 2px solid #99adb8; border-radius: 4px; background: #172832; }
QCheckBox::indicator:checked { background: #21dfc1; border-color: #21dfc1; }
QCheckBox::indicator:indeterminate { background: #57dec8; }
QPushButton#SharingOff { background: #1e514e; border: 2px solid #21dfc1; font-weight: 700; min-height: 24px; }
QPushButton#SharingActive { background: #21dfc1; color: #092922; border: 2px solid #21dfc1; font-weight: 700; min-height: 24px; }
QPushButton#Toolbar, QToolButton#Toolbar { border: 1px solid #3b515f; background: #1b2c36; border-radius: 6px; padding: 2px 4px; min-height: 0; }
QPushButton#Toolbar:checked { background: #1e514e; border-color: #21dfc1; color: #21dfc1; }
QPushButton#Toolbar:hover, QToolButton#Toolbar:hover { background: #28434e; border-color: #6b939e; }
QCheckBox#SelectAudio::indicator { width: 18px; height: 18px; border-width: 2px; }
QLabel#Signature { font-size: 10px; }
"""

# Keep existing painters and icons on the same neutral gray / soft green palette.
_DARK = {
    "#101920": "#191c21", "#17252e": "#20252b", "#edf4f5": "#f0f3f5",
    "#99adb8": "#adb5bf", "#21dfc1": "#b3e6c6", "#1b2c36": "#2a3038",
    "#3b515f": "#404852", "#30434e": "#363e47", "#28434e": "#343d46",
    "#1e514e": "#263d35", "#172630": "#252b32", "#263f49": "#343d46",
    "#12212b": "#20252b", "#15232c": "#1f242b", "#172832": "#242a32",
    "#3b5362": "#404852", "#1b3038": "#263d35", "#304651": "#363e47",
    "#121f28": "#20252b", "#2a3e49": "#363e47", "#1b2b34": "#262c34",
    "#2a404c": "#363e47", "#1b2e37": "#242a32", "#4c6c77": "#52606b",
    "#092922": "#153127", "#73ecd6": "#c8eed5", "#45e3cb": "#b3e6c6",
    "#e2fff7": "#b3e6c6", "#39a694": "#80b795", "#57dec8": "#b3e6c6",
    "#3b5060": "#52606b", "#1b2d37": "#242a32", "#192b35": "#242a32",
    "#425965": "#52606b", "#3bc8b4": "#b3e6c6", "#263d46": "#263d35",
    "#263d48": "#363e47", "#304550": "#404852", "#287e73": "#365c49",
    "#202832": "#20252b", "#202c34": "#20252b",
}
_LIGHT.update({
    "#101920": "#f5f6f8", "#17252e": "#ffffff", "#edf4f5": "#232b33",
    "#99adb8": "#616b77", "#21dfc1": "#24694e", "#1b2c36": "#eef1f4",
    "#3b515f": "#c4ccd5", "#30434e": "#dce1e7", "#28434e": "#e4e9ee",
    "#1e514e": "#e8f3ed", "#172832": "#ffffff", "#3b5362": "#c4ccd5",
    "#1b3038": "#e8f3ed", "#304651": "#dce1e7", "#121f28": "#ffffff",
    "#2a3e49": "#dce1e7", "#1b2b34": "#eef1f4", "#2a404c": "#dce1e7",
    "#45e3cb": "#24694e", "#e2fff7": "#24694e", "#39a694": "#24694e",
    "#57dec8": "#24694e", "#73ecd6": "#1e5942", "#3bc8b4": "#24694e",
})
