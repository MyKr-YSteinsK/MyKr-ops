from __future__ import annotations

import os
import tomllib
from pathlib import Path

from .filesystem import FilesystemSafetyError, assert_ordinary_directory
from .models import NotesConfig


class SettingsError(RuntimeError):
    """Raised for invalid MyKr-ops configuration or application state paths."""


def application_data_dir(*, create: bool) -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    user_profile = os.environ.get("USERPROFILE")
    if local_app_data:
        directory = Path(local_app_data) / "mykr-ops"
    elif user_profile:
        directory = Path(user_profile) / ".mykr-ops"
    else:
        raise SettingsError("LOCALAPPDATA and USERPROFILE are unavailable")

    if create:
        try:
            directory.mkdir(parents=True, exist_ok=True)
            assert_ordinary_directory(directory, "application-data directory")
        except (OSError, FilesystemSafetyError) as exc:
            raise SettingsError(f"application-data directory is not usable: {exc}") from exc
    return directory


def load_notes_config(config_path: Path | None = None) -> NotesConfig:
    source_dir = Path("D:/Downloads")
    study_root = Path("D:/Study")
    if config_path is None:
        candidate = application_data_dir(create=False) / "config.toml"
    else:
        candidate = config_path
    if not candidate.exists():
        return NotesConfig(source_dir=source_dir, study_root=study_root)
    try:
        data = tomllib.loads(candidate.read_text(encoding="utf-8"))
        notes = data.get("notes", {})
        if not isinstance(notes, dict):
            raise ValueError("[notes] must be a table")
        configured_source = notes.get("source_dir", str(source_dir))
        configured_study = notes.get("study_root", str(study_root))
        if not isinstance(configured_source, str) or not isinstance(configured_study, str):
            raise ValueError("notes paths must be strings")
    except (OSError, tomllib.TOMLDecodeError, ValueError) as exc:
        raise SettingsError(f"could not read configuration {candidate}: {exc}") from exc
    return NotesConfig(source_dir=Path(configured_source), study_root=Path(configured_study))
