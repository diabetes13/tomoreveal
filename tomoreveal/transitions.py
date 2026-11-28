"""Easing and transitions between movie parts (3.5): how the camera moves between two poses, how gently a
transition leaves one part and arrives at the next, and the settings of the transition into a part."""


def lerp_pose(a, b, u, d_az=None, fov=None):
    """Pose between a and b; the azimuth takes the shorter way round unless d_az (the turn from a to b) is given.
    A field of view of its own (3.5: per point of a camera path) changes gradually too; fov is the movie's, for the
    pose without one. The result has its own field of view only when a or b has one."""
    d = ((b["az"] - a["az"] + 180.0) % 360.0) - 180.0 if d_az is None else float(d_az)
    out = dict(c=a["c"] + (b["c"] - a["c"]) * u, tilt=a["tilt"] + (b["tilt"] - a["tilt"]) * u, az=a["az"] + d * u,
               dist=a["dist"] * (b["dist"] / a["dist"]) ** u, scale=a["scale"] * (b["scale"] / a["scale"]) ** u)
    fa, fb = a.get("fov"), b.get("fov")
    if fa is not None or fb is not None:
        fa = float(fa if fa is not None else (fov if fov is not None else fb))
        fb = float(fb if fb is not None else (fov if fov is not None else fa))
        out["fov"] = fa + (fb - fa) * u
    return out


def camera_axes(fr, p):
    """The camera's orientation as a rotation matrix (columns: right, up, back = centre -> camera), roll included."""
    import numpy as np
    if p.get("pos") is not None:
        back = np.asarray(p["pos"], float) - np.asarray(p["c"], float); up = np.asarray(p["up"], float)
    else:
        back, up = (np.asarray(v, float) for v in fr.axes(p["tilt"], p["az"]))
    back = back / (np.linalg.norm(back) or 1.0); up = up - back * float(up @ back); up = up / (np.linalg.norm(up) or 1.0)
    return np.column_stack([np.cross(up, back), up, back])


def lerp_camera(fr, a, b, u, d_az=None, a_end=None, b_start=None):
    """Camera between a and b, a fraction u of the way. Where no pose has a camera position of its own, exactly
    lerp_pose. Otherwise (a spin around X or Y, a camera path, the wiggle) the orientation turns the shorter way from
    a_end (where the part we leave ends) to b_start (where the next one starts), with the motion of both parts since
    (a after a_end, b after b_start) added; the camera is at the blended distance from the blended focus. Blending tilt
    and azimuth instead flips such cameras (spins can turn half a circle during an overlap)."""
    a_end = a if a_end is None else a_end; b_start = b if b_start is None else b_start
    out = lerp_pose(a, b, u, d_az, fov=fr.fov)
    if all(p.get("pos") is None for p in (a, b, a_end, b_start)):
        return out
    from scipy.spatial.transform import Rotation, Slerp
    from .paths import tilt_az
    R0, R1 = camera_axes(fr, a_end), camera_axes(fr, b_start)
    turn = Slerp([0.0, 1.0], Rotation.from_matrix([R0, R1]))([min(1.0, max(0.0, float(u)))]).as_matrix()[0]
    R = (camera_axes(fr, b) @ R1.T) @ (camera_axes(fr, a) @ R0.T) @ turn
    t, az = tilt_az(R[:, 2], fr.u)
    out.update(tilt=float(t), az=float(az), pos=out["c"] + out["dist"] * R[:, 2], up=R[:, 1])
    return out


def smooth01(u):
    u = min(1.0, max(0.0, u))
    return u * u * (3 - 2 * u)


# ---- easing (3.5): how gently a transition leaves the previous part and arrives at the next ----
EASES = ("sudden", "gentle", "very_gentle")
_EASE_START = {"sudden": (0.0, 0.0), "gentle": (0.42, 0.0), "very_gentle": (0.65, 0.0)}
_EASE_END = {"sudden": (1.0, 1.0), "gentle": (0.58, 1.0), "very_gentle": (0.35, 1.0)}


def ease_curve(u, leave="gentle", arrive="gentle"):
    """Progress 0..1 at time u 0..1: a cubic Bezier as in CSS, with its own strength at each end. Gentle / gentle is
    exactly smooth01 (the easing of every earlier version); sudden / sudden is a constant speed."""
    u = min(1.0, max(0.0, float(u)))
    leave = leave if leave in _EASE_START else "gentle"; arrive = arrive if arrive in _EASE_END else "gentle"
    if leave == "gentle" and arrive == "gentle":
        return smooth01(u)
    (x1, y1), (x2, y2) = _EASE_START[leave], _EASE_END[arrive]
    if (x1, y1, x2, y2) == (0.0, 0.0, 1.0, 1.0):
        return u

    def bez(t, a, b):
        return 3 * (1 - t) ** 2 * t * a + 3 * (1 - t) * t * t * b + t ** 3
    lo, hi = 0.0, 1.0
    for _ in range(40):
        mid = (lo + hi) / 2
        if bez(mid, x1, x2) < u:
            lo = mid
        else:
            hi = mid
    return bez((lo + hi) / 2, y1, y2)


TRANSITION_KINDS = ("flight", "cut", "dissolve", "black", "white")
TRANSITION_DEFAULTS = dict(kind="flight", ease_out_prev="gentle", ease_in_next="gentle", overlap_prev=0.0, overlap_next=0.0, path="straight")


def transition_of(part):
    """Settings of the transition into a part (stored in the part as "transition_in"; its length stays in
    "transition", None = automatic). Missing settings mean the behaviour of every earlier version."""
    t = dict(TRANSITION_DEFAULTS); own = part.get("transition_in") if isinstance(part, dict) else None
    if isinstance(own, dict):
        t.update({k: v for k, v in own.items() if k in TRANSITION_DEFAULTS})
    t["overlap_prev"] = max(0.0, float(t["overlap_prev"] or 0)); t["overlap_next"] = max(0.0, float(t["overlap_next"] or 0))
    if t["kind"] not in TRANSITION_KINDS:
        t["kind"] = "flight"
    return t


def with_end_ease(fn, part):
    """A part's own motion slowed down at its end ("Slow down at the end", 3.5); unchanged for parts without it."""
    how = (part or {}).get("ease_end", "sudden")
    if how not in ("gentle", "very_gentle"):
        return fn
    return lambda u, fn=fn, how=how: fn(ease_curve(u, "sudden", how))
