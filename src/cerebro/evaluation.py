from __future__ import annotations

from pathlib import Path

import yaml

from .bundle import load_validated_bundle
from .paths import DEFAULT_BUNDLE, ROOT
from .retrieval import SemanticRetriever


def _normalized_relationships(bundle_path: Path | str) -> set[tuple[str, str]]:
    bundle = load_validated_bundle(bundle_path)
    normalized: set[tuple[str, str]] = set()
    for item in bundle.objects:
        if item.type != "relationship":
            continue
        source = f"{item.cerebro.get('source_table')}.{item.cerebro.get('source_column')}"
        target = f"{item.cerebro.get('target_table')}.{item.cerebro.get('target_column')}"
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


def run_evaluation(
    bundle_path: Path | str = DEFAULT_BUNDLE,
    questions_path: Path | str = ROOT / "evaluation" / "golden-questions.yaml",
) -> list[dict]:
    retriever = SemanticRetriever(load_validated_bundle(bundle_path))
    cases = yaml.safe_load(Path(questions_path).read_text(encoding="utf-8"))["questions"]
    results = []
    for case in cases:
        grounding = retriever.grounding(case["question"], limit=10)
        actual = {
            item["id"] for item in grounding.concepts + grounding.tables + grounding.metrics + grounding.joins
        }
        expected = set(case["required_ids"])
        missing = sorted(expected - actual)
        results.append({"id": case["id"], "passed": not missing, "missing": missing, "question": case["question"]})
    return results
