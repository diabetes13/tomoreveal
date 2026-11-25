"""Outlines on some structures only, in one part (Movie tab ▸ Look and effects in this part ▸ Outline these)."""
import numpy as np

from tomoreveal import look as lk
from tomoreveal import storyboard as sb
from test_part_effects import two_parts
from test_tidy_panels import pump, window  # noqa: F401 (fixture)


def test_part_look_keeps_a_list_of_structures():
    assert sb.part_look({"look": {"outline_only": ["b", "a", "a"]}}) == {"outline_only": ["a", "b"]}
    assert sb.part_look({"look": {"outline_only": []}}) == {"outline_only": []}
    assert sb.part_look({"look": {"outline_only": "a"}}) == {}  # not a list: ignored


def test_only_the_listed_structures_get_an_outline(studio):
    ds, ren = studio.ds, studio.ren
    spec, a, b = two_parts(ds)
    lk.set_look(spec, outline="thick")
    f = sb.build_plan(spec, ds).frames[5]
    keys = [ds.key_of(k) for k in ds.classes]
    mem = next(ds.key_of(k) for k, c in ds.classes.items() if c["name"] == "membrane")

    def draw(lk_=None, plain=()):
        sp = {**spec, "classes": {k: dict(v, no_outline=k in plain) for k, v in spec["classes"].items()}}
        return ren.draw(dict(f, lk=lk_ or {}), sp, (320, 180)).astype(int)
    every = draw()
    assert np.array_equal(draw({"outline_only": keys}), every)  # all listed = as before
    assert np.array_equal(draw({"outline_only": []}), draw(plain=keys))  # none listed = no outlines
    only = draw({"outline_only": [mem]})
    assert np.array_equal(only, draw(plain=[k for k in keys if k != mem]))  # the membrane only
    assert np.abs(only - every).mean() > 0.2
    # the part's list wins over the Structures tab: the membrane is outlined here although its box is unticked
    assert np.array_equal(draw({"outline_only": [mem]}, plain=[mem]), only)


def test_the_movie_tab_picks_structures_to_outline(window):
    from PySide6 import QtWidgets
    app, win, errs = window
    lk.set_look(win.spec, outline="off")
    p = sb.new_part("overview", motion="none", seconds=2)
    win.editor.insert(p, "start"); pump(app); pid = p["id"]
    win.select_part(pid); pump(app)
    btn = next(b for b in win.parts_panel.findChildren(QtWidgets.QToolButton) if b.toolTip().startswith("Outlines on some"))
    assert btn.text().startswith("every structure")
    menu = btn.menu(); menu.aboutToShow.emit()
    names = [a.text() for a in menu.actions() if a.isCheckable()]
    assert names[0].startswith("Every structure") and len(names) > 2
    target = next(a for a in menu.actions() if a.isCheckable() and a.text() == names[1])
    target.setChecked(False); pump(app)  # untick one structure: every other one stays outlined
    q = win.parts_panel.part(pid); own = sb.part_look(q)
    assert len(own["outline_only"]) == len(names) - 2
    assert own["outline"] == "medium"  # outlines were off in the Look tab: picking structures turns them on here
    menu.aboutToShow.emit()
    next(a for a in menu.actions() if a.text().startswith("Every structure")).trigger(); pump(app)
    assert "outline_only" not in sb.part_look(win.parts_panel.part(pid))
    assert btn.text().startswith("every structure")
    win.do_preview(); pump(app)
    assert not errs
