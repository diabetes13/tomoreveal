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
