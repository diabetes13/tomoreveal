"""The settings of a flat membrane surface (3.6, R21) under a structure's "Smoother surface": fill gaps, flatness,
thickness (measured, or typed), shown when "flat sheet, even thickness" is chosen."""
from PySide6 import QtWidgets as QtW

from . import flat_membrane as fm
from .gui_theme import DIM


def measured(win, key):
    """(thickness used, measured) in voxels from the structure's current flat surface, or (None, None)."""
    ds = win.st.ds; k = ds.id_of(key) if ds is not None else None
    m = ds.meshes.get(k) if k is not None else None
    if m is None:
        return None, None
    fd = m.field_data
    t = float(fd["thickness"][0]) if "thickness" in fd else None
    me = float(fd["measured"][0]) if "measured" in fd else None
    return t, me


def nm(win, vox):
    from .overlays import voxel_size
    a = voxel_size(win.spec, win.st.ds) if win.spec is not None else None
    return f" ({vox * a / 10.0:.1f} nm)" if a else ""


class FlatRows:
    """The rows added to a structure's settings form; shown only while "flat sheet" is chosen."""

    def __init__(self, form, widgets, refresh):
        self.form, self.widgets, self.refresh = form, widgets, refresh

    def setVisible(self, on):
        for w in self.widgets:
            self.form.setRowVisible(w, bool(on))

    def isHidden(self):
        return all(w.isHidden() for w in self.widgets)


def flat_rows(win, key, sc, f):
    """Fill gaps up to / Flatness / Thickness / a note, as rows of the form f. Returns a FlatRows."""
    st = fm.settings(sc.get("flat"))

    def set_(**kw):
        cur = fm.settings((win.spec["classes"].get(key) or {}).get("flat")); cur.update(kw)
        win._param_changed(key, "flat", cur)
    gaps = QtW.QComboBox(); gaps.setSizeAdjustPolicy(QtW.QComboBox.AdjustToMinimumContentsLengthWithIcon)
    for t, v in fm.GAPS:
        gaps.addItem(t, v)
    gaps.setCurrentIndex(max(0, gaps.findData(st["gaps"])))
    gaps.setToolTip("Holes and breaks in the segmentation up to this size are bridged, so a broken membrane becomes one sheet. "
                    "Two membranes closer than this stay two sheets only where each is segmented as its own sheet.")
    gaps.activated.connect(lambda j: set_(gaps=gaps.itemData(j)))
    f.addRow("Fill gaps up to", gaps)
    fl = QtW.QComboBox()
    for t, v in fm.FLATNESS:
        fl.addItem(t, v)
    fl.setCurrentIndex(max(0, fl.findData(st["flatness"])))
    fl.setToolTip("How strongly the middle of the membrane is smoothed: more gives a flatter sheet with fewer small bends; "
                  "less keeps membranes that run close together (2 to 4 voxels apart) as two sheets")
    fl.activated.connect(lambda j: set_(flatness=fl.itemData(j)))
    f.addRow("Flatness", fl)
    th = QtW.QDoubleSpinBox(); th.setRange(0, 20); th.setSingleStep(0.25); th.setDecimals(2); th.setSuffix(" voxels")
    th.setSpecialValueText("as measured"); th.setValue(float(st["thickness"] or 0))
    th.setToolTip("The thickness of the sheet: 'as measured' uses the median thickness of the segmented membrane; type a "
                  "number to choose it yourself")
    th.editingFinished.connect(lambda: set_(thickness=float(th.value()) or None))
    f.addRow("Thickness", th)
    note = QtW.QLabel(""); note.setWordWrap(True); note.setStyleSheet(DIM)
    note.setToolTip("Flat sheet: a model, not the segmentation as it is")
    f.addRow(note)

    def refresh():
        """The note under the settings: the measured thickness of the surface shown now."""
        cur = fm.settings((win.spec["classes"].get(key) or {}).get("flat")) if win.spec is not None else st
        used, me = measured(win, key)
        note.setText(("Measured thickness: " + f"{me:.2f} voxels" + nm(win, me) + ". " if me else "")
                     + f"A smooth model of the membrane with even thickness; gaps up to {cur['gaps']} voxels are filled. "
                     "Use the other levels to show the segmentation as it is.")
    refresh()
    return FlatRows(f, [gaps, fl, th, note], refresh)
