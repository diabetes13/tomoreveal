"""Every tooltip of the window in one place, so all texts can be read and improved together.

Keys: an object name ("go_btn"), the text of a button or tick box ("text:Make movie"), or the label of the row a
control sits in ("label:Opacity"). `apply(root)` gives every interactive control that has no tooltip yet its text,
also as status tip (shown in the status bar) and as "What's this?" text.
"""
import re

TEXTS = {
    # Data tab
    "text:Add tomogram...": "Open a tomogram file (.mrc, .rec, .tif). It is always used as the tomogram, even if it looks like a segmentation.",
    "text:Add segmentation...": "Open a Dragonfly session (.ORSSession), a label volume, or easymode / Ais score files (name__feature.mrc).",
    "text:Try demo data": "Load a small made-up tomogram with membranes, vesicles, filaments and particles, to try the program.",
    "text:Start over": "Unload everything and begin a new project. Asks first about changes that are not saved. Your files on disk are not touched.",
    "text:Remove": "Unload this file. Your file on disk is not touched. When nothing is left to show, your movie is kept for the next data.",
    "text:Use the file's value": "Forget the voxel size you typed and use the one in the file's header.",
    "label:<b>Voxel size</b>": "Size of one voxel in nanometres. The scale bar and the depth shown with the slice number use it.",
    "label:From the side": "Where the light comes from, around the scene (in degrees).",
    "label:Height": "How high the light is above the slice: 90\u00b0 is straight from above.",
    "label:Legend": "A title above the legend (optional).",
    "label:Tomogram": "Show the tomogram as set in the Look tab, always, faded or hidden during this part.",
    "label:speed": "Even speed along the path, or slower at the start and the end.",
    "label:Voxel size": "Size of one voxel in nanometres. The scale bar and the depth shown with the slice number use it.",
    # Structures tab
    "text:Zoom": "Add a close-up of this structure to the movie.",
    "text:⚙": "Opacity, threshold, small specks, smoothing and surface style of this structure.",
    "label:Opacity": "How solid the structure looks: 100 % is solid, lower values let you see through it.",
    "label:Threshold": "For easymode / Ais scores: the surface is drawn where the network's score is above this value.",
    "label:Remove specks smaller than": "Leave out separate bits smaller than this many voxels. Helps with noisy segmentations.",
    "label:Smoothing": "Smoothing steps of the surface. 0 shows the raw voxel steps; more makes it rounder.",
    "label:Style": "Normal, Cartoon (flat colour with outlines), Dots, or Glossy (shiny, metallic or glass).",
    "text:Apply to all": "Give every structure this surface style.",
    # Explore tab
    "text:2D slice": "Look at one slice of the tomogram with the segmentation drawn on it.",
    "text:3D at this slice": "Look at the 3D view with the slice at this depth.",
    "text:Previous": "The slice before (Up arrow or Page Up for 10).",
    "text:Next": "The slice after (Down arrow or Page Down for 10).",
    "text:Start the slice sweep here": "The movie's slide through the slices starts at this slice.",
    "text:End the slice sweep here": "The movie's slide through the slices ends at this slice.",
    "text:Add a close-up of the structure under the mouse": "Adds a close-up of the structure you point at to the movie.",
    "label:Overlay opacity": "How strongly the segmentation colours are drawn over the slice.",
    "label:Show": "Draw the segmentation filled, as outlines, or not at all.",
    "label:Segmentation": "How the segmentation is drawn on the slice.",
    # Movie tab: main view and clip
    "label:Tilt": "How steeply the camera looks down: 0° is straight from above.",
    "label:Rotate": "Turns the view around the vertical axis.",
    "label:Zoom": "Moves the camera closer (above 1) or further away (below 1).",
    "label:View": "Use the main view, or give this part its own camera position.",
    "text:Fit the view to the kept part": "Frame only the part of the tomogram kept by the clip box.",
    "text:Reset": "Keep the whole tomogram again.",
    "text:Reset to the default movie": "Slide up, slide back revealing the segmentation, overview. Undo brings your parts back.",
    "text:+ Click a structure in the preview": "Then click a structure in the preview to add a close-up of it. Esc cancels.",
    "text:Return to the overview at the end": "After the last close-up, the camera flies back to the whole view.",
    # Movie tab: parts
    "label:Duration": "How long this part lasts, in seconds. You can also drag the right edge of its block on the timeline.",
    "label:Caption": "Text shown at the bottom of the picture while this part plays.",
    "label:Transition in": "Seconds of camera flight into this part. 0 cuts straight to it; automatic picks a smooth flight.",
    "label:From": "Where the slice starts moving.",
    "label:To": "Where the slice stops.",
    "label:Structures": "Which structures show while the slice moves: none, those above the slice, all, or leave them as they are.",
    "label:Pause before": "Seconds the picture stands still before the slice moves.",
    "label:Pause after": "Seconds the picture stands still after the slice stops.",
    "label:Camera movement": "Orbit around the object, rock from side to side, wiggle slightly, or hold still.",
    "label:Rock angle": "How far the camera swings to each side.",
    "label:Structure": "The structure this close-up shows.",
    "label:Object": "The whole structure, or one object of it picked in the preview.",
    "label:Objects in focus": "Everything this close-up shows together. Each keeps its colour.",
    "label:Other structures": "Colour and opacity of the structures that are not in focus.",
    "label:Wiggle": "How far and how fast the camera wiggles, and in which shape.",
    "label:Camera push-in": "The camera moves slowly closer during the close-up.",
    "label:Highlight": "Make the objects in focus pulse brighter, or draw a glowing outline around them.",
    "label:Object effect": "Cross-section: a cut moves through the objects and shows their inside. Grow in: they appear gradually.",
    "label:Vignette": "Darker edges around the objects in focus, as set in the Look tab, or this close-up's own.",
    "label:On the picture": "Show or hide the legend, slice number and scale bar during this part only.",
    "label:Background": "The background of the card: a colour, a gradient, a picture, or see-through.",
    "label:Blocks": "Text and pictures on the card. Drag them in the preview to move them.",
    "label:Fades": "Fade in from black, fade out, and how the card turns into the next part.",
    "text:+ Text": "Add a block of text to the card.",
    "text:+ Picture": "Add a picture (a logo, a microscope image) to the card.",
    "text:bold": "Bold text.",
    "label:The camera": "Follow a structure, such as a membrane, or move through points you set.",
    "label:Follow": "What the camera glides along.",
    "text:Reverse direction": "Glide from the other end.",
    "label:Points": "The points the camera moves through, in order. Each can pause.",
    "text:Add point at the current view": "Stores where the camera is now in the preview.",
    "text:Add point by clicking in the preview": "Click a structure or the slice; the camera will pass near it, looking at it.",
    "label:Camera": "Distance from the path, the side the camera looks from, and its height.",
    "label:Looks": "Look at the path or a little ahead, and the speed along it.",
    "text:show the path in the preview": "Draw the camera's path in the preview while this part is selected. It is never in the movie.",
    "text:push in": "The camera moves slowly closer during the close-up.",
    "text:move the tomogram slice with the cut": "The slice follows the cut, so you see the density inside the object.",
    "text:blur": "Blur what is nearer or further away than the objects in focus.",
    "text:+ Click objects in the preview": "Click objects to add them; click again to take one out; Shift-click adds the whole structure. Esc ends.",
    # Look tab
    "text:Show the tomogram slice": "Show the grey tomogram slice in the preview and the movie.",
    "text:Invert contrast": "Swap dark and light in the tomogram.",
    "label:Contrast": "Stretch the grey levels: more contrast makes dark darker and light lighter.",
    "label:All structures": "Fades all structures together, for a 'ghost' look.",
    "label:Title": "Text shown at the bottom left of the whole movie.",
    "text:Reset position": "Put this item back where it was at first.",
    "text:automatic length": "Pick a round length (1, 2 or 5 × 10ⁿ nm) that fits the picture.",
    "text:label": "Write the length next to the scale bar.",
    "text:box": "A see-through box behind the legend.",
    "text:Slice number: also show the depth in nm": "Add the depth of the slice in nanometres to the slice number.",
    "label:Preset": "Lighting: Default (as before), Simple, Soft, Full (with slice shadows) or Flat (colours only).",
    "label:Direction": "Where the light comes from. Drag on the ball, or use the sliders.",
    "label:Brightness": "Strength of the main light.",
    "label:Fill light": "A second, weaker light from the other side.",
    "label:Ambient": "Light from everywhere: lifts the dark sides.",
    "label:Shininess": "Bright highlights on the surfaces.",
    "label:Soft shadow size": "How far the soft shadows in creases reach (Soft and Full lighting).",
    "text:Light moves with the camera": "On: the light keeps its direction as seen from the viewer. Off: it stays fixed in the scene.",
    "text:Structures cast shadows on the tomogram slice": "Soft shadows on the slice, as if lit from the chosen direction.",
    "label:Strength": "How strong the effect is.",
    "label:Softness": "How soft the edges are.",
    "label:Preview": "Smooth edges in the preview. Off is fastest.",
    "label:Use": "Vignette never, in every close-up, or for the whole movie.",
    "label:Clear centre": "Size of the part in the middle that stays clear.",
    "label:Colour and shape": "Darken towards black or the background colour; round or following the frame.",
    "text:Blur the background in close-ups": "Blur what is nearer or further away than the objects in focus.",
    # Make the movie
    "label:Quality": "Picture size of the movie. Draft is fast; HD suits talks; 4K is slow.",
    "label:Name": "File name of the movie (without extension).",
    "label:Save in": "Folder for the movie files.",
    "text:Choose...": "Choose a folder or a file.",
    "label:File size": "Smaller files at the same resolution. Balanced is a good choice.",
    "label:Codec": "H.264 plays everywhere. H.265 is about half the size but plays in fewer programs.",
    "label:Smooth edges": "Smooth edges in the movie. Best looks cleanest; Off is fastest.",
    "text:Make movie": "Render the movie and save it. You can keep looking at the window while it runs.",
    "text:Cancel": "Stop making the movie; its unfinished files are deleted.",
    "text:Play movie": "Open the finished movie in your video player.",
    "text:Show folder": "Open the folder with the movie files.",
    # preview and timeline
    "text:Play": "Play the movie in the preview from the playhead (Space).",
    "text:Fit": "Fit the whole movie into the timeline.",
    "text:+ Add part": "Add a part after the one under the playhead.",
    "text:Layout boxes": "Draw dashed boxes around every item on the picture, also hidden ones, so you can find and move them.",
    "label:Theme": "Light or dark window. This does not change the movie.",
    # dialogs
    "text:Locate...": "Show TomoReveal where the file is now.",
    "text:Skip": "Open the project without this file. Its structures come back when you locate it later.",
    "text:Use it anyway": "Use the file although it changed since the project was saved.",
}

INTERACTIVE = ("QAbstractButton", "QComboBox", "QAbstractSlider", "QAbstractSpinBox", "QLineEdit", "QPlainTextEdit")


def _clean(t):
    return re.sub(r"\s+", " ", (t or "").replace("&", "")).strip().rstrip(":")


def _containing_layout(lay, w):
    """The (possibly nested) layout that holds widget w directly."""
    if lay is None:
        return None
    if lay.indexOf(w) >= 0:
        return lay
    for i in range(lay.count()):
        sub = lay.itemAt(i).layout()
        if sub is not None:
            r = _containing_layout(sub, w)
            if r is not None:
                return r
    return None


def _row_label(w):
    """The label of the form row a control sits in, or the text of a label just before it in the same row."""
    from PySide6 import QtWidgets as QtW
    a = w
    for _ in range(4):
        p = a.parentWidget()
        if p is None:
            return None
        lay = _containing_layout(p.layout(), a)
        if isinstance(lay, QtW.QFormLayout):
            lab = lay.labelForField(a)
            if lab is None:  # the control is inside a row layout of the form
                for r in range(lay.rowCount()):
                    f = lay.itemAt(r, QtW.QFormLayout.FieldRole)
                    if f is not None and f.layout() is not None and _containing_layout(f.layout(), a) is not None:
                        li = lay.itemAt(r, QtW.QFormLayout.LabelRole); lab = li.widget() if li else None
            if isinstance(lab, QtW.QLabel) and _clean(lab.text()):
                return _clean(lab.text())
        if isinstance(lay, QtW.QGridLayout):
            idx = lay.indexOf(a)
            if idx >= 0:
                r, c, _, _ = lay.getItemPosition(idx)
                for cc in range(c - 1, -1, -1):
                    it = lay.itemAtPosition(r, cc)
                    lw = it.widget() if it else None
                    if isinstance(lw, QtW.QLabel) and _clean(lw.text()):
                        return _clean(lw.text())
        elif lay is not None:
            prev = None
            for i in range(lay.count()):
                it = lay.itemAt(i).widget()
                if it is a:
                    if isinstance(prev, QtW.QLabel) and _clean(prev.text()) and len(_clean(prev.text())) < 40:
                        return _clean(prev.text())
                    break
                if it is not None:
                    prev = it
        if isinstance(p.layout(), QtW.QFormLayout) or (lay is not None and lay is not p.layout()):
            pass
        a = p
    return None


def text_for(w):
    name = w.objectName()
    if name and name in TEXTS:
        return TEXTS[name]
    t = getattr(w, "text", None)
    if callable(t):
        key = "text:" + _clean(t())
        if key in TEXTS:
            return TEXTS[key]
    lab = _row_label(w)
    if lab and "label:" + lab in TEXTS:
        return TEXTS["label:" + lab]
    if lab:
        return f"{lab}: change this setting; the preview follows."
    if callable(t) and _clean(t()):
        return f"{_clean(t())}."
    return None


def apply(root):
    """Give every interactive control under root a tooltip, status tip and "What's this?" text."""
    from PySide6 import QtWidgets as QtW
    for w in root.findChildren(QtW.QWidget):
        if not any(w.inherits(c) for c in INTERACTIVE) or w.inherits("QScrollBar"):
            continue
        tip = w.toolTip()
        if not tip:
            tip = text_for(w) or ""
            if tip:
                w.setToolTip(tip)
        if tip and not w.statusTip():
            w.setStatusTip(tip.split("\n")[0])
        if tip and not w.whatsThis():
            w.setWhatsThis(tip)
