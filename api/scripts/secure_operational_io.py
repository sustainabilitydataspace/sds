#!/usr/bin/env python3
"""Descriptor-bound intake primitives for privileged SDS operational scripts."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import stat
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath
from typing import BinaryIO, NamedTuple

MAX_TAR_BYTES = 512 * 1024 * 1024
MAX_TAR_MEMBERS = 20_000
MAX_MEMBER_BYTES = 128 * 1024 * 1024
MAX_EXPANDED_BYTES = 1024 * 1024 * 1024
MAX_ENV_BYTES = 1024 * 1024
MAX_BACKUP_BYTES = 256 * 1024 * 1024 * 1024
COPY_CHUNK_BYTES = 1024 * 1024
_ENV_KEY = re.compile(r"^[A-Z_][A-Z0-9_]*$")
_BLOCKED_ENV_KEYS = {
    "BASH_ENV",
    "CDPATH",
    "ENV",
    "GLOBIGNORE",
    "IFS",
    "LD_PRELOAD",
    "PATH",
    "PYTHONHOME",
    "PYTHONPATH",
    "SHELLOPTS",
}


class OperationalIOError(RuntimeError):
    """An operational input failed a fail-closed safety check."""


class IntakeReceipt(NamedTuple):
    sha256: str
    size: int
    members: int = 0
    expanded_bytes: int = 0


def _seal(info: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
        info.st_nlink,
    )


def _open_regular(path: Path, *, max_bytes: int) -> tuple[int, os.stat_result]:
    flags = os.O_RDONLY | os.O_NONBLOCK
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise OperationalIOError(f"cannot open regular input: {path}") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise OperationalIOError(f"input is not a regular file: {path}")
        if info.st_nlink != 1:
            raise OperationalIOError(f"input must have exactly one link: {path}")
        if info.st_size > max_bytes:
            raise OperationalIOError(f"input exceeds byte budget: {path}")
        return fd, info
    except Exception:
        os.close(fd)
        raise


def _hash_fd(fd: int, *, max_bytes: int) -> tuple[str, int]:
    os.lseek(fd, 0, os.SEEK_SET)
    digest = hashlib.sha256()
    total = 0
    while True:
        chunk = os.read(fd, COPY_CHUNK_BYTES)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise OperationalIOError("input exceeded byte budget while reading")
        digest.update(chunk)
    return digest.hexdigest(), total


def _safe_member_path(name: str, strip_components: int) -> tuple[str, ...]:
    if not name or "\x00" in name or "\\" in name:
        raise OperationalIOError(f"unsafe archive member: {name!r}")
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise OperationalIOError(f"unsafe archive member: {name!r}")
    if strip_components < 0:
        raise OperationalIOError("strip-components must not be negative")
    return tuple(path.parts[strip_components:])


def _validate_members(
    members: list[tarfile.TarInfo], strip_components: int
) -> tuple[list[tuple[tarfile.TarInfo, tuple[str, ...]]], int]:
    if len(members) > MAX_TAR_MEMBERS:
        raise OperationalIOError("archive member-count budget exceeded")
    selected: list[tuple[tarfile.TarInfo, tuple[str, ...]]] = []
    kinds: dict[tuple[str, ...], str] = {}
    expanded = 0
    for member in members:
        parts = _safe_member_path(member.name, strip_components)
        if not parts:
            if member.isdir():
                continue
            raise OperationalIOError(
                f"unsafe archive member removed by strip-components: {member.name!r}"
            )
        if not (member.isfile() or member.isdir()):
            raise OperationalIOError(
                f"unsupported archive member type: {member.name!r}"
            )
        if getattr(member, "sparse", None):
            raise OperationalIOError(f"sparse archive member rejected: {member.name!r}")
        if member.size < 0 or member.size > MAX_MEMBER_BYTES:
            raise OperationalIOError(f"archive member byte budget exceeded: {member.name!r}")
        expanded += member.size
        if expanded > MAX_EXPANDED_BYTES:
            raise OperationalIOError("archive expanded-byte budget exceeded")
        if parts in kinds:
            raise OperationalIOError(f"duplicate archive destination: {'/'.join(parts)}")
        for index in range(1, len(parts)):
            if kinds.get(parts[:index]) == "file":
                raise OperationalIOError(
                    f"archive file/directory collision: {'/'.join(parts)}"
                )
        if member.isfile() and any(
            previous[: len(parts)] == parts for previous in kinds if len(previous) > len(parts)
        ):
            raise OperationalIOError(
                f"archive file/directory collision: {'/'.join(parts)}"
            )
        kinds[parts] = "file" if member.isfile() else "directory"
        selected.append((member, parts))
    if not selected:
        raise OperationalIOError("archive contains no extractable members")
    return selected, expanded


def _open_or_create_directory(parent_fd: int, name: str) -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        return os.open(name, flags, dir_fd=parent_fd)
    except FileNotFoundError:
        os.mkdir(name, mode=0o700, dir_fd=parent_fd)
        return os.open(name, flags, dir_fd=parent_fd)
    except OSError as exc:
        raise OperationalIOError(f"unsafe extraction directory component: {name}") from exc


def _directory_fd(root_fd: int, parts: tuple[str, ...]) -> int:
    current = os.dup(root_fd)
    try:
        for part in parts:
            next_fd = _open_or_create_directory(current, part)
            os.close(current)
            current = next_fd
        return current
    except Exception:
        os.close(current)
        raise


def _copy_member(
    archive: tarfile.TarFile,
    member: tarfile.TarInfo,
    root_fd: int,
    parts: tuple[str, ...],
) -> None:
    parent_fd = _directory_fd(root_fd, parts[:-1])
    try:
        if member.isdir():
            child_fd = _open_or_create_directory(parent_fd, parts[-1])
            os.close(child_fd)
            return
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        mode = 0o755 if member.mode & 0o111 else 0o644
        try:
            output_fd = os.open(parts[-1], flags, mode, dir_fd=parent_fd)
        except OSError as exc:
            raise OperationalIOError(
                f"cannot create archive destination: {'/'.join(parts)}"
            ) from exc
        try:
            source = archive.extractfile(member)
            if source is None:
                raise OperationalIOError(
                    f"cannot read archive member: {member.name!r}"
                )
            copied = 0
            with source:
                while True:
                    chunk = source.read(COPY_CHUNK_BYTES)
                    if not chunk:
                        break
                    copied += len(chunk)
                    if copied > member.size or copied > MAX_MEMBER_BYTES:
                        raise OperationalIOError(
                            f"archive member expanded beyond declaration: {member.name!r}"
                        )
                    view = memoryview(chunk)
                    while view:
                        written = os.write(output_fd, view)
                        view = view[written:]
            if copied != member.size:
                raise OperationalIOError(
                    f"archive member size mismatch: {member.name!r}"
                )
            os.fchmod(output_fd, mode)
            os.fsync(output_fd)
        finally:
            os.close(output_fd)
    finally:
        os.close(parent_fd)


def extract_authenticated_tar(
    archive_path: Path | str,
    destination: Path | str,
    *,
    expected_sha256: str,
    strip_components: int = 1,
) -> IntakeReceipt:
    """Authenticate, validate, and extract one TAR through held descriptors."""
    archive_path = Path(archive_path)
    destination = Path(destination)
    expected = expected_sha256.lower()
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise OperationalIOError("expected TAR digest is not canonical SHA-256")
    fd, initial = _open_regular(archive_path, max_bytes=MAX_TAR_BYTES)
    created = False
    try:
        actual, size = _hash_fd(fd, max_bytes=MAX_TAR_BYTES)
        if actual != expected:
            raise OperationalIOError("TAR digest mismatch")
        if _seal(os.fstat(fd)) != _seal(initial):
            raise OperationalIOError("TAR changed during authentication")
        os.lseek(fd, 0, os.SEEK_SET)
        with os.fdopen(os.dup(fd), "rb", closefd=True) as stream:
            try:
                with tarfile.open(fileobj=stream, mode="r:*") as archive:
                    selected, expanded = _validate_members(
                        archive.getmembers(), strip_components
                    )
                    destination.mkdir(mode=0o700)
                    created = True
                    root_flags = os.O_RDONLY | os.O_DIRECTORY
                    root_flags |= getattr(os, "O_CLOEXEC", 0) | getattr(
                        os, "O_NOFOLLOW", 0
                    )
                    root_fd = os.open(destination, root_flags)
                    try:
                        root_info = os.fstat(root_fd)
                        root_identity = (
                            root_info.st_dev,
                            root_info.st_ino,
                        )
                        for member, parts in selected:
                            _copy_member(archive, member, root_fd, parts)
                        final_root = os.fstat(root_fd)
                        if (
                            final_root.st_dev,
                            final_root.st_ino,
                        ) != root_identity:
                            raise OperationalIOError(
                                "release destination changed during extraction"
                            )
                        os.fsync(root_fd)
                    finally:
                        os.close(root_fd)
            except (tarfile.TarError, EOFError, OSError) as exc:
                if isinstance(exc, OperationalIOError):
                    raise
                raise OperationalIOError("invalid or unreadable TAR archive") from exc
        final_digest, final_size = _hash_fd(fd, max_bytes=MAX_TAR_BYTES)
        if final_digest != expected or final_size != size:
            raise OperationalIOError("TAR changed during extraction")
        if _seal(os.fstat(fd)) != _seal(initial):
            raise OperationalIOError("TAR identity changed during extraction")
        return IntakeReceipt(actual, size, len(selected), expanded)
    except Exception:
        if created:
            shutil.rmtree(destination, ignore_errors=True)
        raise
    finally:
        os.close(fd)


def parse_env_bytes(content: bytes) -> dict[str, str]:
    """Parse a strict KEY=VALUE file as data; no shell grammar is evaluated."""
    if len(content) > MAX_ENV_BYTES:
        raise OperationalIOError("runtime env file exceeds byte budget")
    if content.startswith(b"\xef\xbb\xbf") or b"\x00" in content or b"\r" in content:
        raise OperationalIOError("runtime env file has unsupported encoding or line endings")
    try:
        text = content.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise OperationalIOError("runtime env file is not valid UTF-8") from exc
    values: dict[str, str] = {}
    for line_number, line in enumerate(text.split("\n"), start=1):
        if not line or line.startswith("#"):
            continue
        if line[0].isspace() or "=" not in line:
            raise OperationalIOError(f"invalid runtime env line {line_number}")
        key, value = line.split("=", 1)
        if not _ENV_KEY.fullmatch(key) or key in _BLOCKED_ENV_KEYS:
            raise OperationalIOError(f"unsafe runtime env key on line {line_number}")
        if key in values:
            raise OperationalIOError(f"duplicate runtime env key on line {line_number}")
        values[key] = value
    if not values:
        raise OperationalIOError("runtime env file is empty")
    return values


def load_env_file(
    path: Path | str,
    *,
    expected_sha256: str | None = None,
    required_uid: int = 0,
) -> tuple[dict[str, str], IntakeReceipt]:
    path = Path(path)
    fd, initial = _open_regular(path, max_bytes=MAX_ENV_BYTES)
    try:
        if initial.st_uid != required_uid:
            raise OperationalIOError("runtime env file must be owned by root")
        if stat.S_IMODE(initial.st_mode) & 0o077:
            raise OperationalIOError("runtime env file permits group/other access")
        digest, size = _hash_fd(fd, max_bytes=MAX_ENV_BYTES)
        if expected_sha256 is not None and digest != expected_sha256.lower():
            raise OperationalIOError("runtime env digest mismatch")
        os.lseek(fd, 0, os.SEEK_SET)
        content = os.read(fd, size + 1)
        if len(content) != size:
            raise OperationalIOError("runtime env file changed while reading")
        if _seal(os.fstat(fd)) != _seal(initial):
            raise OperationalIOError("runtime env identity changed while reading")
        return parse_env_bytes(content), IntakeReceipt(digest, size)
    finally:
        os.close(fd)


def stream_authenticated_file(
    path: Path | str,
    *,
    expected_sha256: str,
    expected_size: int | None = None,
    output: BinaryIO,
) -> IntakeReceipt:
    """Copy authenticated input to a private snapshot, then stream that snapshot."""
    expected = expected_sha256.lower()
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise OperationalIOError("expected backup digest is not canonical SHA-256")
    fd, initial = _open_regular(Path(path), max_bytes=MAX_BACKUP_BYTES)
    try:
        digest = hashlib.sha256()
        total = 0
        with tempfile.TemporaryFile(mode="w+b") as snapshot:
            while True:
                chunk = os.read(fd, COPY_CHUNK_BYTES)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_BACKUP_BYTES:
                    raise OperationalIOError("backup exceeded byte budget while reading")
                digest.update(chunk)
                snapshot.write(chunk)
            actual = digest.hexdigest()
            if actual != expected:
                raise OperationalIOError("backup digest mismatch")
            if expected_size is not None and total != expected_size:
                raise OperationalIOError("backup size does not match manifest")
            if total != initial.st_size or _seal(os.fstat(fd)) != _seal(initial):
                raise OperationalIOError("backup changed while snapshotting")
            snapshot.flush()
            snapshot.seek(0)
            while True:
                chunk = snapshot.read(COPY_CHUNK_BYTES)
                if not chunk:
                    break
                view = memoryview(chunk)
                while view:
                    written = output.write(view)
                    if written is None:
                        written = len(view)
                    if written <= 0:
                        raise OperationalIOError(
                            "backup output stream stopped accepting bytes"
                        )
                    view = view[written:]
            output.flush()
        return IntakeReceipt(actual, total)
    except (BrokenPipeError, OSError) as exc:
        raise OperationalIOError("failed to stream authenticated backup") from exc
    finally:
        os.close(fd)


def _create_private_output(path: Path) -> tuple[int, os.stat_result]:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags, 0o600)
    except OSError as exc:
        raise OperationalIOError(f"cannot create private output: {path}") from exc
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        os.close(fd)
        raise OperationalIOError(f"private output is not an independent file: {path}")
    return fd, info


def _unlink_same_output(path: Path, identity: os.stat_result) -> None:
    try:
        current = path.lstat()
        if (current.st_dev, current.st_ino) == (identity.st_dev, identity.st_ino):
            path.unlink()
    except FileNotFoundError:
        pass


def capture_private_file(path: Path | str, source: BinaryIO) -> IntakeReceipt:
    """Capture a stream into a new mode-0600 regular file without clobbering."""
    path = Path(path)
    fd, identity = _create_private_output(path)
    completed = False
    try:
        digest = hashlib.sha256()
        total = 0
        while True:
            chunk = source.read(COPY_CHUNK_BYTES)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_BACKUP_BYTES:
                raise OperationalIOError("private output exceeds byte budget")
            digest.update(chunk)
            view = memoryview(chunk)
            while view:
                written = os.write(fd, view)
                view = view[written:]
        os.fchmod(fd, 0o600)
        os.fsync(fd)
        final = os.fstat(fd)
        if (
            final.st_dev,
            final.st_ino,
            final.st_nlink,
            final.st_size,
        ) != (identity.st_dev, identity.st_ino, 1, total):
            raise OperationalIOError("private output identity changed while writing")
        completed = True
        return IntakeReceipt(digest.hexdigest(), total)
    finally:
        os.close(fd)
        if not completed:
            _unlink_same_output(path, identity)


def write_backup_manifest(
    path: Path | str,
    *,
    created_at_utc: str,
    backup_path: str,
    size_bytes: int,
    sha256: str,
    compose_project: str,
    env_file: str,
    compose_file: str,
    postgres_db: str,
    postgres_user: str,
) -> IntakeReceipt:
    """Write a strict JSON backup manifest with exclusive private creation."""
    if size_bytes < 0 or not re.fullmatch(r"[0-9a-fA-F]{64}", sha256):
        raise OperationalIOError("invalid backup manifest receipt")
    string_values = {
        "created_at_utc": created_at_utc,
        "backup_path": backup_path,
        "compose_project": compose_project,
        "env_file": env_file,
        "compose_file": compose_file,
        "postgres_db": postgres_db,
        "postgres_user": postgres_user,
    }
    if any(not value or "\x00" in value for value in string_values.values()):
        raise OperationalIOError("invalid empty/control value in backup manifest")
    payload = {
        "schema_version": 1,
        **string_values,
        "size_bytes": size_bytes,
        "sha256": sha256.lower(),
    }
    encoded = (json.dumps(payload, sort_keys=True, indent=2) + "\n").encode("utf-8")
    return capture_private_file(Path(path), source=io.BytesIO(encoded))


_BACKUP_MANIFEST_KEYS = {
    "schema_version",
    "created_at_utc",
    "backup_path",
    "size_bytes",
    "sha256",
    "compose_project",
    "env_file",
    "compose_file",
    "postgres_db",
    "postgres_user",
}


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise OperationalIOError(f"duplicate JSON key in backup manifest: {key}")
        result[key] = value
    return result


def read_backup_manifest(
    path: Path | str,
) -> tuple[dict[str, object], IntakeReceipt]:
    """Read and validate one closed-shape backup manifest through a held descriptor."""
    fd, initial = _open_regular(Path(path), max_bytes=MAX_ENV_BYTES)
    try:
        digest, size = _hash_fd(fd, max_bytes=MAX_ENV_BYTES)
        os.lseek(fd, 0, os.SEEK_SET)
        content = os.read(fd, size + 1)
        if len(content) != size or _seal(os.fstat(fd)) != _seal(initial):
            raise OperationalIOError("backup manifest changed while reading")
        try:
            manifest = json.loads(
                content.decode("utf-8", errors="strict"),
                object_pairs_hook=_unique_json_object,
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OperationalIOError("backup manifest is not strict JSON") from exc
        if not isinstance(manifest, dict) or set(manifest) != _BACKUP_MANIFEST_KEYS:
            raise OperationalIOError("backup manifest has an unsupported shape")
        if manifest["schema_version"] != 1:
            raise OperationalIOError("backup manifest schema version is unsupported")
        if not isinstance(manifest["size_bytes"], int) or manifest["size_bytes"] < 0:
            raise OperationalIOError("backup manifest size is invalid")
        if not isinstance(manifest["sha256"], str) or not re.fullmatch(
            r"[0-9a-fA-F]{64}", manifest["sha256"]
        ):
            raise OperationalIOError("backup manifest digest is invalid")
        for key in _BACKUP_MANIFEST_KEYS - {
            "schema_version",
            "size_bytes",
            "sha256",
        }:
            value = manifest[key]
            if (
                not isinstance(value, str)
                or not value
                or any(character in value for character in ("\x00", "\n", "\r", "\t"))
            ):
                raise OperationalIOError(f"backup manifest field is unsafe: {key}")
        manifest["sha256"] = str(manifest["sha256"]).lower()
        return manifest, IntakeReceipt(digest, size)
    finally:
        os.close(fd)


def compose_service_is_stopped(content: bytes, *, service: str) -> bool:
    """Interpret Docker Compose JSON exactly and fail closed on unknown states."""
    if not service or len(content) > MAX_ENV_BYTES or b"\x00" in content:
        raise OperationalIOError("invalid compose service-state input")
    try:
        decoded = content.decode("utf-8", errors="strict").strip()
    except UnicodeDecodeError as exc:
        raise OperationalIOError("compose service state is not UTF-8") from exc
    if not decoded:
        return True
    try:
        parsed = json.loads(decoded)
        records = parsed if isinstance(parsed, list) else [parsed]
    except json.JSONDecodeError:
        try:
            records = [json.loads(line) for line in decoded.splitlines() if line]
        except json.JSONDecodeError as exc:
            raise OperationalIOError("compose service state is not valid JSON") from exc
    stopped_states = {"created", "dead", "exited"}
    active_states = {"paused", "restarting", "running"}
    for record in records:
        if not isinstance(record, dict):
            raise OperationalIOError("compose service state contains a non-object")
        observed_service = record.get("Service")
        state = record.get("State")
        if not isinstance(observed_service, str) or not isinstance(state, str):
            raise OperationalIOError("compose service state omits exact Service/State")
        if observed_service != service:
            raise OperationalIOError("compose returned an unexpected service record")
        normalized = state.casefold()
        if normalized in active_states:
            return False
        if normalized not in stopped_states:
            raise OperationalIOError(f"unknown compose service state: {state}")
    return True


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    extract = subparsers.add_parser("extract-tar")
    extract.add_argument("--tarball", required=True)
    extract.add_argument("--sha256", required=True)
    extract.add_argument("--destination", required=True)
    extract.add_argument("--strip-components", type=int, default=1)

    digest = subparsers.add_parser("env-digest")
    digest.add_argument("--env-file", required=True)

    execute = subparsers.add_parser("env-exec")
    execute.add_argument("--env-file", required=True)
    execute.add_argument("--sha256", required=True)
    execute.add_argument("argv", nargs=argparse.REMAINDER)

    stream = subparsers.add_parser("stream-file")
    stream.add_argument("--path", required=True)
    stream.add_argument("--sha256", required=True)
    stream.add_argument("--size", type=int)

    service_state = subparsers.add_parser("compose-service-stopped")
    service_state.add_argument("--service", required=True)

    capture = subparsers.add_parser("capture-file")
    capture.add_argument("--path", required=True)

    manifest = subparsers.add_parser("write-backup-manifest")
    manifest.add_argument("--path", required=True)
    manifest.add_argument("--created-at-utc", required=True)
    manifest.add_argument("--backup-path", required=True)
    manifest.add_argument("--size-bytes", required=True, type=int)
    manifest.add_argument("--sha256", required=True)
    manifest.add_argument("--compose-project", required=True)
    manifest.add_argument("--env-file", required=True)
    manifest.add_argument("--compose-file", required=True)
    manifest.add_argument("--postgres-db", required=True)
    manifest.add_argument("--postgres-user", required=True)

    read_manifest = subparsers.add_parser("read-backup-manifest")
    read_manifest.add_argument("--path", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "extract-tar":
            receipt = extract_authenticated_tar(
                args.tarball,
                args.destination,
                expected_sha256=args.sha256,
                strip_components=args.strip_components,
            )
            print(receipt.sha256)
            return 0
        if args.command == "env-digest":
            _, receipt = load_env_file(args.env_file)
            print(receipt.sha256)
            return 0
        if args.command == "env-exec":
            command = list(args.argv)
            if command[:1] == ["--"]:
                command = command[1:]
            if not command:
                raise OperationalIOError("env-exec requires a command")
            values, _ = load_env_file(
                args.env_file, expected_sha256=args.sha256
            )
            environment = os.environ.copy()
            environment.update(values)
            os.execvpe(command[0], command, environment)
        if args.command == "stream-file":
            stream_authenticated_file(
                args.path,
                expected_sha256=args.sha256,
                expected_size=args.size,
                output=sys.stdout.buffer,
            )
            return 0
        if args.command == "compose-service-stopped":
            content = sys.stdin.buffer.read(MAX_ENV_BYTES + 1)
            return (
                0
                if compose_service_is_stopped(content, service=args.service)
                else 3
            )
        if args.command == "capture-file":
            receipt = capture_private_file(args.path, sys.stdin.buffer)
            print(f"{receipt.sha256}\t{receipt.size}")
            return 0
        if args.command == "write-backup-manifest":
            write_backup_manifest(
                args.path,
                created_at_utc=args.created_at_utc,
                backup_path=args.backup_path,
                size_bytes=args.size_bytes,
                sha256=args.sha256,
                compose_project=args.compose_project,
                env_file=args.env_file,
                compose_file=args.compose_file,
                postgres_db=args.postgres_db,
                postgres_user=args.postgres_user,
            )
            return 0
        if args.command == "read-backup-manifest":
            manifest, _ = read_backup_manifest(args.path)
            print(
                "\t".join(
                    (
                        str(manifest["sha256"]),
                        str(manifest["size_bytes"]),
                        str(manifest["postgres_db"]),
                        str(manifest["postgres_user"]),
                    )
                )
            )
            return 0
        raise OperationalIOError("unsupported operation")
    except OperationalIOError as exc:
        print(f"secure operational I/O rejected input: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
