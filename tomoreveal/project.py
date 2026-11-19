"""Project files (.tomoreveal): where the data files are, what is switched on, and every movie setting.

A project stores paths, never data. It is UTF-8 JSON, indented. `schema` is an integer; `migrate_project` upgrades
older files step by step and refuses newer ones. Unknown keys are kept and written back unchanged.
"""
import copy
import datetime
import hashlib
import json
import os

from .common import UserError, __version__

FORMAT = "tomoreveal-project"
SCHEMA = 1
EXT = ".tomoreveal"
KEY_PREFIXES = ("file:", "session:", "dragonfly:", "labels:", "scores:")


# ---------------------------------------------------------------------------------------------------------------
# files on disk
# ---------------------------------------------------------------------------------------------------------------
def fingerprint(path, chunk=1 << 20):
    """sha256 of the first and last MiB and the size: quick, and changes when the file is replaced or edited."""
    size = os.path.getsize(path); h = hashlib.sha256(str(size).encode())
    with open(path, "rb") as f:
        h.update(f.read(chunk))
        if size > chunk:
            f.seek(max(chunk, size - chunk)); h.update(f.read(chunk))
    return h.hexdigest()


def file_entry(key, kind, path, project_path=None, **extra):
    """One source of a project: the absolute path, the path relative to the project file, size, time and fingerprint."""
    d = dict(key=key, kind=kind, path=path, relpath=None, size=0, mtime=0.0, fingerprint="")
    if project_path:
        try:
            d["relpath"] = os.path.relpath(path, os.path.dirname(os.path.abspath(project_path)))
        except ValueError:  # another drive (Windows)
            d["relpath"] = None
    try:
        d.update(size=os.path.getsize(path), mtime=os.path.getmtime(path), fingerprint=fingerprint(path))
    except OSError:
        pass
    d.update(extra)
    return d


def write_atomic(path, data):
    """Write JSON so that a crash never leaves a half-written file: write name.tmp, flush, fsync, replace.
    The previous version is kept as name.bak."""
    text = json.dumps(data, indent=2, ensure_ascii=False, default=float)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text); f.flush(); os.fsync(f.fileno())
        if os.path.exists(path):
            try:
                with open(path, "rb") as a, open(path + ".bak", "wb") as b:
                    b.write(a.read())
            except OSError:
                pass
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def migrate_project(data):
    """Bring a project (or older plain settings) up to the current schema; refuse newer schemas with a plain message."""
    if not isinstance(data, dict):
        raise UserError("This is not a TomoReveal project file.")
    if "schema" not in data:  # schema 0: settings of 3.3 or earlier written as plain JSON (the spec itself, or {"spec": ...})
        spec = data.get("spec") if isinstance(data.get("spec"), dict) else data
        if not isinstance(spec, dict) or not any(k in spec for k in ("shots", "classes", "style")):
            raise UserError("This is not a TomoReveal project file.")
        data = dict(format=FORMAT, schema=1, app_version=data.get("app_version", "3.3"), sources=list(data.get("sources", [])),
                    tomogram_source=data.get("tomogram_source"), segmentation_enabled=dict(data.get("segmentation_enabled", {})),
                    spec=spec, ui=dict(data.get("ui", {})))
    s = data.get("schema")
    if not isinstance(s, int):
        raise UserError("This project file is damaged (no schema number).")
    if s > SCHEMA:
        raise UserError(f"This project was saved by a newer TomoReveal (schema {s}). Please update TomoReveal.")
    # future: `if s == 1: ...; s = 2` steps go here
    data.setdefault("format", FORMAT); data.setdefault("sources", []); data.setdefault("segmentation_enabled", {})
    data.setdefault("tomogram_source", None); data.setdefault("spec", None); data.setdefault("ui", {})
    return data


def read_project(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        raise UserError(f"Project file not found: {path}")
    except (OSError, ValueError) as exc:
        raise UserError(f"Cannot read the project file {os.path.basename(path)}: {exc}")
    return migrate_project(data)


# ---------------------------------------------------------------------------------------------------------------
# what is loaded <-> project
# ---------------------------------------------------------------------------------------------------------------
def studio_sources(st, project_path=None, fingerprints=True):
    """The files loaded in a Studio, as project sources (one per file)."""
    out = []

    def entry(key, kind, path, **extra):
        if fingerprints:
            return file_entry(key, kind, path, project_path, **extra)
        return dict(key=key, kind=kind, path=path, **extra)
    for p in st.tomo_files:
        out.append(entry("file:" + p, "tomogram", p))
    for p, ses in st.sessions.items():
        objs = [s["title"] for s in st.seg_sources if s["kind"] == "dragonfly" and s["path"] == p]
        out.append(entry("session:" + p, "dragonfly", p, objects=objs))
    for s in st.seg_sources:
        if s["kind"] == "labels":
            out.append(entry(s["key"], "labels", s["path"]))
        elif s["kind"] == "scores":
            out.append(entry(s["key"], "scores", s["path"], threshold=float(s.get("thr", st.default_thr))))
    return out


def card_pictures(spec):
    """Paths of the pictures used on cards (backgrounds and picture blocks)."""
    out = []
    for p in (spec or {}).get("shots", []):
        if p.get("type") != "card":
            continue
        bg = p.get("background") or {}
        if bg.get("kind") == "image" and bg.get("image"):
            out.append(bg["image"])
        out += [b["path"] for b in p.get("blocks", []) if b.get("kind") == "image" and b.get("path")]
    return list(dict.fromkeys(out))


def make_project(st, spec, ui=None, project_path=None, fingerprints=True, extra=None):
    """Project data for what a Studio has loaded plus the movie settings. `extra`: unknown keys read earlier, kept."""
    data = dict(extra or {})
    data.update(format=FORMAT, schema=SCHEMA, app_version=__version__,
                saved=datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
                sources=studio_sources(st, project_path, fingerprints) + [
                    (file_entry if fingerprints else (lambda k, kind, p, pp: dict(key=k, kind=kind, path=p)))("image:" + p, "image", p, project_path)
                    for p in card_pictures(spec)],
                tomogram_source=st.tomo_source,
                tomogram_auto=bool(st._tomo_auto),
                segmentation_enabled={s["key"]: bool(s["enabled"]) for s in st.seg_sources},
                spec=copy.deepcopy(spec), ui=dict(ui or {}))
    data["spec"] = json.loads(json.dumps(data["spec"], default=float)) if data["spec"] is not None else None
    return data


def core(data):
    """The parts of a project that make it 'changed' (not the time saved or the window state)."""
    srcs = [(s.get("key"), s.get("threshold"), tuple(s.get("objects") or ())) for s in data.get("sources", [])]
    return json.dumps(dict(sources=srcs, t=data.get("tomogram_source"), seg=data.get("segmentation_enabled"), spec=data.get("spec")),
                      sort_keys=True, default=float)


# ---------------------------------------------------------------------------------------------------------------
# finding the files again
# ---------------------------------------------------------------------------------------------------------------
def _search(name, folder, depth=2):
    """A file called `name` in folder and its subfolders (depth levels)."""
    if not folder or not os.path.isdir(folder):
        return None
    cand = os.path.join(folder, name)
    if os.path.isfile(cand):
        return cand
    if depth <= 0:
        return None
    try:
        subs = sorted(e.path for e in os.scandir(folder) if e.is_dir() and not e.name.startswith("."))
    except OSError:
        return None
    for d in subs:
        r = _search(name, d, depth - 1)
        if r:
            return r
    return None


def locate(entry, project_path=None):
    """Where a source is now: (path or None, status) with status "ok", "changed" (size or fingerprint differ) or "missing".
    Tried in order: the absolute path, the path relative to the project file, the same name near the project file."""
    folder = os.path.dirname(os.path.abspath(project_path)) if project_path else None
    cands = [entry.get("path")]
    if folder and entry.get("relpath"):
        cands.append(os.path.normpath(os.path.join(folder, entry["relpath"])))
    found = next((c for c in cands if c and os.path.isfile(c)), None)
    if found is None and folder and entry.get("path"):
        found = _search(os.path.basename(entry["path"]), folder)
    if found is None:
        return None, "missing"
    return found, check(entry, found)


def check(entry, path):
    """"ok" when the file at path is the one the project saw, else "changed"."""
    try:
        if entry.get("size") and os.path.getsize(path) != entry["size"]:
            return "changed"
        if entry.get("fingerprint") and fingerprint(path) != entry["fingerprint"]:
            return "changed"
    except OSError:
        return "changed"
    return "ok"


def resolve_sources(data, project_path=None):
    """For each source: dict(entry, path (found or None), status). Nothing is loaded here."""
    return [dict(entry=e, path=p, status=s) for e in data.get("sources", []) for p, s in [locate(e, project_path)]]


def path_mapping(resolved):
    """{old path: new path} for sources that were found somewhere else."""
    return {r["entry"]["path"]: os.path.abspath(r["path"]) for r in resolved
            if r.get("path") and r["entry"].get("path") and os.path.abspath(r["path"]) != r["entry"]["path"]}


def remap(obj, mapping):
    """Rewrite source and structure keys (they contain absolute paths) after files were found elsewhere."""
    if not mapping:
        return obj

    def fix(s):
        if isinstance(s, str) and s.startswith(KEY_PREFIXES):
            pre, rest = s.split(":", 1)
            for old, new in mapping.items():
                if rest == old or rest.startswith(old + ":") or rest.startswith(old + "#"):
                    return f"{pre}:{new}{rest[len(old):]}"
        return s
    if isinstance(obj, dict):
        return {fix(k): remap(v, mapping) for k, v in obj.items()}
    if isinstance(obj, list):
        return [remap(v, mapping) for v in obj]
    return fix(obj)


def load_into(st, data, resolved, log=print):
    """Load the found sources of a project into an empty Studio and switch on what was switched on.
    resolved: resolve_sources() after the user's choices; entries with path None are skipped. Returns st.info()."""
    mapping = path_mapping(resolved)
    use = [r for r in resolved if r.get("path") and r["entry"].get("kind") != "image"]
    paths = [os.path.abspath(r["path"]) for r in use]
    kinds = {os.path.abspath(r["path"]): r["entry"].get("kind") for r in use}
    st._register(paths, as_features=[p for p in paths if kinds[p] == "scores"],
                 as_segmentation=[p for p in paths if kinds[p] == "labels"],
                 as_tomogram=[p for p in paths if kinds[p] == "tomogram"], log=log)
    for r in use:
        if r["entry"].get("kind") == "scores" and r["entry"].get("threshold") is not None:
            s = st._source("scores:" + os.path.abspath(r["path"]))
            if s is not None:
                s["thr"] = float(r["entry"]["threshold"])
    enabled = remap(data.get("segmentation_enabled") or {}, mapping)
    for s in st.seg_sources:
        if s["key"] in enabled:
            s["enabled"] = bool(enabled[s["key"]])
    t = remap(data.get("tomogram_source"), mapping)
    if not data.get("tomogram_auto", False) and t in [c["key"] for c in st._tomogram_choices()]:
        st.tomo_source = t; st._tomo_auto = False
    return st._update(log, make_renderer=False)


def project_spec(data, resolved):
    """The project's movie settings with keys rewritten for files found elsewhere."""
    if not data.get("spec"):
        return None
    m = path_mapping(resolved); sp = remap(copy.deepcopy(data["spec"]), m)
    for p in sp.get("shots", []):  # pictures on cards that were found somewhere else
        if p.get("type") == "card":
            bg = p.get("background") or {}
            if bg.get("image") in m:
                bg["image"] = m[bg["image"]]
            for b in p.get("blocks", []):
                if b.get("path") in m:
                    b["path"] = m[b["path"]]
    return sp


# ---------------------------------------------------------------------------------------------------------------
# autosave, logs
# ---------------------------------------------------------------------------------------------------------------
def data_dir():
    """TomoReveal's own folder for autosaves and logs (~/.local/share/TomoReveal on Linux).
    TOMOREVEAL_DATA_DIR overrides it (the tests use a temporary folder)."""
    env = os.environ.get("TOMOREVEAL_DATA_DIR")
    if env:
        return env
    try:
        from PySide6 import QtCore
        p = QtCore.QStandardPaths.writableLocation(QtCore.QStandardPaths.AppDataLocation)
        if p:
            return p
    except ImportError:
        pass
    return os.path.join(os.path.expanduser("~"), ".local", "share", "TomoReveal")


def autosave_dir():
    d = os.path.join(data_dir(), "autosave"); os.makedirs(d, exist_ok=True)
    return d


def logs_dir():
    d = os.path.join(data_dir(), "logs"); os.makedirs(d, exist_ok=True)
    return d


def pid_alive(pid):
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        k = ctypes.windll.kernel32; h = k.OpenProcess(0x1000, False, pid)
        if not h:
            return False
        code = ctypes.c_ulong(); ok = k.GetExitCodeProcess(h, ctypes.byref(code)); k.CloseHandle(h)
        return bool(ok) and code.value == 259
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


class Autosave:
    """The autosave of one window: autosave/<name>.tomoreveal and its lock file <name>.lock (holding the process id)."""

    def __init__(self, name=None):
        self.pid = os.getpid(); name = str(name or self.pid); d = autosave_dir()
        self.path = os.path.join(d, f"{name}{EXT}"); self.lock = os.path.join(d, f"{name}.lock")
        with open(self.lock, "w") as f:
            json.dump(dict(pid=self.pid, started=datetime.datetime.now().astimezone().isoformat(timespec="seconds")), f)

    def write(self, data, project_path=None):
        d = dict(data); d["autosave_of"] = project_path
        write_atomic(self.path, d)

    def remove(self):
        """Nothing to keep (everything was unloaded): the autosave goes, the lock stays while the window is open."""
        for p in (self.path, self.path + ".bak"):
            try:
                os.remove(p)
            except OSError:
                pass

    def clear(self):
        """A clean exit: the autosave and the lock go."""
        for p in (self.path, self.path + ".bak", self.lock):
            try:
                os.remove(p)
            except OSError:
                pass


def orphaned_autosaves():
    """Autosaves left by a TomoReveal that did not close cleanly (its process is gone): [(path, time saved)], newest first."""
    d = autosave_dir(); out = []
    for f in os.listdir(d):
        if not f.endswith(EXT):
            continue
        p = os.path.join(d, f); lock = p[:-len(EXT)] + ".lock"
        try:
            alive = pid_alive(json.load(open(lock)).get("pid"))
        except (OSError, ValueError, AttributeError):
            alive = False
        if not alive:
            out.append((p, os.path.getmtime(p)))
    return sorted(out, key=lambda x: -x[1])


def discard_autosave(path):
    base = path[:-len(EXT)]
    for p in (path, path + ".bak", base + ".lock"):
        try:
            os.remove(p)
        except OSError:
            pass


def keep_last_logs(n=20):
    try:
        logs = sorted((os.path.join(logs_dir(), f) for f in os.listdir(logs_dir()) if f.endswith(".log")), key=os.path.getmtime)
        for p in logs[:-n]:
            os.remove(p)
    except OSError:
        pass
