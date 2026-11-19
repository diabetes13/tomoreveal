"""Short names for lists of long file names: only the part that differs is shown.

Names are split into tokens at "_", "-", ".", spaces and "__" (the separators are kept). The longest token prefix
and suffix that all names share are removed, keeping at least one token. When the shared part is the whole of one
name, that name is shown as "original".
"""
import re

SEP = re.compile(r"(__|[_\-. ])")
ORIGINAL = "original"


def tokens(name):
    return [t for t in SEP.split(name) if t != ""]


def _is_sep(t):
    return bool(SEP.fullmatch(t))


def short_names(names):
    """(shared prefix, shared suffix, [short name per input]). A single name is returned unchanged."""
    names = list(names)
    if len(names) < 2:
        return "", "", list(names)
    toks = [tokens(n) for n in names]
    m = min(len(t) for t in toks)
    p = 0
    while p < m and all(t[p] == toks[0][p] for t in toks):
        p += 1
    s = 0
    while s < m - p and all(t[len(t) - 1 - s] == toks[0][len(toks[0]) - 1 - s] for t in toks):
        s += 1
    # keep at least one token in every name that is longer than the shared part
    out = []
    for t in toks:
        mid = t[p:len(t) - s if s else len(t)]
        while mid and _is_sep(mid[0]):
            mid = mid[1:]
        while mid and _is_sep(mid[-1]):
            mid = mid[:-1]
        out.append("".join(mid) if mid else ORIGINAL)
    if all(o == ORIGINAL for o in out):  # nothing differs (the same name twice)
        return "", "", list(names)
    prefix = "".join(toks[0][:p]); suffix = "".join(toks[0][len(toks[0]) - s:]) if s else ""
    return prefix, suffix, out
