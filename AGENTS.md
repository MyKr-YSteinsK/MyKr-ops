# AGENTS.md

## Project

`mykr-ops` is a Windows-first, local-first personal automation toolkit.

Its baseline behavior must remain deterministic and usable without AI. AI may be added later only as optional assistance for ambiguous recognition or interaction; it must not become a hidden dependency for basic file operations.

The project should grow through small, verified modules driven by real usage. Do not turn it into a generic automation platform prematurely.

## Current priorities

1. Keep file operations safe, explicit, traceable, and reversible where practical.
2. Prefer the smallest implementation that fully satisfies the current task.
3. Preserve deterministic behavior.
4. Avoid unnecessary runtime dependencies, abstractions, services, and permanent documentation.
5. Keep Windows path behavior and filesystem edge cases explicit.

## Scope discipline

Do not expand the active task unless the supplied execution plan explicitly requires it.

Do not add any of the following by default:

- account systems;
- cloud synchronization;
- AI integrations;
- GUI frameworks;
- background services;
- realtime filesystem watchers;
- plugin frameworks;
- workflow engines;
- microservices;
- message queues;
- telemetry or analytics;
- broad refactors;
- unrelated dependency upgrades;
- generic rule engines;
- speculative abstractions for future modules.

When a future requirement is not needed by the current implementation, leave a clear extension point only if it naturally emerges from the current design. Do not create unused interfaces or placeholder modules.

## Source of truth

The permanent source of truth is:

1. current code;
2. automated tests;
3. database migrations or other irreversible evolution files, when they exist;
4. `README.md` and other necessary permanent documentation;
5. Git history;
6. verified runtime behavior.

Temporary plans and handoff files are read-only execution inputs. They are not project state.

Do not:

- edit a supplied temporary plan;
- mark completion status inside it;
- rename or delete it;
- commit it;
- copy the plan wholesale into permanent documentation.

## File operation safety

Any feature that moves, renames, creates, or deletes user files must follow these rules unless the active task explicitly defines stricter behavior:

- preview by default where practical;
- require an explicit apply action before modifying files;
- never overwrite an existing file;
- never silently delete a user file;
- validate source and destination paths immediately before execution;
- verify that computed destination paths remain within the configured root;
- do not follow symbolic links, junctions, or other reparse points unless explicitly required;
- treat ambiguous matches as conflicts rather than guesses;
- detect multiple source files resolving to one destination before execution;
- record successful modifications and meaningful failures;
- continue after isolated file failures when the task permits;
- do not record an operation as successful until the resulting filesystem state is verified;
- undo must never overwrite newer user data;
- automatic directory removal is allowed only for directories created by the recorded operation and still empty.

Do not weaken safety validation to make tests or local execution pass.

## Windows-specific requirements

Assume the primary runtime environment is Windows 10 or Windows 11.

Pay attention to:

- case-insensitive path matching;
- reserved device names;
- invalid filename characters;
- trailing spaces and periods;
- path containment;
- drive-letter behavior;
- locked files;
- symbolic links;
- junctions;
- reparse points;
- long or unusual Unicode names;
- files changing between scan and apply.

Do not determine path containment with raw string-prefix checks.

## Architecture

Prefer simple modules with explicit responsibilities.

For the current project scale:

- use a single CLI application;
- use Python standard-library facilities when sufficient;
- use SQLite directly through `sqlite3`;
- keep module-specific behavior separate from shared filesystem, configuration, and persistence helpers;
- avoid ORM, dependency injection frameworks, plugin registries, and service containers;
- keep business logic testable against temporary directories instead of real user paths.

Add new permanent architecture documentation only when the repository becomes difficult to understand from the code and README alone.

A separate project map is not required at the current project size. Reconsider it only after multiple independent modules and shared infrastructure make navigation genuinely difficult.

## Configuration

Configuration should be minimal and explicit.

- Provide safe defaults when the active requirement defines them.
- Keep user-specific state outside the repository.
- Do not commit runtime databases, logs, secrets, caches, or local configuration.
- Do not invent environment variables or configuration layers without a current need.
- Fail clearly when required roots do not exist or are unsafe.

## Persistence

Use SQLite only for durable state that supports real behavior such as operation history and undo.

- Use parameterized queries.
- Keep transactions focused.
- Record actual execution, not speculative preview plans, unless a requirement explicitly says otherwise.
- Do not treat SQLite as a full index of the user's filesystem.
- Schema changes must preserve existing recorded operations where practical.

## CLI behavior

CLI output should be readable to a non-expert user.

- Show concise summaries.
- Show reasons for duplicate, conflict, invalid, skipped, and failed items.
- Do not print full tracebacks during normal use.
- Write diagnostic tracebacks to logs when helpful.
- Return meaningful non-zero exit codes for command-level failure.
- Do not require interactive prompts when an explicit apply flag already represents confirmation.

Windows `.cmd` scripts may wrap CLI commands, but must not duplicate business logic.

## Testing and verification

Use targeted verification first.

For filesystem features, tests should cover:

- parsing and validation;
- path resolution;
- preview side-effect freedom;
- conflict handling;
- execution behavior;
- partial failure;
- persistence;
- undo safety;
- Windows-specific edge cases that can be represented portably.

Tests must use temporary directories and must not modify the user's real paths.

Do not default to:

- scanning the entire repository;
- running unrelated test suites;
- adding broad integration infrastructure;
- introducing mocks where real temporary filesystem operations are clearer.

Expand verification only when risk justifies it.

## Documentation

Keep permanent documentation small and accurate.

`README.md` should explain:

- project purpose;
- supported environment;
- installation;
- commands;
- current filename or input contracts;
- safety behavior;
- state and log locations;
- testing.

Do not create by default:

- development plans;
- patch logs;
- roadmaps;
- decision logs;
- project maps;
- duplicated architecture documents.

Update permanent documentation only when behavior, setup, or stable architecture changes.

## Change discipline

Before modifying code:

1. inspect only files relevant to the task;
2. understand current behavior and tests;
3. identify unrelated working-tree changes;
4. avoid touching unrelated files.

During implementation:

- keep changes focused;
- do not reformat unrelated code;
- do not rename public commands or persistent fields without need;
- add regression tests for behavior changes;
- stop rather than improvising around unsafe or ambiguous filesystem state.

## Git completion rules

After required verification passes:

1. review the diff and exclude unrelated or generated files;
2. create one focused commit;
3. push the current branch to its configured upstream;
4. report the commit SHA and push result.

Stop and report the blocker when:

- required verification fails;
- secrets or private user data may be included;
- unrelated changes cannot be separated;
- the current branch is ambiguous;
- the remote or upstream is ambiguous;
- push would require force;
- authentication or environment problems prevent a safe push.

Do not use force push.

Use `frugal-dev-runner`. Do not expand scope.
