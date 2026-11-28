"""Writes a tiny .ORSSession in the layout described in CLAUDE.md (tests only; not a full Dragonfly writer)."""
import re
import struct

import numpy as np
import zstandard


def _record(buf, data):
    off = len(buf)
    z = zstandard.ZstdCompressor().compress(data)
    buf += struct.pack("<Q", len(data)) + struct.pack("<Q", len(z)) + z
    return off


def write_session(path, dims=(6, 5, 4), runs=((0, 9, 1), (20, 29, 2), (60, 61, 3)),
                  names=("ER", "Membrane", "background"), colors=(0x0000FF, 0x00FF00, 0xFF0000),
                  visible=(1, 1, 0), title="Segmentation SegWiz test", data_types=(4, 4, 9, 5, 5, 8, 4), image=None, labels=None,
                  extra_title=None, slot0=None, slots_override=None, layout=2022, spare=0, image_dtype="<f4", freed=0):
    """image: optional (nz, ny, nx) array stored as the session's image (image_dtype "<f4" or "<u2").
    labels: optional int (nz, ny, nx) array; its runs replace `runs`. extra_title: also store a copy titled this.
    layout 2025: Dragonfly 2025 'CAIFZ' file. spare: unused tree nodes after the runs, holding junk, with
    LastFree="0" and a junk free stack (as Dragonfly 2025 writes them). freed: junk nodes mixed in among the runs (in
    reverse order), only half of them on the free stack, and the runs linked Head -> Next -> Tail (3.6.1)."""
    buf = bytearray(b"ORSZ" + b"\0" * 8)
    nx, ny, nz = dims
    if labels is not None:
        flat = np.asarray(labels).ravel().astype(np.int64)
        edges = np.flatnonzero(np.diff(np.concatenate([[0], flat, [0]])) != 0)
        runs = [(int(b), int(e) - 1, int(flat[b])) for b, e in zip(edges[:-1], edges[1:]) if flat[b] > 0]
    junk = [7] * spare
    nodes, at, gone = [None] + list(runs), list(range(1, len(runs) + 1)), []
    if freed:  # node 0 unused, then the runs last first, with a junk node after each of the first `freed` of them
        nodes, at = [None], [0] * len(runs)
        for j in range(len(runs) - 1, -1, -1):
            at[j] = len(nodes); nodes.append(runs[j])
            if len(gone) < freed:
                gone.append(len(nodes)); nodes.append((3, 3, 7))
    lab = np.array([0] + [r[2] if r else 0 for r in nodes[1:]] + junk, "<u4")
    beg = np.array([0] + [r[0] if r else 0 for r in nodes[1:]] + junk, "<u4")
    end = np.array([0] + [r[1] if r else 0 for r in nodes[1:]] + junk, "<u4")
    offs = {k: _record(buf, a.tobytes()) for k, a in (("Labels", lab), ("BeginInt", beg), ("EndInt", end))}
    n = len(names) + 1
    strings = b"".join(struct.pack("<Q", k) + nm.encode("utf-16-be") + b"\0\0" for k, nm in enumerate(names, 1))
    # numbered from 0 as in Dragonfly 2022.2: _1 colours, _2 names, _5 visible flag
    slots = [("CxvArrayUnsignedLong", (np.zeros(n, "<u4") if slot0 is None else np.asarray(slot0, "<u4")).tobytes(), n),
             ("CxvArrayUnsignedLong", np.array([0, *colors], "<u4").tobytes(), n),
             ("CxvArrayString", strings, len(names)),
             ("CxvArrayDouble", np.zeros(n, "<f8").tobytes(), n),
             ("CxvArrayDouble", np.ones(n, "<f8").tobytes(), n),
             ("CxvArrayBool", np.array([0, *visible], "u1").tobytes(), n),
             ("CxvArrayUnsignedLong", np.zeros(n, "<u4").tobytes(), n)]
    if slots_override is not None:  # list of (GUID suffix, bytes, size) replacing the standard layout
        slots = slots_override
    slot_xml = "".join(f'<ScalarValues_{i} GUID="ORS.{g}"><RecordOffset>{_record(buf, d)}</RecordOffset><size>{s}</size></ScalarValues_{i}>'
                       for i, (g, d, s) in enumerate(slots))
    types_off = _record(buf, np.array(data_types, "<u2").tobytes())  # stored like Dragonfly 2022.2: 2 bytes per entry
    total = sum(e - b + 1 for b, e, _ in runs)
    box = "".join(f' direction{i}Size="{d * 1e-9}" direction{i}Spacing="1e-9"' for i, d in enumerate(dims))
    ld = "".join(f"<{k}><RecordOffset>{o}</RecordOffset><size>{len(lab)}</size></{k}>" for k, o in offs.items())
    tree = f'ElementCount="{len(lab)}" IntervalCount="{len(runs)}"'
    if freed:
        nxt = np.zeros(len(nodes), "<u4"); nxt[at[:-1]] = at[1:]
        tree += (f' Head="{at[0]}" Tail="{at[-1]}" LastFree="{len(gone)}"><RecordOffsetN>{_record(buf, nxt.tobytes())}'
                 f'</RecordOffsetN><RecordOffsetFS>{_record(buf, np.array([0] + gone[:len(gone) // 2], "<u4").tobytes())}'
                 f'</RecordOffsetFS></TreeData')
    elif spare:
        tree += f' LastFree="0"><RecordOffsetFS>{_record(buf, np.array([0, 1, 1, 2] * spare, "<u4").tobytes())}</RecordOffsetFS></TreeData'
    else:
        tree += "/"
    mroi = (f'<CxvLabeledMultiROI TotalLabelSize="{{total}}"><title>{{title}}</title>'
            f'<orientationBox{box}/><LabelData>{ld}<TreeData {tree}></LabelData>'
            f'<InternalScalars><DataTypesScalarValues><RecordOffset>{types_off}</RecordOffset><size>{len(data_types)}</size>'
            f'</DataTypesScalarValues>{slot_xml}</InternalScalars>'
            f'</CxvLabeledMultiROI>')
    body = mroi.format(total=total, title=title) + (mroi.format(total=total, title=extra_title) if extra_title else "")
    if image is not None:
        off = _record(buf, np.ascontiguousarray(image, image_dtype).tobytes())
        kind = {"<f4": 6, "<u2": 2}[image_dtype]
        body = (f'<CxvChannel><title>stored image</title><DataType>{kind}</DataType><DataOffset>{off}</DataOffset>'
                f'<orientationBox{box}/></CxvChannel>') + body
    xml = f'<ORS:Session Version="{layout}.2.0.1409">{body}</ORS:Session>'
    if layout == 2025:
        return _write_2025(path, bytes(buf), xml)
    struct.pack_into("<Q", buf, 4, len(buf))
    buf += xml.encode()
    open(path, "wb").write(bytes(buf))
    return path


UNSET = "18446744073709551615"
_FIELD = re.compile(r"((?:RecordOffset\w*|DataOffset)>)(\d+)")


def _write_2025(path, buf, xml):
    """Dragonfly 2025: 'CAIFZ\\0\\0\\0' + 4-character build + uint64 index size, the index (offsets unset, zero padded),
    then the records one after another in the order their offset fields appear in the index."""
    starts = sorted({int(m.group(2)) for m in _FIELD.finditer(xml)})
    span = {o: buf[o:nxt] for o, nxt in zip(starts, starts[1:] + [len(buf)])}
    order = [int(m.group(2)) for m in _FIELD.finditer(xml)]
    index = _FIELD.sub(lambda m: m.group(1) + UNSET, xml).encode() + b"\0" * 37
    out = b"CAIFZ\0\0\0" + b"2053" + struct.pack("<Q", len(index)) + index + b"".join(span[o] for o in order)
    open(path, "wb").write(out)
    return path
