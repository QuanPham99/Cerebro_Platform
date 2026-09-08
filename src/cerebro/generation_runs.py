from __future__ import annotations

import logging
import json
import threading
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .enrichment import GenerationProvider
from .llm import GenerationOutputError
from .generation import (
    ActivationError,
    CandidateValidationError,
    activate_bundle,
    review_bundle,
    run_generation_workflow,
)
from .models import (
    GenerationCandidate,
    GenerationEvent,
    GenerationRun,
    GenerationTrace,
    GenerationTraceStep,
    ReviewRecord,
    ReviewRequest,
    SemanticObject,
)
from .paths import DEFAULT_CONFIG, ROOT
from .retrieval import SemanticRetriever

logger = logging.getLogger(__name__)

_LLM_ERROR_MESSAGES = {
    "timeout": "The model provider timed out",
    "rate_limited": "The model provider rate-limited the request",
    "server_error": "The model provider returned a server error",
    "connection_error": "Could not connect to the model provider",
}

COMMANDS = {
    "source_check": "cerebro doctor",
    "catalog_scan": "cerebro scan",
    "business_semantics": "cerebro generate",
    "relationship_semantics": "cerebro generate",
    "query_semantics": "cerebro generate",
    "compile_okf": "cerebro generate",
    "validate_candidate": "cerebro validate --bundle <candidate>",
    "candidate_ready": "cerebro review --bundle <candidate> --reviewer <name> --acknowledge-ai-risk",
}

STAGE_ORDER = tuple(COMMANDS)
STAGE_ACTORS = {
    "source_check": ("source", None),
    "catalog_scan": ("source", None),
    "business_semantics": ("agent", "semantic_inventory"),
    "relationship_semantics": ("agent", "relationship"),
    "query_semantics": ("agent", "metric_rule"),
    "compile_okf": ("compiler", None),
    "validate_candidate": ("validator", None),
    "candidate_ready": ("system", None),
}


class GenerationRunNotFound(KeyError):
    pass


class GenerationRunConflict(RuntimeError):
    def __init__(self, run_id: str):
        self.run_id = run_id
        super().__init__(f"Generation run {run_id} is already in progress")


class GenerationRunNotReady(RuntimeError):
    pass


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


class GenerationRunManager:
    def __init__(
        self,
        database_path: Path | str | None,
        provider_factory: Callable[[], GenerationProvider | None],
        config_path: Path | str = DEFAULT_CONFIG,
        output_root: Path | str = ROOT / "knowledge" / "generated",
        reviewed_root: Path | str = ROOT / "knowledge" / "reviewed",
        database_schema: str | None = None,
        on_activate: Callable[[Path], None] | None = None,
    ):
        self.database_path = database_path
        self.provider_factory = provider_factory
        self.config_path = Path(config_path)
        self.output_root = Path(output_root)
        self.reviewed_root = Path(reviewed_root)
        self.database_schema = database_schema
        self.on_activate = on_activate
        self._runs: dict[str, GenerationRun] = {}
        self._retrievers: dict[str, SemanticRetriever] = {}
        self._outputs: dict[str, Path] = {}
        self._reviews: dict[str, ReviewRecord] = {}
        self._traces: dict[str, dict[str, GenerationTraceStep]] = {}
        self._active_id: str | None = None
        self._lock = threading.Lock()

    def start(self, source_mode: str = "configured") -> GenerationRun:
        if source_mode not in {"configured", "database_only"}:
            raise ValueError("source_mode must be configured or database_only")
        with self._lock:
            if self._active_id:
                active = self._runs[self._active_id]
                if active.status in {"queued", "running"}:
                    raise GenerationRunConflict(active.id)
            now = _timestamp()
            run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]
            run = GenerationRun(
                id=run_id,
                status="queued",
                created_at=now,
                updated_at=now,
                source_mode=source_mode,  # type: ignore[arg-type]
            )
            self._runs[run_id] = run
            self._traces[run_id] = {}
            self._active_id = run_id
        threading.Thread(target=self._execute, args=(run_id,), daemon=True, name=f"cerebro-generation-{run_id}").start()
        return self.get(run_id)

    def get(self, run_id: str) -> GenerationRun:
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                raise GenerationRunNotFound(run_id)
            return run.model_copy(deep=True)

    def events_after(self, run_id: str, sequence: int) -> tuple[list[GenerationEvent], bool]:
        run = self.get(run_id)
        events = [event for event in run.events if event.sequence > sequence]
        return events, run.status in {"succeeded", "failed"}

    def trace(self, run_id: str) -> GenerationTrace:
        with self._lock:
            if run_id not in self._runs:
                raise GenerationRunNotFound(run_id)
            steps = self._traces.get(run_id, {})
            ordered = [steps[stage].model_copy(deep=True) for stage in STAGE_ORDER if stage in steps]
        return GenerationTrace(run_id=run_id, steps=ordered)

    def graph(self, run_id: str) -> dict[str, Any]:
        retriever = self._get_retriever(run_id)
        return retriever.graph().model_dump(mode="json")

    def concept(self, run_id: str, concept_id: str) -> SemanticObject | None:
        return self._get_retriever(run_id).by_id.get(concept_id)

    def snapshot(self, run_id: str) -> dict[str, Any]:
        output = self._ready_output(run_id)
        return json.loads((output / "snapshot.json").read_text(encoding="utf-8"))

    def document(self, run_id: str, document_path: str) -> Path:
        output = self._ready_output(run_id).resolve()
        requested = (output / document_path).resolve()
        if output not in requested.parents or requested.suffix != ".md" or not requested.is_file():
            raise FileNotFoundError(document_path)
        return requested

    def review(self, run_id: str, request: ReviewRequest) -> ReviewRecord:
        output = self._ready_output(run_id)
        record = review_bundle(
            output,
            reviewer=request.reviewer,
            decision=request.decision,
            comment=request.comment,
            acknowledge_ai_risk=request.acknowledge_ai_risk,
            reviewed_root=self.reviewed_root,
        )
        with self._lock:
            self._reviews[run_id] = record
            candidate = self._runs[run_id].candidate
            if candidate is not None:
                candidate.review_state = "approved" if record.decision == "approve" else "rejected"
                candidate.review_record = record.model_dump(mode="json")
                self._runs[run_id].updated_at = _timestamp()
        return record

    def activate(self, run_id: str) -> dict[str, Any]:
        self._ready_output(run_id)
        with self._lock:
            record = self._reviews.get(run_id)
        if record is None:
            review_path = self._outputs[run_id] / "review.json"
            if review_path.is_file():
                record = ReviewRecord.model_validate_json(review_path.read_text(encoding="utf-8"))
        if record is None or record.decision != "approve" or not record.reviewed_bundle:
            raise ActivationError("Generation run does not have an approved reviewed bundle")
        reviewed_path = Path(record.reviewed_bundle)
        payload = activate_bundle(reviewed_path)
        if self.on_activate:
            self.on_activate(reviewed_path)
        return payload

    def _ready_output(self, run_id: str) -> Path:
        run = self.get(run_id)
        if run.status != "succeeded" or run_id not in self._outputs:
            raise GenerationRunNotReady(run_id)
        return self._outputs[run_id]

    def _get_retriever(self, run_id: str) -> SemanticRetriever:
        run = self.get(run_id)
        if run.status != "succeeded" or run_id not in self._retrievers:
            raise GenerationRunNotReady(run_id)
        return self._retrievers[run_id]

    def _emit(self, run_id: str, stage: str, status: str, summary: str, details: dict[str, Any]) -> None:
        with self._lock:
            run = self._runs[run_id]
            timestamp = _timestamp()
            event = GenerationEvent(
                sequence=len(run.events) + 1,
                stage=stage,
                status=status,
                summary=summary,
                command=COMMANDS[stage],
                timestamp=timestamp,
                details=details,
            )
            run.events.append(event)
            run.status = "running"
            run.updated_at = event.timestamp
            traces = self._traces[run_id]
            trace_status = "running" if status == "started" else status
            step = traces.get(stage)
            if step is None:
                actor, agent_id = STAGE_ACTORS[stage]
                step = GenerationTraceStep(
                    stage=stage,
                    actor=actor,
                    agent_id=agent_id,
                    status=trace_status,
                    command=COMMANDS[stage],
                )
                traces[stage] = step
            step.status = trace_status
            step.summary = summary
            if status == "started" and step.started_at is None:
                step.started_at = timestamp
            if status in {"completed", "skipped", "failed", "degraded"}:
                step.completed_at = timestamp

    def _record_trace(
        self,
        run_id: str,
        stage: str,
        status: str,
        input_payload: dict[str, Any] | None,
        output_payload: dict[str, Any] | None,
    ) -> None:
        with self._lock:
            traces = self._traces[run_id]
            step = traces.get(stage)
            if step is None:
                actor, agent_id = STAGE_ACTORS[stage]
                step = GenerationTraceStep(
                    stage=stage,
                    actor=actor,
                    agent_id=agent_id,
                    status="running" if status == "started" else status,
                    command=COMMANDS[stage],
                )
                traces[stage] = step
            step.status = "running" if status == "started" else status
            if input_payload is not None:
                step.input = input_payload
            if output_payload is not None:
                step.output = output_payload
            if status == "started" and step.started_at is None:
                step.started_at = _timestamp()
            if status in {"completed", "skipped", "failed", "degraded"}:
                step.completed_at = _timestamp()
            if status == "failed":
                message = str((output_payload or {}).get("error", "This stage failed."))
                step.error = {"message": message}

    def _execute(self, run_id: str) -> None:
        output = self.output_root / run_id
        try:
            result = run_generation_workflow(
                config_path=self.config_path,
                database_path=self.database_path,
                output=output,
                provider=self.provider_factory(),
                on_stage=lambda stage, status, summary, details: self._emit(
                    run_id, stage, status, summary, details
                ),
                on_trace=lambda stage, status, input_payload, output_payload: self._record_trace(
                    run_id, stage, status, input_payload, output_payload
                ),
                source_mode=self.get(run_id).source_mode,
                database_schema=self.database_schema,
                include_query_semantics=False,
            )
            counts: dict[str, int] = {}
            for obj in result.bundle.objects:
                counts[obj.type] = counts.get(obj.type, 0) + 1
            candidate = GenerationCandidate(
                name=result.bundle.name,
                version=result.bundle.version,
                counts=counts,
                generation_mode=result.proposal.generation_mode,
                provider=result.proposal.provider,
                model=result.proposal.model,
                source_mode=result.snapshot.source_mode,
                discovery_evidence=result.snapshot.discovery_evidence,
            )
            retriever = SemanticRetriever(result.bundle)
            with self._lock:
                run = self._runs[run_id]
                run.candidate = candidate
                run.status = "succeeded"
                run.updated_at = _timestamp()
                self._retrievers[run_id] = retriever
                self._outputs[run_id] = result.output
                if self._active_id == run_id:
                    self._active_id = None
        except Exception as exc:
            logger.exception("Generation run %s failed", run_id)
            if isinstance(exc, CandidateValidationError):
                error = {
                    "code": "candidate_validation_failed",
                    "message": exc.public_message,
                    "type": type(exc).__name__,
                }
            elif isinstance(exc, GenerationOutputError):
                error = {
                    "code": "generation_output_invalid",
                    "message": exc.public_message,
                    "type": type(exc).__name__,
                }
            elif getattr(exc, "cerebro_reason", None) in _LLM_ERROR_MESSAGES:
                reason = exc.cerebro_reason  # type: ignore[attr-defined]
                attempts = getattr(exc, "cerebro_attempts", None)
                attempt_note = f" after {attempts} attempt(s)" if attempts else ""
                error = {
                    "code": f"llm_{reason}",
                    "message": (
                        f"{_LLM_ERROR_MESSAGES[reason]}{attempt_note}. Check the CEREBRO_LLM_TIMEOUT_SECONDS "
                        "and CEREBRO_LLM_MAX_RETRIES settings and the provider's status, then retry the build."
                    ),
                    "type": type(exc).__name__,
                }
            else:
                error = {
                    "code": "generation_failed",
                    "message": "Candidate generation failed. Inspect the server log for the underlying error.",
                    "type": type(exc).__name__,
                }
            with self._lock:
                run = self._runs[run_id]
                run.status = "failed"
                run.updated_at = _timestamp()
                run.error = error
                if self._active_id == run_id:
                    self._active_id = None
