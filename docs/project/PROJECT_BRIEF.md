# MyKr-ops Project Brief

## Product identity

MyKr-ops is a single-user, Windows-first, local-first personal automation toolkit for small, explicit file workflows. It favors deterministic behavior, visible plans, and recoverable mutations over broad automation or hidden intelligence.

The current lifecycle classification is Stabilization. The enabled modules are:

1. Study Note Organizer;
2. Batch Rename;
3. Rename's per-user Explorer Send To integration.

## Stable architecture boundary

The product is one Python package and CLI (mykr_ops) with a dedicated Tkinter Rename GUI. Runtime behavior uses the Python standard library plus the development-only pytest dependency. SQLite stores durable operation state; per-user state remains outside the repository. The package does not require a network service, cloud account, AI runtime, background watcher, plugin framework, or universal GUI shell.

Notes and Rename are separate modules with separate planning and operation semantics. They share only deliberately small filesystem, configuration, database, logging, and mutation-lock infrastructure.

## Stable safety invariants

- Preview is side-effect-free and Apply/Undo are explicit.
- File and directory mutations never overwrite existing entries and do not silently delete user files.
- Only ordinary, non-reparse filesystem objects are eligible. Ambiguous case-insensitive matches, invalid Windows names, unsafe containment, identity changes, and unsupported filesystem states are rejected or reported as conflicts/failures.
- Notes operate on direct files under configured roots; Rename operates on explicitly selected objects in one ordinary parent and never performs a cross-directory or recursive operation.
- Windows mutations are revalidated against current object and parent identity. Rename uses verified-parent, handle-relative, no-overwrite operations and temporary same-parent staging for swaps, cycles, and case-only changes.
- Durable interruptions are reconciled conservatively. An ambiguous state becomes recovery_required, leaves paths untouched, and blocks further MyKr-ops mutations until resolved.
- Undo is bounded to the module's supported latest/exact run semantics and never overwrites newer or unrelated user data.

## Canonical UX baseline

Notes preview by default and require --apply. Rename provides a dedicated GUI with transform and independent numbering modes, current-row-order numbering, extension locking, manual override precedence, restore/clear controls, inline validation, explicit Apply, fresh rebase for another round, and safe exact GUI Undo. There is no hard item-count Apply limit; large lists use reduced or minimal presentation modes.

## Stable non-goals

MyKr-ops is not a generic automation platform. Account systems, cloud synchronization, network APIs, background services/watchers, AI-first control, OCR/metadata classification, recursive or cross-directory rename, automatic conflict renaming/deletion, arbitrary multi-level rollback, shell extensions, a Windows 11 first-level context-menu integration, and a universal GUI shell are outside the current product boundary.
