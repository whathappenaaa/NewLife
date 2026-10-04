"""One bundled brand icon for all windows, dialogs, the taskbar and the tray."""
from pathlib import Path
import sys

from PySide6.QtGui import QIcon


def asset_path(name: str = "app.svg") -> Path:
    if name not in {"app.svg", "app.ico"}:
        raise ValueError("未知的品牌资源")
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    return root / "assets" / name


def app_icon() -> QIcon:
    """Call after creating QApplication; the ICO contains eight native sizes."""
    path = asset_path("app.ico")
    if not path.is_file():
        raise FileNotFoundError(f"缺少应用图标资源：{path}")
    return QIcon(str(path))
