"""Color parsing helpers."""

NAMED = {
    "black": (0, 0, 0), "white": (255, 255, 255), "red": (255, 0, 0),
    "green": (0, 255, 0), "blue": (0, 0, 255), "yellow": (255, 255, 0),
    "cyan": (0, 255, 255), "magenta": (255, 0, 255), "orange": (255, 100, 0),
    "purple": (150, 0, 255), "pink": (255, 60, 160), "teal": (0, 200, 160),
    "lime": (170, 255, 0), "off": (0, 0, 0),
}


def parse_color(s):
    """Parse '#rrggbb', 'rrggbb', 'r,g,b', or a named color into (r,g,b)."""
    if isinstance(s, (list, tuple)) and len(s) == 3:
        return tuple(int(x) for x in s)
    s = str(s).strip().lower()
    if s in NAMED:
        return NAMED[s]
    if "," in s:
        parts = [int(p) for p in s.split(",")]
        if len(parts) != 3:
            raise ValueError(f"expected r,g,b got {s!r}")
        return tuple(max(0, min(255, p)) for p in parts)
    h = s[1:] if s.startswith("#") else s
    if len(h) == 3:
        h = "".join(ch * 2 for ch in h)
    if len(h) != 6:
        raise ValueError(f"cannot parse color {s!r}")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def scale(color, brightness):
    b = max(0.0, min(1.0, brightness))
    return tuple(int(c * b) for c in color)
