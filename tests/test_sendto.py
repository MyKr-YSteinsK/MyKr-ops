from __future__ import annotations

import base64
import os
from pathlib import Path

import pytest

from mykr_ops import sendto


def test_install_refuses_to_overwrite_unowned_existing_entry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / sendto.SHORTCUT_NAME
    path.write_text("unowned", encoding="utf-8")
    pythonw = tmp_path / "pythonw.exe"
    pythonw.write_text("", encoding="utf-8")
    monkeypatch.setattr(sendto, "shortcut_path", lambda: path)
    monkeypatch.setattr(sendto, "_pythonw_path", lambda: pythonw)
    monkeypatch.setattr(sendto, "_is_owned_shortcut", lambda *_: False)

    with pytest.raises(sendto.SendToError, match="refusing to overwrite"):
        sendto.install_sendto()


def test_install_updates_only_verified_owned_entry_and_verifies_result(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / sendto.SHORTCUT_NAME
    path.write_text("owned", encoding="utf-8")
    pythonw = tmp_path / "pythonw.exe"
    pythonw.write_text("", encoding="utf-8")
    calls: list[tuple[str, tuple[str, ...]]] = []
    monkeypatch.setattr(sendto, "shortcut_path", lambda: path)
    monkeypatch.setattr(sendto, "_pythonw_path", lambda: pythonw)
    monkeypatch.setattr(sendto, "_is_owned_shortcut", lambda *_: True)
    monkeypatch.setattr(sendto, "_run_powershell", lambda script, *args: calls.append((script, args)) or "")

    assert sendto.install_sendto() == path
    assert calls[0][1] == (str(path), str(pythonw), sendto.SHORTCUT_ARGUMENTS, sendto.SHORTCUT_DESCRIPTION)


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


def test_powershell_helper_preserves_shell_special_and_unicode_arguments() -> None:
    selected = "space & % () - \u4e2d\u6587-\u65e5\u672c\u8a9e"
    encoded = sendto._run_powershell(
        "[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($mykrArguments[0]))", selected
    )

    assert base64.b64decode(encoded).decode("utf-8") == selected


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
