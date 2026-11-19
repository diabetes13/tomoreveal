"""Dragonfly sessions: TomoReveal keeps no file open between reads (F-02) and reports damaged files clearly (F-15)."""
import os
import shutil
import sys

import numpy as np
import pytest

import tomoreveal as tr
from tomoreveal.common import UserError
from tomoreveal.dragonfly import OrsSession

from fake_session import write_session

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="Windows refuses to replace or delete a mapped file")


@windows_only
def test_a_loaded_session_can_be_replaced_on_disk(tmp_path):
    p = str(tmp_path / "s.ORSSession"); write_session(p)
    copy = str(tmp_path / "copy.ORSSession"); shutil.copy2(p, copy)
    st = tr.Studio(headless=True)
    st.add_files([p], log=lambda *a: None, make_renderer=False)
    ses = OrsSession(p); obj = ses.objects()[0]
    lab, _ = ses.read_selection([obj])
    os.replace(copy, p)  # what Dragonfly does when it saves; failed with PermissionError while TomoReveal had it open
    with pytest.raises(UserError, match="changed on disk"):  # copy2 keeps size and time; the file id differs
        ses.read_selection([obj])


@windows_only
def test_the_temporary_folder_of_the_browser_version_can_be_removed(tmp_path):
    st = tr.Studio(headless=True)
    p = os.path.join(st.work, "upload.ORSSession"); write_session(p)
    st.add_files([p], log=lambda *a: None, make_renderer=False)
    shutil.rmtree(st.work)  # what the atexit clean-up does, with the session still loaded
    assert not os.path.exists(st.work)


def test_reading_twice_gives_the_same_labels(tmp_path):
    p = str(tmp_path / "s.ORSSession"); write_session(p)
    ses = OrsSession(p); obj = ses.objects()[0]
    a, ca = ses.read_selection([obj]); b, cb = ses.read_selection([obj])
    assert np.array_equal(a, b) and ca == cb and int((a > 0).sum()) == obj["voxels"]


# ---- F-15: damaged sessions give a clear UserError, never a traceback ----

def _session(tmp_path, damage=None, cut=0, xml_offset=None):
    """A small valid session, with its XML text changed by damage(xml), its end cut off, or its XML offset replaced."""
    import struct
    p = str(tmp_path / "s.ORSSession"); write_session(p)
    raw = bytearray(open(p, "rb").read()); xoff = struct.unpack_from("<Q", raw, 4)[0]
    if damage:
        raw = raw[:xoff] + damage(raw[xoff:].decode()).encode()
    if xml_offset is not None:
        struct.pack_into("<Q", raw, 4, xml_offset)
    open(p, "wb").write(bytes(raw[:len(raw) - cut]))
    return p


def _read_all(p):
    ses = OrsSession(p)
    for o in ses.objects(include_undo=True):
        ses.preview_classes(o); ses.read_selection([o])


def _set_child(xml, parent, child, value):
    import re
    return re.sub(rf"(<{parent}>(?:(?!</{parent}>).)*?<{child}>)[^<]*(</{child}>)", rf"\g<1>{value}\g<2>", xml, count=1)


@pytest.mark.parametrize("case", ["empty file", "cut-off XML", "XML offset past the end", "spacing 0", "record offset past the end",
                                  "wrong label size", "missing RecordOffset"])
def test_damaged_session_gives_a_clear_error(tmp_path, case):
    if case == "empty file":
        p = str(tmp_path / "s.ORSSession"); open(p, "wb").close()
    elif case == "cut-off XML":
        p = _session(tmp_path, cut=40)
    elif case == "XML offset past the end":
        p = _session(tmp_path, xml_offset=10 ** 9)
    elif case == "spacing 0":
        p = _session(tmp_path, lambda x: x.replace('direction0Spacing="1e-9"', 'direction0Spacing="0"'))
    elif case == "record offset past the end":
        p = _session(tmp_path, lambda x: _set_child(x, "Labels", "RecordOffset", 10 ** 9))
    elif case == "wrong label size":
        p = _session(tmp_path, lambda x: _set_child(x, "Labels", "size", 3))
    else:
        p = _session(tmp_path, lambda x: x.replace("<BeginInt><RecordOffset>", "<BeginInt><Other>").replace(
            "</RecordOffset><size>4</size></BeginInt>", "</Other><size>4</size></BeginInt>"))
    with pytest.raises(UserError) as err:
        _read_all(p)
    assert "s.ORSSession" in str(err.value)
    st = tr.Studio(headless=True)
    with pytest.raises(UserError):
        st.add_files([p], log=lambda *a: None, make_renderer=False)


def test_inspect_reports_a_damaged_object_instead_of_crashing(tmp_path):
    from tomoreveal.dragonfly import describe_session
    p = _session(tmp_path, lambda x: _set_child(x, "Labels", "size", 3))
    (o,) = describe_session(p)["objects"]
    assert "damaged" in o["error"]


def test_two_zstd_frames_in_one_chunk(tmp_path, monkeypatch):
    """A chunk may hold the end of one frame and the start of the next; the second frame was dropped."""
    import struct
    import zstandard
    import fake_session

    def two_frames(buf, data):
        off = len(buf); h = len(data) // 2; c = zstandard.ZstdCompressor()
        z = c.compress(data[:h]) + c.compress(data[h:])
        buf += struct.pack("<Q", len(data)) + struct.pack("<Q", len(z)) + z
        return off
    monkeypatch.setattr(fake_session, "_record", two_frames)
    p = str(tmp_path / "s.ORSSession"); write_session(p)
    ses = OrsSession(p); (o,) = ses.objects()
    lab, classes = ses.read_selection([o])
    assert int((lab > 0).sum()) == o["voxels"] and classes[2]["name"] == "Membrane"


def test_single_slice_preview_is_never_the_guess():
    """F-20: extract without --roi picks the 3D segmentation, as the app does, not a one-slice preview."""
    from tomoreveal.dragonfly import guess_roi
    objs = [dict(kind="multi-ROI", title="Segmentation preview", dims=(20, 10, 1), voxels=200),
            dict(kind="multi-ROI", title="final", dims=(20, 10, 5), voxels=50)]
    assert guess_roi(objs) == "final"
    rois = [dict(kind="ROI", title="a", dims=(20, 10, 5), voxels=50), dict(kind="ROI", title="b", dims=(20, 10, 1), voxels=5)]
    assert guess_roi(rois) == "a"  # one 3D ROI left: that one, not all of them

