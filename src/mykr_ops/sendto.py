from __future__ import annotations

import base64
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import sys

from .filesystem import FilesystemSafetyError, assert_ordinary_directory, is_ordinary_file


SHORTCUT_NAME = "MyKr-ops Rename.lnk"
LEGACY_SHORTCUT_DESCRIPTION = "Managed by MyKr-ops Rename Send To integration v1"
LEGACY_SHORTCUT_ARGUMENTS = "-m mykr_ops rename gui"
SHORTCUT_DESCRIPTION = "Managed by MyKr-ops Rename Send To integration v2"
SHORTCUT_ARGUMENTS = ""
LAUNCHER_NAME = "mykr-ops-rename.exe"


class SendToError(RuntimeError):
    """Raised when the per-user Send To entry cannot be changed safely."""


if os.name == "nt":
    class _Guid(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", ctypes.c_ubyte * 8),
        ]


    _FOLDERID_SENDTO = _Guid(
        0x8983036C, 0x27C0, 0x404B, (ctypes.c_ubyte * 8)(0x8F, 0x08, 0x10, 0x2D, 0x10, 0xDC, 0xFD, 0x74)
    )
    _shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    _sh_get_known_folder_path = _shell32.SHGetKnownFolderPath
    _sh_get_known_folder_path.argtypes = [ctypes.POINTER(_Guid), wintypes.DWORD, wintypes.HANDLE, ctypes.POINTER(wintypes.LPWSTR)]
    _sh_get_known_folder_path.restype = ctypes.c_long
    _ole32 = ctypes.WinDLL("ole32")
    _co_task_mem_free = _ole32.CoTaskMemFree
    _co_task_mem_free.argtypes = [wintypes.LPVOID]


def sendto_directory() -> Path:
    if os.name != "nt":
        raise SendToError("Send To integration is available only on Windows")
    raw_path = wintypes.LPWSTR()
    result = _sh_get_known_folder_path(ctypes.byref(_FOLDERID_SENDTO), 0, None, ctypes.byref(raw_path))
    if result != 0 or not raw_path.value:
        raise SendToError(f"could not locate the Windows SendTo folder (HRESULT 0x{result & 0xFFFFFFFF:08X})")
    try:
        directory = Path(raw_path.value)
    finally:
        _co_task_mem_free(ctypes.cast(raw_path, wintypes.LPVOID))
    try:
        directory.mkdir(parents=True, exist_ok=True)
        assert_ordinary_directory(directory, "Windows SendTo directory")
    except (OSError, FilesystemSafetyError) as exc:
        raise SendToError(f"Windows SendTo directory is not usable: {exc}") from exc
    return directory


def shortcut_path() -> Path:
    return sendto_directory() / SHORTCUT_NAME


def _pythonw_path() -> Path:
    path = Path(sys.executable).with_name("pythonw.exe")
    if not path.is_file():
        raise SendToError(f"current Python environment has no pythonw.exe: {path}")
    return path


def _launcher_path(*, require_exists: bool = True) -> Path:
    path = Path(sys.executable).with_name(LAUNCHER_NAME)
    if require_exists and not is_ordinary_file(path):
        raise SendToError(
            "MyKr-ops Rename launcher is unavailable. "
            f"Run `{sys.executable} -m pip install -e \".[dev]\"` and try again."
        )
    return path


def _run_powershell(script: str, *arguments: str) -> str:
    payload = base64.b64encode(
        json.dumps({"values": list(arguments)}, ensure_ascii=False).encode("utf-8")
    ).decode("ascii")
    command = (
        f"$mykrPayload = '{payload}'; "
        "$mykrArguments = @((ConvertFrom-Json -InputObject "
        "([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($mykrPayload)))).values); "
        f"{script}"
    )
    encoded_command = base64.b64encode(command.encode("utf-16le")).decode("ascii")
    try:
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded_command],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        raise SendToError(f"could not start PowerShell for Send To integration: {exc}") from exc
    if completed.returncode != 0:
        raise SendToError(f"PowerShell could not manage the Send To shortcut: {completed.stderr.strip()}")
    return completed.stdout.strip()


def _shortcut_properties(path: Path) -> dict[str, str]:
    if not is_ordinary_file(path):
        raise SendToError(f"Send To entry is not an ordinary shortcut file: {path}")
    script = (
        "$shell = New-Object -ComObject WScript.Shell; "
        "$shortcut = $shell.CreateShortcut($mykrArguments[0]); "
        "[pscustomobject]@{target=$shortcut.TargetPath;arguments=$shortcut.Arguments;description=$shortcut.Description} "
        "| ConvertTo-Json -Compress"
    )
    try:
        data = json.loads(_run_powershell(script, str(path)))
    except (json.JSONDecodeError, SendToError) as exc:
        raise SendToError(f"could not inspect Send To shortcut ownership: {exc}") from exc
    return {key: str(data.get(key, "")) for key in ("target", "arguments", "description")}


def _same_target(actual: str, expected: Path) -> bool:
    try:
        return Path(actual).resolve(strict=True) == expected.resolve(strict=True)
    except OSError:
        return False


def _is_owned_shortcut(
    path: Path, launcher: Path | None = None, pythonw: Path | None = None
) -> bool:
    properties = _shortcut_properties(path)
    if (
        properties["arguments"] == LEGACY_SHORTCUT_ARGUMENTS
        and properties["description"] == LEGACY_SHORTCUT_DESCRIPTION
    ):
        return _same_target(properties["target"], pythonw or _pythonw_path())
    if properties["arguments"] == SHORTCUT_ARGUMENTS and properties["description"] == SHORTCUT_DESCRIPTION:
        return _same_target(properties["target"], launcher or _launcher_path(require_exists=False))
    return False


def install_sendto() -> Path:
    path = shortcut_path()
    launcher = _launcher_path()
    if path_exists(path):
        if not _is_owned_shortcut(path, launcher):
            raise SendToError(f"refusing to overwrite a Send To entry not owned by MyKr-ops: {path}")
    script = (
        "$shell = New-Object -ComObject WScript.Shell; "
        "$shortcut = $shell.CreateShortcut($mykrArguments[0]); "
        "$shortcut.TargetPath = $mykrArguments[1]; "
        "$shortcut.Arguments = $mykrArguments[2]; "
        "$shortcut.Description = $mykrArguments[3]; "
        "$shortcut.Save()"
    )
    _run_powershell(script, str(path), str(launcher), SHORTCUT_ARGUMENTS, SHORTCUT_DESCRIPTION)
    if not _is_owned_shortcut(path, launcher):
        raise SendToError("created Send To shortcut could not be verified as MyKr-ops owned")
    return path


def uninstall_sendto() -> bool:
    path = shortcut_path()
    if not path_exists(path):
        return False
    if not _is_owned_shortcut(path):
        raise SendToError(f"refusing to delete a Send To entry not owned by MyKr-ops: {path}")
    try:
        path.unlink()
    except OSError as exc:
        raise SendToError(f"could not remove MyKr-ops Send To entry: {exc}") from exc
    return True


def path_exists(path: Path) -> bool:
    try:
        return os.path.lexists(path)
    except OSError:
        return False
