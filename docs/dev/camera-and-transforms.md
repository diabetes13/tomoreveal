# Camera and transforms (audit for 3.6, phase 0.3)

State of `release-3.6` at `ff5d253` (3.5.0 plus "Outline these"). Every place that computes or converts a camera pose, projects
a 3D point to the picture, or transforms the data.

## The pose

One dict per frame, `frame["pose"]`:

| Key | Meaning | Set by |
|---|---|---|
| `c` | rotation centre / focal point (x, y, z voxels) | every framing below |
| `tilt`, `az` | turntable angles (°) around `Framer.u` and z | framings, `paths.tilt_az` for free cameras |
| `dist` | camera distance (perspective) | `Framer.fit_dir` |
| `scale` | half height of the picture in voxels (orthographic) | `Framer.fit_dir`: in-plane size only, **not** `dist·tan(fov/2)` |
| `pos`, `up` | free camera (paths, 3.5 wiggle, X/Y spins) | `paths._pose`, `animations.turned_pose`, `storyboard.axis_turn_poses` |
| `fov` | own field of view (path points) | `paths.points_poses` |
| `wdir` | wiggle direction | `animations.turned_pose` |

## Computing poses

| Where | What | Repeats? |
|---|---|---|
| `storyboard.Framer.__init__` | base axes: up = −x (landscape, tall tomogram) or +y; from the **picture** shape | deliberate (Q4: keep) |
| `Framer.axes(tilt, az)` | direction centre → camera, and up | the one conversion of tilt/az |
| `Framer.fit` / `fit_dir` | centre = middle of the box, `dist` and `scale` that just frame it | — |
| `storyboard.build_plan` → `ov(d, view)` (≈ line 590) | overview / slide / hold pose: `fit` + zoom + `view["pan"]` | **refits every frame of a Z spin or rock** → distance pumps (R14) |
| `storyboard.main_box` | framing box: clip box when "Fit the view" is on, else the whole volume | centre far from the kept part when Fit is off (R2) |
| `storyboard.view_framing` | zoom and pan that frame a box, relative to the main view | used by Navigator `fit_box` |
| `storyboard.axis_turn_poses` | X/Y spins: one distance for every angle | already steady |
| `animations.steady_framing` | close-ups: one distance over the angles reached | already steady |
| `animations.closeup_animation` (≈ line 109) | push-in / keeps objects in frame (`fit` again) | — |
| `animations.turned_pose` | 3.5 wiggle: turns the view direction, gives `pos`/`up` | — |
| `paths._pose` | path camera → pose (`tilt_az`) | sets `scale = dist·tan(fov/2)`: differs from `fit_dir`'s rule; only matters for **orthographic path parts** |
| `paths.path_poses`, `steady_follow_poses`, `points_poses` | camera paths | use the movie's Framer |
| `transitions.lerp_pose` | between two poses; az the short way unless `d_az` | the one blend |
| `gui_navigate.Navigator` | mouse drag / pan / wheel / centre / fit box: edits `spec["view"]` (tilt, az, zoom, pan) or `part["view"]` | changes the **view**, never a pose; `_lerp_view` for gliding |
| `gui_paths` (line 47) | camera of the frame shown, for adding path points | movie's shape: OK |

## Writing the VTK camera

Only `render.py` ≈ 325–334 (`draw`): it builds a `Framer` with the **drawn** picture's shape and sets focal point,
position, up, parallel scale or view angle. The shadow self-test (`shadows.py:42`) sets its own fixed camera.

## Projecting 3D to the picture

| Where | Used for | Agrees with the renderer? |
|---|---|---|
| `annotations.project` | arrows, text pointers | yes (numpy copy of the same camera) |
| `overlays.px_per_voxel` + `render.py` ≈ 794 | scale bar length | yes |
| `picking.PreviewPicking.project` | mouse queries on the last frame | uses VTK's own camera: yes |
| `render.py` 405 and 413 (`Framer(self.shape, 16/9, 30.0)`) | path line drawn in the preview | **no** for portrait or square movies, and ignores the movie's field of view and orthographic setting |

## Data transforms

| Transform | Where | Note |
|---|---|---|
| flips, binning, crop of an original file to match a session | `matching.apply_match` at load | once, on arrays |
| flip the volume (3.5) | `Studio._flipped` / `set_flip` (views of arrays), `storyboard.flip_spec` (moves points in the spec), window `_refind_objects` | spec and data flipped together |
| clip box | `storyboard.clip_box` → framing (`main_box`, `build_plan`), clipping planes (`render.py` ≈ 251), explore (`explore.py:70`, `gui_explore.py:236`), annotations (`annotations.py:215`), Navigator `fit_box` | one function, read in several places: consistent |
| slice range | `spec["z_range"]` and the clip box's z in `build_plan` | — |
| voxel size | `ds.voxel` → scale bar, nm read-outs | — |

## Findings

1. **Redundancies are small.** There is one pose format, one blend (`lerp_pose`), one conversion of tilt/az
   (`Framer.axes`) and one writer of the VTK camera. A new camera model is not needed (as the revised plan says).
2. **Bug:** the preview's path line uses a fixed 16/9 picture and 30° field of view (`render.py` 405, 413). Fix in 1.6.
3. **Inconsistency:** `paths._pose` derives `scale` from `dist·tan(fov/2)` while every other pose uses `fit_dir`'s
   in-plane rule (about 21 % different). Only orthographic camera paths are affected. Note for 1.6; do not change old
   movies without asking.
4. **Distance pumping (R14):** Z spins and rocks refit every frame in `ov()`. X/Y spins and close-ups already keep one
   distance. Fix in 1.1 with `steady_framing`.
5. **Rotation centre (R2):** with "Fit the view" off, the centre is the whole volume's middle. Fix in 1.2 (`centre`).
6. **Spin after a drag (R11):** a drag only changes tilt/az/pan of the view; Z spins add `d` to az and come back to the
   start. X/Y spins turn around the *volume's* axes, which after a drag are no longer horizontal or vertical on screen:
   the likely cause. Fix in 1.3 (`spin_axis: "screen"`), after reproducing it on the user's project.
