from __future__ import annotations

import json

import pytest
import text2sql_factories as factories

from cerebro.models import CachedGeneration, relational_ir_sha256
from cerebro.provenance import canonical_question_sha256, canonicalize_question
from cerebro.selfcheck import validate_ir
from cerebro.text2sql_cache import (
    CacheIntegrityError,
    CacheKey,
    Text2SQLCache,
    cache_payload_sha256,
)

QUESTION = "Show accounts in London"

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


@pytest.fixture(scope="module")
def snapshot():
    return factories.valid_snapshot()


@pytest.fixture()
def canonical_question() -> str:
    return canonicalize_question(QUESTION)


def _key(snapshot, canonical_question, **overrides) -> CacheKey:
    base = {
        "authorization_scope_hash": snapshot.authorization_scope_hash,
        "snapshot_hash": snapshot.snapshot_hash,
        "policy_version": snapshot.policy_version,
        "canonicalization_version": "008.question.v1",
        "canonical_question_hash": canonical_question_sha256(canonical_question),
        "literal_registry_version": "008.literal-span.v1",
        "dialect": "duckdb",
        "generation_route": "default_ir",
        "provider": "organizer",
        "model": "organizer-model",
        "model_revision": "2026-08-27",
        "schema_mechanism": "json_schema",
        "prompt_version": "008.prompt.v1",
        "ir_contract_version": "008.ir.v1",
        "router_version": "008.router.v1",
        "compiler_version": "008.compiler.v1",
        "type_registry_version": "008.types.v1",
        "checker_version": "008.checker.v1",
    }
    base.update(overrides)
    return CacheKey(**base)


def _value(snapshot, route="default_ir", ir=None) -> CachedGeneration:
    ir = ir if ir is not None else factories.minimal_ir(snapshot)
    plan_hash = None
    if route == "planned_ir":
        from cerebro.models import complex_plan_sha256

        plan_hash = complex_plan_sha256(factories.complex_window_plan(snapshot))
    payload = {
        "ir": ir,
        "generation_route": route,
        "accepted_complex_plan_hash": plan_hash,
    }
    return CachedGeneration(**payload, payload_sha256=cache_payload_sha256(**payload))


# --- key identity ----------------------------------------------------------


def _mutate_key(key: CacheKey, field: str) -> CacheKey:
    current = getattr(key, field)
    if field == "generation_route":
        return key.model_copy(update={field: "planned_ir"})
    if field == "dialect":
        # The contract admits one dialect, so drift shows up as a different key
        # fingerprint rather than a different validated value.
        return key.model_copy(update={"canonical_question_hash": "b" * 64})
    if isinstance(current, str) and len(current) == 64:
        # Use a digest the fixture never uses, so no mutation is a no-op.
        return key.model_copy(update={field: "9" * 64})
    return key.model_copy(update={field: f"{current}-changed"})


@pytest.mark.parametrize("field", CACHE_KEY_MUTATIONS)
def test_every_security_relevant_change_is_a_cache_miss(
    snapshot, canonical_question, field
):
    cache = Text2SQLCache()
    original = _key(snapshot, canonical_question)
    cache.put(original, _value(snapshot))
    assert cache.get(original) is not None
    assert cache.get(_mutate_key(original, field)) is None


def test_question_canonicalization_preserves_case_and_literals():
    assert canonicalize_question("  Revenue\u00a0 for  Q1  ") == "Revenue for Q1"
    assert canonicalize_question("Q1") != canonicalize_question("q1")
    assert canonical_question_sha256("Q1") != canonical_question_sha256("q1")


def test_key_fingerprint_uses_full_digests(snapshot, canonical_question):
    key = _key(snapshot, canonical_question)
    assert len(key.fingerprint()) == 64
    assert key.fingerprint() == _key(snapshot, canonical_question).fingerprint()


def test_cache_key_rejects_cache_as_a_generation_route(snapshot, canonical_question):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        _key(snapshot, canonical_question, generation_route="cache")


# --- cross-scope isolation --------------------------------------------------


def test_payload_copied_to_another_scope_or_question_misses(
    snapshot, canonical_question
):
    cache = Text2SQLCache()
    original = _key(snapshot, canonical_question)
    cache.put(original, _value(snapshot))
    other_scope = original.model_copy(update={"authorization_scope_hash": "f" * 64})
    other_question = original.model_copy(
        update={
            "canonical_question_hash": canonical_question_sha256(
                canonicalize_question("Show branches in Delhi")
            )
        }
    )
    assert cache.get(other_scope) is None
    assert cache.get(other_question) is None


# --- integrity --------------------------------------------------------------


def test_mutated_cached_ir_is_an_integrity_error(snapshot, canonical_question):
    cache = Text2SQLCache()
    key = _key(snapshot, canonical_question)
    value = _value(snapshot)
    cache.put(key, value)
    tampered = value.model_copy(update={"ir": factories.branch_volume_ir(snapshot)})
    cache.overwrite_for_test(key, tampered)
    with pytest.raises(CacheIntegrityError):
        cache.get(key)


def test_mutated_route_or_plan_hash_is_an_integrity_error(snapshot, canonical_question):
    cache = Text2SQLCache()
    key = _key(snapshot, canonical_question)
    value = _value(snapshot)
    cache.put(key, value)
    cache.overwrite_for_test(key, value.model_copy(update={"payload_sha256": "0" * 64}))
    with pytest.raises(CacheIntegrityError):
        cache.get(key)


def test_stored_route_must_equal_the_key_route(snapshot, canonical_question):
    cache = Text2SQLCache()
    key = _key(snapshot, canonical_question, generation_route="planned_ir")
    with pytest.raises(CacheIntegrityError):
        cache.put(key, _value(snapshot, route="default_ir"))


def test_planned_route_requires_a_plan_hash_and_default_rejects_one(
    snapshot, canonical_question
):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        CachedGeneration(
            ir=factories.complex_node_ir(snapshot, "window"),
            generation_route="planned_ir",
            accepted_complex_plan_hash=None,
            payload_sha256="a" * 64,
        )
    with pytest.raises(ValidationError):
        CachedGeneration(
            ir=factories.minimal_ir(snapshot),
            generation_route="default_ir",
            accepted_complex_plan_hash="a" * 64,
            payload_sha256="a" * 64,
        )


def test_cached_value_is_value_free(snapshot, canonical_question):
    cache = Text2SQLCache()
    key = _key(snapshot, canonical_question)
    ir = factories.sensitive_ir(snapshot)
    cache.put(key, _value(snapshot, ir=ir))
    hit = cache.get(key)
    serialized = hit.model_dump_json()
    for forbidden in (
        "London",
        "customer name",
        "SELECT",
        '"value"',
        '"intent"',
        '"sql"',
    ):
        assert forbidden not in serialized
    payload = json.loads(serialized)

    def walk(node):
        if isinstance(node, dict):
            for name, child in node.items():
                assert name not in {"value", "intent", "sql", "raw_value"}
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(payload)


# --- route preservation on a hit -------------------------------------------


def test_cached_planned_ir_preserves_route_and_revalidates_complex_nodes(snapshot):
    question = canonicalize_question("accounts")
    cache = Text2SQLCache()
    key = _key(snapshot, question, generation_route="planned_ir")
    ir = factories.complex_node_ir(snapshot, "window")
    cache.put(key, _value(snapshot, route="planned_ir", ir=ir))

    hit = cache.get(key)
    assert hit.generation_route == "planned_ir"
    assert hit.accepted_complex_plan_hash is not None

    result = validate_ir(
        hit.ir, snapshot, question, generation_route=hit.generation_route
    )
    assert result.violations == ()
    # The same cached IR on the default route must not be revalidated as simple.
    downgraded = validate_ir(hit.ir, snapshot, question, generation_route="default_ir")
    assert "complex_node_requires_planned_route" in {
        violation.code for violation in downgraded.violations
    }


def test_probing_finds_the_original_route_without_a_route_hint(
    snapshot, canonical_question
):
    cache = Text2SQLCache()
    planned_key = _key(snapshot, canonical_question, generation_route="planned_ir")
    cache.put(
        planned_key,
        _value(snapshot, route="planned_ir", ir=factories.complex_node_ir(snapshot)),
    )
    found = cache.probe(planned_key.without_route())
    assert found is not None
    route, value = found
    assert route == "planned_ir"
    assert value.generation_route == "planned_ir"


def test_two_routes_stored_for_one_question_is_an_integrity_error(
    snapshot, canonical_question
):
    cache = Text2SQLCache()
    default_key = _key(snapshot, canonical_question)
    planned_key = _key(snapshot, canonical_question, generation_route="planned_ir")
    cache.put(default_key, _value(snapshot))
    cache.put(
        planned_key,
        _value(snapshot, route="planned_ir", ir=factories.complex_node_ir(snapshot)),
    )
    with pytest.raises(CacheIntegrityError):
        cache.probe(default_key.without_route())


def test_hit_reconstructs_validated_ir_with_matching_hashes(
    snapshot, canonical_question
):
    cache = Text2SQLCache()
    key = _key(snapshot, canonical_question)
    cache.put(key, _value(snapshot))
    hit = cache.get(key)
    validated = cache.reconstruct_validated_ir(hit, key)
    assert validated.generation_route == "default_ir"
    assert validated.ir_hash == relational_ir_sha256(hit.ir)
    assert validated.snapshot_hash == key.snapshot_hash
    assert validated.canonical_question_hash == key.canonical_question_hash
