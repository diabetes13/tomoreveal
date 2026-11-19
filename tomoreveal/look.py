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
