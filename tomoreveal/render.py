"""Off-screen 3D drawing of one frame (see movie.py for writing movies)."""
import glob
import os
import sys

import numpy as np

from .common import hex2rgb
from .picking import PreviewPicking
from .shadows import restore_unlit, unlit_to_ambient
from .storyboard import Framer, class_spec, clip_box, others_of, state_key, target_items


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


def software_rendering_available():
    """True where VTK's OSMesa window can start. Elsewhere (Windows, macOS, no mesalib) it crashes the
    whole process on the first draw, so headless rendering falls back to the normal off-screen window."""
    if not sys.platform.startswith("linux"):
        return False
    import vtk
    if not hasattr(vtk, "vtkOSOpenGLRenderWindow"):
        return False
    import ctypes.util
    return bool(ctypes.util.find_library("OSMesa") or glob.glob(os.path.join(sys.prefix, "lib", "libOSMesa*")))


PEELS = 10  # layers of see-through surfaces drawn correctly


def set_opacity(act, op):
    """Opacity of a structure's actor (3.5 phase 1A). Opacity 0 hides it, so its outline (which follows the actor's
    visibility and opacity) and its shadow go too. A see-through closed surface would show its near and its far wall,
    so 40 % would look like 64 %: see-through surfaces draw only the walls that face the camera. Our surfaces are wound
    inwards, so those are the faces VTK calls back faces, and the front faces are culled."""
    op = float(min(1.0, max(0.0, op)))
    pr = act.GetProperty(); pr.SetOpacity(op); pr.SetFrontfaceCulling(op < 0.999)
    if op <= 0.002:
        act.SetVisibility(False)


class Renderer(PreviewPicking):
    def __init__(self, ds, headless=False):
        if headless and software_rendering_available():
            os.environ["VTK_DEFAULT_OPENGL_WINDOW"] = "vtkOSOpenGLRenderWindow"
        import pyvista as pv, vtk
        self.pv, self.vtk, self.ds = pv, vtk, ds
        self.size, self.aa, self.bg, self.shown, self.peeling = (960, 540), "off", None, False, False
        self._light_key, self._ssao, self._real_shadows, self.real_shadows_ok = ("default",), False, False, False
        self.sil_actors, self._dots = {}, {}  # cartoon outlines and thinned-out meshes for dots, per structure
        self.glow_actors, self.cut_actors, self._cut_cache, self._fx_meshes = {}, {}, {}, {}  # close-up animations
        self._markers, self._path_actors, self.preview_part, self._path3d, self.path_view = [], [], None, None, None
        self._slice_grey, self._shadow_key = None, None
        self.pl = pv.Plotter(off_screen=True, window_size=list(self.size))
        self._ms_aa = self.pl.ren_win.GetMultiSamples()  # multisampling wanted by the anti-aliasing mode (off while peeling)
        self.shape = ds.shape; nz, ny, nx = self.shape
        self.img = pv.ImageData(dimensions=(nx, ny, 1), spacing=(1, 1, 1), origin=(0, 0, 0))
        self.img.point_data["I"] = np.zeros(nx * ny, np.uint8); self._slice_key = None
        self.slice_actor = self.pl.add_mesh(self.img, scalars="I", cmap="gray", clim=(0, 255), lighting=False,
                                            show_scalar_bar=False, pickable=False)
        self.plane = vtk.vtkPlane(); self.plane.SetNormal(0, 0, 1)
        # clip box: x-, x+, y-, y+, z+ (the reveal plane is never below the lower z face); that is the 6 OpenGL allows
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
        if s.get("blend"):  # a dissolve or a fade through black / white between two parts (3.5)
            b = s["blend"]; q = float(b["q"])
            a_img = self.draw(dict(b["src"], blend=None), spec, size, aa, transparent, annotate, preview).astype(np.float32)
            b_img = self.draw(dict(s, blend=None), spec, size, aa, transparent, annotate, preview).astype(np.float32)
            if b["via"] in ("black", "white"):
                tone = np.zeros_like(a_img) if b["via"] == "black" else np.full_like(a_img, 255.0)
                if transparent and a_img.shape[2] == 4:
                    tone[..., 3] = 255.0
                out = a_img + (tone - a_img) * min(1.0, 2 * q) if q < 0.5 else tone + (b_img - tone) * min(1.0, 2 * q - 1)
            else:
                out = a_img * (1 - q) + b_img * q
            return np.clip(np.round(out), 0, 255).astype(np.uint8)
        if s.get("lkfrom") is not None and float(s.get("lkq", 1.0)) < 1.0:  # a part with other effects begins (3.5)
            q = float(s["lkq"])
            a_img = self.draw(dict(s, lk=s["lkfrom"], lkfrom=None), spec, size, aa, transparent, annotate, preview).astype(np.float32)
            b_img = self.draw(dict(s, lkfrom=None), spec, size, aa, transparent, annotate, preview).astype(np.float32)
            return np.clip(np.round(a_img * (1 - q) + b_img * q), 0, 255).astype(np.uint8)
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
            self.aa = mode; self._ms_aa = self.pl.ren_win.GetMultiSamples()
        from .look import GRADIENT_TOP, LOOK_PRESETS, style35
        L35 = style35(spec)
        own = s.get("lk") or {}
        if own:  # this part's own effects (3.5)
            L35 = dict(L35, **{k: v for k, v in own.items() if k in ("shadow", "outline", "depth_fade")})
            if "membranes" in own:
                from .look import CREAM
                L35.update(glass=own["membranes"] == "glass", membrane_colour=CREAM if own["membranes"] == "cream" else None)
            if "tomo_shadow" in own:
                lk["shadows"]["slice"]["on"] = bool(own["tomo_shadow"])
        self._l35 = L35
        bg = style.get("bg", "#000000"); bgkey = (bg, L35["background"] == "gradient")
        if bgkey != self.bg:
            if bgkey[1]:  # light grey at the top to white at the bottom
                self.pl.set_background(hex2rgb(bg), top=hex2rgb(GRADIENT_TOP))
            else:
                self.pl.set_background(hex2rgb(bg))
            self.bg = bgkey
        bgc = hex2rgb(bg)
        light_bg = sum(bgc) > 1.5
        del LOOK_PRESETS
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
        cb = clip_box(spec, self.shape); far = 1e9
        (x0, y0, z0), (x1, y1, z1) = cb if cb else ((-far, -far, -far), (far, far, far))
        self.plane.SetOrigin(0, 0, max({"above": k + 0.5, "all": -10.0}.get(cut, self.shape[0] + 10), z0 - 0.5))
        for p, o in zip(self.cplanes, ((x0 - 0.5, 0, 0), (x1 + 0.5, 0, 0), (0, y0 - 0.5, 0), (0, y1 + 0.5, 0), (0, 0, z1 + 0.5))):
            p.SetOrigin(*o)
        # structures
        e, tgt, mode = float(s["e"]), s["target"], s["others"]
        items = self._focus_items(tgt); whole = {k for k, c in items if c is None}
        legacy = not isinstance(mode, dict)  # older settings (grey / hide / keep) are drawn exactly as before
        ocol, oop = others_of(mode)
        keep = (mode == "keep") if legacy else (ocol == "keep" and oop >= 0.999)
        each = (mode.get("each") or {}) if not legacy else {}  # per structure in this close-up (3.5): glass / hide
        all_op = float(min(1.0, max(0.0, style.get("structures_opacity", 1.0))))
        translucent = False; self._legend_col = {}
        for kk, act in self.actors.items():
            c = class_spec(spec, ds, kk)
            if not c.get("show", True):
                act.SetVisibility(False); continue
            op = float(min(1.0, max(0.0, c.get("opacity", 1.0)))) * all_op
            col = hex2rgb(c.get("color", ds.classes[kk]["color"]))
            mem = (L35["glass"] or L35["membrane_colour"]) and self._membrane(spec, kk)
            if L35["glass"] and mem:  # glass membranes (3.5): pale and see-through
                col = hex2rgb(L35["glass_colour"]); op *= float(L35["glass_opacity"])
            elif L35["membrane_colour"] and mem:  # solid membranes in one colour (Ceramic: cream)
                col = hex2rgb(L35["membrane_colour"])
            elif L35["richer"] and light_bg:
                from .look import richer
                col = richer(col)
            if L35["pastel"] and not mem:
                from .look import pastel
                col = pastel(col)
            self._legend_col[kk] = col  # the colour the legend shows: as drawn by the look
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
            ch = each.get(ds.classes[kk]["key"]) if e > 0 and each else None
            if ch == "hide":  # hidden in this close-up, faded out with e
                op *= 1.0 - e; act.SetVisibility(op > 0.002)
            elif ch == "glass":  # see-through in this close-up, like the glass membranes
                to = hex2rgb(L35["glass_colour"]); col = tuple(a + (b - a) * e for a, b in zip(col, to))
                op *= 1.0 - e * (1.0 - float(L35["glass_opacity"]))
            set_opacity(act, op); translucent |= op < 0.999 and act.GetVisibility()
            act.GetProperty().SetColor(*col)
        translucent |= self._apply_styles(spec, lk, all_op)
        self._apply_baked_shadows(spec, L35)
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
                set_opacity(act, op); translucent |= op < 0.999 and act.GetVisibility()
        translucent |= self._apply_fx(s.get("fx") or {}, items, spec, e, keep, all_op)
        # Depth peeling only while some structure is see-through and the slice is opaque or hidden: with OSMesa (VTK 9.6)
        # peeling plus a see-through slice gives an all-black frame when drawn without anti-aliasing (3.2 and 3.4 pitfall).
        translucent = translucent and not (show_slice and float(s["slice"]) < 0.999)
        self._set_peeling(translucent)
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
            cam.parallel_projection = False; cam.view_angle = float(p.get("fov", fr.fov))  # 3.5: per point of a camera path
        self._show_path(s, spec, preview)
        self._apply_lighting(lk)
        self._apply_slice_texture(s, spec, lk, show_slice, cb)
        self._vig = self._vignette_state(s, spec, lk, size)
        self._apply_blur(self._vig)
        self.pl.renderer.ResetCameraClippingRange()
        self._bar_label = self._prepare_slice_bar(s, spec, size, show_slice) if annotate else None
        if not self.shown:
            self.pl.show(auto_close=False); self.shown = True
        lit = unlit_to_ambient(self.pl.renderer) if self._real_shadows else []
        try:
            self.pl.render()
            fr_img = self.pl.screenshot(None, transparent_background=bool(transparent))
            blur_zb = None
            if self._vig is not None and self._vig["blur"] > 0.001:  # before the outline pass draws over the depth buffer
                from vtkmodules.util.numpy_support import vtk_to_numpy
                W_, H_ = size; arr = self.vtk.vtkFloatArray(); self.pl.ren_win.GetZbufferData(0, 0, W_ - 1, H_ - 1, arr)
                blur_zb = vtk_to_numpy(arr).reshape(H_, W_)[::-1].copy()
        finally:
            restore_unlit(lit)
        self._last_id = None
        if L35["outline"] != "off" or L35["depth_fade"] != "off":  # 3.5 looks: outlines and depth fade, from the depth buffer
            fr_img = self._outlines_and_fade(fr_img, s, spec, size, L35, transparent)
        if blur_zb is not None:
            fr_img = self._background_blur(fr_img, self._vig, blur_zb, size)
        if self._vig is not None and self._vig["a"] > 0.001:
            fr_img = self._apply_vignette(fr_img, self._vig, transparent)
        if not annotate:
            return fr_img
        img = self.annotate(fr_img, s, spec, size, show_slice, transparent)
        if self._path3d:
            self._project_path(size)
        return img

    # ---------- 3.5 looks: baked soft shadows, see-through membranes, outlines, depth fade ----------
    def _membrane(self, spec, kk):
        from .look import is_membrane
        c = self.ds.classes[kk]
        return is_membrane(spec, c["key"], class_spec(spec, self.ds, kk).get("name", c["name"]))

    def _apply_baked_shadows(self, spec, L35):
        """Soft shadows baked into the vertex colours (the colour each actor has now, times the shadow factor)."""
        from .look import SHADOW_DARKEST, BakedShadows
        on = L35["shadow"] != "none"
        acts = [(kk, act, self.ds.meshes.get(kk)) for kk, act in self.actors.items()]
        if not on:
            for _, act, _ in acts:
                if act.mapper.GetScalarVisibility():
                    act.mapper.ScalarVisibilityOff()
            return
        if not hasattr(self, "_ao"):
            self._ao = BakedShadows()
        shown = [kk for kk, act, _ in acts if act.GetVisibility()]
        self._ao.prepare(self.ds, shown, L35["reach"])  # hidden structures cast no shadows
        dark = SHADOW_DARKEST[L35["shadow"]]
        for kk, act, mesh in acts:
            if not act.GetVisibility() or mesh is None or getattr(act, "_shown_mesh", mesh) is not mesh or \
                    class_spec(spec, self.ds, kk).get("no_shadow"):  # this structure without soft shadows (Structures tab)
                act.mapper.ScalarVisibilityOff(); continue
            f = self._ao.factor(mesh, dark)
            col = np.array(act.GetProperty().GetColor(), np.float32)
            key = (id(mesh), tuple(np.round(col, 4)), id(f))
            if getattr(act, "_ao_key", None) != key:
                rgb = np.clip(f[:, None] * col[None, :] * 255.0, 0, 255).astype(np.uint8)
                mesh.point_data["ao_rgb"] = rgb; act._ao_key = key
                m = act.mapper; m.SetInputData(mesh); m.SetScalarModeToUsePointFieldData(); m.SelectColorArray("ao_rgb")
                m.SetColorModeToDirectScalars()
            act.mapper.ScalarVisibilityOn()

    def _slice_depth(self, W, H):
        """Distance from the camera (along its view direction) of the tomogram slice at every pixel (rows from the top),
        inf where the slice is not. Worked out from the camera, so it is right also while the slice is see-through."""
        cam = self.pl.renderer.GetActiveCamera()
        M = cam.GetCompositeProjectionTransformMatrix(W / H, -1, 1)
        inv = np.linalg.inv(np.array([[M.GetElement(i, j) for j in range(4)] for i in range(4)]))
        xs = (np.arange(W) + 0.5) / W * 2 - 1; ys = 1 - (np.arange(H) + 0.5) / H * 2
        X, Y = np.meshgrid(xs, ys)
        def unproject(zn):
            p = np.stack([X, Y, np.full_like(X, zn), np.ones_like(X)], -1) @ inv.T
            return p[..., :3] / p[..., 3:4]
        a, b = unproject(-1.0), unproject(1.0)
        z = float(self.img.origin[2]); dz = b[..., 2] - a[..., 2]
        with np.errstate(divide="ignore", invalid="ignore"):
            t = (z - a[..., 2]) / dz
        P = a + (b - a) * t[..., None]
        nz, ny, nx = self.shape
        xl, xh = max(0.0, self.cplanes[0].GetOrigin()[0]), min(nx - 1.0, self.cplanes[1].GetOrigin()[0])
        yl, yh = max(0.0, self.cplanes[2].GetOrigin()[1]), min(ny - 1.0, self.cplanes[3].GetOrigin()[1])
        inside = np.isfinite(t) & (t >= 0) & (t <= 1) & (P[..., 0] >= xl) & (P[..., 0] <= xh) & (P[..., 1] >= yl) & (P[..., 1] <= yh)
        pos = np.array(cam.GetPosition()); d = np.array(cam.GetDirectionOfProjection())
        depth = (P - pos) @ d
        return np.where(inside, depth, np.inf)

    def _id_pass(self, size):
        """Draw every shown structure in a flat colour of its own, opaque, without the slice: (RGB image, depth buffer
        (rows from the top), {id colour: structure id}). For outlines between touching structures and of see-through ones."""
        from vtkmodules.util.numpy_support import vtk_to_numpy
        saved = []; W, H = size; ids = {}; self._id_actors = {}
        objs = [(kk, a) for kk, a in self.actors.items() if a.GetVisibility()] + \
               [(k[0], a) for k, a in self.comp_actors.items() if a is not None and a.GetVisibility()]
        others = [self.slice_actor, self.bar_actor, *self.sil_actors.values(), *self.glow_actors.values(), *self.cut_actors.values()]
        for a in others:
            if a is not None:
                saved.append(("vis", a, a.GetVisibility())); a.SetVisibility(False)
        for i, (kk, a) in enumerate(objs, start=1):
            pr = a.GetProperty()
            saved.append(("prop", a, (pr.GetColor(), pr.GetLighting(), pr.GetOpacity(), a.mapper.GetScalarVisibility(), pr.GetAmbient(),
                                      pr.GetDiffuse(), pr.GetSpecular(), pr.GetFrontfaceCulling(), pr.GetRepresentation())))
            c = (40 * (i % 6) + 30, 40 * ((i // 6) % 6) + 30, 40 * ((i // 36) % 6) + 30)  # well apart, so edges are clear
            ids[c] = kk; self._id_actors[c] = a
            pr.SetColor(c[0] / 255, c[1] / 255, c[2] / 255); pr.SetLighting(False); pr.SetOpacity(1.0); a.mapper.ScalarVisibilityOff()
            pr.SetAmbient(1.0); pr.SetDiffuse(0.0); pr.SetSpecular(0.0); pr.SetFrontfaceCulling(False)
        peel = self.peeling; self._set_peeling(False)
        self.pl.set_background((0, 0, 0)); self.bg = None  # set again by the next frame
        # Plain drawing for this pass: without the shadow, SSAO and blur passes. Real shadows with these unlit flat
        # colours gave "Could not set shader program" errors on NVIDIA (see shadows.unlit_to_ambient), and SSAO would
        # darken the colours that name each structure.
        ren = self.pl.renderer; passes = ren.GetPass(); ren.SetPass(None)
        try:
            self.pl.render(); img = self.pl.screenshot(None)
            arr = self.vtk.vtkFloatArray(); self.pl.ren_win.GetZbufferData(0, 0, W - 1, H - 1, arr)
            zb = vtk_to_numpy(arr).reshape(H, W)[::-1].copy()
        finally:
            for kind, a, v in saved:
                if kind == "vis":
                    a.SetVisibility(v)
                else:
                    pr = a.GetProperty(); col, lit, op, sv, amb, dif, spc, cull, rep = v
                    pr.SetColor(*col); pr.SetLighting(lit); pr.SetOpacity(op); a.mapper.SetScalarVisibility(sv)
                    pr.SetAmbient(amb); pr.SetDiffuse(dif); pr.SetSpecular(spc); pr.SetFrontfaceCulling(cull); pr.SetRepresentation(rep)
            ren.SetPass(passes)
            self._set_peeling(peel)
        self._last_id = (img[..., :3], zb)
        return img[..., :3], zb, ids

    def _outlines_and_fade(self, img, s, spec, size, L35, transparent):
        from scipy import ndimage
        from vtkmodules.util.numpy_support import vtk_to_numpy
        from .look import FADE_AMOUNT, OUTLINE_PX
        W, H = size; out = img.astype(np.float32)
        cam = self.pl.renderer.GetActiveCamera(); n, f = cam.GetClippingRange(); ortho = cam.GetParallelProjection()

        def linear(zb):
            if ortho:
                return n + zb * (f - n)
            zn = 2.0 * zb - 1.0
            return 2.0 * n * f / np.maximum(1e-9, f + n - zn * (f - n))
        bgc = np.array(hex2rgb(spec.get("style", {}).get("bg", "#000000"))) * 255
        if L35["depth_fade"] != "off":  # far things fade towards the background (towards white over the slice)
            arr = self.vtk.vtkFloatArray(); self.pl.ren_win.GetZbufferData(0, 0, W - 1, H - 1, arr)
            zb = vtk_to_numpy(arr).reshape(H, W)[::-1]; fg = zb < 0.99999; lin = linear(zb)
            amount = FADE_AMOUNT[L35["depth_fade"]]

            def fade_of(lin, fg):
                near, far = s.get("dr") or (float(np.min(lin[fg])) if fg.any() else n, float(np.max(lin[fg])) if fg.any() else f)
                t = np.clip((lin - (near + 0.4 * (far - near))) / max(1e-6, 0.6 * (far - near)), 0, 1) * amount
                t[~fg] = 0
                return t
            op = float(s.get("slice", 0)) if self.slice_actor.GetVisibility() else 0.0
            if 0.0 < op < 1.0:
                # A see-through slice is not in the depth buffer: without this the far slice lost its fade (and the picture
                # jumped) the moment the slice began to fade out. Its depth is worked out here instead, and the fade
                # goes smoothly from "with the slice" (opaque) to "without it" (gone).
                sd = self._slice_depth(W, H)
                ok = np.isfinite(sd) & (~fg | (sd < lin))
                t = op * fade_of(np.where(ok, sd, lin), fg | ok) + (1 - op) * fade_of(lin, fg)
            else:
                t = fade_of(lin, fg)
            m = t > 0.002
            to = np.array([255.0, 255.0, 255.0]) if L35["background"] == "tomogram" else bgc
            out[..., :3][m] = out[..., :3][m] * (1 - t[m][:, None]) + to * t[m][:, None]
        if L35["outline"] != "off":
            idimg, zb, ids = self._id_pass(size)
            bg = zb >= 0.99999; lin = np.where(bg, f * 4.0, linear(zb))
            thr = 0.012 if float(s.get("e", 0)) > 0.5 else 0.02
            from .look import outline_edges
            edge = outline_edges(lin, bg, idimg if L35["between"] else None, thr)
            plain = [c[0] * 65536 + c[1] * 256 + c[2] for c, kk in ids.items() if class_spec(spec, self.ds, kk).get("no_outline")]
            if plain:  # structures drawn without an outline (Structures tab): no edge on them
                code = idimg[..., 0].astype(np.int64) * 65536 + idimg[..., 1].astype(np.int64) * 256 + idimg[..., 2]
                edge &= ~np.isin(code, plain)
            w = OUTLINE_PX[L35["outline"]] * H / 1080.0
            it = int(max(0, round((w - 1.0) / 2)))
            if it:
                edge = ndimage.binary_dilation(edge, iterations=it)
            if L35["glass"] and L35["glass_outline"] != "all":  # see-through membranes: only their outer edge, or none
                key0 = idimg[..., 0].astype(np.int64) * 65536 + idimg[..., 1].astype(np.int64) * 256 + idimg[..., 2]
                glass_ids = [c[0] * 65536 + c[1] * 256 + c[2] for c, kk in ids.items() if self._membrane(spec, kk)]
                if glass_ids:
                    gm = np.isin(key0, glass_ids) & ~bg
                    if L35["glass_outline"] == "none":
                        edge &= ~ndimage.binary_dilation(gm, iterations=2)
                    else:  # keep the edge where a membrane meets something else; drop its folds and inner walls
                        edge &= ~ndimage.binary_erosion(gm, iterations=2)
            alpha = np.clip(ndimage.gaussian_filter(edge.astype(np.float32), 0.5) * 1.6, 0, 1) * 0.9
            col = np.zeros((H, W, 3), np.float32) + (np.array([0, 0, 0]) if L35["outline_colour"] == "black" else np.array([40, 40, 40]))
            if L35["outline_colour"] == "shade" or L35["glass"]:
                key = idimg[..., 0].astype(np.int64) * 65536 + idimg[..., 1].astype(np.int64) * 256 + idimg[..., 2]
                lut = {c[0] * 65536 + c[1] * 256 + c[2]: kk for c, kk in ids.items()}
                near_id = ndimage.grey_dilation(np.where(bg, 0, key), size=3)  # the structure next to each outline pixel
                for code, kk in lut.items():
                    m = near_id == code
                    if not m.any():
                        continue
                    if L35["glass"] and self._membrane(spec, kk):  # see-through membranes: a lighter, warm outline
                        alpha[m] *= 0.55; col[m] = (110, 100, 85)
                    elif L35["outline_colour"] == "shade":
                        c = class_spec(spec, self.ds, kk).get("color", self.ds.classes[kk]["color"])
                        col[m] = np.array(hex2rgb(c)) * 255 * 0.45
            m = alpha > 0.004  # only where there is an outline
            a3 = alpha[m][:, None]
            out[..., :3][m] = out[..., :3][m] * (1 - a3) + col[m] * a3
            if transparent and out.shape[2] == 4:
                out[..., 3][m] = np.maximum(out[..., 3][m], alpha[m] * 255 / 0.9)
        return np.clip(np.round(out), 0, 255).astype(np.uint8)

    def _set_peeling(self, on):
        """Depth peeling for see-through structures, set up by hand (3.5 phase 1A). PyVista's enable_depth_peeling()
        leaves multisampling on, and peeling with multisampling drew every see-through mesh invisible (OSMesa, VTK 9.6):
        so multisampling is off while peeling, and back to what the anti-aliasing mode wants afterwards."""
        rw, ren = self.pl.ren_win, self.pl.renderer
        rw.SetMultiSamples(0 if on else self._ms_aa)
        if on == self.peeling:
            return
        if on:
            rw.SetAlphaBitPlanes(1)
            ren.SetUseDepthPeeling(1); ren.SetMaximumNumberOfPeels(PEELS); ren.SetOcclusionRatio(0.0)
        else:
            ren.SetUseDepthPeeling(0)
        self.peeling = on

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
        # background blur: in close-ups only, whatever the vignette does (the close-up's own choice, else the Look tab's)
        own_blur = (own.get("blur") or {}).get("on") if own is not None and "blur" in own else None
        fb = e if in_close and (lv["blur"].get("on") if own_blur is None else own_blur) else 0.0
        if f <= 0 and fb <= 0:
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
        blur = float(cfg["blur"].get("strength", 0.5)) * fb
        return dict(a=float(cfg["strength"]) * max(0.0, f), cx=cx, cy=cy, size=float(cfg["size"]), soft=float(cfg["softness"]),
                    shape=cfg.get("shape", "round"), colour=col, blur=blur, target=s["target"])

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
        """Before drawing: VTK's own depth of field is never used (it kept only a thin slab sharp, so it blurred
        nearly everything); see `_background_blur`."""
        if getattr(self, "_dof", False):
            self.pl.disable_depth_of_field(); self._dof = False

    def _background_blur(self, img, v, zb, size):
        """Background blur in close-ups (3.5): the objects in focus stay sharp and everything else (the tomogram, the
        other structures, the background) is blurred: a little where it is as far from the camera as the objects, fully
        where it is clearly in front of them or behind them. Sharp pixels are left out of the blur, so no halo spreads
        around the objects."""
        from scipy import ndimage
        H, W = img.shape[:2]
        cam = self.pl.renderer.GetActiveCamera(); n, f = cam.GetClippingRange(); ortho = cam.GetParallelProjection()
        if ortho:
            lin = n + zb * (f - n)
        else:
            lin = 2.0 * n * f / np.maximum(1e-9, f + n - (2.0 * zb - 1.0) * (f - n))
        lin = np.where(zb >= 0.99999, np.inf, lin)
        pos = np.array(cam.GetPosition()); d = np.array(cam.GetDirectionOfProjection())
        try:
            lo, hi = self.ds.targets_box(v["target"], pad=False)
            corners = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])], float)
            dep = (corners - pos) @ d; near, far = float(dep.min()), float(dep.max())
        except (KeyError, IndexError, TypeError, ValueError):
            near = far = float(np.linalg.norm(np.array(cam.GetFocalPoint()) - pos))
        span = max(0.25 * (far - near), 0.03 * max(far, 1e-6))
        wd = np.clip(np.maximum(lin - far, near - lin) / span, 0, 1)  # 0 at the objects' depth .. 1 well away from it
        # the objects in focus: their pixels in the flat-colour pass (the outline pass may have drawn it already)
        if getattr(self, "_last_id", None) is None:
            self._id_pass(size)
        idimg = self._last_id[0]
        want = set()
        for k, comp in self._focus_items(v["target"]):
            a = None if comp is None else self.comp_actors.get((k, int(comp)))
            if a is None or not a.GetVisibility():  # a whole structure, or its objects drawn by the structure itself ("keep")
                a = self.actors.get(k)
            want.update(c for c, act in self._id_actors.items() if act is a)
        sharp = np.zeros((H, W), bool)
        for c in want:
            sharp |= (idimg[..., 0] == c[0]) & (idimg[..., 1] == c[1]) & (idimg[..., 2] == c[2])
        w = np.where(sharp, 0.0, 0.55 + 0.45 * wd).astype(np.float32)
        w = ndimage.gaussian_filter(w, 0.8 * H / 1080.0 + 0.4)
        sigma = (2.0 + 16.0 * float(v["blur"])) * H / 1080.0
        out = img.astype(np.float32)
        keep = (~sharp).astype(np.float32); wt = ndimage.gaussian_filter(keep, sigma)
        for c in range(out.shape[2]):
            bl = ndimage.gaussian_filter(out[..., c] * keep, sigma) / np.maximum(wt, 1e-4)
            out[..., c] = out[..., c] * (1 - w) + bl * w
        return np.clip(np.round(out), 0, 255).astype(np.uint8)

    # ---------- camera paths: drawn in the preview only ----------
    def _show_path(self, s, spec, preview):
        """The selected camera path part, for the preview only (3.5: drawn in 2D by the window, never in a movie):
        3D lines and points here; projected after the frame is drawn (`_project_path`) into self.path_view."""
        self._markers = []; self._path3d = None; self.path_view = None
        for a in getattr(self, "_path_actors", []):
            self.pl.renderer.RemoveActor(a)
        self._path_actors = []
        sel = getattr(self, "preview_part", None)
        part = self._part(spec, sel) if preview and sel else None
        if not part or part.get("type") != "path" or not part.get("show_path_in_preview", True):
            return
        from .paths import PATH_CAMERA, PathPending, catmull_rom, follow_rails, path_line, point_values
        src = part.get("source") or {}; lines = []; marks = []
        try:
            if src.get("kind", "follow") == "follow":
                items = [(it.get("structure"), it.get("component")) for it in src.get("items", [])]
                line = path_line(self.ds, items, clip_box(spec, self.shape)) if items else None
                if line is not None and part.get("reverse"):
                    line = line[::-1]
                if line is not None and part.get("steadiness") is not None:
                    v = spec.get("view", {}); fr = Framer(self.shape, 16 / 9, 30.0)
                    md, _ = fr.axes(float(v.get("tilt", 42)), float(v.get("az", 0)))
                    crail, look = follow_rails(part, line, dict(PATH_CAMERA, **(part.get("camera") or {}), _main_dir=list(md)))
                    lines = [("rail", crail), ("look", look)]
                elif line is not None:
                    lines = [("path", line)]
            else:
                wps = part.get("waypoints") or []
                v = spec.get("view", {}); fr = Framer(self.shape, 16 / 9, 30.0)
                md, _ = fr.axes(float(v.get("tilt", 42)), float(v.get("az", 0)))
                D = 60.0 * float(dict(PATH_CAMERA, **(part.get("camera") or {}))["distance"])
                zooms, _ = point_values(part, "zoom", 1.0) if part.get("interp") == 2 else ([1.0] * len(wps), None)
                cams, looks, raws = [], [], []
                for w, z in zip(wps, zooms):
                    F = np.asarray(w.get("focal") or w.get("point") or w.get("pos"), float)
                    P = np.asarray(w["pos"], float) if w.get("pos") is not None else F + D * np.asarray(md, float)
                    cams.append(F + (P - F) / max(1e-3, z)); looks.append(F); raws.append(P)
                marks = list(zip(range(len(wps)), cams, looks))
                if len(cams) >= 2:
                    lines = [("path", catmull_rom(cams)[0])]
        except (PathPending, Exception):
            lines, marks = [], []
        self._path3d = dict(lines=lines, marks=marks, raw=locals().get("raws", []))
        self._markers = [(i + 1, f) for i, _, f in marks]

    def _project_path(self, size):
        """Project the path of `_show_path` onto the frame just drawn, with what is hidden behind structures or the
        slice (from the depth buffer): self.path_view, and the point boxes in last_rects ("marker:i", "look:i")."""
        if not self._path3d:
            return
        W, H = size; ren = self.pl.renderer
        from vtkmodules.util.numpy_support import vtk_to_numpy
        arr = self.vtk.vtkFloatArray(); self.pl.ren_win.GetZbufferData(0, 0, W - 1, H - 1, arr)
        zb = vtk_to_numpy(arr).reshape(H, W)

        def proj(P):
            ren.SetWorldPoint(*[float(v) for v in P], 1.0); ren.WorldToDisplay(); x, y, z = ren.GetDisplayPoint()
            xi, yi = int(round(x)), int(round(y))
            inside = 0 <= xi < W and 0 <= yi < H and 0 <= z <= 1
            vis = bool(inside and z <= zb[yi, xi] + 2e-4)
            return (x / W, 1 - y / H, vis, 0 <= z <= 1)
        lines = []
        for kind, pts in self._path3d["lines"]:
            step = max(1, len(pts) // 400)
            lines.append(dict(kind=kind, pts=[proj(P) for P in pts[::step]] + [proj(pts[-1])]))
        marks = []; n = len(self._path3d["marks"])
        for i, P, F in self._path3d["marks"]:
            c, l = proj(P), proj(F)
            marks.append(dict(i=i, cam=c, look=l, kind="start" if i == 0 else "end" if i == n - 1 else "mid"))
            r = 11
            if c[3]:
                self.last_rects[f"marker:{i}"] = dict(box=(c[0] * W - r, c[1] * H - r, c[0] * W + r, c[1] * H + r), shown=True)
            if l[3]:
                self.last_rects[f"look:{i}"] = dict(box=(l[0] * W - 7, l[1] * H - 7, l[0] * W + 7, l[1] * H + 7), shown=True)
        self.path_view = dict(lines=lines, marks=marks)

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
                pr.SetOpacity(pr.GetOpacity() * float(fx["fade"])); pr.SetFrontfaceCulling(pr.GetOpacity() < 0.999); act.SetVisibility(act.GetVisibility() and fx["fade"] > 0.002)
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
               bool(lk["shadows"]["real"]["on"] and self.real_shadows_ok), round(float(lk["shadows"]["real"]["strength"]), 3),
               bool(getattr(self, "_l35", {}).get("lighting")))
        if key == self._light_key:
            return
        pv = self.pv; pl = self.pl
        pl.remove_all_lights()
        want_ssao = pre in ("soft", "full")
        if pre == "default" and key[-1]:  # 3.5 looks: a head light (70 %) and a fill light from the upper left (30 %)
            for pos, amount in (((0.0, 0.0, 1.0), 0.7), ((-1.0, 1.0, 1.0), 0.3)):
                lt = pv.Light(position=pos, focal_point=(0, 0, 0), light_type="camera light", intensity=amount); lt.positional = False
                pl.add_light(lt)
        elif pre == "default":
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
                if sign > 0 and key[8]:
                    lt.shadow_attenuation = float(lk["shadows"]["real"]["strength"])
                pl.add_light(lt)
        ssao = key[6:8] if want_ssao else False
        real = key[8]
        if ssao != self._ssao:
            if self._ssao:
                pl.disable_ssao()
            if want_ssao:
                pl.enable_ssao(radius=float(L["ssao_radius"]) * max(self.shape), bias=0.01, kernel_size=64, blur=True)
            self._ssao = ssao
        if real != self._real_shadows:
            pl.enable_shadows() if real else pl.disable_shadows()
            self._real_shadows = real
        self._light_key = key

    def _apply_styles(self, spec, lk, all_op):
        """Surface style (normal / cartoon / dots / glossy) and lighting amounts of every structure actor."""
        from .look import GLOSS, style_of
        L = lk["lighting"]; pre = L.get("preset", "default"); ds = self.ds
        amb, spec_amt = (0.25, 0.2) if pre == "default" else (float(L["ambient"]), float(L["specular"]))
        matte = pre == "default" and bool(getattr(self, "_l35", {}).get("lighting"))  # 3.5 looks: a matte surface
        if matte:
            amb, spec_amt = 0.45, 0.04
        ceramic = pre == "default" and getattr(self, "_l35", {}).get("material") == "ceramic"
        glass = False
        for kk, act in self.actors.items():
            st = style_of(spec, ds.classes[kk]["key"]); pr = act.GetProperty(); kind = st["kind"]
            pr.SetRepresentationToSurface(); pr.SetInterpolationToPhong(); pr.SetMetallic(0.0); pr.SetRoughness(0.5)
            pr.RenderPointsAsSpheresOff(); pr.SetLighting(pre != "flat")
            pr.SetAmbient(amb); pr.SetDiffuse(0.6 if matte else 0.8); pr.SetSpecular(spec_amt); pr.SetSpecularPower(15)
            if ceramic:  # a porcelain model: bright, matte, with a broad soft sheen
                pr.SetAmbient(0.5); pr.SetDiffuse(0.62); pr.SetSpecular(0.12); pr.SetSpecularPower(6)
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
            pr.SetFrontfaceCulling(pr.GetOpacity() < 0.999)  # see-through: one layer per wall (see set_opacity)
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
        def legend_colour(kk):
            c = getattr(self, "_legend_col", {}).get(kk)
            return "#%02x%02x%02x" % tuple(int(round(255 * v)) for v in c) if c is not None else \
                class_spec(spec, ds, kk).get("color", ds.classes[kk]["color"])
        rows = [(class_spec(spec, ds, kk).get("name", ds.classes[kk]["name"]), legend_colour(kk))
                for kk in sorted(self.actors) if class_spec(spec, ds, kk).get("show", True)
                and not (focused and e > 0.5 and not keep and kk not in focused)
                and not (e > 0.5 and isinstance(mode, dict) and (mode.get("each") or {}).get(ds.classes[kk]["key"]) == "hide")]
        P.legend(items["legend"], rows, shown("legend", bool(s["reveal"])))
        self._scale_bar(P, items["scale_bar"], s, spec, size, vox, shown("scale_bar", True))
        P.caption(items["caption"], s["caption"] or "", shown("caption", True), float(s.get("cap_a", 1.0)))
        if s.get("ann"):  # arrows and text boxes (3.5), on top of everything
            from vtkmodules.util.numpy_support import vtk_to_numpy
            from .annotations import draw as draw_annotations
            arr = self.vtk.vtkFloatArray(); self.pl.ren_win.GetZbufferData(0, 0, W - 1, H - 1, arr)
            draw_annotations(layer, P, self, s, spec, size, vtk_to_numpy(arr).reshape(H, W))  # rows from the bottom
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

    def close(self):
        try:
            self.pl.close()
        except Exception:
            pass
