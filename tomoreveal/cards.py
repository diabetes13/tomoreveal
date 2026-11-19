"""Title, end and blank cards: a background (colour, gradient, picture or transparent) with text and picture blocks.
Drawn with PIL only; no 3D rendering is needed."""
import os

import numpy as np

from .overlays import place

MAX_SIZE = 8.0 / 2.4  # text size is relative to 2.4 % of the frame height; at most 8 %


def _rgb(c, default=(0, 0, 0)):
    from PIL import ImageColor
    try:
        return ImageColor.getrgb(c)[:3]
    except (ValueError, AttributeError, TypeError):
        return default


def background(bg, size):
    """RGBA image of a card's background."""
    from PIL import Image
    W, H = size; kind = (bg or {}).get("kind", "color")
    if kind == "transparent":
        return Image.new("RGBA", size, (0, 0, 0, 0))
    if kind == "gradient":
        a, b = np.array(_rgb(bg.get("color", "#000000")), float), np.array(_rgb(bg.get("color2", "#1c1f26")), float)
        t = np.linspace(0, 1, H)[:, None, None]
        arr = np.broadcast_to(a + (b - a) * t, (H, W, 3)).astype(np.uint8)
        return Image.fromarray(np.dstack([arr, np.full((H, W), 255, np.uint8)]), "RGBA")
    if kind == "image" and bg.get("image") and os.path.isfile(bg["image"]):
        try:
            im = Image.open(bg["image"]).convert("RGBA")
            s = max(W / im.width, H / im.height); im = im.resize((max(1, round(im.width * s)), max(1, round(im.height * s))))
            x, y = (im.width - W) // 2, (im.height - H) // 2
            return im.crop((x, y, x + W, y + H))
        except OSError:
            pass
    return Image.new("RGBA", size, _rgb(bg.get("color", "#000000")) + (255,))


def draw_card(part, size, font_fn):
    """(RGBA PIL image, {"block:i": box in pixels}) of a card part."""
    from PIL import Image, ImageDraw
    W, H = size; base = max(10, round(H * 0.024))
    im = background(part.get("background"), size); d = ImageDraw.Draw(im); rects = {}
    for i, b in enumerate(part.get("blocks", [])):
        if b.get("kind") == "text":
            px = max(6, round(base * min(MAX_SIZE, max(0.3, float(b.get("size", 1.2))))))
            f = font_fn(px, bool(b.get("bold")))
            text = b.get("text", "") or ""
            align = b.get("align", "center")
            l, t, r, bt = d.multiline_textbbox((0, 0), text or " ", font=f, align=align, spacing=round(px * 0.25))
            x0, y0 = place(b, r, bt, W, H, (W / 2 - r / 2, H / 2 - bt / 2))
            if text.strip():
                d.multiline_text((x0, y0), text, fill=_rgb(b.get("color", "#ffffff"), (255, 255, 255)) + (255,), font=f, align=align,
                                 spacing=round(px * 0.25))
            rects[f"block:{i}"] = dict(box=(x0, y0, x0 + r, y0 + bt), shown=bool(text.strip()))
        elif b.get("kind") == "image":
            h = max(4, round(H * min(1.0, max(0.02, float(b.get("height", 0.12))))))
            pic = None
            if b.get("path") and os.path.isfile(b["path"]):
                try:
                    pic = Image.open(b["path"]).convert("RGBA")
                except OSError:
                    pic = None
            w = round(h * pic.width / pic.height) if pic is not None else h
            x0, y0 = place(b, w, h, W, H, (W - w - base, H - h - base))
            if pic is not None:
                im.alpha_composite(pic.resize((max(1, w), h)), (round(x0), round(y0)))
            rects[f"block:{i}"] = dict(box=(x0, y0, x0 + w, y0 + h), shown=pic is not None)
    return im, rects


def card_frame(part, size, font_fn, fade=1.0, transparent=False):
    """The card faded from / to black (fade 0..1); with a transparent background it fades out by alpha instead."""
    im, rects = draw_card(part, size, font_fn)
    a = np.asarray(im).astype(np.float32)
    if fade < 1.0:
        if transparent:
            a[..., 3] *= fade
        else:
            a[..., :3] *= fade
    return a, rects
