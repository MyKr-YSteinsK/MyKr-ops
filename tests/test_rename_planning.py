from __future__ import annotations

from pathlib import Path

import pytest

from mykr_ops.rename import (
    RenameItemStatus,
    RenameRules,
    RenameValidationError,
    build_rename_plan,
    validate_windows_component,
)


def test_rules_preview_keeps_file_extensions_and_supports_mixed_items(tmp_path: Path) -> None:
    first = tmp_path / "draft report.txt"
    second = tmp_path / "draft folder"
    first.write_text("x", encoding="utf-8")
    second.mkdir()

    plan = build_rename_plan(
        [first, second],
        RenameRules(find="draft", replace="final", prefix="2026-", suffix="-ready"),
    )

    assert [item.final_name for item in plan.items] == ["2026-final report-ready.txt", "2026-final folder-ready"]
    assert all(item.status == RenameItemStatus.CHANGED for item in plan.items)
    assert first.exists()
    assert second.exists()


def test_numbering_follows_current_order_and_manual_override_can_be_restored(tmp_path: Path) -> None:
    alpha = tmp_path / "alpha.md"
    beta = tmp_path / "beta.md"
    alpha.write_text("a", encoding="utf-8")
    beta.write_text("b", encoding="utf-8")
    plan = build_rename_plan(
        [beta, alpha],
        RenameRules(numbering_enabled=True, numbering_position="prefix", numbering_start=7, numbering_step=3, numbering_width=3),
    )

    assert [item.final_name for item in plan.items] == ["007-beta.md", "010-alpha.md"]
    plan.sort_by_name()
    assert [item.final_name for item in plan.items] == ["007-alpha.md", "010-beta.md"]
    plan.set_manual_stem(0, "custom")
    assert plan.items[0].final_name == "custom.md"
    plan.restore_automatic(0)
    assert plan.items[0].final_name == "007-alpha.md"
    plan.restore_initial_order()
    assert [item.source.path for item in plan.items] == [beta, alpha]


def test_invalid_components_include_windows_devices_and_extension_is_not_editable(tmp_path: Path) -> None:
    source = tmp_path / "original.txt"
    source.write_text("x", encoding="utf-8")
    plan = build_rename_plan([source])

    plan.set_manual_stem(0, "CON")
    assert plan.items[0].final_name == "CON.txt"
    assert plan.items[0].status == RenameItemStatus.INVALID
    assert plan.items[0].reason == "name is a reserved Windows device name"
    for name in ("COM¹", "COM²", "COM³", "LPT¹", "LPT²", "LPT³"):
        with pytest.raises(RenameValidationError, match="reserved"):
            validate_windows_component(name)
    with pytest.raises(RenameValidationError, match="cannot be zero"):
        build_rename_plan([source], RenameRules(numbering_enabled=True, numbering_step=0))


def test_plan_detects_unselected_occupancy_and_selected_swaps_are_allowed(tmp_path: Path) -> None:
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    occupied = tmp_path / "occupied.txt"
    first.write_text("first", encoding="utf-8")
    second.write_text("second", encoding="utf-8")
    occupied.write_text("occupied", encoding="utf-8")

    blocked = build_rename_plan([first])
    blocked.set_manual_stem(0, "occupied")
    assert blocked.items[0].status == RenameItemStatus.CONFLICT
    assert not blocked.can_apply

    swap = build_rename_plan([first, second])
    swap.set_manual_stem(0, "second")
    swap.set_manual_stem(1, "first")
    assert swap.can_apply
    assert [item.status for item in swap.items] == [RenameItemStatus.CHANGED, RenameItemStatus.CHANGED]


def test_batch_duplicates_and_different_parents_are_rejected(tmp_path: Path) -> None:
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("first", encoding="utf-8")
    second.write_text("second", encoding="utf-8")
    plan = build_rename_plan([first, second])
    plan.set_manual_stem(0, "same")
    plan.set_manual_stem(1, "same")
    assert {item.status for item in plan.items} == {RenameItemStatus.CONFLICT}

    other_parent = tmp_path / "other"
    other_parent.mkdir()
    third = other_parent / "third.txt"
    third.write_text("third", encoding="utf-8")
    with pytest.raises(RenameValidationError, match="one ordinary parent"):
        build_rename_plan([first, third])
