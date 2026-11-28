"""Reading volumes, recognising what a file is, surfaces, and the loaded dataset."""
import copy
import hashlib
import itertools
import json
import os
import threading

import numpy as np

from .common import PALETTE, UserError, label_dtype, read_json

_VERSIONS = itertools.count(1)


def new_version():
    """A number for voxels that have just been read or placed. It never comes again, also not after a failed load, so what
    was made from other voxels can always be told apart."""
    return next(_VERSIONS)


def clean_labels(a, path):
    """A label volume with label numbers 0 and up. MRC mode 0 is signed, so an 8-bit file that reads as negative numbers
    (a 0/255 mask, labels 128-255) is taken as 0-255; whole-number floats become integers; other negative labels are an error."""
    if a.dtype == np.int8 and a.size and a.min() < 0:
        a = a.view(np.uint8)
    elif not np.issubdtype(a.dtype, np.integer):
        a = np.rint(a)
    lo, hi = (int(a.min()), int(a.max())) if a.size else (0, 0)
    if lo < 0:
        raise UserError(f"{os.path.basename(path)}: label numbers must be 0 or more, but the file holds {lo}")
    return a.astype(label_dtype(hi)) if a.dtype == np.uint8 or not np.issubdtype(a.dtype, np.integer) else a


def load_volume(path):
    if not os.path.isfile(path):
        raise UserError(f"file not found: {path}")
    ext = os.path.splitext(path)[1].lower()
    vox = None
    if ext in (".tif", ".tiff"):
        import tifffile
        a = tifffile.imread(path)
        if a.ndim == 3 and a.shape[-1] in (3, 4):
            raise UserError(f"{os.path.basename(path)} is a colour (RGB) image, not a 3D volume")
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
    """Few distinct whole numbers, with background (0) the most common value: a label volume rather than a tomogram.
    Floats count when they are whole numbers above 1 (labels saved as float32); a 0/1 float mask stays a score map."""
    sub = a[::max(1, a.shape[0] // 16), ::7, ::7]
    if not np.issubdtype(a.dtype, np.integer) and not (np.isfinite(sub).all() and np.array_equal(sub, np.rint(sub)) and sub.max() > 1):
        return False
    vals, counts = np.unique(sub, return_counts=True)
    if not (vals[counts.argmax()] == 0 and counts.max() >= 0.3 * sub.size):
        return False
    return len(vals) <= 64 or _patches_of_one_number(a)


def _patches_of_one_number(a):
    """Many numbers (an instance segmentation): labels are patches of one number, a tomogram is noise. Measured: label
    files 0.997 to 1.0 of neighbouring non-zero voxels equal, tomograms with a zero border at most 0.19 (denoised 8-bit)."""
    s = a[::max(1, a.shape[0] // 16)]
    left, right = s[:, :, :-1], s[:, :, 1:]; both = (left != 0) & (right != 0)
    return bool(both.any()) and float((left[both] == right[both]).mean()) >= 0.8


def score_scale(a):
    """Value meaning 'certain' in a per-structure score volume (easymode: int8 x127, uint16 x255, float32 0-1)."""
    sub = a[::max(1, a.shape[0] // 16), ::5, ::5]; mx = float(sub.max()) if sub.size else 1.0
    if np.issubdtype(a.dtype, np.integer):
        return 1.0 if mx <= 1 else 127.0 if a.dtype == np.int8 else 255.0
    return 1.0 if mx <= 1.5 else mx


def guess_kind(a, stem, have_tomo, nvol):
    """What a volume given without a role probably is: "tomogram", "labels" or "scores" (an easymode / Ais score map)."""
    scores, labels = looks_like_scores(a), looks_like_labels(a)
    binary = float(a[::max(1, a.shape[0] // 16), ::7, ::7].max()) <= 1  # a 0/1 mask works as a score map too
    named = "__" in stem  # easymode / Ais: <tomogram>__<feature>.mrc
    if scores and (not labels or (binary and named)) and (named or have_tomo or nvol > 1):
        return "scores"
    return "labels" if labels else "tomogram"


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
                return {int(k): {kk: v[kk] for kk in ("name", "color") if kk in v} for k, v in read_json(cand).items()}
            except Exception as exc:
                print(f"  note: could not read {cand} ({exc})")
    return {}


SOFTEN = (("off", 0), ("a little", 1), ("more", 2), ("much", 3))
SOFTEN_SIGMA = {1: 0.7, 2: 1.0, 3: 1.4}  # voxels; with 1.4 a membrane 2 voxels thick still stays whole (1 voxel: up to 0.6)
SOFTEN_STEPS = {1: 30, 2: 60, 3: 120}    # extra smoothing steps (they do not shrink the surface)


def _keep_thin(m, g):
    """The blurred mask g, with the parts the blur would lose put back: thin sheets, thin rods and small objects. Voxels
    of the mask that end up more than one voxel away from what the blur keeps are lost parts (a bump on a surface is
    within one voxel of it, so bumps still go). Those parts get a much lighter blur instead, joined to what was kept."""
    from scipy import ndimage
    kept = g > 0.5
    lost = m & ~kept
    if not lost.any():
        return g
    near = ndimage.binary_dilation(kept, structure=np.ones((3, 3, 3), bool))
    far = lost & ~near
    if not far.any():
        return g
    back = ndimage.binary_dilation(far, structure=np.ones((3, 3, 3), bool), iterations=2, mask=lost)  # up to the kept part
    light = ndimage.gaussian_filter(back.astype(np.float32), 0.5) * KEEP_GAIN
    return np.maximum(g, light)


KEEP_GAIN = 1.8  # a single voxel blurred by 0.5 peaks at 0.32: x 1.8 keeps it above the surface level (0.5)


def surface(mask, off_zyx, smooth, step, soften=0, full_shape=None):
    """Smoothed isosurface of a boolean (z,y,x) array, returned in (x,y,z) voxel coordinates.
    soften (1..3): the mask is blurred a little before the surface is made, which takes away the bumps and ridges a
    segmentation leaves on membranes far better than more smoothing steps (they keep the voxel layers). full_shape
    (the whole grid): where the structure is cut by the edge of the grid, the blur does not pull it back from there."""
    import pyvista as pv
    from skimage.measure import marching_cubes
    sig = SOFTEN_SIGMA.get(int(soften or 0), 0.0)
    if sig <= 0:
        sub = np.pad(mask.astype(np.float32), 1); pad = 1  # pad closes surfaces at the volume edge
    else:
        from scipy import ndimage
        pad = 1 + int(np.ceil(3 * sig)); off = [int(o) for o in off_zyx]
        at_edge = [((o <= 0), full_shape is not None and o + n >= full_shape[i]) for i, (o, n) in enumerate(zip(off, mask.shape))]
        # at the grid's edge the structure goes on (copied outwards) while it is blurred; elsewhere there is nothing
        sub = np.pad(mask.astype(np.float32), [(pad if lo else 0, pad if hi else 0) for lo, hi in at_edge], mode="edge")
        sub = np.pad(sub, [(0 if lo else pad, 0 if hi else pad) for lo, hi in at_edge])
        sub = _keep_thin(sub > 0.5, ndimage.gaussian_filter(sub, sig))
        sub[[0, -1], :, :] = 0; sub[:, [0, -1], :] = 0; sub[:, :, [0, -1]] = 0  # closed surfaces
    try:
        v, f, _, _ = marching_cubes(sub, 0.5, step_size=step)
    except (ValueError, RuntimeError):
        return None
    v = v + np.asarray(off_zyx, np.float32) - float(pad)
    if sig > 0:  # what went on past the grid's edge is laid flat onto it, as without the blur
        hi = np.asarray(full_shape if full_shape is not None else np.asarray(off_zyx) + mask.shape, np.float32) - 0.5
        v = np.clip(v, -0.5, hi)
    v = v[:, ::-1].astype(np.float32)
    pd = pv.PolyData(v, np.hstack([np.full((len(f), 1), 3), f[:, ::-1]]).ravel())
    smooth = int(smooth) + SOFTEN_STEPS.get(int(soften or 0), 0)
    if smooth > 0:
        pd = pd.smooth_taubin(n_iter=int(smooth), pass_band=0.1)  # removes voxel steps without shrinking thin features
    return pd.compute_normals(auto_orient_normals=False, consistent_normals=True, split_vertices=False)


class Dataset:
    """Optional tomogram plus structures from any number of segmentation sources.

    A structure is one label of one source (a label volume, or one thresholded score volume). Each has an internal
    integer id (k, used by the renderer) and a stable key "<source key>#<label>" that the settings use."""
    def __init__(self, smooth=15, step=1):
        self.tomo = None; self.vox = None; self._shape = None
        self._tomo_vox = self._seg_vox = None  # each source's own voxel size: the tomogram's wins
        self.tomo_name = self.seg_name = ""; self.tomo_version = None
        self.flip = ()  # axes of the volume shown flipped (3.5): a subset of "x", "y", "z"
        self.classes, self.bbox, self.meshes, self.scores, self._comp, self._lev = {}, {}, {}, {}, {}, {}
        self._labels = {}      # structure id -> (label array of its source, label value)
        self._mesh_cache = {}  # _cache_key() -> surface, kept while sources change
        self._mesh_lock = threading.Lock()  # surfaces and paths are also made in worker threads of the window: frozen()
        self.smooth, self.step = smooth, step
        self.params = {}       # structure key -> {"min_object_voxels": N, "smoothing": iterations, "soften": 0..3} (the user's choices)
        self.path_cache = {}   # camera paths along structures (paths.centre_line), computed once per change
        self.path_async = False  # the window computes missing paths in a worker thread instead of in build_plan
        self._fp = None        # (versions and flip, fingerprint()) of the voxels it was worked out for

    def frozen(self):
        """This dataset as it is now, for work that may last while the data changes (worker threads): keys, voxels, boxes and
        grid that belong together. Nothing is copied, because these dicts are never changed but replaced, all of them at once
        under _mesh_lock. Its caches of objects and paths are those of now, so a late result never reaches newer data."""
        with self._mesh_lock:
            return copy.copy(self)

    FINGERPRINT_VOXELS = 1_000_000

    def fingerprint(self):
        """What a movie shows of the data, to tell whether an interrupted movie can be continued (encode.settings_hash):
        the grid, the voxel size, the voxel count of each label structure, and about a million voxels of the tomogram and of
        each segmentation, taken as arr[::s, ::s, ::s] (nothing else is copied). The same voxels loaded again give the same
        answer. Thresholds and the flip are in the settings. Kept until the voxels or the flip change."""
        key = (self.tomo_version, tuple(sorted({str(c["data"]) for c in self.classes.values()})), self.flip)
        if self._fp is not None and self._fp[0] == key:
            return self._fp[1]
        counts = {c["key"]: c["voxels"] for c in self.classes.values() if c["kind"] == "label"}
        h = hashlib.sha256(json.dumps(dict(shape=self.shape, vox=self.vox, counts=counts), sort_keys=True, default=str).encode())
        arrays = {id(a): a for a in [lab for lab, _ in self._labels.values()] + [f["arr"] for f in self.scores.values()]}
        for a in ([self.tomo] if self.tomo is not None else []) + list(arrays.values()):
            s = max(1, round((a.size / self.FINGERPRINT_VOXELS) ** (1 / 3)))
            sample = np.ascontiguousarray(a[::s, ::s, ::s])
            h.update(f"{sample.dtype.str}{sample.shape}".encode()); h.update(sample.tobytes())
        self._fp = (key, h.hexdigest()[:16])
        return self._fp[1]

    @property
    def shape(self):
        """Grid (nz, ny, nx) of everything shown: the tomogram's, or else the segmentation's."""
        return tuple(self.tomo.shape) if self.tomo is not None else self._shape

    def set_tomogram(self, arr, vox, name):
        self._tomo_vox = vox if arr is not None else None; self.vox = self._tomo_vox or self._seg_vox
        self.tomo_version = new_version() if arr is not None else None
        if arr is None:
            self.tomo = None; self._lev = {}; self.tomo_name = ""
            return
        self.tomo = np.ascontiguousarray(arr, dtype=np.float32); self._lev = {}; self.tomo_name = name

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
        A label source may carry "_stats" = label_stats(arr); it is computed and stored there when missing. "version" names
        the voxels in arr (new_version()); together with the flip it is the "data" of each structure."""
        structs, bbox, scores, labels = {}, {}, {}, {}
        k = 0
        for src in sources:
            if src.get("version") is None:
                src["version"] = new_version()
            data = (src["version"], self.flip)
            if src["kind"] == "scores":
                k += 1; scores[k] = dict(src)  # its own: the source's arr may be replaced while a worker reads this one
                voxels, bbox[k] = self._score_box(src, src["thr"])
                structs[k] = dict(key=f"{src['key']}#1", name=src["name"], color=src.get("color") or PALETTE[(k - 1) % len(PALETTE)],
                                  kind="score", threshold=float(src["thr"]), background=False, hidden=False, named=True,
                                  voxels=voxels, label=1, source=src["key"], source_title=src.get("title", ""), data=data)
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
                         source_title=src.get("title", ""), data=data)
                c["background"] = bool((not keep_background) and (c["name"].lower() in ("background", "bg") or counts[v] > 0.5 * lab.size))
                c["hidden"] = c.get("visible") is False and not c["background"]  # switched off in Dragonfly
                c["named"] = c.get("named", True)
                structs[k] = c; bbox[k] = boxes[v - 1]; labels[k] = (lab, v)
        with self._mesh_lock:
            self.classes, self.bbox, self.scores, self._labels = structs, bbox, scores, labels
            self._shape = tuple(shape) if shape is not None else (tuple(sources[0]["arr"].shape) if sources else None)
            self.seg_name = ", ".join(s.get("title", "") for s in sources); self._seg_vox = vox; self.vox = self._tomo_vox or vox
            self._comp, self.path_cache, self.meshes = {}, {}, {}
            for k in structs:  # surfaces made earlier for the same structure are reused
                m = self._mesh_cache.get(self._cache_key(k))
                if m is not None:
                    self.meshes[k] = m

    def param(self, k, name):
        """A structure's surface setting: minimum object size (voxels), smoothing iterations, or soften (0..3)."""
        v = (self.params.get(self.classes[k]["key"]) or {}).get(name)
        if name == "smoothing":
            return int(self.smooth if v is None else v)
        return int(v or 0)

    def _cache_key(self, k, soften=None, params=None):
        """The data and the settings a surface is made with. soften: another level than the structure's own (a part's own,
        3.5); params: {"min_object_voxels", "smoothing", "soften"} wanted instead of the current ones."""
        c = self.classes[k]; p = params or {}
        mov = int(p["min_object_voxels"]) if "min_object_voxels" in p else self.param(k, "min_object_voxels")
        sm = int(p["smoothing"]) if "smoothing" in p else self.param(k, "smoothing")
        so = int(soften) if soften is not None else int(p["soften"]) if "soften" in p else self.param(k, "soften")
        return (c["key"], c["data"], c.get("threshold"), mov, sm, so)

    MESH_CACHE = 48  # surfaces kept (with many structures: all four levels of each), so going back to one is instant

    def make_surface(self, ref, soften=None, params=None):
        """The surface of a structure (its number now, or its key) with its settings (or this soften level, or these
        settings), from the cache or made now; None for a structure that is not loaded. Does not change which surface is
        shown, so it may run in a worker thread while the window draws or the data changes; a worker gives the key."""
        ds = self.frozen(); k = ds.id_of(ref)
        if k is None:
            return None
        ck = ds._cache_key(k, soften, params)
        with self._mesh_lock:
            if ck in self._mesh_cache:
                m = self._mesh_cache[ck] = self._mesh_cache.pop(ck)  # recently used
                return m
        sl = ds.bbox.get(k)
        if sl is None:
            return None
        *_, specks, smooth, soft = ck
        mask = ds.kept_mask(k, sl, specks)
        m = surface(mask, [s.start for s in sl], smooth, self.step, soft, ds._shape) if mask.any() else None
        with self._mesh_lock:
            self._mesh_cache[ck] = m
            most = max(self.MESH_CACHE, 4 * len(self.classes))
            if len(self._mesh_cache) > most:
                kept = {id(x) for x in list(self.meshes.values())}
                for old in [c for c, v in self._mesh_cache.items() if id(v) not in kept][:len(self._mesh_cache) - most]:
                    del self._mesh_cache[old]
        return m

    def surface_ready(self, k, soften=None, params=None):
        return self._cache_key(k, soften, params) in self._mesh_cache

    def needs_surface(self, k, params=None):
        """True when structure k is drawn and its surface (with these settings) has still to be made."""
        return self.bbox.get(k) is not None and (k in self.meshes or not self.classes[k]["background"]) and \
            not self.surface_ready(k, params=params)

    def set_params(self, k, **kw):
        """Change a structure's minimum object size and / or smoothing; rebuilds its surface. Returns True if it changed."""
        key = self.classes[k]["key"]; before = self._cache_key(k)
        cur = dict(self.params.get(key) or {}); cur.update({n: v for n, v in kw.items() if n in ("min_object_voxels", "smoothing", "soften")})
        with self._mesh_lock:
            self.params = {**self.params, key: cur}; self.path_cache = {}
        if self._cache_key(k) == before:
            return False
        had = self.meshes.pop(k, None) is not None
        cached = self._mesh_cache.get(self._cache_key(k))
        if cached is not None:
            self.meshes[k] = cached
        elif self._cache_key(k) in self._mesh_cache:
            pass  # nothing left with these settings
        elif had or not self.classes[k]["background"]:
            self._mesh(k, force=True)
        return True

    def kept_mask(self, k, sl, n=None):
        """The structure's voxels in sl, without connected objects smaller than its minimum object size (or n)."""
        m = self.mask(k, sl); n = self.param(k, "min_object_voxels") if n is None else int(n)
        if n > 1 and m.any():
            from scipy import ndimage
            lbl, cnt = ndimage.label(m, structure=np.ones((3, 3, 3), bool))
            sizes = np.bincount(lbl.ravel())
            small = sizes < n; small[0] = True
            m = ~small[lbl]
        return m

    @staticmethod
    def _score_box(src, thr):
        """(Number of voxels, box or None) of a score volume at this threshold."""
        from scipy import ndimage
        m = src["arr"] >= thr * src["scale"]; n = int(m.sum())
        return n, ndimage.find_objects(m.astype(np.uint8))[0] if n else None

    def set_threshold(self, k, thr):
        thr = min(0.99, max(0.01, float(thr)))
        voxels, box = self._score_box(self.scores[k], thr)
        with self._mesh_lock:
            self.scores = {**self.scores, k: dict(self.scores[k], thr=thr)}; self.bbox = {**self.bbox, k: box}
            self.classes = {**self.classes, k: dict(self.classes[k], threshold=thr, voxels=voxels)}
            self._comp = {j: v for j, v in self._comp.items() if j != k}; self.path_cache = {}
        self.meshes.pop(k, None); self._mesh(k)

    def _mesh(self, k, force=False):
        """Build the surface of structure k; background structures only when asked for (force)."""
        sl = self.bbox.get(k)
        if sl is None or (self.classes[k]["background"] and not force):
            return
        m = self.make_surface(k)
        if m is not None:
            self.meshes[k] = m

    def build_meshes(self, log=print):
        for k in self.classes:
            if k not in self.meshes:
                self._mesh(k)
        log(f"built {len(self.meshes)} surfaces")

    def forget(self, source_key):
        """Drop cached surfaces of a source that was removed."""
        with self._mesh_lock:
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
        ds = self.frozen()
        if k not in ds._comp:
            from scipy import ndimage
            lbl, _ = ndimage.label(ds.mask(k, ds.bbox[k]), structure=np.ones((3, 3, 3), bool))
            ds._comp[k] = (lbl, ndimage.find_objects(lbl), np.bincount(lbl.ravel()))
        return ds._comp[k]

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

    def comp_mesh(self, k, cid, soften=None):
        lbl, objs, _ = self.components(k); o = objs[cid - 1]; sl = self._comp_slices(k, cid)
        return surface(lbl[o] == cid, [s.start for s in sl], self.param(k, "smoothing"), 1,
                       self.param(k, "soften") if soften is None else int(soften), self._shape)

    def targets_box(self, tgt, pad=True):
        """The box containing the boxes of every item of a close-up target (one or several objects / structures)."""
        from .storyboard import target_items
        items = target_items(tgt)
        if not items:
            raise KeyError("no target")
        boxes = [self.target_box({"class": k, "component": c}, pad) for k, c in items]
        return np.min([b[0] for b in boxes], axis=0), np.max([b[1] for b in boxes], axis=0)

    def main_axis(self, tgt):
        """Unit vector along the longest direction of the voxels of a close-up's items (for the cross-section)."""
        from .storyboard import target_items
        pts = []
        for key, comp in target_items(tgt):
            k = self.id_of(key)
            if k is None:
                continue
            if comp is None:
                sl = self.bbox[k]; z, y, x = np.nonzero(self.mask(k, sl))
            else:
                lbl, _, _ = self.components(k); sl = self.bbox[k]; z, y, x = np.nonzero(lbl == int(comp))
            step = max(1, len(x) // 20000)
            pts.append(np.c_[x[::step] + sl[2].start, y[::step] + sl[1].start, z[::step] + sl[0].start])
        if not pts:
            return np.array([0.0, 0.0, 1.0])
        p = np.vstack(pts).astype(float); p -= p.mean(0)
        w, v = np.linalg.eigh(p.T @ p)
        a = v[:, -1]
        return a if a[2] >= 0 else -a

    def target_box(self, tgt, pad=True):
        """Padded (lo, hi) box in (x,y,z) voxels around a structure or one of its connected objects (pad=False: tight)."""
        k = self.id_of(tgt["class"])
        if k is None:
            raise KeyError(tgt["class"])
        sl = self._comp_slices(k, int(tgt["component"])) if tgt.get("component") is not None else self.bbox[k]
        lo = np.array([sl[2].start, sl[1].start, sl[0].start], float); hi = np.array([sl[2].stop, sl[1].stop, sl[0].stop], float) - 1
        if not pad:
            return lo, hi
        c = (lo + hi) / 2
        half = np.maximum((hi - lo) / 2 * (1.35 if tgt.get("component") is not None else 1.05), 8.0)
        half[:2] = np.maximum(half[:2], 0.03 * max(self.shape))  # keep some context around very small objects
        return c - half, c + half
