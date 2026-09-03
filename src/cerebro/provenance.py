from __future__ import annotations

import json
import re
from hashlib import sha256
from pathlib import Path
from typing import cast

from pydantic import BaseModel

from .models import SemanticBundle, SourceManifest, TableId

_RAW_TABLE_NAME = re.compile(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*")


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
