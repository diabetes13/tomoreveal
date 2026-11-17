# Change log

## 3.3.0

- **Timeline (T1).** The slider under the preview is now a timeline, as in a video editor. Each part of the movie is a block with its duration; transitions and pauses are shown between them. Add parts where the playhead is (slide up, reveal, overview, close-up, hold) with **+ Add part** or the **+** on the playhead; change durations by dragging the edge of a block; reorder by dragging blocks; split a slide or a hold at the playhead. Parts can come in any order: the camera and the slice fly automatically from one part to the next.
- **Remove parts (T2).** Any part can be deleted, also the default ones, with the Delete button or key or the right-click menu. **Undo** and **Redo** cover every change to the movie settings. **Reset to the default movie** brings back the three default parts.
- **Parts list with drop-down menus (T3).** The Movie tab lists every part as one line that opens to show its settings and closes again. A close-up's structure and object are chosen from drop-down lists (structures with their colours); new parts are added from a drop-down menu. A close-up of a structure that is switched off is kept and skipped, not deleted; changing an easymode threshold turns close-ups of single objects into close-ups of the whole structure instead of deleting them.
- **View from here.** Overview parts can have their own camera position; drag in the preview to set it. Two of them in a row make the camera fly from one view to the other.
- **Play** the movie in the preview, in real time (frames are skipped when drawing is slow).

## 3.2.0

- **Structure names from Dragonfly (F1).** The Structures tab now always shows the names and colours of the segmentation that is switched on.
  - Cause: in 3.1.0 the list of structures could not be rebuilt after the first load. Choosing another segmentation, or dropping another session, kept the names of the first one on screen while the movie already used the new ones.
  - Names, colours and Dragonfly's "visible" flag are now read by the type of each stored property, not by guessing. This also fixes wrong, almost black colours in some training multi-ROIs.
  - Structures that are hidden in Dragonfly, and background structures, are listed in grey with the tick box off. You can switch them on.
  - A label without a name is shown as "label 3 (no name in the session)"; names are never made up.
  - New command `tomoreveal inspect session.ORSSession` prints everything TomoReveal reads from a session.
- **Tomogram opacity (F2).** In the Look tab: show or hide the tomogram slice, and set its opacity. In the Movie tab, each shot can use the default, show, fade or hide the tomogram, with a fade time. Movies without any tomogram are possible.
- **Data tab (F3).** Load a tomogram only, a segmentation only, or both. Several sessions and files can be loaded at the same time.
  - The tomogram is one choice: none, your file, or an image saved in a Dragonfly session. A session alone uses its own image.
  - Every segmentation of every session is listed with a tick box, its number of structures and their names. Segmentations on another grid are greyed out with the reason. Segmentation Wizard previews are hidden.
  - "Add tomogram..." and "Add segmentation..." always treat a file as what you chose; right-click a file to change it. Each file has a Remove button.
  - A structure keeps its name, colour and tick when you add or remove other files.
- **Explore tab (F4).** Go through the slices by hand, with the segmentation on top (filled, outlines or off) or in 3D at that slice. The line below the image shows the slice, the depth in nm, and the grey value and structure under the mouse. Buttons set where the slice sweep of the movie starts and ends, and add a close-up of the structure under the mouse.
- **Dark mode (F5).** View menu or Look tab: System, Light or Dark. The choice is remembered.
- The left side of the window is now in tabs: Data, Structures, Explore, Movie and Look.

## 3.1.0

- The original tomogram file can be used together with a Dragonfly session even when Dragonfly changed the image at import. The program compares the file with the copy stored in the session and lines it up (flipped axes, binning, cropping). The Image list offers both "your file" and "Dragonfly's copy".
- easymode / Ais files made from the original tomogram are lined up in the same way.
- Clip box: keep only a chosen X, Y and Z range of the tomogram in the preview and the movie.
- "Try demo data" button and `tomoreveal demo` command, to check an installation without your own files.
- Label volumes can carry names and colours in a `.json` file with the same name.
- The program is now an installable project (`tomoreveal` command) instead of one script file.

## 3.0

- Desktop window (no browser needed): drop files, drag to rotate, scroll to zoom, click a structure for a close-up, make the movie.
- Installers for Linux, macOS and Windows.

## 2.1

- easymode / Ais score volumes (`<tomogram>__<feature>.mrc`), one structure per file with its own threshold.

## 2.0

- Drag-and-drop app in the web browser with live preview, close-ups of structures and of single objects.

## 1.0

- Command-line script: slice sweep, segmentation reveal, view presets. Reads Dragonfly `.ORSSession` files directly.
