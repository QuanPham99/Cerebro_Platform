from __future__ import annotations

import csv
import os
import re
import shutil
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
    for name in sorted(bundle_tables):
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

        try:
            actual_hash = sha256_file(source_file)
            if actual_hash != manifest_table.sha256:
                errors.append(
                    f"SHA-256 mismatch for {manifest_table.file_name}: "
                    f"expected {manifest_table.sha256}, got {actual_hash}"
                )
        except OSError as exc:
            errors.append(f"could not hash {manifest_table.file_name}: {exc}")

        try:
            actual_header, actual_count = _read_csv_shape(source_file)
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

        try:
            cast_count = _check_castability(table_inventory)
            if cast_count != manifest_table.row_count:
                errors.append(
                    f"cast row count mismatch for {manifest_table.file_name}: "
                    f"expected {manifest_table.row_count}, got {cast_count}"
                )
        except LoadError as exc:
            errors.append(str(exc))

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


def _create_private_workspace(target: Path) -> Path:
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

    try:
        workspace_mode = workspace.lstat().st_mode
        if not stat.S_ISDIR(workspace_mode) or stat.S_ISLNK(workspace_mode):
            raise LoadError("private materialization workspace is not a directory")
        os.chmod(workspace, 0o700)
        if stat.S_IMODE(workspace.lstat().st_mode) != 0o700:
            raise LoadError("private materialization workspace is not mode 0700")
        return workspace
    except Exception:
        shutil.rmtree(workspace, ignore_errors=True)
        raise


def _remove_private_workspace(
    workspace: Path | None,
    *,
    expected_identity: tuple[int, int] | None = None,
) -> None:
    if workspace is None:
        return
    if expected_identity is not None:
        try:
            workspace_stat = os.stat(workspace, follow_symlinks=False)
        except OSError:
            return
        if (
            not stat.S_ISDIR(workspace_stat.st_mode)
            or (workspace_stat.st_dev, workspace_stat.st_ino) != expected_identity
        ):
            return
    # rmtree refuses to traverse a symlink, so cleanup remains scoped to the
    # invocation-owned directory even if the path is unexpectedly disturbed.
    try:
        shutil.rmtree(workspace)
    except FileNotFoundError:
        pass
    except OSError:
        # Cleanup must not mask the materialization result or original failure.
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
    index: int,
) -> Path:
    snapshot = workspace / f"source-{index:04d}.csv"
    source_descriptor = _open_regular_source(table.source_file)
    destination_descriptor: int | None = None
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    digest = sha256()
    try:
        destination_descriptor = os.open(snapshot, flags, 0o600)
        with os.fdopen(source_descriptor, "rb") as source:
            source_descriptor = -1
            with os.fdopen(destination_descriptor, "wb") as destination:
                destination_descriptor = None
                while chunk := source.read(1024 * 1024):
                    digest.update(chunk)
                    destination.write(chunk)
                destination.flush()
                os.fsync(destination.fileno())
    except OSError as exc:
        raise LoadError(
            f"could not create verified snapshot for {table.source_file.name}"
        ) from exc
    finally:
        if source_descriptor >= 0:
            os.close(source_descriptor)
        if destination_descriptor is not None:
            os.close(destination_descriptor)

    if digest.hexdigest() != table.source_file_sha256:
        raise LoadError(f"source changed after preflight: {table.source_file.name}")

    try:
        os.chmod(snapshot, 0o400)
        snapshot_mode = snapshot.lstat().st_mode
        if not stat.S_ISREG(snapshot_mode) or stat.S_ISLNK(snapshot_mode):
            raise LoadError("verified source snapshot is not a regular file")
        if sha256_file(snapshot) != table.source_file_sha256:
            raise LoadError("verified source snapshot hash changed after copying")
    except OSError as exc:
        raise LoadError(
            f"could not validate source snapshot for {table.source_file.name}"
        ) from exc
    return snapshot


def _snapshot_inventory(
    inventory: SourceInventory,
    workspace: Path,
) -> SourceInventory:
    snapshot_tables: list[SourceTableInventory] = []
    for index, table in enumerate(inventory.tables):
        snapshot_tables.append(
            SourceTableInventory(
                name=table.name,
                table_id=table.table_id,
                source_file=_copy_verified_snapshot(table, workspace, index),
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


def _build_database(path: Path, inventory: SourceInventory) -> None:
    workspace_mode = path.parent.lstat().st_mode
    if (
        not stat.S_ISDIR(workspace_mode)
        or stat.S_ISLNK(workspace_mode)
        or stat.S_IMODE(workspace_mode) != 0o700
    ):
        raise LoadError("database workspace is not a private mode-0700 directory")
    if os.path.lexists(path):
        raise LoadError("private database path already exists")
    for table in inventory.tables:
        snapshot_mode = table.source_file.lstat().st_mode
        if (
            table.source_file.parent != path.parent
            or not stat.S_ISREG(snapshot_mode)
            or stat.S_ISLNK(snapshot_mode)
        ):
            raise LoadError("source snapshot escaped the private workspace")

    connection: duckdb.DuckDBPyConnection | None = None
    transaction_open = False
    try:
        connection = duckdb.connect(str(path))
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


def _write_receipt_temp(
    receipt_workspace: Path,
    receipt_bytes: bytes,
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

    workspace: Path | None = None
    receipt_workspace: Path | None = None
    receipt_workspace_identity: tuple[int, int] | None = None
    receipt_descriptor: int | None = None
    receipt_path: Path | None = None
    try:
        workspace = _create_private_workspace(target)
        snapshot_inventory = _snapshot_inventory(inventory, workspace)
        database_temp = workspace / "database.duckdb"
        _build_database(database_temp, snapshot_inventory)
        database_hash = sha256_file(database_temp)

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
        receipt_workspace = _create_private_workspace(
            receipts / "materialization-receipt"
        )
        receipt_workspace_stat = receipt_workspace.lstat()
        receipt_workspace_identity = (
            receipt_workspace_stat.st_dev,
            receipt_workspace_stat.st_ino,
        )
        receipt_descriptor, receipt_temp = _write_receipt_temp(
            receipt_workspace,
            receipt_bytes,
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
        os.replace(database_temp, target)
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
            expected_identity=receipt_workspace_identity,
        )
        _remove_private_workspace(workspace)
