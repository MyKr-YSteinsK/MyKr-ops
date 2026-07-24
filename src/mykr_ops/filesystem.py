from __future__ import annotations

import hashlib
import os
import shutil
import stat
import tempfile
from pathlib import Path


FILE_ATTRIBUTE_REPARSE_POINT = 0x0400


class FilesystemSafetyError(RuntimeError):
    """Raised when a filesystem state cannot be handled safely."""


def path_exists_no_follow(path: Path) -> bool:
    return os.path.lexists(path)


def is_reparse_point(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return False
    attributes = getattr(metadata, "st_file_attributes", 0)
    return path.is_symlink() or bool(attributes & FILE_ATTRIBUTE_REPARSE_POINT)


def is_ordinary_file(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return False
    return stat.S_ISREG(metadata.st_mode) and not is_reparse_point(path)


def is_ordinary_directory(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return False
    return stat.S_ISDIR(metadata.st_mode) and not is_reparse_point(path)


def assert_ordinary_directory(path: Path, description: str) -> None:
    if not is_ordinary_directory(path):
        if path_exists_no_follow(path) and is_reparse_point(path):
            raise FilesystemSafetyError(f"{description} is a symbolic link, junction, or reparse point")
        raise FilesystemSafetyError(f"{description} is not an ordinary directory")
    try:
        with os.scandir(path):
            pass
    except OSError as exc:
        raise FilesystemSafetyError(f"{description} cannot be read: {exc}") from exc


def assert_within(path: Path, root: Path) -> None:
    """Prove containment with resolved paths instead of string prefixes."""
    try:
        resolved_root = root.resolve(strict=True)
        resolved_path = path.resolve(strict=False)
        resolved_path.relative_to(resolved_root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise FilesystemSafetyError(f"path escapes configured root: {path}") from exc


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def direct_casefold_matches(parent: Path, name: str) -> list[Path]:
    assert_ordinary_directory(parent, f"directory {parent}")
    try:
        return [child for child in parent.iterdir() if child.name.casefold() == name.casefold()]
    except OSError as exc:
        raise FilesystemSafetyError(f"could not inspect directory {parent}: {exc}") from exc


def resolve_child_directory(parent: Path, name: str, root: Path) -> tuple[Path, bool]:
    """Return a safe direct child directory and whether it must be created."""
    assert_ordinary_directory(parent, f"directory {parent}")
    assert_within(parent, root)
    matches = direct_casefold_matches(parent, name)
    if len(matches) > 1:
        raise FilesystemSafetyError(
            f"multiple case-insensitive matches exist for directory {name!r} in {parent}"
        )
    if matches:
        child = matches[0]
        if is_reparse_point(child):
            raise FilesystemSafetyError(f"required directory {child} is a symbolic link, junction, or reparse point")
        if not is_ordinary_directory(child):
            if is_ordinary_file(child):
                raise FilesystemSafetyError(f"required directory path is occupied by a file: {child}")
            raise FilesystemSafetyError(f"required directory path is not an ordinary directory: {child}")
        assert_within(child, root)
        return child, False

    candidate = parent / name
    assert_within(candidate, root)
    return candidate, True


def create_child_directory(parent: Path, name: str, root: Path) -> tuple[Path, bool]:
    """Create one validated direct child directory without following links."""
    child, needs_creation = resolve_child_directory(parent, name, root)
    if not needs_creation:
        return child, False
    try:
        child.mkdir()
    except FileExistsError:
        # A concurrent actor created something. Reinspect it rather than trusting it.
        return resolve_child_directory(parent, name, root)[0], False
    except OSError as exc:
        raise FilesystemSafetyError(f"could not create directory {child}: {exc}") from exc
    if not is_ordinary_directory(child):
        raise FilesystemSafetyError(f"new directory is not safe to use: {child}")
    assert_within(child, root)
    return child, True


def remove_empty_directory(path: Path, root: Path) -> bool:
    """Remove only an ordinary, empty directory known to be inside its root."""
    assert_within(path, root)
    if not path_exists_no_follow(path):
        return False
    if not is_ordinary_directory(path):
        raise FilesystemSafetyError(f"directory is no longer safe to remove: {path}")
    try:
        path.rmdir()
    except OSError:
        return False
    return True


def snapshot_matches(path: Path, expected_size: int, expected_mtime_ns: int, expected_sha256: str) -> None:
    if not is_ordinary_file(path):
        raise FilesystemSafetyError(f"source is no longer an ordinary file: {path}")
    try:
        metadata = path.stat()
    except OSError as exc:
        raise FilesystemSafetyError(f"could not stat source {path}: {exc}") from exc
    if metadata.st_size != expected_size or metadata.st_mtime_ns != expected_mtime_ns:
        raise FilesystemSafetyError(f"source changed after planning: {path}")
    if sha256_file(path) != expected_sha256:
        raise FilesystemSafetyError(f"source content changed after planning: {path}")


def move_file_without_overwrite(source: Path, destination: Path, expected_sha256: str) -> None:
    """Copy through a private temporary file, claim destination exclusively, then remove source.

    Linking the completed temporary file into place guarantees a competing destination is
    never overwritten. The temporary file is the only file this routine removes directly.
    """
    if not is_ordinary_file(source):
        raise FilesystemSafetyError(f"source is not an ordinary file: {source}")
    assert_ordinary_directory(destination.parent, f"destination directory {destination.parent}")
    if path_exists_no_follow(destination):
        raise FilesystemSafetyError(f"destination already exists: {destination}")

    descriptor: int | None = None
    temporary_path: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".mykr-ops-", suffix=".tmp", dir=destination.parent
        )
        temporary_path = Path(temporary_name)
        with os.fdopen(descriptor, "wb") as temporary_handle, source.open("rb") as source_handle:
            descriptor = None
            shutil.copyfileobj(source_handle, temporary_handle, length=1024 * 1024)
        if sha256_file(temporary_path) != expected_sha256:
            raise FilesystemSafetyError("source changed while preparing destination")
        try:
            os.link(temporary_path, destination)
        except FileExistsError as exc:
            raise FilesystemSafetyError(f"destination already exists: {destination}") from exc
        except OSError as exc:
            raise FilesystemSafetyError(
                f"could not reserve destination without overwriting it: {exc}"
            ) from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary_path is not None and path_exists_no_follow(temporary_path):
            try:
                temporary_path.unlink()
            except OSError:
                pass

    if not is_ordinary_file(destination) or sha256_file(destination) != expected_sha256:
        raise FilesystemSafetyError(f"destination verification failed: {destination}")
    # Recheck immediately before removing the source so a changed source is retained.
    if not is_ordinary_file(source) or sha256_file(source) != expected_sha256:
        raise FilesystemSafetyError(f"source changed before move could finish: {source}")
    try:
        source.unlink()
    except OSError as exc:
        raise FilesystemSafetyError(f"destination is complete but source could not be removed: {exc}") from exc
    if path_exists_no_follow(source):
        raise FilesystemSafetyError(f"source still exists after move: {source}")
