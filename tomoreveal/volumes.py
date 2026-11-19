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
    """Optional tomogram plus structures from any number of segmentation sources.

    A structure is one label of one source (a label volume, or one thresholded score volume). Each has an internal
    integer id (k, used by the renderer) and a stable key "<source key>#<label>" that the settings use."""
    def __init__(self, smooth=15, step=1):
        self.tomo = None; self.vox = None; self._shape = None
        self.tomo_name = self.seg_name = ""
        self.classes, self.bbox, self.meshes, self.scores, self._comp, self._lev = {}, {}, {}, {}, {}, {}
        self._labels = {}      # structure id -> (label array of its source, label value)
        self._mesh_cache = {}  # (structure key, threshold, min object size, smoothing) -> surface, kept while sources change
        self.smooth, self.step = smooth, step
        self.params = {}       # structure key -> {"min_object_voxels": N, "smoothing": iterations} (the user's choices)

    @property
    def shape(self):
        """Grid (nz, ny, nx) of everything shown: the tomogram's, or else the segmentation's."""
        return tuple(self.tomo.shape) if self.tomo is not None else self._shape

    def set_tomogram(self, arr, vox, name):
        if arr is None:
            self.tomo = None; self._lev = {}; self.tomo_name = ""
            return
        self.tomo = np.ascontiguousarray(arr, dtype=np.float32); self._lev = {}; self.tomo_name = name
        self.vox = vox or self.vox

    def has_seg(self):
        return bool(self.classes)

    def id_of(self, ref):
        """Structure id from an id, a number given as text, or a stable key; None if there is no such structure."""
        if isinstance(ref, (int, np.integer)):
            return int(ref) if int(ref) in self.classes else None
        if isinstance(ref, str):
            if ref.isdigit():
                return int(ref) if int(ref) in self.classes else None
            for k, c in self.classes.items():
                if c["key"] == ref:
                    return k
        return None

    def key_of(self, k):
        return self.classes[k]["key"]

    def mask(self, k, sl=None):
        if k in self.scores:
            f = self.scores[k]; a = f["arr"] if sl is None else f["arr"][sl]
            return a >= f["thr"] * f["scale"]
        lab, v = self._labels[k]
        return (lab if sl is None else lab[sl]) == v

    @staticmethod
    def label_stats(lab):
        """(voxel count per label, bounding box per label) of a label volume."""
        from scipy import ndimage
        mx = int(lab.max()) if lab.size else 0
        counts = np.bincount(lab.ravel().astype(np.int64), minlength=mx + 1)
        boxes = ndimage.find_objects(lab.astype(np.int32), max_label=max(mx, 1))
        return counts, boxes

    def set_segmentation(self, sources, vox=None, keep_background=False, shape=None):
        """sources: list of dicts in display order, each either
             dict(kind="labels", key, title, arr (int label volume on the grid), classes {label: {name, color}}), or
             dict(kind="scores", key, title, arr, scale, thr, name, color) for one easymode / Ais score volume.
        A label source may carry "_stats" = label_stats(arr); it is computed and stored there when missing."""
        self._comp, self.meshes, self.classes, self.bbox, self.scores, self._labels = {}, {}, {}, {}, {}, {}
        self._shape = tuple(shape) if shape is not None else (tuple(sources[0]["arr"].shape) if sources else None)
        self.seg_name = ", ".join(s.get("title", "") for s in sources); self.vox = self.vox or vox
        k = 0
        for src in sources:
            if src["kind"] == "scores":
                k += 1; self.scores[k] = src
                self.classes[k] = dict(key=f"{src['key']}#1", name=src["name"], color=src.get("color") or PALETTE[(k - 1) % len(PALETTE)],
                                       kind="score", threshold=float(src["thr"]), background=False, hidden=False, named=True, voxels=0, label=1,
                                       source=src["key"], source_title=src.get("title", ""))
                self._rescore(k)
                continue
            lab = src["arr"]
            if not np.issubdtype(lab.dtype, np.integer):
                lab = src["arr"] = np.rint(lab).astype(np.int16)
            if src.get("_stats") is None:
                src["_stats"] = self.label_stats(lab)
            counts, boxes = src["_stats"]; classes = src.get("classes") or {}
            for v in range(1, len(counts)):
                if counts[v] == 0:
                    continue
                k += 1
                c = dict(dict(name=f"class {v}", color=PALETTE[(v - 1) % len(PALETTE)]), **(classes.get(v) or {}))
                c.update(key=f"{src['key']}#{v}", voxels=int(counts[v]), kind="label", label=v, source=src["key"],
                         source_title=src.get("title", ""))
                c["background"] = bool((not keep_background) and (c["name"].lower() in ("background", "bg") or counts[v] > 0.5 * lab.size))
                c["hidden"] = c.get("visible") is False and not c["background"]  # switched off in Dragonfly
                c["named"] = c.get("named", True)
                self.classes[k] = c; self.bbox[k] = boxes[v - 1]; self._labels[k] = (lab, v)
        for k, c in self.classes.items():  # surfaces made earlier for the same structure are reused
            m = self._mesh_cache.get(self._cache_key(k))
            if m is not None:
                self.meshes[k] = m

    def param(self, k, name):
        """A structure's surface setting: minimum object size (voxels) or smoothing iterations."""
        v = (self.params.get(self.classes[k]["key"]) or {}).get(name)
        if name == "smoothing":
            return int(self.smooth if v is None else v)
        return int(v or 0)

    def _cache_key(self, k):
        c = self.classes[k]
        return (c["key"], c.get("threshold"), self.param(k, "min_object_voxels"), self.param(k, "smoothing"))

    def set_params(self, k, **kw):
        """Change a structure's minimum object size and / or smoothing; rebuilds its surface. Returns True if it changed."""
        key = self.classes[k]["key"]; before = self._cache_key(k)
        cur = dict(self.params.get(key) or {}); cur.update({n: v for n, v in kw.items() if n in ("min_object_voxels", "smoothing")})
        self.params[key] = cur
        if self._cache_key(k) == before:
            return False
        had = self.meshes.pop(k, None) is not None
        cached = self._mesh_cache.get(self._cache_key(k))
        if cached is not None:
            self.meshes[k] = cached
        elif had or not self.classes[k]["background"]:
            self._mesh(k, force=True)
        return True

    def kept_mask(self, k, sl):
        """The structure's voxels in sl, without connected objects smaller than its minimum object size."""
        m = self.mask(k, sl); n = self.param(k, "min_object_voxels")
        if n > 1 and m.any():
            from scipy import ndimage
            lbl, cnt = ndimage.label(m, structure=np.ones((3, 3, 3), bool))
            sizes = np.bincount(lbl.ravel())
            small = sizes < n; small[0] = True
            m = ~small[lbl]
        return m

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

    def _mesh(self, k, force=False):
        """Build the surface of structure k; background structures only when asked for (force)."""
        sl = self.bbox.get(k)
        if sl is None or (self.classes[k]["background"] and not force):
            return
        mask = self.kept_mask(k, sl)
        m = surface(mask, [s.start for s in sl], self.param(k, "smoothing"), self.step) if mask.any() else None
        if m is not None:
            self.meshes[k] = m; self._mesh_cache[self._cache_key(k)] = m

    def build_meshes(self, log=print):
        for k in self.classes:
            if k not in self.meshes:
                self._mesh(k)
        log(f"built {len(self.meshes)} surfaces")

    def forget(self, source_key):
        """Drop cached surfaces of a source that was removed."""
        for ck in [ck for ck in self._mesh_cache if ck[0].rsplit("#", 1)[0] == source_key]:
            del self._mesh_cache[ck]

    def levels(self, p_lo, p_hi):
        if self.tomo is None:
            return 0.0, 1.0
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
        return surface(lbl[o] == cid, [s.start for s in sl], self.param(k, "smoothing"), 1)

    def target_box(self, tgt):
        """Padded (lo, hi) box in (x,y,z) voxels around a structure or one of its connected objects."""
        k = self.id_of(tgt["class"])
        if k is None:
            raise KeyError(tgt["class"])
        sl = self._comp_slices(k, int(tgt["component"])) if tgt.get("component") is not None else self.bbox[k]
        lo = np.array([sl[2].start, sl[1].start, sl[0].start], float); hi = np.array([sl[2].stop, sl[1].stop, sl[0].stop], float) - 1
        c = (lo + hi) / 2
        half = np.maximum((hi - lo) / 2 * (1.35 if tgt.get("component") is not None else 1.05), 8.0)
        half[:2] = np.maximum(half[:2], 0.03 * max(self.shape))  # keep some context around very small objects
        return c - half, c + half
