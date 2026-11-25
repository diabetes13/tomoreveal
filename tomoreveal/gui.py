"""The desktop window (Qt)."""
import json
import os
import re
import sys
import tempfile
import time
import traceback

import numpy as np

from .common import UserError, VIEWS
from .demo import make_demo
from .movie import movie_frames, poster_path
from .render import software_rendering_available
from .storyboard import flip_spec, focus_shot, merge_settings, shot_tomogram
from .studio import Studio
from .webapp import cmd_app


def make_icon(size=256):
    from PIL import Image, ImageDraw
    s = size; im = Image.new("RGBA", (s, s), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
    d.rounded_rectangle([0, 0, s - 1, s - 1], radius=round(s * 0.2), fill=(24, 28, 36, 255))
    d.polygon([(s * .08, s * .80), (s * .26, s * .54), (s * .92, s * .54), (s * .74, s * .80)], fill=(196, 199, 204, 255))
    lw = max(2, round(s * .04))
    for cx, cy, r, col in ((.36, .52, .14, (242, 142, 43)), (.63, .46, .10, (77, 163, 255)), (.78, .60, .07, (225, 87, 89))):
        d.ellipse([s * (cx - r), s * (cy - r * .55), s * (cx + r), s * (cy + r * .55)], outline=col + (255,), width=lw)
    d.line([(s * .14, s * .36), (s * .40, s * .26), (s * .66, s * .23), (s * .88, s * .28)], fill=(176, 122, 161, 255), width=lw)
    return im


def cmd_icon(args):
    im = make_icon(256)
    if args.path.lower().endswith(".ico"):
        im.save(args.path, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    else:
        im.save(args.path)
    print(f"wrote {args.path}")


SKIP_NOTE = "The preview skips frames; the movie will be smooth"


def build_gui(args):
    from PySide6 import QtCore as QtC, QtGui as QtG, QtWidgets as QtW
    from .gui_data import DataPanel, clear_layout
    from .gui_explore import ExplorePanel
    from .gui_timeline import Timeline
    from .gui_movie import MovieEditor, PartsPanel
    from .gui_theme import CHOICES as THEMES, DIM, apply_theme, message_color
    from .gui_project import ProjectManager, log_exception, setup_crash_log, start_hang_watch
    from .encode import read_partial
    from .gui_overlays import OverlayControls, OverlayDrag, OverlayToolbar, VoxelRow
    from .look import look_settings, AA_MOVIE, ensure as ensure_look
    from .gui_look import LookControls, style_rows
    from .gui_export import ExportControls
    from .encode import output_paths
    from .gui_widgets import Preview, RangeSlider, TipFilter, Worker, fmt_time, to_qimage
    QUALITY = [("Draft 960 x 540 (fast)", (960, 540)), ("HD 1920 x 1080", (1920, 1080)), ("4K 3840 x 2160 (slow)", (3840, 2160)),
               ("Square 1080 x 1080", (1080, 1080)), ("Portrait 1080 x 1920", (1080, 1920))]

    class Main(QtW.QMainWindow):
        def __init__(self):
            super().__init__()
            self.settings = QtC.QSettings()  # organisation and application names are set in build_gui (tests: the default format)
            outdir = args.outdir or self.settings.value("outdir", "") or os.path.join(os.path.expanduser("~"), "TomoReveal movies")
            self.st = Studio(headless=args.headless, outdir=outdir)
            self.spec = self.info = None; self.frame, self.total, self.cur_shot, self.cur_type = 0, 1, 0, None; self.req = None
            self.selected_part = None  # id of the part selected on the timeline
            self.pick_for = None       # (part id, structure key) while picking the object of a close-up
            self.multi_pick = None     # part id while clicking objects for a close-up of several objects
            self.waypoint_pick = None  # part id while clicking points for a camera path
            self.region_pick = None    # (part id or None for a new close-up, insert after) while drawing a frame
            self.path_pick = None      # part id while clicking the object a camera path follows
            self._path_worker = None
            self.busy = False; self.gen = None; self.pick_mode = False; self.worker = None; self._drag0 = None
            self._thr_pending = {}; self.vox_lbl = {}; self.last_movie = None
            self._struct_open = set()  # structures whose settings are open in the Structures tab
            self._param_pending = {}; self._soften_cb = {}; self._surf_worker = None
            self.setWindowTitle("TomoReveal"); self.setAcceptDrops(True); self.resize(1480, 940)
            self.prev_timer = QtC.QTimer(self); self.prev_timer.setSingleShot(True); self.prev_timer.timeout.connect(self.do_preview)
            self.thr_timer = QtC.QTimer(self); self.thr_timer.setSingleShot(True); self.thr_timer.setInterval(400)
            self.thr_timer.timeout.connect(self.apply_thresholds)
            self.param_timer = QtC.QTimer(self); self.param_timer.setSingleShot(True); self.param_timer.setInterval(500)
            self.param_timer.timeout.connect(self.apply_params)
            self.render_timer = QtC.QTimer(self); self.render_timer.setInterval(0); self.render_timer.timeout.connect(self._render_step)
            self._build(); self._menus(); self.editor = MovieEditor(self); self.show_empty()
            self.project = ProjectManager(self, getattr(args, "files", None) or ())
            self._help_menu(); self.apply_help()
            QtC.QTimer.singleShot(500, self._test_real_shadows)
            # playback in the preview (real time: frames are skipped when drawing is slower than the frame rate)
            self.play_btn = QtW.QPushButton("Play"); self.play_btn.setToolTip("Play the movie in the preview from the playhead (Space)")
            self.play_btn.clicked.connect(self.toggle_play); self.timeline.buttons.insertWidget(self.timeline.buttons.count() - 1, self.play_btn)
            self.play_timer = QtC.QTimer(self); self.play_timer.setInterval(5); self.play_timer.timeout.connect(self._play_step)
            self.playing = None  # dict(t0, f0, spec, frames, size, drawn) while playing
            self.timeline.canvas.frame_changed.connect(lambda _f: self.stop_play())  # any click on the timeline stops it
            self.timeline.part_selected.connect(lambda _p: self.stop_play())
            self._sync_theme()
            hints = QtW.QApplication.instance().styleHints()
            if hasattr(hints, "colorSchemeChanged"):  # follow the desktop while "System" is chosen
                hints.colorSchemeChanged.connect(lambda *_: self.theme == "system" and self.set_theme("system"))

        # ---------- layout ----------
        def _group(self, title, parent, key=None, open=True, tip=None):
            """A settings group; with a key it folds (click its title) and remembers whether it is open (3.5)."""
            from .gui_fold import Section
            g = Section(title, key, open, tip); lay = QtW.QVBoxLayout(g.body); parent.addWidget(g)
            return g, lay

        def _note(self, text, parent):
            lab = QtW.QLabel(text); lab.setWordWrap(True); lab.setStyleSheet(DIM); parent.addWidget(lab)
            return lab

        def _slider(self, parent, label, lo, hi, on_change, fmt):
            row = QtW.QHBoxLayout(); lab = QtW.QLabel(label); lab.setFixedWidth(52)
            s = QtW.QSlider(QtC.Qt.Horizontal); s.setRange(lo, hi); val = QtW.QLabel(""); val.setFixedWidth(52)
            s.valueChanged.connect(lambda v: (val.setText(fmt(v)), on_change(v)))
            row.addWidget(lab); row.addWidget(s, 1); row.addWidget(val); parent.addLayout(row)
            s._val, s._fmt = val, fmt
            return s

        def _setv(self, s, v):  # set a control without triggering its handler
            s.blockSignals(True)
            if isinstance(s, QtW.QAbstractButton):
                s.setChecked(bool(v))
            else:
                s.setValue(v if isinstance(s, QtW.QDoubleSpinBox) else int(round(v)))
            s.blockSignals(False)
            if hasattr(s, "_val"):
                s._val.setText(s._fmt(int(round(v))))

        def _menus(self):
            view = self.menuBar().addMenu("View"); tm = view.addMenu("Theme")
            self.theme_group = QtG.QActionGroup(self); self.theme_group.setExclusive(True); self.theme_acts = {}
            for t, d in THEMES:
                a = QtG.QAction(t, self, checkable=True); a.triggered.connect(lambda _=False, d=d: self.set_theme(d))
                self.theme_group.addAction(a); tm.addAction(a); self.theme_acts[d] = a
            nm = view.addMenu("Moving around in the preview")
            for key, text in (("nav/swap_buttons", "Swap left and right drag (rotate with the right button)"),
                              ("nav/invert_wheel", "Turn the mouse wheel the other way to zoom in")):
                a = QtG.QAction(text, self, checkable=True); a.setChecked(self.settings.value(key, False, type=bool))
                a.toggled.connect(lambda on, key=key: self.settings.setValue(key, bool(on))); nm.addAction(a)
            nm.addSeparator(); nm.addAction("Undo view change", lambda: self.nav.undo()); nm.addAction("Redo view change", lambda: self.nav.redo())

        @property
        def theme(self):
            t = self.settings.value("theme", "system")
            return t if t in ("system", "light", "dark") else "system"

        def _sync_theme(self):
            self.theme_acts[self.theme].setChecked(True)
            self.theme_cb.setCurrentIndex(max(0, self.theme_cb.findData(self.theme)))

        def set_theme(self, choice):
            """Change the window theme now and remember it (QSettings key "theme")."""
            self.settings.setValue("theme", choice)
            apply_theme(QtW.QApplication.instance(), choice); self._sync_theme()
            self.say(*getattr(self, "_msg", ("", None)))
            if self.spec is not None and self.info is not None:
                self.build_controls()  # colour swatches and grey notes
            for w in (self.timeline.canvas, *self.clip_s.values()):
                w.update()

        def _tab(self, name):
            """A scrollable page of the tab widget; returns its layout."""
            scroll = QtW.QScrollArea(); scroll.setWidgetResizable(True); scroll.setHorizontalScrollBarPolicy(QtC.Qt.ScrollBarAsNeeded)
            page = QtW.QWidget(); lay = QtW.QVBoxLayout(page); scroll.setWidget(page)
            lay.setSizeConstraint(QtW.QLayout.SetMinimumSize)  # grow and scroll rather than squash rows together
            self.tabs.addTab(scroll, name); self.pages.append(page)
            return lay

        def _build(self):
            split = QtW.QSplitter(); self.setCentralWidget(split)
            left = QtW.QWidget(); left.setMinimumWidth(470); outer = QtW.QVBoxLayout(left); outer.setContentsMargins(4, 4, 4, 4)
            self.msg_lbl = QtW.QLabel(""); self.msg_lbl.setWordWrap(True); outer.addWidget(self.msg_lbl)
            self.tabs = QtW.QTabWidget(); self.pages = []; outer.addWidget(self.tabs, 1)
            # data
            lay = self._tab("Data")
            win = self

            class Names:  # names the user gave to files, kept in the project (spec["data"]["names"])
                @staticmethod
                def available():
                    return win.spec is not None

                @staticmethod
                def get(key):
                    return ((win.spec or {}).get("data") or {}).get("names", {}).get(key)

                @staticmethod
                def set(key, name):
                    if win.spec is None:
                        return
                    names = win.spec.setdefault("data", {}).setdefault("names", {})
                    if name:
                        names[key] = name
                    else:
                        names.pop(key, None)
                    win.editor.record(); win.schedule()
            self.data = DataPanel(self.st, self.run_job, self.settings, self.load_demo, self.start_over, names=Names()); lay.addWidget(self.data)
            self.voxel_row = VoxelRow(self); lay.addWidget(self.voxel_row); self.voxel_row.setVisible(False)
            # structures
            lay = self._tab("Structures")
            self.struct_group, sl = self._group("Structures", lay)
            self.struct_box = QtW.QWidget(); self.struct_grid = QtW.QGridLayout(self.struct_box); self.struct_grid.setContentsMargins(0, 0, 0, 0)
            self.struct_grid.setColumnStretch(2, 1); sl.addWidget(self.struct_box)
            self.struct_note = QtW.QLabel("No segmentation loaded. Add one in the Data tab."); sl.addWidget(self.struct_note)
            self.struct_group.setToolTip("Untick to hide. Zoom adds a close-up of that structure to the movie. easymode structures "
                                         "have a threshold: the surface is drawn where the network's score is above it.")
            lay.addStretch(1)
            # explore
            lay = self._tab("Explore")
            self.explore = ExplorePanel(self.st, lambda: self.spec, self.spec_for, self._set_z_range,
                                        lambda key, name: self.add_focus({"class": key}, name), self.say)
            lay.addWidget(self.explore)
            # view
            lay = self._tab("Movie"); self.movie_lay = lay
            self.view_group, vl = self._group("Main view", lay)
            pg = QtW.QGridLayout()
            for i, (name, (tilt, az)) in enumerate(VIEWS.items()):
                b = QtW.QPushButton(name); b.clicked.connect(lambda _=False, t=tilt, a=az: self.set_view(tilt=t, az=a)); pg.addWidget(b, i // 4, i % 4)
            vl.addLayout(pg)
            self.tilt_s = self._slider(vl, "Tilt", 0, 85, lambda v: self.set_view(tilt=v), lambda v: f"{v}\u00b0")
            self.az_s = self._slider(vl, "Rotate", -180, 180, lambda v: self.set_view(az=v), lambda v: f"{v}\u00b0")
            self.zoom_s = self._slider(vl, "Zoom", 50, 300, lambda v: self.set_view(zoom=v / 100), lambda v: f"{v / 100:.2f}x")
            self.view_group.setToolTip("Tip: drag in the preview to rotate, scroll to zoom.")
            # clip
            self.clip_group, cl = self._group("Clip: show only part of the tomogram", lay, "clip", False,
                                              "Like the clip box in Dragonfly: drag the two handles of a bar to keep only that range. "
                                              "Everything outside is left out of the preview and the movie.")
            self.clip_s = {}
            for ax, text in (("x", "X"), ("y", "Y"), ("z", "Z (slices)")):
                row = QtW.QHBoxLayout(); lab = QtW.QLabel(text); lab.setFixedWidth(64)
                bar = RangeSlider(); val = QtW.QLabel(""); val.setFixedWidth(84); bar._val = val
                bar.changed.connect(lambda lo, hi, ax=ax: self._clip_changed(ax, lo, hi))
                row.addWidget(lab); row.addWidget(bar, 1); row.addWidget(val); cl.addLayout(row); self.clip_s[ax] = bar
            row = QtW.QHBoxLayout(); self.clipfit_cb = QtW.QCheckBox("Fit the view to the kept part")
            self.clipfit_cb.toggled.connect(self._clipfit_changed)
            b = QtW.QPushButton("Reset"); b.clicked.connect(self.reset_clip); row.addWidget(self.clipfit_cb, 1); row.addWidget(b); cl.addLayout(row)
            # movie
            self.movie_group, ml = self._group("Movie", lay)
            row = QtW.QHBoxLayout(); row.addWidget(QtW.QLabel("<b>Parts of the movie</b>"), 1)
            self.reset_parts_btn = QtW.QPushButton("Reset to the default movie")
            self.reset_parts_btn.setToolTip("Slide up, slide back revealing the segmentation, overview. Undo brings your parts back.")
            self.reset_parts_btn.clicked.connect(lambda: self.editor.reset_default()); row.addWidget(self.reset_parts_btn); ml.addLayout(row)
            self.parts_panel = PartsPanel(self); ml.addWidget(self.parts_panel)
            self.sweep_hint = self._note("A slide-up shows only the moving slice; with the tomogram hidden it shows nothing.", ml)
            self.sweep_hint.setVisible(False)
            self.movie_group.setToolTip("Add, remove, split and reorder parts on the timeline under the preview, or with its right-click menu.")
            self.pick_btn = QtW.QPushButton("+ Click a structure in the preview"); self.pick_btn.setCheckable(True)
            self.pick_btn.toggled.connect(self.set_pick); ml.addWidget(self.pick_btn)
            self.rto_cb = QtW.QCheckBox("Return to the overview at the end"); self.rto_cb.toggled.connect(lambda v: self._spec_set("return_to_overview", v))
            ml.addWidget(self.rto_cb)
            # look
            mk = lay  # the Make the movie box goes at the end of this tab
            lay = self._tab("Look")
            self.tslice_group, tg = self._group("Tomogram slice", lay, "tomogram_slice", True,
                                                "The default for the whole movie and the preview. Each part in the Movie tab can show, "
                                                "fade or hide the tomogram on its own.")
            self.tshow_cb = QtW.QCheckBox("Show the tomogram slice"); self.tshow_cb.toggled.connect(lambda v: self._tomo_style("show", v))
            tg.addWidget(self.tshow_cb)
            self.topac_s = self._slider(tg, "Opacity", 0, 100, lambda v: self._tomo_style("opacity", v / 100), lambda v: f"{v} %")
            self.look_group, ll = self._group("Background and contrast", lay, "background", False)
            row = QtW.QHBoxLayout(); self.invert_cb = QtW.QCheckBox("Invert contrast")
            self.invert_cb.toggled.connect(lambda v: self._style_set("invert", v)); row.addWidget(self.invert_cb); row.addStretch(1)
            ll.addLayout(row)
            form = QtW.QFormLayout(); self.bg_cb = QtW.QComboBox()
            for t, c in (("black", "black"), ("white", "white"), ("light grey", "lightgrey"), ("dark grey", "darkgrey"),
                         ("gradient (light grey to white)", "gradient"), ("the tomogram slice", "tomogram")):
                self.bg_cb.addItem(t, c)
            self.bg_cb.setToolTip("Behind the structures. White suits figures; 'the tomogram slice' shows the slice behind them.")
            self.bg_cb.activated.connect(lambda j: self.spec is not None and (__import__("tomoreveal.look", fromlist=["set_look"]).set_look(
                self.spec, background=self.bg_cb.itemData(j)), self.look35.sync(), self.schedule()))
            self.contrast_s = QtW.QSlider(QtC.Qt.Horizontal); self.contrast_s.setRange(0, 20)
            self.contrast_s.valueChanged.connect(lambda v: self._style_set("contrast", [v / 2, 100 - v / 2]))
            self.title_le = QtW.QLineEdit(); self.title_le.setPlaceholderText("optional text, bottom left")
            self.title_le.textChanged.connect(lambda t: self._style_set("title", t))
            self.allop_s = QtW.QSlider(QtC.Qt.Horizontal); self.allop_s.setRange(0, 100)
            self.allop_s.setToolTip("Fades all structures together, for a 'ghost' look. Each structure's own opacity is in the Structures tab.")
            self.allop_s.valueChanged.connect(lambda v: self._style_set("structures_opacity", v / 100))
            form.addRow("Background", self.bg_cb); form.addRow("Contrast", self.contrast_s); form.addRow("All structures", self.allop_s)
            form.addRow("Title", self.title_le); ll.addLayout(form)
            from .gui_look import Look35Controls
            self.look35 = Look35Controls(self, lay)
            lay.removeWidget(self.look35.group); lay.insertWidget(0, self.look35.group)  # the look first: it sets most of the rest
            self.overlay_controls = OverlayControls(self, lay)
            from .gui_annotations import AnnotationMouse, AnnotationPanel, preview_menu
            self.ann_panel = AnnotationPanel(self, lay); self.ann_mouse = AnnotationMouse(self)
            self.arrow_pick = False; self.label_editor = None; self.preview_menu_extras = [preview_menu(self)]
            self.look_controls = LookControls(self, lay)
            self.legend_cb, self.slabel_cb = self.overlay_controls.cb["legend"], self.overlay_controls.cb["slice_label"]
            from .gui_presets import PresetsPanel
            self.presets = PresetsPanel(self, self._tab("Presets")); self.pages[-1].layout().addStretch(1)
            self.tabs.tabBar().moveTab(5, 3)  # choose a look before making the movie: Presets comes before Movie
            wg, wl = self._group("Window", lay, "window", False, "Light or dark window. This does not change the movie: its "
                                 "background is set in the Look.")
            row = QtW.QHBoxLayout(); row.addWidget(QtW.QLabel("Theme")); self.theme_cb = QtW.QComboBox()
            for t, d in THEMES:
                self.theme_cb.addItem(t, d)
            self.theme_cb.activated.connect(lambda j: self.set_theme(self.theme_cb.itemData(j))); row.addWidget(self.theme_cb); row.addStretch(1)
            wl.addLayout(row)
            lay.addStretch(1)
            # preview side
            self.right = QtW.QStackedWidget()
            right = QtW.QWidget(); rl = QtW.QVBoxLayout(right); rl.setContentsMargins(0, 0, 0, 0)
            self.right.addWidget(right); self.right.addWidget(self.explore.page)
            self.tabs.currentChanged.connect(self._tab_changed)
            self.prev = Preview()
            self.toolbar = OverlayToolbar(self); rl.addWidget(self.toolbar)
            self.prev.overlay = OverlayDrag(self)
            self.prev.clicked.connect(self.on_click); self.prev.dragged.connect(self.on_drag)
            self.prev.released.connect(self.on_release); self.prev.wheel.connect(self.on_wheel)
            self.prev.boxed.connect(self.on_boxed)
            from .gui_navigate import Navigator
            from .gui_paths import PointPanel
            self.nav = Navigator(self); self.prev.nav = self.nav
            self.path_point = None; self.path_edit_paused = None; self.point_panel = PointPanel(self)
            rl.addWidget(self.prev, 1)
            self.timeline = Timeline(); rl.addWidget(self.timeline)
            self.timeline.frame_changed.connect(self.on_scrub); self.timeline.released.connect(lambda: self.schedule(0))
            self.timeline.part_selected.connect(self.select_part); self.timeline.part_activated.connect(self.open_part)
            self.timeline.transition_changed.connect(self.set_transition)
            self.timeline.annotation_changed.connect(self.set_annotation_time)
            self.timeline.annotation_selected.connect(lambda aid: (setattr(self.ann_mouse, "selected", [aid]), self.prev.update()))
            g = QtW.QGroupBox("Make the movie"); grid = QtW.QGridLayout(g); self.make_group = g
            self.quality_cb = QtW.QComboBox()
            for t, wh in QUALITY:
                self.quality_cb.addItem(t, wh)
            self.quality_cb.setCurrentIndex(1); self.quality_cb.currentIndexChanged.connect(lambda _: self.schedule())
            self.quality_cb.setSizeAdjustPolicy(QtW.QComboBox.AdjustToMinimumContentsLengthWithIcon); self.quality_cb.setMinimumContentsLength(16)
            self.folder_le = QtW.QLineEdit(self.st.outdir); self.folder_le.setMinimumWidth(120); fb = QtW.QPushButton("Choose..."); fb.clicked.connect(self.choose_folder)
            self.name_le = QtW.QLineEdit("tomogram_reveal")
            grid.addWidget(QtW.QLabel("Quality"), 0, 0); grid.addWidget(self.quality_cb, 0, 1); grid.addWidget(QtW.QLabel("Name"), 0, 2); grid.addWidget(self.name_le, 0, 3, 1, 2)
            grid.addWidget(QtW.QLabel("Save in"), 1, 0); grid.addWidget(self.folder_le, 1, 1, 1, 3); grid.addWidget(fb, 1, 4)
            self.aa_movie = QtW.QComboBox()
            for t, d in AA_MOVIE:
                self.aa_movie.addItem(t, d)
            self.aa_movie.setToolTip("Smooth edges in the movie. Best (the default) looks cleanest; Off is fastest.")
            self.aa_movie.activated.connect(lambda j: self.spec is not None and (ensure_look(self.spec)["aa"].__setitem__("movie", self.aa_movie.itemData(j)), self.schedule()))
            self.export = ExportControls(self, grid, 2)  # rows 2-5: save as, file size and codec, notes, estimates
            grid.addWidget(QtW.QLabel("Smooth edges"), 6, 0); grid.addWidget(self.aa_movie, 6, 1)
            self.fps_cb = QtW.QComboBox()  # 3.5: smoother motion
            for t, d in (("30 frames/s", 30), ("60 frames/s", 60)):
                self.fps_cb.addItem(t, d)
            self.fps_cb.setToolTip("Smoother motion: 60 fps. Files are about 1.6 times larger and rendering takes twice as long.")
            self.fps_cb.activated.connect(lambda j: self.spec is not None and (self.spec["output"].__setitem__("fps", int(self.fps_cb.itemData(j))),
                                                                           self.schedule()))
            self.blur_cb = QtW.QCheckBox("Motion blur")
            self.blur_cb.setToolTip("Blur fast-moving detail a little, as a camera would, so fine membranes do not look stepped while the "
                                    "camera moves. Renders 4 pictures per frame (4 times slower); used for the movie only, not the preview.")
            self.blur_cb.toggled.connect(lambda on: self.spec is not None and (self.spec["output"].__setitem__("motion_blur", bool(on)),
                                                                              self.schedule()))
            grid.addWidget(QtW.QLabel("Smoother"), 7, 0); grid.addWidget(self.fps_cb, 7, 1); grid.addWidget(self.blur_cb, 7, 2, 1, 3)
            self.go_btn = QtW.QPushButton("Make movie"); self.go_btn.setStyleSheet("font-weight:600; padding:6px 18px"); self.go_btn.clicked.connect(self.make_movie)
            self.cancel_btn = QtW.QPushButton("Cancel"); self.cancel_btn.clicked.connect(self.cancel_render)
            self.open_btn = QtW.QPushButton("Play movie"); self.open_btn.clicked.connect(lambda: self._open(self.last_movie))
            self.dir_btn = QtW.QPushButton("Show folder"); self.dir_btn.clicked.connect(lambda: self._open(os.path.dirname(self.last_movie or self.folder_le.text())))
            row = QtW.QHBoxLayout()
            for w in (self.go_btn, self.cancel_btn, self.open_btn, self.dir_btn):
                row.addWidget(w)
            row.addStretch(1); grid.addLayout(row, 8, 0, 1, 5)
            self.progress = QtW.QProgressBar(); grid.addWidget(self.progress, 9, 0, 1, 5)
            self.status_lbl = QtW.QLabel(""); self.status_lbl.setStyleSheet(DIM); self.status_lbl.setWordWrap(True); grid.addWidget(self.status_lbl, 10, 0, 1, 5)
            mk.addWidget(g); mk.addStretch(1)
            split.addWidget(left); split.addWidget(self.right); split.setStretchFactor(1, 1); split.setSizes([560, 920])
            self.cancel_btn.hide(); self.progress.hide(); self.open_btn.setEnabled(False)

        # ---------- messages and state ----------
        def say(self, text, kind=None):
            self._msg = (text, kind); col = message_color(kind)
            self.msg_lbl.setStyleSheet(f"color:{col}" if col else DIM); self.msg_lbl.setText(text or "")
            if text:
                self.statusBar().showMessage(text, 10000)

        TAB_DATA, TAB_STRUCT, TAB_EXPLORE, TAB_PRESETS, TAB_MOVIE, TAB_LOOK = 0, 1, 2, 3, 4, 5

        # ---------- playback ----------
        def toggle_play(self):
            self.stop_play() if self.playing else self.start_play()

        def start_play(self):
            if self.playing or self.spec is None or self.busy or self.gen is not None or self.st.ren is None or not self.st.can_show:
                return
            spec = self.spec_for(); plan = self.st.plan_frame(spec, {"frame": self.timeline.frame})[0]
            if len(plan.frames) < 2:
                return
            f0 = self.timeline.frame if self.timeline.frame < len(plan.frames) - 1 else 0  # at the end: from the start
            w, h = self.st._psize(spec)
            self.playing = dict(t0=time.monotonic(), f0=f0, spec=spec, frames=plan.frames, fps=plan.fps,
                                size=(w * 2 // 3, h * 2 // 3), drawn=0, last=None)
            self.playing.update(full=self.playing["size"], small=(w // 2, h // 2))
            self.play_btn.setText("Stop"); self.prev_timer.stop(); self.play_timer.start()

        def play_part(self, pid):
            """Play only one part in the preview (from its start to its end)."""
            plan = self.timeline.canvas.plan
            rng = None
            if plan is not None:
                from .storyboard import main_range
                rng = main_range(plan, pid)
            if rng is None:
                return
            self.stop_play(); self.timeline.set_frame(rng[0]); self.toggle_play()
            if self.playing:
                self.playing["stop_at"] = rng[0] + rng[1] - 1

        def stop_play(self):
            if not self.playing:
                return
            last = self.playing["last"]; self.playing = None; self.play_timer.stop(); self.play_btn.setText("Play")
            if self.prev.note.text() == SKIP_NOTE:
                self.prev.set_note("")
            if last is not None:  # leave the preview on the frame it stopped at, at full quality
                self.req = {"frame": last}; self.schedule(0)

        def _play_step(self):
            p = self.playing
            if p is None:
                return
            f = p["f0"] + int((time.monotonic() - p["t0"]) * p["fps"])
            last = min(len(p["frames"]) - 1, p.get("stop_at", len(p["frames"]) - 1))
            end = f >= last
            # Frames that take longer to draw than the movie gives them: show every n-th frame, evenly spaced, rather
            # than whichever frame the clock reached (that skipped 1, then 4, then 2 ... and looked jittery).
            step = p.get("step", 1)
            f = min(p["f0"] + (f - p["f0"]) // step * step, last) if not end else last
            if f != p["last"]:
                t0 = time.monotonic(); self.st.ren.surface_async = True
                try:
                    img = self.st.ren.draw(p["frames"][f], p["spec"], p["size"], aa=look_settings(p["spec"])["aa"]["preview"])
                except Exception as exc:
                    traceback.print_exc(); self.stop_play(); self.say(f"Playback stopped: {exc}", "err"); return
                finally:
                    self.st.ren.surface_async = False
                self._make_part_surfaces()
                dt = time.monotonic() - t0
                p["avg"] = dt if p.get("avg") is None else 0.7 * p["avg"] + 0.3 * dt
                if p["drawn"] >= 2:
                    if p["avg"] * p["fps"] > 2.5 and p["size"] == p["full"]:  # slow: a smaller picture (scaled up to fit)
                        p["size"] = p["small"]; p["avg"] = None
                    else:
                        p["step"] = max(1, min(10, int(np.ceil(p["avg"] * p["fps"] * 1.1))))
                skipped = p["last"] is not None and f - p["last"] > 1
                p["last"] = f; p["drawn"] += 1
                if skipped and not p.get("noted"):  # 3.5: judge smoothness from the movie, not from the preview
                    p["noted"] = True; self.prev.set_note(SKIP_NOTE)
                self.prev.set_image(to_qimage(img)); self.frame = f
                self.timeline.set_frame(f)
            if end:
                self.stop_play()

        def _test_real_shadows(self):
            """Find out once, off the window's thread, whether VTK shadow maps work here (never with software rendering)."""
            from .shadows import real_shadows_available
            if self.st.headless:
                self.st.real_shadows_ok, self.st.real_shadows_note = real_shadows_available(True)
                self.look_controls.sync(); return
            self._shadow_worker = Worker(lambda log: real_shadows_available(False))

            def done(r):
                self.st.real_shadows_ok, self.st.real_shadows_note = r
                if self.st.ren is not None:
                    self.st.ren.real_shadows_ok = r[0]
                self.look_controls.sync()
            self._shadow_worker.done.connect(done); self._shadow_worker.start()

        def apply_help(self):
            """Tooltips for every control (texts in help_texts.py); called after lists are rebuilt."""
            from .help_texts import apply
            apply(self)
            for page in self.pages:  # drop-down lists may shrink (long entries end in "..."), so rows fit the left side
                for cb in page.findChildren(QtW.QComboBox):
                    if cb.sizeAdjustPolicy() != QtW.QComboBox.AdjustToMinimumContentsLengthWithIcon:
                        cb.setSizeAdjustPolicy(QtW.QComboBox.AdjustToMinimumContentsLengthWithIcon); cb.setMinimumContentsLength(6)

        def _help_menu(self):
            h = self.project.help_menu; h.addSeparator()
            self.tips_act = h.addAction("Show tips on hover"); self.tips_act.setCheckable(True)
            self.tips_act.setChecked(self.settings.value("tips", True, type=bool))
            app = QtW.QApplication.instance()
            self.tips_act.toggled.connect(lambda on: (self.settings.setValue("tips", bool(on)), app.setProperty("tomoreveal_tips", bool(on))))
            app.setProperty("tomoreveal_tips", self.tips_act.isChecked())
            if not hasattr(app, "_tip_filter"):  # one filter for the whole program (a filter per window slows every event)
                app._tip_filter = TipFilter(app); app.installEventFilter(app._tip_filter)
            h.addAction("What is this?", lambda: QtW.QWhatsThis.enterWhatsThisMode())

        def _scroll_to(self, w):
            """Scroll the tab page that holds w so that w is visible."""
            p = w.parentWidget()
            while p is not None and not isinstance(p, QtW.QScrollArea):
                p = p.parentWidget()
            if p is not None:
                p.ensureWidgetVisible(w, 0, 20); p.horizontalScrollBar().setValue(0)

        def _tab_changed(self, i):
            """The Explore tab shows its own slice view on the right; every other tab shows the movie preview."""
            self.stop_play()
            self.right.setCurrentIndex(1 if i == self.TAB_EXPLORE else 0)
            if i == self.TAB_EXPLORE:
                self.explore.view.setFocus()
            self.schedule(0)
            if i == self.TAB_PRESETS:
                QtC.QTimer.singleShot(300, self.presets.refresh)  # after the preview

        def _set_z_range(self, zr):
            if self.spec:
                self.spec["z_range"] = zr; self.schedule()

        def show_empty(self):
            for i in (self.TAB_STRUCT, self.TAB_EXPLORE, self.TAB_MOVIE, self.TAB_LOOK, self.TAB_PRESETS):
                self.tabs.setTabVisible(i, False)
            self.tabs.setCurrentIndex(self.TAB_DATA)
            self.timeline.setVisible(False); self.go_btn.setEnabled(False); self.toolbar.setVisible(False); self.voxel_row.setVisible(False)
            kept = hasattr(self, "project") and self.project.kept is not None
            self.prev.clear_image("Your movie is kept. Add data, or File ▸ New to start over." if kept else
                                  "Drop files to begin.\n\nA tomogram, a segmentation, or both: the preview appears here.")

        def set_busy(self, flag, msg=None):
            """While files are read: the Data tab shows a progress line and the other tabs are disabled."""
            self.busy = flag; self.go_btn.setEnabled(not flag and self.st.can_show)
            for w in [p for i, p in enumerate(self.pages) if i != self.TAB_DATA] + [self.timeline, self.play_btn, self.explore.page]:
                w.setEnabled(not flag)  # nothing may use Studio or the renderer while the worker replaces them
            self.data.set_busy(flag, msg or "")
            if flag:
                self.stop_play(); self.pick_btn.setChecked(False)
                QtW.QApplication.setOverrideCursor(QtC.Qt.BusyCursor); self.say(msg)
            else:
                QtW.QApplication.restoreOverrideCursor()

        # ---------- loading ----------
        def dragEnterEvent(self, e):
            if e.mimeData().hasUrls():
                e.acceptProposedAction()

        def dropEvent(self, e):
            paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
            projects = [p for p in paths if p.lower().endswith(".tomoreveal")]
            if projects:
                e.acceptProposedAction(); self.project.open(projects[0])
            elif paths:
                e.acceptProposedAction(); self.load(paths=paths)

        def run_job(self, fn, msg="Reading the files and building 3D surfaces. This can take a minute for large tomograms ..."):
            """Run a Studio call that returns info() in the Worker thread; the renderer is made afterwards on this thread."""
            if self.busy or self.gen is not None:
                return
            if self._thr_pending:  # now, while the structures still have these numbers
                self.thr_timer.stop(); self.apply_thresholds()
            self.editor.record_pending(); self.project.hold(); self.set_busy(True, msg)
            self.worker = Worker(fn)
            self.worker.log.connect(lambda m: (self.say(m), self.data.log(m)))
            self.worker.done.connect(self._loaded); self.worker.failed.connect(self._load_failed)
            self.worker.start()

        def load(self, paths=(), roi=None, image=None, features=()):
            st, paths, features = self.st, list(paths), list(features)
            self.run_job(lambda log: st.load(paths, roi=roi, image=image, log=log, as_features=features, make_renderer=False))

        def set_tomogram(self, key):
            self.data.choose_tomogram(key or None)

        def set_segmentation(self, key, on):
            self.data.switch(key, on)

        def remove_source(self, key):
            self.data.remove(key)

        def _object_refs(self):
            """Every place the settings name one object of a structure: dicts holding a structure key and a component."""
            out = []
            for p in self.spec.get("shots", []):
                tgt = p.get("target") or {}
                if tgt.get("class") is not None and tgt.get("component") is not None:
                    out.append((tgt, "class"))
                for it in tgt.get("items") or []:
                    if it.get("component") is not None:
                        out.append((it, "structure"))
                for it in (p.get("source") or {}).get("items") or []:
                    if it.get("component") is not None:
                        out.append((it, "structure"))
            for a in self.spec.get("annotations") or []:
                for anc in [a.get("anchor"), a.get("pin")] + list(a.get("more") or []):
                    if isinstance(anc, dict) and anc.get("structure") is not None and anc.get("component") is not None:
                        out.append((anc, "structure"))
            return out

        def flip_volume(self, axes):
            """Show the volume flipped (Data tab, 3.5). The settings' points move along; the objects in focus are found
            again after the flip by one of their voxels (their numbers change when the volume is flipped)."""
            axes = tuple(a for a in "xyz" if a in axes)
            if self.busy or self.gen is not None or not self.spec or not self.info:
                self.data.refresh(self.st.info()); return
            toggled = set(self.st.flip) ^ set(axes)
            if not toggled:
                return
            ds = self.st.ds; nz, ny, nx = ds.shape; n = {"x": nx, "y": ny, "z": nz}; fix = []
            for ref, field in self._object_refs():
                k = ds.id_of(ref.get(field))
                try:
                    sl = ds._comp_slices(k, int(ref["component"])); lbl, objs, _ = ds.components(k)
                    o = objs[int(ref["component"]) - 1]; vox = np.argwhere(lbl[o] == int(ref["component"]))
                    z, y, x = vox[len(vox) // 2] + np.array([s.start for s in sl])
                except (KeyError, IndexError, TypeError, ValueError):
                    continue
                p = {"x": float(x), "y": float(y), "z": float(z)}
                for a in toggled:
                    p[a] = n[a] - 1 - p[a]
                fix.append((ref, field, (p["x"], p["y"], p["z"])))
            flip_spec(self.spec, ds.shape, toggled)
            if axes:
                self.spec["flip"] = list(axes)
            else:
                self.spec.pop("flip", None)
            self._flip_fix = fix
            self.run_job(lambda log: self.st.set_flip(axes, log=log, make_renderer=False), "Flipping the volume and building 3D surfaces ...")

        def _refind_objects(self):
            """After a flip: each object in focus by the voxel it had, now flipped."""
            fix, self._flip_fix = getattr(self, "_flip_fix", None), None
            if not fix:
                return
            ds = self.st.ds; lost = 0
            for ref, field, xyz in fix:
                k = ds.id_of(ref.get(field)); cid = None
                try:
                    cid = ds.pick_component(k, xyz, r=2) if k is not None else None
                except (KeyError, IndexError, TypeError, ValueError):
                    cid = None
                if cid:
                    ref["component"] = int(cid)
                else:
                    lost += 1
            if lost:
                self.say(f"{lost} object{'s' if lost != 1 else ''} in focus could not be found again after the flip; "
                         "choose them again in their close-up.", "err")

        def _loaded(self, info):
            try:
                self.st.ensure_renderer()
            except Exception as exc:
                traceback.print_exc(); self.set_busy(False); self.say(f"Could not start the 3D renderer: {exc}", "err"); return
            self.set_busy(False); self.apply_info(info)

        def _load_failed(self, msg):
            self.set_busy(False); msg = self.project.load_failed() + msg
            try:
                self.st.ensure_renderer()
                self.apply_info(self.st.info(), keep_message=True)
            except Exception:
                traceback.print_exc()
            self.say(msg, "err")

        def start_over(self):
            """Unload everything: a new, empty project (File > New)."""
            if self.busy or self.gen is not None:
                return
            if not self.project.confirm_discard("starting over"):
                return
            self.unload(); self.project.autosave.remove(); self.say("")

        def unload(self):
            """Leave the current work (whoever calls this has asked): end everything that would go on using its settings
            or its renderer, then empty the window."""
            self.stop_play(); self.end_picks(); self.pick_btn.setChecked(False); self.editor.pending_after = None
            self.point_panel.close_panel()
            le, self.label_editor = self.label_editor, None
            if le is not None:
                le.hide(); le.deleteLater()
            for t in (self.thr_timer, self.param_timer, self.prev_timer, self.editor.hist_timer, self.nav.anim):
                t.stop()
            self._thr_pending = {}; self._param_pending = {}; self.nav._anim = None
            self.st.reset(); self.info = self.spec = None
            self.editor.reset_history(); self.data.refresh(self.st.info()); self.project.forget(); self.show_empty()

        def apply_info(self, info, keep_message=False):
            prev = self.info
            self.info = info; self.data.refresh(info)
            if not info.get("can_show"):
                if not keep_message:
                    self.say(info.get("message", ""))
                self._thr_pending = {}; self.thr_timer.stop()  # for structures that are not there now
                self.spec = None; self.project.nothing_shown(); self.show_empty(); return
            kept, note = self.project.take_kept(info)
            if kept is not None:
                self.spec, prev = kept.spec, kept.info
            same = bool(prev and prev.get("can_show") and self.spec)
            if not same:
                self.spec = info["spec"]; self.req = self._main_req()
                if self.project.pending is None:  # a new project (not one being opened): the Soft look (3.5, the user's choice)
                    from .look import apply_look
                    apply_look(self.spec, "soft")
                    for part in self.spec.get("shots", []):  # and still overviews (3.5: the user's choice)
                        if part.get("type") == "overview" and part.get("motion") == "rock" and part.get("view") is None:
                            part["motion"] = "none"
            else:  # keep the user's settings of every structure that is still there
                merge_settings(self.spec, info["spec"], same_grid=prev.get("dims") == info.get("dims"))
            if not same or not self.name_le.text().strip():
                self.name_le.setText(re.sub(r"\.[^.]+$", "", info.get("tomo_name") or "tomogram")[:60] + "_reveal")
            for i in (self.TAB_STRUCT, self.TAB_EXPLORE, self.TAB_MOVIE, self.TAB_LOOK, self.TAB_PRESETS):
                self.tabs.setTabVisible(i, True)
            self.explore.set_data(info)
            self.timeline.setVisible(True); self.go_btn.setEnabled(True); self.toolbar.setVisible(True)
            self.build_controls()
            if not keep_message:
                self.say(note or "Loaded. Change anything on the left; the preview follows.", "ok")
            self._refind_objects()
            if same and prev.get("dims") == info.get("dims") and prev.get("flip") == info.get("flip"):
                self.editor.history.merge(info["spec"]); self.editor.refresh_buttons()
            else:  # older steps belong to another movie, another grid, or name objects whose numbers have changed
                self.editor.reset_history()
            self.schedule(0)
            self.project.after_apply(fresh=not same)
            self.data.refresh(self.info); self.apply_help()  # names the user gave the files, from the settings

        # ---------- controls ----------
        def _struct_details(self, c, sc):
            """The settings that open under a structure: opacity, threshold (score volumes), specks, smoothing."""
            w = QtW.QWidget(); f = QtW.QFormLayout(w); f.setContentsMargins(0, 0, 0, 6); k = c["key"]

            def slider(lo, hi, v, fmt, fn):
                row = QtW.QWidget(); h = QtW.QHBoxLayout(row); h.setContentsMargins(0, 0, 0, 0)
                s = QtW.QSlider(QtC.Qt.Horizontal); s.setRange(lo, hi); s.setValue(int(round(v))); val = QtW.QLabel(fmt(s.value())); val.setFixedWidth(44)
                s.valueChanged.connect(lambda x: (val.setText(fmt(x)), fn(x))); h.addWidget(s, 1); h.addWidget(val)
                return row
            f.addRow("Opacity", slider(0, 100, 100 * float(sc.get("opacity", 1.0)), lambda x: f"{x} %",
                                       lambda x, k=k: self._class_set(k, "opacity", x / 100)))
            if c["kind"] == "score":
                f.addRow("Threshold", slider(1, 19, (c["threshold"] or 0.5) * 20, lambda x: f"{x / 20:.2f}",
                                             lambda x, cid=c["id"]: self._thr_changed(cid, x / 20)))
            sp = QtW.QSpinBox(); sp.setRange(0, 10_000_000); sp.setSingleStep(10); sp.setSuffix(" voxels"); sp.setSpecialValueText("keep all")
            sp.setValue(int(sc.get("min_object_voxels", 0) or 0))
            sp.setToolTip("Leave out separate bits smaller than this. Helps with noisy segmentations.")
            sp.valueChanged.connect(lambda v, k=k: self._param_changed(k, "min_object_voxels", int(v)))
            f.addRow("Remove specks smaller than", sp)
            sm = QtW.QSpinBox(); sm.setRange(0, 200); sm.setValue(int(sc.get("smoothing", self.st.ds.smooth) if sc.get("smoothing") is not None else self.st.ds.smooth))
            sm.setToolTip("Surface smoothing steps; 0 shows the raw voxel steps")
            sm.valueChanged.connect(lambda v, k=k: self._param_changed(k, "smoothing", int(v)))
            f.addRow("Smoothing", sm)
            from .volumes import SOFTEN
            so = QtW.QComboBox(); so.setSizeAdjustPolicy(QtW.QComboBox.AdjustToMinimumContentsLengthWithIcon)
            for t, _ in SOFTEN:
                so.addItem(t)
            so.setCurrentIndex(next((i for i, (_, d) in enumerate(SOFTEN) if d == int(sc.get("soften", 0) or 0)), 0))
            so.setToolTip("Smoother surface: takes away the bumps and ridges a segmentation leaves, for example on membranes. "
                          "'Much' can lose parts thinner than 2 voxels")
            so.activated.connect(lambda i, k=k: self._param_changed(k, "soften", SOFTEN[i][1])); self._soften_cb[k] = so
            f.addRow("Smoother surface", so)
            style_rows(self, f, k)
            from .gui_look import membrane_row
            membrane_row(self, f, k, self.spec["classes"].get(k, {}).get("name", c.get("name", "")))
            return w

        def _param_changed(self, key, name, v):
            self.spec["classes"][key][name] = v; self._param_pending[key] = True; self.param_timer.start(); self.schedule()

        def _wanted_params(self, c):
            sc = self.spec["classes"].get(c["key"], {})
            return dict(min_object_voxels=int(sc.get("min_object_voxels", 0) or 0),
                        smoothing=int(sc["smoothing"]) if sc.get("smoothing") is not None else self.st.ds.smooth,
                        soften=int(sc.get("soften", 0) or 0))

        def apply_params(self):
            """Rebuild the surfaces whose speck size or smoothing changed (after the controls settle). New surfaces are
            made in a worker thread, so the window keeps answering; they are swapped in when they are ready."""
            if self.busy or self.gen is not None or (self._surf_worker is not None and self._surf_worker.isRunning()):
                self.param_timer.start(); return
            if not self.spec or not self.info:
                return
            ds = self.st.ds; todo = []
            for c in self.info.get("structures", []):
                k = c["id"]; want = self._wanted_params(c)
                if k in ds.classes and any(ds.param(k, n) != v for n, v in want.items()) and not ds.surface_ready(k, params=want) \
                        and (k in ds.meshes or not ds.classes[k]["background"]):
                    todo.append((k, want))
            if not todo:
                return self._params_ready()
            self.say("Making the new surfaces ..."); self._param_pending = {}

            def work(log):
                for k, want in todo:
                    ds.make_surface(k, params=want)
                return True
            self._surf_worker = Worker(work)
            self._surf_worker.done.connect(lambda _r: (self.say(""), self._params_ready()))
            self._surf_worker.failed.connect(lambda msg: (self.say(msg, "err"), self._params_ready()))
            self._surf_worker.start()

        def _params_ready(self):
            """Swap in the surfaces of the current settings (made already: quick) and bring the controls in line."""
            if self.busy or self.gen is not None:
                self.param_timer.start(); return
            self._param_pending = {}
            if self._sync_params():
                self.sync_surface_controls()
            if hasattr(self, "look35"):
                self.look35.sync()
            self.schedule(0)

        def sync_surface_controls(self):
            """The "Smoother surface" choice of each structure, after it was changed elsewhere (Look tab, undo)."""
            from .volumes import SOFTEN
            for key, so in list(self._soften_cb.items()):
                try:
                    v = int((self.spec["classes"].get(key) or {}).get("soften", 0) or 0)
                    so.blockSignals(True); so.setCurrentIndex(next((i for i, (_, d) in enumerate(SOFTEN) if d == v), 0))
                    so.blockSignals(False)
                except RuntimeError:  # rebuilt meanwhile
                    self._soften_cb.pop(key, None)

        def _sync_params(self, ready_only=False):
            """Bring the surfaces in line with the settings (after a change, undo, or opening a project). ready_only:
            only those whose new surface is made already; the others are made in a worker thread (apply_params)."""
            if not self.spec or not self.info:
                return False
            changed = False; later = False
            for c in self.info.get("structures", []):
                k = c["id"]; want = self._wanted_params(c)
                if any(self.st.ds.param(k, n) != v for n, v in want.items()):
                    if ready_only and not self.st.ds.surface_ready(k, params=want):
                        later = True; continue  # made in the worker thread first (apply_params)
                    try:
                        r = self.st.set_structure_params(c["key"], **want)
                    except UserError as exc:
                        self.say(str(exc), "err"); continue
                    c.update(mesh=r["mesh"]); changed = True
            if later:
                self.param_timer.start(0)
            return changed

        def build_controls(self):
            sp = self.spec
            self._sync_params(ready_only=True)
            grid = self.struct_grid
            while grid.count():
                it = grid.takeAt(0); w = it.widget()  # store it first: after setParent(None), it.widget() is None
                if w:
                    w.setParent(None); w.deleteLater()
                elif it.layout():
                    lay = it.layout()
                    while lay.count():
                        w = lay.takeAt(0).widget()
                        if w:
                            w.setParent(None); w.deleteLater()
            self.vox_lbl = {}; r = 0
            structs = self.info.get("structures", [])
            self.struct_note.setVisible(not structs); self.pick_btn.setEnabled(bool(structs))
            several = len({c["source"] for c in structs}) > 1; src = None
            for c in structs:
                k = c["key"]; sc = sp["classes"][k]
                if several and c["source"] != src:
                    src = c["source"]; head = QtW.QLabel(f"<b>{c['source_title']}</b>"); grid.addWidget(head, r, 0, 1, 5); r += 1
                cb = QtW.QCheckBox(); cb.setChecked(bool(sc["show"])); cb.toggled.connect(lambda v, c=c: self._show_set(c, v))
                colb = QtW.QPushButton(); colb.setFixedSize(30, 22); self._paint(colb, sc["color"])
                colb.clicked.connect(lambda _=False, k=k, b=colb: self.pick_color(k, b))
                ne = QtW.QLineEdit(sc["name"]); ne.setMinimumWidth(70); ne.textChanged.connect(lambda t, k=k: self._class_set(k, "name", t))
                v = QtW.QLabel(f"{c['voxels']:,}"); v.setStyleSheet(DIM); v.setAlignment(QtC.Qt.AlignRight | QtC.Qt.AlignVCenter); self.vox_lbl[c["id"]] = v
                cb.setToolTip("Show or hide this structure in the preview and the movie")
                colb.setToolTip("Choose the colour of this structure"); ne.setToolTip("The name shown in the legend")
                zb = QtW.QPushButton("Zoom"); zb.setToolTip("Add a close-up of this structure to the movie")
                zb.clicked.connect(lambda _=False, k=k: self.add_focus({"class": k}, self.spec["classes"][k]["name"]))
                zb.setEnabled(bool(c["mesh"]))
                why = ("background" if c["background"] else "hidden in Dragonfly" if c["hidden"] else
                       "no name in the session" if not c["named"] else "")
                if why:  # listed in grey, switched off at first; tick it to show it anyway
                    ne.setStyleSheet(DIM); ne.setToolTip(f"{why}; tick the box to show it anyway")
                    cb.setToolTip(f"{why}; tick to show it anyway")
                more = QtW.QToolButton(); more.setText("\u2699"); more.setCheckable(True); more.setChecked(k in self._struct_open)
                more.setToolTip("Opacity, threshold, small specks and smoothing of this structure")
                for col, w in enumerate((cb, colb, ne, v, zb, more)):
                    grid.addWidget(w, r, col)
                r += 1
                det = self._struct_details(c, sc); det.setVisible(k in self._struct_open); grid.addWidget(det, r, 2, 1, 4); r += 1
                more.toggled.connect(lambda on, k=k, det=det: (det.setVisible(on), self._struct_open.add(k) if on else self._struct_open.discard(k)))
                if why and why != "no name in the session":
                    lab = QtW.QLabel(f"{why}, so not drawn at first" if not sc["show"] else why); lab.setStyleSheet(DIM)
                    grid.addWidget(lab, r, 2, 1, 3); r += 1
            v = sp["view"]
            self._setv(self.tilt_s, v["tilt"]); self._setv(self.az_s, v["az"]); self._setv(self.zoom_s, v["zoom"] * 100)
            self._sync_clip()
            has_t, has_s = self.info.get("has_tomogram"), self.info.get("has_segmentation")
            pass  # (3.5: the help text is the group's tooltip)
            self.parts_panel.sync()
            tst = sp["style"].get("tomogram") or {}
            self._setv(self.tshow_cb, tst.get("show", True)); self._setv(self.topac_s, 100 * float(tst.get("opacity", 1.0)))
            self.tslice_group.setEnabled(bool(has_t)); self.tslice_group.setToolTip("" if has_t else "needs a tomogram")
            self._sweep_hint()
            self._setv(self.rto_cb, sp.get("return_to_overview", True))
            st = sp["style"]
            self._setv(self.invert_cb, st.get("invert", False)); self._setv(self.allop_s, 100 * float(st.get("structures_opacity", 1.0)))
            self.overlay_controls.sync(); self.toolbar.sync(); self.voxel_row.sync(); self.look_controls.sync(); self.export.sync()
            self.ann_panel.rebuild()
            self.aa_movie.setCurrentIndex(max(0, self.aa_movie.findData(look_settings(sp)["aa"]["movie"])))
            self.fps_cb.setCurrentIndex(max(0, self.fps_cb.findData(int(sp.get("output", {}).get("fps", 30)))))
            self.blur_cb.blockSignals(True); self.blur_cb.setChecked(bool(sp.get("output", {}).get("motion_blur", False))); self.blur_cb.blockSignals(False)
            self.apply_help()
            from .look import BG_COLOURS, style35
            kind = style35(self.spec)["background"]
            if kind not in BG_COLOURS:
                kind = next((k for k, c in BG_COLOURS.items() if c == st.get("bg", "#000000").lower() and k not in ("gradient", "tomogram")), "black")
            self.bg_cb.setCurrentIndex(max(0, self.bg_cb.findData(kind))); self.look35.sync()
            self._setv(self.contrast_s, st.get("contrast", [1, 99])[0] * 2)
            self.title_le.blockSignals(True); self.title_le.setText(st.get("title", "")); self.title_le.blockSignals(False)

        def _paint(self, b, hexc):
            b.setStyleSheet(f"background:{hexc}; border:1px solid palette(mid); border-radius:3px")

        def pick_color(self, k, b):
            c = QtW.QColorDialog.getColor(QtG.QColor(self.spec["classes"][k]["color"]), self, "Colour")
            if c.isValid():
                self.spec["classes"][k]["color"] = c.name(); self._paint(b, c.name()); self.schedule()

        def _show_set(self, c, v):
            self._class_set(c["key"], "show", v)
            if v and not c["mesh"]:  # e.g. a background structure: its surface is built only when asked for
                self.run_job(lambda log: self.st.build_structure(c["key"], log=log, make_renderer=False),
                             f"Building the surface of {self.spec['classes'][c['key']]['name']} ...")

        def _class_set(self, k, key, v):
            self.spec["classes"][k][key] = v; self.schedule()

        def _tomo_row(self, sh, index=None):
            """'Tomogram: as default / shown / faded / hidden' for one shot, with the faded opacity and the fade time."""
            t = shot_tomogram(sh)

            def put(**kw):
                sh["tomogram"] = dict(shot_tomogram(sh), **kw); sh.pop("slice", None); self.schedule()
            w = QtW.QWidget(); row = QtW.QHBoxLayout(w); row.setContentsMargins(0, 0, 0, 0)
            row.addWidget(QtW.QLabel("Tomogram"))
            cb = QtW.QComboBox()
            for text, mode in (("as default", "default"), ("shown", "shown"), ("faded", "faded"), ("hidden", "hidden")):
                cb.addItem(text, mode)
            cb.setCurrentIndex(max(0, cb.findData(t["mode"])))
            op = QtW.QSpinBox(); op.setRange(0, 100); op.setSuffix(" %"); op.setValue(int(round(100 * t["opacity"])))
            op.setToolTip("Opacity of the faded tomogram"); op.setVisible(t["mode"] == "faded")
            fade = QtW.QDoubleSpinBox(); fade.setRange(0, 10); fade.setSingleStep(0.25); fade.setPrefix("fade "); fade.setSuffix(" s")
            fade.setValue(float(t["fade"])); fade.setToolTip("How long the change takes at the start of this shot")

            def mode_changed(j):
                put(mode=cb.itemData(j)); op.setVisible(cb.itemData(j) == "faded"); self._sweep_hint()
            cb.activated.connect(mode_changed)
            op.valueChanged.connect(lambda v: put(opacity=v / 100))
            fade.valueChanged.connect(lambda v: put(fade=float(v)))
            whole = QtW.QCheckBox("whole part"); whole.setChecked(bool(t.get("whole")))
            whole.setToolTip("Change the tomogram slowly over the whole part instead of in the first seconds. Good for a slide that "
                             "reveals the segmentation while the tomogram fades away.")
            fade.setEnabled(not whole.isChecked())

            def whole_changed(on):
                put(whole=bool(on)); fade.setEnabled(not on)
            whole.toggled.connect(whole_changed)
            row.addWidget(cb); row.addWidget(op); row.addWidget(fade); row.addWidget(whole); row.addStretch(1)
            has_t = bool(self.info and self.info.get("has_tomogram"))
            w.setEnabled(has_t); w.setToolTip("" if has_t else "needs a tomogram")
            return w

        def _sweep_hint(self):
            if self.spec:
                st = self.spec["style"].get("tomogram", {})

                def hidden(p):
                    t = shot_tomogram(p)
                    return t["mode"] == "hidden" or (t["mode"] == "faded" and t["opacity"] == 0) or \
                        (t["mode"] == "default" and (not st.get("show", True) or float(st.get("opacity", 1)) == 0))
                bare = [p for p in self.spec["shots"] if p.get("on", True) and p["type"] == "slide" and p.get("structures") == "none" and hidden(p)]
                self.sweep_hint.setVisible(bool(bare and self.info.get("has_tomogram")))

        def _tomo_style(self, key, v):
            if self.spec:
                self.spec["style"].setdefault("tomogram", dict(show=True, opacity=1.0))[key] = v
                self._sweep_hint(); self.schedule()

        def _style_set(self, key, v):
            if self.spec:
                self.spec["style"][key] = v; self.schedule()

        def _spec_set(self, key, v):
            if self.spec:
                self.spec[key] = v; self.schedule()

        def add_focus(self, target, name, after=None):
            self.editor.insert(focus_shot(target, name), after)

        # ---------- clip box ----------
        def _sync_clip(self):
            dims = dict(zip("xyz", self.info["dims"])); c = self.spec.get("clip") or {}
            for a in "xyz":
                lo, hi = c.get(a) or (0, dims[a] - 1)
                self.clip_s[a].set_range(0, dims[a] - 1, lo, hi); self.clip_s[a]._val.setText(f"{self.clip_s[a].lo} - {self.clip_s[a].hi}")
            self._setv(self.clipfit_cb, c.get("fit", True))

        def _clip_changed(self, ax, lo, hi):
            if not self.spec:
                return
            self.clip_s[ax]._val.setText(f"{lo} - {hi}")
            dims = dict(zip("xyz", self.info["dims"]))
            c = dict(self.spec.get("clip") or {a: [0, dims[a] - 1] for a in "xyz"}); c[ax] = [int(lo), int(hi)]
            c["fit"] = self.clipfit_cb.isChecked()
            self.spec["clip"] = None if all(c[a] == [0, dims[a] - 1] for a in "xyz") else c
            if self._in_closeup() and self.req is None:
                self.req = self._main_req()
            self.schedule(40)

        def _clipfit_changed(self, v):
            if self.spec and self.spec.get("clip"):
                self.spec["clip"]["fit"] = bool(v); self.schedule()

        def reset_clip(self):
            if self.spec:
                self.spec["clip"] = None; self._sync_clip(); self.schedule(0)

        def load_demo(self):
            if self.busy or self.gen is not None:
                return
            try:
                p = make_demo(os.path.join(tempfile.gettempdir(), "tomoreveal_demo"))
            except Exception as exc:
                self.say(f"Could not write the demo data: {exc}", "err"); return
            self.load(paths=[p["tomogram"], p["labels"]])

        def _thr_changed(self, cid, v):
            self._thr_pending[cid] = v; self.thr_timer.start()

        def apply_thresholds(self):
            if self.busy or self.gen is not None:
                self.thr_timer.start(); return
            pend, self._thr_pending = self._thr_pending, {}
            if self.spec is None:
                return
            reset = 0  # close-ups of single objects set back to the whole structure
            QtW.QApplication.setOverrideCursor(QtC.Qt.WaitCursor)
            try:
                for cid, v in pend.items():
                    r = self.st.set_threshold(cid, v)
                    key = None
                    for c in self.info["structures"]:
                        if c["id"] == cid:
                            c.update(threshold=r["threshold"], voxels=r["voxels"], mesh=r["mesh"]); key = c["key"]
                    if cid in self.vox_lbl:
                        self.vox_lbl[cid].setText(f"{r['voxels']:,}")
                    # object numbers change with the threshold: those close-ups show the whole structure again
                    from .storyboard import make_target, target_items
                    for s in self.spec["shots"]:
                        if s["type"] == "focus" and (s.get("target") or {}).get("class") == key and s["target"].get("component") is not None:
                            s["target"] = {"class": key}; reset += 1
                        elif s["type"] == "focus" and "items" in (s.get("target") or {}):
                            its = target_items(s["target"])
                            if any(k2 == key and c is not None for k2, c in its):
                                new = list(dict.fromkeys((k2, None if k2 == key else c) for k2, c in its))
                                s["target"] = make_target(new); reset += 1
                self.say("Threshold updated." + (f" {reset} close-up{'s' if reset > 1 else ''} of one object now show{'' if reset > 1 else 's'} "
                                                 "the whole structure, because the objects changed." if reset else ""), "ok")
            except UserError as exc:
                self.say(str(exc), "err")
            finally:
                QtW.QApplication.restoreOverrideCursor()
            self.build_controls(); self.editor.record(); self.schedule(0)

        # ---------- view and preview ----------
        def _main_req(self):
            """The frame that shows the main view best: the start of the first overview, else the end of a reveal."""
            on = [p for p in self.spec["shots"] if p.get("on", True)]
            ov = next((p for p in on if p["type"] == "overview" and not p.get("view")), None)
            if ov:
                return {"at": {"part": ov["id"], "u": 0}}
            rv = next((p for p in on if p["type"] == "slide" and p.get("structures") in ("above", "all")), None)
            if rv:
                return {"at": {"part": rv["id"], "u": 0.999}}
            return {"t": 0.5}

        def _in_closeup(self):
            return self.spec is not None and self.cur_type == "focus"

        def set_view(self, tilt=None, az=None, zoom=None, quick=False):
            if not self.spec:
                return
            v = self.spec["view"]
            if tilt is not None:
                v["tilt"] = float(tilt)
            if az is not None:
                v["az"] = float(az)
            if zoom is not None:
                v["zoom"] = float(zoom)
            self._setv(self.tilt_s, v["tilt"]); self._setv(self.az_s, v["az"]); self._setv(self.zoom_s, v["zoom"] * 100)
            if self._in_closeup() and self.req is None:
                self.req = self._main_req()  # the main view is not visible during a close-up: jump to the overview
            if quick:
                self.do_preview(quick=True)
            else:
                self.schedule()

        def schedule(self, delay=120):
            self.prev_timer.start(delay)
            if hasattr(self, "export") and not (self.export.est_gen is not None and delay == 0 and self.export.tick.isActive()):
                self.export.changed()
            if hasattr(self, "project"):
                self.project.touch()
            if self.spec is not None and hasattr(self, "editor"):
                self.editor.changed()  # one undo step once things settle

        def spec_for(self):
            sp = json.loads(json.dumps(self.spec)); sp["output"]["size"] = list(self.quality_cb.currentData())
            return sp

        def do_preview(self, quick=False):
            if self.spec is None or self.busy or self.gen is not None or not self.st.can_show or self.st.ren is None or self.playing:
                return
            if self.tabs.currentIndex() == self.TAB_EXPLORE:
                self.explore._range_text(); self.explore.refresh()
                return
            spec = self.spec_for(); req = self.req or {"t": self.frame / max(1, self.total - 1)}; self.req = None
            plan, i = self.st.plan_frame(spec, req); frames = plan.frames
            w, h = self.st._psize(spec); size = (w * 2 // 3, h * 2 // 3) if quick else (w, h)
            self.st.ren.preview_part = self.selected_part; self.st.ds.path_async = True
            try:
                self.st.ren.surface_async = True
                img = self.st.ren.draw(frames[i], spec, size, aa=look_settings(spec)["aa"]["preview"], preview=True)
            except Exception as exc:
                traceback.print_exc(); soft = software_rendering_available()  # OSMesa hard-crashes where it is missing
                if not self.st.headless and soft:
                    self.st.headless = True; self.st._stale = True; self.st.ensure_renderer(); self.req = req
                    self.say("Graphics problem: switched to software rendering (slower).", "err")
                    return self.do_preview(quick)
                self.say(f"Preview failed: {exc}" + ("" if soft else " (software rendering is not available on this computer)"), "err"); return
            finally:
                if self.st.ren is not None:
                    self.st.ren.surface_async = False
            self._make_part_surfaces()
            self.frame, self.total, self.cur_shot, self.cur_type = i, len(frames), frames[i]["shot"], frames[i].get("type")
            self.prev.set_image(to_qimage(img))
            if self.selected_part not in {p["id"] for p in self.spec["shots"]}:
                self.selected_part = None
            self.timeline.set_plan(plan, self.spec, self.st.ds); self.timeline.set_frame(i); self.timeline.set_selected(self.selected_part)
            self.parts_panel.refresh_lines()  # the notes come from this plan
            vp = self.view_part(); ep = self.editing_path()
            note = (f"Changing the view of part {self.spec['shots'].index(vp) + 1}" if vp else
                    f"Editing the camera path of part {self.spec['shots'].index(ep) + 1} – click to add points, drag to move, Esc to finish"
                    if ep else "")
            cur_note = self.prev.note.text()
            if note or cur_note.startswith(("Changing the view of part", "Editing the camera path")):
                self.prev.set_note(note)  # other notes (the first-time hint, skipped frames) stay
            if self.path_point and self.point_panel.isVisible():
                self.point_panel.refresh(); self.point_panel.place()
            empty = not any(p.get("on", True) for p in self.spec["shots"])
            self.go_btn.setEnabled(not empty)
            self.go_btn.setToolTip("The movie has no parts. Add one with + Add part." if empty else
                                   "Render the movie and save it. You can keep looking at the window while it runs.")
            if empty:
                self.say("The movie has no parts. Add one with + Add part under the timeline.")
            self.editor.refresh_buttons()
            if any(v == "working out the path ..." for v in plan.problems.values()):
                self._compute_paths(spec)

        def _make_part_surfaces(self):
            """Smoother membranes of a part (its own level) are made in a worker thread the first time; the preview
            shows the usual surfaces meanwhile and is drawn again when they are ready."""
            ren = self.st.ren
            if ren is None or not ren.surfaces_pending or (self._surf_worker is not None and self._surf_worker.isRunning()):
                return
            todo = sorted(ren.surfaces_pending); ren.surfaces_pending = set(); ds = self.st.ds

            def work(log):
                for k, lvl in todo:
                    if k in ds.classes:
                        ds.make_surface(k, lvl)
                return True
            self.prev.set_note("Making smoother membranes for this part ...")
            self._surf_worker = Worker(work)
            self._surf_worker.done.connect(lambda _r: (self._clear_surface_note(), self.schedule(0)))
            self._surf_worker.failed.connect(lambda msg: (self._clear_surface_note(), self.say(msg, "err")))
            self._surf_worker.start()

        def _clear_surface_note(self):
            if self.prev.note.text().startswith("Making smoother membranes"):
                self.prev.set_note("")

        def _compute_paths(self, spec):
            """Centre lines of camera paths are worked out in a worker thread (a large membrane can take seconds)."""
            if self._path_worker is not None and self._path_worker.isRunning():
                return
            from .paths import centre_line, path_key
            from .storyboard import clip_box
            ds = self.st.ds; cb = clip_box(spec, ds.shape); todo = []
            for p in spec["shots"]:
                if p.get("type") == "path" and (p.get("source") or {}).get("kind", "follow") == "follow":
                    items = [(it.get("structure"), it.get("component")) for it in p["source"].get("items", [])]
                    if items and path_key(items, cb) not in ds.path_cache:
                        todo.append(items)

            def work(log):
                for items in todo:
                    log("working out the camera path ...")
                    try:
                        ds.path_cache[path_key(items, cb)] = centre_line(ds, items, cb)
                    except UserError as exc:
                        ds.path_cache[path_key(items, cb)] = exc
                return True
            self._path_worker = Worker(work); self._path_worker.log.connect(self.say)
            self._path_worker.done.connect(lambda _r: (self.say(""), self.parts_panel.sync(force=True), self.schedule(0)))
            self._path_worker.start()

        def select_part(self, pid):
            """A block clicked on the timeline: its line opens in the Movie tab."""
            self.end_picks(keep=pid)  # clicks in the preview must never go on adding to a part that is not selected
            if self.path_point and self.path_point[0] != pid:
                self.point_panel.close_panel()
            if self.path_edit_paused != pid:
                self.path_edit_paused = None
            self.selected_part = pid; self.parts_panel.open(pid, select=False); self.editor.refresh_buttons()
            self.ann_panel.rebuild()

        def editing_path(self):
            """The selected camera path through points, while it is being edited in the preview (3.5)."""
            p = self.parts_panel.part(self.selected_part) if self.spec and self.selected_part else None
            if p is None or p.get("type") != "path" or (p.get("source") or {}).get("kind") != "points" or self.path_edit_paused == p["id"]:
                return None
            return p

        def select_path_point(self, pid, i):
            self.path_point = (pid, i)
            if self.parts_panel.open_id != pid:
                self.parts_panel.open(pid)
            self.point_panel.show_for(pid, i); self.prev.update()

        def point_frame(self, pid, i):
            """The frame where the camera of a path is at point i."""
            plan = self.timeline.canvas.plan; ren = self.st.ren
            marks = (ren._path3d or {}).get("marks") if ren is not None else None
            if plan is None or not marks or i >= len(marks):
                return None
            target = np.asarray(marks[i][1], float)
            idx = [j for j, f in enumerate(plan.frames) if f.get("part") == pid and f["phase"] == "main" and f["pose"].get("pos") is not None]
            return min(idx, key=lambda j: float(np.linalg.norm(np.asarray(plan.frames[j]["pose"]["pos"]) - target))) if idx else None

        def look_from_point(self, pid, i):
            j = self.point_frame(pid, i)
            if j is not None:
                self.req = {"frame": j}; self.schedule(0)

        def preview_point(self, pid, i):
            """While a point's zoom or field of view slider moves: show the view from that point."""
            self.editor.changed(); j = self.point_frame(pid, i)
            self.req = {"frame": j} if j is not None else {"frame": self.frame}; self.do_preview(quick=True)

        def end_picks(self, keep=None):
            """End every way of clicking in the preview for a part (objects of a close-up, points of a path, the object a
            path follows), except for the part `keep`. Only one of them is ever active (3.5 fix: clicks for a new
            close-up were added to an earlier close-up whose object picking was still on)."""
            if self.multi_pick is not None and self.multi_pick != keep:
                self.end_multi_pick()
            if self.waypoint_pick is not None and self.waypoint_pick != keep:
                self.end_waypoint_pick()
            if self.path_pick is not None and self.path_pick != keep:
                self.path_pick = None; self.prev.setCursor(QtC.Qt.ArrowCursor)
            if self.region_pick is not None and (self.region_pick[0] is None or self.region_pick[0] != keep):
                self.end_region_pick()
            if getattr(self, "arrow_pick", False):
                self.arrow_pick = False; self.prev.setCursor(QtC.Qt.ArrowCursor)

        def set_annotation_time(self, aid, pid, start, end):
            """An arrow's or text box's bar moved on the timeline: one undo step."""
            from .annotations import find
            a = find(self.spec, aid)
            if a is None:
                return
            if (a.get("show") or {}).get("parts", "all") == "all" and len([p for p in self.spec["shots"] if p.get("on", True)]) > 1:
                a["show"]["parts"] = [pid]  # its timing is now for this part
            a["show"]["start"] = round(float(start), 2); a["show"]["end"] = round(float(end), 2)
            self.editor.commit(); self.ann_panel.rebuild()

        def set_transition(self, pid, key, value):
            """A transition's end dragged on the timeline: one undo step."""
            from .transitions import transition_of
            part = self.parts_panel.part(pid)
            if part is None:
                return
            cur = transition_of(part); cur[key] = float(value); part["transition_in"] = cur
            self.editor.commit(select=pid)
            what = "starts before the previous part ends" if key == "overlap_prev" else "runs into this part"
            self.say(f"The transition now {what} by {value:.1f} s.", "ok")

        def open_part(self, pid):
            """Double-click on a block: show the part's settings in the Movie tab."""
            self.tabs.setCurrentIndex(self.TAB_MOVIE); self.select_part(pid)

        def start_object_pick(self, pid, key):
            """'Pick an object in the preview...' of a close-up: the next click on that structure sets the object."""
            self.pick_for = (pid, key); self.pick_btn.setChecked(True)
            self.editor.pending_after = None
            name = self.spec["classes"].get(key, {}).get("name", "structure")
            self.say(f"Click one {name} object in the preview.")

        def on_scrub(self, v):
            self.req = {"frame": int(v)}; self.schedule(40)

        def set_pick(self, on):
            if on:
                self.end_picks()
            self.pick_mode = bool(on); self.prev.setCursor(QtC.Qt.CrossCursor if on else QtC.Qt.ArrowCursor)
            if not on:
                self.pick_for = None
            if on and self.spec and self.editor.pending_after is None:
                self.editor.pending_after = self.editor.anchor()  # the close-up goes where the playhead was
            if on and self.spec:
                self.req = self._main_req(); self.schedule(0)
                self.say("Click the structure you want a close-up of.")

        def start_multi_pick(self, pid):
            """'+ Click objects in the preview' of a close-up: each click adds or removes one object."""
            if self.pick_mode:
                self.pick_btn.setChecked(False)
            self.end_picks(keep=pid)
            self.multi_pick = pid; self.prev.setCursor(QtC.Qt.CrossCursor)
            self.req = self._main_req(); self.schedule(0)
            self.say("Click objects to add them to the close-up; click one again to take it out; Shift-click adds the whole "
                     "structure. Press Esc when done.")

        def end_multi_pick(self):
            if self.multi_pick is None:
                return
            pid = self.multi_pick; self.multi_pick = None; self.prev.setCursor(QtC.Qt.ArrowCursor)
            if self.parts_panel.open_id == pid:
                self.parts_panel.fill_details(pid)
            self.req = {"at": {"part": pid, "u": 0.5}}; self.schedule(0); self.say("")

        def start_region_pick(self, pid=None, after=None):
            """Draw a frame in the preview: the close-up `pid` (or a new close-up after `after`) frames what is inside."""
            self.pick_btn.setChecked(False); self.end_picks()
            self.region_pick = (pid, after if pid is None else None)
            self.prev.box_mode = True; self.prev.setCursor(QtC.Qt.CrossCursor)
            self.say("Draw a frame around what the close-up should show: press the mouse button and drag. Esc cancels.")

        def end_region_pick(self):
            self.region_pick = None; self.prev.box_mode = False; self.prev._box = None; self.prev.setCursor(QtC.Qt.ArrowCursor)

        def on_boxed(self, fx0, fy0, fx1, fy1):
            if self.region_pick is None or self.st.ren is None or self.gen is not None:
                self.end_region_pick(); return
            pid, after = self.region_pick; self.end_region_pick()
            plan, i = self.st.plan_frame(self.spec_for(), {"frame": self.frame})
            self.st.ren.draw(plan.frames[i], self.spec_for(), self.st.ren.size, annotate=False)  # the frame that was drawn
            box = self.st.ren.region_box(fx0, fy0, fx1, fy1)
            if box is None:
                self.say("There is nothing inside that frame. Draw it around structures or the slice.", "err"); return
            region = dict(lo=[round(float(v), 2) for v in box[0]], hi=[round(float(v), 2) for v in box[1]])
            if pid is None:
                from .storyboard import make_target, new_part
                part = new_part("focus", target=make_target([]), others=dict(colour="keep", opacity=1.0), region=region,
                                motion="wiggle", caption="")
                self.editor.insert(part, after)
                self.say("Added a close-up of the frame you drew.", "ok")
            else:
                part = self.parts_panel.part(pid)
                if part is None:
                    return
                part["region"] = region; self.editor.commit(select=pid, show=pid)
                self.say("The close-up now shows the frame you drew.", "ok")

        def start_waypoint_pick(self, pid):
            self.pick_btn.setChecked(False); self.end_picks(keep=pid); self.waypoint_pick = pid; self.prev.setCursor(QtC.Qt.CrossCursor)
            self.say("Click a structure or the slice to add a point. Press Esc when done.")

        def end_waypoint_pick(self):
            pid, self.waypoint_pick = self.waypoint_pick, None; self.prev.setCursor(QtC.Qt.ArrowCursor)
            if pid and self.parts_panel.open_id == pid:
                self.parts_panel.fill_details(pid)

        def start_path_pick(self, pid):
            self.pick_btn.setChecked(False); self.end_picks(keep=pid); self.path_pick = pid; self.prev.setCursor(QtC.Qt.CrossCursor)
            self.req = self._main_req(); self.schedule(0)
            self.say("Click the object the camera should follow.")

        def keyPressEvent(self, e):
            if e.key() == QtC.Qt.Key_Escape and self.editing_path() is not None and not (self.multi_pick or self.pick_mode or self.waypoint_pick
                                                                                         or self.path_pick or self.region_pick):
                self.path_edit_paused = self.selected_part; self.point_panel.close_panel(); self.prev.set_note(""); self.say("Done editing the path.")
                return
            if e.key() == QtC.Qt.Key_Escape and (self.multi_pick or self.pick_mode or self.waypoint_pick or self.path_pick or self.region_pick or self.arrow_pick):
                self.end_multi_pick(); self.end_waypoint_pick(); self.path_pick = None; self.end_region_pick(); self.arrow_pick = False; self.pick_btn.setChecked(False)
                return
            super().keyPressEvent(e)

        def _multi_click(self, fx, fy):
            from .storyboard import target_items
            try:
                p = self.st.pick(self.spec_for(), {"frame": self.frame, "x": fx, "y": fy})
            except Exception as exc:
                self.say(f"Could not pick: {exc}", "err"); return
            if not p.get("found"):
                self.say("Nothing there. Click directly on a coloured structure.", "err"); return
            part = self.parts_panel.part(self.multi_pick)
            if part is None:
                self.end_multi_pick(); return
            whole = bool(QtW.QApplication.keyboardModifiers() & QtC.Qt.ShiftModifier) or p.get("component") is None
            it = (p["class"], None if whole else int(p["component"]))
            items = target_items(part.get("target"))
            items = [x for x in items if x != it] if it in items else items + [it]
            self.parts_panel.set_items(self.multi_pick, items)
            self.req = self._main_req(); self.schedule(0)
            self.say(f"{len(items)} in focus. Click more, or press Esc when done.", "ok")

        def on_click(self, fx, fy):
            if self.busy:  # the worker is replacing the data and the renderer
                return
            if self.waypoint_pick and self.gen is None:
                pt = self.st.ren.pick_point(fx, fy) if self.st.ren is not None else None
                part = self.parts_panel.part(self.waypoint_pick)
                if pt is None or part is None:
                    self.say("Nothing there. Click on a structure or on the slice.", "err"); return
                ws = list(part.get("waypoints") or []); ws.append(dict(point=[float(v) for v in pt], pause=0.0))
                part["waypoints"] = ws; self.editor.commit(select=self.waypoint_pick)
                self.say(f"Point {len(ws)} added. Click more, or press Esc when done.", "ok"); return
            if self.path_pick and self.st.has_segmentation and self.gen is None:
                p = self.st.pick(self.spec_for(), {"frame": self.frame, "x": fx, "y": fy}); part = self.parts_panel.part(self.path_pick)
                if not p.get("found") or part is None:
                    self.say("Nothing there. Click directly on a coloured structure.", "err"); return
                part["source"] = dict(kind="follow", items=[{"structure": p["class"], "component": p.get("component")}])
                pid, self.path_pick = self.path_pick, None; self.prev.setCursor(QtC.Qt.ArrowCursor)
                self.editor.commit(select=pid, show=pid); self.say("The camera now follows that object.", "ok"); return
            if self.multi_pick and self.st.has_segmentation and self.gen is None:
                return self._multi_click(fx, fy)
            if isinstance(self.arrow_pick, str) and self.gen is None:  # "Point at more things": until a click on empty space
                from .gui_annotations import add_pointer_at
                if not add_pointer_at(self, self.arrow_pick, (fx, fy)):
                    self.arrow_pick = False; self.prev.setCursor(QtC.Qt.ArrowCursor); self.say("Done.", "ok")
                return
            if self.arrow_pick and self.gen is None:  # "+ Add arrow": this click is where it points
                from .gui_annotations import add_arrow_at
                self.arrow_pick = False; self.prev.setCursor(QtC.Qt.ArrowCursor); add_arrow_at(self, (fx, fy)); return
            ep = self.editing_path() if not (self.pick_mode or self.region_pick) and self.gen is None else None
            if ep is not None and self.st.ren is not None:  # path editor: a click adds a point after the selected one
                pt = self.st.ren.pick_point(fx, fy)
                if pt is None:
                    self.say("Click on a structure or on the slice to add a point.", "err"); return
                ws = list(ep.get("waypoints") or []); at = (self.path_point[1] + 1) if self.path_point and self.path_point[0] == ep["id"] else len(ws)
                ws.insert(at, dict(point=[float(v) for v in pt], pause=0.0)); ep["waypoints"] = ws
                self.editor.commit(select=ep["id"]); self.path_point = (ep["id"], at)
                self.say(f"Point {at + 1} added.", "ok"); return
            if not self.pick_mode or not self.st.has_segmentation or self.gen is not None:
                return
            try:
                p = self.st.pick(self.spec_for(), {"frame": self.frame, "x": fx, "y": fy})
            except Exception as exc:
                self.say(f"Could not pick: {exc}", "err"); return
            if not p.get("found"):
                self.say("Nothing there. Click directly on a coloured structure.", "err"); return
            if self.pick_for:  # choosing the object of an existing close-up
                pid, key = self.pick_for
                if p["class"] != key or p.get("component") is None:
                    self.say(f"That is not one {self.spec['classes'].get(key, {}).get('name', 'such')} object. Click one of them.", "err"); return
                self.pick_for = None; self.pick_btn.setChecked(False)
                part = self.parts_panel.part(pid)
                if part is not None:
                    part["target"] = {"class": key, "component": p["component"]}
                    self.editor.commit(select=pid); self.parts_panel.fill_details(pid)
                    self.say(f"The close-up now shows one object ({p['voxels']:,} voxels).", "ok")
                return
            self.pick_btn.setChecked(False)
            extra = f" (one object, {p['voxels']:,} voxels)" if p.get("component") is not None else ""
            after, self.editor.pending_after = self.editor.pending_after, None
            self.add_focus({"class": p["class"], "component": p["component"]} if p.get("component") is not None else {"class": p["class"]},
                           p["name"], after)
            self.say(f"Added a close-up of {p['name']}{extra}.", "ok")

        def view_part(self):
            """The selected "own view" part when the playhead is inside it: dragging and scrolling change its view."""
            pid = self.selected_part; plan = self.timeline.canvas.plan
            if not (pid and self.spec and plan) or not (0 <= self.frame < len(plan.frames)) or plan.frames[self.frame].get("part") != pid:
                return None
            part = self.parts_panel.part(pid)
            return part if part and part["type"] in ("overview", "slide", "hold") and part.get("view") and not part.get("locked") else None

        def locked_part(self, say=True):
            """The part at the playhead when its view is locked (3.5): moving around in the preview leaves it alone."""
            plan = self.timeline.canvas.plan if hasattr(self, "timeline") else None
            if not (self.spec and plan) or not (0 <= self.frame < len(plan.frames)):
                return None
            pid = plan.frames[self.frame].get("part"); part = self.parts_panel.part(pid) if pid else None
            if part is None or not part.get("locked"):
                return None
            if say:
                n = self.spec["shots"].index(part) + 1
                self.prev.set_note(f"Part {n} has a locked view. Untick 'Lock the view of this part' (Movie tab) to change it.")
            return part

        def set_part_view(self, part, quick=False, **kw):
            vw = dict(part["view"])
            vw.update({k: float(v) for k, v in kw.items() if v is not None}); part["view"] = vw
            self.parts_panel.show_view(part["id"]); self.parts_panel.refresh_line(part["id"])
            if quick:
                self.req = {"frame": self.frame}; self.do_preview(quick=True)
            else:
                self.schedule()

        def on_drag(self, dx, dy):
            if self.pick_mode or not self.spec or self.gen is not None or self.busy or self.locked_part():
                return
            part = self.view_part()
            if self._drag0 is None:
                self._drag0 = dict(part["view"] if part else self.spec["view"])
            v0 = self._drag0
            tilt, az = min(85.0, max(0.0, v0["tilt"] + dy * 0.25)), ((v0["az"] - dx * 0.35 + 180.0) % 360.0) - 180.0
            if part:
                self.set_part_view(part, quick=True, tilt=tilt, az=az)
            else:
                self.set_view(tilt=tilt, az=az, quick=True)

        def on_release(self):
            self._drag0 = None; self.schedule(0)

        def on_wheel(self, delta):
            if self.spec and self.gen is None and not self.locked_part():
                part = self.view_part()
                z = (part["view"] if part else self.spec["view"]).get("zoom", 1.0)
                z = min(3.0, max(0.5, float(z) * 1.1 ** (delta / 120.0)))
                self.set_part_view(part, zoom=z) if part else self.set_view(zoom=z)

        # ---------- making the movie ----------
        def choose_folder(self):
            d = QtW.QFileDialog.getExistingDirectory(self, "Save movies in", self.folder_le.text() or os.path.expanduser("~"))
            if d:
                self.folder_le.setText(d)

        def make_movie(self):
            if not self.st.can_show or self.gen is not None or self.busy:
                return
            if not any(p.get("on", True) for p in self.spec["shots"]):
                self.say("The movie has no parts. Add one with + Add part under the timeline.", "err"); return
            folder = os.path.abspath(os.path.expanduser(self.folder_le.text().strip() or self.st.outdir))
            name = re.sub(r"[^\w\-. ]+", "_", self.name_le.text().strip()).strip() or "tomogram_reveal"
            name = re.sub(r"\.(mp4|mov)$", "", name, flags=re.I) + ".mp4"
            path = os.path.join(folder, name)
            spec = self.spec_for(); resume = False; outs = output_paths(path, spec["output"].get("formats") or ["mp4"])
            part = read_partial(path)
            if part and part.get("chunks_done"):
                from .encode import settings_hash
                same = part.get("settings") == settings_hash(spec, self.st.ds)
                resume = self.ask_resume(part, same)
                if resume is None:
                    return
            there = [os.path.basename(p) for p in list(outs.values()) + [poster_path(path)] if os.path.exists(p)]  # the poster is replaced too
            if there and not resume:  # asked once for all of them
                r = QtW.QMessageBox.question(self, "TomoReveal", f"{', '.join(there)} already exist{'s' if len(there) == 1 else ''} in that "
                                             f"folder. Replace {'it' if len(there) == 1 else 'them'}?")
                if r != QtW.QMessageBox.Yes:
                    return
            try:
                os.makedirs(folder, exist_ok=True)
            except OSError as exc:
                self.say(f"Cannot use that folder: {exc}", "err"); return
            self.settings.setValue("outdir", folder)
            self.stop_play(); self.project.autosave_now(); self.export.stop()
            try:
                self.gen = movie_frames(self.st.ren, spec, path, resume=resume)
            except Exception as exc:
                traceback.print_exc(); self.say(f"Could not start the movie: {exc}", "err"); return
            self.render_path = path; self.gen_paths = dict(self.gen.paths); self.t0 = time.time()
            self._rendering(True); self.progress.setMaximum(len(self.gen)); self.progress.setValue(self.gen.start); self.status_lbl.setText("Starting ..." if not self.gen.start else
                                                           f"Continuing after frame {self.gen.start} ..."); self.render_timer.start()

        def ask_resume(self, part, same):
            """An interrupted movie of this name: True = continue, False = start again, None = do nothing."""
            if not same:
                r = QtW.QMessageBox.question(self, "TomoReveal", "A movie of this name was interrupted, but the settings have changed "
                                             "since. Start it again from the beginning?")
                return False if r == QtW.QMessageBox.Yes else None
            box = QtW.QMessageBox(self); box.setWindowTitle("TomoReveal")
            box.setText(f"A movie was interrupted after {part['frames_done']} of {part['total']} frames. Continue it or start again?")
            b_c = box.addButton("Continue", QtW.QMessageBox.AcceptRole); b_s = box.addButton("Start again", QtW.QMessageBox.DestructiveRole)
            box.addButton("Cancel", QtW.QMessageBox.RejectRole); box.exec()
            return True if box.clickedButton() is b_c else False if box.clickedButton() is b_s else None

        def _rendering(self, on):
            # everything is locked while the movie is made, except Cancel (in the Make the movie box)
            for i, page in enumerate(self.pages):
                if i != self.TAB_MOVIE:
                    page.setEnabled(not on)
            for g in (self.view_group, self.clip_group, self.movie_group):
                g.setEnabled(not on)
            self.timeline.setEnabled(not on); self.play_btn.setEnabled(not on)
            for w in (self.quality_cb, self.name_le, self.folder_le, self.aa_movie, *self.export.fmt.values(), self.export.size_cb, self.export.codec_cb):
                w.setEnabled(not on)
            self.go_btn.setVisible(not on); self.cancel_btn.setVisible(on); self.progress.setVisible(on)
            if on:
                self.progress.setValue(0)

        def _render_step(self):
            try:
                i, n, img = next(self.gen)
            except StopIteration:
                return self._render_done()
            except Exception as exc:
                traceback.print_exc(); self._render_stop()
                self.say(f"Making the movie failed: {exc}", "err"); self.status_lbl.setText(""); return
            self.progress.setMaximum(n); self.progress.setValue(i)
            el = time.time() - self.t0; done = max(1, i - self.gen.start)
            self.status_lbl.setText(f"frame {i} of {n} \u00b7 about {fmt_time(el / done * (n - i))} left")
            if i % 6 == 1 or i == n:
                self.prev.set_image(to_qimage(img))

        def _render_stop(self):
            self.render_timer.stop()
            if self.gen is not None:
                self.gen.close(); self.gen = None
            self._rendering(False)

        def cancel_render(self):
            self._render_stop()  # deletes the partial files and the new poster; the old movie and poster are left alone
            self.status_lbl.setText("Cancelled."); self.schedule(0)

        def _render_done(self):
            self.render_timer.stop(); self.gen = None; self._rendering(False)
            el = time.time() - self.t0
            outs = self.gen_paths if hasattr(self, "gen_paths") else {"mp4": self.render_path}
            self.last_movie = next(iter(outs.values())); self.open_btn.setEnabled(True)
            n = self.progress.maximum(); secs = n / int(self.spec["output"].get("fps", 30))
            sizes = self.export.log_true_size(self.quality_cb.currentIndex(), outs)
            self.status_lbl.setText(f"Finished in {fmt_time(el)} \u00b7 {secs:.1f} s movie \u00b7 {sizes} \u00b7 saved in {os.path.dirname(self.last_movie)}")
            self.say("Movie saved.", "ok"); self.schedule(0)

        def _open(self, path):
            if path and os.path.exists(path):
                QtG.QDesktopServices.openUrl(QtC.QUrl.fromLocalFile(path))

        def closeEvent(self, e):
            if self.gen is not None:
                r = QtW.QMessageBox.question(self, "TomoReveal", "A movie is still being made. Stop it and quit?")
                if r != QtW.QMessageBox.Yes:
                    e.ignore(); return
            if not self.project.confirm_discard("closing TomoReveal"):
                e.ignore(); return
            if self.gen is not None:  # it went on while the questions were open, so it may have ended
                self.cancel_render()
            self.playing = None; self.play_timer.stop(); self.prev_timer.stop(); self.thr_timer.stop()  # nothing draws after this
            if getattr(self, "_hang_watch", None) is not None:  # closing may wait for a load: not a freeze
                import faulthandler
                self._hang_watch.stop(); faulthandler.cancel_dump_traceback_later()
            if self.worker is not None and self.worker.isRunning():  # let the load end, but never make a renderer for it
                for sig in (self.worker.done, self.worker.failed, self.worker.log):
                    try:
                        sig.disconnect()
                    except (RuntimeError, TypeError):  # nothing connected
                        pass
                self.hide(); self.worker.wait()
            self.param_timer.stop()
            for wk in (self._surf_worker, self._path_worker):  # surfaces and camera paths being worked out: let them end
                if wk is not None and wk.isRunning():
                    for sig in (wk.done, wk.failed, wk.log):
                        try:
                            sig.disconnect()
                        except (RuntimeError, TypeError):
                            pass
                    self.hide(); wk.wait()
            self.project.clean_exit(); self.st._drop_renderer(); e.accept()

    app = QtW.QApplication.instance() or QtW.QApplication(sys.argv[:1])
    app.setApplicationName("TomoReveal"); app.setOrganizationName("TomoReveal")
    app.setWindowIcon(QtG.QIcon(QtG.QPixmap.fromImage(to_qimage(np.asarray(make_icon(256))))))
    apply_theme(app, QtC.QSettings().value("theme", "system"))  # before the window is made
    setup_crash_log()
    win = Main(); win.show()
    if not getattr(args, "headless", False):
        win._hang_watch = start_hang_watch(win)

    def excepthook(t, v, tb):  # never fail silently: log it, autosave, and say so in the window
        path = log_exception(t, v, tb); traceback.print_exception(t, v, tb)
        try:
            win.project.autosave_now()
            win.say(f"Something went wrong. Your work was autosaved. Details: {path}", "err")
        except Exception:
            pass
    sys.excepthook = excepthook
    return app, win


def cmd_demo(args):
    paths = make_demo(args.folder or os.path.join(tempfile.gettempdir(), "tomoreveal_demo"))
    print("demo files:", paths["tomogram"], paths["labels"])
    if not args.no_gui:
        args.files = [paths["tomogram"], paths["labels"]]
        cmd_gui(args)


def cmd_gui(args):
    try:
        import PySide6  # noqa: F401
    except ImportError:
        print("tomoreveal: the desktop window needs PySide6 (conda install -c conda-forge pyside6); opening the browser version instead.")
        args.port, args.no_browser, args.outdir = 8765, False, args.outdir or "tomoreveal_output"
        return cmd_app(args)
    app, _win = build_gui(args)
    sys.exit(app.exec())
