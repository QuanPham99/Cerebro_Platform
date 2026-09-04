from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .bundle import BundleLoader, BundleValidator
from .chat import ChatOrchestrator
from .enrichment import provider_from_environment
from .evaluation import run_evaluation
from .generation import activate_bundle, review_bundle, run_generation_workflow
from .models import ChatRequest
from .paths import DEFAULT_BUNDLE, DEFAULT_CONFIG
from .retrieval import SemanticRetriever
from .settings import Settings, resolve_active_bundle
from .source import DuckDBSource


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cerebro", description="Build and serve explainable OKF semantic grounding")
    commands = parser.add_subparsers(dest="command", required=True)
    scan = commands.add_parser("scan", help="Discover catalog metadata without reading source rows")
    scan.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    scan.add_argument("--database", type=Path)
    scan.add_argument("--output", type=Path)
    generate = commands.add_parser("generate", help="Generate and validate a candidate OKF bundle")
    generate.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    generate.add_argument("--database", type=Path)
    generate.add_argument("--output", type=Path)
    generate.add_argument("--source-mode", choices=("configured", "database-only"), default="configured")
    review = commands.add_parser("review", help="Record a whole-candidate review decision")
    review.add_argument("--bundle", type=Path, required=True)
    review.add_argument("--reviewer", required=True)
    review.add_argument("--decision", choices=("approve", "reject"), default="approve")
    review.add_argument("--comment", default="")
    review.add_argument("--acknowledge-ai-risk", action="store_true")
    review.add_argument("--reviewed-root", type=Path)
    activate = commands.add_parser("activate", help="Activate a validated candidate bundle")
    activate.add_argument("--bundle", type=Path, required=True)
    validate = commands.add_parser("validate", help="Validate upstream OKF and Cerebro contracts")
    validate.add_argument("--bundle", type=Path, default=DEFAULT_BUNDLE)
    evaluate = commands.add_parser("evaluate", help="Run the ten golden retrieval questions")
    evaluate.add_argument("--bundle", type=Path, default=DEFAULT_BUNDLE)
    serve = commands.add_parser("serve", help="Serve HTTP, MCP, and built web UI")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", default=8000, type=int)
    serve.add_argument("--bundle", type=Path)
    serve.add_argument("--database", type=Path)
    doctor = commands.add_parser("doctor", help="Check local configuration without making a model call")
    doctor.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    doctor.add_argument("--database", type=Path)
    ask = commands.add_parser("ask", help="Ask one governed question from the terminal")
    ask.add_argument("question")
    ask.add_argument("--bundle", type=Path)
    ask.add_argument("--database", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "scan":
            snapshot = DuckDBSource(args.config, args.database).scan()
            payload = snapshot.model_dump_json(indent=2)
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(payload + "\n", encoding="utf-8")
            print(json.dumps({"source": snapshot.source_name, "tables": len(snapshot.tables), "columns": snapshot.column_count, "relationships": len(snapshot.relationships), "row_sampling": snapshot.row_sampling}))
        elif args.command == "generate":
            def print_progress(stage: str, status: str, summary: str, _: dict) -> None:
                print(f"[{status:9}] {stage}: {summary}", file=sys.stderr)

            result = run_generation_workflow(
                args.config,
                args.database,
                args.output,
                provider_from_environment(),
                print_progress,
                source_mode=args.source_mode.replace("-", "_"),
            )
            print(json.dumps({
                "output": str(result.output), "version": result.bundle.version, "objects": len(result.bundle.objects),
                "relationships": sum(item.type == "relationship" for item in result.bundle.objects),
                "generation_mode": result.proposal.generation_mode,
                "provider": result.proposal.provider,
                "model": result.proposal.model,
                "source_mode": result.snapshot.source_mode,
                "discovery_evidence": result.snapshot.discovery_evidence,
            }, indent=2))
        elif args.command == "review":
            kwargs = {}
            if args.reviewed_root:
                kwargs["reviewed_root"] = args.reviewed_root
            record = review_bundle(
                args.bundle,
                reviewer=args.reviewer,
                decision=args.decision,
                comment=args.comment,
                acknowledge_ai_risk=args.acknowledge_ai_risk,
                **kwargs,
            )
            print(record.model_dump_json(indent=2))
        elif args.command == "activate":
            print(json.dumps(activate_bundle(args.bundle), indent=2))
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

            if args.bundle:
                os.environ["CEREBRO_BUNDLE_PATH"] = str(args.bundle.resolve())
            if args.database:
                os.environ["CEREBRO_DATABASE_PATH"] = str(args.database.resolve())
            uvicorn.run("cerebro.api:app", host=args.host, port=args.port)
        elif args.command == "doctor":
            settings = Settings.from_environment()
            source = DuckDBSource(args.config, args.database or settings.database_path)
            snapshot = source.scan()
            bundle = BundleLoader().load(resolve_active_bundle())
            status = settings.public_status()
            status.update({
                "database_configured": True,
                "database_reachable": source.database_path.is_file(),
                "tables": len(snapshot.tables),
                "columns": snapshot.column_count,
                "relationships": len(snapshot.relationships),
                "active_bundle": bundle.name,
                "semantic_version": bundle.version,
                "ready_for_chat": settings.llm_configured,
            })
            print(json.dumps(status, indent=2))
            return 0 if source.database_path.is_file() else 1
        elif args.command == "ask":
            settings = Settings.from_environment()
            source = DuckDBSource(DEFAULT_CONFIG, args.database or settings.database_path)
            bundle = BundleLoader().load(resolve_active_bundle(args.bundle))
            retriever = SemanticRetriever(bundle)
            response = ChatOrchestrator(
                bundle, retriever, source.database_path, settings, provider_from_environment()
            ).chat(ChatRequest(message=args.question))
            print(response.model_dump_json(indent=2))
            return 0 if response.status != "blocked" else 1
        return 0
    except Exception as exc:
        print(f"cerebro: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
