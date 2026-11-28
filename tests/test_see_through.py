"""Plan 3.5 phase 1A: lowering a structure's opacity makes it see-through, and its outline fades with it."""
import numpy as np
import pytest

import tomoreveal as tr
from tomoreveal.volumes import Dataset

FRONT, BACK = "#ff0000", "#0000ff"


@pytest.fixture(scope="module")
def slabs(needs_3d):
    """Two flat slabs, one above the other (seen from above), no tomogram; drawn with flat light so colours are exact."""
    nz, ny, nx = 40, 80, 80
    lab = np.zeros((nz, ny, nx), np.int16)
    lab[24:30, 10:70, 10:45] = 1   # front (higher z: nearer the camera looking down)
    lab[6:12, 10:70, 25:70] = 2    # back, overlapping the front one in x 25..45
    ds = Dataset()
    ds.set_segmentation([dict(kind="labels", key="labels:slabs", title="slabs", arr=lab,
                              classes={1: {"name": "front", "color": FRONT}, 2: {"name": "back", "color": BACK}})], vox=10.0)
    ds.build_meshes(log=lambda *a: None)
    from tomoreveal.render import Renderer
    ren = Renderer(ds, headless=True)
    yield ds, ren
    ren.close()


def frame(ds, ren, op=1.0, style="normal", aa=False):
    spec = tr.default_spec(ds)
    spec["style"].update(legend=False, slice_label=False, ortho=True); spec["view"].update(tilt=0, az=0)
    spec["look"] = {"lighting": {"preset": "flat"}}
    k1, k2 = ds.key_of(1), ds.key_of(2)
    spec["classes"][k1].update(color=FRONT, opacity=op, style=dict(kind=style, outline_width=3))
    spec["classes"][k2].update(color=BACK)
    spec["shots"] = [tr.storyboard.new_part("overview", seconds=0.1, motion="none")]
    f = tr.build_timeline(spec, ds)[0][0]
    return ren.draw(f, spec, (320, 320), aa=aa).astype(int)


def centre_of(ds, ren, img, x, y):
    """Pixel of the world point (x, y, top of the front slab)."""
    r = ren.pl.renderer; r.SetWorldPoint(x, y, 30, 1.0); r.WorldToDisplay(); px, py, _ = r.GetDisplayPoint()
    return img[int(round(img.shape[0] - py)), int(round(px))]


def test_front_structure_at_40_percent_shows_the_one_behind(slabs):
    ds, ren = slabs
    for aa in (False, "ssaa"):
        img = frame(ds, ren, 0.4, aa=aa)
        both = centre_of(ds, ren, img, 35, 40)      # front over back
        front_only = centre_of(ds, ren, img, 15, 40)
        back_only = centre_of(ds, ren, img, 60, 40)
        assert np.abs(both - (0.4 * np.array([255, 0, 0]) + 0.6 * np.array([0, 0, 255]))).max() <= 12, (aa, both)
        assert np.abs(front_only - [102, 0, 0]).max() <= 12 and np.abs(back_only - [0, 0, 255]).max() <= 6, (front_only, back_only)


def test_outline_fades_with_the_surface(slabs):
    ds, ren = slabs
    plain1, cart1 = frame(ds, ren, 1.0), frame(ds, ren, 1.0, "cartoon")
    plain4, cart4 = frame(ds, ren, 0.4), frame(ds, ren, 0.4, "cartoon")
    line = (np.abs(cart1 - plain1).sum(2) > 150) & (cart1.sum(2) < 40)  # outline pixels: black at full opacity
    assert line.sum() > 50
    keep = plain4[line].sum(1) > 60  # where the faded picture has something to darken
    ratio = cart4[line][keep].sum(1) / plain4[line][keep].sum(1)
    assert 0.45 <= np.median(ratio) <= 0.75, np.median(ratio)  # darkened by about 40 %, not drawn at full strength


def test_opacity_zero_draws_nothing(slabs):
    ds, ren = slabs
    for style in ("normal", "cartoon", "dots", "glossy"):
        img = frame(ds, ren, 0.0, style)
        assert not (img[..., 0] > 30).any(), style  # no pixel of the red slab or its outline
        assert (img[..., 2] > 200).any()


def test_a_30_percent_sphere_is_drawn(needs_3d):
    """The PyVista pitfall: enable_depth_peeling() with multisampling drew see-through meshes invisible (OSMesa)."""
    import pyvista as pv
    from tomoreveal.render import Renderer
    ds = Dataset(); lab = np.zeros((30, 30, 30), np.int16); z, y, x = np.mgrid[:30, :30, :30]
    lab[(z - 15) ** 2 + (y - 15) ** 2 + (x - 15) ** 2 < 64] = 1
    ds.set_segmentation([dict(kind="labels", key="labels:s", title="s", arr=lab, classes={1: {"name": "s", "color": "#00ff00"}})], vox=10.0)
    ds.build_meshes(log=lambda *a: None)
    ren = Renderer(ds, headless=True)
    try:
        spec = tr.default_spec(ds); spec["style"].update(legend=False, slice_label=False)
        spec["classes"][ds.key_of(1)]["opacity"] = 0.3; spec["shots"] = [tr.storyboard.new_part("overview", seconds=0.1, motion="none")]
        spec["classes"][ds.key_of(1)]["opacity"] = 1.0
        ren.draw(tr.build_timeline(spec, ds)[0][0], spec, (160, 160)); ms0 = ren.pl.ren_win.GetMultiSamples()
        spec["classes"][ds.key_of(1)]["opacity"] = 0.3
        img = ren.draw(tr.build_timeline(spec, ds)[0][0], spec, (160, 160))
        assert (img[..., 1] > 20).sum() > 200 and ren.peeling
        assert ren.pl.ren_win.GetMultiSamples() == 0
        spec["classes"][ds.key_of(1)]["opacity"] = 1.0
        ren.draw(tr.build_timeline(spec, ds)[0][0], spec, (160, 160))
        assert not ren.peeling and ren.pl.ren_win.GetMultiSamples() == ms0 == 0  # no mode uses multisampling
    finally:
        ren.close()
    del pv
