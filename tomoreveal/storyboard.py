"""Camera framing, the clip box, and turning the movie settings into per-frame states."""
import json
import math

import numpy as np


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


def default_spec(ds):
    return dict(
        classes={c["key"]: dict(name=c["name"], color=c["color"], show=not (c["background"] or c.get("hidden"))) for c in ds.classes.values()},
        view=dict(tilt=42, az=0, zoom=1.0),
        style=dict(bg="#000000", legend=True, slice_label=True, title="", contrast=[1, 99], invert=False, ortho=False, fov=30,
                   tomogram=dict(show=True, opacity=1.0)),
        shots=[dict(type="sweep", on=True, seconds=7, tomogram=tomogram_setting()),
               dict(type="reveal", on=True, seconds=7, tomogram=tomogram_setting()),
               dict(type="overview", on=True, motion="rock", seconds=6, angle=25, tomogram=tomogram_setting())],
        return_to_overview=True, z_range=None, clip=None,
        output=dict(size=[1920, 1080], fps=30, crf=20, aa=True))


def merge_settings(spec, new, same_grid=True):
    """Keep the user's settings (spec) when what is loaded changes; new is default_spec() of the new data.
    Every structure that is still there keeps its name, colour and visibility (they are keyed by its stable key);
    new structures get their defaults; close-ups of structures that are gone are dropped."""
    spec["classes"] = {k: spec["classes"].get(k) or v for k, v in new["classes"].items()}
    spec["shots"] = [s for s in spec["shots"] if s["type"] != "focus" or (s.get("target") or {}).get("class") in new["classes"]]
    if not same_grid:  # the slice range and the clip box belonged to the old grid
        spec["clip"] = None; spec["z_range"] = None
    return spec


def focus_shot(target, caption, **kw):
    s = dict(type="focus", on=True, target=target, caption=caption, others="grey", motion="orbit", seconds=5,
             zoom=1.0, angle=30, tomogram=tomogram_setting())
    s.update(kw)
    return s


def build_timeline(spec, ds):
    """Storyboard -> list of per-frame states, plus (frame, label) marks for the scrubber."""
    out = spec.get("output", {}); fps = int(out.get("fps", 30)); w, h = out.get("size", [1920, 1080])
    style, v = spec.get("style", {}), spec.get("view", {})
    shape = ds.shape; nz, ny, nx = shape
    has_tomo, has_seg = ds.tomo is not None, ds.has_seg()
    base = default_opacity(spec, has_tomo)  # tomogram slice opacity when no shot asks for another

    def target_of(shot):
        if not has_tomo:
            return 0.0
        t = shot_tomogram(shot)
        return {"default": base, "shown": 1.0, "hidden": 0.0}.get(t["mode"], float(min(1.0, max(0.0, t["opacity"]))))
    zr = spec.get("z_range") or [0, nz - 1]
    z0, z1 = max(0, int(zr[0])), min(nz - 1, int(zr[1]))
    if z1 < z0:
        z0, z1 = 0, nz - 1
    fr = Framer(shape, w / h, float(style.get("fov", 30)), bool(style.get("ortho", False)))
    lo, hi = np.zeros(3), np.array([nx - 1, ny - 1, nz - 1], float)
    cb = clip_box(spec, shape)
    if cb:  # sweep only the kept slices and, unless switched off, frame the kept region
        z0, z1 = max(z0, cb[0][2]), min(z1, cb[1][2])
        if z1 < z0:
            z0, z1 = cb[0][2], cb[1][2]
        if spec["clip"].get("fit", True):
            lo, hi = np.array(cb[0], float), np.array(cb[1], float)
    tilt, az, zoom = float(v.get("tilt", 42)), float(v.get("az", 0)), max(0.05, float(v.get("zoom", 1)))

    def zoomed(p, z):
        p = dict(p); p["dist"] /= z; p["scale"] /= z
        return p

    def ov(d=0.0):
        return zoomed(fr.fit(lo, hi, tilt, az + d, 1.07), zoom)

    frames, marks = [], []
    st = dict(k=z0, reveal=False, slice=base, e=0.0, target=None, others="grey", caption="")
    pose = [ov()]
    half = max(1, round(fps * 0.5))
    ramp = {}  # the tomogram fade at the start of the current shot

    def start_fade(shot):
        t = shot_tomogram(shot)
        ramp.clear(); ramp.update(start=len(frames), s0=st["slice"], s1=target_of(shot), n=max(1, round(fps * max(0.0, float(t["fade"])))))

    def emit(n, fn, si, phase, label=None):
        n = max(1, int(n))
        if label:
            marks.append(dict(frame=len(frames), label=label))
        for i in range(n):
            s = dict(st); s["pose"] = pose[0]; s["shot"] = si; s["phase"] = phase
            d = fn(i / (n - 1) if n > 1 else 1.0)
            if ramp and "slice" not in d:
                q = smooth01((len(frames) - ramp["start"] + 1) / ramp["n"])
                d["slice"] = ramp["s1"] if q >= 1 else ramp["s0"] + (ramp["s1"] - ramp["s0"]) * q
            s.update(d); frames.append(s)
        last = frames[-1]
        for key in st:
            st[key] = last[key]
        pose[0] = last["pose"]

    def show_all():
        if not st["reveal"]:
            st.update(reveal=True, k=z0)

    def fly_back(si, to=None):
        p0, e0, s0 = pose[0], st["e"], st["slice"]; s1 = base if to is None else to
        emit(round(fps * 1.3), lambda u: dict(pose=lerp_pose(p0, ov(), smooth01(u)), e=e0 * (1 - smooth01(u)),
                                              slice=s0 + (s1 - s0) * smooth01(u), caption=""), si, "fly")
        st.update(target=None, e=0.0); pose[0] = ov()

    shots = spec.get("shots", [])
    for si, shot in enumerate(shots):
        if not shot.get("on", True):
            continue
        typ = shot.get("type"); n = max(1, round(fps * max(0.1, float(shot.get("seconds", 5)))))
        if (typ == "sweep" and not has_tomo) or (typ == "reveal" and not has_seg):
            continue
        start_fade(shot)
        if typ == "sweep":
            st.update(reveal=False, k=z0)
            emit(half, lambda u: {}, si, "hold", "Slices")
            emit(n, lambda u: dict(k=z0 + int(round(u * (z1 - z0)))), si, "main")
            emit(max(1, half // 2), lambda u: {}, si, "hold")
        elif typ == "reveal":
            st.update(k=z1)
            emit(n, lambda u: dict(k=z1 - int(round(u * (z1 - z0))), reveal=True), si, "main", "Reveal")
            emit(half, lambda u: {}, si, "hold")
        elif typ == "overview":
            show_all()
            if st["e"] > 0:
                fly_back(si, ramp["s1"]); start_fade(dict(shot, tomogram=dict(shot_tomogram(shot), fade=0)))
            m, ang = shot.get("motion", "rock"), float(shot.get("angle", 25))
            emit(n, lambda u: dict(pose=ov(ang * math.sin(2 * math.pi * u) if m == "rock" else 360.0 * u if m == "spin" else 0.0)),
                 si, "main", "Overview")
            pose[0] = ov()
        elif typ == "focus":
            show_all(); tgt = shot.get("target") or {}
            try:
                blo, bhi = ds.target_box(tgt)
            except (KeyError, IndexError, TypeError, ValueError):
                continue
            ft, fz = float(shot.get("tilt", max(tilt, 50))), max(0.05, float(shot.get("zoom", 1)))

            def fp(d=0.0, blo=blo, bhi=bhi, ft=ft, fz=fz):
                return zoomed(fr.fit(blo, bhi, ft, az + d, 1.0), fz)

            p0, p1, e0, old, oldmode, s0 = pose[0], fp(), st["e"], st["target"], st["others"], st["slice"]
            s1 = ramp["s1"]  # the fly-in is this shot's tomogram fade
            mode, cap = shot.get("others", "grey"), shot.get("caption", "")

            def fly(u):
                q = smooth01(u); d = dict(pose=lerp_pose(p0, p1, q), slice=s0 + (s1 - s0) * q)
                if e0 > 0 and old is not None:  # hand over from the previous focus shot
                    if u < 0.5:
                        d.update(e=e0 * (1 - smooth01(2 * u)), target=old, others=oldmode, caption="")
                    else:
                        d.update(e=smooth01(2 * u - 1), target=tgt, others=mode, caption=cap if u > 0.8 else "")
                else:
                    d.update(e=q, target=tgt, others=mode, caption=cap if q > 0.6 else "")
                return d

            emit(round(fps * 1.3), fly, si, "fly", cap or "Focus")
            m, ang = shot.get("motion", "orbit"), float(shot.get("angle", 30))
            emit(n, lambda u: dict(pose=fp(360.0 * u if m == "orbit" else ang * math.sin(2 * math.pi * u) if m == "rock" else 0.0)),
                 si, "main")
            pose[0] = fp()
    if st["e"] > 0 and spec.get("return_to_overview", True):
        ramp.clear(); fly_back(len(shots)); emit(half, lambda u: {}, len(shots), "hold")
    if not frames:
        show_all(); emit(1, lambda u: {}, -1, "main")
    return frames, marks


def state_key(s):
    p = s["pose"]
    return (s["k"], s["reveal"], round(s["slice"], 3), round(s["e"], 3), json.dumps(s["target"], sort_keys=True), s["others"],
            s["caption"], tuple(np.round(p["c"], 2)), round(p["tilt"], 3), round(p["az"] % 360.0, 3), round(p["dist"], 2))
