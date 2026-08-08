from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
from typing import Any

from .database import Database
from .rename import (
    RenameError,
    RenameItemStatus,
    RenamePlan,
    RenameResult,
    RenameRules,
    apply_rename,
    build_rename_plan,
    undo_latest_rename,
)


@dataclass
class RenameGuiState:
    """UI-facing state; rename planning stays in the core module."""

    plan: RenamePlan
    completed: bool = False
    result: RenameResult | None = None

    @property
    def apply_enabled(self) -> bool:
        return not self.completed and self.plan.can_apply

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
        invalid = sum(item.status == RenameItemStatus.INVALID for item in self.plan.items)
        conflict = sum(item.status == RenameItemStatus.CONFLICT for item in self.plan.items)
        failed = sum(item.status == RenameItemStatus.FAILED for item in self.plan.items)
        return f"{changed} changes · {invalid} invalid · {conflict} conflicts · {failed} failed"

    def update_rules(self, **values: object) -> None:
        for name, value in values.items():
            setattr(self.plan.rules, name, value)
        self.plan.recompute()

    def set_manual_stem(self, index: int, value: str) -> None:
        self.plan.set_manual_stem(index, value)

    def restore_automatic(self, index: int) -> None:
        self.plan.restore_automatic(index)

    def restore_order(self) -> None:
        self.plan.restore_initial_order()

    def sort_by_name(self, descending: bool = False) -> None:
        self.plan.sort_by_name(descending)

    def move_item(self, source_index: int, destination_index: int) -> None:
        self.plan.move_item(source_index, destination_index)

    def complete(self, result: RenameResult) -> None:
        self.result = result
        self.completed = not result.failed


def launch_rename_gui(paths: list[Path], database: Database, logger: logging.Logger | None = None) -> None:
    """Launch the optional Tk interface only when the gui command is selected."""
    try:
        import tkinter as tk
        from tkinter import messagebox, ttk
    except ImportError as exc:  # pragma: no cover - Windows Python normally includes Tk.
        raise RenameError("Tkinter is unavailable in this Python installation") from exc

    state = RenameGuiState(build_rename_plan(paths))
    root = tk.Tk()
    root.title("MyKr-ops Rename")
    root.geometry("1100x720")
    root.minsize(900, 600)
    root.configure(background="#f6f8fb")

    style = ttk.Style(root)
    style.configure("MyKr.TFrame", background="#f6f8fb")
    style.configure("MyKr.TLabel", background="#f6f8fb", foreground="#1d2733", font=("Segoe UI", 10))
    style.configure("MyKr.Header.TLabel", background="#f6f8fb", foreground="#15202b", font=("Segoe UI Semibold", 15))
    style.configure("MyKr.Muted.TLabel", background="#f6f8fb", foreground="#617080", font=("Segoe UI", 9))
    style.configure("MyKr.TButton", font=("Segoe UI", 9), padding=(9, 5))
    style.configure("MyKr.Accent.TButton", font=("Segoe UI Semibold", 10), padding=(12, 6))

    header = ttk.Frame(root, style="MyKr.TFrame", padding=(22, 16, 22, 8))
    header.pack(fill="x")
    ttk.Label(header, text="MyKr-ops Rename", style="MyKr.Header.TLabel").pack(side="left")
    count_label = ttk.Label(header, text=f"{len(state.plan.items)} items", style="MyKr.Muted.TLabel")
    count_label.pack(side="right")
    ttk.Label(root, text=str(state.plan.parent), style="MyKr.Muted.TLabel", padding=(22, 0, 22, 10)).pack(fill="x")

    content = ttk.Frame(root, style="MyKr.TFrame", padding=(22, 0, 22, 0))
    content.pack(fill="both", expand=True)
    rules_panel = ttk.Frame(content, style="MyKr.TFrame", width=260)
    rules_panel.pack(side="left", fill="y", padx=(0, 16))
    rows_panel = ttk.Frame(content, style="MyKr.TFrame")
    rows_panel.pack(side="left", fill="both", expand=True)

    ttk.Label(rules_panel, text="Rules", style="MyKr.Header.TLabel").pack(anchor="w", pady=(0, 10))
    active_panel = tk.StringVar(value="find")
    panel_host = ttk.Frame(rules_panel, style="MyKr.TFrame")
    panel_host.pack(fill="x", pady=(0, 12))
    panels: dict[str, ttk.Frame] = {}
    variables: dict[str, Any] = {
        "find": tk.StringVar(value=state.plan.rules.find),
        "replace": tk.StringVar(value=state.plan.rules.replace),
        "prefix": tk.StringVar(value=state.plan.rules.prefix),
        "suffix": tk.StringVar(value=state.plan.rules.suffix),
        "numbering_enabled": tk.BooleanVar(value=state.plan.rules.numbering_enabled),
        "numbering_position": tk.StringVar(value=state.plan.rules.numbering_position),
        "numbering_start": tk.StringVar(value=str(state.plan.rules.numbering_start)),
        "numbering_step": tk.StringVar(value=str(state.plan.rules.numbering_step)),
        "numbering_width": tk.StringVar(value=str(state.plan.rules.numbering_width)),
        "numbering_separator": tk.StringVar(value=state.plan.rules.numbering_separator),
    }

    table_header = ttk.Frame(rows_panel, style="MyKr.TFrame")
    table_header.pack(fill="x", pady=(0, 5))
    ttk.Label(table_header, text="Original name", style="MyKr.Muted.TLabel", width=34).pack(side="left")
    ttk.Label(table_header, text="New name", style="MyKr.Muted.TLabel", width=38).pack(side="left", padx=(12, 0))
    ttk.Label(table_header, text="Status", style="MyKr.Muted.TLabel").pack(side="left", padx=(12, 0))

    canvas = tk.Canvas(rows_panel, background="#f6f8fb", highlightthickness=0, borderwidth=0)
    scrollbar = ttk.Scrollbar(rows_panel, orient="vertical", command=canvas.yview)
    list_host = ttk.Frame(canvas, style="MyKr.TFrame")
    list_window = canvas.create_window((0, 0), window=list_host, anchor="nw")
    canvas.configure(yscrollcommand=scrollbar.set)
    canvas.pack(side="left", fill="both", expand=True)
    scrollbar.pack(side="right", fill="y")

    footer = ttk.Frame(root, style="MyKr.TFrame", padding=(22, 12, 22, 18))
    footer.pack(fill="x")
    summary_label = ttk.Label(footer, text=state.summary, style="MyKr.Muted.TLabel")
    summary_label.pack(side="left")
    apply_button = ttk.Button(footer, text="Apply rename", style="MyKr.Accent.TButton")
    apply_button.pack(side="right")
    undo_button = ttk.Button(footer, text="Undo this batch", style="MyKr.TButton")
    drag_source: list[int | None] = [None]

    def safe_recompute() -> bool:
        try:
            state.plan.recompute()
            return True
        except RenameError as exc:
            summary_label.configure(text=f"Input error: {exc}")
            return False

    def refresh_rows() -> None:
        for child in list_host.winfo_children():
            child.destroy()
        compact = state.item_mode != "normal"
        for index, item in enumerate(state.plan.items):
            row = ttk.Frame(list_host, style="MyKr.TFrame", padding=(0, 4))
            row.pack(fill="x")
            original_label = ttk.Label(row, text=item.source.original_name, style="MyKr.TLabel", width=34)
            original_label.pack(side="left")
            original_label.bind("<ButtonPress-1>", lambda _event, position=index: drag_source.__setitem__(0, position))
            original_label.bind("<ButtonRelease-1>", lambda _event, position=index: finish_drag(position))
            name_frame = ttk.Frame(row, style="MyKr.TFrame")
            name_frame.pack(side="left", fill="x", expand=True, padx=(12, 0))
            stem_value = tk.StringVar(value=item.editable_stem)
            entry = ttk.Entry(name_frame, textvariable=stem_value, width=30)
            entry.pack(side="left", fill="x", expand=True)
            ttk.Label(name_frame, text=item.extension, style="MyKr.Muted.TLabel").pack(side="left")
            status = item.status.value.lower()
            if item.reason:
                status = f"{status}: {item.reason}"
            ttk.Label(row, text=status, style="MyKr.Muted.TLabel", width=22 if compact else 30).pack(side="left", padx=(12, 0))
            if not compact:
                ttk.Button(row, text="Auto", style="MyKr.TButton", command=lambda position=index: restore_auto(position)).pack(side="right")

            def schedule_change(*_: object, position: int = index, value: Any = stem_value) -> None:
                root.after(120, lambda: update_manual(position, value.get()))

            stem_value.trace_add("write", schedule_change)
        list_host.update_idletasks()
        canvas.configure(scrollregion=canvas.bbox("all"))
        canvas.itemconfigure(list_window, width=canvas.winfo_width())
        summary_label.configure(text=state.summary)
        apply_button.configure(state="normal" if state.apply_enabled else "disabled")

    def update_manual(index: int, value: str) -> None:
        if state.completed:
            return
        try:
            state.set_manual_stem(index, value)
        except RenameError:
            return
        refresh_rows()

    def restore_auto(index: int) -> None:
        state.restore_automatic(index)
        refresh_rows()

    def finish_drag(destination: int) -> None:
        source = drag_source[0]
        drag_source[0] = None
        if source is None or source == destination or state.completed:
            return
        state.move_item(source, destination)
        refresh_rows()

    def update_rules(*_: object) -> None:
        if state.completed:
            return
        try:
            state.update_rules(
                find=variables["find"].get(),
                replace=variables["replace"].get(),
                prefix=variables["prefix"].get(),
                suffix=variables["suffix"].get(),
                numbering_enabled=variables["numbering_enabled"].get(),
                numbering_position=variables["numbering_position"].get(),
                numbering_start=int(variables["numbering_start"].get()),
                numbering_step=int(variables["numbering_step"].get()),
                numbering_width=int(variables["numbering_width"].get()),
                numbering_separator=variables["numbering_separator"].get(),
            )
        except (ValueError, RenameError) as exc:
            summary_label.configure(text=f"Input error: {exc}")
            apply_button.configure(state="disabled")
            return
        refresh_rows()

    def show_panel(name: str) -> None:
        active_panel.set(name)
        for panel_name, panel in panels.items():
            if panel_name == name:
                panel.pack(fill="x")
            else:
                panel.pack_forget()

    panel_buttons = ttk.Frame(rules_panel, style="MyKr.TFrame")
    panel_buttons.pack(fill="x", pady=(0, 6))
    for label, name in (("Find / replace", "find"), ("Prefix / suffix", "affix"), ("Numbering", "number")):
        ttk.Button(panel_buttons, text=label, style="MyKr.TButton", command=lambda panel=name: show_panel(panel)).pack(fill="x", pady=2)

    find_panel = ttk.Frame(panel_host, style="MyKr.TFrame")
    panels["find"] = find_panel
    for label, name in (("Find", "find"), ("Replace", "replace")):
        ttk.Label(find_panel, text=label, style="MyKr.Muted.TLabel").pack(anchor="w")
        ttk.Entry(find_panel, textvariable=variables[name]).pack(fill="x", pady=(0, 8))
    affix_panel = ttk.Frame(panel_host, style="MyKr.TFrame")
    panels["affix"] = affix_panel
    for label, name in (("Prefix", "prefix"), ("Suffix", "suffix")):
        ttk.Label(affix_panel, text=label, style="MyKr.Muted.TLabel").pack(anchor="w")
        ttk.Entry(affix_panel, textvariable=variables[name]).pack(fill="x", pady=(0, 8))
    number_panel = ttk.Frame(panel_host, style="MyKr.TFrame")
    panels["number"] = number_panel
    ttk.Checkbutton(number_panel, text="Add numbering", variable=variables["numbering_enabled"], command=update_rules).pack(anchor="w")
    ttk.Combobox(number_panel, textvariable=variables["numbering_position"], values=("prefix", "suffix"), state="readonly").pack(fill="x", pady=(5, 6))
    for label, name in (("Start", "numbering_start"), ("Step", "numbering_step"), ("Zero fill", "numbering_width"), ("Separator", "numbering_separator")):
        ttk.Label(number_panel, text=label, style="MyKr.Muted.TLabel").pack(anchor="w")
        ttk.Entry(number_panel, textvariable=variables[name]).pack(fill="x", pady=(0, 5))
    for variable in variables.values():
        if hasattr(variable, "trace_add"):
            variable.trace_add("write", update_rules)
    show_panel("find")

    order_buttons = ttk.Frame(rules_panel, style="MyKr.TFrame")
    order_buttons.pack(fill="x", pady=(10, 0))
    ttk.Label(order_buttons, text="Order", style="MyKr.Muted.TLabel").pack(anchor="w")
    ttk.Button(order_buttons, text="Name ascending", style="MyKr.TButton", command=lambda: (state.sort_by_name(), refresh_rows())).pack(fill="x", pady=(3, 0))
    ttk.Button(order_buttons, text="Name descending", style="MyKr.TButton", command=lambda: (state.sort_by_name(True), refresh_rows())).pack(fill="x", pady=2)
    ttk.Button(order_buttons, text="Restore initial order", style="MyKr.TButton", command=lambda: (state.restore_order(), refresh_rows())).pack(fill="x")

    def apply_action() -> None:
        try:
            result = apply_rename(state.plan, database, logger)
        except Exception as exc:
            if logger:
                logger.exception("rename gui apply failed: %s", exc)
            messagebox.showerror("MyKr-ops Rename", str(exc), parent=root)
            return
        state.complete(result)
        if result.failed:
            messagebox.showerror("MyKr-ops Rename", result.message, parent=root)
            return
        refresh_rows()
        summary_label.configure(text=f"Renamed {result.renamed_count} items. You can undo this batch now.")
        apply_button.pack_forget()
        undo_button.pack(side="right")

    def undo_action() -> None:
        try:
            result = undo_latest_rename(database, logger)
        except Exception as exc:
            if logger:
                logger.exception("rename gui undo failed: %s", exc)
            messagebox.showerror("MyKr-ops Rename", str(exc), parent=root)
            return
        messagebox.showinfo("MyKr-ops Rename", result.message, parent=root)
        if not result.failed:
            undo_button.configure(state="disabled")

    apply_button.configure(command=apply_action)
    undo_button.configure(command=undo_action)
    canvas.bind("<Configure>", lambda event: canvas.itemconfigure(list_window, width=event.width))
    root.bind_all("<MouseWheel>", lambda event: canvas.yview_scroll(int(-event.delta / 120), "units"))
    refresh_rows()
    root.mainloop()
