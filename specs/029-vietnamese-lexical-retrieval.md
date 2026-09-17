# 029 — Vietnamese-aware lexical retrieval for the customer workspace

## Problem

Live verification of spec 028 (running all 30 customer preset questions against a real dev
server) found that roughly half of them — including several with no data or grounding gap at
all — resolve to `status: "clarification"` with `evidence_ids: []` (zero retrieved objects).
Root-caused with a controlled comparison against the same server:

```
"Tôi có bao nhiêu tài khoản đang hoạt động?"   -> evidence_ids: [], clarification
"How many active accounts do I have?"          -> evidence_ids: [table.accounts, rule.active-customer, ...], answered
```

The dev environment has no `CEREBRO_EMBEDDING_MODEL` configured, so `SemanticRetriever` runs in
`lexical_graph` mode only (`src/cerebro/retrieval.py`). Its tokenizer,
`TOKEN = re.compile(r"[a-z0-9]+")` (`retrieval.py:44`), matches ASCII letters/digits only.
Vietnamese text shreds into garbage fragments:

```python
>>> re.findall(r"[a-z0-9]+", "tài khoản đang hoạt động".lower())
['t', 'i', 'kho', 'n', 'ang', 'ho', 't', 'ng']
```

Since every customer preset question is Vietnamese (spec 018/024) and every knowledge-bundle
object's `name`/`description`/`aliases` text is English, lexical matching has near-zero token
overlap for Vietnamese questions even when the relevant object exists and is correctly scoped —
this is independent of, and larger in impact than, the specific unanswerable-question fixes in
spec 028.

Two separate defects compound this, and fixing only one is insufficient:

1. The tokenizer itself cannot produce meaningful tokens from Vietnamese text (confirmed above).
2. Even with a correct tokenizer, the bundle objects have no Vietnamese vocabulary at all for
   lexical matching to find — `aliases: list[str]` (`src/cerebro/models.py:668`,
   `SemanticObject`) exists precisely for this purpose (already consumed by
   `SemanticRetriever._tokenized_names`, `retrieval.py:68-70`) but is empty on every object in
   `knowledge/bank-workshop`.

## Goal

A Vietnamese customer preset question that names a customer-scoped concept in ordinary Vietnamese
banking vocabulary (account, transaction, card, loan, balance, active/inactive, late payment,
fraud, etc.) retrieves the relevant object(s) via lexical matching, in an environment with no
embedding model configured — matching, as closely as lexical matching allows, what the same
question already achieves when phrased in English.

## Non-Goals

- Configuring an embedding model / hybrid retrieval — a deployment/cost decision, not a code or
  content change; out of scope here.
- Translating the internal English `metric_intent`/`rule_intent` keyword-gate word lists in
  `SemanticRetriever._progressive_grounding_ids` (`retrieval.py:385`, `:384`) to Vietnamese. This
  spec relies on the `direct` (lexical/vector evidence) candidate path, which does not depend on
  those English gate keywords; the gates only add or narrow candidate kinds, they do not block a
  strong direct lexical match from being selected (confirmed by the `rule_has_direct_match`
  override at `retrieval.py:424-429` and by this spec's own live-verified test cases). Extending
  the gates themselves is flagged as possible future work if a specific question shape still
  needs it.
- Vietnamese aliases for objects outside the customer-scope allowlist (branches, employees,
  population-level risk metrics, etc.) — not asked about by any customer preset question.
- Any change to `SQLGuardrail`, the row-level filter, or `SemanticRetriever`'s ranking/scoring
  logic beyond the tokenizer regex itself.

## Functional Requirements

- FR-1: `src/cerebro/retrieval.py`'s `TOKEN` regex MUST be changed from `r"[a-z0-9]+"` to a
  Unicode-letter-and-digit-aware equivalent (`r"[^\W_]+"`, using Python's default Unicode-aware
  `re` semantics for `str` patterns) so that Vietnamese diacritic letters tokenize as intact
  words. For existing ASCII/ english text, the new pattern MUST produce identical tokenization to
  the old one (both match runs of letters/digits, excluding `_`; the only behavioral difference
  is scripts beyond ASCII now tokenizing correctly instead of being shredded).
- FR-2: every knowledge-bundle object in `CUSTOMER_SCOPE_OBJECT_IDS` whose `profile_kind` is
  `entity`, `physical_table`, `dimension`, `metric`, or `business_rule` (33 objects: 7 entities, 7
  tables, 8 dimensions, 5 metrics, 6 business rules, per `src/cerebro/customer_scope.py`) MUST
  gain a top-level `aliases:` frontmatter list containing natural Vietnamese terms/phrases a
  customer would plausibly use to refer to that concept (e.g. `entity.account` gains `tài khoản`;
  `rule.active-customer` gains terms like `đang hoạt động`, `còn hoạt động`). Domains, policies,
  and relationships are not required to gain aliases (not directly named in preset question
  text).
- FR-3: adding aliases MUST NOT change any object's `id`, `type`, or existing `cerebro:` contract
  fields — purely additive frontmatter, so `cerebro validate` continues to pass with the same
  document count as after spec 028 (74).

## Acceptance Criteria

- AC-1: `re.findall(new_pattern, "tài khoản đang hoạt động")` (or the equivalent
  `SemanticRetriever`-internal `_tokens` call) returns intact Vietnamese words
  (`['tài', 'khoản', 'đang', 'hoạt', 'động']`), not single-letter fragments.
- AC-2: `cerebro validate` passes with `document_count: 74` after the alias additions (no new
  objects, only new frontmatter fields on existing ones).
- AC-3: a live run of `"Tôi có bao nhiêu tài khoản đang hoạt động?"` against a running dev server
  returns non-empty `evidence_ids` including `table.accounts` and/or `entity.account`, and
  `status: "answered"` (previously: `evidence_ids: []`, `status: "clarification"`).
- AC-4: a live re-run of all 30 customer preset questions shows a meaningfully higher pass rate
  than spec 028's pre-fix baseline (documented in the completion report with before/after
  `evidence_ids`/`status` for every question that changed outcome) — this spec does not require
  literally 100% (some questions may still hit unrelated, pre-existing model non-determinism per
  spec 018's documented precedent), but the empty-`evidence_ids` failure mode specifically MUST
  be eliminated for questions naming an in-scope concept.
- AC-5: `pytest` (full suite, excluding the pre-existing `test_report.py` collection error) shows
  no new failures beyond the 4 already-documented pre-existing ones
  (`test_load_duckdb.py` x3, `test_text2sql_preflight.py` x1). In particular, no existing
  English-language retrieval/grounding test regresses due to the tokenizer change.
- AC-6: `cd apps/web && npm run test && npm run build` continue to pass (frontend is untouched by
  this spec, but is the standing regression gate).

## Test Design

- Extend `tests/test_retrieval.py` (or add it if it doesn't exist as a dedicated file — check
  first) with a unit test asserting the new `TOKEN` pattern tokenizes a Vietnamese phrase into
  intact words, and a before/after-style comment noting the old pattern's failure mode for
  documentation.
- Add a grounding-retrieval test in `tests/test_customer_scope.py` (alongside the spec-028 tests
  already there) asserting a Vietnamese customer question (e.g.
  `"Tôi có bao nhiêu tài khoản đang hoạt động?"`) retrieves `table.accounts` and/or
  `entity.account` via `SemanticRetriever.grounding(..., allowed_object_ids=CUSTOMER_SCOPE_OBJECT_IDS)`
  with no embedder configured (lexical-only), mirroring the live-verified failure/fix.
- AC-4's full 30-question live re-run is manual (same one-off approach as spec 028's AC-5), not
  new checked-in tooling.
