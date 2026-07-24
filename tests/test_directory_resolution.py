from __future__ import annotations

import os
from pathlib import Path

import pytest

from mykr_ops.filesystem import FilesystemSafetyError
from mykr_ops.models import NotesConfig
from mykr_ops.notes import _resolve_destination, parse_note_filename


def make_config(tmp_path: Path) -> NotesConfig:
    source = tmp_path / "Downloads"
    study = tmp_path / "Study"
    source.mkdir()
    study.mkdir()
    return NotesConfig(source, study)


def test_resolves_existing_directories_and_preserves_disk_case(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    course = config.study_root / "cS" / "MachineLearning"
    course.mkdir(parents=True)

    destination, planned = _resolve_destination(parse_note_filename("01-Topic_CS_machinelearning.md"), config)

    assert destination == course / "01-Topic.md"
    assert planned == ()


def test_plans_missing_directory_levels_without_creating_them(tmp_path: Path) -> None:
    config = make_config(tmp_path)

    destination, planned = _resolve_destination(parse_note_filename("01-Topic_CS_Course.md"), config)

    assert destination == config.study_root / "CS" / "Course" / "01-Topic.md"
    assert planned == (config.study_root / "CS", config.study_root / "CS" / "Course")
    assert not (config.study_root / "CS").exists()


def test_plans_only_missing_course_directory(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    (config.study_root / "CS").mkdir()

    _, planned = _resolve_destination(parse_note_filename("01-Topic_CS_Course.md"), config)

    assert planned == (config.study_root / "CS" / "Course",)


def test_rejects_directory_path_occupied_by_file(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    (config.study_root / "CS").write_text("not a directory", encoding="utf-8")

    with pytest.raises(FilesystemSafetyError, match="occupied by a file"):
        _resolve_destination(parse_note_filename("01-Topic_CS_Course.md"), config)


def test_rejects_path_escape_from_study_root(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    parsed = parse_note_filename("01-Topic_CS_Course.md")
    malicious = parsed.__class__(parsed.original_name, parsed.sequence, parsed.topic, "..", parsed.course)

    with pytest.raises(FilesystemSafetyError, match="escapes"):
        _resolve_destination(malicious, config)


def test_rejects_symlink_as_existing_directory(tmp_path: Path) -> None:
    if not hasattr(os, "symlink"):
        pytest.skip("symlinks are unavailable")
    config = make_config(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (config.study_root / "CS").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("this environment cannot create symlinks")

    with pytest.raises(FilesystemSafetyError, match="reparse point"):
        _resolve_destination(parse_note_filename("01-Topic_CS_Course.md"), config)


@pytest.mark.skipif(os.name == "nt", reason="Windows cannot represent case-variant sibling paths")
def test_rejects_multiple_casefold_matches_when_representable(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    (config.study_root / "CS").mkdir()
    (config.study_root / "cs").mkdir()

    with pytest.raises(FilesystemSafetyError, match="multiple case-insensitive"):
        _resolve_destination(parse_note_filename("01-Topic_CS_Course.md"), config)
