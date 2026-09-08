# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Cerebro is a semantic-grounding platform: it scans a DuckDB source, uses three bounded LLM
stages to propose an Open Knowledge Format (OKF) knowledge bundle (entities, dimensions,
metrics, business rules, relationships, policies), validates/reviews/activates that bundle
deterministically, and serves it through a FastAPI + MCP backend and a React graph/chat
frontend. A governed chat runtime turns natural-language questions into validated, read-only
DuckDB SQL grounded in the active bundle. Full product vision and roadmap: `README.md`.

Google's OKF reference implementation is vendored unmodified as a git subtree at
`vendor/open-knowledge-format` (pinned to a specific upstream commit — never hand-edit it).

## Spec-driven development is mandatory

**Read `AI-rule.md` before making any non-trivial change.** This repo enforces strict
Discover → Specify → Design Tests → Implement → Verify → Report. In short:

- Every non-trivial feature/fix/refactor needs a spec in `specs/<feature>.md` (see `specs/README.md`
  for the existing numbered spec sequence and `specs/011-semantic-profile-v0.1.md` for the
  current core contract) with functional requirements and testable acceptance criteria, written
  *before* implementation — never backfilled after the fact.
- Tests must be designed against those acceptance criteria before the feature is considered done;
  add a regression test for every bug fix.
- Only trivial changes (typos, formatting, comment/doc-only, mechanical renames) skip the spec.
- Completion reports must state real verification evidence (commands actually run) and end with a
  compliance status (`COMPLIANT` / `PARTIALLY COMPLIANT` / `NON-COMPLIANT` / `BLOCKED`) per
  `AI-rule.md` §17. Never claim tests passed without running them.

## Commands

### Python backend

```bash
python3 -m pip install -e '.[ai,dev]'   # editable install + openai + pytest
cp .env.example .env                     # configure CEREBRO_DATABASE_PATH and (optional) LLM keys

pytest                                   # full suite (testpaths=tests, pythonpath=src via pyproject.toml)
pytest tests/test_chat.py                # one file
pytest tests/test_chat.py::test_name -v  # one test

cerebro doctor                           # validate local config without a model call
cerebro validate                         # validate upstream OKF syntax + Cerebro profile contracts
cerebro scan --config config/bank-source.yaml
cerebro generate --output knowledge/generated/<name>            # configured 3-stage generation
cerebro generate --source-mode database-only --database <path> --output knowledge/generated/<name>
cerebro review --bundle knowledge/generated/<name> --reviewer '<name>' --acknowledge-ai-risk
cerebro activate --bundle knowledge/reviewed/<name>
cerebro evaluate                         # 30 golden semantic-grounding questions
cerebro ask 'How many customers are there by gender?'
cerebro serve --host 127.0.0.1 --port 8000
```

No linter/type-checker is configured for the Python side — `pytest` is the only gate.

### Frontend (`apps/web`)

```bash
cd apps/web
npm install
npm run dev            # vite dev server (port 5173)
npm run build           # tsc -b && vite build; must succeed before `cerebro serve` uses dist/
npm run test             # vitest run
npx vitest run src/App.test.tsx     # one file
```

### Full local run

```bash
./scripts/dev.sh
```
Starts the API/MCP backend on :8000 (preferring `.venv/bin/python` if present) and Vite on
:5173; exits with the backend's own error instead of starting Vite if the API fails to boot.
For a production-style single-process run, build the frontend first (`npm run build` in
`apps/web`) then `cerebro serve`, which serves the built `apps/web/dist` alongside the API/MCP.

## Architecture

### Knowledge generation pipeline (production, not autonomous)

```
DuckDB (read-only) → catalog scan
  → SemanticInventoryAgent   (entities, dimensions, table purposes, classifications)
  → RelationshipAgent        (physical/entity endpoints, cardinality, evidence)
  → MetricRuleAgent          (metrics, compatible dimensions, business rules)
  → deterministic Semantic Linker → OKF Compiler → deterministic Validator
  → isolated candidate bundle → human review → explicit activation
  → active versioned OKF bundle → retrieval index + graph projection
```

This is a **fixed, bounded workflow**, not an agent loop: each of the three LLM stages
(`src/cerebro/semantic/agents.py`) makes exactly one typed provider call from catalog-only
metadata — it never sees source rows and cannot itself compile, validate, review, or activate.
Those later steps are deterministic code (`src/cerebro/semantic/linker.py`,
`src/cerebro/semantic/compiler.py`, `src/cerebro/semantic/validator.py`) or an explicit human
action (`cerebro review`, `cerebro activate` in `src/cerebro/generation.py`). Credential-free
runs still produce a valid structural candidate (no invented business meaning).

**Knowledge production is separated from knowledge consumption**: generating a candidate never
touches the active bundle; only `cerebro activate` swaps what `src/cerebro/bundle.py` serves to
retrieval, the graph, and chat.

### Runtime query path

`src/cerebro/chat.py` (`ChatOrchestrator`) retrieves grounding from the active bundle via
`src/cerebro/retrieval.py` (`SemanticRetriever`: type-aware lexical ranking + optional
embeddings + reciprocal-rank fusion + shortest governed join paths), proposes one SQL query,
validates and executes it read-only through `src/cerebro/executor.py` and `src/cerebro/sql_compiler.py`
against DuckDB only, and returns results with evidence/provenance. `src/cerebro/text2sql*.py`
implements the newer, more elaborate Text-to-SQL agent (spec 008) with its own budget/cache/
preflight layers, layered on top of the same grounding contract — see `specs/008-text-to-sql-agent.md`
before touching it, it's the largest spec in the repo.

Both HTTP (`src/cerebro/api.py`, FastAPI) and MCP (`retrieve_grounding`, `get_concept`,
`expand_neighborhood`) expose the same grounding contract — see the runtime interface table in
`README.md` for the full endpoint list.

### Semantic profile

`src/cerebro/semantic/profile.py` and `src/cerebro/semantic/models.py` define the Cerebro
Semantic Profile v0.1 layered on top of raw OKF v0.2 objects (`specs/011-semantic-profile-v0.1.md`
is the contract). The frontend graph (`apps/web/src/GraphView.tsx`,
`apps/web/src/profilePresentation.ts`) filters/presents by this `profile_kind` while preserving
the raw OKF `type`.

### Frontend structure (`apps/web/src`)

- `App.tsx` — top-level layout switching between the semantic constellation graph and the
  governed Text-to-SQL chat workspace, including preset questions, SQL/results, evidence,
  warnings, and agent trace.
- `GraphView.tsx` / `NodeNavigator.tsx` / `Inspector.tsx` — graph visualization, browsing, and
  concept detail panels, all reading from `/api/graph` and `/api/concepts/{id}`.
- `GenerationPanel.tsx` — drives `/api/generation/runs` (start a run, stream SSE progress via
  `/api/generation/runs/{id}/events`, review, activate) — this is "Semantic generation" in the UI.
- `DefinitionComposer.tsx` — manual/edited concept definitions.
- `api.ts` / `types.ts` — shared HTTP client, including `/api/chat`, and API type definitions
  mirroring the backend models.

### Configuration

Runtime config is env-var driven (`.env`, loaded by `src/cerebro/settings.py`) rather than
files under `config/`, which instead holds the declarative source description
(`config/bank-source.yaml`) used by `cerebro scan`/`generate`. Key vars:
`CEREBRO_DATABASE_PATH`, `CEREBRO_LLM_BASE_URL` / `_API_KEY` / `_MODEL` (generation stages), and
the separate `CEREBRO_API_KEY` / `CEREBRO_MODEL` / `CEREBRO_BASE_URL` triple for the Text-to-SQL
provider (spec 008). Run `cerebro doctor` after changing any of these — it never makes a model
call itself.

### Golden data

`knowledge/bank-workshop` is the checked-in, `stable`, reviewed golden bundle for the bank
workshop dataset — the default demo input, independent of any credentials. `knowledge/generated/`
holds unreviewed candidates from `cerebro generate`; treat it as disposable/gitignored scratch
output, not something to hand-edit.
