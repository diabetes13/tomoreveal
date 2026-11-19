"""Phase 4: tomogram opacity for the whole movie and per shot, with fades."""
import numpy as np
import pytest

import tomoreveal as tr


@pytest.fixture(scope="module")
def both(demo):
    st = tr.Studio(headless=True)
    st.add_files([demo["tomogram"], demo["labels"]], log=lambda *a: None, make_renderer=False)
    return st


def slices(spec, st, shot):
    frames, _ = tr.build_timeline(spec, st.ds)
    return [f["slice"] for f in frames if f["shot"] == shot]


def test_hidden_overview_fades_out_then_stays_hidden(both):
    spec = tr.default_spec(both.ds)
    spec["shots"][2]["tomogram"] = dict(mode="hidden", opacity=0.4, fade=0.5)
    s = slices(spec, both, 2); nfade = round(30 * 0.5)
    assert s[0] > 0.9 and all(v == 0 for v in s[nfade:])
    assert all(a >= b for a, b in zip(s[:nfade], s[1:nfade + 1]))  # monotonic from 1 down to 0
    assert all(v == 1.0 for v in slices(spec, both, 1))  # the reveal before it is untouched


def test_faded_shot_and_default_opacity(both):
    spec = tr.default_spec(both.ds)
    spec["style"]["tomogram"]["opacity"] = 0.4
    frames, _ = tr.build_timeline(spec, both.ds)
    assert max(f["slice"] for f in frames) <= 0.4 + 1e-9 and frames[0]["slice"] == pytest.approx(0.4)
    spec["shots"][2]["tomogram"] = dict(mode="faded", opacity=0.15, fade=0.0)
    assert set(slices(spec, both, 2)) == {0.15}
    spec["style"]["tomogram"]["show"] = False  # "Show the tomogram slice" off: hidden everywhere unless a shot says "shown"
    spec["shots"][0]["tomogram"] = dict(mode="shown", opacity=0.4, fade=0.0)
    assert set(slices(spec, both, 0)) == {1.0} and slices(spec, both, 1)[-1] == 0.0


def test_old_closeup_setting_hide_still_hides(both):
    spec = tr.default_spec(both.ds)
    shot = tr.focus_shot({"class": both.ds.key_of(2)}, "vesicles", seconds=1)
    shot.pop("tomogram"); shot["slice"] = "hide"  # as saved by 3.1
    spec["shots"].append(shot)
    frames, _ = tr.build_timeline(spec, both.ds)
    main = [f["slice"] for f in frames if f["shot"] == 3 and f["phase"] == "main"]
    assert main and all(v == 0 for v in main)
    assert frames[-1]["slice"] == 1.0  # back to the default after returning to the overview



def test_fade_longer_than_the_flight_never_jumps_back(both):
    """F-22: a fade that the fly-in (or the flight back from a close-up) already did is not started again."""
    spec = tr.default_spec(both.ds)
    shot = tr.focus_shot({"class": both.ds.key_of(2)}, "vesicles", seconds=2)
    shot["tomogram"] = dict(mode="hidden", opacity=0.4, fade=4.0)
    spec["shots"].append(shot)
    spec["shots"].append(dict(id="s-after", type="slide", **{"from": "first", "to": "last"}, seconds=2,
                              tomogram=dict(mode="shown", opacity=0.4, fade=4.0)))
    down, up = slices(spec, both, 3), slices(spec, both, 4)
    assert all(a >= b for a, b in zip(down, down[1:])) and down[-1] == 0
    assert all(a <= b for a, b in zip(up, up[1:])) and up[-1] == 1.0


def test_no_tomogram_means_no_slice(demo):
    st = tr.Studio(headless=True)
    st.add_files([demo["labels"]], log=lambda *a: None, make_renderer=False)
    spec = tr.default_spec(st.ds)
    spec["shots"][2]["tomogram"] = dict(mode="shown", opacity=0.4, fade=0.5)
    frames, _ = tr.build_timeline(spec, st.ds)
    assert all(f["slice"] == 0 for f in frames)


def test_rendered_slice_brightness_follows_opacity(studio):
    spec = tr.default_spec(studio.ds)
    spec["style"].update(legend=False, slice_label=False); spec["view"].update(tilt=0, az=0)
    frames, _ = tr.build_timeline(spec, studio.ds)
    s = [f for f in frames if f["shot"] == 2][0]
    out = {}
    for op in (1.0, 0.4, 0.0):
        img = studio.ren.draw(dict(s, slice=op, k=20), spec, (480, 270)).reshape(-1, 3).astype(int)
        coloured = (np.abs(img[:, 0] - img[:, 1]) > 40) | (np.abs(img[:, 1] - img[:, 2]) > 40)
        grey = img[~coloured].mean(1)
        out[op] = (grey.mean(), coloured.mean())
    assert out[1.0][0] > out[0.4][0] > out[0.0][0]
    assert out[0.4][0] / out[1.0][0] == pytest.approx(0.4, abs=0.15)
    assert all(c > 0.01 for _, c in out.values())  # structures drawn every time
