from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .models import SemanticBundle, SemanticObject, ValidationIssue, ValidationReport
from .upstream import OKFDocument, OKFDocumentError

ALLOWED_CARDINALITIES = {"one-to-one", "one-to-many", "many-to-one", "many-to-many"}


class BundleLoadError(ValueError):
    pass


class BundleLoader:
    def load(self, root: Path | str) -> SemanticBundle:
        root_path = Path(root)
        if not root_path.exists():
            raise FileNotFoundError(f"OKF bundle not found: {root_path}")
        objects: list[SemanticObject] = []
        for path in sorted(root_path.rglob("*.md")):
            try:
                document = OKFDocument.parse(path.read_text(encoding="utf-8"))
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
        )


class BundleValidator:
    def validate(self, bundle: SemanticBundle) -> ValidationReport:
        issues: list[ValidationIssue] = []
        by_id: dict[str, SemanticObject] = {}
        for obj in bundle.objects:
            if obj.id in by_id:
                issues.append(ValidationIssue(code="duplicate_id", message=f"Duplicate ID: {obj.id}", path=obj.path))
            by_id[obj.id] = obj
        tables = {obj.id: obj for obj in bundle.objects if obj.type == "table"}
        table_columns = {
            obj.id: {str(col.get("name")) for col in obj.cerebro.get("columns", [])}
            for obj in tables.values()
        }
        for obj in bundle.objects:
            for link in obj.links:
                if link not in by_id:
                    issues.append(ValidationIssue(code="dangling_link", message=f"{obj.id} links to missing {link}", path=obj.path))
            if obj.type == "relationship":
                self._validate_relationship(obj, tables, table_columns, issues)
            if obj.type == "metric":
                self._validate_metric(obj, by_id, issues)
        return ValidationReport(valid=not issues, document_count=len(bundle.objects), issues=issues)

    @staticmethod
    def _validate_relationship(
        obj: SemanticObject,
        tables: dict[str, SemanticObject],
        table_columns: dict[str, set[str]],
        issues: list[ValidationIssue],
    ) -> None:
        spec = obj.cerebro
        source = str(spec.get("source_table", ""))
        target = str(spec.get("target_table", ""))
        cardinality = str(spec.get("cardinality", ""))
        if source not in tables or target not in tables:
            issues.append(ValidationIssue(code="missing_endpoint", message=f"{obj.id} has missing relationship endpoint", path=obj.path))
        if cardinality not in ALLOWED_CARDINALITIES:
            issues.append(ValidationIssue(code="invalid_cardinality", message=f"{obj.id} has invalid cardinality {cardinality}", path=obj.path))
        for table_id, field in ((source, "source_column"), (target, "target_column")):
            column = str(spec.get(field, ""))
            if table_id in table_columns and column not in table_columns[table_id]:
                issues.append(ValidationIssue(code="undeclared_join_column", message=f"{obj.id} references missing {table_id}.{column}", path=obj.path))

    @staticmethod
    def _validate_metric(
        obj: SemanticObject,
        by_id: dict[str, SemanticObject],
        issues: list[ValidationIssue],
    ) -> None:
        dependencies = obj.cerebro.get("dependencies", [])
        for dependency in dependencies:
            if dependency not in by_id:
                issues.append(ValidationIssue(code="unresolved_metric_dependency", message=f"{obj.id} depends on missing {dependency}", path=obj.path))
        filters = obj.cerebro.get("filters", [])
        if not isinstance(filters, list):
            issues.append(ValidationIssue(code="invalid_metric_filter", message=f"{obj.id} filters must be a list", path=obj.path))
        if not obj.cerebro.get("formula"):
            issues.append(ValidationIssue(code="invalid_metric_formula", message=f"{obj.id} has no formula", path=obj.path))


def load_validated_bundle(root: Path | str) -> SemanticBundle:
    bundle = BundleLoader().load(root)
    report = BundleValidator().validate(bundle)
    if not report.valid:
        rendered = "; ".join(f"{item.code}: {item.message}" for item in report.issues)
        raise BundleLoadError(rendered)
    return bundle

