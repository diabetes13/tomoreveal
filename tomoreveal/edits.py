"""Edits of the movie parts (one undo step each in the window, see gui_movie.MovieEditor) and the undo history.
Parts are always found by their id, never by their position in the list."""
import copy

from .common import UserError
from .storyboard import build_plan, main_range, merge_settings, migrate_spec, new_id


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
    """Split a slide or a hold at a frame of its main phase into two parts."""
    i = _index(spec, part_id); p = spec["shots"][i]
    if p.get("type") not in ("slide", "hold"):
        raise UserError("This kind of part cannot be split.")
    plan = build_plan(spec, ds); rng = main_range(plan, part_id)
    if rng is None or not (rng[0] < frame < rng[0] + rng[1]):
        raise UserError("Put the playhead inside the part (not at its very start or end) to split it.")
    start, count = rng; f = (frame - start) / count; secs = float(p.get("seconds", 5))
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
    return spec


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
