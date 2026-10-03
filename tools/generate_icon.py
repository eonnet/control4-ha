"""Generate a small original PNG brand icon using only the standard library."""

from __future__ import annotations

from math import hypot
from pathlib import Path
import struct
import zlib


SIZE = 256
OUTPUT = Path(__file__).resolve().parents[1] / "custom_components/control4_advanced/brand/icon.png"


def line_distance(x: float, y: float, start: tuple[int, int], end: tuple[int, int]) -> float:
    ax, ay = start
    bx, by = end
    dx, dy = bx - ax, by - ay
    fraction = max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / (dx * dx + dy * dy)))
    return hypot(x - (ax + fraction * dx), y - (ay + fraction * dy))


def pixel(x: int, y: int) -> bytes:
    # Dark blue background with a gentle center highlight.
    glow = max(0.0, 1.0 - hypot(x - 128, y - 128) / 180)
    color = [int(12 + 10 * glow), int(25 + 16 * glow), int(45 + 23 * glow)]
    roof = (((48, 132), (128, 63)), ((128, 63), (208, 132)))
    walls = (((66, 118), (66, 192)), ((66, 192), (190, 192)), ((190, 192), (190, 118)))
    if min(line_distance(x, y, a, b) for a, b in roof + walls) <= 5:
        color = [235, 245, 255]
    links = (((91, 150), (128, 113)), ((128, 113), (165, 150)), ((91, 150), (128, 174)),
             ((165, 150), (128, 174)))
    if min(line_distance(x, y, a, b) for a, b in links) <= 4:
        color = [51, 204, 204]
    if any(hypot(x - nx, y - ny) <= 10 for nx, ny in ((91, 150), (128, 113), (165, 150), (128, 174))):
        color = [240, 253, 255]
    return bytes((*color, 255))


def chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack("!I", len(data)) + kind + data + struct.pack("!I", zlib.crc32(kind + data))


def main() -> None:
    scanlines = b"".join(b"\x00" + b"".join(pixel(x, y) for x in range(SIZE)) for y in range(SIZE))
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack("!2I5B", SIZE, SIZE, 8, 6, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(scanlines, level=9)) + chunk(b"IEND", b"")
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_bytes(png)


if __name__ == "__main__":
    main()
