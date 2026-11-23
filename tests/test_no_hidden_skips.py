"""A real error while loading must fail the tests, not be reported as "3D rendering is not available" (F-12)."""
import pytest

import tomoreveal as tr
from conftest import load_demo_studio


def test_an_error_during_load_fails_instead_of_skipping(demo, monkeypatch):
    def broken(self, *a, **k):
        raise RuntimeError("injected bug in Studio._update")
    monkeypatch.setattr(tr.Studio, "_update", broken)
    try:
        with pytest.raises(RuntimeError, match="injected bug"):
            load_demo_studio(demo)
    except pytest.skip.Exception:
        pytest.fail("a load error was reported as a skip")
