from __future__ import annotations

from pathlib import Path

import yaml

from .bundle import load_validated_bundle
from .paths import DEFAULT_BUNDLE, ROOT
from .retrieval import SemanticRetriever


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

