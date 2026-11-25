"""3.6 phase 5: the orientation marker (R8) and a caption box in any colour (R4, the 'box' of question Q1)."""
import math

import numpy as np

import tomoreveal as tr
from tomoreveal import camera, overlays as ov
from tomoreveal import storyboard as sb


def _frame(ds, spec, tilt, az):
    spec["view"].update(tilt=tilt, az=az)
    spec["shots"] = [sb.new_part("overview", motion="none", seconds=1)]
    return sb.build_plan(spec, ds).frames[3]


def _axis_angle(img, box, colour):
    """Direction (degrees, y up) from the marker's middle to the pixels of one arrow colour."""
    x0, y0, x1, y1 = (int(round(v)) for v in box)
    sub = np.asarray(img)[y0:y1, x0:x1, :3].astype(int)
    m = (np.abs(sub - np.array(colour)).sum(2) < 40)
    ys, xs = np.nonzero(m)
    assert len(xs) > 20, "no pixels of that arrow"
    cx, cy = (x1 - x0) / 2, (y1 - y0) / 2
    return math.degrees(math.atan2(-(ys.mean() - cy), xs.mean() - cx))


def test_the_x_arrow_points_along_the_tomograms_x(studio):
    ds, ren = studio.ds, studio.ren
    spec = tr.default_spec(ds); spec["style"]["bg"] = "#000000"
    ov.set_item(spec, "axes", show=True)
    size = (640, 360)
    for tilt, az in ((40, 0), (60, 35), (25, 120), (75, 250)):
        f = _frame(ds, spec, tilt, az)
        ren.draw(f, spec, size, preview=True)
        r = ren.last_rects["axes"]; assert r["shown"]
        layer = np.asarray(ren.last_layer)
        fr = ren._framer(spec, size); c = np.asarray(f["pose"]["c"], float)
        a = camera.project(fr, f["pose"], c, size[0] / size[1]); b = camera.project(fr, f["pose"], c + [20.0, 0, 0], size[0] / size[1])
        want = math.degrees(math.atan2(-(b[1] - a[1]) * size[1], (b[0] - a[0]) * size[0]))
        got = _axis_angle(layer, r["box"], ov.AXIS_COLOURS["x"])
        assert abs((got - want + 180) % 360 - 180) < 3.0, (tilt, az, got, want)


def test_the_marker_stays_out_of_movies_unless_asked(studio):
    ds, ren = studio.ds, studio.ren
    spec = tr.default_spec(ds); ov.set_item(spec, "axes", show=True)
    f = _frame(ds, spec, 45, 30)
    ren.draw(f, spec, (320, 180)); assert not ren.last_rects["axes"]["shown"]  # a movie frame
    ren.draw(f, spec, (320, 180), preview=True); assert ren.last_rects["axes"]["shown"]
    ov.set_item(spec, "axes", in_movie=True)
    ren.draw(f, spec, (320, 180)); assert ren.last_rects["axes"]["shown"]


def test_a_caption_box_in_any_colour(studio):
    ds, ren = studio.ds, studio.ren
    spec = tr.default_spec(ds); ov.set_item(spec, "caption", box="fill", fill="#e0a030")
    spec["shots"] = [sb.new_part("overview", motion="none", seconds=1, caption="Vesicles")]
    f = sb.build_plan(spec, ds).frames[5]
    img = ren.draw(f, spec, (640, 360))
    x0, y0, x1, y1 = (int(round(v)) for v in ren.last_rects["caption"]["box"])
    edge = img[y0 + 3:y1 - 3, x0 + 3:x0 + 8].reshape(-1, 3).astype(int)  # the box's left end, left of the text
    assert np.abs(np.median(edge, 0) - [224, 160, 48]).max() < 25
    inner = img[y0:y1, x0:x1].reshape(-1, 3).astype(int)
    assert (inner.sum(1) < 120).sum() > 30  # dark text on the light orange box
