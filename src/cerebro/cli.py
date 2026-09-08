from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
from pathlib import Path

from .bundle import BundleLoader, BundleValidator
from .chat import ChatOrchestrator
from .enrichment import provider_from_environment
from .generation import activate_bundle, review_bundle, run_generation_workflow
from .models import ChatRequest
from .evaluation import (
    DEFAULT_SUPPORTED_QUESTIONS,
    EXPECTED_GOLDEN_QUESTIONS,
    LiveBaselineError,
    ReferenceQuestionError,
    RuntimeCompositionError,
    build_agent,
    load_authorization_scope,
    load_reference_questions,
    prepare_live_evidence,
    run_evaluation,
    run_live_baseline,
    run_offline_reference,
    summarize_evaluation,
    validate_live_baseline,
    write_evaluation_artifact,
    write_offline_reference,
)
from .paths import DEFAULT_BUNDLE, DEFAULT_CONFIG
from .retrieval import SemanticRetriever
from .settings import Settings, resolve_active_bundle
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
    evaluate = commands.add_parser("evaluate", help="Run the 30 semantic grounding questions")
    evaluate.add_argument("--bundle", type=Path, default=DEFAULT_BUNDLE)

    ask = commands.add_parser(
        "ask", help="Ask through the governed chat or strict Text-to-SQL runtime"
    )
    ask.add_argument("question", nargs="?")
    ask.add_argument("--question", dest="option_question")
    ask.add_argument("--bundle", type=Path)
    ask.add_argument("--database", type=Path)
    ask.add_argument("--authorization-scope", type=Path)
    ask.add_argument("--max-rows", type=int, default=1000)

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
    baseline.add_argument("--questions", type=Path, default=EXPECTED_GOLDEN_QUESTIONS)
    baseline.add_argument("--capability-receipt-dir", type=Path, required=True)
    baseline.add_argument("--manifest", type=Path, required=True)
    baseline.add_argument("--materialization-receipt", type=Path, required=True)
    baseline.add_argument("--output", type=Path, required=True)

    serve = commands.add_parser("serve", help="Serve HTTP, MCP, and built web UI")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", default=8000, type=int)
    serve.add_argument("--bundle", type=Path)
    serve.add_argument("--database", type=Path)
    doctor = commands.add_parser("doctor", help="Check local configuration without making a model call")
    doctor.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    doctor.add_argument("--database", type=Path)
    serve.add_argument(
        "--authorization-scope",
        type=Path,
        help="Enable the chat agent under this trusted scope; omit to serve grounding only",
    )
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
    question = args.option_question or args.question
    if not question:
        raise CommandConfigurationError(
            "question_required",
            "ask requires a positional question or --question",
        )

    if args.authorization_scope is not None:
        scope = load_authorization_scope(args.authorization_scope)
        runtime = build_agent(
            args.database or DEFAULT_DATABASE,
            "organizer",
            scope,
            bundle_path=args.bundle or DEFAULT_BUNDLE,
        )
        try:
            from .models import SQLGenerationRequest

            response = runtime.agent.run(
                SQLGenerationRequest(
                    question=question,
                    authorization_scope=scope,
                    dialect="duckdb",
                    max_rows=args.max_rows,
                )
            )
        finally:
            runtime.close()
        print(response.model_dump_json(indent=2))
        return 0 if response.status == "ok" else 1

    settings = Settings.from_environment()
    source = DuckDBSource(DEFAULT_CONFIG, args.database or settings.database_path)
    bundle = BundleLoader().load(resolve_active_bundle(args.bundle))
    retriever = SemanticRetriever(bundle)
    response = ChatOrchestrator(
        bundle, retriever, source.database_path, settings, provider_from_environment()
    ).chat(ChatRequest(message=question))
    print(response.model_dump_json(indent=2))
    return 0 if response.status != "blocked" else 1


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
    scope = load_authorization_scope(args.authorization_scope)
    runtime = build_agent(
        args.database,
        "organizer",
        scope,
        bundle_path=args.bundle,
    )
    try:
        candidate, receipt_path = run_live_baseline(
            runtime,
            capability_receipt_dir=args.capability_receipt_dir,
            questions_path=args.questions,
            max_rows=args.max_rows,
        )
    finally:
        runtime.close()

    evidence = prepare_live_evidence(
        source_manifest=args.manifest,
        materialization_receipt=args.materialization_receipt,
        bundle=args.bundle,
        database=args.database,
        capability_receipt=receipt_path,
        golden_set=args.questions,
    )
    outcome = validate_live_baseline(candidate, evidence)
    if outcome.run_kind == "blocked":
        # A blocked baseline writes nothing at all: no artifact, no placeholder.
        print(outcome.model_dump_json(indent=2), file=sys.stderr)
        return 2
    written = write_evaluation_artifact(outcome, args.output)
    print(
        json.dumps(
            {
                "run_kind": outcome.run_kind,
                "run_id": outcome.provenance.run_id,
                "total_questions": outcome.total_questions,
                "ok_count": outcome.ok_count,
                "refused_count": outcome.refused_count,
                "check_failed_count": outcome.check_failed_count,
                "capability_receipt": str(receipt_path),
                "output": str(written),
            }
        )
    )
    return 0


def _run_serve(args) -> int:
    """Serve the UI, the grounding API, MCP, and optionally the chat agent.

    The agent is opt-in. Without `--authorization-scope` this is exactly the
    grounding-only server it has always been, so forgetting a flag cannot
    accidentally expose query execution.
    """
    import uvicorn

    from .agent_api import attach_agent
    from .api import create_app
    from .paths import ROOT

    if args.database is not None:
        os.environ["CEREBRO_DATABASE_PATH"] = str(args.database.resolve())
    app = create_app(args.bundle)
    runtime = None
    if args.authorization_scope is not None:
        scope = load_authorization_scope(args.authorization_scope)
        # Built here, not on the first request: a missing credential or database
        # should stop the server rather than turn every chat message into a 500.
        runtime = build_agent(
            args.database or DEFAULT_DATABASE,
            "organizer",
            scope,
            bundle_path=args.bundle or DEFAULT_BUNDLE,
        )
        attach_agent(app, runtime)

    built = (ROOT / "apps" / "web" / "dist").exists()
    base = f"http://{args.host}:{args.port}"
    print(
        f"web UI   {base}/"
        if built
        else "web UI   not built (cd apps/web && npm install && npm run build)"
    )
    print(f"HTTP API {base}/api/health")
    print(f"MCP      {base}/mcp")
    if runtime is None:
        print("agent    disabled (pass --authorization-scope to enable the chat)")
    else:
        print(f"agent    {base}/api/agent/ask")
        print("         this endpoint has no authentication; keep the host loopback")
    try:
        uvicorn.run(app, host=args.host, port=args.port)
    finally:
        if runtime is not None:
            runtime.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "scan":
            snapshot = DuckDBSource(args.config, args.database).scan()
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
                "relationships": sum(item.profile_kind == "relationship" for item in result.bundle.objects),
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
                print(
                    f"{'PASS' if result['passed'] else 'FAIL'} "
                    f"{result['id']}: {result['question']}"
                )
                if result["missing"]:
                    print(f"  missing: {', '.join(result['missing'])}")
            summary = summarize_evaluation(results)
            print(f"SUMMARY {summary['cases']['passed']}/{summary['cases']['total']} cases; join-path accuracy {summary['join_path_accuracy']:.1%}")
            return 0 if all(item["passed"] for item in results) else 1
        elif args.command == "ask":
            return _run_ask(args)
        elif args.command == "reference":
            return _run_reference(args)
        elif args.command == "baseline":
            return _run_baseline(args)
        elif args.command == "serve":
            return _run_serve(args)
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
        return 0
    except (
        CommandConfigurationError,
        RuntimeCompositionError,
        ReferenceQuestionError,
        LiveBaselineError,
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
