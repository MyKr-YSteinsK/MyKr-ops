from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
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
                CREATE TABLE IF NOT EXISTS operations (
                    id INTEGER PRIMARY KEY,
                    run_id INTEGER NOT NULL,
                    sequence_index INTEGER NOT NULL,
                    action TEXT NOT NULL CHECK (action IN ('move', 'mkdir', 'undo_move', 'undo_rmdir')),
                    source_path TEXT,
                    destination_path TEXT,
                    file_size INTEGER,
                    source_mtime_ns INTEGER,
                    sha256 TEXT,
                    status TEXT NOT NULL CHECK (status IN ('success', 'duplicate', 'conflict', 'invalid', 'failed', 'skipped')),
                    reason TEXT,
                    related_operation_id INTEGER,
                    undone_at TEXT,
                    undo_run_id INTEGER,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES runs(id)
                );
                CREATE INDEX IF NOT EXISTS operations_run_id_idx ON operations(run_id);
                CREATE INDEX IF NOT EXISTS operations_undo_run_id_idx ON operations(undo_run_id);
                CREATE INDEX IF NOT EXISTS operations_related_operation_id_idx ON operations(related_operation_id);
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def create_run(self, command: str, mode: str) -> int:
        self.initialize()
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO runs (command, mode, started_at, status) VALUES (?, ?, ?, 'running')",
                (command, mode, utc_now()),
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
                         sha256: str | None = None, reason: str | None = None,
                         related_operation_id: int | None = None) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO operations (
                    run_id, sequence_index, action, source_path, destination_path, file_size,
                    source_mtime_ns, sha256, status, reason, related_operation_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (run_id, sequence_index, action,
                 str(source_path) if source_path is not None else None,
                 str(destination_path) if destination_path is not None else None,
                 file_size, source_mtime_ns, sha256, status, reason, related_operation_id, utc_now()),
            )
            return int(cursor.lastrowid)

    def latest_eligible_apply_run(self) -> sqlite3.Row | None:
        with self._connect() as connection:
            return connection.execute(
                """
                SELECT r.*
                FROM runs AS r
                WHERE r.mode = 'apply'
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
                ORDER BY LENGTH(destination_path) DESC, id DESC
                """,
                (run_id,),
            ).fetchall()

    def mark_move_undone(self, operation_id: int, undo_run_id: int) -> None:
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
