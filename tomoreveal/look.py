"""Lighting, shadows, anti-aliasing and surface styles: settings with defaults, and the geometry they need.

spec["look"] = {"lighting": {...}, "shadows": {"slice": {...}, "real": {...}}, "aa": {"preview", "movie"}}
spec["classes"][key]["style"] = {"kind": "normal" | "cartoon" | "dots" | "glossy", ...}
The "default" lighting preset is the lighting of every earlier version, so old movies look the same.
"""
import copy
import math

import numpy as np

PRESETS = (("Default", "default"), ("Simple", "simple"), ("Soft", "soft"), ("Full", "full"), ("Flat", "flat"))
AA_PREVIEW = (("Off", "off"), ("Fast", "fxaa"))
AA_MOVIE = (("Off", "off"), ("Fast", "fxaa"), ("Best", "ssaa"), ("Multisample", "msaa"))
STYLES = (("Normal", "normal"), ("Cartoon", "cartoon"), ("Dots", "dots"), ("Glossy", "glossy"))
GLOSS = {"shiny": (0.0, 0.25, 1.0), "metallic": (0.8, 0.3, 1.0), "glass": (0.0, 0.05, 0.35)}  # metallic, roughness, opacity

DEFAULTS = {
    "lighting": dict(preset="default", azimuth=-45.0, elevation=50.0, follow_camera=False, intensity=1.0, fill=0.35,
                     ambient=0.25, specular=0.2, ssao_radius=0.03, ssao_strength=1.0),
    "shadows": dict(slice=dict(on=False, strength=0.5, softness=3.0), real=dict(on=False, strength=0.6)),
    "aa": dict(preview="off", movie=None),  # movie None: from output.aa (True = "ssaa", as before)
    "vignette": dict(when="never", strength=0.6, size=0.55, softness=0.35, colour="black", shape="round",
                     blur=dict(on=False, strength=0.5)),
}
VIGNETTE_WHEN = (("never", "never"), ("in close-ups", "closeups"), ("always", "always"))
STYLE_DEFAULTS = dict(kind="normal", outline_width=2, outline_color="black", point_size=4, gloss="shiny")


def look_settings(spec):
    out = copy.deepcopy(DEFAULTS); own = spec.get("look") or {}
    out["lighting"].update(own.get("lighting") or {})
    sh = own.get("shadows") or {}
    out["shadows"]["slice"].update(sh.get("slice") or {}); out["shadows"]["real"].update(sh.get("real") or {})
    out["aa"].update(own.get("aa") or {})
    vg = own.get("vignette") or {}
    out["vignette"].update({k: v for k, v in vg.items() if k != "blur"}); out["vignette"]["blur"].update(vg.get("blur") or {})
    if out["aa"]["movie"] is None:
        out["aa"]["movie"] = "ssaa" if spec.get("output", {}).get("aa", True) else "off"
    return out


def ensure(spec):
    """spec["look"] filled in, for the window, which edits it in place."""
    lk = look_settings(spec); own = spec.setdefault("look", {})
    for k in ("lighting", "shadows", "aa", "vignette"):
        own[k] = lk[k]
    return own


def style_of(spec, key):
    st = dict(STYLE_DEFAULTS); st.update(((spec.get("classes") or {}).get(key) or {}).get("style") or {})
    return st


def aa_mode(v):
    """Anti-aliasing mode from a bool (older callers) or a mode name."""
    if v is True:
        return "ssaa"
    if not v:
        return "off"
    return v if v in ("off", "fxaa", "ssaa", "msaa") else "off"


def direction(az, el):
    """Unit vector from the scene towards a light at azimuth az (around z) and elevation el (above the slice plane)."""
    a, e = math.radians(az), math.radians(el)
    return np.array([math.cos(e) * math.cos(a), math.cos(e) * math.sin(a), math.sin(e)])


def camera_direction(az, el):
    """Light position in VTK camera-light coordinates (camera at +z looking at the origin, up +y)."""
    a, e = math.radians(az), math.radians(el)
    return np.array([math.cos(e) * math.sin(a), math.sin(e), math.cos(e) * math.cos(a)])


def to_world(cam_vec, position, focal, up):
    """A camera-light direction in world coordinates."""
    back = np.asarray(position, float) - np.asarray(focal, float); back /= np.linalg.norm(back) or 1.0
    upv = np.asarray(up, float); upv = upv - back * upv.dot(back); upv /= np.linalg.norm(upv) or 1.0
    right = np.cross(upv, back)
    return cam_vec[0] * right + cam_vec[1] * upv + cam_vec[2] * back


def shadow_mask(points_list, k, light_to, shape, softness=3.0, q=4, clip=None):
    """Soft shadow (0..1, (ny, nx)) that structures above slice k cast on it, lit from direction light_to (unit
    vector from the scene towards the light). points_list: vertex arrays (x, y, z) of the visible surfaces.
    Vertices are projected along the light onto the slice and counted on a grid q times coarser, then blurred."""
    from scipy import ndimage
    nz, ny, nx = shape
    L = np.asarray(light_to, float); lz = max(0.15, L[2])  # a light near the horizon would throw endless shadows
    gy, gx = ny // q + 1, nx // q + 1
    hits = np.zeros(gy * gx, np.float32)
    for pts in points_list:
        p = np.asarray(pts, np.float32)
        p = p[p[:, 2] > k + 0.5]
        if clip is not None:
            (x0, y0, _), (x1, y1, z1) = clip
            p = p[(p[:, 0] >= x0 - 0.5) & (p[:, 0] <= x1 + 0.5) & (p[:, 1] >= y0 - 0.5) & (p[:, 1] <= y1 + 0.5) & (p[:, 2] <= z1 + 0.5)]
        if not len(p):
            continue
        t = (p[:, 2] - k) / lz
        x = p[:, 0] - L[0] * t; y = p[:, 1] - L[1] * t
        ok = (x >= 0) & (x < nx) & (y >= 0) & (y < ny)
        idx = (y[ok] / q).astype(np.int64) * gx + (x[ok] / q).astype(np.int64)
        hits += np.bincount(idx, minlength=gy * gx).astype(np.float32)
    m = (hits.reshape(gy, gx) > 0).astype(np.float32)
    if not m.any():
        return None
    m = ndimage.binary_closing(m, iterations=1).astype(np.float32)
    m = ndimage.gaussian_filter(m, max(0.6, float(softness) / q))
    full = ndimage.zoom(m, (ny / gy, nx / gx), order=1)[:ny, :nx]
    out = np.zeros((ny, nx), np.float32); out[:full.shape[0], :full.shape[1]] = full
    return np.clip(out, 0, 1)


def vignette_weight(W, H, cx, cy, size, softness, shape="round"):
    """0 in the clear centre, rising smoothly to 1 towards the edges. cx, cy: centre in pixels."""
    y, x = np.mgrid[0:H, 0:W].astype(np.float32)
    if shape == "frame":  # follows the frame's proportions
        r = np.sqrt(((x - cx) / (W / 2)) ** 2 + ((y - cy) / (H / 2)) ** 2) / math.sqrt(2)
    else:
        r = np.sqrt((x - cx) ** 2 + (y - cy) ** 2) / (math.hypot(W, H) / 2)
    u = np.clip((r - float(size)) / max(1e-3, float(softness)), 0, 1)
    return u * u * (3 - 2 * u)


# ---------------------------------------------------------------------------------------------------------------
# 3.5 phase 7: segmentation looks like ChimeraX and Amira (spec["look"]["style35"])
# ---------------------------------------------------------------------------------------------------------------
LOOKS = (("Classic", "classic"), ("Soft", "soft"), ("Soft on black", "dark"), ("Ceramic", "ceramic"),
         ("Ceramic with see-through membranes", "ceramic_glass"), ("Glass membranes", "glass"), ("Illustration", "illustration"),
         ("On the tomogram", "tomogram"), ("X-ray", "xray"))
LOOK_NOTES = {
    "classic": "As in earlier versions: lit colours on black.",
    "soft": "White background, soft shadows in creases, thin outlines.",
    "dark": "Soft shadows and gentle lighting on a black background, no outlines.",
    "ceramic": "Solid, matte and pale like a porcelain model: deep soft shadows, cream membranes, no outlines.",
    "ceramic_glass": "Ceramic, with see-through membranes so what is inside them shows.",
    "glass": "Soft, with pale see-through membranes outlined at their outer edge.",
    "illustration": "Thick black outlines and flat light, like a textbook drawing.",
    "tomogram": "The structures over the grey tomogram slice.",
    "xray": "Everything see-through on black, with glowing edges in each structure's colour: good for crowded scenes.",
}
GLASS_OUTLINES = (("all edges", "all"), ("outer edge only", "outer"), ("none", "none"))
MATERIALS = (("normal", "normal"), ("ceramic (matte, soft sheen)", "ceramic"), ("X-ray (see-through, glowing)", "xray"))
XRAY_OPACITY = 0.3  # every structure with the X-ray material: see-through, so what lies behind and inside shows
MEMBRANE_LOOKS = (("their own colour", "own"), ("see-through (glass)", "glass"), ("solid cream (ceramic)", "cream"))
CREAM = "#efe7d4"
BACKGROUNDS = (("black", "black"), ("white", "white"), ("light grey", "lightgrey"), ("gradient (light grey to white)", "gradient"),
               ("the tomogram slice", "tomogram"))
BG_COLOURS = {"black": "#000000", "white": "#ffffff", "lightgrey": "#e6e7ea", "darkgrey": "#1c1f26", "gradient": "#ffffff", "tomogram": "#ffffff"}
GRADIENT_TOP = "#d4d7dc"
SHADOW_LEVELS = (("none", "none"), ("light", "light"), ("medium", "medium"), ("strong", "strong"))
SHADOW_DARKEST = {"none": 1.0, "light": 0.6, "medium": 0.3, "strong": 0.15}  # brightness in the deepest creases
REACHES = (("short", "short"), ("normal", "normal"), ("long", "long"))
REACH_SCALE = {"short": 0.6, "normal": 1.0, "long": 1.8}
OUTLINES = (("off", "off"), ("thin", "thin"), ("medium", "medium"), ("thick", "thick"))
OUTLINE_PX = {"off": 0.0, "thin": 1.5, "medium": 2.5, "thick": 3.5}  # at 1080 lines, scaled with the frame height
OUTLINE_COLOURS = (("dark", "dark"), ("black", "black"), ("darker shade of each colour", "shade"),
                   ("glowing, in each colour", "glow"))
FADES = (("off", "off"), ("light", "light"), ("medium", "medium"), ("strong", "strong"))
FADE_AMOUNT = {"off": 0.0, "light": 0.25, "medium": 0.5, "strong": 0.7}
STYLE35_DEFAULTS = dict(preset="classic", background=None, shadow="none", reach="normal", outline="off", outline_colour="dark",
                        between=False, glass=False, glass_opacity=0.3, glass_colour="#e6dcc4", depth_fade="off", lighting=False,
                        richer=False, glass_outline="all", material="normal", pastel=False, membrane_colour=None)
LOOK_PRESETS = {
    "classic": dict(background="black", shadow="none", outline="off", between=False, glass=False, depth_fade="off", lighting=False, richer=False),
    "soft": dict(background="white", shadow="medium", outline="thin", outline_colour="dark", between=True, glass=False, depth_fade="medium",
                 lighting=True, richer=True),
    "dark": dict(background="black", shadow="medium", outline="off", between=False, glass=False, depth_fade="medium", lighting=True,
                 richer=False),
    "ceramic": dict(background="white", shadow="strong", reach="long", outline="off", between=False, glass=False, depth_fade="light",
                    lighting=True, richer=False, material="ceramic", pastel=True, membrane_colour=CREAM),
    "ceramic_glass": dict(background="white", shadow="strong", reach="long", outline="off", between=False, glass=True, glass_opacity=0.3,
                          glass_colour=CREAM, glass_outline="outer", depth_fade="light", lighting=True, richer=False, material="ceramic",
                          pastel=True),
    "glass": dict(background="white", shadow="medium", outline="thin", outline_colour="dark", between=True, glass=True, depth_fade="medium",
                  lighting=True, richer=True, glass_outline="outer"),
    "illustration": dict(background="white", shadow="light", outline="thick", outline_colour="black", between=True, glass=True,
                         depth_fade="light", lighting=True, richer=True, glass_outline="outer"),
    "tomogram": dict(background="tomogram", shadow="medium", outline="thin", outline_colour="dark", between=True, glass=False,
                     depth_fade="off", lighting=True, richer=False),
    "xray": dict(background="black", shadow="none", outline="thin", outline_colour="glow", between=True, glass=False,
                 depth_fade="medium", lighting=True, richer=False, material="xray"),
}
MEMBRANE_WORDS = ("membrane", "er", "envelope", "vesicle")


def style35(spec):
    """The look settings of 3.5. Missing (projects and settings from before 3.5): Classic, which looks as before."""
    out = dict(STYLE35_DEFAULTS); out.update(((spec.get("look") or {}).get("style35")) or {})
    if out.get("background") is None:  # older settings: the background colour of the Look tab
        bg = spec.get("style", {}).get("bg", "#000000").lower()
        out["background"] = next((k for k, c in BG_COLOURS.items() if c == bg and k not in ("gradient", "tomogram")), "custom")
    return out


def apply_look(spec, name):
    """Choose a look preset: it sets several settings at once (each can still be changed; the look then shows Custom)."""
    st = spec.setdefault("look", {}).setdefault("style35", {})
    st.clear(); st.update(STYLE35_DEFAULTS); st.update(LOOK_PRESETS[name]); st["preset"] = name
    set_background(spec, st["background"])
    return spec


def set_look(spec, **kw):
    """Change one look setting; the preset becomes "custom" unless the values still match it."""
    st = spec.setdefault("look", {}).setdefault("style35", dict(style35(spec)))
    st.update(kw)
    if "background" in kw:
        set_background(spec, kw["background"])
    p = st.get("preset", "classic")
    if p in LOOK_PRESETS and any(st.get(k) != v for k, v in LOOK_PRESETS[p].items()):
        st["preset"] = "custom"
    return spec


def set_background(spec, kind):
    style = spec.setdefault("style", {})
    if kind in BG_COLOURS:
        style["bg"] = BG_COLOURS[kind]
    if kind == "tomogram":
        style.setdefault("tomogram", {})["show"] = True


def is_membrane(spec, key, name):
    """Structures drawn see-through with "Glass membranes": ticked per structure, else guessed from the name."""
    c = (spec.get("classes") or {}).get(key) or {}
    if c.get("membrane") is not None:
        return bool(c["membrane"])
    import re
    words = re.findall(r"[a-z]+", (name or "").lower())
    return any(w in MEMBRANE_WORDS or w.startswith("membran") or w.startswith("vesic") for w in words)


def pastel(rgb, amount=0.3):
    """A paler colour, as on a ceramic model: mixed a little towards white."""
    return tuple(c + (1.0 - c) * amount for c in rgb)


def membrane_look(L35):
    """'own' / 'glass' / 'cream' for the Membranes choice of the Look group."""
    return "glass" if L35.get("glass") else "cream" if L35.get("membrane_colour") else "own"


def richer(rgb):
    """Colours that do not wash out on a white background: 15 % more saturation, 10 % darker."""
    import colorsys
    h, s, v = colorsys.rgb_to_hsv(*rgb)
    return colorsys.hsv_to_rgb(h, min(1.0, s * 1.15), v * 0.9)


class BakedShadows:
    """Soft shadows (ambient occlusion) baked into the surface colours (plan 3.5 phase 7.2): the occupancy of the shown
    structures, blurred at three distances, sampled a little outside each vertex. Does not depend on the camera, so it
    never flickers in movies, and it works with software rendering."""
    RADII = (2.0, 5.0, 10.0); WEIGHTS = (0.5, 0.3, 0.2); Q = 2  # fields at half resolution

    def __init__(self):
        self.key = None; self.fields = None; self.occ = None; self._factor = {}

    def prepare(self, ds, ks, reach):
        """Blurred occupancy fields for the structures ks (cached until they or the reach change)."""
        key = (tuple(sorted(ks)), reach, id(ds), tuple(id(ds.meshes.get(k)) for k in sorted(ks)))
        if key == self.key:
            return
        from scipy import ndimage
        nz, ny, nx = ds.shape; q = self.Q
        occ = np.zeros(((nz + q - 1) // q, (ny + q - 1) // q, (nx + q - 1) // q), np.float32)
        for k in ks:
            sl = ds.bbox.get(k)
            if sl is None:
                continue
            m = ds.kept_mask(k, sl).astype(np.float32)
            z0, y0, x0 = sl[0].start, sl[1].start, sl[2].start
            pz, py, px = z0 % q, y0 % q, x0 % q  # align to the coarse grid
            m = np.pad(m, ((pz, (-(m.shape[0] + pz)) % q), (py, (-(m.shape[1] + py)) % q), (px, (-(m.shape[2] + px)) % q)))
            c = m.reshape(m.shape[0] // q, q, m.shape[1] // q, q, m.shape[2] // q, q).mean(axis=(1, 3, 5))
            Z, Y, X = (z0 - pz) // q, (y0 - py) // q, (x0 - px) // q
            sub = occ[Z:Z + c.shape[0], Y:Y + c.shape[1], X:X + c.shape[2]]
            np.maximum(sub, c[:sub.shape[0], :sub.shape[1], :sub.shape[2]], out=sub)
        s = REACH_SCALE.get(reach, 1.0)
        self.fields = [ndimage.gaussian_filter(occ, r * s / q) for r in self.RADII]
        self.occ = occ; self.key = key; self._factor = {}; self.reach = s

    def factor(self, mesh, darkest):
        """Brightness 0..1 of each vertex of a mesh (1: open, `darkest`: deep in a crease)."""
        key = (id(mesh), round(darkest, 3), self.key)
        if key in self._factor:
            return self._factor[key]
        from scipy import ndimage
        p = np.asarray(mesh.points, np.float32); q = self.Q
        n = mesh.point_data.get("Normals")
        if n is None:
            n = mesh.compute_normals(cell_normals=False, auto_orient_normals=False).point_data["Normals"]
        n = np.asarray(n, np.float32)

        def sample(f, pts):
            return ndimage.map_coordinates(f, [pts[:, 2] / q, pts[:, 1] / q, pts[:, 0] / q], order=1, mode="nearest")
        step = max(1, len(p) // 4000)  # which way is outside: where there is less of the structures
        a = sample(self.occ, p[::step] + 1.5 * n[::step]).mean(); b = sample(self.occ, p[::step] - 1.5 * n[::step]).mean()
        if a > b:
            n = -n
        occ = sum(w * sample(f, p + n * r * self.reach) for f, w, r in zip(self.fields, self.WEIGHTS, self.RADII))
        f = 1.0 - (1.0 - darkest) * np.clip((occ - 0.16) / 0.34, 0.0, 1.0)
        self._factor[key] = f.astype(np.float32)
        return self._factor[key]


def outline_edges(lin, bg, ids=None, thr=0.02):
    """Outline pixels (H, W bool): where the log of the depth jumps by more than thr to a neighbour (the nearer pixel
    gets the outline), and, with an object-id picture, wherever the object changes (touching structures)."""
    lg = np.log(np.maximum(lin, 1e-6)); H, W = lin.shape; edge = np.zeros((H, W), bool)
    idf = ids.astype(np.int32) if ids is not None else None
    for ax in (0, 1):
        a = [slice(None)] * 2; b = [slice(None)] * 2; a[ax] = slice(0, -1); b[ax] = slice(1, None); a, b = tuple(a), tuple(b)
        jump = np.abs(lg[a] - lg[b]) > thr
        if idf is not None:
            jump |= np.abs(idf[a] - idf[b]).sum(-1) > 30
        jump &= ~(bg[a] & bg[b])
        nearer_a = lin[a] <= lin[b]
        edge[a] |= jump & nearer_a; edge[b] |= jump & ~nearer_a
    return edge
