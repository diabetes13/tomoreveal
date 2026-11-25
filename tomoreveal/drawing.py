"""Shapes drawn with the mouse (3.6, R17): stroke simplification, curves, outlines and arrowheads, in 2D.

Points are (x, y) pairs in any unit (pixels while drawing, fractions of the picture when stored). Nothing here needs
Qt or VTK, so it is tested on its own."""
import math

import numpy as np

SIMPLIFY_PX = 1.5  # Ramer-Douglas-Peucker tolerance for freehand strokes (pixels on screen)
SNAP_DEG = 15.0  # Shift: straight lines in steps of this many degrees


def simplify(points, tol=SIMPLIFY_PX):
    """Ramer-Douglas-Peucker: the fewest points that keep the stroke within `tol` of the original."""
    P = np.asarray(points, float)
    if len(P) < 3:
        return P.copy()
    keep = np.zeros(len(P), bool); keep[0] = keep[-1] = True
    stack = [(0, len(P) - 1)]
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        a, b = P[i], P[j]; ab = b - a; L = float(np.hypot(*ab))
        seg = P[i + 1:j]
        if L < 1e-9:
            d = np.hypot(*(seg - a).T)
        else:
            d = np.abs(ab[0] * (seg[:, 1] - a[1]) - ab[1] * (seg[:, 0] - a[0])) / L
        k = int(np.argmax(d))
        if d[k] > tol:
            m = i + 1 + k; keep[m] = True; stack += [(i, m), (m, j)]
    return P[keep]


def drop_close(points, min_gap=0.5):
    """Points closer than min_gap to the last kept one are left out (a mouse that holds still sends repeats)."""
    out = []
    for p in points:
        if not out or math.hypot(p[0] - out[-1][0], p[1] - out[-1][1]) >= min_gap:
            out.append((float(p[0]), float(p[1])))
    if len(points) and out[-1] != (float(points[-1][0]), float(points[-1][1])):
        out.append((float(points[-1][0]), float(points[-1][1])))
    return out


def smooth(points, samples=10):
    """A Catmull-Rom curve through the points (the kept points of a freehand stroke)."""
    from .paths import catmull_rom
    P = np.asarray(points, float)
    if len(P) < 3:
        return P.copy()
    c, _ = catmull_rom(np.column_stack([P, np.zeros(len(P))]), samples)
    return c[:, :2]


def bezier(p0, c, p1, n=48):
    """Points along a quadratic Bezier curve from p0 to p1 bent towards c."""
    t = np.linspace(0, 1, n)[:, None]
    p0, c, p1 = (np.asarray(v, float) for v in (p0, c, p1))
    return (1 - t) ** 2 * p0 + 2 * (1 - t) * t * c + t ** 2 * p1


def bezier_middle(p0, c, p1):
    """The point half way along the curve's parameter (where its handle is drawn)."""
    return 0.25 * np.asarray(p0, float) + 0.5 * np.asarray(c, float) + 0.25 * np.asarray(p1, float)


def control_for_middle(p0, m, p1):
    """The control point that puts the curve's middle at m."""
    return 2 * np.asarray(m, float) - 0.5 * (np.asarray(p0, float) + np.asarray(p1, float))


def ellipse(a, b, n=72):
    """Outline of the ellipse inside the box with corners a and b (closed: the last point is the first)."""
    (x0, y0), (x1, y1) = a, b; cx, cy, rx, ry = (x0 + x1) / 2, (y0 + y1) / 2, abs(x1 - x0) / 2, abs(y1 - y0) / 2
    t = np.linspace(0, 2 * math.pi, n + 1)
    return np.column_stack([cx + rx * np.cos(t), cy + ry * np.sin(t)])


def rect(a, b):
    (x0, y0), (x1, y1) = a, b
    return np.array([(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)], float)


def snap_angle(p0, p1, step=SNAP_DEG):
    """p1 moved so the line from p0 runs at a multiple of `step` degrees (same length)."""
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]; L = math.hypot(dx, dy)
    if L < 1e-9:
        return tuple(p1)
    ang = round(math.degrees(math.atan2(dy, dx)) / step) * step
    return (p0[0] + L * math.cos(math.radians(ang)), p0[1] + L * math.sin(math.radians(ang)))


def square(p0, p1):
    """p1 moved so the box from p0 is a square (Shift for circles and squares)."""
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]; s = max(abs(dx), abs(dy))
    return (p0[0] + math.copysign(s, dx or 1), p0[1] + math.copysign(s, dy or 1))


def length(line):
    L = np.asarray(line, float)
    return float(np.hypot(*np.diff(L, axis=0).T).sum()) if len(L) > 1 else 0.0


def cut(line, frac):
    """The first `frac` (0..1) of a polyline, by length (drawing it on)."""
    L = np.asarray(line, float)
    if frac >= 0.999 or len(L) < 2:
        return L
    seg = np.hypot(*np.diff(L, axis=0).T); cum = np.concatenate([[0], np.cumsum(seg)]); want = cum[-1] * max(0.0, frac)
    k = int(np.searchsorted(cum, want, side="right")) - 1; k = min(max(k, 0), len(seg) - 1)
    u = (want - cum[k]) / seg[k] if seg[k] > 0 else 0.0
    return np.vstack([L[:k + 1], L[k] + (L[k + 1] - L[k]) * u])


def end_direction(line, back):
    """Unit direction of a polyline at its end, measured over the last `back` of its length (a steady arrowhead)."""
    L = np.asarray(line, float); tip = L[-1]; acc = 0.0
    for i in range(len(L) - 2, -1, -1):
        acc += float(np.hypot(*(L[i + 1] - L[i])))
        if acc >= back:
            break
    d = tip - L[i] if len(L) > 1 else np.array([1.0, 0.0])
    n = float(np.hypot(*d))
    return d / n if n > 1e-9 else np.array([1.0, 0.0])


def head(tip, u, hl, hw):
    """Triangle of an arrowhead at tip pointing along u."""
    ux, uy = u; nx, ny = -uy, ux; bx, by = tip[0] - ux * hl, tip[1] - uy * hl
    return [tuple(tip), (bx + nx * hw, by + ny * hw), (bx - nx * hw, by - ny * hw)]


def similarity(points, fixed, old, new):
    """Turn and scale points about `fixed` so that `old` lands on `new` (dragging one end of a stroke)."""
    P = np.asarray(points, float); f = np.asarray(fixed, float)
    a = complex(*(np.asarray(old, float) - f)); b = complex(*(np.asarray(new, float) - f))
    if abs(a) < 1e-9:
        return P + (np.asarray(new, float) - np.asarray(old, float))
    z = (P[:, 0] - f[0]) + 1j * (P[:, 1] - f[1]); z = z * (b / a)
    return np.column_stack([z.real + f[0], z.imag + f[1]])
