"""Plan 3.4 phase 9: several file formats from one render, smaller files, size estimates."""
import argparse
import os
import subprocess
import sys

import numpy as np
import pytest

import tomoreveal as tr
from tomoreveal import encode
from tomoreveal.estimate import Estimator


def short_spec(ds, seconds=0.5, size=(320, 180), fps=10, **out):
    spec = tr.default_spec(ds)
    for p in spec["shots"]:
        p["seconds"] = seconds
    spec["output"].update(size=list(size), fps=fps, aa=False, **out)
    return spec


def decode_frame(path, n, w, h):
    """Frame n of a movie as RGBA uint8."""
    raw = subprocess.run([encode.ffmpeg_exe(), "-v", "error", "-i", path, "-vf", f"select=eq(n\\,{n})", "-vframes", "1",
                          "-f", "rawvideo", "-pix_fmt", "rgba", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(h, w, 4)


def test_one_render_writes_four_formats(studio, tmp_path):
    spec = short_spec(studio.ds, formats=["mp4", "mov", "mov-transparent", "mov-prores"])
    spec["style"]["bg"] = "#204060"
    out = str(tmp_path / "m.mp4")
    n, _ = tr.render_movie(studio.ren, spec, out, poster=False)
    paths = encode.output_paths(out, spec["output"]["formats"])
    assert sorted(os.path.basename(p) for p in paths.values()) == ["m.mov", "m.mp4", "m_prores.mov", "m_transparent.mov"]
    assert sorted(os.listdir(tmp_path)) == sorted(os.path.basename(p) for p in paths.values())
    for p in paths.values():
        assert encode.count_frames(p) == n
    frames, _ = tr.build_timeline(spec, studio.ds)
    i = next(j for j, f in enumerate(frames) if f["type"] == "overview" and f["phase"] == "main")
    rgba = studio.ren.draw(frames[i], spec, (320, 180), transparent=True)
    ys, xs = np.nonzero((rgba[..., 3] == 255) & (np.abs(rgba[..., 0].astype(int) - rgba[..., 2]) > 60))  # a coloured structure
    sy, sx = ys[len(ys) // 2], xs[len(xs) // 2]
    t = decode_frame(paths["mov-transparent"], i, 320, 180)
    assert t[2, 160, 3] == 0 and t[sy, sx, 3] >= 250  # see-through outside the slice, opaque on a structure
    for f in ("mp4", "mov", "mov-prores"):
        o = decode_frame(paths[f], i, 320, 180)
        assert np.abs(o[2, 160, :3].astype(int) - (0x20, 0x40, 0x60)).max() <= 6, f  # the background colour there


def test_h265_is_smaller_than_h264(studio, tmp_path):
    sizes = {}
    for codec in ("h264", "h265"):
        spec = short_spec(studio.ds, seconds=1.5, size=(640, 360), fps=30, codec=codec, file_size="balanced")
        out = str(tmp_path / f"{codec}.mp4"); n, _ = tr.render_movie(studio.ren, spec, out, poster=False)
        assert encode.count_frames(out) == n
        sizes[codec] = os.path.getsize(out)
    assert sizes["h265"] < sizes["h264"]
    assert encode.video_args("mp4", "h264", "smaller")[encode.video_args("mp4", "h264", "smaller").index("-crf") + 1] == "23"
    assert "-tag:v" in encode.video_args("mov", "h265") and encode.video_args("mp4", crf=17)[5] == "17"


def test_resume_with_prores_and_h264(studio, tmp_path):
    spec = short_spec(studio.ds, seconds=0.6, formats=["mp4", "mov-prores"])
    out = str(tmp_path / "r.mp4")
    job = tr.movie_frames(studio.ren, spec, out, poster=False, chunk_frames=20)
    total = len(job)
    for i, _, _ in job:
        if i == 30:
            break
    job.crash()
    job = tr.movie_frames(studio.ren, spec, out, poster=False, resume=True, chunk_frames=20)
    assert job.start == 20
    for _ in job:
        pass
    for p in encode.output_paths(out, ["mp4", "mov-prores"]).values():
        assert encode.count_frames(p) == total


def test_estimates_are_close(studio, tmp_path):
    spec = short_spec(studio.ds, seconds=2, size=(960, 540), fps=30, formats=["mp4", "mov-prores"])
    spec["output"]["aa"] = True
    e = Estimator(studio.ren, spec)
    for _ in e.steps():
        pass
    est = e.result["Draft"]
    out = str(tmp_path / "e.mp4"); tr.render_movie(studio.ren, spec, out, poster=False)
    real = {f: os.path.getsize(p) for f, p in encode.output_paths(out, ["mp4", "mov-prores"]).items()}
    assert abs(est["mp4"] / real["mp4"] - 1) < 0.35 and abs(est["mov-prores"] / real["mov-prores"] - 1) < 0.15
    assert "4K" in e.rough and e.result["4K"]["mp4"] > e.result["HD"]["mp4"]


def test_command_line_options():
    from tomoreveal.cli import build_parser
    a = build_parser().parse_args(["movie", "--labels", "l.mrc", "t.mrc", "--format", "mp4,mov-transparent", "--codec", "h265",
                                   "--file-size", "smaller"])
    assert a.format == "mp4,mov-transparent" and a.codec == "h265" and a.file_size == "smaller" and a.crf is None


QtCore = pytest.importorskip("PySide6.QtCore")


def test_estimate_in_the_window_stops_when_settings_change(tmp_path, demo):
    from tomoreveal.gui import build_gui
    app, win = build_gui(argparse.Namespace(files=[], outdir=str(tmp_path), headless=True))
    errs = []; old = sys.excepthook; sys.excepthook = lambda t, v, tb: errs.append(v)
    try:
        loop = QtCore.QEventLoop(); win.load(paths=[demo["tomogram"], demo["labels"]]); win.worker.finished.connect(loop.quit); loop.exec()
        for _ in range(40):
            app.processEvents()
        if win.st.ren is None:
            pytest.skip("3D rendering is not available here")
        win.tabs.setCurrentIndex(win.TAB_MOVIE); win.export.wait.stop(); win.export.start()
        assert win.export.est_gen is not None and "estimating" in win.export.est_lbl.text()
        for _ in range(5):
            win.export.step()
        est = win.export.est
        win.export.fmt["mov-transparent"].setChecked(True)  # a setting changes while it runs
        assert win.export.est is None and est.cancelled and not win.export.tick.isActive()
        assert win.spec["output"]["formats"] == ["mp4", "mov-transparent"] and "Keynote" in win.export.note.text()
        assert not errs, errs
    finally:
        sys.excepthook = old; win.close()
