"""Reader for Dragonfly .ORSSession files (multi-ROIs, ROIs and stored images)."""
import contextlib
import functools
import os
import re
import struct
import xml.etree.ElementTree as ET

import numpy as np

from .common import PALETTE, UserError, ZMAGIC, label_dtype

# what a cut-off, overwritten or otherwise unexpected file raises while it is parsed
_PARSE_ERRORS = (struct.error, KeyError, IndexError, TypeError, ValueError, AttributeError, ZeroDivisionError, OverflowError,
                 ET.ParseError)


@contextlib.contextmanager
def _damaged(path):
    """Turns parse errors into one clear UserError; a UserError passes through unchanged."""
    try:
        yield
    except UserError:
        raise
    except Exception as exc:
        if not isinstance(exc, _PARSE_ERRORS) and type(exc).__module__.split(".")[0] != "zstandard":
            raise
        raise UserError(f"{os.path.basename(path)} looks damaged and cannot be read ({type(exc).__name__}: {exc}). "
                        "Save it again in Dragonfly, or export the ROI as a TIFF stack and use that instead.") from exc


def _checked(method):
    @functools.wraps(method)
    def run(self, *a, **k):
        with _damaged(self.path):
            return method(self, *a, **k)
    return run


class OrsSession:
    def __init__(self, path):
        with _damaged(path):
            self._open_session(path)

    def _open_session(self, path):
        try:
            import zstandard
        except ImportError:
            raise UserError("reading .ORSSession needs the 'zstandard' package")
        if not os.path.isfile(path):
            raise UserError(f"file not found: {path}")
        self._zstd = zstandard
        self.dctx = zstandard.ZstdDecompressor()
        self.path = path
        # No file stays open between reads, so Dragonfly can save over the session while it is loaded here.
        with open(path, "rb") as f:
            st = os.fstat(f.fileno()); self._stamp = (st.st_ino, st.st_size, st.st_mtime_ns)
            head = f.read(20)
            if head[:4] == b"ORSZ":  # Dragonfly 2022: data records first, then the XML index pointing at them
                f.seek(struct.unpack_from("<Q", head, 4)[0]); xml = f.read().decode("utf-8", "replace")
            elif head[:8] == b"CAIFZ\0\0\0" and len(head) == 20:  # Dragonfly 2025: XML first, then the records in order
                xml = self._caifz_index(f, st.st_size, struct.unpack_from("<Q", head, 12)[0])
            else:
                raise UserError(f"{os.path.basename(path)} is not a compressed Dragonfly session (no 'ORSZ' or 'CAIFZ' "
                                "header). Export the ROI from Dragonfly as a TIFF stack and use that instead.")
            xml = re.sub(r"<(/?)([\w.\-]+):([\w.\-]+)", r"<\1\2_\3", xml)  # tags with ':' are not valid XML names
            self.root = ET.fromstring(xml)
            self.version = self.root.attrib.get("Version", "?")
            self._images = self._find_images(f)

    def _caifz_index(self, f, size, n):
        """The XML index of a Dragonfly 2025 ('CAIFZ') session, with each record offset filled in.

        The index (n bytes from byte 20, padded with zeros) gives every offset as 2**64 - 1; the records follow it one after
        another, in the order their offset fields appear in the index. The record walk must end exactly at the end of the
        file and give one record per offset field, or the file is refused."""
        f.seek(20); raw = f.read(n)
        if len(raw) != n:
            raise ValueError("the index runs past the end of the file")
        xml = raw.rstrip(b"\0").decode("utf-8", "replace")
        fields = list(_UNSET_OFFSET.finditer(xml))
        starts, pos = [], 20 + n
        while pos < size:
            starts.append(pos); pos = self._skip_record(f, pos)
        if pos != size or len(starts) != len(fields):
            raise UserError(f"{os.path.basename(self.path)}: this Dragonfly 2025 session has {len(starts)} data records "
                            f"for {len(fields)} entries in its index. Its layout differs from what the reader understands; "
                            "export the ROI from Dragonfly as a TIFF stack and use that instead.")
        it = iter(starts)
        return _UNSET_OFFSET.sub(lambda m: m.group(1) + str(next(it)), xml)

    def _skip_record(self, f, off):
        """Byte just after the record at off (decompresses it, keeping nothing: chunks do not say where a record ends)."""
        f.seek(off); total = struct.unpack("<Q", f.read(8))[0]; p, got, dob, carry = off + 8, 0, None, b""
        while got < total:
            cs = struct.unpack("<Q", f.read(8))[0]; p += 8 + cs
            while cs:  # the chunk, read in pieces of at most 16 MB
                data = f.read(min(cs, 1 << 24))
                if not data:
                    raise ValueError(f"the record at byte {off} runs past the end of the file")
                cs -= len(data); data = carry + data; carry = b""
                while data:  # one piece may end one zstd frame and start the next
                    if dob is None:
                        if len(data) < 4 and got < total:  # the next frame's magic number runs on into the next piece
                            carry = data; break
                        if data[:4] != ZMAGIC:
                            raise ValueError(f"unexpected record layout at byte {off}")
                        dob = self.dctx.decompressobj()
                    got += len(dob.decompress(data)); data = b""
                    if got > total:
                        raise ValueError(f"the record at byte {off} holds more data than its header says")
                    if dob.eof:
                        data = dob.unused_data; dob = None
        return p

    def _open(self):
        """The session file, opened for one read; an error if it changed since it was loaded (its offsets would be stale)."""
        f = open(self.path, "rb"); st = os.fstat(f.fileno())
        if (st.st_ino, st.st_size, st.st_mtime_ns) != self._stamp:
            f.close()
            raise UserError(f"{os.path.basename(self.path)} changed on disk since it was loaded; remove it and add it again")
        return f

    # [uint64 total bytes] then repeated [uint64 chunk size][zstd data]; a zstd frame may span chunks
    def _read(self, off):
        with self._open() as f:
            f.seek(off); total = struct.unpack("<Q", f.read(8))[0]
            return self._chunks(f, off, total)

    def _chunks(self, f, off, total):
        p, out, got, dob, left, carry = off + 8, bytearray(total), 0, None, 0, b""  # one buffer of the size the header gives
        while got < total:
            cs = struct.unpack("<Q", f.read(8))[0]
            blob = f.read(cs)
            if len(blob) != cs:
                raise ValueError(f"the record at byte {p} runs past the end of the file")
            p += 8 + cs
            blob = carry + blob; carry = b""
            while blob:  # one chunk may end one zstd frame and start the next
                if dob is None:
                    if len(blob) < 4 and got < total:  # the next frame's magic number runs on into the next chunk
                        carry = blob; break
                    if blob[:4] != ZMAGIC:
                        raise ValueError(f"unexpected record layout at byte {p}")
                    dob = self.dctx.decompressobj()
                    if not hasattr(dob, "eof"):
                        left = self._zstd.get_frame_parameters(blob[:18]).content_size
                d = dob.decompress(blob); blob = b""
                if got + len(d) > total:
                    raise ValueError(f"the record at byte {off} holds more data than its header says")
                out[got:got + len(d)] = d; got += len(d)
                done = getattr(dob, "eof", None)
                if done is None:
                    left -= len(d); done = left <= 0
                if done:
                    blob = getattr(dob, "unused_data", b""); dob = None
        return out

    def _array(self, off, count=None):
        raw = self._read(off)
        item = len(raw) // count if count else 4
        return np.frombuffer(raw, dtype={1: "u1", 2: "<u2", 4: "<u4", 8: "<u8"}[item])

    @staticmethod
    def _grid(el):
        ob = el.find("orientationBox").attrib
        dims = [int(round(float(ob[f"direction{i}Size"]) / float(ob[f"direction{i}Spacing"]))) for i in range(3)]
        return tuple(dims), float(ob["direction0Spacing"]) * 1e10  # (nx, ny, nz), voxel size in angstrom

    def images(self):
        """Image volumes stored in the session: float32 (DataType 6) or 16-bit unsigned (DataType 2, Dragonfly 2025)."""
        return list(self._images)

    def _find_images(self, f):
        res = []
        for el in self.root.findall("CxvChannel"):
            off = el.findtext("DataOffset")
            if off is None:
                continue
            dims, vox = self._grid(el)
            f.seek(int(off)); size = struct.unpack("<Q", f.read(8))[0]; n = dims[0] * dims[1] * dims[2]
            kind = (el.findtext("DataType") or "").strip()
            dtype = "<f4" if size == 4 * n and kind in ("6", "") else "<u2" if size == 2 * n and kind == "2" else None
            if dtype:
                res.append(dict(title=el.findtext("title") or "image", off=int(off), dims=dims, voxel=vox, dtype=dtype))
        return res

    @_checked
    def read_image(self, im):
        nx, ny, nz = im["dims"]
        a = np.frombuffer(self._read(im["off"]), im.get("dtype", "<f4")).reshape(nz, ny, nx)
        return a if a.dtype == np.float32 else a.astype(np.float32)

    @_checked
    def objects(self, include_undo=False):
        res = []
        for tag, kind in (("CxvLabeledMultiROI", "multi-ROI"), ("CxvVolume_ROI", "ROI")):
            for el in self.root.findall(tag):
                title = el.findtext("title") or ""
                if title.startswith("undoROI") and not include_undo:
                    continue
                if kind == "multi-ROI" and (el.find("LabelData") is None or el.find("LabelData/Labels/RecordOffset") is None):
                    continue
                if kind == "ROI" and el.findtext("RecordOffset") is None:
                    continue
                dims, vox = self._grid(el)
                res.append(dict(kind=kind, title=title, el=el, dims=dims, voxel=vox,
                                voxels=int(el.attrib.get("TotalLabelSize", -1))))
        return res

    def find(self, name, include_undo=False):
        objs = self.objects(include_undo)
        exact = [o for o in objs if o["title"] == name]
        hits = exact or [o for o in objs if name.lower() in o["title"].lower()]
        if len(hits) != 1:
            names = "\n  ".join(f"[{o['kind']}] {o['title']}" for o in (hits or objs))
            raise UserError(f"'{name}' matches {len(hits)} objects in the session. {'Candidates' if hits else 'Available'}:\n  {names}")
        return hits[0]

    @_checked
    def _runs(self, obj):
        """Checked run-length intervals of a multi-ROI -> (begin, end, label) as int64 arrays, sorted by begin."""
        el = obj["el"]; ld = el.find("LabelData"); td = ld.find("TreeData")
        nx, ny, nz = obj["dims"]; n = nx * ny * nz
        arr = {}
        for nm in ("Labels", "BeginInt", "EndInt"):
            c = ld.find(nm)
            arr[nm] = self._array(int(c.findtext("RecordOffset")), int(c.findtext("size")))
        n_el, n_int = int(td.attrib["ElementCount"]), int(td.attrib["IntervalCount"])

        def free_stack():  # Dragonfly 2022: unused tree nodes are listed on a free stack
            valid = np.ones(n_el, bool); valid[0] = False
            if n_el - n_int > 1:
                free = self._array(int(td.findtext("RecordOffsetFS")))
                valid[free[:n_el - n_int]] = False
            return np.flatnonzero(valid)

        def packed():  # Dragonfly 2025, nothing ever freed: the intervals are nodes 1 .. IntervalCount
            return np.arange(1, n_int + 1) if td.attrib.get("LastFree") == "0" and n_int < n_el else None

        for rule in (free_stack, packed):
            idx = rule()
            if idx is None:
                break
            b = arr["BeginInt"][idx].astype(np.int64); e = arr["EndInt"][idx].astype(np.int64); l = arr["Labels"][idx].astype(np.int64)
            s = np.argsort(b); b, e, l = b[s], e[s], l[s]
            total = int((e - b + 1).sum())
            ok = len(b) == n_int and (len(b) < 2 or bool(np.all(b[1:] > e[:-1]))) and (len(b) == 0 or e.max() < n) \
                and total == obj["voxels"]
            if ok:
                break
        if not ok:
            raise UserError(f"'{obj['title']}': decoded {total} labelled voxels in {len(b)} runs, but Dragonfly records "
                            f"{obj['voxels']} voxels in {n_int} runs. This session's layout differs from what the reader "
                            "understands; export the multi-ROI from Dragonfly as a TIFF stack and drop that instead.")
        return b, e, l

    @_checked
    def read_multiroi(self, obj):
        el = obj["el"]; nx, ny, nz = obj["dims"]; n = nx * ny * nz
        b, e, l = self._runs(obj)
        dt = label_dtype(int(l.max()) if len(l) else 0)
        delta = np.zeros(n + 1, dt)
        delta[b] += l.astype(dt); delta[e + 1] -= l.astype(dt)
        lab = np.cumsum(delta[:n], dtype=dt).reshape(nz, ny, nx)
        return lab, self._multiroi_classes(el, int(l.max()) if len(l) else 0)

    def _multiroi_classes(self, el, nlab):
        return self.class_table(el, nlab)[0]

    @_checked
    def class_table(self, el, nlab):
        """Names, colours and Dragonfly's visible flag of labels 1..nlab -> ({label: dict(name, color, visible, named)}, rules).

        Slots are identified by their type in DataTypesScalarValues (9 text, 8 on/off, 4 whole number, 5 decimal), numbered
        from 0 as ScalarValues_0, _1, ... (Dragonfly 2022.2). rules says, in plain words, which slot gave what."""
        isc = el.find("InternalScalars"); rules = {}
        types, _ = _scalar_types(self, isc)
        slots = {}
        for c in (isc if isc is not None else []):
            num = _slot_number(c.tag)
            if c.tag.startswith("ScalarValues_") and num is not None and c.findtext("RecordOffset") is not None:
                guid = c.attrib.get("GUID", "")
                by_guid = next((k for suf, k in _GUID_KINDS if guid.endswith(suf)), None)
                by_type = SLOT_TYPES.get(types[num]) if types and num < len(types) else None
                slots[num] = dict(el=c, kind=by_type or by_guid, typed=by_type is not None and (by_guid in (None, by_type)))
        typed = bool(slots) and all(v["typed"] for v in slots.values())
        standard = typed and types == OBSERVED_TYPES
        rules["layout"] = (f"slot types {types} (as in Dragonfly 2022.2)" if standard
                           else f"slot types {types} (another layout; guessing)" if typed else "slot types unknown or not matching; guessing")

        def read(num):
            c = slots[num]["el"]; kind = slots[num]["kind"]
            return _decode_slot(self, c, kind)

        names = {}
        for num in sorted(n for n, v in slots.items() if v["kind"] == "string"):
            try:
                cand = {int(k): v for k, v in read(num).items()}
            except Exception as exc:
                print(f"  note: could not read text slot {num} ({exc})"); continue
            if cand and min(cand) >= 1 and max(cand) <= max(nlab, len(cand)):
                names = cand
                rules["names"] = f"text slot ScalarValues_{num}" + ("" if typed else " (guessed: first text slot)"); break
        if not names:
            rules["names"] = "no names found in the session"
        colors = {}

        def as_colours(vals):
            return {k: bgr_hex(int(x, 16)) for k, x in enumerate(vals) if k > 0}

        whole = sorted(n for n, v in slots.items() if v["kind"] == "uint32")

        def colour_like(v):  # 24-bit values, not all zero, and not simply the label numbers 1, 2, 3, ...
            return len(v) > 1 and max(v[1:]) < 1 << 24 and v[1:] != list(range(1, len(v))) and len(set(v[1:]) - {0}) > 0

        try:
            if standard and 1 in whole and colour_like([int(x, 16) for x in read(1)]):
                colors = as_colours(read(1)); rules["colours"] = "slot ScalarValues_1 (whole numbers, as in Dragonfly 2022.2)"
            else:
                for num in whole:
                    if colour_like([int(x, 16) for x in read(num)]):
                        colors = as_colours(read(num)); rules["colours"] = f"slot ScalarValues_{num} (guessed: values look like colours)"; break
        except Exception as exc:
            print(f"  note: could not read colours from the session ({exc})")
        if not colors:
            rules["colours"] = "no colours found in the session; using TomoReveal's own"
        visible = {}
        bools = sorted(n for n, v in slots.items() if v["kind"] == "bool")
        if len(bools) == 1:
            try:
                visible = {k: bool(x) for k, x in enumerate(read(bools[0])) if k > 0}
                rules["visible"] = f"on/off slot ScalarValues_{bools[0]} (taken as Dragonfly's 'visible' flag)"
            except Exception as exc:
                print(f"  note: could not read the visible flags ({exc})")
        if not visible:
            rules["visible"] = "no visible flags found"
        out = {}
        for k in range(1, nlab + 1):
            nm = (names.get(k) or "").strip()
            if not nm and names:
                print(f"  note: label {k} has no name in the session")
            out[k] = dict(name=nm or f"label {k} (no name in the session)", named=bool(nm),
                          color=colors.get(k) or PALETTE[(k - 1) % len(PALETTE)], visible=visible.get(k))
        return out, rules

    @_checked
    def preview_classes(self, obj):
        """Names and colours of an object's structures without decoding its voxels (for listing it before use)."""
        if obj["kind"] == "ROI":
            return {1: dict(name=obj["title"], color=PALETTE[0])}
        names = {}
        isc = obj["el"].find("InternalScalars")
        for c in (isc if isc is not None else []):
            if c.tag.startswith("ScalarValues_") and c.attrib.get("GUID", "").endswith("CxvArrayString") and c.findtext("RecordOffset"):
                try:
                    names = parse_strings(self._read(int(c.findtext("RecordOffset"))))
                except Exception:
                    names = {}
                break
        nlab = max(names, default=0)
        if not nlab:
            try:
                _, _, l = self._runs(obj); nlab = int(l.max()) if len(l) else 0
            except UserError:
                nlab = 0
        return self._multiroi_classes(obj["el"], nlab)

    @_checked
    def read_roi(self, obj):
        """Plain ROI: uint32 pairs (first voxel, last voxel) of each run."""
        el = obj["el"]; nx, ny, nz = obj["dims"]; n = nx * ny * nz
        v = self._array(int(el.findtext("RecordOffset"))).astype(np.int64)
        if len(v) % 2 or (len(v) > 1 and not np.all(np.diff(v) >= 0)) or (len(v) and v.max() >= n):
            raise UserError(f"ROI '{obj['title']}': unexpected layout; export it from Dragonfly as a TIFF stack instead.")
        delta = np.zeros(n + 1, np.int8)
        delta[v[0::2]] += 1; delta[v[1::2] + 1] -= 1
        mask = np.cumsum(delta[:n], dtype=np.int8).astype(bool).reshape(nz, ny, nx)
        col = None; ic = el.find("InitialColor")
        if ic is not None:
            try:
                col = "#%02x%02x%02x" % tuple(int(round(float(ic.attrib[f"C010{i}"]) * 255)) for i in (1, 2, 3))
            except Exception:
                pass
        return mask, col

    @_checked
    def read_selection(self, objs):
        """One multi-ROI, or one or more plain ROIs (one class each) -> (labels, classes)."""
        if len(objs) == 1 and objs[0]["kind"] == "multi-ROI":
            return self.read_multiroi(objs[0])
        if not objs or any(o["kind"] != "ROI" for o in objs):
            raise UserError("choose either one multi-ROI or one or more plain ROIs")
        nx, ny, nz = objs[0]["dims"]
        lab = np.zeros((nz, ny, nx), label_dtype(len(objs))); classes = {}
        for k, o in enumerate(objs, 1):
            if o["dims"] != objs[0]["dims"]:
                raise UserError(f"ROI '{o['title']}' is on a different grid from '{objs[0]['title']}'")
            mask, col = self.read_roi(o)
            lab[mask] = k
            classes[k] = dict(name=o["title"], color=col or PALETTE[(k - 1) % len(PALETTE)])
        return lab, classes


def guess_roi(objs):
    """Most likely 'final segmentation' among a session's objects; one-slice previews never are."""
    objs = [o for o in objs if o["dims"][2] > 1]
    multi = [o for o in objs if o["kind"] == "multi-ROI"]
    def score(o):
        t = o["title"].lower()
        return (("seg" in t) - 2 * any(w in t for w in ("train", "label", "frame")), o["voxels"])
    if multi:
        return max(multi, key=score)["title"]
    return "__all_rois__" if len(objs) > 1 else (objs[0]["title"] if objs else None)


def parse_strings(raw):
    """A string slot: repeated [uint64 label][UTF-16-BE text][0x0000] -> {label: text}."""
    out, p = {}, 0
    while p + 8 <= len(raw):
        k = struct.unpack_from("<Q", raw, p)[0]; p += 8; q = p
        while q + 1 < len(raw) and raw[q:q + 2] != b"\x00\x00":
            q += 2
        out[int(k)] = raw[p:q].decode("utf-16-be", "replace").strip(); p = q + 2
    return out


def bgr_hex(x):
    """Dragonfly colour 0x00BBGGRR -> '#rrggbb'."""
    x = int(x)
    return "#%02x%02x%02x" % (x & 255, (x >> 8) & 255, (x >> 16) & 255)


# an offset field left unset (2**64 - 1), as every one is in a Dragonfly 2025 index
_UNSET_OFFSET = re.compile(r"((?:RecordOffset\w*|DataOffset)(?:=\"|>))18446744073709551615\b")

# DataTypesScalarValues codes seen in Dragonfly 2022.2, and the GUID suffix of each slot kind
SLOT_TYPES = {9: "string", 8: "bool", 4: "uint32", 5: "double"}
OBSERVED_TYPES = [4, 4, 9, 5, 5, 8, 4]  # slot 1 colours, slot 2 names, slot 5 visible flag
_GUID_KINDS = (("CxvArrayString", "string"), ("CxvArrayUnsignedLong", "uint32"), ("CxvArrayDouble", "double"),
               ("CxvArrayBool", "bool"), ("CxvArrayBoolean", "bool"))


def _slot_number(tag):
    try:
        return int(tag.rsplit("_", 1)[1])
    except (IndexError, ValueError):
        return None


def _scalar_types(ses, isc):
    """The DataTypesScalarValues list of an InternalScalars element, wherever it is stored (None if absent)."""
    if isc is None:
        return None, "no InternalScalars element"
    for c in isc.iter():
        if not c.tag.startswith("DataTypesScalarValues"):
            continue
        if c.findtext("RecordOffset") is not None:
            raw = ses._read(int(c.findtext("RecordOffset")))
            n = int(c.findtext("size") or 0) or len(raw) // 4
            item = len(raw) // n if n else 4
            v = np.frombuffer(raw[:n * item], {1: "u1", 2: "<u2", 4: "<u4", 8: "<u8"}.get(item, "<u4"))
            return [int(x) for x in v], f"binary record ({item} bytes per entry)"
        txt = " ".join(t.strip() for t in c.itertext() if t.strip())
        nums = [int(x) for x in re.findall(r"-?\d+", txt)]
        if nums:
            return nums, "XML text"
        nums = [int(v) for v in c.attrib.values() if re.fullmatch(r"-?\d+", v)]
        return (nums or None), "XML attributes"
    return None, "not found"


def _decode_slot(ses, c, kind):
    raw = ses._read(int(c.findtext("RecordOffset")))
    size = int(c.findtext("size") or 0)
    if kind == "string":
        return {str(k): v for k, v in parse_strings(raw).items()}
    item = len(raw) // size if size else {"uint32": 4, "double": 8, "bool": 1}.get(kind, 4)
    if kind == "double" and item == 8:
        return [float(x) for x in np.frombuffer(raw[:size * 8], "<f8")]
    v = np.frombuffer(raw[:(size or len(raw) // item) * item], {1: "u1", 2: "<u2", 4: "<u4", 8: "<u8"}.get(item, "u1"))
    if kind == "bool":
        return [int(x) for x in v]
    if kind == "uint32":
        return ["0x%08x" % int(x) for x in v]
    return {"bytes": len(raw), "first bytes": raw[:32].hex()}


def describe_session(path):
    """Everything inspect needs to know about a session, as plain JSON-able data."""
    ses = OrsSession(path)
    out = dict(path=os.path.abspath(path), dragonfly_version=ses.version, images=[], objects=[])
    for im in ses.images():
        out["images"].append(dict(kind="image", title=im["title"], grid=list(im["dims"]), voxel_nm=im["voxel"] / 10))
    for o in ses.objects(include_undo=True):
        d = dict(kind=o["kind"], title=o["title"], grid=list(o["dims"]), voxel_nm=o["voxel"] / 10,
                 total_label_size=o["voxels"], undo_copy=o["title"].startswith("undoROI"),
                 single_slice=o["dims"][2] == 1)
        if o["kind"] == "ROI":
            try:
                mask, col = ses.read_roi(o)
                d["table"] = [dict(label=1, voxels=int(mask.sum()), name=o["title"], colour=col, visible=None)]
            except UserError as exc:
                d["error"] = str(exc)
            out["objects"].append(d); continue
        isc = o["el"].find("InternalScalars")
        try:
            with _damaged(path):
                types, where = _scalar_types(ses, isc)
        except UserError as exc:
            types, where = None, str(exc)
        d["data_types"] = types; d["data_types_source"] = where
        slots = []
        for c in (isc if isc is not None else []):
            if not c.tag.startswith("ScalarValues_") or c.findtext("RecordOffset") is None:
                continue
            num, guid = _slot_number(c.tag), c.attrib.get("GUID", "")
            kind = next((k for suf, k in _GUID_KINDS if guid.endswith(suf)), None)
            code = None
            if types and num is not None:  # slot numbering may start at 0 or 1; record both readings
                code = dict(zero_based=types[num] if num < len(types) else None,
                            one_based=types[num - 1] if 0 < num <= len(types) else None)
            kind = (SLOT_TYPES.get(code["zero_based"]) if code else None) or kind  # the reader's rule (class_table)
            sl = dict(slot=c.tag, number=num, guid_kind=guid.rsplit(".", 1)[-1] if guid else "",
                      type_code=code, decoded_as=kind or "unknown", size=int(c.findtext("size") or 0))
            try:
                sl["values"] = _decode_slot(ses, c, kind)
            except Exception as exc:
                sl["error"] = f"{type(exc).__name__}: {exc}"
            slots.append(sl)
        d["slots"] = slots
        try:
            b, e, l = ses._runs(o)
            nlab = int(l.max()) if len(l) else 0
            counts = np.bincount(l, weights=(e - b + 1), minlength=nlab + 1).astype(np.int64)
            classes, d["rules"] = ses.class_table(o["el"], nlab)
            d["table"] = [dict(label=k, voxels=int(counts[k]), name=c["name"], colour=c["color"],
                               visible=(None if c["visible"] is None else int(c["visible"])))
                          for k, c in classes.items()]
            d["decoded_voxels"] = int(counts.sum())
        except UserError as exc:
            d["error"] = str(exc)
        out["objects"].append(d)
    return out
