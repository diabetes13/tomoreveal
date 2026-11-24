"""What is under the mouse in the preview, and where a 3D point appears on it: the Renderer's questions about the
last frame it drew (clicking structures, adding points, framing a region, moving around in the preview)."""
import math

import numpy as np


class PreviewPicking:
    """Mixed into Renderer, which provides pl (the PyVista plotter), size, vtk, slice_actor, actors and comp_actors."""

    def _sight_line(self, fx, fy):
        """The near and far points of the line of sight through a position (fractions of the last frame)."""
        ren = self.pl.renderer; W, H = self.size; pts = []
        for depth in (0.0, 1.0):
            ren.SetDisplayPoint(fx * W, (1 - fy) * H, depth); ren.DisplayToWorld(); w = ren.GetWorldPoint()
            pts.append(np.array(w[:3]) / (w[3] or 1.0))
        return pts

    def to_plane(self, fx, fy, z):
        """The point on the plane at height z under a position (fractions of the last frame), or None."""
        a, b = self._sight_line(fx, fy); dz = b[2] - a[2]
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

    # ---------- moving around in the preview (3.5 phase 1B) ----------
    def camera_frame(self):
        """(position, focal point, unit right, unit up, unit direction camera -> focal) of the last frame drawn."""
        cam = self.pl.renderer.GetActiveCamera()
        pos, foc = np.array(cam.GetPosition()), np.array(cam.GetFocalPoint())
        d = foc - pos; d /= np.linalg.norm(d) or 1.0
        up = np.array(cam.GetViewUp()); up = up - d * float(up @ d); up /= np.linalg.norm(up) or 1.0
        return pos, foc, np.cross(d, up), up, d

    def world_per_height(self, depth):
        """World units spanned by the full frame height at a distance `depth` in front of the camera."""
        cam = self.pl.renderer.GetActiveCamera()
        if cam.GetParallelProjection():
            return 2.0 * cam.GetParallelScale()
        return 2.0 * max(1e-6, depth) * math.tan(math.radians(cam.GetViewAngle()) / 2)

    def ray(self, fx, fy):
        """(origin, unit direction) of the line of sight through a position (fractions of the last frame)."""
        a, b = self._sight_line(fx, fy); d = b - a
        return a, d / (np.linalg.norm(d) or 1.0)

    def point_under(self, fx, fy):
        """The 3D point under a position: on a structure or the slice, else on the plane through the focal point
        facing the camera. Returns (point, hit something)."""
        p = self.pick_point(fx, fy)
        if p is not None:
            return np.asarray(p, float), True
        o, r = self.ray(fx, fy); _, foc, _, _, d = self.camera_frame()
        den = float(r @ d)
        t = float((foc - o) @ d) / den if abs(den) > 1e-9 else float(np.linalg.norm(foc - o))
        return o + r * t, False

    def project(self, p):
        """Position (fractions of the last frame) of a 3D point, and whether it is in front of the camera."""
        ren = self.pl.renderer; W, H = self.size
        ren.SetWorldPoint(*[float(v) for v in p], 1.0); ren.WorldToDisplay(); x, y, z = ren.GetDisplayPoint()
        return (x / W, 1 - y / H), 0.0 <= z <= 1.0

    def region_box(self, fx0, fy0, fx1, fy1, max_points=40000):
        """The 3D box (lo, hi) in voxels (x, y, z) around what is visible inside a rectangle of the last frame (fractions
        of the frame), from the depth buffer: structures and the slice there, not the empty background. None if the
        rectangle shows nothing. Used to frame a close-up on a region drawn in the preview (3.5)."""
        W, H = self.size
        x0, x1 = sorted((int(round(min(max(fx0, 0), 1) * (W - 1))), int(round(min(max(fx1, 0), 1) * (W - 1)))))
        y0, y1 = sorted((int(round((1 - min(max(fy0, 0), 1)) * (H - 1))), int(round((1 - min(max(fy1, 0), 1)) * (H - 1)))))
        if x1 - x0 < 2 or y1 - y0 < 2:
            return None
        vtk = self.vtk; arr = vtk.vtkFloatArray()
        self.pl.ren_win.GetZbufferData(x0, y0, x1, y1, arr)
        from vtkmodules.util.numpy_support import vtk_to_numpy
        z = vtk_to_numpy(arr).reshape(y1 - y0 + 1, x1 - x0 + 1)
        ys, xs = np.nonzero(z < 0.99999)
        if len(xs) < 10:
            return None
        step = max(1, len(xs) // max_points); ys, xs = ys[::step], xs[::step]
        d = z[ys, xs].astype(float)
        cam = self.pl.renderer.GetActiveCamera()
        m = cam.GetCompositeProjectionTransformMatrix(W / H, -1, 1)
        M = np.array([[m.GetElement(i, j) for j in range(4)] for i in range(4)]); Minv = np.linalg.inv(M)
        ndc = np.c_[2 * (xs + x0 + 0.5) / W - 1, 2 * (ys + y0 + 0.5) / H - 1, 2 * d - 1, np.ones(len(d))]
        w = ndc @ Minv.T; pts = w[:, :3] / w[:, 3:4]
        lo, hi = np.percentile(pts, 2, axis=0), np.percentile(pts, 98, axis=0)
        c = (lo + hi) / 2; half = np.maximum((hi - lo) / 2, 3.0)
        return c - half, c + half

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
