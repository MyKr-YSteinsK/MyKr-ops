# MyKr-ops

MyKr-ops is a deterministic, local-first Windows automation toolkit. It currently provides a safe study-note organizer and an explicit batch rename tool for files and folders you select.

Maintainer-facing project intent, rationale, current state, and supporting-document ownership live in [docs/project/](docs/project/). This README remains the user and operations reference.

## Requirements and installation

Use Windows 10 or 11 and Python 3.12 or newer. The Rename GUI also needs a normal Windows Python installation with Tcl/Tk included; the command-line Notes/Rename operations do not need a third-party runtime dependency. A desktop session can verify the GUI prerequisite with:

```powershell
py -c "import tkinter as tk; root = tk.Tk(); root.destroy()"
```

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

## New computer and switching machines

A clone contains the code, tests, packaging metadata, command wrappers, and active project documentation. The virtual environment, editor settings, caches, local configuration, SQLite history, logs, locks, and disposable test data are intentionally machine-local and are not part of the repository.

### Fresh setup

Install Git and Python 3.12 or newer on Windows, then run:

```powershell
git clone https://github.com/MyKr-YSteinsK/MyKr-ops.git
Set-Location MyKr-ops
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m mykr_ops --help
```

The package has no runtime dependencies. The optional `dev` extra installs pytest for verification. If the editable install reports a transient package-download error, rerun the same command; no project-local package cache is required. A Python distribution without Tcl/Tk can still run the CLI but cannot provide the Rename GUI or its widget-level tests.

### What must be restored separately

- The real Notes files and folders under `D:\Downloads` and `D:\Study` (or the configured roots) are user data, not project files. Sync or back them up with the user's chosen file-storage method; MyKr-ops does not sync them.
- An optional Notes configuration belongs at `%LOCALAPPDATA%\mykr-ops\config.toml` (fallback `%USERPROFILE%\.mykr-ops\config.toml`). Start from the tracked `config.example.toml` and copy/edit it only when the new computer uses different roots. Never commit a personal config containing machine-specific paths.
- `%LOCALAPPDATA%\mykr-ops\mykr-ops.db` (fallback `%USERPROFILE%\.mykr-ops\mykr-ops.db`) is private operation history used by History and Undo. It is not needed to run a fresh checkout. To preserve history, close MyKr-ops first and make a separate backup of the database; restore it only when the configured roots and recorded paths still make sense. Do not merge or concurrently sync databases from two computers, and do not restore one blindly over an unresolved `recovery_required` state. Logs and the lock file are not required for recovery and should not be copied.
- The Explorer Send To shortcut is per-user Windows state, not Git content. After installing the package on each computer, run `mykr-ops rename install-sendto` when that integration is wanted.

### Switching between two computers

Before leaving the computer that was used for development, finish the intended source/documentation change and push it. Review the files before adding them so local configuration, databases, logs, caches, and secrets are never staged. On the other computer, update the checkout before doing new work:

```powershell
git status --short --branch
git pull --ff-only
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

Rerun the install when `pyproject.toml` or the development dependencies change. Keep one computer as the active writer for a simple personal workflow: pull before editing and push after finishing. If both computers have local commits, stop instead of force-pushing; reconcile the branches deliberately, then update the other checkout with `git pull --ff-only`. A non-fast-forward or dirty-worktree error is a synchronization warning, not a reason to discard local work.

When starting a later Plan on the new computer, give the new Plan's objective and acceptance criteria explicitly and ask Codex to read `AGENTS.md`, `README.md`, `docs/project/PROJECT_BRIEF.md`, `docs/project/DECISIONS.md`, `docs/project/CURRENT_STATE.md`, and `docs/project/SUPPORTING_DOCS_MANIFEST.md` first. Those files plus the current Git checkout are the continuity boundary; ignored local state and old `.handoff` material are not. A suitable starting instruction is:

```text
在当前 checkout 继续执行 Plan-4。先检查 git status，并读取 AGENTS.md、README.md、docs/project/PROJECT_BRIEF.md、docs/project/DECISIONS.md、docs/project/CURRENT_STATE.md、docs/project/SUPPORTING_DOCS_MANIFEST.md；以当前 Git、代码、测试和 CI 为事实，明确本 Plan 的目标与验收标准，不把旧 .handoff/计划当执行契约。完成后按 AGENTS.md 做相关验证、审查，并报告 commit/push 与工作树状态。
```

## Study notes

By default, `mykr-ops` reads direct files from `D:\Downloads` and places notes under `D:\Study`. `D:\Study` must already exist. An optional `%LOCALAPPDATA%\mykr-ops\config.toml` may contain only:

```toml
[notes]
source_dir = "D:/Downloads"
study_root = "D:/Study"
```

Copy `config.example.toml` to that location only when different roots are needed.

Valid note filenames have this form:

```text
01-Topic_CS_MachineLearning.md
08-Python_notes_with_underscores_CS_DataProcessing.md
```

The sequence is `01` through `99`, the extension is exactly lowercase `.md`, and the final two underscores separate the first-level directory and course. The topic may contain underscores. Directory and course names cannot contain underscores, Windows-invalid characters, reserved device names, leading/trailing whitespace, or trailing periods. For example, `1-Topic_CS_Course.md`, `01-Topic_CS_CON.md`, and `01-Topic_CS_Course.MD` are invalid or ignored.

Preview is the default and changes nothing:

```powershell
mykr-ops notes
```

Apply is explicit:

```powershell
mykr-ops notes --apply
```

The organizer never overwrites an existing target. An identical target is reported as a duplicate and both files remain. Different content, a target directory, or competing source files are reported as conflicts and remain untouched.
Each move must stay on the same filesystem volume; cross-volume moves fail safely and leave the source unchanged.
Destination directories are verified beneath the fixed configured study root before a move. Only direct ordinary lowercase `.md` files are considered; subdirectories, symbolic links, junctions, and other reparse points are left untouched.

Undo the latest eligible apply run, or inspect recorded history:

```powershell
mykr-ops undo
mykr-ops history
mykr-ops history --run 12
```

Before an apply or undo move, MyKr-ops commits a prepared operation record and holds an exclusive local mutation lock. Directory creation uses the same durable prepare-and-verify pattern. If a previous command was interrupted, the next apply or undo first reconciles recorded files and directory intents without creating or deleting anything. Confirmed completed moves and directories are restored to normal history; ambiguous states are marked `recovery_required`, leave paths untouched, and block new file changes until you manually resolve the filesystem state and rerun the command. `history --run` shows these states and a stable `error_type` for failed filesystem operations.

Apply and undo records are stored in `%LOCALAPPDATA%\mykr-ops\mykr-ops.db` (or `%USERPROFILE%\.mykr-ops\mykr-ops.db` when `LOCALAPPDATA` is unavailable). Logs are written alongside it as `mykr-ops.log`. The included `scripts\preview-notes.cmd` and `scripts\apply-notes.cmd` wrappers are suitable for double-click use and stay open after printing results.

## Batch Rename

MyKr-ops Rename works only on the files and folders you explicitly select. Select entries from one ordinary parent directory, then open the GUI:

```powershell
mykr-ops rename gui "D:\Examples\draft report.txt" "D:\Examples\draft folder"
```

The GUI is in Simplified Chinese. Its **常规重命名** mode supports literal find/replace, prefix, and suffix. Its **连续编号** mode independently generates names such as `01.jpg`, `02.jpg`, or `EP-001-1080P.mkv`; the current displayed order determines the number sequence. Sort and drag ordering are available, and a per-item manual name always takes precedence until you choose **恢复自动**. File extensions are locked; folders use their full name. It accepts files, folders, mixed selections, Unicode, spaces, and other normal Windows filename characters, but rejects reparse points, different parent directories, invalid names, reserved device names, extension changes, and occupied targets. For 201–500 entries the GUI uses a reduced presentation, and above 500 it uses a minimal presentation intended for planning and preview. There is no hard item-count Apply ban: Apply is enabled when the current plan is valid, safe, and contains a change.

The preview is side-effect free. Apply is enabled only when there is at least one change and every selected target is safe. After a successful apply, the window refreshes to the actual new names with fresh rules so you can continue with another round; **撤销本次** always targets only that latest round. The operation revalidates each selected object and parent directory immediately before it starts, never overwrites an existing entry, and uses temporary same-directory names so swaps, cycles, and case-only renames are safe. If any step fails, MyKr-ops attempts to restore the entire batch; an ambiguous interruption is recorded as `recovery_required` and blocks later file mutations rather than guessing.

Undo only the latest eligible rename batch:

```powershell
mykr-ops rename undo
```

Undo verifies the complete batch before changing anything and does not overwrite newer files. The top-level `mykr-ops undo` command remains the study-notes undo command. `mykr-ops history` includes both modules; `mykr-ops history --run N` shows logical rename records without exposing internal temporary names.

To add the per-user Explorer **Send To** entry, run:

```powershell
mykr-ops rename install-sendto
```

This creates (or safely updates) the owned `MyKr-ops Rename` shortcut in the Windows SendTo known folder. It starts the Rename GUI without an extra console and passes the selected Explorer paths directly; no registry entry, shell extension, administrator permission, or pywin32 dependency is required. MyKr-ops refuses to replace or remove a Send To entry unless it can prove the entry belongs to it. To remove the owned entry:

```powershell
mykr-ops rename uninstall-sendto
```

Rename records share the same `%LOCALAPPDATA%\mykr-ops\mykr-ops.db` database, mutation lock, and `mykr-ops.log` location as study notes, but are stored separately from note move records.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest
```
