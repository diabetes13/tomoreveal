# TomoReveal

TomoReveal is a desktop application that makes movies from cryo-electron tomograms and their segmentations. A slice moves up through the tomogram. On the way back, the 3D segmentation appears above the slice. Then the camera can rock, spin, or fly to close-ups of the structures that you select.

![TomoReveal with the demo data loaded](docs/images/02-loaded.png)

TomoReveal reads these segmentations:

- Dragonfly sessions (`.ORSSession`). You do not have to export anything from Dragonfly.
- easymode and Ais output (one `name__feature.mrc` file for each structure).
- Label volumes from other programs (`.mrc` or `.tif` that contain whole numbers).

The application is read-only for your data. It does not edit, rename or move tomograms, sessions or segmentations. It writes only the movies, in the folder that you select. It runs on your computer and does not send data to the internet.

This is version 3.5.0. The [change log](CHANGELOG.md) lists the changes in each release.

## Supported platforms

| Operating system | Terminal to use | Status |
|---|---|---|
| Linux | Bash | Tested (installation, tests and the window, with software rendering) |
| Windows 10 or 11 | PowerShell, Miniforge Prompt or Anaconda Prompt | Not tested yet |
| macOS | Terminal with Zsh or Bash | Not tested yet |

Everything is installed into a separate Conda environment named `tomoreveal`. The Python of your system is left as it is.

## Before you start

You need:

- A 64-bit Linux, Windows or macOS computer with a graphical desktop. About 4 GB of free disk space.
- A Conda-compatible installer, for example [Miniforge](https://conda-forge.org/download/). See the [Conda installation instructions](https://docs.conda.io/projects/conda/en/stable/user-guide/install/).
- [Git](https://git-scm.com/downloads). This is not necessary if you download a ZIP archive.
- A tomogram and a segmentation of it. See [Supported files](#supported-files). To try the application first, use the built-in demo data.

Install Conda first if it is not on the computer, and then start a new terminal. This command shows whether Conda is ready:

~~~text
conda --version
~~~

If you do not want to install Conda, use the [installer scripts](#install-without-conda).

## Install and start the application

### Windows PowerShell

In PowerShell, run these commands. Put the address of the repository in the first line.

~~~powershell
git clone PASTE_REPOSITORY_URL_HERE
Set-Location TomoReveal
conda env create -f environment.yml
conda activate tomoreveal
python -m pip install --no-deps -e .
tomoreveal
~~~

### Linux or macOS

In a terminal, run these commands. Put the address of the repository in the first line.

~~~bash
git clone PASTE_REPOSITORY_URL_HERE
cd TomoReveal
conda env create -f environment.yml
conda activate tomoreveal
python -m pip install --no-deps -e .
tomoreveal
~~~

No Git? Download the ZIP archive of the repository and unpack it. Open a terminal in the unpacked folder (the one with `environment.yml` in it) and skip the `git clone` line and the line after it.

The `conda env create` command downloads Python, Qt and the 3D and video libraries. This takes some minutes. FFmpeg for the movie files is included; a separate installation is not necessary.

### Linux: add software rendering

On Linux, also install the Mesa software renderer. TomoReveal uses it when the graphics driver cannot be used, and on computers without a display:

~~~bash
conda install -n tomoreveal -c conda-forge mesalib
~~~

Do not install this package on Windows. There it replaces the graphics driver with slow software rendering for the whole environment.

If `tomoreveal` is not found, this does the same:

~~~text
python -m tomoreveal
~~~

To start the application on a later day, open a terminal and run:

~~~text
conda activate tomoreveal
tomoreveal
~~~

### Install without Conda

The `installers` folder contains scripts that download a small package manager (micromamba), install everything in one folder of your user account, and make a shortcut. Administrator rights are not necessary.

| Operating system | What to do | Result |
|---|---|---|
| Linux | Run `bash installers/install_tomoreveal.sh` in the TomoReveal folder | TomoReveal in the applications menu |
| macOS | Run `bash installers/install_tomoreveal.sh` in the TomoReveal folder | `TomoReveal.command` on the Desktop |
| Windows | Double-click `installers\install_tomoreveal.bat` | TomoReveal on the desktop and in the Start menu |

The Linux script is tested. The macOS and Windows scripts are not tested yet.

### Make sure that the installation is correct

Start the application and click **Try demo data**. Or run this command:

~~~text
tomoreveal demo
~~~

The application writes a small made-up tomogram in a temporary folder and opens it. If you see the tomogram slice with coloured membranes, vesicles, filaments and particles, the installation is correct.

## Make a movie

### 1. Load the files

Drop the files on the window, or use the buttons in the **Data** tab. You can drop all files at the same time, and add more later: new files are added to what is loaded. **Start over** unloads everything and begins a new project (it asks first about changes that are not saved). **Remove** unloads one file; when nothing is left to show, your movie is kept and comes back with the next data.

![Empty window](docs/images/01-start.png)

| You have | Drop these files |
|---|---|
| A Dragonfly session | The `.ORSSession` file. The tomogram file is optional, because the session contains a copy of the image. |
| easymode or Ais output | The tomogram and the `name__feature.mrc` files |
| A label volume | The tomogram and the label file |
| Only a tomogram | The tomogram. The movie is the slice sweep and the overview. |
| Only a segmentation | The segmentation. The movie shows the structures without a tomogram. |
| A Dragonfly session and easymode output | All of them. The structures of both are shown. |

TomoReveal recognises each dropped file by its content. To choose yourself, use **Add tomogram...** (the file is always used as the tomogram) or **Add segmentation...** (a session, a label volume or easymode files; never used as the tomogram). If a dropped file was recognised wrongly, right-click it in the Data tab and choose **Use as tomogram** or **Use as segmentation**.

Large tomograms and sessions can take a minute to load. A line in the Data tab shows the progress.

### 2. Choose the tomogram and the segmentations

![The Data tab with a session loaded](docs/images/02-data.png)

**TOMOGRAM** lists every image that you can show under the segmentation. Only one is used at a time:

- **None**: no tomogram. The structures are drawn on their own.
- **your file**: a tomogram file that you loaded.
- **from Dragonfly session ...**: each image that is saved in a session, also filtered copies that you made in Dragonfly. When you load only a session, its first image is selected for you.

Sometimes the image is changed during the import into Dragonfly: it is cropped, binned, or an axis is flipped. Then the tomogram file and the segmentation are not on the same grid. TomoReveal compares your file with Dragonfly's copy and finds the crop, the binning and the flips. It then puts your file on the grid of the segmentation. A line below the list tells you what it found, for example "cropped to x 60-767, y 150-1315, z 6-73". easymode files that were made from the original tomogram file are put on the Dragonfly grid in the same way. If the file is not the image that was imported, TomoReveal tells you and shows Dragonfly's copy.

**SEGMENTATION** lists every multi-ROI and ROI of each session, and each label or easymode file, with a tick box. Next to each one you see how many structures it has and, in grey, their names, so that you can see what you switch on. You can switch on several at the same time; their structures are listed in groups in the Structures tab. For each session, TomoReveal switches on the one that looks like the final segmentation. Change it with one click if it chose wrongly.

- A segmentation on another grid than the tomogram is greyed out, with the reason, for example "511 × 720 × 250 voxels, but the tomogram is 512 × 720 × 250".
- The single-slice previews that Dragonfly's Segmentation Wizard leaves in a session, and Dragonfly's undo copies, are not listed. A grey line says how many were left out.
- **Remove** unloads a file or a session. Your files on disk are not touched.

### 3. Set the structures

The **Structures** tab lists the structures with the names and colours that are saved in the Dragonfly session (or in the `.json` file next to a label volume). For each structure you can:

- remove the tick to hide it.
- click the coloured box to change the colour.
- change the name. The legend uses this name.
- click the **⚙** button to open the structure's settings:
  - **Opacity**: 100 % is solid; lower values let you see through it.
  - **Threshold** (easymode and Ais structures only): the surface is drawn where the score of the network is above it. It is saved with the movie, and Undo brings it back. A close-up of one object then shows the whole structure, because the objects change with the threshold.
  - **Remove specks smaller than** a number of voxels: leaves out small separate bits of a noisy segmentation.
  - **Smoothing**: how round the surface is; 0 shows the raw voxel steps.
  - **Style**: Normal, Cartoon (flat colour with dark outlines), Dots, or Glossy (shiny, metallic or glass). **Apply to all** gives every structure this style.

**All structures** in the Look tab fades every structure together, for a "ghost" look.

Structures that are hidden in Dragonfly, and the background, are listed in grey with the tick removed. Tick them to show them anyway. A structure that has no name in the session is shown as "label 3 (no name in the session)".

### Look through the slices

The **Explore** tab is for looking at the data slice by slice. It does not change the movie.

![The Explore tab](docs/images/06-explore.png)

- Move through the slices with the slider, **Previous** and **Next**, the mouse wheel, or the keys: Down or Right for the next slice, Up or Left for the previous one, Page Down and Page Up for ten slices, Home and End for the first and the last slice.
- **2D slice** shows the slice flat, with the segmentation filled, as outlines, or off. **Overlay opacity** sets how strongly the colours cover the slice. Ctrl + mouse wheel zooms, dragging moves the image, and a double-click shows the whole slice again.
- **3D at this slice** shows the 3D view with the slice at this position, and the structures above the slice, all structures, or none.
- The line below the image shows the slice number, the depth in nm, and under the mouse the position, the grey value and the name of the structure.
- Colours and which structures are shown come from the Structures tab. The clip box is drawn as a dashed rectangle, and slices outside the movie's slice range are dimmed.

Three buttons pass what you found to the movie: **Start the slice sweep here**, **End the slice sweep here**, and **Add a close-up of** the structure under the mouse.

### 4. Set the view

In the **Movie** tab, set the **main view**: click a view button, or use the **Tilt**, **Rotate** and **Zoom** bars. Or move around in the preview with the mouse:

| In the preview | Does |
|---|---|
| Left drag | Rotates |
| Right drag (or middle drag, or Shift + left drag) | Moves the view sideways and up and down; the spot under the mouse stays under it |
| Mouse wheel | Zooms towards the spot under the mouse |
| Ctrl + right drag up or down | Zooms smoothly |
| Double-click a structure | The view turns around that spot from now on |
| Right click (without moving) | A menu: centre here, look from above / the side / the front, fit everything, fit the clip box, zoom to / hide / show only / close-up of the structure under the mouse, add an arrow or text here, use this view for the selected part, undo or redo a view change |
| Arrow keys, Shift + arrow keys | Move, rotate (click the preview first) |
| + and −, F, Home | Zoom in and out, fit everything, back to the main view |
| 1, 3, 7 | Front, side, top view |

The **?** in the corner of the preview lists these. **View ▸ Moving around in the preview** can swap the left and right buttons or turn the mouse wheel the other way.

### 5. Clip the tomogram

Use **Clip** to keep only a part of the tomogram, as with the clip box in Dragonfly. Each bar has two handles. Drag them to set the range that you keep along X, Y and Z. The parts outside the range are not shown in the preview or in the movie. The slice moves only through the Z range that you keep.

![Clip bars keep only a part of the tomogram](docs/images/03-clip.png)

**Fit the view to the kept part** moves the camera closer. Remove the tick to keep the camera where it is. **Reset** shows the full tomogram again.

### 6. Build the movie on the timeline

The strip under the preview is the **timeline**. Each part of the movie is a coloured block, in the order in which it plays; its width is its duration. Hatched stretches are the automatic camera flights and pauses between parts. The orange line is the **playhead**: the preview shows the frame under it.

![The timeline with five parts](docs/images/09-timeline.png)

The default movie has three parts: **Slide up through the slices**, **Slide back, revealing the segmentation**, and **Overview**. You can change all of it:

- **Add a part** with **+ Add part** under the timeline, or with the small **+** on the playhead. The new part goes directly after the part under the playhead. Choose from: Slide up through the slices, Slide back revealing the segmentation, Overview, View from here, **Close-up of** a structure (or of "an object I click in the preview"), Hold (a pause), **Camera path along** a structure or **through points**, and **Title card**, **End card** or **Blank card**.
- **Remove a part** with **Delete** (or the Delete key). Any part can be removed, also the default ones. **Undo** brings it back. **Reset to the default movie** (Movie tab) starts again from the three default parts.
- **Make a part longer or shorter**: drag the right edge of its block.
- **Move a part**: drag its block to another place.
- **Split** cuts a slide or a hold in two at the playhead. Then **+ Add part** puts something between the halves, for example a hold to stop the slice in the middle, or a close-up.
- Untick a part in the Movie tab to leave it out without removing it.
- Right-click a block for the same actions.

![Adding a part at the playhead](docs/images/10-add-part.png)

| On the timeline | Does |
|---|---|
| Click or drag on the time scale | Moves the playhead; the preview follows |
| Click a block | Selects the part and opens its settings in the Movie tab |
| Double-click a block | Opens its settings and shows the Movie tab |
| Drag the right edge of a block | Changes its duration (0.1 s steps; with Shift 0.5 s) |
| Drag a block sideways | Moves it; Esc cancels |
| Right-click a block | Add part after, Duplicate, Split here, Move left, Move right, Switch off, Delete |
| Ctrl + mouse wheel, touchpad pinch, Ctrl + plus / minus | Zooms in time, around the mouse |
| Mouse wheel, Shift + wheel, two-finger swipe, middle drag | Scrolls |
| Ctrl + 0, or Shift + Z | Shows the whole movie (**Fit all**) |
| Drag either end of a transition | Starts it before the previous part ends, or lets it run into the next part (overlap) |
| Left / Right arrow | One frame back / forward (with Shift: one second) |
| Up / Down arrow | Previous / next part |
| Home / End | Start / end of the movie |
| Space | Play / stop |
| Delete | Removes the selected part |
| Ctrl+D | Duplicates it |
| S | Splits the part under the playhead |
| Ctrl+Z, Ctrl+Shift+Z (or Ctrl+Y) | Undo, redo |

The keys work while the timeline has the focus (click on it first), so they never change the movie while you type a caption.

**Zoom**: the bar at the right end of the timeline has **−**, a slider, **+**, **Fit all**, **Zoom to part** and **Compact** (lower blocks for long movies). When you are zoomed in, a thin strip above the timeline shows the whole movie with a light box for the part you see: drag the box to scroll, drag its edges to zoom, click elsewhere to jump there. Very short parts merge into a grey "several parts" mark when zoomed out; its tooltip lists them. The zoom is saved with the project.

![The timeline zoomed in, with the overview strip](docs/images/17-timeline-zoom.png)

**Play** plays the movie in the preview from the playhead, in real time. When drawing is slow (large tomograms, software rendering), it skips frames to keep time; the movie file always has every frame.

#### The parts in the Movie tab

The **Movie** tab lists the same parts, one line each. Click a line to open its settings; the other lines close, and the part is selected on the timeline. Every part has a **Duration**, a **Caption** (text at the bottom while it plays), a **Tomogram** choice (see the next step) and **Transition in** (the seconds of camera flight into it; **automatic**, or 0 to cut straight to it).

| Part | Settings |
|---|---|
| Slide | From (first slice, last slice, where the slice is, a slice number), To, Structures (hidden, above the slice, all, leave as they are), Pause before, Pause after |
| Overview | Camera movement (rock, full spin, still), Rock angle, View (main view or own view) |
| Close-up | Structure and Object (or Objects in focus), Other structures (colour and opacity), Camera movement (orbit, rock, wiggle, still), Camera push-in, Highlight, Object effect, Zoom, Tilt, Vignette |
| Hold | only the common settings |
| Card | Background, text and picture blocks, Fades (see [Title and end cards](#9-title-and-end-cards)) |
| Camera path | What the camera follows, or its points; Camera, Looks, Other structures (see [Camera paths](#camera-paths)) |

Every part also has **On the picture**: show or hide the legend, slice number and scale bar during this part only.

**Transitions.** Each part's settings end with "Transition from part 3 to part 4":

- **Kind**: camera flight, cut, cross-dissolve, fade through black or white, and its length (automatic, or seconds).
- **Leave / arrive**: how gently the camera leaves the previous part and arrives at this one (sudden, gentle, very gentle); the small picture shows the speed.
- **Overlap**: start the transition before the previous part ends, or let this part's own motion start before the transition ends (also by dragging the ends of the transition on the timeline).
- **Flight path**: straight, or an arc a little higher in the middle.

Slides, spins and orbits also have **Slow down at the end**.

![A transition and its settings](docs/images/18-transition.png)

![A close-up open in the Movie tab](docs/images/04-closeup.png)

#### Close-ups

- Click **Zoom** next to a structure in the Structures tab, or use **+ Add part > Close-up of**, to add a close-up of all of that structure.
- Click **+ Click a structure in the preview** (or choose "an object I click in the preview"), then click one object (one vesicle, one filament) to add a close-up of that object only.
- To change what a close-up shows, open it and choose another **Structure** or **Object** from the lists. The caption follows the structure unless you typed your own.
- A close-up of a structure that you switched off in the Data tab stays in the list, marked "structure not loaded", and is left out of the movie until the structure is back.
- **Several objects together**: open the close-up, tick whole structures under **Structures in focus**, or click **+ Click objects in the preview** and click objects one by one (click again to take one out, Shift-click for the whole structure, Esc when done). The camera frames all of them; each keeps its colour. **Make a close-up for each** (in the part's ⋮ menu) turns them into one close-up each, a quick tour.
- **A close-up of a frame you draw**: **Frame a region...** in a close-up's settings (or **+ Add part > Close-up of > a region I draw in the preview**), then drag a frame over the preview: the close-up shows only what is inside it, for example where two objects touch.
- **Other structures**: grey, darker, unchanged colour, or **hidden**, and their opacity.
- New close-ups look from the top (Tilt 0°). **Keep zoom steady** keeps the camera at one distance, so the object does not grow and shrink while the camera moves.
- **Vignette**: darker edges around the objects in focus, as set in the Look tab or for this close-up only, with an optional **blur** of what is nearer or further away.

#### Close-up animations

They can be combined:

- **Camera movement: wiggle**: the camera circles slightly around the object, so its 3D shape shows while it stays in place. It starts and ends at rest, and fits whole circles into the close-up so it ends where it began. Choose the size, the speed (slow to fast) and the shape: circle, figure eight, or drift (slow, never repeating).
- **Camera push-in**: the camera moves slowly closer (it never goes so close that the objects leave the picture).
- **Highlight**: the objects pulse brighter, or get a glowing outline.
- **Object effect: cross-section sweep**: a cut moves through the objects and shows their inside, for example the lumen of a vesicle. With **move the tomogram slice with the cut**, the slice follows the cut, so you see the density inside the object exactly where it is cut. The cut can also come from the viewer's side or run along the object's longest axis.
- **Object effect: grow in**: the objects build up from the bottom or from the slice, or fade in.

![A cross-section through a vesicle](docs/images/14-cross-section.png)

#### View from here

**+ Add part > View from here** adds a still view with its own camera position, taken from the main view. While that part is selected and the playhead is inside it, dragging and scrolling in the preview change the view of this part only (the preview says "Changing the view of part 4"); its line in the Movie tab also has Tilt, Rotate and Zoom bars. Two of these parts in a row make the camera fly from one position to the other.

#### Camera paths

**+ Add part > Camera path along** a structure makes the camera glide along it, for example along a membrane. TomoReveal finds the middle line of the structure (or of the object you click); **Reverse direction** starts from the other end. If the object is too small or round to follow, it says so: use points instead.

**+ Add part > Camera path through points** makes the camera fly through points that you set: **Add point at the current view** stores where the camera is in the preview; **Add point by clicking in the preview** picks a spot on a structure or on the slice, which the camera passes looking at it. Each point can **stop** the camera for some seconds. In the preview, drag a numbered point to move it, or right-click it to delete it.

For both: **Camera** sets the distance and the side the camera looks from, **Looks** whether it looks at the path or a little ahead and whether it moves at even speed or slows down at the ends. The path is drawn in the preview while the part is selected, never in the movie.

**Following a structure calmly**: **Steadiness** chooses close (follows the bends), steady (the default) or very steady, with a slider for fine control. The camera glides on a smoothed rail at a constant height, keeps the picture upright and turns at most 20° per second. **Edit as points** turns the path into a path through points you can adjust.

**The path editor** (a path through points, while its part is selected):

- The path is drawn as a thin line with arrows, the points as numbered circles (green **S**, red **E**), each with a small camera mark and a crosshair where it looks.
- Click on a structure or the slice to add a point after the selected one. Drag a point to move the camera (with Alt: up and down), and drag its crosshair to change where it looks. Double-click a point to look from there. Esc ends editing.
- Click a point to open its panel: **Zoom** and **Field of view** at that point, **Keep** this zoom for the next points or only here, and **Keep the size the same** when the field of view changes (a dolly zoom). Ctrl + wheel and Alt + wheel change them.
- The list of points in the Movie tab has a description, the zoom (grey with a chain when it is kept from an earlier point), the speed to the next point and **stop gently**. There is also one zoom for all points and **Play path**.

![The path editor with numbered points](docs/images/19-path-editor.png)

![A camera path through three points](docs/images/15-camera-path.png)

### 7. Show, fade or hide the tomogram

In the **Look** tab, **Tomogram slice** sets the default for the whole movie: **Show the tomogram slice**, and its **Opacity** from 0 to 100 %.

In the **Movie** tab, each part of the movie has its own **Tomogram** choice:

- **as default**: as set in the Look tab.
- **shown**: fully visible.
- **faded**: see-through, with the opacity that you set for this part.
- **hidden**: not shown, so only the structures are visible.

The change happens at the start of the part, over the **fade** time (0.5 s unless you change it). For example, set the overview to **hidden** to let the tomogram fade out after the reveal. If you hide the tomogram during a slide up, a grey line warns you that this part then shows nothing.

![The tomogram choice for each part](docs/images/07-tomogram.png)

### 8. On the picture: legend, slice number, scale bar

The Look tab group **On the picture**, and the buttons above the preview, switch the **legend**, the **slice number**, the **scale bar**, the **title** and the **captions** on or off. Each part can override this for itself (Movie tab, **On the picture**).

- **Move** an item by dragging it in the preview; drag its corner handle to make it bigger or smaller (hold Shift for steps of 0.1). It snaps to the edges and the centre lines. Right-click an item for Hide, Reset position, Bigger, Smaller, and, for the legend, one or two columns and the box. **Layout boxes** shows all items, also hidden ones, as dashed boxes.
- The positions are kept as fractions of the picture, so they stay in place at every quality (Draft, HD, 4K, square, portrait).
- **Scale bar**: its length comes from the voxel size of the data, shown in the Data tab. Many files say 1 Å or 0 by mistake; then the Data tab warns you, and you can type the real voxel size there. With **automatic length**, a round number (1, 2 or 5 × 10ⁿ nm) is chosen for each part. **Flat on the picture**, the bar is exact at the centre of the view (switch on Orthographic view for an exact bar everywhere); **lying on the slice**, it is exact in every view.

![Moving the legend; the scale bar](docs/images/11-overlays.png)

### Arrows and text

In the Look tab, **Arrows and text** lists them:

- **+ Add arrow**, then click the spot the arrow should point to (or right-click in the preview ▸ **Add arrow here**). The tip stays on that spot while the camera moves. The label is the structure's name; type over it.
- **+ Add text** adds a text box; drag it into place.
- In the preview: drag an arrow's tail to turn it or make it longer, and drag its tip to another spot. Right-click an arrow or a text box for its shape (arrow, thin arrow, pointer, ring, bracket), box, colour, Duplicate, Copy to another part, Delete, and **Make all arrows look like this one**. Ctrl + click selects several; with several text boxes, **Align** lines them up.
- Each item has its own **from** and **to** time in its part (**⏵** and **⏹** take the playhead). It fades in, grows or pops. Its bar on the timeline can be dragged.
- Arrows hide while their structure is switched off, not yet revealed by the slide, outside the clip box, or out of the picture. Labels never cover each other.

![Two arrows with labels](docs/images/20-arrows.png)

### 9. Title and end cards

**+ Add part > Title card** puts a card with the tomogram's name at the start; **End card (thank you)** puts one at the end; **Blank card** puts one at the playhead. Open the card in the Movie tab to set its **background** (a colour, a gradient, a picture, or see-through), its **blocks** of text (with line breaks, size, colour, bold) and pictures (logos with their transparency), and its **fades**: fade in from black, fade out, or dissolve into the next part. Drag the blocks in the preview to place them. Cards hide the legend, slice number and scale bar unless you switch them on for the card.

![A title card](docs/images/12-title-card.png)

### 10. Look, lighting, shadows and smooth edges

In the Look tab:

- **Look**: one choice sets several things at once:
  - **Classic**: as in earlier versions.
  - **Soft**: white background, soft shadows in creases, thin outlines, far things paler. New projects start with Soft.
  - **Glass membranes**: Soft with pale, see-through membranes.
  - **Illustration**: thick black outlines.
  - **On the tomogram**: the structures over the grey slice.

  You can then change **Soft shadows** (strength and reach; they are part of the surfaces, so they never flicker in movies), **Outlines** (thin, medium, thick; dark, black or a darker shade; also between touching structures), **See-through membranes** (which structures count as membranes: **Treat as membrane** in the Structures tab), **Depth fade**, **Richer colours on white backgrounds** and **Matte lighting**. The look then shows "Custom". **Background** offers black, white, light grey, dark grey, a gradient, or the tomogram slice.
- **Opacity** of a structure (Structures tab) makes it see-through, and its outline fades with it.

![The five looks](docs/images/21-looks.png)

- **Lighting**: the **Preset** Default is the lighting of earlier versions. Simple uses one light; Soft adds soft shadows in creases (ambient occlusion; it works on the whole picture); Full adds slice shadows; Flat shows colours only. Set the **direction** with the sliders or by dragging on the ball, and **Brightness**, **Fill light**, **Ambient** and **Shininess**. **Light moves with the camera** keeps the light where the viewer is; without it, the light stays in place and highlights move when the camera turns.
- **Shadows**: **Structures cast shadows on the tomogram slice**, with **Strength** and **Softness**, lit from the direction above. **Shadows between structures** needs a graphics card and is offered only where TomoReveal's test of the graphics card passes.
- **Smooth edges** for the preview (Off or Fast). The movie has its own choice in Make the movie (Best is the default).

![Cartoon membranes and vesicles with slice shadows](docs/images/13-cartoon-shadows.png)

### 11. Make the movie

At the end of the **Movie** tab, select the quality, the name and the folder.

- **Save as**: tick any of **MP4** (plays everywhere), **MOV** (the same video in a QuickTime file), **Transparent MOV** (see-through background, for putting the movie over a coloured slide or another video; the tomogram slice stays opaque), and **Editing MOV** (large, high quality, for video editing programs). One render makes all ticked files: `name.mp4`, `name.mov`, `name_transparent.mov`, `name_prores.mov`.
- **File size**: Balanced, Larger (best quality) or Smaller (about a quarter smaller at nearly the same quality). The resolution never changes.
- **Codec**: H.264 plays everywhere. **H.265** gives about half the file size at the same quality; it plays on recent Macs, iPhones and in VLC, but Windows may need the HEVC extension and some PowerPoint versions cannot play it. Check before a talk.
- Transparent backgrounds work in Keynote, Final Cut, Premiere and DaVinci Resolve. Check your presentation program before relying on it.
- **Frame rate** 60 frames/s gives smoother motion (files about 1.6 times larger, rendering twice as long). **Motion blur** blurs fast-moving detail a little, as a camera would (never across a cut); it renders 4 pictures per frame, so it is used for the movie only.
- The **estimated file sizes** for every quality and ticked format appear under these choices a few seconds after you stop changing settings. They come from a few frames of your movie; "rough" ones are scaled from HD.

Click **Make movie**. The movie is exactly what the timeline shows. If you have just changed smoothing, a threshold, a card's text or a camera path, it first waits until they are ready, then starts by itself. Parts that can't be drawn are listed first, with the reason, and you can stop there. A bar shows the progress and the time that remains. When the movie is complete, the real sizes appear next to the estimates; click **Play movie** to open it in your video player, or **Show folder**. If the old movie is still open in a video player, the new one is saved next to it as `name (new).mp4`.

![The size estimates and the finished movie](docs/images/16-size-table.png)

Save the movies in a folder that is not your data folder.

## Save your work

- **File > Save** (Ctrl+S) saves a project file (`.tomoreveal`). It records where your data files are, which tomogram and segmentations are switched on, and every setting of the movie. It does not copy the data. **File > Open project** (or dropping the project file on the window) brings everything back. The window title shows the project's name, with a * when there are unsaved changes; opening another project, starting over or quitting asks whether to save first. **File > Revert to saved** takes back every change since the last save.
- If you move the data and the project together, the project still finds the files. If a file is missing or has changed, one dialog lists them: **Locate...**, **Skip**, or **Use it anyway**. Skipped files are left out; their structures and close-ups come back when you locate the file later.
- **If TomoReveal crashes**: your tomograms, sessions and segmentation files are only ever read, so a crash cannot damage them. Your settings are autosaved a few seconds after each change and just before a movie starts; when you start TomoReveal again, it offers to **Restore** your work. Restored work counts as not saved until you save it.
- **If a movie is interrupted** (a crash, a power cut, an error), start TomoReveal if needed, restore or open your project, and press **Make movie** with the same name: it offers to **continue** the movie from where it stopped (in whole steps of 300 frames), as long as the settings, the data and the program are the same. A movie is renamed to its final name only when it is complete, so a half-written file never looks like a finished movie.
- **Help > Show log folder** opens the folder with error logs, useful when reporting a problem.

## Dark mode

In the **View** menu, or in the Look tab under **Window**, choose the theme: **System** (follow the desktop), **Light** or **Dark**. TomoReveal remembers the choice. The theme changes only the window; the background of the movie is set separately in the Look tab.

![Dark mode](docs/images/08-dark.png)

## Supported files

| Type | Files | Notes |
|---|---|---|
| Tomogram | `.mrc` `.rec` `.map` `.mrcs` `.st` `.tif` `.tiff` | |
| Dragonfly session | `.ORSSession` | Multi-ROIs, ROIs and the saved images |
| Label volume | `.mrc` `.tif` with whole numbers | One number for each structure. Put a `.json` file with the same name next to it to give names and colours: `{"1": {"name": "membrane", "color": "#d7566e"}}` |
| easymode or Ais output | `name__feature.mrc` | One file for each structure. Scores as int8 (0 to 127), uint16 (0 to 255) or float32 (0 to 1). |

The tomogram and the segmentation must show the same volume, and so must several segmentations that are switched on together. A segmentation at a different binning of the same volume is resampled. For Dragonfly sessions, see [Choose the tomogram and the segmentations](#2-choose-the-tomogram-and-the-segmentations).

These files are not supported: Dragonfly `.ORSObject` files, Amira `.am`, IMOD `.mod`, and particle coordinate files such as `.star`.

## Update an installation

In a terminal in the TomoReveal folder, run:

~~~text
git pull
conda env update -n tomoreveal -f environment.yml --prune
conda activate tomoreveal
python -m pip install --no-deps -e .
~~~

Without Git, download and unpack the new ZIP archive, and run the last three commands in that folder.

If you used an installer script, run the installer script of the new version again.

On Linux, `--prune` removes the software renderer. Run the command in [Linux: add software rendering](#linux-add-software-rendering) again after an update.

## Troubleshooting

### conda is not recognised

A terminal that was open before Conda was installed does not know about it: start a new one. On Windows, the Miniforge Prompt or Anaconda Prompt always works. On Linux or macOS, this command sets up your shell; start a new terminal after it:

~~~text
conda init
~~~

### The environment already exists

You installed it before. Bring it up to date:

~~~text
conda env update -n tomoreveal -f environment.yml --prune
conda activate tomoreveal
python -m pip install --no-deps -e .
~~~

### tomoreveal is not recognised

The environment is probably not active. From the TomoReveal folder:

~~~text
conda activate tomoreveal
python -m pip install --no-deps -e .
python -m tomoreveal
~~~

### The preview is black, or the application reports a graphics problem

On Linux, the application then changes to software rendering, which is slower but does not use the graphics driver. This needs the package from [Linux: add software rendering](#linux-add-software-rendering). To start with software rendering, run:

~~~text
tomoreveal --headless
~~~

On Windows and macOS, update the graphics driver or the operating system.

### The structure names are not the ones in Dragonfly

Check which segmentation is ticked in the Data tab: a session can hold several, for example training data and the final result. To see exactly what TomoReveal reads from a session, run:

~~~text
tomoreveal inspect session.ORSSession
~~~

It prints each image and segmentation in the session, the stored names, colours and visible flags, and which stored property each was read from. Compare it with Dragonfly's Data Properties panel. Give a folder instead of a file to inspect every session in it, and add `--json` for output that other programs can read.

### A segmentation is greyed out, or "grid mismatch"

The tomogram and the segmentation do not show the same volume. Make sure that you selected the tomogram that the segmentation was made from. For a Dragonfly session, load the session without a tomogram file to use Dragonfly's copy of the image.

### A Dragonfly session cannot be read

The `.ORSSession` format is not documented. The reader was made from sessions that Dragonfly 2022.2 saved. For each multi-ROI, the reader compares its result with the voxel count that Dragonfly saved, and stops if they are different. If your session cannot be read, export the multi-ROI from Dragonfly as a TIFF stack and load it as a label volume.

### Linux shows a Qt platform-plugin error

The window needs a desktop. It cannot open over an SSH connection that has no display. To make movies on such a computer, see [Use without the window](#use-without-the-window).

## Use without the window

The same functions are available as commands, for example on a computer without a display:

~~~text
tomoreveal list    session.ORSSession
tomoreveal inspect session.ORSSession
tomoreveal movie   tomogram.mrc --session session.ORSSession --view oblique,top --focus "vesicles" -o movie.mp4
tomoreveal movie   tomogram.mrc --features tomogram__membrane.mrc --threshold 0.5 --clip 0,400,0,600,10,50
tomoreveal movie   tomogram.mrc --session session.ORSSession --format mp4,mov-transparent --file-size smaller --scale-bar
tomoreveal extract session.ORSSession --masks -o segmentation_out
tomoreveal --help
~~~

On a Linux computer without a display, add `--headless` to the `movie` command. `--format` takes `mp4`, `mov`, `mov-transparent` and `mov-prores`; `--codec h265` and `--file-size larger|balanced|smaller` set the file size (`--crf` overrides it); `--scale-bar` adds a scale bar (`auto` or a length in nm) and `--voxel-nm` sets the voxel size when the file's header is wrong. `--frames-dir` also saves every frame as a PNG picture. Parts that can't be drawn are left out with a note that says why.

`tomoreveal extract` writes the selected Dragonfly segmentation as `labels.mrc` with the names and colours in `classes.json`.

`tomoreveal inspect` prints what TomoReveal reads from a session; see [The structure names are not the ones in Dragonfly](#the-structure-names-are-not-the-ones-in-dragonfly).

`tomoreveal app` shows the controls in a web browser tab on this computer in place of the window. This older interface does not have the clip bars, the Explore tab, the tomogram controls, dark mode, or the features added in 3.4 (projects, overlays, cards, lighting, close-up animations, camera paths, file formats).

## Scope and data safety

- Use this application to make movies of tomograms with segmentations. It does not make or edit segmentations.
- The application does not write to your tomograms, sessions or segmentation files.
- The application does not connect to the internet. The installation downloads the libraries one time.
- The screenshots in this document show made-up demo data.
- The surfaces in the movie are smoothed for display. Do not use the movie for measurements.

## Developer use

The source code is in the `tomoreveal` folder:

| File | Contents |
|---|---|
| `dragonfly.py` | Reader for `.ORSSession` files |
| `volumes.py` | Reading volumes, recognising file types, surfaces |
| `matching.py` | Lining up a tomogram file with Dragonfly's copy |
| `storyboard.py` | Camera, clip box, and the frame-by-frame plan |
| `parts.py` | The parts of the movie: making them, close-up targets, older settings, names |
| `render.py` | Drawing one frame and writing the movie |
| `studio.py` | Program state that the window and the commands share |
| `gui.py` | The desktop window |
| `gui_widgets.py` | The preview widget, the worker thread and other small widgets of the window |
| `gui_data.py` | The Data tab |
| `gui_explore.py`, `explore.py` | The Explore tab and its slice drawing |
| `gui_theme.py` | Light and dark themes |
| `gui_timeline.py` | The timeline under the preview |
| `gui_movie.py` | Editing the movie (add, delete, split, undo) and the parts list of the Movie tab |
| `project.py`, `gui_project.py` | Project files, finding moved data, autosave, crash log; the File menu |
| `encode.py`, `estimate.py`, `gui_export.py` | Writing movies through FFmpeg in chunks (several formats at once), file size estimates |
| `overlays.py`, `gui_overlays.py` | Legend, slice number, scale bar, title and caption; dragging them in the preview |
| `cards.py`, `gui_cards.py` | Title and end cards |
| `look.py`, `gui_look.py` | Lighting, shadows, anti-aliasing, vignette, surface styles |
| `paths.py`, `gui_paths.py` | Camera paths along structures and through points |
| `names.py`, `help_texts.py` | Short file names; every tooltip |
| `cli.py`, `webapp.py`, `demo.py` | Commands, browser interface, demo data |

To run the tests:

~~~text
conda activate tomoreveal
python -m pytest
~~~

The tests use made-up data, including small made-up Dragonfly sessions. To also test real sessions, set `TOMOREVEAL_TEST_DATA` to a folder that contains them (outside the repository); those tests are skipped otherwise. The tests that draw are skipped on a computer where no renderer can start.

## License

TomoReveal is released under the [MIT License](LICENSE).
