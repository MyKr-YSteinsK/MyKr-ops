# MyKr-ops Current State

Last reconciled: 2026-08-26.

## Repository identity

- Repository: MyKr-YSteinsK/MyKr-ops
- Local path: D:/CS/MyKr-ops
- Branch: main
- Upstream: origin/main
- Adoption starting HEAD: ad8b00e8a1fde5ae2d275fd4c263525707e70e16 (fix: allow rename planning with explorer-held directories)
- Adoption resulting HEAD: the single migration commit reported in the formal TASK_RESULT; this document intentionally uses the delivery record rather than a self-referential commit hash.
- Worktree: clean after the adoption change set.

## Package and entry points

- Package version: 0.1.0, owned by pyproject.toml.
- Python requirement: >=3.12; the adoption verification ran on Windows with Python 3.14.6.
- Console entry point: mykr-ops = mykr_ops.cli:main.
- GUI entry point: mykr-ops-rename = mykr_ops.rename_launcher:main.
- Module entry: python -m mykr_ops routes to the CLI.
- Runtime dependencies: none; pytest is development-only.
- Release/deployment model: none formal; no version bump, tag, or deployment was created by this adoption.

## Implemented capabilities

- Notes: direct ordinary lowercase .md recognition, right-split filename parsing, safe destination planning, duplicate/conflict classification, explicit apply, durable history, interruption reconciliation, and latest eligible undo.
- Rename: explicit same-parent file/folder selection, transform and independent numbering modes, locked file extensions, row-order numbering, sorting/drag reorder, manual override precedence, inline validation, two-stage same-parent staging, batch-aware rollback/recovery, exact GUI Undo, latest CLI Rename Undo, and no-overwrite identity checks.
- Explorer integration: owned per-user Send To shortcut with the dedicated GUI launcher; install/uninstall ownership checks are fail-safe.
- Persistence: SQLite schema v4 with separate Notes operations and rename_items, shared mutation lock, per-user DB/log/config state.

## Architecture hotspots

- src/mykr_ops/notes.py: Notes protocol, planning, apply/undo, directory intents, and recovery.
- src/mykr_ops/rename.py: Rename plan/rules, identity validation, staging, rollback/recovery, and run-specific undo.
- src/mykr_ops/rename_gui.py: Tkinter presentation/state binding, debounce flushing, manual reset, ordering, fresh rebase, and exact GUI Undo.
- src/mykr_ops/filesystem.py: ordinary-object checks, containment, identity, verified roots, and Windows handle-relative mutation primitives.
- src/mykr_ops/database.py: schema v4, migrations, durable runs/items, indexes, lock, and recovery blockers.
- src/mykr_ops/sendto.py / rename_launcher.py: Windows Send To ownership and GUI launch path.
- tests/: temporary-filesystem coverage for parsing, planning, execution, recovery, undo, GUI state, CLI, migration, and Send To behavior.

## Adoption verification

The migration verification was run against the post-adoption source/documentation tree:

- .\.venv\Scripts\python.exe -m compileall src — PASS.
- .\.venv\Scripts\python.exe -m pytest — PASS: 185 passed, 6 skipped, 191 collected.
- git diff --check — PASS.
- Documentation/metadata review — PASS: product source, tests, pyproject.toml, workflow, config contract, schema, and version were not intentionally changed.
- Ownership/private-boundary review — PASS: canonical owners are distinct; legacy handoffs are not tracked or active; no database, log, Send To shortcut, virtual environment, or other private/generated artifact is part of the change set.

## Known limitations and evidence gaps

Automated tests do not replace the following real-user checks:

1. Explorer Send To end-to-end with an open parent directory, single/multiple files and folders, Chinese names, spaces, common special characters, and exact argv fidelity.
2. Real Tk continuity for reorder/sort plus numbering, immediate Apply after edits, manual override persistence, invalid-rule recovery, multi-round Apply/Undo, focus/keyboard behavior, and large-list presentation.
3. Compatibility with real historical user SQLite rows or recovery_required state; no live database was read or changed during adoption.
4. Notes wrapper convenience in the user's normal installed/PATH environment.

These are pending USER CHECK items, not claims of automated pass.

## Migration status

Canonical ownership is adopted. The root AGENTS.md is repo-specific, the three project-state documents and supporting manifest are present, README pointers/contracts are reconciled, and legacy .handoff plans have been deactivated as historical material and archived outside the repository. This adoption did not intentionally change product source behavior, UI behavior, business logic, schema, entry-point semantics, file-safety semantics, version, or release behavior.
