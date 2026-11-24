"""Close-up motion and animations (3.4, 3.5): wiggle, push-in, highlight and object effects, and the steady framing
that keeps an object the same size while the camera turns around it."""
import math

import numpy as np

from .transitions import smooth01


FX_DEFAULTS = dict(wiggle=dict(amplitude=8.0, frequency=0.5, shape="circle"), push=dict(on=False, start=1.0, end=1.35),
                   highlight=dict(kind="none", pulses=2), effect=dict(kind="none", direction="slices", move_slice=True, sweep="whole",
                                                                       how="bottom", seconds=None))


def fx_settings(part):
    out = {k: dict(v) for k, v in FX_DEFAULTS.items()}
    for k in out:
        if isinstance(part.get(k), dict):
            out[k].update(part[k])
    return out


def steady_framing(part, fr, lo, hi, tilt, az, motion, angle):
    """(distance, scale) that frames the box at every angle a close-up's motion reaches (sampled at 36 angles)."""
    pts = [(tilt, az)]
    if motion == "orbit":
        pts = [(tilt, az + 360.0 * k / 36) for k in range(36)]
    elif motion == "rock":
        pts = [(tilt, az + angle * math.sin(2 * math.pi * k / 36)) for k in range(36)]
    elif motion == "wiggle":
        w = fx_settings(part)["wiggle"]; A = float(w["amplitude"])
        if w.get("smooth"):  # the directions the 3.5 wiggle really reaches
            T = max(0.1, float(part.get("seconds", 5))); base = dict(c=np.zeros(3), tilt=tilt, az=az, dist=1.0, scale=1.0)
            for t in np.linspace(0, T, 90):
                q = turned_pose(fr, base, *wiggle_offsets(w, float(t), T)); pts.append((q["tilt"], q["az"]))
        pts += [(min(89.0, max(0.0, tilt + A * math.sin(2 * math.pi * k / 36))), az + A * math.cos(2 * math.pi * k / 36)) for k in range(36)]
    fits = [fr.fit(lo, hi, t, a, 1.0) for t, a in pts]
    return max(f["dist"] for f in fits), max(f["scale"] for f in fits)


def wiggle_offsets(w, t, T):
    """(sideways, up/down) turn of the viewing direction in degrees at time t of a close-up of T seconds (3.5 wiggle):
    zero speed and position at both ends, a whole number of cycles, shapes circle / eight / drift."""
    A = float(w.get("amplitude", 5.0)); shape = w.get("shape", "circle")
    Te = min(0.75, T / 2)

    def rise(x):  # "smootherstep": speed and acceleration are 0 at both of its ends, so the acceleration never jumps
        x = min(1.0, max(0.0, x))
        return x * x * x * (x * (6 * x - 15) + 10)
    env = rise(t / Te) * rise((T - t) / Te)
    if shape == "drift":  # slow and non-repeating
        x = 0.6 * math.sin(2 * math.pi * 0.13 * t + 1.1) + 0.4 * math.sin(2 * math.pi * 0.21 * t + 2.3)
        y = 0.6 * math.sin(2 * math.pi * 0.34 * t + 0.7)
        return A * env * x, A * env * y
    f = max(1, round(float(w.get("frequency", 0.35)) * T)) / max(1e-6, T)  # whole cycles: it ends where it began
    if shape == "eight":
        return A * env * math.sin(2 * math.pi * f * t), A / 2 * env * math.sin(4 * math.pi * f * t)
    return A * env * math.sin(2 * math.pi * f * t), A * env * (1 - math.cos(2 * math.pi * f * t)) / 2


def turned_pose(fr, base, dx, dy):
    """The pose with its viewing direction turned by dx degrees sideways and dy degrees up/down around the centre,
    keeping the picture upright (no roll). Gives pos and up, so it also works when looking straight down."""
    from .paths import tilt_az
    d0, up0 = fr.axes(base["tilt"], base["az"]); d0, up0 = np.asarray(d0, float), np.asarray(up0, float)
    right = np.cross(up0, d0)
    a, b = math.radians(dx), math.radians(dy)
    d = d0 * math.cos(a) * math.cos(b) + right * math.sin(a) * math.cos(b) + up0 * math.sin(b)
    d /= np.linalg.norm(d)
    up = up0 - d * float(up0 @ d); up /= np.linalg.norm(up)
    t, az = tilt_az(d, fr.u)
    if abs(dx) < 1e-9 and abs(dy) < 1e-9:
        t, az = base["tilt"], base["az"]
    return dict(base, tilt=float(t), az=float(az), wdir=d, pos=np.asarray(base["c"]) + base["dist"] * d, up=up)


def closeup_animation(part, ds, fr, fp, n, fps, k0=0, nz=1):
    """Frame function u -> state changes for a close-up's main phase with wiggle, push-in, highlight or an object effect;
    None when the close-up has none of them (it is then drawn exactly as before)."""
    S = fx_settings(part); m = part.get("motion", "orbit"); ang = float(part.get("angle", 30))
    wig, push, hl, eff = S["wiggle"], S["push"], S["highlight"], S["effect"]
    if m != "wiggle" and not push.get("on") and hl["kind"] == "none" and eff["kind"] == "none":
        return None
    from .storyboard import focus_box
    T = max(1e-6, (n - 1) / fps); tgt = part.get("target") or {}
    base = fp(); obj_lo, obj_hi = focus_box(part, ds, pad=False)
    lo, hi = np.asarray(obj_lo, float) - 0.5, np.asarray(obj_hi, float) + 0.5
    centre = (lo + hi) / 2; radius = float(np.linalg.norm(hi - lo)) / 2
    axis = ds.main_axis(tgt) if eff["kind"] == "section" and eff.get("direction") == "axis" else None

    def fn(u):
        t = u * T; d = {}; fx = {}
        if m == "wiggle" and wig.get("smooth"):  # 3.5: calm start and end, whole cycles, no roll
            dx, dy = wiggle_offsets(wig, t, T)
            p = turned_pose(fr, base, dx, dy)
            fx["wiggle"] = [round(dx, 4), round(dy, 4)]
        elif m == "wiggle":
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
        if p.get("wdir") is not None:  # a turned (wiggle) pose: the camera position follows the distance
            p = dict(p, pos=np.asarray(p["c"]) + p["dist"] * p["wdir"])
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
