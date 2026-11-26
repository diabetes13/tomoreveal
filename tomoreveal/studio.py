"""Program state shared by the desktop window, the browser version and the command line."""
import atexit
import base64
import contextlib
import functools
import io
import os
import queue
import re
import shutil
import tempfile
import threading
import time
import traceback

import numpy as np

from .common import UserError, VIEWS, __version__, norm_path
from .dragonfly import OrsSession, guess_roi
from .matching import apply_match, match_volume
from .movie import render_movie
from .render import Renderer
from .storyboard import build_plan, build_timeline, class_spec, default_spec
from .volumes import (Dataset, clean_labels, guess_kind, load_volume, looks_like_scores, new_version, regrid, score_scale,
                      sidecar_classes)

# what _init_sources creates: the whole state of the loaded sources, which a failed change puts back
_SOURCE_STATE = ("ds", "sessions", "tomo_files", "seg_sources", "hidden", "tomo_source", "_tomo_auto", "_tomo_loaded",
                 "_seg_loaded", "_matches", "_stale", "match_note")


def _atomic(method):
    """A change of the loaded sources: if it fails, everything is left as it was and the error is raised unchanged.
    The renderer is made afterwards, outside the rollback, so a graphics problem never undoes a load."""
    @functools.wraps(method)
    def run(self, *a, make_renderer=True, **k):
        with self._rollback():
            info = method(self, *a, make_renderer=False, **k)
        if make_renderer:
            self.ensure_renderer()
        return info
    return run


def _keep(v):
    return v, (v.copy() if isinstance(v, (dict, list)) else None)


def _put_back(v, saved):
    # in place, so everything that refers to this dict or list sees the old contents; one that was not changed is left
    # alone, because a worker may be reading it (Dataset.frozen)
    if saved is not None and not _same(v, saved):
        v.clear(); (v.update if isinstance(v, dict) else v.extend)(saved)
    return v


def _same(v, saved):
    a, b = (list(x.items()) if isinstance(x, dict) else [(None, y) for y in x] for x in (v, saved))
    return len(a) == len(b) and all(n == m and x is y for (n, x), (m, y) in zip(a, b))


class Studio:
    def __init__(self, headless=False, outdir="tomoreveal_output", smooth=15, step=1, keep_background=False):
        self.headless, self.outdir = headless, os.path.abspath(outdir)
        self.smooth, self.step, self.keep_background = smooth, step, keep_background
        self.ren = None; self.default_thr = 0.5
        self.real_shadows_ok, self.real_shadows_note = False, "Not tested yet"  # set by the window after a self-test
        self._init_sources()
        self.q = queue.Queue(); self.job = dict(state="idle"); self._cancel = False; self.thumb = None
        self.work = tempfile.mkdtemp(prefix="tomoreveal_"); atexit.register(shutil.rmtree, self.work, True)

    # ---- loading ----
    # Tomogram source: None, "file:<path>" or "session:<path>:<image index>".
    # Segmentation sources (self.seg_sources, in order): "dragonfly:<path>:<object title>", "labels:<path>", "scores:<path>".
    _norm = staticmethod(norm_path)

    @property
    def has_tomogram(self):
        return self.ds.tomo is not None

    @property
    def has_segmentation(self):
        return self.ds.has_seg()

    @property
    def can_show(self):
        return self.has_tomogram or self.has_segmentation

    def ready(self):  # kept for older callers
        return self.can_show

    def _source(self, key):
        for s in self.seg_sources:
            if s["key"] == key:
                return s
        return None

    def _add_session(self, p, log):
        if p in self.sessions:
            log(f"{os.path.basename(p)} is already loaded")
            return
        ses = OrsSession(p); self.sessions[p] = ses
        objs = ses.objects(); usable = [o for o in objs if o["dims"][2] > 1]
        self.hidden[p] = dict(previews=len(objs) - len(usable), undo=len(ses.objects(include_undo=True)) - len(objs))
        guess = guess_roi(usable)
        for o in usable:
            on = guess == o["title"] or (guess == "__all_rois__" and o["kind"] == "ROI")
            nx, ny, nz = o["dims"]
            self.seg_sources.append(dict(key=f"dragonfly:{p}:{o['title']}", kind="dragonfly", label=o["title"], title=o["title"],
                                         path=p, group=os.path.basename(p), obj=o, roi=o["kind"] == "ROI", enabled=on,
                                         native=(nz, ny, nx), vox=o["voxel"], preview=ses.preview_classes(o)))

    def _add_volume(self, p, as_features, as_segmentation, as_tomogram, as_labels, nvol):
        a, vox = load_volume(p); stem = os.path.splitext(os.path.basename(p))[0]
        if p in as_tomogram:  # a role you give always wins over the guess
            kind = "tomogram"
        elif p in as_labels:
            kind = "labels"
        elif p in as_features:
            kind = "scores"
        else:
            have_tomo = bool(self.tomo_files) or any(s.images() for s in self.sessions.values())
            kind = guess_kind(a, stem, have_tomo, nvol)
            if p in as_segmentation and kind == "tomogram":  # "Add segmentation": anything but a tomogram
                kind = "scores" if looks_like_scores(a) else "labels"
        if kind == "labels":
            a = clean_labels(a, p)
        for other in ("file:", "labels:", "scores:"):  # the same file dropped again, perhaps as something else
            self._drop(other + p)
        if kind == "tomogram":
            self.tomo_files[p] = dict(arr=a, vox=vox, version=new_version())
            return
        src = dict(key=f"{kind}:{p}", kind=kind, path=p, group=os.path.basename(p), enabled=True, native=tuple(a.shape), vox=vox, raw=a)
        if kind == "labels":
            src.update(label=os.path.basename(p), title=os.path.basename(p), classes=sidecar_classes(p))
            src["preview"] = {int(v): dict(dict(name=f"class {v}"), **src["classes"].get(int(v), {}))
                              for v in np.unique(a[::max(1, a.shape[0] // 16), ::4, ::4]) if v > 0} if not src["classes"] else src["classes"]
        else:
            name = stem.split("__")[-1] if "__" in stem else stem  # easymode / Ais: <tomogram>__<feature>.mrc
            src.update(label=f"{os.path.basename(p)} (easymode)", title=name, name=name, scale=score_scale(a), thr=self.default_thr,
                       preview={1: dict(name=name)})
        self.seg_sources.append(src)

    def _drop(self, key):
        if key.startswith("file:"):
            self._matches = {k: v for k, v in self._matches.items() if k[0] != key[5:]}
            if self.tomo_files.pop(key[5:], None) is not None and self.tomo_source == key:
                self.tomo_source = None; self._tomo_auto = True
            return
        s = self._source(key)
        if s is not None:
            self.seg_sources.remove(s); self.ds.forget(key)

    def _register(self, paths, as_features=(), as_segmentation=(), as_tomogram=(), as_labels=(), log=print):
        paths = [self._norm(p) for p in paths if p]
        as_features, as_segmentation, as_tomogram, as_labels = ({self._norm(p) for p in x if p}
                                                                for x in (as_features, as_segmentation, as_tomogram, as_labels))
        for p in paths:
            if not os.path.isfile(p):
                raise UserError(f"file not found: {p}")
        nvol = sum(not p.lower().endswith(".orssession") for p in paths)
        for p in paths:
            if p.lower().endswith(".orssession"):
                self._add_session(p, log)
            else:
                self._add_volume(p, as_features, as_segmentation, as_tomogram, as_labels, nvol)

    @_atomic
    def add_files(self, paths, as_features=(), as_segmentation=(), as_tomogram=(), as_labels=(), log=print, make_renderer=True):
        """Add files to what is loaded. Sessions and segmentation files add segmentation sources; tomograms add tomogram choices."""
        self._register(paths, as_features, as_segmentation, as_tomogram, as_labels, log)
        return self._update(log, make_renderer)

    @_atomic
    def set_tomogram_source(self, key, log=print, make_renderer=True):
        key = key or None
        if key not in [c["key"] for c in self._tomogram_choices()]:
            raise UserError("that tomogram is no longer loaded")
        self.tomo_source = key; self._tomo_auto = False
        return self._update(log, make_renderer)

    @_atomic
    def set_segmentation_enabled(self, key, on, log=print, make_renderer=True):
        s = self._source(key)
        if s is None:
            raise UserError("that segmentation is no longer loaded")
        s["enabled"] = bool(on)
        return self._update(log, make_renderer)

    @_atomic
    def remove_source(self, key, log=print, make_renderer=True):
        """Remove a tomogram file ("file:..."), a whole session ("session:<path>") or one segmentation source."""
        if key.startswith("session:") and key[8:] in self.sessions:
            p = key[8:]
            self.sessions.pop(p, None); self.hidden.pop(p, None)
            for s in [s for s in self.seg_sources if s["kind"] == "dragonfly" and s["path"] == p]:
                self._drop(s["key"])
            if (self.tomo_source or "").startswith(f"session:{p}:"):
                self.tomo_source = None; self._tomo_auto = True
            self._matches = {k: v for k, v in self._matches.items() if k[1] != p}
        else:
            self._drop(key)
        return self._update(log, make_renderer)

    @_atomic
    def load(self, paths=(), roi=None, image=None, log=print, as_features=(), as_tomogram=(), as_labels=(), make_renderer=True):
        """Older interface (command line, browser version): add files, then optionally choose a segmentation and an image.
        roi: a multi-ROI / ROI title (or a unique part of it) or "__all_rois__", in the session loaded last.
        image: "file", "s<N>" (image N of that session), "none", or a tomogram source key."""
        self._register(paths, as_features, as_tomogram=as_tomogram, as_labels=as_labels, log=log)
        if self.sessions and roi:
            p = list(self.sessions)[-1]; ses = self.sessions[p]
            mine = [s for s in self.seg_sources if s["kind"] == "dragonfly" and s["path"] == p]
            if roi == "__all_rois__":
                pick = {s["key"] for s in mine if s["roi"]}
            else:
                title = roi if any(s["title"] == roi for s in mine) else ses.find(roi)["title"]
                pick = {f"dragonfly:{p}:{title}"}
                if not any(s["key"] in pick for s in mine):
                    raise UserError(f"'{title}' is a single-slice preview or an undo copy and cannot be shown")
            for s in mine:
                s["enabled"] = s["key"] in pick
        if image:
            if image == "file" and self.tomo_files:
                image = "file:" + list(self.tomo_files)[-1]
            elif re.fullmatch(r"s\d+", image) and self.sessions:
                image = f"session:{list(self.sessions)[-1]}:{image[1:]}"
            elif image == "none":
                image = None
            if image not in [c["key"] for c in self._tomogram_choices()]:
                raise UserError(f"no such image: {image}")
            self.tomo_source = image; self._tomo_auto = False
        return self._update(log, make_renderer)

    # -- working out what is shown --
    def _tomogram_choices(self):
        out = [dict(key=None, label="None", grid=None)]
        for p, f in self.tomo_files.items():
            nz, ny, nx = f["arr"].shape
            out.append(dict(key="file:" + p, label="your file: " + os.path.basename(p), grid=[nx, ny, nz], group=os.path.basename(p),
                            name=os.path.basename(p), where="your file", path=p))
        for p, ses in self.sessions.items():
            for i, im in enumerate(ses.images()):
                out.append(dict(key=f"session:{p}:{i}", label=f"from Dragonfly session {os.path.splitext(os.path.basename(p))[0]}: {im['title']}",
                                name=im["title"], where=f"in {os.path.splitext(os.path.basename(p))[0]}", path=p,
                                grid=list(im["dims"]), group=os.path.basename(p)))
        return out

    def _default_tomogram(self):
        if self.tomo_files:
            return "file:" + list(self.tomo_files)[-1]
        for p, ses in self.sessions.items():
            if ses.images():
                return f"session:{p}:0"
        return None

    def _match(self, fpath, log):
        """Line up a tomogram file with the image of a loaded session; first session that fits wins."""
        res = None
        for p, ses in self.sessions.items():
            imgs = ses.images()
            if not imgs:
                continue
            mkey = (fpath, p)
            if mkey not in self._matches:
                f = self.tomo_files[fpath]
                log(f"comparing your tomogram file with the image stored in {os.path.basename(p)} ...")
                try:
                    v, notes, r, tf = match_volume(f["arr"], f["vox"], ses.read_image(imgs[0]), imgs[0]["voxel"], log)
                    self._matches[mkey] = dict(key=mkey, v=v, notes=notes, r=r, tf=tf, error=None, vox=imgs[0]["voxel"])
                except UserError as exc:
                    self._matches[mkey] = dict(key=mkey, v=None, notes=[], r=0.0, tf=None, error=str(exc))
            m = self._matches[mkey]
            if m["error"] is None:
                return m
            res = res or m
        return res

    def _load_tomogram(self, log):
        """Put the chosen tomogram into the dataset; returns the transform used for files made from the original file."""
        if self._tomo_auto:
            self.tomo_source = self._default_tomogram()
        src, tf, m, self.match_note = self.tomo_source, None, None, ""
        if src and src.startswith("file:"):
            fpath = src[5:]; m = self._match(fpath, log) if self.sessions else None
            if m is not None and m["error"] and self._tomo_auto and self._default_session_image():
                self.match_note = f"Your tomogram file is not used: {m['error']}. Showing Dragonfly's own copy instead."
                src = self.tomo_source = self._default_session_image()
            elif m is not None and m["error"]:
                self.match_note = f"Your tomogram file does not line up with the Dragonfly session: {m['error']}"
            elif m is not None:
                tf = m["tf"] if not m["tf"]["identity"] else None
                self.match_note = (f"Your tomogram file was lined up with Dragonfly's copy: {'; '.join(m['notes'])} (similarity {m['r']:.2f})."
                                   if m["notes"] else "Your tomogram file and Dragonfly's copy are on the same grid.")
        # what is shown: the source, the version of the file, how it was lined up (None: as it is, or a session's image) and
        # the flip. None: no tomogram. Anything else than what was put in last time is put in anew.
        if src is None:
            want = None
        elif src.startswith("file:"):
            want = (src, self.tomo_files[src[5:]]["version"], m["key"] if m is not None and m["error"] is None else None, self.flip)
        else:
            want = (src, None, None, self.flip)
        if want != self._tomo_loaded:
            if src is None:
                self.ds.set_tomogram(None, None, "")
            elif src.startswith("file:"):
                fpath = src[5:]; f = self.tomo_files[fpath]
                if want[2] is not None:
                    self.ds.set_tomogram(self._flipped(m["v"]), m["vox"] if not m["tf"]["identity"] else (f["vox"] or m["vox"]),
                                         os.path.basename(fpath))
                else:
                    self.ds.set_tomogram(self._flipped(f["arr"]), f["vox"], os.path.basename(fpath))
            else:
                p, i = src[8:].rsplit(":", 1); im = self.sessions[p].images()[int(i)]
                log(f"reading image '{im['title']}' from the session ...")
                self.ds.set_tomogram(self._flipped(self.sessions[p].read_image(im)), im["voxel"], im["title"])
            self._tomo_loaded = want
        return tf

    def _default_session_image(self):
        for p, ses in self.sessions.items():
            if ses.images():
                return f"session:{p}:0"
        return None

    @staticmethod
    def _grid_text(shape):
        nz, ny, nx = shape
        return f"{nx} × {ny} × {nz}"

    def _place(self, s, ref, ref_name, tf, log):
        """Bring one source onto the reference grid (nz, ny, nx); sets s["arr"], s["usable"] and s["reason"]."""
        s["usable"], s["reason"] = True, ""
        if ref is None:
            ref = s["native"]
        if s["kind"] == "dragonfly":
            if s["native"] != ref:
                s["usable"] = False
                s["reason"] = f"{self._grid_text(s['native'])} voxels, but {ref_name} is {self._grid_text(ref)}"
                return
            if s.get("arr") is None and s["enabled"]:
                log(f"reading segmentation '{s['title']}' ...")
                lab, classes = self.sessions[s["path"]].read_selection([s["obj"]])
                s.update(arr=lab, classes=classes, _stats=None, version=new_version())
            return
        raw = s["raw"]; key = (tuple(ref), (tf["s"], tf["flip"], tf["off"]) if tf else None)
        if tf and tf["flip"] and s["kind"] == "labels" and raw.shape == tuple(ref) == tuple(tf["src_shape"]):
            self.match_note = (f"{self.match_note} {s['group']}: used as it is, on Dragonfly's grid. If it was made from your "
                               "original tomogram file, it is mirrored.").strip()
        if s.get("placed") == key:
            return
        # Made from your original tomogram file: the same flip / binning / crop as for that file. Score files always are;
        # a label file on Dragonfly's own grid may be a Dragonfly export instead, so it is used as it is, with a note.
        from_file = bool(tf) and raw.shape == tuple(tf["src_shape"]) and (s["kind"] == "scores" or raw.shape != tuple(ref))
        if from_file:
            log(f"{s['group']}: lined up with the Dragonfly grid in the same way as your tomogram file")
            arr = np.ascontiguousarray(apply_match(raw, tf, ref, labels=s["kind"] == "labels"))
        elif raw.shape == tuple(ref):
            arr = raw
        elif s["kind"] == "scores":
            try:
                arr = regrid(raw, ref, s["group"], log)
            except UserError:
                s["usable"] = False
                s["reason"] = f"{self._grid_text(raw.shape)} voxels, but {ref_name} is {self._grid_text(ref)}, and the proportions differ"
                return
        else:
            s["usable"] = False
            s["reason"] = f"{self._grid_text(raw.shape)} voxels, but {ref_name} is {self._grid_text(ref)}"
            return
        s.update(arr=arr, placed=key, _stats=None, version=new_version())

    def _update(self, log=print, make_renderer=True):
        tf = self._load_tomogram(log)
        ref, ref_name = (self.ds.tomo.shape, "the tomogram") if self.ds.tomo is not None else (None, "")
        if ref is None:  # no tomogram: the first switched-on segmentation sets the grid
            first = next((s for s in self.seg_sources if s["enabled"]), None)
            if first is not None:
                ref, ref_name = first["native"], f"'{first['label']}'"
        for s in self.seg_sources:
            self._place(s, ref, ref_name, tf, log)
        use = [s for s in self.seg_sources if s["enabled"] and s["usable"]]
        sig = (tuple((s["key"], s["version"]) for s in use), tuple(ref) if ref is not None else None, self.keep_background, self.flip)
        if sig != self._seg_loaded:
            parts = []
            for s in use:
                if self.flip:  # shown flipped: a copy of the source's settings with the flipped volume (statistics of its own)
                    parts.append(dict(s, arr=self._flipped(s["arr"]), _stats=None, **({} if s["kind"] in ("labels", "scores") else {"kind": "labels"})))
                else:
                    parts.append(s if s["kind"] in ("labels", "scores") else dict(s, kind="labels"))
            self.ds.set_segmentation(parts, vox=next((s["vox"] for s in use if s.get("vox")), None),
                                     keep_background=self.keep_background, shape=ref)
            for s, part in zip(use, parts):  # keep label statistics for the next rebuild
                if part is not s and not self.flip:
                    s["_stats"] = part.get("_stats")
            self._seg_loaded = sig; self._stale = True
        if self.has_segmentation and any(k not in self.ds.meshes and self.ds.bbox.get(k) is not None and not c["background"]
                                         for k, c in self.ds.classes.items()):
            log("building surfaces ...")
            self.ds.build_meshes(log)
        if make_renderer:
            self.ensure_renderer()
        return self.info()

    def build_structure(self, key, log=print, make_renderer=True):
        """Build the surface of a structure that has none yet (a background structure the user switched on)."""
        k = self.ds.id_of(key)
        if k is None:
            raise UserError("that structure is no longer loaded")
        if k not in self.ds.meshes:
            log(f"building the surface of {self.ds.classes[k]['name']} ...")
            self.ds._mesh(k, force=True); self._stale = True
            if k not in self.ds.meshes:
                raise UserError(f"{self.ds.classes[k]['name']} has no surface to draw")
        if make_renderer:
            self.ensure_renderer()
        return self.info()

    def ensure_renderer(self):
        """Create or rebuild the 3D renderer; must run on the thread that draws."""
        if not self.can_show:
            self._drop_renderer()
            return None
        nz, ny, nx = self.ds.shape
        if self.ren is None or self._stale or self.ren.img.dimensions[:2] != (nx, ny) or self.ren.shape != self.ds.shape:
            self._drop_renderer(); self.ren = Renderer(self.ds, self.headless); self._stale = False
            self.ren.real_shadows_ok = self.real_shadows_ok
        self.ren._slice_key = None
        return self.ren

    def _drop_renderer(self):
        if self.ren is not None:
            self.ren.close(); self.ren = None

    @contextlib.contextmanager
    def _rollback(self):
        """Snapshot by reference (no arrays are copied) of the sources, each source dict and the dataset."""
        state = {n: _keep(getattr(self, n)) for n in _SOURCE_STATE}
        sources = [_keep(s) for s in self.seg_sources]
        ds = {n: _keep(v) for n, v in self.ds.__dict__.items()}
        try:
            yield
        except BaseException:
            for n, kept in state.items():
                setattr(self, n, _put_back(*kept))
            for kept in sources:
                _put_back(*kept)
            with self.ds._mesh_lock:  # workers may be asking: every value is overwritten, none is ever missing
                self.ds.__dict__.update({n: _put_back(*kept) for n, kept in ds.items()})
                self.ds._comp, self.ds.path_cache = {}, {}  # a worker may have put something of the failed state into them
            raise

    def _init_sources(self):
        self.ds = Dataset(self.smooth, self.step)
        self.sessions = {}; self.tomo_files = {}; self.seg_sources = []; self.hidden = {}
        self.tomo_source = None; self._tomo_auto = True; self._tomo_loaded = None; self._seg_loaded = None
        self._matches = {}; self._stale = False; self.match_note = ""

    FLIP_AXES = {"z": 0, "y": 1, "x": 2}  # numpy axis of each (arrays are z, y, x)

    @property
    def flip(self):
        """The axes the volume is shown flipped in (kept in the dataset, so a failed change puts them back)."""
        return self.ds.flip

    @flip.setter
    def flip(self, axes):
        self.ds.flip = tuple(axes)

    def _flipped(self, arr):
        """The volume as shown: flipped in the chosen axes (a view, nothing is copied and the files are not changed)."""
        ax = tuple(sorted(self.FLIP_AXES[a] for a in self.flip))
        return np.flip(arr, ax) if ax and arr is not None else arr

    @_atomic
    def set_flip(self, axes, log=print, make_renderer=True):
        """Show the tomogram and every segmentation flipped in these axes ("x", "y", "z"); surfaces are made again."""
        axes = tuple(a for a in "xyz" if a in (axes or ()))
        if axes == self.flip:
            return self.info()
        self.flip = axes
        self._drop_renderer()
        log("flipping the volume and building 3D surfaces ...")
        return self._update(log, make_renderer)

    def reset(self):
        self._drop_renderer(); self._init_sources()
        return self.info()

    def set_threshold(self, k, value):
        k = self.ds.id_of(k)
        if k not in self.ds.scores:
            raise UserError("only structures loaded from score volumes have a threshold")
        self.ds.set_threshold(k, value)
        src = self._source(self.ds.scores[k].get("key"))
        if src is not None:  # the dataset has a copy: the source keeps the threshold for the next rebuild and the project
            src["thr"] = self.ds.scores[k]["thr"]
        if self.ren is not None:
            self.ren.update_class(k)
        return dict(voxels=self.ds.classes[k]["voxels"], mesh=k in self.ds.meshes, threshold=self.ds.classes[k]["threshold"])

    def set_structure_params(self, key, **kw):
        """Minimum object size / smoothing of one structure; rebuilds its surface and its 3D actor when they change."""
        k = self.ds.id_of(key)
        if k is None:
            raise UserError("that structure is no longer loaded")
        if self.ds.set_params(k, **kw) and self.ren is not None:
            self.ren.update_class(k)
        c = self.ds.classes[k]
        return dict(voxels=c["voxels"], mesh=k in self.ds.meshes)

    def info(self):
        d = dict(can_show=self.can_show, has_tomogram=self.has_tomogram, has_segmentation=self.has_segmentation,
                 outdir=self.outdir, views=VIEWS, version=__version__, match_note=self.match_note,
                 tomogram=self.tomo_source, tomogram_choices=self._tomogram_choices(), flip=list(self.flip),
                 sessions=[dict(key=f"session:{p}", label=os.path.basename(p), **self.hidden.get(p, {})) for p in self.sessions],
                 segmentation_sources=[dict(key=s["key"], kind=s["kind"], label=s["label"], group=s["group"], path=s["path"],
                                            session=(f"session:{s['path']}" if s["kind"] == "dragonfly" else None),
                                            enabled=s["enabled"], usable=s.get("usable", True), reason=s.get("reason", ""),
                                            grid=list(s["native"][::-1]), count=len(s.get("preview") or {}),
                                            names=[c["name"] for _, c in sorted((s.get("preview") or {}).items())])
                                       for s in self.seg_sources])
        if self.ds.shape is not None and self.can_show:
            nz, ny, nx = self.ds.shape
            d.update(dims=[nx, ny, nz], voxel=self.ds.vox,
                     tomo_name=self.ds.tomo_name or next((s["title"] for s in self.seg_sources if s["enabled"] and s.get("usable")), "segmentation"))
        if self.can_show:
            d.update(spec=default_spec(self.ds),
                     structures=[dict(id=k, key=c["key"], name=c["name"], color=c["color"], voxels=c["voxels"], background=c["background"],
                                      hidden=c.get("hidden", False), named=c.get("named", True), label=c.get("label"),
                                      mesh=k in self.ds.meshes, kind=c["kind"], threshold=c.get("threshold"), source=c["source"],
                                      source_title=c["source_title"]) for k, c in self.ds.classes.items()])
        elif self.seg_sources:
            d["message"] = "None of the loaded segmentations is switched on, and there is no tomogram. Switch one on, or add a tomogram."
        else:
            d["message"] = "Drop a tomogram, a segmentation, or both to begin."
        return d

    # ---- preview / pick ----
    def plan_frame(self, spec, req):
        """(Plan, index of the frame asked for)."""
        plan = build_plan(spec, self.ds)
        frames, marks, i = self._pick_frame(plan.frames, plan.marks, req)
        return plan, i

    def _frame(self, spec, req):
        plan = build_plan(spec, self.ds)
        return self._pick_frame(plan.frames, plan.marks, req)

    @staticmethod
    def _pick_frame(frames, marks, req):
        n = len(frames)
        if req.get("at"):
            at = req["at"]  # {"part": id, "u": ...}, or {"shot": position, "u": ...} from the browser version
            idx = [i for i, s in enumerate(frames) if s["phase"] == "main" and
                   (s.get("part") == at["part"] if at.get("part") else s["shot"] == int(at.get("shot", -9)))]
            i = idx[min(len(idx) - 1, int(float(req["at"].get("u", 0.5)) * len(idx)))] if idx else n - 1
        elif req.get("frame") is not None:
            i = int(req["frame"])
        else:
            i = int(round(float(req.get("t", 0.5)) * (n - 1)))
        return frames, marks, max(0, min(n - 1, i))

    def _psize(self, spec):
        w, h = spec.get("output", {}).get("size", [1920, 1080])
        return (960, max(2, round(960 * h / w))) if w >= h else (max(2, round(720 * w / h)), 720)

    def preview(self, spec, req):
        from PIL import Image
        if not self.can_show:
            raise UserError("load a tomogram or a segmentation first")
        frames, marks, i = self._frame(spec, req)
        img = self.ren.draw(frames[i], spec, self._psize(spec), aa=False)
        buf = io.BytesIO(); Image.fromarray(img[..., :3]).save(buf, "JPEG", quality=88)
        fps = int(spec.get("output", {}).get("fps", 30))
        return dict(img="data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode(), frame=i, total=len(frames),
                    seconds=round(len(frames) / fps, 1), marks=marks)

    def pick(self, spec, req):
        frames, _, i = self._frame(spec, req)
        self.ren.draw(frames[i], spec, self._psize(spec), aa=False, annotate=False)
        hit = self.ren.pick(float(req["x"]), float(req["y"]))
        if hit is None:
            return dict(found=False)
        k, pos = hit
        cid = self.ds.pick_component(k, pos)
        key = self.ds.key_of(k)
        name = class_spec(spec, self.ds, k).get("name", self.ds.classes[k]["name"])
        if cid is None:
            return dict(found=True, **{"class": key}, id=k, component=None, name=name, voxels=self.ds.classes[k]["voxels"])
        return dict(found=True, **{"class": key}, id=k, component=cid, name=name, voxels=int(self.ds.components(k)[2][cid]))

    # ---- rendering job (runs on the main thread, like every other VTK call) ----
    def start_render(self, spec, name):
        if not self.can_show:
            raise UserError("load a tomogram or a segmentation first")
        if self.job.get("state") == "rendering":
            raise UserError("a movie is already being rendered")
        name = re.sub(r"[^\w\-. ]+", "_", os.path.basename(name or "tomogram_reveal")).strip() or "tomogram_reveal"
        if not name.lower().endswith(".mp4"):
            name += ".mp4"
        self.job = dict(state="rendering", done=0, total=len(build_timeline(spec, self.ds)[0]), t0=time.time(), file=name,
                        path=os.path.join(self.outdir, name), message=""); self._cancel = False
        self.q.put((self._render, (spec,), None, None))
        return self.job

    def _render(self, spec):
        from PIL import Image
        job = self.job
        def prog(i, n, img):
            job["done"] = i
            if i % 8 == 1 or i == n:
                im = Image.fromarray(img[..., :3]); im.thumbnail((640, 640)); b = io.BytesIO(); im.save(b, "JPEG", quality=80)
                self.thumb = b.getvalue()
        try:
            res = render_movie(self.ren, spec, job["path"], progress=prog, cancelled=lambda: self._cancel)
            if res is None:  # the unfinished files are already gone, and the old movie is kept
                job.update(state="cancelled", message="cancelled")
            else:
                job.update(state="done", seconds=round(res[1], 1), mb=round(os.path.getsize(job["path"]) / 1e6, 1),
                           elapsed=round(time.time() - job["t0"]))
        except Exception as exc:
            traceback.print_exc(); job.update(state="error", message=str(exc))

    def progress(self):
        j = dict(self.job)
        if j.get("state") == "rendering" and j.get("done"):
            el = time.time() - j["t0"]; j["eta"] = round(el / j["done"] * (j["total"] - j["done"]))
        j.pop("t0", None)
        return j

    # ---- main-thread job queue ----
    def call(self, fn, *a):
        ev, box = threading.Event(), {}
        self.q.put((fn, a, ev, box)); ev.wait()
        if "err" in box:
            raise box["err"]
        return box.get("res")

    def loop(self):
        while True:
            try:
                fn, a, ev, box = self.q.get(timeout=0.25)
            except queue.Empty:
                continue
            try:
                res = fn(*a)
                if box is not None:
                    box["res"] = res
            except Exception as exc:
                if not isinstance(exc, UserError):
                    traceback.print_exc()
                if box is not None:
                    box["err"] = exc
            finally:
                if ev is not None:
                    ev.set()
