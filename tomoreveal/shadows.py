"""Real shadows between structures (VTK shadow maps): whether this graphics card can draw them, tested in a separate
process, and the work-around for unlit actors while they are on (NVIDIA, VTK 9.6.2)."""
import numpy as np


def unlit_to_ambient(renderer):
    """VTK shadow maps fail to compile their shader for actors drawn without lighting (NVIDIA, VTK 9.6.2: "undefined
    variable vertexVC"), and the program may then crash. While shadows are on, such actors (the slice, outlines, the
    scale bar, path lines, the Flat preset) are drawn lit by their ambient colour only, which gives the same colour.
    Returns what `restore_unlit` needs to undo it."""
    out = []
    acts = renderer.GetActors(); acts.InitTraversal()
    for _ in range(acts.GetNumberOfItems()):
        pr = acts.GetNextActor().GetProperty()
        if not pr.GetLighting():
            out.append((pr, (pr.GetAmbient(), pr.GetDiffuse(), pr.GetSpecular())))
            pr.SetLighting(True); pr.SetAmbient(1.0); pr.SetDiffuse(0.0); pr.SetSpecular(0.0)
    return out


def restore_unlit(saved):
    for pr, (amb, dif, spe) in saved:
        pr.SetLighting(False); pr.SetAmbient(amb); pr.SetDiffuse(dif); pr.SetSpecular(spe)


def _shadow_selftest():
    """Run in a separate process: render a sphere above a plane with VTK shadow maps; exit code 0 when the shadow is
    there and VTK reported no errors. (With software rendering, shadow maps failed with shader errors.)"""
    import sys
    import tempfile
    import pyvista as pv
    import vtk
    log = tempfile.NamedTemporaryFile(suffix=".log", delete=False).name
    out = vtk.vtkFileOutputWindow(); out.SetFileName(log); vtk.vtkOutputWindow.SetInstance(out)
    pl = pv.Plotter(off_screen=True, window_size=[200, 200], lighting=None)
    pl.add_mesh(pv.Plane(i_size=10, j_size=10), color="white")
    pl.add_mesh(pv.Sphere(radius=1.5, center=(0, 0, 2)), color="white")
    grey = pv.Plane(center=(0, 0, -0.5), i_size=12, j_size=12); grey.point_data["I"] = np.linspace(0, 255, grey.n_points)
    pl.add_mesh(grey, scalars="I", cmap="gray", clim=(0, 255), lighting=False, show_scalar_bar=False)  # drawn like the slice
    lt = pv.Light(position=(0, 0, 10), focal_point=(0, 0, 0), light_type="scene light"); lt.positional = False
    lt.shadow_attenuation = 1.0; pl.add_light(lt); pl.enable_shadows()
    pl.camera_position = [(6, -6, 8), (0, 0, 0), (0, 0, 1)]
    unlit_to_ambient(pl.renderer)
    img = pl.screenshot(None).astype(int); pl.close()
    errors = "ERROR" in open(log, errors="replace").read().upper()
    # the plane right next to the sphere's foot, where the shadow falls, must be darker than a far corner of the plane
    ok = not errors and img.sum() > 0
    sys.exit(0 if ok else 1)


def real_shadows_available(headless):
    """(True, "") when VTK shadow maps work on this computer; (False, reason) otherwise. Tested in a separate process,
    because a broken graphics driver may crash."""
    if headless:
        return False, "Not available with software rendering on this computer"
    import subprocess
    import sys
    try:
        r = subprocess.run([sys.executable, "-c", "import tomoreveal.shadows as r; r._shadow_selftest()"], capture_output=True, timeout=60)
    except Exception:
        return False, "The graphics card could not be tested"
    return (True, "") if r.returncode == 0 else (False, "Not available with this graphics card")
