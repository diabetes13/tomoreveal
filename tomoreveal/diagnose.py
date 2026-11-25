"""How TomoReveal uses the computer (3.6, PLAN-3.6 2.1): what draws the pictures, memory, and where a frame's time goes.
Used by Help ▸ Performance in the window and by `tomoreveal --diagnose`."""
import os
import shutil
import subprocess
import sys
import time

SOFTWARE = ("llvmpipe", "softpipe", "swrast", "mesa offscreen", "osmesa")


def renderer_string(ren_win):
    """The OpenGL renderer string of a VTK render window (after it has drawn once), or ""."""
    try:
        for line in ren_win.ReportCapabilities().splitlines():
            if "renderer string" in line:
                return line.split(":", 1)[1].strip()
    except Exception:
        pass
    return ""


def in_plain_words(renderer, window_class=""):
    """'your NVIDIA GeForce RTX 4070 SUPER (fast)' or 'software rendering on the processor (slow)'."""
    low = (renderer or "").lower()
    if "osopengl" in window_class.lower() or any(s in low for s in SOFTWARE):
        return "software rendering on the processor (slow)"
    if not renderer:
        return "not known yet (nothing drawn so far)"
    name = renderer.split("/")[0].strip()
    return f"your {name} (fast)"


def gpu_load():
    """(load %, memory used MB, memory total MB) of the first NVIDIA card from nvidia-smi, or None."""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "--query-gpu=utilization.gpu,memory.used,memory.total", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=3).stdout.strip().splitlines()
        a, b, c = (float(x) for x in out[0].split(","))
        return a, b, c
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None


def data_memory(ds):
    """{name: bytes} of what the loaded data holds in memory: tomogram, segmentations, surfaces."""
    if ds is None:
        return {}
    out = {}
    if ds.tomo is not None:
        out["tomogram"] = int(ds.tomo.nbytes)
    seen, lab = set(), 0
    for arr, _ in ds._labels.values():
        if id(arr) not in seen:
            seen.add(id(arr)); lab += int(arr.nbytes)
    for f in ds.scores.values():
        if id(f["arr"]) not in seen:
            seen.add(id(f["arr"])); lab += int(f["arr"].nbytes)
    out["segmentation"] = lab
    out["surfaces"] = int(sum(m.points.nbytes + m.faces.nbytes for m in ds.meshes.values() if m is not None))
    return out


def triangles(ds):
    return int(sum(m.n_cells for m in ds.meshes.values() if m is not None)) if ds is not None else 0


def frame_steps(ren, state, spec, size, aa=None):
    """Seconds of one frame, split: [(step, seconds)], measured by timing the renderer's parts (cProfile)."""
    import cProfile
    import pstats
    pr = cProfile.Profile(); t0 = time.perf_counter(); pr.enable()
    ren.draw(state, spec, size, aa=aa)
    pr.disable(); total = time.perf_counter() - t0
    st = pstats.Stats(pr).stats

    def cum(name, file_part=""):
        return sum(v[3] for (f, _, fn), v in st.items() if fn == name and file_part in f)
    render = cum("Render")
    ids = cum("_id_pass")
    looks = cum("_outlines_and_fade") - ids
    overlays = cum("annotate", "render.py")
    readback = cum("screenshot")
    other = max(0.0, total - render - looks - overlays - readback)
    steps = [("drawing the 3D scene (graphics card)", max(0.0, render - ids)), ("object outlines pass", ids),
             ("reading the picture back", readback), ("outlines, depth fade, blur (processor)", max(0.0, looks)),
             ("legend, arrows and text", overlays), ("everything else", other)]
    return total, steps


def report(st=None):
    """Plain-text report for `tomoreveal --diagnose` (and the window's log)."""
    from .common import __version__
    lines = [f"TomoReveal {__version__}, Python {sys.version.split()[0]}, {sys.platform}"]
    try:
        import vtk
        lines.append(f"VTK {vtk.vtkVersion.GetVTKVersion()}")
    except ImportError:
        lines.append("VTK is not installed")
    win_class, rs = "", ""
    if st is not None and st.ren is not None:
        win_class = type(st.ren.pl.ren_win).__name__; rs = renderer_string(st.ren.pl.ren_win)
    else:
        try:
            import pyvista as pv
            pl = pv.Plotter(off_screen=True, window_size=[64, 64]); pl.add_mesh(pv.Sphere()); pl.screenshot(return_img=True)
            win_class, rs = type(pl.ren_win).__name__, renderer_string(pl.ren_win); pl.close()
        except Exception as exc:
            lines.append(f"3D drawing could not start: {exc}")
    lines.append(f"Drawing with: {in_plain_words(rs, win_class)}" + (f"  [{rs}; {win_class}]" if rs else ""))
    g = gpu_load()
    if g:
        lines.append(f"Graphics card: {g[0]:.0f} % busy, {g[1]:.0f} of {g[2]:.0f} MB memory used")
    enc = shutil.which("ffmpeg")
    lines.append("Video encoder: processor (x264)" + ("" if enc else " — ffmpeg not found"))
    if st is not None and st.ds is not None:
        mem = data_memory(st.ds)
        lines.append("Memory: " + ", ".join(f"{k} {v / 1e9:.2f} GB" for k, v in mem.items()) + f"; {triangles(st.ds):,} triangles")
    return "\n".join(lines)


def main():
    print(report())
    return 0


if __name__ == "__main__":
    sys.exit(main())
