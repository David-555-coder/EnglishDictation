"""Render the tracked SVG source to a multi-resolution Windows icon."""

from pathlib import Path
from io import BytesIO

from PIL import Image
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtSvg import QSvgRenderer


def main():
    root = Path(__file__).resolve().parents[1]
    source = root / "assets" / "english_dictation.svg"
    renderer = QSvgRenderer(QByteArray(source.read_bytes()))
    if not renderer.isValid():
        raise ValueError(f"Invalid SVG: {source}")
    canvas = QImage(256, 256, QImage.Format_ARGB32)
    canvas.fill(Qt.transparent)
    painter = QPainter(canvas)
    renderer.render(painter)
    painter.end()
    output = QByteArray()
    buffer = QBuffer(output)
    buffer.open(QIODevice.WriteOnly)
    if not canvas.save(buffer, "PNG"):
        raise OSError("Could not render the icon PNG")
    image = Image.open(BytesIO(bytes(output))).convert("RGBA")
    image.save(root / "assets" / "english_dictation.png")
    image.save(root / "assets" / "english_dictation.ico", format="ICO",
               sizes=[(size, size) for size in (16, 24, 32, 48, 64, 128, 256)])


if __name__ == "__main__":
    main()
