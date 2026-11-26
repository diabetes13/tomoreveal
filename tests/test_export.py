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
from test_movie_output import window  # noqa: F401 (fixture)


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
    job.close(keep=True)  # as a crash leaves it
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


RED = (225, 87, 89)


def colour_tags(path):
    """(range, matrix/primaries/transfer) as FFmpeg reads them from the file, e.g. ("tv", "bt709")."""
    import re
    err = subprocess.run([encode.ffmpeg_exe(), "-hide_banner", "-i", path], capture_output=True).stderr.decode(errors="replace")
    m = re.search(r"Video: .*?\((tv|pc), ([\w/]+)", err)
    return (m.group(1), m.group(2)) if m else (None, err[-300:])


def decoded_bt709(path, n, w, h):
    """Frame n as RGB, decoded with the BT.709 matrix that HD players use."""
    raw = subprocess.run([encode.ffmpeg_exe(), "-v", "error", "-i", path, "-vf",
                          f"select=eq(n\\,{n}),scale=in_color_matrix=bt709:in_range=tv", "-vframes", "1",
                          "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(h, w, 3)


@pytest.mark.parametrize("fmt", list(encode.FORMATS))
def test_movies_are_tagged_and_encoded_as_bt709(tmp_path, fmt):
    """Before, FFmpeg converted with BT.601 and wrote no tags, and players showed the colours shifted (G-39). Five frames in
    chunks of two, stopped after three and continued: the joined movie keeps the tags."""
    w, h, alpha = 64, 36, encode.FORMATS[fmt][2]; out = str(tmp_path / "c.mp4")
    img = np.zeros((h, w, 4 if alpha else 3), np.uint8); img[..., :3] = RED
    if alpha:
        img[..., 3] = 255
    for start, n in ((0, 3), (1, 3)):
        wr = encode.ChunkedWriter(out, {fmt: encode.video_args(fmt)}, (w, h), 10, 5, "s", chunk_frames=2, start_chunk=start)
        for _ in range(n):
            wr.write(img)
        wr.kill() if start == 0 else wr.close()
    path = wr.paths[fmt]
    assert encode.count_frames(path) == 5 and colour_tags(path) == ("tv", "bt709")
    for n in (0, 4):
        got = decoded_bt709(path, n, w, h)[4:-4, 4:-4].reshape(-1, 3).mean(0)
        assert np.abs(got - RED).max() <= 4, (n, got)


def broken_encoder(*a, **k):
    raise RuntimeError("the encoder broke")


def test_an_encoder_error_ends_the_estimate(studio, monkeypatch):
    """Before, an error in the encoder's thread was lost and the estimate said 0 MB (G-62)."""
    monkeypatch.setattr(encode, "encode_sample", broken_encoder)
    e = Estimator(studio.ren, short_spec(studio.ds, seconds=0.3))
    with pytest.raises(RuntimeError, match="encoder broke"):
        for _ in e.steps():
            pass
    assert e.result is None


def estimate(app, win, seconds=120):
    import time
    from test_movie_output import run_for
    win.tabs.setCurrentIndex(win.TAB_MOVIE); win.prev_timer.stop(); win.export.wait.stop(); win.export.start()
    end = time.monotonic() + seconds
    while win.export.est_gen is not None:
        assert time.monotonic() < end
        run_for(app, 0.02)
    return win.export.est_lbl.text()


def test_the_window_says_why_there_is_no_estimate(window, monkeypatch):
    app, win, errs = window; monkeypatch.setattr(encode, "encode_sample", broken_encoder)
    assert estimate(app, win) == "Could not estimate the file sizes: the encoder broke" and win.export.cache == {}
    assert not errs, errs


def test_prores_sizes_per_minute_are_shown(window):
    """Before, the length of the movie was gone when the result was shown, so "per minute" never was (G-59)."""
    app, win, errs = window; win.export.fmt["mov-prores"].setChecked(True)
    assert "Editing MOV per minute: about" in estimate(app, win)
    win.export.stop(); win.export.est_lbl.setText(""); win.export.start()  # from the cache
    assert win.export.est is None and "Editing MOV per minute: about" in win.export.est_lbl.text()
    assert not errs, errs
