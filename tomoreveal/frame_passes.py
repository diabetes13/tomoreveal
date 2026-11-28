"""Passes over the frames of a plan, after build_plan has made them: the looks of single parts, caption fades and the
depth range of the depth fade."""
import numpy as np

from .parts import focus_box


OVERVIEW = "(overview)"  # the owner of the depth range after the flight back: the overview of the whole movie


def each_state(frames):
    """Every frame once, then the frames that a dissolve took back from the end of the part before it: they are in the
    movie only as the picture that it blends away. For what depends on the part alone."""
    ids, seen = {id(f) for f in frames}, set()
    yield from frames
    for f in frames:
        src = (f.get("blend") or {}).get("src")
        if src is not None and id(src) not in ids and id(src) not in seen:
            seen.add(id(src))
            yield src


def taken_back(frames):
    """[(frames a dissolve took back, the last frame that stayed in the movie before them)]. What works on a sequence of
    frames gives them what that frame has."""
    ids, seen, out, start = {id(f) for f in frames}, set(), [], 0
    for i, f in enumerate(frames):
        b = f.get("blend")
        if not b:
            continue
        if i == 0 or not frames[i - 1].get("blend"):
            start = i
        src = b["src"]
        if src is None or id(src) in ids or id(src) in seen:
            continue
        seen.add(id(src)); stayed = frames[start - 1] if start else None
        if out and out[-1][1] is stayed:
            out[-1][0].append(src)
        else:
            out.append(([src], stayed))
    return out


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
    "soften": (("as in the Structures tab", None), ("off", 0), ("a little", 1), ("more", 2), ("much", 3)),
}
LOOK_BLEND = 0.6


def part_look(part):
    """The Look settings this part changes: {setting: value} (only known settings with a real choice)."""
    own = (part or {}).get("look") if isinstance((part or {}).get("look"), dict) else {}
    return {k: v for k, v in own.items() if k in PART_LOOK and v is not None and any(v == d for _, d in PART_LOOK[k][1:])}


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
    for run, stayed in taken_back(frames):
        for f in run:
            f["lk"] = own.get(f.get("part"), {})
            for k in ("lkfrom", "lkq"):
                if stayed is not None and k in stayed:
                    f[k] = stayed[k]


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
    for run, stayed in taken_back(frames):
        for f in run:
            if stayed is not None and stayed.get("cap_a") is not None and f.get("caption") == stayed.get("caption"):
                f["cap_a"] = stayed["cap_a"]


def depth_ranges(spec, ds, frames, segments, fr, box, overview):
    """The depth range of the depth fade in every frame ("dr"), so that the fade never pumps: each part's from its first
    frame, and the overview's (pose `overview`) after the flight back. A frame's "rng" says whose it shows; a flight
    glides from one to the other. box: (lo, hi) of what the main view frames."""
    mlo, mhi = box; first = {}
    for sg in segments:
        if sg["phase"] == "main" and sg["part"] is not None and sg["part"] not in first:
            first[sg["part"]] = sg["start"]
    ranges = {OVERVIEW: _range_of(fr, overview, mlo, mhi)}
    for pid, j in first.items():
        part = next((q for q in spec.get("shots", []) if q.get("id") == pid), {})
        try:
            lo_, hi_ = focus_box(part, ds) if part.get("type") == "focus" else (mlo, mhi)
        except (KeyError, IndexError, TypeError, ValueError):
            lo_, hi_ = mlo, mhi
        ranges[pid] = _range_of(fr, frames[j]["pose"], lo_, hi_)
    for f in each_state(frames):
        a, b, q = f.get("rng") or (None, None, 1.0)
        ra, rb = ranges.get(a), ranges.get(b)
        ra, rb = ra or rb, rb or ra
        if ra is not None:
            f["dr"] = ra if q <= 0 else rb if q >= 1 else _between(ra, rb, q)


def _range_of(fr, p, lo_, hi_):
    """(near, far): the depths of the nearest and the farthest corner of the box lo_..hi_ seen with pose p."""
    cpos = np.asarray(p["pos"], float) if p.get("pos") is not None else np.asarray(p["c"]) + p["dist"] * np.asarray(fr.axes(p["tilt"], p["az"])[0])
    dvec = np.asarray(p["c"], float) - cpos; dvec /= np.linalg.norm(dvec) or 1.0
    corners = np.array([[x, y, z] for x in (lo_[0], hi_[0]) for y in (lo_[1], hi_[1]) for z in (lo_[2], hi_[2])])
    dd = (corners - cpos) @ dvec
    return (float(max(1e-3, dd.min())), float(max(dd.max(), dd.min() + 1)))


def _between(ra, rb, q):
    """The range a fraction q of the way from ra to rb. Where the fade starts (near + 0.4 x (far - near)) and where it
    ends (far) change by the same ratio in every frame; near follows from them (it may be negative). Blending near and
    far themselves would switch the fade off in the middle of a flight into a close-up."""
    sa, sb = ra[0] + 0.4 * (ra[1] - ra[0]), rb[0] + 0.4 * (rb[1] - rb[0])
    s, far = sa * (sb / sa) ** q, ra[1] * (rb[1] / ra[1]) ** q
    return ((s - 0.4 * far) / 0.6, far)
