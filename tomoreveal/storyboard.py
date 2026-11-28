"""Camera framing, the clip box, and turning the movie settings into per-frame states."""
import json
import math

import numpy as np

from .animations import closeup_animation, steady_framing
from .frame_passes import LOOK_BLEND, OVERVIEW, PART_LOOK, caption_fades, depth_ranges, part_look, part_looks  # noqa: F401 (used from here)
from .common import UserError
from .parts import (  # most of them are used from here by other modules and tests
    CLOSEUP_TRANSITION, OTHER_TRANSITION, VIEW_TRANSITION, _mark_label, as_legacy, class_spec, closeup_caption,
    closeup_others, default_opacity, default_parts, default_spec, each_of, flip_spec, focus_box, focus_shot,
    image_block, make_target, merge_settings, migrate_spec, new_id, new_part, others_choice, others_of,
    part_name, part_region, set_others_choice, shot_tomogram, target_items, text_block, tomogram_setting)
from .paths import PathPending, path_poses
from .transitions import ease_curve, lerp_camera, smooth01, transition_of, with_end_ease


def clip_box(spec, shape):
    """Kept region of the tomogram as ((x0, y0, z0), (x1, y1, z1)) inclusive, or None when nothing is clipped."""
    c = spec.get("clip")
    if not c:
        return None
    nz, ny, nx = shape; lo, hi = [], []
    for ax, n in (("x", nx), ("y", ny), ("z", nz)):
        a, b = c.get(ax) or (0, n - 1)
        a = min(max(0, int(a)), n - 1); b = min(max(a, int(b)), n - 1)
        lo.append(a); hi.append(b)
    if lo == [0, 0, 0] and hi == [nx - 1, ny - 1, nz - 1]:
        return None
    return tuple(lo), tuple(hi)


class Framer:
    """Right-handed view from +z; the long in-plane axis runs along the long side of the frame."""
    def __init__(self, shape, aspect, fov=30.0, ortho=False):
        nz, ny, nx = shape
        if (ny > nx) == (aspect >= 1):
            self.u = np.array([-1., 0, 0])
        else:
            self.u = np.array([0., 1, 0])
        self.z = np.array([0., 0, 1]); self.aspect = aspect; self.fov = fov; self.ortho = ortho

    def axes(self, tilt, az):
        t, a = math.radians(tilt), math.radians(az)
        p = math.cos(t) * self.z - math.sin(t) * self.u      # centre -> camera
        up = math.cos(t) * self.u + math.sin(t) * self.z
        R = np.array([[math.cos(a), -math.sin(a), 0], [math.sin(a), math.cos(a), 0], [0, 0, 1]])
        return R @ p, R @ up

    def fit(self, lo, hi, tilt, az, margin=1.07):
        """Pose that just frames the box lo..hi."""
        p, up = self.axes(tilt, az)
        c, dist, scale = self.fit_dir(lo, hi, p, up, margin)
        return dict(c=c, tilt=float(tilt), az=float(az), dist=dist, scale=scale)

    def fit_dir(self, lo, hi, p, up, margin=1.07):
        """(centre, distance, scale) that just frame the box lo..hi seen along p (centre -> camera) with this up."""
        lo, hi = np.asarray(lo, float), np.asarray(hi, float)
        hi = np.maximum(hi, lo + 1.0); c = (lo + hi) / 2  # at least one voxel wide: a zero-size box has no distance
        corners = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]) - c
        th = math.tan(math.radians(self.fov) / 2)
        right = np.cross(up, p)
        xc, yc, zc = np.abs(corners @ right), np.abs(corners @ up), corners @ p
        dist = float(np.max(zc + np.maximum(yc / th, xc / (th * self.aspect)))) * margin
        scale = float(np.max(np.maximum(yc, xc / self.aspect))) * margin
        return c, dist, scale


SPIN_AXES = (("around Z (up and down through the slices)", "z"), ("around X", "x"), ("around Y", "y"))


def _turn(axis, deg):
    """Rotation matrix of deg degrees around the x, y or z axis of the volume."""
    a = math.radians(deg); c, s = math.cos(a), math.sin(a)
    return {"x": np.array([[1, 0, 0], [0, c, -s], [0, s, c]]), "y": np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]]),
            "z": np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])}[axis]


def axis_turn_poses(fr, p0, axis, angles, lo, hi):
    """Camera poses turned around the volume's x or y axis (through the rotation centre of p0) by each angle. The
    distance is the one that frames lo..hi at every angle reached, so nothing leaves the picture on the way round."""
    from .paths import _pose
    d0, up0 = fr.axes(p0["tilt"], p0["az"]); c = np.asarray(p0["c"], float)
    zoom = p0["dist"] / max(1e-9, fr.fit(lo, hi, p0["tilt"], p0["az"], 1.07)["dist"])  # the view's own zoom (and pan)
    samples = np.linspace(min(angles), max(angles), 25) if len(angles) else [0.0]
    D = max(fr.fit_dir(lo, hi, _turn(axis, a) @ d0, _turn(axis, a) @ up0)[1] for a in samples) * zoom
    out = []
    for a in angles:
        R = _turn(axis, a); pos = c + D * (R @ d0)
        q = _pose(fr, pos, c, R @ up0)
        if fr.ortho:
            q["scale"] = p0["scale"] * D / p0["dist"]
        out.append(q)
    return out


def view_pan(view):
    """Offset (x, y, z voxels) of the rotation centre of a view from the middle of what it frames, or None."""
    pan = (view or {}).get("pan")
    if pan is None:
        return None
    pan = np.asarray(pan, float)
    return None if pan.shape != (3,) or not np.any(pan) else pan


def main_box(spec, shape):
    """(lo, hi) in voxels (x, y, z) of what the main view frames: the whole volume, or the clip box (unless not fitted)."""
    nz, ny, nx = shape
    lo, hi = np.zeros(3), np.array([nx - 1, ny - 1, nz - 1], float)
    cb = clip_box(spec, shape)
    if cb and (spec.get("clip") or {}).get("fit", True):
        lo, hi = np.array(cb[0], float), np.array(cb[1], float)
    return lo, hi


def view_framing(spec, shape, lo, hi, tilt, az):
    """(zoom, pan) of a view at (tilt, az) that frames the box lo..hi, as the main view frames its box at zoom 1."""
    out = spec.get("output", {}); w, h = out.get("size", [1920, 1080]); style = spec.get("style", {})
    fr = Framer(shape, w / h, float(style.get("fov", 30)), bool(style.get("ortho", False)))
    mlo, mhi = main_box(spec, shape)
    base, want = fr.fit(mlo, mhi, tilt, az, 1.07), fr.fit(lo, hi, tilt, az, 1.07)
    z = base["scale"] / want["scale"] if fr.ortho else base["dist"] / want["dist"]
    return float(z), [float(v) for v in want["c"] - base["c"]]


def _pause_frames(fps, seconds):
    """Frames of a pause; halves round down (0.25 s at 30 fps is 7 frames, as in 3.1)."""
    s = float(seconds or 0)
    return 0 if s <= 0 else max(1, math.ceil(fps * s - 0.5))


class Plan:
    """The movie as frames: frames (one state each), segments (which frames belong to which part), marks, fps,
    problems ({part id: plain-language reason it is skipped})."""
    def __init__(self, frames, segments, marks, fps, problems):
        self.frames, self.segments, self.marks, self.fps, self.problems = frames, segments, marks, fps, problems

    def __len__(self):
        return len(self.frames)


def build_plan(spec, ds):
    """Settings -> Plan. Parts may come in any order; each starts where the previous one ended, and a transition is
    inserted when the camera, the slice or the close-up emphasis must change first."""
    migrate_spec(spec)
    out = spec.get("output", {}); fps = int(out.get("fps", 30)); w, h = out.get("size", [1920, 1080])
    style, v = spec.get("style", {}), spec.get("view", {})
    shape = ds.shape; nz, ny, nx = shape
    has_tomo, has_seg = ds.tomo is not None, ds.has_seg()
    base = default_opacity(spec, has_tomo)  # tomogram slice opacity when no part asks for another
    zr = spec.get("z_range") or [0, nz - 1]
    z0, z1 = max(0, int(zr[0])), min(nz - 1, int(zr[1]))
    if z1 < z0:
        z0, z1 = 0, nz - 1
    fr = Framer(shape, w / h, float(style.get("fov", 30)), bool(style.get("ortho", False)))
    lo, hi = np.zeros(3), np.array([nx - 1, ny - 1, nz - 1], float)
    cb = clip_box(spec, shape)
    if cb:  # slide only through the kept slices and, unless switched off, frame the kept region
        z0, z1 = max(z0, cb[0][2]), min(z1, cb[1][2])
        if z1 < z0:
            z0, z1 = cb[0][2], cb[1][2]
        if spec["clip"].get("fit", True):
            lo, hi = np.array(cb[0], float), np.array(cb[1], float)
    tilt, az, zoom = float(v.get("tilt", 42)), float(v.get("az", 0)), max(0.05, float(v.get("zoom", 1)))

    def zoomed(p, z):
        p = dict(p); p["dist"] /= z; p["scale"] /= z
        return p

    def ov(d=0.0, view=None):
        t, a, zm = (tilt, az, zoom) if not view else (float(view.get("tilt", tilt)), float(view.get("az", az)),
                                                       max(0.05, float(view.get("zoom", zoom))))
        p = zoomed(fr.fit(lo, hi, t, a + d, 1.07), zm)
        pan = view_pan(view if view else v)  # the rotation centre moved in the preview (3.5)
        if pan is not None:
            p["c"] = p["c"] + pan
        return p

    def target_of(part):
        if not has_tomo:
            return 0.0
        t = shot_tomogram(part)
        return {"default": base, "shown": 1.0, "hidden": 0.0}.get(t["mode"], float(min(1.0, max(0.0, t["opacity"]))))

    def slice_no(x, cur):
        if x == "first":
            return z0
        if x == "last":
            return z1
        if x == "current" or x is None:
            return cur
        return int(min(nz - 1, max(0, int(x))))

    frames, marks, segments, problems = [], [], [], {}
    st = dict(k=z0, reveal=False, cut=None, slice=base, e=0.0, target=None, others="grey", caption="", card=None)
    pose = [ov()]
    ramp = {}  # the tomogram fade at the start of the current part
    cur = dict(id=None, index=-1, type=None); shown = {}  # shown: the last part that drew frames
    tbase, ended = {}, []  # the first pause or main frame of each part (for "tp"); set once the flight back has begun
    rown = [None]  # whose depth range the frames show now (a part id, or OVERVIEW); see "rng" in emit and depth_ranges

    def start_fade(part, fade=None):
        t = shot_tomogram(part); f = t["fade"] if fade is None else fade
        if fade is None and t.get("whole"):  # fade gently over the whole part (for example while the segmentation is revealed)
            f = float(part.get("seconds", 5)) + float(part.get("pause_before") or 0)
        ramp.clear(); ramp.update(made=0, s0=st["slice"], s1=target_of(part), n=max(1, round(fps * max(0.0, float(f)))))

    last = {}   # pending dissolve / fade, waiting for the next part's first frames

    def emit(n, fn, phase, label=None, seg_phase=None, xb=None):
        n = max(1, int(n)); shown.update(cur)
        if last.get("blend") and phase in ("main", "pause") and xb is None:  # a dissolve or fade into this part (3.5)
            b = last.pop("blend"); nf = max(0, b["n"] - b["m2"])
            if nf > 0:
                emit(nf, lambda u, fn=fn: fn(0.0), "transition", label, xb=dict(b, offset=0)); label = None
            xb = dict(b, offset=nf)
        if label:
            marks.append(dict(frame=len(frames), label=label))
        sg = dict(part=cur["id"], index=cur["index"], type=cur["type"], phase=seg_phase or phase, start=len(frames), count=n)
        if (seg_phase or phase) == "transition" and cur["id"] is not None:
            sg["transition"] = f"{cur['id']}:in"  # selectable on the timeline (3.5)
        segments.append(sg)
        for i in range(n):
            s = dict(st); s["pose"] = pose[0]; s["shot"] = cur["index"]; s["part"] = cur["id"]; s["type"] = cur["type"]; s["phase"] = phase
            d = fn(i / (n - 1) if n > 1 else 1.0)
            if ramp:  # the tomogram fade counts the frames it has made (frames taken back by an overlap are not made)
                ramp["made"] += 1
                if "slice" not in d:
                    q = smooth01(ramp["made"] / ramp["n"])
                    d["slice"] = ramp["s1"] if q >= 1 else ramp["s0"] + (ramp["s1"] - ramp["s0"]) * q
            s.update(d)
            if "rng" not in d:  # (from, to, q): whose depth range the frame shows; a flight glides from one to the other
                s["rng"] = (rown[0], rown[0], 1.0)
            # seconds since the part's first pause or main frame: the clock of arrows and text (None where they never show)
            s["tp"] = None if phase not in ("main", "pause") or cur["id"] is None or s.get("card") or ended else                 (len(frames) - tbase.setdefault(cur["id"], len(frames))) / fps
            if xb is not None:
                g = xb["offset"] + i
                if g < xb["n"]:
                    src = xb["src"][min(g, len(xb["src"]) - 1)]
                    if src is not None:
                        s["blend"] = dict(src=src, q=round((g + 1) / (xb["n"] + 1), 4), via=xb["via"])
            frames.append(s)
        lastf = frames[-1]
        for key in st:
            st[key] = lastf[key]
        pose[0] = lastf["pose"]

    def emit_main(n, fn, label, skip=0, part=None):
        """A part's own motion: slowed down at its end when asked (3.5), and starting `skip` frames in when the
        transition into it overlapped its start."""
        fn = with_end_ease(fn, part)
        skip = int(min(max(0, skip), n - 1))
        if skip:
            emit(n - skip, lambda u, fn=fn: fn((skip + u * (n - skip - 1)) / (n - 1) if n > 1 else 1.0), "main", label)
        else:
            emit(n, fn, "main", label)
        segments[-1]["skip"] = skip  # (edits.split_part counts these frames too)

    def need_structures():
        """Overviews and close-ups show structures: switch them all on if none are shown."""
        if not st["reveal"]:
            st.update(reveal=True, cut="all")

    def same_pose(a, b):
        return (np.allclose(a["c"], b["c"]) and abs(a["tilt"] - b["tilt"]) < 1e-6 and abs(((a["az"] - b["az"] + 180) % 360) - 180) < 1e-6
                and abs(a["dist"] - b["dist"]) < 1e-6)

    def take_previous(m):
        """Overlap with the previous part: take back its last m frames (they become the start of the transition)."""
        if m <= 0 or not segments or not frames:
            return []
        sg = segments[-1]
        if sg["phase"] not in ("main", "pause") or sg["part"] == cur["id"] or sg["type"] == "card":  # (a card keeps its fade)
            return []
        m = min(m, sg["count"] - 1)
        if m <= 0:
            return []
        out = frames[-m:]; del frames[-m:]; sg["count"] -= m
        return out

    def overlap(tr, seconds):
        """The previous part's last frames that a transition overlaps, taken back. The state goes back to the first of
        them, so that the next part's tomogram fade starts there and its structures are switched on from there."""
        n = round(fps * max(0.0, seconds))
        if n <= 0 or tr["kind"] == "cut":
            return []
        A = take_previous(min(round(fps * tr["overlap_prev"]), n))
        if A:
            st.update({k: A[0][k] for k in st}); pose[0] = A[0]["pose"]
        return A

    def flight_pose(i, n, q, p0, p1, A, m2, nxt, tr):
        a = A[i]["pose"] if i < len(A) else (A[-1]["pose"] if A else p0)
        a_end = A[-1]["pose"] if A else p0  # where the part we leave stops moving
        b = p1
        if m2 > 1:  # the next part's own motion starts with the flight, slowly, and reaches its own speed as the flight
            k = (n - 1) / (m2 - 1)  # ends: its clock runs as (m2 - 1)·x^k, whose rate grows from 0 to exactly 1 frame per frame
            b = nxt((m2 - 1) * (i / (n - 1)) ** k).get("pose", p1)  # (a slide moves the slice, not the camera)
        # the turn: the shorter way from where we leave to where the next part starts, plus however far that part has
        # turned on its own since (a spin can turn more than half a circle during the overlap)
        p = lerp_camera(fr, a, b, q, (((p1["az"] - a["az"] + 180.0) % 360.0) - 180.0) + (b["az"] - p1["az"]), a_end, p1)
        if tr["path"] == "arc":  # fly a little higher in the middle
            da, ua = fr.axes(a["tilt"], a["az"]); db, _ = fr.axes(b["tilt"], b["az"])
            span = float(np.linalg.norm((a["c"] + a["dist"] * da) - (b["c"] + b["dist"] * db)))
            lift = np.asarray(ua) * 0.15 * span * math.sin(math.pi * q)
            p = dict(p, c=p["c"] + lift)
            if p.get("pos") is not None:  # a camera of its own rises with what it looks at
                p["pos"] = p["pos"] + lift
        return p

    def go_to(p1, k1, seconds, slice_to=None, label=None, tr=None, nxt=None, nn=0, A=()):
        """Transition: fly the camera to p1 and glide the slice to k1; the close-up emphasis fades out. tr: the
        transition settings of the part (3.5: easing at each end, overlaps, kind, arc); nxt(j): the next part's main
        frame j (nn frames), for an overlap with its start; A: the frames overlap() took back from the part before.
        Returns the frames of the next part already shown."""
        tr = tr or transition_of({})
        p0, k0, e0, s0 = pose[0], st["k"], st["e"], st["slice"]
        n = round(fps * max(0.0, seconds))
        m1 = round(fps * tr["overlap_prev"]); m2 = min(round(fps * tr["overlap_next"]), n, max(0, nn - 1)) if nxt else 0
        if n <= 0 or tr["kind"] == "cut":  # a hard cut
            st.update(k=k1, e=0.0, target=None, caption=""); pose[0] = p1; rown[0] = cur["id"]
            if slice_to is not None:
                st["slice"] = slice_to
            return 0
        if tr["kind"] in ("dissolve", "black", "white"):  # blended in 2D with the next part's first frames
            src = list(A) if A else ([frames[-1]] if frames else [None])  # the frames themselves: the passes below dress them
            st.update(k=k1, e=0.0, target=None, caption=""); pose[0] = p1; rown[0] = cur["id"]
            if slice_to is not None:
                st["slice"] = slice_to
            last["blend"] = dict(n=n, m2=m2, src=src, via=tr["kind"])
            return 0
        if same_pose(p0, p1) and k0 == k1 and e0 <= 0 and not m1 and not m2:
            rown[0] = cur["id"]
            return 0
        ra = cur["id"] if rown[0] is None else rown[0]

        def fn(u):
            i = int(round(u * (n - 1))) if n > 1 else 0
            q = ease_curve(u, tr["ease_out_prev"], tr["ease_in_next"])
            ka = A[i]["k"] if i < len(A) else (A[-1]["k"] if A else k0)
            kb = nxt((m2 - 1) * (i / (n - 1)) ** ((n - 1) / (m2 - 1))).get("k", k1) if m2 > 1 else k1
            d = dict(pose=flight_pose(i, n, q, p0, p1, A, m2, nxt, tr), k=int(round(ka + (kb - ka) * q)), e=e0 * (1 - q), caption="",
                     rng=(ra, cur["id"], q))
            if slice_to is not None:
                d["slice"] = s0 + (slice_to - s0) * q
            return d
        emit(n, fn, "transition", label)
        st.update(target=None, e=0.0); pose[0] = p1; rown[0] = cur["id"]
        return m2

    shots = spec.get("shots", [])
    for si, part in enumerate(shots):
        if not part.get("on", True):
            continue
        typ = part.get("type"); n = max(1, round(fps * max(0.1, float(part.get("seconds", 5)))))
        cur.update(id=part.get("id"), index=si, type=typ)
        tr = transition_of(part)
        if typ == "slide":
            show = part.get("structures", "keep")
            if (not has_tomo and show == "none") or (not has_seg and show in ("above", "all")):
                continue  # nothing to see: no tomogram to slide through, or no segmentation to reveal
            a, b = slice_no(part.get("from"), st["k"]), slice_no(part.get("to"), st["k"])  # (from where the part before ends)
            t = part.get("transition"); back = st["e"] > 0; t = (CLOSEUP_TRANSITION if back else OTHER_TRANSITION) if t is None else float(t)
            A = overlap(tr, t); start_fade(part)
            slide = (lambda u, a=a, b=b: dict(k=a + int(round(u * (b - a)))))
            pb = _pause_frames(fps, part.get("pause_before"))
            slide_w = with_end_ease(slide, part)
            vw = part.get("view") or None  # a view of its own (3.5): for example one slice from the top, zoomed in
            skip = go_to(ov(0.0, vw), a, t, slice_to=(target_of(part) if back else None), tr=tr,
                         nxt=None if pb else (lambda j, f=slide_w, n=n: f(j / (n - 1) if n > 1 else 1.0)), nn=n, A=A)
            if back:
                start_fade(part, fade=0)  # the flight back from the close-up already faded the tomogram
            if show == "none":
                st.update(reveal=False, cut=None)
            elif show == "above":
                st.update(reveal=True, cut=None)
            elif show == "all":
                st.update(reveal=True, cut="all")
            st["k"] = a; st["caption"] = part.get("caption", "")
            label = _mark_label(part)
            if pb:
                emit(pb, lambda u: {}, "pause", label); label = None
            emit_main(n, slide, label, skip, part)
            pa = _pause_frames(fps, part.get("pause_after"))
            if pa:
                emit(pa, lambda u: {}, "pause")
        elif typ == "overview":
            vw = part.get("view") or None; k1 = st["k"]  # (the slice the part before ends on)
            t = part.get("transition"); t = (CLOSEUP_TRANSITION if st["e"] > 0 else OTHER_TRANSITION) if t is None else float(t)
            back = st["e"] > 0
            A = overlap(tr, t); start_fade(part)
            need_structures()
            m, ang = part.get("motion", "rock"), float(part.get("angle", 25))
            turn = (lambda u, m=m, ang=ang: ang * math.sin(2 * math.pi * u) if m == "rock" else 360.0 * u if m == "spin" else 0.0)
            axis = part.get("spin_axis", "z") if m in ("rock", "spin") else "z"
            if axis in ("x", "y"):  # turning around another axis of the volume (3.5): poses worked out once, in steps of 1/n
                uu = [i / (n - 1) if n > 1 else 1.0 for i in range(n)]
                tp = axis_turn_poses(fr, ov(0.0, vw), axis, [turn(u) for u in uu], lo, hi)
                main = (lambda u, tp=tp, n=n: dict(pose=tp[int(round(min(1.0, max(0.0, u)) * (n - 1)))]))
                start_pose = tp[0]
            else:
                main = (lambda u, turn=turn, vw=vw: dict(pose=ov(turn(u), vw)))
                start_pose = ov(0.0, vw)
            ep = part if m == "spin" else None  # "slow down at the end" suits a spin, not a rock (it already ends at rest)
            main_w = with_end_ease(main, ep)
            skip = go_to(start_pose, k1, t, slice_to=ramp["s1"] if back else None, tr=tr,
                         nxt=lambda j, f=main_w, n=n: f(j / (n - 1) if n > 1 else 1.0), nn=n, A=A)
            if back:
                start_fade(part, fade=0)  # the flight back from the close-up already faded the tomogram
            st["caption"] = part.get("caption", "")
            emit_main(n, main, _mark_label(part), skip, ep)
            pose[0] = start_pose
        elif typ == "focus":
            tgt = part.get("target") or {}
            try:
                blo, bhi = focus_box(part, ds)
            except (KeyError, IndexError, TypeError, ValueError):
                problems[part.get("id")] = "structure not loaded" if target_items(tgt) else "choose what to show: objects, or draw a frame"
                continue
            if target_items(tgt) and not any(class_spec(spec, ds, ds.id_of(key)).get("show", True) for key, _ in target_items(tgt)):  # every object is off
                problems[part.get("id")] = "structure switched off in the Structures tab"
                continue
            t = part.get("transition"); t = CLOSEUP_TRANSITION if t is None else float(t)
            nt = round(fps * max(0.0, t)); k0 = st["k"]
            A = overlap(tr, t) if tr["kind"] == "flight" else []  # (a close-up flown into goes on from the rewound slice)
            start_fade(part)
            need_structures()
            ft, fz = float(part.get("tilt", max(tilt, 50))), max(0.05, float(part.get("zoom", 1)))
            faz = float(part["view"]["az"]) if isinstance(part.get("view"), dict) and part["view"].get("az") is not None else az

            m, ang = part.get("motion", "orbit"), float(part.get("angle", 30))
            steady = steady_framing(part, fr, blo, bhi, ft, faz, m, ang) if part.get("steady_zoom") else None

            def fp(d=0.0, blo=blo, bhi=bhi, ft=ft, fz=fz, steady=steady, faz=faz):
                p = fr.fit(blo, bhi, ft, faz + d, 1.0)
                if steady is not None:  # the same distance at every angle: the object does not grow and shrink (3.5)
                    p["dist"], p["scale"] = steady
                return zoomed(p, fz)

            anim = closeup_animation(part, ds, fr, fp, n, fps, k0=k0, nz=nz)
            if anim is None:  # orbit / rock / still without effects: exactly as in 3.3
                anim = (lambda u, fp=fp, m=m, ang=ang: dict(pose=fp(360.0 * u if m == "orbit" else ang * math.sin(2 * math.pi * u) if m == "rock" else 0.0)))
            ep = part if m == "orbit" else None  # wiggle and rock have their own calm ends
            anim_w = with_end_ease(anim, ep)
            nxt = (lambda j, f=anim_w, n=n: f(j / (n - 1) if n > 1 else 1.0))
            m2 = min(round(fps * tr["overlap_next"]), nt, n - 1) if tr["kind"] == "flight" else 0
            p0, p1, e0, old, oldmode, s0 = pose[0], fp(), st["e"], st["target"], st["others"], st["slice"]
            s1 = ramp["s1"]  # the fly-in is this part's tomogram fade
            mode, cap = closeup_others(part), part.get("caption", "")
            ra = cur["id"] if rown[0] is None else rown[0]

            def fly(u, p0=p0, p1=p1, e0=e0, old=old, oldmode=oldmode, s0=s0, s1=s1, tgt=tgt, mode=mode, cap=cap, A=A, m2=m2, nt=nt, nxt=nxt, tr=tr,
                    ra=ra, rb=cur["id"]):
                q = ease_curve(u, tr["ease_out_prev"], tr["ease_in_next"]); i = int(round(u * (nt - 1))) if nt > 1 else 0
                d = dict(pose=flight_pose(i, nt, q, p0, p1, A, m2, nxt, tr), slice=s0 + (s1 - s0) * q, rng=(ra, rb, q))
                if e0 > 0 and old is not None:  # hand over from the previous close-up
                    if u < 0.5:
                        d.update(e=e0 * (1 - smooth01(2 * u)), target=old, others=oldmode, caption="")
                    else:
                        d.update(e=smooth01(2 * u - 1), target=tgt, others=mode, caption=cap if u > 0.8 else "")
                else:
                    d.update(e=q, target=tgt, others=mode, caption=cap if q > 0.6 else "")
                return d

            if (part.get("effect") or {}).get("kind") == "grow":  # the objects disappear on the way in, then grow in
                fly = (lambda u, f0=fly: dict(f0(u), fx={"fade": 1.0 - smooth01(u)}))
            if nt > 0 and tr["kind"] == "flight":
                emit(nt, fly, "transition", cap or "Focus")
                label = None
            else:
                if nt > 0 and tr["kind"] in ("dissolve", "black", "white"):
                    last["blend"] = dict(n=nt, m2=0, src=[frames[-1]] if frames else [None], via=tr["kind"])
                st.update({k: v for k, v in fly(1.0).items() if k in st}); pose[0] = p1; label = cap or "Focus"  # (not "fx", nor the pose)
                m2 = 0
            rown[0] = cur["id"]
            start_fade(part, fade=0)  # the fly-in already faded the tomogram
            emit_main(n, anim, label, m2, ep)
            pose[0] = fp()
        elif typ == "hold":
            vw = part.get("view") or None; A = []
            if vw:  # a view of its own (3.5): the camera flies there first
                t = part.get("transition"); t = (CLOSEUP_TRANSITION if st["e"] > 0 else OTHER_TRANSITION) if t is None else float(t)
                k1 = st["k"]; A = overlap(tr, t)
            start_fade(part)
            if vw:
                go_to(ov(0.0, vw), k1, t, tr=tr, A=A)
            elif rown[0] is None:  # (without a view of its own it shows the depth range of what came before)
                rown[0] = cur["id"]
            st["caption"] = part.get("caption", "")
            emit(n, lambda u: {}, "main", _mark_label(part))
        elif typ == "path":
            try:
                poses = path_poses(part, ds, fr, spec, n, fps, cb)
            except PathPending:
                problems[part.get("id")] = "working out the path ..."
                continue
            except UserError as exc:
                problems[part.get("id")] = str(exc)
                continue
            t = part.get("transition"); t = (CLOSEUP_TRANSITION if st["e"] > 0 else OTHER_TRANSITION) if t is None else float(t)
            k1 = st["k"]; A = overlap(tr, t); start_fade(part)
            need_structures()
            m = len(poses)
            skip = go_to(poses[0], k1, t, tr=tr, nxt=lambda j, poses=poses: dict(pose=poses[int(round(j))]), nn=m, A=A)
            items = [(it.get("structure"), it.get("component")) for it in (part.get("source") or {}).get("items", [])]
            oc, oo = others_of(part.get("others"))
            if (part.get("source") or {}).get("kind", "follow") == "follow" and items and (oc, oo) != ("keep", 1.0):
                st.update(target=make_target(items), others=dict(colour=oc, opacity=oo), e=1.0)
            st["caption"] = part.get("caption", "")
            emit_main(m, lambda u, poses=poses, m=m: dict(pose=poses[int(round(u * (m - 1)))]), _mark_label(part), skip, None)
            pose[0] = poses[-1]
            if st["e"] > 0:
                st.update(e=0.0, target=None)
        elif typ == "card":
            ramp.clear(); last.pop("blend", None)
            fi = max(0, round(fps * float(part.get("fade_in", 0.6) or 0))); fo = max(0, round(fps * float(part.get("fade_out", 0.6) or 0)))
            how = part.get("transition_to_next", "fade")
            fo = 0 if how == "cut" else fo
            pid = part.get("id"); cap = part.get("caption", "")

            def card(u, n=n, fi=fi, fo=fo, how=how, pid=pid, cap=cap):
                i = int(round(u * (n - 1))) if n > 1 else 0
                a = 1.0
                if fi and i < fi:
                    a = (i + 1) / (fi + 1)
                d = dict(card=pid, card_fade=1.0, dissolve=0.0, caption=cap)
                if fo and i >= n - fo:
                    q = (i - (n - fo) + 1) / (fo + 1)
                    if how == "dissolve":
                        d["dissolve"] = q
                    else:
                        a = min(a, 1 - q)
                d["card_fade"] = a
                return d
            emit(n, card, "main", _mark_label(part))
            st.update(card=None, card_fade=1.0, dissolve=0.0, caption="")
    if st["e"] > 0 and spec.get("return_to_overview", True):
        ramp.clear(); cur.update(shown, index=len(shots)); ended.append(True)  # the flight back belongs to the last part shown
        p0, e0, s0, ra = pose[0], st["e"], st["slice"], rown[0]
        emit(round(fps * CLOSEUP_TRANSITION), lambda u: dict(pose=lerp_camera(fr, p0, ov(), smooth01(u)), e=e0 * (1 - smooth01(u)),
                                                               slice=s0 + (base - s0) * smooth01(u), caption="",
                                                               rng=(ra, OVERVIEW, smooth01(u))), "transition")
        st.update(target=None, e=0.0); pose[0] = ov(); rown[0] = OVERVIEW
        emit(max(1, round(fps * 0.5)), lambda u: {}, "pause")
    if not frames:
        cur.update(id=None, index=-1, type=None); rown[0] = OVERVIEW
        if has_seg:
            need_structures()
        emit(1, lambda u: {}, "main")
    for i, f in enumerate(frames):  # a dissolve blends a card with the first frame after it
        if f.get("dissolve"):
            j = i + 1
            while j < len(frames) and frames[j].get("card") == f["card"]:
                j += 1
            f["dissolve_to"] = frames[j] if j < len(frames) else None
    from .overlays import add_bar_lengths
    add_bar_lengths(frames, spec, ds, w, h, fr.fov, fr.ortho)
    from .look import part_style35
    if any(part_style35(spec, part_look(p))["depth_fade"] != "off" for p in shots + [{}]):  # in the movie, or in one part
        depth_ranges(spec, ds, frames, segments, fr, main_box(spec, shape), ov())
    cap_fade = float(((spec.get("overlays") or {}).get("caption") or {}).get("fade") or 0.0)
    if cap_fade > 0:  # captions fade in and out (3.5)
        caption_fades(frames, max(1, int(round(cap_fade * fps))))
    if any(isinstance(p.get("look"), dict) and p["look"] for p in spec.get("shots", [])):
        part_looks(spec, frames, fps)  # effects only in some parts (3.5)
    if spec.get("annotations"):  # arrows and text boxes (3.5): opacity, growth and tail angle per frame
        from .annotations import plan_annotations
        plan_annotations(spec, ds, frames, segments, fr, fps, w / h)
    return Plan(frames, segments, marks, fps, problems)


def left_out(spec, plan):
    """[(part number, reason)] of the parts that are switched on but can't be drawn."""
    return [(i, plan.problems[p["id"]]) for i, p in enumerate(spec.get("shots", []), 1) if p.get("id") in plan.problems]


def build_timeline(spec, ds):
    """Settings -> (list of per-frame states, (frame, label) marks for the scrubber). See build_plan."""
    plan = build_plan(spec, ds)
    return plan.frames, plan.marks


def part_at_frame(plan, frame):
    """Id of the part that owns a frame (a transition belongs to the part it leads into)."""
    for sg in plan.segments:
        if sg["start"] <= frame < sg["start"] + sg["count"]:
            return sg["part"]
    return None


def main_range(plan, part_id):
    """(first frame, number of frames) of a part's main phase, or None when it is not in the movie."""
    for sg in plan.segments:
        if sg["part"] == part_id and sg["phase"] == "main":
            return sg["start"], sg["count"]
    return None


def state_key(s):
    p = s["pose"]
    oth = s["others"] if not isinstance(s["others"], dict) else json.dumps(s["others"], sort_keys=True)
    extra = (json.dumps(s["fx"], sort_keys=True, default=float),) if s.get("fx") else ()  # close-up animations (3.4)
    if s.get("ann"):  # arrows and text boxes (3.5)
        extra += (json.dumps(s["ann"], sort_keys=True),)
    if s.get("blend"):  # dissolves and fades between parts (3.5)
        extra += (s["blend"]["via"], s["blend"]["q"], state_key(dict(s["blend"]["src"], blend=None)))  # (drawn without its own)
    if s.get("dr"):  # the depth range of the depth fade, where it changes (a flight from one part to the next)
        extra += (round(float(s["dr"][0]), 2), round(float(s["dr"][1]), 2))
    if s.get("cap_a") is not None:  # a caption fading in or out (3.5)
        extra += (s["cap_a"],)
    if s.get("lk") or s.get("lkfrom"):  # effects in some parts only (3.5)
        extra += (json.dumps(s.get("lk"), sort_keys=True), json.dumps(s.get("lkfrom"), sort_keys=True), s.get("lkq"))
    if p.get("fov") is not None:  # field of view per point of a camera path (3.5)
        extra += (round(float(p["fov"]), 3),)
    if p.get("pos") is not None:  # camera paths and the 3.5 wiggle give the camera position and up direction themselves
        extra += (tuple(np.round(np.asarray(p["pos"], float), 2)), tuple(np.round(np.asarray(p["up"], float), 4)))
    return extra + (s["k"], s["reveal"], s.get("cut"), round(s["slice"], 3), round(s["e"], 3), json.dumps(s["target"], sort_keys=True), oth,
            s["caption"], tuple(np.round(p["c"], 2)), round(p["tilt"], 3), round(p["az"] % 360.0, 3), round(p["dist"], 2))
