# Supporting Documents Manifest

This manifest names only active references that support the canonical project state. It does not make temporary plans, generated files, or private runtime state authoritative.

| Path | Owner | Scope |
| --- | --- | --- |
| README.md | User / operations reference | Installation, commands, input contracts, safety behavior, state locations, and user-facing Notes/Rename workflows. It points to canonical project state but does not duplicate it. |
| config.example.toml | Notes configuration reference | The minimal supported [notes] keys and default paths. |
| pyproject.toml | Package metadata owner | Package name, version (0.1.0 at this reconciliation), Python requirement, runtime/development dependencies, and CLI/GUI entry points. |
| .github/workflows/test.yml | CI verification owner | Windows/Python test environment and the repository's compile/test commands. |

Implementation behavior and compatibility remain owned by src/, tests/, and the SQLite schema/migration code; they are not replaced by narrative documents.

## Inactive or non-project-state material

- .handoff/*.md files from earlier development phases are ignored historical artifacts. They were archived outside the repository during this migration and have no active authority.
- .venv/, .pytest_cache/, *.egg-info/, .idea/, caches, logs, databases, locks, local configuration, and disposable test data are generated/private/runtime material and are never canonical documentation.
- The supplied migration plan is an external, read-only execution input and is not part of this repository's project state.
