from __future__ import annotations

from pathlib import Path

import pytest

from mykr_ops.database import Database, MutationBlockedError
from mykr_ops.filesystem import entry_identity, rename_entry_without_overwrite
from mykr_ops.models import NotesConfig
from mykr_ops.notes import apply_notes, undo_latest
from mykr_ops.rename import (
    RenameRecoveryRequired,
    RenameRules,
    apply_rename,
    build_rename_plan,
    recover_rename_operations,
)


def make_database(tmp_path: Path) -> Database:
    database = Database(tmp_path / "state" / "mykr-ops.db")
    database.initialize()
    return database


def create_prepared_rename(database: Database, source: Path, temporary: Path, final: Path) -> int:
    source_identity = entry_identity(source)
    parent_identity = entry_identity(source.parent)
    return database.create_rename_run(
        "apply",
        [{
            "sequence_index": 1,
            "object_kind": source_identity.kind,
            "original_path": source,
            "temporary_path": temporary,
            "final_path": final,
            "volume_serial": source_identity.volume_serial,
            "file_index": source_identity.file_index,
            "parent_volume_serial": parent_identity.volume_serial,
            "parent_file_index": parent_identity.file_index,
        }],
    )


def test_recovery_rolls_interrupted_staged_rename_back_to_original(tmp_path: Path) -> None:
    source = tmp_path / "draft.txt"
    temporary = tmp_path / ".mykr-ops-rename-interrupted"
    final = tmp_path / "final.txt"
    source.write_text("data", encoding="utf-8")
    database = make_database(tmp_path)
    run_id = create_prepared_rename(database, source, temporary, final)
    identity = entry_identity(source)
    rename_entry_without_overwrite(source, temporary, identity)

    recover_rename_operations(database)

    assert source.read_text(encoding="utf-8") == "data"
    assert not temporary.exists()
    assert database.rename_items_for_run(run_id)[0]["state"] == "rolled_back"
    assert database.get_run(run_id)["status"] == "failed"  # type: ignore[index]


def test_recovery_marks_ambiguous_hardlinked_locations_for_manual_recovery(tmp_path: Path) -> None:
    source = tmp_path / "draft.txt"
    temporary = tmp_path / ".mykr-ops-rename-interrupted"
    final = tmp_path / "final.txt"
    source.write_text("data", encoding="utf-8")
    database = make_database(tmp_path)
    run_id = create_prepared_rename(database, source, temporary, final)
    temporary.hardlink_to(source)

    with pytest.raises(RenameRecoveryRequired, match="ambiguous"):
        recover_rename_operations(database)

    assert source.exists()
    assert temporary.exists()
    assert database.rename_items_for_run(run_id)[0]["state"] == "recovery_required"


def test_recovery_finishes_an_all_success_run_interrupted_before_run_finalization(tmp_path: Path) -> None:
    source = tmp_path / "draft.txt"
    temporary = tmp_path / ".mykr-ops-rename-interrupted"
    final = tmp_path / "final.txt"
    source.write_text("data", encoding="utf-8")
    database = make_database(tmp_path)
    run_id = create_prepared_rename(database, source, temporary, final)
    identity = entry_identity(source)
    rename_entry_without_overwrite(source, temporary, identity)
    row = database.rename_items_for_run(run_id)[0]
    database.update_rename_item_state(int(row["id"]), "staged")
    rename_entry_without_overwrite(temporary, final, identity)
    database.update_rename_item_state(int(row["id"]), "success")

    recover_rename_operations(database)

    assert final.read_text(encoding="utf-8") == "data"
    assert database.get_run(run_id)["status"] == "success"  # type: ignore[index]


def test_recovery_finalizes_interrupted_successful_undo_links(tmp_path: Path) -> None:
    source = tmp_path / "draft.txt"
    source.write_text("data", encoding="utf-8")
    database = make_database(tmp_path)
    apply_result = apply_rename(build_rename_plan([source], RenameRules(prefix="done-")), database)
    apply_item = database.rename_items_for_run(apply_result.run_id or 0)[0]
    current = tmp_path / "done-draft.txt"
    temporary = tmp_path / ".mykr-ops-undo-interrupted"
    identity = entry_identity(current)
    parent = entry_identity(tmp_path)
    undo_run = database.create_rename_run(
        "undo",
        [{
            "sequence_index": 1,
            "object_kind": "file",
            "original_path": current,
            "temporary_path": temporary,
            "final_path": source,
            "volume_serial": identity.volume_serial,
            "file_index": identity.file_index,
            "parent_volume_serial": parent.volume_serial,
            "parent_file_index": parent.file_index,
            "related_item_id": int(apply_item["id"]),
        }],
    )
    rename_entry_without_overwrite(current, temporary, identity)
    undo_item = database.rename_items_for_run(undo_run)[0]
    database.update_rename_item_state(int(undo_item["id"]), "staged")
    rename_entry_without_overwrite(temporary, source, identity)
    database.update_rename_item_state(int(undo_item["id"]), "success")

    recover_rename_operations(database)

    assert source.read_text(encoding="utf-8") == "data"
    assert database.get_run(undo_run)["status"] == "success"  # type: ignore[index]
    assert database.rename_items_for_run(apply_result.run_id or 0)[0]["undone_at"]


def test_rename_mutation_stops_when_notes_recovery_is_unresolved(tmp_path: Path) -> None:
    source = tmp_path / "draft.txt"
    source.write_text("data", encoding="utf-8")
    database = make_database(tmp_path)
    run_id = database.create_run("notes", "apply", matched_count=1)
    database.prepare_operation(run_id=run_id, sequence_index=1, action="move", source_path=source)
    plan = build_rename_plan([source], RenameRules(prefix="done-"))

    with pytest.raises(MutationBlockedError, match="notes recovery"):
        apply_rename(plan, database)
    assert source.exists()


def test_unresolved_rename_blocks_notes_apply_and_undo_without_recovering_it(tmp_path: Path) -> None:
    notes_source = tmp_path / "Downloads"
    study_root = tmp_path / "Study"
    notes_source.mkdir()
    study_root.mkdir()
    rename_source = tmp_path / "draft.txt"
    rename_source.write_text("data", encoding="utf-8")
    database = make_database(tmp_path)
    create_prepared_rename(
        database,
        rename_source,
        tmp_path / ".mykr-ops-rename-interrupted",
        tmp_path / "final.txt",
    )
    config = NotesConfig(notes_source, study_root)

    with pytest.raises(MutationBlockedError, match="rename recovery"):
        apply_notes(config, database)
    with pytest.raises(MutationBlockedError, match="rename recovery"):
        undo_latest(config, database)
    assert rename_source.exists()
