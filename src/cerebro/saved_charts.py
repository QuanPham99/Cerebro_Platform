from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError

from .models import SavedChart, SaveChartRequest
from .paths import ROOT


class SavedChartNotFound(LookupError):
    pass


class SavedChartStore:
    """Persists frozen chat-result snapshots as one JSON file per saved chart."""

    def __init__(self, root: Path | str = ROOT / "knowledge" / "saved_charts") -> None:
        self.root = Path(root).resolve()

    def _path(self, identifier: str) -> Path:
        if not identifier or Path(identifier).name != identifier or identifier in {".", ".."}:
            raise SavedChartNotFound(identifier)
        path = (self.root / f"{identifier}.json").resolve()
        if path.parent != self.root:
            raise SavedChartNotFound(identifier)
        return path

    def list(self) -> list[SavedChart]:
        if not self.root.is_dir():
            return []
        charts: list[SavedChart] = []
        for path in self.root.glob("*.json"):
            try:
                charts.append(SavedChart.model_validate_json(path.read_text(encoding="utf-8")))
            except (OSError, ValueError, ValidationError):
                continue
        charts.sort(key=lambda chart: chart.created_at, reverse=True)
        return charts

    def create(self, request: SaveChartRequest) -> SavedChart:
        self.root.mkdir(parents=True, exist_ok=True)
        chart = SavedChart(
            id=uuid4().hex,
            question=request.question,
            sql=request.sql,
            columns=request.columns,
            rows=request.rows,
            row_count=request.row_count,
            truncated=request.truncated,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        self._path(chart.id).write_text(chart.model_dump_json(), encoding="utf-8")
        return chart

    def delete(self, identifier: str) -> None:
        path = self._path(identifier)
        if not path.is_file():
            raise SavedChartNotFound(identifier)
        path.unlink()
