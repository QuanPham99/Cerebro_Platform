# Deploy Cerebro to one GreenNode vServer

This runbook deploys one `linux/amd64` Cerebro container behind Caddy. Caddy is the only public
service and applies HTTPS plus team-wide Basic Authentication before forwarding any SPA, API,
documentation, knowledge, or MCP request. Cerebro's database is read-only; semantic candidates,
reviewed versions, and the workspace-default pointer are persistent bind mounts.

The production launcher is Docker Compose. Do not use `scripts/dev.sh` on the server.

## 1. Required infrastructure

Provision an Ubuntu LTS x86-64 GreenNode vServer with:

- at least 2 vCPU and 4 GB RAM;
- one floating IP;
- a separate data volume with growth room;
- inbound TCP 80 and 443 from the internet;
- inbound SSH only from the administrator allowlist;
- no public rule for 8000, Docker, DuckDB, or MCP.

Follow GreenNode's [Linux volume guide](https://helpdesk.greennode.ai/portal/en/kb/articles/h%C6%B0%E1%BB%9Bng-d%E1%BA%ABn-add-th%C3%AAm-%E1%BB%95-c%E1%BB%A9ng-tr%C3%AAn-linux-%C4%91%E1%BB%83-extend-dung-l%C6%B0%E1%BB%A3ng-17-1-2024)
to identify and prepare the new volume. Confirm the device is the empty attached volume before any
filesystem operation. Mount it persistently by filesystem UUID at `/srv/cerebro`; a typical fstab
entry has this shape:

```text
UUID=<volume-filesystem-uuid> /srv/cerebro ext4 defaults,nofail 0 2
```

Run `findmnt /srv/cerebro` after `mount -a` and again after a reboot. Never use a volatile device
name as the persistence contract.

Install Docker Engine with the Compose plugin from Docker's
[Ubuntu installation guide](https://docs.docker.com/engine/install/ubuntu/). Install `age` for
encrypted backups and ensure `curl`, `python3`, `tar`, and coreutils are present. Copy this repository's
`scripts/deploy` directory to an administrator checkout, then run:

```bash
sudo scripts/deploy/provision-vserver.sh
```

The script validates Ubuntu, amd64, CPU, memory, Docker Compose, the volume's matching UUID entry in
`/etc/fstab`, and at least 10 GiB free by default before it creates the fixed directory ownership
needed by container UID/GID 10001. Override `CEREBRO_MIN_FREE_KIB` only when the operator has chosen
a different documented growth margin.

## 2. DNS and server files

Create an A record for the chosen hostname pointing to the floating IP. Wait until public DNS resolves
to that address. Caddy needs working inbound 80/443 and outbound internet access to obtain and renew
the certificate.

Install the tracked deployment definitions:

```bash
sudo install -o root -g root -m 0644 deploy/compose.production.yaml /srv/cerebro/deploy/compose.production.yaml
sudo install -o root -g root -m 0644 deploy/Caddyfile /srv/cerebro/config/Caddyfile
sudo install -o root -g root -m 0600 deploy/cerebro.env.example /srv/cerebro/config/cerebro.env
sudo install -o root -g root -m 0600 deploy/deployment.env.example /srv/cerebro/config/deployment.env
sudo install -o root -g root -m 0600 deploy/caddy-users.caddy.example /srv/cerebro/config/caddy-users.caddy
```

Edit `/srv/cerebro/config/deployment.env`:

- `CEREBRO_IMAGE` is the exact vCR `registry/repository@sha256:<64 hex>` promotion input.
- `CEREBRO_ROLLBACK_IMAGE` is empty initially and is maintained by promotion.
- `CEREBRO_DOMAIN` is the DNS name created above.
- `CADDY_ACME_EMAIL` is the certificate-account contact.

Edit `/srv/cerebro/config/cerebro.env` and keep mode `0600`:

```dotenv
CEREBRO_DATABASE_PATH=/data/workshop.duckdb
CEREBRO_DATABASE_SCHEMA=main
CEREBRO_LLM_BASE_URL=<GreenNode-compatible base URL>
CEREBRO_LLM_API_KEY=<server-only key>
CEREBRO_LLM_MODEL=<selected model>
CEREBRO_LLM_PROVIDER_ID=<stable provider id>
CEREBRO_LLM_PROVIDER_NAME=<safe display name>
```

Copy the query limits and provider timeout controls from the example, adjusting them only when the
deployment owner has approved the values. Do not put secrets in Compose YAML, Git, Docker build
arguments, image labels, shell history, or CI output.

## 3. Team credentials

Caddy requires password hashes for [`basic_auth`](https://caddyserver.com/docs/caddyfile/directives/basic_auth).
Generate one bcrypt hash per person on a trusted terminal. The command prompts without echoing the
password:

```bash
docker run --rm -it caddy:2.10.2-alpine caddy hash-password --algorithm bcrypt
```

Write only usernames and returned hashes to `/srv/cerebro/config/caddy-users.caddy`:

```caddyfile
(team_auth) {
    basic_auth {
        alice <alice-bcrypt-hash>
        bob <bob-bcrypt-hash>
    }
}
```

Keep this file root-owned and `0600`. Do not save team passwords. The imported snippet is applied at
the site root, so `/`, `/api`, `/docs`, `/knowledge`, and `/mcp` all require authentication.

## 4. vCR credentials and GitHub release settings

Create two separate vCR identities:

- GitHub Actions push credential;
- vServer pull-only robot credential.

Configure these GitHub repository secrets:

| Secret | Value |
|---|---|
| `VCR_REGISTRY` | vCR registry hostname, without `https://` |
| `VCR_IMAGE_NAME` | repository path, without registry hostname |
| `VCR_USERNAME` | CI push username |
| `VCR_PASSWORD` | CI push token/password |

The workflow `.github/workflows/release-container.yml` runs for `v*` tags or a manual version,
executes the full Python and frontend gates, builds and smoke-tests amd64, pushes release-version and
commit-SHA tags, and prints the exact digest reference. It never deploys a floating tag.

On the vServer, log in interactively with the pull-only robot without putting the token on the command
line:

```bash
printf '%s' "$VCR_PULL_TOKEN" | docker login "$VCR_REGISTRY" --username "$VCR_PULL_USER" --password-stdin
```

## 5. Upload and verify DuckDB

On the trusted source machine, record both independent transfer checks:

```bash
sha256sum data/workshop.duckdb
stat --format=%s data/workshop.duckdb
scp data/workshop.duckdb administrator@<floating-ip>:workshop.duckdb.upload
```

On the server, finish the upload name and run preflight with the recorded values:

```bash
sudo mv ~/workshop.duckdb.upload /srv/cerebro/incoming/workshop.duckdb
sudo scripts/deploy/preflight-database.sh \
  /srv/cerebro/incoming/workshop.duckdb \
  <expected-sha256> \
  <expected-byte-size>
```

Preflight rejects symlinks, paths outside `incoming`, an existing production database, wrong checksum
or size, a different filesystem, a mutable image reference, and a DuckDB read-only open failure. It
then uses a same-filesystem move to publish `/srv/cerebro/data/workshop.duckdb` atomically.

## 6. Optional reviewed-version seed

Seed only approved reviewed bundle directories that should be visible in the Semantic Version
Library. Verify each `approval.json` and bundle digest before copying, then make the resulting files
owned by 10001:10001. Do not seed candidates.

Never copy a local `artifacts/active-bundle.json`: it contains machine-specific paths. Ensure the
server starts with an empty artifacts directory:

```bash
sudo test -z "$(find /srv/cerebro/state/artifacts -mindepth 1 -print -quit)"
```

With no pointer, Cerebro selects the immutable Golden bundle. Import and approval do not activate a
reviewed version; changing the workspace default remains a later explicit UI/API action.

## 7. Backup target and first promotion

Mount an off-server backup filesystem at a path such as `/mnt/cerebro-offsite`. It must be a distinct
filesystem from `/srv/cerebro`. Store an age recipient file on the vServer; keep the corresponding
identity offline. Export backup and smoke inputs only for the operator session:

```bash
export CEREBRO_BACKUP_DESTINATION=/mnt/cerebro-offsite
export CEREBRO_AGE_RECIPIENT_FILE=/root/cerebro-backup-recipients.txt
export CEREBRO_SMOKE_USER=<team-user>
read -r -s -p 'Cerebro smoke password: ' CEREBRO_SMOKE_PASSWORD
export CEREBRO_SMOKE_PASSWORD
export CEREBRO_NO_ACTIVE_RUNS_CONFIRMED=yes
```

For the first deployment, set `CEREBRO_IMAGE` to the selected digest, confirm there can be no active
run, and promote that same digest:

```bash
sudo --preserve-env=CEREBRO_BACKUP_DESTINATION,CEREBRO_AGE_RECIPIENT_FILE,CEREBRO_SMOKE_USER,CEREBRO_SMOKE_PASSWORD,CEREBRO_NO_ACTIVE_RUNS_CONFIRMED \
  scripts/deploy/promote.sh '<registry>/<repository>@sha256:<digest>'
```

Promotion takes an encrypted backup, atomically changes the pin, pulls, recreates, waits for container
readiness, and runs authenticated smoke tests. If any gate fails, it restores the old pin and attempts
to restart that image. It never restores persistent data automatically.

After the container starts, validate configuration without a live model call:

```bash
sudo docker compose \
  --env-file /srv/cerebro/config/deployment.env \
  --file /srv/cerebro/deploy/compose.production.yaml \
  exec cerebro python -m cerebro.cli doctor
```

## 8. Release smoke and product checks

The promotion smoke stores readiness, runtime status, SPA HTML, and an MCP initialize response under
`/srv/cerebro/evidence/<UTC timestamp>`. Confirm independently:

- HTTP redirects to HTTPS and the certificate chain is trusted.
- Missing credentials return 401; team credentials load the SPA.
- `/api/health/ready` returns 200 and every component is `ok`.
- `/api/runtime/status` reports built UI, reachable database, configured provider, and chat readiness
  without a credential value.
- SPA graph inspection works and Golden is selected on the first deployment.
- Versions can be previewed; Define is locked to the exact selected approved version.
- Governed chat returns a result for an approved workshop question.
- A database-only generation streams progress through Caddy, produces a separate candidate, and does
  not alter the active graph.
- Approval adds a reviewed version but does not select it as default.
- Restarting both containers preserves reviewed versions and the selected default:

```bash
sudo docker compose --env-file /srv/cerebro/config/deployment.env \
  --file /srv/cerebro/deploy/compose.production.yaml restart cerebro caddy
```

Because run status and traces are held in process memory, never promote or restart while a generation
run is active. Interrupted runs do not resume, though their filesystem output remains.

## 9. Backup and restore drill

Every promotion invokes `backup.sh`. It archives the database plus checksum/size, generated and
reviewed directories, artifacts (including the pointer if present), and deployment configuration. It
encrypts before copying to the off-server mount and retains the newest three encrypted
archives.

Run a standalone backup with the same exported inputs:

```bash
sudo --preserve-env=CEREBRO_BACKUP_DESTINATION,CEREBRO_AGE_RECIPIENT_FILE \
  scripts/deploy/backup.sh
```

Perform a restore drill into an isolated directory; this command never replaces live data:

```bash
export CEREBRO_AGE_IDENTITY_FILE=/root/cerebro-backup-identity.txt
sudo --preserve-env=CEREBRO_BACKUP_DESTINATION,CEREBRO_AGE_IDENTITY_FILE \
  scripts/deploy/restore.sh /mnt/cerebro-offsite/cerebro-<timestamp>.tar.gz.age
```

Compare the restored manifest, database checksum, reviewed versions, generated output, pointer, and
configuration. Promote restored persistent data only during an explicit maintenance window and only
when a release changed its format. This containerization release has no data migration.

## 10. Rollback

Rollback changes application code only and uses the same backup, readiness, and smoke gates:

```bash
sudo --preserve-env=CEREBRO_BACKUP_DESTINATION,CEREBRO_AGE_RECIPIENT_FILE,CEREBRO_SMOKE_USER,CEREBRO_SMOKE_PASSWORD,CEREBRO_NO_ACTIVE_RUNS_CONFIRMED \
  scripts/deploy/rollback.sh '<registry>/<repository>@sha256:<previous-digest>'
```

Omit the argument to use `CEREBRO_ROLLBACK_IMAGE` recorded by the last successful promotion. Restore
persistent data only when a separately reviewed data-format change requires it.

## 11. Release evidence record

Release acceptance requires one evidence record containing:

| Field | Evidence |
|---|---|
| Image | exact vCR repository and sha256 digest |
| Source | Git commit from the image OCI revision label |
| Backup | encrypted off-server archive path and checksum |
| Health | captured `/api/health/ready` JSON |
| Runtime | captured sanitized `/api/runtime/status` JSON |
| Smoke | automated result plus manual graph/chat/generation/restart checks |
| Restore | isolated restore-drill directory and verification result |
| Rollback | previously accepted digest |

Do not accept a version tag alone as deployment evidence.
