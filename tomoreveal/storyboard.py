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


def default_spec(ds):
    return dict(
        classes={str(k): dict(name=c["name"], color=c["color"], show=not c["background"]) for k, c in ds.classes.items()},
        view=dict(tilt=42, az=0, zoom=1.0),
        style=dict(bg="#000000", legend=True, slice_label=True, title="", contrast=[1, 99], invert=False, ortho=False, fov=30),
        shots=[dict(type="sweep", on=True, seconds=7), dict(type="reveal", on=True, seconds=7),
               dict(type="overview", on=True, motion="rock", seconds=6, angle=25)],
        return_to_overview=True, z_range=None, clip=None,
        output=dict(size=[1920, 1080], fps=30, crf=20, aa=True))


def focus_shot(target, caption, **kw):
    s = dict(type="focus", on=True, target=target, caption=caption, others="grey", motion="orbit", seconds=5,
             zoom=1.0, slice="keep", angle=30)
    s.update(kw)
    return s


def build_timeline(spec, ds):
    """Storyboard -> list of per-frame states, plus (frame, label) marks for the scrubber."""
    out = spec.get("output", {}); fps = int(out.get("fps", 30)); w, h = out.get("size", [1920, 1080])
    style, v = spec.get("style", {}), spec.get("view", {})
    nz, ny, nx = ds.tomo.shape
    zr = spec.get("z_range") or [0, nz - 1]
    z0, z1 = max(0, int(zr[0])), min(nz - 1, int(zr[1]))
    if z1 < z0:
        z0, z1 = 0, nz - 1
    fr = Framer(ds.tomo.shape, w / h, float(style.get("fov", 30)), bool(style.get("ortho", False)))
    lo, hi = np.zeros(3), np.array([nx - 1, ny - 1, nz - 1], float)
    cb = clip_box(spec, ds.tomo.shape)
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
    st = dict(k=z0, reveal=False, slice=1.0, e=0.0, target=None, others="grey", caption="")
    pose = [ov()]
    half = max(1, round(fps * 0.5))

    def emit(n, fn, si, phase, label=None):
        n = max(1, int(n))
        if label:
            marks.append(dict(frame=len(frames), label=label))
        for i in range(n):
            s = dict(st); s["pose"] = pose[0]; s["shot"] = si; s["phase"] = phase
            s.update(fn(i / (n - 1) if n > 1 else 1.0)); frames.append(s)
        last = frames[-1]
        for key in st:
            st[key] = last[key]
        pose[0] = last["pose"]

    def show_all():
        if not st["reveal"]:
            st.update(reveal=True, k=z0)

    def fly_back(si):
        p0, e0, s0 = pose[0], st["e"], st["slice"]
        emit(round(fps * 1.3), lambda u: dict(pose=lerp_pose(p0, ov(), smooth01(u)), e=e0 * (1 - smooth01(u)),
                                              slice=s0 + (1 - s0) * smooth01(u), caption=""), si, "fly")
        st.update(target=None, e=0.0); pose[0] = ov()

    shots = spec.get("shots", [])
    for si, shot in enumerate(shots):
        if not shot.get("on", True):
            continue
        typ = shot.get("type"); n = max(1, round(fps * max(0.1, float(shot.get("seconds", 5)))))
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
                fly_back(si)
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
            s1 = 0.0 if shot.get("slice") == "hide" else 1.0
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
        fly_back(len(shots)); emit(half, lambda u: {}, len(shots), "hold")
    if not frames:
        show_all(); emit(1, lambda u: {}, -1, "main")
    return frames, marks


def state_key(s):
    p = s["pose"]
    return (s["k"], s["reveal"], round(s["slice"], 3), round(s["e"], 3), json.dumps(s["target"], sort_keys=True), s["others"],
            s["caption"], tuple(np.round(p["c"], 2)), round(p["tilt"], 3), round(p["az"] % 360.0, 3), round(p["dist"], 2))
