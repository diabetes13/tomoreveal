"""The Data tab of the desktop window: which tomogram to show, and which segmentations."""
import os

from PySide6 import QtCore as QtC, QtWidgets as QtW

from .gui_theme import DIM
VOLUMES = ("Volumes (*.mrc *.MRC *.rec *.map *.mrcs *.st *.tif *.tiff *.TIF *.TIFF);;All files (*)")
SEGMENTATIONS = ("Segmentations (*.ORSSession *.orssession *.mrc *.MRC *.rec *.map *.mrcs *.tif *.tiff *.TIF *.TIFF);;"
                 "All files (*)")


def clear_layout(lay):
    """Remove every row of a layout at once. Each widget is stored before setParent(None): afterwards item.widget() is None."""
    while lay.count():
        item = lay.takeAt(0); w = item.widget(); sub = item.layout()
        if w is not None:
            w.setParent(None); w.deleteLater()
        elif sub is not None:
            clear_layout(sub)


def dim(text, wrap=True, indent=0):
    lab = QtW.QLabel(text); lab.setStyleSheet(DIM); lab.setWordWrap(wrap)
    if indent:
        lab.setContentsMargins(indent, 0, 0, 0)
    return lab


class DataPanel(QtW.QWidget):
    """Lists the tomogram choices (one can be used) and the segmentation sources (any number can be switched on).

    It changes what is loaded only through Studio.add_files, set_tomogram_source, set_segmentation_enabled and
    remove_source, each run by `run(fn, message)` in the window's worker thread (fn takes the log function)."""

    def __init__(self, studio, run, settings, on_demo, on_start_over, names=None):
        super().__init__()
        self.st, self.run, self.settings = studio, run, settings
        self.names = names  # display names the user gave (get(key) / set(key, name or None)), stored in the project
        self.info = None; self.busy = False
        lay = QtW.QVBoxLayout(self)
        lay.addWidget(dim("Drop files anywhere on the window, or:"))
        row = QtW.QHBoxLayout()
        self.add_tomo_btn = QtW.QPushButton("Add tomogram...")
        self.add_tomo_btn.setToolTip("The file is always used as a tomogram, even if it looks like a segmentation")
        self.add_tomo_btn.clicked.connect(self.add_tomogram)
        self.add_seg_btn = QtW.QPushButton("Add segmentation...")
        self.add_seg_btn.setToolTip("A Dragonfly .ORSSession, a label volume, or easymode / Ais files (name__feature.mrc)")
        self.add_seg_btn.clicked.connect(self.add_segmentation)
        row.addWidget(self.add_tomo_btn); row.addWidget(self.add_seg_btn); row.addStretch(1); lay.addLayout(row)
        row = QtW.QHBoxLayout()
        self.demo_btn = QtW.QPushButton("Try demo data"); self.demo_btn.setToolTip("Load a small made-up tomogram to try the program")
        self.demo_btn.clicked.connect(on_demo)
        self.over_btn = QtW.QPushButton("Start over"); self.over_btn.setToolTip("Unload everything")
        self.over_btn.clicked.connect(on_start_over)
        row.addWidget(self.demo_btn); row.addStretch(1); row.addWidget(self.over_btn); lay.addLayout(row)
        self.progress = dim(""); self.progress.setVisible(False); lay.addWidget(self.progress)
        self.empty_lbl = dim("Nothing loaded yet. A Dragonfly session alone is enough: its stored image is used as the tomogram.")
        lay.addWidget(self.empty_lbl)

        self.tomo_head = QtW.QLabel("<b>TOMOGRAM</b>"); lay.addWidget(self.tomo_head)
        self.tomo_shared = dim("", wrap=False); self.tomo_shared.setContentsMargins(8, 0, 0, 0); lay.addWidget(self.tomo_shared)
        self.tomo_w = QtW.QWidget(); self.tomo_lay = QtW.QVBoxLayout(self.tomo_w); self.tomo_lay.setContentsMargins(8, 0, 0, 0)
        lay.addWidget(self.tomo_w)
        self.tomo_group = QtW.QButtonGroup(self); self.tomo_group.setExclusive(True)
        self.dims_lbl = dim(""); self.dims_lbl.setContentsMargins(8, 0, 0, 0); lay.addWidget(self.dims_lbl)
        self.match_lbl = dim(""); self.match_lbl.setContentsMargins(8, 0, 0, 0); lay.addWidget(self.match_lbl)

        self.seg_head = QtW.QLabel("<b>SEGMENTATION</b>"); lay.addWidget(self.seg_head)
        self.seg_shared = dim("", wrap=False); self.seg_shared.setContentsMargins(8, 0, 0, 0); lay.addWidget(self.seg_shared)
        self.seg_w = QtW.QWidget(); self.seg_lay = QtW.QVBoxLayout(self.seg_w); self.seg_lay.setContentsMargins(8, 0, 0, 0)
        lay.addWidget(self.seg_w)
        self.hint = dim("Right-click a file to use it as a tomogram or as a segmentation instead.")
        lay.addWidget(self.hint)
        lay.addStretch(1)
        self.refresh(studio.info())

    # ---- actions ----
    def _ask(self, title, filt):
        start = self.settings.value("lastdir", "") or os.path.expanduser("~")
        paths, _ = QtW.QFileDialog.getOpenFileNames(self, title, start, filt)
        if paths:
            self.settings.setValue("lastdir", os.path.dirname(paths[0]))
        return paths

    def add_tomogram(self, paths=None):
        paths = paths or self._ask("Add a tomogram", VOLUMES)
        if paths:
            self.run(lambda log: self.st.add_files(paths, as_tomogram=paths, log=log, make_renderer=False))

    def add_segmentation(self, paths=None):
        paths = paths or self._ask("Add a segmentation", SEGMENTATIONS)
        if paths:
            self.run(lambda log: self.st.add_files(paths, as_segmentation=paths, log=log, make_renderer=False))

    def choose_tomogram(self, key):
        if self.info is None or key == self.info.get("tomogram"):
            return
        self.run(lambda log: self.st.set_tomogram_source(key, log=log, make_renderer=False), "Changing the tomogram ...")

    def switch(self, key, on):
        self.run(lambda log: self.st.set_segmentation_enabled(key, on, log=log, make_renderer=False),
                 "Reading the segmentation and building 3D surfaces ..." if on else "Updating ...")

    def remove(self, key):
        self.run(lambda log: self.st.remove_source(key, log=log, make_renderer=False), "Updating ...")

    def _menu(self, widget, key, rename_key=None):
        """Right-click on a file: rename it (in TomoReveal only), use it as the other kind, remove it."""
        if ":" not in key:
            return
        kind, path = key.split(":", 1)
        widget.setContextMenuPolicy(QtC.Qt.CustomContextMenu)
        rk = rename_key or key

        def show(pos):
            m = QtW.QMenu(widget)
            if self.names is not None and self.names.available():
                m.addAction("Rename...", lambda: self.rename(rk))
                if self.names.get(rk):
                    m.addAction("Reset name", lambda: self.set_name(rk, None))
                m.addSeparator()
            if kind == "file":
                m.addAction("Use as segmentation", lambda: self.add_segmentation([path]))
            elif kind in ("labels", "scores"):
                m.addAction("Use as tomogram", lambda: self.add_tomogram([path]))
            if kind not in ("dragonfly",) and not (kind == "session" and path.count(":") and path.rsplit(":", 1)[1].isdigit()):
                m.addAction("Remove", lambda: self.remove(key))
            m.exec(widget.mapToGlobal(pos))
        widget.customContextMenuRequested.connect(show)

    def rename(self, key):
        cur = self.names.get(key) or ""
        text, ok = QtW.QInputDialog.getText(self, "Rename", "Name shown in TomoReveal (the file on disk is not renamed):", text=cur)
        if ok:
            self.set_name(key, text.strip() or None)

    def set_name(self, key, name):
        self.names.set(key, name); self.refresh(self.info)

    def _shown(self, key, short):
        """The user's name for a file, or its short name."""
        own = self.names.get(key) if self.names is not None else None
        return own or short

    @staticmethod
    def _elide(widget, text, width=330):
        return widget.fontMetrics().elidedText(text, QtC.Qt.ElideMiddle, width)

    def _shared(self, label, prefix, suffix):
        txt = (prefix + "\u2026" + suffix) if (prefix or suffix) else ""
        label.setText(self._elide(label, "all start with: " + txt, 380) if txt else ""); label.setVisible(bool(txt))
        label.setToolTip(f"The part shared by every name below: {prefix} \u2026 {suffix}" if txt else "")

    @staticmethod
    def _remove_btn(fn):
        b = QtW.QPushButton("Remove"); b.setToolTip("Unload this file (your file on disk is not touched)")
        b.clicked.connect(fn)
        return b

    # ---- state ----
    def set_busy(self, flag, msg=""):
        self.busy = flag
        for w in (self.add_tomo_btn, self.add_seg_btn, self.demo_btn, self.over_btn, self.tomo_w, self.seg_w):
            w.setEnabled(not flag)
        self.progress.setText(msg or ""); self.progress.setVisible(bool(flag and msg))

    def log(self, msg):
        if self.busy:
            self.progress.setText(msg); self.progress.setVisible(True)

    def refresh(self, info):
        self.info = info
        choices = info.get("tomogram_choices", []); sources = info.get("segmentation_sources", [])
        loaded = len(choices) > 1 or bool(sources)
        self.empty_lbl.setVisible(not loaded); self.hint.setVisible(loaded)
        # tomogram: radio buttons, only one can be used
        clear_layout(self.tomo_lay)
        for b in self.tomo_group.buttons():
            self.tomo_group.removeButton(b)
        self.tomo_head.setVisible(loaded); self.tomo_w.setVisible(loaded)
        from .names import short_names
        real = [c for c in choices if c["key"]]
        pre, suf, shorts = short_names([c.get("name") or c["label"] for c in real])
        short = {c["key"]: sh for c, sh in zip(real, shorts)}
        self._shared(self.tomo_shared, pre, suf); self.tomo_shared.setVisible(bool(loaded and (pre or suf)))
        for c in (choices if loaded else []):
            row = QtW.QWidget(); rl = QtW.QHBoxLayout(row); rl.setContentsMargins(0, 0, 0, 0)
            text = "None" if not c["key"] else f"{self._shown(c['key'], short[c['key']])}  ({c.get('where', '')})"
            rb = QtW.QRadioButton(self._elide(self, text)); rb.setChecked(c["key"] == info.get("tomogram"))
            tip = "Show no tomogram" if not c["key"] else f"{c.get('name', c['label'])}\n{c.get('path', '')}"
            if c.get("grid"):
                tip += "\n" + " × ".join(map(str, c["grid"])) + " voxels"
            rb.setToolTip(tip)
            rb.clicked.connect(lambda _=False, key=c["key"]: self.choose_tomogram(key))
            self.tomo_group.addButton(rb); rl.addWidget(rb, 1)
            if c["key"] and c["key"].startswith("file:"):
                rl.addWidget(self._remove_btn(lambda _=False, key=c["key"]: self.remove(key)))
            if c["key"]:
                self._menu(row, c["key"])
            self.tomo_lay.addWidget(row)
        dims = info.get("dims")
        txt = ""
        if dims and info.get("has_tomogram"):
            txt = " × ".join(map(str, dims)) + " voxels" + (f" · {info['voxel'] / 10:.2f} nm per voxel" if info.get("voxel") else "")
        elif dims:
            txt = "No tomogram shown. The segmentation is drawn on its own (" + " × ".join(map(str, dims)) + " voxels)."
        self.dims_lbl.setText(txt); self.dims_lbl.setVisible(bool(txt) and loaded)
        self.match_lbl.setText(info.get("match_note") or ""); self.match_lbl.setVisible(bool(info.get("match_note")))
        # segmentation: tick boxes grouped by file
        clear_layout(self.seg_lay)
        self.seg_head.setVisible(bool(sources)); self.seg_w.setVisible(bool(sources))
        hidden = {s["key"]: s for s in info.get("sessions", [])}
        groups = []
        for s in sources:
            gkey = s["session"] or s["key"]
            if not groups or groups[-1][0] != gkey:
                groups.append((gkey, []))
            groups[-1][1].append(s)
        gpre, gsuf, gshort = short_names([items[0]["group"] for _, items in groups])
        self._shared(self.seg_shared, gpre, gsuf); self.seg_shared.setVisible(bool(sources and (gpre or gsuf)))
        for (gkey, items), gname in zip(groups, gshort):
            head = QtW.QWidget(); hl = QtW.QHBoxLayout(head); hl.setContentsMargins(0, 6, 0, 0)
            kind = " (easymode)" if items[0]["kind"] == "scores" else ""
            glab = QtW.QLabel(self._elide(self, self._shown(gkey, gname) + kind, 300)); hl.addWidget(glab, 1)
            glab.setToolTip(f"{items[0]['group']}\n{items[0].get('path', '')}\nRight-click to rename it in TomoReveal")
            hl.addWidget(self._remove_btn(lambda _=False, key=gkey: self.remove(key)))
            self._menu(head, gkey); self.seg_lay.addWidget(head)
            _, _, ishort = short_names([s["label"] for s in items]) if items[0]["kind"] == "dragonfly" else ("", "", [s["label"] for s in items])
            for s, sh in zip(items, ishort):
                n = s["count"]
                text = s["label"] if s["kind"] == "scores" else f"{self._shown(s['key'], sh)} ({n} structure{'s' if n != 1 else ''})"
                if s["kind"] != "dragonfly":
                    text = ", ".join(s["names"]) if s["kind"] == "scores" else f"{n} structure{'s' if n != 1 else ''}"
                tick = QtW.QCheckBox(self._elide(self, text, 320)); tick.setChecked(bool(s["enabled"] and s["usable"])); tick.setEnabled(bool(s["usable"]))
                tick.setContentsMargins(12, 0, 0, 0)
                tick.setToolTip("\n".join(x for x in (s["label"], s["reason"] or ", ".join(s["names"]),
                                                          "Tick to show it; untick to hide it without unloading it") if x))
                tick.clicked.connect(lambda on, key=s["key"]: self.switch(key, on))
                self._menu(tick, s["key"])
                self.seg_lay.addWidget(tick)
                if not s["usable"]:
                    self.seg_lay.addWidget(dim(s["reason"], indent=34))
                elif s["kind"] != "scores" and s["names"]:
                    names = ", ".join(s["names"][:8]) + (", ..." if len(s["names"]) > 8 else "")
                    self.seg_lay.addWidget(dim(names, indent=34))
            h = hidden.get(gkey, {})
            notes = []
            if h.get("previews"):
                notes.append(f"{h['previews']} single-slice preview{'s' if h['previews'] != 1 else ''} from the Segmentation Wizard hidden")
            if h.get("undo"):
                notes.append(f"{h['undo']} undo cop{'ies' if h['undo'] != 1 else 'y'} hidden")
            if notes:
                self.seg_lay.addWidget(dim("; ".join(notes), indent=12))
        w = self.window()
        if hasattr(w, "apply_help"):
            w.apply_help()
