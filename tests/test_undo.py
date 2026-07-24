from __future__ import annotations

from pathlib import Path

from mykr_ops.database import Database
from mykr_ops.models import NotesConfig
from mykr_ops import notes


def make_config(tmp_path: Path) -> NotesConfig:
    source = tmp_path / "Downloads"
    study = tmp_path / "Study"
    source.mkdir()
    study.mkdir()
    return NotesConfig(source, study)


def database_for(tmp_path: Path) -> Database:
    return Database(tmp_path / "state" / "mykr-ops.db")


def apply_one(config: NotesConfig, database: Database, name: str = "01-Topic_CS_Course.md", content: str = "note") -> Path:
    (config.source_dir / name).write_text(content, encoding="utf-8")
    notes.apply_notes(config, database)
    return config.study_root / "CS" / "Course" / "01-Topic.md"


def test_undo_restores_original_complete_filename_and_removes_empty_dirs(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    database = database_for(tmp_path)
    destination = apply_one(config, database)

    result = notes.undo_latest(config, database)
    restored = config.source_dir / "01-Topic_CS_Course.md"

    assert result.moved_count == 1
    assert restored.read_text(encoding="utf-8") == "note"
    assert not destination.exists()
    assert not (config.study_root / "CS").exists()


def test_undo_refuses_changed_destination_content(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    database = database_for(tmp_path)
    destination = apply_one(config, database)
    destination.write_text("newer", encoding="utf-8")

    result = notes.undo_latest(config, database)

    assert result.failed_count == 1
    assert destination.read_text(encoding="utf-8") == "newer"
    assert not (config.source_dir / "01-Topic_CS_Course.md").exists()


def test_undo_refuses_occupied_original_source_path(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    database = database_for(tmp_path)
    apply_one(config, database)
    original = config.source_dir / "01-Topic_CS_Course.md"
    original.write_text("new source", encoding="utf-8")

    result = notes.undo_latest(config, database)

    assert result.failed_count == 1
    assert original.read_text(encoding="utf-8") == "new source"


def test_undo_continues_after_one_failure_and_can_continue_later(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    database = database_for(tmp_path)
    (config.source_dir / "01-One_CS_Course.md").write_text("one", encoding="utf-8")
    (config.source_dir / "02-Two_CS_Course.md").write_text("two", encoding="utf-8")
    notes.apply_notes(config, database)
    changed = config.study_root / "CS" / "Course" / "02-Two.md"
    changed.write_text("changed", encoding="utf-8")

    first_undo = notes.undo_latest(config, database)

    assert first_undo.moved_count == 1
    assert first_undo.failed_count == 1
    assert (config.source_dir / "01-One_CS_Course.md").exists()
    changed.write_text("two", encoding="utf-8")
    second_undo = notes.undo_latest(config, database)

    assert second_undo.moved_count == 1
    assert (config.source_dir / "02-Two_CS_Course.md").exists()
    assert not (config.study_root / "CS").exists()


def test_undo_preserves_preexisting_and_nonempty_directories(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    database = database_for(tmp_path)
    existing = config.study_root / "CS" / "Course"
    existing.mkdir(parents=True)
    destination = apply_one(config, database)
    (existing / "keep.txt").write_text("keep", encoding="utf-8")

    result = notes.undo_latest(config, database)

    assert result.moved_count == 1
    assert existing.exists()
    assert (existing / "keep.txt").exists()
    assert not destination.exists()


def test_undo_reports_no_eligible_run(tmp_path: Path) -> None:
    config = make_config(tmp_path)

    result = notes.undo_latest(config, None)

    assert result.message == "No eligible apply run to undo."
