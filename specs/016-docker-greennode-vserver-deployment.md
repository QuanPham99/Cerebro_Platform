# 016 — Docker and GreenNode vServer deployment

## Problem

Cerebro currently has a development launcher but no reproducible production image, hardened
single-host topology, readiness contract, or digest-based release procedure. Local credentials,
DuckDB data, generated candidates, reviewed versions, and the active pointer must not leak into a
release artifact, while approved runtime state must survive container replacement.

## Goal

Package the current FastAPI, MCP, and compiled React application as one `linux/amd64` image and
operate it on one GreenNode Ubuntu LTS vServer behind Caddy-managed HTTPS and team Basic
Authentication. Publish immutable release inputs to GreenNode vCR and require an explicit manual
promotion of a selected image digest.

## Non-goals

- Multiple Cerebro replicas, rolling deployments, or resuming interrupted in-memory generation runs.
- Application-level identity, authorization roles, or cryptographic binding of reviewer names.
- AgentBase port-8080 or 60-second gateway adaptations.
- Scheduled daily backups or an automated database/schema migration.
- Exposing Cerebro port 8000, DuckDB, Docker, or MCP on separate public listeners.
- Automatically activating imported, generated, or reviewed semantic versions.

## Functional requirements

- FR-1601: A multi-stage Docker build MUST use Node 20 to run `npm ci`, frontend tests, and the
  production build, then use Python 3.12 slim for the runtime.
- FR-1602: Production Python dependencies, including the `ai` extra, MUST resolve from `uv.lock` in
  frozen mode. The runtime image MUST contain only application source, runtime configuration,
  vendored OKF, Golden knowledge, and compiled SPA assets.
- FR-1603: The image MUST run as fixed UID/GID 10001, use an init process, bind to
  `0.0.0.0:8000`, expose only port 8000 to its container network, carry OCI version/revision labels,
  and define a readiness-based health check.
- FR-1604: The build context MUST exclude Git and CodeGraph metadata, environment files, virtual
  environments, Node modules, caches, DuckDB/WAL files, artifacts, generated candidates, reviewed
  runtime data, tests not required by the frontend build, and non-runtime documentation.
- FR-1605: `GET /api/health` MUST remain compatible. `GET /api/health/ready` MUST return 200 only
  when the active and Golden bundles still validate, the SPA index exists, the configured DuckDB
  accepts a read-only connection/query, and the deployment LLM key/model configuration is present.
- FR-1606: Readiness MUST NOT call a model or disclose credentials, filesystem paths, exception
  text, query contents, or other secret-bearing values. A failed check MUST return 503 with only
  bounded component names, statuses, and safe public bundle metadata.
- FR-1607: Production Compose MUST pin Cerebro through a required digest-valued variable, publish
  no Cerebro host port, use a read-only root filesystem, writable `/tmp` tmpfs, dropped capabilities,
  `no-new-privileges`, bounded JSON log rotation, an explicit restart policy, and the image health check.
- FR-1608: Compose MUST bind the host DuckDB read-only at `/data/workshop.duckdb` and bind writable
  host directories to `/app/knowledge/generated`, `/app/knowledge/reviewed`, and `/app/artifacts`.
  These stable paths MUST keep activation-pointer values valid across restarts.
- FR-1609: Caddy MUST be the only public service on ports 80/443, redirect HTTP to HTTPS, manage
  certificate state persistently, compress responses, add security headers, authenticate every route,
  and reverse proxy to `cerebro:8000`. Passwords MUST be represented only by Caddy-compatible hashes
  in a root-readable server file.
- FR-1610: Provisioning guidance and automation MUST validate an x86-64 Ubuntu host, a separately
  mounted `/srv/cerebro` data volume, Docker Compose availability, directory ownership for UID/GID
  10001, DNS/floating-IP prerequisites, and firewall boundaries.
- FR-1611: Database import MUST stage under `/srv/cerebro`, compare operator-supplied SHA-256 and
  byte size, open the database read-only, and publish it with a same-filesystem atomic move. Existing
  production data MUST not be overwritten implicitly.
- FR-1612: Initial deployment MUST omit an active-pointer file and seed only approved reviewed
  bundles selected by the operator. Golden MUST therefore remain the initial workspace default;
  import or approval MUST not select a different default.
- FR-1613: A release workflow triggered by `v*` tags or manual dispatch MUST run the complete Python
  suite and frontend tests/build, build and smoke-test `linux/amd64`, log in to vCR from GitHub
  secrets, push version and commit-SHA tags but never `latest`, and report the pushed digest.
- FR-1614: Promotion MUST accept only an exact `@sha256:<64 hex>` image reference, take a
  pre-release backup, update the pinned deployment value atomically, pull, recreate, wait for
  readiness, and run authenticated smoke tests. It MUST restore the prior pin automatically if the
  deployment or smoke gate fails.
- FR-1615: Before each release, backup MUST include the database checksum, database, generated and
  reviewed directories, active pointer when present, and deployment configuration. The archive MUST
  be encrypted, copied to a separately mounted off-server destination, and pruned to the newest three.
- FR-1616: Rollback MUST restore a previous image digest and rerun the same promotion gates. Persistent
  data MUST be restored only by an explicit restore operation when a release changed its format.
- FR-1617: Deployment evidence MUST record the image digest, Git commit, backup location, readiness
  output, smoke results, and rollback digest. Operators MUST confirm no generation run is active
  before promotion because run state and traces are process-local.

## Acceptance criteria

- AC-1601: A clean build does not depend on `.env`, `.venv`, local `node_modules`, built assets,
  DuckDB files, generated candidates, reviewed data, or Git metadata.
- AC-1602: Image inspection reports UID/GID 10001, OCI revision/version labels, and no forbidden
  release content; a container using a read-only root can write only through the declared tmpfs and
  state mounts and exits cleanly after SIGTERM.
- AC-1603: Readiness returns 200 for a valid test deployment and 503 independently for a missing or
  unreadable database, missing SPA, changed/invalid active bundle, changed/invalid Golden bundle, or
  absent LLM configuration. Responses contain no configured secret marker.
- AC-1604: Compose configuration exposes only Caddy 80/443, waits on Cerebro health, and contains all
  FR-1607 through FR-1609 controls and stable mounts.
- AC-1605: An unauthenticated public request returns 401; an authenticated request reaches SPA, API,
  and MCP through the same HTTPS origin; Caddy does not buffer SSE generation events.
- AC-1606: Database preflight rejects checksum, size, non-regular-file, existing-target, and DuckDB
  open failures without changing the production database.
- AC-1607: Release configuration has only tag/manual triggers, amd64 build/smoke gates, vCR secrets,
  version/SHA tags, no `latest`, and a digest promotion output.
- AC-1608: Promotion and rollback reject mutable tags, preserve the prior pin, and stop on failed
  readiness/smoke. Backup refuses a destination on the Cerebro data filesystem and retains three
  encrypted archives.
- AC-1609: A fresh server with no pointer reports Golden as the default; reviewed versions survive
  container restarts and remain inactive until an explicit default-selection request.
- AC-1610: The runbook covers GreenNode volume-by-UUID mounting, floating-IP DNS, 80/443 exposure,
  allowlisted SSH, `0600` secrets/hash files, vCR robot pulls, database upload verification, reviewed
  seeding, promotion, smoke, evidence capture, backup/restore drill, and digest rollback.

## Edge cases

- EC-1601: A stale pointer naming a path absent inside the container falls back to Golden under the
  existing compatibility contract; operators must not copy machine-specific local pointer files.
- EC-1602: A valid DuckDB path that cannot be opened read-only is not ready even if it exists.
- EC-1603: Readiness remains local and deterministic during provider outage because it checks only
  required configuration, not provider reachability.
- EC-1604: A failed promotion restores the previous deployment pin but does not roll back persistent
  state automatically.
- EC-1605: Caddy credentials containing plaintext are an operator error; only hashes generated with
  `caddy hash-password` are accepted by the documented procedure.
- EC-1606: Backup or restore must not follow an untrusted arbitrary source path or overwrite live data
  without an explicit operator-selected archive and maintenance action.

## Interfaces and contracts

### Readiness response

Success uses HTTP 200; any failed component uses HTTP 503:

```json
{
  "status": "ready",
  "components": {
    "active_bundle": {"status": "ok", "name": "bank-workshop", "version": "0.2.0"},
    "golden_bundle": {"status": "ok", "name": "bank-workshop", "version": "0.2.0"},
    "web_ui": {"status": "ok"},
    "database": {"status": "ok", "configured": true},
    "llm": {"status": "ok", "configured": true}
  }
}
```

Failure changes the top-level status to `not_ready` and affected component status to `error`.
No error message or configured value is returned.

### Deployment inputs

- `CEREBRO_IMAGE`: full vCR repository plus `@sha256:<digest>`.
- `CEREBRO_DOMAIN`: public DNS name used by Caddy.
- `/srv/cerebro/config/cerebro.env`: application environment, mode `0600`.
- `/srv/cerebro/config/caddy-users.caddy`: named Caddy auth snippet with one bcrypt hash per member,
  mode `0600`.
- `/srv/cerebro/data/workshop.duckdb`: production source, bind-mounted read-only.
- `/srv/cerebro/state/{generated,reviewed,artifacts}`: mutable semantic state owned by 10001:10001.

### Deployment topology

`Internet :80/:443 -> Caddy + Basic Auth -> private Compose network -> Cerebro :8000`

All HTTP routes, including `/`, `/api`, `/docs`, `/knowledge`, and `/mcp`, cross the same Caddy auth
handler. No application port is host-published.

## Security boundaries

- Basic Authentication is the selected private-team MVP perimeter. Every authenticated team member
  has the same Cerebro capabilities.
- TLS termination and authentication belong to Caddy. Cerebro trusts only traffic on the private
  Compose network and does not infer reviewer identity from Caddy.
- The vServer uses a pull-only vCR robot credential. GitHub uses independent push credentials stored
  as repository secrets.
- Application credentials exist only in a root-readable server environment file. They are absent from
  Compose YAML, Git, build arguments, labels, image layers, and CI output.
- The DuckDB source is authorized for this private deployment and is read-only inside Cerebro.
- Generated/reviewed bundles and the active pointer are mutable application state; Golden remains
  immutable in the read-only image filesystem.

## Persistence and lifecycle rules

- Candidate generation writes only to `/app/knowledge/generated`.
- Approval writes an immutable copy only to `/app/knowledge/reviewed` and never changes the pointer.
- Explicit default selection writes `/app/artifacts/active-bundle.json` using stable `/app/...` paths.
- A new server starts without this pointer and therefore selects Golden.
- Container replacement reuses all three state mounts. Database replacement is a separate, verified
  operator action.
- Deployment occurs only after confirming that no generation run is active. Interrupted in-memory
  run status is not resumed, although filesystem output remains available.

## Test design

| Test ID | Requirement / criterion | Level | Scenario | Expected result |
|---|---|---|---|---|
| T-1601 | FR-1605, AC-1603 | API | Valid bundles, SPA, DuckDB, and LLM config | 200 with bounded `ready` payload |
| T-1602 | FR-1605, EC-1602 | API | Missing and non-DuckDB database | 503, database `error` |
| T-1603 | FR-1605, AC-1603 | API | Missing SPA index | 503, web UI `error` |
| T-1604 | FR-1605, AC-1603 | API | Active bundle changes after startup | 503, active bundle `error` |
| T-1605 | FR-1605, AC-1603 | API | Golden bundle validation fails | 503, Golden `error` |
| T-1606 | FR-1605/1606, AC-1603 | API/security | Missing LLM and secret marker in configured environment | 503 as applicable; marker absent from body |
| T-1607 | FR-1601–1604, AC-1601/1602 | Static/image | Inspect Dockerfile, ignore rules, image config/content/user | Required build/runtime controls and no forbidden content |
| T-1608 | FR-1607–1609, AC-1604/1605 | Static/integration | Render Compose; start with temporary mounts and Caddy credentials | Only 80/443 public; hardening, auth, routing, SSE, restart persistence pass |
| T-1609 | FR-1611, AC-1606 | Script/integration | Database preflight success and each invalid input | Only verified database is atomically published |
| T-1610 | FR-1613, AC-1607 | Static/CI | Inspect workflow and execute release job | All gates run, no latest, digest reported |
| T-1611 | FR-1614/1616, AC-1608 | Script/integration | Mutable ref, successful digest promotion, failed smoke, rollback | Rejections/restore behavior match contract |
| T-1612 | FR-1615, AC-1608 | Script/integration | Encrypted backup to distinct mount with more than three archives | Complete archive copied; newest three retained |
| T-1613 | FR-1612, AC-1609 | Compose/API | Fresh state, reviewed import, restart, explicit selection | Golden first; persistence without implicit activation |
| T-1614 | FR-1610/1617, AC-1610 | Documentation review | Follow runbook and evidence checklist | Every operator action/evidence field is present |

Test categories: happy path T-1601/T-1608; validation T-1602/T-1609/T-1611; edge cases
T-1603–T-1606; error handling T-1602–T-1606/T-1611; regression T-1601 plus full repository
suites; integration T-1608/T-1609/T-1613; contract/schema T-1601/T-1607/T-1610; security
T-1606–T-1609/T-1612; performance is N/A because this release introduces no request-path workload
beyond periodic local readiness checks.

## Constraints

- Target host is Ubuntu LTS x86-64 with at least 2 vCPU, 4 GB RAM, one floating IP, and a separate
  data volume mounted by filesystem UUID at `/srv/cerebro` with growth room.
- The current source DuckDB remains outside the image. No data-format migration is introduced.
- Production does not use `scripts/dev.sh`.
- The image architecture is exactly `linux/amd64` for the first deployment.
- Caddy certificate issuance requires correct public DNS and inbound 80/443.

## Assumptions

- The operator supplies the GreenNode vCR registry/repository, production domain, pull-only robot
  credential, LLM settings/key, bcrypt user hashes, expected database checksum/size, backup recipient,
  and an off-server mounted backup destination.
- GreenNode-compatible provider values are revalidated with `cerebro doctor`; readiness deliberately
  performs no live provider call.
- The separately mounted backup destination is managed outside this repository.

## Open questions

None block repository implementation. Infrastructure-specific values remain explicit operator inputs.
