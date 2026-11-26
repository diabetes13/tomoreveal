"""Flat membrane surfaces with an even thickness (3.6, R21; PLAN-3.6 phase 9).

A smooth model of a membrane instead of the segmentation as it is: the mask is closed (small gaps filled) and split
into pieces; on each piece a blurred copy gives, through its Hessian, the direction across the sheet `n` and the
signed distance `s` of every voxel from the sheet's middle; the surface is drawn at half the membrane's thickness on
either side of that middle, on a grid twice as fine (sheets 2 voxels thick stay whole), and smoothed.

The eigenvector `n` has no fixed sign (it may point either way from one voxel to the next) and `s` flips with it, so
`s` is never smoothed on its own: the vector `s·n` is (it is the same whichever way `n` points).
"""
import numpy as np
from scipy import ndimage

GAPS = (("small (1 voxel)", 1), ("medium (3 voxels)", 3), ("large (6 voxels)", 6))
FLATNESS = (("less", 2.0), ("medium", 3.0), ("more", 4.0))
DEFAULT = dict(gaps=3, flatness=3.0, thickness=None)
MIN_PIECE = 500        # voxels: smaller pieces keep the normal surface
TILE = 128             # coarse voxels per tile side for the fine grid (255 fine voxels): bounded memory
RIDGE_KEEP = 0.4       # keep voxels whose ridge strength is at least 40 % of the median on the membrane
KEEP_PART = 0.02       # parts of a piece's surface smaller than 2 % of its largest part are left out
MAX_SAMPLES = 6000     # voxels sampled to measure the thickness


def settings(flat):
    """The flat-membrane settings with defaults: gaps (closing iterations), flatness (blur), thickness (voxels or None)."""
    out = dict(DEFAULT)
    if isinstance(flat, dict):
        out.update({k: v for k, v in flat.items() if k in DEFAULT})
    elif isinstance(flat, (tuple, list)) and len(flat) == 3:
        out.update(gaps=flat[0], flatness=flat[1], thickness=flat[2])
    out["gaps"] = int(out["gaps"]); out["flatness"] = float(out["flatness"])
    out["thickness"] = None if out["thickness"] in (None, "", 0) else float(out["thickness"])
    return out


def key(flat):
    """A hashable form of the settings (cache keys)."""
    s = settings(flat)
    return (s["gaps"], s["flatness"], s["thickness"])


def pieces(mask, gaps):
    """The mask closed by `gaps` iterations of a 3x3x3 element (gaps filled), and its connected pieces (labels, n)."""
    ball = np.ones((3, 3, 3), bool)
    if gaps > 0:
        p = gaps + 1
        closed = ndimage.binary_closing(np.pad(mask, p), structure=ball, iterations=gaps)[p:-p, p:-p, p:-p] | mask
    else:
        closed = mask.copy()
    lab, n = ndimage.label(closed, ball)
    return closed, lab, n


def offsets(m, sigma, region, flip=None):
    """For a float mask m: the vector from each voxel of `region` to the sheet's middle (3 arrays, zeros elsewhere),
    whether it is valid there (a ridge: most negative Hessian eigenvalue < 0), and that eigenvalue (lambda1).
    flip: voxels whose n is turned round on purpose (tests: the result must not change)."""
    orders1 = ((1, 0, 0), (0, 1, 0), (0, 0, 1))
    g = [ndimage.gaussian_filter(m, sigma, order=o) for o in orders1]
    idx = np.nonzero(region); N = len(idx[0])
    H = np.empty((N, 3, 3), np.float64)
    for i in range(3):
        for j in range(i, 3):
            o = [0, 0, 0]; o[i] += 1; o[j] += 1
            h = ndimage.gaussian_filter(m, sigma, order=o)[idx]
            H[:, i, j] = h; H[:, j, i] = h
    del h
    w, v = np.linalg.eigh(H); del H
    l1 = w[:, 0]; n = v[:, :, 0]
    if flip is not None:
        n[flip[idx]] *= -1
    gv = np.stack([gi[idx] for gi in g], 1)
    ok = l1 < -1e-6
    s = np.where(ok, (gv * n).sum(1) / np.where(ok, l1, -1.0), 0.0)
    d = [np.zeros(m.shape, np.float32) for _ in range(3)]
    for c in range(3):
        d[c][idx] = (s * n[:, c]).astype(np.float32)
    valid = np.zeros(m.shape, bool); valid[idx] = ok
    lam = np.zeros(m.shape, np.float32); lam[idx] = l1
    nn = [np.zeros(m.shape, np.float32) for _ in range(3)]
    for c in range(3):
        nn[c][idx] = n[:, c]
    return d, valid, lam, nn


def smooth_vector(d, valid, sigma=1.0):
    """The vector field smoothed with a Gaussian, weighted by where it is valid (never the length on its own)."""
    w = valid.astype(np.float32); ws = ndimage.gaussian_filter(w, sigma)
    return [np.where(ws > 1e-3, ndimage.gaussian_filter(c * w, sigma) / np.maximum(ws, 1e-3), 0.0).astype(np.float32) for c in d]


def measure_thickness(mask, dist, nn, valid):
    """Median thickness (voxels) across the sheet: from voxels near its middle, the mask is followed along +n and -n
    in 0.25-voxel steps until it drops below 0.5."""
    sel = np.argwhere(mask & valid & (dist < 0.5))
    if len(sel) == 0:
        return None
    if len(sel) > MAX_SAMPLES:
        sel = sel[np.random.default_rng(0).choice(len(sel), MAX_SAMPLES, replace=False)]
    n = np.stack([nn[c][tuple(sel.T)] for c in range(3)], 1)
    m = mask.astype(np.float32); steps = np.arange(0.25, 12.01, 0.25)
    out = np.zeros(len(sel))
    for sign in (1.0, -1.0):
        pts = sel[None, :, :] + sign * steps[:, None, None] * n[None, :, :]  # (steps, N, 3)
        vals = ndimage.map_coordinates(m, pts.reshape(-1, 3).T, order=1, mode="constant").reshape(len(steps), -1)
        inside = np.cumprod(vals >= 0.5, axis=0)  # still inside, step by step
        out += inside.sum(0) * 0.25  # the last step still inside: on average just short of the edge on each side
    return float(np.median(out))


def _up2(a):
    """Linear upsampling by 2 on every axis (n -> 2n - 1 samples): tiles that share a border get the same samples."""
    for ax in range(3):
        n = a.shape[ax]
        if n < 2:
            continue
        shp = list(a.shape); shp[ax] = 2 * n - 1; out = np.empty(shp, np.float32)
        sl = [slice(None)] * 3
        sl[ax] = slice(0, None, 2); out[tuple(sl)] = a
        lo = [slice(None)] * 3; hi = [slice(None)] * 3; lo[ax] = slice(0, -1); hi[ax] = slice(1, None)
        sl[ax] = slice(1, None, 2); out[tuple(sl)] = 0.5 * (a[tuple(lo)] + a[tuple(hi)])
        a = out
    return a


def _keep_main_parts(pd):
    """The largest connected part of a piece's surface, and the parts above KEEP_PART of it."""
    conn = pd.connectivity("all")
    rid = np.asarray(conn.cell_data["RegionId"]).copy(); counts = np.bincount(rid)
    keep = np.nonzero(counts >= KEEP_PART * counts.max())[0]
    if len(keep) < len(counts):
        conn = conn.extract_cells(np.nonzero(np.isin(rid, keep))[0]).extract_surface(algorithm="dataset_surface")
    conn.clear_data()
    return conn


def true_distance(s, k):
    """The real distance from the sheet's middle for the estimate s = (g.n)/lambda1. For a sheet blurred to a
    Gaussian-like profile of variance k, s = x / (1 - x^2/k): the estimate grows without bound away from the middle
    (it was 40 voxels 3 voxels out on a test plate), so it is turned back into x, which stays below sqrt(k)."""
    s = np.abs(np.asarray(s, np.float64))
    with np.errstate(divide="ignore", invalid="ignore"):
        x = np.where(s > 1e-6, (np.sqrt(1.0 + 4.0 * s * s / k) - 1.0) * k / (2.0 * s), s)
    return x


def _triangles_only(pd):
    """The triangles of a mesh, without degenerate ones, loose lines or cell arrays (decimation needs that)."""
    import pyvista as pv
    pd = pd.triangulate()
    f = np.asarray(pd.faces)
    if pd.n_lines or pd.n_verts or pd.n_strips:
        pd = pv.PolyData(pd.points, pd.faces); f = np.asarray(pd.faces)
    tri = f.reshape(-1, 4)[:, 1:] if len(f) else np.zeros((0, 3), int)
    ok = (tri[:, 0] != tri[:, 1]) & (tri[:, 1] != tri[:, 2]) & (tri[:, 0] != tri[:, 2])
    tri = tri[ok]
    return pv.PolyData(np.asarray(pd.points), np.hstack([np.full((len(tri), 1), 3), tri]).ravel())


MID_SMOOTH = 2.5  # voxels: each point of the middle is averaged with its neighbours within this distance along the sheet


def piece_middle(mask_c, closed_c, sigma, flip=None, thickness=None):
    """For one piece (cropped, padded): points on the middle of the sheet, smoothed along it, the voxels where the
    surface may be (`region`), and the measured thickness. Each voxel of the piece that lies on a ridge of the blurred
    mask moves to the middle by its own offset s.n (corrected for the blur, true_distance), so the points do not depend
    on which way n points."""
    from scipy.spatial import cKDTree
    m = mask_c.astype(np.float32)  # the ridges of the segmentation itself: two close sheets stay two sheets
    region = ndimage.binary_dilation(closed_c, np.ones((3, 3, 3), bool), iterations=3)
    d, valid, lam, nn = offsets(m, sigma, closed_c, flip)  # the filled gaps (closed) give points of the middle too
    raw = np.sqrt(d[0] ** 2 + d[1] ** 2 + d[2] ** 2)
    t = measure_thickness(mask_c, raw, nn, valid)
    T = thickness or t or 2.0
    k = sigma * sigma + T * T / 12.0  # the blurred profile's variance (a box of width T blurred by sigma)
    sel = valid & closed_c
    mid = sel & (raw < 0.5)
    if mid.any():  # ridge condition: leave out weak ridges (far from the membrane, or between two sheets)
        sel &= -lam >= RIDGE_KEEP * float(np.median(-lam[mid]))
    sel &= raw < 3.0 * max(T, 1.0)
    idx = np.nonzero(sel)
    if len(idx[0]) == 0:
        return None, region, t
    r = raw[idx]; scale = np.where(r > 1e-6, true_distance(r, k) / np.maximum(r, 1e-6), 1.0)
    off = np.stack([d[c][idx] * scale for c in range(3)], 1)
    P = np.stack(idx, 1).astype(np.float64) - off  # x = m + s.n  ->  m = x - s.n
    tree = cKDTree(P); dd, ii = tree.query(P, k=24, distance_upper_bound=MID_SMOOTH)
    ok = np.isfinite(dd); ii = np.where(ok, ii, 0)
    w = ok.astype(np.float64)
    P = (P[ii] * w[..., None]).sum(1) / w.sum(1)[:, None]
    return P, region, t


def piece_field(mask_c, closed_c, sigma, flip=None, thickness=None):
    """(middle points, region, measured thickness) of one piece; see piece_middle."""
    return piece_middle(mask_c, closed_c, sigma, flip, thickness)


def _field_tiles(P, region, T, off_zyx):
    """The surface at T/2 from the middle points P, on a grid twice as fine, in tiles: (vertices zyx, faces)."""
    from scipy.spatial import cKDTree
    from skimage.measure import marching_cubes
    tree = cKDTree(P); vs, fs, nv = [], [], 0
    starts = [list(range(0, max(1, n - 1), TILE - 1)) for n in region.shape]
    for z0 in starts[0]:
        for y0 in starts[1]:
            for x0 in starts[2]:
                rt = region[z0:z0 + TILE, y0:y0 + TILE, x0:x0 + TILE]
                if min(rt.shape) < 2 or not rt.any():
                    continue
                rf = _up2(rt.astype(np.float32)) > 0.01
                F = np.full(rf.shape, -1.0, np.float32)
                idx = np.nonzero(rf)
                q = np.stack(idx, 1) / 2.0 + np.array([z0, y0, x0], np.float64)
                dist, _ = tree.query(q, distance_upper_bound=T)
                F[idx] = np.where(np.isfinite(dist), T / 2.0 - dist, -1.0).astype(np.float32)
                if F.max() <= 0:
                    continue
                try:
                    v, f, _, _ = marching_cubes(F, 0.0)
                except (ValueError, RuntimeError):
                    continue
                vs.append(v / 2.0 + np.array([z0, y0, x0], np.float32) + np.asarray(off_zyx, np.float32)); fs.append(f + nv); nv += len(v)
    if not vs:
        return None, None
    return np.concatenate(vs), np.concatenate(fs)


def flat_surface(mask, off_zyx, full_shape=None, flat=None, smooth=20, step=1, info=None, flip=None):
    """The flat-sheet surface of a boolean (z, y, x) mask, in (x, y, z) voxel coordinates (a pyvista PolyData), or
    None. info (dict): gets "thickness" (voxels, the one used), "measured" and "pieces". flip: see offsets()."""
    import pyvista as pv
    from .volumes import surface
    st = settings(flat); sigma = st["flatness"]
    closed, lab, n = pieces(mask, st["gaps"])
    objs = ndimage.find_objects(lab)
    pad = int(np.ceil(3 * sigma)) + 4
    big, small = [], []
    for i, sl in enumerate(objs, start=1):
        if sl is None:
            continue
        (big if int((lab[sl] == i).sum()) >= MIN_PIECE else small).append((i, sl))
    mids, ts, ws = [], [], []
    for i, sl in big:
        crop = tuple(slice(s.start, s.stop) for s in sl)
        pc = np.pad(lab[crop] == i, pad); mc = np.pad(mask[crop] & (lab[crop] == i), pad)
        fl = None if flip is None else np.pad(flip[crop], pad)
        P, region, t = piece_field(mc, pc, sigma, fl, st["thickness"])
        if P is not None:
            mids.append((P, region, [s.start - pad for s in sl]))
        if t is not None:
            ts.append(t); ws.append(int(mc.sum()))
    measured = float(np.average(ts, weights=ws)) if ts else None
    T = max(1.0, st["thickness"] or measured or 2.0)
    parts = []
    for P, region, off in mids:
        v, f = _field_tiles(P, region, T, np.asarray(off_zyx) + np.asarray(off))
        if v is None:
            continue
        pd = pv.PolyData(v[:, ::-1].astype(np.float32), np.hstack([np.full((len(f), 1), 3), f[:, ::-1]]).ravel()).clean(tolerance=1e-6)
        if pd.n_cells == 0:
            continue
        pd = _triangles_only(_keep_main_parts(pd))
        pd = pd.smooth_taubin(n_iter=30, pass_band=0.05)
        if pd.n_cells > 2000:
            pd = _triangles_only(pd).decimate_pro(0.6, preserve_topology=True)
        parts.append(pd)
    for i, sl in small:  # small pieces: the normal surface of what was segmented there
        crop = tuple(slice(s.start, s.stop) for s in sl)
        sm = surface(mask[crop] & (lab[crop] == i), [o + s.start for o, s in zip(off_zyx, sl)], smooth, step, 0, full_shape)
        if sm is not None and sm.n_points:
            parts.append(sm)
    if info is not None:
        info.update(thickness=T, measured=measured, pieces=len(big) + len(small))
    if not parts:
        return None
    out = parts[0] if len(parts) == 1 else pv.merge(parts)
    if full_shape is not None:  # sheets cut by the grid's edge end on it, as with the normal surface
        hi = np.asarray(full_shape, np.float32)[::-1] - 0.5
        out.points = np.clip(np.asarray(out.points), -0.5, hi)
    out = out.compute_normals(auto_orient_normals=False, consistent_normals=True, split_vertices=False)
    out.field_data["thickness"] = np.array([T], np.float32)
    if measured is not None:
        out.field_data["measured"] = np.array([measured], np.float32)
    return out
