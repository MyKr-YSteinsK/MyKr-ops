from __future__ import annotations

import ctypes
import os
from pathlib import Path

import pytest

from mykr_ops.database import Database
from mykr_ops.rename import RenameError, RenameRules, apply_rename, build_rename_plan
import mykr_ops.rename as rename_module
from mykr_ops import filesystem


def make_database(tmp_path: Path) -> Database:
    database = Database(tmp_path / "state" / "mykr-ops.db")
    database.initialize()
    return database


def test_apply_renames_files_and_folders_without_hashing(tmp_path: Path) -> None:
    file = tmp_path / "draft.txt"
    folder = tmp_path / "draft folder"
    file.write_text("same bytes stay untouched", encoding="utf-8")
    folder.mkdir()
    database = make_database(tmp_path)
    plan = build_rename_plan([file, folder], RenameRules(prefix="done-"))

    result = apply_rename(plan, database)

    assert not result.failed
    assert (tmp_path / "done-draft.txt").read_text(encoding="utf-8") == "same bytes stay untouched"
    assert (tmp_path / "done-draft folder").is_dir()
    rows = database.rename_items_for_run(result.run_id or 0)
    assert [row["state"] for row in rows] == ["success", "success"]
    assert {row["object_kind"] for row in rows} == {"file", "directory"}


def test_apply_handles_swap_three_cycle_and_case_only_rename(tmp_path: Path) -> None:
    first = tmp_path / "one.txt"
    second = tmp_path / "two.txt"
    third = tmp_path / "three.txt"
    first.write_text("one", encoding="utf-8")
    second.write_text("two", encoding="utf-8")
    third.write_text("three", encoding="utf-8")
    plan = build_rename_plan([first, second, third])
    plan.set_manual_stem(0, "two")
    plan.set_manual_stem(1, "three")
    plan.set_manual_stem(2, "one")

    result = apply_rename(plan, make_database(tmp_path))

    assert not result.failed
    assert (tmp_path / "one.txt").read_text(encoding="utf-8") == "three"
    assert (tmp_path / "two.txt").read_text(encoding="utf-8") == "one"
    assert (tmp_path / "three.txt").read_text(encoding="utf-8") == "two"

    case_source = tmp_path / "CASE.txt"
    case_source.write_text("case", encoding="utf-8")
    case_plan = build_rename_plan([case_source])
    case_plan.set_manual_stem(0, "case")
    case_result = apply_rename(case_plan, make_database(tmp_path))
    assert not case_result.failed
    assert (tmp_path / "case.txt").read_text(encoding="utf-8") == "case"


def test_apply_revalidates_source_identity_and_does_not_create_run_for_stale_preview(tmp_path: Path) -> None:
    source = tmp_path / "draft.txt"
    source.write_text("old", encoding="utf-8")
    database = make_database(tmp_path)
    plan = build_rename_plan([source], RenameRules(prefix="done-"))
    source.unlink()
    source.write_text("replacement", encoding="utf-8")

    with pytest.raises(RenameError, match="identity changed"):
        apply_rename(plan, database)
    assert database.list_runs() == []
    assert source.read_text(encoding="utf-8") == "replacement"


def test_exactly_unchanged_batch_is_skipped_without_a_database_run(tmp_path: Path) -> None:
    source = tmp_path / "draft.txt"
    source.write_text("data", encoding="utf-8")
    database = make_database(tmp_path)

    result = apply_rename(build_rename_plan([source]), database)

    assert result.message == "No selected names need to change."
    assert source.read_text(encoding="utf-8") == "data"
    assert database.list_runs() == []


def test_apply_conflict_leaves_files_and_database_unchanged(tmp_path: Path) -> None:
    source = tmp_path / "draft.txt"
    occupied = tmp_path / "done-draft.txt"
    source.write_text("source", encoding="utf-8")
    occupied.write_text("occupied", encoding="utf-8")
    database = make_database(tmp_path)
    plan = build_rename_plan([source], RenameRules(prefix="done-"))

    assert not plan.can_apply
    with pytest.raises(RenameError, match="contains invalid"):
        apply_rename(plan, database)
    assert source.read_text(encoding="utf-8") == "source"
    assert occupied.read_text(encoding="utf-8") == "occupied"
    assert database.list_runs() == []


def test_failure_during_final_stage_rolls_the_entire_batch_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("first", encoding="utf-8")
    second.write_text("second", encoding="utf-8")
    database = make_database(tmp_path)
    plan = build_rename_plan([first, second], RenameRules(prefix="done-"))
    real_rename = rename_module.rename_entry_without_overwrite
    call_count = 0

    def fail_once(*args: object, **kwargs: object) -> None:
        nonlocal call_count
        call_count += 1
        if call_count == 3:
            raise RenameError("injected final-stage failure")
        real_rename(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(rename_module, "rename_entry_without_overwrite", fail_once)
    result = apply_rename(plan, database)

    assert result.failed
    assert first.read_text(encoding="utf-8") == "first"
    assert second.read_text(encoding="utf-8") == "second"
    rows = database.rename_items_for_run(result.run_id or 0)
    assert [row["state"] for row in rows] == ["rolled_back", "rolled_back"]


@pytest.mark.skipif(os.name != "nt", reason="Windows handle-relative rename")
def test_windows_rename_uses_the_verified_parent_handle_and_relative_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.txt"
    destination = tmp_path / "renamed.txt"
    source.write_text("data", encoding="utf-8")
    identity = filesystem.entry_identity(source)
    actual_rename = filesystem._nt_set_information_file

    def inspect(
        source_handle: int, io_status: object, rename_buffer: object, buffer_size: int, information_class: int
    ) -> int:
        info = ctypes.cast(rename_buffer, ctypes.POINTER(filesystem._FileRenameInformation)).contents
        encoded = ctypes.string_at(
            ctypes.addressof(rename_buffer) + filesystem._FileRenameInformation.FileName.offset,
            info.FileNameLength,
        )
        assert not bool(info.ReplaceIfExists)
        assert info.RootDirectory == parent.handle  # type: ignore[union-attr]
        assert encoded.decode("utf-16-le") == destination.name
        assert str(destination).encode("utf-16-le") not in ctypes.string_at(rename_buffer, buffer_size)
        return actual_rename(source_handle, io_status, rename_buffer, buffer_size, information_class)

    monkeypatch.setattr(filesystem, "_nt_set_information_file", inspect)
    with filesystem.open_verified_directory_root(tmp_path, "rename parent") as parent:
        filesystem.rename_entry_without_overwrite(source, destination, identity, verified_parent=parent)

    assert destination.read_text(encoding="utf-8") == "data"


@pytest.mark.skipif(os.name != "nt", reason="Windows handle-relative rename")
def test_windows_native_failure_keeps_source_and_uses_stable_error_type(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.txt"
    destination = tmp_path / "renamed.txt"
    source.write_text("data", encoding="utf-8")
    identity = filesystem.entry_identity(source)
    monkeypatch.setattr(filesystem, "_nt_set_information_file", lambda *_: 0xC0000035)

    with pytest.raises(filesystem.FilesystemSafetyError) as exc_info:
        filesystem.rename_entry_without_overwrite(source, destination, identity)

    assert exc_info.value.error_type == "destination_exists"
    assert source.read_text(encoding="utf-8") == "data"
    assert not destination.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows handle-relative rename")
def test_windows_verified_rename_parent_cannot_be_replaced_during_rename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = tmp_path / "parent"
    replacement = tmp_path / "replacement"
    parent.mkdir()
    source = parent / "source.txt"
    destination = parent / "renamed.txt"
    source.write_text("data", encoding="utf-8")
    identity = filesystem.entry_identity(source)
    attempts: list[OSError] = []
    actual_rename = filesystem._nt_set_information_file

    def attempt_replacement(
        source_handle: int, io_status: object, rename_buffer: object, buffer_size: int, information_class: int
    ) -> int:
        try:
            parent.rename(replacement)
        except OSError as exc:
            attempts.append(exc)
        return actual_rename(source_handle, io_status, rename_buffer, buffer_size, information_class)

    monkeypatch.setattr(filesystem, "_nt_set_information_file", attempt_replacement)
    with filesystem.open_verified_directory_root(parent, "rename parent") as verified_parent:
        filesystem.rename_entry_without_overwrite(source, destination, identity, verified_parent=verified_parent)

    assert attempts
    assert destination.read_text(encoding="utf-8") == "data"
    assert not replacement.exists()
