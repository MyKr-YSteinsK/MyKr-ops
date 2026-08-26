# MyKr-ops Durable Decisions

This file records decisions that constrain more than one implementation area. The current code, tests, and schema remain the implementation authority; archived plans are historical evidence only.

## Accepted decisions

### D1 — Deterministic local-first behavior

MyKr-ops must remain useful without AI, network access, accounts, or cloud state. AI may assist a future ambiguous workflow only as an optional layer and must not become a hidden dependency for basic file operations.

### D2 — Personal toolbox, not a platform

Keep one small CLI/package with focused modules and standard-library runtime dependencies. Do not generalize Notes/Rename into a plugin, rule, workflow, service, or universal GUI framework before real usage establishes a need.

### D3 — Plan, explicit Apply, and fail-safe recovery

Filesystem workflows preview first and mutate only after an explicit Apply or Undo action. Plans are authoritative only after fresh validation. Prepared/transitional durable records, a shared mutation lock, conservative reconciliation, and recovery_required blocking are preferred to guessing after interruption.

### D4 — Separate Notes and Rename semantics

Notes owns its direct-file filename protocol, configured roots, content-aware duplicate detection, and top-level mykr-ops undo. Rename owns explicitly selected same-parent files/folders, content-independent identity validation, and mykr-ops rename undo. Their history and recovery records must not be silently mixed.

### D5 — Dedicated Rename GUI

Rename has a dedicated Tkinter GUI for the selected work set. It is not a universal GUI shell and does not require a third-party GUI toolkit, thumbnails, or image/media decoding.

### D6 — Explorer Send To with a dedicated launcher

The supported Explorer path is a per-user MyKr-ops Rename Send To shortcut that safely passes selected paths to the dedicated mykr-ops-rename GUI launcher. It uses no administrator permission, registry shell extension, or business logic in a shell script. Ownership must be proven before update or removal.

### D7 — Independent Rename rules and manual precedence

Transform and numbering are independent modes. Numbering is computed from the current displayed order; file extensions remain locked; folders edit their full name. A manual stem is the highest-priority per-item value until restored or cleared, and sorting/reordering does not erase it.

### D8 — Fresh rebase and multi-round editing

After a successful Rename Apply or exact GUI Undo, the GUI rebuilds a fresh plan from actual filesystem paths so the user can continue with another round. The latest result remains bound to the correct GUI Undo action; a successful Apply does not permanently freeze the window.

### D9 — Windows identity and handle-relative mutation boundary

Path inspection is not sufficient authority for a Windows mutation. Rename revalidates object kind, source identity, parent identity, direct-child relationship, reparse status, and destination absence, then performs a verified-parent, handle-relative, no-overwrite rename. Notes similarly revalidates source snapshots and roots. Copy/delete or weak absolute-path fallback must not replace this safety boundary.

### D10 — Batch-aware staging and rollback

Changed Rename items use unique temporary names in the same parent before finalization. Swap, cycle, and case-only operations are legitimate batch states, not external conflicts. Recovery and rollback reason over the complete object-identity-to-location mapping and enter recovery_required for unknown occupants, missing/duplicated objects, or any state that cannot be proven safe.

### D11 — Exact and latest Undo semantics

The GUI action 撤销本次 undoes only the exact apply run completed by that window. The CLI mykr-ops rename undo selects the latest eligible Rename apply run. Notes retains its separate top-level latest eligible undo. All Undo paths validate the complete batch before mutation and never overwrite newer data.

### D12 — External durable state and schema compatibility

Database, log, lock, and optional configuration live under the per-user application-data directory, not in the repository. SQLite schema version 4 preserves historical Notes records while adding module-specific Rename records and indexes. Existing durable state must not be treated as a disposable filesystem index.

### D13 — Honest evidence, non-blocking manual acceptance, and restrained maturity

Automated tests, CI, static/diff review, and low-cost smoke must remain honest: they prove only the behavior they actually cover, and an unrun Explorer/Tk interaction remains `NOT RUN`, never `PASS`. For ordinary development, high-cost desktop acceptance is non-blocking by default. Relevant automated coverage, review, basic smoke, and normal-use feedback together provide the practical maturity evidence; an unrun manual scenario is a non-blocking residual risk, not a standing blocking debt.

Escalate manual acceptance to a blocking USER CHECK only when the user explicitly requires it, a high-risk irreversible data/migration/release action cannot be adequately proven automatically, the task itself is interactive/device/environment acceptance, or a confirmed root cause can only be validated in the real environment. This does not weaken any file-safety, no-overwrite, identity, containment, recovery, or Undo invariant. Once the safety model, regression coverage, and normal use sufficiently support the current requirement, stop speculative hardening: reopen the area only for real user feedback, a Windows behavior issue, a regression, an explicit new requirement, or a reliable UI/integration automation oracle. This avoids both costly over-reliance on focus-stealing manual campaigns and endless theoretical hardening while keeping evidence truthful.

## Superseded directions

The following ideas found in legacy handoffs are not active decisions:

- numbering-position/separator variants beyond the current independent numbering rules with prefix/suffix fields and row-order semantics;
- a permanent post-Apply GUI freeze; current behavior fresh-rebases for another editing round;
- using the package module selector as the product Send To launcher; the current shortcut targets the dedicated GUI launcher, while python -m mykr_ops remains only the thin CLI entry;
- generic platformization, universal GUI shells, plugin registries, and workflow engines;
- schema-v3-only Rename persistence; the current compatibility boundary is schema v4.

No archived plan may reactivate a superseded direction without a new, explicit product decision reconciled against the current code and state.
