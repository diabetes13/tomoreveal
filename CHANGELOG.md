# Change log

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
