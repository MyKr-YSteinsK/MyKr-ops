from __future__ import annotations

import io
import logging
import os
from pathlib import Path

import pytest

from mykr_ops import cli, filesystem, notes
from mykr_ops.database import Database
from mykr_ops.models import NotesConfig


def make_config(tmp_path: Path) -> NotesConfig:
    source = tmp_path / "Downloads"
    study = tmp_path / "Study"
    source.mkdir()
    study.mkdir()
    return NotesConfig(source, study)


def database_for(tmp_path: Path) -> Database:
    return Database(tmp_path / "state" / "mykr-ops.db")


def test_history_detail_shows_operation_error_type(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    database = database_for(tmp_path)
    run_id = database.create_run("notes", "apply", matched_count=1)
    database.record_operation(
        run_id=run_id,
        sequence_index=1,
        action="move",
        status="failed",
        source_path=tmp_path / "source.md",
        destination_path=tmp_path / "destination.md",
        reason="destination already exists",
        error_type="destination_exists",
    )

    cli._print_history(database, run_id)

    assert "error_type=destination_exists" in capsys.readouterr().out


def test_operation_log_includes_structured_error_type() -> None:
    logger = logging.getLogger("mykr_ops.error_reporting_test")
    logger.handlers.clear()
    logger.propagate = False
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    logger.addHandler(handler)
    try:
        notes._log_operation(
            logger,
            level=logging.ERROR,
            run_id=7,
            action="move",
            status="failed",
            source=Path("source.md"),
            destination=Path("destination.md"),
            reason="destination already exists",
            error_type="destination_exists",
        )
    finally:
        logger.removeHandler(handler)

    assert "error_type=destination_exists" in stream.getvalue()


@pytest.mark.skipif(os.name != "nt", reason="Windows sharing-mode integration")
def test_windows_locked_source_is_recorded_with_error_type(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = make_config(tmp_path)
    source = config.source_dir / "01-Topic_CS_Course.md"
    source.write_text("note", encoding="utf-8")
    database = database_for(tmp_path)
    actual_ensure = notes._ensure_destination_directories
    held_handle: int | None = None

    def lock_source(parsed: object, current_config: NotesConfig) -> tuple[Path, list[Path]]:
        nonlocal held_handle
        destination, created = actual_ensure(parsed, current_config)  # type: ignore[arg-type]
        held_handle = filesystem._create_file(
            str(source), filesystem._GENERIC_READ, 0, None, filesystem._OPEN_EXISTING, 0, None
        )
        assert held_handle != filesystem._INVALID_HANDLE_VALUE
        return destination, created

    monkeypatch.setattr(notes, "_ensure_destination_directories", lock_source)
    try:
        result = notes.apply_notes(config, database)
    finally:
        if held_handle is not None:
            filesystem._close_handle(held_handle)

    operation = next(row for row in database.operations_for_run(result.run_id or 0) if row["action"] == "move")
    assert operation["status"] == "failed"
    assert operation["error_type"] == "source_locked"
    assert source.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows sharing-mode integration")
def test_windows_locked_destination_is_recorded_with_error_type(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = make_config(tmp_path)
    source = config.source_dir / "01-Topic_CS_Course.md"
    source.write_text("note", encoding="utf-8")
    destination_parent = config.study_root / "CS" / "Course"
    destination_parent.mkdir(parents=True)
    database = database_for(tmp_path)
    actual_move = notes.move_file_without_overwrite

    def lock_destination(source_path: Path, destination: Path, digest: str, **kwargs: object) -> None:
        held_handle = filesystem._create_file(
            str(destination.parent), filesystem._GENERIC_READ, 0, None, filesystem._OPEN_EXISTING,
            filesystem._FILE_FLAG_BACKUP_SEMANTICS, None,
        )
        assert held_handle != filesystem._INVALID_HANDLE_VALUE
        try:
            actual_move(source_path, destination, digest, **kwargs)
        finally:
            filesystem._close_handle(held_handle)

    monkeypatch.setattr(notes, "move_file_without_overwrite", lock_destination)
    result = notes.apply_notes(config, database)

    operation = next(row for row in database.operations_for_run(result.run_id or 0) if row["action"] == "move")
    assert operation["status"] == "failed"
    assert operation["error_type"] == "destination_locked"
    assert source.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows reparse-point integration")
def test_windows_junction_or_symlink_target_is_recorded_as_unsafe(
    tmp_path: Path
) -> None:
    config = make_config(tmp_path)
    source = config.source_dir / "01-Topic_CS_Course.md"
    source.write_text("note", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    target = config.study_root / "CS"
    try:
        target.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"creating a directory symlink is unavailable: {exc}")
    database = database_for(tmp_path)

    result = notes.apply_notes(config, database)

    operation = next(row for row in database.operations_for_run(result.run_id or 0) if row["action"] == "move")
    assert operation["status"] == "failed"
    assert operation["error_type"] == "unsafe_reparse_point"
    assert source.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows case-insensitive path integration")
def test_windows_case_variant_target_conflict_is_classified(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    source = config.source_dir / "01-Topic_CS_Course.md"
    source.write_text("source", encoding="utf-8")
    target = config.study_root / "CS" / "Course" / "01-TOPIC.md"
    target.parent.mkdir(parents=True)
    target.write_text("different", encoding="utf-8")
    database = database_for(tmp_path)

    result = notes.apply_notes(config, database)

    operation = next(row for row in database.operations_for_run(result.run_id or 0) if row["action"] == "move")
    assert result.conflict_count == 1
    assert operation["error_type"] == "destination_conflict"
    assert target.read_text(encoding="utf-8") == "different"
