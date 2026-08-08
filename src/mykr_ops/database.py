from __future__ import annotations

import msvcrt
import sqlite3
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from pathlib import Path


SCHEMA_VERSION = 4
OPERATION_STATUSES = (
    "prepared",
    "success",
    "duplicate",
    "conflict",
    "invalid",
    "failed",
    "skipped",
    "recovery_required",
)
RENAME_ITEM_STATES = (
    "prepared",
    "stage_prepared",
    "staged",
    "finalize_prepared",
    "success",
    "rollback_stage_prepared",
    "rollback_staged",
    "rollback_finalize_prepared",
    "rolled_back",
    "failed",
    "recovery_required",
)


class DatabaseSchemaError(RuntimeError):
    """Raised when an existing database cannot be migrated safely."""


class MutationLockError(RuntimeError):
    """Raised when another MyKr-ops process holds the mutation lock."""


class MutationBlockedError(RuntimeError):
    """Raised when another module has durable filesystem work requiring recovery."""


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class MutationLock(AbstractContextManager["MutationLock"]):
    """A one-byte Windows advisory lock released automatically on process exit."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._handle = None

    def __enter__(self) -> "MutationLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        try:
            handle.seek(0, 2)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            handle.close()
            raise MutationLockError(
                "another MyKr-ops apply or undo command is already running"
            ) from exc
        self._handle = handle
        return self

    def __exit__(self, *_: object) -> None:
        if self._handle is None:
            return
        try:
            self._handle.seek(0)
            msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            self._handle.close()
            self._handle = None


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path

    def mutation_lock(self) -> MutationLock:
        return MutationLock(self.path.parent / "mykr-ops.lock")

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            self._initialize_schema(connection)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize_schema(self, connection: sqlite3.Connection) -> None:
        tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name IN ('runs', 'operations', 'rename_items')"
            )
        }
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if not tables:
            if version not in (0, 3, SCHEMA_VERSION):
                raise DatabaseSchemaError(f"database schema version {version} is not recognized")
            self._create_latest_schema(connection)
            return
        if tables == {"runs", "operations"}:
            if version == 0:
                self._migrate_phase1_schema(connection)
                return
            if version == 1:
                self._migrate_phase2a_schema(connection)
                return
            if version == 2:
                self._migrate_phase2e_schema(connection)
                return
            if version == 3:
                self._migrate_phase2g_schema(connection)
                return
        elif tables != {"runs", "operations", "rename_items"}:
            raise DatabaseSchemaError("database has an incomplete or unrecognized MyKr-ops schema")
        if version != SCHEMA_VERSION:
            raise DatabaseSchemaError(f"database schema version {version} is not recognized")
        self._validate_latest_schema(connection)

    def _create_latest_schema(self, connection: sqlite3.Connection) -> None:
        with connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    id INTEGER PRIMARY KEY,
                    command TEXT NOT NULL,
                    mode TEXT NOT NULL CHECK (mode IN ('apply', 'undo')),
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    status TEXT NOT NULL CHECK (status IN ('running', 'success', 'partial', 'failed')),
                    matched_count INTEGER NOT NULL DEFAULT 0,
                    moved_count INTEGER NOT NULL DEFAULT 0,
                    duplicate_count INTEGER NOT NULL DEFAULT 0,
                    conflict_count INTEGER NOT NULL DEFAULT 0,
                    invalid_count INTEGER NOT NULL DEFAULT 0,
                    failed_count INTEGER NOT NULL DEFAULT 0,
                    created_dir_count INTEGER NOT NULL DEFAULT 0,
                    summary TEXT
                );
                """
            )
            self._create_operations_table(connection)
            self._create_rename_items_table(connection)
            self._create_indexes(connection)
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def _create_operations_table(self, connection: sqlite3.Connection) -> None:
        status_values = ", ".join(repr(status) for status in OPERATION_STATUSES)
        connection.execute(
            f"""
            CREATE TABLE operations (
                id INTEGER PRIMARY KEY,
                run_id INTEGER NOT NULL,
                sequence_index INTEGER NOT NULL,
                action TEXT NOT NULL CHECK (action IN ('move', 'mkdir', 'undo_move', 'undo_rmdir')),
                source_path TEXT,
                destination_path TEXT,
                file_size INTEGER,
                source_mtime_ns INTEGER,
                sha256 TEXT,
                directory_name TEXT,
                status TEXT NOT NULL CHECK (status IN ({status_values})),
                reason TEXT,
                error_type TEXT,
                related_operation_id INTEGER,
                undone_at TEXT,
                undo_run_id INTEGER,
                created_at TEXT NOT NULL,
                FOREIGN KEY(run_id) REFERENCES runs(id)
            )
            """
        )

    def _create_rename_items_table(self, connection: sqlite3.Connection) -> None:
        state_values = ", ".join(repr(state) for state in RENAME_ITEM_STATES)
        connection.execute(
            f"""
            CREATE TABLE IF NOT EXISTS rename_items (
                id INTEGER PRIMARY KEY,
                run_id INTEGER NOT NULL,
                sequence_index INTEGER NOT NULL,
                object_kind TEXT NOT NULL CHECK (object_kind IN ('file', 'directory')),
                original_path TEXT NOT NULL,
                temporary_path TEXT NOT NULL,
                rollback_path TEXT,
                final_path TEXT NOT NULL,
                volume_serial INTEGER NOT NULL,
                file_index INTEGER NOT NULL,
                parent_volume_serial INTEGER NOT NULL,
                parent_file_index INTEGER NOT NULL,
                state TEXT NOT NULL CHECK (state IN ({state_values})),
                reason TEXT,
                error_type TEXT,
                related_item_id INTEGER,
                undone_at TEXT,
                undo_run_id INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(run_id) REFERENCES runs(id),
                FOREIGN KEY(related_item_id) REFERENCES rename_items(id)
            )
            """
        )

    def _create_indexes(self, connection: sqlite3.Connection) -> None:
        connection.execute("CREATE INDEX IF NOT EXISTS operations_run_id_idx ON operations(run_id)")
        connection.execute("CREATE INDEX IF NOT EXISTS operations_undo_run_id_idx ON operations(undo_run_id)")
        connection.execute(
            "CREATE INDEX IF NOT EXISTS operations_related_operation_id_idx ON operations(related_operation_id)"
        )
        connection.execute("CREATE INDEX IF NOT EXISTS operations_status_idx ON operations(status)")
        connection.execute("CREATE INDEX IF NOT EXISTS rename_items_run_id_idx ON rename_items(run_id)")
        connection.execute("CREATE INDEX IF NOT EXISTS rename_items_state_idx ON rename_items(state)")
        connection.execute("CREATE INDEX IF NOT EXISTS rename_items_undo_run_id_idx ON rename_items(undo_run_id)")
        connection.execute("CREATE INDEX IF NOT EXISTS rename_items_related_item_id_idx ON rename_items(related_item_id)")

    @staticmethod
    def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
        return {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}

    def _migrate_phase1_schema(self, connection: sqlite3.Connection) -> None:
        required_runs = {
            "id", "command", "mode", "started_at", "finished_at", "status", "matched_count",
            "moved_count", "duplicate_count", "conflict_count", "invalid_count", "failed_count",
            "created_dir_count", "summary",
        }
        required_operations = {
            "id", "run_id", "sequence_index", "action", "source_path", "destination_path",
            "file_size", "source_mtime_ns", "sha256", "status", "reason", "related_operation_id",
            "undone_at", "undo_run_id", "created_at",
        }
        if not required_runs.issubset(self._columns(connection, "runs")) or not required_operations.issubset(
            self._columns(connection, "operations")
        ):
            raise DatabaseSchemaError("existing version-0 database is not the expected Phase 1 schema")
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DROP INDEX IF EXISTS operations_run_id_idx")
            connection.execute("DROP INDEX IF EXISTS operations_undo_run_id_idx")
            connection.execute("DROP INDEX IF EXISTS operations_related_operation_id_idx")
            connection.execute("DROP INDEX IF EXISTS operations_status_idx")
            connection.execute("ALTER TABLE operations RENAME TO operations_phase1")
            self._create_operations_table(connection)
            connection.execute(
                """
                INSERT INTO operations (
                    id, run_id, sequence_index, action, source_path, destination_path, file_size,
                    source_mtime_ns, sha256, status, reason, related_operation_id, undone_at,
                    undo_run_id, created_at
                )
                SELECT
                    id, run_id, sequence_index, action, source_path, destination_path, file_size,
                    source_mtime_ns, sha256, status, reason, related_operation_id, undone_at,
                    undo_run_id, created_at
                FROM operations_phase1
                """
            )
            connection.execute("DROP TABLE operations_phase1")
            self._create_rename_items_table(connection)
            self._create_indexes(connection)
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            connection.commit()
        except sqlite3.Error as exc:
            connection.rollback()
            raise DatabaseSchemaError(f"could not migrate the Phase 1 database safely: {exc}") from exc

    def _migrate_phase2a_schema(self, connection: sqlite3.Connection) -> None:
        required_operations = {
            "id", "run_id", "sequence_index", "action", "source_path", "destination_path",
            "file_size", "source_mtime_ns", "sha256", "status", "reason", "related_operation_id",
            "undone_at", "undo_run_id", "created_at",
        }
        columns = self._columns(connection, "operations")
        if not required_operations.issubset(columns) or "error_type" in columns:
            raise DatabaseSchemaError("existing version-1 database is not the expected Phase 2A schema")
        definition = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'operations'"
        ).fetchone()["sql"].casefold()
        if "prepared" not in definition or "recovery_required" not in definition:
            raise DatabaseSchemaError("existing version-1 operation status schema is not recognized")
        try:
            with connection:
                connection.execute("ALTER TABLE operations ADD COLUMN error_type TEXT")
                connection.execute("ALTER TABLE operations ADD COLUMN directory_name TEXT")
                self._create_rename_items_table(connection)
                self._create_indexes(connection)
                connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        except sqlite3.Error as exc:
            raise DatabaseSchemaError(f"could not migrate the Phase 2A database safely: {exc}") from exc

    def _migrate_phase2e_schema(self, connection: sqlite3.Connection) -> None:
        required_operations = {
            "id", "run_id", "sequence_index", "action", "source_path", "destination_path",
            "file_size", "source_mtime_ns", "sha256", "status", "reason", "error_type",
            "related_operation_id", "undone_at", "undo_run_id", "created_at",
        }
        columns = self._columns(connection, "operations")
        if not required_operations.issubset(columns) or "directory_name" in columns:
            raise DatabaseSchemaError("existing version-2 database is not the expected Phase 2E schema")
        definition = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'operations'"
        ).fetchone()["sql"].casefold()
        if "prepared" not in definition or "recovery_required" not in definition:
            raise DatabaseSchemaError("existing version-2 operation status schema is not recognized")
        try:
            with connection:
                connection.execute("ALTER TABLE operations ADD COLUMN directory_name TEXT")
                self._create_rename_items_table(connection)
                self._create_indexes(connection)
                connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        except sqlite3.Error as exc:
            raise DatabaseSchemaError(f"could not migrate the Phase 2E database safely: {exc}") from exc

    def _migrate_phase2g_schema(self, connection: sqlite3.Connection) -> None:
        required_operations = {
            "id", "run_id", "sequence_index", "action", "source_path", "destination_path",
            "file_size", "source_mtime_ns", "sha256", "directory_name", "status", "reason", "error_type",
            "related_operation_id", "undone_at", "undo_run_id", "created_at",
        }
        if not required_operations.issubset(self._columns(connection, "operations")):
            raise DatabaseSchemaError("existing version-3 database is not the expected Phase 2G schema")
        definition = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'operations'"
        ).fetchone()["sql"].casefold()
        if "prepared" not in definition or "recovery_required" not in definition:
            raise DatabaseSchemaError("existing version-3 operation status schema is not recognized")
        try:
            with connection:
                self._create_rename_items_table(connection)
                self._create_indexes(connection)
                connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        except sqlite3.Error as exc:
            raise DatabaseSchemaError(f"could not migrate the Phase 2G database safely: {exc}") from exc

    def _validate_latest_schema(self, connection: sqlite3.Connection) -> None:
        required_operations = {
            "id", "run_id", "sequence_index", "action", "source_path", "destination_path",
            "file_size", "source_mtime_ns", "sha256", "directory_name", "status", "reason", "error_type", "related_operation_id",
            "undone_at", "undo_run_id", "created_at",
        }
        if not required_operations.issubset(self._columns(connection, "operations")):
            raise DatabaseSchemaError("database version is current but required operation columns are missing")
        definition = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'operations'"
        ).fetchone()["sql"].casefold()
        if "prepared" not in definition or "recovery_required" not in definition:
            raise DatabaseSchemaError("database operation status schema is not recognized")
        required_rename_items = {
            "id", "run_id", "sequence_index", "object_kind", "original_path", "temporary_path",
            "rollback_path", "final_path", "volume_serial", "file_index", "parent_volume_serial",
            "parent_file_index", "state", "reason", "error_type", "related_item_id", "undone_at",
            "undo_run_id", "created_at", "updated_at",
        }
        if not required_rename_items.issubset(self._columns(connection, "rename_items")):
            raise DatabaseSchemaError("database version is current but required rename item columns are missing")
        self._create_indexes(connection)

    def create_run(self, command: str, mode: str, matched_count: int = 0) -> int:
        self.initialize()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO runs (command, mode, started_at, status, matched_count)
                VALUES (?, ?, ?, 'running', ?)
                """,
                (command, mode, utc_now(), matched_count),
            )
            return int(cursor.lastrowid)

    def finish_run(self, run_id: int, *, status: str, summary: str, matched_count: int = 0,
                   moved_count: int = 0, duplicate_count: int = 0, conflict_count: int = 0,
                   invalid_count: int = 0, failed_count: int = 0, created_dir_count: int = 0) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE runs SET finished_at = ?, status = ?, summary = ?, matched_count = ?, moved_count = ?,
                    duplicate_count = ?, conflict_count = ?, invalid_count = ?, failed_count = ?,
                    created_dir_count = ?
                WHERE id = ?
                """,
                (utc_now(), status, summary, matched_count, moved_count, duplicate_count,
                 conflict_count, invalid_count, failed_count, created_dir_count, run_id),
            )

    def record_operation(self, *, run_id: int, sequence_index: int, action: str, status: str,
                         source_path: Path | None = None, destination_path: Path | None = None,
                         file_size: int | None = None, source_mtime_ns: int | None = None,
                         sha256: str | None = None, directory_name: str | None = None, reason: str | None = None,
                         error_type: str | None = None,
                         related_operation_id: int | None = None) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO operations (
                    run_id, sequence_index, action, source_path, destination_path, file_size,
                    source_mtime_ns, sha256, directory_name, status, reason, error_type, related_operation_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (run_id, sequence_index, action,
                 str(source_path) if source_path is not None else None,
                 str(destination_path) if destination_path is not None else None,
                 file_size, source_mtime_ns, sha256, directory_name, status, reason, error_type,
                 related_operation_id, utc_now()),
            )
            return int(cursor.lastrowid)

    def prepare_operation(self, **kwargs: object) -> int:
        return self.record_operation(status="prepared", **kwargs)

    def update_operation_status(
        self, operation_id: int, status: str, reason: str | None = None, error_type: str | None = None
    ) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE operations SET status = ?, reason = ?, error_type = ? WHERE id = ?",
                (status, reason, error_type, operation_id),
            )
            if cursor.rowcount != 1:
                raise DatabaseSchemaError(f"operation {operation_id} no longer exists")

    def create_rename_run(self, mode: str, items: list[dict[str, object]]) -> int:
        """Commit a rename run and every durable item intent before mutation begins."""
        if mode not in {"apply", "undo"}:
            raise DatabaseSchemaError(f"rename mode is not recognized: {mode}")
        self.initialize()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                cursor = connection.execute(
                    """
                    INSERT INTO runs (command, mode, started_at, status, matched_count)
                    VALUES ('rename', ?, ?, 'running', ?)
                    """,
                    (mode, utc_now(), len(items)),
                )
                run_id = int(cursor.lastrowid)
                now = utc_now()
                for item in items:
                    connection.execute(
                        """
                        INSERT INTO rename_items (
                            run_id, sequence_index, object_kind, original_path, temporary_path, final_path,
                            volume_serial, file_index, parent_volume_serial, parent_file_index, state,
                            related_item_id, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'prepared', ?, ?, ?)
                        """,
                        (
                            run_id,
                            item["sequence_index"],
                            item["object_kind"],
                            str(item["original_path"]),
                            str(item["temporary_path"]),
                            str(item["final_path"]),
                            item["volume_serial"],
                            item["file_index"],
                            item["parent_volume_serial"],
                            item["parent_file_index"],
                            item.get("related_item_id"),
                            now,
                            now,
                        ),
                    )
                connection.commit()
                return run_id
            except Exception:
                connection.rollback()
                raise

    def update_rename_item_state(
        self,
        item_id: int,
        state: str,
        reason: str | None = None,
        error_type: str | None = None,
        *,
        rollback_path: Path | None = None,
    ) -> None:
        if state not in RENAME_ITEM_STATES:
            raise DatabaseSchemaError(f"rename item state is not recognized: {state}")
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE rename_items
                SET state = ?, reason = ?, error_type = ?, rollback_path = COALESCE(?, rollback_path), updated_at = ?
                WHERE id = ?
                """,
                (state, reason, error_type, str(rollback_path) if rollback_path else None, utc_now(), item_id),
            )
            if cursor.rowcount != 1:
                raise DatabaseSchemaError(f"rename item {item_id} no longer exists")

    def rename_items_for_run(self, run_id: int) -> list[sqlite3.Row]:
        with self._connect() as connection:
            return connection.execute(
                "SELECT * FROM rename_items WHERE run_id = ? ORDER BY sequence_index, id", (run_id,)
            ).fetchall()

    def rename_item_counts_for_run(self, run_id: int) -> dict[str, int]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT state, COUNT(*) AS count FROM rename_items WHERE run_id = ? GROUP BY state", (run_id,)
            ).fetchall()
        return {str(row["state"]): int(row["count"]) for row in rows}

    def latest_eligible_rename_apply_run(self) -> sqlite3.Row | None:
        with self._connect() as connection:
            return connection.execute(
                """
                SELECT r.* FROM runs AS r
                WHERE r.command = 'rename' AND r.mode = 'apply'
                  AND r.status = 'success'
                  AND EXISTS (
                      SELECT 1 FROM rename_items AS i
                      WHERE i.run_id = r.id AND i.state = 'success' AND i.undone_at IS NULL
                  )
                ORDER BY r.id DESC LIMIT 1
                """
            ).fetchone()

    def successful_rename_items_for_run(self, run_id: int) -> list[sqlite3.Row]:
        with self._connect() as connection:
            return connection.execute(
                """
                SELECT * FROM rename_items
                WHERE run_id = ? AND state = 'success' AND undone_at IS NULL
                ORDER BY sequence_index, id
                """,
                (run_id,),
            ).fetchall()

    def unresolved_rename_items(self) -> list[sqlite3.Row]:
        terminal = ("success", "rolled_back", "failed")
        with self._connect() as connection:
            return connection.execute(
                "SELECT * FROM rename_items WHERE state NOT IN (?, ?, ?) ORDER BY id", terminal
            ).fetchall()

    def incomplete_rename_runs(self) -> list[sqlite3.Row]:
        """Return rename runs whose item state or run finalization needs recovery."""
        with self._connect() as connection:
            return connection.execute(
                "SELECT * FROM runs WHERE command = 'rename' AND status = 'running' ORDER BY id"
            ).fetchall()

    def raise_if_unresolved_rename(self) -> None:
        unresolved = self.unresolved_rename_items()
        if unresolved:
            first = unresolved[0]
            raise MutationBlockedError(
                f"rename recovery is required for run {first['run_id']} item {first['id']}; "
                "resolve it with `mykr-ops rename undo` or inspect history before notes mutation"
            )

    def finalize_rename_undo(self, undo_run_id: int) -> None:
        """Mark linked apply items undone only after every undo rename is durable success."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                undo_items = connection.execute(
                    "SELECT related_item_id FROM rename_items WHERE run_id = ?", (undo_run_id,)
                ).fetchall()
                related_ids = [int(row["related_item_id"]) for row in undo_items if row["related_item_id"] is not None]
                if not related_ids or len(related_ids) != len(undo_items):
                    raise DatabaseSchemaError("rename undo items are missing their apply-item links")
                incomplete = connection.execute(
                    "SELECT COUNT(*) FROM rename_items WHERE run_id = ? AND state != 'success'", (undo_run_id,)
                ).fetchone()[0]
                if incomplete:
                    raise DatabaseSchemaError("cannot finalize an incomplete rename undo run")
                placeholders = ", ".join("?" for _ in related_ids)
                updated = connection.execute(
                    f"""
                    UPDATE rename_items SET undone_at = ?, undo_run_id = ?
                    WHERE id IN ({placeholders}) AND state = 'success' AND undone_at IS NULL
                    """,
                    (utc_now(), undo_run_id, *related_ids),
                ).rowcount
                if updated != len(related_ids):
                    raise DatabaseSchemaError("could not atomically finalize the rename undo operation")
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def finalize_undo_operation(self, undo_operation_id: int, apply_operation_id: int, undo_run_id: int) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                updated_undo = connection.execute(
                    "UPDATE operations SET status = 'success', reason = NULL, error_type = NULL WHERE id = ?",
                    (undo_operation_id,),
                ).rowcount
                updated_apply = connection.execute(
                    """
                    UPDATE operations SET undone_at = ?, undo_run_id = ?
                    WHERE id = ? AND action = 'move' AND status = 'success' AND undone_at IS NULL
                    """,
                    (utc_now(), undo_run_id, apply_operation_id),
                ).rowcount
                if updated_undo != 1 or updated_apply != 1:
                    raise DatabaseSchemaError("could not atomically finalize the recovered undo operation")
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def latest_eligible_apply_run(self) -> sqlite3.Row | None:
        with self._connect() as connection:
            return connection.execute(
                """
                SELECT r.*
                FROM runs AS r
                WHERE r.command = 'notes' AND r.mode = 'apply'
                  AND EXISTS (
                      SELECT 1 FROM operations AS o
                      WHERE o.run_id = r.id AND o.action = 'move'
                        AND o.status = 'success' AND o.undone_at IS NULL
                  )
                ORDER BY r.id DESC
                LIMIT 1
                """
            ).fetchone()

    def successful_moves_for_run(self, run_id: int) -> list[sqlite3.Row]:
        with self._connect() as connection:
            return connection.execute(
                """
                SELECT * FROM operations
                WHERE run_id = ? AND action = 'move' AND status = 'success' AND undone_at IS NULL
                ORDER BY sequence_index DESC, id DESC
                """,
                (run_id,),
            ).fetchall()

    def created_directories_for_run(self, run_id: int) -> list[sqlite3.Row]:
        with self._connect() as connection:
            return connection.execute(
                """
                SELECT * FROM operations
                WHERE run_id = ? AND action = 'mkdir' AND status = 'success'
                  AND source_path IS NOT NULL AND directory_name IS NOT NULL
                ORDER BY LENGTH(destination_path) DESC, id DESC
                """,
                (run_id,),
            ).fetchall()

    def prepared_or_recovery_operations(self) -> list[sqlite3.Row]:
        with self._connect() as connection:
            return connection.execute(
                """
                SELECT o.*, r.mode AS run_mode
                FROM operations AS o JOIN runs AS r ON r.id = o.run_id
                WHERE o.status IN ('prepared', 'recovery_required')
                ORDER BY o.id
                """
            ).fetchall()

    def recovery_required_operations(self) -> list[sqlite3.Row]:
        with self._connect() as connection:
            return connection.execute(
                "SELECT * FROM operations WHERE status = 'recovery_required' ORDER BY id"
            ).fetchall()

    def running_runs(self) -> list[sqlite3.Row]:
        with self._connect() as connection:
            return connection.execute("SELECT * FROM runs WHERE status = 'running' ORDER BY id").fetchall()

    def operation_counts_for_run(self, run_id: int, action: str) -> dict[str, int]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT status, COUNT(*) AS count FROM operations WHERE run_id = ? AND action = ? GROUP BY status",
                (run_id, action),
            ).fetchall()
        return {str(row["status"]): int(row["count"]) for row in rows}

    def mark_move_undone(self, operation_id: int, undo_run_id: int) -> None:
        # Retained for direct compatibility with existing callers; new undo paths use
        # finalize_undo_operation so both rows are committed atomically.
        with self._connect() as connection:
            connection.execute(
                "UPDATE operations SET undone_at = ?, undo_run_id = ? WHERE id = ?",
                (utc_now(), undo_run_id, operation_id),
            )

    def list_runs(self, limit: int = 10) -> list[sqlite3.Row]:
        with self._connect() as connection:
            return connection.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()

    def get_run(self, run_id: int) -> sqlite3.Row | None:
        with self._connect() as connection:
            return connection.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()

    def operations_for_run(self, run_id: int) -> list[sqlite3.Row]:
        with self._connect() as connection:
            return connection.execute(
                "SELECT * FROM operations WHERE run_id = ? ORDER BY sequence_index, id", (run_id,)
            ).fetchall()
