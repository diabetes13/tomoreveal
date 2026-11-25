"""Light and dark window themes (the window only; the movie keeps its own background)."""
from PySide6 import QtCore as QtC, QtGui as QtG, QtWidgets as QtW

CHOICES = (("System", "system"), ("Light", "light"), ("Dark", "dark"))
DIM = "color: palette(placeholder-text)"  # grey notes that follow the theme
# colour of each kind of movie part: timeline blocks, the Movie tab's stripes and menus that list parts (3.6: one table)
TYPE_COLOURS = {"slide": "#4e79a7", "overview": "#59a14f", "focus": "#d1495b", "hold": "#8a8f98", "card": "#9c6ade", "path": "#e0a030"}


def part_colour(part):
    """The colour of a movie part (by its type), as on the timeline."""
    return TYPE_COLOURS.get((part or {}).get("type", ""), "#888888")


def part_icon(part, size=12):
    """A small square of the part's colour, for menus that list parts."""
    pm = QtG.QPixmap(size, size); pm.fill(QtG.QColor(part_colour(part)))
    return QtG.QIcon(pm)

_MESSAGE = {False: {"err": "#c62828", "ok": "#2e7d32"},   # readable on a light window
            True: {"err": "#ff7b72", "ok": "#56d364"}}    # readable on a dark window


def light_palette():
    pal = QtW.QStyleFactory.create("Fusion").standardPalette()
    pal.setColor(QtG.QPalette.PlaceholderText, QtG.QColor("#6b7079"))
    return pal


def dark_palette():
    P, c = QtG.QPalette, QtG.QColor
    pal = QtG.QPalette()
    for role, col in ((P.Window, "#2b2d31"), (P.WindowText, "#e6e6e6"), (P.Base, "#1e1f22"), (P.AlternateBase, "#26282c"),
                      (P.Text, "#e6e6e6"), (P.Button, "#35373c"), (P.ButtonText, "#e6e6e6"), (P.BrightText, "#ff6b6b"),
                      (P.Highlight, "#3d7ccf"), (P.HighlightedText, "#ffffff"), (P.ToolTipBase, "#1e1f22"),
                      (P.ToolTipText, "#e6e6e6"), (P.PlaceholderText, "#9399a2"), (P.Link, "#6ea8fe"),
                      (P.Light, "#4a4d53"), (P.Midlight, "#3d4045"), (P.Mid, "#55585e"), (P.Dark, "#1a1b1e"), (P.Shadow, "#0d0d0f")):
        pal.setColor(role, c(col))
    for role, col in ((P.WindowText, "#6c6f75"), (P.Text, "#6c6f75"), (P.ButtonText, "#6c6f75"),
                      (P.Highlight, "#3a3d42"), (P.HighlightedText, "#9a9da3"), (P.Base, "#252629"), (P.Button, "#2e3034")):
        pal.setColor(P.Disabled, role, c(col))
    return pal


def system_is_dark(app):
    """The desktop's colour scheme (Qt 6.5+); Light where Qt cannot tell."""
    hints = app.styleHints()
    scheme = getattr(hints, "colorScheme", None)
    try:
        return scheme is not None and scheme() == QtC.Qt.ColorScheme.Dark
    except Exception:
        return False


def resolve(choice, app):
    return system_is_dark(app) if choice == "system" else choice == "dark"


def apply_theme(app, choice):
    """Fusion style with a light or dark palette; returns True when dark."""
    dark = resolve(choice if choice in ("system", "light", "dark") else "system", app)
    app.setStyle("Fusion")
    app.setPalette(dark_palette() if dark else light_palette())
    app.setProperty("tomoreveal_dark", dark)
    for w in app.allWidgets():  # style sheets that use palette(...) are resolved again
        if w.styleSheet():
            w.setStyleSheet(w.styleSheet())
        w.update()
    return dark


def is_dark(app=None):
    app = app or QtW.QApplication.instance()
    return bool(app and app.property("tomoreveal_dark"))


def message_color(kind):
    return _MESSAGE[is_dark()].get(kind)
