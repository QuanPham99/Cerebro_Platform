"""Canonical, value-free evidence identity (FR-700/704/708)."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel


def _canonical(value):
    if isinstance(value, BaseModel):
        # Respect serialization exclusions (notably BoundParameter.value).
        return _canonical(value.model_dump(mode="python"))
    if isinstance(value, dict):
        return {str(k): _canonical(v) for k, v in value.items()}
    if isinstance(value, (set, frozenset)):
        return sorted((_canonical(v) for v in value), key=lambda v: json.dumps(v, sort_keys=True))
    if isinstance(value, (list, tuple)):
        return [_canonical(v) for v in value]
    if isinstance(value, Decimal):
        return str(value)
    return value


def canonical_json_bytes(value, *, exclude=frozenset()) -> bytes:
    payload = _canonical(value)
    if exclude:
        payload = {k: v for k, v in payload.items() if k not in exclude}
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()


def digest(value, *, exclude=frozenset()) -> str:
    return hashlib.sha256(canonical_json_bytes(value, exclude=exclude)).hexdigest()


def sha256_file(path: Path | str) -> str:
    result = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def atomic_json(path: Path | str, value) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".evidence-")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(canonical_json_bytes(value) + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def bundle_digest(bundle) -> str:
    return digest({"version": bundle.version, "objects": [x.model_dump(exclude={"path"}) for x in sorted(bundle.objects, key=lambda x: x.id)]})


class QueryFault(Exception):
    """Only codes and authorized subjects cross failure boundaries."""
    def __init__(self, code: str, subject: str = "", *, transport_attempts: int = 0):
        self.transport_attempts = transport_attempts
        self.code = code
        self.subject = subject
        super().__init__(code)


def canonical_question_sha256(question: str) -> str:
    return hashlib.sha256(question.encode("utf-8")).hexdigest()


def source_manifest_sha256(manifest) -> str:
    from .query_models import SourceManifest
    return digest(SourceManifest.model_validate(manifest))


def manifest_table_id(raw_name: str) -> str:
    from pydantic import TypeAdapter
    from .query_models import TableId
    return TypeAdapter(TableId).validate_python("table." + raw_name)
