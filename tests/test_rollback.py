"""F-14: a failed add, switch or removal leaves Studio exactly as it was, and the original error is raised."""
import shutil

import numpy as np

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


# ---- surfaces and paths made while the data changes (G-13) ----
class Slow:
    """Something stretched in a worker thread: it waits inside `real` until it is let go."""
    def __init__(self, real):
        import threading
        self.real, self.inside, self.go = real, threading.Event(), threading.Event()

    def __call__(self, *a, **k):
        import threading
        if threading.current_thread().name == "slow":
            self.inside.set(); assert self.go.wait(20)
        return self.real(*a, **k)

    def during(self, work, meanwhile):
        """Runs work in a thread; meanwhile() while it is inside. Returns what work returned; its error is raised."""
        import threading
        out = {}

        def run():
            try:
                out["value"] = work()
            except BaseException as exc:
                out["error"] = exc
        t = threading.Thread(target=run, name="slow"); t.start()
        try:
            assert self.inside.wait(20); meanwhile()
        finally:
            self.go.set(); t.join(20)
        assert "error" not in out, out["error"]
        return out["value"]


def fits(st, k):
    """The surface of structure k lies in the box of its voxels."""
    b = np.array(st.ds.meshes[k].bounds).reshape(3, 2); sl = st.ds.bbox[k][::-1]
    return np.allclose(b[:, 0], [s.start for s in sl], atol=1.5) and np.allclose(b[:, 1], [s.stop - 1 for s in sl], atol=1.5)


def one_sided(tmp_path, name="a", at=10):
    """A label file with one long object on one side of a 60 x 70 x 80 grid, and a small one."""
    from tomoreveal.volumes import write_mrc
    lab = np.zeros((60, 70, 80), np.int8); lab[8:14, at:at + 8, 5:45] = 1; lab[40:44, 50:54, 60:66] = 2; lab[50:53, 10:14, 10:14] = 2
    p = str(tmp_path / f"{name}_labels.mrc"); write_mrc(p, lab, 10.0)
    return p


def test_a_surface_made_during_a_flip_is_on_the_right_side(tmp_path, monkeypatch):
    st = tr.Studio(headless=True); kw = dict(log=quiet, make_renderer=False)
    st.add_files([one_sided(tmp_path)], **kw)
    key = st.ds.key_of(1); want = dict(smoothing=3)
    slow = Slow(Dataset.kept_mask); monkeypatch.setattr(Dataset, "kept_mask", lambda self, *a: slow(self, *a))
    slow.during(lambda: st.ds.make_surface(key, params=want), lambda: st.set_flip(("x",), **kw))
    st.set_structure_params(key, **want)
    assert fits(st, 1) and st.ds.bbox[1][2].start == 35
    st.set_flip((), **kw); assert fits(st, 1) and st.ds.bbox[1][2].start == 5


def test_a_surface_made_during_a_tick_belongs_to_its_structure(tmp_path, monkeypatch):
    a, b = one_sided(tmp_path, "a", 10), one_sided(tmp_path, "b", 40)
    st = tr.Studio(headless=True); kw = dict(log=quiet, make_renderer=False)
    st.add_files([a, b], as_labels=[a, b], **kw)
    key = f"labels:{st._norm(b)}#1"; assert st.ds.id_of(key) == 3
    slow = Slow(Dataset.kept_mask); monkeypatch.setattr(Dataset, "kept_mask", lambda self, *a: slow(self, *a))
    slow.during(lambda: st.ds.make_surface(key, params=dict(smoothing=3)),
                lambda: st.set_segmentation_enabled("labels:" + st._norm(a), False, **kw))
    k = st.ds.id_of(key); assert k == 1
    st.set_structure_params(key, smoothing=3); assert fits(st, k)
    st.set_segmentation_enabled("labels:" + st._norm(a), True, **kw)
    assert all(fits(st, k) for k in st.ds.classes)  # every structure is drawn again, in its own place
    assert st.ds.make_surface("labels:gone#1") is None  # a structure that is not loaded any more


def test_asking_while_the_segmentation_is_replaced_never_fails(tmp_path, monkeypatch):
    import threading
    a, b = one_sided(tmp_path, "a", 10), one_sided(tmp_path, "b", 40)
    st = tr.Studio(headless=True); kw = dict(log=quiet, make_renderer=False)
    st.add_files([a, b], as_labels=[a, b], **kw)
    keys = [c["key"] for c in st.ds.classes.values()]; stop = threading.Event(); errors = []; n = [0]

    def ask():
        while not stop.is_set():
            for key in keys:
                try:
                    n[0] += 1; st.ds.make_surface(key, params=dict(smoothing=n[0] % 7)); st.ds.frozen().components(1)
                except BaseException as exc:
                    errors.append(exc); return
    t = threading.Thread(target=ask); t.start()
    try:
        for on in (False, True) * 6:
            st.set_segmentation_enabled("labels:" + st._norm(a), on, **kw)
        for axes in (("x",), (), ("z", "y"), ()):
            st.set_flip(axes, **kw)
        real = Dataset.set_segmentation
        monkeypatch.setattr(Dataset, "set_segmentation", lambda self, *a, **k: real(self, *a, **k) or exec("raise Injected()", globals()))
        for _ in range(3):  # a rollback while the worker runs
            with pytest.raises(Injected):
                st.set_segmentation_enabled("labels:" + st._norm(a), False, **kw)
    finally:
        stop.set(); t.join(20)
    assert not errors and n[0] > 20, errors


def test_objects_and_paths_worked_out_during_a_flip_are_forgotten(tmp_path, monkeypatch):
    from tomoreveal import paths
    st = tr.Studio(headless=True); kw = dict(log=quiet, make_renderer=False)
    st.add_files([one_sided(tmp_path)], **kw); ds = st.ds; key = ds.key_of(1)
    slow = Slow(Dataset.mask)
    with monkeypatch.context() as m:
        m.setattr(Dataset, "mask", lambda self, *a: slow(self, *a))
        slow.during(lambda: ds.components(2), lambda: st.set_flip(("y",), **kw))
    got = ds.components(2)[0]
    fresh = tr.Studio(headless=True); fresh.flip = ("y",); fresh.add_files([one_sided(tmp_path)], **kw)
    assert np.array_equal(got, fresh.ds.components(2)[0])
    work = ds.frozen(); slow = Slow(paths._items_mask)
    with monkeypatch.context() as m:
        m.setattr(paths, "_items_mask", slow)
        slow.during(lambda: paths.path_line(work, [(key, None)], None), lambda: st.set_flip((), **kw))
    line = paths.path_line(ds, [(key, None)], None)  # asked for again, from the voxels of now
    sl = ds.bbox[1]; assert sl[1].start - 1 <= line[:, 1].min() and line[:, 1].max() <= sl[1].stop


def test_a_threshold_changed_meanwhile_does_not_mix(tmp_path, monkeypatch):
    from tomoreveal.volumes import write_mrc
    z, y, x = np.indices((40, 40, 40)); blob = np.exp(-((z - 20) ** 2 + (y - 20) ** 2 + (x - 20) ** 2) / 120.0).astype(np.float32)
    p = str(tmp_path / "t__blob.mrc"); write_mrc(p, blob, 10.0)
    st = tr.Studio(headless=True); kw = dict(log=quiet, make_renderer=False)
    st.add_files([p], as_features=[p], **kw); key = st.ds.key_of(1)
    slow = Slow(Dataset.kept_mask); monkeypatch.setattr(Dataset, "kept_mask", lambda self, *a: slow(self, *a))
    big = slow.during(lambda: st.ds.make_surface(key, params=dict(smoothing=3)), lambda: st.set_threshold(key, 0.9))
    assert np.ptp(big.points, axis=0).min() > 15  # the surface that was asked for: at 0.5
    st.set_structure_params(key, smoothing=3); assert fits(st, 1) and np.ptp(st.ds.meshes[1].points, axis=0).max() < 10
    st.set_threshold(key, 0.5); assert st.ds.meshes[1] is big
