# Supporting Documents Manifest

This manifest names only active references that support the canonical project state. It does not make temporary plans, generated files, or private runtime state authoritative.

## Active narrative supporting documents

| Path | Owner | Scope |
| --- | --- | --- |
| README.md | User / operations reference | Installation, commands, input contracts, safety behavior, state locations, and user-facing Notes/Rename workflows. It points to canonical project state but does not duplicate it. |
| config.example.toml | Notes configuration reference | The minimal supported [notes] keys and default paths. |

## Implementation, package, and CI evidence owners

These files and areas are implementation or verification evidence, not additional narrative project-state documents:

| Path / area | Owner | Scope |
| --- | --- | --- |
| pyproject.toml | Package metadata owner | Package name, version (0.1.0 at this reconciliation), Python requirement, runtime/development dependencies, and CLI/GUI entry points. |
| .github/workflows/test.yml | CI verification owner | Windows/Python test environment and the repository's compile/test commands. |
| src/ | Implementation behavior owner | Runtime behavior, module boundaries, filesystem safety, and CLI/GUI implementation. |
| tests/ | Verification evidence owner | Automated behavior and regression evidence. |
| SQLite schema/migration code | Persistence compatibility owner | Durable-state shape, migrations, and compatibility behavior. |

## Explicitly absent specifications

The following are not present as independent active specifications:

- PROJECT_MAP / architecture map;
- detailed visual/UX specification;
- independent domain/data-source specification;
- public/API specification;
- release/deployment specification;
- ADR collection;
- CHANGELOG.

Do not create these by template. Add one only when current product complexity or an explicit requirement makes it a useful owner. The durable Rename UX baseline is owned by PROJECT_BRIEF.md and DECISIONS.md; there is no separate UX_CONTRACT.md.

## Inactive or non-project-state material

- .handoff/*.md files from earlier development phases are ignored historical artifacts. They were archived outside the repository during this migration and have no active authority.
- .venv/, .pytest_cache/, *.egg-info/, .idea/, caches, logs, databases, locks, local configuration, and disposable test data are generated/private/runtime material and are never canonical documentation.
- The supplied migration plan is an external, read-only execution input and is not part of this repository's project state.
