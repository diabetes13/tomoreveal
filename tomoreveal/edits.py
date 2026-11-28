"""Edits of the movie parts (one undo step each in the window, see gui_movie.MovieEditor) and the undo history.
Parts are always found by their id, never by their position in the list."""
import copy

from .common import UserError
from .storyboard import build_plan, main_range, make_target, merge_settings, migrate_spec, new_id, target_items


def _index(spec, part_id):
    for i, p in enumerate(spec["shots"]):
        if p.get("id") == part_id:
            return i
    raise UserError("That part is no longer in the movie.")


def insert_part(spec, part, after=None):
    """Insert after the part with id `after`; at the end when after is None; at the start when after == "start"."""
    migrate_spec(spec)
    if after is None:
        spec["shots"].append(part)
    elif after == "start":
        spec["shots"].insert(0, part)
    else:
        spec["shots"].insert(_index(spec, after) + 1, part)
    return spec


def remove_part(spec, part_id):
    del spec["shots"][_index(spec, part_id)]
    return spec


def duplicate_part(spec, part_id):
    i = _index(spec, part_id)
    p = copy.deepcopy(spec["shots"][i]); p["id"] = new_id()
    spec["shots"].insert(i + 1, p)
    return spec


def move_part(spec, part_id, new_index):
    i = _index(spec, part_id); p = spec["shots"].pop(i)
    spec["shots"].insert(int(min(len(spec["shots"]), max(0, new_index))), p)
    return spec


def set_part(spec, part_id, **fields):
    spec["shots"][_index(spec, part_id)].update(fields)
    return spec


def split_part(spec, ds, part_id, frame):
    """Split a slide or a hold at a frame of its main phase into two parts, joined at that frame. Its arrows and text
    go on in the second half (see _split_annotations)."""
    i = _index(spec, part_id); p = spec["shots"][i]
    if p.get("type") not in ("slide", "hold"):
        raise UserError("This kind of part cannot be split.")
    plan = build_plan(spec, ds); rng = main_range(plan, part_id)
    if rng is None or not (rng[0] < frame < rng[0] + rng[1]):
        raise UserError("Put the playhead inside the part (not at its very start or end) to split it.")
    start, count = rng; secs = float(p.get("seconds", 5))
    sg = next(sg for sg in plan.segments if sg["part"] == part_id and sg["phase"] == "main")
    n = max(1, round(plan.fps * max(0.1, secs)))  # all its frames: transitions may overlap its start (skip) or end
    f = (frame - start + sg.get("skip", 0)) / n
    first, second = copy.deepcopy(p), copy.deepcopy(p)
    second["id"] = new_id()
    first["seconds"] = secs * f; second["seconds"] = secs * (1 - f)
    first["pause_after"] = 0.0; second["pause_before"] = 0.0; second["transition"] = 0
    if "ease_end" in first:  # the first half carries on into the second without slowing down at the join
        first["ease_end"] = "sudden"
    if p["type"] == "slide":
        k = int(plan.frames[frame]["k"])
        first["to"] = k; second["from"] = "current"
        if second.get("structures") in ("none", "above", "all"):
            second["structures"] = "keep"
    spec["shots"][i:i + 1] = [first, second]
    _split_annotations(spec, part_id, second["id"], plan.frames[frame].get("tp"))
    return spec


def _split_annotations(spec, pid, new, T):
    """The arrows and text of a part split at T seconds into it (its clock "tp"): what shows in the second half gets a
    copy there (it is moved, if it shows nowhere else), its times earlier by T. A start before the join becomes negative,
    so that a fade or growth simply goes on. Items in all parts keep "all" when they have no times; with times, their
    list of parts is written out (the times now belong to these parts)."""
    from . import annotations as an
    if T is None:
        return
    ids = [p.get("id") for p in spec["shots"] if p.get("id") != new]
    its = {id(a) for a in an.items(spec)}; out = []
    for a in spec.get("annotations") or []:
        out.append(a)
        if id(a) not in its or not an.shown_in(a, pid):
            continue
        sh = a.setdefault("show", {}); s0, s1 = an.show_times(a)
        if sh.get("parts", "all") == "all":
            if sh.get("start") is None and sh.get("end") is None:
                continue
            sh["parts"] = list(ids)
        if s1 is not None and s1 < T:  # only in the first half
            continue
        b = copy.deepcopy(a); b["id"] = new_id()
        b["show"].update(parts=[new], start=s0 - T, end=None if s1 is None else s1 - T)
        if s0 >= T:  # only in the second half
            sh["parts"] = [q for q in sh["parts"] if q != pid]
            if not sh["parts"]:
                b["id"] = a["id"]; out[-1] = b; continue
        out.append(b)
    spec["annotations"] = out


def whole_structure(spec, key):
    """Close-ups of single objects of this structure show all of it (its object numbers are about to change). Returns how
    many there were."""
    n = 0
    for p in spec["shots"]:
        tgt = p.get("target") or {}
        if p["type"] == "focus" and tgt.get("class") == key and tgt.get("component") is not None:
            p["target"] = {"class": key}; n += 1
        elif p["type"] == "focus" and "items" in tgt:
            items = target_items(tgt)
            if any(k == key and c is not None for k, c in items):
                p["target"] = make_target(list(dict.fromkeys((k, None if k == key else c) for k, c in items))); n += 1
    return n


class History:
    """Undo and redo for the movie settings: deep copies of the whole spec, the last `limit` kept."""
    def __init__(self, limit=100):
        self.limit, self.states, self.pos = limit, [], -1

    def clear(self):
        self.states, self.pos = [], -1

    def push(self, spec):
        """Record the settings after a change (or the starting point)."""
        snap = copy.deepcopy(spec)
        if self.pos >= 0 and self.states[self.pos] == snap:
            return
        del self.states[self.pos + 1:]
        self.states.append(snap)
        if len(self.states) > self.limit:
            del self.states[0]
        self.pos = len(self.states) - 1

    def merge(self, new):
        """What is loaded has changed (new: default_spec() of it): every stored state knows the structures that came, as
        the settings do, so each step can still be shown."""
        for s in self.states:
            merge_settings(s, copy.deepcopy(new))

    @property
    def can_undo(self):
        return self.pos > 0

    @property
    def can_redo(self):
        return 0 <= self.pos < len(self.states) - 1

    def undo(self):
        if not self.can_undo:
            return None
        self.pos -= 1
        return copy.deepcopy(self.states[self.pos])

    def redo(self):
        if not self.can_redo:
            return None
        self.pos += 1
        return copy.deepcopy(self.states[self.pos])
