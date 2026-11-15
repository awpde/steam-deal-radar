"""生成程序图标 docs/icon.ico（纯标准库，不依赖 Pillow）。

图标内容：深色圆角方块 + 绿色百分号（一眼看出是"折扣"），
256×256，用 3 倍超采样再缩，边缘不会锯齿。
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

SIZE = 256
SS = 3                      # 超采样倍数
W = SIZE * SS

BG = (27, 35, 46)           # #1b232e
EDGE = (42, 53, 66)         # #2a3542
GREEN = (109, 200, 73)      # #6dc849


def rounded(x: float, y: float, w: float, h: float, r: float) -> bool:
    if x < 0 or y < 0 or x > w or y > h:
        return False
    cx = min(max(x, r), w - r)
    cy = min(max(y, r), h - r)
    if (x, y) in ((cx, cy),):
        return True
    return (x - cx) ** 2 + (y - cy) ** 2 <= r * r


def ring(x: float, y: float, cx: float, cy: float, r: float, thick: float) -> bool:
    d = ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5
    return abs(d - r) <= thick / 2


def seg(x: float, y: float, x1: float, y1: float, x2: float, y2: float, thick: float) -> bool:
    dx, dy = x2 - x1, y2 - y1
    length2 = dx * dx + dy * dy
    t = 0.0 if length2 == 0 else max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / length2))
    px, py = x1 + t * dx, y1 + t * dy
    return (x - px) ** 2 + (y - py) ** 2 <= (thick / 2) ** 2


def build_rgba() -> bytes:
    buf = bytearray(W * W * 4)
    s = W / SIZE            # 坐标换算到超采样尺寸
    for py in range(W):
        for px in range(W):
            x, y = px / s, py / s
            r = g = b = a = 0
            if rounded(x, y, SIZE, SIZE, 56):
                inside = rounded(x, y, SIZE - 2, SIZE - 2, 55)
                r, g, b, a = (BG if inside else EDGE) + (255,)
                # 百分号：两个圆环 + 一条斜线
                if ring(x, y, 92, 92, 28, 17) or ring(x, y, 164, 164, 28, 17):
                    r, g, b = GREEN
                if seg(x, y, 176, 66, 80, 190, 17):
                    r, g, b = GREEN
            i = (py * W + px) * 4
            buf[i:i + 4] = bytes((r, g, b, a))
    return bytes(buf)


def downsample(data: bytes) -> bytes:
    out = bytearray(SIZE * SIZE * 4)
    n = SS * SS
    for y in range(SIZE):
        for x in range(SIZE):
            r = g = b = a = 0
            for sy in range(SS):
                for sx in range(SS):
                    i = ((y * SS + sy) * W + x * SS + sx) * 4
                    r += data[i]
                    g += data[i + 1]
                    b += data[i + 2]
                    a += data[i + 3]
            o = (y * SIZE + x) * 4
            out[o:o + 4] = bytes((r // n, g // n, b // n, a // n))
    return bytes(out)


def png_bytes(w: int, h: int, rgba: bytes) -> bytes:
    raw = b"".join(b"\x00" + rgba[y * w * 4:(y + 1) * w * 4] for y in range(h))
    comp = zlib.compress(raw, 9)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", comp) + chunk(b"IEND", b""))


def main() -> None:
    rgba = downsample(build_rgba())
    png = png_bytes(SIZE, SIZE, rgba)
    # ICO 容器：单个 256×256（PNG 压缩条目，Vista 及以上支持）
    header = struct.pack("<HHH", 0, 1, 1)
    entry = struct.pack("<BBBBHHII", 0, 0, 0, 0, 1, 32, len(png), 6 + 16)
    out = Path(__file__).resolve().parent.parent / "docs" / "icon.ico"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(header + entry + png)
    print(f"已生成 {out}  {out.stat().st_size} 字节")


if __name__ == "__main__":
    main()
