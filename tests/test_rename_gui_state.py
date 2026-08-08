from __future__ import annotations

from pathlib import Path

import pytest

from mykr_ops import rename_gui
from mykr_ops.database import Database
from mykr_ops.rename import RenameResult, RenameRules, build_rename_plan
from mykr_ops.rename_gui import RenameGuiState, _RenameWindow


def make_window(tmp_path: Path, paths: list[Path], rules: RenameRules | None = None) -> _RenameWindow:
    tkinter = pytest.importorskip("tkinter")
    from tkinter import ttk

    database = Database(tmp_path / "state" / "mykr-ops.db")
    try:
        window = _RenameWindow(
            tkinter,
            ttk,
            object(),
            RenameGuiState(build_rename_plan(paths, rules)),
            database,
            None,
        )
    except tkinter.TclError as exc:
        pytest.skip(f"Tk is unavailable for widget verification: {exc}")
    window.root.withdraw()
    return window


def test_gui_state_binds_rules_manual_override_and_apply_state(tmp_path: Path) -> None:
    source = tmp_path / "draft.txt"
    source.write_text("data", encoding="utf-8")
    state = RenameGuiState(build_rename_plan([source]))

    assert not state.apply_enabled
    state.update_rules(prefix="done-")
    assert state.apply_enabled
    assert state.plan.items[0].final_name == "done-draft.txt"
    state.set_manual_stem(0, "custom")
    assert state.plan.items[0].final_name == "custom.txt"
    state.restore_automatic(0)
    assert state.plan.items[0].final_name == "done-draft.txt"
    state.set_manual_stem(0, "another")
    state.clear_manual_overrides()
    assert not state.plan.items[0].is_manual
    assert state.plan.items[0].final_name == "done-draft.txt"


def test_gui_state_drag_order_recomputes_numbering(tmp_path: Path) -> None:
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("first", encoding="utf-8")
    second.write_text("second", encoding="utf-8")
    state = RenameGuiState(
        build_rename_plan([first, second], RenameRules(numbering_enabled=True, numbering_width=1))
    )

    state.move_item(1, 0)

    assert [item.final_name for item in state.plan.items] == ["1-second.txt", "2-first.txt"]


def test_gui_state_freezes_after_success_and_uses_large_item_modes(tmp_path: Path) -> None:
    paths: list[Path] = []
    for number in range(201):
        path = tmp_path / f"entry-{number}.txt"
        path.write_text("x", encoding="utf-8")
        paths.append(path)
    state = RenameGuiState(build_rename_plan(paths, RenameRules(prefix="done-")))

    assert state.item_mode == "reduced"
    state.complete(RenameResult(1, 201, 0, False, "Renamed 201 item(s)."))
    assert state.completed
    assert not state.apply_enabled

    for number in range(201, 501):
        path = tmp_path / f"entry-{number}.txt"
        path.write_text("x", encoding="utf-8")
        paths.append(path)
    assert RenameGuiState(build_rename_plan(paths)).item_mode == "minimal"


def test_gui_keeps_row_widgets_and_manual_input_during_debounced_rule_updates(tmp_path: Path) -> None:
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("first", encoding="utf-8")
    second.write_text("second", encoding="utf-8")
    window = make_window(tmp_path, [first, second])
    try:
        first_view = window._rows[first]
        window._remember_edit_start(first)
        first_view.stem.set("manual-name")
        window.root.after(130, window.root.quit)
        window.root.mainloop()

        assert window._rows[first] is first_view
        assert window.state.plan.items[0].editable_stem == "manual-name"

        assert window._commit_and_move(second, -1, None) == "break"
        assert not window.state.plan.items[1].is_manual

        second_view = window._rows[second]
        first_view.stem.set("same-name")
        second_view.stem.set("same-name")
        window.root.after(130, window.root.quit)
        window.root.mainloop()

        assert first_view.stem.get() == "same-name"
        assert second_view.stem.get() == "same-name"
        assert not window.state.apply_enabled

        window._restore_auto(first)
        window._restore_auto(second)
        window._rule_variables["prefix"].set("batch-")
        window.root.after(130, window.root.quit)
        window.root.mainloop()

        assert window._rows[first] is first_view
        assert window.state.plan.items[0].editable_stem == "batch-first"
        assert window.state.plan.items[1].editable_stem == "batch-second"
    finally:
        window.root.destroy()


def test_gui_apply_flushes_pending_manual_and_rule_edits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "draft.txt"
    source.write_text("data", encoding="utf-8")
    captured: list[str] = []

    def apply(plan: object, *_: object) -> RenameResult:
        captured.append(plan.items[0].final_name)  # type: ignore[attr-defined]
        return RenameResult(1, 1, 0, False, "Renamed 1 item.")

    monkeypatch.setattr(rename_gui, "apply_rename", apply)
    window = make_window(tmp_path, [source], RenameRules(prefix="old-"))
    try:
        window._rows[source].stem.set("manual-new")
        window._apply_action()
        assert captured == ["manual-new.txt"]
    finally:
        window.root.destroy()

    captured.clear()
    window = make_window(tmp_path, [source], RenameRules(prefix="old-"))
    try:
        window._rule_variables["prefix"].set("new-")
        window._apply_action()
        assert captured == ["new-draft.txt"]
    finally:
        window.root.destroy()


def test_gui_apply_flush_blocks_pending_invalid_rules_and_manual_conflicts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("first", encoding="utf-8")
    second.write_text("second", encoding="utf-8")
    calls: list[object] = []
    monkeypatch.setattr(rename_gui, "apply_rename", lambda *args: calls.append(args))
    window = make_window(tmp_path, [first, second], RenameRules(prefix="old-"))
    try:
        window._rule_variables["numbering_step"].set("0")
        window._apply_action()
        assert not calls
        assert str(window._apply["state"]) == "disabled"
        assert "numbering step cannot be zero" in window._summary["text"]
    finally:
        window.root.destroy()

    window = make_window(tmp_path, [first, second], RenameRules(prefix="old-"))
    try:
        window._rows[first].stem.set("same")
        window._rows[second].stem.set("same")
        window._apply_action()
        assert not calls
        assert not window.state.apply_enabled
        assert "conflicts" in window._summary["text"]
    finally:
        window.root.destroy()


def test_gui_reset_manual_edits_clears_pending_and_committed_overrides(tmp_path: Path) -> None:
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("first", encoding="utf-8")
    second.write_text("second", encoding="utf-8")
    window = make_window(tmp_path, [first, second], RenameRules(prefix="batch-"))
    try:
        window._rows[first].stem.set("manual-first")
        window._rows[second].stem.set("manual-second")
        assert window._flush_pending_state()
        assert all(item.is_manual for item in window.state.plan.items)

        window._reset_manual_edits()
        assert not any(item.is_manual for item in window.state.plan.items)
        assert [item.final_name for item in window.state.plan.items] == ["batch-first.txt", "batch-second.txt"]
        assert window._rows[first].stem.get() == "batch-first"
        assert window._rows[second].stem.get() == "batch-second"
        assert str(window._reset_manual["state"]) == "disabled"

        window._rows[first].stem.set("manual-again")
        assert window._flush_pending_state()
        window._rule_variables["prefix"].set("current-")
        window._reset_manual_edits()
        assert not any(item.is_manual for item in window.state.plan.items)
        assert [item.final_name for item in window.state.plan.items] == ["current-first.txt", "current-second.txt"]

        window._rows[first].stem.set("pending-old-value")
        window._reset_manual_edits()
        window.root.after(220, window.root.quit)
        window.root.mainloop()
        assert not any(item.is_manual for item in window.state.plan.items)
        assert window._rows[first].stem.get() == "current-first"
    finally:
        window.root.destroy()
