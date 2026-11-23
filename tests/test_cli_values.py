"""Command-line values are checked before anything is loaded or drawn (F-36, F-37, F-40)."""
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

