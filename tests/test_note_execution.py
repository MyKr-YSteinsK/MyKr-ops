from __future__ import annotations

import os
from pathlib import Path

import pytest

from mykr_ops.database import Database
from mykr_ops.models import NotesConfig, PlanItem, PlanResult, PlanStatus
from mykr_ops import filesystem, notes


def make_config(tmp_path: Path) -> NotesConfig:
    source = tmp_path / "Downloads"
    study = tmp_path / "Study"
    source.mkdir()
    study.mkdir()
    return NotesConfig(source, study)


def database_for(tmp_path: Path) -> Database:
    return Database(tmp_path / "state" / "mykr-ops.db")


def write_note(config: NotesConfig, name: str, content: str = "note") -> Path:
    source = config.source_dir / name
    source.write_text(content, encoding="utf-8")
    return source


def test_apply_creates_directories_moves_and_renames_note(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    source = write_note(config, "01-Python_notes_CS_DataProcessing.md", "content")

    result = notes.apply_notes(config, database_for(tmp_path))
    destination = config.study_root / "CS" / "DataProcessing" / "01-Python_notes.md"

    assert result.moved_count == 1
    assert result.created_dir_count == 2
    assert not source.exists()
    assert destination.read_text(encoding="utf-8") == "content"


def test_apply_processes_multiple_courses(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    write_note(config, "01-One_CS_Algorithms.md")
    write_note(config, "02-Two_Math_Algebra.md")

    result = notes.apply_notes(config, database_for(tmp_path))

    assert result.moved_count == 2
    assert (config.study_root / "CS" / "Algorithms" / "01-One.md").exists()
    assert (config.study_root / "Math" / "Algebra" / "02-Two.md").exists()


def test_apply_leaves_duplicate_and_conflict_untouched(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    duplicate = write_note(config, "01-Duplicate_CS_Course.md", "same")
    conflict = write_note(config, "02-Conflict_CS_Course.md", "source")
    target_dir = config.study_root / "CS" / "Course"
    target_dir.mkdir(parents=True)
    (target_dir / "01-Duplicate.md").write_text("same", encoding="utf-8")
    (target_dir / "02-Conflict.md").write_text("different", encoding="utf-8")

    result = notes.apply_notes(config, database_for(tmp_path))

    assert result.duplicate_count == 1
    assert result.conflict_count == 1
    assert duplicate.exists() and conflict.exists()
    assert (target_dir / "02-Conflict.md").read_text(encoding="utf-8") == "different"


def test_apply_continues_after_one_move_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = make_config(tmp_path)
    write_note(config, "01-Fail_CS_Course.md")
    write_note(config, "02-Works_CS_Course.md")
    actual_move = notes.move_file_without_overwrite

    def fail_first(source: Path, destination: Path, digest: str, **_: object) -> None:
        if source.name.startswith("01-"):
            raise notes.FilesystemSafetyError("simulated locked file")
        actual_move(source, destination, digest)

    monkeypatch.setattr(notes, "move_file_without_overwrite", fail_first)
    result = notes.apply_notes(config, database_for(tmp_path))

    assert result.failed_count == 1
    assert result.moved_count == 1
    assert (config.source_dir / "01-Fail_CS_Course.md").exists()
    assert (config.study_root / "CS" / "Course" / "02-Works.md").exists()


def test_apply_rejects_source_changed_after_planning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = make_config(tmp_path)
    source = write_note(config, "01-Topic_CS_Course.md", "before")
    actual_ensure = notes._ensure_destination_directories

    def change_source(*args: object, **kwargs: object) -> tuple[Path, list[Path]]:
        source.write_text("after", encoding="utf-8")
        return actual_ensure(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(notes, "_ensure_destination_directories", change_source)
    result = notes.apply_notes(config, database_for(tmp_path))

    assert result.failed_count == 1
    assert source.read_text(encoding="utf-8") == "after"
    assert not (config.study_root / "CS" / "Course" / "01-Topic.md").exists()


def test_apply_records_success_and_failure_operations(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = make_config(tmp_path)
    write_note(config, "01-Good_CS_Course.md")
    write_note(config, "02-Bad_CS_Course.md")
    database = database_for(tmp_path)
    actual_move = notes.move_file_without_overwrite

    def fail_bad(source: Path, destination: Path, digest: str, **_: object) -> None:
        if source.name.startswith("02-"):
            raise notes.FilesystemSafetyError("locked")
        actual_move(source, destination, digest)

    monkeypatch.setattr(notes, "move_file_without_overwrite", fail_bad)
    result = notes.apply_notes(config, database)
    operations = database.operations_for_run(result.run_id or 0)

    assert {operation["status"] for operation in operations if operation["action"] == "move"} == {
        "success",
        "failed",
    }
    by_source = {Path(operation["source_path"]).name: operation for operation in operations if operation["action"] == "move"}
    assert by_source["01-Good_CS_Course.md"]["error_type"] is None
    assert by_source["02-Bad_CS_Course.md"]["error_type"] == "source_locked"


def test_apply_removes_empty_current_run_directories_after_all_moves_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = make_config(tmp_path)
    write_note(config, "01-Topic_CS_Course.md")

    def fail_move(_: Path, __: Path, ___: str, **____: object) -> None:
        raise notes.FilesystemSafetyError("locked")

    monkeypatch.setattr(notes, "move_file_without_overwrite", fail_move)
    result = notes.apply_notes(config, database_for(tmp_path))

    assert result.failed_count == 1
    assert not (config.study_root / "CS").exists()


def test_apply_removes_first_level_created_before_course_creation_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = make_config(tmp_path)
    write_note(config, "01-Topic_CS_Course.md")
    actual_create = notes.create_child_directory
    call_count = 0

    def fail_course_creation(
        parent: Path, name: str, root: Path, **kwargs: object
    ) -> tuple[Path, bool]:
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise notes.FilesystemSafetyError("course directory is locked")
        return actual_create(parent, name, root, **kwargs)

    monkeypatch.setattr(notes, "create_child_directory", fail_course_creation)
    result = notes.apply_notes(config, database_for(tmp_path))

    assert result.failed_count == 1
    assert not (config.study_root / "CS").exists()


def test_apply_records_destination_hash(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    write_note(config, "01-Topic_CS_Course.md", "stable")
    database = database_for(tmp_path)

    result = notes.apply_notes(config, database)
    operation = next(row for row in database.operations_for_run(result.run_id or 0) if row["action"] == "move")

    assert operation["status"] == "success"
    assert operation["sha256"] == notes.sha256_file(Path(operation["destination_path"]))


@pytest.mark.skipif(os.name != "nt", reason="Windows fixed source-root integration")
def test_windows_apply_uses_fixed_source_root_through_root_replacement_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = make_config(tmp_path)
    write_note(config, "01-Topic_CS_Course.md")
    replacement = tmp_path / "Downloads-replacement"
    actual_move = notes.move_file_without_overwrite
    replacement_attempts: list[OSError] = []
    source_root_identities: list[tuple[tuple[int, int], tuple[int, int]]] = []

    def attempt_source_root_replacement(
        source: Path, destination: Path, digest: str, **kwargs: object
    ) -> None:
        source_root = kwargs["source_root"]
        assert isinstance(source_root, filesystem.VerifiedDirectoryRoot)
        source_root_identities.append(
            (source_root.identity[:2], filesystem._handle_identity(source_root.handle, "source directory")[:2])
        )
        try:
            config.source_dir.rename(replacement)
        except OSError as exc:
            replacement_attempts.append(exc)
        actual_move(source, destination, digest, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(notes, "move_file_without_overwrite", attempt_source_root_replacement)
    result = notes.apply_notes(config, database_for(tmp_path))

    assert result.moved_count == 1
    assert source_root_identities and source_root_identities[0][0] == source_root_identities[0][1]
    assert replacement_attempts
    assert (config.study_root / "CS" / "Course" / "01-Topic.md").exists()
    assert not replacement.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows fixed source-root integration")
def test_windows_apply_rejects_source_outside_similar_prefix_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = make_config(tmp_path)
    outside = tmp_path / "DownloadsBackup"
    outside.mkdir()
    source = outside / "01-Topic_CS_Course.md"
    source.write_text("note", encoding="utf-8")
    metadata = source.stat()
    item = PlanItem(
        source=source,
        status=PlanStatus.READY,
        parsed=notes.parse_note_filename(source.name),
        source_size=metadata.st_size,
        source_mtime_ns=metadata.st_mtime_ns,
    )
    monkeypatch.setattr(notes, "plan_notes", lambda _: PlanResult(items=[item]))
    database = database_for(tmp_path)

    result = notes.apply_notes(config, database)

    move = next(row for row in database.operations_for_run(result.run_id or 0) if row["action"] == "move")
    assert result.failed_count == 1
    assert move["error_type"] == "path_escape"
    assert source.exists()
    assert not (config.study_root / "CS" / "Course" / "01-Topic.md").exists()
    assert not (config.study_root / "CS").exists()
