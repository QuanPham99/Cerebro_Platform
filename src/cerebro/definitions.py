from __future__ import annotations

import json
import shutil
import tempfile
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from .bundle import BundleLoader, BundleValidator
from .generation import ActivationError, ReviewValidationError, activate_bundle, bundle_digest, review_bundle
from .models import (
    BusinessRuleCandidate,
    BusinessRuleDefinitionPayload,
    DefinitionApplyRequest,
    DefinitionRevision,
    DefinitionTranslateRequest,
    DefinitionTranslation,
    MetricDefinitionPayload,
    ReviewRecord,
    ReviewRequest,
    SemanticBundle,
    SemanticObject,
    StructuredMetricCandidate,
)
from .semantic.agents import GenerationProvider
from .semantic.compiler import canonical_object_id, metric_formula, normalize_reference
from .retrieval import SemanticRetriever
from .upstream import OKFDocument


class DefinitionRevisionNotFound(KeyError):
    pass


class DefinitionConflictError(RuntimeError):
    pass


class DefinitionValidationError(ValueError):
    pass


class DefinitionProviderUnavailable(RuntimeError):
    pass


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _revision_id() -> str:
    return "definition-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]


def _reference(bundle: SemanticBundle, value: str, kind: str) -> str:
    raw = value.strip()
    if raw in bundle.by_id():
        return raw
    if kind == "physical_table" and f"table.{raw}" in bundle.by_id():
        return f"table.{raw}"
    return normalize_reference(kind, raw)


def _normalize_metric(bundle: SemanticBundle, value: StructuredMetricCandidate) -> StructuredMetricCandidate:
    payload = value.model_dump(mode="json")
    payload["id"] = canonical_object_id("metric", value.id)
    payload["entity"] = _reference(bundle, value.entity, "entity")
    payload["dependencies"] = [_reference(bundle, item, "physical_table") for item in value.dependencies]
    payload["compatible_dimensions"] = [_reference(bundle, item, "dimension") for item in value.compatible_dimensions]
    if value.time_dimension:
        payload["time_dimension"] = _reference(bundle, value.time_dimension, "dimension")
    measure = payload["measure"]
    terms = [measure] if measure["kind"] == "aggregate" else [measure["numerator"], measure["denominator"]]
    for term in terms:
        if term.get("source"):
            term["source"]["table"] = _reference(bundle, term["source"]["table"], "physical_table")
        for predicate in term.get("predicates", []):
            predicate["source"]["table"] = _reference(bundle, predicate["source"]["table"], "physical_table")
    return StructuredMetricCandidate.model_validate(payload)


def _rule_dependency(bundle: SemanticBundle, value: str) -> str:
    raw = value.strip()
    if raw in bundle.by_id():
        return raw
    if f"table.{raw}" in bundle.by_id():
        return f"table.{raw}"
    prefix = raw.partition(".")[0].lower()
    kinds = {"entity": "entity", "dimension": "dimension", "metric": "metric", "rule": "business_rule"}
    return normalize_reference(kinds[prefix], raw) if prefix in kinds else raw


def _normalize_rule(bundle: SemanticBundle, value: BusinessRuleCandidate) -> BusinessRuleCandidate:
    payload = value.model_dump(mode="json")
    payload["id"] = canonical_object_id("rule", value.id)
    payload["entity"] = _reference(bundle, value.entity, "entity")
    payload["dependencies"] = [_rule_dependency(bundle, item) for item in value.dependencies]
    return BusinessRuleCandidate.model_validate(payload)


def _normalize_request(bundle: SemanticBundle, request: DefinitionApplyRequest) -> DefinitionApplyRequest:
    payload = request.payload
    if payload.kind == "metric":
        normalized = MetricDefinitionPayload(kind="metric", definition=_normalize_metric(bundle, payload.definition))
    else:
        normalized = BusinessRuleDefinitionPayload(
            kind="business_rule",
            definition=_normalize_rule(bundle, payload.definition),
        )
    result = DefinitionApplyRequest(payload=normalized, origin=request.origin)
    _validate_normalized(bundle, result)
    return result


def _validate_normalized(bundle: SemanticBundle, request: DefinitionApplyRequest) -> None:
    by_id = bundle.by_id()

    def require(object_id: str, kinds: set[str], field: str) -> SemanticObject:
        target = by_id.get(object_id)
        if target is None or target.profile_kind not in kinds:
            raise DefinitionValidationError(f"{field} references invalid target {object_id}")
        return target

    definition = request.payload.definition
    require(definition.entity, {"entity"}, "entity")
    if request.payload.kind == "business_rule":
        for dependency in definition.dependencies:
            require(
                dependency,
                {"entity", "dimension", "metric", "business_rule", "physical_table"},
                "dependency",
            )
        return
    for dependency in definition.dependencies:
        require(dependency, {"physical_table"}, "dependency")
    for dimension in definition.compatible_dimensions:
        require(dimension, {"dimension"}, "compatible dimension")
    if definition.time_dimension:
        require(definition.time_dimension, {"dimension"}, "time dimension")
    measure = definition.measure.model_dump(mode="json")
    terms = [measure] if measure["kind"] == "aggregate" else [measure["numerator"], measure["denominator"]]
    for term in terms:
        bindings = ([term.get("source")] if term.get("source") else []) + [
            predicate.get("source") for predicate in term.get("predicates", [])
        ]
        for binding in bindings:
            if not binding:
                continue
            table = require(str(binding["table"]), {"physical_table"}, "measure")
            columns = {str(column.get("name")) for column in table.cerebro.get("columns", [])}
            if str(binding["column"]) not in columns:
                raise DefinitionValidationError(
                    f"measure references invalid column {binding['table']}.{binding['column']}"
                )


def _definition_frontmatter(
    request: DefinitionApplyRequest,
    *,
    provider: GenerationProvider | None,
) -> tuple[str, dict[str, Any], str]:
    payload = request.payload
    definition = payload.definition
    generated = None
    if request.origin == "ai_proposed":
        generated = {
            "by": f"{provider.name}/{provider.model}" if provider else "definition-composer/unknown",
            "at": _timestamp(),
        }
    common: dict[str, Any] = {
        "id": definition.id,
        "name": definition.name,
        "title": definition.name,
        "description": definition.description,
        "status": "draft",
        "sources": [],
        "provenance": {"origin": request.origin, "source": "definition-composer"},
    }
    if generated:
        common["generated"] = generated
    if payload.kind == "metric":
        metric = definition
        links = sorted(set([metric.entity, *metric.dependencies, *metric.compatible_dimensions] + ([metric.time_dimension] if metric.time_dimension else [])))
        frontmatter = {
            **common,
            "type": "Metric",
            "links": links,
            "cerebro": {
                "kind": "metric",
                "classification": metric.classification,
                "entity": metric.entity,
                "measure": metric.measure.model_dump(mode="json"),
                "dependencies": metric.dependencies,
                "formula": metric_formula(metric.measure.model_dump(mode="json")),
                "filters": [],
                "grain": metric.grain.model_dump(mode="json"),
                "compatible_dimensions": metric.compatible_dimensions,
                "time_dimension": metric.time_dimension,
                "relative_time_anchor": metric.relative_time_anchor,
                "warnings": metric.warnings,
            },
        }
        body = f"# {metric.name}\n\n{metric.description}\n\nFormula: `{frontmatter['cerebro']['formula']}`"
        return "metrics", frontmatter, body
    rule = definition
    frontmatter = {
        **common,
        "type": "Business Rule",
        "links": sorted(set([rule.entity, *rule.dependencies])),
        "cerebro": {
            "kind": "business_rule",
            "classification": rule.classification,
            "entity": rule.entity,
            "rule_kind": rule.rule_kind,
            "output_type": rule.output_type,
            "dependencies": rule.dependencies,
            "logic": rule.logic,
            "grain": rule.grain.model_dump(mode="json"),
            "warnings": rule.warnings,
        },
    }
    return "rules", frontmatter, f"# {rule.name}\n\n{rule.logic}"


def _write_document(root: Path, folder: str, frontmatter: dict[str, Any], body: str) -> None:
    suffix = str(frontmatter["id"]).split(".", 1)[1]
    path = root / folder / f"{suffix}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(OKFDocument(frontmatter=frontmatter, body=body.strip() + "\n").serialize(), encoding="utf-8")
    index = root / folder / "index.md"
    if not index.exists():
        index.write_text(f"# {folder.title()}\n", encoding="utf-8")


def _add_metric_backlinks(root: Path, metric: StructuredMetricCandidate) -> None:
    for dimension_id in metric.compatible_dimensions:
        path = root / "dimensions" / f"{dimension_id.split('.', 1)[1]}.md"
        document = OKFDocument.parse(path.read_text(encoding="utf-8"))
        frontmatter = dict(document.frontmatter)
        links = {str(item) for item in frontmatter.get("links", [])}
        links.add(metric.id)
        frontmatter["links"] = sorted(links)
        cerebro = dict(frontmatter.get("cerebro", {}))
        compatible = {str(item) for item in cerebro.get("compatible_metrics", [])}
        compatible.add(metric.id)
        cerebro["compatible_metrics"] = sorted(compatible)
        frontmatter["cerebro"] = cerebro
        path.write_text(OKFDocument(frontmatter=frontmatter, body=document.body).serialize(), encoding="utf-8")


def _apply_to_copy(
    root: Path,
    request: DefinitionApplyRequest,
    provider: GenerationProvider | None,
) -> None:
    bundle = BundleLoader().load(root)
    normalized = _normalize_request(bundle, request)
    definition = normalized.payload.definition
    if definition.id in bundle.by_id():
        raise DefinitionConflictError(f"Definition {definition.id} already exists")
    folder, frontmatter, body = _definition_frontmatter(normalized, provider=provider)
    _write_document(root, folder, frontmatter, body)
    if normalized.payload.kind == "metric":
        _add_metric_backlinks(root, normalized.payload.definition)
    compiled = BundleLoader().load(root)
    report = BundleValidator().validate(compiled)
    if not report.valid:
        message = "; ".join(f"{issue.code}: {issue.message}" for issue in report.issues)
        raise DefinitionValidationError(message)


def _summary(revision_id: str, root: Path, review: ReviewRecord | None = None) -> DefinitionRevision:
    bundle = BundleLoader().load(root)
    manifest = yaml.safe_load((root / "bundle.yaml").read_text(encoding="utf-8")) or {}
    counts: dict[str, int] = {}
    for obj in bundle.objects:
        counts[obj.profile_kind] = counts.get(obj.profile_kind, 0) + 1
    return DefinitionRevision(
        id=revision_id,
        base_version=str(manifest.get("parent_version", "")),
        version=bundle.version,
        counts=counts,
        review_state="approved" if review and review.decision == "approve" else "rejected" if review else "candidate",
        review_record=review.model_dump(mode="json") if review else None,
    )


class DefinitionRevisionManager:
    def __init__(
        self,
        active_bundle: Callable[[], SemanticBundle],
        provider_factory: Callable[[], GenerationProvider | None],
        output_root: Path,
        reviewed_root: Path,
        on_activate: Callable[[Path], None] | None = None,
    ):
        self.active_bundle = active_bundle
        self.provider_factory = provider_factory
        self.output_root = output_root
        self.reviewed_root = reviewed_root
        self.on_activate = on_activate
        self._paths: dict[str, Path] = {}
        self._reviews: dict[str, ReviewRecord] = {}

    def translate(self, request: DefinitionTranslateRequest) -> DefinitionTranslation:
        provider = self.provider_factory()
        if provider is None:
            raise DefinitionProviderUnavailable("No model provider is configured; use the manual form instead")
        bundle = self.active_bundle()
        context = [
            {
                "id": obj.id,
                "kind": obj.profile_kind,
                "name": obj.name,
                "description": obj.description,
                "cerebro": obj.cerebro,
            }
            for obj in bundle.objects
            if obj.profile_kind in {"physical_table", "entity", "dimension", "relationship", "metric", "business_rule"}
        ]
        prompt = (
            f"Translate the user's {request.kind.replace('_', ' ')} idea into exactly one strict typed definition. "
            "Use only identifiers, tables, columns, dimensions, and relationships in the supplied approved semantic graph. "
            "Never read or infer source rows. Add warnings for business assumptions. "
            f"User idea: {request.intent}\nPreferred entity: {request.entity_id or 'unspecified'}\n"
            + json.dumps({"semantic_version": bundle.version, "objects": context}, sort_keys=True)
        )
        if request.kind == "metric":
            generated = provider.generate("metric_definition", prompt, MetricDefinitionPayload)
            payload = MetricDefinitionPayload(kind="metric", definition=_normalize_metric(bundle, generated.definition))
        else:
            generated = provider.generate("business_rule_definition", prompt, BusinessRuleDefinitionPayload)
            payload = BusinessRuleDefinitionPayload(
                kind="business_rule",
                definition=_normalize_rule(bundle, generated.definition),
            )
        return DefinitionTranslation(
            payload=payload,
            warnings=payload.definition.warnings,
            provider=provider.name,
            model=provider.model,
        )

    def create(self, request: DefinitionApplyRequest) -> DefinitionRevision:
        base = self.active_bundle()
        if base.review_state != "approved":
            raise DefinitionValidationError("Definitions can be added only after the graph is approved and activated")
        revision_id = _revision_id()
        self.output_root.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix=f".{revision_id}-", dir=self.output_root))
        try:
            shutil.copytree(Path(base.root), temporary, dirs_exist_ok=True)
            for receipt in ("approval.json", "review.json"):
                (temporary / receipt).unlink(missing_ok=True)
            manifest_path = temporary / "bundle.yaml"
            manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
            base_version = base.version.split("+", 1)[0]
            manifest.update({
                "version": f"{base_version}+rev.{revision_id.removeprefix('definition-')}",
                "generation_mode": "authored",
                "review_state": "candidate",
                "run_id": revision_id,
                "parent_version": base.version,
                "parent_digest": bundle_digest(base.root),
            })
            manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
            _apply_to_copy(temporary, request, self.provider_factory())
            destination = self.output_root / revision_id
            temporary.replace(destination)
            self._paths[revision_id] = destination
            return _summary(revision_id, destination)
        except Exception:
            if temporary.exists():
                shutil.rmtree(temporary)
            raise

    def add(self, revision_id: str, request: DefinitionApplyRequest) -> DefinitionRevision:
        current = self._path(revision_id)
        if (current / "review.json").exists():
            raise DefinitionConflictError("Reviewed definition revisions are immutable")
        temporary = Path(tempfile.mkdtemp(prefix=f".{revision_id}-", dir=self.output_root))
        backup = self.output_root / f".{revision_id}-backup"
        try:
            shutil.copytree(current, temporary, dirs_exist_ok=True)
            _apply_to_copy(temporary, request, self.provider_factory())
            current.replace(backup)
            temporary.replace(current)
            shutil.rmtree(backup)
            return _summary(revision_id, current)
        except Exception:
            if temporary.exists():
                shutil.rmtree(temporary)
            if backup.exists() and not current.exists():
                backup.replace(current)
            raise

    def get(self, revision_id: str) -> DefinitionRevision:
        review = self._reviews.get(revision_id)
        if review is None:
            review_path = self._path(revision_id) / "review.json"
            if review_path.exists():
                review = ReviewRecord.model_validate_json(review_path.read_text(encoding="utf-8"))
                self._reviews[revision_id] = review
        return _summary(revision_id, self._path(revision_id), review)

    def graph(self, revision_id: str) -> dict[str, Any]:
        bundle = BundleLoader().load(self._path(revision_id))
        return SemanticRetriever(bundle).graph().model_dump(mode="json")

    def object(self, revision_id: str, object_id: str) -> SemanticObject | None:
        return BundleLoader().load(self._path(revision_id)).by_id().get(object_id)

    def review(self, revision_id: str, request: ReviewRequest) -> ReviewRecord:
        record = review_bundle(
            self._path(revision_id),
            reviewer=request.reviewer,
            decision=request.decision,
            comment=request.comment,
            acknowledge_ai_risk=request.acknowledge_ai_risk,
            reviewed_root=self.reviewed_root,
        )
        self._reviews[revision_id] = record
        return record

    def activate(self, revision_id: str) -> dict[str, Any]:
        review = self._reviews.get(revision_id)
        if review is None:
            review_path = self._path(revision_id) / "review.json"
            if review_path.exists():
                review = ReviewRecord.model_validate_json(review_path.read_text(encoding="utf-8"))
        if review is None or review.decision != "approve" or not review.reviewed_bundle:
            raise ActivationError("Definition revision is not approved")
        reviewed_path = Path(review.reviewed_bundle)
        payload = activate_bundle(reviewed_path)
        if self.on_activate:
            self.on_activate(reviewed_path)
        return payload

    def _path(self, revision_id: str) -> Path:
        path = self._paths.get(revision_id, self.output_root / revision_id)
        if not path.is_dir() or not revision_id.startswith("definition-"):
            raise DefinitionRevisionNotFound(revision_id)
        self._paths[revision_id] = path
        return path
