# Deploy Cerebro to GreenNode Agent Runtime

This runbook targets GreenNode **Container Registry** + **Agent Runtime** (a managed PaaS that runs
a pushed container image) — the two GreenNode products actually available on this account. There is
no vServer here, so there is no Docker Compose stack and no Caddy sidecar; the release image is
self-contained (the demo DuckDB database is baked in, see `Dockerfile`) and pushed manually from the
release operator's own machine. See `specs/017-greennode-agent-runtime-deployment.md` for the full
functional contract this runbook implements.

For the alternate self-managed-vServer path (not used today), see
[`deployment-greennode-vserver.md`](deployment-greennode-vserver.md).

## 1. Stage the database and build the image

The demo database is fixed and lives only on the release operator's machine, not in this repository
or in CI. Before every build:

```bash
cd /home/kwan/Documents/Cerebro_Platform
mkdir -p data
cp ~/Documents/Code_Beavers_Txt2Sql_WS/data/workshop.duckdb data/workshop.duckdb

docker build \
  --build-arg BUILD_VERSION=<release-version, e.g. v1.0.0> \
  --build-arg BUILD_REVISION=$(git rev-parse --short HEAD) \
  -t cerebro:local .
```

`data/` is gitignored — this staging step never gets committed. The build fails immediately (a
`test -s /data/workshop.duckdb` step) if the file wasn't staged or is empty, rather than producing a
silently broken image.

## 2. Verify locally before pushing anything

```bash
docker run --rm --entrypoint /bin/sh cerebro:local -c 'stat -c "%u:%g %a %s" /data/workshop.duckdb'
# expect: 10001:10001 444 <~75MB>

docker run --rm -d --name cerebro-verify -p 127.0.0.1:8000:8000 \
  --env CEREBRO_LLM_API_KEY=<your key> \
  --env CEREBRO_LLM_MODEL=<your model> \
  cerebro:local
curl -sf http://127.0.0.1:8000/api/health/ready | python3 -m json.tool
docker stop cerebro-verify
```

Expect `"status": "ready"` with no `/data` bind mount at all — the database is already inside the
image. Then run the full test suite and the updated image smoke test:

```bash
uv run --frozen --extra ai --extra dev python -m pytest -q
scripts/deploy/image-smoke.sh cerebro:local
```

If you want the Basic Auth fallback active (see §4), repeat the verification run with
`CEREBRO_BASIC_AUTH_USER`/`CEREBRO_BASIC_AUTH_PASSWORD` set and confirm `curl` without `-u` returns
401 while `curl -u user:pass` returns 200.

## 3. Push to GreenNode Container Registry

Only after step 2 passes:

```bash
docker tag cerebro:local vcr.vngcloud.vn/111480-abp114537/cerebro:<release-version>
docker push vcr.vngcloud.vn/111480-abp114537/cerebro:<release-version>
```

Confirm the image is pullable with the same credentials before wiring it into Agent Runtime:

```bash
docker pull vcr.vngcloud.vn/111480-abp114537/cerebro:<release-version>
```

## 4. Console checklist before creating the Agent Runtime app

Open the GreenNode console and confirm each of the following. These are genuinely unknown until
checked in the console — resolve them here, then come back and adjust the Dockerfile/spec if the
answer differs from the current assumption:

| Item | Current assumption | If different |
|---|---|---|
| Image reference | Pulls `vcr.vngcloud.vn/111480-abp114537/cerebro:<tag>` directly, tag- or digest-based | Adjust the tag/digest used at deploy time |
| Listening port | Fixed `8000` (image `EXPOSE 8000`) | If Agent Runtime requires a platform `$PORT`, change the Dockerfile `CMD`/`HEALTHCHECK` to `${PORT:-8000}` and re-verify (spec 016 already flagged an "AgentBase port-8080" quirk worth checking here) |
| Env var / secret injection | Console fields or a secrets manager, mapping every var in `deploy/cerebro.env.example` (`CEREBRO_LLM_API_KEY`, `CEREBRO_LLM_MODEL`, `CEREBRO_LLM_BASE_URL`, optionally `CEREBRO_BASIC_AUTH_USER`/`_PASSWORD`) | Follow whatever mechanism the console provides; never bake secrets into the image |
| Health check | Points at `GET /api/health/ready` on the chosen port | Configure per the console's probe format |
| Persistent volumes | Assumed unavailable — `knowledge/generated`/`reviewed`/`artifacts` are ephemeral, reset on every redeploy | If volumes exist and matter, consider mounting them (out of scope for this iteration) |
| Domain / TLS | Assumed platform-terminated (own subdomain or attachable custom domain) | Attach a custom domain if desired |
| Gateway / access control | Unknown whether Agent Runtime provides one | If yes, prefer it; keep the Basic Auth fallback (§4 above, off by default) as a safety net regardless |
| Request/gateway timeout | Unknown; watch for cutoffs on `/mcp` and generation-run SSE streaming | If a hard timeout exists, document it as a known limitation |

## 5. Deploy and verify live

Create the Agent Runtime app pointing at the pushed image, set the env vars from §4, and once it's
running:

```bash
curl -sf https://<agent-runtime-url>/api/health/ready | python3 -m json.tool
```

If Basic Auth is enabled, repeat with `-u <user>:<password>` and confirm an unauthenticated request
to the same URL (any route other than `/api/health` or `/api/health/ready`) returns 401.

## 6. Updating the knowledge bundle or the database later

Both the database and the checked-in `knowledge/bank-workshop` bundle are baked into the image for
this iteration — there is no live "edit in place" path on Agent Runtime. To ship a change: edit the
bundle (or re-stage a new `data/workshop.duckdb`), rebuild, re-verify (§2), re-push (§3), and
redeploy the new image tag in the Agent Runtime console.
