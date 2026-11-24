"""Sections that fold open and closed (3.5): fewer settings and less text on screen at once.

`Section` is a group box whose title opens and closes it; its contents go in `.body`. `fold_rows` moves rows of a
QFormLayout into a closed "More settings" part. Whether each one is open is remembered (QSettings "fold/<key>")."""
from PySide6 import QtCore as QtC, QtWidgets as QtW

OPEN, CLOSED = "▾ ", "▸ "


def _remembered(key, default):
    if not key:
        return default
    v = QtC.QSettings().value(f"fold/{key}", None)
    return default if v is None else str(v).lower() in ("true", "1")


def _remember(key, on):
    if key:
        QtC.QSettings().setValue(f"fold/{key}", bool(on))


class Section(QtW.QGroupBox):
    """A group box that folds: click its title. Put the contents in `.body` (give it a layout)."""
    toggled = QtC.Signal(bool)

    def __init__(self, title, key=None, open=True, tip=None):
        super().__init__()
        self._title, self.key = title, key
        lay = QtW.QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(0)
        self.body = QtW.QWidget(); lay.addWidget(self.body)
        if tip:
            self.setToolTip(tip)
        self.set_open(_remembered(key, open), remember=False)

    def title_text(self):
        return self._title

    def setTitle(self, title):  # noqa: N802 (Qt name): keeps the fold mark when the title changes
        self._title = title; super().setTitle((OPEN if self.is_open() else CLOSED) + title)

    def is_open(self):
        return not self.body.isHidden()

    def set_open(self, on, remember=True):
        self.body.setVisible(bool(on)); super().setTitle((OPEN if on else CLOSED) + self._title)
        self.setFlat(not on)  # closed: only the title, no empty frame
        if remember:
            _remember(self.key, on)
        self.toggled.emit(bool(on))

    def _on_title(self, pos):
        return pos.y() <= self.fontMetrics().height() + 6

    def mousePressEvent(self, e):
        if e.button() == QtC.Qt.LeftButton and self._on_title(e.position()):
            self.set_open(not self.is_open()); e.accept(); return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        self.setCursor(QtC.Qt.PointingHandCursor if self._on_title(e.position()) else QtC.Qt.ArrowCursor)
        super().mouseMoveEvent(e)


class Fold(QtW.QWidget):
    """A "▸ More settings" button with the rows under it (inside a form: added as one spanning row)."""

    def __init__(self, text, key=None, open=False, tip=None, header=None):
        super().__init__()
        lay = QtW.QVBoxLayout(self); lay.setContentsMargins(0, 2, 0, 0); lay.setSpacing(2)
        top = QtW.QHBoxLayout(); top.setContentsMargins(0, 0, 0, 0); top.setSpacing(4)
        self.button = QtW.QToolButton(); self.button.setAutoRaise(True); self.button.setCheckable(True)
        self.button.setToolButtonStyle(QtC.Qt.ToolButtonTextBesideIcon)
        self.button.setToolTip(tip or "Show or hide these settings")
        self._text, self.key = text, key
        top.addWidget(self.button)
        if header is not None:  # a label next to the button (for example the transition's heading)
            top.addWidget(header, 1)
            header.mousePressEvent = lambda e: self.button.toggle()
        else:
            top.addStretch(1)
        lay.addLayout(top)
        self.body = QtW.QWidget(); self.form = QtW.QFormLayout(self.body); self.form.setContentsMargins(14, 0, 0, 0)
        lay.addWidget(self.body)
        self.button.toggled.connect(self._toggled)
        on = _remembered(key, open)
        self.button.setChecked(on); self._toggled(on, remember=False)

    def _toggled(self, on, remember=True):
        self.body.setVisible(bool(on)); self.button.setArrowType(QtC.Qt.DownArrow if on else QtC.Qt.RightArrow)
        self.button.setText(self._text)
        if remember:
            _remember(self.key, on)

    def is_open(self):
        return self.button.isChecked()


def _row_label(form, i):
    it = form.itemAt(i, QtW.QFormLayout.LabelRole)
    w = it.widget() if it is not None else None
    if w is None:
        return None if form.itemAt(i, QtW.QFormLayout.SpanningRole) is not None else ""
    return w.text() if isinstance(w, QtW.QLabel) else ""


def fold_rows(form, labels, fold, start=0, stop=None):
    """Move the rows of `form` named in `labels` (each with the unnamed rows right after it) into `fold.form`,
    keeping their order. Rows from `stop` on are left alone. Returns how many rows moved."""
    stop = form.rowCount() if stop is None else stop
    plan, take = [], False
    for i in range(start, stop):
        lab = _row_label(form, i)
        if lab:  # a named row starts a new group
            take = lab in labels
        elif lab is None:  # a spanning row (a heading) ends the group
            take = False
        if take:
            plan.append(i)
    for i in reversed(plan):
        r = form.takeRow(i)
        lw = r.labelItem.widget() if r.labelItem is not None else None
        fw = r.fieldItem.widget() if r.fieldItem is not None else None
        fl = r.fieldItem.layout() if r.fieldItem is not None and fw is None else None
        plan[plan.index(i)] = (lw, fw or fl)
    for lw, field in plan:
        if field is None:
            continue
        if lw is not None:
            fold.form.addRow(lw, field)
        else:
            fold.form.addRow(field)
    return len(plan)
