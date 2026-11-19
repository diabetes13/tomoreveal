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
        lo, hi = np.asarray(lo, float), np.asarray(hi, float); c = (lo + hi) / 2
        corners = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]) - c
        th = math.tan(math.radians(self.fov) / 2)
        p, up = self.axes(tilt, az); right = np.cross(up, p)
        xc, yc, zc = np.abs(corners @ right), np.abs(corners @ up), corners @ p
        dist = float(np.max(zc + np.maximum(yc / th, xc / (th * self.aspect)))) * margin
        scale = float(np.max(np.maximum(yc, xc / self.aspect))) * margin
        return dict(c=c, tilt=float(tilt), az=float(az), dist=dist, scale=scale)


def lerp_pose(a, b, u):
    d = ((b["az"] - a["az"] + 180.0) % 360.0) - 180.0
    return dict(c=a["c"] + (b["c"] - a["c"]) * u, tilt=a["tilt"] + (b["tilt"] - a["tilt"]) * u, az=a["az"] + d * u,
                dist=a["dist"] * (b["dist"] / a["dist"]) ** u, scale=a["scale"] * (b["scale"] / a["scale"]) ** u)


def smooth01(u):
    u = min(1.0, max(0.0, u))
    return u * u * (3 - 2 * u)


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


def make_target(items):
    """A close-up target from [(structure key, component or None)]."""
    return {"items": [{"structure": k, "component": None if c is None else int(c)} for k, c in items]}


OTHER_COLOURS = ("keep", "grey", "darken")


def others_of(v):
    """(colour, opacity) of the other structures during a close-up. Older settings: grey = (grey, 1), hide = (keep, 0),
    keep = (keep, 1)."""
    if isinstance(v, dict):
        c = v.get("colour", "grey")
        return (c if c in OTHER_COLOURS else "grey"), float(min(1.0, max(0.0, v.get("opacity", 1.0))))
    return {"grey": ("grey", 1.0), "hide": ("keep", 0.0), "keep": ("keep", 1.0)}.get(v, ("grey", 1.0))


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
        p = _common(type="focus", seconds=5, target=None, others="grey", motion="orbit", zoom=1.0, angle=30)
    elif kind == "hold":
        p = _common(type="hold", seconds=2)
    elif kind == "path":
        p = _common(type="path", seconds=10, source=dict(kind="follow", items=[]), waypoints=[], camera=dict(PATH_CAMERA),
                    speed="even", ease=True, reverse=False, others=dict(colour="keep", opacity=1.0), show_path_in_preview=True)
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
    p.update(fields)
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
                if k not in ("on", "seconds", "tomogram", "caption"):
                    p[k] = v
            p["type"] = "slide"
        elif t == "reveal":
            base = new_part("reveal"); base.pop("id")
            for k, v in base.items():
                if k not in ("on", "seconds", "tomogram", "caption"):
                    p[k] = v
            p["type"] = "slide"
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
        output=dict(size=[1920, 1080], fps=30, crf=20, aa=True))


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
        return zoomed(fr.fit(lo, hi, t, a + d, 1.07), zm)

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
    cur = dict(id=None, index=-1, type=None)

    def start_fade(part, fade=None):
        t = shot_tomogram(part); f = t["fade"] if fade is None else fade
        ramp.clear(); ramp.update(start=len(frames), s0=st["slice"], s1=target_of(part), n=max(1, round(fps * max(0.0, float(f)))))

    def emit(n, fn, phase, label=None, seg_phase=None):
        n = max(1, int(n))
        if label:
            marks.append(dict(frame=len(frames), label=label))
        segments.append(dict(part=cur["id"], index=cur["index"], type=cur["type"], phase=seg_phase or phase, start=len(frames), count=n))
        for i in range(n):
            s = dict(st); s["pose"] = pose[0]; s["shot"] = cur["index"]; s["part"] = cur["id"]; s["type"] = cur["type"]; s["phase"] = phase
            d = fn(i / (n - 1) if n > 1 else 1.0)
            if ramp and "slice" not in d:
                q = smooth01((len(frames) - ramp["start"] + 1) / ramp["n"])
                d["slice"] = ramp["s1"] if q >= 1 else ramp["s0"] + (ramp["s1"] - ramp["s0"]) * q
            s.update(d); frames.append(s)
        last = frames[-1]
        for key in st:
            st[key] = last[key]
        pose[0] = last["pose"]

    def need_structures():
        """Overviews and close-ups show structures: switch them all on if none are shown."""
        if not st["reveal"]:
            st.update(reveal=True, cut="all")

    def same_pose(a, b):
        return (np.allclose(a["c"], b["c"]) and abs(a["tilt"] - b["tilt"]) < 1e-6 and abs(((a["az"] - b["az"] + 180) % 360) - 180) < 1e-6
                and abs(a["dist"] - b["dist"]) < 1e-6)

    def go_to(p1, k1, seconds, slice_to=None, label=None):
        """Transition: fly the camera to p1 and glide the slice to k1; the close-up emphasis fades out."""
        p0, k0, e0, s0 = pose[0], st["k"], st["e"], st["slice"]
        if same_pose(p0, p1) and k0 == k1 and e0 <= 0:
            return
        n = round(fps * max(0.0, seconds))
        if n <= 0:  # a hard cut
            st.update(k=k1, e=0.0, target=None, caption=""); pose[0] = p1
            if slice_to is not None:
                st["slice"] = slice_to
            return

        def fn(u):
            q = smooth01(u)
            d = dict(pose=lerp_pose(p0, p1, q), k=int(round(k0 + (k1 - k0) * q)), e=e0 * (1 - q), caption="")
            if slice_to is not None:
                d["slice"] = s0 + (slice_to - s0) * q
            return d
        emit(n, fn, "transition", label)
        st.update(target=None, e=0.0); pose[0] = p1

    shots = spec.get("shots", [])
    for si, part in enumerate(shots):
        if not part.get("on", True):
            continue
        typ = part.get("type"); n = max(1, round(fps * max(0.1, float(part.get("seconds", 5)))))
        cur.update(id=part.get("id"), index=si, type=typ)
        if typ == "slide":
            show = part.get("structures", "keep")
            if (not has_tomo and show == "none") or (not has_seg and show in ("above", "all")):
                continue  # nothing to see: no tomogram to slide through, or no segmentation to reveal
            start_fade(part)
            a, b = slice_no(part.get("from"), st["k"]), slice_no(part.get("to"), st["k"])
            t = part.get("transition"); t = (CLOSEUP_TRANSITION if st["e"] > 0 else OTHER_TRANSITION) if t is None else float(t)
            go_to(ov(), a, t, slice_to=(target_of(part) if st["e"] > 0 else None))
            if show == "none":
                st.update(reveal=False, cut=None)
            elif show == "above":
                st.update(reveal=True, cut=None)
            elif show == "all":
                st.update(reveal=True, cut="all")
            st["k"] = a; st["caption"] = part.get("caption", "")
            label = _mark_label(part); pb = _pause_frames(fps, part.get("pause_before"))
            if pb:
                emit(pb, lambda u: {}, "pause", label); label = None
            emit(n, lambda u, a=a, b=b: dict(k=a + int(round(u * (b - a)))), "main", label)
            pa = _pause_frames(fps, part.get("pause_after"))
            if pa:
                emit(pa, lambda u: {}, "pause")
        elif typ == "overview":
            start_fade(part)
            need_structures()
            vw = part.get("view") or None
            t = part.get("transition"); t = (CLOSEUP_TRANSITION if st["e"] > 0 else OTHER_TRANSITION) if t is None else float(t)
            back = st["e"] > 0
            go_to(ov(0.0, vw), st["k"], t, slice_to=ramp["s1"] if back else None)
            if back:
                start_fade(part, fade=0)  # the flight back from the close-up already faded the tomogram
            st["caption"] = part.get("caption", "")
            m, ang = part.get("motion", "rock"), float(part.get("angle", 25))
            emit(n, lambda u: dict(pose=ov(ang * math.sin(2 * math.pi * u) if m == "rock" else 360.0 * u if m == "spin" else 0.0, vw)),
                 "main", _mark_label(part))
            pose[0] = ov(0.0, vw)
        elif typ == "focus":
            tgt = part.get("target") or {}
            try:
                blo, bhi = ds.targets_box(tgt)
            except (KeyError, IndexError, TypeError, ValueError):
                problems[part.get("id")] = "structure not loaded"
                continue
            start_fade(part)
            need_structures()
            ft, fz = float(part.get("tilt", max(tilt, 50))), max(0.05, float(part.get("zoom", 1)))

            def fp(d=0.0, blo=blo, bhi=bhi, ft=ft, fz=fz):
                return zoomed(fr.fit(blo, bhi, ft, az + d, 1.0), fz)

            p0, p1, e0, old, oldmode, s0 = pose[0], fp(), st["e"], st["target"], st["others"], st["slice"]
            s1 = ramp["s1"]  # the fly-in is this part's tomogram fade
            mode, cap = part.get("others", "grey"), part.get("caption", "")

            def fly(u, p0=p0, p1=p1, e0=e0, old=old, oldmode=oldmode, s0=s0, s1=s1, tgt=tgt, mode=mode, cap=cap):
                q = smooth01(u); d = dict(pose=lerp_pose(p0, p1, q), slice=s0 + (s1 - s0) * q)
                if e0 > 0 and old is not None:  # hand over from the previous close-up
                    if u < 0.5:
                        d.update(e=e0 * (1 - smooth01(2 * u)), target=old, others=oldmode, caption="")
                    else:
                        d.update(e=smooth01(2 * u - 1), target=tgt, others=mode, caption=cap if u > 0.8 else "")
                else:
                    d.update(e=q, target=tgt, others=mode, caption=cap if q > 0.6 else "")
                return d

            t = part.get("transition"); t = CLOSEUP_TRANSITION if t is None else float(t)
            nt = round(fps * max(0.0, t))
            if (part.get("effect") or {}).get("kind") == "grow":  # the objects disappear on the way in, then grow in
                fly = (lambda u, f0=fly: dict(f0(u), fx={"fade": 1.0 - smooth01(u)}))
            if nt > 0:
                emit(nt, fly, "transition", cap or "Focus")
                label = None
            else:
                st.update(fly(1.0)); pose[0] = p1; label = cap or "Focus"
            m, ang = part.get("motion", "orbit"), float(part.get("angle", 30))
            anim = closeup_animation(part, ds, fr, fp, n, fps, k0=st["k"], nz=nz)
            if anim is None:  # orbit / rock / still without effects: exactly as in 3.3
                emit(n, lambda u, fp=fp, m=m, ang=ang: dict(pose=fp(360.0 * u if m == "orbit" else ang * math.sin(2 * math.pi * u) if m == "rock" else 0.0)),
                     "main", label)
            else:
                emit(n, anim, "main", label)
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
            go_to(poses[0], st["k"], t)
            items = [(it.get("structure"), it.get("component")) for it in (part.get("source") or {}).get("items", [])]
            oc, oo = others_of(part.get("others"))
            if (part.get("source") or {}).get("kind", "follow") == "follow" and items and (oc, oo) != ("keep", 1.0):
                st.update(target=make_target(items), others=dict(colour=oc, opacity=oo), e=1.0)
            st["caption"] = part.get("caption", "")
            m = len(poses)
            emit(m, lambda u, poses=poses, m=m: dict(pose=poses[int(round(u * (m - 1)))]), "main", _mark_label(part))
            pose[0] = poses[-1]
            if st["e"] > 0:
                st.update(e=0.0, target=None)
        elif typ == "card":
            ramp.clear()
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
        ramp.clear(); cur.update(index=len(shots))  # the flight back belongs to the last part shown
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


def closeup_animation(part, ds, fr, fp, n, fps, k0=0, nz=1):
    """Frame function u -> state changes for a close-up's main phase with wiggle, push-in, highlight or an object effect;
    None when the close-up has none of them (it is then drawn exactly as before)."""
    S = fx_settings(part); m = part.get("motion", "orbit"); ang = float(part.get("angle", 30))
    wig, push, hl, eff = S["wiggle"], S["push"], S["highlight"], S["effect"]
    if m != "wiggle" and not push.get("on") and hl["kind"] == "none" and eff["kind"] == "none":
        return None
    T = max(1e-6, (n - 1) / fps); tgt = part.get("target") or {}
    base = fp(); obj_lo, obj_hi = ds.targets_box(tgt, pad=False)
    lo, hi = np.asarray(obj_lo, float) - 0.5, np.asarray(obj_hi, float) + 0.5
    centre = (lo + hi) / 2; radius = float(np.linalg.norm(hi - lo)) / 2
    axis = ds.main_axis(tgt) if eff["kind"] == "section" and eff.get("direction") == "axis" else None

    def fn(u):
        t = u * T; d = {}; fx = {}
        if m == "wiggle":
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
    return extra + (s["k"], s["reveal"], s.get("cut"), round(s["slice"], 3), round(s["e"], 3), json.dumps(s["target"], sort_keys=True), oth,
            s["caption"], tuple(np.round(p["c"], 2)), round(p["tilt"], 3), round(p["az"] % 360.0, 3), round(p["dist"], 2))
