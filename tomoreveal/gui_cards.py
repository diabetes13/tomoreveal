"""Settings of a title / end / blank card in the Movie tab: background, text and picture blocks, fades."""
import copy
import os

from PySide6 import QtCore as QtC, QtGui as QtG, QtWidgets as QtW

from . import storyboard as sb
from .gui_theme import DIM

BACKGROUNDS = (("plain colour", "color"), ("colour gradient", "gradient"), ("picture", "image"), ("transparent", "transparent"))
THEN = (("fade to black", "fade"), ("dissolve into the next part", "dissolve"), ("cut", "cut"))
ALIGN = (("centred", "center"), ("left", "left"), ("right", "right"))
PICTURES = "Pictures (*.png *.jpg *.jpeg *.PNG *.JPG *.JPEG)"


def _colour_button(hexc, on_pick, title="Colour"):
    b = QtW.QPushButton(); b.setFixedSize(30, 22); b.setToolTip(f"Choose the {title.lower()}")
    b.setStyleSheet(f"background:{hexc}; border:1px solid palette(mid); border-radius:3px")

    def pick():
        c = QtW.QColorDialog.getColor(QtG.QColor(hexc), b, title)
        if c.isValid():
            b.setStyleSheet(f"background:{c.name()}; border:1px solid palette(mid); border-radius:3px"); on_pick(c.name())
    b.clicked.connect(pick)
    return b


def _row(*widgets, stretch=True):
    w = QtW.QWidget(); h = QtW.QHBoxLayout(w); h.setContentsMargins(0, 0, 0, 0)
    for x in widgets:
        h.addWidget(x)
    if stretch:
        h.addStretch(1)
    return w


def card_rows(panel, pid, p, form):
    """Add the card's settings to the part's form. panel: the PartsPanel."""
    win = panel.win

    def part():
        return panel.part(pid)

    def changed(rebuild=False):
        q = part()
        if q is not None:
            panel.set(pid, "blocks", q["blocks"], rebuild=rebuild)

    # background
    bg = p.get("background") or {}
    kind = QtW.QComboBox()
    for t, d in BACKGROUNDS:
        kind.addItem(t, d)
    kind.setCurrentIndex(max(0, kind.findData(bg.get("kind", "color"))))

    def set_bg(**kw):
        q = part()
        if q is not None:
            b = dict(q.get("background") or {}); b.update(kw); panel.set(pid, "background", b, rebuild="kind" in kw)
    kind.activated.connect(lambda j: set_bg(kind=kind.itemData(j)))
    widgets = [kind]
    k = bg.get("kind", "color")
    if k in ("color", "gradient"):
        widgets.append(_colour_button(bg.get("color", "#000000"), lambda c: set_bg(color=c)))
    if k == "gradient":
        widgets.append(_colour_button(bg.get("color2", "#1c1f26"), lambda c: set_bg(color2=c), "Bottom colour"))
    if k == "image":
        lab = QtW.QLabel(os.path.basename(bg.get("image") or "") or "no picture"); lab.setStyleSheet(DIM)
        lab.setToolTip(bg.get("image") or "")
        ch = QtW.QPushButton("Choose...")

        def choose():
            f, _ = QtW.QFileDialog.getOpenFileName(win, "Background picture", os.path.dirname(bg.get("image") or "") or os.path.expanduser("~"), PICTURES)
            if f:
                set_bg(image=f, kind="image")
        ch.clicked.connect(choose); widgets += [ch, lab]
    if k == "transparent":
        n = QtW.QLabel("see-through in a transparent MOV; black elsewhere"); n.setStyleSheet(DIM); widgets.append(n)
    form.addRow("Background", _row(*widgets))

    # blocks
    blocks = p.get("blocks") or []
    for i, b in enumerate(blocks):
        box = QtW.QFrame(); box.setFrameShape(QtW.QFrame.StyledPanel); v = QtW.QVBoxLayout(box); v.setContentsMargins(4, 2, 4, 2)
        head = QtW.QHBoxLayout(); v.addLayout(head)
        head.addWidget(QtW.QLabel(f"<b>{'Text' if b.get('kind') == 'text' else 'Picture'} {i + 1}</b>"), 1)

        def move(d, i=i):
            q = part()
            if q is not None and 0 <= i + d < len(q["blocks"]):
                q["blocks"][i], q["blocks"][i + d] = q["blocks"][i + d], q["blocks"][i]; changed(True)

        def delete(i=i):
            q = part()
            if q is not None:
                del q["blocks"][i]; changed(True)
        for text, tip, fn in (("↑", "Move up", lambda _=False, m=move: m(-1)), ("↓", "Move down", lambda _=False, m=move: m(1)),
                              ("✕", "Delete this block", lambda _=False, d=delete: d())):
            tb = QtW.QToolButton(); tb.setText(text); tb.setToolTip(tip); tb.clicked.connect(fn); head.addWidget(tb)
        if b.get("kind") == "text":
            ed = QtW.QPlainTextEdit(b.get("text", "")); ed.setFixedHeight(54); ed.setPlaceholderText("text (line breaks are kept)")
            ed.setToolTip("The text of this block. The preview follows as you type.")
            timer = QtC.QTimer(ed); timer.setSingleShot(True); timer.setInterval(300)

            def typed(i=i, ed=ed, timer=timer):  # into the settings at once; the preview follows when the typing stops
                q = part()
                if q is not None and i < len(q["blocks"]):
                    q["blocks"][i]["text"] = ed.toPlainText(); timer.start()
            timer.timeout.connect(lambda: (changed(), panel.refresh_line(pid))); ed.textChanged.connect(typed); v.addWidget(ed)
            size = QtW.QDoubleSpinBox(); size.setRange(0.3, 3.3); size.setSingleStep(0.1); size.setValue(float(b.get("size", 1.2)))
            size.setPrefix("size "); size.setToolTip("Text size (3.3 is the largest: 8 % of the picture height)")
            size.valueChanged.connect(lambda val, i=i: (part()["blocks"][i].update(size=float(val)), changed()))
            col = _colour_button(b.get("color", "#ffffff"), lambda c, i=i: (part()["blocks"][i].update(color=c), changed()))
            bold = QtW.QCheckBox("bold"); bold.setChecked(bool(b.get("bold")))
            bold.toggled.connect(lambda val, i=i: (part()["blocks"][i].update(bold=bool(val)), changed()))
            al = QtW.QComboBox()
            for t, d in ALIGN:
                al.addItem(t, d)
            al.setCurrentIndex(max(0, al.findData(b.get("align", "center")))); al.setToolTip("Line up the lines of this text")
            al.activated.connect(lambda j, i=i, al=al: (part()["blocks"][i].update(align=al.itemData(j)), changed()))
            v.addWidget(_row(size, col, bold, al))
        else:
            lab = QtW.QLabel(os.path.basename(b.get("path") or "") or "no picture"); lab.setToolTip(b.get("path") or "")
            if b.get("path") and not os.path.isfile(b["path"]):
                lab.setText(f"{os.path.basename(b['path'])} (not found)")
            ch = QtW.QPushButton("Choose...")

            def choose(i=i):
                q = part(); old = q["blocks"][i].get("path") or ""
                f, _ = QtW.QFileDialog.getOpenFileName(win, "Picture", os.path.dirname(old) or os.path.expanduser("~"), PICTURES)
                if f:
                    q["blocks"][i]["path"] = f; changed(True)
            ch.clicked.connect(lambda _=False, c=choose: c())
            h = QtW.QSpinBox(); h.setRange(2, 100); h.setSuffix(" % of the height"); h.setValue(round(100 * float(b.get("height", 0.12))))
            h.valueChanged.connect(lambda val, i=i: (part()["blocks"][i].update(height=val / 100), changed()))
            v.addWidget(_row(ch, lab, h))
        form.addRow("" if i else "Blocks", box)
    add_text = QtW.QPushButton("+ Text"); add_pic = QtW.QPushButton("+ Picture")

    def add_block(kind):
        q = part()
        if q is None:
            return
        blocks = q.setdefault("blocks", [])
        if kind == "text":
            blocks.append(sb.text_block("Text", y=min(0.9, 0.3 + 0.12 * len(blocks))))
        else:
            f, _ = QtW.QFileDialog.getOpenFileName(win, "Picture (a logo, a microscope image ...)", os.path.expanduser("~"), PICTURES)
            if not f:
                return
            blocks.append(sb.image_block(f))
        changed(True)
    add_text.clicked.connect(lambda: add_block("text")); add_pic.clicked.connect(lambda: add_block("image"))
    tip = QtW.QLabel("Drag blocks in the preview to move them, or their corner to resize them."); tip.setStyleSheet(DIM)
    form.addRow("" if blocks else "Blocks", _row(add_text, add_pic, tip))
    fi = QtW.QDoubleSpinBox(); fi.setRange(0, 10); fi.setSingleStep(0.1); fi.setSuffix(" s"); fi.setValue(float(p.get("fade_in", 0.6)))
    fi.setPrefix("fade in "); fi.valueChanged.connect(lambda val: panel.set(pid, "fade_in", float(val)))
    fo = QtW.QDoubleSpinBox(); fo.setRange(0, 10); fo.setSingleStep(0.1); fo.setSuffix(" s"); fo.setValue(float(p.get("fade_out", 0.6)))
    fo.setPrefix("out "); fo.valueChanged.connect(lambda val: panel.set(pid, "fade_out", float(val)))
    then = QtW.QComboBox()
    for t, d in THEN:
        then.addItem(t, d)
    then.setCurrentIndex(max(0, then.findData(p.get("transition_to_next", "fade"))))
    then.activated.connect(lambda j: panel.set(pid, "transition_to_next", then.itemData(j)))
    form.addRow("Fades", _row(fi, fo, then))


def copy_blocks(p):
    return copy.deepcopy(p.get("blocks", []))
