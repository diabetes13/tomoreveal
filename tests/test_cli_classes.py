"""Class numbers in the CLI are the numbers it prints (F-29)."""
import argparse
import json
import re

import numpy as np
import pytest

from tomoreveal.cli import cli_spec, cli_studio
from tomoreveal.common import UserError
from tomoreveal.volumes import load_volume, write_mrc

from fake_session import write_session


def args(**kw):
    a = dict(headless=True, smooth=15, mesh_step=1, keep_background=False, threshold=0.5, tomogram=None, session=None,
             labels=None, features=[], roi=None, classes=None, only=None, exclude=None, bg="#000000", contrast=(1.0, 99.0),
             invert=False, ortho=False, fov=30.0, zoom=1.0, clip=None, size=(320, 180), no_aa=True)
    a.update(kw)
    return argparse.Namespace(**a)


def shown(st, spec):
    return {c["name"] for k, c in st.ds.classes.items() if spec["classes"][st.ds.key_of(k)]["show"]}


def printed(out):
    """{name: number} from the 'class N ... name' lines."""
    return {m.group(2).split("  (")[0]: m.group(1) for m in re.finditer(r"class\s+(\S+)\s+#\w+\s+[\d,]+ voxels\s+(.+)", out)}


def test_printed_number_of_a_file_with_gaps_hides_that_structure(tmp_path, demo, capsys):
    lab, vox = load_volume(demo["labels"])
    p = str(tmp_path / "gaps.mrc"); write_mrc(p, ((lab == 1) * 1 + (lab == 3) * 3).astype("int8"), vox)
    a = args(tomogram=demo["tomogram"], labels=p)
    st, _ = cli_studio(a); cli_spec(st, a)
    nums = printed(capsys.readouterr().out)
    third = next(c["name"] for c in st.ds.classes.values() if c.get("label") == 3)
    a.exclude = nums[third]
    spec = cli_spec(st, a)
    assert third not in shown(st, spec) and len(shown(st, spec)) == 1


def test_a_number_in_two_sources_or_in_none_is_an_error(tmp_path, demo):
    lab, vox = load_volume(demo["labels"]); tomo, _ = load_volume(demo["tomogram"])
    ses = write_session(str(tmp_path / "s.ORSSession"), dims=lab.shape[::-1], labels=lab, image=tomo,
                        names=("membrane", "vesicles", "filaments", "particles"), colors=(0x6E56D7, 0xE8B945, 0x3FD2FF, 0x86E9B8),
                        visible=(1, 1, 1, 1), title="Segmentation demo")
    other = str(tmp_path / "other.mrc"); write_mrc(other, (lab == 2).astype("int8") * 2, vox)
    a = args(tomogram=demo["tomogram"], session=ses, labels=other)
    st, _ = cli_studio(a)
    a.exclude = "2"
    with pytest.raises(UserError) as e:
        cli_spec(st, a)
    assert "vesicles" in str(e.value) and "other.mrc" in str(e.value)
    a.exclude = "99"
    with pytest.raises(UserError):
        cli_spec(st, a)


def test_classes_file_keyed_by_name(tmp_path, demo):
    cj = tmp_path / "classes.json"; cj.write_text(json.dumps({"vesicles": {"name": "my vesicles", "show": False}}), encoding="utf-8")
    a = args(tomogram=demo["tomogram"], labels=demo["labels"], classes=str(cj))
    st, _ = cli_studio(a); spec = cli_spec(st, a)
    names = {c["name"] for c in st.ds.classes.values()}
    assert "my vesicles" in names and "my vesicles" not in shown(st, spec) and len(shown(st, spec)) >= 2
