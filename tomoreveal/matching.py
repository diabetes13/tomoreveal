"""Lining up an original tomogram file with the copy stored in a Dragonfly session."""

import numpy as np

from .common import UserError


def _coarse(a, f):
    """Block-mean by (fz, fy, fx), then remove slow intensity gradients so correlation follows real features."""
    from scipy.ndimage import gaussian_filter
    fz, fy, fx = f
    n = [(s // k) * k for s, k in zip(a.shape, f)]
    c = a[:n[0], :n[1], :n[2]].astype(np.float32).reshape(n[0] // fz, fz, n[1] // fy, fy, n[2] // fx, fx).mean(axis=(1, 3, 5))
    return c - gaussian_filter(c, (1.5, 6, 6))


def _corr(a, b):
    a = a - a.mean(); b = b - b.mean()
    d = float(np.sqrt((a * a).sum() * (b * b).sum()))
    return float((a * b).sum() / d) if d > 0 else 0.0


def _rescale(vol, s):
    """Resample by factor s (< 1 = coarser). Whole-number binning is done by block averaging."""
    b = 1.0 / s
    if abs(b - round(b)) < 0.02 and round(b) >= 2:
        b = int(round(b)); n = [(x // b) * b for x in vol.shape]
        return vol[:n[0], :n[1], :n[2]].astype(np.float32).reshape(n[0] // b, b, n[1] // b, b, n[2] // b, b).mean(axis=(1, 3, 5))
    from scipy.ndimage import zoom
    return zoom(vol.astype(np.float32), s, order=1)


def apply_match(vol, tf, ref_shape):
    """Apply a transform found by match_volume to a volume on the original file's grid."""
    rs = np.array(ref_shape)
    v = vol if tf["s"] == 1.0 else _rescale(vol, tf["s"])
    if np.any(np.array(v.shape) < rs):  # off by a voxel or two from rounding
        v = np.pad(v, [(0, max(0, int(r - x))) for r, x in zip(rs, v.shape)], mode="edge")
    if tf["flip"]:
        v = np.flip(v, tf["flip"])
    off = np.clip(np.array(tf["off"]), 0, np.array(v.shape) - rs)
    return v[off[0]:off[0] + rs[0], off[1]:off[1] + rs[1], off[2]:off[2] + rs[2]]


def match_volume(vol, vox, ref, ref_vox, log=print):
    """Bring a tomogram file onto the grid of the image a Dragonfly session stores (ref).

    Handles what can change at import: flipped axes, binning and cropping. Returns
    (volume on the ref grid, plain-language notes, similarity 0-1, transform).
    Raises UserError when the file does not correspond to the session image."""
    from skimage.feature import match_template
    rs = np.array(ref.shape); fs = np.array(vol.shape)
    fp = max(1, int(round(max(rs[1:]) / 256))); f = (max(1, int(round(rs[0] / 128))), fp, fp)
    f2 = (f[0], 2 * fp, 2 * fp)  # coarser grid for trying the eight possible flips
    scales = []
    ratio = rs / fs.astype(float)
    if ratio.max() / ratio.min() < 1.03 and abs(ratio.mean() - 1) > 0.03:
        scales.append(float(ratio.mean()))           # same field of view at another binning
    if vox and ref_vox and 0.05 < vox / ref_vox < 0.97:
        scales.append(float(vox / ref_vox))          # binned at import, possibly cropped too
    scales = list(dict.fromkeys([round(x, 3) for x in scales] + [1.0]))
    flips = [(), (1,), (0,), (2,), (0, 1), (1, 2), (0, 2), (0, 1, 2)]
    best = None
    for sc in scales:
        v = vol if sc == 1.0 else _rescale(vol, sc)
        vs = np.array(v.shape)
        if np.any(vs < rs - 2):
            continue                                   # does not cover the session image at this scale
        if np.any(vs < rs):
            v = np.pad(v, [(0, max(0, int(r - x))) for r, x in zip(rs, vs)], mode="edge"); vs = np.array(v.shape)
        same = bool(np.all(vs == rs))
        ff = f if same else f2
        R, V = _coarse(ref, ff), _coarse(v, ff)
        for ax in flips:
            Vf = np.flip(V, ax) if ax else V
            if same:
                r = _corr(R, Vf)
            else:
                cc = match_template(Vf, R); r = float(cc.flat[int(np.argmax(np.abs(cc)))])
            if best is None or abs(r) > abs(best["r"]) + (0.03 if ax else 0.0):   # prefer "no flip" unless a flip is clearly better
                best = dict(r=r, s=sc, flip=ax, same=same)
            if not ax and abs(r) >= 0.85:
                break                                  # unflipped already matches
        if best is not None and abs(best["r"]) >= 0.85:
            break
    if best is None:
        raise UserError(f"the tomogram file ({fs[2]} x {fs[1]} x {fs[0]} voxels) does not cover the image stored in the "
                        f"Dragonfly session ({rs[2]} x {rs[1]} x {rs[0]}), so it cannot be the file that was imported")
    tf = dict(s=best["s"], flip=best["flip"], off=(0, 0, 0), src_shape=tuple(int(x) for x in fs))
    if not best["same"]:                               # where the session image sits inside the file
        v = vol if tf["s"] == 1.0 else _rescale(vol, tf["s"])
        if np.any(np.array(v.shape) < rs):
            v = np.pad(v, [(0, max(0, int(r - x))) for r, x in zip(rs, v.shape)], mode="edge")
        if tf["flip"]:
            v = np.flip(v, tf["flip"])
        cc = match_template(_coarse(v, f), _coarse(ref, f))
        off = np.array(np.unravel_index(int(np.argmax(np.abs(cc))), cc.shape)) * np.array(f)
        blk = np.minimum(rs, (48, 192, 192)); b0 = (rs - blk) // 2   # refine on a central block at full resolution
        tmpl = ref[b0[0]:b0[0] + blk[0], b0[1]:b0[1] + blk[1], b0[2]:b0[2] + blk[2]].astype(np.float32)
        m = np.array(f) + 1
        lo = np.maximum(off + b0 - m, 0); hi = np.minimum(off + b0 + blk + m, v.shape)
        reg = v[lo[0]:hi[0], lo[1]:hi[1], lo[2]:hi[2]].astype(np.float32)
        if np.all(np.array(reg.shape) >= blk):
            cc = match_template(reg, tmpl)
            off = lo + np.array(np.unravel_index(int(np.argmax(np.abs(cc))), cc.shape)) - b0
        tf["off"] = tuple(int(x) for x in np.clip(off, 0, np.array(v.shape) - rs))
    out = apply_match(vol, tf, rs)
    r = _corr(_coarse(out, f), _coarse(ref, f))
    tf["identity"] = bool(tf["s"] == 1.0 and not tf["flip"] and best["same"])
    if abs(r) < (0.25 if best["same"] else 0.4):
        if best["same"]:
            tf.update(flip=(), identity=True)
            return vol, [f"it has the same size as Dragonfly's copy but different content (similarity {abs(r):.2f}), so it is used as it is"], abs(r), tf
        raise UserError(f"the tomogram file does not match the image stored in the Dragonfly session (best similarity {abs(r):.2f})")
    notes = []
    if tf["s"] != 1.0:
        b = 1.0 / tf["s"]
        notes.append(f"binned x{int(round(b))}" if abs(b - round(b)) < 0.02 else f"resampled x{tf['s']:.3f}")
    if tf["flip"]:
        names = {0: "Z (slice order reversed)", 1: "Y", 2: "X"}
        notes.append("flipped in " + " and ".join(names[a] for a in sorted(tf["flip"], reverse=True)))
    if not best["same"]:
        z, y, x = tf["off"]
        notes.append(f"cropped to x {x}-{x + rs[2] - 1}, y {y}-{y + rs[1] - 1}, z {z}-{z + rs[0] - 1}")
    if r < 0:
        notes.append("contrast is inverted compared with Dragonfly's copy")
    return out, notes, abs(r), tf
