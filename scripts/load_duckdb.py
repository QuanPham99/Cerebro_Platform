from __future__ import annotations

import csv
import os
import re
import secrets
import stat
import tempfile
from dataclasses import dataclass
from hashlib import sha256
from importlib import metadata
from pathlib import Path
from typing import Any

import duckdb

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

_IDENTIFIER = re.compile(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*")
_DATA_TYPE = re.compile(
    r"[A-Z][A-Z0-9_]*(?: [A-Z][A-Z0-9_]*)*"
    r"(?:\([0-9]+(?:,[0-9]+)?\))?(?:\[\])*"
)
_HARD_LINK = os.link


class LoadError(RuntimeError):
    """The source set could not be safely materialized."""


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


def _quote_identifier(identifier: str) -> str:
    # Callers pass only names accepted by _IDENTIFIER.
    return f'"{identifier}"'


def _validated_data_type(value: Any, *, table_name: str, column_name: str) -> str:
    if not isinstance(value, str) or _DATA_TYPE.fullmatch(value) is None:
        raise LoadError(f"invalid data type for {table_name}.{column_name}: {value!r}")
    try:
        canonical = str(duckdb.sqltype(value))
    except (TypeError, ValueError, duckdb.Error) as exc:
        raise LoadError(
            f"invalid data type for {table_name}.{column_name}: {value!r}"
        ) from exc
    if canonical != value:
        raise LoadError(
            f"data type for {table_name}.{column_name} must be canonical: "
            f"{value!r} != {canonical!r}"
        )
    return value


def _active_bundle_tables(
    bundle: SemanticBundle,
) -> dict[str, tuple[SourceColumn, ...]]:
    if not isinstance(bundle, SemanticBundle):
        raise LoadError("bundle must be a SemanticBundle")

    tables: dict[str, tuple[SourceColumn, ...]] = {}
    for table in bundle.objects:
        if table.type != "table" or table.status != "active":
            continue
        prefix, separator, raw_name = table.id.partition(".")
        if (
            prefix != "table"
            or separator != "."
            or _IDENTIFIER.fullmatch(raw_name) is None
            or manifest_table_id(raw_name) != table.id
        ):
            raise LoadError(f"invalid active table ID: {table.id!r}")
        if raw_name in tables:
            raise LoadError(f"duplicate active table metadata: {raw_name}")

        raw_columns = table.cerebro.get("columns")
        if not isinstance(raw_columns, list) or not raw_columns:
            raise LoadError(f"active table {raw_name} has no column metadata")
        columns: list[SourceColumn] = []
        names: set[str] = set()
        for index, raw_column in enumerate(raw_columns):
            if not isinstance(raw_column, dict):
                raise LoadError(f"invalid column metadata at {raw_name}[{index}]")
            column_name = raw_column.get("name")
            if (
                not isinstance(column_name, str)
                or _IDENTIFIER.fullmatch(column_name) is None
            ):
                raise LoadError(
                    f"invalid column name for {raw_name}[{index}]: {column_name!r}"
                )
            if column_name in names:
                raise LoadError(f"duplicate column metadata: {raw_name}.{column_name}")
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
                    raise LoadError(f"blank CSV row in {source_file.name}")
                if len(row) != len(header):
                    raise LoadError(
                        f"CSV row width mismatch in {source_file.name}: "
                        f"expected {len(header)}, got {len(row)}"
                    )
                row_count += 1
            return header, row_count
    except UnicodeDecodeError as exc:
        raise LoadError(f"CSV is not valid UTF-8: {source_file.name}") from exc
    except csv.Error as exc:
        raise LoadError(f"invalid CSV syntax in {source_file.name}: {exc}") from exc
    except OSError as exc:
        raise LoadError(f"could not read CSV {source_file.name}: {exc}") from exc


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
    except duckdb.Error as exc:
        raise LoadError(
            f"CSV values for {table.name} cannot cast to bundle-declared types: {exc}"
        ) from exc
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
                    raise LoadError(f"blank CSV row in {display_name}")
                if len(row) != len(header):
                    raise LoadError(
                        f"CSV row width mismatch in {display_name}: "
                        f"expected {len(header)}, got {len(row)}"
                    )
                row_count += 1
            return header, row_count
    except UnicodeDecodeError as exc:
        raise LoadError(f"CSV is not valid UTF-8: {display_name}") from exc
    except csv.Error as exc:
        raise LoadError(f"invalid CSV syntax in {display_name}: {exc}") from exc
    except OSError as exc:
        raise LoadError(f"could not read CSV {display_name}: {exc}") from exc
    finally:
        if duplicate >= 0:
            os.close(duplicate)


def _retained_descriptor_path(descriptor: int) -> Path:
    try:
        descriptor_stat = os.fstat(descriptor)
    except OSError as exc:
        raise LoadError("private preflight snapshot descriptor is unavailable") from exc
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
    except OSError as exc:
        raise LoadError(
            f"could not create private preflight snapshot for {source_file.name}"
        ) from exc
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
    except OSError as exc:
        raise LoadError("CSV file set could not be enumerated") from exc

    if actual_names != expected_names:
        missing = sorted(expected_names - actual_names)
        extra = sorted(actual_names - expected_names)
        raise LoadError(f"CSV file set mismatch (missing={missing}, extra={extra})")
    if len(actual_names) != expected_count:
        raise LoadError("CSV file count does not match the expected active table count")


def preflight_csvs(
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
        raise LoadError(f"CSV directory does not exist: {root}")

    bundle_tables = _active_bundle_tables(bundle_snapshot)
    manifest_tables = {table.name: table for table in manifest.tables}
    bundle_names = set(bundle_tables)
    manifest_names = set(manifest_tables)
    if bundle_names != manifest_names or len(bundle_tables) != len(manifest.tables):
        missing = sorted(bundle_names - manifest_names)
        extra = sorted(manifest_names - bundle_names)
        raise LoadError(
            "active bundle table set does not match source manifest "
            f"(missing={missing}, extra={extra})"
        )

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
                    errors.append(
                        f"SHA-256 mismatch for {manifest_table.file_name}: "
                        f"expected {manifest_table.sha256}, got {actual_hash}"
                    )

                try:
                    actual_header, actual_count = _read_csv_shape_descriptor(
                        snapshot_descriptor,
                        manifest_table.file_name,
                    )
                    if actual_header != expected_header:
                        errors.append(
                            f"header mismatch for {manifest_table.file_name}: "
                            f"expected {expected_header}, got {actual_header}"
                        )
                    if actual_count != manifest_table.row_count:
                        errors.append(
                            f"row count mismatch for {manifest_table.file_name}: "
                            f"expected {manifest_table.row_count}, got {actual_count}"
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
                            f"cast row count mismatch for {manifest_table.file_name}: "
                            f"expected {manifest_table.row_count}, got {cast_count}"
                        )
                except LoadError as exc:
                    errors.append(str(exc))
                except OSError as exc:
                    errors.append(
                        f"could not rewind retained snapshot for "
                        f"{manifest_table.file_name}: {exc}"
                    )

                if _sha256_descriptor(snapshot_descriptor) != actual_hash:
                    errors.append(
                        f"private preflight snapshot changed while validating "
                        f"{manifest_table.file_name}"
                    )
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
    except duckdb.Error as exc:
        raise LoadError(f"failed loading {source_file.name}: {exc}") from exc


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
        raise LoadError(
            "materialized table set mismatch: "
            f"expected {sorted(expected_tables)}, got {sorted(actual_tables)}"
        )

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
            raise LoadError(
                f"materialized schema mismatch for {table.name}: "
                f"expected {expected_columns}, got {actual_columns}"
            )
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
    except OSError as exc:
        raise LoadError(error_message) from exc
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
        retained_stat = os.fstat(descriptor)
    except OSError:
        retained_stat = None

    try:
        os.close(descriptor)
        return
    except OSError:
        pass

    # A test seam or an interrupted close can fail before releasing the FD.
    # Retry only while fstat proves that the numeric FD still identifies the
    # same object; never risk closing a descriptor that has been reused.
    if retained_stat is None:
        return
    try:
        current_stat = os.fstat(descriptor)
    except OSError:
        return
    if _identity(current_stat) != _identity(retained_stat) or stat.S_IFMT(
        current_stat.st_mode
    ) != stat.S_IFMT(retained_stat.st_mode):
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
    except OSError as exc:
        raise LoadError(error_message) from exc
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
    except OSError as exc:
        raise LoadError(error_message) from exc
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
    except OSError as exc:
        raise LoadError(error_message) from exc
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
    except OSError as exc:
        raise LoadError(error_message) from exc
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
    except OSError as exc:
        raise LoadError(error_message) from exc
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


def _link_no_follow(
    source_name: str,
    destination_name: str,
    *,
    src_dir_fd: int,
    dst_dir_fd: int,
) -> None:
    if _HARD_LINK not in getattr(os, "supports_follow_symlinks", ()):
        raise LoadError("secure receipt publication is not supported")
    os.link(
        source_name,
        destination_name,
        src_dir_fd=src_dir_fd,
        dst_dir_fd=dst_dir_fd,
        follow_symlinks=False,
    )


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
        except OSError as exc:
            raise LoadError("retained receipt content could not be read") from exc
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
    except Exception as exc:
        raise LoadError(error_message) from exc
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
    except OSError as exc:
        raise LoadError("pre-existing receipt could not be securely opened") from exc

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
        except OSError as exc:
            raise LoadError("pre-existing receipt could not be read") from exc
        if existing_bytes != receipt_bytes:
            raise LoadError("content-addressed receipt collision")
        try:
            existing = MaterializationReceipt.model_validate_json(existing_bytes)
        except Exception as exc:
            raise LoadError("pre-existing receipt is invalid") from exc
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
        except OSError as exc:
            raise LoadError(
                "could not create private materialization workspace"
            ) from exc
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
    except OSError as exc:
        raise LoadError(f"could not securely open source {source_file.name}") from exc

    try:
        descriptor_stat = os.fstat(descriptor)
        if not stat.S_ISREG(descriptor_stat.st_mode):
            raise LoadError(f"source is not a regular file: {source_file.name}")
        if no_follow == 0:
            path_stat = os.stat(source_file, follow_symlinks=False)
            if (
                not stat.S_ISREG(path_stat.st_mode)
                or path_stat.st_dev != descriptor_stat.st_dev
                or path_stat.st_ino != descriptor_stat.st_ino
            ):
                raise LoadError(
                    f"source path is not the opened regular file: {source_file.name}"
                )
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
        raise LoadError(
            f"could not create verified snapshot for {table.source_file.name}"
        ) from exc
    if snapshot_hash != table.source_file_sha256:
        os.close(snapshot_descriptor)
        raise LoadError(f"source changed after preflight: {table.source_file.name}")
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
    except OSError as exc:
        raise LoadError("private database path could not be inspected") from exc
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

    connection: duckdb.DuckDBPyConnection | None = None
    database_descriptor: int | None = None
    transaction_open = False
    try:
        try:
            connection = duckdb.connect(str(path))
            database_descriptor, _ = _open_regular_directory_entry(
                workspace_descriptor,
                path.name,
                error_message="database entry is not the opened regular file",
                known_entries=known_entries,
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
                except OSError as exc:
                    raise LoadError(
                        f"could not rewind verified source snapshot: {table.name}"
                    ) from exc
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

        if database_descriptor is None:
            raise LoadError("database descriptor was not retained")
        _validate_regular_descriptor_entry(
            database_descriptor,
            workspace_descriptor,
            path.name,
            error_message="database entry changed when DuckDB closed",
        )
        return database_descriptor
    except Exception:
        if database_descriptor is not None:
            os.close(database_descriptor)
        raise


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
    except OSError as exc:
        raise LoadError("receipt workspace descriptor is unavailable") from exc
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
            except OSError as exc:
                raise LoadError("could not create temporary receipt") from exc
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
) -> tuple[int, str, str] | None:
    try:
        target_stat = os.stat(
            target_name,
            dir_fd=target_parent_descriptor,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise LoadError("existing database target could not be inspected") from exc
    if not stat.S_ISREG(target_stat.st_mode):
        raise LoadError("existing database target is not a regular file")

    target_descriptor: int | None = None
    try:
        target_descriptor, target_stat = _open_regular_directory_entry(
            target_parent_descriptor,
            target_name,
            error_message="existing database target changed while opening",
        )
        target_hash = _sha256_descriptor(target_descriptor)
        backup_name = "previous-target.duckdb"
        # Register the expected identity before linking. A failed or hostile
        # link cannot make cleanup remove an entry with any other identity.
        known_entries[backup_name] = _identity(target_stat)
        link_arguments: dict[str, Any] = {
            "src_dir_fd": target_parent_descriptor,
            "dst_dir_fd": workspace_descriptor,
        }
        if _HARD_LINK in getattr(os, "supports_follow_symlinks", ()):
            link_arguments["follow_symlinks"] = False
        try:
            _HARD_LINK(target_name, backup_name, **link_arguments)
        except OSError as exc:
            raise LoadError("existing database target could not be backed up") from exc

        _validate_regular_descriptor_entry(
            target_descriptor,
            workspace_descriptor,
            backup_name,
            error_message="database backup is not the retained target inode",
        )
        _validate_regular_descriptor_entry(
            target_descriptor,
            target_parent_descriptor,
            target_name,
            error_message="existing database target changed during backup",
        )
        if _sha256_descriptor(target_descriptor) != target_hash:
            raise LoadError("existing database target changed during backup")
        return target_descriptor, target_hash, backup_name
    except Exception:
        if target_descriptor is not None:
            os.close(target_descriptor)
        raise


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


def _restore_target_after_publication_failure(
    *,
    target_parent_descriptor: int,
    target_name: str,
    workspace_descriptor: int,
    database_descriptor: int,
    previous_target_descriptor: int | None,
    previous_target_hash: str | None,
    backup_name: str | None,
) -> None:
    if previous_target_descriptor is None:
        if not _entry_matches_retained_file(
            database_descriptor,
            target_parent_descriptor,
            target_name,
        ):
            return
        try:
            os.unlink(target_name, dir_fd=target_parent_descriptor)
        except OSError as exc:
            raise LoadError(
                "failed to remove the invocation-created database after publication"
            ) from exc
        return

    if previous_target_hash is None or backup_name is None:
        raise LoadError("previous database recovery state is incomplete")
    if _entry_matches_retained_file(
        previous_target_descriptor,
        target_parent_descriptor,
        target_name,
        previous_target_hash,
    ):
        return

    _validate_regular_descriptor_entry(
        previous_target_descriptor,
        workspace_descriptor,
        backup_name,
        error_message="previous database backup changed before restoration",
    )
    try:
        os.replace(
            backup_name,
            target_name,
            src_dir_fd=workspace_descriptor,
            dst_dir_fd=target_parent_descriptor,
        )
    except OSError as exc:
        raise LoadError("failed to restore previous database target") from exc
    _validate_regular_descriptor_entry(
        previous_target_descriptor,
        target_parent_descriptor,
        target_name,
        error_message="restored database is not the retained previous target",
    )
    if _sha256_descriptor(previous_target_descriptor) != previous_target_hash:
        raise LoadError("restored database hash does not match the previous target")


def _discard_target_backup_best_effort(
    previous_target_descriptor: int | None,
    workspace_descriptor: int,
    backup_name: str | None,
) -> None:
    if previous_target_descriptor is None or backup_name is None:
        return
    try:
        _validate_regular_descriptor_entry(
            previous_target_descriptor,
            workspace_descriptor,
            backup_name,
            error_message="database backup changed before cleanup",
        )
        os.unlink(backup_name, dir_fd=workspace_descriptor)
    except (LoadError, OSError):
        # Publication already succeeded. Never turn a conservative cleanup
        # leak into a reported failure after mutating the caller's target.
        pass


def load_csvs(
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
        raise LoadError(f"database target directory does not exist: {target_dir}")
    receipts = Path(receipt_dir) if receipt_dir is not None else target_dir

    target_parent_descriptor: int | None = None
    receipt_parent_descriptor: int | None = None
    previous_target_descriptor: int | None = None
    previous_target_hash: str | None = None
    backup_name: str | None = None
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
            except OSError as exc:
                raise LoadError("receipt directory could not be retained") from exc
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
        if receipt_uses_retained_inode:
            _validate_retained_receipt(
                receipt_descriptor,
                receipt_parent_descriptor,
                receipt_name,
                receipt_bytes,
                error_message="published receipt content changed",
            )
        else:
            _validate_existing_receipt(
                receipt_parent_descriptor,
                receipt_name,
                receipt_bytes,
            )
        previous_target = _prepare_existing_target_backup(
            target_parent_descriptor,
            target.name,
            workspace_descriptor,
            workspace_entries,
        )
        if previous_target is not None:
            (
                previous_target_descriptor,
                previous_target_hash,
                backup_name,
            ) = previous_target

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
            os.replace(
                database_temp.name,
                target.name,
                src_dir_fd=workspace_descriptor,
                dst_dir_fd=target_parent_descriptor,
            )
            _validate_regular_descriptor_entry(
                database_descriptor,
                target_parent_descriptor,
                target.name,
                error_message="published database is not the retained regular file",
            )
            if _sha256_descriptor(database_descriptor) != database_hash:
                raise LoadError("published database hash changed during publication")
            if receipt_uses_retained_inode:
                _validate_retained_receipt(
                    receipt_descriptor,
                    receipt_parent_descriptor,
                    receipt_name,
                    receipt_bytes,
                    error_message="published receipt content changed during publication",
                )
            else:
                _validate_existing_receipt(
                    receipt_parent_descriptor,
                    receipt_name,
                    receipt_bytes,
                )
            _validate_directory_descriptor_path(
                receipt_parent_descriptor,
                receipts,
                error_message="receipt directory changed during database publication",
            )
        except Exception:
            _restore_target_after_publication_failure(
                target_parent_descriptor=target_parent_descriptor,
                target_name=target.name,
                workspace_descriptor=workspace_descriptor,
                database_descriptor=database_descriptor,
                previous_target_descriptor=previous_target_descriptor,
                previous_target_hash=previous_target_hash,
                backup_name=backup_name,
            )
            raise

        _discard_target_backup_best_effort(
            previous_target_descriptor,
            workspace_descriptor,
            backup_name,
        )
        return receipt, receipt_path
    except Exception as exc:
        if isinstance(exc, LoadError):
            raise
        raise LoadError(f"DuckDB materialization failed: {exc}") from exc
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
        _close_best_effort(previous_target_descriptor)
        _close_best_effort(database_descriptor)
        _close_best_effort(workspace_descriptor)
        _close_best_effort(target_parent_descriptor)
