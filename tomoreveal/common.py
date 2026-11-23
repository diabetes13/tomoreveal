"""Shared constants and small helpers."""


__version__ = "3.4.1"


ZMAGIC = b"\x28\xb5\x2f\xfd"


PALETTE = ["#4e79a7", "#f28e2b", "#e15759", "#76b7b2", "#59a14f", "#edc948", "#b07aa1", "#ff9da7",
           "#9c755f", "#bab0ac", "#1f77b4", "#d62728", "#2ca02c", "#9467bd", "#17becf", "#bcbd22"]


VIEWS = {  # name: (tilt from the top view in degrees, azimuth about z in degrees)
    "top": (0, 0), "steep": (25, 0), "oblique": (42, 0), "low": (62, 0),
    "oblique-left": (42, 35), "oblique-right": (42, -35), "end-on": (55, 90), "corner": (50, 135),
}


class UserError(Exception):
    """A problem the user can fix; shown as a plain message."""


def hex2rgb(c):
    from PIL import ImageColor
    return tuple(v / 255.0 for v in ImageColor.getrgb(c)[:3])
