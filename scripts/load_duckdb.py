from __future__ import annotations

import csv
import os as _stdlib_os
import re
import secrets
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from hashlib import sha256
from importlib import metadata
from pathlib import Path
from typing import Any

import duckdb
from pydantic import ValidationError

from cerebro.models import (
    MaterializationReceipt,
    MaterializedTableReceipt,
    SemanticBundle,
    SourceManifest,
)
from cerebro.provenance import (
    canonical_json_bytes,
    manifest_table_id,
    semantic_bundle_sha256,
    source_manifest_sha256,
)


class _LoaderOSFacade:
    """Keep loader fault-injection seams local to this module."""

    def __getattr__(self, name: str) -> Any:
        return getattr(_stdlib_os, name)


os = _LoaderOSFacade()

_IDENTIFIER = re.compile(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*")
_DATA_TYPE = re.compile(
    r"[A-Z][A-Z0-9_]*(?: [A-Z][A-Z0-9_]*)*"
    r"(?:\([0-9]+(?:,[0-9]+)?\))?(?:\[\])*"
)
_HARD_LINK = os.link
_TARGET_RESTORE_RETRY_LIMIT = 128


class LoadError(RuntimeError):
    """The source set could not be safely materialized."""


def _public_load_error(failure: Exception, *, fallback: str) -> LoadError:
    """Create a chain-free public error from trusted category text only."""
    if isinstance(failure, LoadError) and len(failure.args) == 1:
        message = failure.args[0]
        if type(message) is str and message:
            return LoadError(message)
    return LoadError(fallback)


_DATABASE_LOCK_PROBE_TIMEOUT_SECONDS = 2.0
_DATABASE_LOCK_UNSUPPORTED_EXIT = 74
_DATABASE_LOCK_OWNER_OUTPUT = re.compile(rb"PID:([1-9][0-9]*)\n")
_DATABASE_LOCK_PROBE_SCRIPT = f"""
import fcntl
import os
import struct
import sys

UNSUPPORTED_EXIT = {_DATABASE_LOCK_UNSUPPORTED_EXIT}

try:
    descriptor = int(sys.argv[1])
except (IndexError, ValueError):
    raise SystemExit(UNSUPPORTED_EXIT)

if struct.calcsize("P") != 8:
    raise SystemExit(UNSUPPORTED_EXIT)
if sys.platform == "darwin":
    layout = "@qqihh"
    expected_size = 24
    request = struct.pack(
        layout,
        0,
        0,
        0,
        fcntl.F_WRLCK,
        os.SEEK_SET,
    )
    type_index = 3
    pid_index = 2
elif sys.platform.startswith("linux"):
    layout = "@hhqqi4x"
    expected_size = 32
    request = struct.pack(
        layout,
        fcntl.F_WRLCK,
        os.SEEK_SET,
        0,
        0,
        0,
    )
    type_index = 0
    pid_index = 4
else:
    raise SystemExit(UNSUPPORTED_EXIT)

if struct.calcsize(layout) != expected_size or len(request) != expected_size:
    raise SystemExit(UNSUPPORTED_EXIT)
try:
    response = fcntl.fcntl(descriptor, fcntl.F_GETLK, request)
except (OSError, ValueError):
    raise SystemExit(UNSUPPORTED_EXIT)
if not isinstance(response, bytes) or len(response) != expected_size:
    raise SystemExit(UNSUPPORTED_EXIT)
try:
    fields = struct.unpack(layout, response)
except struct.error:
    raise SystemExit(UNSUPPORTED_EXIT)

lock_type = fields[type_index]
owner_pid = fields[pid_index]
if lock_type == fcntl.F_UNLCK:
    os.write(1, b"UNLOCKED\\n")
elif lock_type in (fcntl.F_RDLCK, fcntl.F_WRLCK) and owner_pid > 0:
    os.write(1, b"PID:" + str(owner_pid).encode("ascii") + b"\\n")
else:
    raise SystemExit(UNSUPPORTED_EXIT)
raise SystemExit(0)
"""


class _DatabaseLockStateMismatch(LoadError):
    def __init__(self, observed: str) -> None:
        super().__init__("DuckDB connection is not bound to the retained database")
        self.observed = observed


class _DatabaseLockOwnershipMismatch(LoadError):
    def __init__(self) -> None:
        super().__init__(
            "DuckDB connection lock owner does not match the retained database"
        )


def _probe_database_lock(descriptor: int) -> str:
    """Probe the POSIX whole-file lock owner from a short local child."""
    try:
        descriptor_stat = os.fstat(descriptor)
    except OSError:
        raise LoadError("database lock probe descriptor is unavailable") from None
    if not stat.S_ISREG(descriptor_stat.st_mode):
        raise LoadError("database lock probe descriptor is not a regular file")

    try:
        completed = subprocess.run(
            [
                sys.executable,
                "-I",
                "-c",
                _DATABASE_LOCK_PROBE_SCRIPT,
                str(descriptor),
            ],
            check=False,
            close_fds=True,
            pass_fds=(descriptor,),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=_DATABASE_LOCK_PROBE_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError, ValueError):
        raise LoadError("database lock probe is unsupported or unavailable") from None

    if completed.returncode != 0:
        raise LoadError("database lock probe returned an unsupported result")
    output = completed.stdout
    if not isinstance(output, bytes) or len(output) > 64:
        raise LoadError("database lock probe returned an unsupported result")
    if output == b"UNLOCKED\n":
        return "acquired"
    owner_match = _DATABASE_LOCK_OWNER_OUTPUT.fullmatch(output)
    if owner_match is None:
        raise LoadError("database lock probe returned an unsupported result")
    owner_pid = int(owner_match.group(1))
    if owner_pid > 2_147_483_647:
        raise LoadError("database lock probe returned an unsupported result")
    if owner_pid != os.getpid():
        raise _DatabaseLockOwnershipMismatch
    return "blocked"


def _require_database_lock_state(descriptor: int, expected: str) -> None:
    if expected not in {"acquired", "blocked"}:
        raise LoadError("invalid database lock transition expectation")
    observed = _probe_database_lock(descriptor)
    if observed != expected:
        raise _DatabaseLockStateMismatch(observed)


@dataclass(frozen=True)
class SourceColumn:
    name: str
    data_type: str


@dataclass(frozen=True)
class SourceTableInventory:
    name: str
    table_id: str
    source_file: Path
    source_file_sha256: str
    row_count: int
    columns: tuple[SourceColumn, ...]
    ddl: str


@dataclass(frozen=True)
class SourceInventory:
    source_manifest_sha256: str
    bundle_sha256: str
    tables: tuple[SourceTableInventory, ...]


@dataclass(frozen=True)
class TargetBackup:
    descriptor: int
    sha256: str
    workspace_name: str
    workspace_identity: tuple[int, int]
    original_identity: tuple[int, int]


def _quote_identifier(identifier: str) -> str:
    # Callers pass only names accepted by _IDENTIFIER.
    return f'"{identifier}"'


def _validated_data_type(value: Any, *, table_name: str, column_name: str) -> str:
    del table_name, column_name
    if not isinstance(value, str) or _DATA_TYPE.fullmatch(value) is None:
        raise LoadError("invalid data type in active table metadata")
    try:
        canonical = str(duckdb.sqltype(value))
    except (TypeError, ValueError, duckdb.Error):
        raise LoadError("invalid data type in active table metadata") from None
    if canonical != value:
        raise LoadError("data type in active table metadata must be canonical")
    return value


def _active_bundle_tables(
    bundle: SemanticBundle,
) -> dict[str, tuple[SourceColumn, ...]]:
    if not isinstance(bundle, SemanticBundle):
        raise LoadError("bundle must be a SemanticBundle")

    tables: dict[str, tuple[SourceColumn, ...]] = {}
    for table in bundle.objects:
        if table.profile_kind != "physical_table" or table.status not in {"active", "stable"}:
            continue
        prefix, separator, raw_name = table.id.partition(".")
        if (
            prefix != "table"
            or separator != "."
            or _IDENTIFIER.fullmatch(raw_name) is None
            or manifest_table_id(raw_name) != table.id
        ):
            raise LoadError("invalid active table ID")
        if raw_name in tables:
            raise LoadError("duplicate active table metadata")

        raw_columns = table.cerebro.get("columns")
        if not isinstance(raw_columns, list) or not raw_columns:
            raise LoadError("active table has no column metadata")
        columns: list[SourceColumn] = []
        names: set[str] = set()
        for raw_column in raw_columns:
            if not isinstance(raw_column, dict):
                raise LoadError("invalid column metadata in active table")
            column_name = raw_column.get("name")
            if (
                not isinstance(column_name, str)
                or _IDENTIFIER.fullmatch(column_name) is None
            ):
                raise LoadError("invalid column name in active table metadata")
            if column_name in names:
                raise LoadError("duplicate column metadata")
            names.add(column_name)
            data_type = _validated_data_type(
                raw_column.get("data_type"),
                table_name=raw_name,
                column_name=column_name,
            )
            columns.append(SourceColumn(name=column_name, data_type=data_type))
        tables[raw_name] = tuple(columns)

    if not tables:
        raise LoadError("bundle has no active table metadata")
    return tables


def _csv_relation(columns: tuple[SourceColumn, ...]) -> str:
    declarations = ",".join(f"'{column.name}': 'VARCHAR'" for column in columns)
    return (
        "read_csv(?, header = true, auto_detect = false, "
        f"columns = {{{declarations}}}, strict_mode = true)"
    )


def _table_ddl(name: str, columns: tuple[SourceColumn, ...]) -> str:
    projections = ", ".join(
        f"CAST({_quote_identifier(column.name)} AS {column.data_type}) "
        f"AS {_quote_identifier(column.name)}"
        for column in columns
    )
    return (
        f"CREATE TABLE {_quote_identifier(name)} AS "
        f"SELECT {projections} FROM {_csv_relation(columns)}"
    )


def _read_csv_shape(source_file: Path) -> tuple[tuple[str, ...], int]:
    try:
        with source_file.open(encoding="utf-8", newline="") as source:
            reader = csv.reader(source, strict=True)
            raw_header = next(reader, None)
            if raw_header is None:
                return (), 0
            header = tuple(raw_header)
            row_count = 0
            for row in reader:
                if not row:
                    raise LoadError("blank CSV row")
                if len(row) != len(header):
                    raise LoadError(
                        "CSV row width mismatch "
                        f"(expected {len(header)}, got {len(row)})"
                    )
                row_count += 1
            return header, row_count
    except UnicodeDecodeError:
        raise LoadError("CSV is not valid UTF-8") from None
    except csv.Error:
        raise LoadError("invalid CSV syntax") from None
    except OSError:
        raise LoadError("could not read CSV file") from None


def _execute_ddl(
    connection: duckdb.DuckDBPyConnection,
    source_file: Path,
    ddl: str,
) -> int:
    result = connection.execute(ddl, [str(source_file)]).fetchone()
    if result is None or len(result) != 1 or not isinstance(result[0], int):
        raise LoadError("DuckDB did not report a materialized row count")
    return result[0]


def _check_castability(table: SourceTableInventory) -> int:
    connection = duckdb.connect(":memory:")
    try:
        return _execute_ddl(connection, table.source_file, table.ddl)
    except LoadError:
        raise
    except duckdb.Error:
        raise LoadError(
            f"CSV values for table {table.name} cannot cast to declared types"
        ) from None
    finally:
        connection.close()


def _register_created_entry(
    descriptor: int,
    name: str,
    known_entries: dict[str, tuple[int, int]],
) -> os.stat_result:
    try:
        descriptor_stat = os.fstat(descriptor)
    except OSError as initial_error:
        # A one-shot metadata fault must not leave an otherwise identifiable
        # created entry outside conservative cleanup ownership.
        try:
            descriptor_stat = os.fstat(descriptor)
        except OSError:
            raise initial_error
        known_entries[name] = _identity(descriptor_stat)
        raise
    known_entries[name] = _identity(descriptor_stat)
    return descriptor_stat


def _read_csv_shape_descriptor(
    descriptor: int,
    display_name: str,
) -> tuple[tuple[str, ...], int]:
    del display_name
    duplicate = -1
    try:
        duplicate = os.dup(descriptor)
        os.lseek(duplicate, 0, os.SEEK_SET)
        source = os.fdopen(duplicate, "r", encoding="utf-8", newline="")
        duplicate = -1
        with source:
            reader = csv.reader(source, strict=True)
            raw_header = next(reader, None)
            if raw_header is None:
                return (), 0
            header = tuple(raw_header)
            row_count = 0
            for row in reader:
                if not row:
                    raise LoadError("blank CSV row")
                if len(row) != len(header):
                    raise LoadError(
                        "CSV row width mismatch "
                        f"(expected {len(header)}, got {len(row)})"
                    )
                row_count += 1
            return header, row_count
    except UnicodeDecodeError:
        raise LoadError("CSV is not valid UTF-8") from None
    except csv.Error:
        raise LoadError("invalid CSV syntax") from None
    except OSError:
        raise LoadError("could not read CSV file") from None
    finally:
        if duplicate >= 0:
            os.close(duplicate)


def _retained_descriptor_path(descriptor: int) -> Path:
    try:
        descriptor_stat = os.fstat(descriptor)
    except OSError:
        raise LoadError(
            "private preflight snapshot descriptor is unavailable"
        ) from None
    probe_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    for root in (Path("/dev/fd"), Path("/proc/self/fd")):
        candidate = root / str(descriptor)
        try:
            probe_descriptor = os.open(candidate, probe_flags)
        except OSError:
            continue
        try:
            probe_stat = os.fstat(probe_descriptor)
            if stat.S_ISREG(probe_stat.st_mode) and _identity(probe_stat) == _identity(
                descriptor_stat
            ):
                return candidate
        finally:
            os.close(probe_descriptor)
    raise LoadError("runtime cannot expose a retained preflight descriptor")


def _copy_preflight_snapshot(
    source_file: Path,
    snapshot: Path,
    workspace_descriptor: int,
    known_entries: dict[str, tuple[int, int]],
) -> tuple[int, str]:
    """Copy one source into an unlinked, retained read-only snapshot."""
    source_descriptor = _open_regular_source(source_file)
    destination_descriptor: int | None = None
    retained_descriptor: int | None = None
    flags = (
        os.O_RDWR
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    digest = sha256()
    try:
        destination_descriptor = os.open(
            snapshot.name,
            flags,
            0o600,
            dir_fd=workspace_descriptor,
        )
        destination_stat = _register_created_entry(
            destination_descriptor,
            snapshot.name,
            known_entries,
        )
        if not stat.S_ISREG(destination_stat.st_mode):
            raise LoadError("private preflight snapshot is not a regular file")
        _validate_regular_descriptor_entry(
            destination_descriptor,
            workspace_descriptor,
            snapshot.name,
            error_message="private preflight snapshot changed after creation",
        )

        with os.fdopen(source_descriptor, "rb") as source:
            source_descriptor = -1
            with os.fdopen(
                destination_descriptor,
                "wb",
                closefd=False,
            ) as destination:
                while chunk := source.read(1024 * 1024):
                    digest.update(chunk)
                    destination.write(chunk)
                destination.flush()
                os.fsync(destination_descriptor)

        os.fchmod(destination_descriptor, 0o400)
        descriptor_stat = _validate_regular_descriptor_entry(
            destination_descriptor,
            workspace_descriptor,
            snapshot.name,
            error_message="private preflight snapshot changed after copying",
        )
        if stat.S_IMODE(descriptor_stat.st_mode) != 0o400:
            raise LoadError("private preflight snapshot is not mode 0400")
        snapshot_hash = digest.hexdigest()
        if _sha256_descriptor(destination_descriptor) != snapshot_hash:
            raise LoadError("private preflight snapshot hash changed after copying")

        read_flags = (
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        )
        retained_descriptor = os.open(
            snapshot.name,
            read_flags,
            dir_fd=workspace_descriptor,
        )
        _validate_regular_descriptor_entry(
            retained_descriptor,
            workspace_descriptor,
            snapshot.name,
            error_message="retained preflight snapshot is not the copied inode",
        )
        os.unlink(snapshot.name, dir_fd=workspace_descriptor)
        known_entries.pop(snapshot.name, None)
        result_descriptor = retained_descriptor
        retained_descriptor = None
        return result_descriptor, snapshot_hash
    except OSError:
        raise LoadError("could not create private preflight snapshot") from None
    finally:
        if source_descriptor >= 0:
            os.close(source_descriptor)
        if destination_descriptor is not None:
            os.close(destination_descriptor)
        if retained_descriptor is not None:
            os.close(retained_descriptor)


def _validate_csv_file_set(
    root: Path,
    expected_names: set[str],
    expected_count: int,
) -> None:
    try:
        actual_names = {
            entry.name for entry in root.iterdir() if entry.suffix == ".csv"
        }
    except OSError:
        raise LoadError("CSV file set could not be enumerated") from None

    if actual_names != expected_names:
        raise LoadError("CSV file set mismatch")
    if len(actual_names) != expected_count:
        raise LoadError("CSV file count does not match the expected active table count")


def _preflight_csvs_impl(
    csv_dir: Path | str,
    bundle: SemanticBundle,
    manifest: SourceManifest,
) -> SourceInventory:
    """Validate every source and return immutable, name-aligned load metadata."""
    if not isinstance(manifest, SourceManifest):
        raise LoadError("manifest must be a SourceManifest")
    if not isinstance(bundle, SemanticBundle):
        raise LoadError("bundle must be a SemanticBundle")
    bundle_snapshot = SemanticBundle.model_validate(bundle.model_dump(mode="python"))
    bundle_hash = semantic_bundle_sha256(bundle_snapshot)
    root = Path(csv_dir)
    if not root.is_dir():
        raise LoadError("CSV directory does not exist")

    bundle_tables = _active_bundle_tables(bundle_snapshot)
    manifest_tables = {table.name: table for table in manifest.tables}
    bundle_names = set(bundle_tables)
    manifest_names = set(manifest_tables)
    if bundle_names != manifest_names or len(bundle_tables) != len(manifest.tables):
        raise LoadError("active bundle table set does not match source manifest")

    expected_csv_names = {table.file_name for table in manifest.tables}
    _validate_csv_file_set(root, expected_csv_names, len(bundle_tables))

    errors: list[str] = []
    inventory_tables: list[SourceTableInventory] = []
    preflight_parent = Path(tempfile.gettempdir())
    parent_descriptor: int | None = None
    workspace: Path | None = None
    workspace_descriptor: int | None = None
    workspace_identity: tuple[int, int] | None = None
    workspace_entries: dict[str, tuple[int, int]] = {}
    try:
        parent_descriptor, _ = _open_directory_descriptor(
            preflight_parent,
            error_message="private preflight parent could not be securely opened",
        )
        workspace, workspace_descriptor, workspace_identity = _create_private_workspace(
            preflight_parent / "cerebro-preflight",
            parent_descriptor=parent_descriptor,
        )
        _validate_directory_descriptor_entry(
            workspace_descriptor,
            parent_descriptor,
            workspace.name,
            required_mode=0o700,
            error_message="private preflight workspace is not in the pinned parent",
        )

        for index, name in enumerate(sorted(bundle_tables)):
            manifest_table = manifest_tables[name]
            source_file = root / manifest_table.file_name
            columns = bundle_tables[name]
            expected_header = tuple(column.name for column in columns)
            ddl = _table_ddl(name, columns)
            table_inventory = SourceTableInventory(
                name=name,
                table_id=manifest_table_id(name),
                source_file=source_file,
                source_file_sha256=manifest_table.sha256,
                row_count=manifest_table.row_count,
                columns=columns,
                ddl=ddl,
            )
            inventory_tables.append(table_inventory)
            snapshot = workspace / f"source-{index:04d}.csv"
            snapshot_descriptor: int | None = None

            try:
                snapshot_descriptor, actual_hash = _copy_preflight_snapshot(
                    source_file,
                    snapshot,
                    workspace_descriptor,
                    workspace_entries,
                )
            except LoadError as exc:
                errors.append(str(exc))
                continue

            try:
                if actual_hash != manifest_table.sha256:
                    errors.append("SHA-256 mismatch for source file")

                try:
                    actual_header, actual_count = _read_csv_shape_descriptor(
                        snapshot_descriptor,
                        manifest_table.file_name,
                    )
                    if actual_header != expected_header:
                        errors.append("header mismatch for CSV file")
                    if actual_count != manifest_table.row_count:
                        errors.append(
                            "row count mismatch for CSV file "
                            f"(expected {manifest_table.row_count}, "
                            f"got {actual_count})"
                        )
                except LoadError as exc:
                    errors.append(str(exc))

                descriptor_path = _retained_descriptor_path(snapshot_descriptor)
                snapshot_inventory = SourceTableInventory(
                    name=table_inventory.name,
                    table_id=table_inventory.table_id,
                    source_file=descriptor_path,
                    source_file_sha256=table_inventory.source_file_sha256,
                    row_count=table_inventory.row_count,
                    columns=table_inventory.columns,
                    ddl=table_inventory.ddl,
                )
                try:
                    os.lseek(snapshot_descriptor, 0, os.SEEK_SET)
                    cast_count = _check_castability(snapshot_inventory)
                    if cast_count != manifest_table.row_count:
                        errors.append(
                            "cast row count mismatch for CSV file "
                            f"(expected {manifest_table.row_count}, "
                            f"got {cast_count})"
                        )
                except LoadError as exc:
                    errors.append(str(exc))
                except OSError:
                    errors.append("could not rewind retained source snapshot")

                if _sha256_descriptor(snapshot_descriptor) != actual_hash:
                    errors.append("private preflight snapshot changed while validating")
            finally:
                os.close(snapshot_descriptor)

        _validate_directory_descriptor_entry(
            workspace_descriptor,
            parent_descriptor,
            workspace.name,
            required_mode=0o700,
            error_message="private preflight workspace changed during validation",
        )
        _validate_csv_file_set(root, expected_csv_names, len(bundle_tables))
    finally:
        _remove_private_workspace(
            workspace,
            descriptor=workspace_descriptor,
            expected_identity=workspace_identity,
            known_entries=workspace_entries,
            parent_descriptor=parent_descriptor,
        )
        if workspace_descriptor is not None:
            os.close(workspace_descriptor)
        if parent_descriptor is not None:
            os.close(parent_descriptor)

    if errors:
        raise LoadError("CSV preflight failed: " + "; ".join(errors))
    return SourceInventory(
        source_manifest_sha256=source_manifest_sha256(manifest),
        bundle_sha256=bundle_hash,
        tables=tuple(inventory_tables),
    )


def preflight_csvs(
    csv_dir: Path | str,
    bundle: SemanticBundle,
    manifest: SourceManifest,
) -> SourceInventory:
    """Validate every source and return immutable, name-aligned load metadata."""
    failure: Exception | None = None
    try:
        return _preflight_csvs_impl(csv_dir, bundle, manifest)
    except Exception as error:  # noqa: BLE001 - public exception boundary
        failure = error
    if failure is None:  # pragma: no cover - the try either returns or captures
        raise LoadError("CSV preflight failed")
    raise _public_load_error(failure, fallback="CSV preflight failed")


def _load_table(
    connection: duckdb.DuckDBPyConnection,
    source_file: Path,
    ddl: str,
) -> int:
    """Load one validated source; kept injectable for atomicity tests."""
    try:
        return _execute_ddl(connection, source_file, ddl)
    except LoadError:
        raise
    except duckdb.Error:
        raise LoadError("failed loading materialized table") from None


def _verify_materialization(
    connection: duckdb.DuckDBPyConnection,
    inventory: SourceInventory,
) -> None:
    actual_tables = {
        row[0]
        for row in connection.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'main' AND table_type = 'BASE TABLE'"
        ).fetchall()
    }
    expected_tables = {table.name for table in inventory.tables}
    if actual_tables != expected_tables:
        raise LoadError("materialized table set mismatch")

    for table in inventory.tables:
        actual_columns = tuple(
            (str(name), str(data_type))
            for name, data_type in connection.execute(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_schema = 'main' AND table_name = ? "
                "ORDER BY ordinal_position",
                [table.name],
            ).fetchall()
        )
        expected_columns = tuple(
            (column.name, column.data_type) for column in table.columns
        )
        if actual_columns != expected_columns:
            raise LoadError(f"materialized schema mismatch for table {table.name}")
        actual_count = connection.execute(
            f"SELECT count(*) FROM {_quote_identifier(table.name)}"
        ).fetchone()
        if actual_count is None or actual_count[0] != table.row_count:
            got = None if actual_count is None else actual_count[0]
            raise LoadError(
                f"materialized row count mismatch for {table.name}: "
                f"expected {table.row_count}, got {got}"
            )


def _validate_regular_descriptor_path(
    descriptor: int,
    path: Path,
    *,
    error_message: str,
) -> os.stat_result:
    try:
        descriptor_stat = os.fstat(descriptor)
        path_stat = os.stat(path, follow_symlinks=False)
    except OSError:
        raise LoadError(error_message) from None
    if (
        not stat.S_ISREG(descriptor_stat.st_mode)
        or not stat.S_ISREG(path_stat.st_mode)
        or descriptor_stat.st_dev != path_stat.st_dev
        or descriptor_stat.st_ino != path_stat.st_ino
    ):
        raise LoadError(error_message)
    return descriptor_stat


def _identity(value: os.stat_result) -> tuple[int, int]:
    return value.st_dev, value.st_ino


def _close_best_effort(descriptor: int | None) -> None:
    if descriptor is None or descriptor < 0:
        return
    try:
        os.close(descriptor)
    except OSError:
        pass


def _validate_directory_descriptor_path(
    descriptor: int,
    path: Path,
    *,
    required_mode: int | None = None,
    error_message: str,
) -> os.stat_result:
    try:
        descriptor_stat = os.fstat(descriptor)
        path_stat = os.stat(path, follow_symlinks=False)
    except OSError:
        raise LoadError(error_message) from None
    if (
        not stat.S_ISDIR(descriptor_stat.st_mode)
        or not stat.S_ISDIR(path_stat.st_mode)
        or _identity(descriptor_stat) != _identity(path_stat)
        or (
            required_mode is not None
            and stat.S_IMODE(descriptor_stat.st_mode) != required_mode
        )
    ):
        raise LoadError(error_message)
    return descriptor_stat


def _validate_directory_descriptor_entry(
    descriptor: int,
    parent_descriptor: int,
    name: str,
    *,
    required_mode: int | None = None,
    error_message: str,
) -> os.stat_result:
    try:
        descriptor_stat = os.fstat(descriptor)
        entry_stat = os.stat(
            name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
    except OSError:
        raise LoadError(error_message) from None
    if (
        not stat.S_ISDIR(descriptor_stat.st_mode)
        or not stat.S_ISDIR(entry_stat.st_mode)
        or _identity(descriptor_stat) != _identity(entry_stat)
        or (
            required_mode is not None
            and stat.S_IMODE(descriptor_stat.st_mode) != required_mode
        )
    ):
        raise LoadError(error_message)
    return descriptor_stat


def _open_directory_descriptor(
    path: Path,
    *,
    required_mode: int | None = None,
    error_message: str,
) -> tuple[int, os.stat_result]:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(path, flags)
    except OSError:
        raise LoadError(error_message) from None
    try:
        descriptor_stat = _validate_directory_descriptor_path(
            descriptor,
            path,
            required_mode=required_mode,
            error_message=error_message,
        )
        return descriptor, descriptor_stat
    except Exception:
        os.close(descriptor)
        raise


def _validate_regular_descriptor_entry(
    descriptor: int,
    directory_descriptor: int,
    name: str,
    *,
    error_message: str,
) -> os.stat_result:
    try:
        descriptor_stat = os.fstat(descriptor)
        entry_stat = os.stat(
            name,
            dir_fd=directory_descriptor,
            follow_symlinks=False,
        )
    except OSError:
        raise LoadError(error_message) from None
    if (
        not stat.S_ISREG(descriptor_stat.st_mode)
        or not stat.S_ISREG(entry_stat.st_mode)
        or _identity(descriptor_stat) != _identity(entry_stat)
    ):
        raise LoadError(error_message)
    return descriptor_stat


def _open_regular_directory_entry(
    directory_descriptor: int,
    name: str,
    *,
    error_message: str,
    known_entries: dict[str, tuple[int, int]] | None = None,
) -> tuple[int, os.stat_result]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(name, flags, dir_fd=directory_descriptor)
    except OSError:
        raise LoadError(error_message) from None
    try:
        if known_entries is not None:
            _register_created_entry(descriptor, name, known_entries)
        descriptor_stat = _validate_regular_descriptor_entry(
            descriptor,
            directory_descriptor,
            name,
            error_message=error_message,
        )
        return descriptor, descriptor_stat
    except Exception:
        os.close(descriptor)
        raise


def _sha256_descriptor(descriptor: int) -> str:
    digest = sha256()
    offset = 0
    while chunk := os.pread(descriptor, 1024 * 1024, offset):
        digest.update(chunk)
        offset += len(chunk)
    return digest.hexdigest()


def _copy_descriptor_contents(
    source_descriptor: int, destination_descriptor: int
) -> str:
    digest = sha256()
    offset = 0
    while True:
        chunk = os.pread(source_descriptor, 1024 * 1024, offset)
        if not chunk:
            return digest.hexdigest()
        digest.update(chunk)
        view = memoryview(chunk)
        while view:
            written = os.write(destination_descriptor, view)
            if written <= 0:
                raise LoadError("descriptor snapshot write was incomplete")
            view = view[written:]
        offset += len(chunk)


def _create_read_only_workspace_copy(
    source_descriptor: int,
    workspace_descriptor: int,
    name: str,
    known_entries: dict[str, tuple[int, int]],
    *,
    expected_hash: str | None,
    error_message: str,
) -> tuple[int, tuple[int, int], str]:
    if Path(name).name != name:
        raise LoadError(error_message)
    writable_descriptor: int | None = None
    retained_descriptor: int | None = None
    flags = (
        os.O_RDWR
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        writable_descriptor = os.open(
            name,
            flags,
            0o600,
            dir_fd=workspace_descriptor,
        )
        created_stat = _register_created_entry(
            writable_descriptor,
            name,
            known_entries,
        )
        created_identity = _identity(created_stat)
        _validate_regular_descriptor_entry(
            writable_descriptor,
            workspace_descriptor,
            name,
            error_message=error_message,
        )
        copied_hash = _copy_descriptor_contents(
            source_descriptor,
            writable_descriptor,
        )
        os.fsync(writable_descriptor)
        os.fchmod(writable_descriptor, 0o400)
        os.fsync(writable_descriptor)
        readonly_stat = _validate_regular_descriptor_entry(
            writable_descriptor,
            workspace_descriptor,
            name,
            error_message=error_message,
        )
        if (
            _identity(readonly_stat) != created_identity
            or stat.S_IMODE(readonly_stat.st_mode) != 0o400
        ):
            raise LoadError(error_message)

        read_flags = (
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        )
        retained_descriptor = os.open(
            name,
            read_flags,
            dir_fd=workspace_descriptor,
        )
        retained_stat = _validate_regular_descriptor_entry(
            retained_descriptor,
            workspace_descriptor,
            name,
            error_message=error_message,
        )
        if _identity(retained_stat) != created_identity:
            raise LoadError(error_message)
        retained_hash = _sha256_descriptor(retained_descriptor)
        if retained_hash != copied_hash or (
            expected_hash is not None and retained_hash != expected_hash
        ):
            raise LoadError(error_message)
        result = retained_descriptor
        retained_descriptor = None
        return result, created_identity, retained_hash
    except OSError:
        raise LoadError(error_message) from None
    finally:
        _close_best_effort(writable_descriptor)
        _close_best_effort(retained_descriptor)


def _link_no_follow(
    source_name: str,
    destination_name: str,
    *,
    src_dir_fd: int,
    dst_dir_fd: int,
) -> None:
    if _HARD_LINK not in getattr(os, "supports_follow_symlinks", ()):
        raise LoadError("secure receipt publication is not supported")
    try:
        os.link(
            source_name,
            destination_name,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
            follow_symlinks=False,
        )
    except FileExistsError:
        raise
    except OSError:
        raise LoadError("receipt finalize failure") from None


def _read_bounded_descriptor(descriptor: int, limit: int) -> bytes:
    contents = bytearray()
    while len(contents) < limit:
        chunk = os.read(descriptor, min(64 * 1024, limit - len(contents)))
        if not chunk:
            break
        contents.extend(chunk)
    return bytes(contents)


def _read_bounded_descriptor_from_start(descriptor: int, limit: int) -> bytes:
    contents = bytearray()
    while len(contents) < limit:
        try:
            chunk = os.pread(
                descriptor,
                min(64 * 1024, limit - len(contents)),
                len(contents),
            )
        except OSError:
            raise LoadError("retained receipt content could not be read") from None
        if not chunk:
            break
        contents.extend(chunk)
    return bytes(contents)


def _validate_retained_receipt(
    descriptor: int,
    directory_descriptor: int,
    name: str,
    receipt_bytes: bytes,
    *,
    error_message: str,
) -> None:
    _validate_regular_descriptor_entry(
        descriptor,
        directory_descriptor,
        name,
        error_message=error_message,
    )
    retained_bytes = _read_bounded_descriptor_from_start(
        descriptor,
        len(receipt_bytes) + 1,
    )
    if retained_bytes != receipt_bytes:
        raise LoadError(error_message)
    try:
        retained = MaterializationReceipt.model_validate_json(retained_bytes)
    except ValidationError:
        raise LoadError(error_message) from None
    if canonical_json_bytes(retained) != receipt_bytes:
        raise LoadError(error_message)
    _validate_regular_descriptor_entry(
        descriptor,
        directory_descriptor,
        name,
        error_message=error_message,
    )


def _validate_existing_receipt(
    directory_descriptor: int,
    name: str,
    receipt_bytes: bytes,
) -> None:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_NONBLOCK", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(name, flags, dir_fd=directory_descriptor)
    except OSError:
        raise LoadError("pre-existing receipt could not be securely opened") from None

    try:
        descriptor_stat = _validate_regular_descriptor_entry(
            descriptor,
            directory_descriptor,
            name,
            error_message="pre-existing receipt is not the opened regular file",
        )
        if not stat.S_ISREG(descriptor_stat.st_mode):
            raise LoadError("pre-existing receipt is not a regular file")
        try:
            existing_bytes = _read_bounded_descriptor(
                descriptor,
                len(receipt_bytes) + 1,
            )
        except OSError:
            raise LoadError("pre-existing receipt could not be read") from None
        if existing_bytes != receipt_bytes:
            raise LoadError("content-addressed receipt collision")
        try:
            existing = MaterializationReceipt.model_validate_json(existing_bytes)
        except ValidationError:
            raise LoadError("pre-existing receipt is invalid") from None
        if canonical_json_bytes(existing) != receipt_bytes:
            raise LoadError("pre-existing receipt is not canonical")
        _validate_regular_descriptor_entry(
            descriptor,
            directory_descriptor,
            name,
            error_message="pre-existing receipt changed while validating",
        )
    finally:
        _close_best_effort(descriptor)


def _validate_published_receipt(
    *,
    uses_retained_inode: bool,
    retained_descriptor: int,
    directory_descriptor: int,
    name: str,
    receipt_bytes: bytes,
    error_message: str,
) -> None:
    if uses_retained_inode:
        _validate_retained_receipt(
            retained_descriptor,
            directory_descriptor,
            name,
            receipt_bytes,
            error_message=error_message,
        )
        return
    try:
        _validate_existing_receipt(
            directory_descriptor,
            name,
            receipt_bytes,
        )
    except LoadError:
        raise LoadError(error_message) from None


def _create_private_workspace(
    target: Path,
    *,
    parent_descriptor: int,
) -> tuple[Path, int, tuple[int, int]]:
    workspace_name: str | None = None
    for _ in range(128):
        candidate = f".{target.name}.{secrets.token_hex(16)}.tmp"
        try:
            os.mkdir(candidate, 0o700, dir_fd=parent_descriptor)
        except FileExistsError:
            continue
        except OSError:
            raise LoadError(
                "could not create private materialization workspace"
            ) from None
        workspace_name = candidate
        break
    if workspace_name is None:
        raise LoadError("could not allocate private materialization workspace")

    workspace = target.parent / workspace_name
    descriptor: int | None = None
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(
            workspace_name,
            flags,
            dir_fd=parent_descriptor,
        )
        os.fchmod(descriptor, 0o700)
        descriptor_stat = _validate_directory_descriptor_entry(
            descriptor,
            parent_descriptor,
            workspace_name,
            required_mode=0o700,
            error_message="private materialization workspace is not mode 0700",
        )
        return workspace, descriptor, _identity(descriptor_stat)
    except Exception:
        if descriptor is not None:
            try:
                descriptor_stat = os.fstat(descriptor)
                entry_stat = os.stat(
                    workspace_name,
                    dir_fd=parent_descriptor,
                    follow_symlinks=False,
                )
                if stat.S_ISDIR(entry_stat.st_mode) and _identity(
                    entry_stat
                ) == _identity(descriptor_stat):
                    os.rmdir(workspace_name, dir_fd=parent_descriptor)
            except OSError:
                pass
            _close_best_effort(descriptor)
        else:
            try:
                entry_stat = os.stat(
                    workspace_name,
                    dir_fd=parent_descriptor,
                    follow_symlinks=False,
                )
                if stat.S_ISDIR(entry_stat.st_mode):
                    os.rmdir(workspace_name, dir_fd=parent_descriptor)
            except OSError:
                pass
        raise


def _remove_private_workspace(
    workspace: Path | None,
    *,
    descriptor: int | None,
    expected_identity: tuple[int, int] | None,
    known_entries: dict[str, tuple[int, int]],
    parent_descriptor: int | None = None,
    remove_mismatched_symlinks: bool = False,
) -> None:
    if workspace is None or descriptor is None or expected_identity is None:
        return
    try:
        descriptor_stat = os.fstat(descriptor)
    except OSError:
        return
    if (
        not stat.S_ISDIR(descriptor_stat.st_mode)
        or _identity(descriptor_stat) != expected_identity
    ):
        return

    for name, entry_identity in sorted(known_entries.items()):
        if Path(name).name != name:
            continue
        try:
            entry_stat = os.stat(
                name,
                dir_fd=descriptor,
                follow_symlinks=False,
            )
        except OSError:
            continue
        matches = _identity(entry_stat) == entry_identity
        if not matches and not (
            remove_mismatched_symlinks and stat.S_ISLNK(entry_stat.st_mode)
        ):
            continue
        try:
            os.unlink(name, dir_fd=descriptor)
        except OSError:
            pass

    try:
        if parent_descriptor is not None:
            workspace_stat = os.stat(
                workspace.name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
        else:
            workspace_stat = os.stat(workspace, follow_symlinks=False)
    except OSError:
        return
    if (
        not stat.S_ISDIR(workspace_stat.st_mode)
        or _identity(workspace_stat) != expected_identity
    ):
        return
    try:
        if parent_descriptor is not None:
            os.rmdir(workspace.name, dir_fd=parent_descriptor)
        else:
            os.rmdir(workspace)
    except OSError:
        # Unknown or substituted entries make the directory non-empty. Leak it
        # safely rather than recurse into data this invocation does not own.
        pass


def _open_regular_source(source_file: Path) -> int:
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | no_follow
    try:
        descriptor = os.open(source_file, flags)
    except OSError:
        raise LoadError("could not securely open source file") from None

    try:
        descriptor_stat = os.fstat(descriptor)
        if not stat.S_ISREG(descriptor_stat.st_mode):
            raise LoadError("source is not a regular file")
        if no_follow == 0:
            path_stat = os.stat(source_file, follow_symlinks=False)
            if (
                not stat.S_ISREG(path_stat.st_mode)
                or path_stat.st_dev != descriptor_stat.st_dev
                or path_stat.st_ino != descriptor_stat.st_ino
            ):
                raise LoadError("source path is not the opened regular file")
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _copy_verified_snapshot(
    table: SourceTableInventory,
    workspace: Path,
    workspace_descriptor: int,
    known_entries: dict[str, tuple[int, int]],
    index: int,
) -> tuple[Path, int]:
    snapshot = workspace / f"source-{index:04d}.csv"
    _validate_directory_descriptor_path(
        workspace_descriptor,
        workspace,
        required_mode=0o700,
        error_message="source snapshot workspace is not the pinned mode-0700 directory",
    )
    try:
        snapshot_descriptor, snapshot_hash = _copy_preflight_snapshot(
            table.source_file,
            snapshot,
            workspace_descriptor,
            known_entries,
        )
    except LoadError as exc:
        if str(exc).startswith("could not securely open source"):
            raise
        raise LoadError("could not create verified snapshot") from None
    if snapshot_hash != table.source_file_sha256:
        os.close(snapshot_descriptor)
        raise LoadError("source changed after preflight")
    try:
        return _retained_descriptor_path(snapshot_descriptor), snapshot_descriptor
    except Exception:
        os.close(snapshot_descriptor)
        raise


def _snapshot_inventory(
    inventory: SourceInventory,
    workspace: Path,
    workspace_descriptor: int,
    known_entries: dict[str, tuple[int, int]],
) -> tuple[SourceInventory, tuple[int, ...]]:
    snapshot_tables: list[SourceTableInventory] = []
    snapshot_descriptors: list[int] = []
    try:
        for index, table in enumerate(inventory.tables):
            snapshot, snapshot_descriptor = _copy_verified_snapshot(
                table,
                workspace,
                workspace_descriptor,
                known_entries,
                index,
            )
            snapshot_descriptors.append(snapshot_descriptor)
            snapshot_tables.append(
                SourceTableInventory(
                    name=table.name,
                    table_id=table.table_id,
                    source_file=snapshot,
                    source_file_sha256=table.source_file_sha256,
                    row_count=table.row_count,
                    columns=table.columns,
                    ddl=table.ddl,
                )
            )
    except Exception:
        for descriptor in snapshot_descriptors:
            os.close(descriptor)
        raise
    return (
        SourceInventory(
            source_manifest_sha256=inventory.source_manifest_sha256,
            bundle_sha256=inventory.bundle_sha256,
            tables=tuple(snapshot_tables),
        ),
        tuple(snapshot_descriptors),
    )


def _create_database_path_fence(
    path: Path,
    workspace_descriptor: int,
    known_entries: dict[str, tuple[int, int]],
) -> tuple[Path, int, tuple[int, int]]:
    fence_name: str | None = None
    fence_identity: tuple[int, int] | None = None
    for _ in range(128):
        candidate = f".database-{secrets.token_hex(16)}.fence"
        try:
            os.mkdir(candidate, 0o700, dir_fd=workspace_descriptor)
        except FileExistsError:
            continue
        except OSError:
            raise LoadError("could not create private database path fence") from None
        fence_name = candidate
        try:
            created_stat = os.stat(
                fence_name,
                dir_fd=workspace_descriptor,
                follow_symlinks=False,
            )
        except OSError:
            raise LoadError(
                "private database path fence could not be retained"
            ) from None
        if not stat.S_ISDIR(created_stat.st_mode):
            raise LoadError("private database path fence is not a directory")
        fence_identity = _identity(created_stat)
        known_entries[fence_name] = fence_identity
        break
    if fence_name is None or fence_identity is None:
        raise LoadError("could not allocate private database path fence")

    fence_path = path.parent / fence_name
    fence_descriptor: int | None = None
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        fence_descriptor = os.open(
            fence_name,
            flags,
            dir_fd=workspace_descriptor,
        )
        os.fchmod(fence_descriptor, 0o700)
        descriptor_stat = _validate_directory_descriptor_entry(
            fence_descriptor,
            workspace_descriptor,
            fence_name,
            required_mode=0o700,
            error_message="private database path fence changed during creation",
        )
        if _identity(descriptor_stat) != fence_identity:
            raise LoadError("private database path fence changed during creation")
        _validate_directory_descriptor_path(
            fence_descriptor,
            fence_path,
            required_mode=0o700,
            error_message="private database path fence is not pathname-bound",
        )
        return fence_path, fence_descriptor, fence_identity
    except Exception:
        if fence_descriptor is not None:
            _remove_private_workspace(
                fence_path,
                descriptor=fence_descriptor,
                expected_identity=fence_identity,
                known_entries={},
                parent_descriptor=workspace_descriptor,
            )
        elif fence_identity is not None:
            try:
                entry_stat = os.stat(
                    fence_name,
                    dir_fd=workspace_descriptor,
                    follow_symlinks=False,
                )
                if (
                    stat.S_ISDIR(entry_stat.st_mode)
                    and _identity(entry_stat) == fence_identity
                ):
                    os.rmdir(fence_name, dir_fd=workspace_descriptor)
            except OSError:
                pass
        try:
            os.stat(
                fence_name,
                dir_fd=workspace_descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            known_entries.pop(fence_name, None)
        except OSError:
            pass
        _close_best_effort(fence_descriptor)
        raise


def _register_expected_database_entry_best_effort(
    directory_descriptor: int,
    name: str,
    known_entries: dict[str, tuple[int, int]],
) -> None:
    if name in known_entries:
        return
    descriptor: int | None = None
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(name, flags, dir_fd=directory_descriptor)
        descriptor_stat = _validate_regular_descriptor_entry(
            descriptor,
            directory_descriptor,
            name,
            error_message="database fence entry is not the opened regular file",
        )
        known_entries[name] = _identity(descriptor_stat)
    except (LoadError, OSError):
        pass
    finally:
        _close_best_effort(descriptor)


def _remove_database_path_fence_best_effort(
    fence_path: Path | None,
    *,
    fence_descriptor: int | None,
    fence_identity: tuple[int, int] | None,
    fence_entries: dict[str, tuple[int, int]],
    workspace_descriptor: int,
    workspace_entries: dict[str, tuple[int, int]],
) -> None:
    if fence_path is None:
        return
    _remove_private_workspace(
        fence_path,
        descriptor=fence_descriptor,
        expected_identity=fence_identity,
        known_entries=fence_entries,
        parent_descriptor=workspace_descriptor,
    )
    try:
        os.stat(
            fence_path.name,
            dir_fd=workspace_descriptor,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        workspace_entries.pop(fence_path.name, None)
    except OSError:
        pass


def _promote_built_database_from_fence(
    path: Path,
    database_descriptor: int,
    fence_descriptor: int,
    fence_entries: dict[str, tuple[int, int]],
    workspace_descriptor: int,
    workspace_entries: dict[str, tuple[int, int]],
) -> None:
    database_stat = _validate_regular_descriptor_entry(
        database_descriptor,
        fence_descriptor,
        path.name,
        error_message="database entry changed before leaving the path fence",
    )
    database_identity = _identity(database_stat)
    try:
        os.stat(
            path.name,
            dir_fd=workspace_descriptor,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        pass
    except OSError:
        raise LoadError(
            "private database publication path could not be inspected"
        ) from None
    else:
        raise LoadError("private database publication path already exists")
    if _HARD_LINK not in getattr(os, "supports_follow_symlinks", ()):
        raise LoadError("secure database path-fence publication is not supported")

    # Record only the retained inode as cleanup-owned before the atomic
    # create-if-absent link. A competing entry with any other identity leaks.
    workspace_entries[path.name] = database_identity
    try:
        _HARD_LINK(
            path.name,
            path.name,
            src_dir_fd=fence_descriptor,
            dst_dir_fd=workspace_descriptor,
            follow_symlinks=False,
        )
    except OSError:
        raise LoadError("database could not leave the private path fence") from None
    _validate_regular_descriptor_entry(
        database_descriptor,
        workspace_descriptor,
        path.name,
        error_message="promoted database is not the retained regular file",
    )
    _validate_regular_descriptor_entry(
        database_descriptor,
        fence_descriptor,
        path.name,
        error_message="database path-fence entry changed during promotion",
    )
    try:
        os.unlink(path.name, dir_fd=fence_descriptor)
    except OSError:
        raise LoadError("database path-fence entry could not be released") from None
    fence_entries.pop(path.name, None)


def _build_database(
    path: Path,
    inventory: SourceInventory,
    workspace_descriptor: int,
    known_entries: dict[str, tuple[int, int]],
    snapshot_descriptors: tuple[int, ...],
) -> int:
    _validate_directory_descriptor_path(
        workspace_descriptor,
        path.parent,
        required_mode=0o700,
        error_message="database workspace is not the pinned mode-0700 directory",
    )
    try:
        os.stat(
            path.name,
            dir_fd=workspace_descriptor,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        pass
    except OSError:
        raise LoadError("private database path could not be inspected") from None
    else:
        raise LoadError("private database path already exists")

    if len(snapshot_descriptors) != len(inventory.tables):
        raise LoadError("retained source snapshot count does not match inventory")
    for table, descriptor in zip(
        inventory.tables,
        snapshot_descriptors,
        strict=True,
    ):
        if _retained_descriptor_path(descriptor) != table.source_file:
            raise LoadError("source snapshot is not the retained descriptor")
        if _sha256_descriptor(descriptor) != table.source_file_sha256:
            raise LoadError(f"verified source snapshot changed: {table.name}")

    fence_path: Path | None = None
    fence_descriptor: int | None = None
    fence_identity: tuple[int, int] | None = None
    fence_entries: dict[str, tuple[int, int]] = {}
    connection: duckdb.DuckDBPyConnection | None = None
    database_descriptor: int | None = None
    transaction_open = False
    path_authority_confirmed = False

    def require_database_lock_state(descriptor: int, expected: str) -> None:
        nonlocal path_authority_confirmed
        try:
            _require_database_lock_state(descriptor, expected)
        except _DatabaseLockOwnershipMismatch:
            fence_entries.pop(path.name, None)
            path_authority_confirmed = False
            raise

    try:
        fence_path, fence_descriptor, fence_identity = _create_database_path_fence(
            path,
            workspace_descriptor,
            known_entries,
        )
        database_path = fence_path / path.name
        _validate_directory_descriptor_path(
            workspace_descriptor,
            path.parent,
            required_mode=0o700,
            error_message="database workspace changed before DuckDB connect",
        )
        _validate_directory_descriptor_path(
            fence_descriptor,
            fence_path,
            required_mode=0o700,
            error_message="database path fence changed before DuckDB connect",
        )
        try:
            connection = duckdb.connect(str(database_path))
            try:
                _validate_directory_descriptor_path(
                    workspace_descriptor,
                    path.parent,
                    required_mode=0o700,
                    error_message="database workspace changed during DuckDB connect",
                )
                _validate_directory_descriptor_path(
                    fence_descriptor,
                    fence_path,
                    required_mode=0o700,
                    error_message="database path fence changed during DuckDB connect",
                )
            except Exception:
                connection.close()
                connection = None
                raise
            database_flags = (
                os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
            )
            try:
                database_descriptor = os.open(
                    path.name,
                    database_flags,
                    dir_fd=fence_descriptor,
                )
            except OSError:
                raise LoadError("database entry could not be securely opened") from None
            try:
                database_stat = _validate_regular_descriptor_entry(
                    database_descriptor,
                    fence_descriptor,
                    path.name,
                    error_message="database entry is not the opened regular file",
                )
            except LoadError:
                try:
                    recovered_stat = _validate_regular_descriptor_entry(
                        database_descriptor,
                        fence_descriptor,
                        path.name,
                        error_message="database entry validation did not recover",
                    )
                    fence_entries[path.name] = _identity(recovered_stat)
                    path_authority_confirmed = True
                    try:
                        require_database_lock_state(database_descriptor, "blocked")
                    except _DatabaseLockStateMismatch as error:
                        if error.observed == "acquired":
                            fence_entries.pop(path.name, None)
                            path_authority_confirmed = False
                        raise
                    try:
                        connection.close()
                    finally:
                        connection = None
                    require_database_lock_state(database_descriptor, "acquired")
                    recovered_stat = _validate_regular_descriptor_entry(
                        database_descriptor,
                        fence_descriptor,
                        path.name,
                        error_message="database entry changed after lock recovery",
                    )
                except _DatabaseLockOwnershipMismatch:
                    raise
                except (LoadError, OSError, duckdb.Error):
                    raise LoadError("database entry validation failed") from None
                raise LoadError("database entry validation failed") from None
            fence_entries[path.name] = _identity(database_stat)
            path_authority_confirmed = True
            try:
                require_database_lock_state(database_descriptor, "blocked")
            except _DatabaseLockStateMismatch as error:
                if error.observed == "acquired":
                    fence_entries.pop(path.name, None)
                    path_authority_confirmed = False
                raise
            database_stat = _validate_regular_descriptor_entry(
                database_descriptor,
                fence_descriptor,
                path.name,
                error_message=(
                    "database entry changed during connection lock validation"
                ),
            )
            connection.execute("BEGIN TRANSACTION")
            transaction_open = True
            for table, descriptor in zip(
                inventory.tables,
                snapshot_descriptors,
                strict=True,
            ):
                if _sha256_descriptor(descriptor) != table.source_file_sha256:
                    raise LoadError(f"verified source snapshot changed: {table.name}")
                try:
                    os.lseek(descriptor, 0, os.SEEK_SET)
                except OSError:
                    raise LoadError(
                        f"could not rewind verified source snapshot: {table.name}"
                    ) from None
                loaded_count = _load_table(
                    connection,
                    table.source_file,
                    table.ddl,
                )
                if loaded_count != table.row_count:
                    raise LoadError(
                        f"loaded row count mismatch for {table.name}: "
                        f"expected {table.row_count}, got {loaded_count}"
                    )
                if _sha256_descriptor(descriptor) != table.source_file_sha256:
                    raise LoadError(
                        f"verified source snapshot changed while loading: {table.name}"
                    )
            _verify_materialization(connection, inventory)
            for table, descriptor in zip(
                inventory.tables,
                snapshot_descriptors,
                strict=True,
            ):
                if _sha256_descriptor(descriptor) != table.source_file_sha256:
                    raise LoadError(
                        f"verified source snapshot changed while loading: {table.name}"
                    )
            connection.execute("COMMIT")
            transaction_open = False
            _validate_regular_descriptor_entry(
                database_descriptor,
                fence_descriptor,
                path.name,
                error_message="database entry changed while connection was open",
            )
            require_database_lock_state(database_descriptor, "blocked")
        except Exception:
            if connection is not None and transaction_open:
                try:
                    connection.execute("ROLLBACK")
                except duckdb.Error:
                    pass
            raise
        finally:
            if connection is not None:
                connection.close()
                connection = None

        if database_descriptor is None:
            raise LoadError("database descriptor was not retained")
        _validate_regular_descriptor_entry(
            database_descriptor,
            fence_descriptor,
            path.name,
            error_message="database entry changed when DuckDB closed",
        )
        require_database_lock_state(database_descriptor, "acquired")
        _validate_directory_descriptor_path(
            workspace_descriptor,
            path.parent,
            required_mode=0o700,
            error_message="database workspace changed before DuckDB publication",
        )
        _validate_directory_descriptor_path(
            fence_descriptor,
            fence_path,
            required_mode=0o700,
            error_message="database path fence changed before DuckDB publication",
        )
        _validate_regular_descriptor_entry(
            database_descriptor,
            fence_descriptor,
            path.name,
            error_message="database entry changed when DuckDB closed",
        )
        wal_name = f"{path.name}.wal"
        try:
            os.stat(
                wal_name,
                dir_fd=fence_descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            pass
        except OSError:
            raise LoadError("database WAL could not be inspected after close") from None
        else:
            _register_expected_database_entry_best_effort(
                fence_descriptor,
                wal_name,
                fence_entries,
            )
            raise LoadError("database WAL remained after DuckDB closed")

        _promote_built_database_from_fence(
            path,
            database_descriptor,
            fence_descriptor,
            fence_entries,
            workspace_descriptor,
            known_entries,
        )
        return database_descriptor
    except Exception:
        _close_best_effort(database_descriptor)
        raise
    finally:
        if path_authority_confirmed and fence_descriptor is not None:
            for expected_name in (path.name, f"{path.name}.wal"):
                _register_expected_database_entry_best_effort(
                    fence_descriptor,
                    expected_name,
                    fence_entries,
                )
        _remove_database_path_fence_best_effort(
            fence_path,
            fence_descriptor=fence_descriptor,
            fence_identity=fence_identity,
            fence_entries=fence_entries,
            workspace_descriptor=workspace_descriptor,
            workspace_entries=known_entries,
        )
        _close_best_effort(fence_descriptor)


def _write_receipt_temp(
    receipt_workspace: Path,
    receipt_bytes: bytes,
    known_entries: dict[str, tuple[int, int]] | None = None,
    *,
    workspace_descriptor: int,
) -> tuple[int, str]:
    del receipt_workspace  # The retained descriptor is the authority.
    try:
        workspace_stat = os.fstat(workspace_descriptor)
    except OSError:
        raise LoadError("receipt workspace descriptor is unavailable") from None
    if (
        not stat.S_ISDIR(workspace_stat.st_mode)
        or stat.S_IMODE(workspace_stat.st_mode) != 0o700
    ):
        raise LoadError("receipt workspace is not a private mode-0700 directory")

    descriptor = -1
    read_descriptor = -1
    receipt_temp_name: str | None = None
    create_flags = (
        os.O_RDWR
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        for _ in range(128):
            candidate = f"receipt-{secrets.token_hex(16)}.tmp"
            try:
                descriptor = os.open(
                    candidate,
                    create_flags,
                    0o600,
                    dir_fd=workspace_descriptor,
                )
            except FileExistsError:
                continue
            except OSError:
                raise LoadError("could not create temporary receipt") from None
            receipt_temp_name = candidate
            break
        if receipt_temp_name is None:
            raise LoadError("could not allocate temporary receipt")

        if known_entries is not None:
            descriptor_stat = _register_created_entry(
                descriptor,
                receipt_temp_name,
                known_entries,
            )
        else:
            descriptor_stat = os.fstat(descriptor)
        descriptor_stat = _validate_regular_descriptor_entry(
            descriptor,
            workspace_descriptor,
            receipt_temp_name,
            error_message="temporary receipt entry is not the opened regular file",
        )
        if stat.S_IMODE(descriptor_stat.st_mode) != 0o600:
            os.fchmod(descriptor, 0o600)

        with os.fdopen(descriptor, "w+b", closefd=False) as destination:
            written = destination.write(receipt_bytes)
            if written != len(receipt_bytes):
                raise LoadError("temporary receipt write was incomplete")
            destination.flush()
            os.fsync(descriptor)
            destination.seek(0)
            validated_bytes = destination.read(len(receipt_bytes) + 1)

        if validated_bytes != receipt_bytes:
            raise LoadError("temporary receipt validation failed")
        validated = MaterializationReceipt.model_validate_json(validated_bytes)
        if canonical_json_bytes(validated) != receipt_bytes:
            raise LoadError("temporary receipt is not canonical")

        os.fchmod(descriptor, 0o400)
        read_flags = (
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        )
        read_descriptor = os.open(
            receipt_temp_name,
            read_flags,
            dir_fd=workspace_descriptor,
        )
        read_stat = _validate_regular_descriptor_entry(
            read_descriptor,
            workspace_descriptor,
            receipt_temp_name,
            error_message="read-only receipt is not the validated regular file",
        )
        if _identity(read_stat) != _identity(os.fstat(descriptor)):
            raise LoadError("read-only receipt is not the written receipt inode")
        os.dup2(read_descriptor, descriptor, inheritable=False)
        _close_best_effort(read_descriptor)
        read_descriptor = -1
        _validate_retained_receipt(
            descriptor,
            workspace_descriptor,
            receipt_temp_name,
            receipt_bytes,
            error_message="temporary receipt content changed before publication",
        )
        return descriptor, receipt_temp_name
    except Exception:
        _close_best_effort(read_descriptor)
        _close_best_effort(descriptor)
        raise


def _prepare_existing_target_backup(
    target_parent_descriptor: int,
    target_name: str,
    workspace_descriptor: int,
    known_entries: dict[str, tuple[int, int]],
) -> TargetBackup | None:
    try:
        target_stat = os.stat(
            target_name,
            dir_fd=target_parent_descriptor,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        return None
    except OSError:
        raise LoadError("existing database target could not be inspected") from None
    if not stat.S_ISREG(target_stat.st_mode):
        raise LoadError("existing database target is not a regular file")

    target_descriptor: int | None = None
    backup_descriptor: int | None = None
    try:
        target_descriptor, target_stat = _open_regular_directory_entry(
            target_parent_descriptor,
            target_name,
            error_message="existing database target changed while opening",
        )
        original_identity = _identity(target_stat)
        target_hash_before = _sha256_descriptor(target_descriptor)
        backup_name = "previous-target.duckdb"
        (
            backup_descriptor,
            backup_identity,
            copied_hash,
        ) = _create_read_only_workspace_copy(
            target_descriptor,
            workspace_descriptor,
            backup_name,
            known_entries,
            expected_hash=target_hash_before,
            error_message="existing database target could not be snapshotted",
        )
        _validate_regular_descriptor_entry(
            target_descriptor,
            target_parent_descriptor,
            target_name,
            error_message="existing database target changed during snapshot",
        )
        target_hash_after = _sha256_descriptor(target_descriptor)
        _validate_regular_descriptor_entry(
            target_descriptor,
            target_parent_descriptor,
            target_name,
            error_message="existing database target changed during snapshot",
        )
        if not (
            target_hash_before == copied_hash == target_hash_after
            and _identity(os.fstat(target_descriptor)) == original_identity
        ):
            raise LoadError("existing database target changed during snapshot")
        result = TargetBackup(
            descriptor=backup_descriptor,
            sha256=copied_hash,
            workspace_name=backup_name,
            workspace_identity=backup_identity,
            original_identity=original_identity,
        )
        backup_descriptor = None
        return result
    except OSError:
        raise LoadError("existing database target could not be snapshotted") from None
    finally:
        _close_best_effort(target_descriptor)
        _close_best_effort(backup_descriptor)


def _validate_named_target_backup(
    target_backup: TargetBackup,
    workspace_descriptor: int,
) -> None:
    backup_stat = _validate_regular_descriptor_entry(
        target_backup.descriptor,
        workspace_descriptor,
        target_backup.workspace_name,
        error_message="database backup is not the retained regular file",
    )
    if (
        _identity(backup_stat) != target_backup.workspace_identity
        or stat.S_IMODE(backup_stat.st_mode) != 0o400
    ):
        raise LoadError("database backup identity or mode changed")
    if _sha256_descriptor(target_backup.descriptor) != target_backup.sha256:
        raise LoadError("database backup hash changed")
    backup_stat = _validate_regular_descriptor_entry(
        target_backup.descriptor,
        workspace_descriptor,
        target_backup.workspace_name,
        error_message="database backup changed while validating",
    )
    if (
        _identity(backup_stat) != target_backup.workspace_identity
        or stat.S_IMODE(backup_stat.st_mode) != 0o400
    ):
        raise LoadError("database backup identity or mode changed")


def _revoke_target_backup_cleanup_authority(
    target_backup: TargetBackup,
    workspace_descriptor: int,
    known_entries: dict[str, tuple[int, int]],
) -> None:
    _validate_named_target_backup(target_backup, workspace_descriptor)
    known_entries.pop(target_backup.workspace_name, None)


def _authorize_target_backup_cleanup(
    target_backup: TargetBackup,
    workspace_descriptor: int,
    known_entries: dict[str, tuple[int, int]],
) -> None:
    _validate_named_target_backup(target_backup, workspace_descriptor)
    known_entries[target_backup.workspace_name] = target_backup.workspace_identity


def _entry_matches_retained_file(
    descriptor: int,
    directory_descriptor: int,
    name: str,
    expected_hash: str | None = None,
) -> bool:
    try:
        _validate_regular_descriptor_entry(
            descriptor,
            directory_descriptor,
            name,
            error_message="retained file entry changed",
        )
        return expected_hash is None or _sha256_descriptor(descriptor) == expected_hash
    except (LoadError, OSError):
        return False


def _target_matches_original_backup(
    target_parent_descriptor: int,
    target_name: str,
    backup: TargetBackup,
) -> bool:
    descriptor: int | None = None
    try:
        descriptor, descriptor_stat = _open_regular_directory_entry(
            target_parent_descriptor,
            target_name,
            error_message="database target is not the opened regular file",
        )
        if _identity(descriptor_stat) != backup.original_identity:
            return False
        if _sha256_descriptor(descriptor) != backup.sha256:
            return False
        descriptor_stat = _validate_regular_descriptor_entry(
            descriptor,
            target_parent_descriptor,
            target_name,
            error_message="database target changed while validating",
        )
        return _identity(descriptor_stat) == backup.original_identity
    except (LoadError, OSError):
        return False
    finally:
        _close_best_effort(descriptor)


def _validate_target_before_publication(
    target_parent_descriptor: int,
    target_name: str,
    backup: TargetBackup | None,
) -> None:
    if backup is None:
        try:
            os.stat(
                target_name,
                dir_fd=target_parent_descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            return
        except OSError:
            raise LoadError("database target could not be revalidated") from None
        raise LoadError("database target appeared before publication")

    descriptor: int | None = None
    try:
        descriptor, descriptor_stat = _open_regular_directory_entry(
            target_parent_descriptor,
            target_name,
            error_message="existing database target changed before publication",
        )
        if _identity(descriptor_stat) != backup.original_identity:
            raise LoadError("existing database target changed before publication")
        if _sha256_descriptor(descriptor) != backup.sha256:
            raise LoadError("existing database target changed before publication")
        descriptor_stat = _validate_regular_descriptor_entry(
            descriptor,
            target_parent_descriptor,
            target_name,
            error_message="existing database target changed before publication",
        )
        if _identity(descriptor_stat) != backup.original_identity:
            raise LoadError("existing database target changed before publication")
    finally:
        _close_best_effort(descriptor)


def _validate_published_database(
    *,
    target_parent_descriptor: int,
    target_dir: Path,
    target_name: str,
    database_descriptor: int,
    expected_hash: str,
) -> None:
    _validate_directory_descriptor_path(
        target_parent_descriptor,
        target_dir,
        error_message="database target directory changed at publication boundary",
    )
    _validate_regular_descriptor_entry(
        database_descriptor,
        target_parent_descriptor,
        target_name,
        error_message="published database is not the retained regular file",
    )
    if _sha256_descriptor(database_descriptor) != expected_hash:
        raise LoadError("published database hash changed at publication boundary")
    _validate_regular_descriptor_entry(
        database_descriptor,
        target_parent_descriptor,
        target_name,
        error_message="published database changed while validating publication",
    )
    _validate_directory_descriptor_path(
        target_parent_descriptor,
        target_dir,
        error_message="database target directory changed at publication boundary",
    )
    _validate_regular_descriptor_path(
        database_descriptor,
        target_dir / target_name,
        error_message="public database target is not the retained regular file",
    )


def _create_target_restore_candidate(
    backup: TargetBackup,
    workspace_descriptor: int,
    known_entries: dict[str, tuple[int, int]],
) -> tuple[int, str]:
    if _sha256_descriptor(backup.descriptor) != backup.sha256:
        raise LoadError("retained previous database snapshot changed")
    candidate_name = f"restore-target-{secrets.token_hex(16)}.duckdb"
    candidate_descriptor, _candidate_identity, candidate_hash = (
        _create_read_only_workspace_copy(
            backup.descriptor,
            workspace_descriptor,
            candidate_name,
            known_entries,
            expected_hash=backup.sha256,
            error_message="previous database restore candidate could not be created",
        )
    )
    try:
        if (
            candidate_hash != backup.sha256
            or _sha256_descriptor(backup.descriptor) != backup.sha256
        ):
            raise LoadError("retained previous database snapshot changed")
        return candidate_descriptor, candidate_name
    except Exception:
        _close_best_effort(candidate_descriptor)
        raise


def _quarantine_unknown_target(
    target_parent_descriptor: int,
    target_name: str,
) -> None:
    try:
        source_stat = os.stat(
            target_name,
            dir_fd=target_parent_descriptor,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        return
    except OSError:
        raise LoadError("unknown database target could not be inspected") from None

    quarantine_name: str | None = None
    quarantine_descriptor: int | None = None
    quarantine_identity: tuple[int, int] | None = None
    moved = False
    for _ in range(128):
        candidate = f".{target_name}.preserved-{secrets.token_hex(16)}"
        try:
            os.mkdir(candidate, 0o700, dir_fd=target_parent_descriptor)
        except FileExistsError:
            continue
        except OSError:
            raise LoadError("unknown database target could not be preserved") from None
        quarantine_name = candidate
        break
    if quarantine_name is None:
        raise LoadError("unknown database target could not be preserved")

    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        quarantine_descriptor = os.open(
            quarantine_name,
            flags,
            dir_fd=target_parent_descriptor,
        )
        quarantine_stat = _validate_directory_descriptor_entry(
            quarantine_descriptor,
            target_parent_descriptor,
            quarantine_name,
            required_mode=0o700,
            error_message="unknown database quarantine is not retained",
        )
        quarantine_identity = _identity(quarantine_stat)
        source_stat = os.stat(
            target_name,
            dir_fd=target_parent_descriptor,
            follow_symlinks=False,
        )
        os.rename(
            target_name,
            target_name,
            src_dir_fd=target_parent_descriptor,
            dst_dir_fd=quarantine_descriptor,
        )
        moved = True
        preserved_stat = os.stat(
            target_name,
            dir_fd=quarantine_descriptor,
            follow_symlinks=False,
        )
        if _identity(preserved_stat) != _identity(source_stat) or stat.S_IFMT(
            preserved_stat.st_mode
        ) != stat.S_IFMT(source_stat.st_mode):
            raise LoadError("unknown database target quarantine changed identity")
        _validate_directory_descriptor_entry(
            quarantine_descriptor,
            target_parent_descriptor,
            quarantine_name,
            required_mode=0o700,
            error_message="unknown database quarantine changed after preservation",
        )
        try:
            os.stat(
                target_name,
                dir_fd=target_parent_descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            pass
        else:
            raise LoadError("database target changed during quarantine")
    except LoadError:
        raise
    except OSError:
        raise LoadError("unknown database target could not be preserved") from None
    finally:
        _close_best_effort(quarantine_descriptor)
        if not moved and quarantine_identity is not None:
            try:
                quarantine_stat = os.stat(
                    quarantine_name,
                    dir_fd=target_parent_descriptor,
                    follow_symlinks=False,
                )
                if (
                    stat.S_ISDIR(quarantine_stat.st_mode)
                    and _identity(quarantine_stat) == quarantine_identity
                ):
                    os.rmdir(quarantine_name, dir_fd=target_parent_descriptor)
            except OSError:
                pass


def _validate_target_restore_candidate(
    *,
    restore_descriptor: int,
    restore_name: str,
    workspace_descriptor: int,
    target_backup: TargetBackup,
) -> None:
    candidate_stat = _validate_regular_descriptor_entry(
        restore_descriptor,
        workspace_descriptor,
        restore_name,
        error_message="previous database restore candidate changed",
    )
    if stat.S_IMODE(candidate_stat.st_mode) != 0o400:
        raise LoadError("previous database restore candidate is not mode 0400")
    if (
        _sha256_descriptor(restore_descriptor) != target_backup.sha256
        or _sha256_descriptor(target_backup.descriptor) != target_backup.sha256
    ):
        raise LoadError("retained previous database snapshot changed")
    candidate_stat = _validate_regular_descriptor_entry(
        restore_descriptor,
        workspace_descriptor,
        restore_name,
        error_message="previous database restore candidate changed",
    )
    if stat.S_IMODE(candidate_stat.st_mode) != 0o400:
        raise LoadError("previous database restore candidate is not mode 0400")


def _target_matches_restore_candidate(
    *,
    restore_descriptor: int,
    target_parent_descriptor: int,
    target_name: str,
    expected_hash: str,
) -> bool:
    try:
        target_stat = _validate_regular_descriptor_entry(
            restore_descriptor,
            target_parent_descriptor,
            target_name,
            error_message="restored database target changed",
        )
        if (
            stat.S_IMODE(target_stat.st_mode) != 0o400
            or _sha256_descriptor(restore_descriptor) != expected_hash
        ):
            return False
        _validate_regular_descriptor_entry(
            restore_descriptor,
            target_parent_descriptor,
            target_name,
            error_message="restored database target changed",
        )
        return True
    except (LoadError, OSError):
        return False


def _validate_restored_target(
    *,
    restore_descriptor: int,
    target_parent_descriptor: int,
    target_name: str,
    target_backup: TargetBackup,
) -> None:
    restored_stat = _validate_regular_descriptor_entry(
        restore_descriptor,
        target_parent_descriptor,
        target_name,
        error_message="restored database is not the retained restore candidate",
    )
    if stat.S_IMODE(restored_stat.st_mode) != 0o400:
        raise LoadError("restored database target is not mode 0400")
    if (
        _sha256_descriptor(restore_descriptor) != target_backup.sha256
        or _sha256_descriptor(target_backup.descriptor) != target_backup.sha256
    ):
        raise LoadError("restored database hash does not match the previous target")
    _validate_regular_descriptor_entry(
        restore_descriptor,
        target_parent_descriptor,
        target_name,
        error_message="restored database identity changed after restoration",
    )


def _preserve_target_restore_candidate(
    *,
    target_parent_descriptor: int,
    target_name: str,
    workspace_descriptor: int,
    restore_descriptor: int,
    restore_name: str,
    target_backup: TargetBackup,
) -> None:
    _validate_target_restore_candidate(
        restore_descriptor=restore_descriptor,
        restore_name=restore_name,
        workspace_descriptor=workspace_descriptor,
        target_backup=target_backup,
    )
    if _HARD_LINK not in getattr(os, "supports_follow_symlinks", ()):
        raise LoadError("secure database recovery preservation is not supported")

    preservation_name: str | None = None
    preservation_descriptor: int | None = None
    preservation_identity: tuple[int, int] | None = None
    artifact_linked = False
    for _ in range(128):
        candidate = f".{target_name}.recovery-{secrets.token_hex(16)}"
        try:
            os.mkdir(candidate, 0o700, dir_fd=target_parent_descriptor)
        except FileExistsError:
            continue
        except OSError:
            raise LoadError(
                "previous database recovery artifact could not be preserved"
            ) from None
        preservation_name = candidate
        break
    if preservation_name is None:
        raise LoadError("previous database recovery artifact could not be preserved")

    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        preservation_descriptor = os.open(
            preservation_name,
            flags,
            dir_fd=target_parent_descriptor,
        )
        preservation_stat = _validate_directory_descriptor_entry(
            preservation_descriptor,
            target_parent_descriptor,
            preservation_name,
            required_mode=0o700,
            error_message="database recovery preservation directory is not retained",
        )
        preservation_identity = _identity(preservation_stat)
        artifact_name = "previous-database.duckdb"
        try:
            _HARD_LINK(
                restore_name,
                artifact_name,
                src_dir_fd=workspace_descriptor,
                dst_dir_fd=preservation_descriptor,
                follow_symlinks=False,
            )
        except FileExistsError:
            raise LoadError(
                "previous database recovery artifact could not be preserved"
            ) from None
        except OSError:
            raise LoadError(
                "previous database recovery artifact could not be preserved"
            ) from None
        artifact_linked = True
        artifact_stat = _validate_regular_descriptor_entry(
            restore_descriptor,
            preservation_descriptor,
            artifact_name,
            error_message="previous database recovery artifact is not retained",
        )
        if stat.S_IMODE(artifact_stat.st_mode) != 0o400:
            raise LoadError("previous database recovery artifact is not mode 0400")
        if (
            _sha256_descriptor(restore_descriptor) != target_backup.sha256
            or _sha256_descriptor(target_backup.descriptor) != target_backup.sha256
        ):
            raise LoadError("previous database recovery artifact hash changed")
        artifact_stat = _validate_regular_descriptor_entry(
            restore_descriptor,
            preservation_descriptor,
            artifact_name,
            error_message="previous database recovery artifact identity changed",
        )
        if stat.S_IMODE(artifact_stat.st_mode) != 0o400:
            raise LoadError("previous database recovery artifact is not mode 0400")
        _validate_directory_descriptor_entry(
            preservation_descriptor,
            target_parent_descriptor,
            preservation_name,
            required_mode=0o700,
            error_message="database recovery preservation directory changed",
        )
    finally:
        _close_best_effort(preservation_descriptor)
        if not artifact_linked and preservation_identity is not None:
            try:
                preservation_stat = os.stat(
                    preservation_name,
                    dir_fd=target_parent_descriptor,
                    follow_symlinks=False,
                )
                if (
                    stat.S_ISDIR(preservation_stat.st_mode)
                    and _identity(preservation_stat) == preservation_identity
                ):
                    os.rmdir(preservation_name, dir_fd=target_parent_descriptor)
            except OSError:
                pass


def _restore_target_after_publication_failure(
    *,
    target_parent_descriptor: int,
    target_name: str,
    workspace_descriptor: int,
    database_descriptor: int,
    target_backup: TargetBackup | None,
    known_entries: dict[str, tuple[int, int]],
) -> None:
    if target_backup is None:
        if not _entry_matches_retained_file(
            database_descriptor,
            target_parent_descriptor,
            target_name,
        ):
            return
        _quarantine_unknown_target(
            target_parent_descriptor,
            target_name,
        )
        return

    _revoke_target_backup_cleanup_authority(
        target_backup,
        workspace_descriptor,
        known_entries,
    )
    if _target_matches_original_backup(
        target_parent_descriptor,
        target_name,
        target_backup,
    ):
        _authorize_target_backup_cleanup(
            target_backup,
            workspace_descriptor,
            known_entries,
        )
        return
    if not _entry_matches_retained_file(
        database_descriptor,
        target_parent_descriptor,
        target_name,
    ):
        _quarantine_unknown_target(
            target_parent_descriptor,
            target_name,
        )
    retry_limit = _TARGET_RESTORE_RETRY_LIMIT
    if type(retry_limit) is not int or retry_limit < 1:
        raise LoadError("invalid database target restore retry limit")

    restore_descriptor: int | None = None
    preservation_verified = False
    try:
        restore_descriptor, restore_name = _create_target_restore_candidate(
            target_backup,
            workspace_descriptor,
            known_entries,
        )
        for attempt in range(retry_limit):
            _validate_target_restore_candidate(
                restore_descriptor=restore_descriptor,
                restore_name=restore_name,
                workspace_descriptor=workspace_descriptor,
                target_backup=target_backup,
            )
            if _target_matches_original_backup(
                target_parent_descriptor,
                target_name,
                target_backup,
            ):
                _authorize_target_backup_cleanup(
                    target_backup,
                    workspace_descriptor,
                    known_entries,
                )
                return
            if _target_matches_restore_candidate(
                restore_descriptor=restore_descriptor,
                target_parent_descriptor=target_parent_descriptor,
                target_name=target_name,
                expected_hash=target_backup.sha256,
            ):
                _authorize_target_backup_cleanup(
                    target_backup,
                    workspace_descriptor,
                    known_entries,
                )
                return

            _quarantine_unknown_target(
                target_parent_descriptor,
                target_name,
            )
            try:
                if _HARD_LINK not in getattr(os, "supports_follow_symlinks", ()):
                    raise LoadError(
                        "secure database target restoration is not supported"
                    )
                _HARD_LINK(
                    restore_name,
                    target_name,
                    src_dir_fd=workspace_descriptor,
                    dst_dir_fd=target_parent_descriptor,
                    follow_symlinks=False,
                )
            except FileExistsError:
                if _target_matches_original_backup(
                    target_parent_descriptor,
                    target_name,
                    target_backup,
                ):
                    _authorize_target_backup_cleanup(
                        target_backup,
                        workspace_descriptor,
                        known_entries,
                    )
                    return
                if _target_matches_restore_candidate(
                    restore_descriptor=restore_descriptor,
                    target_parent_descriptor=target_parent_descriptor,
                    target_name=target_name,
                    expected_hash=target_backup.sha256,
                ):
                    _authorize_target_backup_cleanup(
                        target_backup,
                        workspace_descriptor,
                        known_entries,
                    )
                    return
                if attempt + 1 == retry_limit:
                    raise LoadError(
                        "database target restoration retry limit was exhausted"
                    )
                _quarantine_unknown_target(
                    target_parent_descriptor,
                    target_name,
                )
                continue
            except OSError:
                raise LoadError("failed to restore previous database target") from None

            _validate_restored_target(
                restore_descriptor=restore_descriptor,
                target_parent_descriptor=target_parent_descriptor,
                target_name=target_name,
                target_backup=target_backup,
            )
            _authorize_target_backup_cleanup(
                target_backup,
                workspace_descriptor,
                known_entries,
            )
            return
        raise LoadError("database target restoration retry limit was exhausted")
    except Exception:
        if restore_descriptor is not None and not preservation_verified:
            _preserve_target_restore_candidate(
                target_parent_descriptor=target_parent_descriptor,
                target_name=target_name,
                workspace_descriptor=workspace_descriptor,
                restore_descriptor=restore_descriptor,
                restore_name=restore_name,
                target_backup=target_backup,
            )
            preservation_verified = True
        if preservation_verified:
            _authorize_target_backup_cleanup(
                target_backup,
                workspace_descriptor,
                known_entries,
            )
        raise
    finally:
        _close_best_effort(restore_descriptor)


def _discard_target_backup_best_effort(
    target_backup: TargetBackup | None,
    workspace_descriptor: int,
) -> None:
    if target_backup is None:
        return
    try:
        _validate_named_target_backup(target_backup, workspace_descriptor)
    except (LoadError, OSError):
        # Keep the named copy under its creation-provenance cleanup authority.
        # Final success cleanup removes it; rollback revokes that authority first.
        pass


def _load_csvs_impl(
    csv_dir: Path | str,
    db_path: Path | str,
    bundle: SemanticBundle,
    manifest: SourceManifest,
    receipt_dir: Path | str | None = None,
) -> tuple[MaterializationReceipt, Path]:
    """Build a verified DuckDB off to the side and atomically publish it."""
    source_root = Path(csv_dir)
    inventory = preflight_csvs(source_root, bundle, manifest)
    expected_csv_names = {table.source_file.name for table in inventory.tables}
    _validate_csv_file_set(
        source_root,
        expected_csv_names,
        len(inventory.tables),
    )
    target = Path(db_path)
    target_dir = target.parent
    if not target_dir.is_dir():
        raise LoadError("database target directory does not exist")
    receipts = Path(receipt_dir) if receipt_dir is not None else target_dir

    target_parent_descriptor: int | None = None
    receipt_parent_descriptor: int | None = None
    target_backup: TargetBackup | None = None
    workspace: Path | None = None
    workspace_descriptor: int | None = None
    workspace_identity: tuple[int, int] | None = None
    workspace_entries: dict[str, tuple[int, int]] = {}
    source_snapshot_descriptors: tuple[int, ...] = ()
    database_descriptor: int | None = None
    receipt_workspace: Path | None = None
    receipt_workspace_descriptor: int | None = None
    receipt_workspace_identity: tuple[int, int] | None = None
    receipt_workspace_entries: dict[str, tuple[int, int]] = {}
    receipt_descriptor: int | None = None
    receipt_uses_retained_inode = False
    try:
        target_parent_descriptor, _ = _open_directory_descriptor(
            target_dir,
            error_message="database target directory could not be securely opened",
        )
        workspace, workspace_descriptor, workspace_identity = _create_private_workspace(
            target,
            parent_descriptor=target_parent_descriptor,
        )
        _validate_directory_descriptor_entry(
            workspace_descriptor,
            target_parent_descriptor,
            workspace.name,
            required_mode=0o700,
            error_message="private workspace is not in the pinned target directory",
        )
        snapshot_inventory, source_snapshot_descriptors = _snapshot_inventory(
            inventory,
            workspace,
            workspace_descriptor,
            workspace_entries,
        )
        database_temp = workspace / "database.duckdb"
        database_descriptor = _build_database(
            database_temp,
            snapshot_inventory,
            workspace_descriptor,
            workspace_entries,
            source_snapshot_descriptors,
        )
        for descriptor in source_snapshot_descriptors:
            _close_best_effort(descriptor)
        source_snapshot_descriptors = ()
        database_hash = _sha256_descriptor(database_descriptor)

        receipt = MaterializationReceipt(
            source_manifest_sha256=inventory.source_manifest_sha256,
            bundle_sha256=inventory.bundle_sha256,
            tables=tuple(
                MaterializedTableReceipt(
                    table_id=table.table_id,
                    source_file_sha256=table.source_file_sha256,
                    row_count=table.row_count,
                )
                for table in inventory.tables
            ),
            database_sha256=database_hash,
            engine="duckdb",
            engine_version=metadata.version("duckdb"),
        )
        receipt_bytes = canonical_json_bytes(receipt)
        receipt_hash = sha256(receipt_bytes).hexdigest()
        receipt_name = f"{receipt_hash}.json"
        receipts.mkdir(parents=True, exist_ok=True)
        receipt_path = receipts / receipt_name
        if receipts == target_dir:
            try:
                receipt_parent_descriptor = os.dup(target_parent_descriptor)
            except OSError:
                raise LoadError("receipt directory could not be retained") from None
            _validate_directory_descriptor_path(
                receipt_parent_descriptor,
                receipts,
                error_message="receipt directory changed while opening",
            )
        else:
            receipt_parent_descriptor, _ = _open_directory_descriptor(
                receipts,
                error_message="receipt directory could not be securely opened",
            )
        (
            receipt_workspace,
            receipt_workspace_descriptor,
            receipt_workspace_identity,
        ) = _create_private_workspace(
            receipts / "materialization-receipt",
            parent_descriptor=receipt_parent_descriptor,
        )
        receipt_descriptor, receipt_temp_name = _write_receipt_temp(
            receipt_workspace,
            receipt_bytes,
            receipt_workspace_entries,
            workspace_descriptor=receipt_workspace_descriptor,
        )

        _validate_regular_descriptor_entry(
            receipt_descriptor,
            receipt_workspace_descriptor,
            receipt_temp_name,
            error_message="temporary receipt entry changed before publication",
        )
        _validate_directory_descriptor_path(
            receipt_parent_descriptor,
            receipts,
            error_message="receipt directory changed before publication",
        )
        _validate_csv_file_set(
            source_root,
            expected_csv_names,
            len(inventory.tables),
        )
        try:
            _link_no_follow(
                receipt_temp_name,
                receipt_name,
                src_dir_fd=receipt_workspace_descriptor,
                dst_dir_fd=receipt_parent_descriptor,
            )
        except FileExistsError:
            _validate_existing_receipt(
                receipt_parent_descriptor,
                receipt_name,
                receipt_bytes,
            )
        except OSError:
            raise LoadError("receipt publication failure") from None
        else:
            receipt_uses_retained_inode = True
            _validate_retained_receipt(
                receipt_descriptor,
                receipt_parent_descriptor,
                receipt_name,
                receipt_bytes,
                error_message="published receipt content changed",
            )

        # Publication makes this immutable path shared evidence. If database
        # replacement fails, leave the harmless orphan in place: its database
        # hash cannot validate against a mismatched target, and another
        # invocation may already have adopted it.
        _validate_directory_descriptor_path(
            receipt_parent_descriptor,
            receipts,
            error_message="receipt directory changed after publication",
        )
        _validate_published_receipt(
            uses_retained_inode=receipt_uses_retained_inode,
            retained_descriptor=receipt_descriptor,
            directory_descriptor=receipt_parent_descriptor,
            name=receipt_name,
            receipt_bytes=receipt_bytes,
            error_message="published receipt content changed",
        )
        target_backup = _prepare_existing_target_backup(
            target_parent_descriptor,
            target.name,
            workspace_descriptor,
            workspace_entries,
        )

        try:
            _validate_csv_file_set(
                source_root,
                expected_csv_names,
                len(inventory.tables),
            )
            _validate_directory_descriptor_path(
                receipt_parent_descriptor,
                receipts,
                error_message="receipt directory changed before database publication",
            )
            _validate_regular_descriptor_entry(
                database_descriptor,
                workspace_descriptor,
                database_temp.name,
                error_message="database entry changed before publication",
            )
            _validate_published_receipt(
                uses_retained_inode=receipt_uses_retained_inode,
                retained_descriptor=receipt_descriptor,
                directory_descriptor=receipt_parent_descriptor,
                name=receipt_name,
                receipt_bytes=receipt_bytes,
                error_message="published receipt content changed before database publication",
            )
            _validate_directory_descriptor_path(
                target_parent_descriptor,
                target_dir,
                error_message="database target directory changed before publication",
            )
            _validate_target_before_publication(
                target_parent_descriptor,
                target.name,
                target_backup,
            )
            try:
                os.replace(
                    database_temp.name,
                    target.name,
                    src_dir_fd=workspace_descriptor,
                    dst_dir_fd=target_parent_descriptor,
                )
            except OSError:
                raise LoadError("database replace failure after adoption") from None
            _validate_published_database(
                target_parent_descriptor=target_parent_descriptor,
                target_dir=target_dir,
                target_name=target.name,
                database_descriptor=database_descriptor,
                expected_hash=database_hash,
            )
            _validate_csv_file_set(
                source_root,
                expected_csv_names,
                len(inventory.tables),
            )
            _validate_published_receipt(
                uses_retained_inode=receipt_uses_retained_inode,
                retained_descriptor=receipt_descriptor,
                directory_descriptor=receipt_parent_descriptor,
                name=receipt_name,
                receipt_bytes=receipt_bytes,
                error_message="published receipt content changed during publication",
            )
            _validate_directory_descriptor_path(
                receipt_parent_descriptor,
                receipts,
                error_message="receipt directory changed during database publication",
            )
            _discard_target_backup_best_effort(
                target_backup,
                workspace_descriptor,
            )
            _validate_published_database(
                target_parent_descriptor=target_parent_descriptor,
                target_dir=target_dir,
                target_name=target.name,
                database_descriptor=database_descriptor,
                expected_hash=database_hash,
            )
            _validate_directory_descriptor_path(
                receipt_parent_descriptor,
                receipts,
                error_message="receipt directory changed at success boundary",
            )
            _validate_published_receipt(
                uses_retained_inode=receipt_uses_retained_inode,
                retained_descriptor=receipt_descriptor,
                directory_descriptor=receipt_parent_descriptor,
                name=receipt_name,
                receipt_bytes=receipt_bytes,
                error_message="published receipt content changed at success boundary",
            )
            _validate_published_database(
                target_parent_descriptor=target_parent_descriptor,
                target_dir=target_dir,
                target_name=target.name,
                database_descriptor=database_descriptor,
                expected_hash=database_hash,
            )
        except Exception:
            _restore_target_after_publication_failure(
                target_parent_descriptor=target_parent_descriptor,
                target_name=target.name,
                workspace_descriptor=workspace_descriptor,
                database_descriptor=database_descriptor,
                target_backup=target_backup,
                known_entries=workspace_entries,
            )
            raise

        return receipt, receipt_path
    except Exception as exc:
        if isinstance(exc, LoadError):
            raise
        raise LoadError("DuckDB materialization failed") from None
    finally:
        for descriptor in source_snapshot_descriptors:
            _close_best_effort(descriptor)
        _close_best_effort(receipt_descriptor)
        _remove_private_workspace(
            receipt_workspace,
            descriptor=receipt_workspace_descriptor,
            expected_identity=receipt_workspace_identity,
            known_entries=receipt_workspace_entries,
            parent_descriptor=receipt_parent_descriptor,
            remove_mismatched_symlinks=True,
        )
        _close_best_effort(receipt_workspace_descriptor)
        _close_best_effort(receipt_parent_descriptor)
        _remove_private_workspace(
            workspace,
            descriptor=workspace_descriptor,
            expected_identity=workspace_identity,
            known_entries=workspace_entries,
            parent_descriptor=target_parent_descriptor,
        )
        _close_best_effort(
            target_backup.descriptor if target_backup is not None else None
        )
        _close_best_effort(database_descriptor)
        _close_best_effort(workspace_descriptor)
        _close_best_effort(target_parent_descriptor)


def load_csvs(
    csv_dir: Path | str,
    db_path: Path | str,
    bundle: SemanticBundle,
    manifest: SourceManifest,
    receipt_dir: Path | str | None = None,
) -> tuple[MaterializationReceipt, Path]:
    """Build a verified DuckDB off to the side and atomically publish it."""
    failure: Exception | None = None
    try:
        return _load_csvs_impl(
            csv_dir,
            db_path,
            bundle,
            manifest,
            receipt_dir=receipt_dir,
        )
    except Exception as error:  # noqa: BLE001 - public exception boundary
        failure = error
    if failure is None:  # pragma: no cover - the try either returns or captures
        raise LoadError("DuckDB materialization failed")
    raise _public_load_error(failure, fallback="DuckDB materialization failed")
