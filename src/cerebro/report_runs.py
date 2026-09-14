"""In-memory background execution of report runs.

Mirrors src/cerebro/generation_runs.py::GenerationRunManager's shape (background
thread per run, sequence-numbered events, SSE-friendly events_after cursor), but
report runs never conflict with each other: sections are read-only, so multiple
runs may execute concurrently. See specs/023-executive-report-agent.md FR-11 to
FR-13.
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Callable
from datetime import datetime, timezone

from .chat import ChatCancellation, ChatCancelled, ChatRequestRegistry
from .models import ReportDocument, ReportRun
from .report_agent import ReportOrchestrator

logger = logging.getLogger(__name__)


class ReportRunNotFound(KeyError):
    pass


class ReportNotReady(RuntimeError):
    pass


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


class ReportRunManager:
    def __init__(self, orchestrator_factory: Callable[[], ReportOrchestrator]):
        self.orchestrator_factory = orchestrator_factory
        self._runs: dict[str, ReportRun] = {}
        self._documents: dict[str, ReportDocument] = {}
        self._pdf_cache: dict[str, bytes] = {}
        self._lock = threading.Lock()
        self._cancellations = ChatRequestRegistry()

    def start(self, request: str) -> ReportRun:
        now = _timestamp()
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]
        run = ReportRun(run_id=run_id, request=request, status="running", started_at=now)
        with self._lock:
            self._runs[run_id] = run

        try:
            orchestrator = self.orchestrator_factory()
        except Exception as exc:  # noqa: BLE001 - reported on the run record, never raised here
            with self._lock:
                run = self._runs[run_id]
                run.status = "failed"
                run.error = str(exc)
                run.completed_at = _timestamp()
            return self.get(run_id)

        cancellation = self._cancellations.register(run_id)
        threading.Thread(
            target=self._execute,
            args=(run_id, orchestrator, request, cancellation),
            daemon=True,
            name=f"cerebro-report-{run_id}",
        ).start()
        return self.get(run_id)

    def cancel(self, run_id: str) -> None:
        self._cancellations.cancel(run_id)

    def get(self, run_id: str) -> ReportRun:
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                raise ReportRunNotFound(run_id)
            return run.model_copy(deep=True)

    def events_after(self, run_id: str, sequence: int) -> tuple[list, bool]:
        run = self.get(run_id)
        events = [event for event in run.events if event.sequence > sequence]
        return events, run.status in {"completed", "partial", "failed", "cancelled"}

    def document(self, run_id: str) -> ReportDocument:
        with self._lock:
            doc = self._documents.get(run_id)
            if doc is None:
                if run_id not in self._runs:
                    raise ReportRunNotFound(run_id)
                raise ReportNotReady(run_id)
            return doc.model_copy(deep=True)

    def pdf(self, run_id: str) -> bytes:
        with self._lock:
            pdf_bytes = self._pdf_cache.get(run_id)
            if pdf_bytes is None:
                if run_id not in self._runs:
                    raise ReportRunNotFound(run_id)
                raise ReportNotReady(run_id)
            return pdf_bytes

    def _emit(self, run_id: str, stage: str, status: str, summary: str, details: dict) -> None:
        from .models import ReportEvent

        with self._lock:
            run = self._runs[run_id]
            event = ReportEvent(sequence=len(run.events) + 1, stage=stage, status=status, summary=summary, details=details)
            run.events.append(event)
            run.current_stage = stage if status == "started" else run.current_stage

    def _execute(self, run_id: str, orchestrator: ReportOrchestrator, request: str, cancellation: ChatCancellation) -> None:
        from .report_pdf import render_report_pdf

        def on_event(stage: str, status: str, summary: str, details: dict) -> None:
            self._emit(run_id, stage, status, summary, details)

        try:
            document = orchestrator.run(request, run_id, on_event=on_event, cancellation=cancellation)
        except ChatCancelled:
            logger.info("report.run.cancelled run_id=%s", run_id)
            with self._lock:
                run = self._runs[run_id]
                run.status = "cancelled"
                run.current_stage = None
                run.completed_at = _timestamp()
            self._cancellations.finish(run_id, cancellation)
            return
        except Exception as exc:  # noqa: BLE001 - surfaced on the run record, not raised in a background thread
            logger.exception("report.run.failed run_id=%s", run_id)
            with self._lock:
                run = self._runs[run_id]
                run.status = "failed"
                run.error = str(exc)
                run.completed_at = _timestamp()
            self._cancellations.finish(run_id, cancellation)
            return

        with self._lock:
            self._documents[run_id] = document
            if document.status != "failed":
                self._pdf_cache[run_id] = render_report_pdf(document)
            run = self._runs[run_id]
            run.status = document.status
            run.current_stage = None
            run.completed_at = _timestamp()
        self._cancellations.finish(run_id, cancellation)
