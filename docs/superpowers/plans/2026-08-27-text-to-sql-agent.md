# Text-to-SQL Agent Option B Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Revision:** 2026-08-30 Option B plan replacing the unconditional two-provider-stage design; amended to separate generation provenance from cache serving, add typed clarification and value-free literal references, and harden local typing/budgets/provider boundaries.

**Goal:** Build a production-balanced Text-to-SQL path in which an authorized metadata snapshot and versioned canonical question ground a value-free relational IR, the organizer model never emits SQL or physical literal values, local code deterministically resolves literal references and compiles parameterized SQL, and every query passes existing semantic, disclosure, engine, and execution gates.

**Architecture:** A normal cache miss starts with semantic-call capacity one and makes one schema-bound organizer/BTC call returning `RelationalQueryIR`, `GroundingRefusal`, `ClarificationRequest`, or `ComplexQueryPlan`. Only local validation of the complex plan produces `AcceptedComplexRoute`, performs the one-way budget transition to capacity two, and permits one `planned_ir` call. `GenerationRoute` is only `default_ir` or `planned_ir`; `cache_status` independently records disabled/miss/hit, so a cache hit preserves and revalidates the original generation route. A deterministic compiler resolves canonical-question/governed literal refs locally and creates SQL, while post-compile AST checks remain defense in depth. Scoped caching, cumulative call/transport/token/cost/deadline budgets, mandatory read-only `EXPLAIN`, and bounded execution apply to both generation routes and cache hits.

**Tech Stack:** Python 3.10+, Pydantic 2.13.5-compatible contracts, DuckDB `1.5.5`, `sqlglot` `30.17.0`, httpx 0.28.x, MCP `>=1.0,<2`, pytest 8.x. `sqlglot` is the only SQL parser and SQL AST builder.

**Spec:** [`specs/008-text-to-sql-agent.md`](../../../specs/008-text-to-sql-agent.md)

## Global Constraints

- The organizer model never emits SQL, embedded physical literal values, or free-form IR intent. No provider output model or prompt schema contains a SQL field or arbitrary literal-value field.
- A normal cache miss starts at semantic-call capacity one and makes exactly one `default_ir` call. Only local `AcceptedComplexRoute` can authorize the one-time transition to capacity two and exactly one `planned_ir` call. A valid cache hit makes zero calls; no path exceeds two.
- `GenerationRoute = Literal["default_ir", "planned_ir"]`. Cache service is represented only by `cache_status = disabled | miss | hit`; `"cache"` is never a generation route, and a hit preserves the original route.
- No semantic retry, execution-driven repair, parallel candidate generation, ensemble, or tournament selection is enabled by default. Transport may retry once within each semantic call's two-attempt cap.
- Every production request starts from raw question plus trusted `AuthorizationScope`; unauthorized objects are removed before ranking and graph expansion. Caller-supplied `GroundingResponse` or snapshot is rejected.
- `canonicalize_question` version `008.question.v1` applies Unicode NFC, Unicode-whitespace trim/collapse to one ASCII space, no case-folding, and no literal rewriting. Canonicalization version and canonical-question hash are cache/provenance inputs.
- `GroundingSnapshot`, value-free IR, compiler output, cache identity, and evidence hashes use canonical JSON helpers from `cerebro.provenance`. Governed literal values are explicitly authored semantic constants, never sampled/discovered database values.
- Provider output never authorizes execution. Clarification, snapshot, IR shape/type, router, literal resolution, compiler, AST, warning, metric, assumption, disclosure, `EXPLAIN`, and executor gates are local.
- A valid `ClarificationRequest` has exact canonical-question spans and at least two distinct relevant grounded/allowlisted candidates and terminates without fallback or engine contact. Invalid ambiguity is `invalid_clarification_request`.
- `sqlglot==30.17.0` is the only SQL parser/builder. Regex-based SQL authorization and string-concatenated identifiers are prohibited.
- DuckDB is pinned to `1.5.5`; do not use nonexistent `SET statement_timeout`.
- Every model-requested SQL literal—including expression/`IN`/`CASE` operands, relative-time amount, IR limit, and physical assumption operands—is a `QuestionLiteralRef` or `GovernedLiteralRef`. Only local resolution creates internal bound values.
- Compiler aliases, CTE names, SQL rendering, and parameter order are deterministic. `BoundParameter.value` remains executor-local and excluded from serialization.
- A cache hit revalidates canonical spans, snapshot membership, original generation-route restrictions, full IR type rules, recompiles locally, then reruns AST, policy, `EXPLAIN`, and execution checks. Cache payloads contain no canonical question text, resolved values, compiled SQL, parameters, results, or credentials.
- Hosted egress contains the canonical question and authorized metadata only. Source rows, sampled/discovered values, result values, resolved literals, raw SQL, raw exceptions, and credentials never enter prompts.
- `status = ok` always contains accepted value-free IR, concrete original generation route, independent cache status, a value-free `SQLArtifact`, successful `QueryResult`, lineage, disclosures, and budget usage.
- Text-to-SQL uses consumer-owned `Text2SQLGenerationProvider`/`ProviderGeneration` contracts. `text2sql.py`, `hosted_provider.py`, and `GoldenProvider` do not import `cerebro.enrichment`; existing enrichment `GenerationProvider` and `tests/test_enrichment.py` remain unchanged.
- Existing `src/cerebro/api.py` stays grounding-only: `/api/grounding` and MCP are advisory, metadata-only, and non-executable. This plan adds no unauthenticated SQL endpoint.
- Deployment budget defaults are exactly: semantic capacity `1` then at most `2` after accepted complex routing; `2` transport attempts per semantic call; `20_000 ms` provider timeout per attempt; `32_000` total input tokens; `8_000` total output tokens; `Decimal("0.50")` total USD; `120_000 ms` end-to-end.
- `knowledge/bank-workshop/` remains read-only. Do not implement specs 009 or 010 beyond their typed handoff contracts.
- Offline tests run without provider keys and with IPv4/IPv6 disabled; no path silently falls back to live mode.
- Real CSVs and the live organizer API remain explicit external gates; absence blocks live evidence but never weakens offline tests.
- Preserve the current dirty working tree. Before code execution, obtain owner approval for a branch or checkpoint; never use `reset --hard`, `clean`, force push, or an implicit stash.
- Commit steps below are executed only after explicit owner authorization. Without that authorization, stop at the verified diff for each task.

## File Structure

| File | Responsibility |
|---|---|
| `pyproject.toml` | exact DuckDB/sqlglot pins and existing dependency ranges |
| `scripts/text2sql_preflight.py` | offline/data/organizer readiness without claiming blocked capabilities |
| `scripts/load_duckdb.py` | atomic CSV materialization and value-free receipts |
| `src/cerebro/provenance.py` | canonical question/JSON helpers, content hashes, raw-name to semantic-table namespace helper |
| `src/cerebro/models.py` | strict scope, snapshot/governed literals, ambiguity, value-free IR, response, budget, lineage, and evidence contracts |
| `src/cerebro/retrieval.py` | authorization-first retrieval, graph expansion, governed semantic constants, snapshot freeze/hash |
| `src/cerebro/api.py` | existing advisory `/api/grounding` and MCP metadata retrieval; remains non-executable and never constructs `SQLGenerationRequest` |
| `src/cerebro/text2sql_provider.py` | consumer-owned generic `Text2SQLGenerationProvider` protocol and `ProviderGeneration[OutputT]` |
| `src/cerebro/enrichment.py` | existing enrichment-only provider contract; explicitly unchanged and not imported by Text-to-SQL |
| `src/cerebro/prompting.py` | strict metadata-only envelope construction and membership authorization |
| `src/cerebro/hosted_provider.py` | internal organizer gateway plus protocol-conforming guarded/cassette/scripted adapters; no enrichment dependency |
| `src/cerebro/complexity.py` | guarded complex-plan validation and opaque `AcceptedComplexRoute` decision |
| `src/cerebro/sql_compiler.py` | canonical-question/snapshot literal resolution and deterministic IR-to-parameterized-DuckDB-SQL compilation |
| `src/cerebro/text2sql_cache.py` | exact scoped cache keys and integrity-checked value-free IR plus original generation route |
| `src/cerebro/selfcheck.py` | clarification, snapshot/IR shape/type, post-compile AST, metric, warning, lineage, and disclosure gates |
| `src/cerebro/executor.py` | parameter-aware mandatory `EXPLAIN` and bounded read-only execution |
| `src/cerebro/text2sql.py` | request budget, generation-route/cache orchestration, gate ordering, and strict response assembly |
| `src/cerebro/evaluation.py` | trusted-scope/resolver composition, offline reference, and evidence-bound live baseline runners |
| `src/cerebro/cli.py` | trusted-scope `ask`, explicit `reference`, and explicit `baseline` composition |
| `tests/test_retrieval_api_mcp.py` | proves grounding-only HTTP/MCP output is advisory and cannot authorize execution |
| `tests/test_enrichment.py` | unchanged enrichment contract regression; verifies provider decoupling did not alter enrichment |
| `tests/text2sql_factories.py` | validated scope, canonical question/snapshot, value-free IR, compiled-query, and response builders shared by tests |

---

### Task 0: Lock compatibility and expose external readiness

Implements FR-701, FR-702, FR-703c, FR-724.

**Files:**
- Modify: `pyproject.toml`
- Modify: `src/cerebro/models.py` (receipt models only)
- Create: `src/cerebro/provenance.py`
- Create or modify: `scripts/text2sql_preflight.py`
- Create or modify: `tests/test_text2sql_preflight.py`

**Interfaces:**
- `canonical_json_bytes(model: BaseModel, *, exclude: set[str] = frozenset()) -> bytes`
- `sha256_file(path: Path) -> str`
- `source_manifest_sha256(manifest: SourceManifest) -> str`
- `manifest_table_id(raw_name: str) -> TableId`
- `check_preflight(...) -> PreflightReport`
- `ProviderCapabilityReceipt` records organizer provider/model/revision/schema mechanism and never a credential or response body.

- [ ] **Step 1: Assert exact dependency versions**

Add or update the dependency test:

```python
def test_text2sql_dependency_versions_are_exact():
    assert importlib.metadata.version("duckdb") == "1.5.5"
    assert importlib.metadata.version("sqlglot") == "30.17.0"
```

Run:

```bash
.venv/bin/python -m pytest tests/test_text2sql_preflight.py::test_text2sql_dependency_versions_are_exact -v
```

Expected: fail until `pyproject.toml` and the environment use the exact pins.

- [ ] **Step 2: Pin only the required compatibility surface**

Use these entries and preserve the existing MCP 1.x range:

```toml
"duckdb==1.5.5",
"sqlglot==30.17.0",
"mcp>=1.0,<2",
```

Run:

```bash
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -c 'import duckdb, sqlglot; print(duckdb.__version__, sqlglot.__version__)'
```

Expected: `1.5.5 30.17.0`.

- [ ] **Step 3: Write readiness tests that distinguish offline, data, and organizer gates**

```python
def test_missing_live_inputs_block_live_but_not_offline(tmp_path):
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
```

Run: `.venv/bin/python -m pytest tests/test_text2sql_preflight.py -v`

Expected: fail until typed readiness reporting exists.

- [ ] **Step 4: Implement canonical evidence helpers and sanitized preflight**

Use canonical JSON with sorted keys, UTF-8, `ensure_ascii=False`, and separators `(",", ":")`. `SourceManifest.tables` keeps raw bundle names; receipt tables use `table.<name>` only through `manifest_table_id`. Preflight checks existence and hash identity without printing path contents, keys, headers, prompts, or response bodies.

- [ ] **Step 5: Verify local blocked state honestly**

Run:

```bash
.venv/bin/python -m pytest tests/test_text2sql_preflight.py -v
.venv/bin/python scripts/text2sql_preflight.py
```

Expected locally: tests pass; command reports offline ready and names missing live prerequisites without exposing secrets.

- [ ] **Step 6: Commit only if authorized**

```bash
git add pyproject.toml src/cerebro/models.py src/cerebro/provenance.py scripts/text2sql_preflight.py tests/test_text2sql_preflight.py
git commit -m "chore: lock text-to-sql runtime prerequisites"
```

---

### Task 1: Preserve atomic DuckDB materialization

Implements FR-700, FR-701, AC-712.

**Files:**
- Modify: `scripts/load_duckdb.py`
- Modify: `tests/test_load_duckdb.py`

**Interfaces:**
- `preflight_csvs(csv_dir, bundle, manifest) -> SourceInventory`
- `load_csvs(csv_dir, db_path, bundle, manifest, receipt_dir=None) -> tuple[MaterializationReceipt, Path]`
- `_load_table(connection, source_file, ddl) -> int` remains injectable for rollback tests.

- [ ] **Step 1: Add a late-failure preservation test**

```python
def test_late_load_failure_preserves_existing_database(tmp_path, bundle, monkeypatch):
    target = tmp_path / "workshop.duckdb"
    con = duckdb.connect(str(target))
    con.execute("CREATE TABLE sentinel(value INTEGER)")
    con.execute("INSERT INTO sentinel VALUES (7)")
    con.close()
    before = target.read_bytes()

    csv_dir, manifest = write_bundle_csv_fixture(tmp_path, bundle)
    import scripts.load_duckdb as loader
    real = loader._load_table
    calls = {"count": 0}

    def fail_on_fifth(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 5:
            raise loader.LoadError("injected late failure")
        return real(*args, **kwargs)

    monkeypatch.setattr(loader, "_load_table", fail_on_fifth)
    with pytest.raises(loader.LoadError):
        loader.load_csvs(csv_dir, target, bundle, manifest)

    assert target.read_bytes() == before
    assert not list(tmp_path.glob(".workshop.duckdb.*.tmp"))
```

Run: `.venv/bin/python -m pytest tests/test_load_duckdb.py -v`

Expected: fail if loading mutates the target table-by-table.

- [ ] **Step 2: Preflight every file before opening the target**

Require exact file set, header order, declared casts, row counts, and configured hashes. Produce immutable `SourceInventory`; the mutation phase accepts only that inventory.

- [ ] **Step 3: Build in a temporary database and atomically replace**

Create the temporary database in the target directory, load in one transaction, verify schema/counts, close, hash, write a content-addressed receipt, then call `os.replace`. On any failure, remove only temporary outputs and leave an existing target unchanged.

- [ ] **Step 4: Verify materialization regressions**

Run:

```bash
.venv/bin/python -m pytest tests/test_load_duckdb.py -v
.venv/bin/python -m pytest tests/test_upstream_and_source.py -v
```

Expected: pass with synthetic fixtures. Report real-data-only checks as blocked when authoritative inputs are absent; never turn them into skips.

- [ ] **Step 5: Commit only if authorized**

```bash
git add scripts/load_duckdb.py tests/test_load_duckdb.py
git commit -m "fix: materialize DuckDB atomically"
```

---

### Task 2: Replace plan/SQL contracts with strict snapshot, ambiguity, and value-free relational IR contracts

Implements FR-704–FR-708, FR-715–FR-717, AC-713, AC-714.

**Files:**
- Modify: `src/cerebro/models.py`
- Rewrite: `tests/test_text2sql_contract.py`
- Create: `tests/text2sql_factories.py`

**Interfaces:**
- Set `TEXT2SQL_CONTRACT_VERSION = "008.v3"`, `GROUNDING_SNAPSHOT_VERSION = "008.grounding.v1"`, `RELATIONAL_IR_VERSION = "008.ir.v1"`, `QUESTION_CANONICALIZATION_VERSION = "008.question.v1"`, `LITERAL_SPAN_REGISTRY_VERSION = "008.literal-span.v1"`, and `EXPRESSION_TYPE_REGISTRY_VERSION = "008.types.v1"`.
- Define `GenerationRoute = Literal["default_ir", "planned_ir"]`; never include `"cache"`.
- Add strict/frozen `AuthorizationScope`, `GroundingSnapshot`, `SnapshotColumn(data_type=ScalarType)`, `SnapshotMetadataObject.metric_result_type`, `SnapshotGovernedLiteral`, `QuestionLiteralRef`, `GovernedLiteralRef`, all value-free IR expression/node variants including relative-time/limit literal refs, `RelationalQueryIR` without free-form `intent`, `ValidatedIR(generation_route=...)`, `ComplexQueryPlan`, non-Pydantic/non-wire `AcceptedComplexRoute`, `GroundingRefusal`, strict `AmbiguityCandidate` variants, `Ambiguity`, `ClarificationRequest`, value-free local `LiteralClarificationNeed`, `IRGenerationOutcome`, internal `CompiledQuery`, value-free public `SQLArtifact`, exact-default `BudgetLimits`, and `BudgetUsage`.
- `models.py` owns the module-private accepted-route constructor token/factory; direct construction with any caller token fails. Task 5 permits only `ComplexityRouter` to invoke that factory after validating a plan. `GuardedGenerationRequest` is a frozen local dataclass rather than a Pydantic/wire model, so dictionaries and provider payloads cannot carry route authority.
- `IRGenerationOutcome` is discriminated across `RelationalQueryIR | ComplexQueryPlan | GroundingRefusal | ClarificationRequest`.
- Replace response `plan` with `ir` and model-written SQL candidate with local `sql_artifact`; `ResponseBase` carries `ir_contract_version`, `type_registry_version`, `prompt_version`, `router_version`, `compiler_version`, and `checker_version`, `ResponseBase.generation_route` is `GenerationRoute | Literal["none"]`, `OkResponse` requires `GenerationRoute`, and `cache_status` independently carries `disabled | miss | hit`.
- `CompiledQuery.parameters` remain internal and `BoundParameter.value` is excluded from serialization. Cached/public IR, assumptions, traces, and evidence contain refs only, never resolved values.
- `AttemptRecord.stage` accepts `snapshot`, `cache`, `default_ir`, `clarification`, `complexity`, `planned_ir`, `literal_resolution`, `compile`, `ast_check`, `engine_validation`, and `execution` plus `provider_transport`.
- Retain typed warning refs, disclosures, lineage, materialization, and live evidence contracts; change every SQL-bound physical operand in direction/status/grain/snapshot assumptions to `LiteralRef`.

- [ ] **Step 1: Write illegal-state contract tests**

```python
def test_ok_requires_concrete_generation_route_and_independent_cache_status(valid_response_base):
    payload = {
        **valid_response_base,
        "status": "ok",
        "generation_route": "none",
        "cache_status": "hit",
    }
    with pytest.raises(ValidationError):
        TypeAdapter(SQLGenerationResponse).validate_python(payload)


def test_cache_is_never_a_generation_route(valid_response_base):
    with pytest.raises(ValidationError):
        TypeAdapter(SQLGenerationResponse).validate_python({
            **valid_response_base,
            "status": "ok",
            "generation_route": "cache",
            "cache_status": "hit",
        })


def test_provider_outcome_schema_has_no_sql_value_or_free_form_intent():
    schema = json.dumps(TypeAdapter(IRGenerationOutcome).json_schema(), sort_keys=True)
    assert '"sql"' not in schema
    assert '"intent"' not in schema
    assert '"value"' not in json.dumps(LiteralExpression.model_json_schema(), sort_keys=True)


def test_literal_expression_rejects_embedded_value():
    with pytest.raises(ValidationError):
        LiteralExpression.model_validate({
            "kind": "literal", "value": "London", "data_type": "string"
        })


def test_clarification_contract_has_only_spans_and_typed_candidates():
    schema = json.dumps(ClarificationRequest.model_json_schema(), sort_keys=True)
    for forbidden in ('"message"', '"reasoning"', '"description"', '"raw_value"'):
        assert forbidden not in schema
    with pytest.raises(ValidationError):
        ClarificationRequest.model_validate({
            "outcome": "clarification_request",
            "ambiguities": [{
                "ambiguity_id": "target",
                "start": 0,
                "end": 6,
                "candidates": [{"kind": "object", "object_id": "table.accounts"}],
                "message": "Which one?",
            }],
        })


def test_local_literal_clarification_is_typed_and_value_free(valid_response_base):
    response = RefusedResponse.model_validate({
        **valid_response_base,
        "status": "refused",
        "reason": "clarification_required",
        "ambiguities": [],
        "literal_needs": [{
            "kind": "literal_need",
            "issue": "unparseable_question_literal",
            "expected_type": "integer",
            "target_column": None,
            "literal_ref": {"kind": "question", "start": 10, "end": 14, "data_type": "integer"},
        }],
    })
    serialized = response.model_dump_json()
    assert '"literal_needs"' in serialized
    assert '"value"' not in serialized


def test_bound_parameter_value_never_serializes():
    parameter = BoundParameter(position=1, data_type="string", value="private")
    assert "private" not in parameter.model_dump_json()


def test_budget_contract_defaults_are_exact():
    limits = BudgetLimits.defaults()
    assert limits.initial_semantic_call_capacity == 1
    assert limits.planned_semantic_call_capacity == 2
    assert limits.max_transport_attempts_per_semantic_call == 2
    assert limits.provider_timeout_ms_per_attempt == 20_000
    assert limits.max_input_tokens == 32_000
    assert limits.max_output_tokens == 8_000
    assert limits.max_cost_usd == Decimal("0.50")
    assert limits.end_to_end_deadline_ms == 120_000


def test_snapshot_types_and_metric_result_type_are_strict():
    payload = valid_snapshot().model_dump(mode="json")
    metric = next(item for item in payload["objects"] if item["object_type"] == "metric")
    del metric["metric_result_type"]
    with pytest.raises(ValidationError):
        GroundingSnapshot.model_validate(payload)


def test_accepted_complex_route_cannot_be_caller_constructed(snapshot):
    plan = complex_window_plan(snapshot)
    with pytest.raises(TypeError):
        AcceptedComplexRoute(
            snapshot_hash=snapshot.snapshot_hash,
            plan_hash=complex_plan_sha256(plan),
            plan=plan,
            _router_token=object(),
        )


def test_refused_response_rejects_sql_artifact(valid_response_base):
    with pytest.raises(ValidationError):
        TypeAdapter(SQLGenerationResponse).validate_python({
            **valid_response_base,
            "status": "refused",
            "reason": "missing_grounding",
            "sql_artifact": {
                "sql": "SELECT 1",
                "sql_sha256": "0" * 64,
                "parameter_count": 0,
                "parameter_types": [],
                "ir_hash": "1" * 64,
                "compiler_version": "test",
                "dialect": "duckdb",
            },
        })
```

Run: `.venv/bin/python -m pytest tests/test_text2sql_contract.py -v`

Expected: fail against the v2 plan/SQL-stage contract.

- [ ] **Step 2: Implement strict discriminated expression and node unions**

Use `ConfigDict(extra="forbid")` on all serializable boundary models and `ConfigDict(extra="forbid", frozen=True)` on immutable evidence. Keep `AcceptedComplexRoute` and `GuardedGenerationRequest` outside Pydantic serialization. `IRNode` is discriminated by `kind`; `IRGenerationOutcome` by `outcome`; public response by `status`. Validate identifiers at parse time, normalize every column type to `ScalarType`, require `formula` plus `metric_result_type` exactly on metric snapshot objects, reject metric-only fields on non-metrics, require every governed constant value to validate against its declared `ScalarType`, and reject recursive expression depth above the configured contract maximum.

The first supported node set is exact:

```python
SUPPORTED_DEFAULT_NODE_KINDS = frozenset({
    "scan", "join", "filter", "aggregate", "project", "sort", "limit",
})
SUPPORTED_COMPLEX_NODE_KINDS = frozenset({"window", "set_operation"})
```

The expression union is exact: `column`, `metric`, ref-only `literal`, allowlisted `function`, allowlisted `binary`, bounded `in`, typed `case`, and `relative_time(data_max, amount_ref, unit, boundaries)`. `QuestionLiteralRef(kind="question", start, end, data_type)` and `GovernedLiteralRef(kind="governed", literal_id)` form a discriminated `LiteralRef`; neither stores a resolved value. `LimitNode.count`, relative-time amount, window offsets, minimum-group guards, and every physical assumption operand use `LiteralRef`. `RelationalQueryIR` has no `intent`, raw SQL, expression SQL, or arbitrary prose/value field. Recursive model references are rebuilt explicitly after all variants are declared, and validation enforces depth and collection-size limits.

- [ ] **Step 3: Make generation-mode and ambiguity states impossible to mix**

`GuardedGenerationRequest` is a frozen, slots-based local dataclass with explicit `__post_init__` checks: `mode="default_ir"` rejects `accepted_complex_route`; `mode="planned_ir"` requires an authentic snapshot/plan-bound `AcceptedComplexRoute`; `mode="provider_probe"` requires an empty probe snapshot and rejects prior violations. It exposes no `model_validate`, dict, or JSON construction path. `ValidatedIR` and `CachedGeneration` require `accepted_complex_plan_hash` exactly for `planned_ir`, reject it for `default_ir`, and require key/payload/validated routes to agree. `IRGenerationOutcome` includes strict `ClarificationRequest`; each `Ambiguity` uses only canonical-question code-point offsets and at least two discriminated object/relationship/governed-literal/grain/operator candidates, with no text/value field. `RefusedResponse(reason="clarification_required")` requires at least one locally validated `Ambiguity` or `LiteralClarificationNeed`: model ambiguity populates only `ambiguities`, while local missing/invalid/unparseable/ungrounded/invented literal handling populates only value-free `literal_needs`. Other refusal reasons reject both fields. A first-call clarification records `generation_route="default_ir"`, while pre-generation failures alone use `none`. `ResponseBase.generation_route` and `cache_status` remain independent, and no model accepts a `PromptEnvelope` from a caller.

- [ ] **Step 4: Create validated shared factories**

`tests/text2sql_factories.py` exports:

```python
valid_scope(allowed_object_ids=None, policy_version="policy.v1")
canonical_question(text="Show accounts in London")
valid_snapshot(scope=None, governed_literals=())
question_literal_ref(question, token="London", data_type="string")
governed_literal_ref(literal_id)
minimal_ir(snapshot=None)
branch_volume_ir(snapshot=None)
relative_growth_ir(snapshot=None, question="... last 24 months ...")
complex_window_plan(snapshot=None)
valid_clarification_request(question, snapshot)
sensitive_ir(snapshot=None, question="Which five ...")
compiled_query(ir=None)
sql_artifact(compiled=None)
valid_request(scope=None)
valid_response_base(snapshot=None)
codes(violations)
```

Every factory returns a normally validated model; none uses `model_construct`, embeds a literal value/free-form intent in IR, or hard-codes warning hashes. Helpers derive code-point spans from the exact canonical question and fail if the requested token is absent or non-unique. Task 5 adds accepted-route factories only after `ComplexityRouter` exists; no test helper constructs route authority directly.

- [ ] **Step 5: Run model regressions**

```bash
.venv/bin/python -m pytest tests/test_text2sql_contract.py tests/test_bundle_validation.py -v
```

Expected: pass. Old orchestration tests may remain red until Task 10; do not reintroduce v2 fields to satisfy them.

- [ ] **Step 6: Commit only if authorized**

```bash
git add src/cerebro/models.py tests/test_text2sql_contract.py tests/text2sql_factories.py
git commit -m "refactor: define snapshot and relational IR contracts"
```

---

### Task 3: Build authorization-first retrieval and immutable grounding snapshots

Implements FR-704, FR-704b, AC-700, AC-706, AC-715.

**Files:**
- Modify: `src/cerebro/retrieval.py`
- Modify: `src/cerebro/models.py`
- Modify: `src/cerebro/provenance.py`
- Verify boundary without adding SQL behavior: `src/cerebro/api.py`
- Create: `tests/test_grounding_snapshot.py`
- Modify: `tests/test_retrieval_api_mcp.py`
- Modify: `tests/test_text2sql_contract.py`

**Interfaces:**
- `canonicalize_question(question: str) -> str` uses version `008.question.v1`: Unicode NFC, trim Unicode whitespace, collapse each internal Unicode-whitespace run to one ASCII space, no case-fold/literal rewrite.
- `canonical_question_sha256(canonical_question: str) -> Sha256`
- `GroundingResolver.resolve(canonical_question: str, scope: AuthorizationScope, dialect: str) -> GroundingSnapshot`
- `authorized_candidates(scope) -> tuple[SemanticObject, ...]` filters before lexical/vector ranking.
- `expand_authorized(seed_ids, scope, depth=1) -> tuple[str, ...]` never traverses into an unauthorized node.
- `authorization_scope_sha256(scope_without_hash) -> str` and `grounding_snapshot_sha256(snapshot_without_hash) -> str`
- `SnapshotGovernedLiteral(literal_id, data_type, value, source_object_id)` represents only explicit authored semantic constants; no discovery/sample path may populate it.
- Production `SQLGenerationRequest` carries only raw question, trusted `AuthorizationScope`, dialect, and trusted row cap—never caller-supplied `GroundingResponse` or snapshot. The resolver recomputes scope/snapshot hashes and rejects mismatches.
- Existing `/api/grounding` and MCP contracts continue returning advisory metadata-only `GroundingResponse`; they neither construct `SQLGenerationRequest` nor authorize execution.

- [ ] **Step 1: Prove authorization happens before retrieval and expansion**

```python
def test_unauthorized_high_score_object_never_enters_snapshot(retriever):
    scope = valid_scope(allowed_object_ids={"table.accounts"})
    snapshot = GroundingResolver(retriever).resolve(
        "show restricted customer identity details", scope, "duckdb"
    )
    ids = {item.object_id for item in snapshot.objects}
    assert ids == {"table.accounts"}
    assert "table.customers" not in ids


def test_graph_expansion_cannot_cross_scope(retriever):
    scope = valid_scope(allowed_object_ids={"table.transactions"})
    snapshot = GroundingResolver(retriever).resolve(
        "transactions by branch", scope, "duckdb"
    )
    assert {item.object_id for item in snapshot.objects} <= scope.allowed_object_ids
```

Run: `.venv/bin/python -m pytest tests/test_grounding_snapshot.py -v`

Expected: fail if filtering occurs after ranking or graph expansion.

- [ ] **Step 2: Add canonical question and snapshot identity tests**

```python
def test_canonicalize_question_is_versioned_unicode_nfc_and_whitespace_only():
    assert canonicalize_question("  Revenu\u0065\u0301\u00a0 for\t Q1  ") == "Revenu\u00e9 for Q1"
    assert canonicalize_question("Q1") != canonicalize_question("q1")


def test_snapshot_hash_is_stable_for_identical_inputs(resolver, scope):
    question = canonicalize_question("transaction volume")
    first = resolver.resolve(question, scope, "duckdb")
    second = resolver.resolve(question, scope, "duckdb")
    assert first.snapshot_hash == second.snapshot_hash
    assert first == second


def test_forged_authorization_scope_hash_is_rejected(resolver, scope):
    forged = scope.model_copy(update={"authorization_scope_hash": "f" * 64})
    with pytest.raises(AuthorizationScopeIntegrityError):
        resolver.resolve(canonicalize_question("transaction volume"), forged, "duckdb")


@pytest.mark.parametrize("mutation", [
    "scope", "policy_version", "retrieval_config", "semantic_version",
    "canonicalization_version", "literal_registry", "object",
])
def test_snapshot_identity_changes_for_security_relevant_mutation(
    resolver, scope, mutation, mutate_snapshot_input,
):
    question = canonicalize_question("transaction volume")
    first = resolver.resolve(question, scope, "duckdb")
    changed_resolver, changed_scope = mutate_snapshot_input(resolver, scope, mutation)
    second = changed_resolver.resolve(question, changed_scope, "duckdb")
    assert first.snapshot_hash != second.snapshot_hash
```

- [ ] **Step 3: Build the metadata-only snapshot field-by-field**

Include IDs, descriptions, normalized declared column/metric result types and classifications, relationship endpoints, metric formulas, warning refs/controls, policy IDs, ranking evidence, semantic/policy/scope/retrieval/canonicalization/literal-registry/type-registry versions, dialect capability, and explicitly authored governed literals with stable IDs/types/source-object IDs. Exclude rows, samples, discovered database values, database paths, vector embeddings, secrets, raw document bodies not explicitly allowlisted, and arbitrary provenance dictionaries. A governed-literal loader accepts only bundle-authored constants and has no database connection.

Compute and verify the scope hash over frozen `AuthorizationScope` payload with `authorization_scope_hash` excluded. Compute the snapshot hash over the frozen snapshot payload with `snapshot_hash` excluded, then construct the final model. Revalidating either model from canonical bytes must reproduce its hash; a supplied mismatch fails before retrieval.

- [ ] **Step 4: Reject caller grounding while preserving the advisory API**

Do not add or modify an executable SQL endpoint in `api.py`. Keep `/api/grounding` and the MCP tools metadata-only and returning `GroundingResponse`. Add contract/API tests proving that output cannot validate as `SQLGenerationRequest` and cannot be passed to `Text2SQLAgent.run`; the only production request shape is question plus trusted `AuthorizationScope`. The actual constructors currently in `cli.py` and `evaluation.py` still use question plus `retriever.grounding(...)`; migrate those two call sites in Task 11 through centralized resolver composition, not through the HTTP/MCP response.

```python
def test_advisory_grounding_cannot_authorize_sql(client, trusted_scope):
    grounding = client.post("/api/grounding", json={"question": "volume"}).json()
    with pytest.raises(ValidationError):
        SQLGenerationRequest.model_validate({
            "question": "volume",
            "authorization_scope": trusted_scope.model_dump(mode="json"),
            "grounding": grounding,
        })
```

- [ ] **Step 5: Verify retrieval and API contracts**

```bash
.venv/bin/python -m pytest tests/test_grounding_snapshot.py tests/test_retrieval_api_mcp.py tests/test_text2sql_contract.py -v
```

Expected: pass with no provider or database; HTTP/MCP remain grounding-only and `GroundingResponse` is rejected by the execution request contract.

- [ ] **Step 6: Commit only if authorized**

```bash
git add src/cerebro/retrieval.py src/cerebro/models.py src/cerebro/provenance.py tests/test_grounding_snapshot.py tests/test_retrieval_api_mcp.py tests/test_text2sql_contract.py
git commit -m "feat: freeze authorized grounding snapshots"
```

---

### Task 4: Replace prompt strings with a guarded organizer model gateway

Implements FR-702, FR-703, FR-703a–FR-703d, AC-707, AC-711.

**Files:**
- Create: `src/cerebro/text2sql_provider.py`
- Create: `src/cerebro/prompting.py`
- Modify: `src/cerebro/hosted_provider.py`
- Modify: `scripts/text2sql_preflight.py`
- Verify unchanged: `src/cerebro/enrichment.py`
- Rewrite: `tests/test_prompt_egress.py`
- Modify: `tests/test_hosted_provider.py`
- Verify unchanged regression: `tests/test_enrichment.py`
- Create or modify: `tests/conftest.py`

**Interfaces:**
- Consumer-owned `@runtime_checkable Text2SQLGenerationProvider(Protocol)` defines `generate(request: GuardedGenerationRequest, output_adapter: TypeAdapter[OutputT]) -> ProviderGeneration[OutputT]` plus provider/model/revision/schema-mechanism identity.
- Generic `ProviderGeneration[OutputT]` records validated output, sanitized transport attempts, and usage.
- Internal `OrganizerModelGateway.generate(schema_name, prompt, output_adapter: TypeAdapter[OutputT]) -> ProviderGeneration[OutputT]` owns transport only.
- `GuardedProvider.generate(request: GuardedGenerationRequest, output_adapter: TypeAdapter[OutputT]) -> ProviderGeneration[OutputT]` is the only provider boundary later consumed by `Text2SQLAgent`; hosted/scripted/cassette adapters conform structurally.
- `_build_prompt_envelope`, `_authorize_prompt_envelope`, and `_render_prompt` remain module-private.
- Typed exceptions: `ProviderConfigurationError`, `ProviderUnavailable`, `ProviderRejected`, `EgressBlocked`.
- `probe_provider_schema(...) -> ProviderCapabilityReceipt` performs one metadata-only schema call.
- `src/cerebro/hosted_provider.py` and Text-to-SQL transport doubles must not import `cerebro.enrichment`; enrichment's existing `GenerationProvider` stays unchanged. Task 10 migrates `text2sql.py`, and Task 11 migrates `GoldenProvider`, to this protocol.

- [ ] **Step 1: Write the no-SQL and forged-input egress tests**

```python
@pytest.mark.parametrize("forged", [
    {"canonical_question": "q"},
    "rendered prompt",
    PromptEnvelope(
        mode="default_ir",
        canonical_question="q",
        snapshot=prompt_snapshot_view(valid_snapshot()),
    ),
])
def test_guard_rejects_caller_built_payloads(forged):
    inner = ScriptedProvider([])
    guarded = GuardedProvider(inner)
    with pytest.raises(EgressBlocked):
        guarded.generate(forged, TypeAdapter(IRGenerationOutcome))
    assert inner.calls == []


def test_all_provider_output_schemas_exclude_sql_and_embedded_literal_values():
    for output_type in (IRGenerationOutcome, RelationalQueryIR, ProviderProbe):
        schema = json.dumps(TypeAdapter(output_type).json_schema(), sort_keys=True)
        assert '"sql"' not in schema
    assert '"value"' not in json.dumps(LiteralExpression.model_json_schema(), sort_keys=True)
    assert '"intent"' not in json.dumps(RelationalQueryIR.model_json_schema(), sort_keys=True)


def test_text2sql_provider_protocol_is_consumer_owned():
    source = inspect.getsource(cerebro.hosted_provider)
    assert "cerebro.enrichment" not in source
    assert "from .enrichment" not in source
    assert isinstance(GuardedProvider(ScriptedProvider([])), Text2SQLGenerationProvider)
```

Run: `.venv/bin/python -m pytest tests/test_prompt_egress.py tests/test_hosted_provider.py -v`

Expected: fail against the rendered-string provider boundary.

- [ ] **Step 2: Add value and diagnostic canaries**

Place source-row, numeric-PII, transformed/discovered-value, locally resolved question/governed literal, result, credential, raw-SQL, raw-exception, and free-text-subject canaries in local-only structures. Capture the actual inner prompt and assert every forbidden canary is absent. Separately prove an explicitly authored governed constant selected into `GroundingPromptView` is allowed while a lookalike value not identified by a snapshot `literal_id` is blocked. A valid-looking but nonmember table/relationship/metric/policy/column/literal subject must raise `EgressBlocked` with no additional call.

- [ ] **Step 3: Build and authorize strict envelopes field-by-field**

`GuardedProvider` accepts an exact `GuardedGenerationRequest` instance. It creates `GroundingPromptView` from allowlisted snapshot fields, includes the exact canonical question, maps local violations through a fixed code/remediation table, reduces output positions to a phase token, revalidates object/relationship/governed-literal membership, renders, and contacts the inner gateway. `planned_ir` accepts only an authentic `AcceptedComplexRoute`, recomputes its current plan hash and snapshot binding immediately before rendering, and rejects any post-validation mutation; it never trusts a caller-supplied plan. Do not serialize models then delete forbidden fields.

- [ ] **Step 4: Implement organizer-compatible configuration without vendor lock-in**

Read runtime values from `CEREBRO_BASE_URL`, `CEREBRO_API_KEY`, `CEREBRO_MODEL`, and `CEREBRO_MODEL_REVISION`. Keep the transport OpenAI-compatible only behind `OrganizerModelGateway`; no orchestration code imports a vendor SDK or assumes a public OpenAI endpoint. Record the provider-declared schema mechanism and token/cost usage when returned.

- [ ] **Step 5: Separate transport attempts from semantic calls**

Bound retryable HTTP statuses and timeouts with exponential backoff to exactly two transport attempts per semantic call and `20_000 ms` per attempt by deployment default. Return sanitized `ProviderGeneration(output, transport_attempts, usage)`. Schema validation errors propagate to orchestration as semantic decoding failures; non-retryable HTTP rejection never becomes `provider_unavailable`. Usage is cumulative in the request budget across both possible semantic calls.

- [ ] **Step 6: Add the offline network guard and capability probe**

Block `AF_INET`/`AF_INET6` when `CEREBRO_TEST_NO_NETWORK=1` while preserving `AF_UNIX`. The capability probe uses `mode="provider_probe"`, one empty metadata snapshot, and `ProviderProbe(ok=True)`; it writes a content-addressed receipt without prompt or response content.

Run:

```bash
CEREBRO_TEST_NO_NETWORK=1 env -u CEREBRO_API_KEY -u OPENAI_API_KEY -u CEREBRO_MODEL .venv/bin/python -m pytest tests/test_prompt_egress.py tests/test_hosted_provider.py tests/test_enrichment.py -v
! grep -R "from \.enrichment\|from cerebro\.enrichment\|import cerebro\.enrichment" src/cerebro/hosted_provider.py src/cerebro/text2sql_provider.py
```

Expected: pass, open no external socket, find no Text-to-SQL enrichment import, and leave the existing enrichment tests/contract unchanged.

- [ ] **Step 7: Commit only if authorized**

```bash
git add src/cerebro/text2sql_provider.py src/cerebro/prompting.py src/cerebro/hosted_provider.py scripts/text2sql_preflight.py tests/conftest.py tests/test_prompt_egress.py tests/test_hosted_provider.py
git commit -m "fix: guard organizer model metadata egress"
```

---

### Task 5: Validate typed relational IR, ambiguity, and complex-plan escalation

Implements FR-703b, FR-704a, FR-707, FR-709, FR-710, FR-715, FR-716, AC-704, AC-709, AC-714.

**Files:**
- Create: `src/cerebro/complexity.py`
- Modify: `src/cerebro/selfcheck.py`
- Create: `tests/test_selfcheck_ir.py`
- Create: `tests/test_complexity_router.py`
- Modify: `tests/text2sql_factories.py`

**Interfaces:**
- `GroundingIndex.from_snapshot(snapshot) -> GroundingIndex`
- `ExpressionTypeRegistry(version="008.types.v1")` normalizes snapshot scalar types and metric result types and owns function/operator signatures.
- `validate_ir(ir, snapshot, canonical_question, generation_route) -> IRValidationResult`
- `IRValidationResult(validated_ir: RelationalQueryIR | None, violations: tuple[CheckViolation, ...])`
- `validate_clarification(request, canonical_question, snapshot) -> ClarificationDecision`
- `ComplexityRouter.validate(plan, snapshot) -> ComplexityDecision`; acceptance calls the module-private factory and returns a non-Pydantic/non-wire `AcceptedComplexRoute` bound to plan/snapshot hashes, never a mutable route string/call count. Direct constructor calls and model/dict/JSON validation paths are rejected.
- Task 5 extends `tests/text2sql_factories.py` with `accepted_complex_route(snapshot=None, plan=None)` and `foreign_accepted_route(snapshot=None)`; both run `ComplexityRouter`, and neither constructs capability fields directly.
- Initial complex allowlist: `window.period_over_period.v1` and `set_operation.safe_binary.v1`.
- `check_grounding_refusal(refusal, snapshot) -> RefusalDecision` verifies absence locally.

- [ ] **Step 1: Add graph-shape and containment failures**

```python
@pytest.mark.parametrize("mutation, expected", [
    ("duplicate_node", "duplicate_ir_node"),
    ("cycle", "cyclic_ir"),
    ("orphan", "orphan_ir_node"),
    ("missing_root", "invalid_ir_root"),
    ("unauthorized_table", "ungrounded_ir_reference"),
    ("disconnected_join", "disconnected_ir"),
])
def test_invalid_ir_fails_before_compilation(snapshot, canonical_question, mutation, expected):
    ir = mutate_ir(minimal_ir(snapshot), mutation)
    result = validate_ir(
        ir, snapshot, canonical_question, generation_route="default_ir"
    )
    assert expected in codes(result.violations)
    assert result.validated_ir is None
```

Run: `.venv/bin/python -m pytest tests/test_selfcheck_ir.py -v`

Expected: fail until IR validation exists.

- [ ] **Step 2: Validate membership, dataflow, and expression types recursively**

Traverse every node and expression with an explicit depth cap. Validate all column, table, metric, relationship, warning, assumption, policy, disclosure, literal, sort, group, and output references against `GroundingIndex` and the current canonical question. Require one reachable root, unique node IDs, acyclicity, no orphans, connected relationship paths, exact node input arity, references available from each node's input, unique aliases in every output scope, and unique root outputs.

Use only `ExpressionTypeRegistry("008.types.v1")` to normalize snapshot scalar/metric result types and check boolean filters/`CASE` conditions; operator and `IN` compatibility; function arity/signatures; aggregate-only placement; window-only/planned-route placement; no aggregate in filters/group keys; no window outside `WindowNode`; no nested aggregate/window; and set output arity plus position-by-position type compatibility.

```python
@pytest.mark.parametrize("mutation, expected", [
    ("numeric_filter", "non_boolean_filter"),
    ("wrong_function_arity", "invalid_function_signature"),
    ("aggregate_in_filter", "invalid_aggregate_placement"),
    ("window_in_project", "invalid_window_placement"),
    ("nested_window_aggregate", "nested_aggregate_or_window"),
    ("duplicate_alias", "duplicate_output_alias"),
    ("wrong_node_input_count", "invalid_node_arity"),
    ("set_arity_mismatch", "set_output_arity_mismatch"),
    ("set_incompatible_types", "set_output_type_mismatch"),
])
def test_named_type_failures_stop_before_compilation(
    snapshot, canonical_question, mutation, expected,
):
    result = validate_ir(
        mutate_ir(minimal_ir(snapshot), mutation),
        snapshot,
        canonical_question,
        generation_route="planned_ir" if mutation.startswith("set_") else "default_ir",
    )
    assert codes(result.violations) == {expected}
    assert result.validated_ir is None
```

- [ ] **Step 3: Prove the default generation route rejects complex nodes**

```python
@pytest.mark.parametrize("kind", ["window", "set_operation"])
def test_default_route_cannot_smuggle_complex_node(snapshot, canonical_question, kind):
    ir = ir_with_complex_node(snapshot, kind)
    result = validate_ir(
        ir, snapshot, canonical_question, generation_route="default_ir"
    )
    assert "complex_node_requires_planned_route" in codes(result.violations)
```

- [ ] **Step 4: Validate typed clarification locally**

```python
def test_valid_clarification_requires_no_fallback_or_engine(snapshot):
    question = canonicalize_question("volume by customer or account?")
    request = valid_clarification_request(question, snapshot)
    decision = validate_clarification(request, question, snapshot)
    assert decision.reason == "clarification_required"
    assert decision.ambiguities == request.ambiguities


@pytest.mark.parametrize("mutation", [
    "out_of_bounds_span", "partial_token_span", "duplicate_candidates",
    "one_candidate", "mixed_candidate_kinds", "ungrounded_candidate",
    "irrelevant_candidate",
])
def test_invalid_clarification_request_is_check_failed(
    snapshot, mutation,
):
    question = canonicalize_question("volume by customer or account?")
    request = mutate_clarification(
        valid_clarification_request(question, snapshot), mutation
    )
    decision = validate_clarification(request, question, snapshot)
    assert decision.violation.code == "invalid_clarification_request"
```

Validate `0 <= start < end <= len(canonical_question)` using Unicode code-point offsets, exact `008.literal-span.v1` token boundaries, at least two distinct canonical candidate payloads, homogeneous candidate kinds, snapshot membership, and deterministic relevance. Object/relationship/governed-literal candidates must be ranked or graph-connected to ranked objects; grain/operator candidates must match fixed span-synonym and grounded-input applicability registries. The validator emits no free-form prompt text and cannot contact provider/compiler/engine.

- [ ] **Step 5: Add guarded escalation tests**

```python
def test_supported_complex_plan_produces_bound_acceptance(snapshot):
    plan = complex_window_plan(snapshot)
    decision = ComplexityRouter().validate(plan, snapshot)
    assert isinstance(decision.accepted, AcceptedComplexRoute)
    assert decision.accepted.generation_route == "planned_ir"
    assert decision.accepted.snapshot_hash == snapshot.snapshot_hash
    assert decision.accepted.plan_hash == complex_plan_sha256(plan)


def test_mixed_supported_and_unsupported_plan_refuses(snapshot):
    plan = complex_window_plan(snapshot).model_copy(update={
        "operator_ids": (
            "window.period_over_period.v1",
            "recursive_query.v1",
        )
    })
    decision = ComplexityRouter().validate(plan, snapshot)
    assert decision.accepted is None
    assert decision.reason == "unsupported_complexity"
```

Validate step dependencies, snapshot membership, declared outputs, and exact operator IDs. `AcceptedComplexRoute` construction is module-private/opaque to caller and provider payloads; it binds current snapshot and plan hashes. The router cannot mutate budgets, compile SQL, or contact a provider.

- [ ] **Step 6: Bind warnings and value-free assumptions at IR level**

Keep stable warning hashes and the deterministic control registry. Positive `metric.transaction-volume` uses governed unsigned `SUM(amount)` with no direction mapping. Signed net requires a complete typed partition whose inflow/outflow operands are `LiteralRef`; status/grain/snapshot physical operands also use refs. Validate question span membership/boundaries and governed-literal IDs/types here, but defer actual scalar resolution to Task 6. Mutation, embedded values, invented refs, or ungrounded refs fail without inspecting fixture-only keywords or serializing resolved values.

- [ ] **Step 7: Verify IR, clarification, and generation-route gates**

```bash
.venv/bin/python -m pytest tests/test_selfcheck_ir.py tests/test_complexity_router.py -v
```

Expected: pass without provider, compiler, or database.

- [ ] **Step 8: Commit only if authorized**

```bash
git add src/cerebro/complexity.py src/cerebro/selfcheck.py tests/test_selfcheck_ir.py tests/test_complexity_router.py tests/text2sql_factories.py
git commit -m "feat: validate typed IR and guard complex routing"
```

---

### Task 6: Resolve literal refs and compile accepted IR to deterministic parameterized SQL

Implements FR-711, FR-712, AC-702, AC-705, AC-706.

**Files:**
- Create: `src/cerebro/sql_compiler.py`
- Modify: `src/cerebro/models.py`
- Create: `tests/test_sql_compiler.py`
- Modify: `tests/text2sql_factories.py`

**Interfaces:**
- `LiteralResolver.resolve(ref: LiteralRef, canonical_question: str, snapshot: GroundingSnapshot, expected_type: ScalarType) -> ResolvedLiteral` validates membership/bounds/token/type without exposing the value outside compiler-local memory; failures return/raise a typed issue that orchestration converts to value-free `LiteralClarificationNeed`, never a fabricated `Ambiguity`.
- `DialectCompiler.compile(validated_ir, snapshot, canonical_question, max_rows) -> CompiledQuery`
- `to_sql_artifact(compiled: CompiledQuery) -> SQLArtifact` copies SQL, types/count, hashes, version, and dialect but never parameter values.
- `BoundParameter(position: int, data_type: ScalarType, value: JsonScalar)` is executor-local and excludes `value` from serialization.
- `CompilerError(code, node_id)` contains no raw data value, canonical question text, or SQL fragment.
- `compiler_version()` changes whenever rendering, literal scanning/parsing, or operator semantics change.

- [ ] **Step 1: Write deterministic compilation tests before implementation**

```python
def test_identical_ir_compiles_to_identical_bytes(snapshot):
    question = canonicalize_question("transaction volume by branch")
    ir = validated_ir(
        branch_volume_ir(snapshot), snapshot, question, generation_route="default_ir"
    )
    compiler = DialectCompiler("duckdb")
    first = compiler.compile(ir, snapshot, question, max_rows=1000)
    second = compiler.compile(ir, snapshot, question, max_rows=1000)
    assert first.model_dump_json() == second.model_dump_json()
    assert [
        (item.position, item.data_type, item.value) for item in first.parameters
    ] == [
        (item.position, item.data_type, item.value) for item in second.parameters
    ]
    assert first.ir_hash == second.ir_hash


def test_question_span_literal_is_parameterized_and_public_models_are_value_free(snapshot):
    question = canonicalize_question("Show accounts in London")
    city_ref = QuestionLiteralRef(
        kind="question", start=17, end=23, data_type="string"
    )
    ir = validated_ir(
        filtered_account_ir(snapshot, city_ref=city_ref),
        snapshot,
        question,
        generation_route="default_ir",
    )
    compiled = DialectCompiler("duckdb").compile(
        ir, snapshot, question, max_rows=100
    )
    artifact = to_sql_artifact(compiled)
    assert "London" not in ir.model_dump_json()
    assert "London" not in compiled.sql
    assert "?" in compiled.sql
    assert [item.value for item in compiled.parameters] == ["London"]
    assert "London" not in artifact.model_dump_json()
    assert artifact.parameter_count == 1
    assert artifact.parameter_types == ("string",)


def test_governed_literal_resolves_only_by_snapshot_id(snapshot_with_literals):
    question = canonicalize_question("Show active accounts")
    ref = GovernedLiteralRef(kind="governed", literal_id="literal.account-active")
    compiled = compile_status_ir(snapshot_with_literals, question, ref)
    assert compiled.parameters[0].value == "ACTIVE"
    assert "ACTIVE" not in compiled.sql


def test_metric_formula_comes_from_snapshot_not_ir(snapshot):
    question = canonicalize_question("card fraud rate")
    ir = validated_ir(
        card_fraud_metric_ir(snapshot), snapshot, question,
        generation_route="default_ir",
    )
    compiled = DialectCompiler("duckdb").compile(
        ir, snapshot, question, max_rows=100
    )
    assert normalized_sql(snapshot.metric("metric.card-fraud-rate").formula) in normalized_sql(compiled.sql)
```

Run: `.venv/bin/python -m pytest tests/test_sql_compiler.py -v`

Expected: fail because `DialectCompiler` does not exist.

- [ ] **Step 2: Topologically normalize validated IR**

Reject unvalidated input at the type boundary by accepting `ValidatedIR`, not bare `RelationalQueryIR`. Before traversal, recompute canonical IR, snapshot, and canonical-question hashes; verify the original generation route and accepted-plan-hash cross-fields; and return `validated_ir_integrity_error` with no SQL if nested IR or bound context changed after validation. Traverse in stable topological order using node ID only as a deterministic tie-breaker. Assign table aliases `t0`, `t1`, and CTE aliases `q0`, `q1` in traversal order; never reuse model-proposed aliases.

- [ ] **Step 3: Resolve literal refs locally and build SQL with sqlglot AST nodes**

Construct identifiers, predicates, joins, aggregates, projections, sort, limit, window, and safe set operations through `sqlglot.exp`. Expand relationship predicates and metric formulas only from the snapshot. `LiteralResolver` first validates the canonical-question hash on `ValidatedIR`. For `QuestionLiteralRef`, enforce code-point bounds and exactly one token from `008.literal-span.v1`: matching single/double-quoted content (no escapes) is one token excluding quotes; otherwise a token is the maximal run between ASCII space or `,;()[]{}?!`. Apply the exact parser registry: NFC text for `string`; signed ASCII digits or a case-insensitive single-token cardinal `0..19`/exact tens `20..90` for `integer`; finite non-exponent base-10 for `decimal`; `true|false` for `boolean`; valid `YYYY-MM-DD` for `date`; and valid `YYYY-MM-DD[T ]HH:MM:SS` with optional fraction/no implicit timezone conversion for `timestamp`. Enforce consuming semantic bounds such as positive amounts/limits. For `GovernedLiteralRef`, require exact snapshot ID/type and use only its authored constant. Every ref becomes one positional `?` plus one internal `BoundParameter`; deterministic depth-first expression traversal fixes parameter order. Invalid boundaries, parse/type/bound mismatch, unknown governed ID, or any embedded/invented value raises a sanitized literal-resolution error and emits no SQL.

- [ ] **Step 4: Enforce generation-route and row-limit semantics during compilation**

Reject `WindowNode` and `SetOperationNode` unless `validated_ir.generation_route == "planned_ir"`; a cache hit does not alter that field. Resolve IR `LimitNode.count`, relative-time amount, window offsets, minimum-group guards, and assumption operands through `LiteralResolver`. The effective limit is the minimum of trusted request max rows, policy cap, and resolved positive IR limit. A sensitive disclosure cannot obtain a larger cap by omitting `LimitNode`. Trusted deployment caps are local configuration and are not model literal refs.

- [ ] **Step 5: Add compiler-defect and no-raw-SQL tests**

```python
def test_ir_contract_contains_no_raw_sql_intent_or_literal_value_field():
    schema = json.dumps(RelationalQueryIR.model_json_schema(), sort_keys=True)
    assert '"sql"' not in schema
    assert '"expression_sql"' not in schema
    assert '"intent"' not in schema
    assert '"value"' not in json.dumps(LiteralExpression.model_json_schema(), sort_keys=True)


@pytest.mark.parametrize("mutation, expected", [
    ("span_out_of_bounds", "invalid_literal_reference"),
    ("span_splits_token", "invalid_literal_reference"),
    ("scalar_parse_mismatch", "invalid_literal_type"),
    ("unknown_governed_literal", "ungrounded_literal_reference"),
    ("governed_type_mismatch", "invalid_literal_type"),
])
def test_literal_resolution_fails_without_partial_query(
    snapshot, mutation, expected,
):
    with pytest.raises(CompilerError) as caught:
        compile_literal_mutation(snapshot, mutation)
    assert caught.value.code == expected
    assert not hasattr(caught.value, "sql")
    assert "value" not in str(caught.value).lower()


def test_mutated_validated_ir_fails_integrity_before_compilation(snapshot):
    question = canonicalize_question("transaction volume by branch")
    accepted = validated_ir(
        branch_volume_ir(snapshot), snapshot, question,
        generation_route="default_ir",
    )
    accepted.ir.nodes[0].node_id = "tampered"
    with pytest.raises(CompilerError) as caught:
        DialectCompiler("duckdb").compile(
            accepted, snapshot, question, max_rows=100
        )
    assert caught.value.code == "validated_ir_integrity_error"
    assert not hasattr(caught.value, "sql")


def test_unknown_compiler_node_fails_without_partial_query(snapshot):
    with pytest.raises(CompilerError) as caught:
        compile_corrupted_validated_ir(snapshot, node_kind="recursive")
    assert caught.value.code == "unsupported_compiler_node"
    assert not hasattr(caught.value, "sql")
```

- [ ] **Step 6: Verify compiler behavior**

```bash
.venv/bin/python -m pytest tests/test_sql_compiler.py -v
```

Expected: pass with stable SQL and parameter order on repeated and shuffled-map inputs.

- [ ] **Step 7: Commit only if authorized**

```bash
git add src/cerebro/sql_compiler.py src/cerebro/models.py tests/test_sql_compiler.py tests/text2sql_factories.py
git commit -m "feat: compile relational IR deterministically"
```

---

### Task 7: Rebind semantic, AST, lineage, and disclosure gates to IR

Implements FR-709a–FR-715, AC-700–AC-703, AC-705, AC-709.

**Files:**
- Modify: `src/cerebro/selfcheck.py`
- Modify: `tests/test_selfcheck_plan.py` (retain only local missing-grounding and policy-refusal regressions; IR graph assertions live in `test_selfcheck_ir.py`)
- Rewrite: `tests/test_selfcheck_sql.py`
- Create: `tests/test_selfcheck_semantics.py`
- Modify: `tests/text2sql_factories.py`

**Interfaces:**
- `authorize_compiled_query(compiled, validated_ir, snapshot, canonical_question, caps) -> SQLAuthorizationResult`
- `SQLReferenceGraph` resolves physical sources, columns, joins, predicates, functions, literal placeholders/parameter positions, branches, windows, set operations, and output positions.
- `PredicateLedger` requires exactly one IR declaration for each non-relationship predicate.
- `SQLAuthorizationResult` exposes lineage/disclosures only when violations are empty; it never exposes parameter values.

- [ ] **Step 1: Inject compiler defects and prove post-compile containment catches them**

```python
@pytest.mark.parametrize("mutation, expected", [
    ("extra_table", "ungrounded_sql_reference"),
    ("extra_column", "ungrounded_sql_reference"),
    ("wrong_join", "relationship_mismatch"),
    ("extra_filter", "undeclared_filter"),
    ("external_scan", "unsafe_sql_source"),
    ("extra_literal", "undeclared_literal"),
    ("parameter_position_swap", "compiled_parameter_mismatch"),
    ("larger_limit", "compiled_limit_mismatch"),
])
def test_compiler_defect_stops_before_engine(
    compiled_query, validated_ir, snapshot, canonical_question, mutation, expected,
):
    corrupted = mutate_compiled_query(compiled_query, mutation)
    result = authorize_compiled_query(
        corrupted, validated_ir, snapshot, canonical_question, DisclosureCaps.defaults()
    )
    assert expected in codes(result.violations)
    assert result.output_lineage == ()
    assert result.disclosures == ()
```

Run:

```bash
.venv/bin/python -m pytest tests/test_selfcheck_sql.py tests/test_selfcheck_semantics.py -v
```

Expected: fail until SQL checks use IR rather than the v2 `QueryPlan`.

- [ ] **Step 2: Resolve the complete SQL AST against IR**

Resolve aliases, CTEs, subqueries, comma joins, `USING`, and each set leaf. Expand relationship equality endpoints to qualified `ColumnRef`. Match every SQL literal placeholder and parameter position to exactly one IR `LiteralRef` resolved against the same canonical-question hash/snapshot, comparing types without serializing values. Deny literal constants introduced by compiler defects, unknown AST nodes/functions, multiple statements, DDL/DML/admin nodes, secrets/settings, extensions, file/network/table functions, `NATURAL JOIN`, unbounded row-level cross joins, and join `OR`.

- [ ] **Step 3: Account for every predicate exactly once**

The ledger claims each term through one of: IR filter predicate, relative-time control, relationship edge, status assumption, or minimum-group disclosure guard. An unclaimed or multiply claimed term is `undeclared_filter`. No string comparison against model SQL is used.

- [ ] **Step 4: Verify metric roots and warning controls**

For each `MetricExpression`, identify one final output root-equivalent to the snapshot formula. Reject formula only in a dead CTE, changed denominator, wrapper, or another output. Re-run warning controls against IR refs, locally resolved parameter positions/types, and compiled AST so a compiler defect cannot bypass them. Never compare or emit resolved parameter values in a violation.

- [ ] **Step 5: Derive complete output lineage and disclosure records**

Propagate source columns and classifications through casts, `CASE`, concatenation, aggregate, window, and set outputs. Sensitive collection/string aggregates fail. Value-preserving output requires every source column plus finite cap. Reducing aggregates require allowed function and minimum group size. For set operations, validate every leaf first and merge lineage by ordinal only after all leaves pass.

- [ ] **Step 6: Add one-unsafe-set-leaf regression matrix**

Cover at least: extra source, wrong metric root, wall-clock relative time, incomplete signed direction, sensitive `LIST`, missing group threshold, and status-mapping mismatch. Every production-path case must prove validator and executor call counts remain zero.

- [ ] **Step 7: Verify deterministic authorization**

```bash
.venv/bin/python -m pytest tests/test_selfcheck_ir.py tests/test_selfcheck_sql.py tests/test_selfcheck_semantics.py -v
```

Expected: pass without provider or engine.

- [ ] **Step 8: Commit only if authorized**

```bash
git add src/cerebro/selfcheck.py tests/test_selfcheck_plan.py tests/test_selfcheck_ir.py tests/test_selfcheck_sql.py tests/test_selfcheck_semantics.py tests/text2sql_factories.py
git commit -m "fix: authorize compiled SQL against relational IR"
```

---

### Task 8: Add scope-safe cache identity and global request budgets

Implements FR-704b, FR-708, FR-716, FR-716a, AC-714, AC-715.

**Files:**
- Create: `src/cerebro/text2sql_cache.py`
- Modify: `src/cerebro/text2sql.py` (budget primitives only)
- Modify: `src/cerebro/models.py`
- Create: `tests/test_text2sql_cache.py`
- Create: `tests/test_text2sql_budget.py`

**Interfaces:**
- `CacheKey.from_request(..., canonical_question_hash, generation_route) -> CacheKey`
- `CachedGeneration(ir, generation_route, accepted_complex_plan_hash, payload_sha256)` contains a value-free IR and original generation route only.
- `Text2SQLCache.get(key) -> CachedGeneration | None`
- `Text2SQLCache.put(key, value) -> None`
- `RequestBudget.start(limits, clock) -> RequestBudget` always starts at semantic capacity one.
- `budget.authorize_planned_ir(decision: AcceptedComplexRoute) -> None` is the only one-way capacity-one-to-two transition and validates current snapshot/plan binding.
- `budget.before(action, estimated_tokens=0, estimated_cost_usd=Decimal("0"))` and `budget.record_*` enforce monotonic usage across provider/compiler/engine work.

- [ ] **Step 1: Write the complete cache-key mutation matrix**

```python
CACHE_KEY_MUTATIONS = (
    "authorization_scope_hash",
    "snapshot_hash",
    "policy_version",
    "canonicalization_version",
    "canonical_question_hash",
    "literal_registry_version",
    "dialect",
    "generation_route",
    "provider",
    "model",
    "model_revision",
    "schema_mechanism",
    "prompt_version",
    "ir_contract_version",
    "router_version",
    "compiler_version",
    "type_registry_version",
    "checker_version",
)


@pytest.mark.parametrize("field", CACHE_KEY_MUTATIONS)
def test_every_security_relevant_change_is_a_cache_miss(cache_fixture, field):
    original = cache_fixture.key
    cache_fixture.cache.put(original, cache_fixture.value)
    changed = mutate_cache_key(original, field)
    assert cache_fixture.cache.get(changed) is None


def test_question_canonicalization_preserves_case_and_literals():
    assert canonicalize_question("  Revenue\u00a0 for  Q1  ") == "Revenue for Q1"
    assert canonicalize_question("Q1") != canonicalize_question("q1")
    assert canonical_question_sha256("Q1") != canonical_question_sha256("q1")
```

Run: `.venv/bin/python -m pytest tests/test_text2sql_cache.py -v`

Expected: fail until exact key identity exists.

- [ ] **Step 2: Add cross-scope and integrity tests**

A payload copied under another tenant/scope or canonical-question key must miss. Mutation of cached IR, `generation_route`, accepted complex-plan hash, or payload hash must yield `cache_integrity_error`; it cannot fall through as a hit. `planned_ir` requires a non-null accepted-plan hash; `default_ir` rejects one, and key route, payload route, reconstructed `ValidatedIR.generation_route`, and hash presence must agree. Reject `generation_route="cache"`. Cache values never contain canonical question text, embedded/resolved literal values, compiled SQL, bound parameters, result rows, credentials, or raw provider content. Add a serialization walk that rejects any IR `intent`/literal `value` key.

- [ ] **Step 3: Implement canonical keys and original generation-route lookup**

Compute `canonical_question_hash` from `canonicalize_question` version `008.question.v1`; do not case-fold or rewrite literals. Include canonicalization and `008.literal-span.v1` registry versions. Compute a base fingerprint, then probe exact `generation_route` keys in order `default_ir`, `planned_ir`; reject multiple hits as integrity failure. Stored `generation_route` must equal key route. Use full SHA-256 hashes, not truncated digests. On a hit, reconstruct `ValidatedIR` with its original route, validate every question span against the current canonical string/hash, rerun planned-route-only checks for cached windows/set operations, and compile again; no cached SQL exists to compare.

```python
def test_cached_planned_ir_preserves_route_and_revalidates_complex_nodes(cache_fixture):
    cache_fixture.store_planned_ir()
    hit = cache_fixture.lookup()
    assert hit.generation_route == "planned_ir"
    validated = validate_ir(
        hit.ir,
        cache_fixture.snapshot,
        cache_fixture.canonical_question,
        generation_route=hit.generation_route,
    )
    assert validated.validated_ir.generation_route == "planned_ir"
```

- [ ] **Step 4: Write call, token, cost, and deadline budget tests**

```python
def test_budget_defaults_are_exact():
    limits = BudgetLimits.defaults()
    assert limits.initial_semantic_call_capacity == 1
    assert limits.planned_semantic_call_capacity == 2
    assert limits.max_transport_attempts_per_semantic_call == 2
    assert limits.provider_timeout_ms_per_attempt == 20_000
    assert limits.max_input_tokens == 32_000
    assert limits.max_output_tokens == 8_000
    assert limits.max_cost_usd == Decimal("0.50")
    assert limits.end_to_end_deadline_ms == 120_000


def test_normal_route_rejects_second_semantic_call(fake_clock):
    budget = RequestBudget.start(BudgetLimits.defaults(), fake_clock)
    budget.before("default_ir")
    budget.record_semantic_call(
        input_tokens=100, output_tokens=20, cost_usd=Decimal("0.01")
    )
    with pytest.raises(BudgetExceeded) as caught:
        budget.before("default_ir")
    assert caught.value.code == "semantic_call_budget_exceeded"


def test_planned_ir_transition_requires_current_router_acceptance(
    fake_clock, snapshot,
):
    budget = RequestBudget.start(BudgetLimits.defaults(), fake_clock)
    with pytest.raises(BudgetExceeded):
        budget.before("planned_ir")
    with pytest.raises(BudgetTransitionDenied):
        budget.authorize_planned_ir(foreign_accepted_route(snapshot))


def test_mutated_accepted_plan_cannot_authorize_fallback(fake_clock, snapshot):
    budget = RequestBudget.start(BudgetLimits.defaults(), fake_clock)
    accepted = ComplexityRouter().validate(
        complex_window_plan(snapshot), snapshot
    ).accepted
    accepted.plan.expected_outputs = ("tampered",)
    with pytest.raises(BudgetTransitionDenied):
        budget.authorize_planned_ir(accepted)


def test_accepted_transition_is_one_time_and_third_call_is_denied(
    fake_clock, snapshot,
):
    budget = RequestBudget.start(BudgetLimits.defaults(), fake_clock)
    budget.before("default_ir")
    budget.record_semantic_call(input_tokens=1, output_tokens=1, cost_usd=Decimal("0"))
    accepted = ComplexityRouter().validate(
        complex_window_plan(snapshot), snapshot
    ).accepted
    budget.authorize_planned_ir(accepted)
    assert budget.semantic_call_capacity == 2
    with pytest.raises(BudgetTransitionDenied):
        budget.authorize_planned_ir(accepted)
    budget.before("planned_ir")
    budget.record_semantic_call(input_tokens=1, output_tokens=1, cost_usd=Decimal("0"))
    with pytest.raises(BudgetExceeded):
        budget.before("planned_ir")


def test_deadline_blocks_action_before_contact(fake_clock):
    budget = RequestBudget.start(
        BudgetLimits(end_to_end_deadline_ms=10),
        fake_clock,
    )
    fake_clock.advance_ms(11)
    with pytest.raises(BudgetExceeded):
        budget.before("engine_validation")
```

- [ ] **Step 5: Implement monotonic budget accounting**

Initialize semantic capacity to one regardless of request shape. Recompute the accepted plan hash and validate that `AcceptedComplexRoute` is authentic, unmodified, and bound to the exact current snapshot/plan before the one-way transition to two; deny caller-created, mutated, stale, foreign, absent, or repeated decisions. Before transition, deny `planned_ir` and every second semantic call; after transition, deny every third call. Enforce at most two transport attempts independently for each semantic call and the configured `20_000 ms` per-attempt timeout. Check hard deadline and estimated remaining cumulative token/USD/call capacity before each provider, compiler, `EXPLAIN`, and execution action. Record actual gateway usage after calls, never decrement counters or erase transport attempts, and span both calls plus engine work in one budget. `BudgetUsage` is returned on every terminal response.

- [ ] **Step 6: Verify cache and budget modules**

```bash
.venv/bin/python -m pytest tests/test_text2sql_cache.py tests/test_text2sql_budget.py -v
```

Expected: pass without provider or database.

- [ ] **Step 7: Commit only if authorized**

```bash
git add src/cerebro/text2sql_cache.py src/cerebro/text2sql.py src/cerebro/models.py tests/test_text2sql_cache.py tests/test_text2sql_budget.py
git commit -m "feat: add scoped cache and request budgets"
```

---

### Task 9: Make `EXPLAIN` and execution parameter-aware, mandatory, and race-safe

Implements FR-714, FR-718–FR-722, AC-709.

**Files:**
- Modify: `src/cerebro/executor.py`
- Rewrite: `tests/test_executor.py`
- Modify: `tests/test_selfcheck_engine.py`

**Interfaces:**
- `EngineValidator.validate(compiled: CompiledQuery) -> tuple[CheckViolation, ...]`
- `Executor.execute(compiled: CompiledQuery, max_rows: int) -> QueryResult`
- `DuckDBExecutor` implements both protocols and owns one serialized read-only connection.
- `_run_with_deadline(action, interrupt, timeout_seconds, phase) -> T` is the only watchdog primitive.

- [ ] **Step 1: Add parameter binding and mandatory dependency tests**

```python
def test_explain_and_execution_receive_identical_parameters(database):
    compiled = CompiledQuery(
        sql="SELECT account_id FROM accounts WHERE account_id = ?",
        parameters=(BoundParameter(position=1, data_type="integer", value=7),),
        ir_hash="0" * 64,
        compiler_version="test",
        dialect="duckdb",
    )
    executor = DuckDBExecutor(database)
    assert executor.validate(compiled) == ()
    result = executor.execute(compiled, max_rows=10)
    assert result.columns == ["account_id"]
```

Run: `.venv/bin/python -m pytest tests/test_executor.py tests/test_selfcheck_engine.py -v`

Expected: fail against string-only executor protocols.

- [ ] **Step 2: Add interruption race and no-partial-row tests**

Prove one watchdog interrupts at most once, success cannot receive a late interrupt, execute plus fetch share one deadline, timeout discards buffered rows, and the connection is verified or replaced only after watchdog join.

- [ ] **Step 3: Implement one synchronized watchdog per phase**

Use `threading.Event` completion/fired flags and unconditional thread join. Set `fired` before `interrupt()` so the deadline wins a simultaneous completion race. Keep the connection lock through join and recovery. Do not use `threading.Timer` or `SET statement_timeout`.

- [ ] **Step 4: Apply finite supported DuckDB settings**

Record effective explain/execute deadlines, row cap, memory, threads, and temporary-directory policy. Unsupported required settings fail construction with `ExecutorConfigurationError`, mapped later to `execution_error` before any provider contact.

- [ ] **Step 5: Verify engine behavior**

```bash
.venv/bin/python -m pytest tests/test_executor.py tests/test_selfcheck_engine.py -v
```

Expected: pass for parameter binding, zero rows, max_rows+1 truncation detection, read-only connection, interruption races, no partial rows, and reuse/replacement.

- [ ] **Step 6: Commit only if authorized**

```bash
git add src/cerebro/executor.py tests/test_executor.py tests/test_selfcheck_engine.py
git commit -m "fix: bind and bound text-to-sql execution"
```

---

### Task 10: Rebuild orchestration around one-call IR and guarded fallback

Implements FR-702–FR-708, FR-714–FR-722, AC-704, AC-711, AC-713–AC-715.

**Files:**
- Rewrite: `src/cerebro/text2sql.py`
- Rewrite: `tests/test_text2sql_agent.py`

**Interfaces:**

```python
Text2SQLAgent(
    resolver: GroundingResolver,
    provider: Text2SQLGenerationProvider,  # production instance is GuardedProvider
    router: ComplexityRouter,
    compiler: DialectCompiler,
    cache: Text2SQLCache,
    validator: EngineValidator,
    executor: Executor,
    *,
    disclosure_caps: DisclosureCaps,
    budget_limits: BudgetLimits,
    versions: RuntimeVersions,
)
```

`run(request: SQLGenerationRequest) -> SQLGenerationResponse`. Every dependency is mandatory; `None` is invalid. The constructor rejects a production provider that is not `GuardedProvider`; protocol-conforming scripted guards are explicit test composition. `text2sql.py` imports `Text2SQLGenerationProvider` from `text2sql_provider.py`, never `GenerationProvider` from enrichment or `OrganizerModelGateway`.

- [ ] **Step 1: Write exact default, complex, and cache call-count tests**

```python
def test_normal_request_makes_one_semantic_call(agent_fixture):
    fixture = agent_fixture(provider_outputs=[minimal_ir()])
    response = fixture.agent.run(valid_request())
    assert response.status == "ok"
    assert response.generation_route == "default_ir"
    assert response.cache_status == "miss"
    assert fixture.provider.semantic_calls == 1


def test_complex_request_makes_exactly_two_semantic_calls_after_transition(agent_fixture):
    fixture = agent_fixture(
        provider_outputs=[complex_window_plan(), relative_growth_ir()]
    )
    response = fixture.agent.run(
        valid_request(question="month over month growth over the last 24 months")
    )
    assert response.status == "ok"
    assert response.generation_route == "planned_ir"
    assert response.cache_status == "miss"
    assert response.budget_usage.semantic_calls == 2
    assert response.budget_usage.planned_ir_authorized is True


def test_cached_planned_ir_preserves_generation_route_and_revalidates(agent_fixture):
    fixture = agent_fixture(preloaded_planned_ir_cache=True, provider_outputs=[])
    response = fixture.agent.run(
        valid_request(question="month over month growth over the last 24 months")
    )
    assert response.status == "ok"
    assert response.generation_route == "planned_ir"
    assert response.cache_status == "hit"
    assert fixture.provider.semantic_calls == 0
    assert fixture.ir_checker.calls == ["planned_ir"]
    assert fixture.compiler.calls == 1
    assert fixture.ast_checker.calls == 1
    assert fixture.validator.calls == 1
    assert fixture.executor.calls == 1
```

Run: `.venv/bin/python -m pytest tests/test_text2sql_agent.py -v`

Expected: fail against unconditional plan plus SQL generation.

- [ ] **Step 2: Implement the exact orchestration order**

1. Start `RequestBudget` at semantic capacity one; validate the request/scope.
2. Canonicalize the question with `008.question.v1`, compute its hash, then resolve/hash the authorized snapshot.
3. Probe exact `default_ir` and `planned_ir` generation-route cache keys; validate payload integrity and preserve the hit's original route.
4. On miss, budget and make one `default_ir` call for `IRGenerationOutcome`.
5. Locally verify `GroundingRefusal`; validate and terminate a `ClarificationRequest`; validate direct IR; or validate complex escalation.
6. For locally produced `AcceptedComplexRoute` only, call `budget.authorize_planned_ir(decision)` once, then budget/make one `planned_ir` call; require fallback IR to implement the accepted plan.
7. Validate IR graph, membership, canonical literal refs, dataflow, explicit type/signature rules, and generation-route restrictions; derive local policy/literal clarification before compilation. A cache hit enters here with its original route and zero semantic calls.
8. Resolve literal refs against the exact canonical question/snapshot and compile deterministically; resolved values remain internal.
9. Authorize compiled AST, placeholders/parameters, metrics, warnings, assumptions, lineage, and disclosure.
10. Run mandatory parameter-aware `EXPLAIN` under the same cumulative deadline/budget.
11. Execute once under remaining deadline; discard partial rows on failure.
12. Assemble exactly one strict response with original `generation_route` plus independent `cache_status`; cache only validated value-free IR/generation artifacts.

A valid clarification performs no fallback, compilation, `EXPLAIN`, or execution. No failure after step 6 contacts the provider again; no path can perform a third semantic call.

- [ ] **Step 3: Map every terminal failure to its originating phase**

Use this exhaustive table:

| Origin | Code | Attempt stage |
|---|---|---|
| missing organizer configuration | `provider_configuration_error` | `provider_transport` |
| exhausted retryable transport (at most two attempts/call) | `provider_unavailable` | `provider_transport` |
| non-retryable rejection | `provider_rejected` | `provider_transport` |
| guarded egress denial | `egress_blocked` | requested semantic stage |
| default outcome schema failure | `unparsable_generation_outcome` | `default_ir` |
| fallback schema failure | `unparsable_fallback_ir` | `planned_ir` |
| valid typed ambiguity | local `refused / clarification_required` | `clarification` |
| invalid span/candidates/relevance in model ambiguity | `invalid_clarification_request` | `clarification` |
| invalid complex plan/operator | local refusal or `invalid_complex_plan` | `complexity` |
| premature/foreign/repeated planned transition or third call | `budget_exceeded` | blocked `planned_ir` stage |
| invalid IR graph/membership/type/placement | exact IR/type violation | requested generation stage |
| post-validation IR/snapshot/question hash drift | `validated_ir_integrity_error` | `compile` |
| ungrounded/unresolvable/invented literal ref | local `refused / clarification_required` with `LiteralClarificationNeed` | `literal_resolution` |
| compiler failure | `compiler_error` | `compile` |
| post-compile parser/containment/parameter failure | exact AST violation | `ast_check` |
| cache hash/payload/generation-route mismatch | `cache_integrity_error` | `cache` |
| hard deadline/token/cost/call limit exhaustion | `budget_exceeded` | blocked next stage |
| binder/type failure | `explain_failed` | `engine_validation` |
| explain deadline | `explain_timeout` | `engine_validation` |
| execute/fetch deadline | `execution_timeout` | `execution` |
| sanitized execute/configuration failure | `execution_error` | `execution` |

- [ ] **Step 4: Add no-repair and no-engine-contact assertions**

For each clarification, IR graph/type, literal, compiler, AST, disclosure, router, cache, and budget failure, assert no `EXPLAIN` or execute call. Valid clarification and local literal clarification also assert no fallback call or SQL artifact. For compiler, validation, and execution failure, assert semantic call count does not increase. For first-boundary egress denial, assert zero inner calls; for denied fallback or premature budget transition, preserve the first call and add no second. Explicitly assert a repeated transition and third call are blocked before provider contact.

- [ ] **Step 5: Verify local refusal behavior**

An absent `GroundingNeed` can return `missing_grounding`; a present claimed-missing object returns `check_failed`. Unbounded sensitive output becomes local `policy_disallowed`. Unsupported complex operator returns refusal before compilation. A valid model `ClarificationRequest` returns `clarification_required` with locally validated `ambiguities`, `generation_route="default_ir"`, and `cache_status="miss"`; out-of-bounds/non-token spans, fewer than two distinct candidates, nonmembers, mixed kinds, or irrelevant candidates return `check_failed / invalid_clarification_request`. Ungrounded, unresolvable, or invented physical literal refs return `clarification_required` with one or more value-free `LiteralClarificationNeed` records, no fabricated ambiguity, the IR's original generation route, and current cache status; they never create an invented bound value.

- [ ] **Step 6: Verify orchestration and contracts**

```bash
.venv/bin/python -m pytest tests/test_text2sql_agent.py tests/test_text2sql_contract.py tests/test_text2sql_cache.py tests/test_text2sql_budget.py tests/test_complexity_router.py tests/test_hosted_provider.py -v
! grep -R "from \.enrichment\|from cerebro\.enrichment\|import cerebro\.enrichment" src/cerebro/text2sql.py
```

Expected: every generation-route/cache call-count, clarification, guarded-transition, phase, no-contact, value-free response, and zero-row invariant passes offline.

- [ ] **Step 7: Commit only if authorized**

```bash
git add src/cerebro/text2sql.py tests/test_text2sql_agent.py
git commit -m "refactor: orchestrate one-call relational IR"
```

---

### Task 11: Rebuild offline reference and CLI composition around the compiler

Implements FR-703c, FR-703d, FR-704, FR-723, AC-706, AC-707, AC-710, AC-714, AC-715.

**Files:**
- Modify actual production constructor: `src/cerebro/evaluation.py`
- Modify actual CLI constructor: `src/cerebro/cli.py`
- Verify grounding-only boundary unchanged: `src/cerebro/api.py`
- Rewrite: `tests/golden_answers.py`
- Create or modify: `tests/fixtures/text2sql-supported-questions.yaml`
- Rewrite: `tests/test_baseline_evaluation.py`
- Modify: `tests/test_prompt_egress.py`
- Modify: `tests/test_retrieval_api_mcp.py`
- Verify unchanged: `tests/test_enrichment.py`

**Interfaces:**
- `build_agent(database_path, provider_mode, authorization_scope, provider=None) -> AgentRuntime`
- `run_offline_reference(...) -> OfflineReferenceRun`; persisted entries retain question ID plus canonical-question hash, never question text.
- `GoldenProvider` structurally implements `Text2SQLGenerationProvider`, emits typed/value-free `IRGenerationOutcome`, and never stores SQL, resolved literals, or imports enrichment.
- CLI modes remain `ask`, `reference`, and `baseline` with no implicit fallback.
- The current `cli.py` and `evaluation.py` calls that build `SQLGenerationRequest(question=..., grounding=retriever.grounding(...))` must become `SQLGenerationRequest(question=..., authorization_scope=trusted_scope)` and use the centralized agent's `GroundingResolver`. No HTTP/MCP `GroundingResponse` is reused.

- [ ] **Step 1: Convert deterministic answers from SQL to IR fixtures**

For metric, two-hop join, bounded disclosure, and zero-row cases, store one value-free `RelationalQueryIR` output. Build each question first with `canonicalize_question`, then derive every amount/limit/filter span by code-point offset; for example, `five`, `24`, and the zero-row fixture's explicit `0` are `QuestionLiteralRef`, not `5`/`24`/`0` embedded in IR. Use `GovernedLiteralRef` only for stable constants authored in the snapshot. Do not infer numeric zero from prose such as “negative amount.” The relative-time growth case stores exactly two scripted outputs: an accepted `ComplexQueryPlan` using `window.period_over_period.v1`, then its conforming fallback IR. Expected SQL and resolved values belong only in compiler/executor assertions, not provider fixtures, cache payloads, or artifacts.

- [ ] **Step 2: Preserve the supported capability set**

Use these cases:

```yaml
questions:
  - id: metric-fidelity
    question: What is the card fraud rate by card type?
  - id: two-hop-join
    question: What is transaction volume by branch?
  - id: relative-time
    question: What is monthly transaction growth over the last 24 months of available data?
  - id: bounded-disclosure
    question: Which five customers have the highest annual income?
  - id: zero-row
    question: List card transaction IDs with amount below 0.
```

`metric-fidelity`, `two-hop-join`, `bounded-disclosure`, and `zero-row` use `generation_route="default_ir"` with one call. `relative-time` requires period-over-period `LAG`, so local complex-plan acceptance performs the budget transition and one `planned_ir` call; it records `generation_route="planned_ir"` and exactly two total semantic calls. Cache status is asserted separately and never changes either route.

- [ ] **Step 3: Assert non-vacuous outcomes and call counts**

```python
@pytest.mark.parametrize("case_id", [
    "metric-fidelity",
    "two-hop-join",
    "relative-time",
    "bounded-disclosure",
    "zero-row",
])
def test_supported_case_reaches_ok(case_id, offline_run):
    response = offline_run.responses_by_id[case_id]
    assert response.status == "ok"
    assert response.output_lineage
    expected_route = "planned_ir" if case_id == "relative-time" else "default_ir"
    expected_calls = 2 if case_id == "relative-time" else 1
    assert response.generation_route == expected_route
    assert response.cache_status == "miss"
    assert response.budget_usage.semantic_calls == expected_calls
    serialized = response.model_dump_json()
    assert '"intent"' not in serialized
    assert '"value"' not in response.ir.model_dump_json()
```

For `zero-row`, require `rows == []`, `row_count == 0`, one execution, and no semantic retry. Test `COUNT(*) = 0` separately as one returned row.

- [ ] **Step 4: Centralize runtime composition**

`build_agent` constructs trusted authorization scope → canonicalizer/resolver → `GuardedProvider` wrapping organizer/cassette/scripted protocol adapter → router → compiler/literal resolver → cache → validator/executor → agent. CLI and evaluation import this function; neither constructs prompts, providers, cache keys, snapshots, or agents ad hoc. Migrate both actual source call sites from `question + retriever.grounding(...)` to `question + trusted AuthorizationScope`; the agent itself resolves grounding. `api.py` stays unchanged as advisory grounding, and tests prove its response is not accepted by this composition.

- [ ] **Step 5: Keep CLI modes explicit**

- `cerebro ask`: live organizer provider plus explicit trusted authorization-scope input only; missing provider/scope configuration returns typed JSON and exit 2. It never calls `/api/grounding` or accepts serialized `GroundingResponse`.
- `cerebro reference`: scripted/cassette offline only with an explicit fixture scope; writes `run_kind=offline_reference` and never result or resolved literal values.
- `cerebro baseline`: live organizer provider plus explicit trusted scope only; owns current-run capability probing and never accepts `GoldenProvider`.
- Existing HTTP `/api/grounding` and MCP commands remain advisory metadata retrieval only; do not add an SQL endpoint.

- [ ] **Step 6: Verify offline operation with networking disabled**

```bash
CEREBRO_TEST_NO_NETWORK=1 env -u CEREBRO_API_KEY -u OPENAI_API_KEY -u CEREBRO_MODEL .venv/bin/python -m pytest tests/test_baseline_evaluation.py tests/test_prompt_egress.py tests/test_retrieval_api_mcp.py tests/test_enrichment.py -v
! grep -R "from cerebro\.enrichment\|import cerebro\.enrichment" tests/golden_answers.py
CEREBRO_TEST_NO_NETWORK=1 env -u CEREBRO_API_KEY -u OPENAI_API_KEY -u CEREBRO_MODEL .venv/bin/python -m cerebro reference --output artifacts/offline-reference.json
```

Expected: all supported fixtures are `ok`, generation-route/cache/call counts match, artifact is value-free `offline_reference`, no external socket opens, grounding-only API cannot authorize execution, and enrichment behavior remains unchanged.

- [ ] **Step 7: Commit only if authorized**

```bash
git add src/cerebro/evaluation.py src/cerebro/cli.py tests/golden_answers.py tests/fixtures/text2sql-supported-questions.yaml tests/test_baseline_evaluation.py tests/test_prompt_egress.py tests/test_retrieval_api_mcp.py
git commit -m "fix: evaluate deterministic IR compilation offline"
```

---

### Task 12: Produce an evidence-bound live Option B baseline

Implements FR-724, FR-725, AC-708, AC-713–AC-715.

**Files:**
- Modify: `src/cerebro/models.py` (artifact/evidence contracts)
- Modify: `src/cerebro/evaluation.py`
- Modify: `src/cerebro/cli.py`
- Modify: `scripts/text2sql_preflight.py`
- Create or modify: `tests/test_text2sql_artifact.py`
- Modify: `tests/test_baseline_evaluation.py`

**Interfaces:**
- `ArtifactProvenance` adds retrieval, scope, snapshot, canonicalization, canonical-question, literal-registry, prompt, IR/type-registry, router, compiler, checker, cache, and exact budget version/hash evidence.
- Each `EvaluationQuestion` records original `generation_route`, independent `cache_status`, canonical-question/snapshot/scope hashes, IR hash, value-free `SQLArtifact`, attempt records, budget usage, terminal status, result schema/count, lineage, and disclosures; it excludes canonical question text, resolved literal/bound parameter values, and result values.
- `prepare_live_evidence(...) -> ValidatedLiveEvidence`
- `validate_live_baseline(candidate, evidence) -> EvaluationArtifact | BlockedEvaluation`
- Atomic writer accepts only validated `EvaluationArtifact`.

- [ ] **Step 1: Make every Option B evidence field mandatory**

```python
@pytest.mark.parametrize("field", [
    "retrieval_config_sha256",
    "policy_version",
    "canonicalization_version",
    "canonical_question_hash_algorithm",
    "literal_registry_version",
    "prompt_version",
    "ir_contract_version",
    "type_registry_version",
    "router_version",
    "compiler_version",
    "checker_sha256",
    "budget_limits",
])
def test_live_artifact_requires_option_b_provenance(field, valid_artifact_dict):
    payload = valid_artifact_dict()
    del payload["provenance"][field]
    with pytest.raises(ValidationError):
        EvaluationArtifact.model_validate(payload)
```

Run: `.venv/bin/python -m pytest tests/test_text2sql_artifact.py -v`

Expected: fail until v3 provenance is mandatory.

- [ ] **Step 2: Bind capability evidence to the actual organizer runtime**

The baseline creates a new run ID, derives provider/model/revision/schema mechanism from the actual gateway, performs exactly one fresh metadata-only capability probe before the first question, and writes a receipt bound to that run. Caller-supplied runtime identity or capability receipt is a type error.

- [ ] **Step 3: Record per-question generation route, cache, snapshot, and budget evidence**

Final validation derives semantic call totals from attempts and requires:

- default cache miss: `generation_route="default_ir"`, `cache_status="miss"`, exactly one semantic call, capacity one;
- planned cache miss: `generation_route="planned_ir"`, `cache_status="miss"`, exactly two semantic calls and one locally accepted one-way capacity transition;
- default cache hit: `generation_route="default_ir"`, `cache_status="hit"`, zero semantic calls plus current span/IR validation, local recompilation, AST/policy checks, and one `EXPLAIN`;
- planned cache hit: `generation_route="planned_ir"`, `cache_status="hit"`, zero semantic calls plus planned-route complex-node validation, current span/type checks, local recompilation, AST/policy checks, and one `EXPLAIN`;
- no entry uses `generation_route="cache"`, parallel candidates, repeated transition, third call, or post-compiler provider call;
- cumulative usage stays within exact configured call/transport/token/USD/deadline limits;
- value-free IR/artifacts contain no canonical question text, resolved literal values, or bound parameters.

Any mismatch blocks the artifact.

- [ ] **Step 4: Preserve retained-path rereads and non-vacuity**

Keep resolved immutable paths for source manifest, materialization receipt, bundle, database, and generated capability receipt. Final validation reopens and rehashes each. Require exactly ten unique expected golden IDs, totals derived from entries, reported-total equality, and at least one `ok`.

- [ ] **Step 5: Prove blocked validation never changes output**

Test absent output remains absent and a pre-existing output remains byte-identical for each blocker: evidence drift, stale probe, wrong provider/model/revision, dirty code, question cardinality/duplicate, falsified totals, all failures, generation-route/cache/call-count mismatch, cached planned IR not route-revalidated, unauthorized/repeated budget transition, budget overflow, canonicalization/literal/type version drift, serialized canonical question/resolved value, or missing v3 provenance.

- [ ] **Step 6: Run offline artifact verification**

```bash
.venv/bin/python -m pytest tests/test_text2sql_artifact.py tests/test_baseline_evaluation.py -v
```

Expected: all schema, mismatch, drift, generation-route/cache, call-count, value-free evidence, budget-transition/overflow, and atomic-writer tests pass without live credentials or real data.

- [ ] **Step 7: Run the live baseline only when external gates exist**

First run:

```bash
.venv/bin/python scripts/text2sql_preflight.py
```

If organizer credentials/capability or authoritative data evidence is missing, record the live baseline as `BLOCKED`; do not create an artifact. When all gates pass, run the explicit `cerebro baseline` command with manifest, bundle, receipt, database, capability-receipt directory, authorization-scope input, and output paths required by the implemented CLI help.

- [ ] **Step 8: Commit only if authorized**

```bash
git add src/cerebro/models.py src/cerebro/evaluation.py src/cerebro/cli.py scripts/text2sql_preflight.py tests/test_text2sql_artifact.py tests/test_baseline_evaluation.py
git commit -m "feat: bind live baseline to Option B evidence"
```

---

## Final Verification

- [ ] **Step 1: Run all targeted Text-to-SQL tests without networking**

```bash
CEREBRO_TEST_NO_NETWORK=1 env -u CEREBRO_API_KEY -u OPENAI_API_KEY -u CEREBRO_MODEL .venv/bin/python -m pytest \
  tests/test_text2sql_preflight.py \
  tests/test_load_duckdb.py \
  tests/test_text2sql_contract.py \
  tests/test_grounding_snapshot.py \
  tests/test_retrieval_api_mcp.py \
  tests/test_prompt_egress.py \
  tests/test_hosted_provider.py \
  tests/test_enrichment.py \
  tests/test_selfcheck_ir.py \
  tests/test_complexity_router.py \
  tests/test_sql_compiler.py \
  tests/test_selfcheck_sql.py \
  tests/test_selfcheck_semantics.py \
  tests/test_text2sql_cache.py \
  tests/test_text2sql_budget.py \
  tests/test_executor.py \
  tests/test_selfcheck_engine.py \
  tests/test_text2sql_agent.py \
  tests/test_baseline_evaluation.py \
  tests/test_text2sql_artifact.py -v
```

Expected: pass with no external socket and no live credential.

- [ ] **Step 2: Run the full repository suite**

```bash
CEREBRO_TEST_NO_NETWORK=1 env -u CEREBRO_API_KEY -u OPENAI_API_KEY -u CEREBRO_MODEL .venv/bin/python -m pytest -q
```

Expected: pass. Real-data/live-only checks report explicit blocked evidence through their commands rather than pytest skips that claim success.

- [ ] **Step 3: Run compile and diff checks**

```bash
.venv/bin/python -m compileall -q src scripts tests
git diff --check
```

Expected: both exit 0.

- [ ] **Step 4: Verify architectural invariants by search**

Search production source and assert:

- no `SQLCandidate`, `SQLDraft`, `_sql_prompt`, provider output SQL field, free-form `RelationalQueryIR.intent`, or `LiteralExpression.value`;
- every SQL-bound model operand uses `QuestionLiteralRef` or `GovernedLiteralRef`; only `sql_compiler.py` resolves refs and creates executable SQL ASTs/internal `BoundParameter.value`;
- only `GuardedProvider` renders hosted prompts, and `Text2SQLAgent` never consumes `OrganizerModelGateway` directly;
- `src/cerebro/text2sql.py`, `src/cerebro/hosted_provider.py`, `src/cerebro/text2sql_provider.py`, and `GoldenProvider` do not import `cerebro.enrichment`; existing enrichment tests pass unchanged;
- `GenerationRoute` contains only `default_ir`/`planned_ir`; no response/cache/evidence path uses `"cache"` as a generation route;
- only the production composition root constructs `Text2SQLAgent`; the actual `cli.py`/`evaluation.py` request constructors pass question plus trusted `AuthorizationScope`, never `retriever.grounding(...)`;
- `src/cerebro/api.py` remains grounding-only; `/api/grounding` and MCP do not construct `SQLGenerationRequest`, expose execution, or authorize a snapshot;
- no execution or validation failure path invokes provider generation;
- no cache hit bypasses canonical-span validation, `validate_ir` with the original generation route, `DialectCompiler.compile`, `authorize_compiled_query`, or `EngineValidator.validate`; cached planned IR reruns planned-route complex-node checks;
- the budget starts at capacity one; only current `AcceptedComplexRoute` calls `authorize_planned_ir`; no premature/repeated transition or third semantic call exists;
- no public response, value-free IR/assumption, cache payload, trace, or evaluation artifact serializes canonical question text, resolved literal values, or `BoundParameter.value`.

- [ ] **Step 5: Report external evidence separately**

Report these independently:

- offline suite: `VERIFIED` or exact failing command;
- authoritative real-data materialization: `VERIFIED` or `BLOCKED` with missing evidence;
- organizer capability: `VERIFIED` or `BLOCKED` with missing configuration/receipt;
- live unadapted baseline: `VERIFIED` only after final evidence validation, otherwise `BLOCKED`.

## Requirement Coverage

| Task | Requirements |
|---|---|
| 0 | FR-701–FR-703d, FR-724 |
| 1 | FR-700, FR-701, AC-712 |
| 2 | FR-704–FR-708, FR-715–FR-717, AC-713, AC-714 |
| 3 | FR-704, FR-704b, AC-700, AC-706, AC-715 |
| 4 | FR-702–FR-703d, AC-707, AC-711 |
| 5 | FR-703b, FR-704a, FR-707, FR-709, FR-710, FR-715, FR-716, AC-704, AC-709, AC-714 |
| 6 | FR-711, FR-712, AC-702, AC-705, AC-706 |
| 7 | FR-709a–FR-715, AC-700–AC-703, AC-705, AC-709 |
| 8 | FR-704b, FR-708, FR-716, FR-716a, AC-714, AC-715 |
| 9 | FR-714, FR-718–FR-722, AC-709 |
| 10 | FR-702–FR-708, FR-714–FR-722, AC-704, AC-711, AC-713–AC-715 |
| 11 | FR-703c, FR-703d, FR-704, FR-723, AC-706, AC-707, AC-710, AC-714, AC-715 |
| 12 | FR-724, FR-725, AC-708, AC-713–AC-715 |
