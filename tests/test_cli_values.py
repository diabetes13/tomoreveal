"""Command-line values are checked before anything is loaded or drawn (F-36, F-37, F-40)."""
import os
import sys

import pytest

from tomoreveal import cli


@pytest.fixture
def run(monkeypatch):
    """Run `tomoreveal ...` with the command itself replaced by a recorder; returns the args it got (or None)."""
    got = []
    for name in ("cmd_movie", "cmd_preview"):
        monkeypatch.setattr(cli, name, lambda a: got.append(a))

    def go(*argv):
        monkeypatch.setattr(sys, "argv", ["tomoreveal", *argv])
        cli.main()
        return got[-1] if got else None
    return go


@pytest.mark.parametrize("argv, word", [
    (("preview", "t.mrc", "--size", "1920"), "--size"),
    (("preview", "t.mrc", "--size", "1920x0"), "--size"),
    (("preview", "t.mrc", "--contrast", "5"), "--contrast"),
    (("preview", "t.mrc", "--contrast", "99,1"), "--contrast"),
    (("movie", "t.mrc", "--z-range", "10"), "--z-range"),
    (("movie", "t.mrc", "--z-range", "30,10"), "--z-range"),
    (("preview", "t.mrc", "--clip", "10,5,0,9,0,9"), "--clip"),
])
def test_bad_values_stop_with_a_message(run, argv, word):
    """F-36: these gave IndexError tracebacks, or quietly a single slab or the whole volume."""
    with pytest.raises(SystemExit) as e:
        run(*argv)
    assert word in str(e.value)


@pytest.mark.parametrize("argv, word", [
    (("movie", "t.mrc", "--fps", "0"), "--fps"),
    (("preview", "t.mrc", "--fov", "0"), "--fov"),
    (("preview", "t.mrc", "--fov", "180"), "--fov"),
])
def test_zero_fps_or_field_of_view_stops(run, argv, word):
    """F-37: fps 0 or fov 0 gave ZeroDivisionError or an infinite camera distance."""
    with pytest.raises(SystemExit) as e:
        run(*argv)
    assert word in str(e.value)


def test_odd_size_is_announced(run, capsys):
    """F-37: an odd size was rounded down without a word."""
    assert run("preview", "t.mrc", "--size", "1919x1081") is not None
    assert "1918x1080" in capsys.readouterr().out
    assert run("preview", "t.mrc") is not None and "rounded" not in capsys.readouterr().out


@pytest.mark.parametrize("argv, out", [
    (("movie", "t.mrc", "-o", "out"), "out"),  # 3.4: --format adds the extension of each movie file (encode.output_paths)
    (("movie", "t.mrc", "-o", "movies/out.mp4"), "movies/out.mp4"),
    (("preview", "t.mrc", "-o", "sheet"), "sheet.png"),
])
def test_output_without_an_extension_gets_one(run, argv, out):
    """F-40: `-o out` rendered every frame, then crashed in imageio (no format for a file without an extension)."""
    assert run(*argv).out == out
    if argv[0] == "movie":
        from tomoreveal.encode import output_paths
        assert output_paths(out, ["mp4"])["mp4"].endswith(".mp4")


def one_line_message(e, word):
    """The way user errors end: exit code 1 (SystemExit with a text), one line, no traceback."""
    text = str(e.value)
    return text.startswith("tomoreveal: ") and word in text and "\n" not in text and "Traceback" not in text


@pytest.mark.parametrize("argv, word", [
    (("movie", "t.mrc", "--bg", "blurple"), "--bg"),
    (("preview", "t.mrc", "--bg", "#12345"), "--bg"),
    (("movie", "t.mrc", "--scale-bar", "50nm"), "--scale-bar"),
    (("movie", "t.mrc", "--scale-bar", "0"), "--scale-bar"),
])
def test_a_bad_colour_or_scale_bar_stops_with_a_message(run, argv, word):
    """G-60: these ended in a traceback once the data were loaded."""
    with pytest.raises(SystemExit) as e:
        run(*argv)
    assert one_line_message(e, word)


@pytest.mark.parametrize("argv", [("movie", "t.mrc", "--bg", "white", "--scale-bar"), ("movie", "t.mrc", "--bg", "#202020", "--scale-bar", "100")])
def test_good_colours_and_scale_bars_are_taken(run, argv):
    assert run(*argv) is not None


@pytest.fixture
def demo_args(demo, tmp_path):
    return [demo["tomogram"], "--labels", demo["labels"], "--size", "64x36", "--no-aa"]


@pytest.mark.parametrize("z", ["48", "999", "-5"])
def test_a_slice_outside_the_volume_stops_with_a_message(demo_args, tmp_path, needs_3d, monkeypatch, z):
    """G-60: --z 999 ended in an IndexError, and --z -5 quietly counted from the end. The demo has 48 slices."""
    monkeypatch.setattr(sys, "argv", ["tomoreveal", "preview", *demo_args, "--z", z, "-o", str(tmp_path / "p.png")])
    with pytest.raises(SystemExit) as e:
        cli.main()
    assert one_line_message(e, "--z") and "0 to 47" in str(e.value)
    monkeypatch.setattr(sys, "argv", ["tomoreveal", "preview", *demo_args, "--z", "47", "-o", str(tmp_path / "p.png")])
    cli.main()


def test_the_command_line_says_which_parts_are_left_out(demo_args, tmp_path, needs_3d, monkeypatch, capsys):
    """G-60: a part that can't be drawn was left out of the movie without a word."""
    from tomoreveal.storyboard import focus_shot
    real = cli.cli_spec

    def with_a_lost_part(st, args):
        spec = real(st, args); spec["shots"].append(focus_shot({"class": "labels:gone#9"}, "gone", seconds=1.0))
        return spec
    monkeypatch.setattr(cli, "cli_spec", with_a_lost_part)
    monkeypatch.setattr(sys, "argv", ["tomoreveal", "movie", *demo_args, "--fps", "5", "--sweep-seconds", "0.4", "--end", "none",
                                      "--no-poster", "-o", str(tmp_path / "m.mp4")])
    cli.main()
    err = capsys.readouterr().err
    assert "part 4 is left out: structure not loaded" in err and os.path.isfile(str(tmp_path / "m.mp4"))
