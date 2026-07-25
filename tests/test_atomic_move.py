from __future__ import annotations

import ctypes
import os
from pathlib import Path

import pytest

from mykr_ops import filesystem


def move_with_snapshot(source: Path, destination: Path) -> None:
    metadata = source.lstat()
    filesystem.move_file_without_overwrite(
        source,
        destination,
        filesystem.sha256_file(source),
        expected_size=metadata.st_size,
        expected_mtime_ns=metadata.st_mtime_ns,
    )


def test_move_preserves_existing_destination_and_source(tmp_path: Path) -> None:
    source = tmp_path / "source.md"
    destination = tmp_path / "destination.md"
    source.write_text("source", encoding="utf-8")
    destination.write_text("existing", encoding="utf-8")

    with pytest.raises(filesystem.FilesystemSafetyError, match="destination already exists"):
        move_with_snapshot(source, destination)

    assert source.read_text(encoding="utf-8") == "source"
    assert destination.read_text(encoding="utf-8") == "existing"


def test_move_rejects_a_source_changed_after_planning(tmp_path: Path) -> None:
    source = tmp_path / "source.md"
    destination = tmp_path / "destination.md"
    source.write_text("before", encoding="utf-8")
    metadata = source.lstat()
    digest = filesystem.sha256_file(source)
    source.write_text("after", encoding="utf-8")

    with pytest.raises(filesystem.FilesystemSafetyError, match="source (changed|content changed)"):
        filesystem.move_file_without_overwrite(
            source,
            destination,
            digest,
            expected_size=metadata.st_size,
            expected_mtime_ns=metadata.st_mtime_ns,
        )

    assert source.read_text(encoding="utf-8") == "after"
    assert not destination.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows handle-level move")
def test_windows_move_uses_handle_path_not_portable_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.md"
    destination = tmp_path / "destination.md"
    source.write_text("note", encoding="utf-8")

    def portable_fallback_must_not_run(*_: object) -> None:
        raise AssertionError("Windows move used the portable fallback")

    monkeypatch.setattr(filesystem, "_move_file_portably", portable_fallback_must_not_run)
    move_with_snapshot(source, destination)

    assert not source.exists()
    assert destination.read_text(encoding="utf-8") == "note"


@pytest.mark.skipif(os.name != "nt", reason="Windows native handle-relative rename")
def test_windows_move_uses_verified_parent_handle_and_relative_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_parent = tmp_path / "source"
    destination_parent = tmp_path / "destination"
    source_parent.mkdir()
    destination_parent.mkdir()
    source = source_parent / "source.md"
    destination = destination_parent / "destination.md"
    source.write_text("note", encoding="utf-8")
    parent_handles: list[int] = []
    actual_open_directory = filesystem._open_directory_handle
    actual_rename = filesystem._nt_set_information_file

    def record_parent_handle(path: Path) -> int:
        handle = actual_open_directory(path)
        parent_handles.append(handle)
        return handle

    def inspect_rename(
        source_handle: int,
        io_status: object,
        rename_buffer: object,
        buffer_size: int,
        information_class: int,
    ) -> int:
        rename_info = ctypes.cast(
            rename_buffer, ctypes.POINTER(filesystem._FileRenameInformation)
        ).contents
        encoded_name = ctypes.string_at(
            ctypes.addressof(rename_buffer) + filesystem._FileRenameInformation.FileName.offset,
            rename_info.FileNameLength,
        )
        assert information_class == filesystem._FILE_RENAME_INFORMATION_CLASS
        assert parent_handles == [int(rename_info.RootDirectory)]
        assert not rename_info.ReplaceIfExists
        assert encoded_name.decode("utf-16-le") == destination.name
        assert Path(encoded_name.decode("utf-16-le")).name == destination.name
        return actual_rename(source_handle, io_status, rename_buffer, buffer_size, information_class)

    monkeypatch.setattr(filesystem, "_open_directory_handle", record_parent_handle)
    monkeypatch.setattr(filesystem, "_nt_set_information_file", inspect_rename)
    before_handle = filesystem._open_read_handle(source)
    try:
        before_identity = filesystem._handle_identity(before_handle, "source before rename")
    finally:
        filesystem._close_handle(before_handle)

    move_with_snapshot(source, destination)

    after_handle = filesystem._open_read_handle(destination)
    try:
        after_identity = filesystem._handle_identity(after_handle, "destination after rename")
    finally:
        filesystem._close_handle(after_handle)
    assert before_identity[:2] == after_identity[:2]


@pytest.mark.skipif(os.name != "nt", reason="Windows native handle-relative rename")
def test_windows_destination_parent_cannot_be_replaced_during_native_rename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.md"
    destination_parent = tmp_path / "destination"
    replacement_parent = tmp_path / "replacement"
    destination_parent.mkdir()
    source.write_text("note", encoding="utf-8")
    destination = destination_parent / "destination.md"
    attempts: list[OSError] = []
    actual_rename = filesystem._nt_set_information_file

    def attempt_parent_replacement(
        source_handle: int,
        io_status: object,
        rename_buffer: object,
        buffer_size: int,
        information_class: int,
    ) -> int:
        try:
            destination_parent.rename(replacement_parent)
        except OSError as exc:
            attempts.append(exc)
        return actual_rename(source_handle, io_status, rename_buffer, buffer_size, information_class)

    monkeypatch.setattr(filesystem, "_nt_set_information_file", attempt_parent_replacement)

    move_with_snapshot(source, destination)

    assert attempts
    assert destination.read_text(encoding="utf-8") == "note"
    assert not replacement_parent.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows native handle-relative rename")
def test_windows_native_rename_ntstatus_failure_keeps_source_and_maps_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.md"
    destination = tmp_path / "destination.md"
    source.write_text("note", encoding="utf-8")

    def unsupported_native_api(*_: object) -> int:
        return ctypes.c_long(0xC00000BB).value

    monkeypatch.setattr(filesystem, "_nt_set_information_file", unsupported_native_api)

    with pytest.raises(filesystem.FilesystemSafetyError, match="NTSTATUS 0xC00000BB") as exc_info:
        move_with_snapshot(source, destination)

    assert exc_info.value.error_type == "unsupported_filesystem"
    assert source.read_text(encoding="utf-8") == "note"
    assert not destination.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows native NTSTATUS mapping")
@pytest.mark.parametrize(
    ("status", "error_type"),
    [
        (0xC0000035, "destination_exists"),
        (0xC00000D4, "cross_volume"),
        (0xC0000043, "destination_locked"),
        (0xC00000BB, "unsupported_filesystem"),
    ],
)
def test_windows_native_ntstatus_mapping_is_stable(status: int, error_type: str) -> None:
    with pytest.raises(filesystem.FilesystemSafetyError, match=f"NTSTATUS 0x{status:08X}") as exc_info:
        filesystem._raise_ntstatus_error("test native failure", ctypes.c_long(status).value)

    assert exc_info.value.error_type == error_type


@pytest.mark.skipif(os.name != "nt", reason="Windows native handle-relative rename")
def test_windows_move_rejects_cross_volume_before_native_rename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.md"
    destination = tmp_path / "destination.md"
    source.write_text("note", encoding="utf-8")
    actual_identity = filesystem._handle_identity

    def report_different_volume(handle: int, description: str) -> tuple[int, int, int, int, int]:
        identity = actual_identity(handle, description)
        if description.startswith("destination directory"):
            return (identity[0] + 1, *identity[1:])
        return identity

    monkeypatch.setattr(filesystem, "_handle_identity", report_different_volume)

    with pytest.raises(filesystem.FilesystemSafetyError, match="different volumes") as exc_info:
        move_with_snapshot(source, destination)

    assert exc_info.value.error_type == "cross_volume"
    assert source.read_text(encoding="utf-8") == "note"
    assert not destination.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows handle-level move")
def test_windows_move_refuses_destination_created_after_precheck(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.md"
    destination = tmp_path / "destination.md"
    source.write_text("source", encoding="utf-8")
    actual_rename = filesystem._rename_handle_without_overwrite

    def create_competing_destination(
        source_handle: int, parent_handle: int, target: Path
    ) -> None:
        target.write_text("competing", encoding="utf-8")
        actual_rename(source_handle, parent_handle, target)

    monkeypatch.setattr(filesystem, "_rename_handle_without_overwrite", create_competing_destination)

    with pytest.raises(filesystem.FilesystemSafetyError, match="destination already exists") as exc_info:
        move_with_snapshot(source, destination)

    assert exc_info.value.error_type == "destination_exists"
    assert source.read_text(encoding="utf-8") == "source"
    assert destination.read_text(encoding="utf-8") == "competing"
