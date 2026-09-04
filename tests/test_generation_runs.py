from __future__ import annotations

import threading
import time

import pytest

import cerebro.generation_runs as generation_runs
from cerebro.generation import CandidateValidationError
from cerebro.generation_runs import GenerationRunConflict, GenerationRunManager, GenerationRunNotReady
from cerebro.llm import GenerationOutputError
from cerebro.models import ValidationIssue


def test_generation_manager_blocks_concurrency_and_sanitizes_failures(monkeypatch, tmp_path):
    started = threading.Event()
    release = threading.Event()

    def fail_workflow(**kwargs):
        kwargs["on_stage"]("source_check", "started", "Checking source.", {})
        started.set()
        release.wait(timeout=2)
        raise RuntimeError("secret path /private/database.duckdb")

    monkeypatch.setattr(generation_runs, "run_generation_workflow", fail_workflow)
    manager = GenerationRunManager(None, lambda: None, output_root=tmp_path)
    first = manager.start()
    assert started.wait(timeout=1)

    with pytest.raises(GenerationRunConflict) as conflict:
        manager.start()
    assert conflict.value.run_id == first.id
    with pytest.raises(GenerationRunNotReady):
        manager.graph(first.id)

    release.set()
    for _ in range(100):
        failed = manager.get(first.id)
        if failed.status == "failed":
            break
        time.sleep(0.01)
    assert failed.status == "failed"
    assert failed.error == {
        "code": "generation_failed",
        "message": "Candidate generation failed. Inspect the server log for the underlying error.",
        "type": "RuntimeError",
    }
    assert "private" not in failed.model_dump_json()


def test_generation_manager_surfaces_safe_candidate_validation_details(monkeypatch, tmp_path):
    def fail_workflow(**_kwargs):
        raise CandidateValidationError([
            ValidationIssue(
                code="invalid_concept_mapping_target",
                message="concept.account maps to unknown table.missing",
                path="concepts/account.md",
            )
        ])

    monkeypatch.setattr(generation_runs, "run_generation_workflow", fail_workflow)
    manager = GenerationRunManager(None, lambda: None, output_root=tmp_path)
    started = manager.start(source_mode="database_only")
    for _ in range(100):
        failed = manager.get(started.id)
        if failed.status == "failed":
            break
        time.sleep(0.01)
    assert failed.error == {
        "code": "candidate_validation_failed",
        "message": "Candidate validation failed: concept.account maps to unknown table.missing.",
        "type": "CandidateValidationError",
    }


def test_generation_manager_types_invalid_model_output(monkeypatch, tmp_path):
    monkeypatch.setattr(
        generation_runs,
        "run_generation_workflow",
        lambda **_kwargs: (_ for _ in ()).throw(GenerationOutputError("business_semantics")),
    )
    manager = GenerationRunManager(None, lambda: None, output_root=tmp_path)
    started = manager.start(source_mode="database_only")
    for _ in range(100):
        failed = manager.get(started.id)
        if failed.status == "failed":
            break
        time.sleep(0.01)
    assert failed.error == {
        "code": "generation_output_invalid",
        "message": "Model output did not match the required business_semantics schema.",
        "type": "GenerationOutputError",
    }
