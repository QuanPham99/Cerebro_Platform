from __future__ import annotations

import argparse
import os
from collections.abc import Mapping, Sequence
from importlib import metadata
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError
from yaml import YAMLError

from cerebro.bundle import load_validated_bundle
from cerebro.models import (
    MaterializationReceipt,
    PreflightBlocker,
    PreflightBlockerCode,
    PreflightGate,
    PreflightReport,
    ProviderCapabilityReceipt,
    SourceManifest,
)
from cerebro.provenance import (
    manifest_table_id,
    semantic_bundle_sha256,
    sha256_file,
    source_manifest_sha256,
)

ModelT = TypeVar("ModelT", bound=BaseModel)


def _is_file(path: Path | None) -> bool:
    if path is None:
        return False
    try:
        return path.is_file()
    except OSError:
        return False


def _is_directory(path: Path | None) -> bool:
    if path is None:
        return False
    try:
        return path.is_dir()
    except OSError:
        return False


def _configured_value(environ: Mapping[str, str], *names: str) -> str:
    """Return the first configured value among equivalent variable names.

    The canonical `CEREBRO_*` names win; the `CEREBRO_LLM_*` spellings are
    accepted because that is how the organizer environment is published.
    """
    for name in names:
        value = environ.get(name, "").strip()
        if value:
            return value
    return ""


def _read_model(path: Path, model_type: type[ModelT]) -> ModelT | None:
    try:
        return model_type.model_validate_json(path.read_bytes())
    except (OSError, ValidationError):
        return None


def check_preflight(
    *,
    csv_dir: Path | None,
    manifest_path: Path | None,
    bundle_path: Path | None,
    environ: Mapping[str, str],
    database_path: Path | None,
    materialization_receipt_path: Path | None,
    provider_capability_receipt_path: Path | None,
) -> PreflightReport:
    """Evaluate offline, data, and organizer readiness without returning values."""
    blockers: list[PreflightBlocker] = []

    def block(code: PreflightBlockerCode, gate: PreflightGate) -> None:
        blocker = PreflightBlocker(code=code, gate=gate)
        if blocker not in blockers:
            blockers.append(blocker)

    manifest: SourceManifest | None = None
    if not _is_file(manifest_path):
        block("missing_data_manifest", "data")
    else:
        manifest = _read_model(manifest_path, SourceManifest)
        if manifest is None:
            block("invalid_data_manifest", "data")

    bundle_exists = _is_directory(bundle_path)
    bundle_hash: str | None = None
    if not bundle_exists:
        block("missing_bundle", "data")
    elif bundle_path is not None:
        try:
            bundle_hash = semantic_bundle_sha256(load_validated_bundle(bundle_path))
        except (
            AttributeError,
            OSError,
            TypeError,
            UnicodeError,
            ValueError,
            YAMLError,
        ):
            # Bundle parsing and validation errors are intentionally reduced to
            # a static blocker so governed content and paths never escape.
            block("bundle_hash_mismatch", "data")

    csv_directory_exists = _is_directory(csv_dir)
    if not csv_directory_exists:
        block("missing_csv_directory", "data")

    materialization_receipt: MaterializationReceipt | None = None
    if not _is_file(materialization_receipt_path):
        block("missing_materialization_receipt", "data")
    else:
        materialization_receipt = _read_model(
            materialization_receipt_path, MaterializationReceipt
        )
        if materialization_receipt is None:
            block("invalid_materialization_receipt", "data")

    if manifest is not None and csv_directory_exists and csv_dir is not None:
        expected_files = {table.file_name for table in manifest.tables}
        try:
            actual_files = {
                entry.name
                for entry in csv_dir.iterdir()
                if entry.is_file() and entry.suffix.lower() == ".csv"
            }
        except OSError:
            actual_files = set()
        if actual_files != expected_files:
            block("source_file_set_mismatch", "data")

        for table in manifest.tables:
            source_path = csv_dir / table.file_name
            if not _is_file(source_path):
                block("source_file_set_mismatch", "data")
                break
            try:
                source_hash = sha256_file(source_path)
            except OSError:
                block("source_file_hash_mismatch", "data")
                break
            if source_hash != table.sha256:
                block("source_file_hash_mismatch", "data")
                break

    if manifest is not None and materialization_receipt is not None:
        if (
            source_manifest_sha256(manifest)
            != materialization_receipt.source_manifest_sha256
        ):
            block("manifest_hash_mismatch", "data")

        manifest_tables = {
            (manifest_table_id(table.name), table.sha256, table.row_count)
            for table in manifest.tables
        }
        receipt_tables = {
            (table.table_id, table.source_file_sha256, table.row_count)
            for table in materialization_receipt.tables
        }
        if manifest_tables != receipt_tables:
            block("materialization_table_mismatch", "data")

    if (
        bundle_hash is not None
        and materialization_receipt is not None
        and bundle_hash != materialization_receipt.bundle_sha256
    ):
        block("bundle_hash_mismatch", "data")

    if materialization_receipt is not None:
        if not _is_file(database_path):
            block("missing_database", "data")
        elif database_path is not None:
            try:
                database_hash = sha256_file(database_path)
            except OSError:
                database_hash = None
            if database_hash != materialization_receipt.database_sha256:
                block("database_hash_mismatch", "data")

        try:
            duckdb_version = metadata.version("duckdb")
        except metadata.PackageNotFoundError:
            duckdb_version = None
        if duckdb_version != materialization_receipt.engine_version:
            block("engine_version_mismatch", "data")

    api_key = _configured_value(environ, "CEREBRO_API_KEY", "CEREBRO_LLM_API_KEY")
    current_model = _configured_value(environ, "CEREBRO_MODEL", "CEREBRO_LLM_MODEL")
    if not api_key.strip():
        block("missing_api_key", "organizer")
    if not current_model.strip():
        block("missing_model", "organizer")

    provider_receipt: ProviderCapabilityReceipt | None = None
    if not _is_file(provider_capability_receipt_path):
        block("missing_provider_capability", "organizer")
    else:
        provider_receipt = _read_model(
            provider_capability_receipt_path, ProviderCapabilityReceipt
        )
        if provider_receipt is None:
            block("invalid_provider_capability", "organizer")

    if provider_receipt is not None:
        if current_model.strip() and provider_receipt.model != current_model:
            block("provider_model_mismatch", "organizer")

        expected_provider_fields = (
            (
                "CEREBRO_PROVIDER",
                provider_receipt.provider,
                "provider_identity_mismatch",
            ),
            (
                "CEREBRO_MODEL_REVISION",
                provider_receipt.revision,
                "provider_revision_mismatch",
            ),
            (
                "CEREBRO_SCHEMA_MECHANISM",
                provider_receipt.schema_mechanism,
                "provider_schema_mechanism_mismatch",
            ),
        )
        for environment_name, receipt_value, mismatch_code in expected_provider_fields:
            configured_value = environ.get(environment_name, "")
            if configured_value.strip() and configured_value != receipt_value:
                block(mismatch_code, "organizer")

    data_ready = not any(blocker.gate == "data" for blocker in blockers)
    organizer_ready = not any(blocker.gate == "organizer" for blocker in blockers)
    return PreflightReport(
        offline_ready=True,
        data_prerequisites_ready=data_ready,
        organizer_prerequisites_ready=organizer_ready,
        live_prerequisites_ready=data_ready and organizer_ready,
        blockers=tuple(blockers),
    )


def probe_and_write_provider_capability(
    *,
    environ: Mapping[str, str],
    receipt_dir: Path,
) -> Path:
    """Perform the one metadata-only organizer schema call and record a receipt.

    This is an explicit opt-in action. `check_preflight` itself stays offline
    and never contacts a provider, so ordinary readiness reporting cannot make
    a network call as a side effect.
    """
    from cerebro.hosted_provider import OrganizerModelGateway, probe_provider_schema

    gateway = OrganizerModelGateway.from_environment(environ)
    _, path = probe_provider_schema(gateway=gateway, receipt_dir=receipt_dir)
    if path is None:  # pragma: no cover - receipt_dir is always provided here
        raise RuntimeError("capability probe did not write a receipt")
    return path


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Report Text-to-SQL offline and external prerequisite readiness."
    )
    parser.add_argument("--csv-dir")
    parser.add_argument("--manifest")
    parser.add_argument("--bundle")
    parser.add_argument("--database")
    parser.add_argument("--materialization-receipt")
    parser.add_argument("--provider-capability-receipt")
    parser.add_argument(
        "--probe-provider-capability",
        metavar="RECEIPT_DIR",
        help=(
            "perform one metadata-only organizer schema call and write a "
            "content-addressed capability receipt into RECEIPT_DIR"
        ),
    )
    return parser


def _configured_path(
    argument_value: str | None, environ: Mapping[str, str], environment_name: str
) -> Path | None:
    raw_value = argument_value or environ.get(environment_name, "")
    return Path(raw_value) if raw_value else None


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> int:
    runtime_environment = os.environ if environ is None else environ
    arguments = _argument_parser().parse_args(list(argv) if argv is not None else None)
    if arguments.probe_provider_capability:
        receipt_path = probe_and_write_provider_capability(
            environ=runtime_environment,
            receipt_dir=Path(arguments.probe_provider_capability),
        )
        print(f"provider_capability_receipt={receipt_path}")
    report = check_preflight(
        csv_dir=_configured_path(
            arguments.csv_dir, runtime_environment, "CEREBRO_CSV_DIR"
        ),
        manifest_path=_configured_path(
            arguments.manifest, runtime_environment, "CEREBRO_SOURCE_MANIFEST"
        ),
        bundle_path=_configured_path(
            arguments.bundle, runtime_environment, "CEREBRO_BUNDLE"
        ),
        environ=runtime_environment,
        database_path=_configured_path(
            arguments.database, runtime_environment, "CEREBRO_DATABASE_PATH"
        ),
        materialization_receipt_path=_configured_path(
            arguments.materialization_receipt,
            runtime_environment,
            "CEREBRO_MATERIALIZATION_RECEIPT",
        ),
        provider_capability_receipt_path=_configured_path(
            arguments.provider_capability_receipt,
            runtime_environment,
            "CEREBRO_PROVIDER_CAPABILITY_RECEIPT",
        ),
    )

    def flag(value: bool) -> str:
        return str(value).lower()

    print(f"offline_ready={flag(report.offline_ready)}")
    print(f"data_prerequisites_ready={flag(report.data_prerequisites_ready)}")
    print(f"organizer_prerequisites_ready={flag(report.organizer_prerequisites_ready)}")
    print(f"live_prerequisites_ready={flag(report.live_prerequisites_ready)}")
    for blocker in report.blockers:
        print(f"blocker={blocker.code}")
    return 0 if report.offline_ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
