"""Camera paths: the centre line of a structure (to glide along a membrane), and smooth curves through points.

A centre line is found by thinning the object to a skeleton (scikit-image) and taking the longest path through it.
Sheet-like objects (membranes) thin to a surface, not a line: then the line follows the object's longest direction,
through the middle of the voxels at each position along it. Lines are smoothed and resampled evenly.
"""
import json
import math
from collections import deque

import numpy as np

from .common import UserError
from .transitions import ease_curve, smooth01

TOO_SMALL = "This object is too small or too compact to follow. Use points instead."


def _items_mask(ds, items, clip=None, max_voxels=4_000_000):
    """Boolean mask of the items (z, y, x) in their joint bounding box, binned so it has at most max_voxels voxels.
    Returns (mask, offset (x, y, z) of the box, bin factor)."""
    boxes = []
    for key, comp in items:
        k = ds.id_of(key)
        if k is None or ds.bbox.get(k) is None:
            continue
        sl = ds._comp_slices(k, int(comp)) if comp is not None else ds.bbox[k]
        boxes.append((k, comp, sl))
    if not boxes:
        raise UserError("The structure to follow is not loaded.")
    lo = [min(b[2][i].start for b in boxes) for i in range(3)]; hi = [max(b[2][i].stop for b in boxes) for i in range(3)]
    if clip is not None:  # only what is inside the clip box
        (x0, y0, z0), (x1, y1, z1) = clip
        lo = [max(lo[0], z0), max(lo[1], y0), max(lo[2], x0)]; hi = [min(hi[0], z1 + 1), min(hi[1], y1 + 1), min(hi[2], x1 + 1)]
        if any(h <= l for l, h in zip(lo, hi)):
            raise UserError("The structure to follow is outside the clip box.")
    sl = tuple(slice(a, b) for a, b in zip(lo, hi))
    m = np.zeros([b - a for a, b in zip(lo, hi)], bool)
    for k, comp, _ in boxes:
        if comp is None:
            m |= ds.kept_mask(k, sl)
        else:
            lbl, _, _ = ds.components(k); bb = ds.bbox[k]
            sub = tuple(slice(max(s.start, b.start) - b.start, min(s.stop, b.stop) - b.start) for s, b in zip(sl, bb))
            tgt = tuple(slice(max(s.start, b.start) - s.start, min(s.stop, b.stop) - s.start) for s, b in zip(sl, bb))
            m[tgt] |= lbl[sub] == int(comp)
    f = 1
    while m.size / f ** 3 > max_voxels:
        f += 1
    if f > 1:
        nz, ny, nx = (np.array(m.shape) // f) * f
        m = m[:nz, :ny, :nx].reshape(nz // f, f, ny // f, f, nx // f, f).any(axis=(1, 3, 5))
    return m, np.array([lo[2], lo[1], lo[0]], float), f


def _longest_path(skel):
    """Voxels (z, y, x) of the longest shortest path in a skeleton (26-neighbourhood graph, two breadth-first searches)."""
    pts = np.argwhere(skel)
    if len(pts) < 2:
        return pts
    index = {tuple(p): i for i, p in enumerate(pts)}
    offs = [(a, b, c) for a in (-1, 0, 1) for b in (-1, 0, 1) for c in (-1, 0, 1) if (a, b, c) != (0, 0, 0)]
    nb = [[index[q] for q in ((p[0] + a, p[1] + b, p[2] + c) for a, b, c in offs) if q in index] for p in pts]

    def bfs(start):
        dist = np.full(len(pts), -1); prev = np.full(len(pts), -1); dist[start] = 0; dq = deque([start])
        while dq:
            i = dq.popleft()
            for j in nb[i]:
                if dist[j] < 0:
                    dist[j] = dist[i] + 1; prev[j] = i; dq.append(j)
        return dist, prev
    biggest = max(range(len(pts)), key=lambda i: 0)  # any start; the first BFS finds one end
    d, _ = bfs(biggest); a = int(np.argmax(d))
    d, prev = bfs(a); b = int(np.argmax(d))
    path = [b]
    while path[-1] != a and prev[path[-1]] >= 0:
        path.append(int(prev[path[-1]]))
    return pts[path[::-1]]


def _spine(points_xyz, bins=60):
    """For sheets: along the longest direction, the mean of the voxels at each position."""
    c = points_xyz.mean(0); p = points_xyz - c
    w, v = np.linalg.eigh(p.T @ p / len(p)); a = v[:, -1]
    t = p @ a; edges = np.linspace(t.min(), t.max(), bins + 1); out = []
    for i in range(bins):
        sel = (t >= edges[i]) & (t <= edges[i + 1])
        if sel.sum() >= 3:
            out.append(points_xyz[sel].mean(0))
    return np.array(out)


def smooth_resample(line, n=None, smooth=None):
    """A smooth line through points (x, y, z), resampled at even spacing (about one point per voxel, at least 20)."""
    from scipy.interpolate import splev, splprep
    line = np.asarray(line, float)
    keep = np.r_[True, np.linalg.norm(np.diff(line, axis=0), axis=1) > 1e-6]; line = line[keep]
    if len(line) < 4:
        raise UserError(TOO_SMALL)
    L = float(np.linalg.norm(np.diff(line, axis=0), axis=1).sum())
    s = len(line) * 2.0 if smooth is None else smooth
    tck, u = splprep(line.T, s=s, k=3)
    fine = np.array(splev(np.linspace(0, 1, max(200, int(L * 4))), tck)).T
    seg = np.linalg.norm(np.diff(fine, axis=0), axis=1); cum = np.r_[0, np.cumsum(seg)]
    n = n or max(20, int(cum[-1]))
    t = np.linspace(0, cum[-1], n)
    return np.array([np.interp(t, cum, fine[:, i]) for i in range(3)]).T


def centre_line(ds, items, clip=None):
    """Evenly spaced points (x, y, z voxels) along the middle of the items, from one end to the other."""
    m, off, f = _items_mask(ds, items, clip)
    if m.sum() < 20:
        raise UserError(TOO_SMALL)
    from scipy import ndimage
    lbl, n = ndimage.label(m, structure=np.ones((3, 3, 3), bool))
    if n > 1:  # several pieces: the largest
        m = lbl == (np.argmax(np.bincount(lbl.ravel())[1:]) + 1)
    pts = np.argwhere(m)[:, ::-1].astype(float) * f + off + (f - 1) / 2  # x, y, z
    ext = np.ptp(pts, axis=0); longest = float(ext.max())
    if longest < 12 or longest < 1.3 * float(np.sort(ext)[-2]):  # round or tiny: nothing to glide along
        raise UserError(TOO_SMALL)
    from skimage.morphology import skeletonize
    skel = skeletonize(m)
    path = _longest_path(skel)
    line = None
    if len(path) >= 4:
        line = path[:, ::-1].astype(float) * f + off + (f - 1) / 2
        span = float(np.ptp((line - pts.mean(0)) @ np.linalg.eigh(np.cov((pts - pts.mean(0)).T))[1][:, -1]))
        # a membrane thins to a surface: its longest skeleton path wanders and misses much of the length
        if span < 0.7 * longest or skel.sum() > 3 * len(path):
            line = None
    if line is None:
        line = _spine(pts)
    return smooth_resample(line)


def catmull_rom(points, samples_per_segment=40):
    """Centripetal Catmull-Rom curve through points (n, 3); returns (curve, index of the sample at each point)."""
    P = np.asarray(points, float)
    if len(P) == 1:
        return P.copy(), [0]
    ext = np.vstack([2 * P[0] - P[1], P, 2 * P[-1] - P[-2]])
    out, at = [], [0]
    for i in range(1, len(ext) - 2):
        p0, p1, p2, p3 = ext[i - 1], ext[i], ext[i + 1], ext[i + 2]
        t0 = 0.0; t1 = t0 + max(1e-6, np.linalg.norm(p1 - p0)) ** 0.5
        t2 = t1 + max(1e-6, np.linalg.norm(p2 - p1)) ** 0.5; t3 = t2 + max(1e-6, np.linalg.norm(p3 - p2)) ** 0.5
        for t in np.linspace(t1, t2, samples_per_segment, endpoint=False):
            a1 = (t1 - t) / (t1 - t0) * p0 + (t - t0) / (t1 - t0) * p1
            a2 = (t2 - t) / (t2 - t1) * p1 + (t - t1) / (t2 - t1) * p2
            a3 = (t3 - t) / (t3 - t2) * p2 + (t - t2) / (t3 - t2) * p3
            b1 = (t2 - t) / (t2 - t0) * a1 + (t - t0) / (t2 - t0) * a2
            b2 = (t3 - t) / (t3 - t1) * a2 + (t - t1) / (t3 - t1) * a3
            out.append((t2 - t) / (t2 - t1) * b1 + (t - t1) / (t2 - t1) * b2)
        at.append(len(out))
    out.append(P[-1])
    at[-1] = len(out) - 1
    return np.array(out), at


def arclength(curve):
    return np.r_[0, np.cumsum(np.linalg.norm(np.diff(curve, axis=0), axis=1))]


def sample(curve, cum, d):
    """Point on a curve at arc length d."""
    d = min(max(0.0, d), cum[-1])
    return np.array([np.interp(d, cum, curve[:, i]) for i in range(3)])


NEAR_TOP = 0.02  # sin(tilt) below this (about 1°): looking straight down, the direction alone gives no azimuth


def tilt_az(direction, u_axis, up=None):
    """(tilt, az) of the storyboard's Framer for a view direction (centre -> camera), so transitions can blend.
    Looking (almost) straight down, the direction says nothing about how the picture is turned: then the azimuth comes
    from the up vector (3.6; before, a top view turned 90° gave az 0 and transitions swung the picture round)."""
    p = np.asarray(direction, float); p = p / (np.linalg.norm(p) or 1.0)
    t = math.degrees(math.acos(max(-1.0, min(1.0, p[2]))))
    st = math.sin(math.radians(t))
    if up is not None and st < NEAR_TOP:
        # Framer.axes: p = R(cos t z - sin t u), up = R(cos t u + sin t z), so R u = cos t up - sin t p
        w = math.cos(math.radians(t)) * np.asarray(up, float) - st * p
        if math.hypot(w[0], w[1]) > 1e-6:
            a = math.atan2(-w[1], -w[0]) if u_axis[0] != 0 else math.atan2(-w[0], w[1])
            return t, math.degrees(a)
    if u_axis[0] != 0:  # u = -x: p = (sin t cos a, sin t sin a, cos t)
        a = math.degrees(math.atan2(p[1], p[0]))
    else:  # u = +y: p = (sin t sin a, -sin t cos a, cos t)
        a = math.degrees(math.atan2(p[0], -p[1]))
    return t, a


def stable_up(dirs, up0):
    """Up vectors for a sequence of view directions (camera -> focal point): the main up vector projected onto the plane
    across each direction, smoothed, so the picture does not spin."""
    ups = []; prev = None
    for d in dirs:
        d = d / (np.linalg.norm(d) or 1.0)
        u = np.asarray(up0, float) - d * float(np.dot(up0, d))
        if np.linalg.norm(u) < 1e-3:
            u = prev if prev is not None else np.array([0.0, 1.0, 0.0])
        u = u / np.linalg.norm(u)
        if prev is not None:
            u = 0.7 * u + 0.3 * prev; u = u - d * float(np.dot(u, d)); u /= np.linalg.norm(u) or 1.0
        ups.append(u); prev = u
    return ups


# ---------------------------------------------------------------------------------------------------------------
# 3.5: a steady rail for the "follow" camera (plan 3.5 phase 4)
# ---------------------------------------------------------------------------------------------------------------
STEADINESS = (("close (follows bends)", 0), ("steady", 50), ("very steady", 100))
RAIL_POINTS = 400


def resample(line, n=RAIL_POINTS):
    """n points evenly spaced along a polyline."""
    line = np.asarray(line, float); cum = arclength(line)
    if cum[-1] < 1e-9:
        return np.repeat(line[:1], n, axis=0)
    t = np.linspace(0, cum[-1], n)
    return np.array([np.interp(t, cum, line[:, i]) for i in range(3)]).T


def _spline_rail(line, factor):
    """Smoothing spline with s = factor * (number of points), resampled evenly."""
    from scipy.interpolate import splev, splprep
    line = np.asarray(line, float)
    keep = np.r_[True, np.linalg.norm(np.diff(line, axis=0), axis=1) > 1e-6]; line = line[keep]
    if len(line) < 4:
        return resample(line)
    tck, _ = splprep(line.T, s=factor * len(line), k=3)
    return resample(np.array(splev(np.linspace(0, 1, RAIL_POINTS * 3), tck)).T)


def _poly_rail(line, degree=3):
    """A polynomial of the position along the line's main direction: very steady, follows only the overall bend."""
    line = np.asarray(line, float); c = line.mean(0)
    _, v = np.linalg.eigh(np.cov((line - c).T)); a = v[:, -1]
    t = (line - c) @ a
    if t[-1] < t[0]:
        a, t = -a, -t
    fit = [np.polyfit(t, line[:, i], degree) for i in range(3)]
    tt = np.linspace(t.min(), t.max(), RAIL_POINTS * 3)
    return resample(np.array([np.polyval(f, tt) for f in fit]).T)


def _smooth_along(rail, sigma_frac):
    """The rail smoothed by a Gaussian along its length (sigma = sigma_frac of its points); the ends are mirrored
    through the end points, so the rail keeps its ends and its direction there."""
    from scipy.ndimage import gaussian_filter1d
    rail = resample(rail); n = len(rail); sig = max(0.5, sigma_frac * n); pad = min(n - 1, int(3 * sig))
    ext = np.vstack([2 * rail[0] - rail[pad:0:-1], rail, 2 * rail[-1] - rail[-2:-pad - 2:-1]])
    out = gaussian_filter1d(ext, sig, axis=0, mode="nearest")[pad:pad + n]
    return resample(out)


def steady_rail(line, steadiness):
    """The look-at rail for a steadiness 0 (close: smoothed over 1.5 % of its length) .. 50 (steady: 6 %) .. 100
    (very steady: a degree-3 polynomial along the main direction); values in between blend continuously. (Measured in
    plan 3.5 on the raw skeleton, a smoothing spline did the same; the centre line here is already smoothed, so the
    smoothing is set by length instead.)"""
    v = float(min(100.0, max(0.0, steadiness)))
    if v <= 50:
        return _smooth_along(line, 0.015 * (0.06 / 0.015) ** (v / 50.0))
    w = (v - 50.0) / 50.0
    a, b = _smooth_along(line, 0.06), _poly_rail(line)
    return resample(a * (1 - w) + b * w)


def moving_average(rail, frac=0.15):
    """The rail averaged over a window of frac of its length (ends reflected), resampled evenly: a calmer copy."""
    rail = np.asarray(rail, float); n = len(rail); k = max(1, int(round(frac * n)) | 1)
    if k < 3:
        return rail.copy()
    pad = k // 2
    ext = np.vstack([2 * rail[0] - rail[pad:0:-1], rail, 2 * rail[-1] - rail[-2:-pad - 2:-1]])
    ker = np.ones(k) / k
    out = np.array([np.convolve(ext[:, i], ker, mode="valid") for i in range(3)]).T
    return resample(out, n)


def gaussian_in_time(points, sigma_frames):
    """Points filtered over time with a Gaussian (ends reflected): a last guard against jitter."""
    from scipy.ndimage import gaussian_filter1d
    pts = np.asarray(points, float)
    if sigma_frames <= 0.3 or len(pts) < 3:
        return pts
    return gaussian_filter1d(pts, sigma_frames, axis=0, mode="reflect")


def upright_ups(dirs, up0, z=(0.0, 0.0, 1.0), limit_deg=20.0):
    """Up vectors: the tomogram's z axis whenever the view is more than limit_deg away from it, otherwise carried along
    from the previous frame with the least turn (parallel transport), so the picture never rolls with the membrane."""
    z = np.asarray(z, float); ups = []; prev = None; cosl = math.cos(math.radians(limit_deg))
    for d in dirs:
        d = np.asarray(d, float); d = d / (np.linalg.norm(d) or 1.0)
        if abs(float(d @ z)) < cosl:
            u = z - d * float(z @ d)
        else:
            base = prev if prev is not None else np.asarray(up0, float)
            u = base - d * float(base @ d)
            if np.linalg.norm(u) < 1e-6:
                u = np.cross(d, [1.0, 0.0, 0.0])
        u = u / (np.linalg.norm(u) or 1.0); ups.append(u); prev = u
    return ups


def turn_limited_times(cam, look, total, max_deg_per_s):
    """Cumulative time 0..total at each rail point: even speed, but slower where the view direction would otherwise
    turn faster than max_deg_per_s."""
    cam, look = np.asarray(cam, float), np.asarray(look, float)
    ds = np.linalg.norm(np.diff(cam, axis=0), axis=1)
    d = look - cam; d /= np.linalg.norm(d, axis=1, keepdims=True) + 1e-12
    dth = np.degrees(np.arccos(np.clip(np.sum(d[1:] * d[:-1], axis=1), -1, 1)))
    if total <= 0 or ds.sum() < 1e-9:
        return np.linspace(0, total, len(cam))
    need = dth.sum() / max_deg_per_s
    if need >= total:  # cannot keep to the limit in this time: turn as evenly as possible
        c = np.maximum(ds / ds.sum(), dth / max(dth.sum(), 1e-9))
        return np.r_[0, np.cumsum(c)] / c.sum() * total
    lo, hi = 1e-9, 1e9  # speed (voxels per second) so the total time fits
    for _ in range(80):
        v = math.sqrt(lo * hi)
        if np.maximum(ds / v, dth / max_deg_per_s).sum() > total:
            lo = v
        else:
            hi = v
    c = np.maximum(ds / hi, dth / max_deg_per_s)
    return np.r_[0, np.cumsum(c)] / c.sum() * total


# ---- camera poses of a path part (moved from storyboard.py in 3.5.0) ----
PATH_CAMERA = dict(distance=1.0, side="above", height=30.0, look="at_path", lead=0.05)


class PathPending(Exception):
    """The window is still working out a path (in a worker thread)."""


def path_key(items, cb):
    return json.dumps([list(i) for i in items]) + json.dumps(cb, default=list)


def path_line(ds, items, cb):
    """The centre line of the items (cached on the dataset). The window computes it in a worker (PathPending)."""
    key = path_key(items, cb)
    if key not in ds.path_cache:
        if ds.path_async:
            raise PathPending(key)
        try:
            ds.path_cache[key] = centre_line(ds, items, cb)
        except UserError as exc:
            ds.path_cache[key] = exc
    r = ds.path_cache[key]
    if isinstance(r, Exception):
        raise r
    return r


def _ease_or_even(part, u):
    return smooth01(u) if part.get("speed") == "ease" else u


def _pose(fr, pos, focal, up, az_from_up=False):
    pos, focal = np.asarray(pos, float), np.asarray(focal, float)
    d = pos - focal; dist = float(np.linalg.norm(d)) or 1.0
    t, a = tilt_az(d, fr.u, up if az_from_up else None)  # camera paths keep their 3.5 azimuth (golden motion)
    return dict(c=focal, tilt=float(t), az=float(a), dist=dist, scale=dist * math.tan(math.radians(fr.fov) / 2),
                pos=pos, up=np.asarray(up, float))


TURN_LIMIT = 20.0      # degrees per second the follow camera's view may turn at most
TIME_FILTER = 0.4      # seconds: the last smoothing of the follow camera over time


def follow_rails(part, line, cam):
    """(camera rail, look-at rail) of a steady follow path (3.5): the look-at rail is the centre line smoothed by the
    part's steadiness; the camera rail is a calmer copy (averaged over 15 % of the length), offset by side and height."""
    look = steady_rail(line, float(part.get("steadiness", 50)))
    base = moving_average(look, 0.15)
    L = float(np.linalg.norm(np.diff(look, axis=0), axis=1).sum())
    D = float(cam.get("distance", 1.0)) * max(30.0, 0.3 * L); h = math.radians(float(cam.get("height", 30)))
    z = np.array([0.0, 0.0, 1.0]); side = cam.get("side", "above")
    T = np.gradient(base, axis=0); T /= np.linalg.norm(T, axis=1, keepdims=True) + 1e-12
    zmean = float(base[:, 2].mean())
    out = []
    for i, P in enumerate(base):
        t = T[i]; horiz = t - z * float(t @ z); horiz /= np.linalg.norm(horiz) or 1.0
        if side == "above":
            C = np.array([P[0], P[1], zmean + D])
        elif side == "behind":
            C = P - horiz * D * math.cos(h); C[2] = zmean + D * math.sin(h)
        elif side == "free":
            C = P + D * np.asarray(cam.get("_main_dir"), float)
        else:
            sv = np.cross(horiz, z); sv *= (1 if side == "left" else -1)
            C = P + sv * D * math.cos(h); C[2] = zmean + D * math.sin(h)  # constant height, not up and down with the membrane
        out.append(C)
    return resample(np.array(out), len(look)), look


def steady_follow_poses(part, fr, line, cam, n, fps, main_dir, main_up):
    cam = dict(cam, _main_dir=list(main_dir))
    crail, look = follow_rails(part, line, cam)
    T = max(1e-6, (n - 1) / fps)
    if cam.get("look") == "ahead":  # look a little ahead along the path
        k = max(1, int(round(float(cam.get("lead", 0.05)) * len(look))))
        look = np.vstack([look[k:], np.repeat(look[-1:], k, axis=0)])
    end = "very_gentle" if part.get("speed") == "ease" else "gentle"  # even speed, with gentle (or very gentle) ends
    peak = max((ease_curve((k + 1) / 200, end, end) - ease_curve(k / 200, end, end)) * 200 for k in range(200))
    times = turn_limited_times(crail, look, T, TURN_LIMIT / peak)  # the eased middle runs up to `peak` times faster
    pos, foc = [], []
    for j in range(n):
        u = ease_curve(j / max(1, n - 1), end, end)
        t = u * times[-1]
        x = float(np.interp(t, times, np.arange(len(times))))
        i0 = int(min(len(times) - 2, max(0, math.floor(x)))); f = x - i0
        pos.append(crail[i0] * (1 - f) + crail[i0 + 1] * f); foc.append(look[i0] * (1 - f) + look[i0 + 1] * f)
    sig = TIME_FILTER * fps
    pos, foc = gaussian_in_time(pos, sig), gaussian_in_time(foc, sig)
    ups = upright_ups([f - p for p, f in zip(pos, foc)], np.asarray(main_up, float))
    return [_pose(fr, p, f, u) for p, f, u in zip(pos, foc, ups)]


SEGMENT_SPEEDS = {"slow": 0.5, "normal": 1.0, "fast": 2.0}


def point_values(part, key, default):
    """Zoom ("zoom") or field of view ("fov") at each point of a path: a number, or "keep" (also when missing): the
    value of the last point before it that has its own, unless that point said "only here" (3.5)."""
    wps = part.get("waypoints") or []
    if part.get("same_zoom"):  # one value for the whole path
        v = float(part.get(f"path_{key}", default) or default)
        return [v] * len(wps), [False] * len(wps)
    kept, out, own = float(default), [], []
    for w in wps:
        v = w.get(key)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out.append(float(v)); own.append(True)
            if not w.get(f"{key}_only_here"):
                kept = float(v)
        else:
            out.append(kept); own.append(False)
    return out, own


def _segment_curve(P, i, u):
    """Point at fraction u of segment i of the centripetal Catmull-Rom curve through P (fine samples, by arc length)."""
    a, b = max(0, i - 1), min(len(P), i + 3)
    sub = P[a:b]; curve, at = catmull_rom(sub, 60)
    j = i - a; seg = curve[at[j]:at[j + 1] + 1]
    if len(seg) < 2:
        return np.asarray(P[i], float)
    cum = np.r_[0, np.cumsum(np.linalg.norm(np.diff(seg, axis=0), axis=1))]
    if cum[-1] < 1e-9:
        return seg[0] + (seg[-1] - seg[0]) * u
    d = u * cum[-1]
    return np.array([np.interp(d, cum, seg[:, k]) for k in range(3)])


def _hermite(vals, i, u):
    """Catmull-Rom (uniform) through 1D values: smooth, and constant where neighbouring values are equal."""
    v = list(vals); p0 = v[max(0, i - 1)]; p1 = v[i]; p2 = v[min(len(v) - 1, i + 1)]; p3 = v[min(len(v) - 1, i + 2)]
    m1 = (p2 - p0) / 2 if 0 < i else (p2 - p1); m2 = (p3 - p1) / 2 if i + 2 < len(v) else (p2 - p1)
    if abs(p2 - p1) < 1e-12:
        return p1
    h00, h10, h01, h11 = 2 * u ** 3 - 3 * u * u + 1, u ** 3 - 2 * u * u + u, -2 * u ** 3 + 3 * u * u, u ** 3 - u * u
    out = h00 * p1 + h10 * m1 + h01 * p2 + h11 * m2
    return min(max(out, min(p1, p2)), max(p1, p2))  # never beyond the two values (no overshoot)


def points_poses(part, fr, cam, n, fps, main_dir, main_up):
    """Poses of a path through points (3.5): position and look-at on their own centripetal Catmull-Rom curves with the
    same clock per segment (so the view never flips), even speed inside a segment, a speed or a time per segment,
    gentle stops where the camera pauses or is asked to stop, zoom and field of view per point."""
    wps = part.get("waypoints") or []; D = 60.0 * float(cam["distance"])
    P, F = [], []
    for w in wps:
        if w.get("pos") is not None:
            P.append(np.asarray(w["pos"], float)); F.append(np.asarray(w.get("focal", w["pos"]), float))
        else:
            q = np.asarray(w["point"], float); F.append(q); P.append(q + D * np.asarray(main_dir, float))
    K = len(wps)
    zooms, _ = point_values(part, "zoom", 1.0); fovs, _ = point_values(part, "fov", fr.fov)
    zall = float(part.get("zoom_all", 1.0) or 1.0)
    # time of each segment: its length over its speed, or the seconds asked for
    def seg_len(Q, i):  # length of segment i of the curve through Q
        c = np.array([_segment_curve(Q, i, u) for u in np.linspace(0, 1, 24)])
        return float(np.linalg.norm(np.diff(c, axis=0), axis=1).sum())
    lens = [seg_len(P, i) + 0.25 * seg_len(F, i) + 1e-6 for i in range(K - 1)]
    T = max(1e-6, (n - 1) / fps)
    fixed = {i: float(wps[i].get("seconds")) for i in range(K - 1) if isinstance(wps[i].get("seconds"), (int, float)) and wps[i]["seconds"] > 0}
    w_free = {i: lens[i] / SEGMENT_SPEEDS.get(wps[i].get("speed", "normal"), 1.0) for i in range(K - 1) if i not in fixed}
    def stops(i):
        return i == 0 or i == K - 1 or bool(wps[i].get("stop_gently")) or float(wps[i].get("pause", 0) or 0) > 0

    Te = min(0.75, T / 4)  # seconds of gentle start and stop: constant speed in between
    extra = {i: 0.5 * Te * (int(stops(i)) + int(stops(i + 1))) for i in w_free}  # time lost while slowing down and setting off
    left = max(0.05 * T, T - sum(fixed.values()) - sum(extra.values()))
    dur = [fixed[i] if i in fixed else extra[i] + left * w_free[i] / max(1e-9, sum(w_free.values())) for i in range(K - 1)]
    scale = T / sum(dur); dur = [d * scale for d in dur]
    starts = np.r_[0, np.cumsum(dur)]

    def progress_table(i):
        """Fraction of segment i done against its time: speed ramps up after a stop and down before one."""
        D = max(1e-9, dur[i]); te = min(Te, D / 2); tt = np.linspace(0, D, 201)
        sp = np.ones_like(tt)
        if stops(i):
            sp *= np.array([smooth01(x / te) for x in tt]) if te > 1e-9 else 1
        if stops(i + 1):
            sp *= np.array([smooth01((D - x) / te) for x in tt]) if te > 1e-9 else 1
        sp = np.maximum(sp, 1e-6); cum = np.r_[0, np.cumsum((sp[1:] + sp[:-1]) / 2 * np.diff(tt))]
        return tt, cum / cum[-1]
    tables = [progress_table(i) for i in range(K - 1)]

    def at(t):
        i = int(min(K - 2, np.searchsorted(starts, t, side="right") - 1)); i = max(0, i)
        tt, frac = tables[i]
        u = float(np.interp(t - starts[i], tt, frac))
        pos, foc = _segment_curve(P, i, u), _segment_curve(F, i, u)
        z = math.exp(_hermite([math.log(max(1e-3, v)) for v in zooms], i, u)) * zall
        fv = _hermite(fovs, i, u)
        return foc + (pos - foc) / z, foc, fv, i, u
    out = []; seen = set(); pauses = {i: max(0, round(fps * float(w.get("pause", 0) or 0))) for i, w in enumerate(wps)}
    for j in range(n):
        t = j / fps if j < n - 1 else T
        pos, foc, fv, i, u = at(t)
        for k in range(K):  # stand still at a point with a pause
            if k not in seen and (starts[k] <= t + 1e-9 if k < K - 1 else j == n - 1):
                seen.add(k)
                if pauses[k]:
                    pk, fk, fvk, _, _ = at(starts[k] if k < K - 1 else T)
                    out += [(pk, fk, fvk)] * pauses[k]
        out.append((pos, foc, fv))
    ups = upright_ups([f - p for p, f, _ in out], np.asarray(main_up, float))
    res = []
    for (p, f, fv), up in zip(out, ups):
        q = _pose(fr, p, f, up)
        if abs(fv - fr.fov) > 1e-6:
            q["fov"] = float(fv); q["scale"] = q["dist"] * math.tan(math.radians(fv) / 2)
        res.append(q)
    return res


def path_poses(part, ds, fr, spec, n, fps, cb):
    """Camera poses of a path part: along the centre line of a structure ("follow"), or through waypoints ("points")."""
    src = part.get("source") or {}; cam = dict(PATH_CAMERA, **(part.get("camera") or {}))
    v = spec.get("view", {}); main_dir, main_up = fr.axes(float(v.get("tilt", 42)), float(v.get("az", 0)))
    if src.get("kind", "follow") == "follow":
        items = [(it.get("structure"), it.get("component")) for it in src.get("items", []) if it.get("structure") is not None]
        if not items:
            raise UserError("Choose the structure the camera should follow.")
        line = path_line(ds, items, cb)
        if part.get("reverse"):
            line = line[::-1]
        if part.get("steadiness") is not None:  # 3.5: a calm tracking shot instead of following every bend
            return steady_follow_poses(part, fr, line, cam, n, fps, main_dir, main_up)
        cum = arclength(line); L = float(cum[-1]); D = float(cam["distance"]) * max(30.0, 0.3 * L)
        z = np.array([0.0, 0.0, 1.0]); h = math.radians(float(cam.get("height", 30)))
        pos, foc, dirs = [], [], []
        for i in range(n):
            d = _ease_or_even(part, i / max(1, n - 1)) * L
            P = sample(line, cum, d); T = sample(line, cum, d + 2) - sample(line, cum, d - 2); T /= np.linalg.norm(T) or 1.0
            upp = z - T * float(z @ T); upp = upp / (np.linalg.norm(upp) or 1.0)
            side = cam.get("side", "above")
            if side == "free":
                off = np.asarray(main_dir, float)
            elif side == "above":
                off = upp
            else:
                sv = np.cross(T, z); sv = sv / (np.linalg.norm(sv) or 1.0) * (1 if side == "left" else -1)
                off = math.cos(h) * sv + math.sin(h) * upp
            C = P + D * off; C[2] = max(C[2], 1.0)  # never below the tomogram
            F = sample(line, cum, d + float(cam.get("lead", 0.05)) * L) if cam.get("look") == "ahead" else P
            pos.append(C); foc.append(F); dirs.append(F - C)
        ups = stable_up(dirs, z if cam.get("side") not in ("above",) else (sample(line, cum, L) - sample(line, cum, 0)))
        return [_pose(fr, p, f, u) for p, f, u in zip(pos, foc, ups)]
    wps = part.get("waypoints") or []
    if len(wps) < 2:
        raise UserError("Add at least two points for the camera to move through.")
    if part.get("interp") == 2:
        return points_poses(part, fr, cam, n, fps, main_dir, main_up)
    D = 60.0 * float(cam["distance"])
    P, F = [], []
    for w in wps:
        if w.get("pos") is not None:
            P.append(np.asarray(w["pos"], float)); F.append(np.asarray(w.get("focal", w["pos"]), float))
        else:  # a point clicked in the preview: the camera passes near it, looking at it
            q = np.asarray(w["point"], float); F.append(q); P.append(q + D * np.asarray(main_dir, float))
    curve, at = catmull_rom(P); fcurve, _ = catmull_rom(F)
    cum = arclength(curve); L = float(cum[-1])
    if L < 1e-6:  # only the looking direction changes: move by index
        cum = np.arange(len(curve), dtype=float); L = float(cum[-1])
    knots = [float(cum[i]) for i in at]
    pauses = {i: max(0, round(fps * float(w.get("pause", 0) or 0))) for i, w in enumerate(wps)}
    poses = []; done = set()
    for j in range(n):
        d = _ease_or_even(part, j / max(1, n - 1)) * L
        for i, kd in enumerate(knots):
            if i not in done and d >= kd - 1e-9:
                done.add(i)
                exact = (P[i], F[i])
                for _ in range(pauses[i]):
                    poses.append(("x",) + exact)
        poses.append((d,))
    out_pos, out_foc = [], []
    for e in poses:
        if e[0] == "x":
            out_pos.append(e[1]); out_foc.append(e[2])
        else:
            out_pos.append(sample(curve, cum, e[0])); out_foc.append(sample(fcurve, cum, e[0]))
    ups = stable_up([f - p for p, f in zip(out_pos, out_foc)], np.asarray(main_up, float))
    return [_pose(fr, p, f, u) for p, f, u in zip(out_pos, out_foc, ups)]
