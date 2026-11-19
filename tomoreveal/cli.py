"""
TomoReveal - slice-sweep and segmentation-reveal movies for cryo-ET tomograms.

    tomoreveal                          open the desktop program
    tomoreveal tomo.mrc seg.ORSSession  open it with these files loaded
    tomoreveal demo                     open it with a small made-up tomogram (checks the installation)

Without the window:
    tomoreveal list    session.ORSSession
    tomoreveal inspect session.ORSSession   (or a folder of sessions; add --json for machine-readable output)
    tomoreveal movie   tomo.mrc --session session.ORSSession --roi SegWiz --view oblique,top --focus "light ves" -o out.mp4
    tomoreveal movie   tomo.mrc --features tomo__membrane.mrc tomo__ribosome.mrc --threshold 0.5 --clip 0,400,0,600,10,50
    tomoreveal preview tomo.mrc --session session.ORSSession --roi SegWiz
    tomoreveal extract session.ORSSession --roi SegWiz -o seg_out --masks
    tomoreveal app                      the same controls in a web browser instead of a window

On Linux, add --headless on a computer without a display or a working graphics driver (needs the conda package mesalib).
If the command "tomoreveal" is not found, use "python -m tomoreveal" in its place.
"""
import argparse
import json
import math
import os
import re
import sys

import numpy as np

from .common import UserError, VIEWS, __version__
from .dragonfly import OrsSession, describe_session, guess_roi
from .gui import cmd_demo, cmd_gui, cmd_icon
from .render import get_font, render_movie
from .storyboard import build_plan, default_spec, focus_shot, main_range
from .studio import Studio
from .volumes import write_mrc
from .webapp import cmd_app


def cli_studio(args):
    st = Studio(headless=getattr(args, "headless", False), smooth=getattr(args, "smooth", 15), step=getattr(args, "mesh_step", 1),
                keep_background=getattr(args, "keep_background", False))
    st.default_thr = args.threshold
    paths = [p for p in (getattr(args, "tomogram", None), args.session, args.labels) if p] + list(args.features)
    roi = None
    if args.roi:
        if len(args.roi) > 1:
            raise UserError("give one --roi (use the app or 'all plain ROIs' via --roi __all_rois__ to combine ROIs)")
        roi = args.roi[0]
    info = st.load(paths, roi=roi, as_features=args.features)
    if not st.can_show:
        raise UserError("nothing to show: give a tomogram and/or a segmentation (--session, --labels, --features)")
    if args.classes:  # keyed by label number
        for k, v in json.load(open(args.classes)).items():
            for c in st.ds.classes.values():
                if c["kind"] == "label" and str(c["label"]) == str(k):
                    c.update({kk: vv for kk, vv in v.items() if kk in ("name", "color")})
    return st, info


def cli_spec(st, args):
    spec = default_spec(st.ds); ds = st.ds
    def match(spec_list, k):
        return any(s == str(ds.classes[k]["label"]) or s.lower() == ds.classes[k]["name"].lower() for s in spec_list)
    only = [s.strip() for s in (args.only or "").split(",") if s.strip()]
    excl = [s.strip() for s in (args.exclude or "").split(",") if s.strip()]
    for k in ds.classes:
        c = spec["classes"][ds.key_of(k)]
        if only:
            c["show"] = match(only, k)
        if match(excl, k):
            c["show"] = False
        if getattr(args, "classes", None):
            ov = json.load(open(args.classes)).get(str(ds.classes[k]["label"]), {})
            if "show" in ov:
                c["show"] = bool(ov["show"])
        if k not in ds.meshes:
            c["show"] = False
    spec["style"].update(bg=args.bg, contrast=list(args.contrast), invert=args.invert, ortho=args.ortho, fov=args.fov)
    spec["view"]["zoom"] = args.zoom
    if getattr(args, "clip", None):
        if len(args.clip) != 6:
            raise UserError("--clip needs six numbers: X0,X1,Y0,Y1,Z0,Z1")
        c = args.clip; spec["clip"] = dict(x=c[0:2], y=c[2:4], z=c[4:6], fit=True)
    if st.match_note:
        print("  " + st.match_note)
    spec["output"].update(size=list(args.size), aa=not args.no_aa)
    for k in sorted(ds.classes):
        c = spec["classes"][ds.key_of(k)]
        if c["show"]:
            print(f"  class {k:>3}  {c['color']}  {ds.classes[k]['voxels']:>12,} voxels  {c['name']}")
    return spec


def parse_views(spec):
    out = []
    for s in spec.split(","):
        s = s.strip()
        if s in VIEWS:
            out.append((s, *VIEWS[s]))
        else:
            try:
                t, a = (float(x) for x in s.split(":")); out.append((f"tilt{t:g}_az{a:g}", t, a))
            except ValueError:
                raise UserError(f"unknown view '{s}'. Presets: {', '.join(VIEWS)}; or TILT:AZIMUTH in degrees, e.g. 30:20")
    return out


def cmd_movie(args):
    st, _ = cli_studio(args); spec = cli_spec(st, args); ds = st.ds
    spec["style"].update(legend=not args.no_legend, slice_label=not args.no_slice_label, title=args.title or "")
    if args.scale_bar:
        from .overlays import set_item, set_show
        set_show(spec, "scale_bar", True)
        if args.scale_bar != "auto":
            set_item(spec, "scale_bar", length_nm=float(args.scale_bar))
    if args.voxel_nm:
        spec["data"] = dict(voxel_size_nm=float(args.voxel_nm))
    spec["z_range"] = list(args.z_range) if args.z_range else None
    spec["output"].update(fps=args.fps, crf=args.crf)
    for part in spec["shots"]:  # by type, never by position
        if part["type"] == "slide":
            part["seconds"] = args.sweep_seconds
        elif part["type"] == "overview":
            part.update(on=args.end != "none", motion=args.end, seconds=args.end_seconds, angle=args.rock_angle)
    for name in args.focus or []:
        hits = [k for k, c in ds.classes.items() if name == str(c["label"]) or name.lower() == c["name"].lower()]
        if not hits or hits[0] not in ds.meshes:
            raise UserError(f"--focus '{name}': no such structure. Available: {', '.join(c['name'] for k, c in ds.classes.items() if k in ds.meshes)}")
        spec["shots"].append(focus_shot({"class": ds.key_of(hits[0])}, ds.classes[hits[0]]["name"], seconds=args.focus_seconds))
    views = parse_views(args.view); base, ext = os.path.splitext(args.out)
    for name, tilt, az in views:
        out = args.out if len(views) == 1 else f"{base}_{name}{ext or '.mp4'}"
        spec["view"].update(tilt=tilt, az=az)
        fdir = None
        if args.frames_dir:
            fdir = args.frames_dir if len(views) == 1 else os.path.join(args.frames_dir, name)
        def prog(i, n, img, name=name):
            if i % 25 == 0 or i == n:
                print(f"\r  {name}: {i}/{n} frames", end="", flush=True)
        n, secs = render_movie(st.ren, spec, out, frames_dir=fdir, transparent=args.transparent_frames, progress=prog, poster=not args.no_poster)
        print(f"\r  wrote {out}  ({n} frames, {secs:.1f} s, {os.path.getsize(out) / 1e6:.1f} MB)")


def cmd_preview(args):
    from PIL import Image, ImageDraw
    st, _ = cli_studio(args); spec = cli_spec(st, args)
    spec["style"].update(legend=False, slice_label=False)
    views = parse_views(args.view) if args.view != "oblique" else [(n, *v) for n, v in VIEWS.items()]
    size = (640, 360) if args.size[0] >= args.size[1] else (360, 640)
    spec["output"]["size"] = list(size)
    cols = min(4, len(views)); rows = math.ceil(len(views) / cols)
    sheet = Image.new("RGB", (cols * size[0], rows * size[1]), args.bg); font = get_font(20)
    for i, (name, tilt, az) in enumerate(views):
        spec["view"].update(tilt=tilt, az=az)
        plan = build_plan(spec, st.ds); frames = plan.frames
        # halfway through the first slide that shows structures
        reveal = next((p["id"] for p in spec["shots"] if p["type"] == "slide" and p.get("structures") in ("above", "all")
                       and main_range(plan, p["id"])), None)
        idx = [j for j, s in enumerate(frames) if s["part"] == reveal and s["phase"] == "main"] or list(range(len(frames)))
        s = dict(frames[idx[len(idx) // 2]])
        if args.z is not None:
            s["k"] = args.z
        im = Image.fromarray(st.ren.draw(s, spec, size)[..., :3])
        ImageDraw.Draw(im).text((12, 8), f"--view {name}", fill=(255, 255, 0), font=font)
        sheet.paste(im, ((i % cols) * size[0], (i // cols) * size[1]))
    sheet.save(args.out)
    print(f"wrote {args.out}  ({len(views)} views)")


def cmd_list(args):
    ses = OrsSession(args.session)
    print(f"{args.session}  (Dragonfly {ses.version})")
    for im in ses.images():
        nx, ny, nz = im["dims"]
        print(f"  [image    ] {nx} x {ny} x {nz}  {im['voxel']:.3f} A  {'':19}  {im['title']}")
    for o in ses.objects(args.all):
        nx, ny, nz = o["dims"]
        vox = f"{o['voxels']:>12,} voxels" if o["voxels"] >= 0 else " " * 19
        print(f"  [{o['kind']:9}] {nx} x {ny} x {nz}  {o['voxel']:.3f} A  {vox}  {o['title']}")
        if o["kind"] == "multi-ROI" and args.classes:
            lab, classes = ses.read_multiroi(o)
            cnt = np.bincount(lab.ravel().astype(np.int64), minlength=len(classes) + 1)
            for k, c in classes.items():
                print(f"        {k:>3}  {c['color']}  {cnt[k]:>12,}  {c['name']}")


def _session_paths(target):
    if target == "demo" and not os.path.exists(target):
        raise UserError("The demo data has no Dragonfly session: it is a tomogram file and a label file. "
                        "There is nothing to inspect. Give a .ORSSession file or a folder that holds some.")
    if os.path.isdir(target):
        found = sorted(os.path.join(target, f) for f in os.listdir(target) if f.lower().endswith(".orssession"))
        if not found:
            raise UserError(f"There is no Dragonfly session (.ORSSession file) in the folder {target}. "
                            "inspect only reads Dragonfly sessions.")
        return found
    if not os.path.isfile(target):
        raise UserError(f"File not found: {target}")
    if not target.lower().endswith(".orssession"):
        raise UserError(f"{os.path.basename(target)} is not a Dragonfly session (.ORSSession file). "
                        "inspect only reads Dragonfly sessions; tomograms and label files need no inspection.")
    return [target]


def _fmt_values(v, limit=12):
    if isinstance(v, dict):
        items = [f"{k}: {x}" for k, x in v.items()]
    elif isinstance(v, list):
        items = [f"{x:g}" if isinstance(x, float) else str(x) for x in v]
    else:
        return str(v)
    more = f"  ... ({len(items) - limit} more)" if len(items) > limit else ""
    return ", ".join(items[:limit]) + more


def print_description(d):
    print(f"{d['path']}")
    print(f"  Dragonfly version {d['dragonfly_version']}")
    for o in d["images"] + d["objects"]:
        nx, ny, nz = o["grid"]
        notes = [n for n, on in (("Dragonfly undo copy, skipped", o.get("undo_copy")),
                                 ("single slice, cannot be shown", o.get("single_slice"))) if on]
        line = f"\n  [{o['kind']}] {o['title']}\n    grid {nx} x {ny} x {nz} voxels, {o['voxel_nm']:.3f} nm per voxel"
        if o["kind"] != "image":
            line += f", TotalLabelSize {o['total_label_size']:,}"
        print(line + "".join(f"\n    ({n})" for n in notes))
        if o["kind"] == "multi-ROI":
            print(f"    DataTypesScalarValues: {o['data_types']}  (from {o['data_types_source']})")
            for sl in o["slots"]:
                code = sl["type_code"]
                cs = f", type code {code['one_based']} if numbered from 1 / {code['zero_based']} if from 0" if code else ""
                print(f"    {sl['slot']}: {sl['decoded_as']} ({sl['guid_kind'] or 'no GUID'}{cs}), {sl['size']} entries")
                print(f"      " + (sl["error"] if "error" in sl else _fmt_values(sl["values"])))
        if "error" in o:
            print(f"    PROBLEM: {o['error']}")
        for what, rule in (o.get("rules") or {}).items():
            print(f"    {what}: {rule}")
        if o.get("table"):
            if "decoded_voxels" in o:
                ok = "matches" if o["decoded_voxels"] == o["total_label_size"] else "DOES NOT MATCH"
                print(f"    decoded {o['decoded_voxels']:,} labelled voxels ({ok} TotalLabelSize)")
            print(f"    {'label':>5} | {'voxels':>12} | {'colour':7} | visible | name")
            for r in o["table"]:
                vis = "?" if r["visible"] is None else ("yes" if r["visible"] else "no")
                print(f"    {r['label']:>5} | {r['voxels']:>12,} | {r['colour'] or '-':7} | {vis:7} | {r['name']}")


def cmd_inspect(args):
    paths = _session_paths(args.session)
    descs = [describe_session(p) for p in paths]
    if args.json:
        print(json.dumps(descs if len(descs) > 1 else descs[0], indent=2))
        return
    for i, d in enumerate(descs):
        if i:
            print("\n" + "-" * 70)
        print_description(d)


def cmd_extract(args):
    ses = OrsSession(args.session)
    if args.roi and len(args.roi) > 1:
        objs = [ses.find(r) for r in args.roi]
    else:
        name = args.roi[0] if args.roi else guess_roi(ses.objects())
        objs = [o for o in ses.objects() if o["kind"] == "ROI"] if name == "__all_rois__" else [ses.find(name)]
    lab, classes = ses.read_selection(objs); vox = objs[0]["voxel"]
    print(f"segmentation: {', '.join(o['title'] for o in objs)}  grid {lab.shape[::-1]}  {vox:.3f} A/voxel")
    os.makedirs(args.out, exist_ok=True)
    write_mrc(os.path.join(args.out, "labels.mrc"), lab, vox)
    json.dump({str(k): v for k, v in classes.items()}, open(os.path.join(args.out, "classes.json"), "w"), indent=2)
    cnt = np.bincount(lab.ravel().astype(np.int64), minlength=max(classes, default=0) + 1)
    for k, c in classes.items():
        print(f"  {k:>3}  {c['color']}  {cnt[k]:>12,} voxels  {c['name']}")
        if args.masks and cnt[k]:
            safe = re.sub(r"[^\w\-]+", "_", c["name"]).strip("_") or f"class{k}"
            write_mrc(os.path.join(args.out, f"mask_{k:02d}_{safe}.mrc"), (lab == k).astype(np.int8), vox)
    print(f"wrote {args.out}/labels.mrc and classes.json" + (" and per-class masks" if args.masks else ""))


def main():
    ap = argparse.ArgumentParser(prog="tomoreveal", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("gui", help="open the desktop program (default when no command is given)")
    p.add_argument("files", nargs="*", help="optional tomogram / segmentation files to open straight away")
    p.add_argument("--outdir", default=None, help="folder for finished movies (remembered between sessions)")
    p.add_argument("--headless", action="store_true", help="software rendering on Linux (no graphics card; needs the conda package mesalib)")
    p.set_defaults(func=cmd_gui)

    p = sub.add_parser("icon", help=argparse.SUPPRESS); p.add_argument("path"); p.set_defaults(func=cmd_icon)

    p = sub.add_parser("demo", help="write a small made-up tomogram + segmentation and open it (checks that the installation works)")
    p.add_argument("--folder", default=None, help="where to write the demo files (default: a temporary folder)")
    p.add_argument("--no-gui", action="store_true", help="only write the files")
    p.add_argument("--outdir", default=None); p.add_argument("--headless", action="store_true")
    p.set_defaults(func=cmd_demo)

    p = sub.add_parser("app", help="the same controls in a web browser instead of a window")
    p.add_argument("files", nargs="*", help="optional tomogram / segmentation files to open straight away")
    p.add_argument("--port", type=int, default=8765); p.add_argument("--no-browser", action="store_true")
    p.add_argument("--outdir", default="tomoreveal_output", help="folder for finished movies")
    p.add_argument("--headless", action="store_true", help="software rendering on Linux for machines without a display (needs the conda package mesalib)")
    p.set_defaults(func=cmd_app)

    def seg_args(p):
        p.add_argument("--session", help="Dragonfly .ORSSession file")
        p.add_argument("--roi", action="append", help="multi-ROI or ROI name (or a unique part of it)")
        p.add_argument("--labels", help="integer label volume (.mrc/.tif) instead of a session")
        p.add_argument("--features", nargs="+", default=[], help="easymode / Ais score volumes (<tomogram>__<feature>.mrc), one structure each")
        p.add_argument("--threshold", type=float, default=0.5, help="score threshold 0-1 for --features (default 0.5)")
        p.add_argument("--classes", help="classes.json to override names/colours/show")

    def render_args(p):
        p.add_argument("tomogram", help="tomogram (.mrc/.rec/.map/.tif) on the same grid as the segmentation")
        seg_args(p)
        p.add_argument("--view", default="oblique", help=f"comma-separated presets ({', '.join(VIEWS)}) or TILT:AZIMUTH; one output per view")
        p.add_argument("--only", help="comma-separated class names or numbers to show")
        p.add_argument("--exclude", help="comma-separated class names or numbers to hide")
        p.add_argument("--keep-background", action="store_true", help="do not auto-hide the background class")
        p.add_argument("--size", type=lambda s: tuple(int(x) for x in s.lower().split("x")), default=(1920, 1080), help="WxH (default 1920x1080)")
        p.add_argument("--bg", default="#000000", help="background colour (name or #rrggbb)")
        p.add_argument("--contrast", type=lambda s: tuple(float(x) for x in s.split(",")), default=(1.0, 99.0), help="low,high grey-level percentiles")
        p.add_argument("--invert", action="store_true", help="invert tomogram contrast")
        p.add_argument("--smooth", type=int, default=15, help="surface smoothing iterations, 0 = raw voxel surface")
        p.add_argument("--mesh-step", type=int, default=1, help="surface coarseness; 2 or 3 speeds up very large tomograms")
        p.add_argument("--fov", type=float, default=30.0); p.add_argument("--ortho", action="store_true", help="orthographic projection")
        p.add_argument("--zoom", type=float, default=1.0); p.add_argument("--no-aa", action="store_true", help="disable anti-aliasing (faster)")
        p.add_argument("--clip", type=lambda s: [int(x) for x in s.split(",")], metavar="X0,X1,Y0,Y1,Z0,Z1",
                       help="keep only this box of the tomogram (voxel indices from 0, inclusive)")
        p.add_argument("--headless", action="store_true", help="software rendering on Linux for machines without a display (needs the conda package mesalib)")

    p = sub.add_parser("list", help="list the images and segmentations stored in a Dragonfly session")
    p.add_argument("session"); p.add_argument("--classes", action="store_true", help="also list the classes of each multi-ROI")
    p.add_argument("--all", action="store_true", help="include Dragonfly's undo copies"); p.set_defaults(func=cmd_list)

    p = sub.add_parser("inspect", help="print everything TomoReveal reads from a Dragonfly session (for checking structure names)")
    p.add_argument("session", help=".ORSSession file, or a folder of them")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.set_defaults(func=cmd_inspect)

    p = sub.add_parser("extract", help="write labels.mrc, classes.json and optional per-class masks from a session")
    p.add_argument("session"); p.add_argument("--roi", action="append")
    p.add_argument("-o", "--out", default="segmentation_out"); p.add_argument("--masks", action="store_true")
    p.set_defaults(func=cmd_extract)

    p = sub.add_parser("movie", help="render a movie without the app")
    render_args(p)
    p.add_argument("-o", "--out", default="tomogram_reveal.mp4"); p.add_argument("--fps", type=int, default=30)
    p.add_argument("--sweep-seconds", type=float, default=7.0, help="duration of each pass through the slices")
    p.add_argument("--z-range", type=lambda s: tuple(int(x) for x in s.split(",")), help="first,last slice (0-based) to sweep")
    p.add_argument("--end", choices=["rock", "spin", "none"], default="rock", help="camera motion after the reveal")
    p.add_argument("--end-seconds", type=float, default=6.0); p.add_argument("--rock-angle", type=float, default=25.0)
    p.add_argument("--focus", action="append", help="structure name to zoom into and orbit after the overview (repeatable)")
    p.add_argument("--focus-seconds", type=float, default=5.0)
    p.add_argument("--no-legend", action="store_true"); p.add_argument("--no-slice-label", action="store_true")
    p.add_argument("--scale-bar", nargs="?", const="auto", help="draw a scale bar: 'auto' or a length in nm")
    p.add_argument("--voxel-nm", type=float, help="voxel size in nm, when the file's header is wrong")
    p.add_argument("--title", help="text shown bottom-left"); p.add_argument("--crf", type=int, default=20, help="H.264 quality, lower = better")
    p.add_argument("--frames-dir", help="also save every rendered frame as PNG here")
    p.add_argument("--transparent-frames", action="store_true", help="PNG frames get a transparent background (needs --frames-dir)")
    p.add_argument("--no-poster", action="store_true")
    p.set_defaults(func=cmd_movie)

    p = sub.add_parser("preview", help="contact sheet of stills, one per view preset")
    render_args(p)
    p.add_argument("-o", "--out", default="views_preview.png"); p.add_argument("--z", type=int, help="slice to show")
    p.set_defaults(func=cmd_preview)

    argv = sys.argv[1:]
    if not argv or argv[0] not in ("gui", "app", "icon", "demo", "list", "inspect", "extract", "movie", "preview", "-h", "--help", "--version"):
        argv = ["gui"] + argv  # bare "tomoreveal [files]" opens the desktop program
    args = ap.parse_args(argv)
    if args.cmd == "movie" and args.transparent_frames and not args.frames_dir:
        sys.exit("tomoreveal: --transparent-frames needs --frames-dir")
    try:
        args.func(args)
    except UserError as exc:
        sys.exit(f"tomoreveal: {exc}")
