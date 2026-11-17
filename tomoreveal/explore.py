"""Looking through the slices by hand: one tomogram slice with the segmentation drawn on it (NumPy only, no VTK)."""
import numpy as np

from .common import hex2rgb
from .storyboard import class_spec, clip_box


def grey_slice(ds, k, contrast=(1, 99), invert=False):
    """Slice k of the tomogram as uint8 grey levels (ny, nx); black when there is no tomogram."""
    nz, ny, nx = ds.shape
    if ds.tomo is None:
        return np.zeros((ny, nx), np.uint8)
    lo, hi = ds.levels(contrast[0], contrast[1])
    g = np.clip((ds.tomo[k] - lo) * (255.0 / (hi - lo)), 0, 255).astype(np.uint8)
    return 255 - g if invert else g


def _edges(lab2d):
    """Pixels where the label differs from a neighbour (outlines of every region of a label slice)."""
    e = np.zeros(lab2d.shape, bool)
    d = lab2d[1:, :] != lab2d[:-1, :]; e[1:, :] |= d; e[:-1, :] |= d
    d = lab2d[:, 1:] != lab2d[:, :-1]; e[:, 1:] |= d; e[:, :-1] |= d
    return e & (lab2d > 0)


def structure_layers(ds, spec, k):
    """For slice k: a list of (id map (ny, nx) int, {id: rgb 0-1}) per source, visible structures only.
    Several sources are composed one after another; their labels may overlap."""
    layers = {}
    for sid, c in ds.classes.items():
        cs = class_spec(spec, ds, sid)
        if not cs.get("show", not (c["background"] or c.get("hidden"))):
            continue
        col = hex2rgb(cs.get("color", c["color"]))
        if c["kind"] == "score":
            m = ds.mask(sid, (slice(k, k + 1),))[0]
            layers[("score", sid)] = (m.astype(np.int32) * sid, {sid: col})
        else:
            lab, v = ds._labels[sid]
            key = ("labels", id(lab))
            if key not in layers:
                layers[key] = (lab[k], {})
            layers[key][1][v] = col
    return list(layers.values())


def slice_image(ds, spec, k, overlay="filled", alpha=0.4, contrast=None, invert=None):
    """RGB uint8 image (ny, nx, 3) of slice k: grey tomogram, structures blended on top ("filled"), drawn as outlines,
    or "off". Slices outside the movie's slice range or the clip box are dimmed."""
    style = spec.get("style", {})
    contrast = contrast or style.get("contrast", [1, 99]); invert = style.get("invert", False) if invert is None else invert
    g = grey_slice(ds, k, contrast, invert)
    img = np.repeat(g[:, :, None], 3, axis=2).astype(np.float32)
    if overlay != "off" and ds.has_seg():
        a = float(min(1.0, max(0.0, alpha)))
        for ids, cols in structure_layers(ds, spec, k):
            if not cols:
                continue
            lut = np.zeros((int(max(max(cols), ids.max() if ids.size else 0)) + 1, 3), np.float32)
            use = np.zeros(len(lut), bool)
            for v, c in cols.items():
                lut[v] = np.array(c) * 255; use[v] = True
            ids = np.clip(ids, 0, len(lut) - 1)
            sel = use[ids]
            if overlay == "outlines":
                sel &= _edges(ids)
                img[sel] = lut[ids[sel]]  # outlines are drawn solid, so they stay readable
            else:
                img[sel] = img[sel] * (1 - a) + lut[ids[sel]] * a
    nz = ds.shape[0]; zr = spec.get("z_range") or [0, nz - 1]; cb = clip_box(spec, ds.shape)
    outside = not (zr[0] <= k <= zr[1]) or (cb is not None and not (cb[0][2] <= k <= cb[1][2]))
    if outside:
        img *= 0.45
    return img.astype(np.uint8)


def readout(ds, spec, k, x, y):
    """What is at voxel (x, y) of slice k: dict(x, y, z_nm, grey, structure name or None, structure key)."""
    nz, ny, nx = ds.shape
    if not (0 <= x < nx and 0 <= y < ny):
        return None
    out = dict(x=int(x), y=int(y), z_nm=(k * ds.vox / 10.0) if ds.vox else None, grey=None, name=None, key=None)
    if ds.tomo is not None:
        out["grey"] = float(ds.tomo[k, int(y), int(x)])
    for sid, c in reversed(list(ds.classes.items())):  # the structure drawn last is on top
        cs = class_spec(spec, ds, sid)
        if not cs.get("show", True):
            continue
        if c["kind"] == "score":
            hit = bool(ds.mask(sid, (slice(k, k + 1), slice(int(y), int(y) + 1), slice(int(x), int(x) + 1))).any())
        else:
            lab, v = ds._labels[sid]; hit = lab[k, int(y), int(x)] == v
        if hit:
            out.update(name=cs.get("name", c["name"]), key=c["key"])
            break
    return out
