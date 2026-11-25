"""One place that turns a frame's pose into a camera (3.6): the renderer sets VTK from it, and arrows, text pointers and
the scale bar project with it, so they always agree."""
import math

import numpy as np


def pose_camera(fr, pose):
    """(position, focal point, up, view angle in degrees, parallel scale or None) of a pose for the Framer `fr` (the
    picture's shape, field of view and projection). Free cameras (paths, wiggle, X/Y/screen spins) give pos and up."""
    focal = np.asarray(pose["c"], float)
    if pose.get("pos") is not None:
        pos = np.asarray(pose["pos"], float); up = np.asarray(pose["up"], float)
    else:
        d, up = fr.axes(pose["tilt"], pose["az"])
        pos = focal + pose["dist"] * np.asarray(d, float); up = np.asarray(up, float)
    angle = float(pose.get("fov", fr.fov))
    return pos, focal, up, angle, (float(pose["scale"]) if fr.ortho else None)


def frame_axes(fr, pose):
    """(camera position, unit view direction camera -> focal point, unit up, unit right) as the picture shows them."""
    pos, focal, up, _, _ = pose_camera(fr, pose)
    d = focal - pos; d = d / (np.linalg.norm(d) or 1.0)
    up = up - d * float(up @ d); up = up / (np.linalg.norm(up) or 1.0)
    return pos, d, up, np.cross(d, up)


def project(fr, pose, P, aspect):
    """(fx, fy, in front) of a 3D point P in the picture (0..1 from the top left)."""
    P = np.asarray(P, float)
    cam, d, up, right = frame_axes(fr, pose)
    v = P - cam; z = float(v @ d)
    if fr.ortho:
        h = pose["scale"]
        return 0.5 + float(v @ right) / (2 * h * aspect), 0.5 - float(v @ up) / (2 * h), True
    if z <= 1e-6:
        return 0.5, 0.5, False
    t = math.tan(math.radians(float(pose.get("fov", fr.fov))) / 2)
    return 0.5 + float(v @ right) / (z * t * aspect) / 2, 0.5 - float(v @ up) / (z * t) / 2, True
