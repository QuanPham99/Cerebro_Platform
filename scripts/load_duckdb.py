from __future__ import annotations

import csv
import os
import re
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
    sha256_file,
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


def _copy_preflight_snapshot(source_file: Path, snapshot: Path) -> str:
    """Copy and hash one securely opened source into a private snapshot."""
    source_descriptor = _open_regular_source(source_file)
    destination_descriptor: int | None = None
    flags = (
        os.O_RDWR
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    digest = sha256()
    try:
        destination_descriptor = os.open(snapshot, flags, 0o600)
        _validate_regular_descriptor_path(
            destination_descriptor,
            snapshot,
            error_message="private preflight snapshot is not the opened regular file",
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
        descriptor_stat = _validate_regular_descriptor_path(
            destination_descriptor,
            snapshot,
            error_message="private preflight snapshot changed after copying",
        )
        if stat.S_IMODE(descriptor_stat.st_mode) != 0o400:
            raise LoadError("private preflight snapshot is not mode 0400")
        if _sha256_descriptor(destination_descriptor) != digest.hexdigest():
            raise LoadError("private preflight snapshot hash changed after copying")
        return digest.hexdigest()
    except OSError as exc:
        raise LoadError(
            f"could not create private preflight snapshot for {source_file.name}"
        ) from exc
    finally:
        if source_descriptor >= 0:
            os.close(source_descriptor)
        if destination_descriptor is not None:
            os.close(destination_descriptor)


def preflight_csvs(
    csv_dir: Path | str,
    bundle: SemanticBundle,
    manifest: SourceManifest,
) -> SourceInventory:
    """Validate every source and return immutable, name-aligned load metadata."""
    if not isinstance(manifest, SourceManifest):
        raise LoadError("manifest must be a SourceManifest")
    root = Path(csv_dir)
    if not root.is_dir():
        raise LoadError(f"CSV directory does not exist: {root}")

    bundle_tables = _active_bundle_tables(bundle)
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

    actual_csv_names = {
        path.name for path in root.iterdir() if path.is_file() and path.suffix == ".csv"
    }
    expected_csv_names = {table.file_name for table in manifest.tables}
    if actual_csv_names != expected_csv_names:
        missing = sorted(expected_csv_names - actual_csv_names)
        extra = sorted(actual_csv_names - expected_csv_names)
        raise LoadError(f"CSV file set mismatch (missing={missing}, extra={extra})")
    if len(actual_csv_names) != len(bundle_tables):
        raise LoadError("CSV file count does not match the expected active table count")

    errors: list[str] = []
    inventory_tables: list[SourceTableInventory] = []
    try:
        with tempfile.TemporaryDirectory(prefix="cerebro-preflight-") as raw_workspace:
            preflight_workspace = Path(raw_workspace)
            workspace_mode = preflight_workspace.lstat().st_mode
            if (
                not stat.S_ISDIR(workspace_mode)
                or stat.S_ISLNK(workspace_mode)
                or stat.S_IMODE(workspace_mode) != 0o700
            ):
                raise LoadError(
                    "private preflight workspace is not a mode-0700 directory"
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
                snapshot = preflight_workspace / f"source-{index:04d}.csv"

                try:
                    actual_hash = _copy_preflight_snapshot(source_file, snapshot)
                except LoadError as exc:
                    errors.append(str(exc))
                    continue
                if actual_hash != manifest_table.sha256:
                    errors.append(
                        f"SHA-256 mismatch for {manifest_table.file_name}: "
                        f"expected {manifest_table.sha256}, got {actual_hash}"
                    )

                try:
                    actual_header, actual_count = _read_csv_shape(snapshot)
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

                snapshot_inventory = SourceTableInventory(
                    name=table_inventory.name,
                    table_id=table_inventory.table_id,
                    source_file=snapshot,
                    source_file_sha256=table_inventory.source_file_sha256,
                    row_count=table_inventory.row_count,
                    columns=table_inventory.columns,
                    ddl=table_inventory.ddl,
                )
                try:
                    cast_count = _check_castability(snapshot_inventory)
                    if cast_count != manifest_table.row_count:
                        errors.append(
                            f"cast row count mismatch for {manifest_table.file_name}: "
                            f"expected {manifest_table.row_count}, got {cast_count}"
                        )
                except LoadError as exc:
                    errors.append(str(exc))
    except OSError as exc:
        raise LoadError("private CSV preflight workspace failed") from exc

    if errors:
        raise LoadError("CSV preflight failed: " + "; ".join(errors))
    return SourceInventory(
        source_manifest_sha256=source_manifest_sha256(manifest),
        bundle_sha256=semantic_bundle_sha256(bundle),
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
) -> tuple[int, os.stat_result]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(name, flags, dir_fd=directory_descriptor)
    except OSError as exc:
        raise LoadError(error_message) from exc
    try:
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


def _link_no_follow(source: Path, destination: Path) -> None:
    if os.link in getattr(os, "supports_follow_symlinks", ()):
        os.link(source, destination, follow_symlinks=False)
    else:
        os.link(source, destination)


def _read_bounded_descriptor(descriptor: int, limit: int) -> bytes:
    contents = bytearray()
    while len(contents) < limit:
        chunk = os.read(descriptor, min(64 * 1024, limit - len(contents)))
        if not chunk:
            break
        contents.extend(chunk)
    return bytes(contents)


def _validate_existing_receipt(receipt_path: Path, receipt_bytes: bytes) -> None:
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    flags = (
        os.O_RDONLY
        | getattr(os, "O_NONBLOCK", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | no_follow
    )
    try:
        descriptor = os.open(receipt_path, flags)
    except OSError as exc:
        raise LoadError("pre-existing receipt could not be securely opened") from exc

    try:
        descriptor_stat = os.fstat(descriptor)
        if not stat.S_ISREG(descriptor_stat.st_mode):
            raise LoadError("pre-existing receipt is not a regular file")
        if no_follow == 0:
            _validate_regular_descriptor_path(
                descriptor,
                receipt_path,
                error_message=(
                    "pre-existing receipt path is not the opened regular file"
                ),
            )
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
        _validate_regular_descriptor_path(
            descriptor,
            receipt_path,
            error_message="pre-existing receipt path changed while validating",
        )
    finally:
        os.close(descriptor)


def _create_private_workspace(
    target: Path,
) -> tuple[Path, int, tuple[int, int]]:
    try:
        workspace = Path(
            tempfile.mkdtemp(
                dir=target.parent,
                prefix=f".{target.name}.",
                suffix=".tmp",
            )
        )
    except OSError as exc:
        raise LoadError("could not create private materialization workspace") from exc

    descriptor: int | None = None
    identity: tuple[int, int] | None = None
    try:
        descriptor, _ = _open_directory_descriptor(
            workspace,
            error_message="private materialization workspace is not a directory",
        )
        os.fchmod(descriptor, 0o700)
        descriptor_stat = _validate_directory_descriptor_path(
            descriptor,
            workspace,
            required_mode=0o700,
            error_message="private materialization workspace is not mode 0700",
        )
        identity = _identity(descriptor_stat)
        return workspace, descriptor, identity
    except Exception:
        if descriptor is not None:
            try:
                descriptor_stat = os.fstat(descriptor)
                path_stat = os.stat(workspace, follow_symlinks=False)
                if stat.S_ISDIR(path_stat.st_mode) and _identity(
                    path_stat
                ) == _identity(descriptor_stat):
                    os.rmdir(workspace)
            except OSError:
                pass
            os.close(descriptor)
        else:
            try:
                workspace_stat = os.stat(workspace, follow_symlinks=False)
                if stat.S_ISDIR(workspace_stat.st_mode):
                    os.rmdir(workspace)
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
) -> Path:
    snapshot = workspace / f"source-{index:04d}.csv"
    _validate_directory_descriptor_path(
        workspace_descriptor,
        workspace,
        required_mode=0o700,
        error_message="source snapshot workspace is not the pinned mode-0700 directory",
    )
    source_descriptor = _open_regular_source(table.source_file)
    destination_descriptor: int | None = None
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
        destination_stat = os.fstat(destination_descriptor)
        known_entries[snapshot.name] = _identity(destination_stat)
        if not stat.S_ISREG(destination_stat.st_mode):
            raise LoadError("verified source snapshot is not a regular file")
        _validate_regular_descriptor_entry(
            destination_descriptor,
            workspace_descriptor,
            snapshot.name,
            error_message="verified source snapshot entry changed after creation",
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

        if digest.hexdigest() != table.source_file_sha256:
            raise LoadError(f"source changed after preflight: {table.source_file.name}")

        os.fchmod(destination_descriptor, 0o400)
        snapshot_stat = _validate_regular_descriptor_entry(
            destination_descriptor,
            workspace_descriptor,
            snapshot.name,
            error_message="verified source snapshot entry changed after copying",
        )
        if stat.S_IMODE(snapshot_stat.st_mode) != 0o400:
            raise LoadError("verified source snapshot is not mode 0400")
        if _sha256_descriptor(destination_descriptor) != table.source_file_sha256:
            raise LoadError("verified source snapshot hash changed after copying")
        return snapshot
    except OSError as exc:
        raise LoadError(
            f"could not create verified snapshot for {table.source_file.name}"
        ) from exc
    finally:
        if source_descriptor >= 0:
            os.close(source_descriptor)
        if destination_descriptor is not None:
            os.close(destination_descriptor)


def _snapshot_inventory(
    inventory: SourceInventory,
    workspace: Path,
    workspace_descriptor: int,
    known_entries: dict[str, tuple[int, int]],
) -> SourceInventory:
    snapshot_tables: list[SourceTableInventory] = []
    for index, table in enumerate(inventory.tables):
        snapshot = _copy_verified_snapshot(
            table,
            workspace,
            workspace_descriptor,
            known_entries,
            index,
        )
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
    return SourceInventory(
        source_manifest_sha256=inventory.source_manifest_sha256,
        bundle_sha256=inventory.bundle_sha256,
        tables=tuple(snapshot_tables),
    )


def _build_database(
    path: Path,
    inventory: SourceInventory,
    workspace_descriptor: int,
    known_entries: dict[str, tuple[int, int]],
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

    for table in inventory.tables:
        expected_identity = known_entries.get(table.source_file.name)
        try:
            snapshot_stat = os.stat(
                table.source_file.name,
                dir_fd=workspace_descriptor,
                follow_symlinks=False,
            )
        except OSError as exc:
            raise LoadError("source snapshot escaped the private workspace") from exc
        if (
            table.source_file.parent != path.parent
            or expected_identity is None
            or not stat.S_ISREG(snapshot_stat.st_mode)
            or _identity(snapshot_stat) != expected_identity
        ):
            raise LoadError("source snapshot escaped the private workspace")

    connection: duckdb.DuckDBPyConnection | None = None
    database_descriptor: int | None = None
    transaction_open = False
    try:
        try:
            connection = duckdb.connect(str(path))
            database_descriptor, database_stat = _open_regular_directory_entry(
                workspace_descriptor,
                path.name,
                error_message="database entry is not the opened regular file",
            )
            known_entries[path.name] = _identity(database_stat)
            connection.execute("BEGIN TRANSACTION")
            transaction_open = True
            for table in inventory.tables:
                if sha256_file(table.source_file) != table.source_file_sha256:
                    raise LoadError(f"verified source snapshot changed: {table.name}")
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
            _verify_materialization(connection, inventory)
            for table in inventory.tables:
                if sha256_file(table.source_file) != table.source_file_sha256:
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
) -> tuple[int, Path]:
    workspace_mode = receipt_workspace.lstat().st_mode
    if (
        not stat.S_ISDIR(workspace_mode)
        or stat.S_ISLNK(workspace_mode)
        or stat.S_IMODE(workspace_mode) != 0o700
    ):
        raise LoadError("receipt workspace is not a private mode-0700 directory")

    descriptor = -1
    try:
        descriptor, raw_path = tempfile.mkstemp(
            dir=receipt_workspace,
            prefix="receipt-",
            suffix=".tmp",
        )
        receipt_temp = Path(raw_path)
        if receipt_temp.parent != receipt_workspace:
            raise LoadError("temporary receipt escaped its private workspace")
        descriptor_stat = os.fstat(descriptor)
        if known_entries is not None:
            known_entries[receipt_temp.name] = _identity(descriptor_stat)
        descriptor_stat = _validate_regular_descriptor_path(
            descriptor,
            receipt_temp,
            error_message="temporary receipt path is not the opened regular file",
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
        _validate_regular_descriptor_path(
            descriptor,
            receipt_temp,
            error_message="temporary receipt path changed before publication",
        )
        return descriptor, receipt_temp
    except Exception:
        if descriptor >= 0:
            os.close(descriptor)
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
    inventory = preflight_csvs(csv_dir, bundle, manifest)
    target = Path(db_path)
    target_dir = target.parent
    if not target_dir.is_dir():
        raise LoadError(f"database target directory does not exist: {target_dir}")
    receipts = Path(receipt_dir) if receipt_dir is not None else target_dir

    target_parent_descriptor: int | None = None
    previous_target_descriptor: int | None = None
    previous_target_hash: str | None = None
    backup_name: str | None = None
    workspace: Path | None = None
    workspace_descriptor: int | None = None
    workspace_identity: tuple[int, int] | None = None
    workspace_entries: dict[str, tuple[int, int]] = {}
    database_descriptor: int | None = None
    receipt_workspace: Path | None = None
    receipt_workspace_descriptor: int | None = None
    receipt_workspace_identity: tuple[int, int] | None = None
    receipt_workspace_entries: dict[str, tuple[int, int]] = {}
    receipt_descriptor: int | None = None
    try:
        target_parent_descriptor, _ = _open_directory_descriptor(
            target_dir,
            error_message="database target directory could not be securely opened",
        )
        workspace, workspace_descriptor, workspace_identity = _create_private_workspace(
            target
        )
        _validate_directory_descriptor_entry(
            workspace_descriptor,
            target_parent_descriptor,
            workspace.name,
            required_mode=0o700,
            error_message="private workspace is not in the pinned target directory",
        )
        snapshot_inventory = _snapshot_inventory(
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
        )
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
        receipts.mkdir(parents=True, exist_ok=True)
        receipt_path = receipts / f"{receipt_hash}.json"
        (
            receipt_workspace,
            receipt_workspace_descriptor,
            receipt_workspace_identity,
        ) = _create_private_workspace(receipts / "materialization-receipt")
        receipt_descriptor, receipt_temp = _write_receipt_temp(
            receipt_workspace,
            receipt_bytes,
            receipt_workspace_entries,
        )

        _validate_regular_descriptor_path(
            receipt_descriptor,
            receipt_temp,
            error_message="temporary receipt path changed before publication",
        )
        try:
            _link_no_follow(receipt_temp, receipt_path)
        except FileExistsError:
            _validate_existing_receipt(receipt_path, receipt_bytes)
        else:
            _validate_regular_descriptor_path(
                receipt_descriptor,
                receipt_path,
                error_message="published receipt is not the validated regular file",
            )

        # Publication makes this immutable path shared evidence. If database
        # replacement fails, leave the harmless orphan in place: its database
        # hash cannot validate against a mismatched target, and another
        # invocation may already have adopted it.
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
        if receipt_descriptor is not None:
            os.close(receipt_descriptor)
        _remove_private_workspace(
            receipt_workspace,
            descriptor=receipt_workspace_descriptor,
            expected_identity=receipt_workspace_identity,
            known_entries=receipt_workspace_entries,
            remove_mismatched_symlinks=True,
        )
        if receipt_workspace_descriptor is not None:
            os.close(receipt_workspace_descriptor)
        _remove_private_workspace(
            workspace,
            descriptor=workspace_descriptor,
            expected_identity=workspace_identity,
            known_entries=workspace_entries,
            parent_descriptor=target_parent_descriptor,
        )
        if previous_target_descriptor is not None:
            os.close(previous_target_descriptor)
        if database_descriptor is not None:
            os.close(database_descriptor)
        if workspace_descriptor is not None:
            os.close(workspace_descriptor)
        if target_parent_descriptor is not None:
            os.close(target_parent_descriptor)
