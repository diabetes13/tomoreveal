"""Writing movie files with FFmpeg: frames go through a pipe, in chunks, so an interrupted movie can be continued.

The movie is written as name.partial.000.mp4, name.partial.001.mp4, ... (a new chunk every `chunk_frames` frames).
name.partial.json records the settings and the chunks that are complete. At the end the chunks are joined without
re-encoding (FFmpeg concat demuxer, stream copy) and the result is renamed to the final name. Every chunk starts with
a key frame, so the join is exact.
"""
import hashlib
import json
import os
import subprocess

import numpy as np

CHUNK_FRAMES = 300

# name: (file suffix, extension, pixel format fed to FFmpeg, encoder arguments)
SIZES = {"larger": 18, "balanced": 20, "smaller": 23}
H265_CRF = {"larger": 21, "balanced": 24, "smaller": 27}


def ffmpeg_exe():
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def video_args(fmt="mp4", codec="h264", size="balanced", crf=None, preset="slow"):
    """FFmpeg output arguments for one format. crf overrides the size choice (H.264 / H.265 only)."""
    if fmt == "mov-transparent":
        return ["-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le", "-alpha_bits", "16", "-vendor", "apl0"]
    if fmt == "mov-prores":
        return ["-c:v", "prores_ks", "-profile:v", "3", "-pix_fmt", "yuv422p10le", "-vendor", "apl0"]
    if codec == "h265":
        q = int(crf) if crf is not None else H265_CRF.get(size, 24)
        args = ["-c:v", "libx265", "-pix_fmt", "yuv420p", "-crf", str(q), "-preset", "medium", "-tag:v", "hvc1",
                "-x265-params", "log-level=error"]
    else:
        q = int(crf) if crf is not None else SIZES.get(size, 20)
        args = ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", str(q), "-preset", preset]
    return args + ["-movflags", "+faststart"]


FORMATS = {  # name: (suffix added to the movie name, extension, needs alpha)
    "mp4": ("", ".mp4", False),
    "mov": ("", ".mov", False),
    "mov-transparent": ("_transparent", ".mov", True),
    "mov-prores": ("_prores", ".mov", False),
}


def output_paths(out, formats):
    """{format: final file path} for a movie called `out` (any extension is replaced)."""
    base = os.path.splitext(out)[0]
    return {f: base + FORMATS[f][0] + FORMATS[f][1] for f in formats}


def settings_hash(spec, ds):
    """Fingerprint of what a movie shows: its settings and the data's grid and structures."""
    s = json.dumps(dict(spec=spec, shape=list(ds.shape), tomo=ds.tomo_name, keys=sorted(c["key"] for c in ds.classes.values())),
                   sort_keys=True, default=str)
    return hashlib.sha256(s.encode()).hexdigest()[:16]


def _partial(path, i=None):
    base, ext = os.path.splitext(path)
    return f"{base}.partial{ext}" if i is None else f"{base}.partial.{i:03d}{ext}"


def _state_path(out):
    return os.path.splitext(out)[0] + ".partial.json"


def read_partial(out):
    """The record of an interrupted movie called `out`, or None. Only chunks whose files still exist count."""
    try:
        st = json.load(open(_state_path(out)))
    except (OSError, ValueError):
        return None
    paths = output_paths(out, st.get("formats", ["mp4"]))
    done = 0
    for i in range(int(st.get("chunks_done", 0))):
        if all(os.path.isfile(_partial(p, i)) for p in paths.values()):
            done = i + 1
        else:
            break
    st["chunks_done"] = done; st["frames_done"] = min(int(st.get("total", 0)), done * int(st.get("chunk_frames", CHUNK_FRAMES)))
    return st


def remove_partial(out, formats=None):
    """Delete every partial file of a movie (after a cancel, or before starting again)."""
    st = read_partial(out) or {}
    formats = formats or st.get("formats") or list(FORMATS)
    folder = os.path.dirname(os.path.abspath(out)) or "."
    for p in output_paths(out, formats).values():
        base, ext = os.path.splitext(os.path.basename(p))
        for f in os.listdir(folder):
            if f.startswith(base + ".partial") and (f.endswith(ext) or f.endswith(".txt")):
                try:
                    os.remove(os.path.join(folder, f))
                except OSError:
                    pass
    try:
        os.remove(_state_path(out))
    except OSError:
        pass


class Pipe:
    """One FFmpeg process that reads raw frames from stdin and writes one file."""

    def __init__(self, path, size, fps, args, alpha=False):
        w, h = size
        cmd = [ffmpeg_exe(), "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgba" if alpha else "rgb24",
               "-s", f"{w}x{h}", "-r", str(fps), "-i", "-", "-an", *args, path]
        self.path, self.alpha = path, alpha
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    def write(self, img):
        a = img if self.alpha else img[..., :3]
        try:
            self.proc.stdin.write(np.ascontiguousarray(a, dtype=np.uint8).tobytes())
        except (BrokenPipeError, OSError):
            self.close(); raise RuntimeError(f"the video encoder stopped: {self.error()}")

    def error(self):
        try:
            return (self.proc.stderr.read() or b"").decode(errors="replace").strip()[-400:]
        except Exception:
            return ""

    def close(self):
        if self.proc.stdin and not self.proc.stdin.closed:
            try:
                self.proc.stdin.close()
            except OSError:
                pass
        rc = self.proc.wait()
        if rc != 0:
            raise RuntimeError(f"the video encoder failed: {self.error()}")

    def kill(self):
        try:
            self.proc.kill(); self.proc.wait()
        except Exception:
            pass


def join(chunks, path):
    """Join chunk files into one without re-encoding."""
    lst = path + ".list.txt"
    with open(lst, "w") as f:
        for c in chunks:
            f.write("file '" + os.path.abspath(c).replace("'", "'\\''") + "'\n")
    try:
        r = subprocess.run([ffmpeg_exe(), "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy",
                            "-movflags", "+faststart", path], capture_output=True)
        if r.returncode != 0:
            raise RuntimeError("joining the movie parts failed: " + r.stderr.decode(errors="replace").strip()[-400:])
    finally:
        try:
            os.remove(lst)
        except OSError:
            pass


class ChunkedWriter:
    """Writes the same frames to one file per format, in chunks; `close()` joins them and renames to the final names.

    formats: {format name: FFmpeg arguments}. start_chunk > 0 continues an interrupted movie (the record must match)."""

    def __init__(self, out, formats, size, fps, total, settings, chunk_frames=CHUNK_FRAMES, start_chunk=0):
        self.out, self.formats, self.size, self.fps, self.total = out, dict(formats), tuple(size), int(fps), int(total)
        self.chunk_frames, self.chunk, self.n_in_chunk = int(chunk_frames), int(start_chunk), 0
        self.paths = output_paths(out, self.formats)
        self.record = dict(settings=settings, total=self.total, fps=self.fps, size=list(self.size), chunk_frames=self.chunk_frames,
                           formats=list(self.formats), chunks_done=self.chunk)
        self.pipes = None
        self._save_record()

    def _save_record(self):
        tmp = _state_path(self.out) + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.record, f, indent=1)
        os.replace(tmp, _state_path(self.out))

    def _open(self):
        self.pipes = {f: Pipe(_partial(p, self.chunk), self.size, self.fps, self.formats[f], alpha=FORMATS[f][2])
                      for f, p in self.paths.items()}
        self.n_in_chunk = 0

    def _close_chunk(self):
        for p in self.pipes.values():
            p.close()
        self.pipes = None; self.chunk += 1
        self.record["chunks_done"] = self.chunk; self._save_record()

    def write(self, img):
        if self.pipes is None:
            self._open()
        for p in self.pipes.values():
            p.write(img)
        self.n_in_chunk += 1
        if self.n_in_chunk >= self.chunk_frames:
            self._close_chunk()

    def close(self):
        """Finish: join the chunks of every format and rename them to the final names."""
        if self.pipes is not None:
            self._close_chunk()
        for f, path in self.paths.items():
            chunks = [_partial(path, i) for i in range(self.chunk)]
            tmp = _partial(path)
            if len(chunks) == 1:
                os.replace(chunks[0], tmp)
            else:
                join(chunks, tmp)
            os.replace(tmp, path)
        remove_partial(self.out, list(self.formats))

    def kill(self):
        """Stop at once and leave the files as they are (what a crash leaves behind; used by tests)."""
        if self.pipes is not None:
            for p in self.pipes.values():
                p.kill()
            self.pipes = None

    def abort(self):
        """Stop and delete every partial file."""
        self.kill(); remove_partial(self.out, list(self.formats))


def count_frames(path):
    """Number of video frames in a file (decodes it)."""
    import imageio_ffmpeg
    return imageio_ffmpeg.count_frames_and_secs(path)[0]
