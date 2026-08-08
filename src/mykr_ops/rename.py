from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import logging
from pathlib import Path
from typing import Iterable
from uuid import uuid4

from .database import Database, MutationBlockedError
from .filesystem import (
    EntryIdentity,
    FilesystemSafetyError,
    VerifiedDirectoryRoot,
    assert_ordinary_directory,
    direct_casefold_matches,
    entry_identity,
    is_ordinary_directory,
    is_ordinary_file,
    names_equal,
    open_verified_directory_root,
    path_exists_no_follow,
    rename_entry_without_overwrite,
)


class RenameError(RuntimeError):
    """Raised when a rename batch cannot be planned or executed safely."""


class RenameValidationError(RenameError):
    """Raised for an invalid Rename GUI input or selected path set."""


class RenameRecoveryRequired(RenameError):
    """Raised when a durable rename state cannot be reconciled safely."""


class RenameItemStatus(StrEnum):
    CHANGED = "CHANGED"
    UNCHANGED = "UNCHANGED"
    INVALID = "INVALID"
    CONFLICT = "CONFLICT"
    FAILED = "FAILED"


class RenameMode(StrEnum):
    TRANSFORM = "transform"
    NUMBERING = "numbering"


@dataclass
class RenameRules:
    mode: RenameMode = RenameMode.TRANSFORM
    find: str = ""
    replace: str = ""
    prefix: str = ""
    suffix: str = ""
    numbering_start: int = 1
    numbering_step: int = 1
    numbering_width: int = 2
    numbering_prefix: str = ""
    numbering_suffix: str = ""


@dataclass(frozen=True)
class RenameSource:
    path: Path
    original_name: str
    object_kind: str
    identity: EntryIdentity


@dataclass
class RenameItem:
    source: RenameSource
    extension: str
    automatic_stem: str = ""
    manual_stem: str | None = None
    final_name: str = ""
    status: RenameItemStatus = RenameItemStatus.UNCHANGED
    reason: str | None = None

    @property
    def editable_stem(self) -> str:
        if self.manual_stem is not None:
            return self.manual_stem
        return self.automatic_stem

    @property
    def is_manual(self) -> bool:
        return self.manual_stem is not None

    @property
    def final_path(self) -> Path:
        return self.source.path.parent / self.final_name


@dataclass
class RenamePlan:
    parent: Path
    parent_identity: EntryIdentity
    items: list[RenameItem]
    rules: RenameRules = field(default_factory=RenameRules)
    initial_order: tuple[Path, ...] = ()

    @property
    def changed_items(self) -> list[RenameItem]:
        return [item for item in self.items if item.status == RenameItemStatus.CHANGED]

    @property
    def has_blocking_items(self) -> bool:
        return any(item.status in {RenameItemStatus.INVALID, RenameItemStatus.CONFLICT, RenameItemStatus.FAILED} for item in self.items)

    @property
    def can_apply(self) -> bool:
        return bool(self.changed_items) and not self.has_blocking_items

    def set_manual_stem(self, index: int, value: str) -> None:
        self.items[index].manual_stem = value
        self.recompute()

    def restore_automatic(self, index: int) -> None:
        self.items[index].manual_stem = None
        self.recompute()

    def clear_manual_overrides(self) -> None:
        for item in self.items:
            item.manual_stem = None
        self.recompute()

    def move_item(self, source_index: int, destination_index: int) -> None:
        item = self.items.pop(source_index)
        self.items.insert(destination_index, item)
        self.recompute()

    def restore_initial_order(self) -> None:
        positions = {path: index for index, path in enumerate(self.initial_order)}
        self.items.sort(key=lambda item: positions[item.source.path])
        self.recompute()

    def sort_by_name(self, descending: bool = False) -> None:
        self.items.sort(key=lambda item: item.source.original_name.casefold(), reverse=descending)
        self.recompute()

    def recompute(self) -> None:
        _validate_rules(self.rules)
        for index, item in enumerate(self.items):
            item.reason = None
            item.automatic_stem = _automatic_stem(item, self.rules, index)
            stem = item.editable_stem
            item.final_name = stem + item.extension
            try:
                validate_windows_component(item.final_name)
            except RenameValidationError as exc:
                item.status = RenameItemStatus.INVALID
                item.reason = str(exc)
            else:
                item.status = (
                    RenameItemStatus.UNCHANGED
                    if item.final_name == item.source.original_name
                    else RenameItemStatus.CHANGED
                )
        _classify_plan_conflicts(self)


@dataclass(frozen=True)
class RenameResult:
    run_id: int | None
    renamed_count: int
    unchanged_count: int
    failed: bool
    message: str
    recovery_item_id: int | None = None


def validate_windows_component(value: str) -> None:
    if not value:
        raise RenameValidationError("name is empty")
    if value in {".", ".."}:
        raise RenameValidationError("name cannot be . or ..")
    if len(value) > 255:
        raise RenameValidationError("name is longer than the Windows component limit")
    if any(ord(character) < 32 for character in value):
        raise RenameValidationError("name contains a Windows control character")
    if any(character in '<>:"/\\|?*' for character in value):
        raise RenameValidationError("name contains an invalid Windows filename character")
    if value.endswith((" ", ".")):
        raise RenameValidationError("name must not end in a space or period")
    device_name = value.split(".", 1)[0].upper()
    reserved = {
        "CON", "PRN", "AUX", "NUL",
        *(f"COM{number}" for number in range(1, 10)),
        *(f"LPT{number}" for number in range(1, 10)),
        *(f"COM{suffix}" for suffix in ("\N{SUPERSCRIPT ONE}", "\N{SUPERSCRIPT TWO}", "\N{SUPERSCRIPT THREE}")),
        *(f"LPT{suffix}" for suffix in ("\N{SUPERSCRIPT ONE}", "\N{SUPERSCRIPT TWO}", "\N{SUPERSCRIPT THREE}")),
    }
    if device_name in reserved:
        raise RenameValidationError("name is a reserved Windows device name")


def build_rename_plan(paths: Iterable[Path], rules: RenameRules | None = None) -> RenamePlan:
    selected_paths = [Path(path) for path in paths]
    if not selected_paths:
        raise RenameValidationError("select at least one file or folder to rename")
    sources: list[RenameSource] = []
    parent: Path | None = None
    parent_identity: EntryIdentity | None = None
    identities: set[EntryIdentity] = set()
    names: list[str] = []
    for candidate in selected_paths:
        if is_ordinary_file(candidate):
            object_kind = "file"
        elif is_ordinary_directory(candidate):
            object_kind = "directory"
        else:
            raise RenameValidationError(f"selected path is not an ordinary file or directory: {candidate}")
        try:
            actual_parent = candidate.parent.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise RenameValidationError(f"could not resolve selected parent {candidate.parent}: {exc}") from exc
        assert_ordinary_directory(actual_parent, "selected parent directory")
        current_parent_identity = entry_identity(actual_parent)
        if parent is None:
            parent = actual_parent
            parent_identity = current_parent_identity
        elif current_parent_identity != parent_identity:
            raise RenameValidationError("all selected items must belong to one ordinary parent directory")
        identity = entry_identity(candidate)
        if identity in identities:
            raise RenameValidationError(f"the same filesystem object was selected more than once: {candidate}")
        if any(names_equal(candidate.name, name) for name in names):
            raise RenameValidationError(f"selected names are ambiguous on Windows: {candidate.name}")
        identities.add(identity)
        names.append(candidate.name)
        sources.append(RenameSource(actual_parent / candidate.name, candidate.name, object_kind, identity))
    assert parent is not None and parent_identity is not None
    items = [RenameItem(source, _extension_for(source)) for source in sources]
    plan = RenamePlan(parent, parent_identity, items, rules or RenameRules(), tuple(source.path for source in sources))
    plan.recompute()
    return plan


def _extension_for(source: RenameSource) -> str:
    return source.path.suffix if source.object_kind == "file" else ""


def _validate_rules(rules: RenameRules) -> None:
    try:
        RenameMode(rules.mode)
    except (TypeError, ValueError) as exc:
        raise RenameValidationError("rename mode is invalid") from exc
    if rules.numbering_step == 0:
        raise RenameValidationError("numbering step cannot be zero")
    if rules.numbering_width < 1:
        raise RenameValidationError("numbering width must be at least one")


def _automatic_stem(item: RenameItem, rules: RenameRules, index: int) -> str:
    if RenameMode(rules.mode) == RenameMode.NUMBERING:
        value = rules.numbering_start + index * rules.numbering_step
        return f"{rules.numbering_prefix}{str(value).zfill(rules.numbering_width)}{rules.numbering_suffix}"
    original_stem = item.source.original_name[:-len(item.extension)] if item.extension else item.source.original_name
    stem = original_stem.replace(rules.find, rules.replace) if rules.find else original_stem
    return f"{rules.prefix}{stem}{rules.suffix}"


def _classify_plan_conflicts(plan: RenamePlan) -> None:
    candidate_items = [item for item in plan.items if item.status in {RenameItemStatus.CHANGED, RenameItemStatus.UNCHANGED}]
    groups: list[list[RenameItem]] = []
    for item in candidate_items:
        for group in groups:
            if names_equal(item.final_name, group[0].final_name):
                group.append(item)
                break
        else:
            groups.append([item])
    for group in groups:
        if len(group) > 1:
            for item in group:
                item.status = RenameItemStatus.CONFLICT
                item.reason = "multiple selected items resolve to the same target name"

    selected_by_identity = {item.source.identity: item for item in plan.items}
    for item in candidate_items:
        if item.status != RenameItemStatus.CHANGED:
            continue
        try:
            matches = direct_casefold_matches(plan.parent, item.final_name)
        except FilesystemSafetyError as exc:
            item.status = RenameItemStatus.FAILED
            item.reason = str(exc)
            continue
        if len(matches) > 1:
            item.status = RenameItemStatus.FAILED
            item.reason = "multiple case-insensitive target matches exist"
            continue
        if not matches:
            continue
        try:
            target_identity = entry_identity(matches[0])
        except FilesystemSafetyError as exc:
            item.status = RenameItemStatus.FAILED
            item.reason = str(exc)
            continue
        if target_identity not in selected_by_identity:
            item.status = RenameItemStatus.CONFLICT
            item.reason = "target name is occupied by an unselected filesystem object"


def _authoritative_changed_items(plan: RenamePlan) -> list[RenameItem]:
    plan.recompute()
    if plan.has_blocking_items:
        raise RenameValidationError("rename plan contains invalid, conflicting, or failed items")
    current_parent_identity = entry_identity(plan.parent)
    if current_parent_identity != plan.parent_identity:
        raise RenameError("rename parent identity changed after preview")
    for item in plan.items:
        matches = direct_casefold_matches(plan.parent, item.source.original_name)
        if len(matches) != 1 or matches[0].name != item.source.original_name:
            raise RenameError(f"rename source is no longer an exact direct child: {item.source.path}")
        if entry_identity(matches[0]) != item.source.identity:
            raise RenameError(f"rename source identity changed after preview: {item.source.path}")
    return plan.changed_items


def _unique_temporary_paths(plan: RenamePlan, items: list[RenameItem], marker: str) -> dict[Path, Path]:
    token = uuid4().hex
    excluded = {item.source.original_name for item in plan.items} | {item.final_name for item in plan.items}
    result: dict[Path, Path] = {}
    for index, item in enumerate(items, start=1):
        counter = index
        while True:
            name = f".{marker}-{token}-{counter}"
            candidate = plan.parent / name
            if (
                name not in excluded
                and not any(names_equal(name, other.name) for other in result.values())
                and not direct_casefold_matches(plan.parent, name)
            ):
                result[item.source.path] = candidate
                break
            counter += 1
    return result


def _item_identity(row: object) -> EntryIdentity:
    return EntryIdentity(str(row["object_kind"]), int(row["volume_serial"]), int(row["file_index"]))


def _row_path(row: object, field: str) -> Path:
    value = row[field]
    if not value:
        raise RenameRecoveryRequired(f"rename item {row['id']} is missing {field}")
    return Path(value)


def inspect_rename_batch_locations(
    rows: list[object], parent_path: Path, verified_parent: VerifiedDirectoryRoot | None = None
) -> dict[int, Path]:
    """Map every recorded object to its one current path within a rename batch.

    A swap or cycle can legitimately put one batch object at another item's
    original or final path. Only an object outside the durable batch, a missing
    object, or one object at multiple distinct paths is ambiguous.
    """
    _assert_recovery_parent(rows, parent_path, verified_parent)
    identities: dict[EntryIdentity, object] = {}
    candidate_names: list[str] = []
    for row in rows:
        identity = _item_identity(row)
        if identity in identities:
            raise RenameRecoveryRequired("rename run records the same object more than once")
        identities[identity] = row
        for field in ("original_path", "temporary_path", "final_path", "rollback_path"):
            value = row[field]
            if value is None:
                continue
            path = Path(value)
            if path.parent != parent_path:
                raise RenameRecoveryRequired("rename run has a location outside its recorded parent directory")
            if not any(names_equal(path.name, name) for name in candidate_names):
                candidate_names.append(path.name)

    locations: dict[EntryIdentity, Path] = {}
    for name in candidate_names:
        try:
            matches = direct_casefold_matches(parent_path, name)
        except FilesystemSafetyError as exc:
            raise RenameRecoveryRequired(f"could not inspect rename batch location {name}: {exc}") from exc
        if len(matches) > 1:
            raise RenameRecoveryRequired(f"rename batch location is case-insensitively ambiguous: {name}")
        if not matches:
            continue
        path = matches[0]
        try:
            identity = entry_identity(path)
        except FilesystemSafetyError as exc:
            raise RenameRecoveryRequired(f"could not inspect rename batch location {path}: {exc}") from exc
        if identity not in identities:
            raise RenameRecoveryRequired(f"rename batch location is occupied by an unrelated object: {path}")
        previous = locations.get(identity)
        if previous is not None and not names_equal(previous.name, path.name):
            raise RenameRecoveryRequired(
                f"rename item {identities[identity]['id']} has multiple filesystem locations (ambiguous)"
            )
        locations[identity] = path

    missing = [row for identity, row in identities.items() if identity not in locations]
    if missing:
        raise RenameRecoveryRequired(f"rename item {missing[0]['id']} is missing from every recorded location")
    return {int(row["id"]): locations[_item_identity(row)] for row in rows}


def _rename_row(row: object, source: Path, destination: Path, parent: VerifiedDirectoryRoot | None) -> None:
    rename_entry_without_overwrite(source, destination, _item_identity(row), verified_parent=parent)


def _mark_recovery_required(database: Database, rows: Iterable[object], reason: str) -> int | None:
    first_id: int | None = None
    for row in rows:
        if row["state"] in {"rolled_back", "failed"}:
            continue
        try:
            database.update_rename_item_state(int(row["id"]), "recovery_required", reason, "recovery_ambiguous")
        except Exception:
            continue
        if first_id is None:
            first_id = int(row["id"])
    return first_id


def _rollback_rows(database: Database, rows: list[object], parent: VerifiedDirectoryRoot | None) -> None:
    parent_path = Path(rows[0]["original_path"]).parent
    _assert_recovery_parent(rows, parent_path, parent)
    current_locations = inspect_rename_batch_locations(rows, parent_path, parent)
    current = [(row, current_locations[int(row["id"])]) for row in rows]
    rollback_paths = _unique_rollback_paths(parent_path, current)
    for row, path in current:
        rollback_path = rollback_paths[int(row["id"])]
        database.update_rename_item_state(
            int(row["id"]), "rollback_stage_prepared", rollback_path=rollback_path
        )
        _rename_row(row, path, rollback_path, parent)
        database.update_rename_item_state(int(row["id"]), "rollback_staged")
    for row, _ in current:
        rollback_path = rollback_paths[int(row["id"])]
        database.update_rename_item_state(int(row["id"]), "rollback_finalize_prepared")
        _rename_row(row, rollback_path, _row_path(row, "original_path"), parent)
        database.update_rename_item_state(int(row["id"]), "rolled_back", "batch rollback completed")
    restored = inspect_rename_batch_locations(
        database.rename_items_for_run(int(rows[0]["run_id"])), parent_path, parent
    )
    for row in rows:
        location = restored[int(row["id"])]
        original = _row_path(row, "original_path")
        if location.name != original.name:
            raise RenameRecoveryRequired(f"rename rollback did not restore item {row['id']} to its original name")


def _unique_rollback_paths(parent: Path, current: list[tuple[object, Path]]) -> dict[int, Path]:
    token = uuid4().hex
    result: dict[int, Path] = {}
    for index, (row, _) in enumerate(current, start=1):
        counter = index
        while True:
            candidate = parent / f".mykr-ops-rollback-{token}-{counter}"
            if not any(names_equal(candidate.name, path.name) for path in result.values()) and not direct_casefold_matches(parent, candidate.name):
                result[int(row["id"])] = candidate
                break
            counter += 1
    return result


def _assert_recovery_parent(
    rows: list[object], parent_path: Path, verified_parent: VerifiedDirectoryRoot | None
) -> None:
    """Prove all durable items still refer to the same verified parent object."""
    if not rows:
        raise RenameRecoveryRequired("rename run has no durable items")
    expected_volume = int(rows[0]["parent_volume_serial"])
    expected_file = int(rows[0]["parent_file_index"])
    for row in rows:
        for field in ("original_path", "temporary_path", "final_path", "rollback_path"):
            if row[field] is not None and Path(row[field]).parent != parent_path:
                raise RenameRecoveryRequired("rename run has entries from different parent directories")
        if (
            int(row["parent_volume_serial"]) != expected_volume
            or int(row["parent_file_index"]) != expected_file
        ):
            raise RenameRecoveryRequired("rename run has inconsistent parent identities")
    if verified_parent is not None:
        if (
            verified_parent.identity[0] != expected_volume
            or verified_parent.identity[1] != expected_file
        ):
            raise RenameRecoveryRequired("verified rename parent handle does not match the durable parent identity")
        return
    parent_identity = entry_identity(parent_path)
    if (
        parent_identity.kind != "directory"
        or parent_identity.volume_serial != expected_volume
        or parent_identity.file_index != expected_file
    ):
        raise RenameRecoveryRequired("rename parent identity changed after the durable intent was written")


def _finalize_completed_run(database: Database, run: object, rows: list[object]) -> bool:
    """Finish only an all-success run whose final paths still prove the recorded identity."""
    if not rows or any(row["state"] != "success" for row in rows):
        return False
    parent = Path(rows[0]["original_path"]).parent
    locations = inspect_rename_batch_locations(rows, parent)
    for row in rows:
        location = locations[int(row["id"])]
        final = _row_path(row, "final_path")
        if location.name != final.name:
            raise RenameRecoveryRequired(
                f"rename run {run['id']} was marked successful but item {row['id']} is not at its final path"
            )
    if run["mode"] == "undo":
        database.finalize_rename_undo(int(run["id"]))
    database.finish_run(
        int(run["id"]),
        status="success",
        summary=f"recovered completed rename; renamed={len(rows)}",
        matched_count=len(rows),
        moved_count=len(rows),
    )
    return True


def _run_payload(plan: RenamePlan, changed: list[RenameItem], temporary_paths: dict[Path, Path], related: dict[Path, int] | None = None) -> list[dict[str, object]]:
    return [
        {
            "sequence_index": sequence_index,
            "object_kind": item.source.object_kind,
            "original_path": item.source.path,
            "temporary_path": temporary_paths[item.source.path],
            "final_path": item.final_path,
            "volume_serial": item.source.identity.volume_serial,
            "file_index": item.source.identity.file_index,
            "parent_volume_serial": plan.parent_identity.volume_serial,
            "parent_file_index": plan.parent_identity.file_index,
            "related_item_id": related.get(item.source.path) if related else None,
        }
        for sequence_index, item in enumerate(changed, start=1)
    ]


def _execute_plan(
    plan: RenamePlan,
    database: Database,
    *,
    mode: str,
    related: dict[Path, int] | None = None,
    logger: logging.Logger | None = None,
) -> RenameResult:
    changed = _authoritative_changed_items(plan)
    unchanged_count = sum(item.status == RenameItemStatus.UNCHANGED for item in plan.items)
    if not changed:
        return RenameResult(None, 0, unchanged_count, False, "No selected names need to change.")
    temporary_paths = _unique_temporary_paths(plan, changed, "mykr-ops-rename")
    run_id = database.create_rename_run(mode, _run_payload(plan, changed, temporary_paths, related))
    rows = database.rename_items_for_run(run_id)
    try:
        with open_verified_directory_root(plan.parent, "rename parent") as parent:
            if parent is not None and (
                parent.identity[0] != plan.parent_identity.volume_serial
                or parent.identity[1] != plan.parent_identity.file_index
            ):
                raise RenameError("rename parent identity changed before execution")
            for row in rows:
                database.update_rename_item_state(int(row["id"]), "stage_prepared")
                _rename_row(row, _row_path(row, "original_path"), _row_path(row, "temporary_path"), parent)
                database.update_rename_item_state(int(row["id"]), "staged")
            for row in rows:
                database.update_rename_item_state(int(row["id"]), "finalize_prepared")
                _rename_row(row, _row_path(row, "temporary_path"), _row_path(row, "final_path"), parent)
                database.update_rename_item_state(int(row["id"]), "success")
        if mode == "undo":
            database.finalize_rename_undo(run_id)
        database.finish_run(
            run_id,
            status="success",
            summary=f"renamed={len(rows)}; unchanged={unchanged_count}",
            matched_count=len(rows),
            moved_count=len(rows),
        )
        if logger:
            logger.info("run_id=%s command=rename mode=%s status=success count=%s", run_id, mode, len(rows))
        return RenameResult(run_id, len(rows), unchanged_count, False, f"Renamed {len(rows)} item(s).")
    except Exception as exc:
        recovery_item_id: int | None = None
        try:
            with open_verified_directory_root(plan.parent, "rename parent") as parent:
                _rollback_rows(database, database.rename_items_for_run(run_id), parent)
        except Exception as rollback_exc:
            recovery_item_id = _mark_recovery_required(
                database, database.rename_items_for_run(run_id), f"rollback could not be verified: {rollback_exc}"
            )
        if recovery_item_id is not None:
            database.finish_run(
                run_id,
                status="failed",
                summary=f"rename failed; manual_recovery_item={recovery_item_id}",
                matched_count=len(rows),
                failed_count=len(rows),
            )
            raise RenameRecoveryRequired(
                f"rename run {run_id} requires manual recovery for item {recovery_item_id}: {exc}"
            ) from exc
        database.finish_run(
            run_id,
            status="failed",
            summary="rename failed and the batch was rolled back",
            matched_count=len(rows),
            failed_count=len(rows),
        )
        if logger:
            logger.error("run_id=%s command=rename mode=%s rolled_back error=%s", run_id, mode, exc)
        return RenameResult(run_id, 0, unchanged_count, True, "Rename failed; the batch was rolled back.")


def recover_rename_operations(database: Database, logger: logging.Logger | None = None) -> None:
    """Roll incomplete rename batches back to their original paths; never continue forward."""
    database.initialize()
    run_ids = {int(run["id"]) for run in database.incomplete_rename_runs()}
    run_ids.update(int(row["run_id"]) for row in database.unresolved_rename_items())
    for run_id in sorted(run_ids):
        run = database.get_run(run_id)
        if run is None:
            raise RenameRecoveryRequired(f"rename recovery is required for missing run {run_id}")
        rows = database.rename_items_for_run(run_id)
        if any(row["state"] == "recovery_required" for row in rows):
            first = next(row for row in rows if row["state"] == "recovery_required")
            raise RenameRecoveryRequired(f"rename recovery is required for run {run_id} item {first['id']}")
        try:
            if _finalize_completed_run(database, run, rows):
                if logger:
                    logger.info("run_id=%s command=rename recovered=completed", run_id)
                continue
        except Exception as exc:
            item_id = _mark_recovery_required(database, rows, str(exc))
            raise RenameRecoveryRequired(
                f"rename recovery is required for run {run_id} item {item_id}: {exc}"
            ) from exc
        parent = Path(rows[0]["original_path"]).parent
        try:
            with open_verified_directory_root(parent, "rename parent") as verified_parent:
                _rollback_rows(database, rows, verified_parent)
        except Exception as exc:
            item_id = _mark_recovery_required(database, database.rename_items_for_run(run_id), str(exc))
            raise RenameRecoveryRequired(
                f"rename recovery is required for run {run_id} item {item_id}: {exc}"
            ) from exc
        database.finish_run(
            run_id,
            status="failed",
            summary="interrupted rename was rolled back during recovery",
            matched_count=len(rows),
            failed_count=len(rows),
        )
        if logger:
            logger.info("run_id=%s command=rename recovered=rolled_back", run_id)


def apply_rename(
    plan: RenamePlan,
    database: Database,
    logger: logging.Logger | None = None,
    *,
    lock_held: bool = False,
) -> RenameResult:
    if not lock_held:
        with database.mutation_lock():
            return apply_rename(plan, database, logger, lock_held=True)
    database.initialize()
    if database.prepared_or_recovery_operations():
        raise MutationBlockedError("notes recovery must be resolved before a rename mutation")
    recover_rename_operations(database, logger)
    return _execute_plan(plan, database, mode="apply", logger=logger)


def _undo_plan(apply_items: list[object]) -> tuple[RenamePlan, dict[Path, int]]:
    source_paths: list[Path] = []
    for row in apply_items:
        final_path = _row_path(row, "final_path")
        if not path_exists_no_follow(final_path) or entry_identity(final_path) != _item_identity(row):
            raise RenameError(f"rename undo source changed or is missing: {final_path}")
        source_paths.append(final_path)
    plan = build_rename_plan(source_paths)
    related: dict[Path, int] = {}
    by_final = {Path(row["final_path"]): row for row in apply_items}
    for index, item in enumerate(plan.items):
        row = by_final[item.source.path]
        original = _row_path(row, "original_path")
        item.manual_stem = original.name[:-len(item.extension)] if item.extension else original.name
        related[item.source.path] = int(row["id"])
    plan.recompute()
    if plan.has_blocking_items:
        raise RenameError("rename undo cannot start because an original name is occupied")
    return plan, related


def undo_latest_rename(
    database: Database,
    logger: logging.Logger | None = None,
    *,
    lock_held: bool = False,
) -> RenameResult:
    if not lock_held:
        with database.mutation_lock():
            return undo_latest_rename(database, logger, lock_held=True)
    database.initialize()
    if database.prepared_or_recovery_operations():
        raise MutationBlockedError("notes recovery must be resolved before a rename mutation")
    recover_rename_operations(database, logger)
    apply_run = database.latest_eligible_rename_apply_run()
    if apply_run is None:
        return RenameResult(None, 0, 0, False, "No eligible rename batch to undo.")
    return undo_rename_run(database, int(apply_run["id"]), logger, lock_held=True)


def undo_rename_run(
    database: Database,
    apply_run_id: int,
    logger: logging.Logger | None = None,
    *,
    lock_held: bool = False,
) -> RenameResult:
    """Undo one exact successful rename apply run; never substitute a newer run."""
    if not lock_held:
        with database.mutation_lock():
            return undo_rename_run(database, apply_run_id, logger, lock_held=True)
    database.initialize()
    if database.prepared_or_recovery_operations():
        raise MutationBlockedError("notes recovery must be resolved before a rename mutation")
    recover_rename_operations(database, logger)
    apply_run = database.get_run(apply_run_id)
    if apply_run is None:
        raise RenameError(f"rename apply run {apply_run_id} does not exist")
    if (
        apply_run["command"] != "rename"
        or apply_run["mode"] != "apply"
        or apply_run["status"] != "success"
    ):
        raise RenameError(f"run {apply_run_id} is not a successful rename apply run")
    recorded_items = database.rename_items_for_run(apply_run_id)
    if not recorded_items or any(item["state"] != "success" or item["undone_at"] is not None for item in recorded_items):
        raise RenameError(f"rename apply run {apply_run_id} is no longer eligible for undo")
    apply_items = database.successful_rename_items_for_run(apply_run_id)
    if len(apply_items) != len(recorded_items):
        raise RenameError(f"rename apply run {apply_run_id} is no longer eligible for undo")
    plan, related = _undo_plan(apply_items)
    result = _execute_plan(plan, database, mode="undo", related=related, logger=logger)
    if not result.failed:
        return RenameResult(result.run_id, result.renamed_count, result.unchanged_count, False, "Rename batch restored.")
    return result
