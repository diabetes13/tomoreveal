"""Shared constants and small helpers."""
import os

__version__ = "3.5.0"


ZMAGIC = b"\x28\xb5\x2f\xfd"


PALETTE = ["#4e79a7", "#f28e2b", "#e15759", "#76b7b2", "#59a14f", "#edc948", "#b07aa1", "#ff9da7",
           "#9c755f", "#bab0ac", "#1f77b4", "#d62728", "#2ca02c", "#9467bd", "#17becf", "#bcbd22"]


VIEWS = {  # name: (tilt from the top view in degrees, azimuth about z in degrees)
    "top": (0, 0), "steep": (25, 0), "oblique": (42, 0), "low": (62, 0),
    "oblique-left": (42, 35), "oblique-right": (42, -35), "end-on": (55, 90), "corner": (50, 135),
}


class UserError(Exception):
    """A problem the user can fix; shown as a plain message."""


def label_dtype(max_label):
    """The smallest signed integer type that holds label numbers up to max_label (int8, int16 or int32)."""
    import numpy as np
    return np.int8 if max_label <= 127 else np.int16 if max_label <= 32767 else np.int32


def norm_path(p):
    """The absolute path, with the letter case on disk (but mapped and subst drives kept as typed). Source keys hold it."""
    a = os.path.abspath(os.path.expanduser(str(p).strip().strip('"'))); r = os.path.realpath(a)
    return r if os.path.normcase(r) == os.path.normcase(a) else a


def read_json(path):
    """A JSON file as UTF-8, with or without a byte-order mark (never the Windows code page)."""
    import json
    with open(path, encoding="utf-8-sig") as f:
        return json.load(f)


def hex2rgb(c):
    from PIL import ImageColor
    return tuple(v / 255.0 for v in ImageColor.getrgb(c)[:3])
