from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path

import pytest

from cerebro.api import create_app
from cerebro.bundle import load_validated_bundle
from cerebro.chat import ChatOrchestrator
from cerebro.enrichment import GenerationProvider
from cerebro.models import (
    AnswerPayload,
    ChatRequest,
    ChatResponse,
    QueryPlanAndSQL,
    ReportDocument,
    ReportOverview,
    ReportPlan,
    ReportPlanSection,
    ReportRunRequest,
    ReportSectionResult,
    SQLProposal,
)
from cerebro.paths import DEFAULT_BUNDLE
from cerebro.report_agent import MAX_SECTIONS, ReportOrchestrator
from cerebro.report_pdf import render_report_html, render_report_pdf
from cerebro.report_runs import ReportNotReady, ReportRunManager, ReportRunNotFound
from cerebro.retrieval import SemanticRetriever
from cerebro.settings import Settings


def _settings(path: Path) -> Settings:
    return Settings(
        database_path=path,
        database_schema="main",
        llm_base_url="http://example.test/v1",
        llm_api_key="secret",
        llm_model="fixture",
        llm_response_mode="json_schema",
        embedding_model=None,
    )


class ReportChatProvider(GenerationProvider):
    """Fixture provider covering planning, section execution, and synthesis."""

    name = "mock"
    model = "report-fixture"

    def __init__(
        self,
        plan: ReportPlan,
        overview: str = "Tổng quan mẫu dựa trên các phần đã chạy.",
        unsafe_sql: bool = False,
        clarify_marker: str | None = None,
    ):
        self.plan = plan
        self.overview = overview
        self.unsafe_sql = unsafe_sql
        self.clarify_marker = clarify_marker
        self.calls: list[tuple[str, str]] = []

    def generate(self, schema_name, prompt, output_model):
        self.calls.append((schema_name, output_model.__name__))
        if output_model is ReportPlan:
            return self.plan
        if output_model is ReportOverview:
            return ReportOverview(summary=self.overview)
        if output_model is QueryPlanAndSQL:
            if self.clarify_marker and self.clarify_marker in prompt:
                return QueryPlanAndSQL(
                    intent="unclear", requires_query=False, clarification="Bạn muốn xem theo tiêu chí nào?"
                )
            if self.unsafe_sql:
                return QueryPlanAndSQL(
                    intent="Count customers by gender",
                    tables=["customers"],
                    group_by=["gender"],
                    sql="SELECT name FROM customers",
                    explanation="unsafe on purpose",
                )
            return QueryPlanAndSQL(
                intent="Count customers by gender",
                tables=["customers"],
                group_by=["gender"],
                sql="SELECT gender, COUNT(*) AS customer_count FROM customers GROUP BY gender",
                explanation="Safe aggregate",
            )
        if output_model is SQLProposal:
            if self.unsafe_sql:
                return SQLProposal(sql="SELECT name FROM customers", explanation="unsafe on purpose")
            return SQLProposal(
                sql="SELECT gender, COUNT(*) AS customer_count FROM customers GROUP BY gender",
                explanation="Safe aggregate",
            )
        if output_model is AnswerPayload:
            return AnswerPayload(answer="Có hai khách hàng nữ và một khách hàng nam.")
        raise AssertionError(schema_name)


def _chat_orchestrator(bank_database: Path, provider: ReportChatProvider) -> ChatOrchestrator:
    bundle = load_validated_bundle(DEFAULT_BUNDLE)
    return ChatOrchestrator(bundle, SemanticRetriever(bundle), bank_database, _settings(bank_database), provider)


# ---------------------------------------------------------------------------
# T-1: planner section-count normalization (FR-3, AC-3)
# ---------------------------------------------------------------------------


def test_plan_falls_back_to_one_section_when_model_returns_none(bank_database: Path):
    provider = ReportChatProvider(plan=ReportPlan(title="Báo cáo trống", sections=[]))
    document = ReportOrchestrator(_chat_orchestrator(bank_database, provider)).run(
        "Đếm khách hàng theo giới tính", "run-empty"
    )
    assert len(document.sections) == 1
    assert document.sections[0].question == "Đếm khách hàng theo giới tính"


def test_plan_truncates_to_max_sections(bank_database: Path):
    oversized = [ReportPlanSection(title=f"Phần {i}", question=f"Câu hỏi {i}") for i in range(12)]
    provider = ReportChatProvider(plan=ReportPlan(title="Báo cáo lớn", sections=oversized))
    document = ReportOrchestrator(_chat_orchestrator(bank_database, provider)).run(
        "Tạo báo cáo tổng quan điều hành", "run-oversized"
    )
    assert len(document.sections) == MAX_SECTIONS == 7
    assert [s.title for s in document.sections] == [f"Phần {i}" for i in range(7)]


# ---------------------------------------------------------------------------
# T-2: sequential execution, plan order, independent requests (FR-5, AC-2)
# ---------------------------------------------------------------------------


def test_orchestrator_runs_sections_in_order_without_shared_history(bank_database: Path, monkeypatch: pytest.MonkeyPatch):
    plan = ReportPlan(
        title="Báo cáo khách hàng",
        sections=[
            ReportPlanSection(title="Khách hàng", question="Đếm khách hàng theo giới tính"),
            ReportPlanSection(title="Số dư", question="Số dư trung bình theo chi nhánh"),
        ],
    )
    provider = ReportChatProvider(plan=plan)
    chat = _chat_orchestrator(bank_database, provider)

    seen_requests: list[ChatRequest] = []
    original_chat = ChatOrchestrator.chat

    def spy(self, request, cancellation=None):
        seen_requests.append(request)
        return original_chat(self, request, cancellation)

    monkeypatch.setattr(ChatOrchestrator, "chat", spy)

    emitted = []
    document = ReportOrchestrator(chat).run(
        "Phân tích khách hàng và số dư", "run-order",
        on_event=lambda *event: emitted.append(event),
    )
    assert emitted[1][0:2] == ("planning", "completed")
    assert emitted[1][3]["sections"] == [
        {"id": f"s{index}", **section.model_dump()}
        for index, section in enumerate(plan.sections, start=1)
    ]

    assert [section.question for section in document.sections] == [s.question for s in plan.sections]
    assert [request.message for request in seen_requests] == [s.question for s in plan.sections]
    assert all(request.conversation_id is None and not request.history for request in seen_requests)
    assert document.title == "Báo cáo khách hàng"
    assert document.status == "completed"
    assert document.overview == provider.overview
    assert document.sections[0].sql and document.sections[0].sql.startswith("SELECT")


# ---------------------------------------------------------------------------
# T-3: one clarification section degrades itself; run still partial + synthesized (FR-6, FR-7, AC-4)
# ---------------------------------------------------------------------------


def test_orchestrator_partial_status_when_one_section_needs_clarification(bank_database: Path):
    plan = ReportPlan(
        title="Báo cáo hỗn hợp",
        sections=[
            ReportPlanSection(title="Rõ ràng", question="Đếm khách hàng theo giới tính"),
            ReportPlanSection(title="Mơ hồ", question="MARKER: khách hàng tốt nhất là ai"),
        ],
    )
    provider = ReportChatProvider(plan=plan, clarify_marker="MARKER")
    document = ReportOrchestrator(_chat_orchestrator(bank_database, provider)).run("...", "run-partial")

    assert document.status == "partial"
    assert document.sections[0].status == "answered"
    assert document.sections[1].status == "clarification"
    assert document.sections[1].sql is None
    assert document.overview
    assert ("report_overview", "ReportOverview") in provider.calls


# ---------------------------------------------------------------------------
# T-4: every section blocked -> run failed, synthesize skipped (FR-7, FR-8, AC-5)
# ---------------------------------------------------------------------------


def test_orchestrator_all_blocked_yields_failed_and_skips_synthesis(bank_database: Path):
    plan = ReportPlan(title="Báo cáo lỗi", sections=[ReportPlanSection(title="A", question="Đếm khách hàng theo giới tính")])
    provider = ReportChatProvider(plan=plan, unsafe_sql=True)
    document = ReportOrchestrator(_chat_orchestrator(bank_database, provider)).run("...", "run-failed")

    assert document.status == "failed"
    assert document.sections[0].status == "blocked"
    assert document.overview == ""
    assert not any(model_name == "ReportOverview" for _, model_name in provider.calls)


# ---------------------------------------------------------------------------
# PDF rendering (FR-14 to FR-16, AC-6)
# ---------------------------------------------------------------------------


def _sample_document(**overrides) -> ReportDocument:
    base: dict = dict(
        run_id="run-x",
        request="Đếm khách hàng theo giới tính",
        title="Báo cáo mẫu",
        overview="Đây là đoạn tổng quan mẫu.",
        generated_at="2026-09-11T00:00:00+00:00",
        semantic_version="0.2.0",
        status="completed",
        sections=[
            ReportSectionResult(
                id="s1",
                title="Khách hàng",
                question="Đếm khách hàng theo giới tính",
                status="answered",
                answer="Có 2 khách hàng nữ và 1 khách hàng nam.",
                sql="SELECT gender, COUNT(*) AS customer_count FROM customers GROUP BY gender",
                columns=["gender", "customer_count"],
                rows=[["Female", 2], ["Male", 1]],
                row_count=2,
            )
        ],
    )
    base.update(overrides)
    return ReportDocument(**base)


def test_render_report_html_contains_overview_titles_and_rows():
    html = render_report_html(_sample_document())
    assert "Báo cáo mẫu" in html
    assert "Đây là đoạn tổng quan mẫu." in html
    assert "Khách hàng" in html
    assert "Female" in html and "Male" in html
    assert "SELECT gender" in html


def test_render_report_html_zero_row_and_truncated_notes():
    zero_row_section = ReportSectionResult(
        id="s1", title="Rỗng", question="Q", status="answered", answer="Không có kết quả.", row_count=0
    )
    html = render_report_html(_sample_document(sections=[zero_row_section]))
    assert "Không có dữ liệu phù hợp" in html

    truncated_section = ReportSectionResult(
        id="s1",
        title="Cắt bớt",
        question="Q",
        status="answered",
        answer="Kết quả lớn.",
        columns=["x"],
        rows=[[i] for i in range(30)],
        row_count=30,
        truncated=True,
    )
    html = render_report_html(_sample_document(sections=[truncated_section]))
    assert "5 dòng không hiển thị" in html
    assert "cắt bớt" in html


def test_render_report_pdf_returns_pdf_bytes():
    pdf_bytes = render_report_pdf(_sample_document())
    assert pdf_bytes.startswith(b"%PDF")


def test_render_report_html_shows_toc_and_status_badges_for_three_or_more_sections():
    sections = [
        ReportSectionResult(id="s1", title="Khách hàng", question="Q1", status="answered", answer="A1", row_count=1),
        ReportSectionResult(id="s2", title="Chi nhánh", question="Q2", status="clarification", answer="A2"),
        ReportSectionResult(id="s3", title="Thẻ", question="Q3", status="blocked", answer="A3"),
    ]
    html = render_report_html(_sample_document(sections=sections, status="partial"))

    # Native PDF outline anchors (FR-26): each section is a link target and a TOC entry.
    assert 'id="s1"' in html and 'id="s2"' in html and 'id="s3"' in html
    assert 'href="#s1"' in html and 'href="#s2"' in html and 'href="#s3"' in html

    # Every section's status label survives as text, independent of badge color (AC-16).
    assert "Đã trả lời" in html
    assert "Cần làm rõ thêm" in html
    assert "Không thể trả lời" in html

    pdf_bytes = render_report_pdf(_sample_document(sections=sections, status="partial"))
    assert pdf_bytes.startswith(b"%PDF")


def test_render_report_html_omits_toc_under_three_sections():
    html = render_report_html(_sample_document())
    assert "Mục lục" not in html


# ---------------------------------------------------------------------------
# ReportRunManager: event sequencing and concurrency (FR-11 to FR-13, AC-8)
# ---------------------------------------------------------------------------


class _StubOrchestrator:
    def __init__(self, status: str = "completed", delay: threading.Event | None = None):
        self.status = status
        self.delay = delay

    def run(self, request, run_id, on_event=None, cancellation=None):
        if cancellation is not None:
            cancellation.checkpoint()
        if self.delay is not None:
            self.delay.wait(timeout=2)
        if cancellation is not None:
            cancellation.checkpoint()
        if on_event is not None:
            on_event("planning", "started", "Đang lên kế hoạch...", {})
            on_event("planning", "completed", "Đã lên kế hoạch 1 phần", {})
            on_event("s1", "started", "Câu hỏi", {})
            on_event("s1", "completed", "Trả lời", {"status": "answered"})
            if self.status != "failed":
                on_event("synthesizing", "started", "Đang tổng hợp...", {})
                on_event("synthesizing", "completed", "Xong", {})
        return ReportDocument(
            run_id=run_id,
            request=request,
            title="Báo cáo giả",
            overview="Tổng quan giả." if self.status != "failed" else "",
            generated_at="2026-09-11T00:00:00+00:00",
            semantic_version="0.2.0",
            status=self.status,
            sections=[
                ReportSectionResult(
                    id="s1",
                    title="Phần 1",
                    question="Câu hỏi",
                    status="answered" if self.status != "failed" else "blocked",
                    answer="Trả lời",
                )
            ],
        )


def _wait_until_terminal(manager: ReportRunManager, run_id: str, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if manager.get(run_id).status != "running":
            return
        time.sleep(0.01)
    raise AssertionError("run did not reach a terminal state in time")


def test_report_run_manager_emits_ordered_events_and_terminal_complete():
    manager = ReportRunManager(orchestrator_factory=lambda: _StubOrchestrator())
    run = manager.start("Tạo báo cáo tổng quan điều hành")
    _wait_until_terminal(manager, run.run_id)

    events, terminal = manager.events_after(run.run_id, 0)
    assert terminal is True
    stages = [event.stage for event in events]
    assert stages == ["planning", "planning", "s1", "s1", "synthesizing", "synthesizing"]
    assert [event.sequence for event in events] == list(range(1, len(events) + 1))

    final_run = manager.get(run.run_id)
    assert final_run.status == "completed"
    document = manager.document(run.run_id)
    assert document.overview == "Tổng quan giả."
    pdf_bytes = manager.pdf(run.run_id)
    assert pdf_bytes.startswith(b"%PDF")


def test_report_run_manager_records_a_clarification_section_without_a_validation_error(bank_database: Path):
    # Regression: ReportEvent.status previously omitted "clarification", so any real
    # clarification section raised a pydantic ValidationError inside ReportRunManager._emit
    # the moment it tried to construct that event -- a path test_orchestrator_partial_status_*
    # never exercised because it calls ReportOrchestrator.run() directly, bypassing the manager.
    plan = ReportPlan(
        title="Báo cáo hỗn hợp",
        sections=[
            ReportPlanSection(title="Rõ ràng", question="Đếm khách hàng theo giới tính"),
            ReportPlanSection(title="Mơ hồ", question="MARKER: khách hàng tốt nhất là ai"),
        ],
    )
    provider = ReportChatProvider(plan=plan, clarify_marker="MARKER")
    manager = ReportRunManager(
        orchestrator_factory=lambda: ReportOrchestrator(_chat_orchestrator(bank_database, provider))
    )

    run = manager.start("Phân tích khách hàng")
    _wait_until_terminal(manager, run.run_id)

    final_run = manager.get(run.run_id)
    assert final_run.status == "partial"
    assert final_run.error is None
    events, terminal = manager.events_after(run.run_id, 0)
    assert terminal is True
    s2_events = [event for event in events if event.stage == "s2"]
    assert [event.status for event in s2_events] == ["started", "clarification"]


def test_report_run_manager_allows_concurrent_runs():
    manager = ReportRunManager(orchestrator_factory=lambda: _StubOrchestrator())
    run_a = manager.start("Yêu cầu A")
    run_b = manager.start("Yêu cầu B")
    assert run_a.run_id != run_b.run_id
    _wait_until_terminal(manager, run_a.run_id)
    _wait_until_terminal(manager, run_b.run_id)
    assert manager.get(run_a.run_id).request == "Yêu cầu A"
    assert manager.get(run_b.run_id).request == "Yêu cầu B"


def test_report_run_manager_cancels_an_in_flight_run():
    delay = threading.Event()
    manager = ReportRunManager(orchestrator_factory=lambda: _StubOrchestrator(delay=delay))
    run = manager.start("Tạo báo cáo tổng quan điều hành")
    assert run.status == "running"

    manager.cancel(run.run_id)
    delay.set()  # unblock the orchestrator so it reaches its post-wait cancellation checkpoint
    _wait_until_terminal(manager, run.run_id)

    final_run = manager.get(run.run_id)
    assert final_run.status == "cancelled"
    events, terminal = manager.events_after(run.run_id, 0)
    assert terminal is True
    assert events == []  # cancelled before any progress event was emitted
    with pytest.raises(ReportNotReady):
        manager.document(run.run_id)
    with pytest.raises(ReportNotReady):
        manager.pdf(run.run_id)


def test_report_run_manager_fails_fast_when_orchestrator_factory_raises():
    def factory():
        raise RuntimeError("Configure CEREBRO_DATABASE_PATH ...")

    manager = ReportRunManager(orchestrator_factory=factory)
    run = manager.start("Tạo báo cáo tổng quan điều hành")
    assert run.status == "failed"
    assert run.completed_at is not None
    assert "CEREBRO_DATABASE_PATH" in (run.error or "")
    with pytest.raises(ReportNotReady):
        manager.document(run.run_id)


def test_report_pdf_endpoint_returns_409_before_terminal_and_200_after(
    bank_source_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    release = threading.Event()
    started = threading.Event()

    def blocking_chat(self, request, cancellation=None):
        started.set()
        release.wait(timeout=2)
        return ChatResponse(
            conversation_id="run-conversation",
            status="answered",
            answer="Có hai khách hàng nữ và một khách hàng nam.",
            sql="SELECT gender, COUNT(*) AS customer_count FROM customers GROUP BY gender",
            columns=["gender", "customer_count"],
            rows=[["Female", 2], ["Male", 1]],
            row_count=2,
            semantic_version="0.2.0",
        )

    monkeypatch.setattr(ChatOrchestrator, "chat", blocking_chat)
    monkeypatch.setattr(
        ReportOrchestrator,
        "_generate",
        lambda self, schema_name, prompt, output_model: (
            ReportPlan(title="Báo cáo", sections=[ReportPlanSection(title="A", question="Đếm khách hàng theo giới tính")])
            if output_model is ReportPlan
            else ReportOverview(summary="Tổng quan.")
        ),
    )
    app = create_app(source_config=bank_source_config, generation_output_root=tmp_path / "generated")
    endpoints = {route.path: route.endpoint for route in app.routes if hasattr(route, "endpoint")}

    async def exercise() -> None:
        run = await endpoints["/api/reports/runs"](ReportRunRequest(request="Tạo báo cáo tổng quan điều hành"))
        run_id = run["run_id"]
        for _ in range(200):
            if started.is_set():
                break
            await asyncio.sleep(0.01)
        assert started.is_set()

        from fastapi import HTTPException

        with pytest.raises(HTTPException) as not_ready:
            await endpoints["/api/reports/runs/{run_id}/pdf"](run_id)
        assert not_ready.value.status_code == 409

        release.set()
        for _ in range(500):
            polled = await endpoints["/api/reports/runs/{run_id}"](run_id)
            if polled["status"] != "running":
                break
            await asyncio.sleep(0.01)
        assert polled["status"] == "completed"

        response = await endpoints["/api/reports/runs/{run_id}/pdf"](run_id)
        assert response.media_type == "application/pdf"
        assert response.body.startswith(b"%PDF")
        assert f'filename="report-{run_id}.pdf"' in response.headers["content-disposition"]

    asyncio.run(exercise())


def test_report_run_endpoint_when_database_unavailable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    missing_config = tmp_path / "missing-source.yaml"
    missing_config.write_text("name: missing\ndatabase_path: does-not-exist.duckdb\n", encoding="utf-8")
    monkeypatch.setenv("CEREBRO_DATABASE_PATH", str(tmp_path / "does-not-exist.duckdb"))
    app = create_app(source_config=missing_config, generation_output_root=tmp_path / "generated")
    endpoints = {route.path: route.endpoint for route in app.routes if hasattr(route, "endpoint")}

    async def exercise() -> None:
        run = await endpoints["/api/reports/runs"](ReportRunRequest(request="Tạo báo cáo tổng quan điều hành"))
        assert run["status"] == "failed"
        assert run["error"]

        from fastapi import HTTPException

        with pytest.raises(HTTPException) as not_ready:
            await endpoints["/api/reports/runs/{run_id}/pdf"](run["run_id"])
        assert not_ready.value.status_code == 409

    asyncio.run(exercise())
