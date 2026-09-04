from __future__ import annotations

import csv
import hashlib
import shutil
import threading
import time
from collections.abc import Callable
from dataclasses import FrozenInstanceError
from importlib import import_module
from pathlib import Path
from typing import Any

import duckdb
import pytest

from cerebro.bundle import BundleLoader
from cerebro.models import SemanticBundle, SourceManifest, SourceTableManifest
from cerebro.paths import DEFAULT_BUNDLE
from cerebro.provenance import (
    canonical_json_bytes,
    manifest_table_id,
    sha256_file,
    source_manifest_sha256,
)

SOURCE_VALUE = "SOURCE_ROW_VALUE_THAT_MUST_NOT_ENTER_A_RECEIPT"


@pytest.fixture()
def bundle() -> SemanticBundle:
    return BundleLoader().load(DEFAULT_BUNDLE)


def _loader() -> Any:
    return import_module("scripts.load_duckdb")


def _active_tables(bundle: SemanticBundle) -> list[Any]:
    return sorted(
        (
            obj
            for obj in bundle.objects
            if obj.type == "table" and obj.status == "active"
        ),
        key=lambda obj: obj.id,
    )


def _raw_name(table: Any) -> str:
    prefix, separator, raw_name = table.id.partition(".")
    assert (prefix, separator, raw_name) == ("table", ".", raw_name)
    return raw_name


def _synthetic_value(data_type: str, table_name: str, column_name: str) -> str:
    if data_type == "BIGINT":
        return "17"
    if data_type == "DOUBLE":
        return "17.25"
    if data_type == "DATE":
        return "2026-08-27"
    if data_type == "VARCHAR":
        return f"{SOURCE_VALUE}_{table_name}_{column_name}"
    raise AssertionError(f"fixture has no synthetic value for {data_type}")


def _write_rows(path: Path, rows: list[list[str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as destination:
        csv.writer(destination, lineterminator="\n").writerows(rows)


def _read_rows(path: Path) -> list[list[str]]:
    with path.open(encoding="utf-8", newline="") as source:
        return list(csv.reader(source))


def write_bundle_csv_fixture(
    tmp_path: Path,
    bundle: SemanticBundle,
) -> tuple[Path, SourceManifest]:
    csv_dir = tmp_path / "csv"
    csv_dir.mkdir()
    tables: list[SourceTableManifest] = []
    for table in _active_tables(bundle):
        name = _raw_name(table)
        columns = table.cerebro["columns"]
        source_file = csv_dir / f"{name}.csv"
        _write_rows(
            source_file,
            [
                [str(column["name"]) for column in columns],
                [
                    _synthetic_value(
                        str(column["data_type"]), name, str(column["name"])
                    )
                    for column in columns
                ],
            ],
        )
        tables.append(
            SourceTableManifest(
                name=name,
                file_name=source_file.name,
                sha256=sha256_file(source_file),
                row_count=1,
            )
        )
    return csv_dir, SourceManifest(tables=tuple(tables))


def _rehash_manifest(
    manifest: SourceManifest,
    csv_dir: Path,
) -> SourceManifest:
    return SourceManifest(
        tables=tuple(
            SourceTableManifest(
                name=table.name,
                file_name=table.file_name,
                sha256=sha256_file(csv_dir / table.file_name),
                row_count=table.row_count,
            )
            for table in manifest.tables
        )
    )


def _replace_manifest_table(
    manifest: SourceManifest,
    table_name: str,
    **updates: Any,
) -> SourceManifest:
    return SourceManifest(
        tables=tuple(
            SourceTableManifest(
                **(
                    {
                        "name": table.name,
                        "file_name": table.file_name,
                        "sha256": table.sha256,
                        "row_count": table.row_count,
                    }
                    | (updates if table.name == table_name else {})
                )
            )
            for table in manifest.tables
        )
    )


def _assert_no_invocation_temps(target: Path, receipt_dir: Path | None = None) -> None:
    assert not list(target.parent.glob(f".{target.name}.*.tmp*"))
    if receipt_dir is not None and receipt_dir.exists():
        assert not list(receipt_dir.glob(".*.tmp*"))


def _read_directory_entry(loader: Any, directory_descriptor: int, name: Any) -> bytes:
    descriptor = loader.os.open(
        name,
        loader.os.O_RDONLY | getattr(loader.os, "O_CLOEXEC", 0),
        dir_fd=directory_descriptor,
    )
    try:
        contents = bytearray()
        offset = 0
        while chunk := loader.os.pread(descriptor, 64 * 1024, offset):
            contents.extend(chunk)
            offset += len(chunk)
        return bytes(contents)
    finally:
        loader.os.close(descriptor)


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def test_preflight_returns_immutable_name_aligned_inventory(
    tmp_path: Path,
    bundle: SemanticBundle,
) -> None:
    loader = _loader()
    csv_dir, manifest = write_bundle_csv_fixture(tmp_path, bundle)
    reordered_bundle = bundle.model_copy(deep=True)
    reordered_bundle.objects.reverse()
    reordered_manifest = SourceManifest(tables=tuple(reversed(manifest.tables)))

    inventory = loader.preflight_csvs(
        csv_dir,
        reordered_bundle,
        reordered_manifest,
    )

    expected_names = sorted(table.name for table in manifest.tables)
    provenance = import_module("cerebro.provenance")
    assert hasattr(provenance, "semantic_bundle_sha256")
    assert inventory.bundle_sha256 == provenance.semantic_bundle_sha256(
        reordered_bundle
    )
    assert not hasattr(loader, "_bundle_sha256")
    assert [table.name for table in inventory.tables] == expected_names
    assert isinstance(inventory.tables, tuple)
    assert all(isinstance(table.columns, tuple) for table in inventory.tables)
    with pytest.raises(FrozenInstanceError):
        inventory.tables = ()
    with pytest.raises(FrozenInstanceError):
        inventory.tables[0].name = "changed"


@pytest.mark.parametrize("change", ["missing", "extra"])
def test_preflight_requires_exact_csv_basename_set(
    tmp_path: Path,
    bundle: SemanticBundle,
    change: str,
) -> None:
    loader = _loader()
    csv_dir, manifest = write_bundle_csv_fixture(tmp_path, bundle)
    if change == "missing":
        (csv_dir / manifest.tables[0].file_name).unlink()
    else:
        (csv_dir / "unexpected.csv").write_text("value\n1\n", encoding="utf-8")

    with pytest.raises(loader.LoadError, match="CSV file set"):
        loader.preflight_csvs(csv_dir, bundle, manifest)


@pytest.mark.parametrize("change", ["reordered", "missing", "extra"])
def test_preflight_requires_exact_utf8_header_order(
    tmp_path: Path,
    bundle: SemanticBundle,
    change: str,
) -> None:
    loader = _loader()
    csv_dir, manifest = write_bundle_csv_fixture(tmp_path, bundle)
    table = manifest.tables[0]
    source_file = csv_dir / table.file_name
    rows = _read_rows(source_file)
    if change == "reordered":
        rows[0][0], rows[0][1] = rows[0][1], rows[0][0]
    elif change == "missing":
        rows = [row[:-1] for row in rows]
    else:
        rows = [row + ["unexpected_column"] for row in rows]
    _write_rows(source_file, rows)
    manifest = _rehash_manifest(manifest, csv_dir)

    with pytest.raises(loader.LoadError, match="header"):
        loader.preflight_csvs(csv_dir, bundle, manifest)


def test_preflight_rejects_non_utf8_header(
    tmp_path: Path,
    bundle: SemanticBundle,
) -> None:
    loader = _loader()
    csv_dir, manifest = write_bundle_csv_fixture(tmp_path, bundle)
    table = manifest.tables[0]
    (csv_dir / table.file_name).write_bytes(b"\xff,broken\n1,2\n")
    manifest = _rehash_manifest(manifest, csv_dir)

    with pytest.raises(loader.LoadError, match="UTF-8"):
        loader.preflight_csvs(csv_dir, bundle, manifest)


def test_preflight_requires_manifest_file_hash(
    tmp_path: Path,
    bundle: SemanticBundle,
) -> None:
    loader = _loader()
    csv_dir, manifest = write_bundle_csv_fixture(tmp_path, bundle)
    source_file = csv_dir / manifest.tables[0].file_name
    source_file.write_bytes(source_file.read_bytes() + b"\n")

    with pytest.raises(loader.LoadError, match="SHA-256"):
        loader.preflight_csvs(csv_dir, bundle, manifest)


def test_preflight_requires_exact_row_count(
    tmp_path: Path,
    bundle: SemanticBundle,
) -> None:
    loader = _loader()
    csv_dir, manifest = write_bundle_csv_fixture(tmp_path, bundle)
    first = manifest.tables[0]
    manifest = _replace_manifest_table(
        manifest,
        first.name,
        row_count=first.row_count + 1,
    )

    with pytest.raises(loader.LoadError, match="row count"):
        loader.preflight_csvs(csv_dir, bundle, manifest)


def test_preflight_requires_manifest_to_match_all_active_bundle_tables(
    tmp_path: Path,
    bundle: SemanticBundle,
) -> None:
    loader = _loader()
    csv_dir, manifest = write_bundle_csv_fixture(tmp_path, bundle)
    removed = manifest.tables[-1]
    (csv_dir / removed.file_name).unlink()
    manifest = SourceManifest(tables=manifest.tables[:-1])

    with pytest.raises(loader.LoadError, match="active bundle table set"):
        loader.preflight_csvs(csv_dir, bundle, manifest)


def test_preflight_rejects_duplicate_active_bundle_tables(
    tmp_path: Path,
    bundle: SemanticBundle,
) -> None:
    loader = _loader()
    csv_dir, manifest = write_bundle_csv_fixture(tmp_path, bundle)
    invalid = bundle.model_copy(deep=True)
    invalid.objects.append(_active_tables(invalid)[0].model_copy(deep=True))

    with pytest.raises(loader.LoadError, match="duplicate active table"):
        loader.preflight_csvs(csv_dir, invalid, manifest)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda columns: columns.append(dict(columns[0])),
            "duplicate column",
        ),
        (
            lambda columns: columns[0].pop("data_type"),
            "data type",
        ),
        (
            lambda columns: columns[0].update(data_type="BIGINT; DROP TABLE x"),
            "data type",
        ),
        (
            lambda columns: columns[0].update(name='unsafe"column'),
            "column name",
        ),
    ],
)
def test_preflight_rejects_ambiguous_or_unsafe_bundle_columns(
    tmp_path: Path,
    bundle: SemanticBundle,
    mutation: Callable[[list[dict[str, Any]]], None],
    message: str,
) -> None:
    loader = _loader()
    csv_dir, manifest = write_bundle_csv_fixture(tmp_path, bundle)
    invalid = bundle.model_copy(deep=True)
    columns = _active_tables(invalid)[0].cerebro["columns"]
    mutation(columns)

    with pytest.raises(loader.LoadError, match=message):
        loader.preflight_csvs(csv_dir, invalid, manifest)


def test_preflight_rejects_value_not_castable_to_bundle_declared_type(
    tmp_path: Path,
    bundle: SemanticBundle,
) -> None:
    loader = _loader()
    csv_dir, manifest = write_bundle_csv_fixture(tmp_path, bundle)
    table = next(
        table
        for table in _active_tables(bundle)
        if any(column["data_type"] == "BIGINT" for column in table.cerebro["columns"])
    )
    column_index = next(
        index
        for index, column in enumerate(table.cerebro["columns"])
        if column["data_type"] == "BIGINT"
    )
    source_file = csv_dir / f"{_raw_name(table)}.csv"
    rows = _read_rows(source_file)
    rows[1][column_index] = "not-an-integer"
    _write_rows(source_file, rows)
    manifest = _rehash_manifest(manifest, csv_dir)

    with pytest.raises(loader.LoadError, match="cast"):
        loader.preflight_csvs(csv_dir, bundle, manifest)


def test_invalid_preflight_never_opens_or_mutates_target_database(
    tmp_path: Path,
    bundle: SemanticBundle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loader = _loader()
    csv_dir, manifest = write_bundle_csv_fixture(tmp_path, bundle)
    target = tmp_path / "workshop.duckdb"
    target.write_bytes(b"existing target bytes")
    before = target.read_bytes()
    source_file = csv_dir / manifest.tables[0].file_name
    source_file.write_bytes(source_file.read_bytes() + b"changed")
    real_connect = duckdb.connect
    opened: list[str] = []

    def tracking_connect(database: str = ":memory:", *args: Any, **kwargs: Any):
        opened.append(str(database))
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr(loader.duckdb, "connect", tracking_connect)

    with pytest.raises(loader.LoadError):
        loader.load_csvs(csv_dir, target, bundle, manifest)

    assert target.read_bytes() == before
    assert str(target) not in opened
    assert not any(Path(path).name.startswith(f".{target.name}.") for path in opened)
    _assert_no_invocation_temps(target)


def test_load_materializes_exact_schema_and_value_free_content_addressed_receipt(
    tmp_path: Path,
    bundle: SemanticBundle,
) -> None:
    loader = _loader()
    csv_dir, manifest = write_bundle_csv_fixture(tmp_path, bundle)
    target = tmp_path / "workshop.duckdb"
    receipt_dir = tmp_path / "receipts"

    receipt, receipt_path = loader.load_csvs(
        csv_dir,
        target,
        bundle,
        manifest,
        receipt_dir=receipt_dir,
    )

    assert receipt.source_manifest_sha256 == source_manifest_sha256(manifest)
    assert receipt.database_sha256 == sha256_file(target)
    assert receipt.engine == "duckdb"
    assert [table.table_id for table in receipt.tables] == [
        manifest_table_id(table.name) for table in manifest.tables
    ]
    assert [table.source_file_sha256 for table in receipt.tables] == [
        table.sha256 for table in manifest.tables
    ]
    receipt_bytes = canonical_json_bytes(receipt)
    assert receipt_path.read_bytes() == receipt_bytes
    assert receipt_path.name == f"{hashlib.sha256(receipt_bytes).hexdigest()}.json"
    assert receipt_path.parent == receipt_dir
    assert SOURCE_VALUE.encode() not in receipt_bytes
    assert str(tmp_path).encode() not in receipt_bytes
    assert str(csv_dir).encode() not in receipt_bytes

    connection = duckdb.connect(str(target), read_only=True)
    try:
        actual_tables = [
            row[0]
            for row in connection.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'main' AND table_type = 'BASE TABLE' "
                "ORDER BY table_name"
            ).fetchall()
        ]
        expected_tables = [_raw_name(table) for table in _active_tables(bundle)]
        assert actual_tables == expected_tables
        for table in _active_tables(bundle):
            table_name = _raw_name(table)
            actual_columns = connection.execute(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_schema = 'main' AND table_name = ? "
                "ORDER BY ordinal_position",
                [table_name],
            ).fetchall()
            assert actual_columns == [
                (column["name"], column["data_type"])
                for column in table.cerebro["columns"]
            ]
            count = connection.execute(
                f"SELECT count(*) FROM {_quote(table_name)}"
            ).fetchone()[0]
            assert count == 1
    finally:
        connection.close()
    _assert_no_invocation_temps(target, receipt_dir)


def test_materialization_receipt_and_bundle_root_share_preflight_identity(
    tmp_path: Path,
) -> None:
    loader = _loader()
    preflight = import_module("scripts.text2sql_preflight")
    bundle_root = tmp_path / "bundle"
    shutil.copytree(DEFAULT_BUNDLE, bundle_root)
    governed_bundle = import_module("cerebro.bundle").load_validated_bundle(bundle_root)
    csv_dir, manifest = write_bundle_csv_fixture(tmp_path, governed_bundle)
    manifest_path = tmp_path / "source-manifest.json"
    manifest_path.write_bytes(canonical_json_bytes(manifest))
    target = tmp_path / "workshop.duckdb"
    receipt_dir = tmp_path / "receipts"

    receipt, receipt_path = loader.load_csvs(
        csv_dir,
        target,
        governed_bundle,
        manifest,
        receipt_dir=receipt_dir,
    )

    report = preflight.check_preflight(
        csv_dir=csv_dir,
        manifest_path=manifest_path,
        bundle_path=bundle_root,
        environ={},
        database_path=target,
        materialization_receipt_path=receipt_path,
        provider_capability_receipt_path=None,
    )
    assert report.data_prerequisites_ready is True
    assert [blocker.code for blocker in report.blockers if blocker.gate == "data"] == []

    governed_object = bundle_root / "tables" / "accounts.md"
    original_text = governed_object.read_text(encoding="utf-8")
    marker = "classification: confidential"
    assert marker in original_text
    governed_object.write_text(
        original_text.replace(marker, "classification: restricted", 1),
        encoding="utf-8",
    )

    drifted_report = preflight.check_preflight(
        csv_dir=csv_dir,
        manifest_path=manifest_path,
        bundle_path=bundle_root,
        environ={},
        database_path=target,
        materialization_receipt_path=receipt_path,
        provider_capability_receipt_path=None,
    )
    assert drifted_report.data_prerequisites_ready is False
    assert {
        blocker.code for blocker in drifted_report.blockers if blocker.gate == "data"
    } == {"bundle_hash_mismatch"}
    serialized = drifted_report.model_dump_json()
    assert str(bundle_root) not in serialized
    assert "restricted" not in serialized
    assert receipt.bundle_sha256
    _assert_no_invocation_temps(target, receipt_dir)


def test_late_load_failure_preserves_existing_database(
    tmp_path: Path,
    bundle: SemanticBundle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loader = _loader()
    target = tmp_path / "workshop.duckdb"
    connection = duckdb.connect(str(target))
    connection.execute("CREATE TABLE sentinel(value INTEGER)")
    connection.execute("INSERT INTO sentinel VALUES (7)")
    connection.close()
    before = target.read_bytes()

    csv_dir, manifest = write_bundle_csv_fixture(tmp_path, bundle)
    real = loader._load_table
    calls = {"count": 0}

    def fail_on_fifth(*args: Any, **kwargs: Any) -> int:
        calls["count"] += 1
        if calls["count"] == 5:
            raise loader.LoadError("injected late failure")
        return real(*args, **kwargs)

    monkeypatch.setattr(loader, "_load_table", fail_on_fifth)
    with pytest.raises(loader.LoadError, match="injected late failure"):
        loader.load_csvs(csv_dir, target, bundle, manifest)

    assert calls["count"] == 5
    assert target.read_bytes() == before
    _assert_no_invocation_temps(target)


def test_source_aba_swap_cannot_change_verified_snapshot_bytes(
    tmp_path: Path,
    bundle: SemanticBundle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loader = _loader()
    csv_dir, manifest = write_bundle_csv_fixture(tmp_path, bundle)
    target = tmp_path / "workshop.duckdb"
    receipt_dir = tmp_path / "receipts"
    selected_table = next(
        table
        for table in _active_tables(bundle)
        if any(column["data_type"] == "VARCHAR" for column in table.cerebro["columns"])
    )
    selected_name = _raw_name(selected_table)
    selected_index = (
        sorted(table.name for table in manifest.tables).index(selected_name) + 1
    )
    selected_column_index = next(
        index
        for index, column in enumerate(selected_table.cerebro["columns"])
        if column["data_type"] == "VARCHAR"
    )
    selected_column = selected_table.cerebro["columns"][selected_column_index]["name"]
    source_file = csv_dir / f"{selected_name}.csv"
    original_bytes = source_file.read_bytes()
    original_rows = _read_rows(source_file)
    unchecked_value = "UNCHECKED_ABA_SOURCE_VALUE"
    changed_rows = [row.copy() for row in original_rows]
    changed_rows[1][selected_column_index] = unchecked_value
    changed_file = tmp_path / "changed.csv"
    _write_rows(changed_file, changed_rows)
    changed_bytes = changed_file.read_bytes()
    changed_file.unlink()
    real_load_table = loader._load_table
    calls = {"count": 0}

    def swap_original_while_table_loads(
        connection: duckdb.DuckDBPyConnection,
        load_source: Path,
        ddl: str,
    ) -> int:
        calls["count"] += 1
        if calls["count"] != selected_index:
            return real_load_table(connection, load_source, ddl)
        source_file.write_bytes(changed_bytes)
        try:
            return real_load_table(connection, load_source, ddl)
        finally:
            source_file.write_bytes(original_bytes)

    monkeypatch.setattr(loader, "_load_table", swap_original_while_table_loads)

    receipt, receipt_path = loader.load_csvs(
        csv_dir,
        target,
        bundle,
        manifest,
        receipt_dir=receipt_dir,
    )

    assert calls["count"] == len(manifest.tables)
    assert source_file.read_bytes() == original_bytes
    connection = duckdb.connect(str(target), read_only=True)
    try:
        loaded_value = connection.execute(
            f"SELECT {_quote(selected_column)} FROM {_quote(selected_name)}"
        ).fetchone()[0]
    finally:
        connection.close()
    assert loaded_value == original_rows[1][selected_column_index]
    assert loaded_value != unchecked_value
    assert [table.source_file_sha256 for table in receipt.tables] == [
        table.sha256 for table in manifest.tables
    ]
    assert unchecked_value.encode() not in receipt_path.read_bytes()
    _assert_no_invocation_temps(target, receipt_dir)


def test_post_preflight_source_symlink_is_rejected_before_database_open(
    tmp_path: Path,
    bundle: SemanticBundle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loader = _loader()
    csv_dir, manifest = write_bundle_csv_fixture(tmp_path, bundle)
    target = tmp_path / "workshop.duckdb"
    target.write_bytes(b"existing target bytes")
    target_before = target.read_bytes()
    backing_file = tmp_path / "verified-source-copy.csv"
    real_preflight = loader.preflight_csvs
    real_connect = loader.duckdb.connect
    opened_databases: list[Path] = []

    def preflight_then_substitute_symlink(*args: Any, **kwargs: Any) -> Any:
        inventory = real_preflight(*args, **kwargs)
        source_file = inventory.tables[0].source_file
        backing_file.write_bytes(source_file.read_bytes())
        source_file.unlink()
        source_file.symlink_to(backing_file)
        return inventory

    def tracking_connect(database: str = ":memory:", *args: Any, **kwargs: Any):
        if str(database) != ":memory:":
            opened_databases.append(Path(database))
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr(loader, "preflight_csvs", preflight_then_substitute_symlink)
    monkeypatch.setattr(loader.duckdb, "connect", tracking_connect)

    with pytest.raises(loader.LoadError, match="securely open source"):
        loader.load_csvs(csv_dir, target, bundle, manifest)

    assert not opened_databases
    assert target.read_bytes() == target_before
    assert (csv_dir / manifest.tables[0].file_name).is_symlink()
    _assert_no_invocation_temps(target)


def test_database_temp_uses_private_workspace_not_claimable_parent_path(
    tmp_path: Path,
    bundle: SemanticBundle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loader = _loader()
    csv_dir, manifest = write_bundle_csv_fixture(tmp_path, bundle)
    target = tmp_path / "workshop.duckdb"
    connection = duckdb.connect(str(target))
    connection.execute("CREATE TABLE sentinel(value INTEGER)")
    connection.execute("INSERT INTO sentinel VALUES (7)")
    connection.close()
    target_before = target.read_bytes()
    claimant = target.parent / "database.duckdb"
    claimant.symlink_to(target)
    real_connect = loader.duckdb.connect
    observed: list[dict[str, Any]] = []
    loaded_sources: list[Path] = []

    def tracking_connect(database: str = ":memory:", *args: Any, **kwargs: Any):
        if str(database) != ":memory:":
            database_path = Path(database)
            snapshots = tuple(database_path.parent.glob("*.csv"))
            observed.append(
                {
                    "path": database_path,
                    "path_existed": loader.os.path.lexists(database_path),
                    "parent_is_symlink": database_path.parent.is_symlink(),
                    "parent_mode": database_path.parent.stat().st_mode & 0o777,
                    "workspace_is_symlink": database_path.parent.parent.is_symlink(),
                    "workspace_mode": (
                        database_path.parent.parent.stat().st_mode & 0o777
                    ),
                    "snapshots": tuple(
                        (snapshot, snapshot.is_file(), snapshot.is_symlink())
                        for snapshot in snapshots
                    ),
                }
            )
        return real_connect(database, *args, **kwargs)

    def fail_first_load(
        _connection: duckdb.DuckDBPyConnection,
        source_file: Path,
        _ddl: str,
    ) -> int:
        loaded_sources.append(Path(source_file))
        raise loader.LoadError("injected private-path failure")

    monkeypatch.setattr(loader.duckdb, "connect", tracking_connect)
    monkeypatch.setattr(loader, "_load_table", fail_first_load)

    with pytest.raises(loader.LoadError, match="injected private-path failure"):
        loader.load_csvs(csv_dir, target, bundle, manifest)

    assert len(observed) == 1
    database = observed[0]
    database_path = database["path"]
    assert database_path.name == "database.duckdb"
    assert database_path.parent.parent.parent == target.parent
    assert database_path.parent.parent != target.parent
    assert not database["path_existed"]
    assert not database["parent_is_symlink"]
    assert not database["workspace_is_symlink"]
    assert database["parent_mode"] == 0o700
    assert database["workspace_mode"] == 0o700
    assert database["snapshots"] == ()
    assert loaded_sources[0].parent in (Path("/dev/fd"), Path("/proc/self/fd"))
    assert loaded_sources[0].name.isdigit()
    assert database_path != claimant
    assert claimant.is_symlink()
    assert claimant.resolve() == target.resolve()
    assert target.read_bytes() == target_before
    _assert_no_invocation_temps(target)


def test_receipt_is_finalized_immediately_before_database_replace(
    tmp_path: Path,
    bundle: SemanticBundle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loader = _loader()
    csv_dir, manifest = write_bundle_csv_fixture(tmp_path, bundle)
    target = tmp_path / "workshop.duckdb"
    receipt_dir = tmp_path / "receipts"
    real_link = loader.os.link
    real_replace = loader.os.replace
    events: list[tuple[str, Path]] = []

    def recording_link(
        source: Any,
        destination: Any,
        **kwargs: Any,
    ) -> None:
        events.append(("receipt", receipt_dir / Path(destination)))
        real_link(source, destination, **kwargs)

    def recording_replace(
        source: Any,
        destination: Any,
        **kwargs: Any,
    ) -> None:
        destination_path = Path(destination)
        if kwargs.get("dst_dir_fd") is not None:
            destination_path = target.parent / destination_path
        events.append(("database", destination_path))
        real_replace(source, destination, **kwargs)

    monkeypatch.setattr(loader.os, "link", recording_link)
    monkeypatch.setattr(loader.os, "replace", recording_replace)

    _receipt, receipt_path = loader.load_csvs(
        csv_dir,
        target,
        bundle,
        manifest,
        receipt_dir=receipt_dir,
    )

    assert events[-2:] == [("receipt", receipt_path), ("database", target)]
    _assert_no_invocation_temps(target, receipt_dir)


def test_receipt_finalize_failure_preserves_target_and_cleans_temps(
    tmp_path: Path,
    bundle: SemanticBundle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loader = _loader()
    csv_dir, manifest = write_bundle_csv_fixture(tmp_path, bundle)
    target = tmp_path / "workshop.duckdb"
    target.write_bytes(b"existing target bytes")
    before = target.read_bytes()
    receipt_dir = tmp_path / "receipts"

    def fail_receipt_link(
        source: Any,
        destination: Any,
        **kwargs: Any,
    ) -> None:
        raise OSError("injected receipt finalize failure")

    monkeypatch.setattr(loader.os, "link", fail_receipt_link)

    with pytest.raises(loader.LoadError, match="receipt finalize failure"):
        loader.load_csvs(
            csv_dir,
            target,
            bundle,
            manifest,
            receipt_dir=receipt_dir,
        )

    assert target.read_bytes() == before
    assert not list(receipt_dir.glob("*.json"))
    _assert_no_invocation_temps(target, receipt_dir)


def test_receipt_descriptor_stays_open_in_private_workspace_through_publication(
    tmp_path: Path,
    bundle: SemanticBundle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loader = _loader()
    csv_dir, manifest = write_bundle_csv_fixture(tmp_path, bundle)
    target = tmp_path / "workshop.duckdb"
    receipt_dir = tmp_path / "receipts"
    real_write_receipt = loader._write_receipt_temp
    real_link = loader.os.link
    reserved: dict[str, Any] = {}
    publication: dict[str, Any] = {}

    def record_reserved_descriptor(*args: Any, **kwargs: Any) -> Any:
        descriptor, name = real_write_receipt(*args, **kwargs)
        workspace = Path(args[0])
        reserved.update(
            descriptor=descriptor,
            name=name,
            path=workspace / name,
            workspace_descriptor=kwargs["workspace_descriptor"],
        )
        return descriptor, name

    def inspect_descriptor_at_publication(
        source: Any,
        destination: Any,
        **kwargs: Any,
    ) -> None:
        descriptor = reserved["descriptor"]
        descriptor_stat = loader.os.fstat(descriptor)
        source_stat = loader.os.stat(
            source,
            dir_fd=kwargs["src_dir_fd"],
            follow_symlinks=False,
        )
        workspace_stat = loader.os.fstat(kwargs["src_dir_fd"])
        publication.update(
            descriptor_is_regular=loader.stat.S_ISREG(descriptor_stat.st_mode),
            source_matches_descriptor=(
                source_stat.st_dev == descriptor_stat.st_dev
                and source_stat.st_ino == descriptor_stat.st_ino
            ),
            source=reserved["path"],
            workspace_mode=workspace_stat.st_mode & 0o777,
            workspace_is_symlink=loader.stat.S_ISLNK(workspace_stat.st_mode),
        )
        real_link(source, destination, **kwargs)

    monkeypatch.setattr(loader, "_write_receipt_temp", record_reserved_descriptor)
    monkeypatch.setattr(loader.os, "link", inspect_descriptor_at_publication)

    _receipt, receipt_path = loader.load_csvs(
        csv_dir,
        target,
        bundle,
        manifest,
        receipt_dir=receipt_dir,
    )

    assert publication == {
        "descriptor_is_regular": True,
        "source_matches_descriptor": True,
        "source": reserved["path"],
        "workspace_mode": 0o700,
        "workspace_is_symlink": False,
    }
    assert reserved["path"].parent.parent == receipt_dir
    assert receipt_path.is_file()
    with pytest.raises(OSError):
        loader.os.fstat(reserved["descriptor"])
    _assert_no_invocation_temps(target, receipt_dir)


def test_receipt_temp_path_substitution_cannot_truncate_existing_database(
    tmp_path: Path,
    bundle: SemanticBundle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loader = _loader()
    csv_dir, manifest = write_bundle_csv_fixture(tmp_path, bundle)
    target = tmp_path / "workshop.duckdb"
    connection = duckdb.connect(str(target))
    connection.execute("CREATE TABLE sentinel(value INTEGER)")
    connection.execute("INSERT INTO sentinel VALUES (7)")
    connection.close()
    target_before = target.read_bytes()
    receipt_dir = tmp_path / "receipts"
    real_open = loader.os.open
    substituted_entries: list[str] = []

    def substitute_reserved_entry(
        path: Any,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        descriptor = real_open(path, flags, mode, dir_fd=dir_fd)
        if (
            dir_fd is not None
            and Path(path).name.startswith("receipt-")
            and flags & loader.os.O_CREAT
            and flags & loader.os.O_EXCL
        ):
            loader.os.unlink(path, dir_fd=dir_fd)
            loader.os.symlink(target, path, dir_fd=dir_fd)
            substituted_entries.append(Path(path).name)
        return descriptor

    monkeypatch.setattr(loader.os, "open", substitute_reserved_entry)

    with pytest.raises(loader.LoadError):
        loader.load_csvs(
            csv_dir,
            target,
            bundle,
            manifest,
            receipt_dir=receipt_dir,
        )

    assert len(substituted_entries) == 1
    assert target.read_bytes() == target_before
    assert not target.is_symlink()
    assert not list(receipt_dir.glob("*.json"))
    _assert_no_invocation_temps(target, receipt_dir)


def test_concurrent_preexisting_receipt_is_never_overwritten_or_deleted(
    tmp_path: Path,
    bundle: SemanticBundle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loader = _loader()
    csv_dir, manifest = write_bundle_csv_fixture(tmp_path, bundle)
    target = tmp_path / "workshop.duckdb"
    target.write_bytes(b"existing target bytes")
    target_before = target.read_bytes()
    receipt_dir = tmp_path / "receipts"
    preexisting = b"concurrently published receipt bytes"
    real_link = loader.os.link

    def competing_link(
        source: Any,
        destination: Any,
        **kwargs: Any,
    ) -> None:
        (receipt_dir / Path(destination)).write_bytes(preexisting)
        real_link(source, destination, **kwargs)

    monkeypatch.setattr(loader.os, "link", competing_link)

    with pytest.raises(loader.LoadError, match="content-addressed receipt collision"):
        loader.load_csvs(
            csv_dir,
            target,
            bundle,
            manifest,
            receipt_dir=receipt_dir,
        )

    assert target.read_bytes() == target_before
    receipt_files = list(receipt_dir.glob("*.json"))
    assert len(receipt_files) == 1
    assert receipt_files[0].read_bytes() == preexisting
    _assert_no_invocation_temps(target, receipt_dir)


def test_existing_receipt_symlink_is_rejected_without_touching_its_target(
    tmp_path: Path,
    bundle: SemanticBundle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loader = _loader()
    csv_dir, manifest = write_bundle_csv_fixture(tmp_path, bundle)
    target = tmp_path / "workshop.duckdb"
    target.write_bytes(b"existing database bytes")
    target_before = target.read_bytes()
    receipt_dir = tmp_path / "receipts"
    symlink_target = tmp_path / "matching-receipt-evidence.json"
    real_link = loader.os.link
    competing: dict[str, Any] = {}

    def publish_matching_symlink(
        source: Any,
        destination: Any,
        **kwargs: Any,
    ) -> None:
        receipt_bytes = _read_directory_entry(
            loader,
            kwargs["src_dir_fd"],
            source,
        )
        symlink_target.write_bytes(receipt_bytes)
        receipt_path = receipt_dir / Path(destination)
        receipt_path.symlink_to(symlink_target)
        competing.update(path=receipt_path, bytes=receipt_bytes)
        real_link(source, destination, **kwargs)

    monkeypatch.setattr(loader.os, "link", publish_matching_symlink)

    with pytest.raises(loader.LoadError):
        loader.load_csvs(
            csv_dir,
            target,
            bundle,
            manifest,
            receipt_dir=receipt_dir,
        )

    receipt_path = competing["path"]
    assert target.read_bytes() == target_before
    assert receipt_path.is_symlink()
    assert receipt_path.readlink() == symlink_target
    assert symlink_target.read_bytes() == competing["bytes"]
    _assert_no_invocation_temps(target, receipt_dir)


def test_existing_receipt_fifo_is_rejected_without_blocking_or_removal(
    tmp_path: Path,
    bundle: SemanticBundle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loader = _loader()
    csv_dir, manifest = write_bundle_csv_fixture(tmp_path, bundle)
    target = tmp_path / "workshop.duckdb"
    target.write_bytes(b"existing database bytes")
    target_before = target.read_bytes()
    receipt_dir = tmp_path / "receipts"
    real_link = loader.os.link
    stop_writer = threading.Event()
    writer_threads: list[threading.Thread] = []
    competing: dict[str, Any] = {}

    def publish_matching_fifo(
        source: Any,
        destination: Any,
        **kwargs: Any,
    ) -> None:
        receipt_bytes = _read_directory_entry(
            loader,
            kwargs["src_dir_fd"],
            source,
        )
        receipt_path = receipt_dir / Path(destination)
        loader.os.mkfifo(receipt_path, 0o600)
        competing.update(path=receipt_path, bytes=receipt_bytes)

        def write_when_reader_opens() -> None:
            while not stop_writer.is_set():
                try:
                    descriptor = loader.os.open(
                        receipt_path,
                        loader.os.O_WRONLY | loader.os.O_NONBLOCK,
                    )
                except OSError:
                    stop_writer.wait(0.01)
                    continue
                try:
                    remaining = memoryview(receipt_bytes)
                    while remaining:
                        written = loader.os.write(descriptor, remaining)
                        remaining = remaining[written:]
                finally:
                    loader.os.close(descriptor)
                return

        writer = threading.Thread(target=write_when_reader_opens, daemon=True)
        writer_threads.append(writer)
        writer.start()
        real_link(source, destination, **kwargs)

    monkeypatch.setattr(loader.os, "link", publish_matching_fifo)
    started = time.monotonic()
    try:
        with pytest.raises(loader.LoadError):
            loader.load_csvs(
                csv_dir,
                target,
                bundle,
                manifest,
                receipt_dir=receipt_dir,
            )
    finally:
        elapsed = time.monotonic() - started
        stop_writer.set()
        for writer in writer_threads:
            writer.join(timeout=1)

    receipt_path = competing["path"]
    assert elapsed < 5
    assert all(not writer.is_alive() for writer in writer_threads)
    assert target.read_bytes() == target_before
    assert loader.stat.S_ISFIFO(receipt_path.lstat().st_mode)
    _assert_no_invocation_temps(target, receipt_dir)


def test_database_replace_failure_preserves_published_receipt_for_adopter(
    tmp_path: Path,
    bundle: SemanticBundle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loader = _loader()
    csv_dir, manifest = write_bundle_csv_fixture(tmp_path, bundle)
    target = tmp_path / "workshop.duckdb"
    target.write_bytes(b"existing target bytes")
    before = target.read_bytes()
    receipt_dir = tmp_path / "receipts"
    real_replace = loader.os.replace
    adopted: dict[str, Any] = {}

    def adopt_receipt_then_fail_database_replace(
        source: Any,
        destination: Any,
        **kwargs: Any,
    ) -> None:
        if Path(destination).name == target.name:
            published = list(receipt_dir.glob("*.json"))
            assert len(published) == 1
            adopted_bytes = published[0].read_bytes()
            adopted_receipt = loader.MaterializationReceipt.model_validate_json(
                adopted_bytes
            )
            assert canonical_json_bytes(adopted_receipt) == adopted_bytes
            adopted["path"] = published[0]
            adopted["bytes"] = adopted_bytes
            raise OSError("injected database replace failure after adoption")
        real_replace(source, destination, **kwargs)

    monkeypatch.setattr(
        loader.os,
        "replace",
        adopt_receipt_then_fail_database_replace,
    )

    with pytest.raises(
        loader.LoadError, match="database replace failure after adoption"
    ):
        loader.load_csvs(
            csv_dir,
            target,
            bundle,
            manifest,
            receipt_dir=receipt_dir,
        )

    assert target.read_bytes() == before
    assert adopted["path"].read_bytes() == adopted["bytes"]
    assert list(receipt_dir.glob("*.json")) == [adopted["path"]]
    _assert_no_invocation_temps(target, receipt_dir)


def test_database_replace_failure_never_deletes_preexisting_receipt(
    tmp_path: Path,
    bundle: SemanticBundle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loader = _loader()
    csv_dir, manifest = write_bundle_csv_fixture(tmp_path, bundle)
    target = tmp_path / "workshop.duckdb"
    receipt_dir = tmp_path / "receipts"
    _receipt, receipt_path = loader.load_csvs(
        csv_dir,
        target,
        bundle,
        manifest,
        receipt_dir=receipt_dir,
    )
    receipt_before = receipt_path.read_bytes()
    target_before = target.read_bytes()
    receipt_files_before = {
        path.name: path.read_bytes() for path in receipt_dir.glob("*.json")
    }
    real_replace = loader.os.replace

    def fail_database_replace(
        source: Any,
        destination: Any,
        **kwargs: Any,
    ) -> None:
        if Path(destination).name == target.name:
            raise OSError("injected database replace failure")
        real_replace(source, destination, **kwargs)

    monkeypatch.setattr(loader.os, "replace", fail_database_replace)

    with pytest.raises(loader.LoadError, match="database replace failure"):
        loader.load_csvs(
            csv_dir,
            target,
            bundle,
            manifest,
            receipt_dir=receipt_dir,
        )

    assert target.read_bytes() == target_before
    assert receipt_path.read_bytes() == receipt_before
    assert {
        path.name: path.read_bytes() for path in receipt_dir.glob("*.json")
    } == receipt_files_before
    _assert_no_invocation_temps(target, receipt_dir)


def test_whole_workspace_substitution_after_database_hash_is_descriptor_bound(
    tmp_path: Path,
    bundle: SemanticBundle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loader = _loader()
    csv_dir, manifest = write_bundle_csv_fixture(tmp_path, bundle)
    target = tmp_path / "workshop.duckdb"
    target.write_bytes(b"existing database bytes")
    target_before = target.read_bytes()
    receipt_dir = tmp_path / "receipts"
    moved_workspace = tmp_path / "attacker-moved-owned-workspace"
    substituted_database_bytes = b"substituted unhashed database bytes"
    unrelated_bytes = b"unrelated tree must survive cleanup"
    real_build_database = loader._build_database
    real_write_receipt_temp = loader._write_receipt_temp
    observed: dict[str, Any] = {}

    def record_built_database(
        database_path: Path,
        inventory: Any,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        result = real_build_database(database_path, inventory, *args, **kwargs)
        observed.update(
            workspace=database_path.parent,
            database_bytes=database_path.read_bytes(),
        )
        return result

    def substitute_workspace_after_hash(
        receipt_workspace: Path,
        receipt_bytes: bytes,
        *args: Any,
        **kwargs: Any,
    ) -> tuple[int, Path]:
        workspace = observed["workspace"]
        workspace.rename(moved_workspace)
        workspace.mkdir(mode=0o700)
        unrelated = workspace / "unrelated-tree"
        unrelated.mkdir()
        (unrelated / "keep.txt").write_bytes(unrelated_bytes)
        (workspace / "database.duckdb").write_bytes(substituted_database_bytes)
        observed["substituted_workspace"] = workspace
        return real_write_receipt_temp(
            receipt_workspace,
            receipt_bytes,
            *args,
            **kwargs,
        )

    monkeypatch.setattr(loader, "_build_database", record_built_database)
    monkeypatch.setattr(loader, "_write_receipt_temp", substitute_workspace_after_hash)

    try:
        receipt, _receipt_path = loader.load_csvs(
            csv_dir,
            target,
            bundle,
            manifest,
            receipt_dir=receipt_dir,
        )
    except loader.LoadError:
        assert target.read_bytes() == target_before
    else:
        retained_bytes = observed["database_bytes"]
        assert target.read_bytes() == retained_bytes
        assert receipt.database_sha256 == hashlib.sha256(retained_bytes).hexdigest()
        assert sha256_file(target) == receipt.database_sha256

    substituted_workspace = observed["substituted_workspace"]
    assert (substituted_workspace / "unrelated-tree" / "keep.txt").read_bytes() == (
        unrelated_bytes
    )
    assert (substituted_workspace / "database.duckdb").read_bytes() == (
        substituted_database_bytes
    )
    assert moved_workspace.is_dir()


def test_database_entry_substitution_after_hash_fails_without_target_or_tree_damage(
    tmp_path: Path,
    bundle: SemanticBundle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loader = _loader()
    csv_dir, manifest = write_bundle_csv_fixture(tmp_path, bundle)
    target = tmp_path / "workshop.duckdb"
    target.write_bytes(b"existing database bytes")
    target_before = target.read_bytes()
    receipt_dir = tmp_path / "receipts"
    substituted_database_bytes = b"substituted unhashed database bytes"
    unrelated_bytes = b"unrelated entry must survive cleanup"
    real_build_database = loader._build_database
    real_write_receipt_temp = loader._write_receipt_temp
    observed: dict[str, Any] = {}

    def record_built_database(
        database_path: Path,
        inventory: Any,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        result = real_build_database(database_path, inventory, *args, **kwargs)
        observed.update(
            workspace=database_path.parent,
            database_bytes=database_path.read_bytes(),
        )
        return result

    def substitute_database_entry_after_hash(
        receipt_workspace: Path,
        receipt_bytes: bytes,
        *args: Any,
        **kwargs: Any,
    ) -> tuple[int, Path]:
        workspace = observed["workspace"]
        database_path = workspace / "database.duckdb"
        retained_database = workspace / "attacker-moved-retained.duckdb"
        database_path.rename(retained_database)
        database_path.write_bytes(substituted_database_bytes)
        unrelated = workspace / "attacker-unrelated.txt"
        unrelated.write_bytes(unrelated_bytes)
        observed.update(
            retained_database=retained_database,
            substituted_database=database_path,
            unrelated=unrelated,
        )
        return real_write_receipt_temp(
            receipt_workspace,
            receipt_bytes,
            *args,
            **kwargs,
        )

    monkeypatch.setattr(loader, "_build_database", record_built_database)
    monkeypatch.setattr(
        loader,
        "_write_receipt_temp",
        substitute_database_entry_after_hash,
    )

    with pytest.raises(loader.LoadError):
        loader.load_csvs(
            csv_dir,
            target,
            bundle,
            manifest,
            receipt_dir=receipt_dir,
        )

    assert target.read_bytes() == target_before
    assert observed["retained_database"].read_bytes() == observed["database_bytes"]
    assert observed["substituted_database"].read_bytes() == (substituted_database_bytes)
    assert observed["unrelated"].read_bytes() == unrelated_bytes


def test_link_no_follow_supported_branch_hard_links_symlink_itself(
    tmp_path: Path,
) -> None:
    loader = _loader()
    assert loader._HARD_LINK in loader.os.supports_follow_symlinks
    backing = tmp_path / "backing.txt"
    backing.write_bytes(b"backing bytes")
    source = tmp_path / "source-link"
    source.symlink_to(backing.name)
    destination = tmp_path / "destination-link"
    directory_descriptor = loader.os.open(
        tmp_path,
        loader.os.O_RDONLY | getattr(loader.os, "O_DIRECTORY", 0),
    )
    try:
        loader._link_no_follow(
            source.name,
            destination.name,
            src_dir_fd=directory_descriptor,
            dst_dir_fd=directory_descriptor,
        )
    finally:
        loader.os.close(directory_descriptor)

    source_stat = source.lstat()
    destination_stat = destination.lstat()
    assert source.is_symlink()
    assert destination.is_symlink()
    assert destination.readlink() == Path(backing.name)
    assert (destination_stat.st_dev, destination_stat.st_ino) == (
        source_stat.st_dev,
        source_stat.st_ino,
    )


def test_oversized_regular_receipt_collision_uses_expected_plus_one_bounded_read(
    tmp_path: Path,
    bundle: SemanticBundle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loader = _loader()
    csv_dir, manifest = write_bundle_csv_fixture(tmp_path, bundle)
    target = tmp_path / "workshop.duckdb"
    target.write_bytes(b"existing database bytes")
    target_before = target.read_bytes()
    receipt_dir = tmp_path / "receipts"
    oversized_size = 16 * 1024 * 1024
    real_link = loader.os.link
    real_read = loader.os.read
    observed: dict[str, Any] = {"read_sizes": []}

    def publish_oversized_regular_collision(
        source: Any,
        destination: Any,
        **kwargs: Any,
    ) -> None:
        receipt_bytes = _read_directory_entry(
            loader,
            kwargs["src_dir_fd"],
            source,
        )
        receipt_path = receipt_dir / Path(destination)
        with receipt_path.open("wb") as competing:
            competing.write(receipt_bytes)
            competing.truncate(oversized_size)
        observed.update(
            path=receipt_path,
            receipt_length=len(receipt_bytes),
        )
        real_link(source, destination, **kwargs)

    def record_bounded_read(descriptor: int, size: int) -> bytes:
        observed["read_sizes"].append(size)
        return real_read(descriptor, size)

    monkeypatch.setattr(loader.os, "link", publish_oversized_regular_collision)
    monkeypatch.setattr(loader.os, "read", record_bounded_read)

    with pytest.raises(loader.LoadError, match="content-addressed receipt collision"):
        loader.load_csvs(
            csv_dir,
            target,
            bundle,
            manifest,
            receipt_dir=receipt_dir,
        )

    expected_limit = observed["receipt_length"] + 1
    assert observed["read_sizes"] == [expected_limit]
    assert observed["path"].stat().st_size == oversized_size
    assert target.read_bytes() == target_before
    _assert_no_invocation_temps(target, receipt_dir)


def test_final_publication_substitution_restores_existing_target(
    tmp_path: Path,
    bundle: SemanticBundle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loader = _loader()
    csv_dir, manifest = write_bundle_csv_fixture(tmp_path, bundle)
    target = tmp_path / "workshop.duckdb"
    target.write_bytes(b"existing target bytes that must be restored exactly")
    target_before = target.read_bytes()
    receipt_dir = tmp_path / "receipts"
    substituted_bytes = b"attacker substitution inside os.replace"
    real_replace = loader.os.replace
    observed = {"substituted": False}

    def substitute_source_inside_publication_replace(
        source: Any,
        destination: Any,
        **kwargs: Any,
    ) -> None:
        if (
            Path(source).name == "database.duckdb"
            and Path(destination).name == target.name
            and not observed["substituted"]
        ):
            source_directory = kwargs["src_dir_fd"]
            loader.os.unlink(source, dir_fd=source_directory)
            replacement = loader.os.open(
                source,
                loader.os.O_WRONLY | loader.os.O_CREAT | loader.os.O_EXCL,
                0o600,
                dir_fd=source_directory,
            )
            try:
                assert loader.os.write(replacement, substituted_bytes) == len(
                    substituted_bytes
                )
            finally:
                loader.os.close(replacement)
            observed["substituted"] = True
        real_replace(source, destination, **kwargs)

    monkeypatch.setattr(
        loader.os, "replace", substitute_source_inside_publication_replace
    )

    with pytest.raises(loader.LoadError, match="published database"):
        loader.load_csvs(
            csv_dir,
            target,
            bundle,
            manifest,
            receipt_dir=receipt_dir,
        )

    assert observed["substituted"] is True
    assert target.read_bytes() == target_before
    assert not target.is_symlink()
    _assert_no_invocation_temps(target, receipt_dir)


def test_preflight_aba_swap_cannot_mix_hash_header_and_cast_sources(
    tmp_path: Path,
    bundle: SemanticBundle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loader = _loader()
    csv_dir, manifest = write_bundle_csv_fixture(tmp_path, bundle)
    selected = next(
        table
        for table in manifest.tables
        if len(_read_rows(csv_dir / table.file_name)[0]) >= 2
    )
    source_file = csv_dir / selected.file_name
    compliant_bytes = source_file.read_bytes()
    invalid_rows = _read_rows(source_file)
    invalid_rows[0][0], invalid_rows[0][1] = (
        invalid_rows[0][1],
        invalid_rows[0][0],
    )
    _write_rows(source_file, invalid_rows)
    invalid_bytes = source_file.read_bytes()
    manifest = _rehash_manifest(manifest, csv_dir)
    real_read_shape = loader._read_csv_shape
    real_check_castability = loader._check_castability
    observed = {"swapped": False, "restored": False}

    def use_compliant_path_for_shape(path: Path) -> tuple[tuple[str, ...], int]:
        if Path(path) == source_file:
            source_file.write_bytes(compliant_bytes)
            observed["swapped"] = True
        return real_read_shape(path)

    def restore_manifest_bound_path_after_cast(table: Any) -> int:
        try:
            return real_check_castability(table)
        finally:
            if table.source_file == source_file and observed["swapped"]:
                source_file.write_bytes(invalid_bytes)
                observed["restored"] = True

    monkeypatch.setattr(loader, "_read_csv_shape", use_compliant_path_for_shape)
    monkeypatch.setattr(
        loader,
        "_check_castability",
        restore_manifest_bound_path_after_cast,
    )

    with pytest.raises(loader.LoadError, match="header mismatch"):
        loader.preflight_csvs(csv_dir, bundle, manifest)

    assert source_file.read_bytes() == invalid_bytes
    assert observed["swapped"] is observed["restored"]


def test_early_snapshot_failure_after_destination_creation_cleans_workspace(
    tmp_path: Path,
    bundle: SemanticBundle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loader = _loader()
    csv_dir, manifest = write_bundle_csv_fixture(tmp_path, bundle)
    target = tmp_path / "workshop.duckdb"
    target.write_bytes(b"existing target bytes")
    target_before = target.read_bytes()
    real_copy_snapshot = loader._copy_verified_snapshot
    real_fdopen = loader.os.fdopen
    observed = {"failed_destination_open": False}

    def fail_copy_after_destination_creation(*args: Any, **kwargs: Any) -> Path:
        def fail_destination_fdopen(
            descriptor: int,
            mode: str = "r",
            *fdopen_args: Any,
            **fdopen_kwargs: Any,
        ) -> Any:
            if mode == "wb" and not observed["failed_destination_open"]:
                observed["failed_destination_open"] = True
                raise OSError("injected failure after snapshot destination creation")
            return real_fdopen(descriptor, mode, *fdopen_args, **fdopen_kwargs)

        with monkeypatch.context() as scoped:
            scoped.setattr(loader.os, "fdopen", fail_destination_fdopen)
            return real_copy_snapshot(*args, **kwargs)

    monkeypatch.setattr(
        loader, "_copy_verified_snapshot", fail_copy_after_destination_creation
    )

    with pytest.raises(loader.LoadError, match="could not create verified snapshot"):
        loader.load_csvs(csv_dir, target, bundle, manifest)

    assert observed["failed_destination_open"] is True
    assert target.read_bytes() == target_before
    _assert_no_invocation_temps(target)


def test_private_preflight_snapshot_substitution_cannot_mix_validated_bytes(
    tmp_path: Path,
    bundle: SemanticBundle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loader = _loader()
    csv_dir, manifest = write_bundle_csv_fixture(tmp_path, bundle)
    selected = next(
        table
        for table in manifest.tables
        if len(_read_rows(csv_dir / table.file_name)[0]) >= 2
    )
    source_file = csv_dir / selected.file_name
    compliant_bytes = source_file.read_bytes()
    invalid_rows = _read_rows(source_file)
    invalid_rows[0][0], invalid_rows[0][1] = (
        invalid_rows[0][1],
        invalid_rows[0][0],
    )
    _write_rows(source_file, invalid_rows)
    invalid_bytes = source_file.read_bytes()
    manifest = _rehash_manifest(manifest, csv_dir)
    real_copy = loader._copy_preflight_snapshot
    observed = {"substituted": False}

    def substitute_private_path_after_hash(
        copy_source: Path,
        snapshot: Path,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        result = real_copy(copy_source, snapshot, *args, **kwargs)
        if Path(copy_source) == source_file and snapshot.exists():
            snapshot.unlink()
            snapshot.write_bytes(compliant_bytes)
            observed["substituted"] = True
        return result

    monkeypatch.setattr(
        loader,
        "_copy_preflight_snapshot",
        substitute_private_path_after_hash,
    )

    with pytest.raises(loader.LoadError, match="header mismatch"):
        loader.preflight_csvs(csv_dir, bundle, manifest)

    assert observed["substituted"] is False
    assert source_file.read_bytes() == invalid_bytes


def test_preflight_workspace_replacement_is_not_recursively_deleted(
    tmp_path: Path,
    bundle: SemanticBundle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loader = _loader()
    csv_dir, manifest = write_bundle_csv_fixture(tmp_path, bundle)
    real_copy = loader._copy_preflight_snapshot
    canary_bytes = b"unrelated replacement tree must survive cleanup"
    observed: dict[str, Any] = {"substituted": False}

    def substitute_workspace_after_first_copy(
        source_file: Path,
        snapshot: Path,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        result = real_copy(source_file, snapshot, *args, **kwargs)
        if not observed["substituted"]:
            workspace = snapshot.parent
            moved_workspace = workspace.with_name(f"{workspace.name}-moved-by-test")
            workspace.rename(moved_workspace)
            workspace.mkdir(mode=0o700)
            canary = workspace / "keep.txt"
            canary.write_bytes(canary_bytes)
            observed.update(
                substituted=True,
                workspace=workspace,
                moved_workspace=moved_workspace,
                canary=canary,
            )
        return result

    monkeypatch.setattr(
        loader,
        "_copy_preflight_snapshot",
        substitute_workspace_after_first_copy,
    )

    try:
        with pytest.raises(loader.LoadError):
            loader.preflight_csvs(csv_dir, bundle, manifest)

        assert observed["substituted"] is True
        assert observed["canary"].read_bytes() == canary_bytes
        assert observed["moved_workspace"].is_dir()
    finally:
        for key in ("workspace", "moved_workspace"):
            path = observed.get(key)
            if isinstance(path, Path):
                shutil.rmtree(path, ignore_errors=True)


def test_snapshot_destination_fstat_failure_cleans_workspace(
    tmp_path: Path,
    bundle: SemanticBundle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loader = _loader()
    csv_dir, manifest = write_bundle_csv_fixture(tmp_path, bundle)
    target = tmp_path / "workshop.duckdb"
    target.write_bytes(b"existing target bytes")
    target_before = target.read_bytes()
    real_copy = loader._copy_verified_snapshot
    real_open = loader.os.open
    real_fstat = loader.os.fstat
    observed: dict[str, Any] = {"failed": False}

    def fail_copy_destination_fstat(*args: Any, **kwargs: Any) -> Path:
        def record_destination_open(
            path: Any,
            flags: int,
            mode: int = 0o777,
            *,
            dir_fd: int | None = None,
        ) -> int:
            descriptor = real_open(path, flags, mode, dir_fd=dir_fd)
            if Path(path).name.startswith("source-") and dir_fd is not None:
                observed.setdefault("destination_descriptor", descriptor)
            return descriptor

        def fail_first_destination_fstat(descriptor: int) -> Any:
            if (
                descriptor == observed.get("destination_descriptor")
                and not observed["failed"]
            ):
                observed["failed"] = True
                raise OSError("injected destination fstat failure")
            return real_fstat(descriptor)

        with monkeypatch.context() as scoped:
            scoped.setattr(loader.os, "open", record_destination_open)
            scoped.setattr(loader.os, "fstat", fail_first_destination_fstat)
            return real_copy(*args, **kwargs)

    monkeypatch.setattr(loader, "_copy_verified_snapshot", fail_copy_destination_fstat)

    with pytest.raises(loader.LoadError, match="could not create verified snapshot"):
        loader.load_csvs(csv_dir, target, bundle, manifest)

    assert observed["failed"] is True
    assert target.read_bytes() == target_before
    _assert_no_invocation_temps(target)


def test_private_load_snapshot_aba_cannot_load_unverified_bytes(
    tmp_path: Path,
    bundle: SemanticBundle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loader = _loader()
    csv_dir, manifest = write_bundle_csv_fixture(tmp_path, bundle)
    target = tmp_path / "workshop.duckdb"
    selected_table = next(
        table
        for table in _active_tables(bundle)
        if any(column["data_type"] == "VARCHAR" for column in table.cerebro["columns"])
    )
    selected_name = _raw_name(selected_table)
    selected_index = (
        sorted(table.name for table in manifest.tables).index(selected_name) + 1
    )
    selected_column_index = next(
        index
        for index, column in enumerate(selected_table.cerebro["columns"])
        if column["data_type"] == "VARCHAR"
    )
    selected_column = selected_table.cerebro["columns"][selected_column_index]["name"]
    source_file = csv_dir / f"{selected_name}.csv"
    original_rows = _read_rows(source_file)
    unchecked_value = "UNCHECKED_PRIVATE_SNAPSHOT_VALUE"
    changed_rows = [row.copy() for row in original_rows]
    changed_rows[1][selected_column_index] = unchecked_value
    changed_file = tmp_path / "changed-private-snapshot.csv"
    _write_rows(changed_file, changed_rows)
    changed_bytes = changed_file.read_bytes()
    changed_file.unlink()
    real_load_table = loader._load_table
    observed = {"calls": 0, "attacked_private_path": False}

    def swap_private_path_while_loading(
        connection: duckdb.DuckDBPyConnection,
        load_source: Path,
        ddl: str,
    ) -> int:
        observed["calls"] += 1
        if observed["calls"] != selected_index or load_source.parent.name == "fd":
            return real_load_table(connection, load_source, ddl)
        verified_bytes = load_source.read_bytes()
        load_source.unlink()
        load_source.write_bytes(changed_bytes)
        observed["attacked_private_path"] = True
        try:
            return real_load_table(connection, load_source, ddl)
        finally:
            load_source.unlink()
            load_source.write_bytes(verified_bytes)

    monkeypatch.setattr(loader, "_load_table", swap_private_path_while_loading)

    receipt, _receipt_path = loader.load_csvs(csv_dir, target, bundle, manifest)

    connection = duckdb.connect(str(target), read_only=True)
    try:
        loaded_value = connection.execute(
            f"SELECT {_quote(selected_column)} FROM {_quote(selected_name)}"
        ).fetchone()[0]
    finally:
        connection.close()
    assert observed["calls"] == len(manifest.tables)
    assert observed["attacked_private_path"] is False
    assert loaded_value == original_rows[1][selected_column_index]
    assert loaded_value != unchecked_value
    assert [table.source_file_sha256 for table in receipt.tables] == [
        table.sha256 for table in manifest.tables
    ]
    _assert_no_invocation_temps(target)


def test_receipt_same_inode_rewrite_is_rejected_before_target_publication(
    tmp_path: Path,
    bundle: SemanticBundle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loader = _loader()
    csv_dir, manifest = write_bundle_csv_fixture(tmp_path, bundle)
    target = tmp_path / "workshop.duckdb"
    target.write_bytes(b"existing target bytes")
    target_before = target.read_bytes()
    receipt_dir = tmp_path / "receipts"
    malicious_bytes = b'{"leaked":"' + SOURCE_VALUE.encode() + b'"}'
    real_link = loader._link_no_follow
    observed: dict[str, Any] = {}

    def rewrite_same_inode_then_publish(
        source: str,
        destination: str,
        **kwargs: Any,
    ) -> None:
        source_directory = kwargs["src_dir_fd"]
        loader.os.chmod(source, 0o600, dir_fd=source_directory)
        write_descriptor = loader.os.open(
            source,
            loader.os.O_WRONLY | loader.os.O_TRUNC,
            dir_fd=source_directory,
        )
        try:
            assert loader.os.write(write_descriptor, malicious_bytes) == len(
                malicious_bytes
            )
        finally:
            loader.os.close(write_descriptor)
        real_link(source, destination, **kwargs)
        observed["receipt_path"] = receipt_dir / destination

    monkeypatch.setattr(loader, "_link_no_follow", rewrite_same_inode_then_publish)

    with pytest.raises(loader.LoadError, match="receipt.*content"):
        loader.load_csvs(
            csv_dir,
            target,
            bundle,
            manifest,
            receipt_dir=receipt_dir,
        )

    assert target.read_bytes() == target_before
    assert observed["receipt_path"].read_bytes() == malicious_bytes
    _assert_no_invocation_temps(target, receipt_dir)


def test_bundle_mutation_during_preflight_cannot_split_ddl_and_receipt_identity(
    tmp_path: Path,
    bundle: SemanticBundle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loader = _loader()
    provenance = import_module("cerebro.provenance")
    csv_dir, manifest = write_bundle_csv_fixture(tmp_path, bundle)
    target = tmp_path / "workshop.duckdb"
    bundle_before = bundle.model_copy(deep=True)
    expected_bundle_hash = provenance.semantic_bundle_sha256(bundle_before)
    selected_table = next(
        table
        for table in _active_tables(bundle)
        if any(column["data_type"] == "BIGINT" for column in table.cerebro["columns"])
    )
    selected_name = _raw_name(selected_table)
    selected_column = next(
        column["name"]
        for column in selected_table.cerebro["columns"]
        if column["data_type"] == "BIGINT"
    )
    real_copy = loader._copy_preflight_snapshot
    observed = {"mutated": False}

    def mutate_caller_bundle_after_snapshot(*args: Any, **kwargs: Any) -> Any:
        result = real_copy(*args, **kwargs)
        if not observed["mutated"]:
            live_table = next(
                table
                for table in _active_tables(bundle)
                if table.id == selected_table.id
            )
            live_column = next(
                column
                for column in live_table.cerebro["columns"]
                if column["name"] == selected_column
            )
            live_column["data_type"] = "DOUBLE"
            observed["mutated"] = True
        return result

    monkeypatch.setattr(
        loader,
        "_copy_preflight_snapshot",
        mutate_caller_bundle_after_snapshot,
    )

    receipt, _receipt_path = loader.load_csvs(csv_dir, target, bundle, manifest)

    connection = duckdb.connect(str(target), read_only=True)
    try:
        actual_type = connection.execute(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_schema = 'main' AND table_name = ? AND column_name = ?",
            [selected_name, selected_column],
        ).fetchone()[0]
    finally:
        connection.close()
    assert observed["mutated"] is True
    assert actual_type == "BIGINT"
    assert receipt.bundle_sha256 == expected_bundle_hash
    assert receipt.bundle_sha256 != provenance.semantic_bundle_sha256(bundle)
    _assert_no_invocation_temps(target)


def test_database_destination_first_fstat_failure_cleans_workspace(
    tmp_path: Path,
    bundle: SemanticBundle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loader = _loader()
    csv_dir, manifest = write_bundle_csv_fixture(tmp_path, bundle)
    target = tmp_path / "workshop.duckdb"
    target.write_bytes(b"existing target bytes")
    target_before = target.read_bytes()
    real_build = loader._build_database
    real_open = loader.os.open
    real_fstat = loader.os.fstat
    observed: dict[str, Any] = {"failed": False}

    def fail_database_fstat(*args: Any, **kwargs: Any) -> Any:
        def record_database_open(
            path: Any,
            flags: int,
            mode: int = 0o777,
            *,
            dir_fd: int | None = None,
        ) -> int:
            descriptor = real_open(path, flags, mode, dir_fd=dir_fd)
            if Path(path).name == "database.duckdb" and dir_fd is not None:
                observed.setdefault("database_descriptor", descriptor)
            return descriptor

        def fail_first_database_fstat(descriptor: int) -> Any:
            if (
                descriptor == observed.get("database_descriptor")
                and not observed["failed"]
            ):
                observed["failed"] = True
                raise OSError("injected database fstat failure")
            return real_fstat(descriptor)

        with monkeypatch.context() as scoped:
            scoped.setattr(loader.os, "open", record_database_open)
            scoped.setattr(loader.os, "fstat", fail_first_database_fstat)
            return real_build(*args, **kwargs)

    monkeypatch.setattr(loader, "_build_database", fail_database_fstat)

    with pytest.raises(loader.LoadError):
        loader.load_csvs(csv_dir, target, bundle, manifest)

    assert observed["failed"] is True
    assert target.read_bytes() == target_before
    _assert_no_invocation_temps(target)


def test_receipt_destination_first_fstat_failure_cleans_workspace(
    tmp_path: Path,
    bundle: SemanticBundle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loader = _loader()
    csv_dir, manifest = write_bundle_csv_fixture(tmp_path, bundle)
    target = tmp_path / "workshop.duckdb"
    target.write_bytes(b"existing target bytes")
    target_before = target.read_bytes()
    receipt_dir = tmp_path / "receipts"
    real_write_receipt = loader._write_receipt_temp
    real_open = loader.os.open
    real_fstat = loader.os.fstat
    observed: dict[str, Any] = {"failed": False}

    def fail_receipt_fstat(*args: Any, **kwargs: Any) -> Any:
        def record_receipt_temp(
            path: Any,
            flags: int,
            mode: int = 0o777,
            *,
            dir_fd: int | None = None,
        ) -> int:
            descriptor = real_open(path, flags, mode, dir_fd=dir_fd)
            if (
                dir_fd is not None
                and Path(path).name.startswith("receipt-")
                and flags & loader.os.O_CREAT
            ):
                observed.setdefault("receipt_descriptor", descriptor)
            return descriptor

        def fail_first_receipt_fstat(descriptor: int) -> Any:
            if (
                descriptor == observed.get("receipt_descriptor")
                and not observed["failed"]
            ):
                observed["failed"] = True
                raise OSError("injected receipt fstat failure")
            return real_fstat(descriptor)

        with monkeypatch.context() as scoped:
            scoped.setattr(loader.os, "open", record_receipt_temp)
            scoped.setattr(loader.os, "fstat", fail_first_receipt_fstat)
            return real_write_receipt(*args, **kwargs)

    monkeypatch.setattr(loader, "_write_receipt_temp", fail_receipt_fstat)

    with pytest.raises(loader.LoadError):
        loader.load_csvs(
            csv_dir,
            target,
            bundle,
            manifest,
            receipt_dir=receipt_dir,
        )

    assert observed["failed"] is True
    assert target.read_bytes() == target_before
    _assert_no_invocation_temps(target, receipt_dir)


def test_post_publication_close_failure_does_not_report_failure_or_interrupt_cleanup(
    tmp_path: Path,
    bundle: SemanticBundle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loader = _loader()
    csv_dir, manifest = write_bundle_csv_fixture(tmp_path, bundle)
    target = tmp_path / "workshop.duckdb"
    target.write_bytes(b"existing target bytes")
    receipt_dir = tmp_path / "receipts"
    real_write_receipt = loader._write_receipt_temp
    real_discard_backup = loader._discard_target_backup_best_effort
    real_close = loader.os.close
    real_fstat = loader.os.fstat
    observed: dict[str, Any] = {
        "armed": False,
        "failed": False,
        "later_close_calls": 0,
    }

    def capture_receipt_descriptor(*args: Any, **kwargs: Any) -> Any:
        descriptor, receipt_temp = real_write_receipt(*args, **kwargs)
        observed["receipt_descriptor"] = descriptor
        return descriptor, receipt_temp

    def arm_close_fault(*args: Any, **kwargs: Any) -> None:
        real_discard_backup(*args, **kwargs)
        observed["armed"] = True

    def fail_receipt_close_once(descriptor: int) -> None:
        if (
            observed["armed"]
            and descriptor == observed.get("receipt_descriptor")
            and not observed["failed"]
        ):
            observed["failed"] = True
            raise OSError("injected post-publication close failure")
        if observed["failed"]:
            observed["later_close_calls"] += 1
        real_close(descriptor)

    monkeypatch.setattr(loader, "_write_receipt_temp", capture_receipt_descriptor)
    monkeypatch.setattr(
        loader,
        "_discard_target_backup_best_effort",
        arm_close_fault,
    )
    monkeypatch.setattr(loader.os, "close", fail_receipt_close_once)

    try:
        receipt, receipt_path = loader.load_csvs(
            csv_dir,
            target,
            bundle,
            manifest,
            receipt_dir=receipt_dir,
        )
    finally:
        descriptor = observed.get("receipt_descriptor")
        if isinstance(descriptor, int):
            try:
                real_fstat(descriptor)
            except OSError:
                pass
            else:
                real_close(descriptor)

    assert observed["failed"] is True
    assert observed["later_close_calls"] > 0
    assert sha256_file(target) == receipt.database_sha256
    receipt_bytes = receipt_path.read_bytes()
    assert (
        canonical_json_bytes(
            loader.MaterializationReceipt.model_validate_json(receipt_bytes)
        )
        == receipt_bytes
    )
    _assert_no_invocation_temps(target, receipt_dir)


def test_csv_set_drift_after_initial_check_fails_before_publication(
    tmp_path: Path,
    bundle: SemanticBundle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loader = _loader()
    csv_dir, manifest = write_bundle_csv_fixture(tmp_path, bundle)
    target = tmp_path / "workshop.duckdb"
    target.write_bytes(b"existing target bytes")
    target_before = target.read_bytes()
    receipt_dir = tmp_path / "receipts"
    unexpected = csv_dir / "unexpected.csv"
    real_copy = loader._copy_preflight_snapshot
    real_connect = loader.duckdb.connect
    opened_databases: list[Path] = []
    observed = {"added": False}

    def add_csv_after_initial_check(*args: Any, **kwargs: Any) -> Any:
        result = real_copy(*args, **kwargs)
        if not observed["added"]:
            unexpected.write_text("value\n1\n", encoding="utf-8")
            observed["added"] = True
        return result

    def track_file_database(
        database: str = ":memory:",
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        if str(database) != ":memory:":
            opened_databases.append(Path(database))
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr(loader, "_copy_preflight_snapshot", add_csv_after_initial_check)
    monkeypatch.setattr(loader.duckdb, "connect", track_file_database)

    with pytest.raises(loader.LoadError, match="CSV file set"):
        loader.load_csvs(
            csv_dir,
            target,
            bundle,
            manifest,
            receipt_dir=receipt_dir,
        )

    assert observed["added"] is True
    assert unexpected.is_file()
    assert opened_databases == []
    assert target.read_bytes() == target_before
    _assert_no_invocation_temps(target, receipt_dir)


def test_receipt_workspace_substitution_creates_no_temp_in_replacement_tree(
    tmp_path: Path,
    bundle: SemanticBundle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loader = _loader()
    csv_dir, manifest = write_bundle_csv_fixture(tmp_path, bundle)
    target = tmp_path / "workshop.duckdb"
    target.write_bytes(b"existing target bytes")
    target_before = target.read_bytes()
    receipt_dir = tmp_path / "receipts"
    canary_bytes = b"unrelated replacement tree must survive"
    real_write_receipt = loader._write_receipt_temp
    observed: dict[str, Any] = {}

    def substitute_receipt_workspace(
        receipt_workspace: Path,
        receipt_bytes: bytes,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        moved_workspace = receipt_workspace.with_name(
            f"{receipt_workspace.name}-retained-by-test"
        )
        receipt_workspace.rename(moved_workspace)
        receipt_workspace.mkdir(mode=0o700)
        canary = receipt_workspace / "keep.txt"
        canary.write_bytes(canary_bytes)
        observed.update(
            replacement_workspace=receipt_workspace,
            moved_workspace=moved_workspace,
            canary=canary,
        )
        return real_write_receipt(
            receipt_workspace,
            receipt_bytes,
            *args,
            **kwargs,
        )

    def fail_receipt_publication(*args: Any, **kwargs: Any) -> None:
        raise OSError("injected receipt publication failure")

    monkeypatch.setattr(loader, "_write_receipt_temp", substitute_receipt_workspace)
    monkeypatch.setattr(loader, "_link_no_follow", fail_receipt_publication)

    try:
        with pytest.raises(loader.LoadError, match="receipt publication failure"):
            loader.load_csvs(
                csv_dir,
                target,
                bundle,
                manifest,
                receipt_dir=receipt_dir,
            )

        replacement_workspace = observed["replacement_workspace"]
        moved_workspace = observed["moved_workspace"]
        assert observed["canary"].read_bytes() == canary_bytes
        assert not list(replacement_workspace.glob("receipt-*.tmp"))
        assert not list(moved_workspace.glob("receipt-*.tmp"))
        assert target.read_bytes() == target_before
        assert not list(receipt_dir.glob("*.json"))
    finally:
        for key in ("replacement_workspace", "moved_workspace"):
            workspace = observed.get(key)
            if isinstance(workspace, Path):
                shutil.rmtree(workspace, ignore_errors=True)


def test_concurrent_old_target_writer_cannot_corrupt_rollback_snapshot(
    tmp_path: Path,
    bundle: SemanticBundle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loader = _loader()
    csv_dir, manifest = write_bundle_csv_fixture(tmp_path, bundle)
    target = tmp_path / "workshop.duckdb"
    target.write_bytes(b"original target bytes that rollback must preserve")
    target_before = target.read_bytes()
    retained_writer = loader.os.open(target, loader.os.O_RDWR)
    real_replace = loader.os.replace
    observed = {"mutated": False}

    def mutate_old_inode_after_database_replace(
        source: Any,
        destination: Any,
        **kwargs: Any,
    ) -> None:
        if Path(destination).name == target.name and not observed["mutated"]:
            real_replace(source, destination, **kwargs)
            loader.os.ftruncate(retained_writer, 0)
            loader.os.lseek(retained_writer, 0, loader.os.SEEK_SET)
            changed = b"concurrent writer changed retained old inode"
            assert loader.os.write(retained_writer, changed) == len(changed)
            loader.os.fsync(retained_writer)
            observed["mutated"] = True
            raise OSError("injected failure after old target inode mutation")
        real_replace(source, destination, **kwargs)

    monkeypatch.setattr(
        loader.os,
        "replace",
        mutate_old_inode_after_database_replace,
    )

    try:
        with pytest.raises(loader.LoadError):
            loader.load_csvs(csv_dir, target, bundle, manifest)
    finally:
        loader.os.close(retained_writer)

    assert observed["mutated"] is True
    assert target.read_bytes() == target_before
    _assert_no_invocation_temps(target)


def test_receipt_rewrite_at_success_boundary_rolls_back_target(
    tmp_path: Path,
    bundle: SemanticBundle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loader = _loader()
    csv_dir, manifest = write_bundle_csv_fixture(tmp_path, bundle)
    target = tmp_path / "workshop.duckdb"
    target.write_bytes(b"existing target bytes")
    target_before = target.read_bytes()
    receipt_dir = tmp_path / "receipts"
    malicious_bytes = b'{"corrupted":"after-final-validation"}'
    real_discard = loader._discard_target_backup_best_effort
    observed: dict[str, Any] = {"mutated": False}

    def discard_then_rewrite_receipt(*args: Any, **kwargs: Any) -> None:
        real_discard(*args, **kwargs)
        receipts = list(receipt_dir.glob("*.json"))
        assert len(receipts) == 1
        receipt_path = receipts[0]
        receipt_path.chmod(0o600)
        receipt_path.write_bytes(malicious_bytes)
        observed.update(mutated=True, receipt_path=receipt_path)

    monkeypatch.setattr(
        loader,
        "_discard_target_backup_best_effort",
        discard_then_rewrite_receipt,
    )

    with pytest.raises(loader.LoadError, match="receipt.*content"):
        loader.load_csvs(
            csv_dir,
            target,
            bundle,
            manifest,
            receipt_dir=receipt_dir,
        )

    assert observed["mutated"] is True
    assert target.read_bytes() == target_before
    assert observed["receipt_path"].read_bytes() == malicious_bytes
    _assert_no_invocation_temps(target, receipt_dir)


def test_database_workspace_substitution_during_connect_leaves_no_database_temp(
    tmp_path: Path,
    bundle: SemanticBundle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loader = _loader()
    csv_dir, manifest = write_bundle_csv_fixture(tmp_path, bundle)
    target = tmp_path / "workshop.duckdb"
    target.write_bytes(b"existing target bytes")
    target_before = target.read_bytes()
    receipt_dir = tmp_path / "receipts"
    real_build = loader._build_database
    real_connect = loader.duckdb.connect
    canary_bytes = b"replacement workspace canary"
    observed: dict[str, Any] = {"substituted": False}

    def capture_workspace(
        database_path: Path,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        observed["workspace"] = database_path.parent
        return real_build(database_path, *args, **kwargs)

    def substitute_workspace_at_connect(
        database: str = ":memory:",
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        if str(database) != ":memory:" and not observed["substituted"]:
            workspace = observed["workspace"]
            moved_workspace = workspace.with_name(f"{workspace.name}-retained-by-test")
            workspace.rename(moved_workspace)
            workspace.mkdir(mode=0o700)
            canary = workspace / "keep.txt"
            canary.write_bytes(canary_bytes)
            observed.update(
                substituted=True,
                replacement_workspace=workspace,
                moved_workspace=moved_workspace,
                canary=canary,
            )
        return real_connect(database, *args, **kwargs)

    def fail_receipt_publication(*args: Any, **kwargs: Any) -> None:
        raise OSError("injected receipt publication failure")

    monkeypatch.setattr(loader, "_build_database", capture_workspace)
    monkeypatch.setattr(loader.duckdb, "connect", substitute_workspace_at_connect)
    monkeypatch.setattr(loader, "_link_no_follow", fail_receipt_publication)

    try:
        with pytest.raises(loader.LoadError):
            loader.load_csvs(
                csv_dir,
                target,
                bundle,
                manifest,
                receipt_dir=receipt_dir,
            )

        replacement_workspace = observed["replacement_workspace"]
        moved_workspace = observed["moved_workspace"]
        assert observed["canary"].read_bytes() == canary_bytes
        assert not list(replacement_workspace.glob("database.duckdb*"))
        assert not list(moved_workspace.glob("database.duckdb*"))
        assert target.read_bytes() == target_before
    finally:
        for key in ("replacement_workspace", "moved_workspace"):
            workspace = observed.get(key)
            if isinstance(workspace, Path):
                shutil.rmtree(workspace, ignore_errors=True)


def test_csv_set_drift_during_database_replace_rolls_back_target(
    tmp_path: Path,
    bundle: SemanticBundle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loader = _loader()
    csv_dir, manifest = write_bundle_csv_fixture(tmp_path, bundle)
    target = tmp_path / "workshop.duckdb"
    target.write_bytes(b"existing target bytes")
    target_before = target.read_bytes()
    receipt_dir = tmp_path / "receipts"
    unexpected = csv_dir / "unexpected.csv"
    real_replace = loader.os.replace
    observed = {"added": False}

    def add_csv_inside_database_replace(
        source: Any,
        destination: Any,
        **kwargs: Any,
    ) -> None:
        if Path(destination).name == target.name and not observed["added"]:
            unexpected.write_text("value\n1\n", encoding="utf-8")
            observed["added"] = True
        real_replace(source, destination, **kwargs)

    monkeypatch.setattr(loader.os, "replace", add_csv_inside_database_replace)

    with pytest.raises(loader.LoadError, match="CSV file set"):
        loader.load_csvs(
            csv_dir,
            target,
            bundle,
            manifest,
            receipt_dir=receipt_dir,
        )

    assert observed["added"] is True
    assert unexpected.is_file()
    assert target.read_bytes() == target_before
    _assert_no_invocation_temps(target, receipt_dir)


def test_close_best_effort_does_not_retry_reused_same_inode_descriptor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loader = _loader()
    retained = tmp_path / "retained.txt"
    retained.write_bytes(b"retained bytes")
    victim = loader.os.open(retained, loader.os.O_RDONLY)
    replacement_source = loader.os.open(retained, loader.os.O_RDONLY)
    real_close = loader.os.close
    real_fstat = loader.os.fstat
    observed = {"victim_close_calls": 0, "reused_number": False}

    def release_reuse_then_report_error(descriptor: int) -> None:
        if descriptor == victim:
            observed["victim_close_calls"] += 1
            if observed["victim_close_calls"] == 1:
                real_close(descriptor)
                loader.os.dup2(replacement_source, victim, inheritable=False)
                observed["reused_number"] = True
                raise OSError("close reported failure after releasing descriptor")
        real_close(descriptor)

    monkeypatch.setattr(loader.os, "close", release_reuse_then_report_error)

    try:
        loader._close_best_effort(victim)
        assert observed == {"victim_close_calls": 1, "reused_number": True}
        assert loader.stat.S_ISREG(real_fstat(victim).st_mode)
    finally:
        for descriptor in (victim, replacement_source):
            try:
                real_close(descriptor)
            except OSError:
                pass


def test_foreign_duckdb_decoy_lock_cannot_authorize_different_connection(
    tmp_path: Path,
    bundle: SemanticBundle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loader = _loader()
    csv_dir, manifest = write_bundle_csv_fixture(tmp_path, bundle)
    target = tmp_path / "workshop.duckdb"
    target.write_bytes(b"previous target bytes")
    target_before = target.read_bytes()
    receipt_dir = tmp_path / "receipts"
    query_database = tmp_path / "different-query-database.duckdb"
    ready_path = tmp_path / "foreign-decoy-ready"
    real_connect = loader.duckdb.connect
    observed: dict[str, Any] = {"connected": False}

    lock_holder_script = """
import os
import sys
from pathlib import Path

import duckdb

connection = duckdb.connect(sys.argv[1])
Path(sys.argv[2]).write_text(str(os.getpid()), encoding="ascii")
try:
    sys.stdin.buffer.read(1)
finally:
    connection.close()
"""

    def terminate_decoy_process() -> None:
        process = observed.get("process")
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=5)
        except loader.subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

    def release_decoy_process() -> None:
        process = observed["process"]
        if process.poll() is None:
            assert process.stdin is not None
            try:
                process.stdin.write(b"release\n")
                process.stdin.flush()
            except BrokenPipeError:
                pass
            finally:
                process.stdin.close()
                process.stdin = None
            try:
                process.wait(timeout=5)
            except loader.subprocess.TimeoutExpired as exc:
                terminate_decoy_process()
                raise AssertionError("foreign DuckDB decoy did not exit") from exc
        stderr = b"" if process.stderr is None else process.stderr.read()
        assert process.returncode == 0, stderr.decode(errors="replace")

    class ReleaseDecoyWhenClosed:
        def __init__(self, connection: Any) -> None:
            self._connection = connection

        def __getattr__(self, name: str) -> Any:
            return getattr(self._connection, name)

        def close(self) -> None:
            try:
                self._connection.close()
            finally:
                release_decoy_process()

    def connect_to_b_while_foreign_process_locks_a(
        database: str = ":memory:",
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        if str(database) == ":memory:" or observed["connected"]:
            return real_connect(database, *args, **kwargs)

        database_path = Path(database)
        seed_connection = real_connect(str(database_path))
        seed_connection.close()
        process = loader.subprocess.Popen(
            [
                loader.sys.executable,
                "-I",
                "-c",
                lock_holder_script,
                str(database_path),
                str(ready_path),
            ],
            stdin=loader.subprocess.PIPE,
            stdout=loader.subprocess.DEVNULL,
            stderr=loader.subprocess.PIPE,
        )
        observed["process"] = process
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not ready_path.exists():
            if process.poll() is not None:
                break
            time.sleep(0.01)
        if not ready_path.exists():
            terminate_decoy_process()
            stderr = b"" if process.stderr is None else process.stderr.read()
            raise AssertionError(
                "foreign DuckDB decoy did not acquire its lock: "
                + stderr.decode(errors="replace")
            )

        decoy_pid = int(ready_path.read_text(encoding="ascii"))
        assert decoy_pid == process.pid
        assert decoy_pid != loader.os.getpid()
        connection_b = real_connect(str(query_database), *args, **kwargs)
        observed.update(connected=True, decoy_pid=decoy_pid)
        return ReleaseDecoyWhenClosed(connection_b)

    monkeypatch.setattr(
        loader.duckdb,
        "connect",
        connect_to_b_while_foreign_process_locks_a,
    )

    try:
        with pytest.raises(loader.LoadError, match="connection.*database"):
            loader.load_csvs(
                csv_dir,
                target,
                bundle,
                manifest,
                receipt_dir=receipt_dir,
            )

        assert observed["connected"] is True
        assert observed["decoy_pid"] != loader.os.getpid()
        assert observed["process"].poll() == 0
        assert target.read_bytes() == target_before
        assert not receipt_dir.exists() or not list(receipt_dir.glob("*.json"))
        _assert_no_invocation_temps(target, receipt_dir)
    finally:
        terminate_decoy_process()


@pytest.mark.parametrize(
    "probe_failure",
    ("spawn-oserror", "timeout", "unsupported-result"),
)
def test_first_database_owner_probe_failure_cleans_private_workspace(
    tmp_path: Path,
    bundle: SemanticBundle,
    monkeypatch: pytest.MonkeyPatch,
    probe_failure: str,
) -> None:
    loader = _loader()
    csv_dir, manifest = write_bundle_csv_fixture(tmp_path, bundle)
    target = tmp_path / "workshop.duckdb"
    target.write_bytes(b"previous target bytes")
    target_before = target.read_bytes()
    receipt_dir = tmp_path / "receipts"
    observed = {"calls": 0}

    def fail_first_owner_probe(*args: Any, **kwargs: Any) -> Any:
        observed["calls"] += 1
        assert observed["calls"] == 1
        if probe_failure == "spawn-oserror":
            raise OSError("PRIVATE_PROBE_SPAWN_CANARY_RESTART_9")
        if probe_failure == "timeout":
            raise loader.subprocess.TimeoutExpired(
                cmd=args[0],
                timeout=kwargs["timeout"],
                output=b"PRIVATE_PROBE_TIMEOUT_CANARY_RESTART_9",
            )
        return loader.subprocess.CompletedProcess(
            args=args[0],
            returncode=91,
            stdout=b"PRIVATE_PROBE_AMBIGUOUS_CANARY_RESTART_9",
        )

    monkeypatch.setattr(loader.subprocess, "run", fail_first_owner_probe)

    with pytest.raises(loader.LoadError, match="lock probe|connection.*database"):
        loader.load_csvs(
            csv_dir,
            target,
            bundle,
            manifest,
            receipt_dir=receipt_dir,
        )

    assert observed["calls"] == 1
    assert target.read_bytes() == target_before
    assert not receipt_dir.exists() or not list(receipt_dir.glob("*.json"))
    _assert_no_invocation_temps(target, receipt_dir)


def test_target_replacement_after_backup_discard_restores_old_target_and_preserves_unknown(
    tmp_path: Path,
    bundle: SemanticBundle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loader = _loader()
    csv_dir, manifest = write_bundle_csv_fixture(tmp_path, bundle)
    target = tmp_path / "workshop.duckdb"
    target.write_bytes(b"previous target bytes")
    target_before = target.read_bytes()
    receipt_dir = tmp_path / "receipts"
    attacker_bytes = b"ATTACKER_POST_DISCARD_TARGET_CANARY_RESTART_9"
    attacker_candidate = tmp_path / "attacker-post-discard.duckdb"
    real_discard = loader._discard_target_backup_best_effort
    observed: dict[str, Any] = {"replaced": False}

    def discard_then_replace_public_target(*args: Any, **kwargs: Any) -> None:
        real_discard(*args, **kwargs)
        attacker_candidate.write_bytes(attacker_bytes)
        loader.os.replace(attacker_candidate, target)
        attacker_stat = target.stat()
        observed.update(
            replaced=True,
            attacker_identity=(attacker_stat.st_dev, attacker_stat.st_ino),
        )

    monkeypatch.setattr(
        loader,
        "_discard_target_backup_best_effort",
        discard_then_replace_public_target,
    )

    with pytest.raises(loader.LoadError):
        loader.load_csvs(
            csv_dir,
            target,
            bundle,
            manifest,
            receipt_dir=receipt_dir,
        )

    assert observed["replaced"] is True
    assert target.read_bytes() == target_before
    preserved_attacker_paths = []
    for candidate in tmp_path.rglob("*"):
        if not candidate.is_file():
            continue
        try:
            if candidate.read_bytes() == attacker_bytes:
                preserved_attacker_paths.append(candidate)
        except OSError:
            continue
    assert preserved_attacker_paths
    _assert_no_invocation_temps(target, receipt_dir)


def test_target_replacement_after_backup_discard_without_prior_target_is_left_untouched(
    tmp_path: Path,
    bundle: SemanticBundle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loader = _loader()
    csv_dir, manifest = write_bundle_csv_fixture(tmp_path, bundle)
    target = tmp_path / "workshop.duckdb"
    receipt_dir = tmp_path / "receipts"
    attacker_bytes = b"ATTACKER_NO_PRIOR_TARGET_CANARY_RESTART_9"
    attacker_candidate = tmp_path / "attacker-no-prior-target.duckdb"
    real_discard = loader._discard_target_backup_best_effort
    observed: dict[str, Any] = {"replaced": False}

    def discard_then_replace_public_target(*args: Any, **kwargs: Any) -> None:
        real_discard(*args, **kwargs)
        attacker_candidate.write_bytes(attacker_bytes)
        loader.os.replace(attacker_candidate, target)
        attacker_stat = target.stat()
        observed.update(
            replaced=True,
            attacker_identity=(attacker_stat.st_dev, attacker_stat.st_ino),
        )

    monkeypatch.setattr(
        loader,
        "_discard_target_backup_best_effort",
        discard_then_replace_public_target,
    )

    with pytest.raises(loader.LoadError):
        loader.load_csvs(
            csv_dir,
            target,
            bundle,
            manifest,
            receipt_dir=receipt_dir,
        )

    assert observed["replaced"] is True
    attacker_stat = target.stat()
    assert (attacker_stat.st_dev, attacker_stat.st_ino) == observed["attacker_identity"]
    assert target.read_bytes() == attacker_bytes
    _assert_no_invocation_temps(target, receipt_dir)


def _assert_sanitized_load_error(
    error: BaseException,
    *,
    forbidden: tuple[str, ...],
) -> None:
    message = str(error)
    formatted = "".join(import_module("traceback").format_exception(error))
    for value in forbidden:
        assert value not in message
        assert value not in formatted
    assert error.__cause__ is None
    assert error.__context__ is None


def test_duckdb_lock_probe_observes_connection_close_transition(
    tmp_path: Path,
) -> None:
    loader = _loader()
    database = tmp_path / "lock-transition.duckdb"
    connection: duckdb.DuckDBPyConnection | None = duckdb.connect(str(database))
    descriptor = loader.os.open(database, loader.os.O_RDWR)
    try:
        assert loader._probe_database_lock(descriptor) == "blocked"
        connection.close()
        connection = None
        assert loader._probe_database_lock(descriptor) == "acquired"
    finally:
        if connection is not None:
            connection.close()
        loader.os.close(descriptor)


def test_database_entry_swap_after_connect_fails_without_publication_or_unknown_cleanup(
    tmp_path: Path,
    bundle: SemanticBundle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loader = _loader()
    csv_dir, manifest = write_bundle_csv_fixture(tmp_path, bundle)
    target = tmp_path / "workshop.duckdb"
    target.write_bytes(b"previous target bytes")
    target_before = target.read_bytes()
    receipt_dir = tmp_path / "receipts"
    attacker_bytes = b"ATTACKER_DATABASE_ENTRY_CANARY_RESTART_8"
    real_connect = loader.duckdb.connect
    observed: dict[str, Any] = {"swapped": False}

    def swap_entry_after_real_connect(
        database: str = ":memory:",
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        connection = real_connect(database, *args, **kwargs)
        if str(database) != ":memory:" and not observed["swapped"]:
            database_path = Path(database)
            database_path.unlink()
            database_path.write_bytes(attacker_bytes)
            observed.update(
                swapped=True,
                database_path=database_path,
                workspace=database_path.parent.parent,
            )
        return connection

    monkeypatch.setattr(loader.duckdb, "connect", swap_entry_after_real_connect)

    try:
        with pytest.raises(loader.LoadError, match="connection.*database"):
            loader.load_csvs(
                csv_dir,
                target,
                bundle,
                manifest,
                receipt_dir=receipt_dir,
            )

        assert observed["swapped"] is True
        assert observed["database_path"].read_bytes() == attacker_bytes
        assert target.read_bytes() == target_before
        assert not receipt_dir.exists() or not list(receipt_dir.glob("*.json"))
    finally:
        workspace = observed.get("workspace")
        if isinstance(workspace, Path):
            shutil.rmtree(workspace, ignore_errors=True)


def test_database_connect_transient_aba_fails_closed(
    tmp_path: Path,
    bundle: SemanticBundle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loader = _loader()
    csv_dir, manifest = write_bundle_csv_fixture(tmp_path, bundle)
    target = tmp_path / "workshop.duckdb"
    target.write_bytes(b"previous target bytes")
    target_before = target.read_bytes()
    receipt_dir = tmp_path / "receipts"
    real_connect = loader.duckdb.connect
    observed: dict[str, Any] = {"swapped": False}

    def connect_to_b_then_restore_a(
        database: str = ":memory:",
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        if str(database) == ":memory:" or observed["swapped"]:
            return real_connect(database, *args, **kwargs)

        database_path = Path(database)
        connection_a = real_connect(database, *args, **kwargs)
        connection_a.close()
        a_bytes = database_path.read_bytes()
        retained_a = database_path.with_name("retained-a.duckdb")
        database_path.rename(retained_a)

        database_b = database_path.with_name("different-b.duckdb")
        connection_b = real_connect(str(database_b), *args, **kwargs)
        database_b.unlink()
        retained_a.rename(database_path)
        observed.update(
            swapped=True,
            database_path=database_path,
            a_bytes=a_bytes,
            workspace=database_path.parent.parent,
        )
        return connection_b

    monkeypatch.setattr(loader.duckdb, "connect", connect_to_b_then_restore_a)

    try:
        with pytest.raises(loader.LoadError, match="connection.*database"):
            loader.load_csvs(
                csv_dir,
                target,
                bundle,
                manifest,
                receipt_dir=receipt_dir,
            )

        assert observed["swapped"] is True
        assert observed["database_path"].read_bytes() == observed["a_bytes"]
        assert target.read_bytes() == target_before
        assert not receipt_dir.exists() or not list(receipt_dir.glob("*.json"))
    finally:
        workspace = observed.get("workspace")
        if isinstance(workspace, Path):
            shutil.rmtree(workspace, ignore_errors=True)


def test_target_parent_substitution_rolls_back_retained_parent_without_touching_replacement(
    tmp_path: Path,
    bundle: SemanticBundle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loader = _loader()
    csv_dir, manifest = write_bundle_csv_fixture(tmp_path, bundle)
    public = tmp_path / "public"
    public.mkdir()
    target = public / "workshop.duckdb"
    target.write_bytes(b"original retained-parent target bytes")
    target_before = target.read_bytes()
    moved_public = tmp_path / "moved-public"
    receipt_dir = tmp_path / "receipts"
    attacker_target = b"ATTACKER_REQUESTED_TARGET_CANARY_RESTART_8"
    attacker_tree = b"ATTACKER_REPLACEMENT_TREE_CANARY_RESTART_8"
    real_replace = loader.os.replace
    observed = {"substituted": False}

    def substitute_target_parent_inside_database_replace(
        source: Any,
        destination: Any,
        **kwargs: Any,
    ) -> None:
        if (
            Path(source).name == "database.duckdb"
            and Path(destination).name == target.name
            and not observed["substituted"]
        ):
            public.rename(moved_public)
            public.mkdir()
            target.write_bytes(attacker_target)
            (public / "keep.txt").write_bytes(attacker_tree)
            observed["substituted"] = True
        real_replace(source, destination, **kwargs)

    monkeypatch.setattr(
        loader.os,
        "replace",
        substitute_target_parent_inside_database_replace,
    )

    with pytest.raises(loader.LoadError, match="target directory"):
        loader.load_csvs(
            csv_dir,
            target,
            bundle,
            manifest,
            receipt_dir=receipt_dir,
        )

    assert observed["substituted"] is True
    assert target.read_bytes() == attacker_target
    assert (public / "keep.txt").read_bytes() == attacker_tree
    assert (moved_public / target.name).read_bytes() == target_before
    assert not list(public.glob(f".{target.name}.*.tmp*"))
    assert not list(moved_public.glob(f".{target.name}.*.tmp*"))


def test_castability_load_error_sanitizes_source_value_paths_and_expression(
    tmp_path: Path,
    bundle: SemanticBundle,
) -> None:
    loader = _loader()
    source_root = tmp_path / "PRIVATE_SOURCE_PATH_CANARY_RESTART_8"
    source_root.mkdir()
    csv_dir, manifest = write_bundle_csv_fixture(source_root, bundle)
    table = next(
        table
        for table in _active_tables(bundle)
        if any(column["data_type"] == "BIGINT" for column in table.cerebro["columns"])
    )
    column_index = next(
        index
        for index, column in enumerate(table.cerebro["columns"])
        if column["data_type"] == "BIGINT"
    )
    source_file = csv_dir / f"{_raw_name(table)}.csv"
    rows = _read_rows(source_file)
    source_cell = "PRIVATE_SOURCE_CELL_CANARY_RESTART_8"
    rows[1][column_index] = source_cell
    _write_rows(source_file, rows)
    manifest = _rehash_manifest(manifest, csv_dir)

    with pytest.raises(loader.LoadError, match="cast") as raised:
        loader.preflight_csvs(csv_dir, bundle, manifest)

    _assert_sanitized_load_error(
        raised.value,
        forbidden=(
            source_cell,
            str(source_root),
            str(csv_dir),
            str(source_file),
            "/dev/fd",
            "/proc/self/fd",
            "CAST(",
            "Could not convert string",
        ),
    )


def test_same_width_header_error_sanitizes_header_cells_and_paths(
    tmp_path: Path,
    bundle: SemanticBundle,
) -> None:
    loader = _loader()
    source_root = tmp_path / "PRIVATE_HEADER_PATH_CANARY_RESTART_8"
    source_root.mkdir()
    csv_dir, manifest = write_bundle_csv_fixture(source_root, bundle)
    table = manifest.tables[0]
    source_file = csv_dir / table.file_name
    rows = _read_rows(source_file)
    expected_header = tuple(rows[0])
    malicious_header = "PRIVATE_HEADER_CELL_CANARY_RESTART_8"
    rows[0][0] = malicious_header
    observed_header = tuple(rows[0])
    _write_rows(source_file, rows)
    manifest = _rehash_manifest(manifest, csv_dir)

    with pytest.raises(loader.LoadError, match="header") as raised:
        loader.preflight_csvs(csv_dir, bundle, manifest)

    _assert_sanitized_load_error(
        raised.value,
        forbidden=(
            malicious_header,
            *expected_header,
            repr(expected_header),
            repr(observed_header),
            str(source_root),
            str(csv_dir),
            str(source_file),
        ),
    )


def test_materialization_duckdb_error_is_sanitized(
    tmp_path: Path,
    bundle: SemanticBundle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loader = _loader()
    csv_dir, manifest = write_bundle_csv_fixture(tmp_path, bundle)
    target = tmp_path / "PRIVATE_TARGET_PATH_CANARY_RESTART_8.duckdb"
    target.write_bytes(b"existing target bytes")
    target_before = target.read_bytes()
    receipt_dir = tmp_path / "receipts"
    source_path = csv_dir / manifest.tables[0].file_name
    temp_path = tmp_path / ".PRIVATE_TEMP_PATH_CANARY_RESTART_8/database.duckdb"
    raw_exception = (
        "PRIVATE_DUCKDB_EXCEPTION_CANARY_RESTART_8 "
        f"{source_path} {temp_path} {target} /dev/fd/987 "
        'CAST("private_column" AS BIGINT)'
    )
    real_preflight = loader.preflight_csvs
    observed = {"armed": False}

    def preflight_then_arm_failure(*args: Any, **kwargs: Any) -> Any:
        inventory = real_preflight(*args, **kwargs)

        def fail_materialization_execute(*_args: Any, **_kwargs: Any) -> int:
            raise duckdb.Error(raw_exception)

        monkeypatch.setattr(loader, "_execute_ddl", fail_materialization_execute)
        observed["armed"] = True
        return inventory

    monkeypatch.setattr(loader, "preflight_csvs", preflight_then_arm_failure)

    with pytest.raises(loader.LoadError, match="failed loading") as raised:
        loader.load_csvs(
            csv_dir,
            target,
            bundle,
            manifest,
            receipt_dir=receipt_dir,
        )

    assert observed["armed"] is True
    assert target.read_bytes() == target_before
    _assert_sanitized_load_error(
        raised.value,
        forbidden=(
            raw_exception,
            "PRIVATE_DUCKDB_EXCEPTION_CANARY_RESTART_8",
            str(source_path),
            str(temp_path),
            str(target),
            "/dev/fd",
            "CAST(",
        ),
    )


def test_generic_materialization_wrapper_error_is_sanitized(
    tmp_path: Path,
    bundle: SemanticBundle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loader = _loader()
    csv_dir, manifest = write_bundle_csv_fixture(tmp_path, bundle)
    target = tmp_path / "PRIVATE_GENERIC_TARGET_CANARY_RESTART_8.duckdb"
    target.write_bytes(b"existing target bytes")
    target_before = target.read_bytes()
    receipt_dir = tmp_path / "receipts"
    source_path = csv_dir / manifest.tables[0].file_name
    temp_path = tmp_path / ".PRIVATE_GENERIC_TEMP_CANARY_RESTART_8/database.duckdb"
    raw_exception = (
        "PRIVATE_GENERIC_EXCEPTION_CANARY_RESTART_8 "
        f"{source_path} {temp_path} {target} /dev/fd/654 "
        'CAST("private_column" AS BIGINT)'
    )

    def fail_with_generic_exception(*args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        raise RuntimeError(raw_exception)

    monkeypatch.setattr(loader, "_snapshot_inventory", fail_with_generic_exception)

    with pytest.raises(
        loader.LoadError, match="DuckDB materialization failed"
    ) as raised:
        loader.load_csvs(
            csv_dir,
            target,
            bundle,
            manifest,
            receipt_dir=receipt_dir,
        )

    assert target.read_bytes() == target_before
    _assert_sanitized_load_error(
        raised.value,
        forbidden=(
            raw_exception,
            "PRIVATE_GENERIC_EXCEPTION_CANARY_RESTART_8",
            str(source_path),
            str(temp_path),
            str(target),
            "/dev/fd",
            "CAST(",
        ),
    )
