from __future__ import annotations

import base64
import os
from pathlib import Path

import pytest

from mykr_ops import rename_launcher, sendto


def test_install_refuses_to_overwrite_unowned_existing_entry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / sendto.SHORTCUT_NAME
    path.write_text("unowned", encoding="utf-8")
    launcher = tmp_path / sendto.LAUNCHER_NAME
    launcher.write_text("", encoding="utf-8")
    monkeypatch.setattr(sendto, "shortcut_path", lambda: path)
    monkeypatch.setattr(sendto, "_launcher_path", lambda: launcher)
    monkeypatch.setattr(sendto, "_is_owned_shortcut", lambda *_: False)

    with pytest.raises(sendto.SendToError, match="refusing to overwrite"):
        sendto.install_sendto()


def test_launcher_path_requires_an_editable_install_when_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sendto.sys, "executable", str(tmp_path / "python.exe"))

    with pytest.raises(sendto.SendToError, match="pip install -e"):
        sendto._launcher_path()


def test_install_updates_only_verified_owned_entry_and_verifies_result(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / sendto.SHORTCUT_NAME
    path.write_text("owned", encoding="utf-8")
    launcher = tmp_path / sendto.LAUNCHER_NAME
    launcher.write_text("", encoding="utf-8")
    calls: list[tuple[str, tuple[str, ...]]] = []
    monkeypatch.setattr(sendto, "shortcut_path", lambda: path)
    monkeypatch.setattr(sendto, "_launcher_path", lambda: launcher)
    monkeypatch.setattr(sendto, "_is_owned_shortcut", lambda *_: True)
    monkeypatch.setattr(sendto, "_run_powershell", lambda script, *args: calls.append((script, args)) or "")

    assert sendto.install_sendto() == path
    assert calls[0][1] == (str(path), str(launcher), "", sendto.SHORTCUT_DESCRIPTION)


def test_v1_owned_shortcut_is_upgraded_to_v2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / sendto.SHORTCUT_NAME
    path.write_text("owned", encoding="utf-8")
    pythonw = tmp_path / "pythonw.exe"
    launcher = tmp_path / sendto.LAUNCHER_NAME
    pythonw.write_text("", encoding="utf-8")
    launcher.write_text("", encoding="utf-8")
    properties = {
        "target": str(pythonw),
        "arguments": sendto.LEGACY_SHORTCUT_ARGUMENTS,
        "description": sendto.LEGACY_SHORTCUT_DESCRIPTION,
    }
    calls: list[tuple[str, tuple[str, ...]]] = []
    monkeypatch.setattr(sendto, "shortcut_path", lambda: path)
    monkeypatch.setattr(sendto, "_launcher_path", lambda: launcher)
    monkeypatch.setattr(sendto, "_pythonw_path", lambda: pythonw)
    monkeypatch.setattr(sendto, "_shortcut_properties", lambda _path: properties)

    def run(script: str, *arguments: str) -> str:
        calls.append((script, arguments))
        properties.update({
            "target": str(launcher),
            "arguments": "",
            "description": sendto.SHORTCUT_DESCRIPTION,
        })
        return ""

    monkeypatch.setattr(sendto, "_run_powershell", run)

    assert sendto.install_sendto() == path
    assert calls[0][1] == (str(path), str(launcher), "", sendto.SHORTCUT_DESCRIPTION)
    assert sendto._is_owned_shortcut(path, launcher)


def test_uninstall_deletes_only_owned_shortcut(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / sendto.SHORTCUT_NAME
    path.write_text("owned", encoding="utf-8")
    monkeypatch.setattr(sendto, "shortcut_path", lambda: path)
    monkeypatch.setattr(sendto, "_is_owned_shortcut", lambda *_: True)

    assert sendto.uninstall_sendto()
    assert not path.exists()

    path.write_text("unowned", encoding="utf-8")
    monkeypatch.setattr(sendto, "_is_owned_shortcut", lambda *_: False)
    with pytest.raises(sendto.SendToError, match="refusing to delete"):
        sendto.uninstall_sendto()
    assert path.exists()


def test_uninstall_deletes_a_v1_owned_shortcut(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / sendto.SHORTCUT_NAME
    pythonw = tmp_path / "pythonw.exe"
    path.write_text("owned", encoding="utf-8")
    pythonw.write_text("", encoding="utf-8")
    monkeypatch.setattr(sendto, "shortcut_path", lambda: path)
    monkeypatch.setattr(sendto, "_pythonw_path", lambda: pythonw)
    monkeypatch.setattr(
        sendto,
        "_shortcut_properties",
        lambda _path: {
            "target": str(pythonw),
            "arguments": sendto.LEGACY_SHORTCUT_ARGUMENTS,
            "description": sendto.LEGACY_SHORTCUT_DESCRIPTION,
        },
    )

    assert sendto.uninstall_sendto()
    assert not path.exists()


def test_powershell_helper_preserves_shell_special_and_unicode_arguments() -> None:
    selected = "space & % () - \u4e2d\u6587-\u65e5\u672c\u8a9e"
    encoded = sendto._run_powershell(
        "[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($mykrArguments[0]))", selected
    )

    assert base64.b64decode(encoded).decode("utf-8") == selected


def test_rename_launcher_rejects_missing_paths(capsys: pytest.CaptureFixture[str]) -> None:
    assert rename_launcher.main([]) == 2
    assert "请选择至少一个" in capsys.readouterr().err


def test_rename_launcher_passes_windows_style_arguments_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_dir = tmp_path / "state"
    selected = [
        str(tmp_path / "space name.txt"),
        str(tmp_path / "中文-日本語 & % ().txt"),
        str(tmp_path / "folder ()"),
    ]
    seen: dict[str, object] = {}
    logger = object()
    monkeypatch.setattr(rename_launcher, "application_data_dir", lambda *, create: state_dir)
    monkeypatch.setattr(rename_launcher, "_configure_logger", lambda _state_dir: logger)

    def launch(paths: list[Path], database: object, logger: object) -> None:
        seen["paths"] = paths
        seen["database"] = database.path  # type: ignore[attr-defined]
        seen["logger"] = logger

    monkeypatch.setattr(rename_launcher, "launch_rename_gui", launch)

    assert rename_launcher.main(selected) == 0
    assert seen == {
        "paths": [Path(value) for value in selected],
        "database": state_dir / "mykr-ops.db",
        "logger": logger,
    }


@pytest.mark.skipif(os.name != "nt", reason="Windows Send To shortcut")
def test_owned_shortcut_can_be_created_verified_and_removed_in_a_temporary_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / sendto.SHORTCUT_NAME
    monkeypatch.setattr(sendto, "shortcut_path", lambda: path)

    assert sendto.install_sendto() == path
    assert sendto._is_owned_shortcut(path)
    assert sendto.uninstall_sendto()
    assert not path.exists()
