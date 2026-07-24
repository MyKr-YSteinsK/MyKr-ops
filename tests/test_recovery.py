from __future__ import annotations

import multiprocessing
import sqlite3
from pathlib import Path

import pytest

from mykr_ops import notes
from mykr_ops.database import Database, MutationLockError
from mykr_ops.models import NotesConfig


def make_config(tmp_path: Path) -> NotesConfig:
    source = tmp_path / "Downloads"
    study = tmp_path / "Study"
    source.mkdir()
    study.mkdir()
    return NotesConfig(source, study)


def database_for(tmp_path: Path) -> Database:
    return Database(tmp_path / "state" / "mykr-ops.db")


def prepared_apply(database: Database, config: NotesConfig, content: str = "note") -> tuple[int, int, Path, Path]:
    source = config.source_dir / "01-Topic_CS_Course.md"
    destination = config.study_root / "CS" / "Course" / "01-Topic.md"
    run_id = database.create_run("notes", "apply", matched_count=1)
    digest = notes.sha256_file(source) if source.exists() else notes.sha256_file(destination)
    operation_id = database.prepare_operation(
        run_id=run_id,
        sequence_index=1,
        action="move",
        source_path=source,
        destination_path=destination,
        file_size=len(content),
        source_mtime_ns=1,
        sha256=digest,
    )
    return run_id, operation_id, source, destination


def operation(database: Database, run_id: int) -> object:
    return next(row for row in database.operations_for_run(run_id) if row["action"] == "move")


def test_prepared_insert_failure_does_not_move_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = make_config(tmp_path)
    source = config.source_dir / "01-Topic_CS_Course.md"
    source.write_text("note", encoding="utf-8")
    database = database_for(tmp_path)

    def fail_prepare(**_: object) -> int:
        raise sqlite3.OperationalError("database unavailable")

    def move_must_not_run(*_: object) -> None:
        raise AssertionError("move must not run before a prepared row commits")

    monkeypatch.setattr(database, "prepare_operation", fail_prepare)
    monkeypatch.setattr(notes, "move_file_without_overwrite", move_must_not_run)

    with pytest.raises(sqlite3.OperationalError):
        notes.apply_notes(config, database)

    assert source.read_text(encoding="utf-8") == "note"
    assert not (config.study_root / "CS" / "Course" / "01-Topic.md").exists()


def test_apply_success_update_failure_is_recovered_and_can_be_undone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = make_config(tmp_path)
    source = config.source_dir / "01-Topic_CS_Course.md"
    source.write_text("note", encoding="utf-8")
    database = database_for(tmp_path)
    original_update = database.update_operation_status

    def fail_success(operation_id: int, status: str, reason: str | None = None) -> None:
        if status == "success":
            raise sqlite3.OperationalError("final update failed")
        original_update(operation_id, status, reason)

    monkeypatch.setattr(database, "update_operation_status", fail_success)
    with pytest.raises(sqlite3.OperationalError):
        notes.apply_notes(config, database)

    destination = config.study_root / "CS" / "Course" / "01-Topic.md"
    run = database.list_runs()[0]
    assert not source.exists()
    assert destination.read_text(encoding="utf-8") == "note"
    assert operation(database, int(run["id"]))["status"] == "prepared"
    monkeypatch.setattr(database, "update_operation_status", original_update)

    with database.mutation_lock():
        notes.recover_interrupted_operations(config, database)
    assert operation(database, int(run["id"]))["status"] == "success"
    assert notes.undo_latest(config, database).moved_count == 1
    assert source.read_text(encoding="utf-8") == "note"


def test_interrupted_undo_recovers_atomically(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = make_config(tmp_path)
    source = config.source_dir / "01-Topic_CS_Course.md"
    source.write_text("note", encoding="utf-8")
    database = database_for(tmp_path)
    notes.apply_notes(config, database)
    original_finalize = database.finalize_undo_operation

    def fail_finalize(*_: object) -> None:
        raise sqlite3.OperationalError("final undo update failed")

    monkeypatch.setattr(database, "finalize_undo_operation", fail_finalize)
    with pytest.raises(sqlite3.OperationalError):
        notes.undo_latest(config, database)
    monkeypatch.setattr(database, "finalize_undo_operation", original_finalize)

    apply_run = next(row for row in database.list_runs() if row["mode"] == "apply")
    apply_operation = operation(database, int(apply_run["id"]))
    undo_run = next(row for row in database.list_runs() if row["mode"] == "undo")
    undo_operation = next(row for row in database.operations_for_run(int(undo_run["id"])) if row["action"] == "undo_move")
    assert undo_operation["status"] == "prepared"
    assert apply_operation["undone_at"] is None

    with database.mutation_lock():
        notes.recover_interrupted_operations(config, database)
    apply_operation = operation(database, int(apply_run["id"]))
    undo_operation = next(row for row in database.operations_for_run(int(undo_run["id"])) if row["action"] == "undo_move")
    assert undo_operation["status"] == "success"
    assert apply_operation["undone_at"] is not None
    assert notes.undo_latest(config, database).message == "No eligible apply run to undo."


def test_confirmed_pre_move_state_recovers_as_failed_without_file_changes(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    source = config.source_dir / "01-Topic_CS_Course.md"
    source.write_text("note", encoding="utf-8")
    database = database_for(tmp_path)
    run_id, _, _, destination = prepared_apply(database, config)

    with database.mutation_lock():
        notes.recover_interrupted_operations(config, database)

    assert source.read_text(encoding="utf-8") == "note"
    assert not destination.exists()
    assert operation(database, run_id)["status"] == "failed"


@pytest.mark.parametrize(
    "state",
    ["both_match", "both_different", "neither", "destination_wrong", "source_changed", "destination_directory"],
)
def test_ambiguous_prepared_states_require_manual_recovery(tmp_path: Path, state: str) -> None:
    config = make_config(tmp_path)
    source = config.source_dir / "01-Topic_CS_Course.md"
    destination = config.study_root / "CS" / "Course" / "01-Topic.md"
    if state in {"both_match", "both_different", "source_changed"}:
        source.write_text("note" if state != "source_changed" else "changed", encoding="utf-8")
    if state in {"both_match", "both_different", "destination_wrong"}:
        destination.parent.mkdir(parents=True)
        destination.write_text("note" if state == "both_match" else "different", encoding="utf-8")
    if state == "destination_directory":
        destination.mkdir(parents=True)
    # The expected digest must represent the original note, independent of the ambiguous copies.
    reference = tmp_path / "reference.md"
    reference.write_text("note", encoding="utf-8")
    database = database_for(tmp_path)
    run_id = database.create_run("notes", "apply", matched_count=1)
    database.prepare_operation(
        run_id=run_id,
        sequence_index=1,
        action="move",
        source_path=source,
        destination_path=destination,
        file_size=4,
        source_mtime_ns=1,
        sha256=notes.sha256_file(reference),
    )

    with database.mutation_lock(), pytest.raises(notes.RecoveryRequiredError):
        notes.recover_interrupted_operations(config, database)

    assert operation(database, run_id)["status"] == "recovery_required"
    assert not source.exists() or source.read_text(encoding="utf-8") in {"note", "changed"}
    assert not destination.exists() or destination.is_dir() or destination.read_text(encoding="utf-8") in {"note", "different"}
    with pytest.raises(notes.RecoveryRequiredError):
        notes.apply_notes(config, database)


def test_interrupted_run_summary_uses_recorded_item_count(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    database = database_for(tmp_path)
    run_id = database.create_run("notes", "apply", matched_count=2)
    database.record_operation(run_id=run_id, sequence_index=1, action="move", status="success")

    with database.mutation_lock():
        notes.recover_interrupted_operations(config, database)

    run = database.get_run(run_id)
    assert run["status"] == "partial"
    assert "Recovered after interruption" in run["summary"]
    assert "recorded=1 of expected=2" in run["summary"]


def test_manually_resolved_recovery_required_operation_is_reconciled_again(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    source = config.source_dir / "01-Topic_CS_Course.md"
    destination = config.study_root / "CS" / "Course" / "01-Topic.md"
    source.write_text("note", encoding="utf-8")
    destination.parent.mkdir(parents=True)
    destination.write_text("note", encoding="utf-8")
    database = database_for(tmp_path)
    run_id, _, _, _ = prepared_apply(database, config)

    with database.mutation_lock(), pytest.raises(notes.RecoveryRequiredError):
        notes.recover_interrupted_operations(config, database)
    destination.unlink()
    with database.mutation_lock():
        notes.recover_interrupted_operations(config, database)

    assert operation(database, run_id)["status"] == "failed"
    assert database.get_run(run_id)["status"] == "failed"


def _hold_lock(path: str, ready: object, release: object) -> None:
    database = Database(Path(path))
    with database.mutation_lock():
        ready.set()
        release.wait(10)


def test_mutation_lock_blocks_another_process_without_creating_database(tmp_path: Path) -> None:
    database = database_for(tmp_path)
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    release = context.Event()
    process = context.Process(target=_hold_lock, args=(str(database.path), ready, release))
    process.start()
    try:
        assert ready.wait(10)
        with pytest.raises(MutationLockError):
            with database.mutation_lock():
                pass
        assert not database.path.exists()
    finally:
        release.set()
        process.join(10)
    assert process.exitcode == 0
    with database.mutation_lock():
        pass
