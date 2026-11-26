"""Surfaces kept on disk between sessions (3.6, PLAN-3.6 2.4), under TomoReveal's own folder (never next to the data).

A surface is keyed by a fingerprint of exactly what it is made from: the structure's voxels (after small objects are
dropped), where they sit in the grid, the grid size and the surface settings. A flipped, binned, re-segmented or edited
volume therefore never meets an old surface, and nothing needs to know file names or dates."""
import hashlib
import os
import threading

import numpy as np

VERSION = b"tomoreveal-surface-1"  # change when the way surfaces are made changes (the old files are then unused)
LIMIT = 5 * 1024 ** 3              # bytes kept at most; the least recently used go first
_lock = threading.Lock()
enabled = True


def folder():
    from .project import data_dir
    return os.path.join(data_dir(), "surfaces")


def fingerprint(mask, off_zyx, full_shape, settings):
    """Hex digest of a boolean mask (packed to bits), its offset, the whole grid's shape and the surface settings."""
    h = hashlib.blake2b(VERSION, digest_size=20)
    h.update(repr((tuple(int(o) for o in off_zyx), tuple(mask.shape), tuple(full_shape or ()), tuple(settings))).encode())
    h.update(np.packbits(np.ascontiguousarray(mask, bool)).tobytes())
    return h.hexdigest()


def load(fp):
    """The stored surface (PolyData), False if stored as "no surface", or None if not stored."""
    if not enabled:
        return None
    p = os.path.join(folder(), fp + ".npz")
    try:
        with np.load(p) as z:
            if z["empty"]:
                out = False
            else:
                import pyvista as pv
                faces = z["faces"].astype(np.int64)
                out = pv.PolyData(z["points"], np.hstack([np.full((len(faces), 1), 3), faces]).ravel())
                out.point_data["Normals"] = z["normals"]
                out.point_data.active_normals_name = "Normals"
                for name in ("thickness", "measured"):  # 3.6: what a flat membrane was made with
                    if name in z.files:
                        out.field_data[name] = z[name]
        os.utime(p)  # recently used
        return out
    except (OSError, KeyError, ValueError, EOFError):
        return None


def save(fp, mesh):
    """Store a surface (or None: nothing there), then keep the folder under LIMIT."""
    if not enabled:
        return
    d = folder()
    try:
        os.makedirs(d, exist_ok=True)
        tmp = os.path.join(d, f".{fp}.{os.getpid()}.{threading.get_ident()}.npz")
        if mesh is None or mesh.n_points == 0:
            np.savez(tmp, empty=True)
        else:
            faces = mesh.faces.reshape(-1, 4)[:, 1:].astype(np.int32)
            normals = mesh.point_data.get("Normals")
            normals = np.zeros((mesh.n_points, 3), np.float32) if normals is None else np.asarray(normals, np.float32)
            extra = {n: np.asarray(mesh.field_data[n], np.float32) for n in ("thickness", "measured") if n in mesh.field_data}
            np.savez(tmp, empty=False, points=np.asarray(mesh.points, np.float32), faces=faces, normals=normals, **extra)
        os.replace(tmp, os.path.join(d, fp + ".npz"))
        _trim(d)
    except OSError:
        pass  # a full or read-only disk only means the surface is made again next time


def _trim(d):
    with _lock:
        files = []
        for e in os.scandir(d):
            if e.name.endswith(".npz") and not e.name.startswith("."):
                st = e.stat(); files.append((st.st_mtime, st.st_size, e.path))
        total = sum(s for _, s, _ in files)
        for _, s, p in sorted(files):
            if total <= LIMIT:
                break
            try:
                os.remove(p); total -= s
            except OSError:
                pass
