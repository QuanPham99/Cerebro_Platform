from __future__ import annotations

from pathlib import Path

import yaml

from .bundle import load_validated_bundle
from .paths import DEFAULT_BUNDLE, ROOT
from .models import SemanticBundle, SemanticObject
from .retrieval import SemanticRetriever


SEMANTIC_QUESTIONS = ROOT / "evaluation" / "semantic-questions.yaml"
EXPECTED_KIND_ALIASES = {
    "concept": "concepts", "entity": "entities", "dimension": "dimensions",
    "metric": "metrics", "rule": "rules", "business_rule": "rules",
    "table": "tables", "relationship": "relationships", "join": "relationships",
}


def _relationship_endpoints(item: SemanticObject) -> tuple[str, str]:
    spec = item.cerebro
    physical = spec.get("physical", {})
    source = physical.get("source", {}) if isinstance(physical, dict) else {}
    target = physical.get("target", {}) if isinstance(physical, dict) else {}
    left = (
        f"{spec.get('source_table')}.{spec.get('source_column')}"
        if spec.get("source_table") and spec.get("source_column")
        else f"{source.get('table')}.{source.get('column')}"
    )
    right = (
        f"{spec.get('target_table')}.{spec.get('target_column')}"
        if spec.get("target_table") and spec.get("target_column")
        else f"{target.get('table')}.{target.get('column')}"
    )
    return left, right


def _normalized_relationships(bundle_path: Path | str) -> set[tuple[str, str]]:
    bundle = load_validated_bundle(bundle_path)
    normalized: set[tuple[str, str]] = set()
    for item in bundle.objects:
        if item.profile_kind != "relationship":
            continue
        source, target = _relationship_endpoints(item)
        normalized.add(tuple(sorted((source, target))))
    return normalized


def compare_relationship_oracle(
    candidate_path: Path | str,
    oracle_path: Path | str = DEFAULT_BUNDLE,
) -> dict[str, object]:
    """Compare only after generation; the oracle is never an input to generation."""
    predicted = _normalized_relationships(candidate_path)
    expected = _normalized_relationships(oracle_path)
    matched = predicted & expected
    return {
        "precision": len(matched) / len(predicted) if predicted else (1.0 if not expected else 0.0),
        "recall": len(matched) / len(expected) if expected else 1.0,
        "matched": len(matched),
        "missing": [list(item) for item in sorted(expected - predicted)],
        "invented": [list(item) for item in sorted(predicted - expected)],
    }


SEMANTIC_ORACLE_KINDS = {"entity", "dimension", "metric", "business_rule", "relationship"}
STRUCTURAL_ORACLE_KINDS = {"physical_table", "entity", "dimension", "relationship", "policy"}


def _ids_by_kind(bundle: SemanticBundle, tracked: set[str] | None = None) -> dict[str, set[str]]:
    tracked = tracked or SEMANTIC_ORACLE_KINDS
    result = {kind: set() for kind in tracked}
    for item in bundle.objects:
        if item.profile_kind in result:
            result[item.profile_kind].add(item.id)
    return result


def compare_semantic_oracle(
    candidate_path: Path | str,
    oracle_path: Path | str = DEFAULT_BUNDLE,
    *,
    included_kinds: set[str] | None = None,
) -> dict[str, object]:
    """Score generated semantic object coverage without exposing the oracle to generation."""
    tracked = included_kinds or SEMANTIC_ORACLE_KINDS
    predicted = _ids_by_kind(load_validated_bundle(candidate_path), tracked)
    expected = _ids_by_kind(load_validated_bundle(oracle_path), tracked)
    by_kind: dict[str, dict[str, object]] = {}
    all_predicted: set[str] = set()
    all_expected: set[str] = set()
    for kind in sorted(expected):
        proposed = predicted[kind]
        golden = expected[kind]
        matched = proposed & golden
        all_predicted.update(proposed)
        all_expected.update(golden)
        by_kind[kind] = {
            "precision": len(matched) / len(proposed) if proposed else (1.0 if not golden else 0.0),
            "recall": len(matched) / len(golden) if golden else 1.0,
            "matched": len(matched),
            "missing": sorted(golden - proposed),
            "invented": sorted(proposed - golden),
        }
    all_matched = all_predicted & all_expected
    return {
        "precision": len(all_matched) / len(all_predicted) if all_predicted else (1.0 if not all_expected else 0.0),
        "recall": len(all_matched) / len(all_expected) if all_expected else 1.0,
        "matched": len(all_matched),
        "by_kind": by_kind,
    }


def compare_structural_oracle(
    candidate_path: Path | str,
    oracle_path: Path | str = DEFAULT_BUNDLE,
) -> dict[str, object]:
    """Compare the smoke-test structure while metrics and rules remain user-authored."""
    result = compare_semantic_oracle(
        candidate_path,
        oracle_path,
        included_kinds=STRUCTURAL_ORACLE_KINDS,
    )
    return {**result, "deferred_kinds": ["business_rule", "metric"]}


def _grounding_ids(grounding: object) -> dict[str, set[str]]:
    return {
        "concepts": {item["id"] for item in grounding.concepts},
        "entities": {item["id"] for item in grounding.entities},
        "dimensions": {item["id"] for item in grounding.dimensions},
        "metrics": {item["id"] for item in grounding.metrics},
        "rules": {item["id"] for item in grounding.rules},
        "tables": {item["id"] for item in grounding.tables},
        "relationships": {item["id"] for item in grounding.joins},
    }


def run_evaluation(
    bundle_path: Path | str = DEFAULT_BUNDLE,
    questions_path: Path | str = SEMANTIC_QUESTIONS,
) -> list[dict]:
    retriever = SemanticRetriever(load_validated_bundle(bundle_path))
    cases = yaml.safe_load(Path(questions_path).read_text(encoding="utf-8"))["questions"]
    results = []
    for case in cases:
        grounding = retriever.grounding(case["question"], limit=10)
        actual_by_kind = _grounding_ids(grounding)
        raw_expected_by_kind = case.get("expected")
        if raw_expected_by_kind is None:
            expected = set(case.get("required_ids", []))
            actual = set().union(*actual_by_kind.values())
            expected_by_kind: dict[str, list[str]] = {"objects": sorted(expected)}
            missing_by_kind: dict[str, list[str]] = {"objects": sorted(expected - actual)} if expected - actual else {}
            missing = sorted(expected - actual)
        else:
            expected_by_kind = {
                EXPECTED_KIND_ALIASES.get(kind, kind): sorted(set(required))
                for kind, required in raw_expected_by_kind.items()
            }
            missing_by_kind = {
                kind: sorted(set(required) - actual_by_kind.get(kind, set()))
                for kind, required in expected_by_kind.items()
                if set(required) - actual_by_kind.get(kind, set())
            }
            missing = sorted(item for values in missing_by_kind.values() for item in values)
        results.append({
            "id": case["id"],
            "passed": not missing,
            "missing": missing,
            "missing_by_kind": missing_by_kind,
            "expected_by_kind": expected_by_kind,
            "question": case["question"],
        })
    return results


def summarize_evaluation(results: list[dict]) -> dict[str, object]:
    """Report case, object-kind, and join-path accuracy as separate measures."""
    required: dict[str, int] = {}
    missing: dict[str, int] = {}
    for result in results:
        for kind, values in result.get("expected_by_kind", {}).items():
            required[kind] = required.get(kind, 0) + len(values)
        for kind, values in result.get("missing_by_kind", {}).items():
            missing[kind] = missing.get(kind, 0) + len(values)
    by_kind = {
        kind: {
            "matched": total - missing.get(kind, 0),
            "required": total,
            "accuracy": (total - missing.get(kind, 0)) / total if total else 1.0,
        }
        for kind, total in sorted(required.items())
    }
    passed = sum(bool(result.get("passed")) for result in results)
    return {
        "cases": {"passed": passed, "total": len(results), "accuracy": passed / len(results) if results else 1.0},
        "by_kind": by_kind,
        "join_path_accuracy": by_kind.get("relationships", {"matched": 0, "required": 0, "accuracy": 1.0})["accuracy"],
    }
