"""Live-validate the Vietnamese preset questions against a running Cerebro chat server.

Reads the single source of truth (apps/web/src/presetQuestions.json), fires each
question at a running server's /api/chat in order, and appends one JSON line to
the output file *immediately* after every response -- never buffers until the
whole run finishes, so the file can be tailed/reviewed while the run is still
in progress.

Usage:
    python3 scripts/validate_preset_questions.py
    python3 scripts/validate_preset_questions.py --base-url http://127.0.0.1:8000
    python3 scripts/validate_preset_questions.py --level Khó --only "Xếp hạng"
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
PRESET_QUESTIONS_PATH = REPO_ROOT / "apps" / "web" / "src" / "presetQuestions.json"
OUTPUT_DIR = REPO_ROOT / "evaluation" / "preset-validation-runs"


def _slugify(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")
    return slug or "level"


def load_cases(level_filter: str | None, only_substring: str | None) -> list[dict]:
    levels = json.loads(PRESET_QUESTIONS_PATH.read_text(encoding="utf-8"))
    cases = []
    for level in levels:
        if level_filter and level["level"] != level_filter:
            continue
        level_slug = _slugify(level["level"])
        for index, question in enumerate(level["questions"], start=1):
            if only_substring and only_substring not in question:
                continue
            cases.append(
                {
                    "id": f"{level_slug}-{index:02d}",
                    "level": level["level"],
                    "requires_sql": level["requiresSql"],
                    "question": question,
                }
            )
    return cases


def run_case(client: httpx.Client, case: dict) -> dict:
    started = time.monotonic()
    result = {**case, "timestamp": datetime.now(timezone.utc).isoformat()}
    try:
        response = client.post(
            "/api/chat",
            json={"message": case["question"], "history": []},
        )
        elapsed = time.monotonic() - started
        response.raise_for_status()
        body = response.json()
        result.update(
            {
                "http_status": response.status_code,
                "status": body.get("status"),
                "sql": body.get("sql"),
                "row_count": body.get("row_count"),
                "warnings": body.get("warnings"),
                "answer": body.get("answer"),
                "error": None,
                "elapsed_seconds": round(elapsed, 2),
            }
        )
        sql_ok = (not case["requires_sql"]) or bool(
            body.get("status") == "answered" and body.get("sql")
        )
        result["passed"] = sql_ok
    except httpx.HTTPError as exc:
        elapsed = time.monotonic() - started
        result.update(
            {
                "http_status": getattr(exc.response, "status_code", None) if hasattr(exc, "response") else None,
                "status": None,
                "sql": None,
                "row_count": None,
                "warnings": None,
                "answer": None,
                "error": str(exc),
                "elapsed_seconds": round(elapsed, 2),
                "passed": False,
            }
        )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--level", default=None, help="Only run questions from this tier (e.g. 'Khó')")
    parser.add_argument("--only", default=None, help="Only run questions containing this substring")
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--output", default=None, help="Override the JSONL output path")
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Skip ids that already have a passing result in --output, and append remaining ones",
    )
    args = parser.parse_args()

    cases = load_cases(args.level, args.only)
    if not cases:
        print("No matching preset questions found.", file=sys.stderr)
        return 1

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = Path(args.output) if args.output else OUTPUT_DIR / f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.jsonl"

    if args.resume and output_path.exists():
        already_passed = set()
        for line in output_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            if record.get("passed"):
                already_passed.add(record["id"])
        before = len(cases)
        cases = [case for case in cases if case["id"] not in already_passed]
        print(f"Resuming: skipping {before - len(cases)} already-passed id(s).")

    if not cases:
        print("Nothing left to run.")
        return 0

    print(f"Running {len(cases)} preset question(s) against {args.base_url}")
    print(f"Writing incremental results to {output_path}")

    passed = 0
    failed = 0
    with httpx.Client(base_url=args.base_url, timeout=args.timeout) as client, output_path.open("a", encoding="utf-8") as sink:
        for case in cases:
            result = run_case(client, case)
            sink.write(json.dumps(result, ensure_ascii=False) + "\n")
            sink.flush()

            marker = "PASS" if result["passed"] else "FAIL"
            passed += result["passed"]
            failed += not result["passed"]
            print(
                f"[{marker}] {result['id']} ({result['level']}) "
                f"status={result['status']} elapsed={result['elapsed_seconds']}s "
                f"sql={'yes' if result['sql'] else 'no'} :: {result['question']}"
            )
            if result["error"]:
                print(f"        error: {result['error']}")

    print(f"\n{passed} passed, {failed} failed. Results: {output_path}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
