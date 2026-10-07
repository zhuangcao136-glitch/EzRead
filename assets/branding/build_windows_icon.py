"""Build Windows icons from the same original used by the EzRead app UI.

Each ICO entry is resized directly from ezread-icon.png, preserving the
approved artwork, shading and transparency at every Windows display size.
"""

from io import BytesIO
from pathlib import Path
import struct

from PIL import Image


ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "static"
SOURCE = STATIC / "ezread-icon.png"
DESTINATION = STATIC / "ezread.ico"
SIZES = (16, 24, 32, 48, 64, 128, 256)


def main():
    encoded = []
    with Image.open(SOURCE) as image:
        original = image.convert("RGBA")
        for size in SIZES:
            icon = original.resize((size, size), Image.Resampling.LANCZOS)
            stream = BytesIO()
            icon.save(stream, format="PNG")
            encoded.append(stream.getvalue())

    # Explicit PNG entries preserve each original-derived image and its alpha.
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
