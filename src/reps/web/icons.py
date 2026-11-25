"""Line icons (SVG), the home-screen icon (PNG drawn in code) and the manifest."""

import functools
import json
import struct
import zlib

ICON_SIZES = (180, 192, 512)
TEAL = (0x0F, 0x7B, 0x6C)
PALE = (0xF4, 0xF4, 0xF0)

# The dumbbell, as (left, top, right, bottom) fractions of the icon.
BARS = (
    (0.38, 0.46, 0.62, 0.54),
    (0.28, 0.32, 0.38, 0.68),
    (0.62, 0.32, 0.72, 0.68),
    (0.20, 0.39, 0.28, 0.61),
    (0.72, 0.39, 0.80, 0.61),
)

PATHS = {
    "today": '<path d="M5 9v6M8 6.5v11M16 6.5v11M19 9v6M8 12h8"/>',
    "week": '<rect x="4" y="5" width="16" height="15" rx="2"/><path d="M4 10h16M9 3v4M15 3v4"/>',
    "history": '<path d="M4 12a8 8 0 1 0 2.5-5.8L4 8.5M4 4v4.5h4.5M12 8v4l3 2"/>',
    "me": '<circle cx="12" cy="8" r="3.5"/><path d="M5 20c1-4 4-5.5 7-5.5s6 1.5 7 5.5"/>',
    "go": '<path d="M9 6l6 6-6 6"/>',
    "back": '<path d="M15 5l-7 7 7 7"/>',
    "edit": '<path d="M4 20l1-4L16 5l3 3L8 19zM14 7l3 3"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "bin": '<path d="M5 7h14M10 4h4M7 7l1 13h8l1-13M10 11v5M14 11v5"/>',
    "lift": '<path d="M4 9.5v5M7 7v10M17 7v10M20 9.5v5M7 12h10"/>',
    "body": '<circle cx="12" cy="5" r="2"/><path d="M6 10l6-1 6 1M12 9v6M12 15l-4 6M12 15l4 6"/>',
    "bolt": '<path d="M13 3L5 14h6l-1 7 8-11h-6z"/>',
    "wave": '<path d="M3 9c3-3 6 3 9 0s6 3 9 0M3 15c3-3 6 3 9 0s6 3 9 0"/>',
    "leaf": '<path d="M5 19C5 9 11 5 20 5c0 9-4 14-11 14-2 0-4-1-4-1M5 19c3-5 6-7 9-8"/>',
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 3v2M12 19v2M3 12h2M19 12h2M6 6l1 1M17 17l1'
    ' 1M6 18l1-1M17 7l1-1"/>',
    "note": '<path d="M9 18V6l10-2v12"/><circle cx="6.5" cy="18" r="2.5"/>'
    '<circle cx="16.5" cy="16" r="2.5"/>',
    "speaker": '<rect x="6" y="3" width="12" height="18" rx="2"/><circle cx="12" cy="14.5" r="3"/>'
    '<path d="M12 7h.01"/>',
    "heart": '<path d="M12 20C5 15 3 11.500 3 8.500a4.500 4.500 0 0 1 9-1 4.500 4.500 0 0 1 9 1C21'
    ' 11.500 19 15 12 20z"/>',
    "target": '<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="3.500"/>',
    "clock": '<circle cx="12" cy="12" r="8"/><path d="M12 7.500V12l3 2"/>',
    "shield": '<path d="M12 3l7 3v5c0 5-3 8-7 10-4-2-7-5-7-10V6z"/>',
    "scale": '<rect x="4" y="4" width="16" height="16" rx="3"/><path d="M8 9a5.500 5.500 0 0 1 8'
    ' 0l-2.500 2.500h-3z"/>',
    "save": '<path d="M12 4v11M7.500 11l4.500 4.500L16.500 11M5 20h14"/>',
    "out": '<path d="M10 4H5v16h5M14 8l4 4-4 4M18 12H9"/>',
    "link": '<path d="M10 14a4 4 0 0 0 6 0l3-3a4 4 0 0 0-6-6l-1 1'
    'M14 10a4 4 0 0 0-6 0l-3 3a4 4 0 0 0 6 6l1-1"/>',
    "stack": '<path d="M12 4l8 4-8 4-8-4zM4 12l8 4 8-4M4 16l8 4 8-4"/>',
    "grip": '<path d="M9 6h.01M15 6h.01M9 12h.01M15 12h.01M9 18h.01M15 18h.01"/>',
    "say": '<path d="M5 5h14a1.500 1.500 0 0 1 1.500 1.500v8A1.500 1.500 0 0 1 19 16h-7l-5 4v-4H5'
    'a1.500 1.500 0 0 1-1.500-1.500v-8A1.500 1.500 0 0 1 5 5z"/>',
    "check": '<path d="M5 12.500l4.500 4.500L19 7"/>',
}


def icon(name: str) -> str:
    return f'<svg class="icon" viewBox="0 0 24 24" aria-hidden="true">{PATHS[name]}</svg>'


def logo() -> str:
    bars = "".join(
        f'<rect x="{left * 24:.2f}" y="{top * 24:.2f}" width="{(right - left) * 24:.2f}"'
        f' height="{(bottom - top) * 24:.2f}" rx="0.8"/>'
        for left, top, right, bottom in BARS
    )
    tile = '<rect class="logo-bg" width="24" height="24" rx="6"/>'
    return (
        '<svg class="logo" viewBox="0 0 24 24" aria-hidden="true">'
        f'{tile}<g class="logo-mark">{bars}</g></svg>'
    )


@functools.cache
def icon_png(size: int) -> bytes:
    boxes = [tuple(round(edge * size) for edge in bar) for bar in BARS]
    rows = []
    for y in range(size):
        row = bytearray()
        for x in range(size):
            inside = any(
                left <= x < right and top <= y < bottom for left, top, right, bottom in boxes
            )
            row += bytes(PALE if inside else TEAL)
        rows.append(bytes(row))
    return _png(size, rows)


def _png(size: int, rows: list[bytes]) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    header = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    raw = b"".join(b"\x00" + row for row in rows)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def manifest() -> str:
    icons = [
        {"src": f"/icon-{size}.png", "sizes": f"{size}x{size}", "type": "image/png"}
        for size in ICON_SIZES
    ]
    return json.dumps(
        {"name": "reps", "short_name": "reps", "display": "standalone", "start_url": "/",
         "background_color": "#121211", "icons": icons}
    )
