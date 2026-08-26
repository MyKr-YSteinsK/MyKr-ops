# MyKr-ops repository contract

This file is the only active repository-specific instruction owner for MyKr-ops. The generic development workflow, verification guidance, and artifact contracts come from the active Codex skills; do not duplicate them here.

## Product boundary

MyKr-ops is a single-user, Windows-first, local-first personal automation toolkit. Its baseline behavior is deterministic and does not require AI, a network service, cloud synchronization, or a background process. The current enabled capabilities are:

- Study Note Organizer;
- Batch Rename for explicitly selected files and folders;
- the Rename Explorer Send To integration.

Keep the application as one Python CLI/package with small modules and standard-library runtime dependencies. Do not introduce account systems, cloud sync, AI runtime dependencies, GUI frameworks, watchers, services, plugin/workflow platforms, or cross-platform abstractions unless a separately authorized product requirement changes this boundary.

## Ownership and source of truth

Use the following ownership boundaries:

- AGENTS.md: this repo-specific safety, platform, command, state, and release contract;
- docs/project/PROJECT_BRIEF.md: stable product identity, scope, and long-lived invariants;
- docs/project/DECISIONS.md: durable rationale and supersession relationships;
- docs/project/CURRENT_STATE.md: mutable implementation facts, verification evidence, limitations, and pending user checks;
- docs/project/SUPPORTING_DOCS_MANIFEST.md: the active supporting-document map;
- README.md: user and operations reference only;
- config.example.toml: the minimal Notes configuration reference;
- pyproject.toml: package metadata, Python requirement, version, and entry-point owner;
- .github/workflows/test.yml: the repository CI verification definition;
- source code, tests, and the SQLite schema/migration code: implementation behavior and compatibility truth.

Plans, handoffs, ChatGPT/Codex exports, audit snapshots, generated files, editor metadata, and runtime state are not project-state owners. Any .handoff material that remains on a working machine is historical only, ignored by Git, and must not be treated as an execution contract.

Do not copy a temporary plan wholesale into permanent documentation. Extract only durable rationale that remains supported by the current implementation and tests.

## Active command contract

### Notes

- mykr-ops notes is side-effect-free preview; mykr-ops notes --apply is the explicit mutation command.
- mykr-ops undo is the Notes undo command. mykr-ops history and mykr-ops history --run N inspect recorded runs.
- Notes inspect only direct ordinary lowercase .md files in the configured source directory. The filename contract uses sequence 01–99 and splits the final two underscores into first-level directory and course; the topic may contain underscores.
- Defaults are D:/Downloads and D:/Study. An optional per-user %LOCALAPPDATA%\mykr-ops\config.toml (fallback %USERPROFILE%\.mykr-ops\config.toml) may provide only [notes].source_dir and [notes].study_root.
- The study root must already be an ordinary readable directory. Only its first-level and course child directories may be created. Duplicate, conflict, invalid, failed, and ignored items remain untouched and are reported.

### Rename

- mykr-ops rename gui PATH [PATH ...] operates only on the explicitly selected ordinary files/folders from one ordinary parent directory. It is non-recursive and never moves across directories.
- mykr-ops rename undo undoes the latest eligible Rename apply batch. The GUI action 撤销本次 is bound to the exact apply run completed by that window.
- mykr-ops rename install-sendto and mykr-ops rename uninstall-sendto manage only the per-user, owned MyKr-ops Rename Send To entry. The shortcut uses the dedicated mykr-ops-rename GUI launcher; it is not a shell extension or a universal launcher.
- Rename has transform and independent numbering modes. Numbering follows the current displayed row order. File extensions are locked, folders edit their complete name, and a manual stem overrides automatic rules until restored or cleared. Sorting and drag reordering change order without erasing manual overrides.
- Apply revalidates the current plan and, after success, rebases the GUI on actual paths so another round can be edited. There is no permanent post-Apply freeze.
- There is no hard item-count Apply ban. The GUI uses normal presentation through 200 items, reduced presentation for 201–500, and minimal presentation above 500; plan validity and filesystem safety determine whether Apply is enabled.

## File-safety invariants

These invariants are product behavior, not optional implementation preferences:

- Preview must not create directories, mutate files, write actual operation records, create Send To entries, or hash large files unnecessarily.
- Every mutation requires an explicit Apply/Undo path and revalidates source, destination, parent, object identity, and containment immediately before mutation. Never overwrite an existing file, folder, or other filesystem entry.
- Ordinary-object checks must reject symbolic links, junctions, and other reparse points. Ambiguous case-insensitive matches, invalid Windows names, occupied destinations, and unsupported filesystem states are conflicts or failures, not guesses.
- Notes moves stay inside the configured roots, preserve source content, and verify size/mtime/content before and after the move. Cross-volume moves fail safely. Undo never overwrites a newer or unrelated object.
- Rename stays inside one verified parent. Its Windows mutation path is handle-relative, binds the source and verified parent identities, uses ReplaceIfExists = False, and verifies the resulting identity. The portable fallback exists only for non-Windows temporary-directory tests; do not replace the Windows path with an absolute-path or copy/delete fallback.
- Filesystem inspection and mutation access are separate concerns: path/lstat/scandir/hash inspection may establish a plan, but the mutation boundary must reopen and verify the current handles/identities. Do not trust a stale preview snapshot.
- Rename changed items use unique same-parent temporary names before finalization, so swaps, cycles, and case-only renames remain no-overwrite operations. A failed batch is rolled back when it can be proven safe.
- Durable prepared/transitional operations are reconciled under the shared local mutation lock. A missing, ambiguous, externally occupied, or identity-inconsistent state becomes recovery_required, leaves paths untouched, and blocks later MyKr-ops mutations until manually resolved. Do not weaken validation to make a test or command pass.
- Recovery is module-aware: Notes records and Rename records remain separate, and unresolved work in either module is a mutation blocker. SQLite schema version 4 and existing history must remain compatible unless a separately authorized data migration is required.

## State, privacy, and release boundary

Runtime state belongs outside the repository:

- %LOCALAPPDATA%\mykr-ops\mykr-ops.db, mykr-ops.log, and mykr-ops.lock;
- fallback %USERPROFILE%\.mykr-ops\ when LOCALAPPDATA is unavailable;
- optional user configuration at the same per-user location.

Do not inspect, rewrite, migrate, clean, or use real user databases, logs, roots, Send To entries, or files as proof of a documentation/framework change. Tests use temporary directories and must not touch D:/Downloads, D:/Study, or live user state.

pyproject.toml owns package metadata and the current version is 0.1.0. The package entry points are mykr-ops and mykr-ops-rename; python -m mykr_ops is the thin module CLI entry. There is currently no formal release, deployment, or tag model. Do not bump the version or create release/deployment machinery without a direct requirement.

## Delivery policy

For a formally completed Plan, after required validation passes, the final diff has been reviewed, and no safety or scope conflict remains, Codex defaults to one focused commit and a push to the current branch's configured upstream. A separate user request to commit or push is not required. `TASK_RESULT` must report the branch, starting HEAD, resulting HEAD, commit status and message, push status, local/remote synchronization, and worktree status.

This policy never permits force push or history rewriting. Do not mix unrelated changes, generated/private/runtime data, secrets, or unknown user data into the commit; do not bypass a failed required check or overwrite unknown remote history. Stop and report a delivery blocker when validation fails, the branch or upstream is unclear, unrelated changes cannot be safely separated, force push would be required, authentication/network/remote conflict prevents a safe push, or the change may contain secrets or private user data.

## Evidence and stop boundaries

pytest, compileall, Windows CI, and targeted tests prove only the behavior they actually cover; never describe an unrun Explorer/Tk interaction as PASS. Explorer/Tk and other high-cost desktop acceptance are non-blocking by default for ordinary development. Do not repeatedly launch a Computer Use or manual matrix campaign merely to close a historical check. Ordinary Plans use relevant automated tests, diff/code review, and necessary low-cost technical smoke; normal user operation is a primary surface for finding real interaction problems, which then become a focused fix task when reproducible.

Upgrade a desktop scenario to a blocking USER CHECK only when the user explicitly requires it, a high-risk irreversible data/migration/release action cannot be adequately proven automatically, the task itself is interactive/device/environment acceptance, or a confirmed root cause can only be validated in the real environment. Lowering this manual-acceptance gate never lowers file-safety, no-overwrite, identity, containment, recovery, or Undo invariants.

Stop and report instead of guessing when work would require:

- touching live user data or installing/removing a real user Send To shortcut;
- resolving an ambiguous recovery state, reparse point, identity, containment, or no-overwrite condition by weakening the safety model;
- destructive migration or an unrecognized database schema;
- unsupported filesystem semantics or a required cross-directory rename;
- changing a durable decision without reconciling docs/project/DECISIONS.md and current evidence;
- reactivating an archived plan, adding a feature/fix/refactor under a documentation migration, or inventing a release/deployment model.
