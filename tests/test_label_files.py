"""Label files: unsigned 8-bit MRCs (F-06) and label numbers too large for small integer types (F-19)."""
import json
import sys

import numpy as np
import pytest

import tomoreveal as tr
from tomoreveal.common import UserError
from tomoreveal.volumes import load_volume, write_mrc


def quiet(*a):
    pass


def labels_info(st, path, **kw):
    info = st.add_files([path], log=quiet, make_renderer=False, **kw)
    return info, [c for c in info["structures"] if c["key"].startswith("labels:")]


@pytest.mark.parametrize("values", [(255,), (128, 200, 255)])
def test_unsigned_8bit_label_mrc_loads(tmp_path, demo, values):
    """MRC mode 0 is signed, so a 0/255 mask or labels 128-255 were read as negative numbers and np.bincount crashed."""
    lab, vox = load_volume(demo["labels"])
    u = np.zeros(lab.shape, np.uint8)
    for i, v in enumerate(values, 1):
        u[lab == i] = v
    p = str(tmp_path / "mask.mrc"); write_mrc(p, u.view(np.int8), vox)  # mrcfile would write uint8 as mode 6
    assert load_volume(p)[0].dtype == np.int8 and load_volume(p)[0].min() < 0
    st = tr.Studio(headless=True)
    st.add_files([demo["tomogram"]], log=quiet, make_renderer=False)
    info, structures = labels_info(st, p)
    assert sorted(c["label"] for c in structures) == sorted(values)
    assert all(c["voxels"] == int((u == c["label"]).sum()) for c in structures)


def test_signed_8bit_tomogram_is_not_changed(tmp_path, demo):
    tomo, vox = load_volume(demo["tomogram"])
    t8 = (tomo.astype(np.int16) - 128).astype(np.int8)
    p = str(tmp_path / "tomo8.mrc"); write_mrc(p, t8, vox)
    st = tr.Studio(headless=True)
    st.add_files([p], as_tomogram=[p], log=quiet, make_renderer=False)
    assert np.array_equal(st.tomo_files[st._norm(p)]["arr"], t8)


def test_negative_labels_give_a_clear_error_and_are_not_listed(tmp_path, demo):
    lab, vox = load_volume(demo["labels"])
    p = str(tmp_path / "neg.mrc"); write_mrc(p, -lab.astype(np.int16), vox)
    st = tr.Studio(headless=True)
    with pytest.raises(UserError, match="neg.mrc"):
        st.add_files([p], log=quiet, make_renderer=False)
    assert not any("neg.mrc" in s["key"] for s in st.seg_sources)


# ---- F-19: label numbers above 127 / 32767 ----

def test_dragonfly_label_above_32767_is_read_without_wrapping(tmp_path):
    from fake_session import write_session
    from tomoreveal.dragonfly import OrsSession
    p = str(tmp_path / "s.ORSSession"); write_session(p, runs=((0, 9, 1), (20, 29, 70000)), names=(), colors=(), visible=())
    ses = OrsSession(p); (o,) = ses.objects()
    lab, classes = ses.read_selection([o])
    assert int(lab.max()) == 70000 and int((lab == 70000).sum()) == 10 and int((lab == 1).sum()) == 10


def test_extract_writes_labels_above_32767(tmp_path):
    import argparse
    from fake_session import write_session
    from tomoreveal.cli import cmd_extract
    p = str(tmp_path / "s.ORSSession"); write_session(p, runs=((0, 9, 1), (20, 29, 70000)), names=(), colors=(), visible=())
    out = str(tmp_path / "out")
    cmd_extract(argparse.Namespace(session=p, roi=None, out=out, masks=False))
    lab, _ = load_volume(out + "/labels.mrc")
    assert float(lab.max()) == 70000 and int((lab == 70000).sum()) == 10


def test_more_than_127_plain_rois_do_not_wrap(tmp_path, monkeypatch):
    from fake_session import write_session
    from tomoreveal.dragonfly import OrsSession
    p = str(tmp_path / "s.ORSSession"); write_session(p)
    ses = OrsSession(p); dims = (25, 10, 1)
    objs = [dict(kind="ROI", title=f"roi {k}", dims=dims, el=None, voxel=1.0, voxels=1) for k in range(1, 201)]

    def one_voxel(o):
        m = np.zeros((1, 10, 25), bool); m.flat[int(o["title"].split()[1]) - 1] = True
        return m, None
    monkeypatch.setattr(ses, "read_roi", one_voxel)
    lab, classes = ses.read_selection(objs)
    assert int(lab.max()) == 200 and int(lab.min()) == 0 and len(classes) == 200


NAMES = {"1": {"name": "Größe"}, "2": {"name": "微管"}}


@pytest.mark.parametrize("encoding", [
    "utf-8-sig",  # with a byte-order mark, as Notepad writes it: failed everywhere
    pytest.param("utf-8", marks=pytest.mark.skipif(sys.platform != "win32", reason="Windows read it as cp1252")),
])
def test_sidecar_json_is_read_as_utf8(tmp_path, demo, encoding):
    """F-16: names in a sidecar JSON keep their letters."""
    from tomoreveal.volumes import sidecar_classes
    lab, vox = load_volume(demo["labels"]); p = str(tmp_path / "named.mrc"); write_mrc(p, lab, vox)
    (tmp_path / "named.json").write_text(json.dumps(NAMES, ensure_ascii=False), encoding=encoding)
    assert {k: v["name"] for k, v in sidecar_classes(p).items()} == {1: "Größe", 2: "微管"}

