# Performance (3.6 baselines, phase 0.4)

User's machine: NVIDIA GeForce RTX 4070 SUPER (VTK `vtkXOpenGLRenderWindow`, "NVIDIA GeForce RTX 4070 SUPER/PCIe/SSE2"),
24 cores, 60 GB RAM. Measured on 2026-11-25 at `ff5d253`, files on the internal NVMe disk (in the page cache).

## Synthetic 1.5 GB volume (300 × 1250 × 1000 float32 + int16 labels, 3 structures, 2.2 M triangles)

| Step | Time |
|---|---|
| Load tomogram + labels + surfaces | 4.7 s (peak RSS 5.2 GB) |
| HD frame (1920 × 1080), Classic | 0.11 s |
| HD frame, Soft | 0.25 s |
| HD frame, Ceramic with see-through membranes | 0.13 s |
| Soft frame split | VTK render 0.13 s (2 renders: picture + object-id pass); outlines and depth fade (numpy) 0.13 s, of which outline edges 0.05 s |
| Slice change | none measurable (5 frames with a new slice each: 0.10 s per frame, as without) |

Phase 2.5 (slice as a texture) is therefore **skipped**: changing the slice costs less than 20 ms per frame on the GPU.

## The user's Dragonfly 2025 session (`Curated_v1.ORSSession`, 1.5 GB; 1024 × 1440 × 361, 16 structures)

`Studio.add_files`, cProfile, 30.6 s in total (the window took about 47 s with the renderer, soft shadows and the
first preview). Peak RSS 6.9 GB.

| Step | Time | Note |
|---|---|---|
| Surfaces of 15 structures (`build_meshes`, one after another) | **22.1 s** | marching cubes 8.9 s, VTK normals 7.1 s, Taubin smoothing 4.0 s, padding 1.1 s |
| `label_stats` (counts and boxes) | 4.2 s | `astype` copies 2.9 s, `find_objects` 1.1 s |
| Reading the session index (walking the 1,123 records) | 2.0 s | Dragonfly 2025 layout: decompresses every record once |
| Reading image and labels | 1.5 s | |

Biggest gains for phase 2: build the 15 surfaces in parallel (they are independent) and cache them on disk (the second
load of the same file would skip the 22 s); label counts without int copies (about 3 s); keeping the image as 16-bit
instead of float32 saves 1 GB.

## After phase 2 (2026-11-26), the same session file

| Step | Before | After |
|---|---|---|
| First opening | 30.6 s | 29.9 s (surfaces 24 s, made one after another) |
| **Opening it again** | 30.6 s | **6.2 s** (15 surfaces from the disk cache in 1.0 s) |
| Tomogram in memory | 2.1 GB (float32) | 1.06 GB (its own 16-bit type) |
| Label counts | 4.2 s, about 6 GB of temporary copies | chunked, on the native type |
| Soft preview frame (960 × 540), 27.8 M triangles | 0.60 s | 0.32 s with Detail ▸ half on the 4 largest structures (12.6 M triangles) |

Tried and dropped: building surfaces in threads (no gain: marching cubes and VTK hold Python's lock) and in separate
processes (51 s instead of 21 s, and 8 workers on large structures ran the machine out of memory once).
The GPU spends about 0.25 s per pass on 28 M triangles, and Soft looks draw two passes (picture + object outlines): the
number of triangles is what to reduce (Detail, or Data ▸ Size ▸ half).
