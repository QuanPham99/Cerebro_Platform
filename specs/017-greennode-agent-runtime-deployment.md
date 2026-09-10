# 017 — GreenNode Agent Runtime deployment with baked-in database

## Problem

Spec 016 packaged Cerebro for a self-managed GreenNode vServer running Docker Compose with a Caddy
sidecar for TLS/auth, and a host-side bind mount supplying the DuckDB source. The account actually
deploying this demo has only two GreenNode products: **Container Registry** and **Agent Runtime**
(a managed PaaS that runs a pushed container image) — there is no vServer to provision, no place to
run a Compose stack, and no confirmed sidecar/reverse-proxy primitive. DuckDB is also embedded and
file-only everywhere it's opened in this codebase (`src/cerebro/executor.py`, `src/cerebro/chat.py`,
`src/cerebro/source.py`, `src/cerebro/api.py`'s readiness check all call
`duckdb.connect(<local path>, read_only=True)`; `chat.py` additionally sets
`enable_external_access: "false"`; `src/cerebro/selfcheck.py` blocks `httpfs`/`ATTACH`/remote
`*_scan`), so a network-attached "data service" split between two images is not realistic without
engine-level changes that are out of scope here.

## Goal

Bake the fixed demo DuckDB file directly into the release image so the container is fully
self-contained, publish that image to GreenNode Container Registry via a manual, documented local
build/push flow, and deploy it to GreenNode Agent Runtime. Add a minimal, off-by-default app-level
Basic Auth fallback so a first deployment can never be accidentally left wide open while GreenNode's
own gateway/access-control capabilities (if any) are still being confirmed.

## Non-goals

- CI-automated image builds. GitHub-hosted runners have no access to the DB file (it lives only on
  the release operator's machine); `.github/workflows/release-container.yml` is kept as a
  manually-runnable option (`workflow_dispatch` only) but does not build/push automatically.
- Changing or removing the spec-016 vServer + Compose + Caddy path. It remains valid, untouched, and
  usable later if the account gains vServer access.
- A network/client-server DuckDB mode, or splitting the database into a second image/service.
- Persistent volumes on Agent Runtime, until the console confirms they exist; `knowledge/generated`,
  `knowledge/reviewed`, and `artifacts` are treated as ephemeral for this iteration.
- Multi-replica or rolling deployment.
- Resolving GreenNode Agent Runtime's exact port/health-check/domain/gateway-auth conventions in this
  document — those remain explicit open questions (see below) confirmed by the operator in the
  GreenNode console before the first real deployment, per
  `docs/deployment-greennode-agent-runtime.md`.

## Functional requirements

- FR-1701: The Docker build MUST copy a staged `data/workshop.duckdb` file into the image at
  `/data/workshop.duckdb`, fail the build immediately if that file is missing or empty, and set its
  final mode to `0444` owned by `10001:10001`.
- FR-1702: `.dockerignore` MUST re-include exactly `data/workshop.duckdb` after the blanket
  `*.duckdb`/`*.duckdb.wal` exclusion, so no other stray DuckDB file can enter the build context.
- FR-1703: `CEREBRO_DATABASE_PATH` MUST keep defaulting to `/data/workshop.duckdb`, so no application
  code (`settings.py`, `source.py`, `executor.py`, `chat.py`) changes across this spec.
- FR-1704: `scripts/deploy/image-smoke.sh` MUST positively assert the baked-in database's presence,
  ownership, and mode, assert no other `*.duckdb`/`*.duckdb.wal` file exists in the image, and prove
  the running container serves `/api/health/ready` as ready with **no** `/data` mount at all.
- FR-1705: `.github/workflows/release-container.yml` MUST NOT trigger automatically on tag push (no
  DB available on hosted runners); it MUST remain runnable via `workflow_dispatch` for a future
  self-hosted runner that does have the database staged.
- FR-1706: The app MUST support an optional Basic Auth gate, controlled by
  `CEREBRO_BASIC_AUTH_USER`/`CEREBRO_BASIC_AUTH_PASSWORD`, disabled by default. When enabled, every
  route MUST require valid credentials — including the static SPA (`/`) and the mounted MCP app
  (`/mcp`) — **except** `GET /api/health` and `GET /api/health/ready`, which platform/Docker-level
  health probes hit unauthenticated and which never disclose secrets.
- FR-1707: Credential comparison MUST use constant-time comparison (`hmac.compare_digest`) for both
  username and password.

## Acceptance criteria

- AC-1701: A `docker build` without a staged `data/workshop.duckdb` fails at build time with a clear
  error, not at container-run time.
- AC-1702: `scripts/deploy/image-smoke.sh` passes against an image built with the DB staged, using no
  `/data` bind mount, and fails as designed if the baked-in file were ever absent, wrong-owned, or
  writable.
- AC-1703: `pytest` (`tests/test_deployment_contract.py`, `tests/test_readiness.py`,
  `tests/test_basic_auth.py`) passes unmodified in behavior for every existing, still-relevant
  assertion; new assertions cover the baked-in DB contract and the disabled `push: tags:` trigger.
- AC-1704: With `CEREBRO_BASIC_AUTH_USER`/`_PASSWORD` unset, every route behaves exactly as before
  this spec (no regression for local dev or the still-valid vServer+Caddy path). With them set, an
  unauthenticated or wrongly-authenticated request to any non-health route returns 401 with a
  `WWW-Authenticate: Basic` header; a correctly authenticated request succeeds; `/api/health` and
  `/api/health/ready` remain reachable without credentials either way.
- AC-1705: `docs/deployment-greennode-agent-runtime.md` documents the local build/tag/push flow
  against the account's actual registry (`vcr.vngcloud.vn/111480-abp114537/...`) and an explicit
  console checklist for the Agent Runtime unknowns (port, env/secret injection, health check wiring,
  volumes, domain/TLS, gateway auth, request timeout).

## Edge cases

- EC-1701: A build context missing `data/workshop.duckdb` must fail loudly (`RUN test -s ...`), never
  silently produce an image with a zero-byte or absent database.
- EC-1702: Docker's `.dockerignore` last-match-wins ordering means the `!data/workshop.duckdb`
  re-include must appear after the general `*.duckdb` exclusion, or it has no effect — covered by a
  dedicated ordering assertion in `tests/test_deployment_contract.py`.
- EC-1703: If a future deployment target does provide a platform-native gateway/access-control layer,
  the app-level Basic Auth fallback stays redundant-but-harmless (opt-in only) rather than conflicting
  with it.
- EC-1704: `knowledge/generated`/`reviewed`/`artifacts` writes made through the running app are lost
  on Agent Runtime redeploy if no persistent volume is attached — this is accepted for this iteration
  and must be documented, not silently swallowed.

## Interfaces and contracts

### Deployment topology (provisional, pending console confirmation)

```
Internet -> GreenNode Agent Runtime ingress (TLS/? auth ?) -> Cerebro container :8000(?)
```

The `?` marks are resolved by walking the console checklist in
`docs/deployment-greennode-agent-runtime.md` before the first real deployment; if the port or health
check path differs from the current fixed `8000` / `GET /api/health/ready`, the Dockerfile's
`CMD`/`HEALTHCHECK` must be revisited to read a platform-assigned `$PORT`.

### Registry

- Local build/tag/push only: `docker build ...`, then `docker tag <local> vcr.vngcloud.vn/111480-abp114537/<image>:<tag>`, then `docker push vcr.vngcloud.vn/111480-abp114537/<image>:<tag>`.

## Security boundaries

- The baked-in database is read-only at every serving-path connection (unchanged from spec 016) and
  additionally mode `0444` plus a read-only container root filesystem, so it cannot be modified from
  inside a running container regardless of how it entered the image.
- The Basic Auth fallback (FR-1706/1707) is the only application-level auth perimeter available today
  for the Agent Runtime path; it is off by default and must be explicitly enabled via environment
  variables that are never baked into the image or logged.
- `/api/health` and `/api/health/ready` remain the sole unauthenticated routes and continue to
  redact all exception text, filesystem paths, and secret markers per spec 016's FR-1606.

## Persistence and lifecycle rules

- The database is immutable per deployment: updating it requires re-staging the file and rebuilding
  the image, never a live mutation.
- The knowledge bundle (business rules, entities, metrics, graph) follows the same
  "edit → rebuild → redeploy" model as the database for this iteration, since Agent Runtime's
  persistent-volume support is unconfirmed.

## Test design

| Test ID | Requirement / criterion | Level | Scenario | Expected result |
|---|---|---|---|---|
| T-1701 | FR-1701, AC-1701 | Static/image | Dockerfile lacks staged DB at build time | `docker build` fails at the `test -s` step |
| T-1702 | FR-1701, AC-1702 | Image | Built image, no `/data` mount | `/data/workshop.duckdb` present, `10001:10001 444`, readiness reports `ready` |
| T-1703 | FR-1704 | Image | `find` for stray `*.duckdb`/`*.duckdb.wal` outside the one expected path | None found |
| T-1704 | FR-1702, EC-1702 | Static | `.dockerignore` line ordering | `*.duckdb` index precedes `!data/workshop.duckdb` index |
| T-1705 | FR-1705 | Static | Workflow trigger block | No `push: tags: "v*"`; `workflow_dispatch:` present |
| T-1706 | FR-1706/1707, AC-1704 | API | Auth unset; auth set + no/wrong/correct credentials, across `/`, `/mcp`, and API routes | No-op when unset; 401/401/200 when set |
| T-1707 | FR-1706, AC-1704 | API | Auth set, hit `/api/health` and `/api/health/ready` | 200/200-or-503 without credentials |

## Constraints

- The image remains `linux/amd64`.
- The database file stays outside the repository (`.gitignore`d); this spec only changes how it
  enters the *Docker build context*, not source control.
- No code changes to `settings.py`'s DB resolution, `executor.py`, `chat.py`, or `source.py`.

## Assumptions

- The GreenNode Container Registry at `vcr.vngcloud.vn/111480-abp114537/...` accepts plain
  `docker login`/`docker tag`/`docker push`, matching what spec 016's release workflow already
  assumed for its registry mechanics.
- The demo database is fixed and changes rarely enough that a local rebuild-and-push per update is
  acceptable; if that assumption changes, CI-based DB staging (e.g. from a checksummed hosted asset)
  should be revisited as a follow-up, not retrofitted silently here.

## Open questions

The following are unresolved until the operator walks the GreenNode Agent Runtime console (see
`docs/deployment-greennode-agent-runtime.md`); none block the code changes in this spec, but each may
require a small follow-up change before the first real deployment:

- Does Agent Runtime pull a digest-pinned image, or only tag-based references?
- Fixed port `8000`, or a platform-assigned `$PORT` convention (spec 016 already flagged an
  "AgentBase port-8080" quirk as a non-goal worth re-checking here)?
- How are env vars/secrets injected (console fields vs. a mounted file)?
- Can a health check be pointed at `GET /api/health/ready`?
- Any persistent volume support at all?
- Does it terminate TLS / support a custom domain, or only a platform subdomain?
- Does it provide its own gateway/access-control auth (making FR-1706 redundant-but-safe)?
- Any hard request/gateway timeout that could cut off SSE (`/mcp`, generation-run streaming)?
