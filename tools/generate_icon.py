"""Deterministically render the checked-in SVG into a multi-size Windows ICO.

Run .venv/Scripts/python.exe tools/generate_icon.py from the repository root.
No image-generation service or optional image package is required.
"""
from __future__ import annotations

from pathlib import Path
import struct

from PySide6.QtCore import QByteArray, QBuffer, QIODevice
from PySide6.QtGui import QImage, QPainter
from PySide6.QtSvg import QSvgRenderer


SIZES = (16, 20, 24, 32, 48, 64, 128, 256)


def make_ico(svg_path: Path, destination: Path) -> None:
    renderer = QSvgRenderer(str(svg_path))
    if not renderer.isValid():
        raise ValueError(f"Invalid SVG: {svg_path}")
    frames = []
    for size in SIZES:
        # Render above target resolution, then downsample to retain smooth horns
        # and muzzle edges in the 16/20px title-bar and tray variants.
        from PySide6.QtCore import Qt
        image = QImage(size * 4, size * 4, QImage.Format.Format_ARGB32_Premultiplied)
        image.setDotsPerMeterX(3780)
        image.setDotsPerMeterY(3780)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        renderer.render(painter)
        painter.end()
        image = image.scaled(size, size, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
        # Qt's default density depends on the current QApplication/screen. Fix
        # PNG metadata too so builds match headless and GUI-process generation.
        image.setDotsPerMeterX(3780)
        image.setDotsPerMeterY(3780)
        payload = QByteArray()
        buffer = QBuffer(payload)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        if not image.save(buffer, "PNG"):
            raise OSError("Unable to encode icon PNG")
        frames.append(bytes(payload))
    offset = 6 + len(SIZES) * 16
    header = bytearray(struct.pack("<HHH", 0, 1, len(SIZES)))
    for size, payload in zip(SIZES, frames, strict=True):
        header.extend(struct.pack("<BBBBHHII", 0 if size == 256 else size,
                                  0 if size == 256 else size, 0, 0, 1, 32, len(payload), offset))
        offset += len(payload)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(bytes(header) + b"".join(frames))


if __name__ == "__main__":
    project = Path(__file__).resolve().parents[1]
    make_ico(project / "assets" / "app.svg", project / "assets" / "app.ico")
    print("Generated assets/app.ico: " + ", ".join(f"{size}px" for size in SIZES))
