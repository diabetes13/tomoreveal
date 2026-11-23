"""F-14: a failed add, switch or removal leaves Studio exactly as it was, and the original error is raised."""
import shutil

import pytest

import tomoreveal as tr
from tomoreveal.volumes import Dataset


def quiet(*a):
    pass


class Injected(Exception):
    pass


def loaded(demo):
    st = tr.Studio(headless=True)
    st.add_files([demo["tomogram"], demo["labels"]], log=quiet, make_renderer=False)
    return st


def test_failed_add_leaves_everything_as_it_was(demo, tmp_path, monkeypatch):
    st = loaded(demo)
    before = (set(st.ds.classes), [s["key"] for s in st.seg_sources], dict(st.ds._mesh_cache), st.info()["structures"])
    other = str(tmp_path / "other_labels.mrc"); shutil.copy(demo["labels"], other)
    err = Injected("label statistics failed")

    def broken(*a, **k):
        raise err
    built = []
    monkeypatch.setattr(Dataset, "label_stats", staticmethod(broken))
    monkeypatch.setattr(Dataset, "build_meshes", lambda self, *a, **k: built.append(1))
    with pytest.raises(Injected) as caught:
        st.add_files([other, demo["labels"]], log=quiet, make_renderer=False)  # demo labels again: forget() empties its cache
    assert caught.value is err and not built
    assert (set(st.ds.classes), [s["key"] for s in st.seg_sources], dict(st.ds._mesh_cache), st.info()["structures"]) == before
    monkeypatch.undo()
    assert len(st.add_files([other], log=quiet, make_renderer=False)["structures"]) == 8  # and the next action works


def test_failed_switch_on_is_switched_off_again(demo, monkeypatch):
    st = loaded(demo)
    key = next(s["key"] for s in st.seg_sources)
    st.set_segmentation_enabled(key, False, log=quiet, make_renderer=False)
    real = Dataset.set_segmentation

    def broken(*a, **k):
        raise Injected("could not build the segmentation")
    monkeypatch.setattr(Dataset, "set_segmentation", broken)
    with pytest.raises(Injected):
        st.set_segmentation_enabled(key, True, log=quiet, make_renderer=False)
    assert st._source(key)["enabled"] is False and not st.info()["structures"]
    monkeypatch.setattr(Dataset, "set_segmentation", real)
    assert len(st.set_segmentation_enabled(key, True, log=quiet, make_renderer=False)["structures"]) == 4
