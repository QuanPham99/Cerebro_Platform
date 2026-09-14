"""Pure HTML->PDF rendering for a finished ReportDocument. No model, no DB, no
network — see specs/023-executive-report-agent.md FR-14 to FR-16."""

from __future__ import annotations

from jinja2 import Environment, select_autoescape
from weasyprint import HTML

from .models import ReportDocument

ROW_DISPLAY_CAP = 25
TOC_MIN_SECTIONS = 3

_STATUS_LABELS = {
    "completed": "Hoàn thành",
    "partial": "Hoàn thành một phần",
    "failed": "Thất bại",
}

_SECTION_STATUS_LABELS = {
    "answered": "Đã trả lời",
    "clarification": "Cần làm rõ thêm",
    "blocked": "Không thể trả lời",
}

_SECTION_STATUS_BADGE_CLASS = {
    "answered": "badge-answered",
    "clarification": "badge-clarification",
    "blocked": "badge-blocked",
}

_TEMPLATE = """
<!doctype html>
<html lang="vi">
<head>
<meta charset="utf-8">
<style>
  @page {
    size: A4; margin: 2cm 1.6cm;
    @bottom-right { content: "Trang " counter(page) " / " counter(pages); font-size: 8pt; color: #888; }
  }
  body { font-family: "Helvetica Neue", Arial, sans-serif; color: #1a1a1a; font-size: 11pt; }
  h1 { font-size: 20pt; margin-bottom: 0.1em; bookmark-level: 1; bookmark-label: content(); }
  .meta { color: #555; font-size: 9pt; margin-bottom: 0.5em; }
  .status-line { margin-bottom: 1.2em; }
  .overview { background: #f4f6f8; border-left: 4px solid #2f6fed; padding: 0.8em 1em; margin-bottom: 1.6em; }
  .toc { margin-bottom: 1.6em; padding: 0.7em 1em; border: 1px solid #ddd; border-radius: 4px; }
  .toc strong { display: block; margin-bottom: 0.4em; font-size: 10pt; }
  .toc ol { margin: 0; padding-left: 1.2em; }
  .toc a { color: #2f6fed; text-decoration: none; }
  .section { margin-bottom: 1.6em; page-break-inside: avoid; }
  .section h2 { font-size: 13pt; border-bottom: 1px solid #ddd; padding-bottom: 0.2em; bookmark-level: 2; bookmark-label: content(); }
  .section-status { display: flex; align-items: center; gap: 0.5em; font-size: 9pt; color: #888; margin-bottom: 0.4em; }
  .badge { display: inline-block; padding: 0.15em 0.6em; border-radius: 999px; font-size: 8pt; font-weight: 600; }
  .badge-answered, .badge-completed { background: #e3f6ec; color: #1f9d6c; }
  .badge-clarification, .badge-partial { background: #fdf1df; color: #b5790a; }
  .badge-blocked, .badge-failed { background: #fdecef; color: #c23c57; }
  .answer { margin: 0.4em 0; }
  table { width: 100%; border-collapse: collapse; font-size: 9pt; margin: 0.6em 0; }
  th, td { border: 1px solid #ddd; padding: 4px 6px; text-align: left; }
  th { background: #f0f0f0; }
  .note { font-size: 9pt; color: #777; font-style: italic; }
  .sql { background: #f7f7f9; border: 1px solid #eee; padding: 0.5em 0.7em; font-family: "Courier New", monospace; font-size: 8.5pt; white-space: pre-wrap; margin-top: 0.4em; }
  .warnings { font-size: 9pt; color: #a15c00; margin-top: 0.4em; }
</style>
</head>
<body>
  <h1>{{ document.title }}</h1>
  <div class="meta">
    Tạo lúc {{ document.generated_at }} · Phiên bản ngữ nghĩa {{ document.semantic_version }} ·
    {{ document.sections|length }} phần
  </div>
  <div class="status-line"><span class="badge badge-{{ document.status }}">{{ status_label }}</span></div>
  {% if document.overview %}
  <div class="overview">{{ document.overview }}</div>
  {% endif %}
  {% if document.sections|length >= toc_min_sections %}
  <div class="toc">
    <strong>Mục lục</strong>
    <ol>
      {% for section in document.sections %}
      <li><a href="#{{ section.id }}">{{ section.title }}</a></li>
      {% endfor %}
    </ol>
  </div>
  {% endif %}
  {% for section in document.sections %}
  <div class="section" id="{{ section.id }}">
    <h2>{{ loop.index }}. {{ section.title }}</h2>
    <div class="section-status">
      <span class="badge {{ section_status_badge_class(section.status) }}">{{ section_status_label(section.status) }}</span>
      <span>{{ section.question }}</span>
    </div>
    <div class="answer">{{ section.answer }}</div>
    {% if section.status == "answered" %}
      {% if section.row_count == 0 %}
        <div class="note">Không có dữ liệu phù hợp.</div>
      {% else %}
        <table>
          <thead><tr>{% for col in section.columns %}<th>{{ col }}</th>{% endfor %}</tr></thead>
          <tbody>
            {% for row in section.rows[:row_cap] %}
            <tr>{% for cell in row %}<td>{{ cell }}</td>{% endfor %}</tr>
            {% endfor %}
          </tbody>
        </table>
        {% if section.row_count > row_cap %}
        <div class="note">{{ section.row_count - row_cap }} dòng không hiển thị.</div>
        {% endif %}
        {% if section.truncated %}
        <div class="note">Kết quả truy vấn đã bị cắt bớt.</div>
        {% endif %}
      {% endif %}
      {% if section.sql %}<div class="sql">{{ section.sql }}</div>{% endif %}
    {% endif %}
    {% if section.warnings %}
    <div class="warnings">Lưu ý: {{ section.warnings|join("; ") }}</div>
    {% endif %}
  </div>
  {% endfor %}
</body>
</html>
"""

_env = Environment(autoescape=select_autoescape(["html"]))
_env.globals["section_status_label"] = lambda status: _SECTION_STATUS_LABELS.get(status, status)
_env.globals["section_status_badge_class"] = lambda status: _SECTION_STATUS_BADGE_CLASS.get(status, "")
_template = _env.from_string(_TEMPLATE)


def render_report_html(document: ReportDocument) -> str:
    return _template.render(
        document=document,
        status_label=_STATUS_LABELS.get(document.status, document.status),
        row_cap=ROW_DISPLAY_CAP,
        toc_min_sections=TOC_MIN_SECTIONS,
    )


def render_report_pdf(document: ReportDocument) -> bytes:
    return HTML(string=render_report_html(document)).write_pdf()


__all__ = ["render_report_html", "render_report_pdf", "ROW_DISPLAY_CAP"]
