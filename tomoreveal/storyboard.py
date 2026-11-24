"""Camera framing, the clip box, and turning the movie settings into per-frame states."""
import copy
import json
import math
import uuid

import numpy as np

from .common import UserError


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

    def fit(self, lo, hi, tilt, az, margin=1.07):
        """Pose that just frames the box lo..hi."""
        lo, hi = np.asarray(lo, float), np.asarray(hi, float)
        hi = np.maximum(hi, lo + 1.0); c = (lo + hi) / 2  # at least one voxel wide: a zero-size box has no distance
        corners = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]) - c
        th = math.tan(math.radians(self.fov) / 2)
        p, up = self.axes(tilt, az); right = np.cross(up, p)
        xc, yc, zc = np.abs(corners @ right), np.abs(corners @ up), corners @ p
        dist = float(np.max(zc + np.maximum(yc / th, xc / (th * self.aspect)))) * margin
        scale = float(np.max(np.maximum(yc, xc / self.aspect))) * margin
        return dict(c=c, tilt=float(tilt), az=float(az), dist=dist, scale=scale)


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


def lerp_pose(a, b, u, d_az=None):
    """Pose between a and b; the azimuth takes the shorter way round unless d_az (the turn from a to b) is given."""
    d = ((b["az"] - a["az"] + 180.0) % 360.0) - 180.0 if d_az is None else float(d_az)
    return dict(c=a["c"] + (b["c"] - a["c"]) * u, tilt=a["tilt"] + (b["tilt"] - a["tilt"]) * u, az=a["az"] + d * u,
                dist=a["dist"] * (b["dist"] / a["dist"]) ** u, scale=a["scale"] * (b["scale"] / a["scale"]) ** u)


def smooth01(u):
    u = min(1.0, max(0.0, u))
    return u * u * (3 - 2 * u)


# ---- easing (3.5): how gently a transition leaves the previous part and arrives at the next ----
EASES = ("sudden", "gentle", "very_gentle")
_EASE_START = {"sudden": (0.0, 0.0), "gentle": (0.42, 0.0), "very_gentle": (0.65, 0.0)}
_EASE_END = {"sudden": (1.0, 1.0), "gentle": (0.58, 1.0), "very_gentle": (0.35, 1.0)}


def ease_curve(u, leave="gentle", arrive="gentle"):
    """Progress 0..1 at time u 0..1: a cubic Bezier as in CSS, with its own strength at each end. Gentle / gentle is
    exactly smooth01 (the easing of every earlier version); sudden / sudden is a constant speed."""
    u = min(1.0, max(0.0, float(u)))
    leave = leave if leave in _EASE_START else "gentle"; arrive = arrive if arrive in _EASE_END else "gentle"
    if leave == "gentle" and arrive == "gentle":
        return smooth01(u)
    (x1, y1), (x2, y2) = _EASE_START[leave], _EASE_END[arrive]
    if (x1, y1, x2, y2) == (0.0, 0.0, 1.0, 1.0):
        return u

    def bez(t, a, b):
        return 3 * (1 - t) ** 2 * t * a + 3 * (1 - t) * t * t * b + t ** 3
    lo, hi = 0.0, 1.0
    for _ in range(40):
        mid = (lo + hi) / 2
        if bez(mid, x1, x2) < u:
            lo = mid
        else:
            hi = mid
    return bez((lo + hi) / 2, y1, y2)


TRANSITION_KINDS = ("flight", "cut", "dissolve", "black", "white")
TRANSITION_DEFAULTS = dict(kind="flight", ease_out_prev="gentle", ease_in_next="gentle", overlap_prev=0.0, overlap_next=0.0, path="straight")


def transition_of(part):
    """Settings of the transition into a part (stored in the part as "transition_in"; its length stays in
    "transition", None = automatic). Missing settings mean the behaviour of every earlier version."""
    t = dict(TRANSITION_DEFAULTS); own = part.get("transition_in") if isinstance(part, dict) else None
    if isinstance(own, dict):
        t.update({k: v for k, v in own.items() if k in TRANSITION_DEFAULTS})
    t["overlap_prev"] = max(0.0, float(t["overlap_prev"] or 0)); t["overlap_next"] = max(0.0, float(t["overlap_next"] or 0))
    if t["kind"] not in TRANSITION_KINDS:
        t["kind"] = "flight"
    return t


def with_end_ease(fn, part):
    """A part's own motion slowed down at its end ("Slow down at the end", 3.5); unchanged for parts without it."""
    how = (part or {}).get("ease_end", "sudden")
    if how not in ("gentle", "very_gentle"):
        return fn
    return lambda u, fn=fn, how=how: fn(ease_curve(u, "sudden", how))


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
PATH_CAMERA = dict(distance=1.0, side="above", height=30.0, look="at_path", lead=0.05)
CARD_TRANSITIONS = ("fade", "dissolve", "cut")


def text_block(text, role="text", size=1.2, x=0.5, y=0.5, anchor="middle-center", color="#ffffff", bold=False, align="center"):
    return dict(kind="text", text=text, role=role, size=size, x=x, y=y, anchor=anchor, color=color, bold=bold, align=align)


def image_block(path, height=0.12, x=0.97, y=0.95, anchor="bottom-right"):
    return dict(kind="image", path=path, height=height, x=x, y=y, anchor=anchor)
VIEW_TRANSITION = 1.5  # seconds of camera flight into a "View from here" part
CLOSEUP_TRANSITION = 1.3  # seconds of camera flight into and out of a close-up
OTHER_TRANSITION = 0.5


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
        p = _common(type="overview", seconds=6, motion="rock", angle=25, view=None)
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
    p.update(fields)
    return p


# Settings that 3.5 gives new parts, and that parts saved before 3.5 lack (missing = the old behaviour).
NEW_IN_3_5 = {"focus": ("tilt", "ease_end", "steady_zoom", "wiggle"), "slide": ("ease_end",), "overview": ("ease_end",),
              "path": ("steadiness", "interp")}
WIGGLE_3_5 = dict(amplitude=5.0, frequency=0.35, shape="circle", smooth=True)  # calmer, and it starts and ends at rest


def as_legacy(part):
    """A part as a project saved before 3.5 would have it: the 3.5 settings of new parts removed (tests, golden data)."""
    p = copy.deepcopy(part)
    for k in NEW_IN_3_5.get(p.get("type"), ()):
        p.pop(k, None)
    return p


def default_parts():
    """Slide up, slide back revealing the segmentation, overview: the default movie."""
    return [new_part("slide_up"), new_part("reveal"), new_part("overview")]


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

    def ov(d=0.0, view=None):
        t, a, zm = (tilt, az, zoom) if not view else (float(view.get("tilt", tilt)), float(view.get("az", az)),
                                                       max(0.05, float(view.get("zoom", zoom))))
        p = zoomed(fr.fit(lo, hi, t, a + d, 1.07), zm)
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
        p = lerp_pose(a, b, q, d_az=(((p1["az"] - a["az"] + 180.0) % 360.0) - 180.0) + (b["az"] - p1["az"]))
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
            skip = go_to(ov(), a, t, slice_to=(target_of(part) if back else None), tr=tr,
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
            m, ang = part.get("motion", "rock"), float(part.get("angle", 25))
            main = (lambda u, m=m, ang=ang, vw=vw: dict(pose=ov(ang * math.sin(2 * math.pi * u) if m == "rock" else 360.0 * u if m == "spin" else 0.0, vw)))
            ep = part if m == "spin" else None  # "slow down at the end" suits a spin, not a rock (it already ends at rest)
            main_w = with_end_ease(main, ep)
            skip = go_to(ov(0.0, vw), st["k"], t, slice_to=ramp["s1"] if back else None, tr=tr,
                         nxt=lambda j, f=main_w, n=n: f(j / (n - 1) if n > 1 else 1.0), nn=n)
            if back:
                start_fade(part, fade=0)  # the flight back from the close-up already faded the tomogram
            st["caption"] = part.get("caption", "")
            emit_main(n, main, _mark_label(part), skip, ep)
            pose[0] = ov(0.0, vw)
        elif typ == "focus":
            tgt = part.get("target") or {}
            try:
                blo, bhi = focus_box(part, ds)
            except (KeyError, IndexError, TypeError, ValueError):
                problems[part.get("id")] = "structure not loaded" if target_items(tgt) else "choose what to show: objects, or draw a frame"
                continue
            if target_items(tgt) and not any(class_spec(spec, ds, ds.id_of(key)).get("show", True) for key, _ in target_items(tgt)):  # every object is off
                problems[part.get("id")] = "structure switched off in the Structures tab"
                continue
            start_fade(part)
            need_structures()
            ft, fz = float(part.get("tilt", max(tilt, 50))), max(0.05, float(part.get("zoom", 1)))

            m, ang = part.get("motion", "orbit"), float(part.get("angle", 30))
            steady = steady_framing(part, fr, blo, bhi, ft, az, m, ang) if part.get("steady_zoom") else None

            def fp(d=0.0, blo=blo, bhi=bhi, ft=ft, fz=fz, steady=steady):
                p = fr.fit(blo, bhi, ft, az + d, 1.0)
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
            mode, cap = part.get("others", "grey"), part.get("caption", "")

            def fly(u, p0=p0, p1=p1, e0=e0, old=old, oldmode=oldmode, s0=s0, s1=s1, tgt=tgt, mode=mode, cap=cap, A=A, m2=m2, nt=nt, nxt=nxt, tr=tr):
                q = ease_curve(u, tr["ease_out_prev"], tr["ease_in_next"]); i = int(round(u * (nt - 1))) if nt > 1 else 0
                d = dict(pose=flight_pose(i, nt, q, p0, p1, A, m2, nxt, tr) if (A or m2 or tr["path"] == "arc") else lerp_pose(p0, p1, q),
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
        emit(round(fps * CLOSEUP_TRANSITION), lambda u: dict(pose=lerp_pose(p0, ov(), smooth01(u)), e=e0 * (1 - smooth01(u)),
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
    return Plan(frames, segments, marks, fps, problems)


FX_DEFAULTS = dict(wiggle=dict(amplitude=8.0, frequency=0.5, shape="circle"), push=dict(on=False, start=1.0, end=1.35),
                   highlight=dict(kind="none", pulses=2), effect=dict(kind="none", direction="slices", move_slice=True, sweep="whole",
                                                                       how="bottom", seconds=None))


def fx_settings(part):
    out = {k: dict(v) for k, v in FX_DEFAULTS.items()}
    for k in out:
        if isinstance(part.get(k), dict):
            out[k].update(part[k])
    return out


def steady_framing(part, fr, lo, hi, tilt, az, motion, angle):
    """(distance, scale) that frames the box at every angle a close-up's motion reaches (sampled at 36 angles)."""
    pts = [(tilt, az)]
    if motion == "orbit":
        pts = [(tilt, az + 360.0 * k / 36) for k in range(36)]
    elif motion == "rock":
        pts = [(tilt, az + angle * math.sin(2 * math.pi * k / 36)) for k in range(36)]
    elif motion == "wiggle":
        w = fx_settings(part)["wiggle"]; A = float(w["amplitude"])
        if w.get("smooth"):  # the directions the 3.5 wiggle really reaches
            T = max(0.1, float(part.get("seconds", 5))); base = dict(c=np.zeros(3), tilt=tilt, az=az, dist=1.0, scale=1.0)
            for t in np.linspace(0, T, 90):
                q = turned_pose(fr, base, *wiggle_offsets(w, float(t), T)); pts.append((q["tilt"], q["az"]))
        pts += [(min(89.0, max(0.0, tilt + A * math.sin(2 * math.pi * k / 36))), az + A * math.cos(2 * math.pi * k / 36)) for k in range(36)]
    fits = [fr.fit(lo, hi, t, a, 1.0) for t, a in pts]
    return max(f["dist"] for f in fits), max(f["scale"] for f in fits)


def wiggle_offsets(w, t, T):
    """(sideways, up/down) turn of the viewing direction in degrees at time t of a close-up of T seconds (3.5 wiggle):
    zero speed and position at both ends, a whole number of cycles, shapes circle / eight / drift."""
    A = float(w.get("amplitude", 5.0)); shape = w.get("shape", "circle")
    Te = min(0.75, T / 2)

    def rise(x):  # "smootherstep": speed and acceleration are 0 at both of its ends, so the acceleration never jumps
        x = min(1.0, max(0.0, x))
        return x * x * x * (x * (6 * x - 15) + 10)
    env = rise(t / Te) * rise((T - t) / Te)
    if shape == "drift":  # slow and non-repeating
        x = 0.6 * math.sin(2 * math.pi * 0.13 * t + 1.1) + 0.4 * math.sin(2 * math.pi * 0.21 * t + 2.3)
        y = 0.6 * math.sin(2 * math.pi * 0.34 * t + 0.7)
        return A * env * x, A * env * y
    f = max(1, round(float(w.get("frequency", 0.35)) * T)) / max(1e-6, T)  # whole cycles: it ends where it began
    if shape == "eight":
        return A * env * math.sin(2 * math.pi * f * t), A / 2 * env * math.sin(4 * math.pi * f * t)
    return A * env * math.sin(2 * math.pi * f * t), A * env * (1 - math.cos(2 * math.pi * f * t)) / 2


def turned_pose(fr, base, dx, dy):
    """The pose with its viewing direction turned by dx degrees sideways and dy degrees up/down around the centre,
    keeping the picture upright (no roll). Gives pos and up, so it also works when looking straight down."""
    from .paths import tilt_az
    d0, up0 = fr.axes(base["tilt"], base["az"]); d0, up0 = np.asarray(d0, float), np.asarray(up0, float)
    right = np.cross(up0, d0)
    a, b = math.radians(dx), math.radians(dy)
    d = d0 * math.cos(a) * math.cos(b) + right * math.sin(a) * math.cos(b) + up0 * math.sin(b)
    d /= np.linalg.norm(d)
    up = up0 - d * float(up0 @ d); up /= np.linalg.norm(up)
    t, az = tilt_az(d, fr.u)
    if abs(dx) < 1e-9 and abs(dy) < 1e-9:
        t, az = base["tilt"], base["az"]
    return dict(base, tilt=float(t), az=float(az), wdir=d, pos=np.asarray(base["c"]) + base["dist"] * d, up=up)


def closeup_animation(part, ds, fr, fp, n, fps, k0=0, nz=1):
    """Frame function u -> state changes for a close-up's main phase with wiggle, push-in, highlight or an object effect;
    None when the close-up has none of them (it is then drawn exactly as before)."""
    S = fx_settings(part); m = part.get("motion", "orbit"); ang = float(part.get("angle", 30))
    wig, push, hl, eff = S["wiggle"], S["push"], S["highlight"], S["effect"]
    if m != "wiggle" and not push.get("on") and hl["kind"] == "none" and eff["kind"] == "none":
        return None
    T = max(1e-6, (n - 1) / fps); tgt = part.get("target") or {}
    base = fp(); obj_lo, obj_hi = focus_box(part, ds, pad=False)
    lo, hi = np.asarray(obj_lo, float) - 0.5, np.asarray(obj_hi, float) + 0.5
    centre = (lo + hi) / 2; radius = float(np.linalg.norm(hi - lo)) / 2
    axis = ds.main_axis(tgt) if eff["kind"] == "section" and eff.get("direction") == "axis" else None

    def fn(u):
        t = u * T; d = {}; fx = {}
        if m == "wiggle" and wig.get("smooth"):  # 3.5: calm start and end, whole cycles, no roll
            dx, dy = wiggle_offsets(wig, t, T)
            p = turned_pose(fr, base, dx, dy)
            fx["wiggle"] = [round(dx, 4), round(dy, 4)]
        elif m == "wiggle":
            A = float(wig["amplitude"]) * smooth01(t / 0.5) * smooth01((T - t) / 0.5); f = float(wig["frequency"])
            if wig.get("shape") == "eight":
                daz, dtl = A * math.sin(2 * math.pi * f * t), A / 2 * math.sin(4 * math.pi * f * t)
            else:
                daz, dtl = A * math.cos(2 * math.pi * f * t), A * math.sin(2 * math.pi * f * t)
            p = dict(base, az=base["az"] + daz, tilt=min(89.0, max(0.0, base["tilt"] + dtl)))
            fx["wiggle"] = [round(daz, 4), round(dtl, 4)]
        else:
            p = fp(360.0 * u if m == "orbit" else ang * math.sin(2 * math.pi * u) if m == "rock" else 0.0)
        if push.get("on"):
            z = float(push["start"]) + (float(push["end"]) - float(push["start"])) * smooth01(u)
            need = fr.fit(lo, hi, p["tilt"], p["az"], 1.02)  # never so close that the objects leave the frame
            p = dict(p, dist=max(p["dist"] / z, need["dist"]), scale=max(p["scale"] / z, need["scale"]))
            fx["push"] = round(z, 4)
        if p.get("wdir") is not None:  # a turned (wiggle) pose: the camera position follows the distance
            p = dict(p, pos=np.asarray(p["c"]) + p["dist"] * p["wdir"])
        d["pose"] = p
        if hl["kind"] == "pulse":
            fx["pulse"] = round((1 - math.cos(2 * math.pi * max(1, int(hl.get("pulses", 2))) * u)) / 2, 4)
        elif hl["kind"] == "glow":
            fx["glow"] = round((1 - math.cos(2 * math.pi * max(1, int(hl.get("pulses", 2))) * u)) / 2, 4)
        if eff["kind"] == "section":
            q = smooth01(min(1.0, u / 0.6)) * 0.5 if eff.get("sweep") == "half" else u
            direc = eff.get("direction", "slices")
            if direc == "viewer":
                cam = fr.axes(p["tilt"], p["az"])[0]; nrm = -np.asarray(cam)  # keep the far side; the cut faces the camera
                o = centre + nrm * (-radius + 2 * radius * q)
            elif direc == "axis":
                nrm = -axis; o = centre + axis * (radius - 2 * radius * q)
            else:  # through the slices, top to bottom: keep what is below the cut
                nrm = np.array([0.0, 0.0, -1.0]); o = np.array([centre[0], centre[1], hi[2] - (hi[2] - lo[2]) * q])
                if eff.get("move_slice", True):
                    d["k"] = int(min(nz - 1, max(0, math.floor(o[2] - 0.6))))  # the slice just under the cut face
            fx["cut"] = dict(normal=[round(float(v), 4) for v in nrm], origin=[round(float(v), 2) for v in o], mode="section")
        elif eff["kind"] == "grow":
            dur = float(eff["seconds"]) if eff.get("seconds") else T * 0.7
            g = smooth01(min(1.0, t / max(1e-6, dur)))
            if eff.get("how") == "fade":
                fx["fade"] = round(g, 4)
            else:
                start = float(k0) if eff.get("how") == "slice" and lo[2] <= k0 <= hi[2] else lo[2]
                z = start + (hi[2] - start) * g if g < 1 else hi[2] + 1
                fx["cut"] = dict(normal=[0.0, 0.0, -1.0], origin=[round(float(centre[0]), 2), round(float(centre[1]), 2), round(float(z), 2)],
                                 mode="grow")
        if fx:
            d["fx"] = fx
        return d
    return fn


class PathPending(Exception):
    """The window is still working out a path (in a worker thread)."""


def path_key(items, cb):
    return json.dumps([list(i) for i in items]) + json.dumps(cb, default=list)


def path_line(ds, items, cb):
    """The centre line of the items (cached on the dataset). The window computes it in a worker (PathPending)."""
    from .paths import centre_line
    key = path_key(items, cb)
    if key not in ds.path_cache:
        if ds.path_async:
            raise PathPending(key)
        try:
            ds.path_cache[key] = centre_line(ds, items, cb)
        except UserError as exc:
            ds.path_cache[key] = exc
    r = ds.path_cache[key]
    if isinstance(r, Exception):
        raise r
    return r


def _ease_or_even(part, u):
    return smooth01(u) if part.get("speed") == "ease" else u


def _pose(fr, pos, focal, up):
    from .paths import tilt_az
    pos, focal = np.asarray(pos, float), np.asarray(focal, float)
    d = pos - focal; dist = float(np.linalg.norm(d)) or 1.0
    t, a = tilt_az(d, fr.u)
    return dict(c=focal, tilt=float(t), az=float(a), dist=dist, scale=dist * math.tan(math.radians(fr.fov) / 2),
                pos=pos, up=np.asarray(up, float))


TURN_LIMIT = 20.0      # degrees per second the follow camera's view may turn at most
TIME_FILTER = 0.4      # seconds: the last smoothing of the follow camera over time


def follow_rails(part, line, cam):
    """(camera rail, look-at rail) of a steady follow path (3.5): the look-at rail is the centre line smoothed by the
    part's steadiness; the camera rail is a calmer copy (averaged over 15 % of the length), offset by side and height."""
    from .paths import moving_average, resample, steady_rail
    look = steady_rail(line, float(part.get("steadiness", 50)))
    base = moving_average(look, 0.15)
    L = float(np.linalg.norm(np.diff(look, axis=0), axis=1).sum())
    D = float(cam.get("distance", 1.0)) * max(30.0, 0.3 * L); h = math.radians(float(cam.get("height", 30)))
    z = np.array([0.0, 0.0, 1.0]); side = cam.get("side", "above")
    T = np.gradient(base, axis=0); T /= np.linalg.norm(T, axis=1, keepdims=True) + 1e-12
    zmean = float(base[:, 2].mean())
    out = []
    for i, P in enumerate(base):
        t = T[i]; horiz = t - z * float(t @ z); horiz /= np.linalg.norm(horiz) or 1.0
        if side == "above":
            C = np.array([P[0], P[1], zmean + D])
        elif side == "behind":
            C = P - horiz * D * math.cos(h); C[2] = zmean + D * math.sin(h)
        elif side == "free":
            C = P + D * np.asarray(cam.get("_main_dir"), float)
        else:
            sv = np.cross(horiz, z); sv *= (1 if side == "left" else -1)
            C = P + sv * D * math.cos(h); C[2] = zmean + D * math.sin(h)  # constant height, not up and down with the membrane
        out.append(C)
    return resample(np.array(out), len(look)), look


def steady_follow_poses(part, fr, line, cam, n, fps, main_dir, main_up):
    from .paths import gaussian_in_time, turn_limited_times, upright_ups
    cam = dict(cam, _main_dir=list(main_dir))
    crail, look = follow_rails(part, line, cam)
    T = max(1e-6, (n - 1) / fps)
    if cam.get("look") == "ahead":  # look a little ahead along the path
        k = max(1, int(round(float(cam.get("lead", 0.05)) * len(look))))
        look = np.vstack([look[k:], np.repeat(look[-1:], k, axis=0)])
    end = "very_gentle" if part.get("speed") == "ease" else "gentle"  # even speed, with gentle (or very gentle) ends
    peak = max((ease_curve((k + 1) / 200, end, end) - ease_curve(k / 200, end, end)) * 200 for k in range(200))
    times = turn_limited_times(crail, look, T, TURN_LIMIT / peak)  # the eased middle runs up to `peak` times faster
    pos, foc = [], []
    for j in range(n):
        u = ease_curve(j / max(1, n - 1), end, end)
        t = u * times[-1]
        x = float(np.interp(t, times, np.arange(len(times))))
        i0 = int(min(len(times) - 2, max(0, math.floor(x)))); f = x - i0
        pos.append(crail[i0] * (1 - f) + crail[i0 + 1] * f); foc.append(look[i0] * (1 - f) + look[i0 + 1] * f)
    sig = TIME_FILTER * fps
    pos, foc = gaussian_in_time(pos, sig), gaussian_in_time(foc, sig)
    ups = upright_ups([f - p for p, f in zip(pos, foc)], np.asarray(main_up, float))
    return [_pose(fr, p, f, u) for p, f, u in zip(pos, foc, ups)]


SEGMENT_SPEEDS = {"slow": 0.5, "normal": 1.0, "fast": 2.0}


def point_values(part, key, default):
    """Zoom ("zoom") or field of view ("fov") at each point of a path: a number, or "keep" (also when missing): the
    value of the last point before it that has its own, unless that point said "only here" (3.5)."""
    wps = part.get("waypoints") or []
    if part.get("same_zoom"):  # one value for the whole path
        v = float(part.get(f"path_{key}", default) or default)
        return [v] * len(wps), [False] * len(wps)
    kept, out, own = float(default), [], []
    for w in wps:
        v = w.get(key)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out.append(float(v)); own.append(True)
            if not w.get(f"{key}_only_here"):
                kept = float(v)
        else:
            out.append(kept); own.append(False)
    return out, own


def _segment_curve(P, i, u):
    """Point at fraction u of segment i of the centripetal Catmull-Rom curve through P (fine samples, by arc length)."""
    from .paths import catmull_rom
    a, b = max(0, i - 1), min(len(P), i + 3)
    sub = P[a:b]; curve, at = catmull_rom(sub, 60)
    j = i - a; seg = curve[at[j]:at[j + 1] + 1]
    if len(seg) < 2:
        return np.asarray(P[i], float)
    cum = np.r_[0, np.cumsum(np.linalg.norm(np.diff(seg, axis=0), axis=1))]
    if cum[-1] < 1e-9:
        return seg[0] + (seg[-1] - seg[0]) * u
    d = u * cum[-1]
    return np.array([np.interp(d, cum, seg[:, k]) for k in range(3)])


def _hermite(vals, i, u):
    """Catmull-Rom (uniform) through 1D values: smooth, and constant where neighbouring values are equal."""
    v = list(vals); p0 = v[max(0, i - 1)]; p1 = v[i]; p2 = v[min(len(v) - 1, i + 1)]; p3 = v[min(len(v) - 1, i + 2)]
    m1 = (p2 - p0) / 2 if 0 < i else (p2 - p1); m2 = (p3 - p1) / 2 if i + 2 < len(v) else (p2 - p1)
    if abs(p2 - p1) < 1e-12:
        return p1
    h00, h10, h01, h11 = 2 * u ** 3 - 3 * u * u + 1, u ** 3 - 2 * u * u + u, -2 * u ** 3 + 3 * u * u, u ** 3 - u * u
    out = h00 * p1 + h10 * m1 + h01 * p2 + h11 * m2
    return min(max(out, min(p1, p2)), max(p1, p2))  # never beyond the two values (no overshoot)


def points_poses(part, fr, cam, n, fps, main_dir, main_up):
    """Poses of a path through points (3.5): position and look-at on their own centripetal Catmull-Rom curves with the
    same clock per segment (so the view never flips), even speed inside a segment, a speed or a time per segment,
    gentle stops where the camera pauses or is asked to stop, zoom and field of view per point."""
    from .paths import upright_ups
    wps = part.get("waypoints") or []; D = 60.0 * float(cam["distance"])
    P, F = [], []
    for w in wps:
        if w.get("pos") is not None:
            P.append(np.asarray(w["pos"], float)); F.append(np.asarray(w.get("focal", w["pos"]), float))
        else:
            q = np.asarray(w["point"], float); F.append(q); P.append(q + D * np.asarray(main_dir, float))
    K = len(wps)
    zooms, _ = point_values(part, "zoom", 1.0); fovs, _ = point_values(part, "fov", fr.fov)
    zall = float(part.get("zoom_all", 1.0) or 1.0)
    # time of each segment: its length over its speed, or the seconds asked for
    def seg_len(Q, i):  # length of segment i of the curve through Q
        c = np.array([_segment_curve(Q, i, u) for u in np.linspace(0, 1, 24)])
        return float(np.linalg.norm(np.diff(c, axis=0), axis=1).sum())
    lens = [seg_len(P, i) + 0.25 * seg_len(F, i) + 1e-6 for i in range(K - 1)]
    T = max(1e-6, (n - 1) / fps)
    fixed = {i: float(wps[i].get("seconds")) for i in range(K - 1) if isinstance(wps[i].get("seconds"), (int, float)) and wps[i]["seconds"] > 0}
    w_free = {i: lens[i] / SEGMENT_SPEEDS.get(wps[i].get("speed", "normal"), 1.0) for i in range(K - 1) if i not in fixed}
    def stops(i):
        return i == 0 or i == K - 1 or bool(wps[i].get("stop_gently")) or float(wps[i].get("pause", 0) or 0) > 0

    Te = min(0.75, T / 4)  # seconds of gentle start and stop: constant speed in between
    extra = {i: 0.5 * Te * (int(stops(i)) + int(stops(i + 1))) for i in w_free}  # time lost while slowing down and setting off
    left = max(0.05 * T, T - sum(fixed.values()) - sum(extra.values()))
    dur = [fixed[i] if i in fixed else extra[i] + left * w_free[i] / max(1e-9, sum(w_free.values())) for i in range(K - 1)]
    scale = T / sum(dur); dur = [d * scale for d in dur]
    starts = np.r_[0, np.cumsum(dur)]

    def progress_table(i):
        """Fraction of segment i done against its time: speed ramps up after a stop and down before one."""
        D = max(1e-9, dur[i]); te = min(Te, D / 2); tt = np.linspace(0, D, 201)
        sp = np.ones_like(tt)
        if stops(i):
            sp *= np.array([smooth01(x / te) for x in tt]) if te > 1e-9 else 1
        if stops(i + 1):
            sp *= np.array([smooth01((D - x) / te) for x in tt]) if te > 1e-9 else 1
        sp = np.maximum(sp, 1e-6); cum = np.r_[0, np.cumsum((sp[1:] + sp[:-1]) / 2 * np.diff(tt))]
        return tt, cum / cum[-1]
    tables = [progress_table(i) for i in range(K - 1)]

    def at(t):
        i = int(min(K - 2, np.searchsorted(starts, t, side="right") - 1)); i = max(0, i)
        tt, frac = tables[i]
        u = float(np.interp(t - starts[i], tt, frac))
        pos, foc = _segment_curve(P, i, u), _segment_curve(F, i, u)
        z = math.exp(_hermite([math.log(max(1e-3, v)) for v in zooms], i, u)) * zall
        fv = _hermite(fovs, i, u)
        return foc + (pos - foc) / z, foc, fv, i, u
    out = []; seen = set(); pauses = {i: max(0, round(fps * float(w.get("pause", 0) or 0))) for i, w in enumerate(wps)}
    for j in range(n):
        t = j / fps if j < n - 1 else T
        pos, foc, fv, i, u = at(t)
        for k in range(K):  # stand still at a point with a pause
            if k not in seen and (starts[k] <= t + 1e-9 if k < K - 1 else j == n - 1):
                seen.add(k)
                if pauses[k]:
                    pk, fk, fvk, _, _ = at(starts[k] if k < K - 1 else T)
                    out += [(pk, fk, fvk)] * pauses[k]
        out.append((pos, foc, fv))
    ups = upright_ups([f - p for p, f, _ in out], np.asarray(main_up, float))
    res = []
    for (p, f, fv), up in zip(out, ups):
        q = _pose(fr, p, f, up)
        if abs(fv - fr.fov) > 1e-6:
            q["fov"] = float(fv); q["scale"] = q["dist"] * math.tan(math.radians(fv) / 2)
        res.append(q)
    return res


def path_poses(part, ds, fr, spec, n, fps, cb):
    """Camera poses of a path part: along the centre line of a structure ("follow"), or through waypoints ("points")."""
    from .paths import arclength, catmull_rom, sample, stable_up
    src = part.get("source") or {}; cam = dict(PATH_CAMERA, **(part.get("camera") or {}))
    v = spec.get("view", {}); main_dir, main_up = fr.axes(float(v.get("tilt", 42)), float(v.get("az", 0)))
    if src.get("kind", "follow") == "follow":
        items = [(it.get("structure"), it.get("component")) for it in src.get("items", []) if it.get("structure") is not None]
        if not items:
            raise UserError("Choose the structure the camera should follow.")
        line = path_line(ds, items, cb)
        if part.get("reverse"):
            line = line[::-1]
        if part.get("steadiness") is not None:  # 3.5: a calm tracking shot instead of following every bend
            return steady_follow_poses(part, fr, line, cam, n, fps, main_dir, main_up)
        cum = arclength(line); L = float(cum[-1]); D = float(cam["distance"]) * max(30.0, 0.3 * L)
        z = np.array([0.0, 0.0, 1.0]); h = math.radians(float(cam.get("height", 30)))
        pos, foc, dirs = [], [], []
        for i in range(n):
            d = _ease_or_even(part, i / max(1, n - 1)) * L
            P = sample(line, cum, d); T = sample(line, cum, d + 2) - sample(line, cum, d - 2); T /= np.linalg.norm(T) or 1.0
            upp = z - T * float(z @ T); upp = upp / (np.linalg.norm(upp) or 1.0)
            side = cam.get("side", "above")
            if side == "free":
                off = np.asarray(main_dir, float)
            elif side == "above":
                off = upp
            else:
                sv = np.cross(T, z); sv = sv / (np.linalg.norm(sv) or 1.0) * (1 if side == "left" else -1)
                off = math.cos(h) * sv + math.sin(h) * upp
            C = P + D * off; C[2] = max(C[2], 1.0)  # never below the tomogram
            F = sample(line, cum, d + float(cam.get("lead", 0.05)) * L) if cam.get("look") == "ahead" else P
            pos.append(C); foc.append(F); dirs.append(F - C)
        ups = stable_up(dirs, z if cam.get("side") not in ("above",) else (sample(line, cum, L) - sample(line, cum, 0)))
        return [_pose(fr, p, f, u) for p, f, u in zip(pos, foc, ups)]
    wps = part.get("waypoints") or []
    if len(wps) < 2:
        raise UserError("Add at least two points for the camera to move through.")
    if part.get("interp") == 2:
        return points_poses(part, fr, cam, n, fps, main_dir, main_up)
    D = 60.0 * float(cam["distance"])
    P, F = [], []
    for w in wps:
        if w.get("pos") is not None:
            P.append(np.asarray(w["pos"], float)); F.append(np.asarray(w.get("focal", w["pos"]), float))
        else:  # a point clicked in the preview: the camera passes near it, looking at it
            q = np.asarray(w["point"], float); F.append(q); P.append(q + D * np.asarray(main_dir, float))
    curve, at = catmull_rom(P); fcurve, _ = catmull_rom(F)
    cum = arclength(curve); L = float(cum[-1])
    if L < 1e-6:  # only the looking direction changes: move by index
        cum = np.arange(len(curve), dtype=float); L = float(cum[-1])
    knots = [float(cum[i]) for i in at]
    pauses = {i: max(0, round(fps * float(w.get("pause", 0) or 0))) for i, w in enumerate(wps)}
    poses = []; done = set()
    for j in range(n):
        d = _ease_or_even(part, j / max(1, n - 1)) * L
        for i, kd in enumerate(knots):
            if i not in done and d >= kd - 1e-9:
                done.add(i)
                exact = (P[i], F[i])
                for _ in range(pauses[i]):
                    poses.append(("x",) + exact)
        poses.append((d,))
    out_pos, out_foc = [], []
    for e in poses:
        if e[0] == "x":
            out_pos.append(e[1]); out_foc.append(e[2])
        else:
            out_pos.append(sample(curve, cum, e[0])); out_foc.append(sample(fcurve, cum, e[0]))
    ups = stable_up([f - p for p, f in zip(out_pos, out_foc)], np.asarray(main_up, float))
    return [_pose(fr, p, f, u) for p, f, u in zip(out_pos, out_foc, ups)]


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
def _index(spec, part_id):
    for i, p in enumerate(spec["shots"]):
        if p.get("id") == part_id:
            return i
    raise UserError("That part is no longer in the movie.")


def insert_part(spec, part, after=None):
    """Insert after the part with id `after`; at the end when after is None; at the start when after == "start"."""
    migrate_spec(spec)
    if after is None:
        spec["shots"].append(part)
    elif after == "start":
        spec["shots"].insert(0, part)
    else:
        spec["shots"].insert(_index(spec, after) + 1, part)
    return spec


def remove_part(spec, part_id):
    del spec["shots"][_index(spec, part_id)]
    return spec


def duplicate_part(spec, part_id):
    i = _index(spec, part_id)
    p = copy.deepcopy(spec["shots"][i]); p["id"] = new_id()
    spec["shots"].insert(i + 1, p)
    return spec


def move_part(spec, part_id, new_index):
    i = _index(spec, part_id); p = spec["shots"].pop(i)
    spec["shots"].insert(int(min(len(spec["shots"]), max(0, new_index))), p)
    return spec


def set_part(spec, part_id, **fields):
    spec["shots"][_index(spec, part_id)].update(fields)
    return spec


def split_part(spec, ds, part_id, frame):
    """Split a slide or a hold at a frame of its main phase into two parts."""
    i = _index(spec, part_id); p = spec["shots"][i]
    if p.get("type") not in ("slide", "hold"):
        raise UserError("This kind of part cannot be split.")
    plan = build_plan(spec, ds); rng = main_range(plan, part_id)
    if rng is None or not (rng[0] < frame < rng[0] + rng[1]):
        raise UserError("Put the playhead inside the part (not at its very start or end) to split it.")
    start, count = rng; f = (frame - start) / count; secs = float(p.get("seconds", 5))
    first, second = copy.deepcopy(p), copy.deepcopy(p)
    second["id"] = new_id()
    first["seconds"] = secs * f; second["seconds"] = secs * (1 - f)
    first["pause_after"] = 0.0; second["pause_before"] = 0.0; second["transition"] = 0
    if p["type"] == "slide":
        k = int(plan.frames[frame]["k"])
        first["to"] = k; second["from"] = "current"
        if second.get("structures") in ("none", "above", "all"):
            second["structures"] = "keep"
    spec["shots"][i:i + 1] = [first, second]
    return spec


class History:
    """Undo and redo for the movie settings: deep copies of the whole spec, the last `limit` kept."""
    def __init__(self, limit=100):
        self.limit, self.states, self.pos = limit, [], -1

    def clear(self):
        self.states, self.pos = [], -1

    def push(self, spec):
        """Record the settings after a change (or the starting point)."""
        snap = copy.deepcopy(spec)
        if self.pos >= 0 and self.states[self.pos] == snap:
            return
        del self.states[self.pos + 1:]
        self.states.append(snap)
        if len(self.states) > self.limit:
            del self.states[0]
        self.pos = len(self.states) - 1

    @property
    def can_undo(self):
        return self.pos > 0

    @property
    def can_redo(self):
        return 0 <= self.pos < len(self.states) - 1

    def undo(self):
        if not self.can_undo:
            return None
        self.pos -= 1
        return copy.deepcopy(self.states[self.pos])

    def redo(self):
        if not self.can_redo:
            return None
        self.pos += 1
        return copy.deepcopy(self.states[self.pos])


def state_key(s):
    p = s["pose"]
    oth = s["others"] if not isinstance(s["others"], dict) else json.dumps(s["others"], sort_keys=True)
    extra = (json.dumps(s["fx"], sort_keys=True, default=float),) if s.get("fx") else ()  # close-up animations (3.4)
    if s.get("blend"):  # dissolves and fades between parts (3.5)
        extra += (s["blend"]["via"], s["blend"]["q"], state_key(s["blend"]["src"]))
    return extra + (s["k"], s["reveal"], s.get("cut"), round(s["slice"], 3), round(s["e"], 3), json.dumps(s["target"], sort_keys=True), oth,
            s["caption"], tuple(np.round(p["c"], 2)), round(p["tilt"], 3), round(p["az"] % 360.0, 3), round(p["dist"], 2))
