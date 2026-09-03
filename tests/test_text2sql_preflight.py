import hashlib
import json
from importlib import import_module, metadata, util
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

ROOT = Path(__file__).resolve().parents[1]
SHA256_A = "a" * 64


def _contracts():
    models = import_module("cerebro.models")
    names = (
        "MaterializationReceipt",
        "MaterializedTableReceipt",
        "PreflightBlocker",
        "PreflightReport",
        "ProviderCapabilityReceipt",
        "SourceManifest",
        "SourceTableManifest",
    )
    missing = [name for name in names if not hasattr(models, name)]
    assert not missing, f"missing Task 0 evidence contracts: {missing}"
    return tuple(getattr(models, name) for name in names)


def _provenance():
    assert util.find_spec("cerebro.provenance") is not None, "missing Task 0 provenance module"
    module = import_module("cerebro.provenance")
    names = (
        "canonical_json_bytes",
        "manifest_table_id",
        "sha256_file",
        "source_manifest_sha256",
    )
    missing = [name for name in names if not hasattr(module, name)]
    assert not missing, f"missing Task 0 provenance helpers: {missing}"
    return tuple(getattr(module, name) for name in names)


def _preflight():
    assert util.find_spec("scripts.text2sql_preflight") is not None, "missing Task 0 preflight module"
    module = import_module("scripts.text2sql_preflight")
    assert hasattr(module, "check_preflight"), "missing check_preflight"
    assert hasattr(module, "main"), "missing preflight CLI entry point"
    return module


def _write_matching_evidence(tmp_path: Path):
    (
        MaterializationReceipt,
        MaterializedTableReceipt,
        _PreflightBlocker,
        _PreflightReport,
        ProviderCapabilityReceipt,
        SourceManifest,
        SourceTableManifest,
    ) = _contracts()
    _canonical_json_bytes, manifest_table_id, sha256_file, source_manifest_sha256 = _provenance()

    csv_dir = tmp_path / "archive"
    csv_dir.mkdir()
    csv_path = csv_dir / "accounts.csv"
    csv_path.write_bytes(b"account_id\n1\n")

    bundle_path = tmp_path / "bundle.yaml"
    bundle_path.write_text("name: caf\u00e9-bank\nversion: 1\n", encoding="utf-8")
    database_path = tmp_path / "workshop.duckdb"
    database_path.write_bytes(b"deterministic database evidence")

    manifest = SourceManifest(
        tables=(
            SourceTableManifest(
                name="accounts",
                file_name="accounts.csv",
                sha256=sha256_file(csv_path),
                row_count=1,
            ),
        )
    )
    manifest_path = tmp_path / "source-manifest.json"
    manifest_path.write_text(manifest.model_dump_json(), encoding="utf-8")

    materialization_receipt = MaterializationReceipt(
        source_manifest_sha256=source_manifest_sha256(manifest),
        bundle_sha256=sha256_file(bundle_path),
        tables=(
            MaterializedTableReceipt(
                table_id=manifest_table_id("accounts"),
                source_file_sha256=sha256_file(csv_path),
                row_count=1,
            ),
        ),
        database_sha256=sha256_file(database_path),
        engine="duckdb",
        engine_version=metadata.version("duckdb"),
    )
    materialization_receipt_path = tmp_path / "materialization-receipt.json"
    materialization_receipt_path.write_text(
        materialization_receipt.model_dump_json(), encoding="utf-8"
    )

    provider_receipt = ProviderCapabilityReceipt(
        provider="organizer",
        model="organizer-model",
        revision="2026-08-27",
        schema_mechanism="json_schema",
    )
    provider_receipt_path = tmp_path / "provider-capability-receipt.json"
    provider_receipt_path.write_text(provider_receipt.model_dump_json(), encoding="utf-8")

    return {
        "bundle_path": bundle_path,
        "csv_dir": csv_dir,
        "database_path": database_path,
        "manifest": manifest,
        "manifest_path": manifest_path,
        "materialization_receipt_path": materialization_receipt_path,
        "provider_receipt_path": provider_receipt_path,
    }


def test_text2sql_dependency_versions_are_exact():
    assert metadata.version("duckdb") == "1.5.5"
    assert metadata.version("sqlglot") == "30.17.0"


def test_text2sql_dependency_declarations_are_exact():
    project_text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    dependency_block = project_text.split("dependencies = [", 1)[1].split("]", 1)[0]
    dependencies = {
        line.strip().removesuffix(",").strip('"')
        for line in dependency_block.splitlines()
        if line.strip()
    }

    assert {item for item in dependencies if item.startswith("duckdb")} == {
        "duckdb==1.5.5"
    }
    assert {item for item in dependencies if item.startswith("sqlglot")} == {
        "sqlglot==30.17.0"
    }
    assert {item for item in dependencies if item.startswith("mcp")} == {"mcp>=1.0,<2"}


def test_evidence_contracts_are_strict_frozen_and_value_free():
    (
        MaterializationReceipt,
        MaterializedTableReceipt,
        PreflightBlocker,
        PreflightReport,
        ProviderCapabilityReceipt,
        SourceManifest,
        SourceTableManifest,
    ) = _contracts()
    _canonical_json_bytes, manifest_table_id, _sha256_file, _source_manifest_sha256 = _provenance()

    table = SourceTableManifest(
        name="accounts", file_name="accounts.csv", sha256=SHA256_A, row_count=2
    )
    manifest = SourceManifest(tables=(table,))
    receipt_table = MaterializedTableReceipt(
        table_id=manifest_table_id(table.name), source_file_sha256=SHA256_A, row_count=2
    )
    materialization = MaterializationReceipt(
        source_manifest_sha256=SHA256_A,
        bundle_sha256=SHA256_A,
        tables=(receipt_table,),
        database_sha256=SHA256_A,
        engine="duckdb",
        engine_version="1.5.5",
    )
    capability = ProviderCapabilityReceipt(
        provider="organizer",
        model="model-id",
        revision="revision-id",
        schema_mechanism="json_schema",
    )
    report = PreflightReport(
        offline_ready=True,
        data_prerequisites_ready=True,
        organizer_prerequisites_ready=False,
        live_prerequisites_ready=False,
        blockers=(PreflightBlocker(code="missing_api_key", gate="organizer"),),
    )

    assert manifest.tables[0].name == "accounts"
    assert materialization.tables[0].table_id == "table.accounts"
    for model in (table, manifest, receipt_table, materialization, capability, report):
        assert model.model_config["extra"] == "forbid"
        assert model.model_config["frozen"] is True

    with pytest.raises(ValidationError):
        table.name = "customers"
    with pytest.raises(ValidationError):
        ProviderCapabilityReceipt.model_validate(
            {
                **capability.model_dump(),
                "api_key": "credential-canary",
                "response_body": "response-canary",
            }
        )
    with pytest.raises(ValidationError):
        SourceTableManifest(
            name="table.accounts", file_name="accounts.csv", sha256=SHA256_A, row_count=2
        )
    with pytest.raises(ValidationError):
        MaterializedTableReceipt(
            table_id="accounts", source_file_sha256=SHA256_A, row_count=2
        )

    capability_schema = json.dumps(capability.model_json_schema(), sort_keys=True).lower()
    for forbidden in ("api_key", "credential", "prompt", "response_body"):
        assert forbidden not in capability_schema


def test_canonical_evidence_helpers_are_deterministic_utf8_and_hash_bound(tmp_path):
    canonical_json_bytes, _manifest_table_id, sha256_file, source_manifest_sha256 = _provenance()
    (
        _MaterializationReceipt,
        _MaterializedTableReceipt,
        _PreflightBlocker,
        _PreflightReport,
        _ProviderCapabilityReceipt,
        SourceManifest,
        SourceTableManifest,
    ) = _contracts()

    class CanonicalSample(BaseModel):
        label: str
        metadata: dict[str, int]
        excluded: str

    first = CanonicalSample(
        label="caf\u00e9", metadata={"z": 2, "a": 1}, excluded="not-evidence"
    )
    second = CanonicalSample(
        label="caf\u00e9", metadata={"a": 1, "z": 2}, excluded="different"
    )
    expected = '{"label":"caf\u00e9","metadata":{"a":1,"z":2}}'.encode()
    assert canonical_json_bytes(first, exclude={"excluded"}) == expected
    assert canonical_json_bytes(second, exclude={"excluded"}) == expected

    evidence_file = tmp_path / "evidence.bin"
    evidence_file.write_bytes(b"evidence\x00bytes")
    assert sha256_file(evidence_file) == hashlib.sha256(b"evidence\x00bytes").hexdigest()

    manifest = SourceManifest(
        tables=(
            SourceTableManifest(
                name="accounts",
                file_name="accounts.csv",
                sha256=SHA256_A,
                row_count=2,
            ),
        )
    )
    assert source_manifest_sha256(manifest) == hashlib.sha256(
        canonical_json_bytes(manifest)
    ).hexdigest()


@pytest.mark.parametrize(
    "raw_name",
    (
        "table.accounts",
        "Accounts",
        " accounts",
        "accounts ",
        "account-details",
        "account.details",
        "account__details",
        "_accounts",
        "accounts_",
        "",
    ),
)
def test_manifest_table_id_rejects_non_raw_lowercase_snake_case(raw_name):
    _canonical_json_bytes, manifest_table_id, _sha256_file, _source_manifest_sha256 = _provenance()
    with pytest.raises(ValueError, match="raw lowercase snake-case"):
        manifest_table_id(raw_name)


def test_manifest_table_id_adds_the_authority_prefix_once():
    _canonical_json_bytes, manifest_table_id, _sha256_file, _source_manifest_sha256 = _provenance()
    assert manifest_table_id("card_transactions") == "table.card_transactions"
    assert manifest_table_id("table2") == "table.table2"


def test_missing_live_inputs_block_live_but_not_offline(tmp_path):
    check_preflight = _preflight().check_preflight
    report = check_preflight(
        csv_dir=tmp_path / "archive",
        manifest_path=None,
        bundle_path=None,
        environ={},
        database_path=None,
        materialization_receipt_path=None,
        provider_capability_receipt_path=None,
    )
    assert report.offline_ready is True
    assert report.data_prerequisites_ready is False
    assert report.organizer_prerequisites_ready is False
    assert report.live_prerequisites_ready is False
    assert {item.code for item in report.blockers} == {
        "missing_api_key",
        "missing_model",
        "missing_provider_capability",
        "missing_data_manifest",
        "missing_bundle",
        "missing_csv_directory",
        "missing_materialization_receipt",
    }


def test_matching_live_evidence_is_ready(tmp_path):
    check_preflight = _preflight().check_preflight
    evidence = _write_matching_evidence(tmp_path)

    report = check_preflight(
        csv_dir=evidence["csv_dir"],
        manifest_path=evidence["manifest_path"],
        bundle_path=evidence["bundle_path"],
        environ={
            "CEREBRO_API_KEY": "credential-canary",
            "CEREBRO_MODEL": "organizer-model",
            "CEREBRO_PROVIDER": "organizer",
            "CEREBRO_MODEL_REVISION": "2026-08-27",
            "CEREBRO_SCHEMA_MECHANISM": "json_schema",
        },
        database_path=evidence["database_path"],
        materialization_receipt_path=evidence["materialization_receipt_path"],
        provider_capability_receipt_path=evidence["provider_receipt_path"],
    )

    assert report.offline_ready is True
    assert report.data_prerequisites_ready is True
    assert report.organizer_prerequisites_ready is True
    assert report.live_prerequisites_ready is True
    assert report.blockers == ()


@pytest.mark.parametrize(
    ("evidence_name", "expected_code"),
    (
        ("bundle_path", "bundle_hash_mismatch"),
        ("database_path", "database_hash_mismatch"),
    ),
)
def test_preflight_rejects_changed_evidence_hashes(tmp_path, evidence_name, expected_code):
    check_preflight = _preflight().check_preflight
    evidence = _write_matching_evidence(tmp_path)
    evidence[evidence_name].write_bytes(b"changed after receipt")

    report = check_preflight(
        csv_dir=evidence["csv_dir"],
        manifest_path=evidence["manifest_path"],
        bundle_path=evidence["bundle_path"],
        environ={"CEREBRO_API_KEY": "key", "CEREBRO_MODEL": "organizer-model"},
        database_path=evidence["database_path"],
        materialization_receipt_path=evidence["materialization_receipt_path"],
        provider_capability_receipt_path=evidence["provider_receipt_path"],
    )

    assert expected_code in {item.code for item in report.blockers}
    assert report.live_prerequisites_ready is False


def test_preflight_rejects_changed_source_file_hash(tmp_path):
    check_preflight = _preflight().check_preflight
    evidence = _write_matching_evidence(tmp_path)
    (evidence["csv_dir"] / "accounts.csv").write_bytes(b"account_id\nprivate-change\n")

    report = check_preflight(
        csv_dir=evidence["csv_dir"],
        manifest_path=evidence["manifest_path"],
        bundle_path=evidence["bundle_path"],
        environ={"CEREBRO_API_KEY": "key", "CEREBRO_MODEL": "organizer-model"},
        database_path=evidence["database_path"],
        materialization_receipt_path=evidence["materialization_receipt_path"],
        provider_capability_receipt_path=evidence["provider_receipt_path"],
    )

    assert "source_file_hash_mismatch" in {item.code for item in report.blockers}
    assert report.data_prerequisites_ready is False


def test_preflight_rejects_receipts_bound_to_other_manifest_or_model(tmp_path):
    check_preflight = _preflight().check_preflight
    (
        MaterializationReceipt,
        _MaterializedTableReceipt,
        _PreflightBlocker,
        _PreflightReport,
        ProviderCapabilityReceipt,
        _SourceManifest,
        _SourceTableManifest,
    ) = _contracts()
    evidence = _write_matching_evidence(tmp_path)

    receipt = MaterializationReceipt.model_validate_json(
        evidence["materialization_receipt_path"].read_bytes()
    )
    evidence["materialization_receipt_path"].write_text(
        receipt.model_copy(update={"source_manifest_sha256": "b" * 64}).model_dump_json(),
        encoding="utf-8",
    )
    provider_receipt = ProviderCapabilityReceipt.model_validate_json(
        evidence["provider_receipt_path"].read_bytes()
    )
    evidence["provider_receipt_path"].write_text(
        provider_receipt.model_copy(update={"model": "different-model"}).model_dump_json(),
        encoding="utf-8",
    )

    report = check_preflight(
        csv_dir=evidence["csv_dir"],
        manifest_path=evidence["manifest_path"],
        bundle_path=evidence["bundle_path"],
        environ={"CEREBRO_API_KEY": "key", "CEREBRO_MODEL": "organizer-model"},
        database_path=evidence["database_path"],
        materialization_receipt_path=evidence["materialization_receipt_path"],
        provider_capability_receipt_path=evidence["provider_receipt_path"],
    )
    codes = {item.code for item in report.blockers}
    assert "manifest_hash_mismatch" in codes
    assert "provider_model_mismatch" in codes


def test_preflight_report_and_cli_never_expose_values_or_paths(tmp_path, capsys):
    module = _preflight()
    private_dir = tmp_path / "private-path-canary"
    private_dir.mkdir()
    invalid_provider_receipt = private_dir / "provider.json"
    invalid_provider_receipt.write_text(
        '{"response_body":"raw-response-canary"}', encoding="utf-8"
    )
    secret = "credential-canary"

    report = module.check_preflight(
        csv_dir=private_dir / "archive",
        manifest_path=private_dir / "manifest.json",
        bundle_path=private_dir / "bundle.yaml",
        environ={"CEREBRO_API_KEY": secret, "CEREBRO_MODEL": "private-model-canary"},
        database_path=private_dir / "private.duckdb",
        materialization_receipt_path=private_dir / "materialization.json",
        provider_capability_receipt_path=invalid_provider_receipt,
    )
    serialized = report.model_dump_json()

    exit_code = module.main(
        [
            "--csv-dir",
            str(private_dir / "archive"),
            "--manifest",
            str(private_dir / "manifest.json"),
            "--bundle",
            str(private_dir / "bundle.yaml"),
            "--database",
            str(private_dir / "private.duckdb"),
            "--materialization-receipt",
            str(private_dir / "materialization.json"),
            "--provider-capability-receipt",
            str(invalid_provider_receipt),
        ],
        environ={"CEREBRO_API_KEY": secret, "CEREBRO_MODEL": "private-model-canary"},
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "offline_ready=true" in output
    assert "live_prerequisites_ready=false" in output
    for canary in (
        secret,
        "private-path-canary",
        "private-model-canary",
        "raw-response-canary",
        str(tmp_path),
    ):
        assert canary not in serialized
        assert canary not in output
