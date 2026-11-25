"""Dragonfly 2025 sessions ('CAIFZ'): the index comes first and the records follow in order; 16-bit stored images."""
import struct

import numpy as np
import pytest
import zstandard

import tomoreveal as tr
from tomoreveal.common import UserError
from tomoreveal.dragonfly import OrsSession

import fake_session
from fake_session import write_session

DIMS = (6, 5, 4)


def _labels():
    lab = np.zeros(DIMS[::-1], np.uint8)
    lab[1:3, 1:4, 1:5] = 1; lab[3, 0, :] = 2; lab[0, 4, 2] = 3
    return lab


def _both(tmp_path, **kw):
    """The same content written in the 2022 and the 2025 layout."""
    return [write_session(str(tmp_path / f"s{y}.ORSSession"), dims=DIMS, labels=_labels(), layout=y, **kw) for y in (2022, 2025)]


def test_a_2025_session_reads_like_the_2022_one(tmp_path):
    img = np.arange(np.prod(DIMS), dtype=np.float32).reshape(DIMS[::-1])
    old, new = (OrsSession(p) for p in _both(tmp_path, image=img, extra_title="copy"))
    assert open(new.path, "rb").read(5) == b"CAIFZ" and new.version.startswith("2025")
    assert [o["title"] for o in new.objects()] == [o["title"] for o in old.objects()]
    for a, b in zip(old.objects(), new.objects()):
        la, ca = old.read_selection([a]); lb, cb = new.read_selection([b])
        assert np.array_equal(la, lb) and ca == cb
    np.testing.assert_array_equal(new.read_image(new.images()[0]), img)
    assert np.array_equal(new.read_selection(new.objects()[:1])[0], _labels())


def test_unused_tree_nodes_without_a_free_list(tmp_path):
    """Dragonfly 2025 leaves spare nodes after the runs with LastFree 0 and a free stack that does not list them."""
    for p in _both(tmp_path, spare=5):
        ses = OrsSession(p); (o,) = ses.objects()
        lab, classes = ses.read_selection([o])
        assert np.array_equal(lab, _labels()) and classes[2]["name"] == "Membrane"


def test_records_split_over_chunks_and_frames(tmp_path, monkeypatch):
    """A record may be several chunks, and one zstd frame may run across chunks: the walk must still find the next record."""
    def split(buf, data):
        off = len(buf); h = len(data) // 2; c = zstandard.ZstdCompressor()
        z = c.compress(data[:h]) + c.compress(data[h:])
        cut = [0, 5, len(z) // 2, len(z)]  # the first chunk ends inside the first frame's header
        buf += struct.pack("<Q", len(data))
        for a, b in zip(cut, cut[1:]):
            buf += struct.pack("<Q", b - a) + z[a:b]
        return off
    monkeypatch.setattr(fake_session, "_record", split)
    img = np.random.default_rng(1).random(DIMS[::-1], dtype=np.float32)
    p = write_session(str(tmp_path / "s.ORSSession"), dims=DIMS, labels=_labels(), layout=2025, image=img)
    ses = OrsSession(p)
    assert np.array_equal(ses.read_selection(ses.objects())[0], _labels())
    np.testing.assert_array_equal(ses.read_image(ses.images()[0]), img)


def test_a_16_bit_stored_image_is_used_as_the_tomogram(tmp_path):
    img = (np.arange(np.prod(DIMS)) % 256).astype(np.uint16).reshape(DIMS[::-1])
    p = write_session(str(tmp_path / "s.ORSSession"), dims=DIMS, labels=_labels(), layout=2025, image=img, image_dtype="<u2")
    ses = OrsSession(p); (im,) = ses.images()
    a = ses.read_image(im)
    assert a.dtype == np.uint16 and np.array_equal(a, img)  # kept as 16-bit (3.6)
    st = tr.Studio(headless=True)
    st.add_files([p], log=lambda *a: None, make_renderer=False)
    assert st.ds.tomo is not None and st.ds.tomo.shape == DIMS[::-1]


def test_an_image_of_another_data_type_is_left_out(tmp_path):
    p = write_session(str(tmp_path / "s.ORSSession"), dims=DIMS, labels=_labels(), layout=2025,
                      image=np.zeros(DIMS[::-1], np.uint16), image_dtype="<u2")
    data = open(p, "rb").read().replace(b"<DataType>2<", b"<DataType>3<")  # same length, so the index size still holds
    open(p, "wb").write(data)
    assert OrsSession(p).images() == []


@pytest.mark.parametrize("case", ["record missing", "extra bytes", "cut off"])
def test_a_2025_session_that_does_not_add_up_is_refused(tmp_path, case):
    p = write_session(str(tmp_path / "s.ORSSession"), dims=DIMS, labels=_labels(), layout=2025)
    data = open(p, "rb").read()
    n = struct.unpack_from("<Q", data, 12)[0]
    first = struct.unpack_from("<Q", data, 20 + n)[0]  # uncompressed size of the first record
    if case == "record missing":  # drop the first record: one record fewer than offset fields
        size = 20 + n + 8; got = 0
        while got < first:
            cs = struct.unpack_from("<Q", data, size)[0]
            got += len(zstandard.ZstdDecompressor().decompressobj().decompress(data[size + 8:size + 8 + cs])); size += 8 + cs
        data = data[:20 + n] + data[size:]
    elif case == "extra bytes":
        data += b"\0" * 9
    else:
        data = data[:-3]
    open(p, "wb").write(data)
    with pytest.raises(UserError) as err:
        OrsSession(p)
    assert "s.ORSSession" in str(err.value)


def test_an_unknown_file_names_both_headers(tmp_path):
    p = tmp_path / "s.ORSSession"; p.write_bytes(b"XXXX" + b"\0" * 40)
    with pytest.raises(UserError, match="CAIFZ"):
        OrsSession(str(p))
