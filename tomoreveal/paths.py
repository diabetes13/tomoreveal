"""Camera paths: the centre line of a structure (to glide along a membrane), and smooth curves through points.

A centre line is found by thinning the object to a skeleton (scikit-image) and taking the longest path through it.
Sheet-like objects (membranes) thin to a surface, not a line: then the line follows the object's longest direction,
through the middle of the voxels at each position along it. Lines are smoothed and resampled evenly.
"""
import math
from collections import deque

import numpy as np

from .common import UserError

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


def tilt_az(direction, u_axis):
    """(tilt, az) of the storyboard's Framer for a view direction (centre -> camera), so transitions can blend."""
    p = np.asarray(direction, float); p = p / (np.linalg.norm(p) or 1.0)
    t = math.degrees(math.acos(max(-1.0, min(1.0, p[2]))))
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
