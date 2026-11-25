"""Arrows, pointers and text boxes (plan 3.5 phase 6).

spec["annotations"] is a list; each item:
  {"id", "kind": "arrow" | "text",
   arrows:  "anchor": {"xyz": [x, y, z] voxels, "structure": key or None, "component": int or None},
            "place": "3d" (default: the tip stays on the spot in the volume while the camera moves) | "2d" (the tip
                     stays at "tip2d": [fx, fy], fractions of the picture, whatever the camera does),
            "tail": {"angle": degrees on screen (0 = right, counter-clockwise), "length": fraction of the frame height},
            "label": {"text", "size", "side": "auto"}, "keep_outside": bool,
   text:    "text", "pos": {"x", "y", "anchor"} (fractions of the frame, as overlay items), "pin": anchor or None,
   both:    "style": {"shape", "color", "outline", "width", "head", "size", "box", "align", "bold", "opacity",
                      "fill": "#rrggbb" (box "fill": a box in this colour), "fill_opacity"},
            "more": [anchor, ...] (3.5): more things it points at; an arrow draws a line from its label to each, a text
                    box a thin leader line from the box to each (as in figure callouts),
            "show": {"parts": "all" | [part ids], "start": s or None, "end": s or None (seconds from the start of the
                     part), "fade": s, "appear": "fade" | "grow" | "pop"},
            "behind": "normal" | "faint" | "hide", "hide_when_hidden": bool, "visible": bool}
3.6 (R17): shapes drawn with the mouse, kinds "curve" (a curved arrow), "freehand" (an arrow along a stroke), "line",
"ellipse" and "rect": "points": [[fx, fy], ...] fractions of the picture as drawn (a curve: tail, bend, tip; ellipses
and rectangles: two corners), "place" "2d" | "3d" with "anchor" (as arrows): pinned, the shape is moved so that its
reference point (the tip; the middle of an ellipse or rectangle) stays on the anchor and keeps its shape on screen.
style "heads": "end" | "start" | "both" | "none". Any item may have "layer": "arrows" (default) | "labels" (which
separate transparent movie it goes into, 6.3), and show "appear": "draw" (drawn on over show["draw"] seconds).
An arrow's tip is pinned to a 3D point: each frame it is projected onto the picture; the tail keeps its angle and length
on screen. Everything is drawn with the overlays after the 3D picture, so it stays sharp at any zoom, and goes into
every movie format (opaque pixels in the transparent MOV).
"""
import copy
import math
import uuid

import numpy as np

SHAPES = (("Arrow", "arrow"), ("Thin arrow", "thin"), ("Pointer", "pointer"), ("Ring", "ring"), ("Bracket", "bracket"))
BOXES = (("dark box", "dark"), ("light box", "light"), ("coloured box", "fill"), ("outline", "outline"), ("no box", "none"))
PLACES = (("on the spot in 3D (moves with the camera)", "3d"), ("on the picture in 2D (stays put)", "2d"))
SUPERSAMPLE = 3  # arrows and boxes are drawn this many times larger and scaled down: smooth edges
APPEAR = (("fade in", "fade"), ("grow", "grow"), ("pop", "pop"), ("draw on", "draw"))
DRAWN = ("curve", "freehand", "line", "ellipse", "rect")  # 3.6: shapes drawn with the mouse
KINDS = ("arrow", "text") + DRAWN
HEADS = (("arrowhead at the end", "end"), ("arrowheads at both ends", "both"), ("arrowhead at the start", "start"),
         ("no arrowhead", "none"))
LAYERS = (("Arrows and text", "arrows"), ("Legend and scale bar", "labels"))  # separate transparent movies (6.3)
DRAW_ON = 1.0  # seconds for a shape to draw itself on ("draw on")
BEHIND = (("draw normally", "normal"), ("draw faintly", "faint"), ("hide", "hide"))
GROW = 0.4  # seconds for the shaft to draw from tail to tip


def new_id():
    return "a" + uuid.uuid4().hex[:7]


def new_arrow(xyz, structure=None, component=None, label="", parts="all"):
    return dict(id=new_id(), kind="arrow", visible=True,
                anchor=dict(xyz=[float(v) for v in xyz], structure=structure, component=None if component is None else int(component)),
                tail=dict(angle=135.0, length=0.12), label=dict(text=label, size=1.0, side="auto"), keep_outside=False,
                style=dict(shape="arrow", color="auto", outline=True, width=1.0, head=1.0, opacity=1.0, radius=0.03),
                show=dict(parts=parts, start=None, end=None, fade=0.4, appear="fade"), behind="normal", hide_when_hidden=True)


def new_text(text, x=0.72, y=0.18, parts="all", pin=None):
    return dict(id=new_id(), kind="text", visible=True, text=text, pos=dict(x=float(x), y=float(y), anchor="top-left"), pin=pin,
                style=dict(size=1.0, color="auto", box="dark", align="left", bold=False, opacity=1.0),
                show=dict(parts=parts, start=None, end=None, fade=0.4, appear="fade"), behind="normal", hide_when_hidden=True)


def new_drawn(kind, points, anchor=None, parts="all", heads=None):
    """A shape drawn with the mouse (3.6): points in fractions of the picture; anchor: pinned to that 3D spot."""
    if heads is None:
        heads = "end" if kind in ("curve", "freehand") else "none"
    return dict(id=new_id(), kind=kind, visible=True, points=[[round(float(x), 5), round(float(y), 5)] for x, y in points],
                place="3d" if anchor else "2d", anchor=anchor, label=dict(text="", size=1.0, side="auto"),
                style=dict(color="auto", outline=True, width=1.0, head=1.0, opacity=1.0, heads=heads),
                show=dict(parts=parts, start=None, end=None, fade=0.4, appear="fade"), behind="normal", hide_when_hidden=True)


def is_drawn(a):
    return a.get("kind") in DRAWN


def layer_of(a):
    return a.get("layer") if a.get("layer") in ("arrows", "labels") else "arrows"


def ref_point(a, points=None):
    """The point of a drawn shape that is pinned (its tip; the middle of an ellipse or rectangle), in its own units."""
    P = np.asarray(points if points is not None else a.get("points") or [[0.5, 0.5]], float)
    if a.get("kind") in ("ellipse", "rect") and len(P) >= 2:
        return (P[0] + P[1]) / 2
    return P[-1]


def pointers(a):
    """The extra anchors an item points at (3.5): [{"xyz", "structure", "component"}]."""
    return [m for m in (a.get("more") or []) if isinstance(m, dict) and m.get("xyz") is not None]


def add_pointer(a, xyz, structure=None, component=None):
    a.setdefault("more", []).append(dict(xyz=[float(v) for v in xyz], structure=structure,
                                         component=None if component is None else int(component)))
    return a


def items(spec):
    return [a for a in (spec.get("annotations") or []) if isinstance(a, dict) and a.get("kind") in KINDS
            and (a.get("kind") not in DRAWN or len(a.get("points") or []) >= 2)]


def find(spec, aid):
    return next((a for a in items(spec) if a.get("id") == aid), None)


def duplicate(spec, aid):
    a = find(spec, aid)
    if a is None:
        return None
    b = copy.deepcopy(a); b["id"] = new_id()
    if b["kind"] == "text":
        b["pos"]["x"] = min(0.97, b["pos"]["x"] + 0.03); b["pos"]["y"] = min(0.97, b["pos"]["y"] + 0.03)
    elif b["kind"] in DRAWN:
        b["points"] = [[min(0.99, x + 0.03), min(0.99, y + 0.03)] for x, y in b["points"]]
    else:
        b["tail"]["angle"] = (b["tail"]["angle"] + 15) % 360
    spec.setdefault("annotations", []).append(b)
    return b


def shown_in(a, pid):
    parts = (a.get("show") or {}).get("parts", "all")
    return parts == "all" or (isinstance(parts, list) and pid in parts)


# ---------------------------------------------------------------------------------------------------------------
# timing: done per frame when the plan is built (frames get "ann": {id: [opacity, grown, tail angle or None]})
# ---------------------------------------------------------------------------------------------------------------
def _ramp(x):
    x = min(1.0, max(0.0, x))
    return x * x * (3 - 2 * x)


def project(fr, pose, P, aspect):
    """(fx, fy, in front) of a 3D point for a frame's camera pose (the same camera the renderer sets up)."""
    from .camera import project as _project
    return _project(fr, pose, P, aspect)


def plan_annotations(spec, ds, frames, segments, fr, fps, aspect):
    """Opacity, growth and (for "keep outside") the tail angle of every annotation in every frame."""
    anns = [a for a in items(spec) if a.get("visible", True)]
    if not anns:
        return
    tpart = [None] * len(frames)  # seconds since the start of the part's main phase (None outside main / pause)
    seen_main = {}
    for sg in segments:
        if sg["phase"] in ("main", "pause") and sg["part"] is not None:
            base = seen_main.setdefault(sg["part"], sg["start"])
            for j in range(sg["start"], sg["start"] + sg["count"]):
                tpart[j] = (j - base) / fps
    span = {}
    for sg in segments:
        if sg["phase"] == "main" and sg["part"] is not None:
            span[sg["part"]] = span.get(sg["part"], 0) + sg["count"] / fps
    for a in anns:
        sh = a.get("show") or {}; fade = max(0.0, float(sh.get("fade", 0.4) or 0)); how = sh.get("appear", "fade")
        alpha = np.zeros(len(frames)); grown = np.ones(len(frames)); angle = [None] * len(frames)
        for j, f in enumerate(frames):
            t = tpart[j]; pid = f.get("part")
            if t is None or f.get("card") or not shown_in(a, pid):
                continue
            s0 = float(sh["start"]) if sh.get("start") is not None else 0.0
            s1 = float(sh["end"]) if sh.get("end") is not None else span.get(pid, t + 1)
            if not (s0 <= t <= s1):
                continue
            ain = 1.0 if how == "pop" or fade <= 0 else _ramp((t - s0) / fade)
            if how in ("grow", "draw"):
                grown[j] = _ramp((t - s0) / (GROW if how == "grow" else max(0.05, float(sh.get("draw", DRAW_ON) or DRAW_ON))))
                ain = 1.0
            aout = 1.0 if fade <= 0 else _ramp((s1 - t) / fade) if sh.get("end") is not None else 1.0
            alpha[j] = min(ain, aout)
        anchor = anchor_xyz(a)
        if anchor is not None:  # fade out (over the fade time) while the point is outside the picture
            on = np.array([(lambda p: p[2] and -0.02 <= p[0] <= 1.02 and -0.02 <= p[1] <= 1.02)(project(fr, f["pose"], anchor, aspect))
                           for f in frames], bool)
            nf = max(1, int(round(fade * fps)))
            off_idx = np.nonzero(~on)[0]
            if len(off_idx):
                dist = np.min(np.abs(np.arange(len(frames))[:, None] - off_idx[None, :]), axis=1)
                alpha *= np.clip(dist / nf, 0, 1)
            if a["kind"] == "arrow" and a.get("keep_outside") and a["anchor"].get("structure") is not None:
                centre = _object_centre(ds, a["anchor"])
                if centre is not None:  # point the tail away from the object's middle, smoothed over time
                    raw = []
                    for f in frames:
                        tip = project(fr, f["pose"], anchor, aspect); c = project(fr, f["pose"], centre, aspect)
                        dx, dy = tip[0] - c[0], -(tip[1] - c[1])
                        raw.append(math.atan2(dy, dx) if (dx * dx + dy * dy) > 1e-8 else None)
                    angle = _smooth_angles(raw, int(round(0.5 * fps)))
        for j in range(len(frames)):
            if alpha[j] > 0.002:
                frames[j].setdefault("ann", {})[a["id"]] = [round(float(alpha[j]), 3), round(float(grown[j]), 3),
                                                            None if angle[j] is None else round(angle[j], 2)]


def _object_centre(ds, anchor):
    try:
        k = anchor["structure"]; c = anchor.get("component")
        lo, hi = ds.target_box({"class": k, "component": c} if c is not None else {"class": k}, pad=False)
        return (np.asarray(lo) + np.asarray(hi)) / 2
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def _smooth_angles(raw, w):
    vals = [r for r in raw if r is not None]
    if not vals:
        return [None] * len(raw)
    filled, last = [], vals[0]
    for r in raw:
        last = r if r is not None else last; filled.append(last)
    un = np.unwrap(np.array(filled)); k = max(1, w)
    sm = np.convolve(np.pad(un, k, mode="edge"), np.ones(2 * k + 1) / (2 * k + 1), mode="valid")
    return [math.degrees(v) % 360 for v in sm]


# ---------------------------------------------------------------------------------------------------------------
# drawing (called by Renderer.annotate, after the 3D picture, before the frame leaves the renderer)
# ---------------------------------------------------------------------------------------------------------------
def _colour(c, dark):
    from .common import hex2rgb
    if c in (None, "auto"):
        return (255, 255, 255) if dark else (20, 20, 20)
    return tuple(int(round(255 * v)) for v in hex2rgb(c))


def _visible_here(a, s, spec, ds, xyz, anc=None):
    """0..1: hidden while its structure is switched off, cut away by the reveal or the clip box, or faded in a close-up."""
    if not a.get("hide_when_hidden", True) or xyz is None:
        return 1.0
    from .storyboard import class_spec, clip_box, others_of, target_items
    if anc is None:
        anc = (a.get("pin") or {}) if a["kind"] == "text" else a.get("anchor")
    key = (anc or {}).get("structure")
    x, y, z = xyz
    cb = clip_box(spec, ds.shape)
    if cb and not (cb[0][0] - 1 <= x <= cb[1][0] + 1 and cb[0][1] - 1 <= y <= cb[1][1] + 1 and cb[0][2] - 1 <= z <= cb[1][2] + 1):
        return 0.0
    if key is None:
        return 1.0
    k = ds.id_of(key)
    if k is None or not class_spec(spec, ds, k).get("show", True):
        return 0.0
    cut = s.get("cut") or ("above" if s.get("reveal") else "none")
    if not s.get("reveal") or (cut == "above" and z < float(s["k"]) + 0.5):
        return 0.0  # not yet revealed: the cutting plane has not passed it
    e = float(s.get("e", 0)); items_ = target_items(s.get("target"))
    if e > 0 and items_ and key not in {kk for kk, _ in items_}:
        mode = s.get("others")
        _, op = others_of(mode)
        return 1.0 - e * (1.0 - op)
    return 1.0


def is_2d(a):
    """Placed on the picture (stays put whatever the camera does): 2D arrows and drawn shapes not pinned to anything."""
    if a.get("kind") in DRAWN:
        return a.get("place") != "3d" or not (a.get("anchor") or {}).get("xyz")
    return a.get("kind") == "arrow" and a.get("place") == "2d" and a.get("tip2d") is not None


def anchor_xyz(a):
    """The 3D point an item is pinned to (None: a free text box, or an arrow or shape placed on the picture)."""
    if a.get("kind") == "arrow" or a.get("kind") in DRAWN:
        return None if is_2d(a) else (a.get("anchor") or {}).get("xyz")
    return (a.get("pin") or {}).get("xyz")


class _Draw:
    """Shapes go to a layer SUPERSAMPLE times larger (scaled down afterwards, so edges are smooth); text goes to a
    layer of its own (PIL already smooths text), drawn over the shapes."""
    def __init__(self, size):
        from PIL import Image, ImageDraw
        W, H = size; self.S = S = SUPERSAMPLE; self.size = size
        self.hi = Image.new("RGBA", (W * S, H * S), (0, 0, 0, 0)); self.g = ImageDraw.Draw(self.hi)
        self.tl = Image.new("RGBA", (W, H), (0, 0, 0, 0)); self.t = ImageDraw.Draw(self.tl)

    def _p(self, pts):
        S = self.S
        return [(float(x) * S, float(y) * S) for x, y in pts]

    def _w(self, width):
        return max(1, int(round(float(width) * self.S)))

    def line(self, pts, fill=None, width=1, joint=None):
        self.g.line(self._p(pts), fill=fill, width=self._w(width), joint=joint)

    def polygon(self, pts, fill=None, outline=None, width=1):
        self.g.polygon(self._p(pts), fill=fill, outline=outline, width=self._w(width) if outline else 1)

    def ellipse(self, box, fill=None, outline=None, width=0):
        self.g.ellipse([float(v) * self.S for v in box], fill=fill, outline=outline, width=self._w(width) if width else 0)

    def rounded_rectangle(self, box, radius=0, fill=None, outline=None, width=0):
        self.g.rounded_rectangle([float(v) * self.S for v in box], radius=float(radius) * self.S, fill=fill, outline=outline,
                                 width=self._w(width) if width else 0)

    def text(self, *a, **kw):
        return self.t.text(*a, **kw)

    def textbbox(self, *a, **kw):
        return self.t.textbbox(*a, **kw)

    def textlength(self, *a, **kw):
        return self.t.textlength(*a, **kw)

    def onto(self, layer):
        """Scale the shapes down (only the part that has any), then put shapes and text over the layer."""
        from PIL import Image
        W, H = self.size; S = self.S; bb = self.hi.getbbox()
        out = layer.copy()
        if bb:
            x0, y0 = bb[0] // S, bb[1] // S; x1, y1 = min(W, -(-bb[2] // S)), min(H, -(-bb[3] // S))
            part = self.hi.crop((x0 * S, y0 * S, x1 * S, y1 * S)).resize((x1 - x0, y1 - y0), Image.LANCZOS)
            sm = Image.new("RGBA", (W, H), (0, 0, 0, 0)); sm.paste(part, (x0, y0))
            out = Image.alpha_composite(out, sm)
        out = Image.alpha_composite(out, self.tl)
        layer.paste(out)


def draw(layer, P, ren, s, spec, size, zbuf=None, only=None):
    """Draw the annotations visible in frame s onto the PIL layer; their boxes go into P.rects as "ann:<id>" (whole
    item), "anntip:<id>" (an arrow's tip), "anntail:<id>" (its tail / label) and "anncurve:<id>" (a curve's bend).
    only: draw the items of that layer only ("arrows" / "labels", for separate layer movies)."""
    vis = s.get("ann") or {}
    if not vis:
        return
    W, H = size; d = _Draw(size); ds = ren.ds; dark = P.dark; px = P.px
    try:
        _draw_all(d, P, ren, s, spec, W, H, ds, dark, px, vis, zbuf, only)
    finally:
        d.onto(layer)


def _draw_all(d, P, ren, s, spec, W, H, ds, dark, px, vis, zbuf, only=None):
    placed = []  # label boxes already drawn (to keep labels apart)
    for a in items(spec):
        if a["id"] not in vis or not a.get("visible", True) or (only is not None and layer_of(a) != only):
            continue
        alpha, grown, ang = vis[a["id"]]
        st = a.get("style") or {}
        alpha *= float(st.get("opacity", 1.0))
        anc = anchor_xyz(a)
        tip = (float(a["tip2d"][0]) * W, float(a["tip2d"][1]) * H) if is_2d(a) and a["kind"] == "arrow" else None
        if anc is not None:
            (fx, fy), front = ren.project(anc)
            if not front:
                continue
            tip = (fx * W, fy * H)
            alpha *= _visible_here(a, s, spec, ds, anc)
            if zbuf is not None and a.get("behind", "normal") != "normal":
                ren_ = ren.pl.renderer; ren_.SetWorldPoint(*[float(v) for v in anc], 1.0); ren_.WorldToDisplay(); _, _, zt = ren_.GetDisplayPoint()
                xi, yi = int(round(tip[0])), int(round(H - tip[1]))
                if 0 <= xi < W and 0 <= yi < H:
                    win = zbuf[max(0, yi - 1):yi + 2, max(0, xi - 1):xi + 2]
                    if zt > float(win.max()) + 2e-4:  # behind something
                        alpha *= 0.5 if a["behind"] == "faint" else 0.0
        if alpha <= 0.002:
            continue
        col = _colour(st.get("color"), dark); A = int(round(255 * min(1.0, alpha)))
        outline = (0, 0, 0, int(A * 0.7)) if sum(col) > 380 else (255, 255, 255, int(A * 0.7))
        extra = []  # more things it points at: (index, screen point, opacity)
        for j, m in enumerate(pointers(a)):
            (mx, my), front = ren.project(m["xyz"])
            aj = _visible_here(a, s, spec, ds, m["xyz"], m) if front else 0.0
            if front and -0.02 <= mx <= 1.02 and -0.02 <= my <= 1.02 and aj > 0.002:
                extra.append((j, (mx * W, my * H), aj))
        if a["kind"] in DRAWN:
            box = _drawn(d, a, tip, grown, W, H, px, col, A, outline, st, P, placed)
        elif a["kind"] == "arrow" and tip is not None:
            box = _arrow(d, a, tip, ang, grown, W, H, px, col, A, outline, st, P, placed, extra)
        else:
            box = _text(d, a, tip, W, H, px, col, A, st, P, placed, dark, extra, outline)
        if box is not None:
            P.rects[f"ann:{a['id']}"] = dict(box=box, shown=True)


def _line(d, a, b, width, fill, outline, ow):
    """A line with round ends (and a thin outline around it)."""
    for col, wd in ((outline, width + 2 * ow), (fill, width)) if outline else ((fill, width),):
        d.line([a, b], fill=col, width=wd); r = wd / 2
        for c in (a, b):
            d.ellipse([c[0] - r, c[1] - r, c[0] + r, c[1] + r], fill=col)


def _solid_arrow(d, tail, tip, w, hl, hw, fill, oc, ow):
    """The whole arrow as one shape, so its outline runs round it without seams where the head meets the shaft."""
    pts = _arrow_shape(tail, tip, w, hl, hw)
    if oc:
        d.line(pts + [pts[0], pts[1]], fill=oc, width=2 * ow, joint="curve")
    d.polygon(pts, fill=fill)


def _arrow_shape(tail, tip, w, hl, hw):
    """One outline of a whole arrow: a shaft with a round end, and a head swept back a little at its wings."""
    L = max(1e-6, math.hypot(tip[0] - tail[0], tip[1] - tail[1])); ux, uy = (tip[0] - tail[0]) / L, (tip[1] - tail[1]) / L
    nx, ny = -uy, ux; a = w / 2; hl = min(hl, L * 0.9)
    neck = (tip[0] - ux * hl * 0.72, tip[1] - uy * hl * 0.72)  # where the shaft meets the head (the notch)
    wing = (tip[0] - ux * hl, tip[1] - uy * hl)
    pts = [tip, (wing[0] + nx * hw, wing[1] + ny * hw), (neck[0] + nx * a, neck[1] + ny * a)]
    for i in range(9):  # the round end of the shaft, from one side round the back to the other
        f = math.pi * i / 8
        pts.append((tail[0] + a * (nx * math.cos(f) - ux * math.sin(f)), tail[1] + a * (ny * math.cos(f) - uy * math.sin(f))))
    pts += [(neck[0] - nx * a, neck[1] - ny * a), (wing[0] - nx * hw, wing[1] - ny * hw)]
    return pts


def _head(d, shape, tail, tip, grown, w, hl, hw, fill, oc, ow):
    """A shaft from tail to tip (drawn to `grown` of its length) and its head."""
    if shape == "arrow" and grown >= 0.999:
        return _solid_arrow(d, tail, tip, w, hl, hw, fill, oc, ow)
    L = max(1e-6, math.hypot(tip[0] - tail[0], tip[1] - tail[1])); ux, uy = (tip[0] - tail[0]) / L, (tip[1] - tail[1]) / L
    end = (tail[0] + (tip[0] - tail[0]) * grown, tail[1] + (tip[1] - tail[1]) * grown)
    stop = (end[0] - ux * hl * 0.8, end[1] - uy * hl * 0.8) if shape in ("arrow", "thin") and grown >= 0.999 else end
    lw = max(1.0, w * (0.45 if shape == "thin" else 1.0))
    _line(d, tail, stop, lw, fill, oc, ow)
    if grown < 0.999:
        return
    if shape == "arrow":
        headp = [tip, (tip[0] - ux * hl - uy * hw, tip[1] - uy * hl + ux * hw), (tip[0] - ux * hl + uy * hw, tip[1] - uy * hl - ux * hw)]
        if oc:
            d.polygon(headp, fill=fill, outline=oc, width=int(round(ow)))
        else:
            d.polygon(headp, fill=fill)
    elif shape == "thin":
        for sgn in (1, -1):
            _line(d, tip, (tip[0] - ux * hl + sgn * uy * hw, tip[1] - uy * hl - sgn * ux * hw), lw, fill, oc, ow * 0.6)
    else:  # pointer (and the extra lines of rings and brackets): a dot at the tip
        r = max(2.5, w * 1.6)
        d.ellipse([tip[0] - r, tip[1] - r, tip[0] + r, tip[1] + r], fill=fill, outline=oc, width=int(round(ow)) if oc else 0)


def _arrow(d, a, tip, ang, grown, W, H, px, col, A, outline, st, P, placed, extra=()):
    t = a.get("tail") or {}; shape = st.get("shape", "arrow")
    angle = math.radians(float(ang if ang is not None else t.get("angle", 135)))
    L = float(t.get("length", 0.12)) * H
    tail = (tip[0] + math.cos(angle) * L, tip[1] - math.sin(angle) * L)
    w = max(1.5, H * 0.005 * float(st.get("width", 1.0))); ow = max(1.0, w * 0.45) if st.get("outline", True) else 0
    fill = col + (A,); oc = outline if st.get("outline", True) else None
    ux, uy = (tip[0] - tail[0]) / max(1e-6, L), (tip[1] - tail[1]) / max(1e-6, L)
    end = (tail[0] + (tip[0] - tail[0]) * grown, tail[1] + (tip[1] - tail[1]) * grown)
    hl = H * 0.024 * float(st.get("head", 1.0)); hw = max(hl * 0.5, w * 1.7)
    if shape == "ring":
        r = float(st.get("radius", 0.03)) * H
        if oc:
            d.ellipse([tip[0] - r, tip[1] - r, tip[0] + r, tip[1] + r], outline=oc, width=int(round(w + 2 * ow)))
        d.ellipse([tip[0] - r, tip[1] - r, tip[0] + r, tip[1] + r], outline=fill, width=int(round(w)))
        if (a.get("label") or {}).get("text"):
            s0 = (tip[0] - ux * r, tip[1] - uy * r); _line(d, tail, s0, max(1.0, w * 0.6), fill, oc, ow)
    elif shape == "bracket":
        nx, ny = -uy, ux; r = float(st.get("radius", 0.03)) * H; c = (tip[0] - ux * hl * 0.6, tip[1] - uy * hl * 0.6)
        pts = [(c[0] + nx * r - ux * hl * 0.5, c[1] + ny * r - uy * hl * 0.5), (c[0] + nx * r, c[1] + ny * r),
               (c[0] - nx * r, c[1] - ny * r), (c[0] - nx * r - ux * hl * 0.5, c[1] - ny * r - uy * hl * 0.5)]
        if oc:
            d.line(pts, fill=oc, width=int(round(w + 2 * ow)), joint="curve")
        d.line(pts, fill=fill, width=int(round(w)), joint="curve")
    elif shape == "arrow" and grown >= 0.999:
        _solid_arrow(d, tail, tip, w, hl, hw, fill, oc, ow)
    else:
        stop = (end[0] - ux * hl * 0.8, end[1] - uy * hl * 0.8) if shape in ("arrow", "thin") and grown >= 0.999 else end
        lw = max(1.0, w * (0.45 if shape == "thin" else 1.0))
        _line(d, tail, stop, lw, fill, oc, ow)
        if grown >= 0.999:
            if shape == "arrow":
                headp = [tip, (tip[0] - ux * hl - uy * hw, tip[1] - uy * hl + ux * hw), (tip[0] - ux * hl + uy * hw, tip[1] - uy * hl - ux * hw)]
                if oc:
                    d.polygon(headp, fill=fill, outline=oc, width=int(round(ow)))
                else:
                    d.polygon(headp, fill=fill)
            elif shape == "thin":
                for sgn in (1, -1):
                    _line(d, tip, (tip[0] - ux * hl + sgn * uy * hw, tip[1] - uy * hl - sgn * ux * hw), lw, fill, oc, ow * 0.6)
            elif shape == "pointer":
                r = max(2.5, w * 1.6)
                d.ellipse([tip[0] - r, tip[1] - r, tip[0] + r, tip[1] + r], fill=fill, outline=oc, width=int(round(ow)) if oc else 0)
    box = [min(tip[0], tail[0]) - 8, min(tip[1], tail[1]) - 8, max(tip[0], tail[0]) + 8, max(tip[1], tail[1]) + 8]
    P.rects[f"anntip:{a['id']}"] = dict(box=(tip[0] - 9, tip[1] - 9, tip[0] + 9, tip[1] + 9), shown=True)
    for j, q, aj in extra:  # more things it points at: from the same tail (the label), with the same head
        Aj = int(round(A * aj)); fj = col + (Aj,); oj = (oc[:3] + (int(oc[3] * aj),)) if oc else None
        _head(d, shape if shape in ("arrow", "thin", "pointer") else "pointer", tail, q, grown, w, hl, hw, fj, oj, ow)
        P.rects[f"anntip:{a['id']}:{j}"] = dict(box=(q[0] - 9, q[1] - 9, q[0] + 9, q[1] + 9), shown=True)
        box = [min(box[0], q[0] - 8), min(box[1], q[1] - 8), max(box[2], q[0] + 8), max(box[3], q[1] + 8)]
    lab = a.get("label") or {}
    if lab.get("text"):
        f = P.font(max(9, round(px * float(lab.get("size", 1.0))))); l, tp, r, b = d.textbbox((0, 0), lab["text"], font=f)
        tw, th = r - l, b - tp; gap = px * 0.35
        x = tail[0] + (gap if math.cos(angle) >= 0 else -gap - tw); y = tail[1] - th / 2 - tp
        x = min(max(2, x), W - tw - 2); y = min(max(2, y), H - th - 2)
        if x <= tip[0] <= x + tw and y <= tip[1] <= y + th:  # clamping would cover the tip: the other side
            x = tail[0] - gap - tw if math.cos(angle) >= 0 else tail[0] + gap; x = min(max(2, x), W - tw - 2)
        x, y = _apart(x, y, tw, th, placed, H)
        d.text((x, y), lab["text"], font=f, fill=col + (A,), stroke_width=max(1, round(px * 0.09)), stroke_fill=outline)
        placed.append((x, y, x + tw, y + th + tp))
        box = [min(box[0], x - 4), min(box[1], y - 4), max(box[2], x + tw + 4), max(box[3], y + th + tp + 4)]
        P.rects[f"anntail:{a['id']}"] = dict(box=(x - 4, y - 4, x + tw + 4, y + th + tp + 4), shown=True)
    else:
        P.rects[f"anntail:{a['id']}"] = dict(box=(tail[0] - 9, tail[1] - 9, tail[0] + 9, tail[1] + 9), shown=True)
    return tuple(box)


def shown_points(a, W, H, ref=None):
    """A drawn shape's points in pixels of a W x H picture; ref: where its pinned point is now (pixels), or None."""
    P = np.asarray(a.get("points"), float) * [W, H]
    if ref is not None:
        P = P + (np.asarray(ref, float) - ref_point(a, P))
    return P


def outline_of(a, P):
    """The line a drawn shape is drawn along (pixels)."""
    from . import drawing
    k = a["kind"]
    if k == "curve" and len(P) >= 3:
        return drawing.bezier(P[0], P[1], P[2])
    if k == "freehand":
        return drawing.smooth(P)
    if k == "ellipse":
        return drawing.ellipse(P[0], P[1])
    if k == "rect":
        return drawing.rect(P[0], P[1])
    return np.asarray([P[0], P[-1]], float)


def _drawn(d, a, ref, grown, W, H, px, col, A, outline, st, P, placed):
    """A curve, freehand stroke, line, ellipse or rectangle (3.6), drawn on to `grown` of its length."""
    from . import drawing
    pts = shown_points(a, W, H, ref if not is_2d(a) else None)
    line = outline_of(a, pts); L = drawing.length(line)
    if L < 1.0:
        return None
    w = max(1.5, H * 0.005 * float(st.get("width", 1.0))); ow = max(1.0, w * 0.45) if st.get("outline", True) else 0
    fill = col + (A,); oc = outline if st.get("outline", True) else None
    hl = H * 0.024 * float(st.get("head", 1.0)); hw = max(hl * 0.5, w * 1.7)
    heads = st.get("heads", "end") if a["kind"] in ("curve", "freehand", "line") else "none"
    shown = drawing.cut(line, grown)
    at_end = heads in ("end", "both") and grown >= 0.999; at_start = heads in ("start", "both")
    body = shown
    if at_end:  # the shaft stops under the head, so its round end does not poke through the point
        body = drawing.cut(body, max(0.0, 1 - hl * 0.8 / max(1e-6, drawing.length(body))))
    if at_start and len(body) >= 2:
        rev = body[::-1]; body = drawing.cut(rev, max(0.0, 1 - hl * 0.8 / max(1e-6, drawing.length(rev))))[::-1]
    closed = a["kind"] in ("ellipse", "rect") and grown >= 0.999
    pl = [tuple(p) for p in body]
    for c, wd in ((oc, w + 2 * ow), (fill, w)) if oc else ((fill, w),):
        if len(pl) >= 2:
            d.line(pl + ([pl[1]] if closed else []), fill=c, width=wd, joint="curve")
            if not closed:
                r = wd / 2
                for q in (pl[0], pl[-1]):
                    d.ellipse([q[0] - r, q[1] - r, q[0] + r, q[1] + r], fill=c)
    for on, tip, seq in ((at_end, line[-1], line), (at_start and grown > 0.05, line[0], line[::-1])):
        if on:
            u = drawing.end_direction(seq, hl); hp = drawing.head(tip, u, hl, hw)
            if oc:
                d.polygon(hp, fill=fill, outline=oc, width=int(round(ow)))
            else:
                d.polygon(hp, fill=fill)
    xs, ys = line[:, 0], line[:, 1]
    box = [float(xs.min()) - 8, float(ys.min()) - 8, float(xs.max()) + 8, float(ys.max()) + 8]
    i0, i1 = (0, 1) if a["kind"] in ("ellipse", "rect") else (0, len(pts) - 1)
    for name, q in (("anntip", pts[i1]), ("anntail", pts[i0])):
        P.rects[f"{name}:{a['id']}"] = dict(box=(q[0] - 9, q[1] - 9, q[0] + 9, q[1] + 9), shown=True)
    if a["kind"] == "curve" and len(pts) >= 3:
        m = drawing.bezier_middle(pts[0], pts[1], pts[2])
        P.rects[f"anncurve:{a['id']}"] = dict(box=(m[0] - 9, m[1] - 9, m[0] + 9, m[1] + 9), shown=True)
    lab = a.get("label") or {}
    if lab.get("text"):  # beside the start of the line (the top left corner of an ellipse or rectangle)
        f = P.font(max(9, round(px * float(lab.get("size", 1.0))))); l, tp, r, b = d.textbbox((0, 0), lab["text"], font=f)
        tw, th = r - l, b - tp; gap = px * 0.35; q = pts[0]
        back = line[min(len(line) - 1, 3)] - line[0] if a["kind"] not in ("ellipse", "rect") else np.array([1.0, 1.0])
        x = q[0] - gap - tw if back[0] > 0 else q[0] + gap; y = q[1] - th / 2 - tp
        x = min(max(2, x), W - tw - 2); y = min(max(2, y), H - th - 2); x, y = _apart(x, y, tw, th, placed, H)
        d.text((x, y), lab["text"], font=f, fill=col + (A,), stroke_width=max(1, round(px * 0.09)), stroke_fill=outline)
        placed.append((x, y, x + tw, y + th + tp))
        box = [min(box[0], x - 4), min(box[1], y - 4), max(box[2], x + tw + 4), max(box[3], y + th + tp + 4)]
    return tuple(box)


def _apart(x, y, tw, th, placed, H, gap=4):
    """Keep labels apart: push this one down below any label it would overlap (never out of the frame)."""
    for _ in range(len(placed) + 1):
        hit = next((p for p in placed if not (x + tw < p[0] or x > p[2] or y + th < p[1] or y > p[3])), None)
        if hit is None:
            break
        y = hit[3] + gap
    return x, min(y, H - th - 2)


def _text(d, a, tip, W, H, px, col, A, st, P, placed, dark, extra=(), outline=None):
    from .overlays import place
    text = str(a.get("text") or "")
    if not text:
        return None
    size = float(st.get("size", 1.0)); f = P.font(max(9, round(px * 1.15 * size)), bool(st.get("bold")))
    lines = text.splitlines() or [text]
    widths = [d.textlength(ln, font=f) for ln in lines]; lh = round(px * 1.15 * size * 1.25)
    pad = px * 0.5; bw, bh = max(widths) + 2 * pad, lh * len(lines) + 2 * pad * 0.8
    if tip is not None:  # pinned to a structure: the box sits next to it
        x0, y0 = tip[0] + px * 0.6, tip[1] - bh / 2
    else:
        pos = a.get("pos") or {}
        x0, y0 = place(dict(x=pos.get("x", 0.72), y=pos.get("y", 0.18), anchor=pos.get("anchor", "top-left")), bw, bh, W, H, (W * 0.72, H * 0.18))
    x0, y0 = min(max(0, x0), W - bw), min(max(0, y0), H - bh)
    x0, y0 = _apart(x0, y0, bw, bh, placed, H)
    box = st.get("box", "dark")
    back = {"dark": (0, 0, 0, int(A * 0.6)), "light": (255, 255, 255, int(A * 0.75))}.get(box)
    if box == "fill":  # a box in a colour chosen from the colour wheel; automatic text: black or white, whichever reads
        fc = _colour(st.get("fill") or "#3a6ea5", dark)
        back = fc + (int(A * float(st.get("fill_opacity", 0.9))),)
        if st.get("color", "auto") in (None, "auto"):
            col = (20, 20, 20) if 0.299 * fc[0] + 0.587 * fc[1] + 0.114 * fc[2] > 150 else (255, 255, 255)
    if box == "light" and st.get("color", "auto") in (None, "auto"):
        col = (20, 20, 20)
    elif box == "dark" and st.get("color", "auto") in (None, "auto"):
        col = (255, 255, 255)
    if back:
        d.rounded_rectangle([x0, y0, x0 + bw, y0 + bh], radius=px * 0.4, fill=back)
    elif box == "outline":
        d.rounded_rectangle([x0, y0, x0 + bw, y0 + bh], radius=px * 0.4, outline=col + (A,), width=max(1, round(px * 0.12)))
    al = st.get("align", "left")
    for i, (ln, w) in enumerate(zip(lines, widths)):
        x = x0 + pad + ((bw - 2 * pad - w) / 2 if al == "center" else (bw - 2 * pad - w) if al == "right" else 0)
        d.text((x, y0 + pad * 0.8 + i * lh), ln, font=f, fill=col + (A,))
    placed.append((x0, y0, x0 + bw, y0 + bh))
    lc = _colour(st.get("color"), dark)  # the leaders run over the picture: white on dark, black on light backgrounds
    lw = max(1.0, H * 0.0022 * float(st.get("width", 1.0)))
    if extra:  # leader lines all start at the middle of the side of the box that faces the things they point at
        mx = sum(q[0] for _, q, _ in extra) / len(extra); my = sum(q[1] for _, q, _ in extra) / len(extra)
        gap = px * 0.3
        if mx > x0 + bw:
            sx, sy = x0 + bw + gap, y0 + bh / 2
        elif mx < x0:
            sx, sy = x0 - gap, y0 + bh / 2
        else:
            sx, sy = x0 + bw / 2, (y0 + bh + gap if my > y0 + bh / 2 else y0 - gap)
    for j, q, aj in extra:  # leader lines from the box to more things it points at, with a small dot at each
        Aj = int(round(A * aj))
        if x0 < q[0] < x0 + bw and y0 < q[1] < y0 + bh:
            continue
        if outline is not None:
            d.line([(sx, sy), q], fill=outline[:3] + (int(outline[3] * aj),), width=int(round(lw + 2)))
        d.line([(sx, sy), q], fill=lc + (Aj,), width=int(round(lw)))
        r = max(2.0, lw * 1.6); d.ellipse([q[0] - r, q[1] - r, q[0] + r, q[1] + r], fill=lc + (Aj,))
        P.rects[f"anntip:{a['id']}:{j}"] = dict(box=(q[0] - 9, q[1] - 9, q[0] + 9, q[1] + 9), shown=True)
    return (x0, y0, x0 + bw, y0 + bh)  # the box only (moving and aligning use it)
