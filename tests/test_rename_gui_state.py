from __future__ import annotations

from pathlib import Path

import pytest

from mykr_ops import rename_gui
from mykr_ops.database import Database
from mykr_ops.rename import RenameError, RenameMode, RenameResult, RenameRules, build_rename_plan
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
        build_rename_plan([first, second], RenameRules(mode=RenameMode.NUMBERING, numbering_width=1))
    )

    state.move_item(1, 0)

    assert [item.source.original_name for item in state.plan.items] == ["second.txt", "first.txt"]
    assert [item.final_name for item in state.plan.items] == ["1.txt", "2.txt"]


def test_gui_can_sort_and_reorder_before_a_rename_change(tmp_path: Path) -> None:
    first = tmp_path / "b.txt"
    second = tmp_path / "a.txt"
    third = tmp_path / "c.txt"
    for path in (first, second, third):
        path.write_text("x", encoding="utf-8")
    window = make_window(tmp_path, [first, second, third])
    try:
        window._sort(False)
        assert [item.source.original_name for item in window.state.plan.items] == ["a.txt", "b.txt", "c.txt"]

        window._drag_source = first
        window._drag_target = 2
        window._finish_drag(None)
        assert [item.source.original_name for item in window.state.plan.items] == ["a.txt", "c.txt", "b.txt"]

        window._select_mode(RenameMode.NUMBERING, "number")
        assert [item.final_name for item in window.state.plan.items] == ["01.txt", "02.txt", "03.txt"]
    finally:
        window.root.destroy()


def test_gui_state_tracks_latest_apply_without_freezing_and_uses_large_item_modes(tmp_path: Path) -> None:
    paths: list[Path] = []
    for number in range(201):
        path = tmp_path / f"entry-{number}.txt"
        path.write_text("x", encoding="utf-8")
        paths.append(path)
    state = RenameGuiState(build_rename_plan(paths, RenameRules(prefix="done-")))

    assert state.item_mode == "reduced"
    state.record_apply(RenameResult(1, 201, 0, False, "Renamed 201 item(s)."), tuple(paths))
    assert state.last_apply_run_id == 1
    assert state.apply_enabled
    state.busy = True
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
        plan.items[0].source.path.rename(plan.items[0].final_path)  # type: ignore[attr-defined]
        return RenameResult(1, 1, 0, False, "Renamed 1 item.")

    monkeypatch.setattr(rename_gui, "apply_rename", apply)
    window = make_window(tmp_path, [source], RenameRules(prefix="old-"))
    try:
        window._rows[source].stem.set("manual-new")
        window._apply_action()
        assert captured == ["manual-new.txt"]
        assert window.state.plan.items[0].source.original_name == "manual-new.txt"
    finally:
        window.root.destroy()

    captured.clear()
    second = tmp_path / "draft-two.txt"
    second.write_text("data", encoding="utf-8")
    window = make_window(tmp_path, [second], RenameRules(prefix="old-"))
    try:
        window._rule_variables["prefix"].set("new-")
        window._apply_action()
        assert captured == ["new-draft-two.txt"]
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
        assert "编号步长不能为 0" in window._summary["text"]
    finally:
        window.root.destroy()

    window = make_window(tmp_path, [first, second], RenameRules(prefix="old-"))
    try:
        window._rows[first].stem.set("same")
        window._rows[second].stem.set("same")
        window._apply_action()
        assert not calls
        assert not window.state.apply_enabled
        assert "冲突" in window._summary["text"]
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


def test_gui_reset_manual_edits_keeps_manual_overrides_when_current_rules_are_invalid(tmp_path: Path) -> None:
    source = tmp_path / "draft.txt"
    source.write_text("data", encoding="utf-8")
    window = make_window(tmp_path, [source], RenameRules(prefix="batch-"))
    try:
        window._rows[source].stem.set("manual-name")
        assert window._flush_pending_state()
        assert window.state.plan.items[0].is_manual

        window._rule_variables["numbering_step"].set("0")
        window._reset_manual_edits()

        assert window.state.plan.items[0].is_manual
        assert "编号步长不能为 0" in str(window._summary["text"])
        assert str(window._apply["state"]) == "disabled"

        window._rule_variables["numbering_step"].set("1")
        window._reset_manual_edits()

        assert not window.state.plan.items[0].is_manual
        assert window.state.plan.items[0].final_name == "batch-draft.txt"
    finally:
        window.root.destroy()


def test_gui_uses_chinese_rename_workflow_labels(tmp_path: Path) -> None:
    source = tmp_path / "draft.txt"
    source.write_text("data", encoding="utf-8")
    window = make_window(tmp_path, [source])
    try:
        texts: list[str] = []

        def collect(widget: object) -> None:
            try:
                texts.append(str(widget.cget("text")))  # type: ignore[attr-defined]
            except Exception:
                pass
            for child in widget.winfo_children():  # type: ignore[attr-defined]
                collect(child)

        collect(window.root)
        assert {
            "常规重命名", "连续编号", "排序", "清除手动修改", "恢复自动", "起始值", "步长", "位数",
            "固定前缀", "固定后缀", "原名称", "新名称", "状态", "无变化", "确认重命名 0 项",
        } <= set(texts)
        assert window.root.title() == "MyKr-ops 重命名"
    finally:
        window.root.destroy()


def test_gui_rebases_after_multiple_rounds_and_undoes_only_the_latest_run(tmp_path: Path) -> None:
    first = tmp_path / "a.txt"
    second = tmp_path / "b.txt"
    first.write_text("a", encoding="utf-8")
    second.write_text("b", encoding="utf-8")
    window = make_window(tmp_path, [first, second])
    try:
        window._select_mode(RenameMode.NUMBERING, "number")
        window._apply_action()

        first_round = window.state.last_apply_run_id
        assert first_round is not None
        assert [item.source.original_name for item in window.state.plan.items] == ["01.txt", "02.txt"]
        assert window.state.plan.rules.mode == RenameMode.TRANSFORM
        assert window._active_panel == "transform"
        assert not window.state.apply_enabled
        assert str(window._rows[tmp_path / "01.txt"].entry["state"]) == "normal"

        window._rule_variables["prefix"].set("new-")
        assert window._flush_pending_state()
        assert [item.final_name for item in window.state.plan.items] == ["new-01.txt", "new-02.txt"]
        window._rule_variables["prefix"].set("")
        assert window._flush_pending_state()

        window._rows[tmp_path / "01.txt"].stem.set("cover")
        window._apply_action()
        second_round = window.state.last_apply_run_id
        assert second_round is not None and second_round != first_round
        assert [item.source.original_name for item in window.state.plan.items] == ["cover.txt", "02.txt"]

        window._undo_action()
        assert [item.source.original_name for item in window.state.plan.items] == ["01.txt", "02.txt"]
        assert window.state.last_apply_run_id is None
        assert window.state.plan.rules.mode == RenameMode.TRANSFORM
        assert window._active_panel == "transform"
        assert str(window._rows[tmp_path / "01.txt"].entry["state"]) == "normal"
        assert (tmp_path / "01.txt").exists()
        assert not (tmp_path / "cover.txt").exists()
    finally:
        window.root.destroy()


def test_gui_keeps_exact_undo_available_when_apply_rebase_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "draft.txt"
    source.write_text("data", encoding="utf-8")
    window = make_window(tmp_path, [source], RenameRules(prefix="done-"))
    original_rebase = window._rebase
    original_undo = rename_gui.undo_rename_run
    undo_run_ids: list[int] = []
    rebase_calls = 0
    try:
        def rebase(paths: tuple[Path, ...]) -> None:
            nonlocal rebase_calls
            rebase_calls += 1
            if rebase_calls == 1:
                raise RenameError("simulated rebase failure")
            original_rebase(paths)

        def undo(database: Database, run_id: int, logger: object) -> RenameResult:
            undo_run_ids.append(run_id)
            return original_undo(database, run_id, logger)

        monkeypatch.setattr(window, "_rebase", rebase)
        monkeypatch.setattr(window, "_show_error", lambda *_args: None)
        monkeypatch.setattr(rename_gui, "undo_rename_run", undo)

        window._apply_action()

        run_id = window.state.last_apply_run_id
        assert run_id is not None
        assert window.state.locked
        assert not window.state.apply_enabled
        assert str(window._rows[source].entry["state"]) == "disabled"
        assert str(window._undo["state"]) == "normal"
        assert window._undo.winfo_manager() == "pack"

        window._undo_action()

        assert undo_run_ids == [run_id]
        assert not window.state.locked
        assert (tmp_path / "draft.txt").exists()
    finally:
        window.root.destroy()
