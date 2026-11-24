"""Arrows, pointers and text boxes (plan 3.5 phase 6).

spec["annotations"] is a list; each item:
  {"id", "kind": "arrow" | "text",
   arrows:  "anchor": {"xyz": [x, y, z] voxels, "structure": key or None, "component": int or None},
            "tail": {"angle": degrees on screen (0 = right, counter-clockwise), "length": fraction of the frame height},
            "label": {"text", "size", "side": "auto"}, "keep_outside": bool,
   text:    "text", "pos": {"x", "y", "anchor"} (fractions of the frame, as overlay items), "pin": anchor or None,
   both:    "style": {"shape", "color", "outline", "width", "head", "size", "box", "align", "bold", "opacity"},
            "show": {"parts": "all" | [part ids], "start": s or None, "end": s or None (seconds from the start of the
                     part), "fade": s, "appear": "fade" | "grow" | "pop"},
            "behind": "normal" | "faint" | "hide", "hide_when_hidden": bool, "visible": bool}
An arrow's tip is pinned to a 3D point: each frame it is projected onto the picture; the tail keeps its angle and length
on screen. Everything is drawn with the overlays after the 3D picture, so it stays sharp at any zoom, and goes into
every movie format (opaque pixels in the transparent MOV).
"""
import copy
import math
import uuid

import numpy as np

SHAPES = (("Arrow", "arrow"), ("Thin arrow", "thin"), ("Pointer", "pointer"), ("Ring", "ring"), ("Bracket", "bracket"))
BOXES = (("dark box", "dark"), ("light box", "light"), ("outline", "outline"), ("no box", "none"))
APPEAR = (("fade in", "fade"), ("grow", "grow"), ("pop", "pop"))
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


def items(spec):
    return [a for a in (spec.get("annotations") or []) if isinstance(a, dict) and a.get("kind") in ("arrow", "text")]


def find(spec, aid):
    return next((a for a in items(spec) if a.get("id") == aid), None)


def duplicate(spec, aid):
    a = find(spec, aid)
    if a is None:
        return None
    b = copy.deepcopy(a); b["id"] = new_id()
    if b["kind"] == "text":
        b["pos"]["x"] = min(0.97, b["pos"]["x"] + 0.03); b["pos"]["y"] = min(0.97, b["pos"]["y"] + 0.03)
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
    P = np.asarray(P, float)
    if pose.get("pos") is not None:
        cam = np.asarray(pose["pos"], float); d = np.asarray(pose["c"], float) - cam; d /= np.linalg.norm(d) or 1.0
        up = np.asarray(pose["up"], float); up = up - d * float(up @ d); up /= np.linalg.norm(up) or 1.0
    else:
        dd, up = fr.axes(pose["tilt"], pose["az"]); dd = np.asarray(dd); up = np.asarray(up)
        cam = np.asarray(pose["c"], float) + pose["dist"] * dd; d = -dd
    right = np.cross(d, up); v = P - cam; z = float(v @ d)
    if fr.ortho:
        h = pose["scale"]
        return 0.5 + float(v @ right) / (2 * h * aspect), 0.5 - float(v @ up) / (2 * h), True
    if z <= 1e-6:
        return 0.5, 0.5, False
    t = math.tan(math.radians(float(pose.get("fov", fr.fov))) / 2)
    return 0.5 + float(v @ right) / (z * t * aspect) / 2, 0.5 - float(v @ up) / (z * t) / 2, True


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
            if how == "grow":
                grown[j] = _ramp((t - s0) / GROW); ain = 1.0
            aout = 1.0 if fade <= 0 else _ramp((s1 - t) / fade) if sh.get("end") is not None else 1.0
            alpha[j] = min(ain, aout)
        anchor = (a.get("anchor") or {}).get("xyz") if a["kind"] == "arrow" else ((a.get("pin") or {}).get("xyz"))
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


def _visible_here(a, s, spec, ds, xyz):
    """0..1: hidden while its structure is switched off, cut away by the reveal or the clip box, or faded in a close-up."""
    if not a.get("hide_when_hidden", True) or xyz is None:
        return 1.0
    from .storyboard import class_spec, clip_box, others_of, target_items
    anc = a.get("anchor") if a["kind"] == "arrow" else (a.get("pin") or {})
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


def draw(layer, P, ren, s, spec, size, zbuf=None):
    """Draw the annotations visible in frame s onto the PIL layer; their boxes go into P.rects as "ann:<id>" (whole
    item), "anntip:<id>" (an arrow's tip) and "anntail:<id>" (its tail / label)."""
    from PIL import ImageDraw
    vis = s.get("ann") or {}
    if not vis:
        return
    W, H = size; d = ImageDraw.Draw(layer); ds = ren.ds; dark = P.dark; px = P.px
    placed = []  # label boxes already drawn (to keep labels apart)
    for a in items(spec):
        if a["id"] not in vis or not a.get("visible", True):
            continue
        alpha, grown, ang = vis[a["id"]]
        st = a.get("style") or {}
        alpha *= float(st.get("opacity", 1.0))
        anc = (a.get("anchor") or {}).get("xyz") if a["kind"] == "arrow" else ((a.get("pin") or {}).get("xyz"))
        tip = None
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
        if a["kind"] == "arrow" and tip is not None:
            box = _arrow(d, a, tip, ang, grown, W, H, px, col, A, outline, st, P, placed)
        else:
            box = _text(d, a, tip, W, H, px, col, A, st, P, placed, dark)
        if box is not None:
            P.rects[f"ann:{a['id']}"] = dict(box=box, shown=True)


def _line(d, a, b, width, fill, outline, ow):
    if outline:
        d.line([a, b], fill=outline, width=int(round(width + 2 * ow)))
    d.line([a, b], fill=fill, width=int(round(width)))


def _arrow(d, a, tip, ang, grown, W, H, px, col, A, outline, st, P, placed):
    t = a.get("tail") or {}; shape = st.get("shape", "arrow")
    angle = math.radians(float(ang if ang is not None else t.get("angle", 135)))
    L = float(t.get("length", 0.12)) * H
    tail = (tip[0] + math.cos(angle) * L, tip[1] - math.sin(angle) * L)
    w = max(1.5, H * 0.005 * float(st.get("width", 1.0))); ow = max(1.0, w * 0.45) if st.get("outline", True) else 0
    fill = col + (A,); oc = outline if st.get("outline", True) else None
    ux, uy = (tip[0] - tail[0]) / max(1e-6, L), (tip[1] - tail[1]) / max(1e-6, L)
    end = (tail[0] + (tip[0] - tail[0]) * grown, tail[1] + (tip[1] - tail[1]) * grown)
    hl = H * 0.022 * float(st.get("head", 1.0)); hw = hl * 0.55
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


def _apart(x, y, tw, th, placed, H, gap=4):
    """Keep labels apart: push this one down below any label it would overlap (never out of the frame)."""
    for _ in range(len(placed) + 1):
        hit = next((p for p in placed if not (x + tw < p[0] or x > p[2] or y + th < p[1] or y > p[3])), None)
        if hit is None:
            break
        y = hit[3] + gap
    return x, min(y, H - th - 2)


def _text(d, a, tip, W, H, px, col, A, st, P, placed, dark):
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
    del dark
    return (x0, y0, x0 + bw, y0 + bh)
