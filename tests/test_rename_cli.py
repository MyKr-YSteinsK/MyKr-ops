from __future__ import annotations

from pathlib import Path
import runpy
import subprocess
import sys

import pytest

from mykr_ops import cli, rename_gui
from mykr_ops.database import Database
from mykr_ops.filesystem import entry_identity


def test_module_execution_exposes_the_cli_help() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "mykr_ops", "--help"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert completed.returncode == 0
    assert "MyKr-ops local automation toolkit" in completed.stdout


def test_module_entry_routes_sendto_style_gui_arguments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "draft.txt"
    source.write_text("data", encoding="utf-8")
    state_dir = tmp_path / "state"
    seen: dict[str, object] = {}
    monkeypatch.setattr(cli, "application_data_dir", lambda *, create: state_dir)
    monkeypatch.setattr(cli, "_configure_logger", lambda _state_dir: object())

    def launch(paths: list[Path], database: Database, logger: object) -> None:
        seen["paths"] = paths
        seen["database"] = database.path

    monkeypatch.setattr(rename_gui, "launch_rename_gui", launch)
    # This is the argv seen after Python has consumed the Send To target's
    # `-m mykr_ops` module selector.
    monkeypatch.setattr(sys, "argv", ["mykr_ops", "rename", "gui", str(source)])

    with pytest.raises(SystemExit) as exc_info:
        runpy.run_module("mykr_ops", run_name="__main__")

    assert exc_info.value.code == 0
    assert seen == {"paths": [source], "database": state_dir / "mykr-ops.db"}


def test_rename_gui_requires_explicit_path() -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["rename", "gui"])
    assert exc_info.value.code == 2


def test_history_and_rename_undo_do_not_load_notes_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    state_dir = tmp_path / "state"
    monkeypatch.setattr(cli, "application_data_dir", lambda *, create: state_dir)
    monkeypatch.setattr(cli, "load_notes_config", lambda: (_ for _ in ()).throw(AssertionError("notes config was loaded")))

    assert cli.main(["history"]) == 0
    assert capsys.readouterr().out == "No recorded runs.\n"
    assert cli.main(["rename", "undo"]) == 0
    assert "No eligible rename batch" in capsys.readouterr().out


def test_rename_gui_passes_only_selected_paths_and_uses_state_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "draft.txt"
    source.write_text("data", encoding="utf-8")
    state_dir = tmp_path / "state"
    seen: dict[str, object] = {}
    monkeypatch.setattr(cli, "application_data_dir", lambda *, create: state_dir)
    monkeypatch.setattr(cli, "load_notes_config", lambda: (_ for _ in ()).throw(AssertionError("notes config was loaded")))

    def launch(paths: list[Path], database: Database, logger: object) -> None:
        seen["paths"] = paths
        seen["database"] = database.path

    monkeypatch.setattr(rename_gui, "launch_rename_gui", launch)
    assert cli.main(["rename", "gui", str(source)]) == 0
    assert seen == {"paths": [source], "database": state_dir / "mykr-ops.db"}


def test_history_run_hides_internal_temporary_paths_for_rename(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    state_dir = tmp_path / "state"
    source = tmp_path / "original.txt"
    source.write_text("data", encoding="utf-8")
    database = Database(state_dir / "mykr-ops.db")
    database.initialize()
    identity = entry_identity(source)
    parent = entry_identity(tmp_path)
    run_id = database.create_rename_run(
        "apply",
        [{
            "sequence_index": 1,
            "object_kind": "file",
            "original_path": source,
            "temporary_path": tmp_path / ".mykr-ops-rename-secret",
            "final_path": tmp_path / "renamed.txt",
            "volume_serial": identity.volume_serial,
            "file_index": identity.file_index,
            "parent_volume_serial": parent.volume_serial,
            "parent_file_index": parent.file_index,
        }],
    )
    row = database.rename_items_for_run(run_id)[0]
    database.update_rename_item_state(int(row["id"]), "success")
    database.finish_run(run_id, status="success", summary="renamed=1", matched_count=1, moved_count=1)
    monkeypatch.setattr(cli, "application_data_dir", lambda *, create: state_dir)

    assert cli.main(["history", "--run", str(run_id)]) == 0
    output = capsys.readouterr().out
    assert "RENAME success" in output
    assert ".mykr-ops-rename-secret" not in output
