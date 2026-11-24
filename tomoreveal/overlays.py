"""Text and marks drawn on top of the 3D picture: legend, slice number, scale bar, title and caption.

Settings live in spec["overlays"][name]. Positions are fractions of the frame (x, y) plus the point of the item's box
that sits there (anchor), so a layout looks the same at any size. x = y = None means the item's default place, which
is exactly where TomoReveal 3.3 drew it. Each part may override show / hide: part["overlays"][name].
"""
import copy
import math

NAMES = ("legend", "slice_label", "scale_bar", "title", "caption")
LABELS = {"legend": "Legend", "slice_label": "Slice number", "scale_bar": "Scale bar", "title": "Title", "caption": "Captions"}
ANCHORS = tuple(f"{v}-{h}" for v in ("top", "middle", "bottom") for h in ("left", "center", "right"))
DEFAULTS = {
    "legend": dict(show=True, x=None, y=None, anchor="top-right", size=1.0, box=True, box_opacity=0.47, columns=1, title="",
                   text_color="auto"),
    "slice_label": dict(show=True, x=None, y=None, anchor="top-left", size=1.0, format="slice {k}/{n}", show_depth=False),
    "scale_bar": dict(show=False, x=0.97, y=0.95, anchor="bottom-right", size=1.0, length_nm="auto", thickness=1.0, color="auto",
                      label=True, mode="screen"),
    "title": dict(show=True, x=None, y=None, anchor="bottom-left", size=1.0),
    "caption": dict(show=True, x=None, y=None, anchor="bottom-center", size=1.4, box="dark", color="auto", bold=False, fade=0.0),
}
CAPTION_BOXES = (("see-through box", "dark"), ("light box", "light"), ("outline", "outline"), ("no box (text with a shadow)", "none"))
CAPTION_COLOURS = (("automatic", "auto"), ("white", "#ffffff"), ("black", "#000000"), ("yellow", "#ffd23f"), ("cyan", "#45b9e8"))
SIZE_RANGE = (0.5, 3.0)
NICE = (1, 2, 5)


STYLE_SHOW = {"legend": "legend", "slice_label": "slice_label"}  # these two are switched on and off in spec["style"] (3.1)


def overlay_settings(spec):
    """All five items with their defaults filled in. Legend and slice number are shown according to
    style.legend / style.slice_label, as in every earlier version."""
    out = copy.deepcopy(DEFAULTS); st = spec.get("style", {}); own = spec.get("overlays")
    if isinstance(own, dict):
        for n in NAMES:
            out[n].update(own.get(n) or {})
    for n, key in STYLE_SHOW.items():
        if key in st:
            out[n]["show"] = bool(st[key])
    return out


def set_show(spec, name, on):
    """Switch one item on or off for the whole movie."""
    if name in STYLE_SHOW:
        spec.setdefault("style", {})[STYLE_SHOW[name]] = bool(on)
    ensure(spec)[name]["show"] = bool(on)


def set_item(spec, name, **kw):
    ensure(spec)[name].update(kw)


def reset_position(spec, name):
    d = DEFAULTS[name]; set_item(spec, name, x=d["x"], y=d["y"], anchor=d["anchor"])


def ensure(spec):
    """spec["overlays"] with every item present (for the window, which edits it in place)."""
    spec["overlays"] = overlay_settings(spec)
    return spec["overlays"]


def part_choice(spec, part_id, name):
    """"default", "show" or "hide" for one item during one part. Cards (phase 3) hide everything by default."""
    for p in spec.get("shots", []):
        if p.get("id") == part_id:
            v = (p.get("overlays") or {}).get(name, "default")
            if v == "default" and p.get("type") == "card" and name != "caption":
                return "hide"
            return v if v in ("show", "hide") else "default"
    return "default"


# ---------------------------------------------------------------------------------------------------------------
# scale
# ---------------------------------------------------------------------------------------------------------------
def voxel_size(spec, ds):
    """Voxel size in Å: the user's value (spec["data"]["voxel_size_nm"]) or the file's; None when unknown."""
    v = (spec.get("data") or {}).get("voxel_size_nm")
    if v:
        return float(v) * 10.0
    return float(ds.vox) if ds.vox else None


def suspicious_voxel(vox):
    """MRC headers often say 0 or 1.0 Å by mistake."""
    return not vox or abs(float(vox) - 1.0) < 1e-6


def nice_lengths(lo, hi):
    """'Nice' lengths 1, 2, 5 x 10^n between lo and hi (nm), largest last."""
    out = []
    if hi <= 0:
        return out
    e = math.floor(math.log10(max(lo, 1e-9))) - 1
    while True:
        for m in NICE:
            v = m * 10 ** e
            if v > hi:
                return out
            if v >= lo:
                out.append(v)
        e += 1


def px_per_voxel(pose, frame_h, fov, ortho):
    """Pixels per voxel at the camera's focal point (the centre of what is looked at)."""
    if ortho:
        return frame_h / (2.0 * pose["scale"])
    return frame_h / (2.0 * pose["dist"] * math.tan(math.radians(fov) / 2.0))


def auto_length(px_per_nm, frame_w):
    """The largest nice length whose bar is 10 % to 25 % of the frame width (nm); a nice length close to that if none."""
    if px_per_nm <= 0:
        return None
    cands = nice_lengths(0.10 * frame_w / px_per_nm, 0.25 * frame_w / px_per_nm)
    if cands:
        return float(cands[-1])
    target = 0.15 * frame_w / px_per_nm
    e = 10 ** math.floor(math.log10(target))
    return float(min((m * e for m in (1, 2, 5, 10)), key=lambda v: abs(v - target)))


def length_text(nm):
    if nm >= 1000:
        v = nm / 1000.0
        return f"{v:g} µm"
    return f"{nm:g} nm"


def add_bar_lengths(frames, spec, ds, w, h, fov, ortho):
    """Automatic scale bar length (nm) per part, chosen at the start of each part's main phase: frames["bar_nm"]."""
    vox = voxel_size(spec, ds)
    if not vox:
        return
    first = {}
    for f in frames:
        if f.get("phase") == "main" and f.get("part") not in first:
            first[f.get("part")] = f["pose"]
    per_part = {}
    last = None
    for f in frames:
        pid = f.get("part")
        if pid not in per_part:
            pose = first.get(pid, f["pose"])
            per_part[pid] = auto_length(px_per_voxel(pose, h, fov, ortho) * 10.0 / vox, w) if pid in first or last is None else last
        f["bar_nm"] = last = per_part[pid]


# ---------------------------------------------------------------------------------------------------------------
# layout and drawing
# ---------------------------------------------------------------------------------------------------------------
def place(item, bw, bh, W, H, default_xy):
    """Top-left corner of an item's box (bw x bh) in pixels, kept inside the frame."""
    if item.get("x") is None or item.get("y") is None:
        x0, y0 = default_xy
    else:
        v, hz = (item.get("anchor") or "top-left").split("-")
        fx = {"left": 0.0, "center": 0.5, "right": 1.0}.get(hz, 0.0); fy = {"top": 0.0, "middle": 0.5, "bottom": 1.0}.get(v, 0.0)
        x0, y0 = float(item["x"]) * W - fx * bw, float(item["y"]) * H - fy * bh
    x0 = min(max(0.0, x0), max(0.0, W - bw)); y0 = min(max(0.0, y0), max(0.0, H - bh))
    return x0, y0


def anchor_for(box, W, H):
    """After a drag: the anchor from the nearest side, and its point as fractions. box = (x0, y0, x1, y1) pixels."""
    x0, y0, x1, y1 = box; cx, cy = (x0 + x1) / 2 / W, (y0 + y1) / 2 / H
    hz = "left" if cx < 1 / 3 else "right" if cx > 2 / 3 else "center"
    v = "top" if cy < 1 / 3 else "bottom" if cy > 2 / 3 else "middle"
    ax = {"left": x0, "center": (x0 + x1) / 2, "right": x1}[hz] / W
    ay = {"top": y0, "middle": (y0 + y1) / 2, "bottom": y1}[v] / H
    return f"{v}-{hz}", ax, ay


def _size(item):
    return min(SIZE_RANGE[1], max(SIZE_RANGE[0], float(item.get("size", 1.0))))


class Painter:
    """Draws the overlay items of one frame onto a PIL RGBA layer and records each item's box (pixels):
    rects[name] = dict(box=(x0, y0, x1, y1), shown=bool)."""

    def __init__(self, layer, font_fn, size, bg_rgb):
        from PIL import ImageDraw
        self.layer, self.d, self.font = layer, ImageDraw.Draw(layer), font_fn
        self.W, self.H = size; self.px = max(10, round(self.H * 0.024))
        dark = sum(bg_rgb) < 1.5; self.dark = dark
        self.fg = (255, 255, 255, 255) if dark else (0, 0, 0, 255)
        self.rects = {}

    def _back(self, opacity):
        a = float(opacity)
        return (0, 0, 0, round(255 * a)) if self.dark else (255, 255, 255, round(255 * min(1.0, a * 1.25)))

    def _record(self, name, x0, y0, bw, bh, shown):
        self.rects[name] = dict(box=(x0, y0, x0 + bw, y0 + bh), shown=bool(shown))

    def text_item(self, name, item, text, default_fn, shown):
        """Plain text (slice label, title). default_fn(px) -> draw origin of the 3.3 layout."""
        px = round(self.px * _size(item)) if _size(item) != 1.0 else self.px
        f = self.font(px); l, t, r, b = self.d.textbbox((0, 0), text or " ", font=f)
        x0, y0 = place(item, r, b, self.W, self.H, default_fn(px))
        if shown and text:
            self.d.text((x0, y0), text, fill=self.fg, font=f)
        self._record(name, x0, y0, r, b, shown and bool(text))

    def caption(self, item, text, shown, alpha=1.0):
        """The part's caption. Options (3.5): box dark (see-through, as before) / light / outline / none, colour, bold;
        alpha < 1 while it fades in or out."""
        s = _size(item); px = self.px * s / 1.4
        try:
            big = self.font(round(self.px * s), bool(item.get("bold")))
        except TypeError:
            big = self.font(round(self.px * s))
        wt = self.d.textlength(text or " ", font=big)
        bw, bh = wt + 2 * px, 2.4 * px
        default = ((self.W - wt) / 2 - px, self.H - round(self.px * 3.4) - px * 0.4)
        x0, y0 = place(item, bw, bh, self.W, self.H, default)
        if shown and text and alpha > 0.002:
            box = item.get("box", "dark"); A = max(0.0, min(1.0, float(alpha)))
            light = box == "light" or (box == "dark" and not self.dark)  # the box is light: dark text
            if item.get("color", "auto") in (None, "auto"):
                fg = (0, 0, 0) if (light if box in ("dark", "light") else not self.dark) else (255, 255, 255)
            else:
                from .common import hex2rgb
                fg = tuple(int(round(255 * v)) for v in hex2rgb(item["color"]))
            if box == "dark":
                b = self._back(0.47); self.d.rounded_rectangle([x0, y0, x0 + bw, y0 + bh], radius=px * 0.5, fill=b[:3] + (round(b[3] * A),))
            elif box == "light":
                self.d.rounded_rectangle([x0, y0, x0 + bw, y0 + bh], radius=px * 0.5, fill=(255, 255, 255, round(220 * A)))
            elif box == "outline":
                self.d.rounded_rectangle([x0, y0, x0 + bw, y0 + bh], radius=px * 0.5, outline=fg + (round(255 * A),),
                                         width=max(1, round(px * 0.12)))
            kw = {}
            if box == "none":  # a soft shadow keeps the text readable over anything
                kw = dict(stroke_width=max(1, round(px * 0.1)), stroke_fill=((0, 0, 0) if sum(fg) > 380 else (255, 255, 255)) + (round(160 * A),))
            self.d.text((x0 + px, y0 + px * 0.4), text, fill=fg + (round(255 * A),), font=big, **kw)
        self._record("caption", x0, y0, bw, bh, shown and bool(text))

    def legend(self, item, rows, shown):
        """rows: [(name, colour)]."""
        s = _size(item); px = round(self.px * s) if s != 1.0 else self.px; f = self.font(px)
        title = (item.get("title") or "").strip(); step = round(px * 1.45)
        cols = 2 if int(item.get("columns", 1)) == 2 and len(rows) > 1 else 1
        per = math.ceil(len(rows) / cols) if rows else 0
        groups = [rows[i * per:(i + 1) * per] for i in range(cols)] if rows else [[]]
        widths = [max([self.d.textlength(n, font=f) for n, _ in g] or [px * 4]) + px * 1.5 for g in groups]
        tw = self.d.textlength(title, font=f) if title else 0
        gap = px * 1.6
        inner_w = max(sum(widths) + gap * (cols - 1), tw)
        bw = inner_w + px * 1.9; bh = step * (per + (1 if title else 0)) + px * 0.7
        x0, y0 = place(item, bw, bh, self.W, self.H, (self.W - px * 0.6 - bw, round(self.px * 1.1) - self.px * 0.5)
                       if s == 1.0 else (self.W - px * 0.6 - bw, px * 0.6))
        fg = self.fg
        if item.get("text_color", "auto") not in ("auto", None, ""):
            from PIL import ImageColor
            fg = ImageColor.getrgb(item["text_color"])[:3] + (255,)
        if shown and rows:
            box = bool(item.get("box", True))
            if box:
                self.d.rounded_rectangle([x0, y0, x0 + bw, y0 + bh], radius=px * 0.5, fill=self._back(item.get("box_opacity", 0.47)))
            light = sum(fg[:3]) > 380
            kw = {} if box else dict(stroke_width=1, stroke_fill=(0, 0, 0, 255) if light else (255, 255, 255, 255))
            x, y = x0 + px * 0.6, y0 + px * 0.5
            if title:
                self.d.text((x, y), title, fill=fg, font=f, **kw); y += step
            for g, gw in zip(groups, widths):
                yy = y
                for n, c in g:
                    self.d.rectangle([x, yy + px * 0.15, x + px * 0.95, yy + px * 1.1], fill=c,
                                     outline=None if box else ((0, 0, 0) if light else (255, 255, 255)))
                    self.d.text((x + px * 1.5, yy), n, fill=fg, font=f, **kw); yy += step
                x += gw + gap
        self._record("legend", x0, y0, bw, bh, shown and bool(rows))

    def scale_bar(self, item, length_px, text, shown, color=None, at=None):
        """A bar of length_px pixels with its label above. at: (x, y) centre of an on-slice bar's label instead of
        a flat bar (the bar itself is drawn in 3D)."""
        s = _size(item); px = round(self.px * s); f = self.font(px)
        col = color or (self.fg if item.get("color", "auto") == "auto" else None)
        if col is None:
            from PIL import ImageColor
            col = ImageColor.getrgb(item["color"])[:3] + (255,)
        th = max(2, round(px * 0.35 * float(item.get("thickness", 1.0))))
        label = bool(item.get("label", True)) and bool(text)
        l, t, r, b = self.d.textbbox((0, 0), text or " ", font=f)
        if at is not None:  # label of a bar lying on the slice
            bw, bh = r, b; x0, y0 = at[0] - r / 2, at[1] - b - th
            if shown and label:
                self.d.text((x0, y0), text, fill=col, font=f, stroke_width=1, stroke_fill=(0, 0, 0, 255) if sum(col[:3]) > 380 else (255, 255, 255, 255))
            self._record("scale_bar", x0, y0, bw, bh, shown)
            return
        L = max(1.0, float(length_px or 0))
        bw = max(L, r); bh = th + ((b + px * 0.3) if label else 0)
        x0, y0 = place(item, bw, bh, self.W, self.H, (self.W - bw - px * 1.5, self.H - bh - px * 1.1))
        if shown and length_px:
            bx = x0 + (bw - L) / 2
            if label:
                self.d.text((x0 + (bw - r) / 2, y0), text, fill=col, font=f)
            self.d.rectangle([bx, y0 + bh - th, bx + L, y0 + bh], fill=col)
        self._record("scale_bar", x0, y0, bw, bh, shown and bool(length_px))
