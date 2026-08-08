from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from pathlib import Path

from .database import Database, DatabaseSchemaError, MutationBlockedError, MutationLockError
from .filesystem import FilesystemSafetyError
from .models import PlanResult, PlanStatus, RunResult, UndoResult
from .notes import RecoveryRequiredError, apply_notes, plan_notes, undo_latest
from .rename import RenameError, RenameRecoveryRequired, RenameResult, undo_latest_rename
from .sendto import SendToError, install_sendto, uninstall_sendto
from .settings import SettingsError, application_data_dir, load_notes_config


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mykr-ops", description="MyKr-ops local automation toolkit")
    commands = parser.add_subparsers(dest="command", required=True)
    notes = commands.add_parser("notes", help="preview or organize study notes")
    notes.add_argument("--apply", action="store_true", help="perform the planned file moves")
    commands.add_parser("undo", help="undo the latest eligible notes apply run")
    history = commands.add_parser("history", help="show recent apply and undo runs")
    history.add_argument("--run", type=int, help="show operation details for one run")
    rename = commands.add_parser("rename", help="safely rename selected files and folders")
    rename_commands = rename.add_subparsers(dest="rename_command", required=True)
    gui = rename_commands.add_parser("gui", help="open the Rename window for explicit paths")
    gui.add_argument("paths", nargs="+", type=Path, metavar="PATH", help="selected same-parent files or folders")
    rename_commands.add_parser("undo", help="undo the latest eligible rename batch")
    rename_commands.add_parser("install-sendto", help="install the MyKr-ops Rename Send To shortcut")
    rename_commands.add_parser("uninstall-sendto", help="remove the owned MyKr-ops Rename Send To shortcut")
    return parser


def _configure_logger(state_dir: Path) -> logging.Logger:
    state_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("mykr_ops")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    handler = logging.FileHandler(state_dir / "mykr-ops.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    return logger


def _print_plan(plan: PlanResult) -> None:
    planned_directories = sorted(
        {directory for item in plan.items for directory in item.planned_directories},
        key=lambda directory: (len(directory.parts), str(directory).casefold()),
    )
    for directory in planned_directories:
        print(f"CREATE DIRECTORY: {directory}")
    for item in plan.items:
        if item.status == PlanStatus.READY:
            print(f"READY: {item.source} -> {item.destination}")
        else:
            print(f"{item.status}: {item.source}: {item.reason}")
    print(
        "Preview summary: "
        f"matched={plan.matched_count}, ready={plan.count(PlanStatus.READY)}, "
        f"duplicates={plan.count(PlanStatus.DUPLICATE)}, "
        f"conflicts={plan.count(PlanStatus.CONFLICT)}, "
        f"invalid={plan.count(PlanStatus.INVALID)}, failed={plan.count(PlanStatus.FAILED)}, "
        f"ignored={plan.ignored_count}"
    )


def _print_apply(result: RunResult) -> None:
    for item in result.items:
        if item.status == PlanStatus.READY:
            print(f"MOVED: {item.source} -> {item.destination}")
        else:
            print(f"{item.status}: {item.source}: {item.reason}")
    print(
        "Apply summary: "
        f"matched={result.matched_count}, moved={result.moved_count}, "
        f"duplicates={result.duplicate_count}, conflicts={result.conflict_count}, "
        f"invalid={result.invalid_count}, failed={result.failed_count}, "
        f"created-directories={result.created_dir_count}, run ID={result.run_id}"
    )
    if result.recovery_operation_id is not None:
        print(
            "Manual recovery required: "
            f"run ID={result.run_id}, operation ID={result.recovery_operation_id}. "
            f"Inspect `mykr-ops history --run {result.run_id}` and resolve the filesystem state "
            "before another apply or undo."
        )


def _print_undo(result: UndoResult) -> None:
    if result.message:
        print(result.message)
        return
    for item in result.items:
        if item.status == "success":
            print(f"RESTORED: {item.source} -> {item.destination}")
        else:
            print(f"FAILED: {item.source}: {item.reason}")
    print(
        f"Undo summary: restored={result.moved_count}, failed={result.failed_count}, "
        f"removed-directories={result.removed_dir_count}, run ID={result.run_id}"
    )
    if result.recovery_operation_id is not None:
        print(
            "Manual recovery required: "
            f"run ID={result.run_id}, operation ID={result.recovery_operation_id}. "
            f"Inspect `mykr-ops history --run {result.run_id}` and resolve the filesystem state "
            "before another apply or undo."
        )


def _print_rename_result(result: RenameResult) -> None:
    print(result.message)
    if result.run_id is not None:
        print(
            f"Rename summary: renamed={result.renamed_count}, unchanged={result.unchanged_count}, "
            f"run ID={result.run_id}"
        )


def _print_history(database: Database | None, run_id: int | None) -> None:
    if database is None:
        print("No recorded runs.")
        return
    if run_id is None:
        rows = database.list_runs()
        if not rows:
            print("No recorded runs.")
            return
        for row in rows:
            print(
                f"Run {row['id']}: {row['command']} {row['mode']} {row['status']} "
                f"moved={row['moved_count']} failed={row['failed_count']} "
                f"started={row['started_at']}"
            )
        return
    run = database.get_run(run_id)
    if run is None:
        print("No recorded runs.")
        return
    print(f"Run {run['id']}: {run['command']} {run['mode']} {run['status']} - {run['summary']}")
    if run["command"] == "rename":
        for item in database.rename_items_for_run(run_id):
            print(
                f"  RENAME {item['state']}: {item['original_path']} -> {item['final_path']}"
                + (f" [error_type={item['error_type']}]" if item["error_type"] else "")
                + (f" ({item['reason']})" if item["reason"] else "")
            )
        return
    for operation in database.operations_for_run(run_id):
        print(
            f"  {operation['action']} {operation['status']}: "
            f"{operation['source_path']} -> {operation['destination_path']}"
            + (f" [error_type={operation['error_type']}]" if operation["error_type"] else "")
            + (f" ({operation['reason']})" if operation["reason"] else "")
        )


def _run_rename_command(args: argparse.Namespace) -> int:
    if args.rename_command == "install-sendto":
        print(f"Installed Send To entry: {install_sendto()}")
        return 0
    if args.rename_command == "uninstall-sendto":
        removed = uninstall_sendto()
        print("Removed MyKr-ops Rename Send To entry." if removed else "No MyKr-ops Rename Send To entry was installed.")
        return 0

    state_dir = application_data_dir(create=True)
    database = Database(state_dir / "mykr-ops.db")
    logger = _configure_logger(state_dir)
    if args.rename_command == "undo":
        with database.mutation_lock():
            result = undo_latest_rename(database, logger, lock_held=True)
        _print_rename_result(result)
        return 1 if result.failed else 0

    from .rename_gui import launch_rename_gui

    try:
        launch_rename_gui(args.paths, database, logger)
    except Exception:
        logger.exception("rename gui failed")
        raise
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    logger: logging.Logger | None = None
    try:
        if args.command == "history":
            state_dir = application_data_dir(create=False)
            database_path = state_dir / "mykr-ops.db"
            if not database_path.exists():
                _print_history(None, args.run)
                return 0
            database = Database(database_path)
            with database.mutation_lock():
                database.initialize()
                _print_history(database, args.run)
            return 0
        if args.command == "rename":
            return _run_rename_command(args)

        config = load_notes_config()
        if args.command == "notes" and not args.apply:
            _print_plan(plan_notes(config))
            return 0
        state_dir = application_data_dir(create=False)
        database_path = state_dir / "mykr-ops.db"
        if args.command == "undo" and not database_path.exists():
            _print_undo(undo_latest(config, None))
            return 0

        state_dir = application_data_dir(create=True)
        database = Database(state_dir / "mykr-ops.db")
        with database.mutation_lock():
            logger = _configure_logger(state_dir)
            if args.command == "notes":
                result = apply_notes(config, database, logger, lock_held=True)
                logger.info("run_id=%s command=notes status failed=%s", result.run_id, result.failed_count)
                _print_apply(result)
                return 1 if result.failed_count else 0
            result = undo_latest(config, database, logger, lock_held=True)
            logger.info("run_id=%s command=undo status failed=%s", result.run_id, result.failed_count)
            _print_undo(result)
            return 1 if result.failed_count else 0
    except (
        DatabaseSchemaError,
        FilesystemSafetyError,
        MutationBlockedError,
        MutationLockError,
        RecoveryRequiredError,
        RenameRecoveryRequired,
        RenameError,
        SendToError,
        SettingsError,
        sqlite3.Error,
        OSError,
    ) as exc:
        if logger:
            error_type = (
                "database_error" if isinstance(exc, sqlite3.Error)
                else getattr(exc, "error_type", None) or "unknown_filesystem_error"
            )
            logger.exception("command failed: error_type=%s error=%s", error_type, exc)
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
