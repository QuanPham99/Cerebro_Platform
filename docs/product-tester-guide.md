# Cerebro Product Tester Guide

This guide verifies the complete local workflow: database discovery, relationship-aware OKF generation, bundle validation and activation, semantic exploration, MCP retrieval, and governed conversational querying.

## 1. Prepare the checkout

From the repository root:

```bash
git branch --show-current
git status -sb
```

Expected branch: `feature/okf-semantic-layer-sdd`. Do not clean or reset the tree; existing UI and documentation changes may be intentional.

Install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e '.[ai,dev]'
cd apps/web
npm ci
cd ../..
```

## 2. Configure the database and model

```bash
cp .env.example .env
```

Edit `.env`:

```dotenv
CEREBRO_DATABASE_PATH=/absolute/path/to/workshop.duckdb
CEREBRO_DATABASE_SCHEMA=main
CEREBRO_LLM_BASE_URL=https://api.openai.com/v1
CEREBRO_LLM_API_KEY=your-real-key
CEREBRO_LLM_MODEL=your-endpoint-supported-model
CEREBRO_LLM_RESPONSE_MODE=auto
```

For another OpenAI-compatible service, change only the base URL, API key, and model. The key stays on the server and must never be entered in the browser or committed.

```bash
cerebro doctor
```

Pass when the database is reachable, the scan reports 10 tables, 75 columns, and 11 declared relationships, and `ready_for_chat` is `true`. This command does not call the model.

## 3. Run the credential-free baseline

Before adding model credentials, or with the model key and model temporarily removed from `.env`, run:

```bash
cerebro scan --output artifacts/tester-offline/catalog.json
cerebro generate --output knowledge/generated/tester-offline-001
cerebro validate --bundle knowledge/generated/tester-offline-001
cerebro validate --bundle knowledge/bank-workshop
cerebro evaluate --bundle knowledge/bank-workshop
python3 -m pytest -q
cd apps/web
npm test -- --run
npm run build
cd ../..
```

Expected results:

- Scan says row sampling is disabled.
- Offline generation creates a new candidate rather than changing `knowledge/bank-workshop`.
- The structural candidate contains 10 tables and all 11 declared relationships. It intentionally has no AI-generated concepts or metrics.
- Both bundle validations are valid and all ten golden retrieval questions pass.
- Python tests, web tests, and the production build pass.

Use a new output directory such as `tester-offline-002` for each repeat. Existing non-empty candidate directories are never overwritten.

## 4. Generate and activate live OKF semantics

Restore the endpoint, key, and model in `.env`, then run:

```bash
cerebro doctor
cerebro generate --output knowledge/generated/tester-live-001
cerebro validate --bundle knowledge/generated/tester-live-001
```

Inspect the generated format:

```bash
find knowledge/generated/tester-live-001 -maxdepth 2 -type f | sort
sed -n '1,160p' knowledge/generated/tester-live-001/bundle.yaml
sed -n '1,200p' knowledge/generated/tester-live-001/relationships/account-customer.md
```

Pass when:

- The manifest records `generation_mode: live`, selected provider/model, run ID, timestamp, and source fingerprint.
- The three stages produced definitions, relationship proposals, and query semantics.
- Every relationship references real tables and columns.
- Declared joins remain present even when the model omits them.
- AI-only relationships carry `ai_proposed` provenance, confidence, evidence, and a review warning.

Activate only after inspecting validation:

```bash
cerebro activate --bundle knowledge/reviewed/tester-live-001
```

Activation updates the ignored `artifacts/active-bundle.json` pointer; it never overwrites the golden bundle. The CLI requires a server restart because it cannot hot-swap an already running process. The web workspace updates the pointer and live runtime together.

## 5. Test the website

```bash
./scripts/dev.sh
```

Open <http://127.0.0.1:5173>.

In **Semantic constellation**:

1. Open **Generate**, run the database-only pipeline, inspect the candidate, and choose **Approve and save version**.
2. Confirm that **Versions** opens with the approved graph highlighted alongside Golden Bank Workshop v0.2.0.
3. Use **View graph** to inspect a saved version without changing runtime behavior.
4. Choose **Set as default**, review the current-to-next confirmation, and verify that Semantic Constellation, Define, Text to SQL, HTTP, and MCP now report that semantic version.
5. Set the Golden entry as default to verify the baseline can be restored without deleting saved versions.

### Inspect, Define, and Pipeline rails

1. Open the default graph and a saved graph preview. Confirm the right rail contains only **Inspect** and **Define**; **Build** must not appear.
2. From a non-default approved graph, choose **Define** and confirm its name and version appear as the locked base graph.
3. Use the guided form to add an aggregate or ratio metric, including table/column bindings, compatible dimensions, grain, and optional filters. Confirm no raw measure JSON field appears.
4. Add a business rule to the same revision, then approve it with a reviewer and acknowledgement. Confirm the immutable definition revision opens in **Versions** without becoming the default.
5. Open **Semantic generation** before, during, and after a run. Confirm the right rail remains **Pipeline** only, including while the candidate graph is visible.

### Semantic constellation

1. Confirm the bundle chip shows the activated semantic version.
2. Search for `card fraud` and inspect its concept, tables, relationship, metric, and policy.
3. Confirm physical joins show cardinality and the inspector exposes grain, classifications, warnings, and provenance.
4. Open an OKF source document from the inspector.

### Text-to-SQL agents

1. Select **Text to SQL agents** from the workspace menu.
2. Confirm all five runtime checks are ready and the model name is visible without any key value.
3. Ask `How many customers are there by gender?`.
4. Expand **Generated SQL**, **Semantic evidence**, and **Agent trace**.
5. Confirm the SQL is a bounded `SELECT`, results appear in a table, the active semantic version is shown, and validation completed before the answer.
6. Ask `Which group is the largest?` and confirm it uses the browser conversation context.
7. Use **Clear chat** and confirm the conversation resets.

## 6. Test safety boundaries

| Test question | Expected behavior |
| --- | --- |
| `Show every customer name and email.` | Blocked because both fields are restricted. |
| `List every account balance.` | Blocked because confidential values require aggregation. |
| `What is the average account balance?` | Allowed as an aggregate. |
| `SELECT * FROM customers` | Blocked because wildcard selection is forbidden. |
| `Delete all customers.` | Blocked because writes are forbidden. |
| `Attach another database and query it.` | Blocked because external access is forbidden. |

A blocked response must explain the reason and must not return database rows. If the first SQL proposal is unsafe, only one repair attempt is allowed.

## 7. Test CLI, HTTP, and MCP

```bash
cerebro ask 'What percentage of card transactions are fraud?'
curl http://127.0.0.1:8000/api/runtime/status
curl -X POST http://127.0.0.1:8000/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"How many customers are there by gender?","history":[]}'
```

The chat response must include `answer`, `sql`, `columns`, `rows`, `semantic_version`, `evidence_ids`, `warnings`, and `trace`. Existing MCP tools remain at `http://127.0.0.1:8000/mcp/` and continue to retrieve grounding without executing SQL.

## 8. Test model and key switching

1. Stop `./scripts/dev.sh`.
2. Change `CEREBRO_LLM_BASE_URL`, `CEREBRO_LLM_API_KEY`, and `CEREBRO_LLM_MODEL` in `.env`.
3. Run `cerebro doctor`; confirm only non-secret endpoint/model metadata appears.
4. Restart `./scripts/dev.sh` and submit one chat question.
5. Confirm the new model appears and no source edit was required.

If strict JSON Schema is rejected, `auto` retries once with JSON-object mode and validates locally. Set `json_schema` to require strict support or `json_object` to skip negotiation.

## 9. Tester sign-off

| Area | Result | Evidence or issue |
| --- | --- | --- |
| Installation and doctor |  |  |
| Catalog scan |  |  |
| Offline candidate generation |  |  |
| Live semantic agents |  |  |
| Relationship generation |  |  |
| OKF validation and activation |  |  |
| Semantic graph |  |  |
| Governed database chat |  |  |
| Follow-up conversation |  |  |
| SQL safety cases |  |  |
| HTTP and MCP |  |  |
| Model/key switch |  |  |

Mark live generation and chat as **skipped**, not passed, when no real endpoint credentials were used.

## Troubleshooting

- `cerebro: command not found`: activate `.venv` and repeat `python3 -m pip install -e '.[ai,dev]'`.
- Database missing: set an absolute `CEREBRO_DATABASE_PATH`; the semantic graph still starts from the golden bundle, but chat remains blocked.
- Model not configured: set both `CEREBRO_LLM_API_KEY` and `CEREBRO_LLM_MODEL`, then restart.
- Candidate already exists: choose a new output directory; generation never overwrites evidence.
- Restore the golden bundle without deleting artifacts: choose Golden in **Versions**. For an operator-enforced default, set `CEREBRO_BUNDLE_PATH=knowledge/bank-workshop` in `.env` and restart; this intentionally locks UI default changes.
