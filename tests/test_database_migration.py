from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from mykr_ops import cli, notes
from mykr_ops.database import Database, DatabaseSchemaError
from mykr_ops.models import NotesConfig


def make_config(tmp_path: Path) -> NotesConfig:
    source = tmp_path / "Downloads"
    study = tmp_path / "Study"
    source.mkdir()
    study.mkdir()
    return NotesConfig(source, study)


def create_phase1_database(path: Path, config: NotesConfig) -> None:
    target = config.study_root / "CS" / "Course" / "01-Topic.md"
    target.parent.mkdir(parents=True)
    target.write_text("note", encoding="utf-8")
    digest = notes.sha256_file(target)
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE runs (
            id INTEGER PRIMARY KEY,
            command TEXT NOT NULL,
            mode TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            status TEXT NOT NULL,
            matched_count INTEGER NOT NULL DEFAULT 0,
            moved_count INTEGER NOT NULL DEFAULT 0,
            duplicate_count INTEGER NOT NULL DEFAULT 0,
            conflict_count INTEGER NOT NULL DEFAULT 0,
            invalid_count INTEGER NOT NULL DEFAULT 0,
            failed_count INTEGER NOT NULL DEFAULT 0,
            created_dir_count INTEGER NOT NULL DEFAULT 0,
            summary TEXT
        );
        CREATE TABLE operations (
            id INTEGER PRIMARY KEY,
            run_id INTEGER NOT NULL,
            sequence_index INTEGER NOT NULL,
            action TEXT NOT NULL,
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
            created_at TEXT NOT NULL
        );
        """
    )
    source = config.source_dir / "01-Topic_CS_Course.md"
    connection.execute(
        "INSERT INTO runs VALUES (7, 'notes', 'apply', '2026-01-01T00:00:00+00:00', ?, 'success', 1, 1, 0, 0, 0, 0, 2, 'legacy apply')",
        ("2026-01-01T00:00:01+00:00",),
    )
    connection.execute(
        """
        INSERT INTO operations VALUES (11, 7, 1, 'move', ?, ?, 4, 1, ?, 'success', NULL, NULL, NULL, NULL, ?)
        """,
        (str(source), str(target), digest, "2026-01-01T00:00:00+00:00"),
    )
    connection.execute(
        """
        INSERT INTO operations VALUES (13, 7, 2, 'move', ?, ?, 4, 1, ?, 'success', NULL, 12, ?, 12, ?)
        """,
        (
            str(config.source_dir / "02-Older_CS_Course.md"),
            str(config.study_root / "CS" / "Course" / "02-Older.md"),
            digest,
            "2026-01-01T00:00:02+00:00",
            "2026-01-01T00:00:00+00:00",
        ),
    )
    connection.commit()
    connection.close()


def create_phase2a_database(path: Path, config: NotesConfig) -> None:
    create_phase1_database(path, config)
    connection = sqlite3.connect(path)
    connection.execute("ALTER TABLE operations RENAME TO operations_phase1")
    connection.execute(
        """
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
            status TEXT NOT NULL CHECK (status IN ('prepared', 'success', 'duplicate', 'conflict', 'invalid', 'failed', 'skipped', 'recovery_required')),
            reason TEXT,
            related_operation_id INTEGER,
            undone_at TEXT,
            undo_run_id INTEGER,
            created_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        INSERT INTO operations
        SELECT * FROM operations_phase1
        """
    )
    connection.execute("DROP TABLE operations_phase1")
    connection.execute("PRAGMA user_version = 1")
    connection.commit()
    connection.close()


def create_phase2e_database(path: Path, config: NotesConfig) -> None:
    create_phase2a_database(path, config)
    connection = sqlite3.connect(path)
    connection.execute("ALTER TABLE operations ADD COLUMN error_type TEXT")
    connection.execute("PRAGMA user_version = 2")
    connection.commit()
    connection.close()


def create_phase2g_database(path: Path, config: NotesConfig) -> None:
    create_phase2e_database(path, config)
    connection = sqlite3.connect(path)
    connection.execute("ALTER TABLE operations ADD COLUMN directory_name TEXT")
    connection.execute("PRAGMA user_version = 3")
    connection.commit()
    connection.close()


def test_phase1_database_migrates_without_losing_history_and_remains_usable(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    path = tmp_path / "state" / "mykr-ops.db"
    path.parent.mkdir()
    create_phase1_database(path, config)
    database = Database(path)

    database.initialize()

    connection = sqlite3.connect(path)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 4
    assert connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'rename_items'"
    ).fetchone() == ("rename_items",)
    row = connection.execute(
        "SELECT id, source_path, destination_path, sha256, undone_at, error_type, directory_name FROM operations WHERE id = 11"
    ).fetchone()
    undo_metadata = connection.execute(
        "SELECT related_operation_id, undone_at, undo_run_id FROM operations WHERE id = 13"
    ).fetchone()
    connection.close()
    assert row[0] == 11
    assert row[1].endswith("01-Topic_CS_Course.md")
    assert row[2].endswith("01-Topic.md")
    assert row[3]
    assert row[4] is None
    assert row[5] is None
    assert row[6] is None
    assert tuple(undo_metadata) == (12, "2026-01-01T00:00:02+00:00", 12)
    assert database.get_run(7) is not None

    undo = notes.undo_latest(config, database)
    assert undo.moved_count == 1
    apply = notes.apply_notes(config, database)
    assert apply.moved_count == 1


def test_phase2a_database_gains_error_type_without_losing_history(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    path = tmp_path / "state" / "mykr-ops.db"
    path.parent.mkdir()
    create_phase2a_database(path, config)

    database = Database(path)
    database.initialize()

    connection = sqlite3.connect(path)
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    row = connection.execute(
        "SELECT id, source_path, destination_path, sha256, error_type FROM operations WHERE id = 11"
    ).fetchone()
    assert version == 4
    assert connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'rename_items'"
    ).fetchone() == ("rename_items",)
    connection.close()
    assert row[0] == 11
    assert row[1].endswith("01-Topic_CS_Course.md")
    assert row[2].endswith("01-Topic.md")
    assert row[3]
    assert row[4] is None


def test_phase2e_database_gains_directory_name_without_losing_history(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    path = tmp_path / "state" / "mykr-ops.db"
    path.parent.mkdir()
    create_phase2e_database(path, config)

    Database(path).initialize()

    connection = sqlite3.connect(path)
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    row = connection.execute("SELECT id, error_type, directory_name FROM operations WHERE id = 11").fetchone()
    assert version == 4
    assert connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'rename_items'"
    ).fetchone() == ("rename_items",)
    connection.close()
    assert tuple(row) == (11, None, None)


def test_phase2g_database_gains_rename_items_without_losing_history(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    path = tmp_path / "state" / "mykr-ops.db"
    path.parent.mkdir()
    create_phase2g_database(path, config)

    Database(path).initialize()

    connection = sqlite3.connect(path)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 4
    assert connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'rename_items'"
    ).fetchone() == ("rename_items",)
    assert connection.execute("SELECT id, directory_name FROM operations WHERE id = 11").fetchone() == (11, None)
    connection.close()


@pytest.mark.parametrize(
    "create_database", [create_phase1_database, create_phase2a_database, create_phase2e_database, create_phase2g_database]
)
def test_history_migrates_existing_legacy_database_before_reading(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    create_database: object,
) -> None:
    config = make_config(tmp_path)
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    path = state_dir / "mykr-ops.db"
    create_database(path, config)  # type: ignore[operator]
    monkeypatch.setattr(cli, "load_notes_config", lambda: config)
    monkeypatch.setattr(cli, "application_data_dir", lambda *, create: state_dir)

    assert cli.main(["history"]) == 0

    connection = sqlite3.connect(path)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 4
    connection.close()
    assert "Run 7: notes apply success" in capsys.readouterr().out


def test_history_without_database_does_not_create_state_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config = make_config(tmp_path)
    state_dir = tmp_path / "absent-state"
    monkeypatch.setattr(cli, "load_notes_config", lambda: config)
    monkeypatch.setattr(cli, "application_data_dir", lambda *, create: state_dir)

    assert cli.main(["history"]) == 0

    assert not state_dir.exists()
    assert capsys.readouterr().out == "No recorded runs.\n"


def test_unknown_schema_stops_safely(tmp_path: Path) -> None:
    path = tmp_path / "unknown.db"
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA user_version = 99")
    connection.commit()
    connection.close()

    with pytest.raises(DatabaseSchemaError, match="not recognized"):
        Database(path).initialize()
