from __future__ import annotations

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

    with pytest.raises(filesystem.FilesystemSafetyError, match="destination already exists"):
        move_with_snapshot(source, destination)

    assert source.read_text(encoding="utf-8") == "source"
    assert destination.read_text(encoding="utf-8") == "competing"
