from __future__ import annotations

import multiprocessing
import os
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
    assert not (config.study_root / "CS").exists()


def test_apply_success_update_failure_is_recovered_and_can_be_undone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = make_config(tmp_path)
    source = config.source_dir / "01-Topic_CS_Course.md"
    source.write_text("note", encoding="utf-8")
    database = database_for(tmp_path)
    original_update = database.update_operation_status

    def fail_success(operation_id: int, status: str, reason: str | None = None) -> None:
        recorded = next(
            row
            for run in database.list_runs()
            for row in database.operations_for_run(int(run["id"]))
            if int(row["id"]) == operation_id
        )
        if status == "success" and recorded["action"] == "move":
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
    assert operation(database, run_id)["error_type"] == "unknown_filesystem_error"


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
    assert operation(database, run_id)["error_type"] == "recovery_ambiguous"
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


def test_directory_intent_is_prepared_before_creation_and_finalized_on_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = make_config(tmp_path)
    source = config.source_dir / "01-Topic_CS_Course.md"
    source.write_text("note", encoding="utf-8")
    database = database_for(tmp_path)
    actual_create = notes.create_child_directory
    observed_prepared = False

    def observe_prepare(*args: object, **kwargs: object) -> tuple[Path, bool]:
        nonlocal observed_prepared
        observed_prepared = any(
            row["action"] == "mkdir" and row["status"] == "prepared"
            for run in database.list_runs()
            for row in database.operations_for_run(int(run["id"]))
        )
        return actual_create(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(notes, "create_child_directory", observe_prepare)
    result = notes.apply_notes(config, database)

    mkdir_operations = [
        row for row in database.operations_for_run(result.run_id or 0) if row["action"] == "mkdir"
    ]
    assert observed_prepared
    assert [row["status"] for row in mkdir_operations] == ["success", "success"]
    assert [row["directory_name"] for row in mkdir_operations] == ["CS", "Course"]
    assert mkdir_operations[0]["source_path"] == str(config.study_root)
    assert mkdir_operations[1]["source_path"] == str(config.study_root / "CS")


def test_interrupted_directory_creation_recovers_exact_safe_directory_as_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = make_config(tmp_path)
    source = config.source_dir / "01-Topic_CS_Course.md"
    source.write_text("note", encoding="utf-8")
    database = database_for(tmp_path)
    original_update = database.update_operation_status

    def fail_directory_success(operation_id: int, status: str, reason: str | None = None) -> None:
        recorded = next(
            row
            for run in database.list_runs()
            for row in database.operations_for_run(int(run["id"]))
            if int(row["id"]) == operation_id
        )
        if recorded["action"] == "mkdir" and status == "success":
            raise sqlite3.OperationalError("directory status update failed")
        original_update(operation_id, status, reason)

    monkeypatch.setattr(database, "update_operation_status", fail_directory_success)
    with pytest.raises(sqlite3.OperationalError):
        notes.apply_notes(config, database)
    monkeypatch.setattr(database, "update_operation_status", original_update)

    run_id = int(database.list_runs()[0]["id"])
    directory_operation = next(
        row for row in database.operations_for_run(run_id) if row["action"] == "mkdir"
    )
    assert directory_operation["status"] == "prepared"
    assert (config.study_root / "CS").is_dir()
    assert not (config.study_root / "CS" / "Course").exists()

    with database.mutation_lock():
        notes.recover_interrupted_operations(config, database)

    directory_operation = next(
        row for row in database.operations_for_run(run_id) if row["action"] == "mkdir"
    )
    assert directory_operation["status"] == "success"


def test_absent_prepared_directory_recovers_as_failed_without_creating_it(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    database = database_for(tmp_path)
    run_id = database.create_run("notes", "apply", matched_count=1)
    database.prepare_operation(
        run_id=run_id,
        sequence_index=1,
        action="mkdir",
        source_path=config.study_root,
        destination_path=config.study_root / "CS",
        directory_name="CS",
    )

    with database.mutation_lock():
        notes.recover_interrupted_operations(config, database)

    directory_operation = next(
        row for row in database.operations_for_run(run_id) if row["action"] == "mkdir"
    )
    assert directory_operation["status"] == "failed"
    assert not (config.study_root / "CS").exists()


def test_current_directory_failure_is_immediately_reconciled_as_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = make_config(tmp_path)
    source = config.source_dir / "01-Topic_CS_Course.md"
    source.write_text("note", encoding="utf-8")
    database = database_for(tmp_path)
    actual_create = notes.create_child_directory

    def create_then_report_failure(*args: object, **kwargs: object) -> tuple[Path, bool]:
        child, created = actual_create(*args, **kwargs)  # type: ignore[arg-type]
        if child.name == "CS" and created:
            raise notes.FilesystemSafetyError("simulated post-create validation failure")
        return child, created

    monkeypatch.setattr(notes, "create_child_directory", create_then_report_failure)
    result = notes.apply_notes(config, database)

    mkdir_operations = [
        row for row in database.operations_for_run(result.run_id or 0) if row["action"] == "mkdir"
    ]
    assert result.moved_count == 1
    assert [row["status"] for row in mkdir_operations] == ["success", "success"]
    assert (config.study_root / "CS" / "Course" / "01-Topic.md").exists()


def test_current_directory_clear_failure_allows_later_independent_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = make_config(tmp_path)
    first = config.source_dir / "01-Fail_CS_Course.md"
    second = config.source_dir / "02-Works_Math_Algebra.md"
    first.write_text("first", encoding="utf-8")
    second.write_text("second", encoding="utf-8")
    database = database_for(tmp_path)
    actual_create = notes.create_child_directory

    def fail_absent_cs(parent: Path, name: str, root: Path, **kwargs: object) -> tuple[Path, bool]:
        if name == "CS":
            raise notes.FilesystemSafetyError("simulated directory lock")
        return actual_create(parent, name, root, **kwargs)

    monkeypatch.setattr(notes, "create_child_directory", fail_absent_cs)
    result = notes.apply_notes(config, database)

    operations = database.operations_for_run(result.run_id or 0)
    cs_directory = next(row for row in operations if row["action"] == "mkdir" and row["directory_name"] == "CS")
    assert cs_directory["status"] == "failed"
    assert cs_directory["error_type"] == "unknown_filesystem_error"
    assert result.failed_count == 1
    assert result.moved_count == 1
    assert first.exists()
    assert (config.study_root / "Math" / "Algebra" / "02-Works.md").exists()


def test_current_directory_ambiguous_failure_stops_batch_immediately(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = make_config(tmp_path)
    first = config.source_dir / "01-First_CS_Course.md"
    second = config.source_dir / "02-Second_Math_Algebra.md"
    first.write_text("first", encoding="utf-8")
    second.write_text("second", encoding="utf-8")
    database = database_for(tmp_path)

    def leave_ambiguous_file(parent: Path, name: str, *_: object, **__: object) -> tuple[Path, bool]:
        child = parent / name
        child.write_text("user file", encoding="utf-8")
        raise notes.FilesystemSafetyError("simulated directory validation failure")

    monkeypatch.setattr(notes, "create_child_directory", leave_ambiguous_file)
    result = notes.apply_notes(config, database)

    operations = database.operations_for_run(result.run_id or 0)
    mkdir_operations = [row for row in operations if row["action"] == "mkdir"]
    mkdir_operation = mkdir_operations[0]
    move_operations = [row for row in operations if row["action"] == "move"]
    assert result.recovery_operation_id == mkdir_operation["id"]
    assert mkdir_operation["status"] == "recovery_required"
    assert result.failed_count == 2
    assert len(mkdir_operations) == 1
    assert len(move_operations) == 1
    assert move_operations[0]["status"] == "failed"
    assert first.exists() and second.exists()
    assert (config.study_root / "CS").read_text(encoding="utf-8") == "user file"
    assert not (config.study_root / "Math").exists()
    assert "manual_recovery_operation=" + str(mkdir_operation["id"]) in database.get_run(result.run_id or 0)["summary"]


@pytest.mark.parametrize("state", ["file", "wrong_parent"])
def test_ambiguous_prepared_directory_requires_manual_recovery_without_changes(
    tmp_path: Path, state: str
) -> None:
    config = make_config(tmp_path)
    database = database_for(tmp_path)
    destination = config.study_root / "CS"
    if state == "file":
        destination.write_text("user file", encoding="utf-8")
        parent = config.study_root
    else:
        destination.mkdir()
        parent = config.study_root / "Other"
    run_id = database.create_run("notes", "apply", matched_count=1)
    database.prepare_operation(
        run_id=run_id,
        sequence_index=1,
        action="mkdir",
        source_path=parent,
        destination_path=destination,
        directory_name="CS",
    )

    with database.mutation_lock(), pytest.raises(notes.RecoveryRequiredError):
        notes.recover_interrupted_operations(config, database)

    directory_operation = next(
        row for row in database.operations_for_run(run_id) if row["action"] == "mkdir"
    )
    assert directory_operation["status"] == "recovery_required"
    if state == "file":
        assert destination.read_text(encoding="utf-8") == "user file"
    else:
        assert destination.is_dir()


@pytest.mark.skipif(os.name != "nt", reason="Windows reparse-point integration")
def test_reparse_prepared_directory_requires_manual_recovery_without_changes(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    database = database_for(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    destination = config.study_root / "CS"
    try:
        destination.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"creating a directory symlink is unavailable: {exc}")
    run_id = database.create_run("notes", "apply", matched_count=1)
    database.prepare_operation(
        run_id=run_id,
        sequence_index=1,
        action="mkdir",
        source_path=config.study_root,
        destination_path=destination,
        directory_name="CS",
    )

    with database.mutation_lock(), pytest.raises(notes.RecoveryRequiredError):
        notes.recover_interrupted_operations(config, database)

    directory_operation = next(
        row for row in database.operations_for_run(run_id) if row["action"] == "mkdir"
    )
    assert directory_operation["status"] == "recovery_required"
    assert destination.is_symlink()


def test_apply_stops_immediately_when_current_prepared_operation_requires_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = make_config(tmp_path)
    first = config.source_dir / "01-First_CS_Course.md"
    second = config.source_dir / "02-Second_Math_Algebra.md"
    first.write_text("first", encoding="utf-8")
    second.write_text("second", encoding="utf-8")
    database = database_for(tmp_path)

    def fail_move(*_: object, **__: object) -> None:
        raise notes.FilesystemSafetyError("simulated native rename failure")

    monkeypatch.setattr(notes, "move_file_without_overwrite", fail_move)
    monkeypatch.setattr(
        notes,
        "_classify_prepared_operation",
        lambda *_: ("recovery_required", "ambiguous filesystem state", "recovery_ambiguous"),
    )

    result = notes.apply_notes(config, database)

    assert result.recovery_operation_id is not None
    assert result.moved_count == 0
    assert result.failed_count == 2
    assert first.exists() and second.exists()
    assert not (config.study_root / "Math").exists()
    operations = database.operations_for_run(result.run_id or 0)
    move_operations = [row for row in operations if row["action"] == "move"]
    assert len(move_operations) == 1
    assert move_operations[0]["status"] == "recovery_required"
    assert database.get_run(result.run_id or 0)["status"] == "failed"


def test_undo_stops_immediately_when_current_prepared_operation_requires_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = make_config(tmp_path)
    first = config.source_dir / "01-First_CS_Course.md"
    second = config.source_dir / "02-Second_CS_Course.md"
    first.write_text("first", encoding="utf-8")
    second.write_text("second", encoding="utf-8")
    database = database_for(tmp_path)
    notes.apply_notes(config, database)

    def fail_move(*_: object, **__: object) -> None:
        raise notes.FilesystemSafetyError("simulated native rename failure")

    monkeypatch.setattr(notes, "move_file_without_overwrite", fail_move)
    monkeypatch.setattr(
        notes,
        "_classify_prepared_operation",
        lambda *_: ("recovery_required", "ambiguous filesystem state", "recovery_ambiguous"),
    )

    result = notes.undo_latest(config, database)

    assert result.recovery_operation_id is not None
    assert result.moved_count == 0
    assert result.failed_count == 1
    assert not first.exists() and not second.exists()
    assert (config.study_root / "CS" / "Course" / "01-First.md").exists()
    assert (config.study_root / "CS" / "Course" / "02-Second.md").exists()
    operations = database.operations_for_run(result.run_id or 0)
    assert [row["action"] for row in operations] == ["undo_move"]
    assert operations[0]["status"] == "recovery_required"
    assert database.get_run(result.run_id or 0)["status"] == "failed"


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
