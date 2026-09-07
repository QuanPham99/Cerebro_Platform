from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path

from .bundle import BundleLoader, BundleValidator
from .enrichment import SemanticEnricher, provider_from_environment
from .evaluation import (
    DEFAULT_SUPPORTED_QUESTIONS,
    ReferenceQuestionError,
    RuntimeCompositionError,
    build_agent,
    load_authorization_scope,
    load_reference_questions,
    run_evaluation,
    run_offline_reference,
    write_offline_reference,
)
from .paths import DEFAULT_BUNDLE, DEFAULT_CONFIG
from .source import DuckDBSource
from .text2sql_provider import ProviderConfigurationError

DEFAULT_DATABASE = Path("data") / "workshop.duckdb"


class CommandConfigurationError(Exception):
    """A typed operator-input failure. It never degrades to another mode."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _add_runtime_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--bundle", type=Path, default=DEFAULT_BUNDLE)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument(
        "--authorization-scope",
        type=Path,
        required=True,
        help="JSON file declaring the trusted authorization scope",
    )
    parser.add_argument("--max-rows", type=int, default=1000)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cerebro", description="Build and serve explainable OKF semantic grounding"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    scan = commands.add_parser(
        "scan", help="Discover catalog metadata without reading source rows"
    )
    scan.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    scan.add_argument("--output", type=Path)
    generate = commands.add_parser(
        "generate", help="Run two-stage enrichment or use the golden fallback"
    )
    generate.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    validate = commands.add_parser(
        "validate", help="Validate upstream OKF and Cerebro contracts"
    )
    validate.add_argument("--bundle", type=Path, default=DEFAULT_BUNDLE)
    evaluate = commands.add_parser(
        "evaluate", help="Run the ten golden retrieval questions"
    )
    evaluate.add_argument("--bundle", type=Path, default=DEFAULT_BUNDLE)

    ask = commands.add_parser(
        "ask", help="Answer one question with the live organizer model"
    )
    ask.add_argument("--question", required=True)
    _add_runtime_arguments(ask)

    reference = commands.add_parser(
        "reference", help="Run the deterministic offline reference set"
    )
    _add_runtime_arguments(reference)
    reference.add_argument(
        "--questions", type=Path, default=DEFAULT_SUPPORTED_QUESTIONS
    )
    reference.add_argument("--output", type=Path, required=True)
    reference.add_argument(
        "--cassette", type=Path, help="Replay recorded outcomes offline"
    )
    reference.add_argument(
        "--provider-factory",
        help="MODULE:ATTRIBUTE naming a scripted offline provider factory",
    )

    baseline = commands.add_parser(
        "baseline", help="Run the live Option B baseline with the organizer model"
    )
    _add_runtime_arguments(baseline)
    baseline.add_argument("--questions", type=Path, default=DEFAULT_SUPPORTED_QUESTIONS)
    baseline.add_argument("--capability-receipt-dir", type=Path, required=True)

    serve = commands.add_parser("serve", help="Serve HTTP, MCP, and built web UI")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", default=8000, type=int)
    return parser


def _load_provider_factory(reference: str):
    if ":" not in reference:
        raise CommandConfigurationError(
            "invalid_provider_factory",
            "a provider factory is named MODULE:ATTRIBUTE",
        )
    module_name, attribute = reference.split(":", 1)
    try:
        module = importlib.import_module(module_name)
        factory = getattr(module, attribute)
    except (ImportError, AttributeError) as error:
        raise CommandConfigurationError(
            "invalid_provider_factory",
            "the named provider factory cannot be imported",
        ) from error
    return factory


def _offline_provider(args) -> tuple[str, object | None, Path | None]:
    """Choose exactly one offline transport. There is no implicit fallback."""
    chosen = [
        name
        for name, value in (
            ("cassette", args.cassette),
            ("provider-factory", args.provider_factory),
        )
        if value
    ]
    if len(chosen) != 1:
        raise CommandConfigurationError(
            "offline_provider_required",
            "reference mode requires exactly one of --cassette or --provider-factory",
        )
    if args.cassette:
        return "cassette", None, args.cassette
    factory = _load_provider_factory(args.provider_factory)
    return "scripted", factory(load_reference_questions(args.questions)), None


def _run_ask(args) -> int:
    scope = load_authorization_scope(args.authorization_scope)
    runtime = build_agent(
        args.database,
        "organizer",
        scope,
        bundle_path=args.bundle,
    )
    try:
        from .models import SQLGenerationRequest

        response = runtime.agent.run(
            SQLGenerationRequest(
                question=args.question,
                authorization_scope=scope,
                dialect="duckdb",
                max_rows=args.max_rows,
            )
        )
    finally:
        runtime.close()
    print(response.model_dump_json(indent=2))
    return 0 if response.status == "ok" else 1


def _run_reference(args) -> int:
    scope = load_authorization_scope(args.authorization_scope)
    provider_mode, provider, cassette = _offline_provider(args)
    runtime = build_agent(
        args.database,
        provider_mode,
        scope,
        provider,
        bundle_path=args.bundle,
        cassette_path=cassette,
    )
    try:
        run = run_offline_reference(
            runtime, questions_path=args.questions, max_rows=args.max_rows
        )
    finally:
        runtime.close()
    written = write_offline_reference(run.artifact, args.output)
    print(
        json.dumps(
            {
                "run_kind": run.artifact.run_kind,
                "total_questions": run.artifact.total_questions,
                "ok_count": run.artifact.ok_count,
                "output": str(written),
            }
        )
    )
    return 0 if run.artifact.ok_count == run.artifact.total_questions else 1


def _run_baseline(args) -> int:
    """Live organizer only. It never accepts a scripted or golden provider."""
    from .hosted_provider import probe_provider_schema
    from .models import SQLGenerationRequest

    scope = load_authorization_scope(args.authorization_scope)
    runtime = build_agent(
        args.database,
        "organizer",
        scope,
        bundle_path=args.bundle,
    )
    try:
        receipt, receipt_path = probe_provider_schema(
            gateway=runtime.provider._inner,
            receipt_dir=args.capability_receipt_dir,
        )
        outcomes = []
        for case in load_reference_questions(args.questions):
            response = runtime.agent.run(
                SQLGenerationRequest(
                    question=case.question,
                    authorization_scope=scope,
                    dialect="duckdb",
                    max_rows=args.max_rows,
                )
            )
            outcomes.append(
                {
                    "id": case.id,
                    "status": response.status,
                    "generation_route": response.generation_route,
                    "cache_status": response.cache_status,
                    "semantic_calls": response.budget_usage.semantic_calls,
                }
            )
    finally:
        runtime.close()
    print(
        json.dumps(
            {
                "run_kind": "live_baseline",
                "provider": receipt.provider,
                "model": receipt.model,
                "revision": receipt.revision,
                "schema_mechanism": receipt.schema_mechanism,
                "capability_receipt": str(receipt_path) if receipt_path else None,
                "questions": outcomes,
            },
            indent=2,
        )
    )
    return 0 if all(item["status"] == "ok" for item in outcomes) else 1


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "scan":
            snapshot = DuckDBSource(args.config).scan()
            payload = snapshot.model_dump_json(indent=2)
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(payload + "\n", encoding="utf-8")
            print(
                json.dumps(
                    {
                        "source": snapshot.source_name,
                        "tables": len(snapshot.tables),
                        "columns": snapshot.column_count,
                        "relationships": len(snapshot.relationships),
                        "row_sampling": snapshot.row_sampling,
                    }
                )
            )
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
                print(
                    f"{'PASS' if result['passed'] else 'FAIL'} "
                    f"{result['id']}: {result['question']}"
                )
                if result["missing"]:
                    print(f"  missing: {', '.join(result['missing'])}")
            return 0 if all(item["passed"] for item in results) else 1
        elif args.command == "ask":
            return _run_ask(args)
        elif args.command == "reference":
            return _run_reference(args)
        elif args.command == "baseline":
            return _run_baseline(args)
        elif args.command == "serve":
            import uvicorn

            uvicorn.run("cerebro.api:app", host=args.host, port=args.port)
        return 0
    except (
        CommandConfigurationError,
        RuntimeCompositionError,
        ReferenceQuestionError,
        ProviderConfigurationError,
    ) as exc:
        # Configuration failures are typed and machine readable: a caller must
        # not have to parse prose to learn that a required input was absent.
        code = getattr(exc, "code", type(exc).__name__)
        print(
            json.dumps({"error": code, "message": str(exc), "command": args.command}),
            file=sys.stderr,
        )
        return 2
    except Exception as exc:  # noqa: BLE001 - one sanitized operator message
        print(f"cerebro: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
