"""The look of a frame after VTK has drawn it (3.5): soft shadows baked into the surface colours, see-through
membranes, outlines and depth fade from the depth buffer and a flat-colour pass, the vignette and the background blur.
Mixed into Renderer, which provides pl, ds, vtk, shape, size, img, cplanes, actors, comp_actors and the other actors."""
import numpy as np

from .common import hex2rgb
from .storyboard import class_spec


class PictureLooks:
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
            if getattr(act, "_ao_key", None) != key or getattr(act, "_ao_mesh", None) is not mesh:
                rgb = np.clip(f[:, None] * col[None, :] * 255.0, 0, 255).astype(np.uint8)
                mesh.point_data["ao_rgb"] = rgb; act._ao_key = key; act._ao_mesh = mesh
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
            glow = L35["outline_colour"] == "glow"
            col = np.zeros((H, W, 3), np.float32) + {"black": 0.0, "glow": 190.0}.get(L35["outline_colour"], 40.0)
            if L35["outline_colour"] in ("shade", "glow") or L35["glass"]:
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
                    elif glow:  # the structure's own colour, lighter, so the edge seems to shine
                        c = np.array(hex2rgb(class_spec(spec, self.ds, kk).get("color", self.ds.classes[kk]["color"])))
                        col[m] = (c + (1.0 - c) * 0.2) * 255
            if glow:
                alpha = np.minimum(1.0, alpha / 0.9)
            m = alpha > 0.004  # only where there is an outline
            a3 = alpha[m][:, None]
            out[..., :3][m] = out[..., :3][m] * (1 - a3) + col[m] * a3
            if transparent and out.shape[2] == 4:
                out[..., 3][m] = np.maximum(out[..., 3][m], alpha[m] * 255 / 0.9)
            if glow:  # a soft halo around each edge, added on top (light adds up, as on an X-ray screen)
                sg = 4.0 * H / 1080.0
                halo = np.stack([ndimage.gaussian_filter(alpha * col[..., i], sg) for i in range(3)], axis=-1) * 0.9
                out[..., :3] = out[..., :3] + halo
                if transparent and out.shape[2] == 4:
                    out[..., 3] = np.maximum(out[..., 3], np.clip(halo.max(axis=-1), 0, 255))
        return np.clip(np.round(out), 0, 255).astype(np.uint8)

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
