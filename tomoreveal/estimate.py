"""Estimated movie file sizes for every quality and format, from short samples of this movie.

Sizes depend on the content (noisy tomograms compress less well than clean scenes), so a few frames of the movie
are rendered and encoded with each ticked format: 3 samples of 15 consecutive frames, evenly spaced, at Draft and HD.
Cards are sampled apart (they compress to almost nothing). 4K, Square and Portrait are scaled from HD ("rough").
Iterate `steps()` from a timer: each step renders one frame or checks on an encode running in a thread.
"""
import threading

from . import encode
from .storyboard import build_plan

QUALITIES = (("Draft", (960, 540)), ("HD", (1920, 1080)), ("4K", (3840, 2160)), ("Square", (1080, 1080)), ("Portrait", (1080, 1920)))
SAMPLES, SAMPLE_LEN = 3, 15
SCALE_4K = {"h264": 2.8, "prores": 4.0}  # bytes at 4K / bytes at HD, measured (plan 3.4)


def _kind(fmt):
    return "prores" if fmt in ("mov-transparent", "mov-prores") else "h264"


class Estimator:
    def __init__(self, ren, spec):
        self.ren, self.spec = ren, spec
        plan = build_plan(spec, ren.ds); self.fps = plan.fps
        self.frames = plan.frames
        self.normal = [i for i, f in enumerate(self.frames) if not f.get("card")]
        self.cards = [i for i, f in enumerate(self.frames) if f.get("card")]
        self.formats = encode.output_settings(spec.get("output", {}))
        self.alpha = any(encode.FORMATS[f][2] for f in self.formats)
        self.per_frame = {}   # (resolution name, format, "normal" | "card") -> bytes per frame
        self.result = None    # {quality name: {format: bytes}}, rough: set of quality names
        self.rough = set()
        self.cancelled = False

    def sample_indices(self):
        idx = self.normal
        if not idx:
            return []
        if len(idx) <= SAMPLES * SAMPLE_LEN:
            return list(idx)
        out = []
        for s in range(SAMPLES):
            start = int((len(idx) - SAMPLE_LEN) * (s + 0.5) / SAMPLES)
            out += idx[start:start + SAMPLE_LEN]
        return out

    def steps(self):
        """Generator: one rendered frame or one check of a running encode per step; ends with self.result filled."""
        bg = [255 * v for v in __import__("tomoreveal.common", fromlist=["hex2rgb"]).hex2rgb(self.spec.get("style", {}).get("bg", "#000000"))]
        aa = __import__("tomoreveal.look", fromlist=["look_settings"]).look_settings(self.spec)["aa"]["movie"]
        for res_name, size in (("Draft", (960, 540)), ("HD", (1920, 1080))):
            for kind, idx in (("normal", self.sample_indices()), ("card", self.cards[:: max(1, len(self.cards) // 10)][:10])):
                if not idx:
                    continue
                imgs = []
                for i in idx:
                    if self.cancelled:
                        return
                    imgs.append(self.ren.draw(self.frames[i], self.spec, size, aa=aa, transparent=self.alpha))
                    yield
                for fmt, args in self.formats.items():
                    box = {}
                    alpha = encode.FORMATS[fmt][2]
                    frames = imgs if alpha else [encode.composite(im, bg) if im.shape[2] == 4 else im for im in imgs]
                    t = threading.Thread(target=self._encode, args=(box, frames, size, args, alpha), daemon=True)
                    t.start()
                    while t.is_alive():
                        if self.cancelled:
                            return
                        yield
                    if "error" in box:  # (the window says "Could not estimate the file sizes: ...")
                        raise box["error"]
                    self.per_frame[(res_name, fmt, kind)] = box["n"] / max(1, len(imgs))
        self.result = self.totals()

    def _encode(self, box, frames, size, args, alpha):
        """In a thread: the number of bytes, or the error, goes into box."""
        try:
            box["n"] = encode.encode_sample(frames, size, self.fps, args, alpha)
        except Exception as exc:
            box["error"] = exc

    def totals(self):
        out = {}
        n, nc = len(self.normal), len(self.cards)
        for q, (w, h) in QUALITIES:
            out[q] = {}
            for fmt in self.formats:
                def pf(res, kind):
                    return self.per_frame.get((res, fmt, kind), 0.0)
                if q in ("Draft", "HD"):
                    b = pf(q, "normal") * n + pf(q, "card") * nc
                else:
                    f = SCALE_4K[_kind(fmt)] if q == "4K" else (w * h) / (1920 * 1080)
                    b = (pf("HD", "normal") * n + pf("HD", "card") * nc) * f
                    self.rough.add(q)
                out[q][fmt] = b
        return out


def human(nbytes, rough=False):
    """'about 34 MB' with two significant figures."""
    mb = nbytes / 1e6
    if mb >= 1000:
        v, unit = mb / 1000, "GB"
    else:
        v, unit = mb, "MB"
    from math import floor, log10
    if v > 0:
        v = round(v, -int(floor(log10(abs(v)))) + 1)
    txt = f"{v:g} {unit}"
    return f"about {txt}" + (" (rough)" if rough else "")


FORMAT_NAMES = {"mp4": "MP4", "mov": "MOV", "mov-transparent": "Transparent MOV", "mov-prores": "Editing MOV"}
