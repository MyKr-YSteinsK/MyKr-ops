from __future__ import annotations

from pathlib import Path

import pytest

from mykr_ops.database import Database
from mykr_ops.rename import RenameError, RenameRules, apply_rename, build_rename_plan, undo_latest_rename


def make_database(tmp_path: Path) -> Database:
    database = Database(tmp_path / "state" / "mykr-ops.db")
    database.initialize()
    return database


def test_undo_restores_a_successful_batch_and_makes_it_ineligible(tmp_path: Path) -> None:
    first = tmp_path / "first.txt"
    second = tmp_path / "second folder"
    first.write_text("first", encoding="utf-8")
    second.mkdir()
    database = make_database(tmp_path)

    applied = apply_rename(build_rename_plan([first, second], RenameRules(prefix="done-")), database)
    restored = undo_latest_rename(database)

    assert not applied.failed
    assert not restored.failed
    assert first.read_text(encoding="utf-8") == "first"
    assert second.is_dir()
    assert undo_latest_rename(database).message == "No eligible rename batch to undo."
    apply_items = database.rename_items_for_run(applied.run_id or 0)
    assert all(item["undone_at"] for item in apply_items)


def test_undo_precheck_is_all_or_nothing_when_a_final_source_changed(tmp_path: Path) -> None:
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("first", encoding="utf-8")
    second.write_text("second", encoding="utf-8")
    database = make_database(tmp_path)
    applied = apply_rename(build_rename_plan([first, second], RenameRules(prefix="done-")), database)
    changed = tmp_path / "done-first.txt"
    changed.unlink()
    changed.write_text("replacement", encoding="utf-8")

    with pytest.raises(RenameError, match="source changed"):
        undo_latest_rename(database)
    assert changed.read_text(encoding="utf-8") == "replacement"
    assert (tmp_path / "done-second.txt").read_text(encoding="utf-8") == "second"
    assert database.latest_eligible_rename_apply_run()["id"] == applied.run_id  # type: ignore[index]


def test_undo_precheck_does_not_overwrite_recreated_original_name(tmp_path: Path) -> None:
    source = tmp_path / "draft.txt"
    source.write_text("original", encoding="utf-8")
    database = make_database(tmp_path)
    apply_rename(build_rename_plan([source], RenameRules(prefix="done-")), database)
    source.write_text("new user file", encoding="utf-8")

    with pytest.raises(RenameError, match="occupied"):
        undo_latest_rename(database)
    assert source.read_text(encoding="utf-8") == "new user file"
    assert (tmp_path / "done-draft.txt").read_text(encoding="utf-8") == "original"
