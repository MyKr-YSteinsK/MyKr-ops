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


def create_prepared_batch(
    database: Database,
    entries: list[tuple[Path, Path, Path]],
    mode: str = "apply",
    related_item_ids: list[int] | None = None,
) -> int:
    parent = entry_identity(entries[0][0].parent)
    items: list[dict[str, object]] = []
    for index, (source, temporary, final) in enumerate(entries, start=1):
        identity = entry_identity(source)
        items.append(
            {
                "sequence_index": index,
                "object_kind": identity.kind,
                "original_path": source,
                "temporary_path": temporary,
                "final_path": final,
                "volume_serial": identity.volume_serial,
                "file_index": identity.file_index,
                "parent_volume_serial": parent.volume_serial,
                "parent_file_index": parent.file_index,
                "related_item_id": related_item_ids[index - 1] if related_item_ids is not None else None,
            }
        )
    return database.create_rename_run(mode, items)


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


def test_recovery_rolls_back_a_partially_finalized_swap_as_one_batch(tmp_path: Path) -> None:
    first = tmp_path / "A.txt"
    second = tmp_path / "B.txt"
    temp_first = tmp_path / ".tmp-A"
    temp_second = tmp_path / ".tmp-B"
    first.write_text("A", encoding="utf-8")
    second.write_text("B", encoding="utf-8")
    database = make_database(tmp_path)
    run_id = create_prepared_batch(database, [(first, temp_first, second), (second, temp_second, first)])
    first_identity = entry_identity(first)
    second_identity = entry_identity(second)
    rename_entry_without_overwrite(first, temp_first, first_identity)
    rename_entry_without_overwrite(second, temp_second, second_identity)
    rename_entry_without_overwrite(temp_first, second, first_identity)

    recover_rename_operations(database)

    assert first.read_text(encoding="utf-8") == "A"
    assert second.read_text(encoding="utf-8") == "B"
    assert not temp_first.exists()
    assert not temp_second.exists()
    assert {row["state"] for row in database.rename_items_for_run(run_id)} == {"rolled_back"}
    assert database.get_run(run_id)["status"] == "failed"  # type: ignore[index]


def test_recovery_rolls_back_a_partially_finalized_three_cycle_as_one_batch(tmp_path: Path) -> None:
    first = tmp_path / "A.txt"
    second = tmp_path / "B.txt"
    third = tmp_path / "C.txt"
    temp_first = tmp_path / ".tmp-A"
    temp_second = tmp_path / ".tmp-B"
    temp_third = tmp_path / ".tmp-C"
    for path, content in ((first, "A"), (second, "B"), (third, "C")):
        path.write_text(content, encoding="utf-8")
    database = make_database(tmp_path)
    run_id = create_prepared_batch(
        database,
        [(first, temp_first, second), (second, temp_second, third), (third, temp_third, first)],
    )
    identities = {path: entry_identity(path) for path in (first, second, third)}
    rename_entry_without_overwrite(first, temp_first, identities[first])
    rename_entry_without_overwrite(second, temp_second, identities[second])
    rename_entry_without_overwrite(third, temp_third, identities[third])
    rename_entry_without_overwrite(temp_first, second, identities[first])
    rename_entry_without_overwrite(temp_second, third, identities[second])

    recover_rename_operations(database)

    assert [path.read_text(encoding="utf-8") for path in (first, second, third)] == ["A", "B", "C"]
    assert not any(path.exists() for path in (temp_first, temp_second, temp_third))
    assert {row["state"] for row in database.rename_items_for_run(run_id)} == {"rolled_back"}


def test_recovery_finishes_a_completed_swap_without_rolling_it_back(tmp_path: Path) -> None:
    first = tmp_path / "A.txt"
    second = tmp_path / "B.txt"
    temp_first = tmp_path / ".tmp-A"
    temp_second = tmp_path / ".tmp-B"
    first.write_text("A", encoding="utf-8")
    second.write_text("B", encoding="utf-8")
    database = make_database(tmp_path)
    run_id = create_prepared_batch(database, [(first, temp_first, second), (second, temp_second, first)])
    first_identity = entry_identity(first)
    second_identity = entry_identity(second)
    rename_entry_without_overwrite(first, temp_first, first_identity)
    rename_entry_without_overwrite(second, temp_second, second_identity)
    rename_entry_without_overwrite(temp_first, second, first_identity)
    rename_entry_without_overwrite(temp_second, first, second_identity)
    for row in database.rename_items_for_run(run_id):
        database.update_rename_item_state(int(row["id"]), "success")

    recover_rename_operations(database)

    assert first.read_text(encoding="utf-8") == "B"
    assert second.read_text(encoding="utf-8") == "A"
    assert database.get_run(run_id)["status"] == "success"  # type: ignore[index]


def test_recovery_rejects_an_unknown_occupant_at_a_recorded_batch_location(tmp_path: Path) -> None:
    source = tmp_path / "A.txt"
    temporary = tmp_path / ".tmp-A"
    final = tmp_path / "B.txt"
    source.write_text("A", encoding="utf-8")
    database = make_database(tmp_path)
    run_id = create_prepared_rename(database, source, temporary, final)
    identity = entry_identity(source)
    rename_entry_without_overwrite(source, temporary, identity)
    final.write_text("external", encoding="utf-8")

    with pytest.raises(RenameRecoveryRequired, match="unrelated"):
        recover_rename_operations(database)

    assert database.rename_items_for_run(run_id)[0]["state"] == "recovery_required"


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


def test_recovery_rolls_back_a_partially_finalized_undo_swap_as_one_batch(tmp_path: Path) -> None:
    first = tmp_path / "A.txt"
    second = tmp_path / "B.txt"
    first.write_text("A", encoding="utf-8")
    second.write_text("B", encoding="utf-8")
    database = make_database(tmp_path)
    apply_plan = build_rename_plan([first, second])
    apply_plan.set_manual_stem(0, "B")
    apply_plan.set_manual_stem(1, "A")
    applied = apply_rename(apply_plan, database)
    apply_rows = database.rename_items_for_run(applied.run_id or 0)
    undo_entries: list[tuple[Path, Path, Path]] = []
    for row in apply_rows:
        undo_entries.append((Path(row["final_path"]), tmp_path / f".undo-{row['id']}", Path(row["original_path"])))
    undo_run = create_prepared_batch(
        database,
        undo_entries,
        mode="undo",
        related_item_ids=[int(row["id"]) for row in apply_rows],
    )
    first_undo, second_undo = database.rename_items_for_run(undo_run)
    first_identity = entry_identity(Path(first_undo["original_path"]))
    second_identity = entry_identity(Path(second_undo["original_path"]))
    rename_entry_without_overwrite(
        Path(first_undo["original_path"]), Path(first_undo["temporary_path"]), first_identity
    )
    rename_entry_without_overwrite(
        Path(second_undo["original_path"]), Path(second_undo["temporary_path"]), second_identity
    )
    rename_entry_without_overwrite(
        Path(first_undo["temporary_path"]), Path(first_undo["final_path"]), first_identity
    )

    recover_rename_operations(database)

    assert first.read_text(encoding="utf-8") == "B"
    assert second.read_text(encoding="utf-8") == "A"
    assert database.get_run(undo_run)["status"] == "failed"  # type: ignore[index]
    assert all(row["undone_at"] is None for row in database.rename_items_for_run(applied.run_id or 0))


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
