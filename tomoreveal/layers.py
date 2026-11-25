"""Separate layer movies (3.6, R12): the 3D movie without overlays, plus one transparent ProRes 4444 movie per layer
("arrows": arrows, text and drawn shapes; "labels": legend, scale bar, captions, title, slice number), a
`name_layers.json` that lists them, and `merge` (FFmpeg overlay, no re-rendering) that lays them back together.

Inside the renderer the layers are premultiplied float32 (0..255), so dissolves and fades mix them like the picture;
they are written as straight (not premultiplied) RGBA, which is what FFmpeg's overlay filter and video editors expect.
"""
import json
import os
import subprocess

import numpy as np

LAYERS = (("labels", "Legend and scale bar", "_labels"), ("arrows", "Arrows and text", "_arrows"))  # bottom to top
FORMAT_OF = {name: "layer-" + name for name, _, _ in LAYERS}  # encode.FORMATS keys


def premul(img):
    """A PIL RGBA image or a straight RGBA array -> premultiplied float32 array (0..255)."""
    a = np.asarray(img, dtype=np.float32)
    out = a.copy(); out[..., :3] *= a[..., 3:4] / 255.0
    return out


def straight(pm):
    """Premultiplied float -> straight RGBA uint8 (colour where alpha is 0: black)."""
    pm = np.asarray(pm, np.float32); al = pm[..., 3:4]
    rgb = np.where(al > 0.5, pm[..., :3] * 255.0 / np.maximum(al, 1e-6), 0.0)
    return np.clip(np.round(np.concatenate([rgb, al], -1)), 0, 255).astype(np.uint8)


def over(top, bottom):
    """Premultiplied `top` over premultiplied `bottom`."""
    return top + bottom * (1.0 - top[..., 3:4] / 255.0)


def _mix(a, b, q, via, opaque_tone):
    if via in ("black", "white"):
        if opaque_tone:
            tone = np.zeros_like(a) if via == "black" else np.full_like(a, 255.0)
            if a.ndim == 3 and a.shape[2] == 4:
                tone[..., 3] = 255.0
        else:  # a layer fades out and back in (transparent in the middle)
            tone = np.zeros_like(a)
        return a + (tone - a) * min(1.0, 2 * q) if q < 0.5 else tone + (b - tone) * min(1.0, 2 * q - 1)
    return a * (1 - q) + b * q


def blend(a, b, q, via="dissolve", transparent=False):
    """A dissolve or fade through black / white between two frames (as Renderer.draw does), for plain pictures
    (uint8) or for separate layers ({"base": uint8, layer: premultiplied float})."""
    if isinstance(a, dict):
        out = {}
        for k in a:
            if k == "base":
                out[k] = np.clip(np.round(_mix(a[k].astype(np.float32), b[k].astype(np.float32), q, via, True)), 0, 255).astype(np.uint8)
            else:
                out[k] = _mix(a[k], b[k], q, via, False)
        return out
    return np.clip(np.round(_mix(a.astype(np.float32), b.astype(np.float32), q, via, True)), 0, 255).astype(np.uint8)


def composite(base, layers):
    """The merged frame: straight-alpha layers (uint8 RGBA) laid over the base in order (RGB uint8)."""
    out = np.asarray(base, np.float32)[..., :3]
    for L in layers:
        L = np.asarray(L, np.float32); al = L[..., 3:4] / 255.0
        out = L[..., :3] * al + out * (1 - al)
    return np.clip(np.round(out), 0, 255).astype(np.uint8)


def average(imgs):
    """Mean of several frames (motion blur), plain or as layers."""
    if isinstance(imgs[0], dict):
        out = {}
        for k in imgs[0]:
            acc = sum(np.asarray(i[k], np.float32) if k == "base" else premul(i[k]) for i in imgs) / len(imgs)
            out[k] = np.clip(np.round(acc), 0, 255).astype(np.uint8) if k == "base" else straight(acc)
        return out
    acc = sum(i.astype(np.float32) for i in imgs)
    return np.clip(np.round(acc / len(imgs)), 0, 255).astype(np.uint8)


def wanted(spec):
    """The layers a movie with separate layers gets: labels unless every overlay item is off; arrows only when it has any."""
    from . import annotations as an, overlays as ov
    anns = [a for a in an.items(spec) if a.get("visible", True)]
    out = []
    items = ov.overlay_settings(spec)
    if any(an.layer_of(a) == "labels" for a in anns) or any(it.get("show") for n, it in items.items() if n != "axes") \
            or items["axes"].get("show") and items["axes"].get("in_movie") \
            or any((p.get("overlays") or {}).get(n) == "show" for p in spec.get("shots", []) for n in items):
        out.append("labels")
    if any(an.layer_of(a) == "arrows" for a in anns):
        out.append("arrows")
    return out  # in the order they are laid over the base


def info_path(out):
    return os.path.splitext(out)[0] + "_layers.json"


def write_info(out, paths, fps, size, frames, layer_names):
    """name_layers.json: which file is which, in the order to lay them (file names relative to the json)."""
    from .common import __version__
    base_fmt = next((f for f in ("mp4", "mov", "mov-prores", "mov-transparent") if f in paths), None)
    titles = {n: t for n, t, _ in LAYERS}
    rec = dict(tomoreveal=__version__, fps=int(fps), size=list(size), frames=int(frames),
               base=os.path.basename(paths[base_fmt]) if base_fmt else None,
               layers=[dict(name=n, title=titles[n], file=os.path.basename(paths[FORMAT_OF[n]])) for n in layer_names
                       if FORMAT_OF[n] in paths],
               alpha="straight (not premultiplied); ProRes 4444",
               how="Lay the layers over the base movie in this order, at the same frame rate. TomoReveal: File > Merge "
                   "layer movies; FFmpeg: the overlay filter; any video editor: one track per file.")
    tmp = info_path(out) + ".tmp"
    with open(tmp, "w") as f:
        json.dump(rec, f, indent=1)
    os.replace(tmp, info_path(out))
    return info_path(out)


def merged_path(info):
    base = info[:-len("_layers.json")] if info.endswith("_layers.json") else os.path.splitext(info)[0]
    return base + "_merged.mp4"


def merge(info, out=None, layers=None, crf=18):
    """Lay the layer movies listed in `info` (name_layers.json) over the base movie into one MP4 (no re-rendering).
    layers: names to include (default: all). Returns the path written."""
    from .common import UserError
    from .encode import ffmpeg_exe
    try:
        rec = json.load(open(info))
    except (OSError, ValueError) as exc:
        raise UserError(f"could not read {os.path.basename(info)}: it is not a layer list made by TomoReveal") from exc
    folder = os.path.dirname(os.path.abspath(info))
    if not rec.get("base"):
        raise UserError("the layer list names no base movie")
    files = [os.path.join(folder, rec["base"])] + [os.path.join(folder, L["file"]) for L in rec.get("layers", [])
                                                   if layers is None or L["name"] in layers]
    missing = [os.path.basename(f) for f in files if not os.path.isfile(f)]
    if missing:
        raise UserError("missing next to the layer list: " + ", ".join(missing))
    out = out or merged_path(info)
    if os.path.abspath(out) in [os.path.abspath(f) for f in files]:
        raise UserError("the merged movie would overwrite one of its layers; choose another name")
    chain, last = [], "[0:v]"
    for i in range(1, len(files)):
        nxt = f"[m{i}]" if i < len(files) - 1 else "[out]"
        chain.append(f"{last}[{i}:v]overlay=format=auto:alpha=straight{nxt}"); last = nxt
    cmd = [ffmpeg_exe(), "-y", "-loglevel", "error"]
    for f in files:
        cmd += ["-i", f]
    if chain:
        cmd += ["-filter_complex", ";".join(chain), "-map", "[out]"]
    else:
        cmd += ["-map", "0:v"]
    tmp = os.path.splitext(out)[0] + ".partial.mp4"
    cmd += ["-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", str(int(crf)), "-preset", "medium",
            "-movflags", "+faststart", tmp]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise UserError("merging the layers failed: " + r.stderr.decode(errors="replace").strip()[-300:])
    os.replace(tmp, out)
    return out
