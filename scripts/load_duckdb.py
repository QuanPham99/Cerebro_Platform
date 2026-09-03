from __future__ import annotations

import csv
import os
import re
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


def _bundle_sha256(bundle: SemanticBundle) -> str:
    normalized = bundle.model_copy(deep=True)
    normalized.objects = sorted(
        normalized.objects,
        key=lambda item: (item.type, item.id, item.path),
    )
    return sha256(canonical_json_bytes(normalized, exclude={"root"})).hexdigest()


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
        bundle_sha256=_bundle_sha256(bundle),
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


def _unique_temp_path(directory: Path, *, prefix: str, suffix: str) -> Path:
    descriptor, raw_path = tempfile.mkstemp(
        dir=directory,
        prefix=prefix,
        suffix=suffix,
    )
    os.close(descriptor)
    return Path(raw_path)


def _remove_database_temp(path: Path | None) -> None:
    if path is None:
        return
    path.unlink(missing_ok=True)
    Path(f"{path}.wal").unlink(missing_ok=True)


def _build_database(path: Path, inventory: SourceInventory) -> None:
    connection: duckdb.DuckDBPyConnection | None = None
    transaction_open = False
    try:
        connection = duckdb.connect(str(path))
        connection.execute("BEGIN TRANSACTION")
        transaction_open = True
        for table in inventory.tables:
            if sha256_file(table.source_file) != table.source_file_sha256:
                raise LoadError(
                    f"source changed after preflight: {table.source_file.name}"
                )
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
                    f"source changed while loading: {table.source_file.name}"
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


def _write_receipt_temp(receipt_dir: Path, receipt_bytes: bytes) -> Path:
    receipt_temp = _unique_temp_path(
        receipt_dir,
        prefix=".materialization-receipt.",
        suffix=".tmp",
    )
    try:
        with receipt_temp.open("wb") as destination:
            destination.write(receipt_bytes)
            destination.flush()
            os.fsync(destination.fileno())
        if receipt_temp.read_bytes() != receipt_bytes:
            raise LoadError("temporary receipt validation failed")
        validated = MaterializationReceipt.model_validate_json(receipt_bytes)
        if canonical_json_bytes(validated) != receipt_bytes:
            raise LoadError("temporary receipt is not canonical")
        return receipt_temp
    except Exception:
        receipt_temp.unlink(missing_ok=True)
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

    database_temp: Path | None = None
    receipt_temp: Path | None = None
    receipt_path: Path | None = None
    receipt_bytes: bytes | None = None
    receipt_created = False
    try:
        database_temp = _unique_temp_path(
            target_dir,
            prefix=f".{target.name}.",
            suffix=".tmp",
        )
        # DuckDB creates a new database but rejects a pre-created empty file.
        # mkstemp claims a collision-resistant name before we release it here.
        database_temp.unlink()
        _build_database(database_temp, inventory)
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
        receipt_temp = _write_receipt_temp(receipts, receipt_bytes)

        try:
            os.link(receipt_temp, receipt_path)
        except FileExistsError:
            existing_bytes = receipt_path.read_bytes()
            if existing_bytes != receipt_bytes:
                raise LoadError(
                    f"content-addressed receipt collision: {receipt_path.name}"
                )
            existing = MaterializationReceipt.model_validate_json(existing_bytes)
            if canonical_json_bytes(existing) != receipt_bytes:
                raise LoadError(
                    f"pre-existing receipt is not canonical: {receipt_path.name}"
                )
            receipt_temp.unlink()
            receipt_temp = None
        else:
            receipt_created = True
            receipt_temp.unlink()
            receipt_temp = None

        # The receipt is finalized immediately before the database replacement.
        os.replace(database_temp, target)
        database_temp = None
        return receipt, receipt_path
    except Exception as exc:
        if (
            receipt_created
            and receipt_path is not None
            and receipt_bytes is not None
            and receipt_path.exists()
        ):
            try:
                if receipt_path.read_bytes() == receipt_bytes:
                    receipt_path.unlink()
            except OSError:
                pass
        if isinstance(exc, LoadError):
            raise
        raise LoadError(f"DuckDB materialization failed: {exc}") from exc
    finally:
        if receipt_temp is not None:
            receipt_temp.unlink(missing_ok=True)
        _remove_database_temp(database_temp)
