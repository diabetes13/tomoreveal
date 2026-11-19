# Change log

## Unreleased: fixes

These fixes come from a review of 3.3.0.

### Crashes and lost work

- **A failed or cancelled movie no longer destroys the old one.** The movie and its poster are written as `.part` files first. They replace the old files only when the render has finished. If the old movie is open in another program, the old files stay and the message names the new `.part` files.
- **Dragonfly can save a session while TomoReveal has it loaded.** The session file is no longer kept open. If the session was changed on disk, you get a clear message asking you to add it again.
- A damaged or cut-off session gives "the session is damaged" instead of a traceback.
- Reading a large tomogram stored in a session needs about half the memory it did before.
- An 8-bit label file with labels 128 to 255 (for example a 0/255 mask) no longer crashes the load.
- A file that fails to load is no longer kept in the list. Before, every later step failed with the same error until Start over.
- A movie part without a type gives an error that names the part, instead of a KeyError.
- A close-up with a one-voxel clip box or a one-voxel structure no longer crashes.

### Wrong results

- Dragonfly labels above 32767, and more than 127 plain ROIs, keep their numbers. `extract` writes labels above 32767 as a float32 MRC, because MRC has no 32-bit integer type.
- The tomogram, `--labels` and `--features` that you give on the command line are always used as you gave them. A float label file no longer replaces your tomogram.
- Files are recognised better: whole-number float files are labels, a multi-label `__labels` file is not an empty score map, an 8-bit tomogram with a narrow range stays a tomogram, and RGB TIFFs are refused.
- When Dragonfly flipped or binned its copy of your tomogram, score and label files made from your original file are now lined up the same way. A label file on Dragonfly's grid is used as it is, with a note that it can be mirrored.
- The lower face of the clip box also cuts the structures when the slice is hidden, faded, or above the box.
- A tomogram fade that is longer than the fly-in no longer jumps back.
- Unticking and ticking a segmentation again keeps its names and colours.
- Explore shows the new mask after you change a score threshold.
- `extract` without `--roi` picks the 3D segmentation, not a one-slice preview.
- The same file typed in another letter case is loaded only once.
- `--frames-dir` writes a PNG for every frame, so pauses are kept in the sequence. A frames folder that still holds frames numbered past the end of the new movie is refused.
- A close-up of a structure that is switched off in the Structures tab is left out with the note "structure switched off in the Structures tab". Before, it showed an empty scene.
- The flight back from the last close-up belongs to the last part that drew frames, not to a later part that was left out.
- The voxel size (the "z = ... nm" readout) follows the current tomogram, or the segmentation when the tomogram has none.
- `inspect` names a property slot without a GUID in the same way that the app reads it.
- Two uploads with the same name in the browser version are both kept.

### Security

- **The browser version answers only its own page.** Requests from other web pages, and requests with a wrong host name, are refused.
- In the browser version, a bad Content-Length or invalid JSON gets a 400 answer, and a backwards Range gets a 416. Before, a negative Content-Length still ran the request.

### Windows

- After a preview error, the window no longer switches to software rendering, which crashed the program on Windows. It shows the error instead.
- `environment.yml` uses OpenBLAS, because some MKL versions crash NumPy on Windows.
- A second `tomoreveal app` moves to the next free port instead of sharing the busy one.
- Command output that goes to a file or a pipe no longer crashes on names with characters that the Windows code page does not have.
- JSON files (names next to label files, and `--classes`) are read as UTF-8, with or without a byte-order mark. Names with accents are no longer garbled.
- Running the installer again now also updates the libraries (on Windows and Linux). It also completes a first install that was stopped part way.
- The installer works for a user folder with an apostrophe, such as `O'Brien`. If the shortcuts cannot be made, it says that the installation failed, instead of "Done".

### Command line

- `movie` and `preview` print label numbers, with the source name when several sources are loaded. `--only`, `--exclude`, `--focus` and `--classes` use these numbers. A number that matches nothing, or that is in two sources, gives an error that lists the structures. `--classes` also accepts names.
- Bad values for `--size`, `--contrast`, `--z-range` and `--clip` give a clear message instead of a traceback, and a backwards `--z-range` or `--clip` is refused.
- `--fps` must be at least 1, and `--fov` between 1 and 179. An odd `--size` prints a note that the size is rounded down.
- `movie -o out` writes `out.mp4`, and `preview -o out` writes `out.png`. Before, the movie rendered every frame and then crashed.
- The `--view` help says to write a negative tilt as `--view=-30:20`.
- `--headless` uses normal off-screen rendering where software rendering is not available, for example on Windows, instead of crashing.

### Window

- Undo straight after a change no longer also takes back the change before it.
- During a load, the timeline, Play, picking and the Explore page are switched off, so they cannot crash the window.
- Closing the window during playback or during a load no longer gives an error.
- "+ Click a structure" no longer fails when the part it was to follow was undone or deleted. The close-up goes at the playhead.
- After you resize a part on the timeline (or use Undo), its open Duration and Object boxes show the new values.
- The notes in the parts list match the current movie after a change in the Data tab.

### Tests

- The test suite runs on Windows. Real errors are no longer reported as "3D rendering is not available", the tests no longer write into your real settings, and the real-session tests fail when they find no sessions. A test of the score threshold no longer fails at random when the computer is slow.

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
