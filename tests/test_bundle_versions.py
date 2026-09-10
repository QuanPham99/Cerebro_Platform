from __future__ import annotations

import json
import shutil
import asyncio
from pathlib import Path

import httpx
import pytest
import yaml

from cerebro.bundle_versions import (
    BundleDefaultLocked,
    BundleVersionInvalid,
    BundleVersionNotFound,
    BundleVersionRegistry,
)
from cerebro.generation import review_bundle
from cerebro import generation
from cerebro.api import create_app
from cerebro.paths import DEFAULT_BUNDLE


def approved_copy(tmp_path: Path, reviewed_root: Path, run_id: str, *, definition: bool = False) -> Path:
    candidate = tmp_path / run_id
    shutil.copytree(DEFAULT_BUNDLE, candidate)
    manifest_path = candidate / "bundle.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest.update({
        "version": f"0.2.0+{'rev' if definition else 'generated'}.{run_id}",
        "generation_mode": "authored" if definition else "live",
        "review_state": "candidate",
        "run_id": run_id,
    })
    if definition:
        manifest["parent_version"] = "0.2.0"
    manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    record = review_bundle(
        candidate,
        reviewer="Data Owner",
        decision="approve",
        comment="Approved for the version library.",
        acknowledge_ai_risk=True,
        reviewed_root=reviewed_root,
    )
    assert record.reviewed_bundle
    return Path(record.reviewed_bundle)


def registry(tmp_path: Path, reviewed_root: Path, *, allowed: bool = True) -> BundleVersionRegistry:
    return BundleVersionRegistry(
        golden_root=DEFAULT_BUNDLE,
        reviewed_root=reviewed_root,
        active_pointer=tmp_path / "active.json",
        default_change_allowed=allowed,
    )


def test_catalog_lists_golden_generation_and_definition_versions(tmp_path: Path):
    reviewed = tmp_path / "reviewed"
    generated = approved_copy(tmp_path, reviewed, "run-generated")
    definition = approved_copy(tmp_path, reviewed, "definition-one", definition=True)

    catalog = registry(tmp_path, reviewed).catalog(DEFAULT_BUNDLE)

    assert catalog.default_id == "golden"
    assert [item.id for item in catalog.versions] == ["golden", "definition-one", "run-generated"]
    by_id = {item.id: item for item in catalog.versions}
    assert by_id["golden"].origin == "golden"
    assert by_id["run-generated"].origin == "generation"
    assert by_id["definition-one"].origin == "definition"
    assert by_id["definition-one"].parent_version == "0.2.0"
    assert by_id["run-generated"].reviewer == "Data Owner"
    assert generated.is_dir() and definition.is_dir()


def test_default_switch_is_persistent_idempotent_and_can_restore_golden(tmp_path: Path):
    reviewed = tmp_path / "reviewed"
    saved = approved_copy(tmp_path, reviewed, "run-generated")
    version_registry = registry(tmp_path, reviewed)

    resolved, payload = version_registry.set_default("run-generated")
    repeated, repeated_payload = version_registry.set_default("run-generated")

    assert resolved.path == saved
    assert repeated.path == saved
    assert payload["bundle_id"] == repeated_payload["bundle_id"] == "run-generated"
    assert json.loads((tmp_path / "active.json").read_text(encoding="utf-8"))["path"] == str(saved.resolve())
    assert version_registry.catalog(saved).default_id == "run-generated"

    version_registry.set_default("golden")
    pointer = json.loads((tmp_path / "active.json").read_text(encoding="utf-8"))
    assert pointer["path"] == str(DEFAULT_BUNDLE.resolve())


def test_catalog_rejects_untrusted_paths_and_tampered_versions(tmp_path: Path):
    reviewed = tmp_path / "reviewed"
    saved = approved_copy(tmp_path, reviewed, "run-generated")
    version_registry = registry(tmp_path, reviewed)
    (saved / "index.md").write_text("tampered", encoding="utf-8")

    assert [item.id for item in version_registry.catalog(DEFAULT_BUNDLE).versions] == ["golden"]
    with pytest.raises(BundleVersionInvalid):
        version_registry.resolve("run-generated")
    with pytest.raises(BundleVersionNotFound):
        version_registry.resolve("../bank-workshop")


def test_configuration_lock_keeps_catalog_read_only(tmp_path: Path):
    version_registry = registry(tmp_path, tmp_path / "reviewed", allowed=False)

    assert version_registry.catalog(DEFAULT_BUNDLE).default_change_allowed is False
    with pytest.raises(BundleDefaultLocked):
        version_registry.set_default("golden")


def test_http_catalog_previews_and_switches_the_workspace_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    reviewed = tmp_path / "reviewed"
    saved = approved_copy(tmp_path, reviewed, "run-generated")
    monkeypatch.setattr(generation, "ACTIVE_BUNDLE_POINTER", tmp_path / "active.json")
    monkeypatch.delenv("CEREBRO_BUNDLE_PATH", raising=False)
    app = create_app(reviewed_output_root=reviewed)

    async def exercise():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            catalog = await client.get("/api/bundles")
            assert catalog.status_code == 200
            assert [item["id"] for item in catalog.json()["versions"]] == ["golden", "run-generated"]

            graph = await client.get("/api/bundles/run-generated/graph")
            assert graph.status_code == 200
            assert graph.json()["version"].endswith("run-generated")
            assert (await client.get("/api/bundles/run-generated/objects/table.accounts")).status_code == 200

            selected = await client.put("/api/bundles/default", json={"bundle_id": "run-generated"})
            assert selected.status_code == 200
            assert selected.json()["bundle_id"] == "run-generated"
            assert (await client.get("/api/bundles")).json()["default_id"] == "run-generated"
            assert (await client.get("/api/graph")).json()["version"].endswith("run-generated")
            assert app.state.bundle.root == str(saved)

            restored = await client.put("/api/bundles/default", json={"bundle_id": "golden"})
            assert restored.status_code == 200
            assert (await client.get("/api/bundles")).json()["default_id"] == "golden"

            missing = await client.get("/api/bundles/not-a-version/graph")
            assert missing.status_code == 404
            traversal = await client.put("/api/bundles/default", json={"bundle_id": "../bank-workshop"})
            assert traversal.status_code == 404

    asyncio.run(exercise())


def test_http_default_change_is_locked_by_explicit_bundle_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("CEREBRO_BUNDLE_PATH", str(DEFAULT_BUNDLE))
    monkeypatch.setattr(generation, "ACTIVE_BUNDLE_POINTER", tmp_path / "active.json")
    app = create_app(reviewed_output_root=tmp_path / "reviewed")

    async def exercise():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            catalog = await client.get("/api/bundles")
            assert catalog.status_code == 200
            assert catalog.json()["default_change_allowed"] is False

            selected = await client.put("/api/bundles/default", json={"bundle_id": "golden"})
            assert selected.status_code == 409
            assert selected.json()["detail"] == {
                "code": "default_locked_by_configuration",
                "message": "The default graph is locked by server configuration",
            }
            assert not (tmp_path / "active.json").exists()

    asyncio.run(exercise())
