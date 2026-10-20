"""Tests for swfactory.tracks: work-id prefixes, ids to tracks, and finding the project root."""
from __future__ import annotations

import pytest

from swfactory.tracks import TRACK_PREFIX, find_project_root, track_for_id


def test_track_prefixes():
    assert TRACK_PREFIX == {"program": "PRG", "feature": "FEA", "fix": "FIX"}


@pytest.mark.parametrize(
    "name, track",
    [
        ("PRG-1", "program"),
        ("PRG-10-big-program", "program"),
        ("FEA-7", "feature"),
        ("FEA-12b", "feature"),
        ("FEA-12b-new-thing", "feature"),
        ("FIX-3", "fix"),
        ("FIX-3-licence-crash", "fix"),
    ],
)
def test_track_for_id_maps_ids_and_work_folder_names(name, track):
    assert track_for_id(name) == track


@pytest.mark.parametrize(
    "name",
    [
        "",
        "fix-3",  # the prefix is upper case
        "FIX",
        "FIX-",
        "FIX-x",
        "FIX3",
        "FOO-1",
        "FIX-3_x",
        "FIX-3 licence",
        "FIX-3b2",  # a letter suffix is one letter, and only after the number
        "FIX-3-",
        "FIX-3\n",
        "T1",
        "factory",
        "../FIX-3",
    ],
)
def test_track_for_id_rejects_anything_else(name):
    with pytest.raises(ValueError, match="work id"):
        track_for_id(name)


def test_find_project_root_from_a_nested_folder(tmp_path):
    (tmp_path / "factory.yaml").write_text("project: x\n", encoding="utf-8")
    work = tmp_path / "factory" / "work" / "FIX-1-x" / "reviews"
    work.mkdir(parents=True)
    assert find_project_root(work) == tmp_path.resolve()


def test_find_project_root_when_start_is_the_root(tmp_path):
    (tmp_path / "factory.yaml").write_text("project: x\n", encoding="utf-8")
    assert find_project_root(tmp_path) == tmp_path.resolve()


def test_find_project_root_takes_the_nearest(tmp_path):
    (tmp_path / "factory.yaml").write_text("project: outer\n", encoding="utf-8")
    inner = tmp_path / "sub" / "project"
    inner.mkdir(parents=True)
    (inner / "factory.yaml").write_text("project: inner\n", encoding="utf-8")
    assert find_project_root(inner / "factory") == inner.resolve()


def test_find_project_root_without_factory_yaml_names_the_start(tmp_path):
    start = tmp_path / "a" / "b"
    start.mkdir(parents=True)
    with pytest.raises(FileNotFoundError) as err:
        find_project_root(start)
    assert str(start) in str(err.value) and "factory.yaml" in str(err.value)
