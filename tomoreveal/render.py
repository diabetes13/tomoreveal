"""Off-screen 3D drawing of one frame, and writing movies."""
import os

import numpy as np

from .common import hex2rgb
from .storyboard import Framer, build_timeline, class_spec, clip_box, others_of, state_key, target_items


def get_font(px, bold=False):
    from PIL import ImageFont
    cands = ("DejaVuSans-Bold.ttf", "Arial Bold.ttf", "arialbd.ttf") if bold else ()
    for cand in cands + ("DejaVuSans.ttf", "Arial.ttf", "arial.ttf", "/System/Library/Fonts/Helvetica.ttc"):
        try:
            return ImageFont.truetype(cand, px)
        except OSError:
            pass
    try:
        import matplotlib
        name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
        return ImageFont.truetype(os.path.join(os.path.dirname(matplotlib.__file__), "mpl-data/fonts/ttf", name), px)
    except Exception:
        pass
    try:
        return ImageFont.load_default(size=px)
    except TypeError:
        return ImageFont.load_default()


class Renderer:
    def __init__(self, ds, headless=False):
        if headless:
            os.environ["VTK_DEFAULT_OPENGL_WINDOW"] = "vtkOSOpenGLRenderWindow"
        import pyvista as pv, vtk
        self.pv, self.vtk, self.ds = pv, vtk, ds
        self.size, self.aa, self.bg, self.shown, self.peeling = (960, 540), "off", None, False, False
        self._light_key, self._ssao, self._real_shadows, self.real_shadows_ok = ("default",), False, False, False
        self.sil_actors, self._dots = {}, {}  # cartoon outlines and thinned-out meshes for dots, per structure
        self.glow_actors, self.cut_actors, self._cut_cache, self._fx_meshes = {}, {}, {}, {}  # close-up animations
        self._markers, self._path_actors, self.preview_part = [], [], None
        self._slice_grey, self._shadow_key = None, None
        self.pl = pv.Plotter(off_screen=True, window_size=list(self.size))
        self.shape = ds.shape; nz, ny, nx = self.shape
        self.img = pv.ImageData(dimensions=(nx, ny, 1), spacing=(1, 1, 1), origin=(0, 0, 0))
        self.img.point_data["I"] = np.zeros(nx * ny, np.uint8); self._slice_key = None
        self.slice_actor = self.pl.add_mesh(self.img, scalars="I", cmap="gray", clim=(0, 255), lighting=False,
                                            show_scalar_bar=False, pickable=False)
        self.plane = vtk.vtkPlane(); self.plane.SetNormal(0, 0, 1)
        # clip box: x-, x+, y-, y+, z+ (the lower z face is the slice itself); with the reveal plane that is the 6 OpenGL allows
        self.cplanes = []
        for nrm in ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, -1)):
            p = vtk.vtkPlane(); p.SetNormal(*nrm); self.cplanes.append(p)
        for p in self.cplanes[:4]:
            self.slice_actor.mapper.AddClippingPlane(p)
        self.actors, self.comp_actors, self._fonts = {}, {}, {}
        self.bar_actor, self.last_rects = None, {}
        self._card_cache, self._next_cache = {}, {}
        for k, m in ds.meshes.items():
            self.actors[k] = self._add(m)

    def _add(self, mesh, front=False):
        a = self.pl.add_mesh(mesh, color="white", smooth_shading=True, specular=0.2, specular_power=15, ambient=0.25, diffuse=0.8)
        a.mapper.AddClippingPlane(self.plane)
        for p in self.cplanes:
            a.mapper.AddClippingPlane(p)
        if front:  # drawn just in front of the identical surface of its parent class
            a.mapper.SetResolveCoincidentTopologyToPolygonOffset()
            a.mapper.SetRelativeCoincidentTopologyPolygonOffsetParameters(-2.0, -2.0)
        return a

    def _comp_actor(self, k, cid):
        key = (k, cid)
        if key not in self.comp_actors:
            m = self.ds.comp_mesh(k, cid)
            self.comp_actors[key] = self._add(m, front=True) if m is not None else None
        return self.comp_actors[key]

    def _font(self, px, bold=False):
        if (px, bold) not in self._fonts:
            self._fonts[(px, bold)] = get_font(px, bold)
        return self._fonts[(px, bold)]

    def _part(self, spec, pid):
        return next((p for p in spec.get("shots", []) if p.get("id") == pid), None)

    def draw_card(self, s, spec, size, aa=False, transparent=False, annotate=True):
        """A card frame: the card image (cached per part and size), faded, or dissolving into the next frame."""
        import json as _json
        from .cards import card_frame
        part = self._part(spec, s["card"]) or {}
        key = (_json.dumps(part, sort_keys=True, default=str), tuple(size), round(float(s.get("card_fade", 1.0)), 4), bool(transparent))
        if self._card_cache.get("key") != key:
            a, rects = card_frame(part, size, self._font, float(s.get("card_fade", 1.0)), transparent)
            self._card_cache = dict(key=key, img=a, rects=rects)
        a, rects = self._card_cache["img"], self._card_cache["rects"]
        q = float(s.get("dissolve", 0.0) or 0.0)
        if q > 0 and s.get("dissolve_to") is not None:
            nxt = s["dissolve_to"]; nk = (state_key(nxt), nxt.get("part"), tuple(size), bool(transparent))
            if self._next_cache.get("key") != nk:
                self._next_cache = dict(key=nk, img=self.draw(nxt, spec, size, aa=aa, transparent=True).astype(np.float32))
            b = self._next_cache["img"]
            a = a * (1 - q) + b * q
        out = np.clip(a, 0, 255).astype(np.uint8)
        if not transparent:
            al = out[..., 3:4].astype(np.float32) / 255.0
            bgc = np.array(hex2rgb(spec.get("style", {}).get("bg", "#000000"))) * 255
            out = (out[..., :3] * al + bgc * (1 - al)).astype(np.uint8)
        if not annotate:
            self.last_rects = dict(rects); return out
        img = self.annotate(out, s, spec, size, False, transparent)
        self.last_rects.update(rects)
        return img

    def draw(self, s, spec, size, aa=False, transparent=False, annotate=True, preview=False):
        """One frame. preview=True also shows helpers that never go into a movie (the path of a camera path part)."""
        ds, style = self.ds, spec.get("style", {})
        size = (int(size[0]) // 2 * 2, int(size[1]) // 2 * 2)
        if s.get("card"):
            return self.draw_card(s, spec, size, aa, transparent, annotate)
        if size != self.size:
            self.pl.window_size = list(size); self.size = size
        from .look import aa_mode, look_settings
        lk = look_settings(spec); mode = aa_mode(aa)
        if mode != self.aa:  # switching is costly: only when the mode changes
            self.pl.disable_anti_aliasing()
            if mode == "msaa":
                self.pl.enable_anti_aliasing("msaa", multi_samples=4)
            elif mode != "off":
                import warnings
                with warnings.catch_warnings():  # with OSMesa, VTK uses SSAA for FXAA and says so
                    warnings.simplefilter("ignore")
                    self.pl.enable_anti_aliasing(mode)
            self.aa = mode
        bg = style.get("bg", "#000000")
        if bg != self.bg:
            self.pl.set_background(hex2rgb(bg)); self.bg = bg
        bgc = hex2rgb(bg)
        # tomogram slice
        con = style.get("contrast", [1, 99]); inv = bool(style.get("invert", False)); k = int(s["k"])
        key = (k, tuple(con), inv, id(ds.tomo))
        if ds.tomo is not None and key != self._slice_key:
            lo, hi = ds.levels(con[0], con[1])
            g = np.clip((ds.tomo[k] - lo) * (255.0 / (hi - lo)), 0, 255).astype(np.uint8)
            self._slice_grey = (255 - g if inv else g); self._slice_key = key; self._shadow_key = None
        self.img.origin = (0, 0, float(k))
        show_slice = ds.tomo is not None and s["slice"] > 0.01
        # No depth peeling for a see-through slice alone: with software rendering (VTK 9.6, OSMesa, 3.2) it hid the slice
        # in frames drawn without anti-aliasing. Plain blending looks right for the slice.
        self.slice_actor.SetVisibility(show_slice); self.slice_actor.GetProperty().SetOpacity(float(s["slice"]))
        # the cutting plane keeps what lies above it: structures above the slice, all of them, or none
        cut = s.get("cut") or ("above" if s["reveal"] else "none")
        self.plane.SetOrigin(0, 0, {"above": k + 0.5, "all": -10.0}.get(cut, self.shape[0] + 10))
        cb = clip_box(spec, self.shape); far = 1e9
        (x0, y0, _), (x1, y1, z1) = cb if cb else ((-far, -far, 0), (far, far, far))
        for p, o in zip(self.cplanes, ((x0 - 0.5, 0, 0), (x1 + 0.5, 0, 0), (0, y0 - 0.5, 0), (0, y1 + 0.5, 0), (0, 0, z1 + 0.5))):
            p.SetOrigin(*o)
        # structures
        e, tgt, mode = float(s["e"]), s["target"], s["others"]
        items = self._focus_items(tgt); whole = {k for k, c in items if c is None}
        legacy = not isinstance(mode, dict)  # older settings (grey / hide / keep) are drawn exactly as before
        ocol, oop = others_of(mode)
        keep = (mode == "keep") if legacy else (ocol == "keep" and oop >= 0.999)
        all_op = float(min(1.0, max(0.0, style.get("structures_opacity", 1.0))))
        translucent = False
        for kk, act in self.actors.items():
            c = class_spec(spec, ds, kk)
            if not c.get("show", True):
                act.SetVisibility(False); continue
            op = float(min(1.0, max(0.0, c.get("opacity", 1.0)))) * all_op
            col = hex2rgb(c.get("color", ds.classes[kk]["color"]))
            if e <= 0 or not items or keep or kk in whole:
                act.SetVisibility(True)
            elif legacy:
                to = (0.27, 0.27, 0.27) if mode == "grey" else bgc
                col = tuple(a + (b - a) * e for a, b in zip(col, to))
                act.SetVisibility(not (mode == "hide" and e >= 0.999))
            else:  # the others: grey / darker / unchanged colour, and their own opacity, both faded in with e
                to = (0.27, 0.27, 0.27) if ocol == "grey" else tuple(v * 0.3 for v in col) if ocol == "darken" else col
                col = tuple(a + (b - a) * e for a, b in zip(col, to))
                op *= 1.0 - e * (1.0 - oop); act.SetVisibility(op > 0.002)
            act.GetProperty().SetOpacity(op); translucent |= op < 0.999 and act.GetVisibility()
            act.GetProperty().SetColor(*col)
        translucent |= self._apply_styles(spec, lk, all_op)
        for act in self.comp_actors.values():
            if act is not None:
                act.SetVisibility(False)
        for tid, comp in items:  # single objects in focus: drawn over their (greyed) structure
            if comp is None or e <= 0 or keep:
                continue
            act = self._comp_actor(tid, int(comp))
            if act is not None:
                act.SetVisibility(True)
                act.GetProperty().SetColor(*hex2rgb(class_spec(spec, ds, tid).get("color", ds.classes[tid]["color"])))
                op = float(min(1.0, max(0.0, class_spec(spec, ds, tid).get("opacity", 1.0)))) * all_op
                act.GetProperty().SetOpacity(op); translucent |= op < 0.999
        translucent |= self._apply_fx(s.get("fx") or {}, items, spec, e, keep, all_op)
        # Depth peeling only while some structure is see-through and the slice is opaque or hidden: with OSMesa (VTK 9.6)
        # peeling plus a see-through slice gives an all-black frame when drawn without anti-aliasing (3.2 and 3.4 pitfall).
        translucent = translucent and not (show_slice and float(s["slice"]) < 0.999)
        if translucent != self.peeling:
            self.pl.enable_depth_peeling(4) if translucent else self.pl.disable_depth_peeling()
            self.peeling = translucent
        # camera
        p = s["pose"]; fr = Framer(self.shape, size[0] / size[1], float(style.get("fov", 30)), bool(style.get("ortho", False)))
        d, up = fr.axes(p["tilt"], p["az"]); cam = self.pl.camera
        if p.get("pos") is not None:  # camera paths give the position and the up direction themselves
            cam.focal_point = tuple(p["c"]); cam.position = tuple(np.asarray(p["pos"], float)); cam.up = tuple(np.asarray(p["up"], float))
        else:
            cam.focal_point = tuple(p["c"]); cam.position = tuple(p["c"] + p["dist"] * d); cam.up = tuple(up)
        if fr.ortho:
            cam.parallel_projection = True; cam.parallel_scale = p["scale"]
        else:
            cam.parallel_projection = False; cam.view_angle = fr.fov
        self._show_path(s, spec, preview)
        self._apply_lighting(lk)
        self._apply_slice_texture(s, spec, lk, show_slice, cb)
        self._vig = self._vignette_state(s, spec, lk, size)
        self._apply_blur(self._vig)
        self.pl.renderer.ResetCameraClippingRange()
        self._bar_label = self._prepare_slice_bar(s, spec, size, show_slice) if annotate else None
        if not self.shown:
            self.pl.show(auto_close=False); self.shown = True
        self.pl.render()
        fr_img = self.pl.screenshot(None, transparent_background=bool(transparent))
        if self._vig is not None and self._vig["a"] > 0.001:
            fr_img = self._apply_vignette(fr_img, self._vig, transparent)
        if not annotate:
            return fr_img
        img = self.annotate(fr_img, s, spec, size, show_slice, transparent)
        if self._markers:
            img = self._draw_markers(img, size)
        return img

    def _vignette_state(self, s, spec, lk, size):
        """Vignette (and background blur) of this frame, or None: the close-up's own settings, else the Look tab's."""
        part = self._part(spec, s.get("part")) or {}
        lv = lk["vignette"]; own = part.get("vignette") if part.get("type") == "focus" and isinstance(part.get("vignette"), dict) else None
        cfg = dict(lv); cfg["blur"] = dict(lv["blur"])
        if own:
            cfg.update({k: v for k, v in own.items() if k != "blur"}); cfg["blur"].update(own.get("blur") or {})
        e = float(s["e"]); items = self._focus_items(s["target"]); in_close = bool(items) and e > 0
        if own is not None:
            f = e if (own.get("on", True) and in_close) else 0.0
        elif lv["when"] == "always":
            f = 1.0
        elif lv["when"] == "closeups":
            f = e if in_close else 0.0
        else:
            f = 0.0
        if f <= 0:
            return None
        W, H = size; cx, cy = W / 2, H / 2
        if in_close:  # centred on the focused objects, wherever they are in the frame
            try:
                lo, hi = self.ds.targets_box(s["target"]); c = (np.asarray(lo) + np.asarray(hi)) / 2
                ren = self.pl.renderer; ren.SetWorldPoint(*c, 1.0); ren.WorldToDisplay(); dx, dy, _ = ren.GetDisplayPoint()
                cx, cy = min(max(dx, 0), W), min(max(H - dy, 0), H)
            except (KeyError, IndexError, TypeError, ValueError):
                pass
        col = (0, 0, 0) if cfg.get("colour", "black") == "black" else tuple(255 * v for v in hex2rgb(spec.get("style", {}).get("bg", "#000000")))
        blur = float(cfg["blur"]["strength"]) * f if cfg["blur"].get("on") else 0.0
        return dict(a=float(cfg["strength"]) * f, cx=cx, cy=cy, size=float(cfg["size"]), soft=float(cfg["softness"]),
                    shape=cfg.get("shape", "round"), colour=col, blur=blur)

    def _apply_vignette(self, img, v, transparent):
        from .look import vignette_weight
        H, W = img.shape[:2]
        key = (W, H, round(v["cx"]), round(v["cy"]), v["size"], v["soft"], v["shape"])
        if getattr(self, "_vig_w", (None,))[0] != key:
            self._vig_w = (key, vignette_weight(W, H, v["cx"], v["cy"], v["size"], v["soft"], v["shape"]))
        w = self._vig_w[1] * v["a"]
        out = img.astype(np.float32)
        if transparent and img.shape[2] == 4:
            out[..., 3] *= 1 - w
        else:
            out[..., :3] = out[..., :3] * (1 - w[..., None]) + np.array(v["colour"], np.float32) * w[..., None]
        return out.astype(np.uint8)

    def _apply_blur(self, v):
        """Background blur in close-ups: VTK depth of field, focused at the camera's focal point (the focused objects)."""
        amount = v["blur"] if v else 0.0
        on = amount > 0.001
        if on != getattr(self, "_dof", False):
            self.pl.enable_depth_of_field(automatic_focal_distance=True) if on else self.pl.disable_depth_of_field()
            self._dof = on
        if on:
            self.pl.camera.SetFocalDisk(amount * 0.08 * max(self.shape))

    # ---------- camera paths: drawn in the preview only ----------
    def _show_path(self, s, spec, preview):
        """The line of the selected camera path part as a thin tube with arrows, and numbered waypoints (preview only)."""
        self._markers = []
        for a in getattr(self, "_path_actors", []):
            self.pl.renderer.RemoveActor(a)
        self._path_actors = []
        sel = getattr(self, "preview_part", None)
        part = self._part(spec, sel) if preview and sel else None
        if not part or part.get("type") != "path" or not part.get("show_path_in_preview", True):
            return
        from .paths import arclength, catmull_rom
        from .storyboard import PathPending, path_line
        pv = self.pv; pts = None; src = part.get("source") or {}
        try:
            if src.get("kind", "follow") == "follow":
                items = [(it.get("structure"), it.get("component")) for it in src.get("items", [])]
                pts = path_line(self.ds, items, clip_box(spec, self.shape)) if items else None
                if pts is not None and part.get("reverse"):
                    pts = pts[::-1]
            else:
                wps = part.get("waypoints") or []
                foc = [np.asarray(w.get("focal") or w.get("point") or w.get("pos"), float) for w in wps]
                self._markers = [(i + 1, f) for i, f in enumerate(foc)]
                pts = catmull_rom(foc)[0] if len(foc) >= 2 else None
        except (PathPending, Exception):
            pts = None
        r = max(0.6, 0.004 * max(self.shape))
        cum = arclength(pts) if pts is not None and len(pts) >= 2 else [0.0]
        if cum[-1] > 1e-3:  # a path that only turns the camera has no line to draw
            tube = pv.Spline(pts, max(20, len(pts))).tube(radius=r)
            self._path_actors.append(self.pl.add_mesh(tube, color="#ffd23f", lighting=False, pickable=False))
        step = cum[-1] / 6 if cum[-1] > 1e-3 else 0
        for d in (np.arange(step / 2, cum[-1], step) if step else []):  # arrows show the direction
            i = int(np.searchsorted(cum, d)); i = min(max(1, i), len(pts) - 1)
            t = pts[i] - pts[i - 1]; t /= np.linalg.norm(t) or 1.0
            cone = pv.Cone(center=pts[i], direction=t, height=r * 8, radius=r * 3)
            self._path_actors.append(self.pl.add_mesh(cone, color="#ffd23f", lighting=False, pickable=False))
        for _, f in self._markers:
            self._path_actors.append(self.pl.add_mesh(pv.Sphere(radius=r * 3, center=f), color="#ff4fa3", lighting=False, pickable=False))

    def _draw_markers(self, img, size):
        """Numbers next to the waypoints; their screen boxes go into last_rects as "marker:i" for dragging."""
        from PIL import Image, ImageDraw
        im = Image.fromarray(img); d = ImageDraw.Draw(im); ren = self.pl.renderer; W, H = size
        f = self._font(max(10, round(H * 0.024)))
        for n, p in self._markers:
            ren.SetWorldPoint(*p, 1.0); ren.WorldToDisplay(); x, y, _ = ren.GetDisplayPoint(); y = H - y
            d.text((x + 6, y - 18), str(n), fill=(255, 79, 163), font=f, stroke_width=2, stroke_fill=(0, 0, 0))
            self.last_rects[f"marker:{n - 1}"] = dict(box=(x - 8, y - 8, x + 8, y + 8), shown=True)
        return np.asarray(im)

    def to_plane(self, fx, fy, z):
        """The point on the plane at height z under a position (fractions of the last frame), or None."""
        ren = self.pl.renderer; W, H = self.size; pts = []
        for depth in (0.0, 1.0):
            ren.SetDisplayPoint(fx * W, (1 - fy) * H, depth); ren.DisplayToWorld(); w = ren.GetWorldPoint()
            pts.append(np.array(w[:3]) / (w[3] or 1.0))
        a, b = pts; dz = b[2] - a[2]
        if abs(dz) < 1e-9:
            return None
        t = (z - a[2]) / dz
        return a + (b - a) * t

    def pick_point(self, fx, fy):
        """A 3D point on a structure or on the tomogram slice under a position (fractions), or None."""
        pk = self.vtk.vtkCellPicker(); pk.SetTolerance(0.003)
        self.slice_actor.SetPickable(True)
        try:
            ok = pk.Pick(fx * self.size[0], (1 - fy) * self.size[1], 0, self.pl.renderer)
        finally:
            self.slice_actor.SetPickable(False)
        return np.array(pk.GetPickPosition()) if ok and pk.GetActor() is not None else None

    # ---------- close-up animations (fx) ----------
    def _fx_mesh(self, k, cid, kind="item"):
        """Surfaces used by the effects, cached: the item itself, the item with outward-facing polygons (for capped
        clipping), and the rest of a structure without one of its objects."""
        key = (k, cid, kind, id(self.ds.meshes.get(k)))
        if key not in self._fx_meshes:
            ds = self.ds
            if kind == "rest":
                from .volumes import surface
                lbl, _, _ = ds.components(k); sl = ds.bbox[k]
                m = surface(ds.kept_mask(k, sl) & (lbl != int(cid)), [x.start for x in sl], ds.param(k, "smoothing"), ds.step)
            else:
                m = ds.meshes.get(k) if cid is None else ds.comp_mesh(k, int(cid))
                if m is not None and kind == "outward":
                    m = m.compute_normals(flip_normals=True, split_vertices=False)  # our surfaces are wound inwards
            self._fx_meshes[key] = m
        return self._fx_meshes[key]

    def _clipped(self, k, cid, normal, origin):
        """(body, cap) of an item cut by a plane on the CPU (the six OpenGL clipping planes are all in use); cached by the
        plane's position rounded to 0.5 voxel."""
        n = np.asarray(normal, float); o = np.round(np.asarray(origin, float) * 2) / 2
        key = (k, cid, tuple(np.round(n, 3)), tuple(o), id(self.ds.meshes.get(k)))
        if key in self._cut_cache:
            return self._cut_cache[key]
        vtk, pv = self.vtk, self.pv
        m = self._fx_mesh(k, cid, "outward")
        body = cap = None
        if m is not None:
            f = vtk.vtkClipClosedSurface(); pc = vtk.vtkPlaneCollection(); pl = vtk.vtkPlane(); pl.SetNormal(*n); pl.SetOrigin(*o)
            pc.AddItem(pl); f.SetClippingPlanes(pc); f.SetInputData(m); f.SetScalarModeToLabels(); f.Update()
            out = pv.wrap(f.GetOutput())
            if out.n_cells:
                lab = np.asarray(out.cell_data["Labels"])
                body = out.extract_cells(np.nonzero(lab == 0)[0]).extract_surface(algorithm="dataset_surface") if (lab == 0).any() else None
                cap = out.extract_cells(np.nonzero(lab == 1)[0]).extract_surface(algorithm="dataset_surface") if (lab == 1).any() else None
        if len(self._cut_cache) > 96:
            self._cut_cache.pop(next(iter(self._cut_cache)))
        self._cut_cache[key] = (body, cap)
        return body, cap

    def _cut_actor(self, key, part):
        a = self.cut_actors.get((key, part))
        if a is None:
            a = self._add(self.pv.Sphere(radius=0.01), front=key[1] is not None); a.SetPickable(False)
            self.cut_actors[(key, part)] = a
        return a

    def _apply_fx(self, fx, items, spec, e, keep, all_op):
        """Pulse, glowing outline, fade and cutting of the objects in focus. Returns True when something is see-through."""
        from colorsys import hsv_to_rgb, rgb_to_hsv
        for a in list(self.glow_actors.values()) + list(self.cut_actors.values()):
            a.SetVisibility(False)
        for kk, act in self.actors.items():  # the structure's own surface again (a cut may have swapped it)
            if getattr(act, "_rest", False):
                act.mapper.SetInputData(self.ds.meshes[kk]); act._rest = False; act._shown_mesh = self.ds.meshes[kk]
        if not fx or not items:
            return False
        ds = self.ds; translucent = False
        targets = []  # (structure id, component, actor drawing it now)
        for k, cid in items:
            act = self.actors.get(k) if cid is None else self.comp_actors.get((k, int(cid)))
            if act is not None and (cid is None or (e > 0 and not keep)):
                targets.append((k, cid, act))
        for k, cid, act in targets:
            pr = act.GetProperty(); cut_body = None
            if "pulse" in fx:  # brighter and back, n times
                h, sat, v = rgb_to_hsv(*pr.GetColor()); v = min(1.0, v * (1 + 0.35 * float(fx["pulse"])))
                pr.SetColor(*hsv_to_rgb(h, sat, v))
            if "fade" in fx:
                pr.SetOpacity(pr.GetOpacity() * float(fx["fade"])); act.SetVisibility(act.GetVisibility() and fx["fade"] > 0.002)
                translucent |= float(fx["fade"]) < 0.999
            if "cut" in fx:
                c = fx["cut"]; body, cap = self._clipped(k, cid, c["normal"], c["origin"]); cut_body = body
                if cid is None:
                    act.SetVisibility(False)
                else:  # the greyed structure must not draw this object uncut over the cross-section
                    act.SetVisibility(False); rest = self._fx_mesh(k, cid, "rest"); sa = self.actors.get(k)
                    if sa is not None and rest is not None:
                        sa.mapper.SetInputData(rest); sa._rest = True; sa._shown_mesh = rest
                col = np.array(pr.GetColor())
                for part_name, mesh, colour in (("body", body, col), ("cap", cap, col + (1 - col) * (0.45 if c.get("mode") == "section" else 0.0))):
                    if mesh is None or mesh.n_cells == 0:
                        continue
                    ca = self._cut_actor((k, cid), part_name); ca.mapper.SetInputData(mesh)
                    cp = ca.GetProperty(); cp.SetColor(*colour); cp.SetOpacity(pr.GetOpacity())
                    cp.SetAmbient(pr.GetAmbient()); cp.SetDiffuse(pr.GetDiffuse()); cp.SetSpecular(pr.GetSpecular())
                    ca.SetVisibility(pr.GetOpacity() > 0.002)
            if "glow" in fx:
                mesh = cut_body if "cut" in fx else self._fx_mesh(k, cid)  # the outline follows the cut
                if mesh is not None:
                    g = self.glow_actors.get((k, cid))
                    if g is None or getattr(g, "_mesh", None) is not mesh:
                        g = self._silhouette(("glow", k, cid), mesh); self.glow_actors[(k, cid)] = g
                    col = np.array(pr.GetColor()); gp = g.GetProperty()
                    gp.SetColor(*(col + (1 - col) * 0.6)); gp.SetLineWidth(2 + 4 * float(fx["glow"])); gp.LightingOff()
                    g.SetVisibility(bool(act.GetVisibility() or ("cut" in fx and pr.GetOpacity() > 0.002)))
        return translucent

    def _focus_items(self, tgt):
        """[(structure id, component or None)] of a close-up target that are loaded."""
        out = []
        for key, comp in target_items(tgt):
            k = self.ds.id_of(key)
            if k is not None:
                out.append((k, comp))
        return out

    # ---------- lighting, styles, shadows ----------
    def _apply_lighting(self, lk):
        """Lights of the chosen preset; 'default' keeps PyVista's light kit, as in every earlier version."""
        L = lk["lighting"]; pre = L.get("preset", "default")
        key = (pre, round(float(L["azimuth"]), 2), round(float(L["elevation"]), 2), bool(L["follow_camera"]), round(float(L["intensity"]), 3),
               round(float(L["fill"]), 3), round(float(L["ssao_radius"]), 4), round(float(L["ssao_strength"]), 3),
               bool(lk["shadows"]["real"]["on"] and self.real_shadows_ok), round(float(lk["shadows"]["real"]["strength"]), 3))
        if key == self._light_key:
            return
        pv = self.pv; pl = self.pl
        pl.remove_all_lights()
        want_ssao = pre in ("soft", "full")
        if pre == "default":
            pl.enable_lightkit()
        elif pre != "flat":
            from .look import camera_direction, direction
            inten = float(L["intensity"]) * (0.8 if want_ssao else 1.0)
            c = np.array(self.shape[::-1], float) / 2
            for sign, amount in ((1, inten), (-1, inten * float(L["fill"]))):
                if amount <= 0:
                    continue
                if L["follow_camera"]:
                    v = camera_direction(float(L["azimuth"]), float(L["elevation"])) * np.array([sign, 1, 1])
                    lt = pv.Light(position=tuple(v), focal_point=(0, 0, 0), light_type="camera light", intensity=amount)
                else:
                    v = direction(float(L["azimuth"]) + (180 if sign < 0 else 0), float(L["elevation"]) if sign > 0 else 30.0)
                    lt = pv.Light(position=tuple(c + v * 1e4), focal_point=tuple(c), light_type="scene light", intensity=amount)
                lt.positional = False
                if sign > 0 and key[-2]:
                    lt.shadow_attenuation = float(lk["shadows"]["real"]["strength"])
                pl.add_light(lt)
        ssao = key[6:8] if want_ssao else False
        if ssao != self._ssao:
            if self._ssao:
                pl.disable_ssao()
            if want_ssao:
                pl.enable_ssao(radius=float(L["ssao_radius"]) * max(self.shape), bias=0.01, kernel_size=64, blur=True)
            self._ssao = ssao
        if key[-2] != self._real_shadows:
            pl.enable_shadows() if key[-2] else pl.disable_shadows()
            self._real_shadows = key[-2]
        self._light_key = key

    def _apply_styles(self, spec, lk, all_op):
        """Surface style (normal / cartoon / dots / glossy) and lighting amounts of every structure actor."""
        from .look import GLOSS, style_of
        L = lk["lighting"]; pre = L.get("preset", "default"); ds = self.ds
        amb, spec_amt = (0.25, 0.2) if pre == "default" else (float(L["ambient"]), float(L["specular"]))
        glass = False
        for kk, act in self.actors.items():
            st = style_of(spec, ds.classes[kk]["key"]); pr = act.GetProperty(); kind = st["kind"]
            pr.SetRepresentationToSurface(); pr.SetInterpolationToPhong(); pr.SetMetallic(0.0); pr.SetRoughness(0.5)
            pr.RenderPointsAsSpheresOff(); pr.SetLighting(pre != "flat")
            pr.SetAmbient(amb); pr.SetDiffuse(0.8); pr.SetSpecular(spec_amt); pr.SetSpecularPower(15)
            mesh = ds.meshes.get(kk)
            if kind == "dots" and mesh is not None:
                pr.SetRepresentationToPoints(); pr.RenderPointsAsSpheresOn(); pr.SetPointSize(float(st["point_size"]))
                dm = self._dots_mesh(kk, mesh, float(st["point_size"]))
                if getattr(act, "_shown_mesh", None) is not dm:
                    act.mapper.SetInputData(dm); act._shown_mesh = dm
            elif mesh is not None and getattr(act, "_shown_mesh", None) not in (None, mesh):
                act.mapper.SetInputData(mesh); act._shown_mesh = mesh
            if kind == "cartoon":
                pr.SetAmbient(0.6); pr.SetDiffuse(0.4); pr.SetSpecular(0.0)
            elif kind == "glossy":
                m, r, o = GLOSS.get(st.get("gloss", "shiny"), GLOSS["shiny"])
                pr.SetInterpolationToPBR(); pr.SetMetallic(m); pr.SetRoughness(r)
                if o < 1:
                    pr.SetOpacity(pr.GetOpacity() * o); glass |= bool(act.GetVisibility())
            if pre == "flat":
                pr.SetAmbient(1.0); pr.SetDiffuse(0.0); pr.SetSpecular(0.0)
            sil = self.sil_actors.get(kk)
            if kind == "cartoon" and mesh is not None:
                sil = self._silhouette(kk, mesh)
                col = np.array(pr.GetColor())
                sp = sil.GetProperty(); sp.SetLineWidth(float(st["outline_width"])); sp.LightingOff()
                sp.SetColor(*((0.0, 0.0, 0.0) if st.get("outline_color", "black") == "black" else tuple(col * 0.45)))
                sp.SetOpacity(pr.GetOpacity()); sil.SetVisibility(act.GetVisibility())
            elif sil is not None:
                sil.SetVisibility(False)
        return glass

    def _dots_mesh(self, k, mesh, size):
        """The surface thinned out so that dots of this size stay apart."""
        key = (k, round(size, 1), id(mesh))
        if self._dots.get(k, (None,))[0] != key:
            target = max(0.0, min(0.95, 1.0 - 1.0 / max(1.0, size * 0.6) ** 2))
            m = mesh.decimate(target) if target > 0.05 and mesh.n_cells > 2000 else mesh
            self._dots[k] = (key, m)
        return self._dots[k][1]

    def _silhouette(self, k, mesh):
        """Outline actor of a cartoon structure: drawn where the surface turns away from the camera, with the same
        clipping planes as the structure, so cut-away parts have no outline."""
        a = self.sil_actors.get(k)
        if a is not None and getattr(a, "_mesh", None) is mesh:
            return a
        if a is not None:
            self.pl.renderer.RemoveActor(a)
        vtk = self.vtk
        src = mesh  # outlines are found on the CPU every frame: large surfaces use a thinned-out copy (measured in 3.4)
        if mesh.n_cells > 200_000:
            src = mesh.decimate(1.0 - 200_000 / mesh.n_cells)
        f = vtk.vtkPolyDataSilhouette(); f.SetInputData(src); f.SetCamera(self.pl.renderer.GetActiveCamera())
        f.SetDirectionToCameraOrigin(); f.BorderEdgesOn(); f.SetFeatureAngle(60)
        m = vtk.vtkPolyDataMapper(); m.SetInputConnection(f.GetOutputPort()); m.ScalarVisibilityOff()
        m.SetResolveCoincidentTopologyToPolygonOffset(); m.SetRelativeCoincidentTopologyLineOffsetParameters(-2.0, -2.0)
        m.AddClippingPlane(self.plane)
        for p in self.cplanes:
            m.AddClippingPlane(p)
        a = vtk.vtkActor(); a.SetMapper(m); a.PickableOff(); a._mesh = mesh; a._filter = f
        self.pl.renderer.AddActor(a); self.sil_actors[k] = a
        return a

    def _visible_points(self):
        out = []
        for kk, act in self.actors.items():
            if act.GetVisibility() and act.GetProperty().GetOpacity() > 0.02 and kk in self.ds.meshes:
                out.append((kk, self.ds.meshes[kk]))
        return out

    def _apply_slice_texture(self, s, spec, lk, show_slice, cb):
        """The grey slice, darkened where structures above it cast their shadow (slice shadows)."""
        if self._slice_grey is None:
            return
        sh = lk["shadows"]["slice"]; L = lk["lighting"]
        cut = s.get("cut") or ("above" if s["reveal"] else "none")
        on = bool(sh.get("on")) and float(sh.get("strength", 0)) > 0 and show_slice and cut != "none"
        key = ("off", self._slice_key)
        if on:
            from .look import camera_direction, direction, to_world
            if L.get("follow_camera"):
                cam = self.pl.camera
                v = to_world(camera_direction(float(L["azimuth"]), float(L["elevation"])), cam.position, cam.focal_point, cam.up)
            else:
                v = direction(float(L["azimuth"]), float(L["elevation"]))
            vis = self._visible_points()
            key = (int(s["k"]), tuple(np.round(v, 3)), tuple((kk, id(m)) for kk, m in vis), round(float(sh["strength"]), 3),
                   round(float(sh["softness"]), 2), cb, self._slice_key)
        if key == self._shadow_key:
            return
        g = self._slice_grey
        if on:
            from .look import shadow_mask
            m = shadow_mask([mm.points for _, mm in vis], int(s["k"]), v, self.shape, float(sh["softness"]), clip=cb)
            if m is not None:
                g = (g.astype(np.float32) * (1.0 - float(sh["strength"]) * m)).astype(np.uint8)
        self.img.point_data["I"] = g.ravel(); self._shadow_key = key

    def annotate(self, fr_img, s, spec, size, show_slice, transparent=False):
        """Draw the overlay items (legend, slice number, scale bar, title, caption) on a rendered frame.
        Their boxes are kept in self.last_rects (pixels) for the preview, which lets the user drag them."""
        from PIL import Image
        from . import overlays as ov
        ds, style = self.ds, spec.get("style", {}); W, H = size; k = int(s["k"])
        items = ov.overlay_settings(spec); pid = s.get("part")

        def shown(name, base):
            c = ov.part_choice(spec, pid, name)
            return items[name]["show"] and base if c == "default" else (c == "show")
        im = Image.fromarray(fr_img).convert("RGBA"); layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
        P = ov.Painter(layer, self._font, size, hex2rgb(style.get("bg", "#000000")))
        sl = items["slice_label"]; nz = self.shape[0]
        txt = str(sl.get("format") or "slice {k}/{n}").replace("{k}", str(k + 1)).replace("{n}", str(nz))
        vox = ov.voxel_size(spec, ds)
        if sl.get("show_depth") and vox:
            txt += f" \u00b7 {k * vox / 10.0:.0f} nm"
        base_slice = show_slice and s["slice"] > 0.5
        P.text_item("slice_label", sl, txt, lambda px: (round(px * 1.5), round(px * 1.1)),
                    shown("slice_label", base_slice) and ds.tomo is not None)
        P.text_item("title", items["title"], str(style.get("title") or ""), lambda px: (round(px * 1.5), H - round(px * 2.4)),
                    shown("title", True))
        e, tgt, mode = float(s["e"]), s["target"], s["others"]
        focused = {k for k, _ in self._focus_items(tgt)}
        keep = mode == "keep" if not isinstance(mode, dict) else others_of(mode) == ("keep", 1.0)
        rows = [(class_spec(spec, ds, kk).get("name", ds.classes[kk]["name"]), class_spec(spec, ds, kk).get("color", ds.classes[kk]["color"]))
                for kk in sorted(self.actors) if class_spec(spec, ds, kk).get("show", True)
                and not (focused and e > 0.5 and not keep and kk not in focused)]
        P.legend(items["legend"], rows, shown("legend", bool(s["reveal"])))
        self._scale_bar(P, items["scale_bar"], s, spec, size, vox, shown("scale_bar", True))
        P.caption(items["caption"], s["caption"] or "", shown("caption", True))
        self.last_rects = P.rects
        im = Image.alpha_composite(im, layer)
        return np.asarray(im if transparent else im.convert("RGB"))

    def _scale_bar(self, P, item, s, spec, size, vox, show):
        from . import overlays as ov
        style = spec.get("style", {}); W, H = size
        on_slice = item.get("mode") == "slice"
        if not vox:
            P.scale_bar(item, 0, "", False); return
        if on_slice:
            if self._bar_label is None:
                P.scale_bar(item, 0, "", False); return
            at, L = self._bar_label
            P.scale_bar(item, 0, ov.length_text(L), show, at=at); return
        L = item.get("length_nm")
        L = float(L) if L not in (None, "auto") else s.get("bar_nm")
        fr = Framer(self.shape, W / H, float(style.get("fov", 30)), bool(style.get("ortho", False)))
        if L is None:
            L = ov.auto_length(ov.px_per_voxel(s["pose"], H, fr.fov, fr.ortho) * 10.0 / vox, W)
        px = ov.px_per_voxel(s["pose"], H, fr.fov, fr.ortho) * float(L) * 10.0 / vox
        P.scale_bar(item, px, ov.length_text(L), show)

    def _prepare_slice_bar(self, s, spec, size, show_slice):
        """Before rendering: the on-slice scale bar, when it is used in this frame. Returns (label position, nm) or None."""
        from . import overlays as ov
        if self.bar_actor is not None:
            self.bar_actor.SetVisibility(False)
        item = ov.overlay_settings(spec)["scale_bar"]; vox = ov.voxel_size(spec, self.ds)
        if item.get("mode") != "slice" or not vox or self.ds.tomo is None or not show_slice:
            return None
        c = ov.part_choice(spec, s.get("part"), "scale_bar")
        if not (item["show"] if c == "default" else c == "show"):
            return None
        nz, ny, nx = self.shape; L = item.get("length_nm")
        L = float(L) if L not in (None, "auto") else ov.auto_length(1.0 / (vox / 10.0), nx) or 10.0  # 10-25 % of the slice width
        return self._slice_bar(item, s, spec, size, L * 10.0 / vox), L

    def _slice_bar(self, item, s, spec, size, L_vox):
        """A flat bar lying on the slice (exact in every view), near the slice corner closest to the item's anchor.
        Returns the screen position (pixels) for its label."""
        pv = self.pv; W, H = size; k = float(s["k"]); nz, ny, nx = self.shape
        cb = clip_box(spec, self.shape)
        (x0, y0, _), (x1, y1, _) = cb if cb else ((0, 0, 0), (nx - 1, ny - 1, nz - 1))
        corners = [(x0, y0), (x1, y0), (x0, y1), (x1, y1)]
        ren = self.pl.renderer; ax = float(item.get("x") if item.get("x") is not None else 0.97) * W
        ay = float(item.get("y") if item.get("y") is not None else 0.95) * H

        def screen(x, y, z):
            ren.SetWorldPoint(x, y, z, 1.0); ren.WorldToDisplay(); dx, dy, _ = ren.GetDisplayPoint()
            return dx, H - dy
        cx, cy = min(corners, key=lambda c: sum((a - b) ** 2 for a, b in zip(screen(c[0], c[1], k), (ax, ay))))
        m = 0.04 * max(x1 - x0, y1 - y0); th = max(1.0, 0.012 * max(x1 - x0, y1 - y0)) * float(item.get("thickness", 1.0))
        sx, sy = (1 if cx == x0 else -1), (1 if cy == y0 else -1)
        bx0 = cx + sx * m; bx1 = bx0 + sx * L_vox; by = cy + sy * m
        box = pv.Box(bounds=(min(bx0, bx1), max(bx0, bx1), min(by, by + sy * th), max(by, by + sy * th), k + 0.2, k + 0.6))
        col = (1, 1, 1) if item.get("color", "auto") == "auto" and sum(hex2rgb(spec.get("style", {}).get("bg", "#000000"))) < 1.5 else \
            (0, 0, 0) if item.get("color", "auto") == "auto" else hex2rgb(item["color"])
        if self.bar_actor is None:
            self.bar_actor = self.pl.add_mesh(box, color=col, lighting=False, pickable=False)
        else:
            self.bar_actor.mapper.SetInputData(box); self.bar_actor.GetProperty().SetColor(*col)
        self.bar_actor.SetVisibility(True)
        return screen((bx0 + bx1) / 2, by + sy * th * 2, k)

    def update_class(self, k):
        """Swap in the new surface of one structure (after a threshold change)."""
        a = self.actors.pop(k, None)
        if a is not None:
            self.pl.remove_actor(a)
        for key in [key for key in self.comp_actors if key[0] == k]:
            c = self.comp_actors.pop(key)
            if c is not None:
                self.pl.remove_actor(c)
        sil = self.sil_actors.pop(k, None)
        if sil is not None:
            self.pl.renderer.RemoveActor(sil)
        self._dots.pop(k, None)
        for key in [key for key in self.glow_actors if key[0] == k]:
            self.pl.renderer.RemoveActor(self.glow_actors.pop(key))
        for key in [key for key in self.sil_actors if isinstance(key, tuple) and key[1] == k]:
            self.pl.renderer.RemoveActor(self.sil_actors.pop(key))
        self._fx_meshes = {key: v for key, v in self._fx_meshes.items() if key[0] != k}
        self._cut_cache = {key: v for key, v in self._cut_cache.items() if key[0] != k}
        if k in self.ds.meshes:
            self.actors[k] = self._add(self.ds.meshes[k])

    def pick(self, x, y):
        """x, y in 0..1 from the top-left of the last drawn frame -> (class, xyz) or None."""
        pk = self.vtk.vtkCellPicker(); pk.SetTolerance(0.003)
        if not pk.Pick(x * self.size[0], (1 - y) * self.size[1], 0, self.pl.renderer) or pk.GetActor() is None:
            return None
        addr = pk.GetActor().GetAddressAsString("")
        for kk, a in self.actors.items():
            if a.GetAddressAsString("") == addr:
                return kk, pk.GetPickPosition()
        for (kk, _), a in self.comp_actors.items():
            if a is not None and a.GetAddressAsString("") == addr:
                return kk, pk.GetPickPosition()
        return None

    def close(self):
        try:
            self.pl.close()
        except Exception:
            pass


class MovieJob:
    """Renders a movie frame by frame: iterate it to get (frames done, total, last image). Closing it before the end
    cancels the movie and deletes its partial files. With resume=True, the frames of the complete chunks of an
    interrupted movie with the same settings are kept and only the rest is rendered (see encode.py)."""

    def __init__(self, ren, spec, out, frames_dir=None, transparent=False, poster=True, resume=False, chunk_frames=None):
        from . import encode
        self.ren, self.spec, self.out, self.frames_dir, self.transparent = ren, spec, out, frames_dir, transparent
        self.frames, _ = build_timeline(spec, ren.ds)
        o = spec.get("output", {}); size = o.get("size", [1920, 1080]); self.fps = int(o.get("fps", 30))
        self.size = (int(size[0]) // 2 * 2, int(size[1]) // 2 * 2)
        from .look import look_settings
        self.aa = look_settings(spec)["aa"]["movie"]
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        if frames_dir:
            os.makedirs(frames_dir, exist_ok=True)
        formats = {"mp4": encode.video_args("mp4", crf=int(o.get("crf", 20)), preset="medium")}
        self.settings = encode.settings_hash(spec, ren.ds)
        chunk = int(chunk_frames or encode.CHUNK_FRAMES); start_chunk = 0
        old = encode.read_partial(out)
        if resume and old and old.get("settings") == self.settings and old.get("formats") == list(formats) \
                and int(old.get("chunk_frames", 0)) == chunk and old["chunks_done"] > 0:
            start_chunk = old["chunks_done"]
        else:
            encode.remove_partial(out)
        self.start = min(len(self.frames), start_chunk * chunk)
        self.writer = encode.ChunkedWriter(out, formats, self.size, self.fps, len(self.frames), self.settings, chunk, start_chunk)
        # poster: a frame from the main phase of a slide that shows structures (else an overview), never a close-up
        fr = self.frames
        idx = [i for i, s in enumerate(fr) if s.get("type") == "slide" and s["reveal"] and s["phase"] == "main" and s["e"] == 0] \
            or [i for i, s in enumerate(fr) if s.get("type") == "overview" and s["phase"] == "main" and s["e"] == 0]
        self.poster_i = idx[len(idx) // 4] if (idx and poster) else -1
        self.i, self.last_key, self.img, self.finished, self.closed = self.start, None, None, False, False

    def __iter__(self):
        return self

    def __len__(self):
        return len(self.frames)

    def __next__(self):
        import imageio.v2 as imageio
        if self.closed:
            raise StopIteration
        if self.i >= len(self.frames):
            self.writer.close(); self.finished = self.closed = True
            raise StopIteration
        try:
            i, s = self.i, self.frames[self.i]
            key = (state_key(s), s.get("part"), s.get("bar_nm"), s.get("card"), round(float(s.get("card_fade", 1)), 4),
                   round(float(s.get("dissolve", 0) or 0), 4))  # overlays may differ per part
            if key != self.last_key:
                self.img = self.ren.draw(s, self.spec, self.size, aa=self.aa, transparent=self.transparent and bool(self.frames_dir))
                self.last_key = key
                if self.frames_dir:
                    imageio.imwrite(os.path.join(self.frames_dir, f"frame_{i:05d}.png"), self.img)
            if i == self.poster_i:
                imageio.imwrite(os.path.splitext(self.out)[0] + "_poster.png", self.img[..., :3])
            self.writer.write(self.img)
        except BaseException:
            self.close(); raise
        self.i += 1
        return self.i, len(self.frames), self.img

    def close(self):
        """Stop; unless the movie is finished, its partial files are deleted."""
        if not self.closed:
            self.closed = True; self.writer.abort()

    def crash(self):
        """Stop as a crash would: the complete chunks stay on disk (for tests)."""
        self.closed = True; self.writer.kill()


def movie_frames(ren, spec, out, frames_dir=None, transparent=False, poster=True, resume=False, chunk_frames=None):
    """A MovieJob: iterate it to render the movie; yields (frames done, total, last image). close() cancels."""
    return MovieJob(ren, spec, out, frames_dir, transparent, poster, resume, chunk_frames)


def render_movie(ren, spec, out, frames_dir=None, transparent=False, progress=None, cancelled=None, poster=True, resume=False):
    fps = int(spec.get("output", {}).get("fps", 30)); n = 0
    gen = movie_frames(ren, spec, out, frames_dir, transparent, poster, resume); n = len(gen)
    for i, n, img in gen:
        if progress:
            progress(i, n, img)
        if cancelled and cancelled():
            gen.close()
            return None
    return n, n / fps


def _shadow_selftest():
    """Run in a separate process: render a sphere above a plane with VTK shadow maps; exit code 0 when the shadow is
    there and VTK reported no errors. (With software rendering, shadow maps failed with shader errors.)"""
    import sys
    import tempfile
    import pyvista as pv
    import vtk
    log = tempfile.NamedTemporaryFile(suffix=".log", delete=False).name
    out = vtk.vtkFileOutputWindow(); out.SetFileName(log); vtk.vtkOutputWindow.SetInstance(out)
    pl = pv.Plotter(off_screen=True, window_size=[200, 200], lighting=None)
    pl.add_mesh(pv.Plane(i_size=10, j_size=10), color="white")
    pl.add_mesh(pv.Sphere(radius=1.5, center=(0, 0, 2)), color="white")
    lt = pv.Light(position=(0, 0, 10), focal_point=(0, 0, 0), light_type="scene light"); lt.positional = False
    lt.shadow_attenuation = 1.0; pl.add_light(lt); pl.enable_shadows()
    pl.camera_position = [(6, -6, 8), (0, 0, 0), (0, 0, 1)]
    img = pl.screenshot(None).astype(int); pl.close()
    errors = "ERROR" in open(log, errors="replace").read().upper()
    # the plane right next to the sphere's foot, where the shadow falls, must be darker than a far corner of the plane
    ok = not errors and img.sum() > 0
    sys.exit(0 if ok else 1)


def real_shadows_available(headless):
    """(True, "") when VTK shadow maps work on this computer; (False, reason) otherwise. Tested in a separate process,
    because a broken graphics driver may crash."""
    if headless:
        return False, "Not available with software rendering on this computer"
    import subprocess
    import sys
    try:
        r = subprocess.run([sys.executable, "-c", "import tomoreveal.render as r; r._shadow_selftest()"], capture_output=True, timeout=60)
    except Exception:
        return False, "The graphics card could not be tested"
    return (True, "") if r.returncode == 0 else (False, "Not available with this graphics card")
