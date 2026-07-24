from __future__ import annotations

import os
from pathlib import Path

import pytest

from mykr_ops.database import Database
from mykr_ops.models import NotesConfig, PlanStatus
from mykr_ops import notes


def make_config(tmp_path: Path) -> NotesConfig:
    source = tmp_path / "Downloads"
    study = tmp_path / "Study"
    source.mkdir()
    study.mkdir()
    return NotesConfig(source, study)


def write_note(config: NotesConfig, name: str, content: str = "note") -> Path:
    path = config.source_dir / name
    path.write_text(content, encoding="utf-8")
    return path


def test_ignores_unrelated_files_and_subdirectories(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    write_note(config, "readme.txt")
    nested = config.source_dir / "nested"
    nested.mkdir()
    (nested / "01-Topic_CS_Course.md").write_text("note", encoding="utf-8")

    plan = notes.plan_notes(config)

    assert plan.items == []
    assert plan.ignored_count == 2


def test_plans_ready_without_side_effects_or_history(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    write_note(config, "01-Topic_CS_Course.md")
    database_path = tmp_path / "state" / "mykr-ops.db"

    plan = notes.plan_notes(config)

    assert plan.items[0].status == PlanStatus.READY
    assert not (config.study_root / "CS").exists()
    assert not database_path.exists()
    assert not Database(database_path).path.exists()


def test_marks_identical_target_as_duplicate(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    write_note(config, "01-Topic_CS_Course.md", "same")
    target = config.study_root / "CS" / "Course" / "01-Topic.md"
    target.parent.mkdir(parents=True)
    target.write_text("same", encoding="utf-8")

    plan = notes.plan_notes(config)

    assert plan.items[0].status == PlanStatus.DUPLICATE


def test_marks_different_target_as_conflict(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    write_note(config, "01-Topic_CS_Course.md", "source")
    target = config.study_root / "CS" / "Course" / "01-Topic.md"
    target.parent.mkdir(parents=True)
    target.write_text("target", encoding="utf-8")

    assert notes.plan_notes(config).items[0].status == PlanStatus.CONFLICT


def test_marks_invalid_note_attempt(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    write_note(config, "00-Topic_CS_Course.md")

    plan = notes.plan_notes(config)

    assert plan.items[0].status == PlanStatus.INVALID


def test_marks_hashing_error_as_failed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = make_config(tmp_path)
    write_note(config, "01-Topic_CS_Course.md")

    def fail_hash(_: Path) -> str:
        raise OSError("locked")

    monkeypatch.setattr(notes, "sha256_file", fail_hash)
    plan = notes.plan_notes(config)

    assert plan.items[0].status == PlanStatus.FAILED
    assert "locked" in plan.items[0].reason


@pytest.mark.skipif(os.name == "nt", reason="Windows cannot represent case-variant source files")
def test_detects_multiple_sources_for_one_casefold_target(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    write_note(config, "01-Topic_CS_Course.md")
    write_note(config, "01-topic_CS_Course.md")

    assert {item.status for item in notes.plan_notes(config).items} == {PlanStatus.CONFLICT}
