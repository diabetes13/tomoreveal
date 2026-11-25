"""The parts of a movie: what a close-up shows, making parts, bringing old settings up to date, and naming them."""
import copy
import uuid

import numpy as np

from .common import UserError
from .paths import PATH_CAMERA


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
    p.update(fields)
    return p


# Settings that 3.5 gives new parts, and that parts saved before 3.5 lack (missing = the old behaviour).
NEW_IN_3_5 = {"focus": ("tilt", "ease_end", "steady_zoom", "wiggle", "each"), "slide": ("ease_end",), "overview": ("ease_end",),
              "path": ("steadiness", "interp")}
WIGGLE_3_5 = dict(amplitude=5.0, frequency=0.35, shape="circle", smooth=True)  # calmer, and it starts and ends at rest


def as_legacy(part):
    """A part as a project saved before 3.5 would have it: the 3.5 settings of new parts removed (tests, golden data)."""
    p = copy.deepcopy(part)
    for k in NEW_IN_3_5.get(p.get("type"), ()):
        p.pop(k, None)
    return p


def default_parts(rock=True):
    """Slide up, slide back revealing the segmentation, overview: the default movie. The command line keeps the
    rocking overview of every earlier version; the window asks for a still one (rock=False)."""
    return [new_part("slide_up"), new_part("reveal"), new_part("overview", motion="rock" if rock else "none")]


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
