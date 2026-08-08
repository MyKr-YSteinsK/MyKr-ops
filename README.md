# MyKr-ops

MyKr-ops is a deterministic, local-first Windows automation toolkit. It currently provides a safe study-note organizer and an explicit batch rename tool for files and folders you select.

## Requirements and installation

Use Windows 10 or 11 and Python 3.12 or newer.

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
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

It supports literal find/replace, prefix, suffix, numbering, sort, drag ordering, and per-item manual names. File extensions are locked; folders use their full name. It accepts files, folders, mixed selections, Unicode, spaces, and other normal Windows filename characters, but rejects reparse points, different parent directories, invalid names, reserved device names, extension changes, and occupied targets. More than 500 entries use a reduced UI mode intended for planning and preview.

The preview is side-effect free. Apply is enabled only when there is at least one change and every selected target is safe. The operation revalidates each selected object and parent directory immediately before it starts, never overwrites an existing entry, and uses temporary same-directory names so swaps, cycles, and case-only renames are safe. If any step fails, MyKr-ops attempts to restore the entire batch; an ambiguous interruption is recorded as `recovery_required` and blocks later file mutations rather than guessing.

Undo only the latest eligible rename batch:

```powershell
mykr-ops rename undo
```

Undo verifies the complete batch before changing anything and does not overwrite newer files. The top-level `mykr-ops undo` command remains the study-notes undo command. `mykr-ops history` includes both modules; `mykr-ops history --run N` shows logical rename records without exposing internal temporary names.

To add the per-user Explorer **Send To** entry, run:

```powershell
mykr-ops rename install-sendto
```

This creates (or safely updates) the owned `MyKr-ops Rename` shortcut in the Windows SendTo known folder. It targets the current environment's `pythonw.exe`, so launching the GUI does not open an extra console. It uses the selected Explorer paths directly; no registry entry, shell extension, administrator permission, or pywin32 dependency is required. MyKr-ops refuses to replace or remove a Send To entry unless it can prove the entry belongs to it. To remove the owned entry:

```powershell
mykr-ops rename uninstall-sendto
```

Rename records share the same `%LOCALAPPDATA%\mykr-ops\mykr-ops.db` database, mutation lock, and `mykr-ops.log` location as study notes, but are stored separately from note move records.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest
```
