# 008 — Text-to-SQL Agent

## Problem

Spec 005 can retrieve semantic metadata, but the platform still needs a bounded path from a natural-language question to executable SQL. A hosted model may infer intent, but it cannot be trusted to choose authorized objects, write executable SQL, enforce disclosure policy, or decide whether a query is safe.

The workshop data makes prompt-only Text-to-SQL particularly unsafe:

- `transactions.amount` is unsigned; transaction direction exists only in `txn_type`, whose value mapping is not governed by the bundle.
- Transaction dates stop at `2026-06-30`; a relative window anchored on `CURRENT_DATE` can return empty or misleading periods.
- `table.transactions` and `table.branches` have no direct relationship; the governed route is through `table.accounts`.
- A syntactically valid query can still read an ungrounded table, use the wrong join, alter a governed metric, expose a sensitive value through an aggregate, or exceed an operational budget.

Direct model-generated SQL therefore remains untrusted even when it passes a parser. The system must constrain model output before SQL exists, compile accepted intent deterministically, then validate the resulting SQL, engine binding, disclosure lineage, and execution result before returning `status = ok`.

## Goal

Build a bounded Text-to-SQL agent that converts a question plus a trusted authorization scope into:

1. one versioned canonical question and an immutable, hash-addressed `GroundingSnapshot` containing only authorized metadata and explicitly authored governed constants;
2. one schema-bound, value-free `RelationalQueryIR` model output on the default path;
3. deterministic, parameterized DuckDB SQL compiled locally from the accepted IR after local literal resolution;
4. a read-only, bounded query result with typed provenance, assumptions, output lineage, disclosure records, generation route, independent cache status, budget, and attempt evidence.

The hosted model interprets intent. Local code canonicalizes the question, retrieves authorized context, validates ambiguity or IR, resolves literal references without persisting resolved values, compiles SQL, authorizes the compiled AST, runs mandatory `EXPLAIN`, executes within hard bounds, and decides the terminal response.

## Architecture Decision

The production default is **one-call typed IR plus deterministic compilation**:

```text
raw question + trusted authorization scope
    -> canonicalize/hash question
    -> authorized semantic retrieval
    -> freeze/hash GroundingSnapshot
    -> scoped cache lookup by GenerationRoute
    -> one organizer-model call
         -> RelationalQueryIR               (normal path), or
         -> ComplexQueryPlan                (guarded escalation), or
         -> GroundingRefusal                (locally verified), or
         -> ClarificationRequest            (locally validated)
    -> optional one-call planned-IR fallback only for AcceptedComplexRoute
    -> deterministic IR shape/type/literal validation
    -> deterministic local literal resolution and dialect compilation
    -> SQL AST / semantic / security / disclosure gates
    -> mandatory read-only EXPLAIN
    -> bounded read-only execution
    -> QueryOutcome + provenance
```

`GenerationRoute = Literal["default_ir", "planned_ir"]` records where an accepted IR was generated. Cache serving is orthogonal: `cache_status` is `disabled`, `miss`, or `hit`; `"cache"` is never a generation route. A cache hit preserves the cached IR's original generation route and reruns generation-route-aware validation, including the planned-route requirement for complex nodes.

A normal cache miss uses exactly one semantic model call. A complex request may use exactly two semantic calls in total: the first returns a typed `ComplexQueryPlan`, local validation produces an opaque `AcceptedComplexRoute`, and only that decision authorizes the second call that converts the accepted plan to `RelationalQueryIR`. A valid `ClarificationRequest` terminates as a local refusal without fallback or engine contact. There is no default candidate ensemble, tournament, model-written SQL, free-running repair loop, or execution-driven model retry.

## Non-Goals

- Authentication, identity-provider integration, tenant provisioning, or policy administration. A trusted upstream supplies `AuthorizationScope`; this spec verifies and enforces that scope.
- Conversation memory or autonomous multi-turn orchestration. A response may request clarification, but it does not maintain a conversation state machine.
- Self-hosting a model, generating synthetic training data, or adapting model weights (spec 010).
- Narrative interpretation or report generation (spec 009).
- Editing the OKF bundle. It is read-only input owned by another workstream.
- Returning database rows, sampled cell values, query results, secrets, or raw exceptions to a hosted provider.
- Claiming that deterministic checks prove the model understood the user's intent. They prove only the declared snapshot-to-IR, IR-to-SQL, SQL-to-engine, and output-policy contracts.

## Runtime Foundation

- **FR-700 — Atomic materialization:** Materialize externally supplied `archive/*.csv` into DuckDB using DDL derived from the bundle's declared columns, never CSV type inference. Preflight every required file and header before opening the target database. Load through a transaction or temporary database, verify schema and row counts, and atomically replace the target. A failure leaves any existing target byte-for-byte unchanged. The source-manifest digest is SHA-256 over UTF-8 canonical JSON from validated `SourceManifest.model_dump(mode="json")` with sorted keys, `ensure_ascii=False`, and separators `(",", ":")`. A successful load emits a value-free `MaterializationReceipt` containing manifest, bundle, table-file, row-count, database, and engine evidence.
- **FR-701 — Source-data fail closed:** Loading fails on a missing or extra file, header/order divergence, declared-type cast failure, unexpected table count, or checksum/row-count mismatch. Synthetic bundle-derived fixtures support offline tests; real-data verification remains an explicit live prerequisite.
- **FR-702 — Organizer model gateway:** Every Text-to-SQL semantic call goes through an organizer/BTC-supplied endpoint behind internal `OrganizerModelGateway` transport and the consumer-owned `Text2SQLGenerationProvider` protocol. `GuardedProvider` is the only provider boundary consumed by `Text2SQLAgent`; `hosted_provider.py`, `text2sql.py`, scripted/cassette adapters, and `GoldenProvider` conform structurally and never import `cerebro.enrichment`. The unrelated enrichment flow and `src/cerebro/enrichment.py::GenerationProvider` remain unchanged. Base URL, model ID, revision, schema mechanism, credentials, quotas, and retention configuration are runtime inputs, never hard-coded provider assumptions. Credentials never appear in prompts, logs, artifacts, exceptions, cassettes, cache keys, or committed files. Missing configuration yields `provider_configuration_error`; exhausted retryable transport faults yield `provider_unavailable`; sanitized non-retryable rejection yields `provider_rejected`.
- **FR-703 — Schema-bound semantic generation:** The default request asks for provider-native schema-bound `IRGenerationOutcome`, discriminated as `RelationalQueryIR`, `ComplexQueryPlan`, `GroundingRefusal`, or `ClarificationRequest`. A guarded fallback asks only for `RelationalQueryIR` bound to one locally produced `AcceptedComplexRoute`. A default union decode failure is `unparsable_generation_outcome`; a guarded fallback decode failure is `unparsable_fallback_ir`. No provider schema asks for SQL or accepts an arbitrary literal value or free-form intent field.
- **FR-703a — Egress by construction:** The public provider boundary accepts a local `GuardedGenerationRequest`, not a rendered prompt, arbitrary dictionary, prebuilt `PromptEnvelope`, SQL string, or result object. `GuardedProvider` constructs an envelope field-by-field, validates exact snapshot membership, and renders only the canonical question, metadata-only snapshot view (including explicitly authored governed literals), generation mode, accepted complex plan when applicable, and sanitized typed violation subjects. Source rows, sampled/discovered database values, result rows, resolved literal values, raw SQL literals, raw exceptions, secrets, arbitrary caller text, and output ordinals cannot enter the envelope. A blocked payload yields `egress_blocked` with zero additional inner-provider calls.
- **FR-703b — Failure, retry, and ambiguity domains:** Transport retries use bounded exponential backoff and are recorded separately from semantic calls. Schema decoding, clarification validation, snapshot/IR checking, complexity routing, literal resolution, compilation, AST authorization, engine validation, and execution are distinct typed domains. A valid `ClarificationRequest` is locally converted to `refused / clarification_required`; an invalid one yields `check_failed / invalid_clarification_request`. A later success never erases a recovered fault.
- **FR-703c — Offline tests:** The full automated suite runs with provider keys unset and IPv4/IPv6 networking disabled, using scripted outputs or versioned cassettes. Missing cassette entries fail loudly and never fall through to live mode.
- **FR-703d — Evaluation modes:** `offline_reference` and `live_unadapted_baseline` are distinct run kinds. Scripted or hand-authored outputs prove contract satisfiability only; they cannot be labelled as a live baseline, adaptation evidence, or leakage evidence.

## Functional Requirements

### Grounding, routing, and public contract

- **FR-704 — Authorized grounding snapshot and canonical question:** Before retrieval, `canonicalize_question` version `008.question.v1` applies Unicode NFC, removes leading/trailing Unicode whitespace, and collapses every internal run of one or more Unicode whitespace code points to one ASCII space; it does not case-fold or rewrite literals. The canonical-question hash is SHA-256 over its UTF-8 bytes. `GroundingResolver.resolve(canonical_question, scope)` first recomputes and verifies `AuthorizationScope.authorization_scope_hash`, then retrieves only objects permitted by that scope, applies deterministic top-k lexical/hybrid ranking and graph expansion, and freezes the result as `GroundingSnapshot`. The snapshot records semantic, policy, canonicalization, literal-registry, type-registry, and retrieval versions; authorization-scope hash; authorized object IDs; ranking evidence; normalized scalar and metric result types; metric formulas; relationship endpoints; column classifications; warning controls; dialect capabilities; and stable governed literals. A governed literal is an explicitly authored semantic constant with stable ID, scalar type, and canonical value; it is never sampled or discovered from database rows. The snapshot contains no database/source values. Scope and snapshot hashes are SHA-256 over canonical payload bytes with their own hash field excluded. `src/cerebro/api.py` remains a grounding-only advisory surface: `/api/grounding` and MCP return metadata-only `GroundingResponse`, never construct `SQLGenerationRequest`, and cannot authorize execution. Production `SQLGenerationRequest` accepts question plus trusted `AuthorizationScope` only; caller-supplied `GroundingResponse`, snapshot, or arbitrary grounding is rejected, and the composition root always invokes the resolver.
- **FR-704a — Complexity-aware bounded routing and typed clarification:** `GenerationRoute` is exactly `Literal["default_ir", "planned_ir"]`. The first `default_ir` call returns a complete IR, a typed complex plan, a missing-grounding proposal, or a typed `ClarificationRequest`. `ComplexityRouter` locally accepts escalation only when every requested operator belongs to a versioned complex-operator allowlist and the decomposition is connected to snapshot objects; only its opaque, snapshot/plan-bound `AcceptedComplexRoute` permits exactly one `planned_ir` call. The fallback IR must preserve accepted step dependencies, operators, object references, and declared outputs. A `ClarificationRequest` contains one or more strict `Ambiguity` records with canonical-question code-point spans and at least two distinct typed candidates. Candidates may identify exact snapshot semantic objects or relationships, stable snapshot governed literals, or version-allowlisted grain/operator choices; they contain no prose or raw values. Local validation checks `0 <= start < end <= len(canonical_question)`, exact versioned token boundaries, candidate distinctness, snapshot membership, homogeneous ambiguity kind, and deterministic relevance: object/relationship/governed-literal candidates must occur in ranking evidence or be graph-connected to such an object, while grain/operator choices must match the fixed synonym/applicability registry for the span and grounded inputs. A valid request returns `refused / clarification_required` with no fallback, SQL, `EXPLAIN`, or execution. Invalid spans, fewer than two distinct candidates, nonmembers, mixed kinds, or irrelevant candidates yield `check_failed / invalid_clarification_request`. Unsupported operators return `refused / unsupported_complexity`. Neither generation route authorizes SQL or execution.
- **FR-704b — Scoped, value-free IR cache:** Cache keys include authorization-scope hash, snapshot hash, policy version, canonicalization version, canonical-question hash, literal-registry version, dialect, `generation_route`, provider/model/revision, schema mechanism, prompt version, IR contract version, router version, compiler version, and checker/type-registry version. A cache payload contains only the value-free accepted IR, its original `generation_route`, accepted complex-plan hash when applicable, and integrity hash—never canonical question text, resolved literal values, compiled SQL, parameters, results, credentials, or raw provider content. `cache_status` is independently `disabled`, `miss`, or `hit`; `"cache"` is not a generation route. A hit preserves `default_ir` or `planned_ir`, revalidates question spans against the current canonical question, reruns snapshot membership, generation-route-aware complex-node and full IR/type checks, recompiles locally, and reruns AST/policy checks, `EXPLAIN`, and execution. A key mismatch is a miss; cross-tenant, cross-scope, cross-policy, cross-question, cross-canonicalization, or cross-version reuse is forbidden.
- **FR-705 — Discriminated response:** The public response is a strict union of `OkResponse`, `CheckFailedResponse`, and `RefusedResponse`, discriminated by `status`. `ResponseBase.generation_route` is `default_ir`, `planned_ir`, or `none` for failures before any generation outcome; `OkResponse` narrows it to `GenerationRoute`. Cache serving remains solely in `cache_status`. Invalid field combinations are schema errors, not conventions.
- **FR-706 — Meaning of `ok`:** `status = ok` requires a validated snapshot, accepted value-free IR, resolved internal literals, deterministic compiled query, every applicable gate in FR-709 through FR-715, successful mandatory `EXPLAIN`, and successful bounded execution yielding `QueryResult`. Only `OkResponse` carries result rows. It carries a parameterized `SQLArtifact` for authorized inspection; canonical-question text, resolved governed/question literal values, and bound parameter values remain execution-local and are excluded from public IR/responses, traces, caches, and evaluation artifacts.
- **FR-707 — Meaning of `refused`:** `status = refused` is locally derived and performs no engine contact. Allowed reasons are: `missing_grounding` after verifying every typed need is absent from the snapshot; `policy_disallowed` after an otherwise valid IR requests output with no permitted finite disclosure bound; `clarification_required` after validating model-proposed ambiguity or locally detecting an ungrounded, unresolvable, or invented physical literal; and `unsupported_complexity` after the router rejects an operator or decomposition. Model ambiguity is returned as locally validated `Ambiguity`; local literal failure is returned as one or more value-free `LiteralClarificationNeed` records containing only an issue code, expected type, optional target column, and optional literal ref—never the resolved/raw value. A model-proposed policy decision is never accepted. A false missing-grounding claim becomes `check_failed`; an invalid model-proposed ambiguity becomes `check_failed / invalid_clarification_request`.
- **FR-708 — Typed provenance and observability:** Every response records contract, semantic, policy, canonicalization, literal-registry, IR, type-registry, router, compiler, checker, prompt, provider/model/revision, dialect, canonical-question, snapshot, and authorization-scope hashes; `generation_route` and independent cache status; typed grounding usage and value-free assumptions; attempt records; budget usage; stage latency; and all observed violations. `OkResponse` additionally records the IR hash, value-free parameterized `SQLArtifact`, deterministic output lineage, and disclosure records. Traces contain no canonical question text, secrets, source values, result values, resolved literal values, bound parameter values, or raw provider/engine bodies.

### Deterministic IR, compiler, and SQL gates

No model participates in a gate below. Every gate is local, deterministic, provider-independent, and testable without network access.

- **FR-709 — IR containment, shape, connectivity, and types:** A `RelationalQueryIR` must have one reachable root, unique node IDs, an acyclic graph, no orphan nodes, at least one scan, and a connected relationship path for all physical tables. Every table, qualified column, relationship, metric, concept, policy, warning, assumption operand, disclosure source, and literal reference must be an exact member of the current canonical question/snapshot and authorization scope. Node and expression kinds are accepted only from version-pinned allowlists. A versioned local expression/type/signature registry normalizes snapshot scalar types and governed metric result types, then validates node input arity; availability of columns/aliases at each input; unique aliases in every output scope and unique root outputs; boolean filter predicates and `CASE` conditions; binary and `IN` operand compatibility; function arity/signatures; aggregate-only and window-only placement; planned-route-only windows/set operations; no aggregate in filters or group keys; no window outside `WindowNode`; no nested aggregate/window combination; and set-operation output arity and position-by-position type compatibility. Unknown, disconnected, duplicated, cyclic, unreachable, ill-typed, or unavailable content fails before literal resolution or compilation. Stable violation codes include `non_boolean_filter`, `invalid_function_signature`, `invalid_aggregate_placement`, `invalid_window_placement`, `nested_aggregate_or_window`, `duplicate_output_alias`, `invalid_node_arity`, `set_output_arity_mismatch`, and `set_output_type_mismatch`.
- **FR-709a — Compiled SQL-to-IR containment:** Parse compiler output with the pinned SQL parser and resolve aliases, CTEs, subqueries, implicit joins, and every set-operation leaf. Every physical table, qualified column, relationship equality, predicate, metric expression, grouping key, order key, window, set operation, row limit, resolved literal placeholder, and parameter position must have exactly one corresponding authorized IR declaration and be present in the canonical question or snapshot. Extra references, predicates, literals, or parameters fail. External scans and table-producing functions, including file, SQLite, PostgreSQL, extension, secret, and network sources, are denied by an empty default allowlist.
- **FR-710 — Stable warning controls:** IR references warnings by stable `(object_id, warning_hash)`, never by copying prose as authorization evidence. Every actionable warning maps to a deterministic control. When grounding lacks a required operand, the IR must carry the appropriate typed `DirectionMappingAssumption`, `StatusMappingAssumption`, `GrainMappingAssumption`, or `SnapshotAssumption`; every physical operand in those assumptions is a `LiteralRef`, never an embedded value. The checker resolves and binds those operands to the canonical question/snapshot, IR, and compiled AST without serializing resolved values. A governed `metric.transaction-volume` output uses positive `SUM(transactions.amount)` and cannot be reinterpreted as signed net. Signed flow requires an explicit complete direction mapping whose distinct non-empty sets resolve from question spans or authored governed literals. Missing or unenforceable controls fail.
- **FR-711 — Metric fidelity:** A `MetricExpression(metric_id)` compiles from the normalized governed formula in the snapshot; the model cannot supply or alter formula SQL. The final identified output must remain root-equivalent to that formula after permitted deterministic normalization. Dead CTE occurrences, changed denominators, hidden wrappers such as `formula + 1`, or a different output fail before engine contact.
- **FR-712 — Deterministic compiler, literal resolver, and read-only shape:** `DialectCompiler.compile(validated_ir, snapshot, canonical_question, max_rows)` is pure with respect to its explicit versioned inputs and emits `CompiledQuery(sql, parameters, ir_hash, compiler_version)`. Before literal resolution, it recomputes canonical IR, snapshot, and canonical-question hashes and verifies generation-route/accepted-plan-hash invariants; mutation after validation yields `validated_ir_integrity_error` and no partial SQL. `LiteralExpression` never contains a value. `QuestionLiteralRef` uses Unicode code-point `start`/`end` offsets into the exact `008.question.v1` canonical string plus a scalar type; `GovernedLiteralRef` names one stable governed literal in the snapshot. The versioned `008.literal-span.v1` scanner treats a matching single- or double-quoted substring (excluding quote delimiters, with escapes forbidden) as one token and otherwise treats the maximal run between ASCII space or one of `,;()[]{}?!` as one token. A question span must equal exactly one scanner token and contain no leading/trailing space. The type parser is exact: `string` returns the NFC token; `integer` accepts `[+-]?[0-9]+` or a case-insensitive single-token English word in `zero`–`nineteen` or the tens `twenty`–`ninety`; `decimal` accepts finite base-10 `[+-]?(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)` with no exponent; `boolean` accepts case-insensitive `true` or `false`; `date` accepts a calendar-valid `YYYY-MM-DD`; and `timestamp` accepts calendar-valid `YYYY-MM-DD[T ]HH:MM:SS` with optional fractional seconds and no implicit timezone conversion. Parsed values must satisfy the consuming signature's semantic bounds, such as positive relative amounts/limits. Governed literal IDs must exist and their snapshot type must match the expression signature. Any SQL-bound physical literal in expressions, `IN`/`CASE`, relative-time amount, IR row limit, or typed assumption operand must use one of these refs; invented, ungrounded, boundary-invalid, type-invalid, or unresolvable values produce local clarification and no SQL. The local literal resolver creates internal `BoundParameter` values only after FR-709 succeeds. The compiler allocates aliases and CTE names deterministically, expands only grounded relationships and metric formulas, parameterizes every resolved literal, enforces the effective row cap, and accepts no raw SQL fragment from a model. The post-compile parser uses version-pinned allowlists for root nodes, descendant AST classes, scalar/aggregate/window functions, and clauses. Exactly one read-only query expression is allowed. Safe `UNION`, `INTERSECT`, and `EXCEPT` are allowed only when `generation_route="planned_ir"` and every leaf independently passes all structural, semantic, lineage, and disclosure checks.
- **FR-713 — Classification and disclosure:** Classification propagates from source columns through IR expressions and compiled AST outputs. Direct or value-preserving restricted/confidential output requires an explicit IR disclosure covering every contributing source column, a finite SQL row limit at or below the configured cap (default 50), and a `DisclosureRecord`. Any IR-requested limit is a literal ref and must resolve locally; trusted deployment/request caps are local configuration, not model literals. Collection/string aggregates over sensitive values always fail. `FIRST`, `LAST`, `ANY_VALUE`, `MIN`, `MAX`, casts, concatenation, and equivalent value-preserving expressions use the complete bounded disclosure path. Reducing numeric aggregates use an explicit allowlist and, except for `COUNT`, require a checker-recognized minimum contributing-group size (default 5) without grouping by sensitive columns. An unbounded sensitive request becomes local `policy_disallowed` before compilation.
- **FR-714 — Mandatory engine validation:** Every executable path runs parameter-aware `EXPLAIN` against the same read-only DuckDB configuration used for execution. Validation has a hard wall-clock deadline and synchronized interruption/cleanup. Binder/type failures yield `explain_failed`; deadline expiry yields `explain_timeout`. No validation failure returns rows. Production construction requires both `EngineValidator` and `Executor`.
- **FR-715 — Structured, value-free ungoverned assumptions:** Ungoverned operands use a discriminated typed union, not free-form authorization text or embedded physical values. Direction mappings name one governed column plus distinct non-empty inflow/outflow sets of `LiteralRef`; status mappings bind a semantic state to literal refs; grain mappings identify source tables and target grouping keys while any physical grain operand uses a literal ref; snapshot assumptions identify a snapshot column and a typed interpretation whose physical operands are literal refs. Reader-facing prose is informational and excluded from authorization/cache payloads. Authorization uses only typed operands resolved against the canonical question/snapshot and bound to the IR and compiled AST. Public IR, assumptions, traces, cache entries, and evaluation evidence never serialize resolved values.
- **FR-716 — Global call, transport, deadline, token, and cost budgets:** Configurable deployment defaults are: initial semantic-call capacity `1`; maximum capacity after accepted complex routing `2`; maximum `2` transport attempts per semantic call; provider timeout `20_000 ms` per attempt; total input tokens `32_000`; total output tokens `8_000`; total cost `Decimal("0.50")` USD; and hard end-to-end deadline `120_000 ms`. A request starts at semantic-call capacity one. Before local complex acceptance, a `planned_ir` call, second semantic call, or direct capacity mutation is rejected. Only the opaque `AcceptedComplexRoute` produced by `ComplexityRouter` for the current snapshot/plan may invoke the one-way, one-time `RequestBudget.authorize_planned_ir(decision)` transition to capacity two. Repeated transitions and every third semantic call are rejected; no path exceeds two calls. The first semantic call must be `default_ir`; after the authorized transition, the second must be `planned_ir`. Unknown modes and out-of-order modes are rejected with `budget_exceeded` before changing the semantic-call count. Token, cost, transport, and deadline totals are monotonic across both semantic calls and all compiler/engine work. Semantic retries and candidate ensembles remain zero; transport retry alone may consume the second transport attempt for the same semantic call. Budget checks run before each provider/compiler/engine action; exhaustion yields `budget_exceeded` and no further contact. Execution, validation, or compiler failures never trigger a new semantic call.
- **FR-716a — Attempt accounting:** Every provider transport attempt, semantic call, cache decision, router decision, clarification validation, literal resolution, compilation, validation, and execution is recorded with domain, phase, ordinal, outcome, latency, sanitized violation codes, `generation_route`, and independent cache status where applicable. Call and cost accounting comes from actual gateway usage where available and conservative configured estimates otherwise.
- **FR-717 — Non-executable failure:** `CheckFailedResponse` may carry the last IR and a value-free `SQLArtifact` for authorized inspection, but marks it non-executable and never carries a result or bound parameter values. No caller can execute it without starting a new request through all current gates.

### Execution

- **FR-718 — Mandatory read-only executor:** Execute only after every preceding gate passes, using a read-only connection owned by the composition root. The validator and executor accept `CompiledQuery` SQL plus bound parameters; tests use explicit protocol-conforming fakes.
- **FR-719 — Resource bounds:** Defaults are a 1,000-row result cap, a 30-second `EXPLAIN` deadline, and a 30-second execute-plus-fetch deadline; all are configuration. Each phase uses one synchronized watchdog that interrupts at most once and joins before connection reuse. Fetch at most `max_rows + 1`; discard partial rows on timeout. Configure finite memory, threads, and temporary-directory limits where supported and record effective values.
- **FR-720 — Result contract:** Return column names, DuckDB types, rows, returned row count, truncation, and elapsed time. `OutputLineage` is ordered one-to-one with result columns. The extra truncation-detection row is never returned.
- **FR-721 — Execution failures:** A timeout yields `execution_timeout`; other sanitized failures yield `execution_error`. Neither causes provider recall or semantic repair. The terminal response is `check_failed` with no partial result.
- **FR-722 — Zero-row success:** A zero-row result after successful validation and execution is `ok`, is not retried, and carries complete metadata, lineage, generation route, independent cache status, budget, and provenance.

### Evaluation and downstream handoff

- **FR-723 — Non-vacuous offline reference:** A deterministic offline run covers metric fidelity, a two-hop relationship, relative time, bounded disclosure, zero-row execution, one `default_ir` one-call generation route, and one guarded `planned_ir` generation route. Every supported fixture reaches `ok`; an all-failure suite is not acceptance evidence.
- **FR-724 — Live baseline provenance:** A live unadapted baseline requires verified real data and a live unadapted organizer provider. It binds a new run ID to materialization and capability receipts; exact provider/model/revision/schema mechanism; prompt, canonicalization, literal registry, router, IR/type registry, compiler, checker, retrieval, policy, bundle, database, and golden-set hashes; effective call/token/cost/deadline/resource limits; per-question canonical-question/snapshot/scope hashes, original generation route, independent cache status, attempts, budget use, and terminal status. Evaluation evidence contains value-free IR/artifacts and never canonical question text or resolved/bound values. Final validation reopens retained evidence paths, requires exactly ten unique expected golden IDs, derives totals from entries, rejects reported-total mismatch, requires at least one `ok`, and atomically writes only a validated artifact. Missing or drifting evidence yields `BLOCKED` without creating or overwriting output.
- **FR-725 — Downstream-safe handoff:** Spec 009 consumes only `OkResponse` and uses lineage/disclosures to decide what may be narrated. Spec 010 consumes a baseline only when `run_kind = live_unadapted_baseline` and all FR-724 evidence is present. Hand-authored references cannot simultaneously serve as model output, live baseline, adaptation corpus source, and leakage oracle.

## Acceptance Criteria

- **AC-700:** No executed SQL contains a table, qualified column, relationship predicate, filter, metric expression, window, set operation, function, external source, physical literal, or parameter absent from the accepted value-free IR and its exact canonical question/snapshot. Ill-typed IR never reaches compilation.
- **AC-701:** A bounded top-five sensitive projection succeeds only when its limit is represented by a valid literal ref (or a stricter trusted local cap), with matching IR disclosure, compiled SQL limit, output lineage, and `DisclosureRecord`. An unbounded customer-name request returns `refused / policy_disallowed`. Sensitive collection/string aggregates fail categorically; value-preserving expressions declare every contributing sensitive source.
- **AC-702:** Every used metric originates from `MetricExpression(metric_id)` and compiles to an identified output root-equivalent to the governed formula. A model cannot submit formula SQL or embedded formula literals. Dead-CTE, changed-denominator, or `formula + 1` variants fail before engine contact.
- **AC-703:** A relative-time IR records the date column, data-relative anchor, amount as a question/governed literal ref, unit, and boundary semantics. GQ-08 resolves that ref locally, compiles to positive governed transaction volume anchored on `MAX(transactions.txn_date)`, rejects wall-clock anchors and invented amounts, and computes growth rather than only monthly volume.
- **AC-704:** For the ATM out-of-scope fixture, a `TableGroundingNeed(object_id="table.atms")` may yield `refused / missing_grounding` only after local absence verification. It emits no fallback call, SQL, `EXPLAIN`, or execution. A claimed-missing object present in the snapshot yields `check_failed`. A valid `ClarificationRequest` with exact canonical spans and at least two relevant distinct grounded/allowlisted candidates yields `refused / clarification_required` with the same no-contact guarantees; an invalid request yields `check_failed / invalid_clarification_request`.
- **AC-705:** A branch transaction query uses `relationship.transaction_account` then `relationship.account_branch`. The compiler emits both exact predicates; a direct transaction-to-branch edge cannot be represented by accepted IR and fails post-compile containment if introduced by a defect.
- **AC-706:** Identical accepted value-free IR, canonical question, snapshot, dialect, `generation_route`, resolved internal parameters, and compiler/versioned literal-registry inputs produce byte-equivalent canonical SQL, parameter order, IR hash, lineage inputs, and cache identity. Repeated live model calls are not claimed to be deterministic.
- **AC-707:** With keys unset and networking disabled, the suite passes. Only `GuardedProvider` can construct/render `PromptEnvelope`; forged envelopes, strings, dictionaries, ungrounded subjects, source/result/resolved literal values, secrets, raw SQL, and raw exceptions are blocked before the inner provider. Text-to-SQL production modules do not import `cerebro.enrichment`; existing enrichment provider behavior remains unchanged.
- **AC-708:** A live artifact contains exact snapshot, scope, retrieval, canonicalization, canonical-question, literal-registry, generation-route, cache, prompt, IR, type-registry, compiler, checker, provider, budget, materialization, bundle, database, and capability evidence for ten unique golden questions. Scripted output, stale evidence, dirty/unknown revision, falsified totals, missing data, serialized resolved values, or an all-`check_failed` run cannot satisfy it.
- **AC-709:** No FR-709 through FR-715 violation reaches `EXPLAIN` or execution. Named shape/type failures—including `non_boolean_filter`, `invalid_function_signature`, `invalid_aggregate_placement`, `invalid_window_placement`, `nested_aggregate_or_window`, `duplicate_output_alias`, `invalid_node_arity`, `set_output_arity_mismatch`, and `set_output_type_mismatch`—stop before compilation. A compiler defect detected by post-compile AST containment also stops before engine contact.
- **AC-710:** Offline fixtures for metric, two-hop join, relative time, bounded disclosure, true zero-row projection, and guarded complex operation reach `ok` with deterministic compiled SQL and results. Their scripted IR and evidence remain value-free.
- **AC-711:** Provider configuration, rejection, transport, egress, default-outcome decoding, fallback-IR decoding, clarification validation, router, IR type checking, literal resolution, compiler, cache-integrity, `EXPLAIN`, budget, and execution failures retain distinct typed codes and phase records. None is mislabeled as model-written SQL failure.
- **AC-712:** A load failure after any table begins loading leaves the previous target database byte-for-byte unchanged and no partial replacement.
- **AC-713:** Every successful response contains complete canonical-question/snapshot/scope hashes, accepted value-free IR and hash, a value-free parameterized `SQLArtifact`, original `generation_route`, independent cache evidence, grounding usage, value-free assumptions, output lineage, disclosures, attempts, budgets, provider/model identity, and contract/semantic/policy/canonicalization/literal/type/compiler versions required downstream. Canonical question text, resolved literal values, and bound parameter values remain internal and do not serialize.
- **AC-714:** A normal cache miss starts at capacity one and makes exactly one semantic model call. Only a locally produced `AcceptedComplexRoute` performs the one-time transition to capacity two and permits exactly one `planned_ir` call; a premature transition, repeated transition, or third call fails before contact. A validated cache hit makes zero semantic calls, preserves the original `default_ir` or `planned_ir` generation route, reruns generation-route-aware validation, and recompiles. No path generates parallel candidates or performs execution-driven model repair.
- **AC-715:** Changing tenant/scope, policy, snapshot, canonicalization version, canonical-question hash, literal registry, dialect, generation route, provider/model/revision, prompt, IR contract, router, compiler, type/checker version produces a cache miss. Even an exact hit preserves its original generation route, revalidates question spans and IR—including planned-route complex-node checks—recompiles locally, reruns AST/policy gates, and performs mandatory `EXPLAIN`; only `cache_status` changes to `hit`.

## Edge Cases

- Empty authorized retrieval, false missing-grounding claims, syntactically valid but unauthorized object IDs, and caller-supplied advisory `GroundingResponse`.
- Empty-after-canonicalization questions; NFC-changing inputs; non-ASCII whitespace; stale code-point spans; spans that split a token; quoted multi-word literals; and invalid scalar parsing.
- Invented embedded values, ungrounded governed-literal IDs, and typed assumption operands that cannot resolve without exposing their values.
- Same column name in multiple grounded tables; all IR references remain qualified and available from the node input.
- Cyclic, disconnected, duplicate, orphaned, unreachable, wrong-arity, or ill-typed IR nodes; duplicate aliases or root outputs.
- Non-boolean filters/`CASE` conditions, incompatible `IN` operands, bad function signatures, aggregate/window placement errors, and nested aggregates/windows.
- A simple query escalated without an allowed complex operator or a premature attempt to authorize `planned_ir` budget capacity.
- A complex plan containing one supported and one unsupported operator; repeated budget transition; or a third semantic call.
- A fallback IR that diverges from its accepted complex plan.
- A cache hit for planned IR mislabeled as `cache`, downgraded to `default_ir`, or revalidated without planned-route complex-node rules.
- A model ambiguity with an out-of-bounds/non-token span, duplicate candidates, mixed candidate kinds, ungrounded IDs, fewer than two candidates, or irrelevant choices.
- Nested CTE alias shadowing or a compiler defect that introduces an undeclared predicate, literal, or parameter.
- `UNION`, `INTERSECT`, or `EXCEPT` with one unsafe leaf, arity mismatch, or positionally incompatible output type.
- Sensitive values hidden in collection, value-preserving, cast, concatenation, `CASE`, min/max, or window expressions.
- Aggregate groups below the minimum contribution threshold.
- Relative time incorrectly anchored to wall-clock time or with an invented/unresolvable amount.
- Zero rows caused by valid filters versus a semantically wrong filter; only declared contracts are provable.
- Cache entries from another authorization scope, policy, snapshot, canonicalization/literal registry, canonical question, generation route, or compiler version.
- Budget expiry between the initial outcome and guarded fallback; cumulative token/cost exhaustion across both calls or engine work.
- Timeout during `EXPLAIN`, execution, or fetch; no partial rows escape.
- Provider transport failure after a recovered attempt; all attempts remain visible and each semantic call has at most two attempts.
- Vietnamese question with English semantic metadata and code-point offsets that remain valid after NFC canonicalization.
- Real CSVs or organizer API unavailable: offline tests continue and live baseline is blocked.

## Interfaces / Contracts

The following shapes are normative. Implementation may split models across files, but every serializable boundary model uses `ConfigDict(extra="forbid")`, every serializable union is discriminated, every identifier is validated, and immutable evidence models reject mutation. Local authority capabilities are deliberately non-Pydantic and have no accepted wire/dict/JSON construction path.

```python
class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
NodeId = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
ColumnName = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*$")]
TableId = Annotated[str, Field(pattern=r"^table\.[a-z][a-z0-9_]*$")]
MetricId = Annotated[str, Field(pattern=r"^metric\.[a-z][a-z0-9_-]*$")]
RelationshipId = Annotated[str, Field(pattern=r"^relationship\.[a-z][a-z0-9_]*$")]
PolicyId = Annotated[str, Field(pattern=r"^policy\.[a-z][a-z0-9_-]*$")]
SemanticObjectId = Annotated[
    str,
    Field(pattern=r"^(dataset|table|concept|relationship|metric|policy)\.[a-z][a-z0-9_-]*$"),
]
Classification = Literal["public", "internal", "confidential", "restricted"]
ScalarType = Literal["string", "integer", "decimal", "boolean", "date", "timestamp"]
JsonScalar = str | int | float | bool | None  # internal/snapshot use only; never stored in IR
GenerationRoute = Literal["default_ir", "planned_ir"]
GovernedLiteralId = Annotated[
    str,
    Field(pattern=r"^literal\.[a-z][a-z0-9_-]*$"),
]
AllowedFunction = Literal[
    "count", "sum", "avg", "min", "max", "stddev", "variance",
    "date_trunc", "nullif", "coalesce",
]
AllowedBinaryOperator = Literal[
    "eq", "neq", "lt", "lte", "gt", "gte", "and", "or",
    "add", "subtract", "multiply", "divide",
]
ComplexOperatorId = Literal[
    "window.period_over_period.v1", "set_operation.safe_binary.v1",
]
QUESTION_CANONICALIZATION_VERSION = "008.question.v1"
LITERAL_SPAN_REGISTRY_VERSION = "008.literal-span.v1"
EXPRESSION_TYPE_REGISTRY_VERSION = "008.types.v1"


def canonicalize_question(question: str) -> str:
    normalized = unicodedata.normalize("NFC", question)
    return re.sub(r"\s+", " ", normalized.strip(), flags=re.UNICODE)


class AuthorizationScope(StrictFrozenModel):
    scope_version: Literal["008.scope.v1"]
    tenant_scope_hash: Sha256
    policy_version: str
    allowed_object_ids: frozenset[SemanticObjectId]
    allowed_classifications: frozenset[Classification]
    authorization_scope_hash: Sha256


class SnapshotColumn(StrictFrozenModel):
    ref: "ColumnRef"
    data_type: ScalarType
    description: str
    classification: Classification


class SnapshotRelationship(StrictFrozenModel):
    relationship_id: RelationshipId
    left: "ColumnRef"
    right: "ColumnRef"


class SnapshotWarning(StrictFrozenModel):
    object_id: SemanticObjectId
    warning_hash: Sha256
    kind: Literal["actionable", "informational"]
    control_id: str


class SnapshotGovernedLiteral(StrictFrozenModel):
    literal_id: GovernedLiteralId
    data_type: ScalarType
    value: JsonScalar  # authored semantic constant, never sampled/discovered data
    source_object_id: SemanticObjectId


class SnapshotMetadataObject(StrictFrozenModel):
    object_id: SemanticObjectId
    object_type: Literal["dataset", "table", "concept", "relationship", "metric", "policy"]
    description: str
    columns: tuple[SnapshotColumn, ...] = ()
    formula: str | None = None
    metric_result_type: ScalarType | None = None
    relationships: tuple[SnapshotRelationship, ...] = ()
    warnings: tuple[SnapshotWarning, ...] = ()


class GroundingSnapshot(StrictFrozenModel):
    snapshot_version: Literal["008.grounding.v1"]
    semantic_version: str
    policy_version: str
    canonicalization_version: Literal["008.question.v1"]
    literal_registry_version: Literal["008.literal-span.v1"]
    type_registry_version: Literal["008.types.v1"]
    authorization_scope_hash: Sha256
    retrieval_config_hash: Sha256
    dialect: Literal["duckdb"]
    objects: tuple[SnapshotMetadataObject, ...]
    governed_literals: tuple[SnapshotGovernedLiteral, ...] = ()
    ranking_evidence: tuple[RankedResult, ...]
    snapshot_hash: Sha256


class ColumnRef(StrictModel):
    table_id: TableId
    column: ColumnName


class ColumnExpression(StrictModel):
    kind: Literal["column"] = "column"
    ref: ColumnRef


class OutputExpression(StrictModel):
    kind: Literal["output"] = "output"
    node_id: NodeId
    alias: ColumnName


class MetricExpression(StrictModel):
    kind: Literal["metric"] = "metric"
    metric_id: MetricId


class QuestionLiteralRef(StrictModel):
    kind: Literal["question"] = "question"
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    data_type: ScalarType


class GovernedLiteralRef(StrictModel):
    kind: Literal["governed"] = "governed"
    literal_id: GovernedLiteralId


LiteralRef = Annotated[
    QuestionLiteralRef | GovernedLiteralRef,
    Field(discriminator="kind"),
]


class LiteralExpression(StrictModel):
    kind: Literal["literal"] = "literal"
    ref: LiteralRef


class FunctionExpression(StrictModel):
    kind: Literal["function"] = "function"
    function: AllowedFunction
    arguments: tuple["IRExpression", ...]


class BinaryExpression(StrictModel):
    kind: Literal["binary"] = "binary"
    operator: AllowedBinaryOperator
    left: "IRExpression"
    right: "IRExpression"


class InExpression(StrictModel):
    kind: Literal["in"] = "in"
    expression: "IRExpression"
    values: tuple[LiteralExpression, ...] = Field(min_length=1, max_length=100)
    negated: bool = False


class WhenThen(StrictModel):
    when: "IRExpression"
    then: "IRExpression"


class CaseExpression(StrictModel):
    kind: Literal["case"] = "case"
    branches: tuple[WhenThen, ...] = Field(min_length=1)
    else_expression: "IRExpression"


class RelativeTimeExpression(StrictModel):
    kind: Literal["relative_time"] = "relative_time"
    date_column: ColumnRef
    anchor: Literal["data_max"]
    amount_ref: LiteralRef
    unit: Literal["day", "week", "month", "quarter", "year"]
    lower_inclusive: bool
    upper_inclusive: bool


IRExpression = Annotated[
    ColumnExpression | OutputExpression | MetricExpression | LiteralExpression |
    FunctionExpression | BinaryExpression | InExpression | CaseExpression |
    RelativeTimeExpression,
    Field(discriminator="kind"),
]


class NamedExpression(StrictModel):
    alias: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*$")]
    expression: IRExpression


class SortKey(StrictModel):
    expression: IRExpression
    direction: Literal["asc", "desc"]
    nulls: Literal["first", "last"]


class WindowExpression(StrictModel):
    alias: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*$")]
    function: Literal["lag", "lead", "row_number", "rank", "dense_rank"]
    argument: IRExpression | None = None
    partition_by: tuple[IRExpression, ...] = ()
    order_by: tuple[SortKey, ...] = ()
    offset: LiteralRef | None = None


class ScanNode(StrictModel):
    kind: Literal["scan"] = "scan"
    node_id: NodeId
    table_id: TableId


class JoinNode(StrictModel):
    kind: Literal["join"] = "join"
    node_id: NodeId
    left_id: NodeId
    right_id: NodeId
    relationship_id: RelationshipId
    join_type: Literal["inner", "left"]


class FilterNode(StrictModel):
    kind: Literal["filter"] = "filter"
    node_id: NodeId
    input_id: NodeId
    predicate: IRExpression


class AggregateNode(StrictModel):
    kind: Literal["aggregate"] = "aggregate"
    node_id: NodeId
    input_id: NodeId
    group_by: tuple[NamedExpression, ...]
    measures: tuple[NamedExpression, ...]
    minimum_group_size: LiteralRef | None = None


class ProjectNode(StrictModel):
    kind: Literal["project"] = "project"
    node_id: NodeId
    input_id: NodeId
    outputs: tuple[NamedExpression, ...]


class SortNode(StrictModel):
    kind: Literal["sort"] = "sort"
    node_id: NodeId
    input_id: NodeId
    keys: tuple[SortKey, ...]


class LimitNode(StrictModel):
    kind: Literal["limit"] = "limit"
    node_id: NodeId
    input_id: NodeId
    count: LiteralRef


class WindowNode(StrictModel):
    kind: Literal["window"] = "window"
    node_id: NodeId
    input_id: NodeId
    outputs: tuple[WindowExpression, ...]


class SetOperationNode(StrictModel):
    kind: Literal["set_operation"] = "set_operation"
    node_id: NodeId
    left_id: NodeId
    right_id: NodeId
    operator: Literal["union", "intersect", "except"]
    all: bool = False


IRNode = Annotated[
    ScanNode | JoinNode | FilterNode | AggregateNode | ProjectNode |
    SortNode | LimitNode | WindowNode | SetOperationNode,
    Field(discriminator="kind"),
]


class RelationalQueryIR(StrictModel):
    outcome: Literal["ir"] = "ir"
    ir_version: Literal["008.ir.v1"]
    root_node_id: NodeId
    nodes: tuple[IRNode, ...]
    warning_decisions: tuple[WarningDecision, ...] = ()
    assumptions: tuple[Assumption, ...] = ()
    requested_disclosures: tuple[RequestedDisclosure, ...] = ()


class ValidatedIR(StrictFrozenModel):
    ir: RelationalQueryIR
    ir_hash: Sha256
    snapshot_hash: Sha256
    canonical_question_hash: Sha256
    generation_route: GenerationRoute
    accepted_complex_plan_hash: Sha256 | None = None


class CachedGeneration(StrictFrozenModel):
    ir: RelationalQueryIR
    generation_route: GenerationRoute
    accepted_complex_plan_hash: Sha256 | None = None
    payload_sha256: Sha256


class ComplexPlanStep(StrictModel):
    step_id: NodeId
    operator_id: ComplexOperatorId
    depends_on: tuple[NodeId, ...] = ()
    input_object_ids: tuple[SemanticObjectId, ...]
    output_names: tuple[str, ...]


class ComplexQueryPlan(StrictModel):
    outcome: Literal["complex_plan"] = "complex_plan"
    plan_version: Literal["008.complex-plan.v1"]
    operator_ids: tuple[ComplexOperatorId, ...]
    steps: tuple[ComplexPlanStep, ...]
    expected_outputs: tuple[str, ...]


_ACCEPTED_COMPLEX_ROUTE_TOKEN = object()  # module-private in models.py


@dataclass(frozen=True, slots=True, init=False)
class AcceptedComplexRoute:
    """Opaque, immutable, non-Pydantic capability; direct/wire construction is disabled."""
    generation_route: Literal["planned_ir"]
    snapshot_hash: Sha256
    plan_hash: Sha256
    plan: ComplexQueryPlan
    _router_token: object = field(repr=False, compare=False)


def _create_accepted_complex_route(
    *,
    snapshot_hash: Sha256,
    plan_hash: Sha256,
    plan: ComplexQueryPlan,
) -> AcceptedComplexRoute:
    decision = object.__new__(AcceptedComplexRoute)
    object.__setattr__(decision, "generation_route", "planned_ir")
    object.__setattr__(decision, "snapshot_hash", snapshot_hash)
    object.__setattr__(decision, "plan_hash", plan_hash)
    object.__setattr__(decision, "plan", plan)
    object.__setattr__(decision, "_router_token", _ACCEPTED_COMPLEX_ROUTE_TOKEN)
    return decision


class ObjectAmbiguityCandidate(StrictModel):
    kind: Literal["object"] = "object"
    object_id: SemanticObjectId


class RelationshipAmbiguityCandidate(StrictModel):
    kind: Literal["relationship"] = "relationship"
    relationship_id: RelationshipId


class GovernedLiteralAmbiguityCandidate(StrictModel):
    kind: Literal["governed_literal"] = "governed_literal"
    literal_id: GovernedLiteralId


class GrainAmbiguityCandidate(StrictModel):
    kind: Literal["grain"] = "grain"
    grain: Literal["row", "day", "week", "month", "quarter", "year"]
    grouping_columns: tuple[ColumnRef, ...] = Field(min_length=1)


class OperatorAmbiguityCandidate(StrictModel):
    kind: Literal["operator"] = "operator"
    operator_id: ComplexOperatorId


AmbiguityCandidate = Annotated[
    ObjectAmbiguityCandidate | RelationshipAmbiguityCandidate |
    GovernedLiteralAmbiguityCandidate | GrainAmbiguityCandidate |
    OperatorAmbiguityCandidate,
    Field(discriminator="kind"),
]


class Ambiguity(StrictModel):
    ambiguity_id: NodeId
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    candidates: tuple[AmbiguityCandidate, ...] = Field(min_length=2)


class ClarificationRequest(StrictModel):
    outcome: Literal["clarification_request"] = "clarification_request"
    ambiguities: tuple[Ambiguity, ...] = Field(min_length=1)


class LiteralClarificationNeed(StrictModel):
    kind: Literal["literal_need"] = "literal_need"
    issue: Literal[
        "missing_literal", "invalid_question_span", "unparseable_question_literal",
        "ungrounded_governed_literal", "literal_type_mismatch", "invented_literal_reference",
    ]
    expected_type: ScalarType
    target_column: ColumnRef | None = None
    literal_ref: LiteralRef | None = None


class GroundingRefusal(StrictModel):
    outcome: Literal["grounding_refusal"] = "grounding_refusal"
    unmet_needs: tuple[GroundingNeed, ...]


IRGenerationOutcome = Annotated[
    RelationalQueryIR | ComplexQueryPlan | GroundingRefusal | ClarificationRequest,
    Field(discriminator="outcome"),
]


@dataclass(frozen=True, slots=True)
class GuardedGenerationRequest:
    """Local-only request; no model/dict/JSON construction path is accepted."""
    mode: Literal["default_ir", "planned_ir", "provider_probe"]
    canonical_question: str
    snapshot: GroundingSnapshot
    accepted_complex_route: AcceptedComplexRoute | None = None
    prior_violations: tuple[CheckViolation, ...] = ()


OutputT = TypeVar("OutputT", bound=BaseModel)


class ProviderGeneration(StrictFrozenModel, Generic[OutputT]):
    output: OutputT
    transport_attempts: tuple[TransportAttempt, ...]
    usage: ProviderUsage


@runtime_checkable
class Text2SQLGenerationProvider(Protocol):
    provider: str
    model: str
    model_revision: str
    schema_mechanism: str

    def generate(
        self,
        request: GuardedGenerationRequest,
        output_adapter: TypeAdapter[OutputT],
    ) -> ProviderGeneration[OutputT]: ...


class BoundParameter(StrictFrozenModel):
    position: int = Field(ge=1)
    data_type: ScalarType
    value: JsonScalar = Field(exclude=True)


class CompiledQuery(StrictFrozenModel):
    sql: str
    parameters: tuple[BoundParameter, ...]
    ir_hash: Sha256
    compiler_version: str
    dialect: Literal["duckdb"]


class SQLArtifact(StrictFrozenModel):
    sql: str
    sql_sha256: Sha256
    parameter_count: int = Field(ge=0)
    parameter_types: tuple[ScalarType, ...]
    ir_hash: Sha256
    compiler_version: str
    dialect: Literal["duckdb"]


class BudgetLimits(StrictFrozenModel):
    initial_semantic_call_capacity: Literal[1] = 1
    planned_semantic_call_capacity: Literal[2] = 2
    max_transport_attempts_per_semantic_call: int = Field(default=2, ge=1, le=2)
    provider_timeout_ms_per_attempt: int = Field(default=20000, gt=0)
    max_input_tokens: int = Field(default=32000, gt=0)
    max_output_tokens: int = Field(default=8000, gt=0)
    max_cost_usd: Decimal = Field(default=Decimal("0.50"), gt=0)
    end_to_end_deadline_ms: int = Field(default=120000, gt=0)

    @classmethod
    def defaults(cls) -> "BudgetLimits":
        return cls()


class BudgetUsage(StrictModel):
    semantic_call_capacity: Literal[1, 2]
    planned_ir_authorized: bool
    semantic_calls: int
    transport_attempts: int
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal
    elapsed_ms: int


class RequestBudget:
    def authorize_planned_ir(self, decision: AcceptedComplexRoute) -> None:
        """One-way 1 -> 2 capacity transition; reject stale/foreign/repeated decisions."""


class SQLGenerationRequest(StrictModel):
    question: str
    authorization_scope: AuthorizationScope
    dialect: Literal["duckdb"] = "duckdb"
    max_rows: int = 1000


class ResponseBase(StrictModel):
    contract_version: Literal["008.v3"]
    semantic_version: str
    policy_version: str
    canonicalization_version: Literal["008.question.v1"]
    literal_registry_version: Literal["008.literal-span.v1"]
    ir_contract_version: Literal["008.ir.v1"]
    type_registry_version: Literal["008.types.v1"]
    prompt_version: str
    router_version: str
    compiler_version: str
    checker_version: str
    dialect: Literal["duckdb"]
    provider: str
    model: str
    model_revision: str
    canonical_question_hash: Sha256
    authorization_scope_hash: Sha256
    snapshot_hash: Sha256 | None = None
    generation_route: GenerationRoute | Literal["none"]
    cache_status: Literal["disabled", "miss", "hit"]
    grounding_usage: GroundingUsage
    assumptions: tuple[Assumption, ...]
    attempt_records: tuple[AttemptRecord, ...]
    budget_usage: BudgetUsage
    violations: tuple[CheckViolation, ...] = ()


class OkResponse(ResponseBase):
    status: Literal["ok"] = "ok"
    generation_route: GenerationRoute
    snapshot_hash: Sha256
    ir: RelationalQueryIR
    sql_artifact: SQLArtifact
    result: QueryResult
    output_lineage: tuple[OutputLineage, ...]
    disclosures: tuple[DisclosureRecord, ...]


class CheckFailedResponse(ResponseBase):
    status: Literal["check_failed"] = "check_failed"
    executable: Literal[False] = False
    ir: RelationalQueryIR | None = None
    sql_artifact: SQLArtifact | None = None
    violations: tuple[CheckViolation, ...] = Field(min_length=1)


class RefusedResponse(ResponseBase):
    status: Literal["refused"] = "refused"
    reason: Literal[
        "missing_grounding", "policy_disallowed",
        "clarification_required", "unsupported_complexity",
    ]
    unmet_needs: tuple[GroundingNeed, ...] = ()
    ambiguities: tuple[Ambiguity, ...] = ()
    literal_needs: tuple[LiteralClarificationNeed, ...] = ()
    policy_ids: tuple[PolicyId, ...] = ()
    unsupported_operator_ids: tuple[ComplexOperatorId, ...] = ()


SQLGenerationResponse = Annotated[
    OkResponse | CheckFailedResponse | RefusedResponse,
    Field(discriminator="status"),
]
```

`DirectionMappingAssumption`, `StatusMappingAssumption`, `GrainMappingAssumption`, `SnapshotAssumption`, `RequestedDisclosure`, `WarningRef`, `WarningDecision`, `AttemptRecord`, `GroundingUsage`, `OutputLineage`, `DisclosureRecord`, `MaterializationReceipt`, `ProviderCapabilityReceipt`, and live evidence contracts retain their strict typed semantics from the previous contract revision. Every SQL-bound physical operand in an assumption is changed to `LiteralRef`; no assumption or reader-facing field may embed or serialize the resolved value. Their references now bind to canonical question/snapshot state, `RelationalQueryIR`, internal `CompiledQuery`, and value-free public `SQLArtifact` rather than a model-written plan/SQL pair. `AcceptedComplexRoute` and `GuardedGenerationRequest` are local-only, non-Pydantic capabilities with no accepted wire/dict/JSON construction path: `models.py` keeps the constructor token and module-private factory, only `ComplexityRouter` calls that factory after validation, `GuardedProvider` rejects dictionaries/model payloads, and both `RequestBudget.authorize_planned_ir` and `GuardedProvider` recompute the current plan hash, verify the capability token plus current snapshot/plan hashes, and reject mutation before consuming/rendering the capability.

Cross-model validators require metric snapshot objects to carry both `formula` and `metric_result_type`, non-metric objects to reject metric-only fields, every column/governed literal type to belong to `ScalarType`, and every governed constant's canonical value to validate exactly against its declared scalar type. They require `ValidatedIR` and `CachedGeneration` with `generation_route="planned_ir"` to carry `accepted_complex_plan_hash`, while `generation_route="default_ir"` rejects that hash and every complex node. Cache key, payload route, validated-IR route, and accepted-plan hash must agree. `RefusedResponse(reason="clarification_required")` requires at least one locally validated `Ambiguity` or `LiteralClarificationNeed`; model-originated clarification uses `ambiguities`, local literal-resolution clarification uses `literal_needs`, and unrelated refusal reasons reject both fields. A clarification produced by the first model call records `generation_route="default_ir"`; a literal clarification found while checking IR preserves that IR's `default_ir` or `planned_ir` generation route. Only failures before a generation outcome use `generation_route="none"`.

Required module boundaries:

| Module | Responsibility | Model call? |
|---|---|---|
| `scripts/load_duckdb.py` | source preflight, atomic materialization, receipts | no |
| `src/cerebro/models.py` | strict scope, snapshot, governed-literal, ambiguity, IR, response, provenance, budget, and evidence contracts | no |
| `src/cerebro/provenance.py` | canonical question/JSON helpers and content hashes | no |
| `src/cerebro/retrieval.py` | authorization-first retrieval, graph expansion, governed semantic constants, snapshot freeze/hash | no hosted generation call |
| `src/cerebro/api.py` | existing advisory `/api/grounding` and MCP metadata retrieval only; never constructs `SQLGenerationRequest` or authorizes execution | no |
| `src/cerebro/text2sql_provider.py` | consumer-owned generic `Text2SQLGenerationProvider` protocol and `ProviderGeneration[OutputT]` | no |
| `src/cerebro/enrichment.py` | existing enrichment-only `GenerationProvider`; explicitly unchanged and not imported by Text-to-SQL modules | enrichment calls only |
| `src/cerebro/prompting.py` | strict metadata-only envelope construction and membership authorization | no |
| `src/cerebro/hosted_provider.py` | internal organizer transport plus protocol-conforming guarded/cassette/scripted adapters; no enrichment dependency | live network only behind guard |
| `src/cerebro/complexity.py` | complex-plan validation and opaque `AcceptedComplexRoute` production | no |
| `src/cerebro/sql_compiler.py` | pure canonical-question/snapshot literal resolution and validated-IR to parameterized dialect SQL compilation | no |
| `src/cerebro/text2sql_cache.py` | scoped cache keys and integrity-checked value-free accepted IR plus original generation route | no |
| `src/cerebro/selfcheck.py` | clarification, snapshot, IR shape/type, AST, metric, warning, assumption, lineage, and disclosure gates | no |
| `src/cerebro/executor.py` | mandatory parameter-aware `EXPLAIN` and bounded read-only execution | no |
| `src/cerebro/text2sql.py` | budgets, generation-route/cache orchestration, gate ordering, and response assembly | only through `GuardedProvider` protocol boundary |
| `src/cerebro/evaluation.py` | trusted-scope/resolver composition, offline reference, and live baseline evidence | no direct call |
| `src/cerebro/cli.py` | trusted-scope `ask` plus explicit reference/baseline composition | no direct call |

## Constraints

- `sqlglot` is the only SQL parser; regex-based SQL authorization is prohibited.
- DuckDB and `sqlglot` versions are exact pins and their compatibility surface is tested.
- The model never emits SQL, raw literal values, or free-form IR intent, and no compiler API accepts a model-provided SQL fragment.
- Hosted egress contains the canonical question and authorized metadata only and passes through one guarded composition root.
- Temperature zero may be requested but is not a determinism guarantee.
- Every production path has a trusted `AuthorizationScope`, `GroundingResolver`, `GuardedProvider`, `EngineValidator`, and `Executor`.
- Advisory `GroundingResponse` from HTTP/MCP is never an input to `SQLGenerationRequest` and cannot authorize SQL generation or execution.
- `GenerationRoute` has only `default_ir` and `planned_ir`; cache service is represented only by `cache_status`.
- Cache hits preserve generation route and do not skip canonical span/literal resolution, generation-route-aware IR/type checks, deterministic compilation, `EXPLAIN`, or execution bounds.
- Resolved literal and `BoundParameter.value` data remain internal to compiler/executor memory and never enter public/cache/evaluation models.
- Text-to-SQL production modules do not import `cerebro.enrichment`; enrichment retains its existing provider contract and tests.
- DuckDB validation and execution connections are read-only.
- `knowledge/bank-workshop/` remains read-only.
- No test, CLI command, or evaluation mode silently falls back among scripted, cassette, and live providers.
- No live prerequisite may be converted into a passing skip.

## Assumptions

- A trusted upstream creates `AuthorizationScope`; this spec does not authenticate users or add an unauthenticated SQL endpoint.
- Semantic metadata, explicitly authored governed literal constants, and policy definitions are available locally through the existing bundle/retrieval layer; database-discovered values are not semantic constants.
- Real `archive/*.csv` files, their authoritative manifest, and checksums are external prerequisites.
- Provider-native schema binding is capability-tested before a live run.
- Organizer provider identity, model/revision, endpoint, quota, schema mechanism, and retention terms are configuration and provenance.
- Classification and normalized scalar/metric result types come from the snapshot/type registry at runtime and are never fixture-hard-coded into production gates.
- The initial compiler targets DuckDB; the IR is dialect-neutral within the declared operator set.
- Module layout remains flat under `src/cerebro/`.

## Open Questions

These questions block only their named live capability, not offline implementation:

- Which organizer/BTC endpoint, auth scheme, model/revision, schema mechanism, quota, token accounting, and retention terms will be supplied. This blocks live gateway verification and the live baseline.
- Where the authoritative real CSV manifest and checksums are distributed. This blocks verified real-data materialization and the live baseline.
- Whether the semantic-layer owner will govern `txn_type` direction values. Until then, typed direction assumptions remain mandatory for signed flow.
- Which complex operators beyond `window` and safe set operations are approved for the first production allowlist. Unapproved operators remain fail-closed.

## Test Design

| Test | Requirements | Level | Verification |
|---|---|---|---|
| T-700 | FR-700, FR-701, AC-712 | Integration | Canonical manifest hashing, full preflight, atomic replacement, late-failure preservation, and value-free receipt evidence. |
| T-701 | FR-702, FR-703, FR-724 | Unit/contract | Organizer capability schema, consumer-owned provider protocol, no Text-to-SQL import from enrichment, unchanged enrichment tests, runtime identity, secret redaction, typed transport/configuration/rejection outcomes, and current-run receipt binding. |
| T-702 | FR-705–FR-708, FR-717, AC-713 | Contract | Round-trip strict response unions; reject `ok` without snapshot/value-free IR/SQL artifact/result or with `generation_route="none"`; reject `refused` with executable SQL/result; prove free-form intent, embedded literals, canonical question text, resolved values, and bound parameter values do not serialize. |
| T-703 | FR-704, AC-700, AC-715 | Security/unit/API | Filter unauthorized objects before ranking/expansion; freeze canonical snapshot/governed constants; verify canonicalization and hash changes; reject forged scope and caller-supplied `GroundingResponse`; prove existing `/api/grounding` and MCP remain metadata-only and cannot authorize execution. |
| T-704 | FR-704a, FR-716, AC-704, AC-714 | Unit/integration | Prove one default call; typed valid/invalid clarification behavior; opaque local complex acceptance; denied premature/repeated transition; exactly two calls after one accepted transition; denied third call; zero calls on cache hit; no ensemble/semantic repair. |
| T-705 | FR-709, FR-715 | Unit/property | Reject empty, cyclic, duplicate, orphaned, disconnected, ungrounded, unavailable, wrong-arity, duplicate-output, ill-typed, misplaced/nested aggregate/window, incompatible set IR; accept one connected typed DAG; bind value-free assumptions and exact named violation codes. |
| T-706 | FR-710, FR-715 | Unit | Stable warning controls and literal-ref operands; positive transaction volume differs from signed net; mutation, unresolved spans, ungrounded governed literals, or embedded values fail. |
| T-707 | FR-711, FR-712, AC-702, AC-706 | Unit/property | Compile governed metrics deterministically; resolve canonical question spans and authored governed literals locally; reject post-validation IR/context mutation, model formula SQL, embedded/invented values, invalid spans/types, changed roots, nondeterministic alias/parameter order, raw SQL fragments, and unsupported expressions. |
| T-708 | FR-709a, FR-712, AC-700, AC-705 | Security/unit | Resolve aliases/CTEs/set leaves and prove every compiled reference, join, predicate, function, limit, literal placeholder, and parameter maps exactly to IR plus canonical question/snapshot; inject compiler defects and stop before engine. |
| T-709 | FR-713, AC-701 | Security/unit | Propagate classifications; verify literal-ref bounded direct/value-preserving disclosure, categorical collection rejection, aggregate threshold, and complete source lineage. |
| T-710 | FR-714, FR-719 | Integration | Parameter-aware `EXPLAIN`, one synchronized watchdog per phase, no late interrupt, no partial rows, and connection recovery/replacement. |
| T-711 | FR-703b, FR-716, FR-716a, AC-711 | Unit | Exact budget defaults; distinct attempts/domains; cumulative deadline/token/cost limits over both calls and engine work; two transport attempts per semantic call; guarded one-way capacity transition; reject unknown modes and a repeated `default_ir` after transition without consuming a call (tests/test_query_budget_cache.py); exact terminal mappings without provider recall after compiler/engine failures. |
| T-712 | FR-706, FR-717, FR-718 | Contract/integration | Mandatory resolver/guarded-provider/validator/executor; no `ok` without `QueryResult` and concrete generation route; failed artifacts remain non-executable and value-free. |
| T-713 | FR-719–FR-721 | Integration | Row/deadline/resource caps, parameter binding, max_rows+1 fetch, sanitized failure, and no execution-driven model retry. |
| T-714 | FR-722 | Integration | Valid zero-row projection returns `ok`, one execution, original generation route plus cache status, no semantic retry; `COUNT(*) = 0` remains one row. |
| T-715 | AC-703 | Acceptance | GQ-08 uses a valid amount literal ref, data-max relative time, governed positive volume, deterministic planned-route window compilation, and month-over-month growth. |
| T-716 | AC-705 | Acceptance | GQ-05 resolves and compiles the two governed relationship edges exactly. |
| T-717 | FR-707, AC-704 | Contract | Verify missing needs locally; reject false claims; derive policy refusal; validate canonical-span ambiguity relevance and candidate cardinality/membership; map invalid ambiguity to `invalid_clarification_request`; return model ambiguities or local value-free `LiteralClarificationNeed` plus unsupported-complexity refusals before engine contact. |
| T-718 | FR-704b, AC-706, AC-714, AC-715 | Regression/security | Canonicalization-version/question/scope/cache identity, complete mutation matrix, cross-scope isolation, value-free payload integrity, original generation-route preservation, cached planned-route complex-node revalidation, zero calls, deterministic recompilation, and full gate/`EXPLAIN` execution on hit. |
| T-719 | FR-703a, AC-707 | Security/contract | Reject dictionaries, strings, forged envelopes, source/result/resolved values, secrets, raw SQL/exceptions, and valid-looking nonmember subjects with zero additional provider calls; permit only authored governed constants from snapshot metadata. |
| T-720 | FR-703c, AC-707 | Integration | Run full suite without keys and with IPv4/IPv6 disabled; missing cassette cannot call live; Text-to-SQL modules have no enrichment import and `tests/test_enrichment.py` still passes. |
| T-721 | FR-703d, FR-723, AC-710 | Acceptance | Offline default and planned fixtures reach `ok`, record generation route/cache/call counts, retain value-free IR/evidence, and cannot be promoted to live evidence. |
| T-722 | FR-724, AC-708 | Acceptance/manual-live | Validate fresh receipts, immutable retained paths, canonical/literal/type/generation-route/cache evidence, value-free artifacts, ten unique IDs, derived totals, non-vacuity, and atomic validated-only output. |
| T-723 | FR-725, AC-713 | Contract | Spec 009 consumes only safe value-free `OkResponse`; spec 010 rejects non-live or provenance-incomplete artifacts. |
| T-724 | FR-709a–FR-715, AC-709 | Regression/integration | Mutate one structural, typed, literal, or semantic element in each set-operation leaf and prove the entire query fails before `EXPLAIN`; merge compatible lineage only after all leaves pass. |

### Requirement traceability summary

- FR-700–FR-703d → T-700, T-701, T-719, T-720, T-721, T-722
- FR-704–FR-708 → T-702–T-704, T-717–T-719, T-723
- FR-709–FR-715 → T-705–T-710, T-715, T-716, T-724
- FR-716–FR-722 → T-704, T-711–T-714
- FR-723–FR-725 → T-721–T-723
- AC-700–AC-715 → T-700, T-702–T-724 as mapped above


## Implementation clarification — 2026-09-07

- FR-726: OutputExpression(node_id, alias) references an available output of an upstream node, never a physical snapshot column. Each node publishes typed output slots with complete physical lineage. References to an unrelated branch, unavailable alias, or shadowed duplicate fail before compilation. Window nodes preserve inputs and add outputs. A complete monthly-growth graph must aggregate before LAG and compute delta/ratio using output references; zero denominators produce NULL.
- FR-727: All query boundary/evidence models are recursively immutable, extra-forbid models. Canonical hashing sorts unordered sets by canonical serialized content. Supporting warning, assumption, lineage, attempt, receipt and grounding-need schemas are versioned in the implementation contract module; no previous unpublished revision is a dependency.
- FR-728: Hosted input is trusted metadata plus a canonical question accepted by an ingress policy. Email/phone/account identifiers and quoted free-text literals require local clarification before provider contact under the default conservative ingress policy. Numeric analytic amounts (limits/time windows) remain permitted. Deployments must not claim arbitrary PII detection; additional ingress authorization is an upstream responsibility. No database sampling is used to classify prompt text.
- FR-729: Atomic materialization accepts a verified manifest for real-data evidence; synthetic/schema-only construction explicitly has no authoritative receipt. No CLI silently replaces an existing populated database without the caller choosing the materialization command.

Acceptance additions: AC-716 validates aggregate -> window -> project output references, including wrong-branch/unknown alias failures; AC-717 verifies deep immutability and cross-process canonical hashes; AC-718 blocks ingress identifiers before provider calls; AC-719 proves failed real-data verification never overwrites a baseline artifact. Tests cover these in contract/compiler, prompting, materialization and baseline suites.

Migration: QueryPlan/model-written SQL is retired from executable paths. Legacy checker unit coverage may remain as characterization only; production callers use the strict query contracts. The user clarified on 2026-09-07 that Stage 2 means Text-to-SQL only: spec 008 and the Option B plan. Specs 009/010 remain downstream work; only their typed handoff contracts are in scope. Authentication and policy administration remain upstream; scope enforcement is mandatory locally.
