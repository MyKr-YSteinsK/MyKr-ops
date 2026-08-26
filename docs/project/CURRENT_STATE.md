# MyKr-ops Current State

Last reconciled: 2026-08-27.

## Repository identity

- Repository: MyKr-YSteinsK/MyKr-ops
- Local path: D:/CS/MyKr-ops
- Branch: main
- Upstream: origin/main
- Adoption starting HEAD: ad8b00e8a1fde5ae2d275fd4c263525707e70e16 (fix: allow rename planning with explorer-held directories)
- Adoption resulting HEAD: the single migration commit reported in the formal TASK_RESULT; this document intentionally uses the delivery record rather than a self-referential commit hash.
- Migration Checkpoint cleanup starting HEAD: 80ff36c52119715ff401e4620e26a8ef3ff9c534 (chore: adopt canonical project ownership)
- Migration Checkpoint cleanup resulting HEAD: the formal TASK_RESULT delivery record; this document intentionally uses the delivery record rather than a self-referential commit hash.
- Worktree: clean after the adoption and Migration Checkpoint cleanup change sets.

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

## OPS-Plan02 acceptance evidence (historical / residual risk)

- Disposable fixture setup — PASS: `D:/CS/temp/MyKrops/OPS-Plan02-Rename-acceptance-20260826` contains a small six-item matrix, a 220-item reduced-list fixture, and a 501-item minimal-list fixture. The fixture contains only harmless dummy files and folders.
- Explorer setup — PASS: a real Explorer window was opened on the disposable `small-matrix` parent with the representative Chinese/special-character file visibly selected while the parent remained open.
- Explorer Send To and real Tk workflow — BLOCKED — desktop control reported active user input in another ChatGPT window before classic-context-menu navigation and the dedicated launcher could be exercised. No reliable Send To launch, argv, Tk interaction, Apply, or Undo result is claimed.
- Live mutation scenarios — NOT RUN: no Apply, Undo, rename, delete, Send To install/uninstall, real user database access, Notes-root access, or valuable/unrelated file change occurred.
- Classification — the blocked/unrun scenarios remain historical evidence and non-blocking residual real-world risk. They are not an active acceptance debt for ordinary development, and no scenario was changed to PASS or inferred from automated evidence.
- Future handling — do not schedule a dedicated Explorer/Tk acceptance campaign by default. Treat normal user operation as the feedback surface; a reproducible real bug or explicit high-risk requirement can open a focused task or blocking USER CHECK.

## Migration Checkpoint cleanup verification

- Canonical documentation text and ownership checks — PASS: durable UX, bidirectional maturity, implementation-evidence ownership, absent-specification, and superseded-direction boundaries are recorded in their canonical owners.
- Change-boundary review — PASS: only docs/project canonical documents changed; src/, tests/, pyproject.toml, workflow, config contract, schema, version, and entry points were untouched.
- git diff --check — PASS.

## Known limitations and residual real-world risks

The following scenarios were not proven by automated evidence and remain residual risks, not standing blockers for ordinary development:

1. Explorer Send To end-to-end with an open parent directory, single/multiple files and folders, Chinese names, spaces, common special characters, and exact argv fidelity.
2. Real Tk continuity for reorder/sort plus numbering, immediate Apply after edits, manual override persistence, invalid-rule recovery, multi-round Apply/Undo, focus/keyboard behavior, and large-list presentation.
3. Compatibility with real historical user SQLite rows or recovery_required state; no live database was read or changed during adoption. Verify this only when a future schema/data task requires it.
4. Notes wrapper convenience in the user's normal installed/PATH environment; address this only if real user friction appears.

They remain `NOT RUN` where applicable, are not claims of automated pass, and are upgraded to blocking USER CHECK only under D13's explicit conditions.

## Migration status

Canonical ownership is adopted. The post-migration framework now uses safe automatic commit/push delivery for completed Plans and treats high-cost Explorer/Tk checks as non-blocking residual risk by default while preserving honest evidence and all product safety invariants. The earlier Migration Checkpoint cleanup restored the durable Rename UX baseline and separated narrative documents from implementation/CI evidence while recording currently absent specifications. The root AGENTS.md is repo-specific, the three project-state documents and supporting manifest are present, README pointers/contracts are reconciled, and legacy .handoff plans have been deactivated as historical material and archived outside the repository. This policy adjustment did not intentionally change product source behavior, UI behavior, business logic, schema, entry-point semantics, file-safety semantics, version, or release behavior. Normal feature/fix Plans may proceed with relevant automated/review/smoke evidence; real-world interaction issues are reopened from concrete user feedback or explicit high-risk requirements.
