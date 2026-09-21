# 033 — Domain Generation and Entity Linking

## Problem

Spec 026 built the entire `domain` profile-kind: `DomainCandidate`, the validator's domain
contract and entity→domain reference checks, the linker's `domain_membership` edge synthesis,
and the `{domain, entity}` graph overview tier — but it deliberately scoped that work to the
*consumption* side, backfilling the golden bundle (`knowledge/bank-workshop`) with four
hand-authored domains rather than generating them. Spec 026 explicitly ruled out algorithmic
clustering as a Non-Goal, but never addressed LLM-proposed domain generation at all.

As a result, `cerebro generate` and the Semantic Constellation workspace's "Run full pipeline"
never produce a `domains/` folder and never set `cerebro.domain` on a generated entity — grepping
`src/cerebro/generation.py` for `"domain"` returns zero hits, and neither `BusinessSemantics` nor
`EntityCandidate` (`src/cerebro/models.py`) has a domain-shaped field. Every AI-generated
candidate bundle is therefore permanently stuck at a flat, domain-less entity layer: once
activated, its default graph overview (`GRAPH_OVERVIEW_TIER_KINDS = {"domain", "entity"}`,
`src/cerebro/retrieval.py:42`) renders bare, disconnected entity nodes with no domain grouping —
structurally unlike the golden bundle's connected default view.

## Goal

`cerebro generate` (database-only or configured source mode) and the GenerationPanel's full
pipeline run produce a `domains/` folder of freely LLM-proposed `Domain` objects, and every
generated entity that fits one is linked into it via `cerebro.domain` plus the existing
`domain_membership` edge — so an activated generated candidate's default graph overview shows a
connected domain+entity layer, matching the golden bundle's shape.

## Non-Goals

- Deterministic/schema-derived domain clustering (e.g. one domain per configured dataset or
  physical schema). Domains remain LLM-proposed from catalog-only metadata, the same trust model
  already applied to entities/dimensions/policies — this reaffirms spec 026's clustering-algorithm
  Non-Goal was about *automated clustering*, not about *ever* generating domains.
- Metrics and business rules. `MetricRuleAgent`, `QuerySemantics`, `StructuredMetricCandidate`,
  and `BusinessRuleCandidate` are untouched; the existing GenerationPanel copy ("Metrics and
  business rules are intentionally deferred until the graph is activated") already covers this
  and needs no wording change.
- Making `domain` a required field on generated entities. It stays optional, reaffirming spec
  026's Constraint that domain is opt-in per entity.
- A new pipeline stage or `GenerationStage` literal value. Domain proposal is folded into the
  existing `business_semantics` stage owned by `SemanticInventoryAgent`.
- Changing `CUSTOMER_SCOPE_DOMAIN_IDS` (`src/cerebro/customer_scope.py`). That allowlist stays
  hardcoded to the golden bundle's three specific domain ids; customer scoping is not exercised
  against generated/candidate bundles today.
- Domain-aware relationships or metrics. `RelationshipAgent`'s and `MetricRuleAgent`'s payloads
  and schemas are unchanged — neither relationship nor metric/rule candidates reference a domain.
- A synthetic "Unassigned" domain bucket for domain-less entities — same open question spec 026
  already deferred; not resolved here.

## Functional Requirements

- FR-1: `EntityCandidate` (`src/cerebro/models.py`) gains an optional `domain: str | None = None`
  field. `BusinessSemantics` (`src/cerebro/models.py`) gains
  `domains: list[DomainCandidate] = Field(default_factory=list)`. `DomainCandidate` itself is
  unchanged (`id`, `name`, `description`, `classification`, optional `owner`, `warnings`).
- FR-2: `SemanticInventoryAgent.run()` (`src/cerebro/semantic/agents.py`) — the single typed call
  that already proposes entities/dimensions/policies — has its prompt extended to also propose a
  small set of business domains (typically 2-6) and assign each entity's `domain` to the id of
  the one proposed domain it most clearly fits, leaving an entity domain-less rather than forcing
  a fit. No new agent class or pipeline stage is introduced; `RelationshipAgent` and
  `MetricRuleAgent` payloads are unchanged (FR non-goal above).
- FR-3: `compile_candidate_bundle` (`src/cerebro/generation.py`) registers domain ids through the
  existing `register_id`/`duplicate_semantic_id` mechanism (same as entities/dimensions/metrics/
  rules), resolves each entity's `domain` reference against the proposed domain set using the
  existing `reference_index` alias-tolerant lookup helper, and writes `domains/<slug>.md` docs
  (`type: Domain`, `cerebro.kind: domain`, `provenance.origin: ai_proposed`) plus
  `domains/index.md`. An entity `domain` reference that does not resolve to a proposed domain
  raises a `ValidationIssue` with code `invalid_entity_domain_reference`, distinct from the
  validator's existing bundle-on-disk `invalid_entity_domain` check (`src/cerebro/semantic/
  validator.py`), since the two checks run at different pipeline stages over different data (an
  in-memory proposal vs. a loaded bundle).
- FR-4: Generated entity docs (`compile_candidate_bundle`'s entity doc-writing loop) append the
  resolved domain id to `links` and set `cerebro.domain` when present — reproducing the golden
  bundle's exact shape (`links: [table.<x>, domain.<y>]`, `cerebro.domain: domain.<y>`).
- FR-5: No changes to `src/cerebro/semantic/validator.py`, `linker.py`, `profile.py`,
  `retrieval.py`, `bundle.py`, or `customer_scope.py` — all of the domain-contract validation,
  `domain_membership` edge synthesis, and graph-overview-tier machinery from spec 026 already
  supports this without modification.

## Acceptance Criteria

- AC-1: `compile_candidate_bundle` on a proposal containing `business.domains` writes a
  `domains/` folder whose objects validate, and `compiled_counts["domain"]` reflects the count.
- AC-2: An entity proposal with a resolvable `domain` compiles to an entity doc whose `links`
  contains both its table and domain id and whose `cerebro.domain` is set; loading the compiled
  bundle and reading `by_id()["entity.<id>"].cerebro["domain"]` confirms the value round-trips.
- AC-3: An entity proposal with an unresolvable `domain` raises `CandidateValidationError` citing
  `invalid_entity_domain_reference`.
- AC-4: Two proposed domains whose canonicalized ids collide raise `duplicate_semantic_id`.
- AC-5: `src/cerebro/semantic/linker.py::profile_edges` emits a `domain_membership` edge for
  every domain-linked generated entity — exercised via `BundleValidator`/`load_validated_bundle`
  producing a graph where domain nodes have at least one incident edge.
- AC-6 (manual, end-to-end): running `cerebro generate --source-mode database-only ...` and
  activating the resulting candidate makes `GET /api/graph?tier=overview` return connected
  domain+entity nodes, not floating/disconnected ones.

## Edge Cases

- EC-1: The LLM proposes a domain with zero entities assigned to it. Not rejected structurally
  (a domain has no required inbound references); discouraged only via the prompt's explicit "do
  not propose a domain with no entities assigned to it" instruction. Left as a soft guarantee for
  this iteration — the graph overview tier tolerates an isolated domain node without breaking.
- EC-2: The LLM proposes an entity's domain reference using a bare/underscore/prefixed variant
  (e.g. `retail_banking`, `domain.retail-banking`) rather than the domain's exact canonical id.
  Resolved via the existing `reference_index`/`normalize_reference` alias machinery, identical to
  how entity/dimension/metric/rule cross-references already tolerate this.
- EC-3: No provider configured (`SemanticEnricher.fallback()`). `domains` defaults to `[]` via
  `Field(default_factory=list)`; no `domains/` folder is written, mirroring today's empty-entity
  fallback behavior.

## Interfaces / Contracts

- `EntityCandidate.domain: str | None` (new, optional).
- `BusinessSemantics.domains: list[DomainCandidate]` (new field, default empty).
- New `ValidationIssue` code: `invalid_entity_domain_reference` (compiler-time, in
  `compile_candidate_bundle`), distinct from the pre-existing validator-time `invalid_entity_domain`.
- No HTTP/API contract changes — `/api/graph`, `/api/generation/*` are unaffected;
  `compiled_counts` gains a `"domain"` key organically once domain objects exist.

## Constraints

- `domain` remains optional on generated entities (reaffirms spec 026's Constraint).
- No new `GenerationStage` value; domain proposal happens inside the existing `business_semantics`
  stage, so `SemanticEnricher.enrich`'s hardcoded 3-stage loop and its stage-order test coverage
  are unaffected.
- No frontend changes required — `apps/web/src/App.tsx`'s default `{domain, entity}` type filter,
  `profilePresentation.ts`'s `domain` entry, and `GraphView.tsx`'s `domain_membership` edge style
  (all from spec 026) are already generic/kind-agnostic.

## Assumptions

- The LLM can propose a coherent small domain set from catalog-only metadata (table/column names,
  no source rows) with the same reliability already assumed for entities/dimensions/policies.
- `CUSTOMER_SCOPE_DOMAIN_IDS` does not need to account for generated-bundle domain ids, since
  customer scoping is not exercised against generated/candidate bundles today.

## Open Questions

- Should the compiler actively drop or warn on a proposed domain with zero linked entities,
  rather than relying solely on the prompt's soft instruction? Deferred to a follow-up if EC-1
  proves common in practice.
- Should the `business_semantics` stage's completed-event summary mention the domain count?
  Cosmetic; left to implementation discretion.

## Test Design

| Test | Requirement | Verification |
|---|---|---|
| T-3301 | FR-1 | Model-level: `EntityCandidate(domain=...)` and `BusinessSemantics(domains=[...])` accept/round-trip; `extra="forbid"` still rejects unknown fields. |
| T-3302 | FR-2 | `tests/test_enrichment.py`: `MockProvider`'s `business_semantics` branch returns domains; a `DomainCandidate` case added to the unknown-field-rejection parametrization; existing 3-stage call-order assertions still pass unchanged. |
| T-3303 | FR-3, FR-4, AC-1, AC-2 | `tests/test_generation.py`: extended `_connected_proposal()` fixture with `domains=[...]` and an entity `domain=...`; new assertions that `domains/` + `domains/index.md` are written, `compiled_counts["domain"]` is correct, and the linked entity's `links`/`cerebro.domain` match the golden-bundle shape. |
| T-3304 | FR-3, AC-3 | `tests/test_generation.py`: entity referencing an unknown domain raises `CandidateValidationError` with `invalid_entity_domain_reference` (mirrors the existing `invalid_entity_table` case). |
| T-3305 | FR-3, AC-4 | `tests/test_generation.py`: duplicate canonicalized domain ids raise `duplicate_semantic_id` (mirrors `test_compiler_rejects_duplicate_semantic_ids`). |
| T-3306 | EC-2 | `tests/test_generation.py`: domain id canonicalization for underscore/prefixed variants (mirrors `test_compiler_canonicalizes_underscore_and_prefixed_semantic_ids`). |
| T-3307 | FR-5, AC-5 | `tests/test_bundle_validation.py`: rerun unchanged — `test_entity_without_a_declared_domain_still_validates` and the existing `invalid_entity_domain` mutation case still pass, confirming no validator regression. |
| T-3308 | AC-6 | Manual: `cerebro generate --source-mode database-only ...` → `cerebro review` → `cerebro activate` → `GET /api/graph?tier=overview` shows connected domain+entity nodes. |
