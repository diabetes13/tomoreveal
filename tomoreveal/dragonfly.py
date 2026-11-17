"""Reader for Dragonfly .ORSSession files (multi-ROIs, ROIs and stored images)."""
import mmap
import os
import re
import struct
import xml.etree.ElementTree as ET

import numpy as np

from .common import PALETTE, UserError, ZMAGIC


class OrsSession:
    def __init__(self, path):
        try:
            import zstandard
        except ImportError:
            raise UserError("reading .ORSSession needs the 'zstandard' package")
        if not os.path.isfile(path):
            raise UserError(f"file not found: {path}")
        self._zstd = zstandard
        self.dctx = zstandard.ZstdDecompressor()
        self.path = path
        self.f = open(path, "rb")
        self.mm = mmap.mmap(self.f.fileno(), 0, access=mmap.ACCESS_READ)
        if self.mm[:4] != b"ORSZ":
            raise UserError(f"{os.path.basename(path)} is not a compressed Dragonfly session (no 'ORSZ' header). "
                            "Export the ROI from Dragonfly as a TIFF stack and use that instead.")
        xoff = struct.unpack_from("<Q", self.mm, 4)[0]
        xml = self.mm[xoff:].decode("utf-8", "replace")
        xml = re.sub(r"<(/?)([\w.\-]+):([\w.\-]+)", r"<\1\2_\3", xml)  # tags with ':' are not valid XML names
        self.root = ET.fromstring(xml)
        self.version = self.root.attrib.get("Version", "?")

    # [uint64 total bytes] then repeated [uint64 chunk size][zstd data]; a zstd frame may span chunks
    def _read(self, off):
        mm = self.mm
        total = struct.unpack_from("<Q", mm, off)[0]
        p, out, got, dob, left = off + 8, [], 0, None, 0
        while got < total:
            cs = struct.unpack_from("<Q", mm, p)[0]
            blob = mm[p + 8:p + 8 + cs]
            if dob is None:
                if blob[:4] != ZMAGIC:
                    raise ValueError(f"unexpected record layout at byte {p}")
                dob = self.dctx.decompressobj()
                if not hasattr(dob, "eof"):
                    left = self._zstd.get_frame_parameters(blob[:18]).content_size
            d = dob.decompress(blob)
            out.append(d); got += len(d); p += 8 + cs
            done = getattr(dob, "eof", None)
            if done is None:
                left -= len(d); done = left <= 0
            if done:
                dob = None
        return b"".join(out)

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
        """Image volumes stored in the session (float32 only)."""
        res = []
        for el in self.root.findall("CxvChannel"):
            off = el.findtext("DataOffset")
            if off is None:
                continue
            dims, vox = self._grid(el)
            if struct.unpack_from("<Q", self.mm, int(off))[0] == 4 * dims[0] * dims[1] * dims[2]:
                res.append(dict(title=el.findtext("title") or "image", off=int(off), dims=dims, voxel=vox))
        return res

    def read_image(self, im):
        nx, ny, nz = im["dims"]
        return np.frombuffer(self._read(im["off"]), "<f4").reshape(nz, ny, nx)

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

    def read_multiroi(self, obj):
        el = obj["el"]; ld = el.find("LabelData"); td = ld.find("TreeData")
        nx, ny, nz = obj["dims"]; n = nx * ny * nz
        arr = {}
        for nm in ("Labels", "BeginInt", "EndInt"):
            c = ld.find(nm)
            arr[nm] = self._array(int(c.findtext("RecordOffset")), int(c.findtext("size")))
        n_el, n_int = int(td.attrib["ElementCount"]), int(td.attrib["IntervalCount"])
        valid = np.ones(n_el, bool); valid[0] = False
        if n_el - n_int > 1:  # unused tree nodes are listed on a free stack
            free = self._array(int(td.findtext("RecordOffsetFS")))
            valid[free[:n_el - n_int]] = False
        idx = np.flatnonzero(valid)
        b = arr["BeginInt"][idx].astype(np.int64); e = arr["EndInt"][idx].astype(np.int64); l = arr["Labels"][idx].astype(np.int64)
        s = np.argsort(b); b, e, l = b[s], e[s], l[s]
        total = int((e - b + 1).sum())
        ok = len(b) == n_int and (len(b) < 2 or bool(np.all(b[1:] > e[:-1]))) and (len(b) == 0 or e.max() < n) \
            and total == obj["voxels"]
        if not ok:
            raise UserError(f"'{obj['title']}': decoded {total} labelled voxels in {len(b)} runs, but Dragonfly records "
                            f"{obj['voxels']} voxels in {n_int} runs. This session's layout differs from what the reader "
                            "understands; export the multi-ROI from Dragonfly as a TIFF stack and drop that instead.")
        dt = np.int16 if (len(l) and l.max() > 127) else np.int8
        delta = np.zeros(n + 1, dt)
        delta[b] += l.astype(dt); delta[e + 1] -= l.astype(dt)
        lab = np.cumsum(delta[:n], dtype=dt).reshape(nz, ny, nx)
        return lab, self._multiroi_classes(el, int(l.max()) if len(l) else 0)

    def _multiroi_classes(self, el, nlab):
        names, colors = {}, {}
        isc = el.find("InternalScalars")
        try:
            best = 0
            for c in (isc if isc is not None else []):
                if not c.tag.startswith("ScalarValues_") or c.findtext("RecordOffset") is None:
                    continue
                off, kind = int(c.findtext("RecordOffset")), c.attrib.get("GUID", "")
                if kind.endswith("CxvArrayString") and not names:
                    raw, p = self._read(off), 0
                    while p + 8 <= len(raw):  # [uint64 label][UTF-16-BE text][0x0000]
                        k = struct.unpack_from("<Q", raw, p)[0]; p += 8; q = p
                        while q + 1 < len(raw) and raw[q:q + 2] != b"\x00\x00":
                            q += 2
                        names[int(k)] = raw[p:q].decode("utf-16-be", "replace").strip(); p = q + 2
                elif kind.endswith("CxvArrayUnsignedLong"):
                    v = self._array(off, int(c.findtext("size")))
                    if len(set(v[1:].tolist()) - {0}) > best:  # the colour slot has the most distinct values
                        best = len(set(v[1:].tolist()) - {0})
                        colors = {k: "#%02x%02x%02x" % (int(x) & 255, (int(x) >> 8) & 255, (int(x) >> 16) & 255)
                                  for k, x in enumerate(v.tolist()) if k > 0}  # stored as 0x00BBGGRR
        except Exception as exc:  # names/colours are cosmetic: fall back rather than fail
            print(f"  note: could not read class names/colours from the session ({exc}); using defaults")
        return {k: dict(name=names.get(k) or f"class {k}", color=colors.get(k) or PALETTE[(k - 1) % len(PALETTE)])
                for k in range(1, nlab + 1)}

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

    def read_selection(self, objs):
        """One multi-ROI, or one or more plain ROIs (one class each) -> (labels, classes)."""
        if len(objs) == 1 and objs[0]["kind"] == "multi-ROI":
            return self.read_multiroi(objs[0])
        if not objs or any(o["kind"] != "ROI" for o in objs):
            raise UserError("choose either one multi-ROI or one or more plain ROIs")
        nx, ny, nz = objs[0]["dims"]
        lab = np.zeros((nz, ny, nx), np.int8); classes = {}
        for k, o in enumerate(objs, 1):
            if o["dims"] != objs[0]["dims"]:
                raise UserError(f"ROI '{o['title']}' is on a different grid from '{objs[0]['title']}'")
            mask, col = self.read_roi(o)
            lab[mask] = k
            classes[k] = dict(name=o["title"], color=col or PALETTE[(k - 1) % len(PALETTE)])
        return lab, classes


def guess_roi(objs):
    """Most likely 'final segmentation' among a session's objects."""
    multi = [o for o in objs if o["kind"] == "multi-ROI"]
    def score(o):
        t = o["title"].lower()
        return (("seg" in t) - 2 * any(w in t for w in ("train", "label", "frame")), o["voxels"])
    if multi:
        return max(multi, key=score)["title"]
    return "__all_rois__" if len(objs) > 1 else (objs[0]["title"] if objs else None)
