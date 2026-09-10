from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_dockerfile_is_locked_multi_stage_non_root_runtime():
    dockerfile = read("Dockerfile")

    assert re.search(r"^FROM node:20[^ ]* AS web-builder$", dockerfile, re.MULTILINE)
    assert "npm ci" in dockerfile
    assert "npm test" in dockerfile
    assert "npm run build" in dockerfile
    assert re.search(r"^FROM python:3\.12[^ ]* AS runtime$", dockerfile, re.MULTILINE)
    assert "uv sync --frozen --no-dev --extra ai --no-install-project" in dockerfile
    assert "USER 10001:10001" in dockerfile
    assert "ENTRYPOINT [\"/usr/bin/tini\", \"--\"]" in dockerfile
    assert "--host 0.0.0.0 --port ${PORT}" in dockerfile
    assert "ENV PORT=8000" in dockerfile
    assert "EXPOSE 8000" in dockerfile
    assert "STOPSIGNAL SIGTERM" in dockerfile
    assert "/health" in dockerfile
    assert "org.opencontainers.image.revision" in dockerfile
    assert "org.opencontainers.image.version" in dockerfile
    assert "COPY --chown=10001:10001 data/workshop.duckdb /data/workshop.duckdb" in dockerfile
    assert "test -s /data/workshop.duckdb" in dockerfile
    assert "chmod 0444 /data/workshop.duckdb" in dockerfile


def test_dockerignore_is_allowlisted_and_excludes_runtime_data():
    ignored = read(".dockerignore").splitlines()

    assert ignored[0] == "**"
    for forbidden in (
        ".git",
        ".codegraph",
        ".env",
        ".venv",
        "**/node_modules",
        "*.duckdb",
        "*.duckdb.wal",
        "artifacts",
        "knowledge/generated",
        "knowledge/reviewed",
        "tests",
    ):
        assert forbidden in ignored
    assert "!knowledge/bank-workshop/**" in ignored
    assert "!vendor/open-knowledge-format/**" in ignored
    assert "!data/workshop.duckdb" in ignored
    assert ignored.index("*.duckdb") < ignored.index("!data/workshop.duckdb"), (
        "the re-include of the baked-in demo database must come after the "
        "blanket *.duckdb exclusion, or Docker's last-match-wins ordering "
        "would still block it"
    )


def test_compose_hardens_cerebro_and_publishes_only_caddy():
    compose = yaml.safe_load(read("deploy/compose.production.yaml"))
    services = compose["services"]
    cerebro = services["cerebro"]
    caddy = services["caddy"]

    assert cerebro["image"].startswith("${CEREBRO_IMAGE:")
    assert "ports" not in cerebro
    assert cerebro["read_only"] is True
    assert cerebro["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in cerebro["security_opt"]
    assert cerebro["restart"] == "unless-stopped"
    assert cerebro["user"] == "10001:10001"
    assert cerebro["logging"]["options"] == {"max-size": "10m", "max-file": "3"}
    assert "/tmp:size=128m,mode=1777" in cerebro["tmpfs"]
    mounts = {item["target"]: item for item in cerebro["volumes"]}
    assert mounts["/data/workshop.duckdb"]["read_only"] is True
    assert set(mounts) == {
        "/data/workshop.duckdb",
        "/app/knowledge/generated",
        "/app/knowledge/reviewed",
        "/app/artifacts",
    }
    assert caddy["ports"] == ["80:80", "443:443"]
    assert caddy["depends_on"]["cerebro"]["condition"] == "service_healthy"
    assert caddy["restart"] == "unless-stopped"


def test_caddy_authenticates_the_entire_site_and_preserves_streaming():
    caddyfile = read("deploy/Caddyfile")
    users = read("deploy/caddy-users.caddy.example")

    assert caddyfile.index("import team_auth") < caddyfile.index("reverse_proxy cerebro:8000")
    assert "encode zstd gzip" in caddyfile
    assert "flush_interval -1" in caddyfile
    assert "Strict-Transport-Security" in caddyfile
    assert "X-Content-Type-Options" in caddyfile
    assert "basic_auth" in users
    assert "caddy hash-password" in users
    assert "plaintext" not in users.lower()


def test_release_workflow_uses_amd64_immutable_tags_and_digest_output():
    workflow = read(".github/workflows/release-container.yml")

    # The release image bakes in data/workshop.duckdb (see Dockerfile), which only
    # exists on the release operator's machine, not on GitHub-hosted runners, so
    # automatic tag-push builds are disabled in favor of a manual, documented
    # local build/push flow (docs/deployment-greennode-agent-runtime.md).
    assert not re.search(r"^\s*-\s*\"v\*\"", workflow, re.MULTILINE)
    assert "workflow_dispatch:" in workflow
    assert "python -m pytest -q" in workflow
    assert "npm test" in workflow and "npm run build" in workflow
    assert "platforms: linux/amd64" in workflow
    assert "secrets.VCR_REGISTRY" in workflow
    assert "secrets.VCR_USERNAME" in workflow
    assert "secrets.VCR_PASSWORD" in workflow
    assert "sha-${{ github.sha }}" in workflow
    assert "steps.push.outputs.digest" in workflow
    assert not re.search(r"(^|[/:])latest($|\s)", workflow, re.MULTILINE)


def test_operator_scripts_cover_all_deployment_lifecycle_gates():
    expected = {
        "scripts/deploy/provision-vserver.sh",
        "scripts/deploy/preflight-database.sh",
        "scripts/deploy/promote.sh",
        "scripts/deploy/smoke.sh",
        "scripts/deploy/backup.sh",
        "scripts/deploy/restore.sh",
        "scripts/deploy/rollback.sh",
        "scripts/deploy/image-smoke.sh",
        "scripts/deploy/compose-smoke.sh",
    }
    for path in expected:
        script = ROOT / path
        assert script.is_file(), path
        assert script.stat().st_mode & 0o111, path
        assert script.read_text(encoding="utf-8").startswith("#!/usr/bin/env bash\nset -Eeuo pipefail\n")

    assert "@sha256:" in read("scripts/deploy/promote.sh")
    assert "backup.sh" in read("scripts/deploy/promote.sh")
    assert "smoke.sh" in read("scripts/deploy/promote.sh")
    assert "sha256sum" in read("scripts/deploy/preflight-database.sh")
    assert "read_only=True" in read("scripts/deploy/preflight-database.sh")
    assert "age" in read("scripts/deploy/backup.sh")
    assert "KEEP_BACKUPS=3" in read("scripts/deploy/backup.sh")
    assert "backup-manifest.txt" in read("scripts/deploy/restore.sh")
    assert "sha256sum" in read("scripts/deploy/restore.sh")
    assert "promote.sh" in read("scripts/deploy/rollback.sh")


def test_runbook_covers_green_node_security_persistence_and_evidence():
    runbook = read("docs/deployment-greennode-vserver.md")

    for required in (
        "GreenNode",
        "filesystem UUID",
        "floating IP",
        "inbound TCP 80 and 443",
        "SSH only",
        "pull-only robot",
        "0600",
        "sha256sum",
        "Golden",
        "database-only generation",
        "restore drill",
        "Rollback",
        "exact vCR repository and sha256 digest",
    ):
        assert required in runbook


def test_promotion_and_rollback_reject_mutable_image_tags():
    environment = {**os.environ, "CEREBRO_NO_ACTIVE_RUNS_CONFIRMED": "yes"}

    promotion = subprocess.run(
        [str(ROOT / "scripts/deploy/promote.sh"), "registry.example/team/cerebro:v1"],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    rollback = subprocess.run(
        [str(ROOT / "scripts/deploy/rollback.sh"), "registry.example/team/cerebro:v1"],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert promotion.returncode == 2
    assert "only a full registry/repository@sha256 digest" in promotion.stderr
    assert rollback.returncode == 2
    assert "@sha256:PREVIOUS_DIGEST" in rollback.stderr
