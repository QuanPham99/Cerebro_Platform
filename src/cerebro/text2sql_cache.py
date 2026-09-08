"""Scope-safe cache identity for value-free accepted IR.

A cache entry is keyed by every input that could change what the query is
allowed to mean. Nothing about the question text, the resolved literals, the
compiled SQL, or the result ever enters a cached value, so a hit still has to
recompile and re-authorize from scratch.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import Field

from .models import (
    CachedGeneration,
    RelationalQueryIR,
    Sha256,
    StrictFrozenModel,
    ValidatedIR,
    relational_ir_sha256,
)

CACHE_IDENTITY_VERSION = "008.cache.v1"


class CacheIntegrityError(Exception):
    """Raised when a stored entry disagrees with its key or its own digest."""


class CacheKey(StrictFrozenModel):
    """Every field here is part of what the cached IR is allowed to mean."""

    authorization_scope_hash: Sha256
    snapshot_hash: Sha256
    policy_version: str = Field(min_length=1)
    canonicalization_version: Literal["008.question.v1"]
    canonical_question_hash: Sha256
    literal_registry_version: Literal["008.literal-span.v1"]
    dialect: Literal["duckdb"]
    generation_route: Literal["default_ir", "planned_ir"]
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    model_revision: str = Field(min_length=1)
    schema_mechanism: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    ir_contract_version: str = Field(min_length=1)
    router_version: str = Field(min_length=1)
    compiler_version: str = Field(min_length=1)
    type_registry_version: str = Field(min_length=1)
    checker_version: str = Field(min_length=1)

    @classmethod
    def from_request(cls, **fields: Any) -> CacheKey:
        return cls(**fields)

    def fingerprint(self) -> str:
        """Return the full SHA-256 identity of this key. Never truncated."""
        payload = json.dumps(
            {"version": CACHE_IDENTITY_VERSION, **self.model_dump(mode="json")},
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def without_route(self) -> dict[str, Any]:
        """Return the route-free base of this key for original-route probing."""
        fields = self.model_dump(mode="json")
        fields.pop("generation_route")
        return fields


def cache_payload_sha256(
    *,
    ir: RelationalQueryIR,
    generation_route: str,
    accepted_complex_plan_hash: str | None,
) -> str:
    """Digest exactly the three fields a cached payload is allowed to carry."""
    payload = json.dumps(
        {
            "ir_hash": relational_ir_sha256(ir),
            "generation_route": generation_route,
            "accepted_complex_plan_hash": accepted_complex_plan_hash,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class Text2SQLCache:
    """An in-memory, integrity-checked store of value-free accepted IR."""

    def __init__(self) -> None:
        self._entries: dict[str, tuple[CacheKey, CachedGeneration]] = {}

    def put(self, key: CacheKey, value: CachedGeneration) -> None:
        if not isinstance(value, CachedGeneration):
            raise CacheIntegrityError("a cache value must be a CachedGeneration")
        if value.generation_route != key.generation_route:
            raise CacheIntegrityError("stored route must equal the key route")
        expected = cache_payload_sha256(
            ir=value.ir,
            generation_route=value.generation_route,
            accepted_complex_plan_hash=value.accepted_complex_plan_hash,
        )
        if value.payload_sha256 != expected:
            raise CacheIntegrityError("payload digest does not match its content")
        self._entries[key.fingerprint()] = (key, value)

    def get(self, key: CacheKey) -> CachedGeneration | None:
        entry = self._entries.get(key.fingerprint())
        if entry is None:
            return None
        stored_key, value = entry
        if stored_key.generation_route != value.generation_route:
            raise CacheIntegrityError("stored route drifted from the key route")
        expected = cache_payload_sha256(
            ir=value.ir,
            generation_route=value.generation_route,
            accepted_complex_plan_hash=value.accepted_complex_plan_hash,
        )
        if value.payload_sha256 != expected:
            # A mutated payload is never served as a hit.
            raise CacheIntegrityError("cached payload failed its integrity check")
        return value

    def probe(
        self, route_free_key: dict[str, Any]
    ) -> tuple[str, CachedGeneration] | None:
        """Find the original route for a question, in a fixed probe order."""
        found: list[tuple[str, CachedGeneration]] = []
        for route in ("default_ir", "planned_ir"):
            candidate = CacheKey(**route_free_key, generation_route=route)
            value = self.get(candidate)
            if value is not None:
                found.append((route, value))
        if not found:
            return None
        if len(found) > 1:
            # One question cannot legitimately hold two accepted routes.
            raise CacheIntegrityError("multiple generation routes stored for one key")
        return found[0]

    def reconstruct_validated_ir(
        self, value: CachedGeneration, key: CacheKey
    ) -> ValidatedIR:
        """Rebuild `ValidatedIR` with the original route and current bindings."""
        return ValidatedIR(
            ir=value.ir,
            ir_hash=relational_ir_sha256(value.ir),
            snapshot_hash=key.snapshot_hash,
            canonical_question_hash=key.canonical_question_hash,
            generation_route=value.generation_route,
            accepted_complex_plan_hash=value.accepted_complex_plan_hash,
        )

    def overwrite_for_test(self, key: CacheKey, value: CachedGeneration) -> None:
        """Insert without integrity checks so a test can prove `get` catches it."""
        self._entries[key.fingerprint()] = (key, value)
