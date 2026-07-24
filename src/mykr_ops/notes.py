from __future__ import annotations

from collections import Counter, defaultdict
import logging
import sqlite3
from pathlib import Path
from typing import Iterable

from .database import Database, DatabaseSchemaError, MutationLockError
from .filesystem import (
    FilesystemSafetyError,
    assert_ordinary_directory,
    assert_within,
    create_child_directory,
    direct_casefold_matches,
    is_ordinary_file,
    is_reparse_point,
    move_file_without_overwrite,
    path_exists_no_follow,
    remove_empty_directory,
    resolve_child_directory,
    sha256_file,
    snapshot_matches,
)
from .models import (
    NotesConfig,
    ParsedNote,
    PlanItem,
    PlanResult,
    PlanStatus,
    RunResult,
    UndoItem,
    UndoResult,
)


INVALID_WINDOWS_CHARACTERS = set('<>:"/\\|?*')
RESERVED_WINDOWS_NAMES = {
    ".",
    "..",
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}


class FilenameError(ValueError):
    """Raised when a filename violates the study-note contract."""


class DirectoryCreationError(FilesystemSafetyError):
    """Retains directories created before a later directory step failed."""

    def __init__(self, message: str, created_directories: list[Path]) -> None:
        super().__init__(message)
        self.created_directories = created_directories


class RecoveryRequiredError(RuntimeError):
    """Raised when durable operation records cannot be reconciled safely."""

    def __init__(self, operations: list[object]) -> None:
        self.operations = operations
        identifiers = ", ".join(
            f"run {operation['run_id']} operation {operation['id']}" for operation in operations
        )
        super().__init__(
            f"manual recovery is required for {identifiers}; inspect history and resolve the filesystem state"
        )


def _is_reserved_windows_name(component: str) -> bool:
    return component.split(".", 1)[0].upper() in RESERVED_WINDOWS_NAMES


def _validate_component(value: str, label: str, *, allow_underscores: bool) -> None:
    if not value:
        raise FilenameError(f"{label} is empty")
    if value != value.strip():
        raise FilenameError(f"{label} has leading or trailing whitespace")
    if any(character in INVALID_WINDOWS_CHARACTERS for character in value):
        raise FilenameError(f"{label} contains an invalid Windows filename character")
    if not allow_underscores and "_" in value:
        raise FilenameError(f"{label} must not contain an underscore")
    if value.endswith((" ", ".")):
        raise FilenameError(f"{label} must not end in a space or period")
    if _is_reserved_windows_name(value):
        raise FilenameError(f"{label} is a reserved Windows device name")


def parse_note_filename(filename: str) -> ParsedNote:
    """Parse one note filename by splitting the last two ASCII underscores."""
    if Path(filename).name != filename:
        raise FilenameError("filename must not contain a path")
    if not filename.endswith(".md"):
        raise FilenameError("extension must be exactly lowercase .md")
    stem = filename[:-3]
    parts = stem.rsplit("_", 2)
    if len(parts) != 3:
        raise FilenameError("filename must end with _<directory>_<course>.md")
    prefix, first_level, course = parts
    if len(prefix) < 4 or prefix[2] != "-":
        raise FilenameError("sequence must be exactly two digits followed by -")
    sequence = prefix[:2]
    topic = prefix[3:]
    if not sequence.isascii() or not sequence.isdigit() or not 1 <= int(sequence) <= 99:
        raise FilenameError("sequence must be from 01 through 99")
    _validate_component(topic, "note topic", allow_underscores=True)
    _validate_component(first_level, "first-level directory", allow_underscores=False)
    _validate_component(course, "course name", allow_underscores=False)
    target_name = f"{sequence}-{topic}.md"
    if any(character in INVALID_WINDOWS_CHARACTERS for character in target_name):
        raise FilenameError("final renamed note filename is not valid for Windows")
    return ParsedNote(
        original_name=filename,
        sequence=sequence,
        topic=topic,
        first_level=first_level,
        course=course,
    )


def _looks_like_note_attempt(filename: str) -> bool:
    """Distinguish malformed note attempts from unrelated files for reporting."""
    return filename.endswith(".md") and filename[:-3].count("_") >= 2


def _validate_roots(config: NotesConfig) -> None:
    assert_ordinary_directory(config.source_dir, "source directory")
    assert_ordinary_directory(config.study_root, "study root")


def _resolve_destination(parsed: ParsedNote, config: NotesConfig) -> tuple[Path, tuple[Path, ...]]:
    root = config.study_root
    first_level, needs_first = resolve_child_directory(root, parsed.first_level, root)
    if needs_first:
        course = first_level / parsed.course
        assert_within(course, root)
        needs_course = True
    else:
        course, needs_course = resolve_child_directory(first_level, parsed.course, root)
    destination = course / parsed.target_name
    assert_within(destination, root)
    planned_directories: list[Path] = []
    if needs_first:
        planned_directories.append(first_level)
    if needs_course:
        planned_directories.append(course)
    return destination, tuple(planned_directories)


def _inspect_target(item: PlanItem, config: NotesConfig) -> None:
    assert item.destination is not None
    # A missing directory level proves that no target exists there yet. Preview
    # must not create it merely to inspect a filename.
    if item.planned_directories:
        item.status = PlanStatus.READY
        return
    matches = direct_casefold_matches(item.destination.parent, item.destination.name)
    if len(matches) > 1:
        item.status = PlanStatus.FAILED
        item.reason = "multiple case-insensitive matches exist for the target filename"
        return
    if not matches:
        item.status = PlanStatus.READY
        return
    target = matches[0]
    item.destination = target
    if is_reparse_point(target):
        item.status = PlanStatus.FAILED
        item.reason = "target is a symbolic link, junction, or reparse point"
        return
    if target.is_dir():
        item.status = PlanStatus.CONFLICT
        item.reason = "target filename is occupied by a directory"
        return
    if not is_ordinary_file(target):
        item.status = PlanStatus.FAILED
        item.reason = "target is not an ordinary file"
        return
    try:
        if sha256_file(target) == item.source_sha256:
            item.status = PlanStatus.DUPLICATE
            item.reason = "target already exists with identical SHA-256 content"
        else:
            item.status = PlanStatus.CONFLICT
            item.reason = "target already exists with different content"
    except OSError as exc:
        item.status = PlanStatus.FAILED
        item.reason = f"could not hash target: {exc}"


def plan_notes(config: NotesConfig) -> PlanResult:
    """Inspect direct source files without creating directories or persistence records."""
    _validate_roots(config)
    result = PlanResult()
    try:
        entries = sorted(config.source_dir.iterdir(), key=lambda entry: entry.name.casefold())
    except OSError as exc:
        raise FilesystemSafetyError(f"could not scan source directory: {exc}") from exc

    for source in entries:
        if not is_ordinary_file(source):
            result.ignored_count += 1
            continue
        try:
            parsed = parse_note_filename(source.name)
        except FilenameError as exc:
            if _looks_like_note_attempt(source.name):
                result.items.append(
                    PlanItem(source=source, status=PlanStatus.INVALID, reason=str(exc))
                )
            else:
                result.ignored_count += 1
            continue

        item = PlanItem(source=source, status=PlanStatus.FAILED, parsed=parsed)
        try:
            metadata = source.stat()
            item.source_size = metadata.st_size
            item.source_mtime_ns = metadata.st_mtime_ns
            item.source_sha256 = sha256_file(source)
            item.destination, item.planned_directories = _resolve_destination(parsed, config)
            _inspect_target(item, config)
        except (FilesystemSafetyError, OSError) as exc:
            item.status = PlanStatus.FAILED
            item.reason = str(exc)
        result.items.append(item)

    competing: dict[str, list[PlanItem]] = defaultdict(list)
    for item in result.items:
        if item.status == PlanStatus.READY and item.destination is not None:
            competing[str(item.destination).casefold()].append(item)
    for items in competing.values():
        if len(items) > 1:
            for item in items:
                item.status = PlanStatus.CONFLICT
                item.reason = "multiple source files resolve to the same target"
    return result


def _record_item(database: Database, run_id: int, sequence_index: int, item: PlanItem) -> None:
    operation_status = "success" if item.status == PlanStatus.READY else item.status.lower()
    database.record_operation(
        run_id=run_id,
        sequence_index=sequence_index,
        action="move",
        status=operation_status,
        source_path=item.source,
        destination_path=item.destination,
        file_size=item.source_size,
        source_mtime_ns=item.source_mtime_ns,
        sha256=item.source_sha256,
        reason=item.reason,
    )


def _ensure_destination_directories(
    parsed: ParsedNote, config: NotesConfig
) -> tuple[Path, list[Path]]:
    root = config.study_root
    created: list[Path] = []
    try:
        first_level, first_created = create_child_directory(root, parsed.first_level, root)
        if first_created:
            created.append(first_level)
        course, course_created = create_child_directory(first_level, parsed.course, root)
        if course_created:
            created.append(course)
    except FilesystemSafetyError as exc:
        if created:
            raise DirectoryCreationError(str(exc), created) from exc
        raise
    return course / parsed.target_name, created


def _target_is_absent(destination: Path) -> None:
    matches = direct_casefold_matches(destination.parent, destination.name)
    if matches:
        raise FilesystemSafetyError(f"destination already exists: {matches[0]}")


def _status_for_apply(moved: int, failed: int) -> str:
    if failed == 0:
        return "success"
    if moved > 0:
        return "partial"
    return "failed"


def _log_operation(
    logger: logging.Logger | None,
    *,
    level: int,
    run_id: int,
    action: str,
    status: str,
    source: Path | None,
    destination: Path | None,
    reason: str | None = None,
) -> None:
    if logger is not None:
        logger.log(
            level,
            "run_id=%s action=%s status=%s source=%s destination=%s reason=%s",
            run_id,
            action,
            status,
            source,
            destination,
            reason,
        )


def _operation_roots(operation: object, config: NotesConfig) -> tuple[Path, Path]:
    if operation["action"] == "move":
        return config.source_dir, config.study_root
    if operation["action"] == "undo_move":
        return config.study_root, config.source_dir
    raise FilesystemSafetyError(f"operation {operation['id']} cannot be reconciled as a file move")


def _file_state(path: Path, expected_sha256: str) -> str:
    if not path_exists_no_follow(path):
        return "absent"
    if not is_ordinary_file(path):
        return "not an ordinary file"
    try:
        return "matches" if sha256_file(path) == expected_sha256 else "hash differs"
    except OSError as exc:
        return f"cannot be read ({exc})"


def _classify_prepared_operation(operation: object, config: NotesConfig) -> tuple[str, str]:
    """Classify a durable move record without changing the filesystem."""
    try:
        source = Path(operation["source_path"])
        destination = Path(operation["destination_path"])
        expected_sha256 = operation["sha256"]
        if not expected_sha256:
            raise FilesystemSafetyError("record is missing its expected SHA-256")
        source_root, destination_root = _operation_roots(operation, config)
        assert_within(source, source_root)
        assert_within(destination, destination_root)
        source_state = _file_state(source, expected_sha256)
        destination_state = _file_state(destination, expected_sha256)
    except (FilesystemSafetyError, OSError, TypeError) as exc:
        return "recovery_required", f"paths cannot be inspected safely: {exc}"

    if source_state == "matches" and destination_state == "absent":
        return "failed", "recovery confirmed that the move did not complete"
    if source_state == "absent" and destination_state == "matches":
        return "success", "recovery confirmed that the move completed"
    return (
        "recovery_required",
        f"ambiguous filesystem state: source is {source_state}; destination is {destination_state}",
    )


def _finalize_recovered_runs(database: Database, recovered_run_ids: set[int]) -> None:
    runs_by_id = {int(run["id"]): run for run in database.running_runs()}
    for run_id in recovered_run_ids:
        run = database.get_run(run_id)
        if run is not None:
            runs_by_id[run_id] = run
    for run in runs_by_id.values():
        action = "move" if run["mode"] == "apply" else "undo_move"
        counts = database.operation_counts_for_run(int(run["id"]), action)
        recorded_count = sum(counts.values())
        expected_count = int(run["matched_count"])
        moved_count = counts.get("success", 0)
        failed_count = counts.get("failed", 0) + counts.get("recovery_required", 0)
        incomplete = recorded_count < expected_count or counts.get("prepared", 0) > 0
        if not incomplete and failed_count == 0:
            status = "success"
        elif moved_count > 0 or counts.get("recovery_required", 0) > 0 or incomplete:
            status = "partial"
        else:
            status = "failed"
        summary = (
            "Recovered after interruption: "
            f"recorded={recorded_count} of expected={expected_count}; "
            f"success={moved_count}; failed={counts.get('failed', 0)}; "
            f"recovery_required={counts.get('recovery_required', 0)}"
        )
        database.finish_run(
            int(run["id"]),
            status=status,
            summary=summary,
            matched_count=expected_count,
            moved_count=moved_count,
            duplicate_count=counts.get("duplicate", 0),
            conflict_count=counts.get("conflict", 0),
            invalid_count=counts.get("invalid", 0),
            failed_count=failed_count,
            created_dir_count=int(run["created_dir_count"]),
        )


def recover_interrupted_operations(
    config: NotesConfig, database: Database, logger: logging.Logger | None = None
) -> None:
    """Reconcile durable prepared records without moving or deleting any files."""
    database.initialize()
    recovered_run_ids: set[int] = set()
    for operation in database.prepared_or_recovery_operations():
        status, reason = _classify_prepared_operation(operation, config)
        if operation["action"] == "undo_move" and status == "success":
            database.finalize_undo_operation(
                int(operation["id"]), int(operation["related_operation_id"]), int(operation["run_id"])
            )
        else:
            database.update_operation_status(int(operation["id"]), status, reason)
        recovered_run_ids.add(int(operation["run_id"]))
        _log_operation(
            logger,
            level=logging.INFO if status == "success" else logging.ERROR,
            run_id=int(operation["run_id"]),
            action=str(operation["action"]),
            status=status,
            source=Path(operation["source_path"]) if operation["source_path"] else None,
            destination=Path(operation["destination_path"]) if operation["destination_path"] else None,
            reason=f"recovered: {reason}",
        )
    _finalize_recovered_runs(database, recovered_run_ids)
    blocked = database.recovery_required_operations()
    if blocked:
        _log_operation(
            logger,
            level=logging.ERROR,
            run_id=int(blocked[0]["run_id"]),
            action="recovery",
            status="blocked",
            source=None,
            destination=None,
            reason="manual recovery is required before a new mutation",
        )
        raise RecoveryRequiredError(blocked)


def apply_notes(
    config: NotesConfig,
    database: Database,
    logger: logging.Logger | None = None,
    *,
    lock_held: bool = False,
) -> RunResult:
    """Rescan and safely apply every ready item with durable prepared records."""
    if not lock_held:
        with database.mutation_lock():
            return apply_notes(config, database, logger, lock_held=True)

    recover_interrupted_operations(config, database, logger)
    plan = plan_notes(config)
    run_id = database.create_run("notes", "apply", matched_count=plan.matched_count)
    created_directories: list[Path] = []

    for sequence_index, item in enumerate(plan.items, start=1):
        if item.status != PlanStatus.READY:
            _record_item(database, run_id, sequence_index, item)
            _log_operation(
                logger, level=logging.INFO, run_id=run_id, action="move",
                status=item.status.lower(), source=item.source, destination=item.destination, reason=item.reason,
            )
            continue

        prepared_operation_id: int | None = None
        try:
            if (
                item.parsed is None or item.source_size is None or item.source_mtime_ns is None
                or item.source_sha256 is None
            ):
                raise FilesystemSafetyError("planned item is missing source verification data")
            snapshot_matches(item.source, item.source_size, item.source_mtime_ns, item.source_sha256)
            destination, just_created = _ensure_destination_directories(item.parsed, config)
            created_directories.extend(just_created)
            for directory in just_created:
                database.record_operation(
                    run_id=run_id, sequence_index=sequence_index, action="mkdir", status="success",
                    destination_path=directory,
                )
                _log_operation(
                    logger, level=logging.INFO, run_id=run_id, action="mkdir", status="success",
                    source=None, destination=directory,
                )
            assert_within(destination, config.study_root)
            _target_is_absent(destination)
            snapshot_matches(item.source, item.source_size, item.source_mtime_ns, item.source_sha256)
            prepared_operation_id = database.prepare_operation(
                run_id=run_id,
                sequence_index=sequence_index,
                action="move",
                source_path=item.source,
                destination_path=destination,
                file_size=item.source_size,
                source_mtime_ns=item.source_mtime_ns,
                sha256=item.source_sha256,
            )
            _log_operation(
                logger, level=logging.INFO, run_id=run_id, action="move", status="prepared",
                source=item.source, destination=destination,
            )
            move_file_without_overwrite(
                item.source,
                destination,
                item.source_sha256,
                expected_size=item.source_size,
                expected_mtime_ns=item.source_mtime_ns,
            )
            assert_within(destination, config.study_root)
            database.update_operation_status(prepared_operation_id, "success")
            item.destination = destination
            item.status = PlanStatus.READY
            item.reason = None
        except sqlite3.Error:
            # A prepared row is deliberately left durable for the next mutating startup.
            raise
        except (FilesystemSafetyError, OSError) as exc:
            if isinstance(exc, DirectoryCreationError):
                for directory in exc.created_directories:
                    created_directories.append(directory)
                    database.record_operation(
                        run_id=run_id, sequence_index=sequence_index, action="mkdir", status="success",
                        destination_path=directory,
                    )
            if prepared_operation_id is not None:
                prepared = next(
                    operation for operation in database.operations_for_run(run_id)
                    if int(operation["id"]) == prepared_operation_id
                )
                recovered_status, recovered_reason = _classify_prepared_operation(prepared, config)
                database.update_operation_status(prepared_operation_id, recovered_status, recovered_reason)
                item.destination = Path(prepared["destination_path"])
                item.status = PlanStatus.READY if recovered_status == "success" else PlanStatus.FAILED
                item.reason = None if recovered_status == "success" else recovered_reason
            else:
                item.status = PlanStatus.FAILED
                item.reason = str(exc)
                _record_item(database, run_id, sequence_index, item)
        _log_operation(
            logger,
            level=logging.INFO if item.status == PlanStatus.READY else logging.ERROR,
            run_id=run_id,
            action="move",
            status="success" if item.status == PlanStatus.READY else "failed",
            source=item.source,
            destination=item.destination,
            reason=item.reason,
        )

    for directory in sorted(set(created_directories), key=lambda path: len(path.parts), reverse=True):
        try:
            remove_empty_directory(directory, config.study_root)
        except FilesystemSafetyError:
            pass

    counts = Counter(item.status for item in plan.items)
    moved_count = counts[PlanStatus.READY]
    duplicate_count = counts[PlanStatus.DUPLICATE]
    conflict_count = counts[PlanStatus.CONFLICT]
    invalid_count = counts[PlanStatus.INVALID]
    failed_count = counts[PlanStatus.FAILED]
    summary = (
        f"matched={plan.matched_count}; moved={moved_count}; duplicates={duplicate_count}; "
        f"conflicts={conflict_count}; invalid={invalid_count}; failed={failed_count}; "
        f"created_directories={len(created_directories)}"
    )
    database.finish_run(
        run_id, status=_status_for_apply(moved_count, failed_count), summary=summary,
        matched_count=plan.matched_count, moved_count=moved_count, duplicate_count=duplicate_count,
        conflict_count=conflict_count, invalid_count=invalid_count, failed_count=failed_count,
        created_dir_count=len(created_directories),
    )
    return RunResult(
        run_id=run_id, items=plan.items, matched_count=plan.matched_count, moved_count=moved_count,
        duplicate_count=duplicate_count, conflict_count=conflict_count, invalid_count=invalid_count,
        failed_count=failed_count, created_dir_count=len(created_directories), ignored_count=plan.ignored_count,
    )


def _validate_undo_paths(source: Path, destination: Path, config: NotesConfig, expected_sha256: str) -> None:
    assert_within(source, config.source_dir)
    assert_within(destination, config.study_root)
    if path_exists_no_follow(source):
        raise FilesystemSafetyError(f"original source path is occupied: {source}")
    if not is_ordinary_file(destination):
        raise FilesystemSafetyError(f"current destination is not an ordinary file: {destination}")
    if sha256_file(destination) != expected_sha256:
        raise FilesystemSafetyError(f"destination content changed since apply: {destination}")
    assert_ordinary_directory(source.parent, f"original source directory {source.parent}")


def undo_latest(
    config: NotesConfig,
    database: Database | None,
    logger: logging.Logger | None = None,
    *,
    lock_held: bool = False,
) -> UndoResult:
    """Undo the latest eligible apply run with durable prepared undo records."""
    if database is None or not database.path.exists():
        return UndoResult(None, None, [], 0, 0, 0, "No eligible apply run to undo.")
    if not lock_held:
        with database.mutation_lock():
            return undo_latest(config, database, logger, lock_held=True)

    recover_interrupted_operations(config, database, logger)
    apply_run = database.latest_eligible_apply_run()
    if apply_run is None:
        return UndoResult(None, None, [], 0, 0, 0, "No eligible apply run to undo.")

    _validate_roots(config)
    apply_run_id = int(apply_run["id"])
    successful_moves = database.successful_moves_for_run(apply_run_id)
    run_id = database.create_run("undo", "undo", matched_count=len(successful_moves))
    results: list[UndoItem] = []
    for sequence_index, operation in enumerate(successful_moves, start=1):
        source = Path(operation["source_path"])
        destination = Path(operation["destination_path"])
        expected_sha256 = operation["sha256"]
        prepared_operation_id: int | None = None
        try:
            if not expected_sha256:
                raise FilesystemSafetyError("recorded move is missing its SHA-256")
            _validate_undo_paths(source, destination, config, expected_sha256)
            prepared_operation_id = database.prepare_operation(
                run_id=run_id,
                sequence_index=sequence_index,
                action="undo_move",
                source_path=destination,
                destination_path=source,
                sha256=expected_sha256,
                related_operation_id=int(operation["id"]),
            )
            _log_operation(
                logger, level=logging.INFO, run_id=run_id, action="undo_move", status="prepared",
                source=destination, destination=source,
            )
            move_file_without_overwrite(destination, source, expected_sha256)
            assert_within(source, config.source_dir)
            database.finalize_undo_operation(prepared_operation_id, int(operation["id"]), run_id)
            results.append(UndoItem(destination, source, "success"))
            _log_operation(
                logger, level=logging.INFO, run_id=run_id, action="undo_move", status="success",
                source=destination, destination=source,
            )
        except sqlite3.Error:
            raise
        except (FilesystemSafetyError, OSError) as exc:
            reason = str(exc)
            if prepared_operation_id is not None:
                prepared = next(
                    operation_row for operation_row in database.operations_for_run(run_id)
                    if int(operation_row["id"]) == prepared_operation_id
                )
                recovered_status, recovered_reason = _classify_prepared_operation(prepared, config)
                if recovered_status == "success":
                    database.finalize_undo_operation(prepared_operation_id, int(operation["id"]), run_id)
                    results.append(UndoItem(destination, source, "success"))
                    continue
                database.update_operation_status(prepared_operation_id, recovered_status, recovered_reason)
                results.append(UndoItem(destination, source, "failed", recovered_reason))
            else:
                database.record_operation(
                    run_id=run_id,
                    sequence_index=sequence_index,
                    action="undo_move",
                    status="failed",
                    source_path=destination,
                    destination_path=source,
                    sha256=expected_sha256,
                    reason=reason,
                    related_operation_id=int(operation["id"]),
                )
                results.append(UndoItem(destination, source, "failed", reason))
            _log_operation(
                logger, level=logging.ERROR, run_id=run_id, action="undo_move", status="failed",
                source=destination, destination=source, reason=results[-1].reason,
            )

    removed_dir_count = 0
    for sequence_index, operation in enumerate(database.created_directories_for_run(apply_run_id), start=1):
        directory = Path(operation["destination_path"])
        try:
            removed = remove_empty_directory(directory, config.study_root)
            status = "success" if removed else "skipped"
            reason = None if removed else "directory is absent or not empty"
            removed_dir_count += int(removed)
        except FilesystemSafetyError as exc:
            status = "failed"
            reason = str(exc)
        database.record_operation(
            run_id=run_id,
            sequence_index=sequence_index,
            action="undo_rmdir",
            status=status,
            destination_path=directory,
            reason=reason,
            related_operation_id=int(operation["id"]),
        )
        _log_operation(
            logger,
            level=logging.INFO if status != "failed" else logging.ERROR,
            run_id=run_id,
            action="undo_rmdir",
            status=status,
            source=None,
            destination=directory,
            reason=reason,
        )

    moved_count = sum(item.status == "success" for item in results)
    failed_count = sum(item.status == "failed" for item in results)
    status = _status_for_apply(moved_count, failed_count)
    summary = (
        f"apply_run={apply_run_id}; restored={moved_count}; failed={failed_count}; "
        f"removed_directories={removed_dir_count}"
    )
    database.finish_run(
        run_id,
        status=status,
        summary=summary,
        matched_count=len(results),
        moved_count=moved_count,
        failed_count=failed_count,
        created_dir_count=removed_dir_count,
    )
    return UndoResult(run_id, apply_run_id, results, moved_count, failed_count, removed_dir_count)


def history_rows(database: Database | None, run_id: int | None = None) -> Iterable[object]:
    if database is None or not database.path.exists():
        return []
    if run_id is None:
        return database.list_runs()
    run = database.get_run(run_id)
    if run is None:
        return []
    return [run, *database.operations_for_run(run_id)]
