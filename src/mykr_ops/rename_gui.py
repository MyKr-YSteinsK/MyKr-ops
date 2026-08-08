from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
from typing import Any

from .database import Database
from .filesystem import FilesystemSafetyError
from .rename import (
    RenameError,
    RenameItem,
    RenameItemStatus,
    RenameMode,
    RenamePlan,
    RenameResult,
    apply_rename,
    build_rename_plan,
    undo_rename_run,
)


@dataclass
class RenameGuiState:
    """UI-facing state; rename planning stays in the core module."""

    plan: RenamePlan
    busy: bool = False
    locked: bool = False
    last_result: RenameResult | None = None
    last_apply_run_id: int | None = None
    last_apply_original_paths: tuple[Path, ...] = ()

    @property
    def apply_enabled(self) -> bool:
        return not self.busy and not self.locked and self.plan.can_apply

    @property
    def item_mode(self) -> str:
        count = len(self.plan.items)
        if count > 500:
            return "minimal"
        if count > 200:
            return "reduced"
        return "normal"

    @property
    def summary(self) -> str:
        changed = len(self.plan.changed_items)
        unchanged = sum(item.status == RenameItemStatus.UNCHANGED for item in self.plan.items)
        invalid = sum(item.status == RenameItemStatus.INVALID for item in self.plan.items)
        conflict = sum(item.status == RenameItemStatus.CONFLICT for item in self.plan.items)
        failed = sum(item.status == RenameItemStatus.FAILED for item in self.plan.items)
        return f"{changed} 项将重命名 · {unchanged} 项无变化 · {conflict} 项冲突 · {invalid + failed} 项名称无效"

    def update_rules(self, **values: object) -> None:
        for name, value in values.items():
            setattr(self.plan.rules, name, value)
        self.plan.recompute()

    def set_manual_stem(self, index: int, value: str) -> None:
        self.plan.set_manual_stem(index, value)

    def restore_automatic(self, index: int) -> None:
        self.plan.restore_automatic(index)

    def clear_manual_overrides(self) -> None:
        self.plan.clear_manual_overrides()

    def restore_order(self) -> None:
        self.plan.restore_initial_order()

    def sort_by_name(self, descending: bool = False) -> None:
        self.plan.sort_by_name(descending)

    def move_item(self, source_index: int, destination_index: int) -> None:
        self.plan.move_item(source_index, destination_index)

    def record_apply(self, result: RenameResult, original_paths: tuple[Path, ...]) -> None:
        self.last_result = result
        self.last_apply_run_id = result.run_id
        self.last_apply_original_paths = original_paths


@dataclass
class _RenameRowView:
    source_path: Path
    frame: Any
    stem: Any
    entry: Any
    extension: Any
    status: Any
    manual: Any
    restore: Any
    debounce_id: str | None = None
    pre_edit_value: str = ""
    was_manual: bool = False


class _RenameWindow:
    _BACKGROUND = "#f5f7fa"
    _SURFACE = "#ffffff"
    _BORDER = "#d7dde5"
    _TEXT = "#19212b"
    _MUTED = "#667589"
    _ACCENT = "#2167b1"
    _SUCCESS = "#20744a"
    _WARNING = "#9a6500"
    _DANGER = "#b33b43"

    def __init__(
        self, tk: Any, ttk: Any, messagebox: Any, state: RenameGuiState, database: Database,
        logger: logging.Logger | None,
    ) -> None:
        self.tk = tk
        self.ttk = ttk
        self.messagebox = messagebox
        self.state = state
        self.database = database
        self.logger = logger
        self.root = tk.Tk()
        self.root.title("MyKr-ops 重命名")
        self.root.geometry("1100x720")
        self.root.minsize(900, 600)
        self.root.configure(background=self._BACKGROUND)
        self._rows: dict[Path, _RenameRowView] = {}
        self._rendering = False
        self._rules_after_id: str | None = None
        self._last_input_error: str | None = None
        self._drag_source: Path | None = None
        self._drag_target: int | None = None
        self._active_panel = "transform"
        self._rule_controls: list[Any] = []
        self._configure_style()
        self._build()

    def _configure_style(self) -> None:
        style = self.ttk.Style(self.root)
        style.configure("MyKr.TFrame", background=self._BACKGROUND)
        style.configure("MyKr.Surface.TFrame", background=self._SURFACE)
        style.configure("MyKr.TLabel", background=self._BACKGROUND, foreground=self._TEXT, font=("Segoe UI", 10))
        style.configure("MyKr.Surface.TLabel", background=self._SURFACE, foreground=self._TEXT, font=("Segoe UI", 10))
        style.configure("MyKr.Header.TLabel", background=self._BACKGROUND, foreground=self._TEXT, font=("Segoe UI Semibold", 16))
        style.configure("MyKr.Muted.TLabel", background=self._BACKGROUND, foreground=self._MUTED, font=("Segoe UI", 9))
        style.configure("MyKr.SurfaceMuted.TLabel", background=self._SURFACE, foreground=self._MUTED, font=("Segoe UI", 9))
        style.configure("MyKr.Success.TLabel", background=self._SURFACE, foreground=self._SUCCESS, font=("Segoe UI Semibold", 9))
        style.configure("MyKr.Warning.TLabel", background=self._SURFACE, foreground=self._WARNING, font=("Segoe UI Semibold", 9))
        style.configure("MyKr.Danger.TLabel", background=self._SURFACE, foreground=self._DANGER, font=("Segoe UI Semibold", 9))
        style.configure("MyKr.TButton", font=("Segoe UI", 9), padding=(10, 5))
        style.map("MyKr.TButton", background=[("active", "#e7eef7"), ("disabled", "#edf0f3")])
        style.configure("MyKr.Tab.TButton", font=("Segoe UI Semibold", 9), padding=(12, 6))
        style.map("MyKr.Tab.TButton", background=[("active", "#dfeaf7")])
        style.configure("MyKr.Accent.TButton", font=("Segoe UI Semibold", 10), padding=(14, 7), foreground="#ffffff", background=self._ACCENT)
        style.map("MyKr.Accent.TButton", background=[("active", "#174f89"), ("disabled", "#aeb8c5")], foreground=[("disabled", "#f3f5f7")])
        style.configure("MyKr.TEntry", fieldbackground=self._SURFACE, foreground=self._TEXT, padding=(6, 4))
        style.map("MyKr.TEntry", fieldbackground=[("focus", "#ffffff")])

    def _build(self) -> None:
        header = self.ttk.Frame(self.root, style="MyKr.TFrame", padding=(24, 16, 24, 6))
        header.pack(fill="x")
        self.ttk.Label(header, text="MyKr-ops 重命名", style="MyKr.Header.TLabel").pack(side="left")
        self.ttk.Label(header, text=f"已选择 {len(self.state.plan.items)} 项", style="MyKr.Muted.TLabel").pack(side="right", pady=(5, 0))
        self.ttk.Label(self.root, text=str(self.state.plan.parent), style="MyKr.Muted.TLabel", padding=(24, 0, 24, 12)).pack(fill="x")

        tools = self.ttk.Frame(self.root, style="MyKr.Surface.TFrame", padding=(24, 8, 24, 0))
        tools.pack(fill="x", padx=24)
        self._tab_host = self.ttk.Frame(tools, style="MyKr.Surface.TFrame")
        self._tab_host.pack(fill="x")
        for text, mode, panel in (
            ("常规重命名", RenameMode.TRANSFORM, "transform"),
            ("连续编号", RenameMode.NUMBERING, "number"),
        ):
            button = self.ttk.Button(
                self._tab_host, text=text, style="MyKr.Tab.TButton",
                command=lambda value=mode, panel_name=panel: self._select_mode(value, panel_name),
            )
            button.pack(side="left", padx=(0, 6))
            self._rule_controls.append(button)
        order_button = self.ttk.Button(self._tab_host, text="排序", style="MyKr.Tab.TButton", command=lambda: self._show_panel("order"))
        order_button.pack(side="left", padx=(0, 6))
        self._rule_controls.append(order_button)
        self._reset_manual = self.ttk.Button(self._tab_host, text="清除手动修改", style="MyKr.TButton", command=self._reset_manual_edits)
        self._reset_manual.pack(side="right")

        self._panel_host = self.tk.Frame(tools, background=self._SURFACE, height=0)
        self._panel_host.pack(fill="x", pady=(5, 8))
        self._panel_host.pack_propagate(False)
        self._panels: dict[str, Any] = {}
        self._rule_variables = self._new_rule_variables()
        self._build_panels()

        body = self.ttk.Frame(self.root, style="MyKr.Surface.TFrame", padding=(18, 10, 18, 10))
        body.pack(fill="both", expand=True, padx=24)
        headings = self.ttk.Frame(body, style="MyKr.Surface.TFrame")
        headings.pack(fill="x", pady=(0, 5))
        self.ttk.Label(headings, text="", style="MyKr.SurfaceMuted.TLabel", width=4).pack(side="left")
        self.ttk.Label(headings, text="原名称", style="MyKr.SurfaceMuted.TLabel", width=30).pack(side="left")
        self.ttk.Label(headings, text="新名称", style="MyKr.SurfaceMuted.TLabel", width=36).pack(side="left", padx=(12, 0))
        self.ttk.Label(headings, text="状态", style="MyKr.SurfaceMuted.TLabel").pack(side="left", padx=(12, 0))
        self._canvas = self.tk.Canvas(body, background=self._SURFACE, highlightthickness=0, borderwidth=0)
        scrollbar = self.ttk.Scrollbar(body, orient="vertical", command=self._canvas.yview)
        self._list_host = self.ttk.Frame(self._canvas, style="MyKr.Surface.TFrame")
        self._list_window = self._canvas.create_window((0, 0), window=self._list_host, anchor="nw")
        self._canvas.configure(yscrollcommand=scrollbar.set)
        self._canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self._drag_indicator = self.tk.Frame(self._list_host, height=2, background=self._ACCENT)

        footer = self.ttk.Frame(self.root, style="MyKr.TFrame", padding=(24, 12, 24, 18))
        footer.pack(fill="x")
        self._summary = self.ttk.Label(footer, text=self.state.summary, style="MyKr.Muted.TLabel")
        self._summary.pack(side="left")
        self._undo = self.ttk.Button(footer, text="撤销本次", style="MyKr.TButton", command=self._undo_action)
        self._apply = self.ttk.Button(footer, text="", style="MyKr.Accent.TButton", command=self._apply_action)
        self._apply.pack(side="right")
        self._cancel = self.ttk.Button(footer, text="取消", style="MyKr.TButton", command=self.root.destroy)
        self._cancel.pack(side="right", padx=(0, 8))

        self._canvas.bind("<Configure>", lambda event: self._canvas.itemconfigure(self._list_window, width=event.width))
        self._list_host.bind("<Configure>", lambda _event: self._canvas.configure(scrollregion=self._canvas.bbox("all")))
        self.root.bind_all("<MouseWheel>", self._scroll)
        self._rebuild_rows()
        initial_panel = "number" if self.state.plan.rules.mode == RenameMode.NUMBERING else "transform"
        self._show_panel(initial_panel, animate=False)
        self._fade_in()

    def _new_rule_variables(self) -> dict[str, Any]:
        rules = self.state.plan.rules
        values = {
            "find": self.tk.StringVar(value=rules.find),
            "replace": self.tk.StringVar(value=rules.replace),
            "prefix": self.tk.StringVar(value=rules.prefix),
            "suffix": self.tk.StringVar(value=rules.suffix),
            "numbering_start": self.tk.StringVar(value=str(rules.numbering_start)),
            "numbering_step": self.tk.StringVar(value=str(rules.numbering_step)),
            "numbering_width": self.tk.StringVar(value=str(rules.numbering_width)),
            "numbering_prefix": self.tk.StringVar(value=rules.numbering_prefix),
            "numbering_suffix": self.tk.StringVar(value=rules.numbering_suffix),
        }
        for variable in values.values():
            variable.trace_add("write", self._schedule_rule_update)
        return values

    def _build_panels(self) -> None:
        transform = self.ttk.Frame(self._panel_host, style="MyKr.Surface.TFrame")
        self._panels["transform"] = transform
        self._entry_pair(transform, "查找", "find", 0)
        self._entry_pair(transform, "替换为", "replace", 1)
        self._entry_pair(transform, "前缀", "prefix", 2)
        self._entry_pair(transform, "后缀", "suffix", 3)

        number = self.ttk.Frame(self._panel_host, style="MyKr.Surface.TFrame")
        self._panels["number"] = number
        for column, (label, name, width) in enumerate((
            ("起始值", "numbering_start", 8), ("步长", "numbering_step", 8), ("位数", "numbering_width", 8),
            ("固定前缀", "numbering_prefix", 20), ("固定后缀", "numbering_suffix", 20),
        )):
            self.ttk.Label(number, text=label, style="MyKr.SurfaceMuted.TLabel").grid(row=0, column=column, sticky="w", padx=(0, 12))
            entry = self.ttk.Entry(number, textvariable=self._rule_variables[name], style="MyKr.TEntry", width=width)
            entry.grid(row=1, column=column, padx=(0, 12), sticky="w")
            self._rule_controls.append(entry)

        order = self.ttk.Frame(self._panel_host, style="MyKr.Surface.TFrame")
        self._panels["order"] = order
        for text, command in (
            ("按名称升序", lambda: self._sort(False)),
            ("按名称降序", lambda: self._sort(True)),
            ("恢复初始顺序", self._restore_order),
        ):
            button = self.ttk.Button(order, text=text, style="MyKr.TButton", command=command)
            button.pack(side="left", padx=(0, 8))
            self._rule_controls.append(button)

    def _entry_pair(self, panel: Any, label: str, variable: str, column: int) -> None:
        self.ttk.Label(panel, text=label, style="MyKr.SurfaceMuted.TLabel").grid(row=0, column=column, sticky="w", padx=(0, 12))
        entry = self.ttk.Entry(panel, textvariable=self._rule_variables[variable], style="MyKr.TEntry", width=22)
        entry.grid(row=1, column=column, sticky="w", padx=(0, 12))
        self._rule_controls.append(entry)

    def _select_mode(self, mode: RenameMode, panel: str) -> None:
        if self.state.busy or self.state.locked or not self._commit_rules():
            return
        try:
            self.state.update_rules(mode=mode)
        except RenameError as exc:
            self._show_input_error(exc)
            return
        self._show_panel(panel)
        self._update_rows()

    def _show_panel(self, name: str, *, animate: bool = True) -> None:
        if self.state.busy or self.state.locked:
            return
        if name == self._active_panel and self._panels.get(name, {}).winfo_manager():
            return
        for panel in self._panels.values():
            panel.pack_forget()
        self._active_panel = name
        self._panels[name].pack(fill="x", padx=4, pady=4)
        if self.state.item_mode == "minimal" or not animate:
            self._panel_host.configure(height=72)
            return
        self._animate_panel_height(0, 72)

    def _animate_panel_height(self, start: int, target: int) -> None:
        if self.state.item_mode == "minimal":
            self._panel_host.configure(height=target)
            return
        step = 12 if target >= start else -12
        next_height = target if abs(target - start) <= abs(step) else start + step
        self._panel_host.configure(height=next_height)
        if next_height != target:
            self.root.after(20, lambda: self._animate_panel_height(next_height, target))

    def _rebuild_rows(self) -> None:
        for view in self._rows.values():
            if view.debounce_id is not None:
                self.root.after_cancel(view.debounce_id)
            view.frame.destroy()
        self._rows.clear()
        for item in self.state.plan.items:
            self._create_row(item)
        self._update_rows()

    def _create_row(self, item: RenameItem) -> None:
        frame = self.ttk.Frame(self._list_host, style="MyKr.Surface.TFrame", padding=(0, 4, 0, 4))
        frame.pack(fill="x")
        handle = self.ttk.Label(frame, text="⋮⋮", style="MyKr.SurfaceMuted.TLabel", width=3)
        handle.pack(side="left")
        icon = self.tk.Canvas(frame, width=16, height=16, background=self._SURFACE, highlightthickness=0, borderwidth=0)
        icon.pack(side="left", padx=(0, 6))
        self._draw_icon(icon, item)
        self.ttk.Label(frame, text=item.source.original_name, style="MyKr.Surface.TLabel", width=30).pack(side="left")
        name = self.ttk.Frame(frame, style="MyKr.Surface.TFrame")
        name.pack(side="left", fill="x", expand=True, padx=(12, 0))
        stem = self.tk.StringVar(value=item.editable_stem)
        entry = self.ttk.Entry(name, textvariable=stem, style="MyKr.TEntry", width=28)
        entry.pack(side="left", fill="x", expand=True)
        extension = self.ttk.Label(name, text=item.extension, style="MyKr.SurfaceMuted.TLabel")
        extension.pack(side="left")
        status = self.ttk.Label(frame, text="", style="MyKr.SurfaceMuted.TLabel", width=28)
        status.pack(side="left", padx=(12, 0))
        manual = self.ttk.Label(frame, text="", style="MyKr.SurfaceMuted.TLabel", width=8)
        manual.pack(side="left", padx=(6, 0))
        restore = self.ttk.Button(frame, text="恢复自动", style="MyKr.TButton")
        restore.pack(side="right")
        view = _RenameRowView(item.source.path, frame, stem, entry, extension, status, manual, restore)
        self._rows[item.source.path] = view
        stem.trace_add("write", lambda *_args, path=item.source.path: self._schedule_manual(path))
        entry.bind("<FocusIn>", lambda _event, path=item.source.path: self._remember_edit_start(path))
        entry.bind("<Return>", lambda event, path=item.source.path: self._commit_and_move(path, 1, event))
        entry.bind("<Shift-Return>", lambda event, path=item.source.path: self._commit_and_move(path, -1, event))
        entry.bind("<Control-a>", self._select_all)
        entry.bind("<Escape>", lambda _event, path=item.source.path: self._restore_pre_edit(path))
        restore.configure(command=lambda path=item.source.path: self._restore_auto(path))
        handle.bind("<ButtonPress-1>", lambda _event, path=item.source.path: self._start_drag(path))
        handle.bind("<B1-Motion>", self._drag_motion)
        handle.bind("<ButtonRelease-1>", self._finish_drag)

    def _draw_icon(self, canvas: Any, item: RenameItem) -> None:
        if item.source.object_kind == "directory":
            canvas.create_rectangle(2, 5, 14, 13, fill="#d99b33", outline="#b77b1b")
            canvas.create_rectangle(3, 3, 9, 6, fill="#e8b653", outline="#b77b1b")
            return
        suffix = item.extension.casefold()
        if suffix in {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}:
            canvas.create_rectangle(2, 2, 14, 14, outline="#3a8a6d", fill="#dff1e9")
            canvas.create_polygon(3, 12, 7, 7, 10, 10, 12, 6, 14, 12, fill="#3a8a6d")
        elif suffix in {".mp4", ".mov", ".avi", ".mkv"}:
            canvas.create_rectangle(2, 3, 12, 13, outline="#7257a6", fill="#ece6fa")
            canvas.create_polygon(6, 6, 6, 10, 10, 8, fill="#7257a6")
        elif suffix in {".txt", ".md", ".doc", ".docx", ".pdf"}:
            canvas.create_rectangle(3, 2, 13, 14, outline="#477cae", fill="#e7f0fa")
            canvas.create_line(5, 6, 11, 6, fill="#477cae")
            canvas.create_line(5, 9, 11, 9, fill="#477cae")
        else:
            canvas.create_rectangle(3, 2, 13, 14, outline="#7b8794", fill="#eef1f4")

    def _item_index(self, source_path: Path) -> int:
        return next(index for index, item in enumerate(self.state.plan.items) if item.source.path == source_path)

    def _remember_edit_start(self, source_path: Path) -> None:
        view = self._rows[source_path]
        item = self.state.plan.items[self._item_index(source_path)]
        view.pre_edit_value = view.stem.get()
        view.was_manual = item.is_manual

    def _schedule_manual(self, source_path: Path) -> None:
        if self._rendering or self.state.busy or self.state.locked:
            return
        view = self._rows[source_path]
        if view.debounce_id is not None:
            self.root.after_cancel(view.debounce_id)
        view.debounce_id = self.root.after(100, lambda path=source_path: self._commit_manual(path))

    def _commit_manual(self, source_path: Path) -> bool:
        view = self._rows.get(source_path)
        if view is None:
            return True
        view.debounce_id = None
        index = self._item_index(source_path)
        item = self.state.plan.items[index]
        if not item.is_manual and view.stem.get() == item.automatic_stem:
            self._update_rows(active_source=source_path)
            return True
        try:
            self.state.set_manual_stem(index, view.stem.get())
        except RenameError as exc:
            self._show_input_error(exc)
            return False
        self._update_rows(active_source=source_path)
        return True

    def _commit_and_move(self, source_path: Path, delta: int, _event: Any) -> str:
        view = self._rows[source_path]
        if view.debounce_id is not None:
            self.root.after_cancel(view.debounce_id)
        self._commit_manual(source_path)
        index = self._item_index(source_path) + delta
        if 0 <= index < len(self.state.plan.items):
            self._rows[self.state.plan.items[index].source.path].entry.focus_set()
        return "break"

    def _select_all(self, event: Any) -> str:
        event.widget.selection_range(0, "end")
        event.widget.icursor("end")
        return "break"

    def _restore_pre_edit(self, source_path: Path) -> str:
        if not self._commit_rules():
            return "break"
        view = self._rows[source_path]
        if view.debounce_id is not None:
            self.root.after_cancel(view.debounce_id)
            view.debounce_id = None
        index = self._item_index(source_path)
        try:
            if view.was_manual:
                self.state.set_manual_stem(index, view.pre_edit_value)
            else:
                self.state.restore_automatic(index)
        except RenameError as exc:
            self._show_input_error(exc)
            return "break"
        view.stem.set(self.state.plan.items[index].editable_stem)
        self._update_rows(active_source=source_path)
        return "break"

    def _restore_auto(self, source_path: Path) -> None:
        if self.state.busy or self.state.locked or not self._commit_rules():
            return
        try:
            self.state.restore_automatic(self._item_index(source_path))
        except RenameError as exc:
            self._show_input_error(exc)
            return
        self._update_rows()

    def _reset_manual_edits(self) -> None:
        if self.state.busy or self.state.locked:
            return
        for view in self._rows.values():
            if view.debounce_id is not None:
                self.root.after_cancel(view.debounce_id)
                view.debounce_id = None
        if self._rules_after_id is not None:
            self.root.after_cancel(self._rules_after_id)
            self._rules_after_id = None
        if not self._commit_rules():
            return
        self.state.clear_manual_overrides()
        self._update_rows()

    def _schedule_rule_update(self, *_: object) -> None:
        if self._rendering or self.state.busy or self.state.locked:
            return
        if self._rules_after_id is not None:
            self.root.after_cancel(self._rules_after_id)
        self._rules_after_id = self.root.after(100, self._commit_rules)

    def _commit_rules(self) -> bool:
        self._rules_after_id = None
        try:
            self.state.update_rules(
                find=self._rule_variables["find"].get(), replace=self._rule_variables["replace"].get(),
                prefix=self._rule_variables["prefix"].get(), suffix=self._rule_variables["suffix"].get(),
                numbering_start=int(self._rule_variables["numbering_start"].get()),
                numbering_step=int(self._rule_variables["numbering_step"].get()),
                numbering_width=int(self._rule_variables["numbering_width"].get()),
                numbering_prefix=self._rule_variables["numbering_prefix"].get(),
                numbering_suffix=self._rule_variables["numbering_suffix"].get(),
            )
        except (ValueError, RenameError) as exc:
            self._show_input_error(exc)
            return False
        self._last_input_error = None
        self._update_rows()
        return True

    def _show_input_error(self, error: BaseException) -> None:
        self._last_input_error = f"输入错误：{self._friendly_error(str(error))}"
        self._summary.configure(text=self._last_input_error)
        self._apply.configure(state="disabled")

    @staticmethod
    def _friendly_error(message: str) -> str:
        translations = {
            "numbering step cannot be zero": "编号步长不能为 0",
            "numbering width must be at least one": "编号位数至少为 1",
            "name is empty": "名称不能为空",
            "name is a reserved Windows device name": "名称是 Windows 保留设备名",
            "name must not end in a space or period": "名称不能以空格或句点结尾",
            "name contains an invalid Windows filename character": "名称包含 Windows 不允许的字符",
            "multiple selected items resolve to the same target name": "多个选中项会得到相同名称",
            "target name is occupied by an unselected filesystem object": "目标名称已被未选中的项目占用",
            "select at least one file or folder to rename": "请至少选择一个文件或文件夹",
            "all selected items must belong to one ordinary parent directory": "所有选中项必须位于同一普通父目录",
        }
        return translations.get(message, message if message else "输入格式无效")

    def _flush_pending_state(self) -> bool:
        if self._rules_after_id is not None:
            self.root.after_cancel(self._rules_after_id)
            self._rules_after_id = None
        valid = self._commit_rules()
        for source_path, view in self._rows.items():
            if view.debounce_id is None:
                continue
            self.root.after_cancel(view.debounce_id)
            view.debounce_id = None
            valid = self._commit_manual(source_path) and valid
        if not valid:
            self._summary.configure(text=self._last_input_error or "输入错误：重命名设置无效")
            self._apply.configure(state="disabled")
            return False
        self.state.plan.recompute()
        self._update_rows()
        return self.state.apply_enabled

    def _update_rows(self, active_source: Path | None = None) -> None:
        self._rendering = True
        try:
            locked = self.state.busy or self.state.locked
            for item in self.state.plan.items:
                view = self._rows[item.source.path]
                if (
                    item.source.path != active_source and view.debounce_id is None and not item.is_manual
                    and view.stem.get() != item.editable_stem
                ):
                    view.stem.set(item.editable_stem)
                view.extension.configure(text=item.extension)
                status, style = self._status(item)
                view.status.configure(text=status, style=style)
                view.manual.configure(text="手动修改" if item.is_manual else "", style="MyKr.Success.TLabel" if item.is_manual else "MyKr.SurfaceMuted.TLabel")
                view.restore.configure(state="normal" if item.is_manual and not locked else "disabled")
                view.entry.configure(state="disabled" if locked else "normal")
            self._summary.configure(text=self._last_input_error or self.state.summary)
            changed = len(self.state.plan.changed_items)
            apply_text = "正在重命名…" if self.state.busy else f"确认重命名 {changed} 项"
            self._apply.configure(text=apply_text, state="normal" if self.state.apply_enabled else "disabled")
            has_manual = any(item.is_manual for item in self.state.plan.items)
            self._reset_manual.configure(state="normal" if has_manual and not locked else "disabled")
            for control in self._rule_controls:
                control.configure(state="disabled" if locked else "normal")
            self._undo.configure(state="normal" if self.state.last_apply_run_id is not None and not locked else "disabled")
        finally:
            self._rendering = False

    def _status(self, item: RenameItem) -> tuple[str, str]:
        if item.status == RenameItemStatus.CHANGED:
            return "将重命名", "MyKr.Success.TLabel"
        if item.status == RenameItemStatus.UNCHANGED:
            return "无变化", "MyKr.SurfaceMuted.TLabel"
        if item.status == RenameItemStatus.CONFLICT:
            return f"冲突：{self._friendly_error(item.reason or '')}", "MyKr.Warning.TLabel"
        return f"名称无效：{self._friendly_error(item.reason or item.status.value)}", "MyKr.Danger.TLabel"

    def _sort(self, descending: bool) -> None:
        if self.state.busy or self.state.locked or not self._flush_pending_state():
            return
        self.state.sort_by_name(descending)
        self._rebuild_rows()

    def _restore_order(self) -> None:
        if self.state.busy or self.state.locked or not self._flush_pending_state():
            return
        self.state.restore_order()
        self._rebuild_rows()

    def _start_drag(self, source_path: Path) -> None:
        if not self.state.busy and not self.state.locked:
            self._drag_source = source_path

    def _drag_motion(self, event: Any) -> None:
        if self._drag_source is None:
            return
        target = len(self.state.plan.items) - 1
        for index, item in enumerate(self.state.plan.items):
            view = self._rows[item.source.path]
            midpoint = view.frame.winfo_rooty() + view.frame.winfo_height() // 2
            if event.y_root < midpoint:
                target = index
                break
        self._drag_target = target
        target_view = self._rows[self.state.plan.items[target].source.path]
        self._drag_indicator.pack_forget()
        self._drag_indicator.pack(before=target_view.frame, fill="x", pady=1)

    def _finish_drag(self, _event: Any) -> None:
        source_path, target = self._drag_source, self._drag_target
        self._drag_source = None
        self._drag_target = None
        self._drag_indicator.pack_forget()
        if source_path is None or target is None:
            return
        if not self._flush_pending_state():
            return
        source_index = self._item_index(source_path)
        if source_index != target:
            self.state.move_item(source_index, target)
            self._rebuild_rows()

    def _set_rule_variables(self) -> None:
        rules = self.state.plan.rules
        values = {
            "find": rules.find,
            "replace": rules.replace,
            "prefix": rules.prefix,
            "suffix": rules.suffix,
            "numbering_start": str(rules.numbering_start),
            "numbering_step": str(rules.numbering_step),
            "numbering_width": str(rules.numbering_width),
            "numbering_prefix": rules.numbering_prefix,
            "numbering_suffix": rules.numbering_suffix,
        }
        self._rendering = True
        try:
            for name, value in values.items():
                self._rule_variables[name].set(value)
        finally:
            self._rendering = False

    def _rebase(self, paths: tuple[Path, ...]) -> None:
        fresh_plan = build_rename_plan(paths)
        self.state.plan = fresh_plan
        self._last_input_error = None
        self._set_rule_variables()
        self._rebuild_rows()

    def _apply_action(self) -> None:
        if not self._flush_pending_state():
            return
        final_paths = tuple(item.final_path for item in self.state.plan.items)
        original_paths = tuple(item.source.path for item in self.state.plan.items)
        self.state.busy = True
        self._update_rows()
        self.root.update_idletasks()
        try:
            result = apply_rename(self.state.plan, self.database, self.logger)
        except Exception as exc:
            if self.logger:
                self.logger.exception("rename gui apply failed: %s", exc)
            self.state.busy = False
            self._update_rows()
            self._show_error("重命名失败", exc)
            return
        self.state.busy = False
        if result.failed:
            self._update_rows()
            self._show_error("重命名失败", RenameError(result.message))
            return
        self.state.record_apply(result, original_paths)
        try:
            self._rebase(final_paths)
        except (RenameError, FilesystemSafetyError, OSError) as exc:
            self.state.locked = True
            self._update_rows()
            self._show_error("重命名已完成，但无法安全刷新预览", exc)
            return
        self._undo.pack(side="right", padx=(0, 8))
        self._summary.configure(text=f"已成功重命名 {result.renamed_count} 项")
        self._update_rows()
        self._summary.configure(text=f"已成功重命名 {result.renamed_count} 项")

    def _undo_action(self) -> None:
        run_id = self.state.last_apply_run_id
        original_paths = self.state.last_apply_original_paths
        if run_id is None or not original_paths:
            self._show_error("无法撤销", RenameError("当前窗口没有可撤销的重命名操作。"))
            return
        self.state.busy = True
        self._update_rows()
        self.root.update_idletasks()
        try:
            result = undo_rename_run(self.database, run_id, self.logger)
        except Exception as exc:
            if self.logger:
                self.logger.exception("rename gui exact undo failed: %s", exc)
            self.state.busy = False
            self._update_rows()
            self._show_error("撤销失败", exc)
            return
        self.state.busy = False
        if result.failed:
            self._update_rows()
            self._show_error("撤销失败", RenameError(result.message))
            return
        self.state.last_result = None
        self.state.last_apply_run_id = None
        self.state.last_apply_original_paths = ()
        try:
            self._rebase(original_paths)
        except (RenameError, FilesystemSafetyError, OSError) as exc:
            self.state.locked = True
            self._update_rows()
            self._show_error("撤销已完成，但无法安全刷新预览", exc)
            return
        self._summary.configure(text="已撤销本次重命名")
        self._update_rows()
        self._summary.configure(text="已撤销本次重命名")

    def _show_error(self, title: str, error: BaseException) -> None:
        self.messagebox.showerror("MyKr-ops 重命名", f"{title}：{self._friendly_error(str(error))}", parent=self.root)

    def _scroll(self, event: Any) -> None:
        self._canvas.yview_scroll(int(-event.delta / 120), "units")

    def _fade_in(self) -> None:
        if self.state.item_mode == "minimal":
            return
        try:
            self.root.attributes("-alpha", 0.96)
            self.root.after(20, lambda: self.root.attributes("-alpha", 1.0))
        except self.tk.TclError:
            pass

    def run(self) -> None:
        self.root.mainloop()


def launch_rename_gui(paths: list[Path], database: Database, logger: logging.Logger | None = None) -> None:
    """Launch the optional Tk interface only when the gui command is selected."""
    try:
        import tkinter as tk
        from tkinter import messagebox, ttk
    except ImportError as exc:  # pragma: no cover - Windows Python normally includes Tk.
        raise RenameError("Tkinter is unavailable in this Python installation") from exc
    _RenameWindow(tk, ttk, messagebox, RenameGuiState(build_rename_plan(paths)), database, logger).run()
