# Change log

## 3.6.0 (2026-11-26)

What older projects notice: only one fix changes how an older project plays: a spin around X or Y right after a top view no longer swings the picture round first (reported as "the transition rotates the tomogram again" and "the full spin breaks after I rotate"). Older camera paths play as before unless you accept the one-time offer to make them smoother. Everything else new is a setting that older projects do not have, so their movies stay the same; the test movies (golden plan and frames) did not need to be made again.

- **Opening a file again is much faster.** Surfaces are kept on disk (in TomoReveal's own folder, up to 5 GB, never next to your data): a 1.5 GB Dragonfly session opened in 6 s the second time instead of 30 s.
- **Less memory:** a tomogram stays in its own data type (8- or 16-bit) instead of being turned into 32-bit numbers, and counting the structures no longer makes large temporary copies.
- **Data tab ▸ Size: full / half / quarter** ("Open smaller"): shows the tomogram and segmentation at half or a quarter size in each direction, about 8 or 64 times lighter and faster. Arrows, frames, slice numbers and close-ups move along; saved with the project. Large volumes suggest it.
- **Detail per structure:** Structures panel ▸ ⚙ ▸ **Detail: full / half**. Half has about 4 times fewer triangles; on a large segmentation it halved the time of a Soft frame.
- **Help ▸ Performance** (and `tomoreveal --diagnose`): whether your graphics card draws the pictures, the memory used, the graphics card's load, and where the time of one movie frame goes, with a hint when there are very many triangles.
- **Loading tells you what it is doing:** each file with its size and time, each surface ("building surfaces 3/15: Capsid"), and a note when a file is on a slow drive.
- **No silent switch to software drawing.** After a drawing error the graphics card is tried once more with a fresh window; only then are you asked whether to draw on the processor for this session. The error is written to the log.
- **Spins and rocks keep one size.** A turning overview no longer swings closer and further away (the distance changed by up to 62 % during a full spin): Movie tab ▸ overview ▸ **Keep the size steady during the turn** (on for new parts).
- **Turn around what is shown.** **Centre of the turn: middle of what is shown** (new parts) turns around the middle of the clip box even when the view is not fitted to it; before, a clipped tomogram could circle around an empty point. Right-click a structure ▸ **Rotate around this** makes it the centre. View ▸ **Show rotation axis** draws the axis in the preview (never in the movie).
- **Turn around the screen's up-down line:** a new choice for spins and rocks: the volume turns around itself however the view is tilted or rotated.
- **Gentle starts and longer flights.** A rock or spin starts slowly (**Start gently**, on for new parts), and the flight into an overview takes longer when the camera has to turn far (up to 2 s; it was always 0.5 s).
- **Type numbers.** The value beside every slider is a box: type 30 for Tilt and press Enter (Esc cancels, the arrow keys step, Shift ten at a time). The clip box has from and to boxes beside each bar, in voxels or nanometres ("120 nm").
- **Fixed: the camera path drawn in the preview** of a portrait or square movie was drawn for a 16:9 picture.
- **Fixed: a spin around X or Y after a top view swung the picture round first.** Seen from straight above with the view rotated (for example Rotate 90°), the flight into the spin turned the picture 90° and it snapped back when the spin began. Also changes old projects that have this combination (bug fix).
- **Fixed: captions and the legend changed size while the preview played.** Playback draws smaller pictures, and text there was drawn up to half as big again. Movies are unchanged.
- **No more flickering panels.** Ticking a part, locking a view, dragging on the timeline, undo or changing an arrow used to empty and refill the Structures list, the arrows list and the open part's settings. Now only the controls whose values changed are updated; the lists are made again only when parts or structures are added or removed. Ticking a part no longer opens it.
- **A steady legend** (new projects): the box keeps one size for the whole movie, and in a close-up the rows of the other structures fade to faint (or out, if the others are hidden) instead of vanishing and shrinking the box. Text keeps its size in the smaller frames of preview playback.
- **Moving the legend, caption, title, slice number or scale bar** moves a picture of it with the mouse, with the same snapping; the frame is drawn again once when you let go (before, every mouse move drew the whole 3D frame, so it lagged and jumped).
- **Hide structures in some parts only (a Structures panel on the right).** The list of structures is now a panel docked on the right of the preview (it was the Structures tab), with an **eye** per structure. **Eyes hide or show in: the selected part only** hides a structure in the part selected on the timeline; it fades out and back in over half a second at the part's edges, and an orange dot marks it. Switch to **the whole movie** to hide it everywhere (a dot there means it is hidden in some parts). Right-click a structure in the preview ▸ **Hide … in this part** does the same. Each click is one undo step.
- **Orientation arrows.** Look ▸ On the picture ▸ **Orientation (X Y Z)** (or the **Axes** button above the preview) shows three arrows, X red, Y green, Z blue, that turn with the camera, so you always see which way the tomogram lies. Drag it anywhere, change its size, and give the arrows your own labels (for example "tilt axis" or "beam"). It shows in the preview only, unless you tick **also in the movie**.
- **A caption box in any colour:** Captions ▸ **coloured box**, with a colour button next to it (a colour wheel). The text turns black or white, whichever reads.
- **Fixed: without the window** (command line), TomoReveal's own folder was your whole `~/.local/share`, so cached surfaces went to `~/.local/share/surfaces`. They now go to TomoReveal's folder, as in the window.
- **Draw arrows and shapes with the mouse.** A **Draw** toolbar above the preview: **Arrow** (press at the tail, drag, let go where it points; Shift for steps of 15°), **Curved** arrow (drag, then bend it with the yellow handle), **Freehand** arrow (draw it as with a pen; it is smoothed and gets an arrowhead at the end), **Line**, **Ellipse** and **Rectangle** (Shift: circle or square). Each drawing is one undo step. Right-click a line or curve for **Arrowheads** (end, start, both, none); every arrow, shape and text box has **Appear ▸ draw on** (it draws itself over a second).
- **Pinned or on the picture:** the **Follows the structure / Stays on the picture** button next to the tools decides whether the tip of a new drawing is pinned to the structure under it (it then follows that spot when the camera moves, keeping its shape on screen); with arrows or shapes selected, it switches them.
- **Moving an arrow's tip works everywhere.** The tip follows the mouse at once, a crosshair shows where it is, and a note says what it will be pinned to ("pin to: vesicles"); let go over empty space and the arrow stays there on the picture instead of not moving at all. The tip can be grabbed 12 pixels around it, before the camera or the label. Right-click ▸ **Move tip...** then click where it should point. Fixed: a slow drag was sometimes split into two undo steps.
- **Arrows and text as separate movies.** Make movie ▸ **Arrows and text: as separate transparent movies** writes the 3D movie without any overlay, plus `name_arrows.mov` (arrows, text and shapes) and `name_labels.mov` (legend, scale bar, captions, title, slice number): transparent ProRes 4444 of the same length, listed in `name_layers.json`. Laid over the 3D movie they give the usual movie, also during dissolves and fades. Right-click an item ▸ **Separate layer movie** puts it on the other layer. **File ▸ Merge layer movies** (or **Merge layers...** in the Movie tab) lays them back together into `name_merged.mp4` without drawing anything again, with the layers you tick. The scale bar drawn on the slice stays in the 3D movie.
- **Background blur is easy to find.** Every close-up has a row **Blur the background: as in the Look tab / off / light / medium / strong**, next to its camera movement; it no longer hides under the vignette, and choosing it does not switch the vignette on or off. While the preview plays, the blur is shown at the light level (the movie uses your level). The Look tab's group is now **Vignette and background blur**. Older projects draw as before.
- **Calm camera paths at their start and end.** The flight into a camera path took half a second whatever the camera had to turn (up to 10° per frame on the demo: the "haywire" start); it now takes as long as a gentle turn needs (under 2° per frame, at most 4 s), and so does the flight out (new paths: **Ends ▸ calm flights in and out**). **Hold at start / at end** (up to 3 s) keep the camera still at the first and last point; they were ignored for paths. Projects with camera paths from older versions offer once, when opened, to make them smoother (Undo brings the old path back).
- **Lay out a camera path from above.** In a path through points, **Edit from above** shows the tomogram from straight above (the view is locked meanwhile and comes back unchanged afterwards, or press Esc): click to add points, drag them, or **Draw the path** with the mouse (about one point every 30 pixels, which you can move afterwards). **Height** puts new points at one height, or on what you click; a strip under the preview shows every point's height, and you drag a point up or down there. Each point also has its own height (z) in the list. **Play path** flies it.
- **Flat membranes with an even thickness.** Structures panel ▸ ⚙ ▸ **Smoother surface: flat sheet, even thickness (a model)**: a membrane drawn as a smooth sheet of its measured thickness (shown in nanometres, or type your own), with holes and breaks up to **Fill gaps** (1, 3 or 6 voxels) bridged; **Flatness** less / medium / more. Two membranes segmented as separate sheets stay separate. On a test sheet with a bumpy middle it follows the true shape about 2.5 times more closely than "much" smoothing; on the "actual membrane" of a 708 × 1166 × 68 session it took 15 s (then it is kept on disk). It is a model, not the segmentation as it is.
- **Parts have their timeline colour in the Movie tab:** a stripe on each part's line (blue slides, green overviews, red close-ups, purple cards, orange camera paths, grey holds), and in menus that list parts.

## 3.5.0

- **Outlines on some structures only, part by part.** Movie tab ▸ a part ▸ Look and effects in this part ▸ **Outline these**: tick the structures to outline in that part (for example only the cone in a close-up, and everything in the overview). "Every structure" follows the outline boxes in the Structures tab. If outlines are off, picking structures turns them on (medium) in that part.
- **Dragonfly 2025 sessions open.** Sessions saved by Dragonfly 2025 were refused with "not a compressed Dragonfly session (no 'ORSZ' header)": they are laid out differently inside. They now open like older ones, with the names, colours and hidden structures from Dragonfly. A tomogram stored in the session as 16-bit numbers (as Dragonfly 2025 stores 8-bit data) is now used too; before, only 32-bit ones were.
- **A Presets tab.** Every look is shown as a small picture of your own data; click one to use it. **Save the current look as a preset** keeps your own looks (style, lighting, shadows, vignette, blur and background) for every project.
- **New looks:** **Soft on black** (soft shadows, no outlines, black background), **Ceramic** (solid, matte and pale like a porcelain model, with deep soft shadows and cream membranes), **Ceramic with see-through membranes** and **X-ray** (every structure see-through on black, with glowing edges in its own colour; surface "X-ray" and outline colour "glowing" can also be chosen on their own). The Look group now has the **Background** (for example Soft on a black background) and **Membranes** (their own colour, see-through, or solid cream) at the top; shadows, outlines, depth fade, the surface (normal or ceramic) and paler colours are under More settings. The legend shows the colours as they are drawn.
- **Outlines of see-through membranes:** all edges, **the outer edge only** (no lines across a membrane where it folds or where something lies behind it; the new default of the glass looks), or none.
- **Effects in some parts only.** Each part has **Effects in this part**: soft shadows, outlines, depth fade, membranes and shadows on the slice can differ from the Look tab (which sets them for the whole movie), for example no shadows while the segmentation is revealed. The change blends in smoothly. In the Structures tab, each structure can be drawn without soft shadows or without an outline.
- **A whole look per part.** **Look and effects in this part** now starts with **Look**: for example Ceramic in the overview and Ceramic with see-through membranes in a close-up. The background stays the movie's; single effects can still be changed on top.
- **Smoother membranes.** Look tab ▸ **smoother membrane surfaces**, or per structure **Smoother surface** (off / a little / more / much) in the Structures tab: takes away the bumps and ridges a segmentation leaves. Structures cut by the edge of the tomogram stay in place. **Thin parts are kept:** sheets and rods one voxel thick and small objects no longer disappear (before, "much" lost up to half of a thin membrane); only bumps on a surface are smoothed away. **In one part only:** Look and effects in this part ▸ **Smoother membranes**.
- **Changing smoothing no longer freezes the window.** New surfaces are made in the background and swapped in when ready (they took 2 to 4 s each on a large segmentation, during which the window did not answer); the soft shadows are not worked out again for them. The Structures tab follows the Look tab's smoother-membranes box, and the box follows "Treat as membrane".
- **Solid membranes in any colour:** Membranes ▸ **solid colour** (was solid cream) with a colour button next to it (a colour wheel); the same button sets the colour of see-through membranes.
- **Own and locked views.** Slides and holds can have a view of their own (as overviews already could), for example one slice seen from the top and zoomed into one area. **Lock the view of this part** keeps a part's camera as it is: moving around in the preview cannot change it by accident, and changes to the main view leave it alone (a close-up keeps the angle it is seen from). + Add part ▸ **One slice, zoomed in** adds the slice at the playhead seen from the top, ready to zoom, lock and annotate, then go back to the segmentation.
- **Turning around X or Y.** A rocking or spinning overview has **Turn around**: Z (as before), X or Y, so the volume tumbles and shows its top, side and bottom. The camera keeps one distance that fits the volume at every angle.
- **Flip the volume.** Data tab ▸ **Flip X / Y / Z** shows the tomogram and the segmentation flipped (your files are not changed), for example when it looks mirrored compared with another program. Arrows, drawn frames, camera path points, the clip box, slice numbers and the objects in close-ups move along. Saved with the project.
- **Nicer arrows.** Arrows are drawn with smooth edges, as one shape with a clean outline, a head swept back a little and round ends. **Keep it** (right-click an arrow): **on the spot in 3D** (it follows the structure while the camera moves, as before) or **on the picture in 2D** (it stays where it is on the screen). Drag an arrow by its body to move it; drag its tip to point it elsewhere.
- **Coloured text boxes and colours from a colour wheel.** A text box can have a **coloured box** in any colour (the text turns black or white, whichever reads); arrows and text can have any colour (Colour ▸ other colour, or the colour button in their row). A text box can be pinned to the spot next to it in 3D, or kept on the picture.
- **The Presets tab comes before Movie.**
- **Smoother preview playback:** when frames take longer to draw than the movie gives them, the preview shows every n-th frame evenly instead of jumping unevenly, and draws a smaller picture when it is very slow. Soft shadows are no longer worked out again each time a close-up hides or shows a structure (2.6 s → 0.3 s on a large segmentation).
- **If the window ever freezes** for 45 s, where it was stuck is written to the crash log (Help ▸ Show log folder), so a force quit leaves a trace.
- **One label, several pointers.** An arrow or a text box can point at more things: **+ point** (or right-click ▸ Point at more things), then click each of them. An arrow draws a line from its label to each; a text box draws thin leader lines from one side, as in figure callouts. Drag the end of a pointer to move it; right-click it to remove it.
- **Clearer arrows and text list:** shape (or box) and colour are in each row, with a delete button; timing and pointers on the line below.
- **Caption options:** a see-through box (as before), a light box, an outline or no box; colour; bold; and fading in and out.
- **Background blur works as intended:** the objects in focus stay sharp and everything else (the tomogram, other structures) is blurred, more the further it is from them. Before, the depth of field kept only a thin slab sharp, so nearly everything was blurred. A **Blur amount** slider is in the Look tab; the blur no longer needs the vignette.
- **No pulse when the tomogram fades after a split.** The far part of the slice suddenly turned darker the moment it started to fade (with the depth fade of the Soft looks). Splitting a part no longer makes the first half slow to a stop at the join. A part's tomogram setting has **whole part**, to fade it slowly over the whole part, for example while the segmentation is revealed.
- **Fixed: "Could not set shader program" errors** with shadows between structures on NVIDIA cards: the hidden drawing used for outlines now draws without the shadow pass.
- **See-through or hidden, structure by structure, in one close-up.** Under "Other structures", a close-up now has **Each structure**: set any structure to see-through (pale, like the glass membranes) or hidden for that close-up only, for example to look inside a membrane or clear what is in the way. It fades in with the close-up, works with a frame you drew, and hidden structures leave the legend. Other parts and older projects are unchanged.
- **Fewer settings on screen at once.** Each group in the Look and Movie tabs folds: click its title. The Look tab opens with the Look, the tomogram slice and Arrows and text; the rest are folded. A part's less used settings (caption, what is on the picture, zoom, tilt, highlight, effects, vignette, pauses, slowing down at the end) are under **More settings**, and its transition is folded under its heading. The program remembers what you open. Long explanations are now tooltips.
- **The mouse wheel no longer changes settings.** Turning the wheel over a drop-down list, number box or slider scrolls the panel instead.
- **Arrows and text boxes are easy to see on the timeline:** each has its own full-height bar under the parts, labelled with its words and with grips at both ends. A tooltip shows when it is shown.
- **New overviews are still** (no rocking). The default movie in the window ends with a still overview; Rock and Full spin remain in its Camera movement. The command line keeps the rocking overview.
- **Fixed: the field of view of a camera path's points in the movie.** When only the field of view changed, the movie showed the same picture for many frames and then jumped. Motion blur drew those frames with the normal field of view. The flight to the next part snapped back to the normal field of view at once; it now changes gradually.
- **Fixed: clicking in the preview while an arrow or text box was on the picture** wrote an error (KeyError 'ann:...') and did nothing.
- **Fixed: a new close-up could add its object to an earlier close-up.** If "+ Click objects in the preview" was still on for one close-up, later clicks (also those meant for a new close-up) went into that first close-up. Only one way of clicking in the preview is now active at a time, and selecting another part ends it. You can still add objects to a close-up later.
- **Close-ups can hide the other structures:** "Other structures" now offers grey, darker, unchanged colour and **hidden**.
- **Close-up of a frame you draw:** "Frame a region..." in a close-up's settings (or + Add part ▸ Close-up ▸ a region I draw in the preview) lets you drag a frame over the preview; the close-up then shows only what is inside it, for example where two objects touch, instead of whole objects.
- **New close-ups look from the top** (tilt 0°). Close-ups in older projects keep their automatic tilt.
- **Timeline zoom is easy to find:** a zoom bar (−, slider, +, Fit all, Zoom to part, Compact), an overview strip of the whole movie, Ctrl + mouse wheel or touchpad pinch to zoom, the wheel or a two-finger swipe to scroll, Ctrl + minus / plus / 0. The zoom is saved with the project.
- **Easier moving around in the preview:** left drag rotates, **right drag moves** the view (also middle drag or Shift + left drag), the wheel zooms towards the mouse, Ctrl + right drag zooms smoothly, double-click a structure to turn around that point. A **right click** opens a menu (centre here, look from above / the side / the front, fit everything or the clip box, zoom to / hide / show only / close-up of the structure under the mouse, use this view for the selected part, undo and redo view changes). Keys: arrows move, Shift + arrows rotate, + and − zoom, F fits, Home goes back, 1 / 3 / 7 front, side and top view. A **?** button in the preview lists these; View ▸ Moving around in the preview can swap the mouse buttons or the wheel direction.
- **Transitions have a start and an end.** Each part's settings now show "Transition from part 3 to part 4": its kind (camera flight, cut, cross-dissolve, fade through black or white), its length, how gently it **leaves** the previous part and **arrives** at this one (sudden, gentle, very gentle, with a small speed picture), an **overlap** with the end of the previous part or the start of this one, and a straight or arc flight. On the timeline, drag either end of a transition block to set the overlap. New slides, spins and orbits also **slow down at their end** (a setting per part). Older projects keep their transitions and motion exactly.
- **Smoother close-ups.** "Keep zoom steady" (on for new close-ups) keeps the camera at one distance, so the object no longer grows and shrinks while the camera orbits, rocks or wiggles. The new wiggle starts and ends at rest, fits whole cycles so it ends where it began, is calmer by default (5°, speed set from slow to fast), has a new "drift" shape, and also works from the top view without turning the picture. Close-ups in older projects move as before.
- **60 frames per second and motion blur** in Make movie, for extra smooth motion. When the preview cannot keep up while playing, it says so: the movie itself will be smooth.
- **A steady "follow" camera.** Following a membrane no longer shakes. The camera glides on a smoothed rail of its own while it keeps looking at a gently smoothed line along the membrane, stays at a constant height, keeps the tomogram upright (no rolling), turns at most 20° per second (it slows down at sharp bends instead), and starts and stops gently. **Steadiness** chooses close (follows bends), steady (the default) or very steady, with a slider for fine control. The camera can also follow from behind, looking along the path. **Edit as points** turns a follow path into a path through points you can adjust. Follow paths in older projects move as before.
- **A clear, easy camera path through points.**
  - **Drawing:** the path is drawn on top of the preview with thin lines (dashed where hidden, with arrows showing the direction) and numbered points (green S, red E). A small camera mark and a crosshair show where each point looks. None of this goes into the movie.
  - **Editing:** click to add a point after the selected one, drag a point to move the camera (Alt: up and down), drag its crosshair to change where it looks, and double-click a point to look from there.
  - **Each point:** clicking it opens a panel for its **zoom** and **field of view**, with "keep for the next points" or "only here" and "keep the size the same" (a dolly zoom along the path). Ctrl + wheel and Alt + wheel change them.
  - **The list of points:** a description, the zoom (grey with a chain when it is kept from an earlier point), the speed to the next point, and "stop gently".
  - **The whole path:** one zoom for all points, a zoom for the whole part, and Play path.
  - **Motion:** the camera now moves at an even speed and the view never flips.
- **Arrows, pointers and text boxes.**
  - **Arrows:** "+ Add arrow" (Look tab ▸ Arrows and text), or right-click ▸ Add arrow here, then click a spot on a structure. The arrow's tip stays on that spot while the camera moves, and its label is the structure's name, ready to type over. Drag its tail to turn it or make it longer, and drag its tip to another spot. Shapes: arrow, thin arrow, pointer, ring and bracket, in any colour.
  - **Text boxes:** add as many as you like, and drag them into place.
  - **Timing:** each item has its own start and end ("start here" and "end here" use the playhead) and fades in, grows or pops. Its bar on the timeline can be dragged.
  - **Visibility:** arrows hide while their structure is switched off, not yet revealed, outside the clip box, or out of the picture.
  - **Several at once:** Ctrl + click selects several, to align them or give them the same look. Labels never overlap.
  - **Movies:** they are part of every movie format, including the transparent MOV.
- **Looks like ChimeraX and Amira.** A **Look** choice at the top of the Look tab sets several things at once:
  - **Classic:** as before.
  - **Soft:** white background, soft shadows in creases, thin outlines, far things paler. New projects start with Soft.
  - **Glass membranes:** like Soft, with pale see-through membranes.
  - **Illustration:** thick black outlines.
  - **On the tomogram:** the structures over the grey slice.

  Each setting can then be changed on its own (the look then shows "Custom"):
  - **Soft shadows:** strength and reach. They are baked into the surfaces, so they never flicker.
  - **Outlines:** thin, medium or thick, in dark, black or a darker shade, also between touching structures.
  - **See-through membranes** and their opacity. "Treat as membrane" is ticked per structure in the Structures tab, guessed from names such as membrane, ER or vesicle.
  - **Depth fade**, richer colours on white, and matte lighting.

  **Background** now also offers light grey, a gradient and the tomogram slice. Older projects keep the Classic look.
- **Opacity makes a structure see-through.** Lowering a structure's opacity used to make it vanish or only lighten its outline; now it becomes see-through, its outline fades with it, and 0 % hides it completely.

## 3.4.1

- **Fixed a crash with "Shadows between structures"** on NVIDIA graphics cards. VTK's shadow shader failed for everything drawn without lighting (the tomogram slice, outlines, the scale bar, the Flat preset), which filled the terminal with shader errors and could end in a segmentation fault while turning the view. Those items are now drawn in the same colours in a way the shadow shader accepts, and the graphics card self-test checks this too.
- **Fixed: camera path settings could be changed only once.** A second change of the camera distance, side, height or where it looks did nothing and wrote an error to the log.

### Fixes from a code review by DcShepherd

These fixes come from a review of 3.3.0 that DcShepherd made on the `DS_Branch` branch; they are carried over to 3.4.

#### Crashes and lost work

- **A failed or cancelled movie no longer replaces the old poster.** The poster is written as `name_poster.partial.png` and replaces the old one only when the movie has finished (3.4 already did this for the movie itself). If the old movie is open in another program, the message says so and names the new `.partial` movie file.
- **Dragonfly can save a session while TomoReveal has it loaded.** The session file is no longer kept open. If the session was changed on disk, you get a clear message asking you to add it again.
- A damaged or cut-off session gives "the session is damaged" instead of a traceback.
- Reading a large tomogram stored in a session needs about half the memory it did before.
- An 8-bit label file with labels 128 to 255 (for example a 0/255 mask) no longer crashes the load.
- A file that fails to load is no longer kept in the list. Before, every later step failed with the same error until Start over.
- A movie part without a type gives an error that names the part, instead of a KeyError.
- A close-up with a one-voxel clip box or a one-voxel structure no longer crashes.

#### Wrong results

- Dragonfly labels above 32767, and more than 127 plain ROIs, keep their numbers. `extract` writes labels above 32767 as a float32 MRC, because MRC has no 32-bit integer type.
- The tomogram, `--labels` and `--features` that you give on the command line are always used as you gave them. A float label file no longer replaces your tomogram. Opening a project also uses each file as it was saved (labels stay labels).
- Files are recognised better: whole-number float files are labels, a multi-label `__labels` file is not an empty score map, an 8-bit tomogram with a narrow range stays a tomogram, and RGB TIFFs are refused.
- When Dragonfly flipped or binned its copy of your tomogram, score and label files made from your original file are now lined up the same way. A label file on Dragonfly's grid is used as it is, with a note that it can be mirrored.
- The lower face of the clip box also cuts the structures when the slice is hidden, faded, or above the box.
- A tomogram fade that is longer than the fly-in no longer jumps back.
- Explore shows the new mask after you change a score threshold.
- `extract` without `--roi` picks the 3D segmentation, not a one-slice preview.
- The same file typed in another letter case is loaded only once.
- `--frames-dir` writes a PNG for every frame, so pauses are kept in the sequence. A frames folder that still holds frames numbered past the end of the new movie is refused.
- A close-up whose structures are all switched off in the Structures tab is left out with the note "structure switched off in the Structures tab". Before, it showed an empty scene.
- The flight back from the last close-up belongs to the last part that drew frames, not to a later part that was left out.
- The voxel size (the "z = ... nm" readout) follows the current tomogram, or the segmentation when the tomogram has none.
- `inspect` names a property slot without a GUID in the same way that the app reads it.
- Two uploads with the same name in the browser version are both kept.

#### Security

- **The browser version answers only its own page.** Requests from other web pages, and requests with a wrong host name, are refused.
- In the browser version, a bad Content-Length or invalid JSON gets a 400 answer, and a backwards Range gets a 416. Before, a negative Content-Length still ran the request.

#### Windows

- After a preview error, the window no longer switches to software rendering, which crashed the program on Windows. It shows the error instead.
- `environment.yml` uses OpenBLAS, because some MKL versions crash NumPy on Windows.
- A second `tomoreveal app` moves to the next free port instead of sharing the busy one.
- Command output that goes to a file or a pipe no longer crashes on names with characters that the Windows code page does not have.
- JSON files (names next to label files, and `--classes`) are read as UTF-8, with or without a byte-order mark. Names with accents are no longer garbled.
- Running the installer again now also updates the libraries (on Windows and Linux). It also completes a first install that was stopped part way.
- The installer works for a user folder with an apostrophe, such as `O'Brien`. If the shortcuts cannot be made, it says that the installation failed, instead of "Done".

#### Command line

- `movie` and `preview` print label numbers, with the source name when several sources are loaded. `--only`, `--exclude`, `--focus` and `--classes` use these numbers. A number that matches nothing, or that is in two sources, gives an error that lists the structures. `--classes` also accepts names.
- Bad values for `--size`, `--contrast`, `--z-range` and `--clip` give a clear message instead of a traceback, and a backwards `--z-range` or `--clip` is refused.
- `--fps` must be at least 1, and `--fov` between 1 and 179. An odd `--size` prints a note that the size is rounded down.
- `preview -o out` writes `out.png` instead of crashing.
- The `--view` help says to write a negative tilt as `--view=-30:20`.
- `--headless` uses normal off-screen rendering where software rendering is not available, for example on Windows, instead of crashing.

#### Window

- Undo straight after a change no longer also takes back the change before it.
- During a load, the timeline, Play, picking and the Explore page are switched off, so they cannot crash the window.
- Closing the window during playback or during a load no longer gives an error.
- "+ Click a structure" no longer fails when the part it was to follow was undone or deleted. The close-up goes at the playhead.
- After you resize a part on the timeline (or use Undo), its open Duration and Object boxes show the new values.
- The notes in the parts list match the current movie after a change in the Data tab.

#### Tests

- The test suite runs on Windows. Real errors are no longer reported as "3D rendering is not available", the tests no longer write into your real settings, and the real-session tests fail when they find no sessions. A test of the score threshold no longer fails at random when the computer is slow.

## 3.4.0

- **Projects and crash safety (R3).** File > Save / Save as / Open / Open recent / Revert. A project (`.tomoreveal`) stores where the data files are and every setting, not the data. Moved data folders are found again; missing or changed files are listed in one dialog (Locate, Skip, Use it anyway). Your work is autosaved a few seconds after each change; after a crash TomoReveal offers to restore it. Movies are written in pieces, so a movie interrupted by a crash can be continued instead of started again. Errors are written to a log (Help > Show log folder).
- **Legend and slice number on or off (R1)**, also for one part only, and the title and captions too.
- **Move and resize the legend and the other items (R2)** by dragging them in the preview, with snapping to the edges and the centre, and a corner handle for their size. Legend title, one or two columns, box on or off.
- **Scale bar (R8)** from the voxel size of the data (or a value you type when the file header is wrong): flat on the picture, or lying on the slice, where it is exact in every view. Its length is a round number chosen for each part.
- **Title and end cards (R9)** with text, logos and pictures, a colour, gradient or picture background, fades, and a dissolve into the movie.
- **Opacity, threshold, speck removal and smoothing (R6)** for each structure, in the structure's settings (the button next to it), and an opacity for all structures together.
- **Lighting, shadows, smooth edges (R13).** Lighting presets (Simple, Soft, Full, Flat), the light's direction (drag a lit ball) and amounts; structures cast soft shadows on the tomogram slice, with a strength slider; anti-aliasing modes for the preview and the movie. Shadows between structures need a graphics card and are offered only where a self-test passes.
- **Surface styles (R16):** cartoon with outlines, dots, glossy (shiny, metallic, glass). Soft shadows in creases (ambient occlusion) are the Soft lighting preset.
- **Several objects in one close-up (R17):** tick structures or click objects in the preview; "Make a close-up for each" turns them into a quick tour.
- **The other structures during a close-up (R5):** their colour (grey, darker, unchanged) and their opacity; a vignette around the objects in focus, and an optional background blur.
- **Close-up animations (R7):** wiggle, slow push-in, pulsing highlight or glowing outline, a cross-section that sweeps through the objects (with the tomogram slice moving along), and a grow-in reveal.
- **Camera paths (R10):** the camera glides along a structure such as a membrane, or flies through points you set (with pauses). The path is shown in the preview only.
- **MP4 and MOV, transparent MOV, editing MOV (R11)** from one render. The transparent MOV (ProRes 4444) keeps the background see-through for slides and video editing.
- **Smaller files at full resolution (R4):** file size Balanced / Larger / Smaller, and H.265 as an option (about half the size; plays in fewer programs). H.264 stays the default.
- **File size before you make the movie (R12):** estimated from a few frames of your movie, for every quality and format.
- **Tooltips everywhere (R14):** every control explains itself; Help > What is this?, and Help > Show tips on hover to switch them off.
- **Short file names (R15)** in the Data tab: only the part that differs is shown, the shared part once above; right-click to rename a file inside TomoReveal.

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
