"""A small made-up tomogram and label volume for trying the program and for tests."""
import json
import os

import numpy as np

from .volumes import write_mrc


def make_demo(folder):
    """Write a small synthetic tomogram and label volume (for trying the program and for tests)."""
    from scipy.ndimage import gaussian_filter
    os.makedirs(folder, exist_ok=True)
    rng = np.random.default_rng(7); nz, ny, nx = 48, 320, 256
    z, y, x = np.mgrid[0:nz, 0:ny, 0:nx].astype(np.float32)
    lab = np.zeros((nz, ny, nx), np.int8)
    for x0, amp in ((70, 10), (186, -8)):                               # two wavy membranes
        lab[np.abs(x - (x0 + amp * np.sin(y / 38.0) + 0.15 * (z - nz / 2))) < 1.6] = 1
    for cz, cy, cx, r in ((24, 60, 128, 20), (22, 150, 120, 26), (26, 250, 135, 17), (20, 205, 222, 13), (28, 95, 28, 11)):
        d = np.sqrt(((z - cz) * 1.3) ** 2 + (y - cy) ** 2 + (x - cx) ** 2)
        lab[(np.abs(d - r) < 1.6) & (lab == 0)] = 2                     # vesicles (hollow)
    for (ya, xa), (yb, xb), zc in (((20, 95), (300, 110), 14), ((40, 150), (290, 170), 34), ((10, 160), (200, 100), 24)):
        t = np.clip(((y - ya) * (yb - ya) + (x - xa) * (xb - xa)) / float((yb - ya) ** 2 + (xb - xa) ** 2), 0, 1)
        d = np.sqrt((y - (ya + t * (yb - ya))) ** 2 + (x - (xa + t * (xb - xa))) ** 2 + (z - zc) ** 2)
        lab[(d < 1.8) & (lab == 0)] = 3                                 # filaments
    for _ in range(70):                                                 # particles
        cz, cy, cx = rng.uniform(5, nz - 5), rng.uniform(8, ny - 8), rng.uniform(8, nx - 8)
        d = (z - cz) ** 2 + (y - cy) ** 2 + (x - cx) ** 2
        lab[(d < 3.2 ** 2) & (lab == 0)] = 4
    dens = gaussian_filter((lab > 0).astype(np.float32), 0.8)
    tomo = (0.55 - 0.35 * dens + 0.05 * gaussian_filter(rng.normal(size=lab.shape).astype(np.float32), 1.0) * 6
            + 0.05 * rng.normal(size=lab.shape)).astype(np.float32)
    paths = dict(tomogram=os.path.join(folder, "demo_tomogram.mrc"), labels=os.path.join(folder, "demo_labels.mrc"))
    write_mrc(paths["tomogram"], tomo, 10.0); write_mrc(paths["labels"], lab, 10.0)
    with open(os.path.join(folder, "demo_labels.json"), "w") as fh:
        json.dump({"1": {"name": "membrane", "color": "#d7566e"}, "2": {"name": "vesicles", "color": "#45b9e8"},
                   "3": {"name": "filaments", "color": "#ffd23f"}, "4": {"name": "particles", "color": "#b8e986"}}, fh, indent=2)
    return paths
