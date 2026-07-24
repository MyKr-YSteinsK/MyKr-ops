# MyKr-ops

MyKr-ops is a deterministic, local-first Windows tool for organizing study notes. Phase 1 provides a safe study-note organizer; later modules may be added when real use requires them.

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
Only direct ordinary lowercase `.md` files are considered; subdirectories, symbolic links, junctions, and other reparse points are left untouched.

Undo the latest eligible apply run, or inspect recorded history:

```powershell
mykr-ops undo
mykr-ops history
mykr-ops history --run 12
```

Before an apply or undo move, MyKr-ops commits a prepared operation record and holds an exclusive local mutation lock. If a previous command was interrupted, the next apply or undo first reconciles the recorded source and destination paths using SHA-256. Confirmed completed moves are restored to normal history; ambiguous states are marked `recovery_required`, leave both paths untouched, and block new file changes until you manually resolve the filesystem state and rerun the command. `history` shows these states for inspection.

Apply and undo records are stored in `%LOCALAPPDATA%\mykr-ops\mykr-ops.db` (or `%USERPROFILE%\.mykr-ops\mykr-ops.db` when `LOCALAPPDATA` is unavailable). Logs are written alongside it as `mykr-ops.log`. The included `scripts\preview-notes.cmd` and `scripts\apply-notes.cmd` wrappers are suitable for double-click use and stay open after printing results.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest
```
