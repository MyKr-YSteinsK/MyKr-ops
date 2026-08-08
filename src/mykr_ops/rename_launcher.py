from __future__ import annotations

import logging
from pathlib import Path
import sys

from .database import Database
from .rename_gui import launch_rename_gui
from .settings import application_data_dir


def _configure_logger(state_dir: Path) -> logging.Logger:
    state_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("mykr_ops.rename_launcher")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    handler = logging.FileHandler(state_dir / "mykr-ops.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    return logger


def main(argv: list[str] | None = None) -> int:
    paths = [Path(value) for value in (sys.argv[1:] if argv is None else argv)]
    if not paths:
        print("请选择至少一个要重命名的文件或文件夹。", file=sys.stderr)
        return 2
    state_dir = application_data_dir(create=True)
    database = Database(state_dir / "mykr-ops.db")
    logger = _configure_logger(state_dir)
    try:
        launch_rename_gui(paths, database, logger)
    except Exception:
        logger.exception("rename launcher failed")
        return 1
    return 0
