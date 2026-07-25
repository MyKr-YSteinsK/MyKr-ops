from __future__ import annotations

import ctypes
import hashlib
import os
import stat
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


FILE_ATTRIBUTE_REPARSE_POINT = 0x0400


class FilesystemSafetyError(RuntimeError):
    """Raised when a filesystem state cannot be handled safely."""

    def __init__(self, message: str, *, error_type: str | None = None) -> None:
        super().__init__(message)
        self.error_type = error_type


@dataclass(frozen=True)
class VerifiedDirectoryRoot:
    """A fixed root directory object retained for one mutation critical section."""

    path: Path
    handle: int
    identity: tuple[int, int, int, int, int]
    final_path: str


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
            raise FilesystemSafetyError(
                f"{description} is a symbolic link, junction, or reparse point",
                error_type="unsafe_reparse_point",
            )
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
        raise FilesystemSafetyError(
            f"path escapes configured root: {path}", error_type="path_escape"
        ) from exc


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def names_equal(left: str, right: str) -> bool:
    """Compare one filename component using the host filesystem's safe rule."""
    if os.name != "nt":
        return left.casefold() == right.casefold()
    result = _compare_string_ordinal(left, len(left), right, len(right), True)
    if result == 0:
        _raise_windows_error("could not compare Windows filenames")
    return result == _CSTR_EQUAL


def direct_casefold_matches(parent: Path, name: str) -> list[Path]:
    assert_ordinary_directory(parent, f"directory {parent}")
    try:
        return [child for child in parent.iterdir() if names_equal(child.name, name)]
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


def create_child_directory(
    parent: Path,
    name: str,
    root: Path,
    *,
    verified_root: VerifiedDirectoryRoot | None = None,
) -> tuple[Path, bool]:
    """Create one validated direct child directory without following links."""
    parent_handle = (
        _open_verified_directory_under_root(verified_root, parent, f"directory {parent}")
        if verified_root is not None
        else None
    )
    try:
        child, needs_creation = resolve_child_directory(parent, name, root)
        if not needs_creation:
            _verify_child_directory(child, parent, name, verified_root)
            return child, False
        try:
            child.mkdir()
        except FileExistsError:
            # A concurrent actor created something. Reinspect it rather than trusting it.
            existing, _ = resolve_child_directory(parent, name, root)
            _verify_child_directory(existing, parent, name, verified_root)
            return existing, False
        except OSError as exc:
            raise FilesystemSafetyError(f"could not create directory {child}: {exc}") from exc
        _verify_child_directory(child, parent, name, verified_root)
        return child, True
    finally:
        if parent_handle is not None:
            _close_handle(parent_handle)


def remove_empty_directory(
    path: Path,
    root: Path,
    *,
    verified_root: VerifiedDirectoryRoot | None = None,
    expected_parent: Path | None = None,
    expected_name: str | None = None,
) -> bool:
    """Remove only an ordinary, empty directory known to be inside its root."""
    assert_within(path, root)
    parent = path.parent if expected_parent is None else expected_parent
    name = path.name if expected_name is None else expected_name
    parent_handle = (
        _open_verified_directory_under_root(verified_root, parent, f"directory {parent}")
        if verified_root is not None
        else None
    )
    try:
        if not path_exists_no_follow(path):
            return False
        if not is_ordinary_directory(path):
            raise FilesystemSafetyError(f"directory is no longer safe to remove: {path}")
        _verify_child_directory(path, parent, name, verified_root)
        try:
            path.rmdir()
        except OSError:
            return False
        return True
    finally:
        if parent_handle is not None:
            _close_handle(parent_handle)


def snapshot_matches(path: Path, expected_size: int, expected_mtime_ns: int, expected_sha256: str) -> None:
    if not is_ordinary_file(path):
        raise FilesystemSafetyError(
            f"source is no longer an ordinary file: {path}", error_type="source_missing"
        )
    try:
        metadata = path.stat()
    except OSError as exc:
        raise FilesystemSafetyError(f"could not stat source {path}: {exc}") from exc
    if metadata.st_size != expected_size or metadata.st_mtime_ns != expected_mtime_ns:
        raise FilesystemSafetyError(f"source changed after planning: {path}", error_type="source_changed")
    if sha256_file(path) != expected_sha256:
        raise FilesystemSafetyError(
            f"source content changed after planning: {path}", error_type="source_changed"
        )


def move_file_without_overwrite(
    source: Path,
    destination: Path,
    expected_sha256: str,
    *,
    expected_size: int | None = None,
    expected_mtime_ns: int | None = None,
    destination_root: VerifiedDirectoryRoot | None = None,
    source_root: VerifiedDirectoryRoot | None = None,
) -> None:
    """Move one file atomically without replacing an existing destination.

    Windows uses a source-file handle with ``NtSetInformationFile`` and an open,
    verified destination-directory handle. Keeping both handles open binds the
    rename to that directory object while the move is checked and committed. The
    non-Windows path exists only for portable test coverage.
    """
    if not is_ordinary_file(source):
        raise FilesystemSafetyError(f"source is not an ordinary file: {source}", error_type="source_missing")
    assert_ordinary_directory(destination.parent, f"destination directory {destination.parent}")
    if path_exists_no_follow(destination):
        raise FilesystemSafetyError(
            f"destination already exists: {destination}", error_type="destination_exists"
        )
    try:
        source_metadata = source.lstat()
    except OSError as exc:
        raise FilesystemSafetyError(f"could not stat source {source}: {exc}") from exc
    source_size = source_metadata.st_size if expected_size is None else expected_size
    source_mtime_ns = source_metadata.st_mtime_ns if expected_mtime_ns is None else expected_mtime_ns

    if os.name == "nt":
        _move_file_windows(
            source,
            destination,
            expected_sha256,
            source_size,
            source_mtime_ns,
            destination_root,
            source_root,
        )
        return
    _move_file_portably(source, destination, expected_sha256, source_size, source_mtime_ns)


def _move_file_portably(
    source: Path,
    destination: Path,
    expected_sha256: str,
    expected_size: int,
    expected_mtime_ns: int,
) -> None:
    """Deterministic test fallback. Windows never uses this hard-link implementation."""
    snapshot_matches(source, expected_size, expected_mtime_ns, expected_sha256)
    try:
        os.link(source, destination)
    except FileExistsError as exc:
        raise FilesystemSafetyError(f"destination already exists: {destination}") from exc
    except OSError as exc:
        raise FilesystemSafetyError(
            f"could not move without overwriting the destination: {exc}"
        ) from exc

    if not is_ordinary_file(destination) or sha256_file(destination) != expected_sha256:
        raise FilesystemSafetyError(f"destination verification failed: {destination}")
    if not is_ordinary_file(source) or sha256_file(source) != expected_sha256:
        raise FilesystemSafetyError(f"source changed before move could finish: {source}")
    try:
        source.unlink()
    except OSError as exc:
        raise FilesystemSafetyError(f"destination is complete but source could not be removed: {exc}") from exc
    if path_exists_no_follow(source):
        raise FilesystemSafetyError(f"source still exists after move: {source}")


if os.name == "nt":
    from ctypes import wintypes

    _GENERIC_READ = 0x80000000
    _DELETE = 0x00010000
    _FILE_SHARE_READ = 0x00000001
    _FILE_SHARE_WRITE = 0x00000002
    _FILE_SHARE_DELETE = 0x00000004
    _FILE_LIST_DIRECTORY = 0x00000001
    _FILE_ADD_FILE = 0x00000002
    _FILE_READ_ATTRIBUTES = 0x00000080
    _SYNCHRONIZE = 0x00100000
    _OPEN_EXISTING = 3
    _FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
    _FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
    _FILE_TYPE_DISK = 0x0001
    _FILE_ATTRIBUTE_DIRECTORY = 0x0010
    _FILE_RENAME_INFORMATION_CLASS = 10
    _VOLUME_NAME_GUID = 0x00000001
    _CSTR_EQUAL = 2
    _ERROR_FILE_EXISTS = 80
    _ERROR_ALREADY_EXISTS = 183
    _ERROR_NOT_SAME_DEVICE = 17
    _ERROR_SHARING_VIOLATION = 32
    _ERROR_LOCK_VIOLATION = 33
    _INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
    _WINDOWS_EPOCH_OFFSET_NS = 11644473600000000000

    class _FileTime(ctypes.Structure):
        _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]

    class _ByHandleFileInformation(ctypes.Structure):
        _fields_ = [
            ("dwFileAttributes", wintypes.DWORD),
            ("ftCreationTime", _FileTime),
            ("ftLastAccessTime", _FileTime),
            ("ftLastWriteTime", _FileTime),
            ("dwVolumeSerialNumber", wintypes.DWORD),
            ("nFileSizeHigh", wintypes.DWORD),
            ("nFileSizeLow", wintypes.DWORD),
            ("nNumberOfLinks", wintypes.DWORD),
            ("nFileIndexHigh", wintypes.DWORD),
            ("nFileIndexLow", wintypes.DWORD),
        ]

    class _FileRenameInformation(ctypes.Structure):
        _fields_ = [
            ("ReplaceIfExists", wintypes.BOOLEAN),
            ("RootDirectory", wintypes.HANDLE),
            ("FileNameLength", wintypes.DWORD),
            ("FileName", wintypes.WCHAR * 1),
        ]

    class _IoStatusBlock(ctypes.Structure):
        _fields_ = [("Status", ctypes.c_long), ("Information", ctypes.c_size_t)]

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _create_file = _kernel32.CreateFileW
    _create_file.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    _create_file.restype = wintypes.HANDLE
    _close_handle = _kernel32.CloseHandle
    _close_handle.argtypes = [wintypes.HANDLE]
    _close_handle.restype = wintypes.BOOL
    _get_file_information = _kernel32.GetFileInformationByHandle
    _get_file_information.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ByHandleFileInformation)]
    _get_file_information.restype = wintypes.BOOL
    _get_file_type = _kernel32.GetFileType
    _get_file_type.argtypes = [wintypes.HANDLE]
    _get_file_type.restype = wintypes.DWORD
    _read_file = _kernel32.ReadFile
    _read_file.argtypes = [
        wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID,
    ]
    _read_file.restype = wintypes.BOOL
    _set_file_pointer = _kernel32.SetFilePointerEx
    _set_file_pointer.argtypes = [
        wintypes.HANDLE, ctypes.c_longlong, ctypes.POINTER(ctypes.c_longlong), wintypes.DWORD,
    ]
    _set_file_pointer.restype = wintypes.BOOL
    _get_final_path_name_by_handle = _kernel32.GetFinalPathNameByHandleW
    _get_final_path_name_by_handle.argtypes = [
        wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD,
    ]
    _get_final_path_name_by_handle.restype = wintypes.DWORD
    _compare_string_ordinal = _kernel32.CompareStringOrdinal
    _compare_string_ordinal.argtypes = [
        wintypes.LPCWSTR, ctypes.c_int, wintypes.LPCWSTR, ctypes.c_int, wintypes.BOOL,
    ]
    _compare_string_ordinal.restype = ctypes.c_int
    _ntdll = ctypes.WinDLL("ntdll")
    try:
        _nt_set_information_file = _ntdll.NtSetInformationFile
        _nt_set_information_file.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(_IoStatusBlock),
            wintypes.LPVOID,
            wintypes.ULONG,
            ctypes.c_int,
        ]
        _nt_set_information_file.restype = ctypes.c_long
        _rtl_nt_status_to_dos_error = _ntdll.RtlNtStatusToDosError
        _rtl_nt_status_to_dos_error.argtypes = [ctypes.c_long]
        _rtl_nt_status_to_dos_error.restype = wintypes.ULONG
    except AttributeError:
        _nt_set_information_file = None
        _rtl_nt_status_to_dos_error = None


def _move_file_windows(
    source: Path,
    destination: Path,
    expected_sha256: str,
    expected_size: int,
    expected_mtime_ns: int,
    destination_root: VerifiedDirectoryRoot | None,
    source_root: VerifiedDirectoryRoot | None,
) -> None:
    source_handle = _open_source_handle(source)
    destination_parent_handle = (
        _open_verified_directory_under_root(
            destination_root, destination.parent, f"destination directory {destination.parent}"
        )
        if destination_root is not None
        else _open_directory_handle(destination.parent)
    )
    try:
        source_identity = _handle_identity(source_handle, f"source {source}")
        if source_root is not None:
            _validate_open_handle_under_root(source_handle, source_root, f"source {source}")
        parent_identity = _handle_identity(
            destination_parent_handle, f"destination directory {destination.parent}"
        )
        if source_identity[0] != parent_identity[0]:
            raise FilesystemSafetyError(
                f"source and destination are on different volumes: {source} -> {destination}",
                error_type="cross_volume",
            )
        if source_identity[2] != expected_size or source_identity[3] != expected_mtime_ns:
            raise FilesystemSafetyError(
                f"source changed after planning: {source}", error_type="source_changed"
            )
        if _hash_handle(source_handle, f"source {source}") != expected_sha256:
            raise FilesystemSafetyError(
                f"source content changed after planning: {source}", error_type="source_changed"
            )
        if path_exists_no_follow(destination):
            raise FilesystemSafetyError(
                f"destination already exists: {destination}", error_type="destination_exists"
            )

        _rename_handle_without_overwrite(source_handle, destination_parent_handle, destination)

        if _handle_identity(
            destination_parent_handle, f"destination directory {destination.parent}"
        )[:2] != parent_identity[:2]:
            raise FilesystemSafetyError(
                f"destination directory identity changed during move: {destination.parent}",
                error_type="unsafe_reparse_point",
            )

        if path_exists_no_follow(source):
            raise FilesystemSafetyError(f"source still exists after move: {source}")
        if not is_ordinary_file(destination):
            raise FilesystemSafetyError(f"destination verification failed: {destination}")
        destination_handle = _open_read_handle(destination)
        try:
            destination_identity = _handle_identity(destination_handle, f"destination {destination}")
            if destination_identity[:2] != source_identity[:2]:
                raise FilesystemSafetyError(f"destination identity verification failed: {destination}")
            if _hash_handle(destination_handle, f"destination {destination}") != expected_sha256:
                raise FilesystemSafetyError(f"destination verification failed: {destination}")
        finally:
            _close_handle(destination_handle)
    finally:
        _close_handle(destination_parent_handle)
        _close_handle(source_handle)


def _open_source_handle(path: Path) -> int:
    return _open_regular_file_handle(path, _GENERIC_READ | _DELETE, f"source {path}")


def _open_read_handle(path: Path) -> int:
    return _open_regular_file_handle(
        path, _GENERIC_READ, f"destination {path}", _FILE_SHARE_READ | _FILE_SHARE_DELETE
    )


def _open_regular_file_handle(
    path: Path, access: int, description: str, share_mode: int = _FILE_SHARE_READ
) -> int:
    handle = _create_file(
        str(path), access, share_mode, None, _OPEN_EXISTING,
        _FILE_FLAG_OPEN_REPARSE_POINT, None,
    )
    if handle == _INVALID_HANDLE_VALUE:
        _raise_windows_error(f"could not safely open {description}")
    if _get_file_type(handle) != _FILE_TYPE_DISK:
        _close_handle(handle)
        raise FilesystemSafetyError(f"{description} is not a regular disk file")
    identity = _handle_identity(handle, description)
    if identity[4] & FILE_ATTRIBUTE_REPARSE_POINT:
        _close_handle(handle)
        raise FilesystemSafetyError(f"{description} is a symbolic link, junction, or reparse point")
    return handle


def _open_directory_handle(path: Path) -> int:
    handle = _create_file(
        str(path), _FILE_LIST_DIRECTORY | _FILE_ADD_FILE | _FILE_READ_ATTRIBUTES | _SYNCHRONIZE,
        _FILE_SHARE_READ | _FILE_SHARE_WRITE,
        None, _OPEN_EXISTING,
        _FILE_FLAG_BACKUP_SEMANTICS | _FILE_FLAG_OPEN_REPARSE_POINT, None,
    )
    if handle == _INVALID_HANDLE_VALUE:
        _raise_windows_error(f"could not safely open destination directory {path}")
    identity = _handle_identity(handle, f"destination directory {path}")
    if not identity[4] & _FILE_ATTRIBUTE_DIRECTORY:
        _close_handle(handle)
        raise FilesystemSafetyError(f"destination directory is not a directory: {path}")
    if identity[4] & FILE_ATTRIBUTE_REPARSE_POINT:
        _close_handle(handle)
        raise FilesystemSafetyError(
            f"destination directory is a symbolic link, junction, or reparse point: {path}"
        )
    return handle


@contextmanager
def open_verified_directory_root(path: Path, description: str) -> Iterator[VerifiedDirectoryRoot | None]:
    """Retain one verified directory object while a mutation uses its descendants."""
    assert_ordinary_directory(path, description)
    if os.name != "nt":
        yield None
        return
    handle = _open_directory_handle(path)
    try:
        identity = _handle_identity(handle, description)
        yield VerifiedDirectoryRoot(path, handle, identity, _final_path_from_handle(handle, description))
    finally:
        _close_handle(handle)


def _normalize_final_path(value: str) -> str:
    """Normalize documented GetFinalPathNameByHandleW path prefixes for comparison."""
    normalized = value.replace("/", "\\")
    if normalized.casefold().startswith("\\\\?\\unc\\"):
        normalized = "\\\\" + normalized[8:]
    elif normalized.casefold().startswith("\\\\?\\"):
        normalized = normalized[4:]
    return normalized.rstrip("\\")


def _final_path_parts(value: str) -> tuple[str, ...]:
    return tuple(part for part in _normalize_final_path(value).split("\\") if part)


def _final_path_is_descendant(value: str, root: str) -> bool:
    value_parts = _final_path_parts(value)
    root_parts = _final_path_parts(root)
    return len(value_parts) >= len(root_parts) and all(
        names_equal(value_part, root_part) for value_part, root_part in zip(value_parts, root_parts)
    )


def _final_path_is_direct_child(value: str, parent: str, name: str) -> bool:
    value_parts = _final_path_parts(value)
    parent_parts = _final_path_parts(parent)
    return (
        len(value_parts) == len(parent_parts) + 1
        and all(names_equal(value_part, parent_part) for value_part, parent_part in zip(value_parts, parent_parts))
        and names_equal(value_parts[-1], name)
    )


def _final_path_from_handle(handle: int, description: str) -> str:
    buffer = ctypes.create_unicode_buffer(32768)
    length = _get_final_path_name_by_handle(handle, buffer, len(buffer), _VOLUME_NAME_GUID)
    if length == 0:
        _raise_windows_error(f"could not resolve final path for {description}")
    if length >= len(buffer):
        buffer = ctypes.create_unicode_buffer(length + 1)
        length = _get_final_path_name_by_handle(handle, buffer, len(buffer), _VOLUME_NAME_GUID)
        if length == 0 or length >= len(buffer):
            _raise_windows_error(f"could not resolve final path for {description}")
    return _normalize_final_path(buffer.value)


def _validate_open_handle_under_root(
    handle: int, root: VerifiedDirectoryRoot, description: str
) -> tuple[int, int, int, int, int]:
    identity = _handle_identity(handle, description)
    if identity[0] != root.identity[0]:
        raise FilesystemSafetyError(
            f"{description} is on a different volume from verified root {root.path}",
            error_type="cross_volume",
        )
    if not _final_path_is_descendant(_final_path_from_handle(handle, description), root.final_path):
        raise FilesystemSafetyError(
            f"{description} escapes verified root {root.path}", error_type="path_escape"
        )
    return identity


def _open_verified_directory_under_root(
    root: VerifiedDirectoryRoot, path: Path, description: str
) -> int:
    handle = _open_directory_handle(path)
    try:
        _validate_open_handle_under_root(handle, root, description)
        return handle
    except BaseException:
        _close_handle(handle)
        raise


def _verify_child_directory(
    child: Path,
    parent: Path,
    expected_name: str,
    verified_root: VerifiedDirectoryRoot | None,
) -> None:
    if not is_ordinary_directory(child):
        raise FilesystemSafetyError(f"new directory is not safe to use: {child}")
    if verified_root is None:
        return
    child_handle = _open_verified_directory_under_root(verified_root, child, f"directory {child}")
    parent_handle = _open_verified_directory_under_root(verified_root, parent, f"directory {parent}")
    try:
        if not _final_path_is_direct_child(
            _final_path_from_handle(child_handle, f"directory {child}"),
            _final_path_from_handle(parent_handle, f"directory {parent}"),
            expected_name,
        ):
            raise FilesystemSafetyError(
                f"directory is not the expected direct child of its verified parent: {child}",
                error_type="path_escape",
            )
    finally:
        _close_handle(parent_handle)
        _close_handle(child_handle)


def verify_child_directory(
    child: Path,
    parent: Path,
    expected_name: str,
    verified_root: VerifiedDirectoryRoot | None = None,
) -> None:
    """Validate an existing direct-child directory without creating anything."""
    _verify_child_directory(child, parent, expected_name, verified_root)


def _handle_identity(handle: int, description: str) -> tuple[int, int, int, int, int]:
    information = _ByHandleFileInformation()
    if not _get_file_information(handle, ctypes.byref(information)):
        _raise_windows_error(f"could not inspect {description}")
    last_write = (
        (int(information.ftLastWriteTime.dwHighDateTime) << 32)
        | int(information.ftLastWriteTime.dwLowDateTime)
    )
    return (
        int(information.dwVolumeSerialNumber),
        (int(information.nFileIndexHigh) << 32) | int(information.nFileIndexLow),
        (int(information.nFileSizeHigh) << 32) | int(information.nFileSizeLow),
        last_write * 100 - _WINDOWS_EPOCH_OFFSET_NS,
        int(information.dwFileAttributes),
    )


def _hash_handle(handle: int, description: str) -> str:
    if not _set_file_pointer(handle, 0, None, 0):
        _raise_windows_error(f"could not seek {description}")
    digest = hashlib.sha256()
    buffer = ctypes.create_string_buffer(1024 * 1024)
    while True:
        read_count = wintypes.DWORD()
        if not _read_file(handle, buffer, len(buffer), ctypes.byref(read_count), None):
            _raise_windows_error(f"could not read {description}")
        if read_count.value == 0:
            return digest.hexdigest()
        digest.update(buffer.raw[:read_count.value])


def _rename_handle_without_overwrite(source_handle: int, parent_handle: int, destination: Path) -> None:
    """Rename relative to an already-verified target directory handle only."""
    relative_name = destination.name
    if relative_name in {"", ".", ".."} or Path(relative_name).name != relative_name:
        raise FilesystemSafetyError(
            f"destination must be a simple relative filename: {destination}",
            error_type="path_escape",
        )
    if _nt_set_information_file is None:
        raise FilesystemSafetyError(
            "NtSetInformationFile is unavailable for handle-relative rename",
            error_type="unsupported_filesystem",
        )

    encoded_name = relative_name.encode("utf-16-le")
    # Keep at least the complete fixed structure even for unusually short names.
    buffer_size = max(
        ctypes.sizeof(_FileRenameInformation),
        _FileRenameInformation.FileName.offset + len(encoded_name),
    )
    rename_buffer = ctypes.create_string_buffer(buffer_size)
    rename_info = ctypes.cast(rename_buffer, ctypes.POINTER(_FileRenameInformation)).contents
    rename_info.ReplaceIfExists = False
    rename_info.RootDirectory = parent_handle
    rename_info.FileNameLength = len(encoded_name)
    ctypes.memmove(
        ctypes.addressof(rename_buffer) + _FileRenameInformation.FileName.offset,
        encoded_name,
        len(encoded_name),
    )
    io_status = _IoStatusBlock()
    status = _nt_set_information_file(
        source_handle,
        ctypes.byref(io_status),
        rename_buffer,
        buffer_size,
        _FILE_RENAME_INFORMATION_CLASS,
    )
    if not _nt_success(status):
        _raise_ntstatus_error("could not rename source relative to verified destination directory", status)
    if not _nt_success(io_status.Status):
        _raise_ntstatus_error(
            "could not rename source relative to verified destination directory", io_status.Status
        )


def _nt_success(status: int) -> bool:
    return ctypes.c_long(status).value >= 0


def _ntstatus_value(status: int) -> int:
    return ctypes.c_ulong(status).value & 0xFFFFFFFF


def _raise_ntstatus_error(message: str, status: int) -> None:
    status_value = _ntstatus_value(status)
    if status_value == 0xC0000035:
        error_type = "destination_exists"
        prefix = "destination already exists"
    elif status_value == 0xC00000D4:
        error_type = "cross_volume"
        prefix = "cross-volume moves are not supported"
    elif status_value in {0xC0000043, 0xC0000054, 0xC0000022}:
        error_type = "destination_locked"
        prefix = "destination directory is locked or cannot be safely shared"
    elif status_value in {0xC00000BB, 0xC0000010}:
        error_type = "unsupported_filesystem"
        prefix = "handle-relative rename is not supported"
    else:
        error_type = "unknown_filesystem_error"
        prefix = "handle-relative rename failed"
    dos_error = (
        int(_rtl_nt_status_to_dos_error(ctypes.c_long(status).value))
        if _rtl_nt_status_to_dos_error is not None
        else None
    )
    converted = f"; DOS error {dos_error}" if dos_error is not None else ""
    raise FilesystemSafetyError(
        f"{prefix}: {message}; NTSTATUS 0x{status_value:08X}{converted}",
        error_type=error_type,
    )


def _raise_windows_error(message: str) -> None:
    error = ctypes.get_last_error()
    if error in {_ERROR_FILE_EXISTS, _ERROR_ALREADY_EXISTS}:
        raise FilesystemSafetyError(
            f"destination already exists ({message})", error_type="destination_exists"
        )
    if error == _ERROR_NOT_SAME_DEVICE:
        raise FilesystemSafetyError(
            f"cross-volume moves are not supported: {message}", error_type="cross_volume"
        )
    if error in {_ERROR_SHARING_VIOLATION, _ERROR_LOCK_VIOLATION}:
        error_type = "destination_locked" if "destination" in message.casefold() else "source_locked"
        raise FilesystemSafetyError(
            f"file is locked or cannot be safely shared: {message}", error_type=error_type
        )
    raise FilesystemSafetyError(
        f"{message}: Windows error {error}", error_type="unknown_filesystem_error"
    )
