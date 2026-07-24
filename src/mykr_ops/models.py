from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path


class PlanStatus(StrEnum):
    READY = "READY"
    DUPLICATE = "DUPLICATE"
    CONFLICT = "CONFLICT"
    INVALID = "INVALID"
    FAILED = "FAILED"


@dataclass(frozen=True)
class NotesConfig:
    source_dir: Path
    study_root: Path


@dataclass(frozen=True)
class ParsedNote:
    original_name: str
    sequence: str
    topic: str
    first_level: str
    course: str

    @property
    def target_name(self) -> str:
        return f"{self.sequence}-{self.topic}.md"


@dataclass
class PlanItem:
    source: Path
    status: PlanStatus
    parsed: ParsedNote | None = None
    destination: Path | None = None
    reason: str | None = None
    source_size: int | None = None
    source_mtime_ns: int | None = None
    source_sha256: str | None = None
    planned_directories: tuple[Path, ...] = ()


@dataclass
class PlanResult:
    items: list[PlanItem] = field(default_factory=list)
    ignored_count: int = 0

    @property
    def matched_count(self) -> int:
        return len(self.items)

    def count(self, status: PlanStatus) -> int:
        return sum(item.status == status for item in self.items)


@dataclass
class RunResult:
    run_id: int | None
    items: list[PlanItem]
    matched_count: int
    moved_count: int
    duplicate_count: int
    conflict_count: int
    invalid_count: int
    failed_count: int
    created_dir_count: int
    ignored_count: int = 0


@dataclass
class UndoItem:
    source: Path | None
    destination: Path | None
    status: str
    reason: str | None = None


@dataclass
class UndoResult:
    run_id: int | None
    apply_run_id: int | None
    items: list[UndoItem]
    moved_count: int
    failed_count: int
    removed_dir_count: int
    message: str | None = None
