from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest

from cerebro.api import create_app
from cerebro.models import SaveChartRequest
from cerebro.saved_charts import SavedChartNotFound, SavedChartStore


def request(question: str = "How many customers by gender?") -> SaveChartRequest:
    return SaveChartRequest(
        question=question,
        sql="SELECT gender, COUNT(*) AS total FROM customers GROUP BY gender",
        columns=["gender", "total"],
        rows=[["Female", 2], ["Male", 1]],
        row_count=2,
        truncated=False,
    )


def test_create_list_delete_round_trip(tmp_path: Path):
    store = SavedChartStore(tmp_path / "saved_charts")
    assert store.list() == []

    saved = store.create(request())
    assert saved.id
    assert saved.created_at
    assert store.list() == [saved]

    store.delete(saved.id)
    assert store.list() == []


def test_delete_rejects_unknown_or_traversal_identifiers(tmp_path: Path):
    store = SavedChartStore(tmp_path / "saved_charts")
    saved = store.create(request())

    with pytest.raises(SavedChartNotFound):
        store.delete("not-a-real-id")
    with pytest.raises(SavedChartNotFound):
        store.delete("../escaped")
    with pytest.raises(SavedChartNotFound):
        store.delete(".")

    assert store.list() == [saved]


def test_list_skips_a_corrupted_file(tmp_path: Path):
    root = tmp_path / "saved_charts"
    store = SavedChartStore(root)
    saved = store.create(request())
    (root / "corrupted.json").write_text("not json", encoding="utf-8")

    assert store.list() == [saved]


def test_list_sorts_newest_first(tmp_path: Path):
    store = SavedChartStore(tmp_path / "saved_charts")
    first = store.create(request("First question"))
    second = store.create(request("Second question"))
    if first.created_at == second.created_at:
        pytest.skip("clock resolution too coarse to order by created_at")

    assert [chart.id for chart in store.list()] == [second.id, first.id]


def test_http_create_list_delete(tmp_path: Path):
    app = create_app(saved_charts_root=tmp_path / "saved_charts")

    async def exercise():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            empty = await client.get("/api/saved-charts")
            assert empty.status_code == 200
            assert empty.json() == []

            created = await client.post(
                "/api/saved-charts",
                json={
                    "question": "How many customers by gender?",
                    "sql": "SELECT gender, COUNT(*) AS total FROM customers GROUP BY gender",
                    "columns": ["gender", "total"],
                    "rows": [["Female", 2], ["Male", 1]],
                    "row_count": 2,
                    "truncated": False,
                },
            )
            assert created.status_code == 200
            chart_id = created.json()["id"]

            listed = await client.get("/api/saved-charts")
            assert [item["id"] for item in listed.json()] == [chart_id]

            missing = await client.delete("/api/saved-charts/not-a-real-id")
            assert missing.status_code == 404
            assert missing.json()["detail"]["code"] == "unknown_saved_chart"

            deleted = await client.delete(f"/api/saved-charts/{chart_id}")
            assert deleted.status_code == 200
            assert deleted.json() == {"deleted": chart_id}

            remaining = await client.get("/api/saved-charts")
            assert remaining.json() == []

    asyncio.run(exercise())
