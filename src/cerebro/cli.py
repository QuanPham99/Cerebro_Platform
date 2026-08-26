from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .bundle import BundleLoader, BundleValidator
from .enrichment import SemanticEnricher, provider_from_environment
from .evaluation import run_evaluation
from .paths import DEFAULT_BUNDLE, DEFAULT_CONFIG
from .source import DuckDBSource


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cerebro", description="Build and serve explainable OKF semantic grounding")
    commands = parser.add_subparsers(dest="command", required=True)
    scan = commands.add_parser("scan", help="Discover catalog metadata without reading source rows")
    scan.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    scan.add_argument("--output", type=Path)
    generate = commands.add_parser("generate", help="Run two-stage enrichment or use the golden fallback")
    generate.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    validate = commands.add_parser("validate", help="Validate upstream OKF and Cerebro contracts")
    validate.add_argument("--bundle", type=Path, default=DEFAULT_BUNDLE)
    evaluate = commands.add_parser("evaluate", help="Run the ten golden retrieval questions")
    evaluate.add_argument("--bundle", type=Path, default=DEFAULT_BUNDLE)
    serve = commands.add_parser("serve", help="Serve HTTP, MCP, and built web UI")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", default=8000, type=int)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "scan":
            snapshot = DuckDBSource(args.config).scan()
            payload = snapshot.model_dump_json(indent=2)
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(payload + "\n", encoding="utf-8")
            print(json.dumps({"source": snapshot.source_name, "tables": len(snapshot.tables), "columns": snapshot.column_count, "relationships": len(snapshot.relationships), "row_sampling": snapshot.row_sampling}))
        elif args.command == "generate":
            snapshot = DuckDBSource(args.config).scan()
            proposal = SemanticEnricher(provider_from_environment()).enrich(snapshot)
            print(proposal.model_dump_json(indent=2))
        elif args.command == "validate":
            bundle = BundleLoader().load(args.bundle)
            report = BundleValidator().validate(bundle)
            print(report.model_dump_json(indent=2))
            return 0 if report.valid else 1
        elif args.command == "evaluate":
            results = run_evaluation(args.bundle)
            for result in results:
                print(f"{'PASS' if result['passed'] else 'FAIL'} {result['id']}: {result['question']}")
                if result["missing"]:
                    print(f"  missing: {', '.join(result['missing'])}")
            return 0 if all(item["passed"] for item in results) else 1
        elif args.command == "serve":
            import uvicorn

            uvicorn.run("cerebro.api:app", host=args.host, port=args.port)
        return 0
    except Exception as exc:
        print(f"cerebro: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

