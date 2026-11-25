"""Camera framing, the clip box, and turning the movie settings into per-frame states."""
import copy
import json
import math
import uuid

import numpy as np

from .animations import closeup_animation, steady_framing
from .common import UserError
from .paths import PATH_CAMERA, PathPending, path_poses
from .transitions import ease_curve, lerp_pose, smooth01, transition_of, with_end_ease


def clip_box(spec, shape):
    """Kept region of the tomogram as ((x0, y0, z0), (x1, y1, z1)) inclusive, or None when nothing is clipped."""
    c = spec.get("clip")
    if not c:
        return None
    nz, ny, nx = shape; lo, hi = [], []
    for ax, n in (("x", nx), ("y", ny), ("z", nz)):
        a, b = c.get(ax) or (0, n - 1)
        a = min(max(0, int(a)), n - 1); b = min(max(a, int(b)), n - 1)
        lo.append(a); hi.append(b)
    if lo == [0, 0, 0] and hi == [nx - 1, ny - 1, nz - 1]:
        return None
    return tuple(lo), tuple(hi)


class Framer:
    """Right-handed view from +z; the long in-plane axis runs along the long side of the frame."""
    def __init__(self, shape, aspect, fov=30.0, ortho=False):
        nz, ny, nx = shape
        if (ny > nx) == (aspect >= 1):
            self.u = np.array([-1., 0, 0])
        else:
            self.u = np.array([0., 1, 0])
        self.z = np.array([0., 0, 1]); self.aspect = aspect; self.fov = fov; self.ortho = ortho

    def axes(self, tilt, az):
        t, a = math.radians(tilt), math.radians(az)
        p = math.cos(t) * self.z - math.sin(t) * self.u      # centre -> camera
        up = math.cos(t) * self.u + math.sin(t) * self.z
        R = np.array([[math.cos(a), -math.sin(a), 0], [math.sin(a), math.cos(a), 0], [0, 0, 1]])
        return R @ p, R @ up

    def fit(self, lo, hi, tilt, az, margin=1.07, centre=None):
        """Pose that just frames the box lo..hi (around `centre` when given, 3.6; else around the box's middle)."""
        p, up = self.axes(tilt, az)
        c, dist, scale = self.fit_dir(lo, hi, p, up, margin, centre)
        return dict(c=c, tilt=float(tilt), az=float(az), dist=dist, scale=scale)

    def fit_dir(self, lo, hi, p, up, margin=1.07, centre=None):
        """(centre, distance, scale) that just frame the box lo..hi seen along p (centre -> camera) with this up.
        centre (3.6): the rotation centre to look at; the corners are measured from it, so the box still fits."""
        lo, hi = np.asarray(lo, float), np.asarray(hi, float)
        hi = np.maximum(hi, lo + 1.0)  # at least one voxel wide: a zero-size box has no distance
        c = (lo + hi) / 2 if centre is None else np.asarray(centre, float)
        corners = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]) - c
        th = math.tan(math.radians(self.fov) / 2)
        right = np.cross(up, p)
        xc, yc, zc = np.abs(corners @ right), np.abs(corners @ up), corners @ p
        dist = float(np.max(zc + np.maximum(yc / th, xc / (th * self.aspect)))) * margin
        scale = float(np.max(np.maximum(yc, xc / self.aspect))) * margin
        return c, dist, scale


SPIN_AXES = (("around Z (up and down through the slices)", "z"), ("around X", "x"), ("around Y", "y"),
             ("around the screen's up-down line", "screen"))


def _turn(axis, deg):
    """Rotation matrix of deg degrees around the x, y or z axis of the volume, or around any vector (3.6, Rodrigues)."""
    a = math.radians(deg); c, s = math.cos(a), math.sin(a)
    if isinstance(axis, str):
        return {"x": np.array([[1, 0, 0], [0, c, -s], [0, s, c]]), "y": np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]]),
                "z": np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])}[axis]
    k = np.asarray(axis, float); k = k / (np.linalg.norm(k) or 1.0)
    K = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
    return np.eye(3) + s * K + (1 - c) * (K @ K)


CENTRES = (("middle of what is shown", "shown"), ("middle of the whole tomogram", "volume"))  # overview "centre" (3.6)


def axis_turn_poses(fr, p0, axis, angles, lo, hi, centre=None):
    """Camera poses turned around the volume's x or y axis (through the rotation centre of p0) by each angle. The
    distance is the one that frames lo..hi at every angle reached, so nothing leaves the picture on the way round."""
    from .paths import _pose
    d0, up0 = fr.axes(p0["tilt"], p0["az"]); c = np.asarray(p0["c"], float)
    if isinstance(axis, str) and axis == "screen":  # the screen's vertical through the centre: turns "around itself" (3.6)
        axis = np.asarray(up0, float)
    zoom = p0["dist"] / max(1e-9, fr.fit(lo, hi, p0["tilt"], p0["az"], 1.07, centre)["dist"])  # the view's own zoom (and pan)
    samples = np.linspace(min(angles), max(angles), 25) if len(angles) else [0.0]
    D = max(fr.fit_dir(lo, hi, _turn(axis, a) @ d0, _turn(axis, a) @ up0, 1.07, centre)[1] for a in samples) * zoom
    out = []
    for a in angles:
        R = _turn(axis, a); pos = c + D * (R @ d0)
        q = _pose(fr, pos, c, R @ up0, az_from_up=True)  # a top view keeps its turn (3.6, user's Y-spin report)
        if fr.ortho:
            q["scale"] = p0["scale"] * D / p0["dist"]
        out.append(q)
    return out


def view_pan(view):
    """Offset (x, y, z voxels) of the rotation centre of a view from the middle of what it frames, or None."""
    pan = (view or {}).get("pan")
    if pan is None:
        return None
    pan = np.asarray(pan, float)
    return None if pan.shape != (3,) or not np.any(pan) else pan


def main_box(spec, shape):
    """(lo, hi) in voxels (x, y, z) of what the main view frames: the whole volume, or the clip box (unless not fitted)."""
    nz, ny, nx = shape
    lo, hi = np.zeros(3), np.array([nx - 1, ny - 1, nz - 1], float)
    cb = clip_box(spec, shape)
    if cb and (spec.get("clip") or {}).get("fit", True):
        lo, hi = np.array(cb[0], float), np.array(cb[1], float)
    return lo, hi


def view_framing(spec, shape, lo, hi, tilt, az):
    """(zoom, pan) of a view at (tilt, az) that frames the box lo..hi, as the main view frames its box at zoom 1."""
    out = spec.get("output", {}); w, h = out.get("size", [1920, 1080]); style = spec.get("style", {})
    fr = Framer(shape, w / h, float(style.get("fov", 30)), bool(style.get("ortho", False)))
    mlo, mhi = main_box(spec, shape)
    base, want = fr.fit(mlo, mhi, tilt, az, 1.07), fr.fit(lo, hi, tilt, az, 1.07)
    z = base["scale"] / want["scale"] if fr.ortho else base["dist"] / want["dist"]
    return float(z), [float(v) for v in want["c"] - base["c"]]


def class_spec(spec, ds, k):
    """Settings of structure k: keyed by its stable key (3.2), or by its number in older settings."""
    cls = spec.get("classes", {}); c = ds.classes.get(k, {})
    return cls.get(c.get("key")) or cls.get(str(k)) or {}


TOMOGRAM_MODES = ("default", "shown", "faded", "hidden")


# ---- close-up targets and the other structures ----
def target_items(tgt):
    """[(structure key, component or None)] of a close-up target: {"items": [{"structure", "component"}, ...]} (3.4),
    or the older single {"class", "component"}."""
    if not tgt:
        return []
    if isinstance(tgt.get("items"), list):
        return [(it.get("structure"), it.get("component")) for it in tgt["items"] if it.get("structure") is not None]
    if tgt.get("class") is not None:
        return [(tgt["class"], tgt.get("component"))]
    return []


def part_region(part):
    """The box (lo, hi) in voxels (x, y, z) of a close-up framed on a region drawn in the preview (3.5), or None."""
    r = part.get("region") if isinstance(part, dict) else None
    if not isinstance(r, dict) or r.get("lo") is None or r.get("hi") is None:
        return None
    lo, hi = np.asarray(r["lo"], float), np.asarray(r["hi"], float)
    return np.minimum(lo, hi), np.maximum(np.maximum(lo, hi), np.minimum(lo, hi) + 1.0)


def flip_spec(spec, shape, axes):
    """Points, boxes and slice numbers of the settings moved along when the volume is flipped in `axes` (3.5): arrows,
    drawn frames, camera path points, view offsets, the clip box and slice numbers then still mean the same things.
    (Objects in focus are found again by the window, which can look at the new surfaces.)"""
    nz, ny, nx = shape; n = {"x": nx, "y": ny, "z": nz}; idx = {"x": 0, "y": 1, "z": 2}
    axes = [a for a in "xyz" if a in axes]
    if not axes:
        return spec

    def pt(p):
        if p is None:
            return p
        q = [float(v) for v in p]
        for a in axes:
            q[idx[a]] = n[a] - 1 - q[idx[a]]
        return q

    def offset(p):
        if p is None:
            return p
        q = [float(v) for v in p]
        for a in axes:
            q[idx[a]] = -q[idx[a]]
        return q

    def span(ab, a):
        lo, hi = int(ab[0]), int(ab[1])
        return [n[a] - 1 - hi, n[a] - 1 - lo]
    for a in spec.get("annotations") or []:
        for anc in [a.get("anchor"), a.get("pin")] + list(a.get("more") or []):
            if isinstance(anc, dict) and anc.get("xyz") is not None:
                anc["xyz"] = pt(anc["xyz"])
    for v in [spec.get("view")] + [p.get("view") for p in spec.get("shots", [])]:
        if isinstance(v, dict) and v.get("pan") is not None:
            v["pan"] = offset(v["pan"])
    for p in spec.get("shots", []):
        r = p.get("region")
        if isinstance(r, dict) and r.get("lo") is not None and r.get("hi") is not None:
            lo, hi = pt(r["lo"]), pt(r["hi"])
            r["lo"], r["hi"] = [min(u, w) for u, w in zip(lo, hi)], [max(u, w) for u, w in zip(lo, hi)]
        for w in p.get("waypoints") or []:
            for key in ("point", "pos", "focal"):
                if isinstance(w, dict) and w.get(key) is not None:
                    w[key] = pt(w[key])
        if p.get("type") == "slide" and "z" in axes:
            for key in ("from", "to"):
                v = p.get(key)
                if isinstance(v, int) and not isinstance(v, bool):  # a chosen slice stays the same slice ("first" and
                    p[key] = nz - 1 - v                                 # "last" stay the bottom and the top)
    c = spec.get("clip")
    if isinstance(c, dict):
        for a in axes:
            if c.get(a):
                c[a] = span(c[a], a)
    if "z" in axes and spec.get("z_range"):
        spec["z_range"] = span(spec["z_range"], "z")
    return spec


def bin_spec(spec, r, shape=None):
    """Points, boxes, slice numbers and sizes in the settings scaled by r when the volume is shown at another size
    ("Open smaller", 3.6; r = old bin / new bin, so 0.5 going from full to half size). Like flip_spec, the settings then
    still mean the same places. shape: the new grid (nz, ny, nx), to keep numbers inside it."""
    if r == 1:
        return spec
    top = None if shape is None else {"x": shape[2] - 1, "y": shape[1] - 1, "z": shape[0] - 1}

    def pt(p):
        return None if p is None else [float(v) * r for v in p]

    def one(v, a):
        v = int(round(int(v) * r))
        return max(0, min(top[a], v)) if top else max(0, v)
    for a in spec.get("annotations") or []:
        for anc in [a.get("anchor"), a.get("pin")] + list(a.get("more") or []):
            if isinstance(anc, dict) and anc.get("xyz") is not None:
                anc["xyz"] = pt(anc["xyz"])
    for v in [spec.get("view")] + [p.get("view") for p in spec.get("shots", [])]:
        if isinstance(v, dict) and v.get("pan") is not None:
            v["pan"] = pt(v["pan"])
    for p in spec.get("shots", []):
        reg = p.get("region")
        if isinstance(reg, dict) and reg.get("lo") is not None and reg.get("hi") is not None:
            reg["lo"], reg["hi"] = pt(reg["lo"]), pt(reg["hi"])
        for w in p.get("waypoints") or []:
            for key in ("point", "pos", "focal"):
                if isinstance(w, dict) and w.get(key) is not None:
                    w[key] = pt(w[key])
        cam = p.get("camera")
        if p.get("type") == "path" and isinstance(cam, dict) and cam.get("distance") is not None:
            cam["distance"] = float(cam["distance"]) * r  # in units of 60 voxels: the same distance in nm
        if p.get("type") == "slide":
            for key in ("from", "to"):
                v = p.get(key)
                if isinstance(v, int) and not isinstance(v, bool):
                    p[key] = one(v, "z")
    c = spec.get("clip")
    if isinstance(c, dict):
        for a in "xyz":
            if c.get(a):
                c[a] = [one(c[a][0], a), one(c[a][1], a)]
    if spec.get("z_range"):
        spec["z_range"] = [one(spec["z_range"][0], "z"), one(spec["z_range"][1], "z")]
    for cl in (spec.get("classes") or {}).values():  # sizes in voxels: the same objects are left out
        if isinstance(cl, dict) and cl.get("min_object_voxels"):
            cl["min_object_voxels"] = max(1, int(round(float(cl["min_object_voxels"]) * r ** 3)))
    return spec


def focus_box(part, ds, pad=True):
    """The box a close-up frames: the drawn region when there is one, else the box of the objects in focus."""
    r = part_region(part)
    if r is not None:
        return r
    return ds.targets_box(part.get("target") or {}, pad=pad)


def make_target(items):
    """A close-up target from [(structure key, component or None)]."""
    return {"items": [{"structure": k, "component": None if c is None else int(c)} for k, c in items]}


OTHER_COLOURS = ("keep", "grey", "darken")


def others_of(v):
    """(colour, opacity) of the other structures during a close-up. Older settings: grey = (grey, 1), hide = (keep, 0),
    keep = (keep, 1). {"colour": "hide"} (3.5) hides them; its opacity is kept for when another colour is chosen."""
    if isinstance(v, dict):
        c = v.get("colour", "grey")
        if c == "hide":
            return "keep", 0.0
        return (c if c in OTHER_COLOURS else "grey"), float(min(1.0, max(0.0, v.get("opacity", 1.0))))
    return {"grey": ("grey", 1.0), "hide": ("keep", 0.0), "keep": ("keep", 1.0)}.get(v, ("grey", 1.0))


def others_choice(v):
    """(choice shown in the window: keep / grey / darken / hide, opacity shown) of the other structures."""
    if isinstance(v, dict):
        c = v.get("colour", "grey")
        return (c if c in OTHER_COLOURS + ("hide",) else "grey"), float(min(1.0, max(0.0, v.get("opacity", 1.0))))
    c, o = others_of(v)
    return ("hide", 1.0) if o <= 0 else (c, o)


def set_others_choice(v, colour=None, opacity=None):
    """New others setting from the window's colour choice and/or opacity."""
    c, o = others_choice(v)
    return dict(colour=c if colour is None else colour, opacity=o if opacity is None else float(opacity))


EACH_CHOICES = ("glass", "hide")  # per structure in one close-up (3.5): see-through, hidden


def each_of(part):
    """{structure key: "glass" | "hide"} of a close-up: structures drawn see-through or hidden in this part only."""
    return {k: v for k, v in (part.get("each") or {}).items() if v in EACH_CHOICES}


HIDE_FADE = 0.5  # seconds a structure hidden in a part (3.6) takes to fade out and back in


def hidden_in(part):
    """Structure keys hidden in this part (3.6: any kind of part; close-ups also through their own `each`)."""
    return {k for k, v in each_of(part).items() if v == "hide"}


def part_visibility(spec, frames, fps):
    """3.6: frames get "vis" = {structure key: 0..1 hidden} when some part other than a close-up hides structures.
    Each structure fades over HIDE_FADE s around the change (half before, half after); card frames keep the value
    before them. Plans without such parts are unchanged (no "vis" key)."""
    shots = [p for p in spec.get("shots", []) if p.get("on", True)]
    if not any(hidden_in(p) for p in shots if p.get("type") != "focus"):
        return
    own = {p["id"]: hidden_in(p) for p in shots}
    keys = sorted(set().union(*own.values()))
    target = np.zeros((len(frames), len(keys)))
    prev = None
    for i, f in enumerate(frames):
        if f.get("card") and prev is not None:
            target[i] = prev
        else:
            h = own.get(f.get("part"), set())
            target[i] = [1.0 if k in h else 0.0 for k in keys]
        prev = target[i]
    n = max(1, int(round(HIDE_FADE * fps)))
    if n > 1 and len(frames) > 1:  # moving average: a linear ramp of n frames centred on each change
        pad = np.concatenate([np.repeat(target[:1], n, 0), target, np.repeat(target[-1:], n, 0)])
        c = np.cumsum(np.vstack([np.zeros((1, len(keys))), pad]), 0)
        lo = np.arange(len(frames)) + n - n // 2
        target = (c[lo + n] - c[lo]) / n
    for i, f in enumerate(frames):
        v = {k: round(float(q * q * (3 - 2 * q)), 4) for k, q in zip(keys, target[i]) if q > 1e-6}
        if v:
            f["vis"] = v


def closeup_others(part):
    """The others setting a close-up's frames carry. Without per-structure choices it is the part's own setting
    (old projects draw exactly as before); with them, a dict that also holds "each"."""
    m = part.get("others", "grey"); each = each_of(part)
    if not each:
        return m
    c, o = others_choice(m)
    return dict(colour=c, opacity=o, each=each)


def closeup_caption(items, name_of):
    """Default caption: the structure's name for one item; "A and B" for two structures; "N objects" otherwise."""
    if not items:
        return ""
    keys = list(dict.fromkeys(k for k, _ in items))
    if len(items) == 1:
        return name_of(items[0][0])
    if len(keys) == 2 and all(c is None for _, c in items):
        return f"{name_of(keys[0])} and {name_of(keys[1])}"
    if len(keys) == 1:
        return f"{len(items)} {name_of(keys[0])} objects"
    return f"{len(items)} objects"


def tomogram_setting(mode="default", opacity=0.4, fade=0.5):
    """How a shot shows the tomogram slice: as the default (Look tab), shown, faded to `opacity`, or hidden;
    the change happens over `fade` seconds at the start of the shot."""
    return dict(mode=mode, opacity=opacity, fade=fade)


def shot_tomogram(shot):
    """A shot's tomogram setting, also reading the older close-up option slice = "keep" / "hide"."""
    t = shot.get("tomogram")
    if isinstance(t, dict) and t.get("mode") in TOMOGRAM_MODES:
        return dict(tomogram_setting(), **t)
    return tomogram_setting("hidden" if shot.get("slice") == "hide" else "default")


def default_opacity(spec, has_tomo=True):
    """Tomogram slice opacity for the whole movie and the preview (Look tab)."""
    if not has_tomo:
        return 0.0
    t = spec.get("style", {}).get("tomogram") or {}
    return float(min(1.0, max(0.0, t.get("opacity", 1.0)))) if t.get("show", True) else 0.0


# ---------------------------------------------------------------------------------------------------------------
# Movie parts. spec["shots"] is the ordered list of parts; each has an "id" and a "type":
#   slide     the slice moves from `from` to `to` ("first", "last", "current" or a slice number), showing structures
#             "none", "above" (the slice), "all" or "keep"
#   overview  the camera shows the whole (or clipped) volume: motion rock / spin / none
#   focus     a close-up of a structure or of one object
#   hold      nothing moves
# Never address a part by its position in the list: use its id.
# ---------------------------------------------------------------------------------------------------------------
PART_TYPES = ("slide", "overview", "focus", "hold", "card", "path")
PRESETS = ("slide_up", "reveal", "overview", "view", "focus", "hold", "title_card", "end_card", "card", "path")
CARD_TRANSITIONS = ("fade", "dissolve", "cut")


def text_block(text, role="text", size=1.2, x=0.5, y=0.5, anchor="middle-center", color="#ffffff", bold=False, align="center"):
    return dict(kind="text", text=text, role=role, size=size, x=x, y=y, anchor=anchor, color=color, bold=bold, align=align)


def image_block(path, height=0.12, x=0.97, y=0.95, anchor="bottom-right"):
    return dict(kind="image", path=path, height=height, x=x, y=y, anchor=anchor)
VIEW_TRANSITION = 1.5  # seconds of camera flight into a "View from here" part
CLOSEUP_TRANSITION = 1.3  # seconds of camera flight into and out of a close-up
OTHER_TRANSITION = 0.5
FLIGHT_PER_DEGREE = 0.7 / 90  # 3.6 auto_flight: seconds added per degree the camera turns (90° -> about 1.2 s)
FLIGHT_MAX = 2.0


def auto_flight_seconds(a, b):
    """Seconds of the automatic flight between two poses (3.6): longer when the camera turns more."""
    d_az = abs(((b["az"] - a["az"] + 180.0) % 360.0) - 180.0); d_t = abs(b["tilt"] - a["tilt"])
    return min(FLIGHT_MAX, OTHER_TRANSITION + FLIGHT_PER_DEGREE * math.hypot(d_az, d_t))


def with_start_ease(fn, part):
    """A part's own motion starting gently (3.6 "ease_start"): speed 0 at its first frame; unchanged without it."""
    how = (part or {}).get("ease_start", "sudden")
    if how not in ("gentle", "very_gentle"):
        return fn
    return lambda u, fn=fn, how=how: fn(ease_curve(u, how, "sudden"))


def new_id():
    return uuid.uuid4().hex[:8]


def _common(**kw):
    d = dict(id=new_id(), on=True, seconds=5, caption="", tomogram=tomogram_setting(), transition=None,
             pause_before=0.0, pause_after=0.0)
    d.update(kw)
    return d


def new_part(kind, **fields):
    """A part with defaults and a fresh id. kind: a preset (slide_up, reveal, overview, focus, hold) or a type."""
    if kind == "slide_up":
        p = _common(type="slide", seconds=7, pause_before=0.5, pause_after=0.25, structures="none", **{"from": "first", "to": "last"})
    elif kind == "reveal":
        p = _common(type="slide", seconds=7, pause_after=0.5, structures="above", **{"from": "last", "to": "first"})
    elif kind == "slide":
        p = _common(type="slide", seconds=7, structures="keep", **{"from": "current", "to": "last"})
    elif kind == "overview":
        p = _common(type="overview", seconds=6, motion="none", angle=25, view=None)  # still (3.5; was rock)
    elif kind == "view":  # "View from here": a still overview with its own camera position (pass view={tilt, az, zoom})
        p = _common(type="overview", seconds=3, motion="none", angle=25, view=dict(tilt=42.0, az=0.0, zoom=1.0),
                    transition=VIEW_TRANSITION)
    elif kind == "focus":
        p = _common(type="focus", seconds=5, target=None, others="grey", motion="orbit", zoom=1.0, angle=30, tilt=0.0,  # from the top (3.5)
                    steady_zoom=True, wiggle=dict(WIGGLE_3_5))
    elif kind == "hold":
        p = _common(type="hold", seconds=2)
    elif kind == "path":
        p = _common(type="path", seconds=10, source=dict(kind="follow", items=[]), waypoints=[], camera=dict(PATH_CAMERA),
                    speed="even", ease=True, reverse=False, others=dict(colour="keep", opacity=1.0), show_path_in_preview=True,
                    steadiness=50.0, interp=2)  # 3.5: a steady rail; points: even speed, zoom and field of view per point
    elif kind in ("card", "title_card", "end_card"):
        title = fields.pop("title", None)
        if kind == "title_card":
            blocks = [text_block(title or "Title", "title", 2.6, 0.5, 0.42, bold=True), text_block("", "subtitle", 1.2, 0.5, 0.55)]
        elif kind == "end_card":
            blocks = [text_block(title or "Thank you", "title", 2.6, 0.5, 0.42, bold=True),
                      text_block("", "subtitle", 1.0, 0.5, 0.56)]
        else:
            blocks = []
        p = _common(type="card", seconds=4, background=dict(kind="color", color="#000000", color2="#1c1f26", image=None),
                    blocks=blocks, fade_in=0.6, fade_out=0.6, transition_to_next="fade")
    else:
        raise ValueError(f"unknown kind of part: {kind}")
    if p["type"] in ("slide", "overview", "focus"):
        p["ease_end"] = "gentle"  # 3.5: new parts slow down at their end; older parts (no key) stop as before
    if p["type"] == "overview":  # 3.6
        p.update(steady_zoom=True, centre="shown", ease_start="gentle", auto_flight=True)
    p.update(fields)
    return p


# Settings that 3.5 gives new parts, and that parts saved before 3.5 lack (missing = the old behaviour).
NEW_IN_3_5 = {"focus": ("tilt", "ease_end", "steady_zoom", "wiggle", "each"), "slide": ("ease_end",), "overview": ("ease_end",),
              "path": ("steadiness", "interp")}
# 3.6 (PLAN-3.6 phase 1): overviews keep one size while turning, turn around the middle of what is shown, start gently,
# and fly longer into a large turn. Missing = the 3.5 behaviour.
NEW_IN_3_6 = {"overview": ("steady_zoom", "centre", "ease_start", "auto_flight")}
WIGGLE_3_5 = dict(amplitude=5.0, frequency=0.35, shape="circle", smooth=True)  # calmer, and it starts and ends at rest


def as_legacy(part):
    """A part as a project saved before 3.5 would have it: the 3.5 settings of new parts removed (tests, golden data)."""
    p = copy.deepcopy(part)
    for k in NEW_IN_3_5.get(p.get("type"), ()) + NEW_IN_3_6.get(p.get("type"), ()):
        p.pop(k, None)
    return p


def default_parts(rock=True):
    """Slide up, slide back revealing the segmentation, overview: the default movie. The command line keeps the
    rocking overview of every earlier version; the window asks for a still one (rock=False)."""
    parts = [new_part("slide_up"), new_part("reveal"), new_part("overview", motion="rock" if rock else "none")]
    if rock:  # the command line's default movie stays as in 3.5 (PLAN-3.6: default_spec unchanged)
        for p in parts:
            for k in NEW_IN_3_6.get(p["type"], ()):
                p.pop(k, None)
    return parts


def focus_shot(target, caption, **kw):
    """A close-up part (kept under its 3.1 name)."""
    return new_part("focus", target=target, caption=caption, **kw)


def migrate_spec(spec):
    """Bring older settings up to date, in place (idempotent): ids for every part, sweep / reveal as slides,
    the 3.1 close-up option slice keep/hide as the tomogram setting. Returns spec."""
    seen = set()
    for i, p in enumerate(spec.get("shots", [])):
        t = p.get("type")
        if t == "sweep":
            base = new_part("slide_up"); base.pop("id")
            for k, v in base.items():
                if k not in ("on", "seconds", "tomogram", "caption", "ease_end"):  # an old part: no 3.5 settings
                    p[k] = v
            p["type"] = "slide"
        elif t == "reveal":
            base = new_part("reveal"); base.pop("id")
            for k, v in base.items():
                if k not in ("on", "seconds", "tomogram", "caption", "ease_end"):  # an old part: no 3.5 settings
                    p[k] = v
            p["type"] = "slide"
        if p.get("type") not in PART_TYPES:
            raise UserError(f"part {i + 1} of the movie has " + (f"an unknown type ({p['type']!r})" if p.get("type") else "no type"))
        if not p.get("id") or p["id"] in seen:
            p["id"] = new_id()
        seen.add(p["id"])
        p.setdefault("on", True); p.setdefault("caption", ""); p.setdefault("transition", None)
        p.setdefault("pause_before", 0.0); p.setdefault("pause_after", 0.0)
        if "tomogram" not in p or not isinstance(p.get("tomogram"), dict):
            p["tomogram"] = shot_tomogram(p)
        p.pop("slice", None)
        if p["type"] == "overview":
            p.setdefault("motion", "rock"); p.setdefault("angle", 25); p.setdefault("view", None)
        elif p["type"] == "focus":
            p.setdefault("others", "grey"); p.setdefault("motion", "orbit"); p.setdefault("zoom", 1.0); p.setdefault("angle", 30)
        elif p["type"] == "slide":
            p.setdefault("from", "first"); p.setdefault("to", "last"); p.setdefault("structures", "keep")
        elif p["type"] == "path":
            p.setdefault("source", dict(kind="follow", items=[])); p.setdefault("waypoints", [])
            p["camera"] = dict(PATH_CAMERA, **(p.get("camera") or {})); p.setdefault("speed", "even"); p.setdefault("reverse", False)
            p.setdefault("others", dict(colour="keep", opacity=1.0)); p.setdefault("show_path_in_preview", True)
        elif p["type"] == "card":
            p.setdefault("background", dict(kind="color", color="#000000", color2="#1c1f26", image=None)); p.setdefault("blocks", [])
            p.setdefault("fade_in", 0.6); p.setdefault("fade_out", 0.6); p.setdefault("transition_to_next", "fade")
    return spec


def default_spec(ds):
    return dict(
        classes={c["key"]: dict(name=c["name"], color=c["color"], show=not (c["background"] or c.get("hidden"))) for c in ds.classes.values()},
        view=dict(tilt=42, az=0, zoom=1.0),
        style=dict(bg="#000000", legend=True, slice_label=True, title="", contrast=[1, 99], invert=False, ortho=False, fov=30,
                   tomogram=dict(show=True, opacity=1.0)),
        shots=default_parts(),
        return_to_overview=True, z_range=None, clip=None,
        output=dict(size=[1920, 1080], fps=30, aa=True, formats=["mp4"], codec="h264", file_size="balanced"))


def merge_settings(spec, new, same_grid=True):
    """Keep the user's settings (spec) when what is loaded changes; new is default_spec() of the new data.
    Every structure that is still there keeps its name, colour and visibility (they are keyed by its stable key);
    new structures get their defaults. Close-ups of structures that are gone stay in the movie, marked and skipped,
    until the structure is back."""
    old = spec.get("classes") or {}
    spec["classes"] = {k: old.get(k) or v for k, v in new["classes"].items()}
    for k, v in old.items():  # settings of structures that are not loaded now are kept, for when they come back
        spec["classes"].setdefault(k, v)
    migrate_spec(spec)
    if not same_grid:  # the slice range and the clip box belonged to the old grid
        spec["clip"] = None; spec["z_range"] = None
        for p in spec["shots"]:
            for end in ("from", "to"):
                if isinstance(p.get(end), int):
                    p[end] = "first" if end == "from" else "last"
    return spec


def part_name(p, spec=None, ds=None):
    """Plain-language name of a part, as shown in the window."""
    t = p.get("type")
    if t == "slide":
        f, to, s = p.get("from"), p.get("to"), p.get("structures")
        if (f, to, s) == ("first", "last", "none"):
            return "Slide up through the slices"
        if (f, to, s) == ("last", "first", "above"):
            return "Slide back, revealing the segmentation"
        word = {"first": "first", "last": "last", "current": "here"}
        what = {"none": "no structures", "above": "structures above", "all": "all structures", "keep": ""}[s or "keep"]
        txt = f"Slide: {word.get(f, f'slice {f + 1}' if isinstance(f, int) else f)} → {word.get(to, f'slice {to + 1}' if isinstance(to, int) else to)}"
        return txt + (f" ({what})" if what else "")
    if t == "overview":
        if p.get("view"):
            return "View from here"
        return f"Overview ({ {'rock': 'rock', 'spin': 'full spin', 'none': 'still'}.get(p.get('motion'), p.get('motion')) })"
    if t == "focus":
        items = target_items(p.get("target"))
        if part_region(p) is not None and not items:
            return f"Close-up: {p.get('caption') or 'framed region'}"
        names = []
        if spec is not None and ds is not None:
            for key, _ in items:
                k = ds.id_of(key)
                if k is not None:
                    names.append(class_spec(spec, ds, k).get("name", ds.classes[k]["name"]))
        if not names:
            return f"Close-up: {p.get('caption') or 'structure'}"
        if len(items) > 1:
            return f"Close-up: {closeup_caption(items, lambda key: names[[k for k, _ in items].index(key)])}"
        return f"Close-up: one object of {names[0]}" if items[0][1] is not None else f"Close-up: {names[0]}"
    if t == "hold":
        return "Hold"
    if t == "path":
        src = p.get("source") or {}
        if src.get("kind") == "points":
            n = len(p.get("waypoints") or [])
            return f"Camera path through {n} point{'s' if n != 1 else ''}"
        names = []
        if spec is not None and ds is not None:
            for key, _ in [(it.get("structure"), it.get("component")) for it in src.get("items", [])]:
                k = ds.id_of(key)
                if k is not None:
                    names.append(class_spec(spec, ds, k).get("name", ds.classes[k]["name"]))
        return f"Camera path along {' and '.join(dict.fromkeys(names))}" if names else "Camera path"
    if t == "card":
        first = next((b.get("text", "").strip().splitlines()[0] for b in p.get("blocks", []) if b.get("kind") == "text" and b.get("text", "").strip()), "")
        return f"Card: {first}" if first else "Card (empty)"
    return str(t)


def _mark_label(p):
    t = p.get("type")
    if t == "slide":
        f, to, s = p.get("from"), p.get("to"), p.get("structures")
        return "Slices" if s == "none" else "Reveal" if s in ("above", "all") and (f, to) == ("last", "first") else "Slide"
    return {"overview": "Overview", "hold": "Hold", "card": "Card", "path": "Path"}.get(t) or p.get("caption") or "Focus"


def _pause_frames(fps, seconds):
    """Frames of a pause; halves round down (0.25 s at 30 fps is 7 frames, as in 3.1)."""
    s = float(seconds or 0)
    return 0 if s <= 0 else max(1, math.ceil(fps * s - 0.5))


class Plan:
    """The movie as frames: frames (one state each), segments (which frames belong to which part), marks, fps,
    problems ({part id: plain-language reason it is skipped})."""
    def __init__(self, frames, segments, marks, fps, problems):
        self.frames, self.segments, self.marks, self.fps, self.problems = frames, segments, marks, fps, problems

    def __len__(self):
        return len(self.frames)


def build_plan(spec, ds):
    """Settings -> Plan. Parts may come in any order; each starts where the previous one ended, and a transition is
    inserted when the camera, the slice or the close-up emphasis must change first."""
    migrate_spec(spec)
    out = spec.get("output", {}); fps = int(out.get("fps", 30)); w, h = out.get("size", [1920, 1080])
    style, v = spec.get("style", {}), spec.get("view", {})
    shape = ds.shape; nz, ny, nx = shape
    has_tomo, has_seg = ds.tomo is not None, ds.has_seg()
    base = default_opacity(spec, has_tomo)  # tomogram slice opacity when no part asks for another
    zr = spec.get("z_range") or [0, nz - 1]
    z0, z1 = max(0, int(zr[0])), min(nz - 1, int(zr[1]))
    if z1 < z0:
        z0, z1 = 0, nz - 1
    fr = Framer(shape, w / h, float(style.get("fov", 30)), bool(style.get("ortho", False)))
    lo, hi = np.zeros(3), np.array([nx - 1, ny - 1, nz - 1], float)
    cb = clip_box(spec, shape)
    if cb:  # slide only through the kept slices and, unless switched off, frame the kept region
        z0, z1 = max(z0, cb[0][2]), min(z1, cb[1][2])
        if z1 < z0:
            z0, z1 = cb[0][2], cb[1][2]
        if spec["clip"].get("fit", True):
            lo, hi = np.array(cb[0], float), np.array(cb[1], float)
    tilt, az, zoom = float(v.get("tilt", 42)), float(v.get("az", 0)), max(0.05, float(v.get("zoom", 1)))

    def zoomed(p, z):
        p = dict(p); p["dist"] /= z; p["scale"] /= z
        return p

    shown_c = (np.array(cb[0], float) + np.array(cb[1], float)) / 2 if cb else None  # middle of the kept part

    def ov(d=0.0, view=None, centre=None, steady=None):
        """Overview pose. centre (3.6): rotation centre instead of the framing box's middle; steady: (dist, scale)
        kept at every angle of a turn instead of refitting each frame."""
        t, a, zm = (tilt, az, zoom) if not view else (float(view.get("tilt", tilt)), float(view.get("az", az)),
                                                       max(0.05, float(view.get("zoom", zoom))))
        p = fr.fit(lo, hi, t, a + d, 1.07, centre)
        if steady is not None:
            p["dist"], p["scale"] = steady
        p = zoomed(p, zm)
        pan = view_pan(view if view else v)  # the rotation centre moved in the preview (3.5)
        if pan is not None:
            p["c"] = p["c"] + pan
        return p

    def target_of(part):
        if not has_tomo:
            return 0.0
        t = shot_tomogram(part)
        return {"default": base, "shown": 1.0, "hidden": 0.0}.get(t["mode"], float(min(1.0, max(0.0, t["opacity"]))))

    def slice_no(x, cur):
        if x == "first":
            return z0
        if x == "last":
            return z1
        if x == "current" or x is None:
            return cur
        return int(min(nz - 1, max(0, int(x))))

    frames, marks, segments, problems = [], [], [], {}
    st = dict(k=z0, reveal=False, cut=None, slice=base, e=0.0, target=None, others="grey", caption="", card=None)
    pose = [ov()]
    ramp = {}  # the tomogram fade at the start of the current part
    cur = dict(id=None, index=-1, type=None); shown = {}  # shown: the last part that drew frames

    def start_fade(part, fade=None):
        t = shot_tomogram(part); f = t["fade"] if fade is None else fade
        if fade is None and t.get("whole"):  # fade gently over the whole part (for example while the segmentation is revealed)
            f = float(part.get("seconds", 5)) + float(part.get("pause_before") or 0)
        ramp.clear(); ramp.update(start=len(frames), s0=st["slice"], s1=target_of(part), n=max(1, round(fps * max(0.0, float(f)))))

    last = {}   # pending dissolve / fade, waiting for the next part's first frames

    def emit(n, fn, phase, label=None, seg_phase=None, xb=None):
        n = max(1, int(n)); shown.update(cur)
        if last.get("blend") and phase in ("main", "pause") and xb is None:  # a dissolve or fade into this part (3.5)
            b = last.pop("blend"); nf = max(0, b["n"] - b["m2"])
            if nf > 0:
                emit(nf, lambda u, fn=fn: fn(0.0), "transition", label, xb=dict(b, offset=0)); label = None
            xb = dict(b, offset=nf)
        if label:
            marks.append(dict(frame=len(frames), label=label))
        sg = dict(part=cur["id"], index=cur["index"], type=cur["type"], phase=seg_phase or phase, start=len(frames), count=n)
        if (seg_phase or phase) == "transition" and cur["id"] is not None:
            sg["transition"] = f"{cur['id']}:in"  # selectable on the timeline (3.5)
        segments.append(sg)
        for i in range(n):
            s = dict(st); s["pose"] = pose[0]; s["shot"] = cur["index"]; s["part"] = cur["id"]; s["type"] = cur["type"]; s["phase"] = phase
            d = fn(i / (n - 1) if n > 1 else 1.0)
            if ramp and "slice" not in d:
                q = smooth01((len(frames) - ramp["start"] + 1) / ramp["n"])
                d["slice"] = ramp["s1"] if q >= 1 else ramp["s0"] + (ramp["s1"] - ramp["s0"]) * q
            s.update(d)
            if xb is not None:
                g = xb["offset"] + i
                if g < xb["n"]:
                    src = xb["src"][min(g, len(xb["src"]) - 1)]
                    if src is not None:
                        s["blend"] = dict(src=src, q=round((g + 1) / (xb["n"] + 1), 4), via=xb["via"])
            frames.append(s)
        lastf = frames[-1]
        for key in st:
            st[key] = lastf[key]
        pose[0] = lastf["pose"]

    def emit_main(n, fn, label, skip=0, part=None):
        """A part's own motion: slowed down at its end when asked (3.5), and starting `skip` frames in when the
        transition into it overlapped its start."""
        fn = with_end_ease(fn, part)
        skip = int(min(max(0, skip), n - 1))
        if skip:
            emit(n - skip, lambda u, fn=fn: fn((skip + u * (n - skip - 1)) / (n - 1) if n > 1 else 1.0), "main", label)
        else:
            emit(n, fn, "main", label)

    def need_structures():
        """Overviews and close-ups show structures: switch them all on if none are shown."""
        if not st["reveal"]:
            st.update(reveal=True, cut="all")

    def same_pose(a, b):
        return (np.allclose(a["c"], b["c"]) and abs(a["tilt"] - b["tilt"]) < 1e-6 and abs(((a["az"] - b["az"] + 180) % 360) - 180) < 1e-6
                and abs(a["dist"] - b["dist"]) < 1e-6)

    def take_previous(m):
        """Overlap with the previous part: take back its last m frames (they become the start of the transition)."""
        if m <= 0 or not segments or not frames:
            return []
        sg = segments[-1]
        if sg["phase"] not in ("main", "pause") or sg["part"] == cur["id"]:
            return []
        m = min(m, sg["count"] - 1)
        if m <= 0:
            return []
        out = frames[-m:]; del frames[-m:]; sg["count"] -= m
        return out

    def flight_pose(i, n, q, p0, p1, A, m2, nxt, tr):
        a = A[i]["pose"] if i < len(A) else (A[-1]["pose"] if A else p0)
        b = p1
        if m2 > 1:  # the next part's own motion starts with the flight, slowly, and reaches its own speed as the flight
            k = (n - 1) / (m2 - 1)  # ends: its clock runs as (m2 - 1)·x^k, whose rate grows from 0 to exactly 1 frame per frame
            b = nxt((m2 - 1) * (i / (n - 1)) ** k)["pose"]
        # the turn: the shorter way from where we leave to where the next part starts, plus however far that part has
        # turned on its own since (a spin can turn more than half a circle during the overlap)
        p = lerp_pose(a, b, q, d_az=(((p1["az"] - a["az"] + 180.0) % 360.0) - 180.0) + (b["az"] - p1["az"]), fov=fr.fov)
        if tr["path"] == "arc":  # fly a little higher in the middle
            da, ua = fr.axes(a["tilt"], a["az"]); db, _ = fr.axes(b["tilt"], b["az"])
            span = float(np.linalg.norm((a["c"] + a["dist"] * da) - (b["c"] + b["dist"] * db)))
            p = dict(p, c=p["c"] + np.asarray(ua) * 0.15 * span * math.sin(math.pi * q))
        return p

    def go_to(p1, k1, seconds, slice_to=None, label=None, tr=None, nxt=None, nn=0):
        """Transition: fly the camera to p1 and glide the slice to k1; the close-up emphasis fades out. tr: the
        transition settings of the part (3.5: easing at each end, overlaps, kind, arc); nxt(j): the next part's main
        frame j (nn frames), for an overlap with its start. Returns the frames of the next part already shown."""
        tr = tr or transition_of({})
        p0, k0, e0, s0 = pose[0], st["k"], st["e"], st["slice"]
        n = round(fps * max(0.0, seconds))
        m1 = round(fps * tr["overlap_prev"]); m2 = min(round(fps * tr["overlap_next"]), n, max(0, nn - 1)) if nxt else 0
        if n <= 0 or tr["kind"] == "cut":  # a hard cut
            st.update(k=k1, e=0.0, target=None, caption=""); pose[0] = p1
            if slice_to is not None:
                st["slice"] = slice_to
            return 0
        if tr["kind"] in ("dissolve", "black", "white"):  # blended in 2D with the next part's first frames
            A = take_previous(min(m1, n))
            src = [dict(f) for f in A] if A else ([dict(frames[-1])] if frames else [None])
            st.update(k=k1, e=0.0, target=None, caption=""); pose[0] = p1
            if slice_to is not None:
                st["slice"] = slice_to
            last["blend"] = dict(n=n, m2=m2, src=src, via=tr["kind"])
            return 0
        if same_pose(p0, p1) and k0 == k1 and e0 <= 0 and not m1 and not m2:
            return 0
        A = take_previous(min(m1, n))
        if A:
            k0, e0 = A[0]["k"], A[0]["e"]

        def fn(u):
            i = int(round(u * (n - 1))) if n > 1 else 0
            q = ease_curve(u, tr["ease_out_prev"], tr["ease_in_next"])
            ka = A[i]["k"] if i < len(A) else (A[-1]["k"] if A else k0)
            kb = nxt((m2 - 1) * (i / (n - 1)) ** ((n - 1) / (m2 - 1))).get("k", k1) if m2 > 1 else k1
            d = dict(pose=flight_pose(i, n, q, p0, p1, A, m2, nxt, tr), k=int(round(ka + (kb - ka) * q)), e=e0 * (1 - q), caption="")
            if slice_to is not None:
                d["slice"] = s0 + (slice_to - s0) * q
            return d
        emit(n, fn, "transition", label)
        st.update(target=None, e=0.0); pose[0] = p1
        return m2

    shots = spec.get("shots", [])
    for si, part in enumerate(shots):
        if not part.get("on", True):
            continue
        typ = part.get("type"); n = max(1, round(fps * max(0.1, float(part.get("seconds", 5)))))
        cur.update(id=part.get("id"), index=si, type=typ)
        tr = transition_of(part)
        if typ == "slide":
            show = part.get("structures", "keep")
            if (not has_tomo and show == "none") or (not has_seg and show in ("above", "all")):
                continue  # nothing to see: no tomogram to slide through, or no segmentation to reveal
            start_fade(part)
            a, b = slice_no(part.get("from"), st["k"]), slice_no(part.get("to"), st["k"])
            t = part.get("transition"); back = st["e"] > 0; t = (CLOSEUP_TRANSITION if back else OTHER_TRANSITION) if t is None else float(t)
            slide = (lambda u, a=a, b=b: dict(k=a + int(round(u * (b - a)))))
            pb = _pause_frames(fps, part.get("pause_before"))
            slide_w = with_end_ease(slide, part)
            vw = part.get("view") or None  # a view of its own (3.5): for example one slice from the top, zoomed in
            skip = go_to(ov(0.0, vw), a, t, slice_to=(target_of(part) if back else None), tr=tr,
                         nxt=None if pb else (lambda j, f=slide_w, n=n: f(j / (n - 1) if n > 1 else 1.0)), nn=n)
            if back:
                start_fade(part, fade=0)  # the flight back from the close-up already faded the tomogram
            if show == "none":
                st.update(reveal=False, cut=None)
            elif show == "above":
                st.update(reveal=True, cut=None)
            elif show == "all":
                st.update(reveal=True, cut="all")
            st["k"] = a; st["caption"] = part.get("caption", "")
            label = _mark_label(part)
            if pb:
                emit(pb, lambda u: {}, "pause", label); label = None
            emit_main(n, slide, label, skip, part)
            pa = _pause_frames(fps, part.get("pause_after"))
            if pa:
                emit(pa, lambda u: {}, "pause")
        elif typ == "overview":
            start_fade(part)
            need_structures()
            vw = part.get("view") or None
            t = part.get("transition"); t = (CLOSEUP_TRANSITION if st["e"] > 0 else OTHER_TRANSITION) if t is None else float(t)
            back = st["e"] > 0
            auto_t = part.get("transition") is None and part.get("auto_flight") and not back
            m, ang = part.get("motion", "rock"), float(part.get("angle", 25))
            turn = (lambda u, m=m, ang=ang: ang * math.sin(2 * math.pi * u) if m == "rock" else 360.0 * u if m == "spin" else 0.0)
            axis = part.get("spin_axis", "z") if m in ("rock", "spin") else "z"
            # 3.6: turn around the middle of what is shown (also when the view is not fitted to the clip box)
            oc = shown_c if part.get("centre") == "shown" and shown_c is not None else None
            if axis in ("x", "y", "screen"):  # turning around another axis (3.5; the screen's vertical 3.6): poses worked out once
                uu = [i / (n - 1) if n > 1 else 1.0 for i in range(n)]
                tp = axis_turn_poses(fr, ov(0.0, vw, oc), axis, [turn(u) for u in uu], lo, hi, oc)
                main = (lambda u, tp=tp, n=n: dict(pose=tp[int(round(min(1.0, max(0.0, u)) * (n - 1)))]))
                start_pose = tp[0]
            else:
                steady = None
                if part.get("steady_zoom") and m in ("rock", "spin"):  # 3.6: one size for every angle of the turn
                    vt = float((vw or {}).get("tilt", tilt)); va = float((vw or {}).get("az", az))
                    angles = [va + turn(k / 72) for k in range(73)]
                    fits = [fr.fit(lo, hi, vt, a2, 1.07, oc) for a2 in angles]
                    steady = (max(f["dist"] for f in fits), max(f["scale"] for f in fits))
                main = (lambda u, turn=turn, vw=vw, oc=oc, steady=steady: dict(pose=ov(turn(u), vw, oc, steady)))
                start_pose = ov(0.0, vw, oc, steady)
            main = with_start_ease(main, part)  # 3.6: a rock or spin starts gently
            ep = part if m == "spin" else None  # "slow down at the end" suits a spin, not a rock (it already ends at rest)
            main_w = with_end_ease(main, ep)
            if auto_t:  # 3.6: a larger turn gets a longer flight (it was always 0.5 s)
                t = auto_flight_seconds(pose[0], start_pose)
            skip = go_to(start_pose, st["k"], t, slice_to=ramp["s1"] if back else None, tr=tr,
                         nxt=lambda j, f=main_w, n=n: f(j / (n - 1) if n > 1 else 1.0), nn=n)
            if back:
                start_fade(part, fade=0)  # the flight back from the close-up already faded the tomogram
            st["caption"] = part.get("caption", "")
            emit_main(n, main, _mark_label(part), skip, ep)
            pose[0] = start_pose
        elif typ == "focus":
            tgt = part.get("target") or {}
            try:
                blo, bhi = focus_box(part, ds)
            except (KeyError, IndexError, TypeError, ValueError):
                problems[part.get("id")] = "structure not loaded" if target_items(tgt) else "choose what to show: objects, or draw a frame"
                continue
            if target_items(tgt) and not any(class_spec(spec, ds, ds.id_of(key)).get("show", True) for key, _ in target_items(tgt)):  # every object is off
                problems[part.get("id")] = "structure switched off in the Structures panel"
                continue
            start_fade(part)
            need_structures()
            ft, fz = float(part.get("tilt", max(tilt, 50))), max(0.05, float(part.get("zoom", 1)))
            faz = float(part["view"]["az"]) if isinstance(part.get("view"), dict) and part["view"].get("az") is not None else az

            m, ang = part.get("motion", "orbit"), float(part.get("angle", 30))
            steady = steady_framing(part, fr, blo, bhi, ft, faz, m, ang) if part.get("steady_zoom") else None

            def fp(d=0.0, blo=blo, bhi=bhi, ft=ft, fz=fz, steady=steady, faz=faz):
                p = fr.fit(blo, bhi, ft, faz + d, 1.0)
                if steady is not None:  # the same distance at every angle: the object does not grow and shrink (3.5)
                    p["dist"], p["scale"] = steady
                return zoomed(p, fz)

            anim = closeup_animation(part, ds, fr, fp, n, fps, k0=st["k"], nz=nz)
            if anim is None:  # orbit / rock / still without effects: exactly as in 3.3
                anim = (lambda u, fp=fp, m=m, ang=ang: dict(pose=fp(360.0 * u if m == "orbit" else ang * math.sin(2 * math.pi * u) if m == "rock" else 0.0)))
            ep = part if m == "orbit" else None  # wiggle and rock have their own calm ends
            anim_w = with_end_ease(anim, ep)
            nxt = (lambda j, f=anim_w, n=n: f(j / (n - 1) if n > 1 else 1.0))
            t = part.get("transition"); t = CLOSEUP_TRANSITION if t is None else float(t)
            nt = round(fps * max(0.0, t))
            m2 = min(round(fps * tr["overlap_next"]), nt, n - 1) if tr["kind"] == "flight" else 0
            A = take_previous(min(round(fps * tr["overlap_prev"]), nt)) if tr["kind"] == "flight" and nt > 0 else []
            if A:
                st.update({k: A[0][k] for k in st}); pose[0] = A[0]["pose"]
            p0, p1, e0, old, oldmode, s0 = pose[0], fp(), st["e"], st["target"], st["others"], st["slice"]
            s1 = ramp["s1"]  # the fly-in is this part's tomogram fade
            mode, cap = closeup_others(part), part.get("caption", "")

            def fly(u, p0=p0, p1=p1, e0=e0, old=old, oldmode=oldmode, s0=s0, s1=s1, tgt=tgt, mode=mode, cap=cap, A=A, m2=m2, nt=nt, nxt=nxt, tr=tr):
                q = ease_curve(u, tr["ease_out_prev"], tr["ease_in_next"]); i = int(round(u * (nt - 1))) if nt > 1 else 0
                d = dict(pose=flight_pose(i, nt, q, p0, p1, A, m2, nxt, tr) if (A or m2 or tr["path"] == "arc") else lerp_pose(p0, p1, q, fov=fr.fov),
                         slice=s0 + (s1 - s0) * q)
                if e0 > 0 and old is not None:  # hand over from the previous close-up
                    if u < 0.5:
                        d.update(e=e0 * (1 - smooth01(2 * u)), target=old, others=oldmode, caption="")
                    else:
                        d.update(e=smooth01(2 * u - 1), target=tgt, others=mode, caption=cap if u > 0.8 else "")
                else:
                    d.update(e=q, target=tgt, others=mode, caption=cap if q > 0.6 else "")
                return d

            if (part.get("effect") or {}).get("kind") == "grow":  # the objects disappear on the way in, then grow in
                fly = (lambda u, f0=fly: dict(f0(u), fx={"fade": 1.0 - smooth01(u)}))
            if nt > 0 and tr["kind"] == "flight":
                emit(nt, fly, "transition", cap or "Focus")
                label = None
            else:
                if nt > 0 and tr["kind"] in ("dissolve", "black", "white"):
                    last["blend"] = dict(n=nt, m2=0, src=[dict(frames[-1])] if frames else [None], via=tr["kind"])
                st.update(fly(1.0)); pose[0] = p1; label = cap or "Focus"
                m2 = 0
            start_fade(part, fade=0)  # the fly-in already faded the tomogram
            emit_main(n, anim, label, m2, ep)
            pose[0] = fp()
        elif typ == "hold":
            start_fade(part)
            vw = part.get("view") or None
            if vw:  # a view of its own (3.5): the camera flies there first
                t = part.get("transition"); t = (CLOSEUP_TRANSITION if st["e"] > 0 else OTHER_TRANSITION) if t is None else float(t)
                go_to(ov(0.0, vw), st["k"], t, tr=tr)
            st["caption"] = part.get("caption", "")
            emit(n, lambda u: {}, "main", _mark_label(part))
        elif typ == "path":
            try:
                poses = path_poses(part, ds, fr, spec, n, fps, cb)
            except PathPending:
                problems[part.get("id")] = "working out the path ..."
                continue
            except UserError as exc:
                problems[part.get("id")] = str(exc)
                continue
            start_fade(part)
            need_structures()
            t = part.get("transition"); t = (CLOSEUP_TRANSITION if st["e"] > 0 else OTHER_TRANSITION) if t is None else float(t)
            m = len(poses)
            skip = go_to(poses[0], st["k"], t, tr=tr, nxt=lambda j, poses=poses: dict(pose=poses[int(round(j))]), nn=m)
            items = [(it.get("structure"), it.get("component")) for it in (part.get("source") or {}).get("items", [])]
            oc, oo = others_of(part.get("others"))
            if (part.get("source") or {}).get("kind", "follow") == "follow" and items and (oc, oo) != ("keep", 1.0):
                st.update(target=make_target(items), others=dict(colour=oc, opacity=oo), e=1.0)
            st["caption"] = part.get("caption", "")
            emit_main(m, lambda u, poses=poses, m=m: dict(pose=poses[int(round(u * (m - 1)))]), _mark_label(part), skip, None)
            pose[0] = poses[-1]
            if st["e"] > 0:
                st.update(e=0.0, target=None)
        elif typ == "card":
            ramp.clear(); last.pop("blend", None)
            fi = max(0, round(fps * float(part.get("fade_in", 0.6) or 0))); fo = max(0, round(fps * float(part.get("fade_out", 0.6) or 0)))
            how = part.get("transition_to_next", "fade")
            fo = 0 if how == "cut" else fo
            pid = part.get("id"); cap = part.get("caption", "")

            def card(u, n=n, fi=fi, fo=fo, how=how, pid=pid, cap=cap):
                i = int(round(u * (n - 1))) if n > 1 else 0
                a = 1.0
                if fi and i < fi:
                    a = (i + 1) / (fi + 1)
                d = dict(card=pid, card_fade=1.0, dissolve=0.0, caption=cap)
                if fo and i >= n - fo:
                    q = (i - (n - fo) + 1) / (fo + 1)
                    if how == "dissolve":
                        d["dissolve"] = q
                    else:
                        a = min(a, 1 - q)
                d["card_fade"] = a
                return d
            emit(n, card, "main", _mark_label(part))
            st.update(card=None, card_fade=1.0, dissolve=0.0, caption="")
    if st["e"] > 0 and spec.get("return_to_overview", True):
        ramp.clear(); cur.update(shown, index=len(shots))  # the flight back belongs to the last part shown
        p0, e0, s0 = pose[0], st["e"], st["slice"]
        emit(round(fps * CLOSEUP_TRANSITION), lambda u: dict(pose=lerp_pose(p0, ov(), smooth01(u), fov=fr.fov), e=e0 * (1 - smooth01(u)),
                                                               slice=s0 + (base - s0) * smooth01(u), caption=""), "transition")
        st.update(target=None, e=0.0); pose[0] = ov()
        emit(max(1, round(fps * 0.5)), lambda u: {}, "pause")
    if not frames:
        cur.update(id=None, index=-1, type=None)
        if has_seg:
            need_structures()
        emit(1, lambda u: {}, "main")
    for i, f in enumerate(frames):  # a dissolve blends a card with the first frame after it
        if f.get("dissolve"):
            j = i + 1
            while j < len(frames) and frames[j].get("card") == f["card"]:
                j += 1
            f["dissolve_to"] = frames[j] if j < len(frames) else None
    from .overlays import add_bar_lengths
    add_bar_lengths(frames, spec, ds, w, h, fr.fov, fr.ortho)
    from .look import style35
    if style35(spec)["depth_fade"] != "off":  # the depth range of each part, from its first frame, so the fade never pumps
        mlo, mhi = main_box(spec, shape); first = {}
        for sg in segments:
            if sg["phase"] == "main" and sg["part"] is not None and sg["part"] not in first:
                first[sg["part"]] = sg["start"]
        ranges = {}
        for pid, j in first.items():
            part = next((q for q in spec.get("shots", []) if q.get("id") == pid), {})
            try:
                lo_, hi_ = focus_box(part, ds) if part.get("type") == "focus" else (mlo, mhi)
            except (KeyError, IndexError, TypeError, ValueError):
                lo_, hi_ = mlo, mhi
            p = frames[j]["pose"]
            cpos = np.asarray(p["pos"], float) if p.get("pos") is not None else np.asarray(p["c"]) + p["dist"] * np.asarray(fr.axes(p["tilt"], p["az"])[0])
            dvec = np.asarray(p["c"], float) - cpos; dvec /= np.linalg.norm(dvec) or 1.0
            corners = np.array([[x, y, z] for x in (lo_[0], hi_[0]) for y in (lo_[1], hi_[1]) for z in (lo_[2], hi_[2])])
            dd = (corners - cpos) @ dvec
            ranges[pid] = (float(max(1e-3, dd.min())), float(max(dd.max(), dd.min() + 1)))
        for f in frames:
            if f.get("part") in ranges:
                f["dr"] = ranges[f["part"]]
    cap_fade = float(((spec.get("overlays") or {}).get("caption") or {}).get("fade") or 0.0)
    if cap_fade > 0:  # captions fade in and out (3.5)
        caption_fades(frames, max(1, int(round(cap_fade * fps))))
    if any(isinstance(p.get("look"), dict) and p["look"] for p in spec.get("shots", [])):
        part_looks(spec, frames, fps)  # effects only in some parts (3.5)
    part_visibility(spec, frames, fps)  # structures hidden in some parts (3.6)
    if spec.get("annotations"):  # arrows and text boxes (3.5): opacity, growth and tail angle per frame
        from .annotations import plan_annotations
        plan_annotations(spec, ds, frames, segments, fr, fps, w / h)
    return Plan(frames, segments, marks, fps, problems)


def build_timeline(spec, ds):
    """Settings -> (list of per-frame states, (frame, label) marks for the scrubber). See build_plan."""
    plan = build_plan(spec, ds)
    return plan.frames, plan.marks


def part_at_frame(plan, frame):
    """Id of the part that owns a frame (a transition belongs to the part it leads into)."""
    for sg in plan.segments:
        if sg["start"] <= frame < sg["start"] + sg["count"]:
            return sg["part"]
    return None


def main_range(plan, part_id):
    """(first frame, number of frames) of a part's main phase, or None when it is not in the movie."""
    for sg in plan.segments:
        if sg["part"] == part_id and sg["phase"] == "main":
            return sg["start"], sg["count"]
    return None


# ---- editing (each returns the changed spec) ----
# ---------------------------------------------------------------------------------------------------------------
# Effects in some parts only (3.5): part["look"] = {setting: value} overrides the Look tab in that part. Missing (or
# no part with one) = the Look tab everywhere, as before. A change between parts is blended over LOOK_BLEND seconds.
# ---------------------------------------------------------------------------------------------------------------
def _part_styles():
    from .look import LOOKS
    return (("as in the Look tab", None),) + tuple((t, d) for t, d in LOOKS if d != "tomogram")


PART_LOOK = {  # setting -> choices (the first is "as in the Look tab", stored by leaving the setting out)
    "style": _part_styles(),  # a whole look for this part (its background stays the movie's)
    "shadow": (("as in the Look tab", None), ("none", "none"), ("light", "light"), ("medium", "medium"), ("strong", "strong")),
    "outline": (("as in the Look tab", None), ("off", "off"), ("thin", "thin"), ("medium", "medium"), ("thick", "thick")),
    "depth_fade": (("as in the Look tab", None), ("off", "off"), ("light", "light"), ("medium", "medium"), ("strong", "strong")),
    "membranes": (("as in the Look tab", None), ("their own colour", "own"), ("see-through (glass)", "glass"), ("solid colour", "cream")),
    "tomo_shadow": (("as in the Look tab", None), ("on", True), ("off", False)),
    # smoother membrane surfaces in this part (structures ticked "Treat as membrane"); the levels of volumes.SOFTEN
    "soften": (("as in the Structures panel", None), ("off", 0), ("a little", 1), ("more", 2), ("much", 3)),
}
LOOK_BLEND = 0.6


OUTLINE_ONLY = "outline_only"  # part["look"]["outline_only"] = [structure keys]: only these get an outline in this part


def part_look(part):
    """The Look settings this part changes: {setting: value} (only known settings with a real choice), plus
    "outline_only" (a list of structure keys) when the part outlines some structures only."""
    own = (part or {}).get("look") if isinstance((part or {}).get("look"), dict) else {}
    out = {k: v for k, v in own.items() if k in PART_LOOK and v is not None and any(v == d for _, d in PART_LOOK[k][1:])}
    only = own.get(OUTLINE_ONLY)
    if isinstance(only, list) and all(isinstance(x, str) for x in only):
        out[OUTLINE_ONLY] = sorted(set(only))
    return out


def part_looks(spec, frames, fps):
    """Each frame gets "lk" (its part's own look settings); the first frames after a change also get "lkfrom" (the
    settings before) and "lkq" (0..1), so the picture blends smoothly from one look into the next."""
    own = {p.get("id"): part_look(p) for p in spec.get("shots", [])}
    n = max(1, int(round(LOOK_BLEND * fps))); prev, since, start = None, None, 0
    for i, f in enumerate(frames):
        cur = own.get(f.get("part"), {}) if not f.get("card") else (prev or {})
        f["lk"] = cur
        if prev is not None and cur != prev:
            since, start = prev, i
        if since is not None:
            q = (i - start + 1) / (n + 1)
            if q < 1.0:
                f["lkfrom"] = since; f["lkq"] = round(q * q * (3 - 2 * q), 4)
            else:
                since = None
        prev = cur


def caption_fades(frames, n):
    """Each run of frames with the same caption fades it in over its first n frames and out over its last n (never
    more than a third of the run); frames get "cap_a" (0..1)."""
    i = 0
    while i < len(frames):
        j = i
        while j + 1 < len(frames) and frames[j + 1].get("caption", "") == frames[i].get("caption", ""):
            j += 1
        L = j - i + 1; m = max(1, min(n, L // 3))
        if frames[i].get("caption"):
            for k in range(i, j + 1):
                u = min((k - i + 1) / m, (j - k + 1) / m, 1.0)
                if u < 1.0:
                    frames[k]["cap_a"] = round(u * u * (3 - 2 * u), 4)
        i = j + 1


def state_key(s):
    p = s["pose"]
    oth = s["others"] if not isinstance(s["others"], dict) else json.dumps(s["others"], sort_keys=True)
    extra = (json.dumps(s["fx"], sort_keys=True, default=float),) if s.get("fx") else ()  # close-up animations (3.4)
    if s.get("ann"):  # arrows and text boxes (3.5)
        extra += (json.dumps(s["ann"], sort_keys=True),)
    if s.get("blend"):  # dissolves and fades between parts (3.5)
        extra += (s["blend"]["via"], s["blend"]["q"], state_key(s["blend"]["src"]))
    if s.get("cap_a") is not None:  # a caption fading in or out (3.5)
        extra += (s["cap_a"],)
    if s.get("vis"):  # structures hidden in some parts (3.6)
        extra += (json.dumps(s["vis"], sort_keys=True),)
    if s.get("lk") or s.get("lkfrom"):  # effects in some parts only (3.5)
        extra += (json.dumps(s.get("lk"), sort_keys=True), json.dumps(s.get("lkfrom"), sort_keys=True), s.get("lkq"))
    if p.get("fov") is not None:  # field of view per point of a camera path (3.5)
        extra += (round(float(p["fov"]), 3),)
    if p.get("pos") is not None:  # camera paths and the 3.5 wiggle give the camera position and up direction themselves
        extra += (tuple(np.round(np.asarray(p["pos"], float), 2)), tuple(np.round(np.asarray(p["up"], float), 4)))
    return extra + (s["k"], s["reveal"], s.get("cut"), round(s["slice"], 3), round(s["e"], 3), json.dumps(s["target"], sort_keys=True), oth,
            s["caption"], tuple(np.round(p["c"], 2)), round(p["tilt"], 3), round(p["az"] % 360.0, 3), round(p["dist"], 2))
