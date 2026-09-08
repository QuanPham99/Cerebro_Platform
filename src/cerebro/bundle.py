from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from pydantic import ValidationError

from .models import SemanticBundle, SemanticObject, ValidationIssue, ValidationReport
from .semantic.validator import validate_profile_object
from .upstream import OKFDocument, OKFDocumentError

ALLOWED_CARDINALITIES = {"one-to-one", "one-to-many", "many-to-one", "many-to-many"}
ALLOWED_CLASSIFICATIONS = {"public", "internal", "confidential", "restricted"}
ALLOWED_PROVENANCE = {"discovered", "declared", "ai_proposed", "human_reviewed", "derived"}


class BundleLoadError(ValueError):
    pass


class BundleLoader:
    def load(self, root: Path | str) -> SemanticBundle:
        root_path = Path(root)
        if not root_path.exists():
            raise FileNotFoundError(f"OKF bundle not found: {root_path}")
        objects: list[SemanticObject] = []
        okf_version: str | None = None
        for path in sorted(root_path.rglob("*.md")):
            try:
                document = OKFDocument.parse(path.read_text(encoding="utf-8"))
                if path.name in {"index.md", "log.md"}:
                    if path == root_path / "index.md" and document.frontmatter.get("okf_version"):
                        okf_version = str(document.frontmatter["okf_version"])
                    continue
                document.validate()
            except OKFDocumentError as exc:
                raise BundleLoadError(f"{path}: {exc}") from exc
            frontmatter = dict(document.frontmatter)
            if not frontmatter.get("id"):
                continue  # navigation-only index
            frontmatter["body"] = document.body
            frontmatter["path"] = str(path.relative_to(root_path))
            try:
                objects.append(SemanticObject.model_validate(frontmatter))
            except ValidationError as exc:
                raise BundleLoadError(f"{path}: invalid Cerebro extension: {exc}") from exc
        manifest = root_path / "bundle.yaml"
        if not manifest.exists():
            raise BundleLoadError(f"Missing bundle manifest: {manifest}")
        import yaml

        metadata = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
        return SemanticBundle(
            name=str(metadata.get("name", root_path.name)),
            version=str(metadata.get("version", "0.0.0")),
            root=str(root_path),
            objects=objects,
            generation_mode=str(metadata.get("generation_mode", "fallback")),
            review_state=str(metadata.get("review_state", "active")),
            provider=metadata.get("provider"),
            model=metadata.get("model"),
            source_mode=metadata.get("source_mode", "configured"),
            discovery_evidence=metadata.get("discovery_evidence", {}),
            okf_version=okf_version or metadata.get("okf_version"),
            semantic_profile_version=metadata.get("semantic_profile_version"),
        )


class BundleValidator:
    def validate(self, bundle: SemanticBundle) -> ValidationReport:
        issues: list[ValidationIssue] = []
        by_id: dict[str, SemanticObject] = {}
        for obj in bundle.objects:
            if obj.id in by_id:
                issues.append(ValidationIssue(code="duplicate_id", message=f"Duplicate ID: {obj.id}", path=obj.path))
            by_id[obj.id] = obj
        tables = {obj.id: obj for obj in bundle.objects if obj.profile_kind == "physical_table"}
        table_columns = {
            obj.id: {str(col.get("name")) for col in obj.cerebro.get("columns", [])}
            for obj in tables.values()
        }
        for obj in bundle.objects:
            classification = str(obj.cerebro.get("classification", "internal"))
            if classification not in ALLOWED_CLASSIFICATIONS:
                issues.append(ValidationIssue(code="invalid_classification", message=f"{obj.id} has invalid classification {classification}", path=obj.path))
            provenance_origin = str(obj.provenance.get("origin", ""))
            if obj.provenance and provenance_origin not in ALLOWED_PROVENANCE:
                issues.append(ValidationIssue(code="invalid_provenance", message=f"{obj.id} has invalid provenance origin {provenance_origin}", path=obj.path))
            for link in obj.links:
                if obj.profile_kind != "generic" and link not in by_id:
                    issues.append(ValidationIssue(code="dangling_link", message=f"{obj.id} links to missing {link}", path=obj.path))
            if obj.profile_kind == "relationship":
                self._validate_relationship(obj, tables, table_columns, issues)
            if obj.profile_kind == "legacy_concept":
                self._validate_concept(obj, by_id, issues)
            if obj.profile_kind == "metric" and not obj.cerebro.get("measure"):
                self._validate_metric(obj, by_id, issues)
            if obj.profile_kind == "policy":
                self._validate_policy(obj, by_id, table_columns, issues)
            issues.extend(validate_profile_object(obj, by_id, tables, table_columns))
        return ValidationReport(valid=not issues, document_count=len(bundle.objects), issues=issues)

    @staticmethod
    def _validate_semantic_links(
        obj: SemanticObject,
        field: str,
        targets: object,
        issues: list[ValidationIssue],
    ) -> list[str]:
        normalized = [str(target) for target in targets] if isinstance(targets, list) else []
        if set(obj.links) != set(normalized):
            issues.append(
                ValidationIssue(
                    code="semantic_links_mismatch",
                    message=f"{obj.id} links do not match cerebro.{field}",
                    path=obj.path,
                )
            )
        return normalized

    @classmethod
    def _validate_concept(
        cls,
        obj: SemanticObject,
        by_id: dict[str, SemanticObject],
        issues: list[ValidationIssue],
    ) -> None:
        mappings = cls._validate_semantic_links(obj, "maps_to", obj.cerebro.get("maps_to", []), issues)
        if not any(target in by_id and by_id[target].profile_kind == "physical_table" for target in mappings):
            issues.append(
                ValidationIssue(
                    code="missing_concept_table_mapping",
                    message=f"{obj.id} must map to at least one table",
                    path=obj.path,
                )
            )
        for target in mappings:
            if target not in by_id or by_id[target].profile_kind not in {"physical_table", "metric"}:
                issues.append(
                    ValidationIssue(
                        code="invalid_concept_mapping_target",
                        message=f"{obj.id} maps to invalid target {target}",
                        path=obj.path,
                    )
                )

    @staticmethod
    def _validate_relationship(
        obj: SemanticObject,
        tables: dict[str, SemanticObject],
        table_columns: dict[str, set[str]],
        issues: list[ValidationIssue],
    ) -> None:
        spec = obj.cerebro
        physical = spec.get("physical", {}) if isinstance(spec.get("physical"), dict) else {}
        physical_source = physical.get("source", {}) if isinstance(physical.get("source"), dict) else {}
        physical_target = physical.get("target", {}) if isinstance(physical.get("target"), dict) else {}
        source = str(spec.get("source_table") or physical_source.get("table") or "")
        target = str(spec.get("target_table") or physical_target.get("table") or "")
        raw_cardinality = spec.get("cardinality", "")
        cardinality = (
            f"{raw_cardinality.get('source')}-to-{raw_cardinality.get('target')}"
            if isinstance(raw_cardinality, dict)
            else str(raw_cardinality)
        )
        if source not in tables or target not in tables:
            issues.append(ValidationIssue(code="missing_endpoint", message=f"{obj.id} has missing relationship endpoint", path=obj.path))
        if cardinality not in ALLOWED_CARDINALITIES:
            issues.append(ValidationIssue(code="invalid_cardinality", message=f"{obj.id} has invalid cardinality {cardinality}", path=obj.path))
        for table_id, field, binding in (
            (source, "source_column", physical_source),
            (target, "target_column", physical_target),
        ):
            column = str(spec.get(field) or binding.get("column") or "")
            if table_id in table_columns and column not in table_columns[table_id]:
                issues.append(ValidationIssue(code="undeclared_join_column", message=f"{obj.id} references missing {table_id}.{column}", path=obj.path))

    @classmethod
    def _validate_metric(
        cls,
        obj: SemanticObject,
        by_id: dict[str, SemanticObject],
        issues: list[ValidationIssue],
    ) -> None:
        dependencies = cls._validate_semantic_links(
            obj, "dependencies", obj.cerebro.get("dependencies", []), issues
        )
        if not dependencies:
            issues.append(
                ValidationIssue(
                    code="missing_metric_dependency",
                    message=f"{obj.id} must depend on at least one table",
                    path=obj.path,
                )
            )
        for dependency in dependencies:
            if dependency not in by_id:
                issues.append(ValidationIssue(code="unresolved_metric_dependency", message=f"{obj.id} depends on missing {dependency}", path=obj.path))
            elif by_id[dependency].profile_kind != "physical_table":
                issues.append(
                    ValidationIssue(
                        code="invalid_metric_dependency_target",
                        message=f"{obj.id} depends on non-table target {dependency}",
                        path=obj.path,
                    )
                )
        filters = obj.cerebro.get("filters", [])
        if not isinstance(filters, list):
            issues.append(ValidationIssue(code="invalid_metric_filter", message=f"{obj.id} filters must be a list", path=obj.path))
        if not obj.cerebro.get("formula"):
            issues.append(ValidationIssue(code="invalid_metric_formula", message=f"{obj.id} has no formula", path=obj.path))
        formula_tables = {f"table.{name}" for name in re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\.", str(obj.cerebro.get("formula", "")))}
        undeclared = sorted(formula_tables - set(dependencies))
        if undeclared:
            issues.append(ValidationIssue(code="undeclared_formula_reference", message=f"{obj.id} formula references undeclared dependencies: {', '.join(undeclared)}", path=obj.path))

    @classmethod
    def _validate_policy(
        cls,
        obj: SemanticObject,
        by_id: dict[str, SemanticObject],
        table_columns: dict[str, set[str]],
        issues: list[ValidationIssue],
    ) -> None:
        targets = cls._validate_semantic_links(
            obj, "applies_to", obj.cerebro.get("applies_to", []), issues
        )
        if not targets:
            issues.append(
                ValidationIssue(
                    code="missing_policy_target",
                    message=f"{obj.id} must apply to at least one table",
                    path=obj.path,
                )
            )
        for target in targets:
            if target not in by_id or by_id[target].profile_kind != "physical_table":
                issues.append(
                    ValidationIssue(
                        code="invalid_policy_target",
                        message=f"{obj.id} applies to invalid target {target}",
                        path=obj.path,
                    )
                )
        if obj.provenance.get("origin") != "ai_proposed":
            return
        if not obj.cerebro.get("rule"):
            issues.append(ValidationIssue(code="missing_policy_rule", message=f"{obj.id} has no policy rule", path=obj.path))
        confidence = obj.cerebro.get("confidence")
        if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or not 0 <= confidence <= 1:
            issues.append(ValidationIssue(code="invalid_policy_confidence", message=f"{obj.id} has invalid confidence", path=obj.path))
        evidence = obj.cerebro.get("evidence", [])
        evidence_items = evidence if isinstance(evidence, list) else []
        known_columns = {
            f"{table_id.removeprefix('table.')}.{column}"
            for table_id, columns in table_columns.items()
            for column in columns
        }
        references = {
            match.group(0)
            for item in evidence_items
            for match in re.finditer(r"\b[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*\b", str(item))
        }
        if not references or not references <= known_columns:
            issues.append(
                ValidationIssue(
                    code="invalid_policy_evidence",
                    message=f"{obj.id} must cite only real table.column catalog identifiers",
                    path=obj.path,
                )
            )


def load_validated_bundle(root: Path | str) -> SemanticBundle:
    bundle = BundleLoader().load(root)
    report = BundleValidator().validate(bundle)
    if not report.valid:
        rendered = "; ".join(f"{item.code}: {item.message}" for item in report.issues)
        raise BundleLoadError(rendered)
    return bundle
