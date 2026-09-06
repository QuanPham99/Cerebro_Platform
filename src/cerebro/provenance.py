from __future__ import annotations

import json
import re
import unicodedata
from hashlib import sha256
from pathlib import Path
from typing import cast

from pydantic import BaseModel

from .models import (
    AuthorizationScope,
    GroundingSnapshot,
    SemanticBundle,
    SourceManifest,
    TableId,
)

_RAW_TABLE_NAME = re.compile(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*")
_UNICODE_WHITESPACE_RUN = re.compile(r"\s+", re.UNICODE)


def canonical_json_bytes(model: BaseModel, *, exclude: set[str] = frozenset()) -> bytes:
    """Serialize validated evidence as deterministic UTF-8 JSON bytes."""
    payload = model.model_dump(mode="json", exclude=exclude)
    return json.dumps(
        payload,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def semantic_bundle_sha256(bundle: SemanticBundle) -> str:
    """Hash complete semantic evidence without machine-specific root authority."""
    if not isinstance(bundle, SemanticBundle):
        raise TypeError("bundle must be a SemanticBundle")
    normalized = SemanticBundle.model_validate(bundle.model_dump(mode="python"))
    normalized.objects = sorted(
        normalized.objects,
        key=canonical_json_bytes,
    )
    return sha256(canonical_json_bytes(normalized, exclude={"root"})).hexdigest()


def sha256_file(path: Path) -> str:
    """Hash a file without retaining or returning its contents."""
    digest = sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_manifest_sha256(manifest: SourceManifest) -> str:
    return sha256(canonical_json_bytes(manifest)).hexdigest()


def manifest_table_id(raw_name: str) -> TableId:
    """Map one exact raw manifest table name into the authority namespace."""
    if not isinstance(raw_name, str) or _RAW_TABLE_NAME.fullmatch(raw_name) is None:
        raise ValueError("table name must be raw lowercase snake-case")
    return cast(TableId, f"table.{raw_name}")


def canonicalize_question(question: str) -> str:
    """Canonicalize a question under version `008.question.v1`.

    Unicode NFC, trimmed of Unicode whitespace, with every internal
    whitespace run collapsed to one ASCII space. It never case-folds and
    never rewrites a literal, so question spans stay meaningful.
    """
    if not isinstance(question, str):
        raise TypeError("a question must be text")
    normalized = unicodedata.normalize("NFC", question)
    # NBSP and friends are Unicode whitespace but not matched by `\s` in every
    # engine, so normalize them explicitly before collapsing runs.
    normalized = "".join(
        " " if character.isspace() or character == "\u00a0" else character
        for character in normalized
    )
    return _UNICODE_WHITESPACE_RUN.sub(" ", normalized).strip()


def canonical_question_sha256(canonical_question: str) -> str:
    """Hash the exact canonical question bytes, refusing noncanonical input."""
    if canonicalize_question(canonical_question) != canonical_question:
        raise ValueError("question must already be canonical")
    return sha256(canonical_question.encode("utf-8")).hexdigest()


def authorization_scope_sha256(scope: AuthorizationScope) -> str:
    """Hash a scope over its canonical payload, excluding its own hash field."""
    if not isinstance(scope, AuthorizationScope):
        raise TypeError("scope must be an AuthorizationScope")
    return sha256(
        canonical_json_bytes(scope, exclude={"authorization_scope_hash"})
    ).hexdigest()


def grounding_snapshot_sha256(snapshot: GroundingSnapshot) -> str:
    """Hash a snapshot over its canonical payload, excluding its own hash field."""
    if not isinstance(snapshot, GroundingSnapshot):
        raise TypeError("snapshot must be a GroundingSnapshot")
    return sha256(canonical_json_bytes(snapshot, exclude={"snapshot_hash"})).hexdigest()
