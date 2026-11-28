"""The Presets tab (3.5): every look as a small picture of the user's own data, and looks the user saved.

Built-in looks come from `look.LOOKS`; saved ones are kept in QSettings ("presets/looks", JSON {name: preset}), so they
are offered in every project. A saved preset holds the whole Look (style, lighting, shadows, vignette and blur, but not
the anti-aliasing) and the background colour."""
import copy
import json

from PySide6 import QtCore as QtC, QtGui as QtG, QtWidgets as QtW

from . import look as lk
from .gui_fold import Section
from .gui_theme import DIM

THUMB = (224, 126)
KEY = "presets/looks"


def saved_presets():
    try:
        out = json.loads(QtC.QSettings().value(KEY, "{}") or "{}")
        return out if isinstance(out, dict) else {}
    except (TypeError, ValueError):
        return {}


def _store(presets):
    QtC.QSettings().setValue(KEY, json.dumps(presets))


def preset_of(spec):
    """What a saved preset keeps of a project's settings."""
    own = copy.deepcopy(spec.get("look") or {}); own.pop("aa", None)
    return {"look": own, "bg": spec.get("style", {}).get("bg", "#000000")}


def apply_preset(spec, p):
    """Put a saved preset into a project's settings (its anti-aliasing choice is kept)."""
    aa = (spec.get("look") or {}).get("aa")
    spec["look"] = copy.deepcopy(p.get("look") or {})
    if aa is not None:
        spec["look"]["aa"] = aa
    spec.setdefault("style", {})["bg"] = p.get("bg", "#000000")
    return spec


class PresetsPanel(QtC.QObject):
    def __init__(self, win, layout):
        super().__init__(win); self.win = win; self.cards = {}; self._todo = []; self._key = None
        g = Section("Looks", "presets_looks", True, "Click a picture to use that look. Each one can then be changed in the Look tab.")
        layout.addWidget(g); v = QtW.QVBoxLayout(g.body)
        note = QtW.QLabel("Click a picture to use that look. The pictures show the frame in the preview."); note.setStyleSheet(DIM)
        note.setWordWrap(True); v.addWidget(note)
        self.grid = QtW.QGridLayout(); self.grid.setSpacing(6); v.addLayout(self.grid)
        for i, (name, key) in enumerate(lk.LOOKS):
            b = self._card(name, lk.LOOK_NOTES.get(key, ""), lambda _=False, key=key: self.use_builtin(key))
            self.cards[("look", key)] = b; self.grid.addWidget(b, i // 2, i % 2)
        self.redo = QtW.QPushButton("Make the pictures again"); self.redo.setToolTip("Draw the pictures again from the frame in the preview")
        self.redo.clicked.connect(lambda: self.refresh(force=True)); v.addWidget(self.redo, 0, QtC.Qt.AlignLeft)
        g = Section("My presets", "presets_mine", True, "Looks you saved. They are offered in every project.")
        layout.addWidget(g); v = QtW.QVBoxLayout(g.body)
        self.save_btn = QtW.QPushButton("Save the current look as a preset...")
        self.save_btn.setToolTip("Keep the current look (style, lighting, shadows, vignette, blur and background) under a name, "
                                 "to use it again in any project")
        self.save_btn.clicked.connect(self._ask_save); v.addWidget(self.save_btn, 0, QtC.Qt.AlignLeft)
        self.mine = QtW.QGridLayout(); self.mine.setSpacing(6); v.addLayout(self.mine)
        self.none_lbl = QtW.QLabel("None yet."); self.none_lbl.setStyleSheet(DIM); v.addWidget(self.none_lbl)
        self.timer = QtC.QTimer(self); self.timer.setInterval(0); self.timer.timeout.connect(self._draw_next)
        self.rebuild_mine()

    # ---------- cards ----------
    def _card(self, name, tip, fn):
        b = QtW.QToolButton(); b.setText(name); b.setToolTip(tip or name); b.setToolButtonStyle(QtC.Qt.ToolButtonTextUnderIcon)
        b.setIconSize(QtC.QSize(*THUMB)); b.setAutoRaise(True); b.setSizePolicy(QtW.QSizePolicy.Expanding, QtW.QSizePolicy.Fixed)
        b.setCursor(QtC.Qt.PointingHandCursor); b.clicked.connect(fn)
        return b

    def rebuild_mine(self):
        while self.mine.count():
            it = self.mine.takeAt(0); w = it.widget()  # store it first: after setParent(None), it.widget() is None
            if w is not None:
                w.setParent(None); w.deleteLater()
        for k in [k for k in self.cards if k[0] == "mine"]:
            self.cards.pop(k)
        presets = saved_presets()
        for i, name in enumerate(sorted(presets, key=str.lower)):
            b = self._card(name, f"Your preset '{name}'. Right-click to delete it.", lambda _=False, n=name: self.use_saved(n))
            b.setContextMenuPolicy(QtC.Qt.CustomContextMenu)
            b.customContextMenuRequested.connect(lambda pos, n=name, b=b: self._menu(n, b.mapToGlobal(pos)))
            self.cards[("mine", name)] = b; self.mine.addWidget(b, i // 2, i % 2)
        self.none_lbl.setVisible(not presets)
        self._key = None

    def _menu(self, name, at):
        m = QtW.QMenu(self.win); a = m.addAction(f"Delete '{name}'")
        if m.exec(at) is a:
            self.delete(name)

    # ---------- actions ----------
    def use_builtin(self, key):
        w = self.win
        if w.spec is None:
            return
        w.look35.choose(key); w.say(f"Look: {dict((d, t) for t, d in lk.LOOKS)[key]}.")

    def use_saved(self, name):
        w = self.win; p = saved_presets().get(name)
        if w.spec is None or p is None:
            return
        apply_preset(w.spec, p); w.build_controls(); w.schedule(); w.say(f"Look: your preset '{name}'.")

    def save(self, name):
        name = (name or "").strip()
        if not name or self.win.spec is None:
            return False
        presets = saved_presets(); presets[name] = preset_of(self.win.spec); _store(presets)
        self.rebuild_mine(); self.refresh()
        return True

    def delete(self, name):
        presets = saved_presets()
        if presets.pop(name, None) is not None:
            _store(presets); self.rebuild_mine()

    def _ask_save(self):
        name, ok = QtW.QInputDialog.getText(self.win, "Save the look", "Name of the preset:")
        if ok and name.strip():
            if name.strip() in saved_presets() and QtW.QMessageBox.question(
                    self.win, "Save the look", f"Replace your preset '{name.strip()}'?") != QtW.QMessageBox.Yes:
                return
            self.save(name)

    # ---------- pictures ----------
    def refresh(self, force=False):
        """Draw the pictures from the preview's frame (one per timer tick, so the window stays responsive)."""
        w = self.win
        if w.spec is None or w.st.ren is None or not w.st.can_show:
            return
        key = (json.dumps(w.spec.get("classes"), sort_keys=True, default=str), w.frame, tuple(sorted(saved_presets())))
        if key == self._key and not force:
            return
        self._key = key; self._todo = list(self.cards); self.resume()

    def resume(self):
        """Go on with the pictures that are left, unless files are read, a movie is made or the preview plays (the
        window calls this when each of those ends)."""
        w = self.win
        if self._todo and not w.busy and w.gen is None and not w.playing:
            self.timer.start()

    def _draw_next(self):
        w = self.win
        if w.busy or w.gen is not None or w.playing:
            self.timer.stop(); return  # the rest waits for resume()
        held = w.mouse_down()  # a drag: this picture waits (not on a 0 ms timer, which would keep a core busy)
        self.timer.setInterval(50 if held else 0)
        if held:
            return
        if not self._todo or w.spec is None or w.st.ren is None:
            self.timer.stop(); return
        which = self._todo.pop(0); b = self.cards.get(which)
        if b is None:
            return
        sp = w.spec_for(); sp["style"].update(legend=False, slice_label=False, title="")
        if which[0] == "look":
            lk.apply_look(sp, which[1])
        else:
            p = saved_presets().get(which[1])
            if p is None:
                return
            apply_preset(sp, p)
        try:
            plan, i = w.st.plan_frame(sp, {"t": w.frame / max(1, w.total - 1)})
            img = w.st.ren.draw(plan.frames[i], sp, THUMB, aa="fxaa", annotate=False)  # no smoothing setting here: Fast
        except Exception:  # a picture is a nicety: never stop the window for it
            return
        import numpy as np
        a = np.ascontiguousarray(img[..., :3]); h, wd = a.shape[:2]
        q = QtG.QImage(a.tobytes(), wd, h, 3 * wd, QtG.QImage.Format_RGB888).copy()
        b.setIcon(QtG.QIcon(QtG.QPixmap.fromImage(q)))
        if not self._todo:
            self.timer.stop()
