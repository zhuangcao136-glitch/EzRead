"""Build a crisp small-surface version of the approved EzRead book icon.

The detailed original remains in ezread-icon.png for the app UI. Every ICO
entry uses the same flat silhouette so Windows cannot select a soft bitmap
at a different display scale.
"""

from io import BytesIO
from pathlib import Path
import struct

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "static"
DESTINATION = STATIC / "ezread.ico"
SIZES = (16, 24, 32, 48, 64, 128, 256)


def small_book(size):
    scale = size * 4 / 100
    canvas = Image.new("RGBA", (size * 4, size * 4), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)

    def point(x, y):
        return (round(x * scale), round(y * scale))

    def polygon(points, color):
        draw.polygon([point(*p) for p in points], fill=color)

    draw.rounded_rectangle((*point(1, 1), *point(99, 99)),
                           radius=round(20 * scale), fill="#174c38")

    # A single light shape per page gives the book a strong outline at 16 px.
    polygon([(12, 31), (18, 27), (28, 27), (39, 30), (50, 38),
             (50, 74), (42, 68), (32, 65), (23, 65), (12, 68)], "#fff9ea")
    polygon([(88, 31), (82, 27), (72, 27), (61, 30), (50, 38),
             (50, 74), (58, 68), (68, 65), (77, 65), (88, 68)], "#fff9ea")
    polygon([(47, 37), (53, 37), (53, 78), (50, 75), (47, 78)], "#e0aa4f")
    return canvas.resize((size, size), Image.Resampling.BOX)


def main():
    encoded = []
    for size in SIZES:
        icon = small_book(size)
        stream = BytesIO()
        icon.save(stream, format="PNG")
        encoded.append(stream.getvalue())

    # PNG-compressed ICO entries preserve the exact hand-tuned small images.
    header = struct.pack("<HHH", 0, 1, len(SIZES))
    offset = 6 + len(SIZES) * 16
    entries = []
    for size, image in zip(SIZES, encoded):
        entries.append(struct.pack("<BBBBHHII", size if size < 256 else 0,
                                   size if size < 256 else 0, 0, 0, 1, 32,
                                   len(image), offset))
        offset += len(image)
    DESTINATION.write_bytes(header + b"".join(entries) + b"".join(encoded))
    print(DESTINATION)


if __name__ == "__main__":
    main()
