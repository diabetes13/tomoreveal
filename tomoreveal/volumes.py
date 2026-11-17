"""Reading volumes, recognising what a file is, surfaces, and the loaded dataset."""
import json
import os

import numpy as np

from .common import PALETTE, UserError


def load_volume(path):
    if not os.path.isfile(path):
        raise UserError(f"file not found: {path}")
    ext = os.path.splitext(path)[1].lower()
    vox = None
    if ext in (".tif", ".tiff"):
        import tifffile
        a = tifffile.imread(path)
    elif ext in (".mrc", ".rec", ".map", ".mrcs", ".st"):
        import mrcfile
        with mrcfile.open(path, permissive=True) as m:
            a = np.array(m.data); vox = float(m.voxel_size.x) or None
    else:
        raise UserError(f"unsupported file type: {os.path.basename(path)} (use .mrc/.rec/.map/.tif or .ORSSession)")
    if a.ndim != 3:
        raise UserError(f"{os.path.basename(path)}: expected a 3D volume, found shape {a.shape}")
    return a, vox


def looks_like_labels(a):
    if not np.issubdtype(a.dtype, np.integer):
        return False
    sub = a[::max(1, a.shape[0] // 16), ::7, ::7]
    return len(np.unique(sub)) <= 64


def score_scale(a):
    """Value meaning 'certain' in a per-structure score volume (easymode: int8 x127, uint16 x255, float32 0-1)."""
    sub = a[::max(1, a.shape[0] // 16), ::5, ::5]; mx = float(sub.max()) if sub.size else 1.0
    if np.issubdtype(a.dtype, np.integer):
        return 1.0 if mx <= 1 else 127.0 if a.dtype == np.int8 else 255.0
    return 1.0 if mx <= 1.5 else mx


def looks_like_scores(a):
    """Non-negative, bounded, and mostly near zero: a probability-like map rather than a tomogram."""
    sub = a[::max(1, a.shape[0] // 16), ::7, ::7]
    lo, hi = float(sub.min()), float(sub.max())
    if lo < 0 or hi > (255 if np.issubdtype(a.dtype, np.integer) else 1.0):
        return False
    return float((sub < 0.1 * score_scale(a)).mean()) > 0.5


def regrid(a, shape, src, log=print):
    """Bring a score volume made at another binning of the same field of view onto the tomogram grid."""
    fac = np.array(shape, float) / np.array(a.shape, float)
    if fac.max() / fac.min() > 1.03:
        raise UserError(f"{src}: its grid is {a.shape[2]} x {a.shape[1]} x {a.shape[0]} voxels but the tomogram is "
                        f"{shape[2]} x {shape[1]} x {shape[0]}, and the proportions differ, so it is not the same volume.")
    from scipy.ndimage import zoom
    log(f"{src}: resampling {a.shape[::-1]} -> {tuple(shape)[::-1]} (same field of view at a different binning)")
    out = zoom(a.astype(np.float32), fac, order=1)
    res = np.zeros(shape, np.float32); sl = tuple(slice(0, min(o, t)) for o, t in zip(out.shape, shape)); res[sl] = out[sl]
    return res


def write_mrc(path, arr, vox):
    import mrcfile
    with mrcfile.new(path, overwrite=True) as m:
        m.set_data(arr)
        if vox:
            m.voxel_size = (vox, vox, vox)


def sidecar_classes(path):
    """Names and colours for a label volume from '<file>.json' or 'classes.json' beside it: {"1": {"name": ..., "color": ...}}."""
    for cand in (os.path.splitext(path)[0] + ".json", os.path.join(os.path.dirname(path), "classes.json")):
        if os.path.isfile(cand):
            try:
                return {int(k): {kk: v[kk] for kk in ("name", "color") if kk in v} for k, v in json.load(open(cand)).items()}
            except Exception as exc:
                print(f"  note: could not read {cand} ({exc})")
    return {}


def surface(mask, off_zyx, smooth, step):
    """Smoothed isosurface of a boolean (z,y,x) array, returned in (x,y,z) voxel coordinates."""
    import pyvista as pv
    from skimage.measure import marching_cubes
    sub = np.pad(mask.astype(np.float32), 1)  # pad closes surfaces at the volume edge
    try:
        v, f, _, _ = marching_cubes(sub, 0.5, step_size=step)
    except (ValueError, RuntimeError):
        return None
    v = (v + np.asarray(off_zyx, np.float32) - 1.0)[:, ::-1].astype(np.float32)
    pd = pv.PolyData(v, np.hstack([np.full((len(f), 1), 3), f[:, ::-1]]).ravel())
    if smooth > 0:
        pd = pd.smooth_taubin(n_iter=int(smooth), pass_band=0.1)  # removes voxel steps without shrinking thin features
    return pd.compute_normals(auto_orient_normals=False, consistent_normals=True, split_vertices=False)


class Dataset:
    """Tomogram plus structures. A structure is either one value of a label volume or a thresholded score volume."""
    def __init__(self, smooth=15, step=1):
        self.tomo = self.lab = None; self.vox = None
        self.tomo_name = self.seg_name = ""
        self.classes, self.bbox, self.meshes, self.scores, self._comp, self._lev = {}, {}, {}, {}, {}, {}
        self.smooth, self.step = smooth, step

    def set_tomogram(self, arr, vox, name):
        self.tomo = np.ascontiguousarray(arr, dtype=np.float32); self._lev = {}; self.tomo_name = name
        self.vox = vox or self.vox

    def has_seg(self):
        return bool(self.classes)

    def mask(self, k, sl=None):
        if k in self.scores:
            f = self.scores[k]; a = f["arr"] if sl is None else f["arr"][sl]
            return a >= f["thr"] * f["scale"]
        a = self.lab if sl is None else self.lab[sl]
        return a == k

    def set_segmentation(self, lab, classes, vox, name, feats=(), keep_background=False):
        from scipy import ndimage
        self._comp, self.meshes, self.classes, self.bbox, self.scores = {}, {}, {}, {}, {}
        self.seg_name = name; self.vox = self.vox or vox; mx = 0
        if lab is not None and not np.issubdtype(lab.dtype, np.integer):
            lab = np.rint(lab).astype(np.int16)
        self.lab = lab
        if lab is not None:
            mx = int(lab.max()) if lab.size else 0
            counts = np.bincount(lab.ravel().astype(np.int64), minlength=mx + 1)
            boxes = ndimage.find_objects(lab.astype(np.int32), max_label=max(mx, 1))
            for k in range(1, mx + 1):
                if counts[k] == 0:
                    continue
                c = dict(dict(name=f"class {k}", color=PALETTE[(k - 1) % len(PALETTE)]), **(classes.get(k) or {}))
                c["voxels"] = int(counts[k]); c["kind"] = "label"
                c["background"] = bool((not keep_background) and (c["name"].lower() in ("background", "bg") or counts[k] > 0.5 * lab.size))
                self.classes[k] = c; self.bbox[k] = boxes[k - 1]
        for i, f in enumerate(feats):  # score volumes become extra structures after the label classes
            k = mx + 1 + i
            self.scores[k] = f
            self.classes[k] = dict(name=f["name"], color=f.get("color") or PALETTE[(k - 1) % len(PALETTE)], kind="score",
                                   threshold=float(f["thr"]), background=False, voxels=0)
            self._rescore(k)

    def _rescore(self, k):
        from scipy import ndimage
        m = self.mask(k); n = int(m.sum())
        self.classes[k]["voxels"] = n
        self.bbox[k] = ndimage.find_objects(m.astype(np.uint8))[0] if n else None
        self._comp.pop(k, None); self.meshes.pop(k, None)

    def set_threshold(self, k, thr):
        thr = min(0.99, max(0.01, float(thr)))
        self.scores[k]["thr"] = thr; self.classes[k]["threshold"] = thr
        self._rescore(k); self._mesh(k)

    def _mesh(self, k):
        sl = self.bbox.get(k)
        if sl is None or self.classes[k]["background"]:
            return
        m = surface(self.mask(k, sl), [s.start for s in sl], self.smooth, self.step)
        if m is not None:
            self.meshes[k] = m

    def build_meshes(self, log=print):
        for k in self.classes:
            if k not in self.meshes:
                self._mesh(k)
        log(f"built {len(self.meshes)} surfaces")

    def levels(self, p_lo, p_hi):
        key = (float(p_lo), float(p_hi))
        if key not in self._lev:
            sub = self.tomo[::max(1, self.tomo.shape[0] // 32), ::4, ::4]
            lo, hi = np.percentile(sub, key)
            self._lev[key] = (float(lo), float(hi) if hi > lo else float(lo) + 1e-6)
        return self._lev[key]

    # connected objects inside a structure, for click-to-focus
    def components(self, k):
        if k not in self._comp:
            from scipy import ndimage
            lbl, _ = ndimage.label(self.mask(k, self.bbox[k]), structure=np.ones((3, 3, 3), bool))
            self._comp[k] = (lbl, ndimage.find_objects(lbl), np.bincount(lbl.ravel()))
        return self._comp[k]

    def pick_component(self, k, xyz, r=4):
        lbl, _, _ = self.components(k); sl = self.bbox[k]
        p = np.array([xyz[2] - sl[0].start, xyz[1] - sl[1].start, xyz[0] - sl[2].start])
        c = np.rint(p).astype(int); lo = np.maximum(c - r, 0); hi = np.minimum(c + r + 1, lbl.shape)
        if np.any(hi <= lo):
            return None
        win = lbl[lo[0]:hi[0], lo[1]:hi[1], lo[2]:hi[2]]; nzp = np.argwhere(win > 0)
        if not len(nzp):
            return None
        j = int(np.argmin(((nzp + lo - p) ** 2).sum(1)))
        return int(win[tuple(nzp[j])])

    def _comp_slices(self, k, cid):
        _, objs, _ = self.components(k)
        if cid < 1 or cid > len(objs) or objs[cid - 1] is None:
            raise KeyError(cid)
        return tuple(slice(o.start + b.start, o.stop + b.start) for o, b in zip(objs[cid - 1], self.bbox[k]))

    def comp_mesh(self, k, cid):
        lbl, objs, _ = self.components(k); o = objs[cid - 1]; sl = self._comp_slices(k, cid)
        return surface(lbl[o] == cid, [s.start for s in sl], self.smooth, 1)

    def target_box(self, tgt):
        """Padded (lo, hi) box in (x,y,z) voxels around a structure or one of its connected objects."""
        k = int(tgt["class"])
        sl = self._comp_slices(k, int(tgt["component"])) if tgt.get("component") is not None else self.bbox[k]
        lo = np.array([sl[2].start, sl[1].start, sl[0].start], float); hi = np.array([sl[2].stop, sl[1].stop, sl[0].stop], float) - 1
        c = (lo + hi) / 2
        half = np.maximum((hi - lo) / 2 * (1.35 if tgt.get("component") is not None else 1.05), 8.0)
        half[:2] = np.maximum(half[:2], 0.03 * max(self.tomo.shape))  # keep some context around very small objects
        return c - half, c + half
