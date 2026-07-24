from __future__ import annotations

from pathlib import Path

import pytest

from mykr_ops import notes
from mykr_ops.database import Database
from mykr_ops.models import NotesConfig, PlanStatus


def make_config(tmp_path: Path) -> NotesConfig:
    source = tmp_path / "Downloads"
    study = tmp_path / "Study"
    source.mkdir()
    study.mkdir()
    return NotesConfig(source, study)


def write_source(config: NotesConfig, content: str = "note") -> Path:
    source = config.source_dir / "01-Topic_CS_Course.md"
    source.write_text(content, encoding="utf-8")
    return source


def test_preview_with_missing_target_does_not_hash_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = make_config(tmp_path)
    source = write_source(config)

    def hash_must_not_run(path: Path) -> str:
        raise AssertionError(f"preview unexpectedly hashed {path}")

    monkeypatch.setattr(notes, "sha256_file", hash_must_not_run)
    plan = notes.plan_notes(config)

    assert plan.items[0].status == PlanStatus.READY
    assert plan.items[0].source == source
    assert plan.items[0].source_sha256 is None


@pytest.mark.parametrize(
    ("target_content", "expected_status"),
    [("same", PlanStatus.DUPLICATE), ("different", PlanStatus.CONFLICT)],
)
def test_preview_hashes_both_sides_when_target_exists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target_content: str,
    expected_status: PlanStatus,
) -> None:
    config = make_config(tmp_path)
    source = write_source(config, "same")
    target = config.study_root / "CS" / "Course" / "01-Topic.md"
    target.parent.mkdir(parents=True)
    target.write_text(target_content, encoding="utf-8")
    actual_hash = notes.sha256_file
    hashed: list[Path] = []

    def record_hash(path: Path) -> str:
        hashed.append(path)
        return actual_hash(path)

    monkeypatch.setattr(notes, "sha256_file", record_hash)
    plan = notes.plan_notes(config)

    assert plan.items[0].status == expected_status
    assert plan.items[0].source_sha256 is not None
    assert hashed == [source, target]


def test_apply_hashes_a_preview_ready_item_and_records_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = make_config(tmp_path)
    source = write_source(config)
    database = Database(tmp_path / "state" / "mykr-ops.db")
    actual_hash = notes.sha256_file
    hashed: list[Path] = []

    def record_hash(path: Path) -> str:
        hashed.append(path)
        return actual_hash(path)

    monkeypatch.setattr(notes, "sha256_file", record_hash)
    result = notes.apply_notes(config, database)
    operation = next(row for row in database.operations_for_run(result.run_id or 0) if row["action"] == "move")

    assert result.moved_count == 1
    assert source in hashed
    assert operation["sha256"]


def test_apply_rejects_source_changed_after_deferred_preview(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = make_config(tmp_path)
    source = write_source(config, "before")
    actual_ensure = notes._ensure_destination_directories

    def change_source(parsed: object, current_config: NotesConfig) -> tuple[Path, list[Path]]:
        source.write_text("after", encoding="utf-8")
        return actual_ensure(parsed, current_config)  # type: ignore[arg-type]

    monkeypatch.setattr(notes, "_ensure_destination_directories", change_source)
    result = notes.apply_notes(config, Database(tmp_path / "state" / "mykr-ops.db"))

    assert result.failed_count == 1
    assert source.read_text(encoding="utf-8") == "after"
    assert not (config.study_root / "CS" / "Course" / "01-Topic.md").exists()


def test_deferred_preview_detects_a_source_changed_before_apply_verification(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    source = write_source(config, "before")
    item = notes.plan_notes(config).items[0]
    source.write_text("changed after preview", encoding="utf-8")

    with pytest.raises(notes.FilesystemSafetyError, match="source changed after planning"):
        notes._verify_item_for_apply(item)

    assert item.source_sha256 is None
