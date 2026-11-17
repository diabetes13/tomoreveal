"""Off-screen 3D drawing of one frame, and writing movies."""
import os

import numpy as np

from .common import hex2rgb
from .storyboard import Framer, build_timeline, class_spec, clip_box, state_key


def get_font(px):
    from PIL import ImageFont
    for cand in ("DejaVuSans.ttf", "Arial.ttf", "arial.ttf", "/System/Library/Fonts/Helvetica.ttc"):
        try:
            return ImageFont.truetype(cand, px)
        except OSError:
            pass
    try:
        import matplotlib
        return ImageFont.truetype(os.path.join(os.path.dirname(matplotlib.__file__), "mpl-data/fonts/ttf/DejaVuSans.ttf"), px)
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
        self.size, self.aa, self.bg, self.shown, self.peeling = (960, 540), False, None, False, False
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

    def _font(self, px):
        if px not in self._fonts:
            self._fonts[px] = get_font(px)
        return self._fonts[px]

    def draw(self, s, spec, size, aa=False, transparent=False, annotate=True):
        from PIL import Image, ImageDraw
        ds, style = self.ds, spec.get("style", {})
        size = (int(size[0]) // 2 * 2, int(size[1]) // 2 * 2)
        if size != self.size:
            self.pl.window_size = list(size); self.size = size
        if aa != self.aa:
            self.pl.enable_anti_aliasing("ssaa") if aa else self.pl.disable_anti_aliasing()
            self.aa = aa
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
            self.img.point_data["I"] = (255 - g if inv else g).ravel(); self._slice_key = key
        self.img.origin = (0, 0, float(k))
        show_slice = ds.tomo is not None and s["slice"] > 0.01
        # No depth peeling for a see-through slice: with software rendering (VTK 9.6, OSMesa) it hid the slice
        # completely in frames drawn without anti-aliasing (the preview). Plain blending looks right here.
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
        tid = ds.id_of(tgt["class"]) if tgt is not None and tgt.get("class") is not None else None
        for kk, act in self.actors.items():
            c = class_spec(spec, ds, kk)
            if not c.get("show", True):
                act.SetVisibility(False); continue
            col = hex2rgb(c.get("color", ds.classes[kk]["color"]))
            is_t = tgt is not None and tid == kk and tgt.get("component") is None
            if e <= 0 or tgt is None or mode == "keep" or is_t:
                act.SetVisibility(True)
            else:
                to = (0.27, 0.27, 0.27) if mode == "grey" else bgc
                col = tuple(a + (b - a) * e for a, b in zip(col, to))
                act.SetVisibility(not (mode == "hide" and e >= 0.999))
            act.GetProperty().SetColor(*col)
        for act in self.comp_actors.values():
            if act is not None:
                act.SetVisibility(False)
        if tid is not None and tgt.get("component") is not None and e > 0 and mode != "keep":
            act = self._comp_actor(tid, int(tgt["component"]))
            if act is not None:
                act.SetVisibility(True)
                act.GetProperty().SetColor(*hex2rgb(class_spec(spec, ds, tid).get("color", ds.classes[tid]["color"])))
        # camera
        p = s["pose"]; fr = Framer(self.shape, size[0] / size[1], float(style.get("fov", 30)), bool(style.get("ortho", False)))
        d, up = fr.axes(p["tilt"], p["az"]); cam = self.pl.camera
        cam.focal_point = tuple(p["c"]); cam.position = tuple(p["c"] + p["dist"] * d); cam.up = tuple(up)
        if fr.ortho:
            cam.parallel_projection = True; cam.parallel_scale = p["scale"]
        else:
            cam.parallel_projection = False; cam.view_angle = fr.fov
        self.pl.renderer.ResetCameraClippingRange()
        if not self.shown:
            self.pl.show(auto_close=False); self.shown = True
        self.pl.render()
        fr_img = self.pl.screenshot(None, transparent_background=bool(transparent))
        if not annotate:
            return fr_img
        # text overlays
        im = Image.fromarray(fr_img).convert("RGBA"); ov = Image.new("RGBA", im.size, (0, 0, 0, 0)); d2 = ImageDraw.Draw(ov)
        px = max(10, round(size[1] * 0.024)); font = self._font(px)
        dark = sum(bgc) < 1.5; fg = (255, 255, 255, 255) if dark else (0, 0, 0, 255); back = (0, 0, 0, 120) if dark else (255, 255, 255, 150)
        if style.get("slice_label", True) and show_slice and s["slice"] > 0.5:
            d2.text((round(px * 1.5), round(px * 1.1)), f"slice {k + 1}/{self.shape[0]}", fill=fg, font=font)
        if style.get("title"):
            d2.text((round(px * 1.5), size[1] - round(px * 2.4)), str(style["title"]), fill=fg, font=font)
        if s["caption"]:
            big = self._font(round(px * 1.4)); wt = d2.textlength(s["caption"], font=big)
            x, y = (size[0] - wt) / 2, size[1] - round(px * 3.4)
            d2.rounded_rectangle([x - px, y - px * 0.4, x + wt + px, y + px * 2.0], radius=px * 0.5, fill=back)
            d2.text((x, y), s["caption"], fill=fg, font=big)
        if style.get("legend", True) and s["reveal"]:
            rows = [(class_spec(spec, ds, kk).get("name", ds.classes[kk]["name"]), class_spec(spec, ds, kk).get("color", ds.classes[kk]["color"]))
                    for kk in sorted(self.actors) if class_spec(spec, ds, kk).get("show", True)
                    and not (tgt is not None and e > 0.5 and mode != "keep" and tid != kk)]
            if rows:
                wtxt = max(d2.textlength(n, font=font) for n, _ in rows)
                x, y = size[0] - round(wtxt + px * 3.4), round(px * 1.1)
                d2.rounded_rectangle([x - px * 0.6, y - px * 0.5, size[0] - px * 0.6, y + round(px * 1.45) * len(rows) + px * 0.2],
                                     radius=px * 0.5, fill=back)
                for n, c in rows:
                    d2.rectangle([x, y + px * 0.15, x + px * 0.95, y + px * 1.1], fill=c)
                    d2.text((x + px * 1.5, y), n, fill=fg, font=font); y += round(px * 1.45)
        im = Image.alpha_composite(im, ov)
        return np.asarray(im if transparent else im.convert("RGB"))

    def update_class(self, k):
        """Swap in the new surface of one structure (after a threshold change)."""
        a = self.actors.pop(k, None)
        if a is not None:
            self.pl.remove_actor(a)
        for key in [key for key in self.comp_actors if key[0] == k]:
            c = self.comp_actors.pop(key)
            if c is not None:
                self.pl.remove_actor(c)
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


def movie_frames(ren, spec, out, frames_dir=None, transparent=False, poster=True):
    """Render a movie frame by frame; yields (frames done, total, last image). Closing the generator stops it."""
    import imageio.v2 as imageio
    frames, _ = build_timeline(spec, ren.ds)
    o = spec.get("output", {}); size = o.get("size", [1920, 1080]); fps = int(o.get("fps", 30))
    size = (int(size[0]) // 2 * 2, int(size[1]) // 2 * 2)
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    if frames_dir:
        os.makedirs(frames_dir, exist_ok=True)
    w = imageio.get_writer(out, fps=fps, codec="libx264", quality=None, macro_block_size=2, pixelformat="yuv420p",
                           output_params=["-crf", str(int(o.get("crf", 20))), "-preset", "medium", "-movflags", "+faststart"])
    # poster: a frame from the main phase of a slide that shows structures (else an overview), never a close-up
    reveal_idx = [i for i, s in enumerate(frames) if s.get("type") == "slide" and s["reveal"] and s["phase"] == "main" and s["e"] == 0] \
        or [i for i, s in enumerate(frames) if s.get("type") == "overview" and s["phase"] == "main" and s["e"] == 0]
    poster_i = reveal_idx[len(reveal_idx) // 4] if (reveal_idx and poster) else -1
    last_key, img = None, None
    try:
        for i, s in enumerate(frames):
            key = state_key(s)
            if key != last_key:
                img = ren.draw(s, spec, size, aa=bool(o.get("aa", True)), transparent=transparent and bool(frames_dir)); last_key = key
                if frames_dir:
                    imageio.imwrite(os.path.join(frames_dir, f"frame_{i:05d}.png"), img)
            if i == poster_i:
                imageio.imwrite(os.path.splitext(out)[0] + "_poster.png", img[..., :3])
            w.append_data(np.ascontiguousarray(img[..., :3]))
            yield i + 1, len(frames), img
    finally:
        w.close()


def render_movie(ren, spec, out, frames_dir=None, transparent=False, progress=None, cancelled=None, poster=True):
    fps = int(spec.get("output", {}).get("fps", 30)); n = 0
    gen = movie_frames(ren, spec, out, frames_dir, transparent, poster)
    for i, n, img in gen:
        if progress:
            progress(i, n, img)
        if cancelled and cancelled():
            gen.close()
            return None
    return n, n / fps
