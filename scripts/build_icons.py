"""Export the vector master to transparent PNGs and a multi-resolution Windows ICO."""
import os
import struct
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer


def main() -> None:
    app = QGuiApplication.instance() or QGuiApplication([])
    assets = Path(__file__).resolve().parent.parent / "vmix_youtube_uploader" / "assets"
    renderer = QSvgRenderer(str(assets / "app.svg"))
    if not renderer.isValid():
        raise RuntimeError("Invalid icon SVG")
    sizes = (16, 20, 24, 32, 40, 48, 64, 128, 256, 512)
    frames = []
    for size in sizes:
        # Supersampling preserves smooth edges in the tiny Windows tray sizes.
        canvas = QImage(size * 4, size * 4, QImage.Format.Format_ARGB32_Premultiplied)
        canvas.fill(Qt.GlobalColor.transparent)
        painter = QPainter(canvas)
        renderer.render(painter)
        painter.end()
        canvas = canvas.scaled(size, size, Qt.AspectRatioMode.IgnoreAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)
        path = assets / f"app-{size}.png"
        if not canvas.save(str(path), "PNG"):
            raise RuntimeError(f"Could not save {path}")
        if size <= 256:
            frames.append((size, path.read_bytes()))
    offset = 6 + 16 * len(frames)
    directory, data = bytearray(), bytearray()
    for size, png in frames:
        directory.extend(struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(png), offset))
        data.extend(png)
        offset += len(png)
    (assets / "app.ico").write_bytes(struct.pack("<HHH", 0, 1, len(frames)) + directory + data)
    print(f"Created {len(sizes)} PNG sizes and app.ico in {assets}")


if __name__ == "__main__":
    main()
