"""Plan -> execute -> synthesize orchestration for the executive report agent.

See specs/023-executive-report-agent.md. Every SQL-backed answer is produced by
the unmodified spec 008/012 ChatOrchestrator.chat(); this module only decides
how many questions to ask (FR-2 to FR-4), runs them in order (FR-5 to FR-7),
and writes a short overview once they are done (FR-8, FR-9).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import datetime, timezone

from .chat import ChatCancellation, ChatOrchestrator
from .models import (
    ChatRequest,
    ReportDocument,
    ReportOverview,
    ReportPlan,
    ReportPlanSection,
    ReportSectionResult,
)

logger = logging.getLogger(__name__)

MIN_SECTIONS = 1
MAX_SECTIONS = 7

_GROUNDING_TYPES = {"Table", "Entity", "Metric", "Dimension"}

_PLAN_PROMPT = (
    "Bạn là agent lập kế hoạch báo cáo ngân hàng. Cho một yêu cầu của người dùng, hãy chia nó "
    "thành một danh sách các câu hỏi con, mỗi câu hỏi đủ nghĩa để trả lời độc lập bằng một câu "
    "SQL duy nhất trên dữ liệu bên dưới. Chỉ dùng từ vựng (bảng, thực thể, metric, dimension) đã "
    "được liệt kê. Tối đa {max_sections} câu hỏi con. Viết title và question bằng tiếng Việt.\n\n"
    "Yêu cầu của người dùng: {request}\n\n"
    "Từ vựng khả dụng:\n{catalog}"
)

_SYNTHESIZE_PROMPT = (
    "Bạn vừa nhận được kết quả của từng phần trong một báo cáo ngân hàng. Viết một đoạn tóm tắt "
    "tổng quan ngắn gọn (tối đa 800 ký tự) bằng tiếng Việt, đặt ở đầu báo cáo. Chỉ nhắc lại các "
    "con số/nhận định đã có trong câu trả lời của từng phần bên dưới; không tự tính hay bịa ra số "
    "liệu mới. Có thể nêu tên phần (title) khi tham chiếu tới nó.\n\n"
    "Các phần đã hoàn thành:\n{sections}"
)

EventCallback = Callable[[str, str, str, dict], None]


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _overall_status(sections: list[ReportSectionResult]) -> str:
    answered = [section for section in sections if section.status == "answered"]
    if not answered:
        return "failed"
    if len(answered) == len(sections):
        return "completed"
    return "partial"


def _grounding_catalog(chat: ChatOrchestrator) -> str:
    lines = [
        f"- {obj.id} ({obj.type}): {obj.title or obj.name} — {obj.description}"
        for obj in chat.bundle.objects
        if obj.type in _GROUNDING_TYPES
    ]
    return "\n".join(lines)


class ReportOrchestrator:
    """Runs the plan -> execute -> synthesize pipeline for one report request."""

    def __init__(self, chat: ChatOrchestrator):
        self.chat = chat

    def _generate(self, schema_name: str, prompt: str, output_model: type):
        provider = self.chat.provider
        if provider is None:
            raise RuntimeError("No generation provider is configured")
        return provider.generate(schema_name, prompt, output_model)

    def _plan(self, request: str) -> ReportPlan:
        catalog = _grounding_catalog(self.chat)
        prompt = _PLAN_PROMPT.format(max_sections=MAX_SECTIONS, request=request, catalog=catalog)
        raw_plan: ReportPlan = self._generate("report_plan", prompt, ReportPlan)
        sections = list(raw_plan.sections)
        if not sections:
            sections = [ReportPlanSection(title=request[:80], question=request)]
        elif len(sections) > MAX_SECTIONS:
            sections = sections[:MAX_SECTIONS]
        title = raw_plan.title or request[:80]
        return ReportPlan(title=title, sections=sections)

    def _synthesize(self, sections: list[ReportSectionResult]) -> str:
        payload = [
            {"title": section.title, "question": section.question, "status": section.status, "answer": section.answer}
            for section in sections
        ]
        prompt = _SYNTHESIZE_PROMPT.format(sections=json.dumps(payload, ensure_ascii=False))
        overview: ReportOverview = self._generate("report_overview", prompt, ReportOverview)
        return overview.summary

    def run(
        self,
        request: str,
        run_id: str,
        on_event: EventCallback | None = None,
        cancellation: ChatCancellation | None = None,
    ) -> ReportDocument:
        def emit(stage: str, status: str, summary: str, details: dict | None = None) -> None:
            if on_event is not None:
                on_event(stage, status, summary, details or {})

        def checkpoint() -> None:
            if cancellation is not None:
                cancellation.checkpoint()

        checkpoint()
        emit("planning", "started", "Đang lên kế hoạch báo cáo...")
        plan = self._plan(request)
        checkpoint()
        emit(
            "planning",
            "completed",
            f"Đã lên kế hoạch {len(plan.sections)} phần: " + ", ".join(section.title for section in plan.sections),
        )

        sections: list[ReportSectionResult] = []
        for index, spec in enumerate(plan.sections, start=1):
            checkpoint()
            section_id = f"s{index}"
            emit(section_id, "started", spec.question)
            response = self.chat.chat(ChatRequest(message=spec.question), cancellation)
            checkpoint()
            section = ReportSectionResult(
                id=section_id,
                title=spec.title,
                question=spec.question,
                status=response.status,
                answer=response.answer,
                sql=response.sql,
                columns=response.columns,
                rows=response.rows,
                row_count=response.row_count,
                truncated=response.truncated,
                evidence_ids=response.evidence_ids,
                warnings=response.warnings,
            )
            sections.append(section)
            emit(
                section_id,
                "completed" if section.status == "answered" else section.status,
                section.answer,
                {"status": section.status, "row_count": section.row_count},
            )

        status = _overall_status(sections)
        overview = ""
        if status != "failed":
            checkpoint()
            emit("synthesizing", "started", "Đang tổng hợp báo cáo...")
            overview = self._synthesize(sections)
            checkpoint()
            emit("synthesizing", "completed", "Đã tổng hợp xong.")

        return ReportDocument(
            run_id=run_id,
            request=request,
            title=plan.title,
            overview=overview,
            generated_at=_timestamp(),
            semantic_version=self.chat.bundle.version,
            status=status,  # type: ignore[arg-type]
            sections=sections,
        )
