"""Writes a tiny .ORSSession in the layout described in CLAUDE.md (tests only; not a full Dragonfly writer)."""
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
                  extra_title=None):
    """image: optional float32 (nz, ny, nx) array stored as the session's image.
    labels: optional int (nz, ny, nx) array; its runs replace `runs`. extra_title: also store a copy titled this."""
    buf = bytearray(b"ORSZ" + b"\0" * 8)
    nx, ny, nz = dims
    if labels is not None:
        flat = np.asarray(labels).ravel().astype(np.int64)
        edges = np.flatnonzero(np.diff(np.concatenate([[0], flat, [0]])) != 0)
        runs = [(int(b), int(e) - 1, int(flat[b])) for b, e in zip(edges[:-1], edges[1:]) if flat[b] > 0]
    lab = np.array([0] + [r[2] for r in runs], "<u4")
    beg = np.array([0] + [r[0] for r in runs], "<u4")
    end = np.array([0] + [r[1] for r in runs], "<u4")
    offs = {k: _record(buf, a.tobytes()) for k, a in (("Labels", lab), ("BeginInt", beg), ("EndInt", end))}
    n = len(names) + 1
    strings = b"".join(struct.pack("<Q", k) + nm.encode("utf-16-be") + b"\0\0" for k, nm in enumerate(names, 1))
    # numbered from 0 as in Dragonfly 2022.2: _1 colours, _2 names, _5 visible flag
    slots = [("CxvArrayUnsignedLong", np.zeros(n, "<u4").tobytes(), n),
             ("CxvArrayUnsignedLong", np.array([0, *colors], "<u4").tobytes(), n),
             ("CxvArrayString", strings, len(names)),
             ("CxvArrayDouble", np.zeros(n, "<f8").tobytes(), n),
             ("CxvArrayDouble", np.ones(n, "<f8").tobytes(), n),
             ("CxvArrayBool", np.array([0, *visible], "u1").tobytes(), n),
             ("CxvArrayUnsignedLong", np.zeros(n, "<u4").tobytes(), n)]
    slot_xml = "".join(f'<ScalarValues_{i} GUID="ORS.{g}"><RecordOffset>{_record(buf, d)}</RecordOffset><size>{s}</size></ScalarValues_{i}>'
                       for i, (g, d, s) in enumerate(slots))
    total = sum(e - b + 1 for b, e, _ in runs)
    box = "".join(f' direction{i}Size="{d * 1e-9}" direction{i}Spacing="1e-9"' for i, d in enumerate(dims))
    ld = "".join(f"<{k}><RecordOffset>{o}</RecordOffset><size>{len(lab)}</size></{k}>" for k, o in offs.items())
    mroi = (f'<CxvLabeledMultiROI TotalLabelSize="{{total}}"><title>{{title}}</title>'
            f'<orientationBox{box}/><LabelData>{ld}<TreeData ElementCount="{len(lab)}" IntervalCount="{len(runs)}"/></LabelData>'
            f'<InternalScalars><DataTypesScalarValues>{" ".join(map(str, data_types))}</DataTypesScalarValues>{slot_xml}</InternalScalars>'
            f'</CxvLabeledMultiROI>')
    body = mroi.format(total=total, title=title) + (mroi.format(total=total, title=extra_title) if extra_title else "")
    if image is not None:
        off = _record(buf, np.ascontiguousarray(image, "<f4").tobytes())
        body = f'<CxvChannel><title>stored image</title><DataOffset>{off}</DataOffset><orientationBox{box}/></CxvChannel>' + body
    xml = f'<ORS:Session Version="2022.2.0.1409">{body}</ORS:Session>'
    struct.pack_into("<Q", buf, 4, len(buf))
    buf += xml.encode()
    open(path, "wb").write(bytes(buf))
    return path
