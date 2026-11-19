"""Software rendering (OSMesa) is asked for only where it can start; elsewhere it hard-crashes the process (F-13)."""
import os
from types import SimpleNamespace
from unittest import mock

import numpy as np

from tomoreveal import render

VAR = "VTK_DEFAULT_OPENGL_WINDOW"


def _environ_after_headless_renderer(monkeypatch, available):
    """The environment a headless Renderer leaves behind. A plain dict stands in for os.environ: setting the
    real variable reaches VTK natively and is not undone by monkeypatch, so later real windows would crash."""
    import pyvista
    env = {k: v for k, v in os.environ.items() if k != VAR}
    monkeypatch.setattr(os, "environ", env)
    monkeypatch.setattr(render, "software_rendering_available", lambda: available)
    monkeypatch.setattr(pyvista, "Plotter", mock.MagicMock())  # no real window is created
    render.Renderer(SimpleNamespace(shape=(4, 6, 8), meshes={}), headless=True)
    return env


def test_headless_does_not_ask_for_osmesa_where_it_is_not_available(monkeypatch):
    assert VAR not in _environ_after_headless_renderer(monkeypatch, False)


def test_headless_asks_for_osmesa_where_it_is_available(monkeypatch):
    assert _environ_after_headless_renderer(monkeypatch, True).get(VAR) == "vtkOSOpenGLRenderWindow"


def test_software_rendering_is_never_available_on_windows(monkeypatch):
    monkeypatch.setattr(render.sys, "platform", "win32")
    assert render.software_rendering_available() is False
