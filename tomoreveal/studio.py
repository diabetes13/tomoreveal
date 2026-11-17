"""Program state shared by the desktop window, the browser version and the command line."""
import atexit
import base64
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

from .common import UserError, VIEWS, __version__
from .dragonfly import OrsSession, guess_roi
from .matching import apply_match, match_volume
from .render import Renderer, render_movie
from .storyboard import build_timeline, default_spec
from .volumes import Dataset, load_volume, looks_like_labels, looks_like_scores, regrid, score_scale, sidecar_classes


class Studio:
    def __init__(self, headless=False, outdir="tomoreveal_output", smooth=15, step=1, keep_background=False):
        self.headless, self.outdir = headless, os.path.abspath(outdir)
        self.smooth, self.step, self.keep_background = smooth, step, keep_background
        self.ds = Dataset(smooth, step); self.ren = None
        self.files = dict(tomogram=None, segmentation=None); self.ses = None
        self.sel = dict(image=None, roi=None); self.opts = dict(images=[], rois=[])
        self._tomo_src = self._seg_src = self._base_src = None; self._lab_file = None; self._base = None
        self.feats = {}; self.default_thr = 0.5  # score volumes by structure name, in the order they were added
        self._stale = False  # the renderer no longer matches the loaded structures
        self._file_vol = None  # (array, voxel size, path) of the tomogram file as it is on disk
        self._match = None     # result of matching that file to the image stored in the Dragonfly session
        self.match_note = ""
        self.q = queue.Queue(); self.job = dict(state="idle"); self._cancel = False; self.thumb = None
        self.work = tempfile.mkdtemp(prefix="tomoreveal_"); atexit.register(shutil.rmtree, self.work, True)

    # ---- loading ----
    def load(self, paths=(), roi=None, image=None, log=print, as_features=(), make_renderer=True):
        norm = lambda p: os.path.abspath(os.path.expanduser(str(p).strip().strip('"')))
        paths = [norm(p) for p in paths if p]; as_features = {norm(p) for p in as_features if p}
        for p in paths:
            if not os.path.isfile(p):
                raise UserError(f"file not found: {p}")
        nvol = sum(not p.lower().endswith(".orssession") for p in paths)
        for p in paths:
            if p.lower().endswith(".orssession"):
                self.files["segmentation"] = p; self.ses = OrsSession(p); self._lab_file = None
                self.sel["roi"] = None
                if self.files["tomogram"] is None:
                    self.sel["image"] = None
                continue
            a, vox = load_volume(p); stem = os.path.splitext(os.path.basename(p))[0]
            scores, labels = looks_like_scores(a), looks_like_labels(a)
            if p in as_features or (scores and "__" in stem) or (scores and not labels and (self.ds.tomo is not None or nvol > 1)):
                name = stem.split("__")[-1] if "__" in stem else stem  # easymode / Ais: <tomogram>__<feature>.mrc
                self.feats[name] = dict(name=name, raw=a, arr=a, key=None, scale=score_scale(a), thr=self.default_thr,
                                        src=os.path.basename(p), vox=vox)
                self._seg_src = None
            elif labels:
                self.files["segmentation"] = p; self.ses = None; self._lab_file = (a, vox, sidecar_classes(p)); self.sel["roi"] = None
            else:
                self.files["tomogram"] = p; self._file_vol = (a, vox, p); self._tomo_src = None
                self.sel["image"] = "file"
        # image options
        imgs = self.ses.images() if self.ses else []
        self.opts["images"] = ([dict(id="file", label="your file: " + os.path.basename(self.files["tomogram"]))] if self.files["tomogram"] else []) + \
            [dict(id=f"s{i}", label=f"Dragonfly's copy: {im['title']}") for i, im in enumerate(imgs)]
        if not self.opts["images"] and self.ds.tomo is not None:  # image kept from a session that was since replaced
            self.opts["images"] = [dict(id="kept", label=self.ds.tomo_name)]
        if image:
            self.sel["image"] = image
        if self.sel["image"] not in [o["id"] for o in self.opts["images"]]:
            self.sel["image"] = self.opts["images"][0]["id"] if self.opts["images"] else None
        # the tomogram file as imported into Dragonfly may differ from Dragonfly's copy (flipped, binned or cropped at import):
        # work out how the two correspond, once per file + session pair
        fpath = self.files["tomogram"]
        mkey = (fpath, self.ses.path) if (fpath and self.ses and imgs) else None
        if mkey is None:
            self._match = None
        elif self._match is None or self._match["key"] != mkey:
            if self._file_vol is None or self._file_vol[2] != fpath:
                self._file_vol = load_volume(fpath) + (fpath,)
            log("comparing your tomogram file with the image stored in the Dragonfly session ...")
            try:
                v, notes, r, tf = match_volume(self._file_vol[0], self._file_vol[1], self.ses.read_image(imgs[0]), imgs[0]["voxel"], log)
                self._match = dict(key=mkey, v=v, notes=notes, r=r, tf=tf, error=None)
            except UserError as exc:
                self._match = dict(key=mkey, v=None, notes=[], r=0.0, tf=None, error=str(exc))
        if self.sel["image"] == "file" and self._match is not None and self._match["error"]:
            self.sel["image"] = "s0"  # cannot be laid over this session: show Dragonfly's copy and say why
        self.match_note = ""
        if self._match is not None:
            m = self._match
            if m["error"]:
                self.match_note = f"Your tomogram file is not used: {m['error']}. Showing Dragonfly's own copy instead."
            elif m["notes"]:
                self.match_note = (f"Your tomogram file was lined up with Dragonfly's copy: {'; '.join(m['notes'])} "
                                   f"(similarity {m['r']:.2f}). Choose which one to show under Image.")
            else:
                self.match_note = "Your tomogram file and Dragonfly's copy are on the same grid. Choose which one to show under Image."
        if self.sel["image"] and self.sel["image"].startswith("s") and self.ses:
            src = ("session", self.ses.path, self.sel["image"])
            if src != self._tomo_src:
                im = imgs[int(self.sel["image"][1:])]
                log(f"reading image '{im['title']}' from the session ...")
                self.ds.set_tomogram(self.ses.read_image(im), im["voxel"], im["title"]); self._tomo_src = src
        elif self.sel["image"] == "file":
            src = ("file", fpath, mkey)
            if src != self._tomo_src:
                if self._file_vol is None or self._file_vol[2] != fpath:
                    self._file_vol = load_volume(fpath) + (fpath,)
                a, vox, _ = self._file_vol
                if self._match is not None:
                    changed = not self._match["tf"]["identity"]
                    self.ds.set_tomogram(self._match["v"], imgs[0]["voxel"] if changed else (vox or imgs[0]["voxel"]), os.path.basename(fpath))
                else:
                    self.ds.set_tomogram(a, vox, os.path.basename(fpath))
                self._tomo_src = src
        # base segmentation: a Dragonfly multi-ROI / ROIs, or a label volume, or nothing
        base_src = None; self.opts["rois"] = []
        if self.ses:
            objs = self.ses.objects()
            nroi = sum(o["kind"] == "ROI" for o in objs)
            self.opts["rois"] = [dict(id=o["title"], label=f"{o['title']}  ({o['kind']})") for o in objs] + \
                ([dict(id="__all_rois__", label=f"all {nroi} plain ROIs combined, one class each")] if nroi > 1 else [])
            if roi:
                self.sel["roi"] = roi if roi in [o["id"] for o in self.opts["rois"]] else self.ses.find(roi)["title"]
            if self.sel["roi"] is None:
                self.sel["roi"] = guess_roi(objs)
            if self.sel["roi"]:
                base_src = ("session", self.ses.path, self.sel["roi"])
        elif self._lab_file is not None:
            base_src = ("file", self.files["segmentation"])
        if base_src != self._base_src:
            if base_src and base_src[0] == "session":
                pick = [o for o in objs if o["kind"] == "ROI"] if self.sel["roi"] == "__all_rois__" else [self.ses.find(self.sel["roi"])]
                log(f"reading segmentation '{self.sel['roi']}' ...")
                lab, classes = self.ses.read_selection(pick)
                self._base = (lab, classes, pick[0]["voxel"], self.sel["roi"])
            elif base_src:
                self._base = (self._lab_file[0], self._lab_file[2], self._lab_file[1], os.path.basename(self.files["segmentation"]))
            else:
                self._base = None
            self._base_src = base_src
        shape = self.ds.tomo.shape if self.ds.tomo is not None else (self._base[0].shape if self._base else None)
        tf = self._match["tf"] if (self._match and self._match["tf"] and not self._match["tf"]["identity"]) else None
        tfkey = (tf["s"], tf["flip"], tf["off"]) if tf else None
        src = (base_src, tuple((n, f["src"]) for n, f in self.feats.items()), shape, tfkey)
        if src != self._seg_src and (self._base or self.feats):
            lab, classes, vox, name = self._base or (None, {}, None, "added structures")
            for n in list(self.feats):  # bring score volumes onto the tomogram grid, or refuse them
                f = self.feats[n]; key = (tuple(shape) if shape is not None else None, tfkey)
                if f["key"] == key:
                    continue
                raw = f["raw"]
                try:
                    if shape is None:
                        f["arr"] = raw
                    elif tf and raw.shape == tuple(tf["src_shape"]):
                        # made from the original tomogram file: apply the same flip / binning / crop as for that file
                        log(f"{f['src']}: lined up with the Dragonfly grid in the same way as your tomogram file")
                        f["arr"] = np.ascontiguousarray(apply_match(raw, tf, shape))
                    elif raw.shape != tuple(shape):
                        f["arr"] = regrid(raw, shape, f["src"], log)
                    else:
                        f["arr"] = raw
                    f["key"] = key
                except UserError:
                    del self.feats[n]
                    raise
            self.ds.set_segmentation(lab, classes, vox, name, list(self.feats.values()), self.keep_background)
            self._seg_src = src; self._stale = True
        if self.ready():
            if self.ds.lab is not None and tuple(self.ds.lab.shape) != tuple(self.ds.tomo.shape):
                t, l = self.ds.tomo.shape[::-1], self.ds.lab.shape[::-1]
                raise UserError(f"grid mismatch: the tomogram is {t[0]} x {t[1]} x {t[2]} voxels but the segmentation is "
                                f"{l[0]} x {l[1]} x {l[2]}. They must be the same volume (same binning and crop).")
            if any(k not in self.ds.meshes and self.ds.bbox.get(k) is not None and not c["background"] for k, c in self.ds.classes.items()):
                log("building surfaces ...")
                self.ds.build_meshes(log)
            if make_renderer:
                self.ensure_renderer()
        return self.info()

    def ensure_renderer(self):
        """Create or rebuild the 3D renderer; must run on the thread that draws."""
        if not self.ready():
            return None
        nz, ny, nx = self.ds.tomo.shape
        if self.ren is None or self._stale or self.ren.img.dimensions[:2] != (nx, ny):
            self._drop_renderer(); self.ren = Renderer(self.ds, self.headless); self._stale = False
        self.ren._slice_key = None
        return self.ren

    def _drop_renderer(self):
        if self.ren is not None:
            self.ren.close(); self.ren = None

    def ready(self):
        return self.ds.tomo is not None and self.ds.has_seg()

    def reset(self):
        self._drop_renderer()
        self.ds = Dataset(self.smooth, self.step)
        self.files = dict(tomogram=None, segmentation=None); self.ses = None
        self.sel = dict(image=None, roi=None); self.opts = dict(images=[], rois=[])
        self._tomo_src = self._seg_src = self._base_src = None; self._lab_file = None; self._base = None; self.feats = {}
        self._stale = False; self._file_vol = None; self._match = None; self.match_note = ""
        return self.info()

    def set_threshold(self, k, value):
        k = int(k)
        if k not in self.ds.scores:
            raise UserError("only structures loaded from score volumes have a threshold")
        self.ds.set_threshold(k, value)
        if self.ren is not None:
            self.ren.update_class(k)
        return dict(voxels=self.ds.classes[k]["voxels"], mesh=k in self.ds.meshes, threshold=self.ds.classes[k]["threshold"])

    def info(self):
        d = dict(ready=self.ready(), files=dict({k: (os.path.basename(v) if v else None) for k, v in self.files.items()},
                                                features=list(self.feats)),
                 images=self.opts["images"], image=self.sel["image"], rois=self.opts["rois"], roi=self.sel["roi"],
                 outdir=self.outdir, views=VIEWS, version=__version__, base_key=str(self._base_src), match_note=self.match_note)
        if self.ds.tomo is not None:
            nz, ny, nx = self.ds.tomo.shape
            d.update(dims=[nx, ny, nz], voxel=self.ds.vox, tomo_name=self.ds.tomo_name)
        if self.ready():
            d.update(spec=default_spec(self.ds), seg_key=f"{self._seg_src}",
                     classes=[dict(id=k, key=("F:" + c["name"]) if c["kind"] == "score" else f"L:{k}", name=c["name"], color=c["color"],
                                   voxels=c["voxels"], background=c["background"], mesh=k in self.ds.meshes, kind=c["kind"],
                                   threshold=c.get("threshold")) for k, c in self.ds.classes.items()])
        else:
            d["message"] = ("Now drop the segmentation: a Dragonfly .ORSSession, a label volume, or easymode files." if self.ds.tomo is not None
                            else "Now drop the tomogram." if (self.ds.has_seg() or self.feats or self.ses) else "Drop a tomogram and a segmentation to begin.")
        return d

    # ---- preview / pick ----
    def _frame(self, spec, req):
        frames, marks = build_timeline(spec, self.ds); n = len(frames)
        if req.get("at"):
            idx = [i for i, s in enumerate(frames) if s["shot"] == int(req["at"].get("shot", -9)) and s["phase"] == "main"]
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
        if not self.ready():
            raise UserError("load a tomogram and a segmentation first")
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
        name = spec.get("classes", {}).get(str(k), {}).get("name", self.ds.classes[k]["name"])
        if cid is None:
            return dict(found=True, **{"class": k}, component=None, name=name, voxels=self.ds.classes[k]["voxels"])
        return dict(found=True, **{"class": k}, component=cid, name=name, voxels=int(self.ds.components(k)[2][cid]))

    # ---- rendering job (runs on the main thread, like every other VTK call) ----
    def start_render(self, spec, name):
        if not self.ready():
            raise UserError("load a tomogram and a segmentation first")
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
            if res is None:
                job.update(state="cancelled", message="cancelled")
                try:
                    os.remove(job["path"])
                except OSError:
                    pass
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
