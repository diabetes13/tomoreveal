"""Light and dark window themes (the window only; the movie keeps its own background)."""
from PySide6 import QtCore as QtC, QtGui as QtG, QtWidgets as QtW

CHOICES = (("System", "system"), ("Light", "light"), ("Dark", "dark"))
DIM = "color: palette(placeholder-text)"  # grey notes that follow the theme

_MESSAGE = {False: {"err": "#c62828", "ok": "#2e7d32"},   # readable on a light window
            True: {"err": "#ff7b72", "ok": "#56d364"}}    # readable on a dark window


def light_palette():
    """Fusion's light colours, written out: Fusion's own standard palette follows the system's colour scheme in Qt 6,
    so on a Windows in dark mode it is dark grey."""
    P, c = QtG.QPalette, QtG.QColor
    pal = QtG.QPalette()
    for role, col in ((P.Window, "#efefef"), (P.WindowText, "#000000"), (P.Base, "#ffffff"), (P.AlternateBase, "#f7f7f7"),
                      (P.Text, "#000000"), (P.Button, "#efefef"), (P.ButtonText, "#000000"), (P.BrightText, "#ffffff"),
                      (P.Highlight, "#308cc6"), (P.HighlightedText, "#ffffff"), (P.ToolTipBase, "#ffffdc"),
                      (P.ToolTipText, "#000000"), (P.PlaceholderText, "#6b7079"), (P.Link, "#0000ff"), (P.LinkVisited, "#ff00ff"),
                      (P.Accent, "#308cc6"), (P.NoRole, "#000000"),
                      (P.Light, "#ffffff"), (P.Midlight, "#cacaca"), (P.Mid, "#b8b8b8"), (P.Dark, "#9f9f9f"), (P.Shadow, "#767676")):
        pal.setColor(role, c(col))
    for role, col in ((P.WindowText, "#bebebe"), (P.Text, "#bebebe"), (P.ButtonText, "#bebebe"), (P.Dark, "#bebebe"),
                      (P.Shadow, "#b1b1b1"), (P.Highlight, "#919191"), (P.Accent, "#919191"), (P.Base, "#efefef")):
        pal.setColor(P.Disabled, role, c(col))
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


def qt_color_scheme(choice):
    """The colour scheme Qt is told: Light or Dark, or Unknown for System (the desktop decides)."""
    S = QtC.Qt.ColorScheme
    return {"light": S.Light, "dark": S.Dark}.get(choice, S.Unknown)


def _tell_qt(app, choice):
    """Qt 6.8+: the title bar and Qt's own dialogs follow the theme too. Older Qt can't; the palette is enough there."""
    try:
        app.styleHints().setColorScheme(qt_color_scheme(choice))
    except (AttributeError, RuntimeError):
        pass


def apply_theme(app, choice):
    """Fusion style with a light or dark palette; returns True when dark."""
    choice = choice if choice in ("system", "light", "dark") else "system"
    _tell_qt(app, choice)  # first: for System, the desktop's own scheme is read after this
    dark = resolve(choice, app)
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
