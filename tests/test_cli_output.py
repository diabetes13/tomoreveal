"""CLI output to a pipe or file never crashes on characters the console cannot show (F-17)."""
import os
import subprocess
import sys

import pytest

from fake_session import write_session


def test_inspect_into_a_cp1252_pipe(tmp_path):
    ses = write_session(str(tmp_path / "s.ORSSession"), names=("微管", "Größe", "background"))
    env = dict(os.environ, PYTHONIOENCODING="cp1252:strict")
    r = subprocess.run([sys.executable, "-m", "tomoreveal", "inspect", ses], capture_output=True, env=env, timeout=120)
    assert r.returncode == 0, r.stderr.decode("cp1252", "replace")
    assert "Größe".encode("cp1252") in r.stdout


def test_no_console_at_all(monkeypatch):
    """Under pythonw (the desktop shortcut) sys.stdout and sys.stderr are None."""
    from tomoreveal import cli
    monkeypatch.setattr(sys, "stdout", None); monkeypatch.setattr(sys, "stderr", None)
    monkeypatch.setattr(sys, "argv", ["tomoreveal", "--version"])
    with pytest.raises(SystemExit) as e:
        cli.main()
    assert e.value.code in (0, None)
