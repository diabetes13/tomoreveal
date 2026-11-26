"""Project files (.tomoreveal): where the data files are, what is switched on, and every movie setting.

A project stores paths, never data. It is UTF-8 JSON, indented. `schema` is an integer; `migrate_project` upgrades
older files step by step and refuses newer ones. Unknown keys are kept and written back unchanged.
"""
import copy
import datetime
import hashlib
import json
import os

from .common import UserError, __version__, norm_path

FORMAT = "tomoreveal-project"
SCHEMA = 2
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


def file_entry(key, kind, path, project_path=None, fingerprints=True, **extra):
    """One source of a project: the absolute path, the path relative to the project file, size, time and (it reads a part
    of the file) fingerprint.
    A file that is not there now keeps what `extra` (its earlier entry) knows about it."""
    d = dict(key=key, kind=kind, path=path, relpath=None, size=0, mtime=0.0, fingerprint=""); d.update(extra)
    if project_path and (d["relpath"] is None or os.path.exists(path)):
        try:
            d["relpath"] = os.path.relpath(path, os.path.dirname(os.path.abspath(project_path)))
        except ValueError:  # another drive (Windows)
            d["relpath"] = None
    try:
        d.update(size=os.path.getsize(path), mtime=os.path.getmtime(path))
        if fingerprints:
            d["fingerprint"] = fingerprint(path)
    except OSError:
        pass
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
    if s == 1:  # 3.5: say so explicitly: projects saved before 3.5 keep the Classic look (new projects start Soft)
        sp = data.get("spec")
        if isinstance(sp, dict):
            sp.setdefault("look", {}).setdefault("style35", {"preset": "classic"})
        data["schema"] = s = 2
    # future: `if s == 2: ...; s = 3` steps go here
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
def source_state(st):
    """What a Studio has loaded (one source per file) and what is switched on, as project data. No file is touched."""
    out = []

    def entry(key, kind, path, **extra):
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
    return dict(sources=out, tomogram_source=st.tomo_source, tomogram_auto=bool(st._tomo_auto),
                segmentation_enabled={s["key"]: bool(s["enabled"]) for s in st.seg_sources})


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


def make_project(state, spec, ui=None, extra=None):
    """Project data for what is loaded (source_state) plus the movie settings, with plain entries (see with_file_facts).
    `extra`: unknown keys read earlier, kept. The thresholds of score volumes are those of the settings (the data may
    follow a moment later); the entries have them too, for 3.5.0."""
    data = dict(extra or {}); pictures = [dict(key="image:" + p, kind="image", path=p) for p in card_pictures(spec)]
    sources = [dict(e) for e in state["sources"]]
    for e in sources:
        thr = ((spec or {}).get("classes") or {}).get(f"{e['key']}#1", {}).get("threshold") if e["kind"] == "scores" else None
        if isinstance(thr, (int, float)):
            e["threshold"] = float(thr)
    data.update(state, format=FORMAT, schema=SCHEMA, app_version=__version__,
                saved=datetime.datetime.now().astimezone().isoformat(timespec="seconds"), sources=sources + pictures,
                spec=json.loads(json.dumps(spec, default=float)), ui=dict(ui or {}))
    return data


def with_file_facts(data, project_path=None, fingerprints=True):
    """The same project data; each source also has what finds its file again and tells whether it is still the same
    (fingerprints=False: size and time only, for autosaves)."""
    return dict(data, sources=[file_entry(project_path=project_path, fingerprints=fingerprints, **e) for e in data["sources"]])


def saved_state(data, resolved):
    """The sources and what was switched on as a project file has them (skipped files too), for make_project. Files found
    elsewhere are where they are now."""
    m = path_mapping(resolved)
    state = remap({k: data.get(k) for k in ("sources", "tomogram_source", "tomogram_auto", "segmentation_enabled")}, m)
    state["sources"] = [dict(e, path=m.get(e["path"], e["path"])) for e in state["sources"] if e.get("kind") != "image"]
    return state


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
        if found and not any(entry.get(k) for k in ("size", "mtime", "fingerprint")):
            return found, "changed"  # only the name is the same: the user decides
    if found is None:
        return None, "missing"
    return found, check(entry, found)


def check(entry, path):
    """"ok" when the file at path is the one the project saw, else "changed". The time counts only without a fingerprint."""
    try:
        if entry.get("size") and os.path.getsize(path) != entry["size"]:
            return "changed"
        if entry.get("fingerprint"):
            return "ok" if fingerprint(path) == entry["fingerprint"] else "changed"
        if entry.get("mtime") and abs(os.path.getmtime(path) - entry["mtime"]) > 0.01:
            return "changed"
    except OSError:
        return "changed"
    return "ok"


def resolve_sources(data, project_path=None):
    """For each source: dict(entry, path (found or None), status). Nothing is loaded here."""
    return [dict(entry=e, path=p, status=s) for e in data.get("sources", []) for p, s in [locate(e, project_path)]]


def path_mapping(resolved):
    """{old path: new path, as in Studio's source keys} for sources that were found somewhere else."""
    return {r["entry"]["path"]: norm_path(r["path"]) for r in resolved
            if r.get("path") and r["entry"].get("path") and norm_path(r["path"]) != r["entry"]["path"]}


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
    resolved: resolve_sources() after the user's choices; entries with path None are skipped. Returns st.info().
    When a file can't be read, the Studio is left empty."""
    with st._rollback():
        return _load_into(st, data, resolved, log)


def _load_into(st, data, resolved, log):
    mapping = path_mapping(resolved)
    use = [r for r in resolved if r.get("path") and r["entry"].get("kind") != "image"]
    paths = [st._norm(r["path"]) for r in use]  # the same spelling as Studio's source keys
    kinds = {st._norm(r["path"]): r["entry"].get("kind") for r in use}
    st._register(paths, as_features=[p for p in paths if kinds[p] == "scores"],
                 as_labels=[p for p in paths if kinds[p] == "labels"],  # saved as labels: never guessed again
                 as_tomogram=[p for p in paths if kinds[p] == "tomogram"], log=log)
    for r in use:
        if r["entry"].get("kind") == "scores" and r["entry"].get("threshold") is not None:
            s = st._source("scores:" + st._norm(r["path"]))
            if s is not None:
                s["thr"] = float(r["entry"]["threshold"])
    for key, c in (remap((data.get("spec") or {}).get("classes") or {}, mapping)).items():  # surfaces built as saved
        kw = {n: c[n] for n in ("min_object_voxels", "smoothing", "soften") if c.get(n) is not None}
        if kw:
            st.ds.params = {**st.ds.params, key: kw}
    enabled = remap(data.get("segmentation_enabled") or {}, mapping)
    for s in st.seg_sources:
        if s["key"] in enabled:
            s["enabled"] = bool(enabled[s["key"]])
    st.flip = tuple(a for a in "xyz" if a in ((data.get("spec") or {}).get("flip") or ()))  # shown flipped (3.5)
    t = remap(data.get("tomogram_source"), mapping)
    if not data.get("tomogram_auto", False) and t in [c["key"] for c in st._tomogram_choices()]:
        st.tomo_source = t; st._tomo_auto = False
    return st._update(log, make_renderer=False)


def project_spec(data, resolved):
    """The project's movie settings with keys rewritten for files found elsewhere. The threshold in the entry of a score
    file wins over the one in the settings: 3.5.0 changes only the entry."""
    if not data.get("spec"):
        return None
    sp = copy.deepcopy(data["spec"])
    for e in data.get("sources") or []:
        c = (sp.get("classes") or {}).get(f"{e.get('key')}#1") if e.get("kind") == "scores" else None
        if c is not None and e.get("threshold") is not None:
            c["threshold"] = float(e["threshold"])
    m = path_mapping(resolved); sp = remap(sp, m)
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
