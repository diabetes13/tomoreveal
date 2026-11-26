"""Making a movie: one frame state at a time through the Renderer into the FFmpeg pipes (MovieJob, so the window
stays responsive), the poster, and motion blur (3.5)."""
import os
import re

import numpy as np

from .common import UserError, hex2rgb
from .paths import missing_paths, work_out
from .storyboard import build_plan, clip_box, state_key
from .transitions import lerp_pose


def poster_path(out):
    return os.path.splitext(out)[0] + "_poster.png"


def poster_partial(out):
    """The poster of a movie that is not finished yet; it replaces the old poster only when the movie is done."""
    return os.path.splitext(out)[0] + "_poster.partial.png"


def _discard(*paths):
    for p in paths:
        try:
            os.remove(p)
        except OSError:
            pass


BLUR_SAMPLES, BLUR_SHUTTER = 4, 0.5  # motion blur: 4 pictures spread over half of the frame interval


def moving(a, b):
    """True when the camera moves between two frame states (motion blur is only worth its cost then)."""
    p, q = a["pose"], b["pose"]
    return (not np.allclose(p["c"], q["c"]) or abs(p["tilt"] - q["tilt"]) > 1e-6 or abs(((p["az"] - q["az"] + 180) % 360) - 180) > 1e-6
            or abs(p["dist"] - q["dist"]) > 1e-6
            or (p.get("pos") is not None and q.get("pos") is not None and not np.allclose(p["pos"], q["pos"])))


def blur_towards(s, nxt):
    """Motion blur only where the camera flies to the next frame: not from or towards a card or a blend, and not across a
    hard cut (to another part that does not start with a transition)."""
    if s.get("card") or s.get("blend") or nxt.get("card") or nxt.get("blend"):
        return False
    return (nxt.get("part") == s.get("part") or nxt.get("phase") == "transition") and moving(s, nxt)


def motion_blurred(ren, s, nxt, spec, size, aa, transparent):
    """The average of BLUR_SAMPLES pictures with the camera between this frame and the next (3.5)."""
    acc = None; fov = float(spec.get("style", {}).get("fov", 30))
    for k in range(BLUR_SAMPLES):
        u = BLUR_SHUTTER * k / BLUR_SAMPLES
        pose = lerp_pose(s["pose"], nxt["pose"], u, fov=fov)
        if s["pose"].get("pos") is not None and nxt["pose"].get("pos") is not None:
            pose["pos"] = np.asarray(s["pose"]["pos"]) * (1 - u) + np.asarray(nxt["pose"]["pos"]) * u
            up = np.asarray(s["pose"]["up"]) * (1 - u) + np.asarray(nxt["pose"]["up"]) * u; pose["up"] = up / (np.linalg.norm(up) or 1.0)
        img = ren.draw(dict(s, pose=pose), spec, size, aa=aa, transparent=transparent).astype(np.float32)
        acc = img if acc is None else acc + img
    return np.clip(np.round(acc / BLUR_SAMPLES), 0, 255).astype(np.uint8)


class MovieJob:
    """Renders a movie frame by frame: iterate it to get (frames done, total, last image). Closing it before the end
    cancels the movie and deletes its partial files. At the end, paths, poster and note say where the files went. With resume=True, the frames of the complete chunks of an
    interrupted movie with the same settings are kept and only the rest is rendered (see encode.py). plan: the plan of these
    settings, when it is there already."""

    def __init__(self, ren, spec, out, frames_dir=None, transparent=False, poster=True, resume=False, chunk_frames=None, plan=None):
        from . import encode
        self.ren, self.spec, self.out, self.frames_dir, self.transparent = ren, spec, out, frames_dir, transparent
        for items in missing_paths(spec, ren.ds):  # (the window's plans leave a part out until its path is there)
            work_out(ren.ds, items, clip_box(spec, ren.ds.shape)); plan = None
        self.frames = (plan or build_plan(spec, ren.ds)).frames
        o = spec.get("output", {}); size = o.get("size", [1920, 1080]); self.fps = int(o.get("fps", 30))
        self.size = (int(size[0]) // 2 * 2, int(size[1]) // 2 * 2)
        from .look import look_settings
        self.aa = look_settings(spec)["aa"]["movie"]
        self.blur = bool(o.get("motion_blur", False))
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        if frames_dir:
            os.makedirs(frames_dir, exist_ok=True)
            beyond = sorted(f for f in os.listdir(frames_dir) if re.fullmatch(r"frame_\d{5}\.png", f) and int(f[6:11]) >= len(self.frames))
            if beyond:  # lower numbers are overwritten by this run; these would stay mixed in
                raise UserError(f"{frames_dir} already holds frames up to {beyond[-1]} from a longer movie; "
                                f"this one has {len(self.frames)} frames. Choose an empty folder, or remove the old frames.")
        formats = encode.output_settings(o)
        self.alpha = any(encode.FORMATS[f][2] for f in formats)  # a transparent MOV: frames are drawn with alpha
        self.paths = encode.output_paths(out, formats)
        self.settings = encode.settings_hash(spec, ren.ds)
        chunk = int(chunk_frames or encode.CHUNK_FRAMES); start_chunk = 0
        old = encode.read_partial(out)
        if resume and old and old.get("settings") == self.settings and old.get("formats") == list(formats) \
                and int(old.get("chunk_frames", 0)) == chunk and old["chunks_done"] > 0:
            start_chunk = old["chunks_done"]
        else:
            encode.remove_partial(out); _discard(poster_partial(out))
        self.start = min(len(self.frames), start_chunk * chunk)
        bg = [255 * v for v in hex2rgb(spec.get("style", {}).get("bg", "#000000"))]
        self.writer = encode.ChunkedWriter(out, formats, self.size, self.fps, len(self.frames), self.settings, chunk, start_chunk, bg=bg)
        # poster: a frame from the main phase of a slide that shows structures (else an overview), never a close-up
        fr = self.frames
        idx = [i for i, s in enumerate(fr) if s.get("type") == "slide" and s["reveal"] and s["phase"] == "main" and s["e"] == 0] \
            or [i for i, s in enumerate(fr) if s.get("type") == "overview" and s["phase"] == "main" and s["e"] == 0]
        self.poster_i = idx[len(idx) // 4] if (idx and poster) else -1
        self.i, self.last_key, self.img, self.finished, self.closed = self.start, None, None, False, False
        self.poster, self.note = None, ""

    def __iter__(self):
        return self

    def __len__(self):
        return len(self.frames)

    def __next__(self):
        import imageio.v2 as imageio
        from . import encode
        if self.closed:
            raise StopIteration
        if self.i >= len(self.frames):
            self._finish()
            raise StopIteration
        # an error leaves the files as they are: the caller closes the job, and decides whether the chunks stay
        i, s = self.i, self.frames[self.i]
        key = (state_key(s), s.get("part"), s.get("bar_nm"), s.get("card"), round(float(s.get("card_fade", 1)), 4),
               round(float(s.get("dissolve", 0) or 0), 4))  # overlays may differ per part
        tr = self.alpha or (self.transparent and bool(self.frames_dir))
        nxt = self.frames[i + 1] if (self.blur and i + 1 < len(self.frames)) else None
        if nxt is not None and blur_towards(s, nxt):
            self.img = motion_blurred(self.ren, s, nxt, self.spec, self.size, self.aa, tr); self.last_key = None
        elif key != self.last_key:
            self.img = self.ren.draw(s, self.spec, self.size, aa=self.aa, transparent=tr)
            self.last_key = key
        if self.frames_dir:  # one PNG per frame, also while the picture holds still (pauses stay in the sequence)
            imageio.imwrite(os.path.join(self.frames_dir, f"frame_{i:05d}.png"), self.img)
        if i == self.poster_i:
            rgb = encode.composite(self.img, self.writer.bg) if self.img.shape[2] == 4 else self.img
            imageio.imwrite(poster_partial(self.out), rgb)
        self.writer.write(self.img)
        self.i += 1
        return self.i, len(self.frames), self.img

    def _finish(self):
        """Save the movie, then its poster: self.paths and self.poster are where they went, self.note says why when that is
        not where they were asked for (see ChunkedWriter.close)."""
        from .encode import free_new_name
        asked = next(iter(self.paths.values())); self.writer.close(); self.finished = self.closed = True
        self.paths = dict(self.writer.paths); notes = list(self.writer.notes); pp = poster_partial(self.out)
        first = next(iter(self.paths.values()))
        if os.path.exists(pp):  # the old poster is replaced only now, with the finished movie; it goes with the first format
            self.poster = poster_path(first)
            try:
                os.replace(pp, self.poster)
            except OSError:
                self.poster = poster_path(free_new_name(asked, lambda p: os.path.exists(poster_path(p))))
                try:
                    os.replace(pp, self.poster)
                    notes.append(f"Could not replace {os.path.basename(poster_path(first))} (is it open in another program?), "
                                 f"so the new poster is saved as {os.path.basename(self.poster)}.")
                except OSError as exc:
                    _discard(pp); self.poster = None; notes.append(f"The poster could not be saved: {exc}")
        self.note = " ".join(notes)

    def close(self, keep=False):
        """Stop. Unless the movie is finished, its partial files are deleted; with keep, its complete chunks and the new
        poster stay, as a crash leaves them, so that it can be continued (resume=True). Returns how many frames are kept."""
        if self.closed:
            return 0
        w = self.writer; self.closed = True; kept = min(w.total, w.chunk * w.chunk_frames) if keep else 0
        if kept:
            w.kill()
        else:  # (also when no chunk is complete)
            w.abort(); _discard(poster_partial(self.out))
        return kept


def movie_frames(ren, spec, out, frames_dir=None, transparent=False, poster=True, resume=False, chunk_frames=None, plan=None):
    """A MovieJob: iterate it to render the movie; yields (frames done, total, last image). close() cancels."""
    return MovieJob(ren, spec, out, frames_dir, transparent, poster, resume, chunk_frames, plan)


def render_movie(ren, spec, out, frames_dir=None, transparent=False, progress=None, cancelled=None, poster=True, resume=False):
    fps = int(spec.get("output", {}).get("fps", 30)); n = 0
    gen = movie_frames(ren, spec, out, frames_dir, transparent, poster, resume); n = len(gen)
    try:
        for i, n, img in gen:
            if progress:
                progress(i, n, img)
            if cancelled and cancelled():
                gen.close()
                return None
    except BaseException as exc:  # this can't continue a movie: the chunks stay only when every frame was drawn (3-D4)
        gen.close(keep=gen.i >= n and not isinstance(exc, KeyboardInterrupt))
        raise
    if gen.note:  # saved, but not (all) where it was asked for
        raise UserError(gen.note)
    return n, n / fps
