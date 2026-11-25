"""Typed numbers next to sliders (3.6, PLAN-3.6 1.5): the value beside a slider is a box you can type into."""
import re

from PySide6 import QtCore as QtC, QtWidgets as QtW

TIP = ("The value of the slider beside it. Type a number and press Enter (Esc cancels); the up and down arrow keys "
       "step it, with Shift ten steps at a time.")
_NUM = re.compile(r"[-+]?\d+(?:[.,]\d+)?")


def number_in(text):
    """The first number in a typed text ('30', '30°', '1.5x', '-12 nm', '2,5'), or None."""
    m = _NUM.search(text or "")
    return float(m.group(0).replace(",", ".")) if m else None


def nearest_step(slider, fmt, value):
    """The slider position whose shown value is nearest to `value` (the shown value comes from fmt, whatever the units)."""
    best, err = None, None
    for v in range(slider.minimum(), slider.maximum() + 1):
        shown = number_in(fmt(v))
        if shown is None:
            continue
        e = abs(shown - value)
        if err is None or e < err - 1e-12:
            best, err = v, e
    return best


class NumberEdit(QtW.QLineEdit):
    """Shows fmt(slider value); typing a number and Enter moves the slider there (clamped to its range)."""

    def __init__(self, slider, fmt, width=52):
        super().__init__(fmt(slider.value()))
        self.slider, self.fmt = slider, fmt
        self.setFixedWidth(width); self.setAlignment(QtC.Qt.AlignRight | QtC.Qt.AlignVCenter); self.setToolTip(TIP)
        self.setFrame(False); self.setProperty("tomoreveal_number", True)
        self.editingFinished.connect(self.apply)

    def show_value(self, v=None):
        if not self.hasFocus():
            self.setText(self.fmt(self.slider.value() if v is None else int(round(v))))

    def apply(self):
        x = number_in(self.text())
        if x is not None:
            v = nearest_step(self.slider, self.fmt, x)
            if v is not None and v != self.slider.value():
                self.slider.setValue(v)  # its own handler runs, as when it is dragged
        self.setText(self.fmt(self.slider.value()))

    def keyPressEvent(self, e):
        k = e.key()
        if k == QtC.Qt.Key_Escape:
            self.setText(self.fmt(self.slider.value())); self.clearFocus(); return
        if k in (QtC.Qt.Key_Up, QtC.Qt.Key_Down):
            step = 10 if e.modifiers() & QtC.Qt.ShiftModifier else 1
            self.slider.setValue(self.slider.value() + (step if k == QtC.Qt.Key_Up else -step))
            self.setText(self.fmt(self.slider.value())); return
        super().keyPressEvent(e)

    def focusOutEvent(self, e):
        super().focusOutEvent(e); self.setText(self.fmt(self.slider.value()))


def to_voxels(text, nm_per_voxel):
    """A typed clip value in voxels: '120' is voxels; '120 nm' (or 'nm 120') is nanometres when the voxel size is known."""
    x = number_in(text)
    if x is None:
        return None
    if "nm" in (text or "").lower() and nm_per_voxel:
        return int(round(x / nm_per_voxel))
    return int(round(x))


class ClipNumbers(QtW.QWidget):
    """From and to of one clip-box axis as two boxes you can type into (voxels, or '120 nm'), beside its bar.
    setText("lo - hi") updates them from code, as the old read-out label did."""
    TIP = ("Where the kept part starts and ends along this axis, in voxels. Type a number and press Enter; add 'nm' to "
           "type nanometres (for example 120 nm).")

    def __init__(self, on_set, nm_per_voxel):
        super().__init__()
        self.on_set, self.nm = on_set, nm_per_voxel
        h = QtW.QHBoxLayout(self); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(2)
        self.lo, self.hi = QtW.QLineEdit("0"), QtW.QLineEdit("0")
        for e in (self.lo, self.hi):
            e.setFixedWidth(46); e.setAlignment(QtC.Qt.AlignRight | QtC.Qt.AlignVCenter); e.setToolTip(self.TIP)
            e.editingFinished.connect(self._typed); h.addWidget(e)
        self._v = (0, 0)

    def setText(self, text):
        nums = [int(float(x)) for x in re.findall(r"-?\d+", text or "")][:2]
        if len(nums) == 2:
            self._v = tuple(nums)
            for e, v in zip((self.lo, self.hi), nums):
                if not e.hasFocus():
                    e.setText(str(v))
            nm = self.nm()
            tip = self.TIP + (f"\nNow {nums[0] * nm:.0f} – {nums[1] * nm:.0f} nm." if nm else "")
            self.lo.setToolTip(tip); self.hi.setToolTip(tip)

    def _typed(self):
        nm = self.nm()
        lo, hi = to_voxels(self.lo.text(), nm), to_voxels(self.hi.text(), nm)
        lo = self._v[0] if lo is None else lo; hi = self._v[1] if hi is None else hi
        if (lo, hi) != self._v:
            self.on_set(min(lo, hi), max(lo, hi))
        self.lo.setText(str(self._v[0])); self.hi.setText(str(self._v[1]))
