"""Command-line entry point for the managed ingestion job."""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path
from uuid import uuid4

from .config import IngestionConfig
from .reconcile import (
    DailyRunApproval,
    DirectiveIngestionRunner,
    format_result,
)
from .run_metrics import IngestionRunMetrics


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="directive-ingest")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("preflight")
    verify = subparsers.add_parser("verify")
    verify.add_argument("--deep-source-audit", action="store_true")
    subparsers.add_parser("bootstrap")
    subparsers.add_parser("bootstrap-gate")
    subparsers.add_parser("maintenance")
    validate = subparsers.add_parser("validate")
    validate.add_argument("--source", type=Path)
    validate.add_argument("--mandates", type=Path)
    daily = subparsers.add_parser("run-daily")
    daily.add_argument("--source", type=Path)
    daily.add_argument("--mandates", type=Path)
    return parser


async def _run(args: argparse.Namespace) -> None:
    config = IngestionConfig.from_environment()
    approval = (
        _daily_run_approval_from_environment()
        if args.command == "run-daily"
        else None
    )
    source_override = getattr(args, "source", None)
    if source_override is not None and config.source_kind != "local":
        raise ValueError(
            "--source cannot be used when DIRECTIVE_SOURCE_KIND=azure_blob"
        )
    runner = DirectiveIngestionRunner(config)
    metrics = IngestionRunMetrics(
        run_id=str(uuid4()),
        operation=args.command,
        processing_hash=config.processing_hash,
    )
    runner.attach_metrics(metrics)
    try:
        try:
            emit_result = True
            if args.command == "preflight":
                result = await runner.preflight()
            elif args.command == "verify":
                expected_validation_digest = (
                    _verify_validation_digest_from_environment()
                    if config.source_kind == "azure_blob"
                    else None
                )
                result = await runner.verify(
                    expected_validation_digest=expected_validation_digest,
                    deep_source_audit=getattr(
                        args,
                        "deep_source_audit",
                        False,
                    ),
                )
            elif args.command == "bootstrap":
                await runner.bootstrap()
                result = {"status": "ready"}
            elif args.command == "bootstrap-gate":
                result = await runner.bootstrap_publication_gate(
                    run_id=metrics.run_id
                )
            elif args.command == "maintenance":
                result = {"status": "maintenance"}
                emit_result = False
            elif args.command == "validate":
                result = await runner.validate_inputs(
                    args.source, args.mandates
                )
            elif args.command == "run-daily":
                if approval is None:
                    raise AssertionError(
                        "run-daily approval was not initialized"
                    )
                result = await runner.run_daily(
                    args.source,
                    args.mandates,
                    approved_validation_digest=approval.validation_digest,
                    approved_environment_digest=approval.environment_digest,
                    approved_source_inventory_digest=(
                        approval.source_inventory_digest
                    ),
                    approved_validation_evidence_digest=(
                        approval.validation_evidence_digest
                    ),
                )
            else:
                raise AssertionError(f"Unknown command: {args.command}")
        except Exception as operation_error:
            metrics.fail(type(operation_error).__name__.casefold())
            try:
                await runner.catalog.record_run_metrics(metrics.to_payload())
            except Exception as metrics_error:
                raise ExceptionGroup(
                    "Ingestion execution and metrics recording both failed",
                    [operation_error, metrics_error],
                ) from operation_error
            raise
        result_run_id = (
            result.get("run_id")
            if isinstance(result, dict)
            else getattr(result, "run_id", None)
        )
        if isinstance(result_run_id, str) and result_run_id:
            metrics.run_id = result_run_id
        changed_count = int(getattr(result, "changed_count", 0))
        skipped_count = int(getattr(result, "skipped_count", 0))
        mandate_changed = bool(getattr(result, "mandate_changed", False))
        if changed_count:
            metrics.increment("changed_count", changed_count)
        if skipped_count:
            metrics.increment("skipped_count", skipped_count)
        if args.command == "maintenance" or (
            args.command == "run-daily"
            and changed_count == 0
            and not mandate_changed
        ):
            metrics.skip()
        else:
            metrics.succeed()
        await runner.catalog.record_run_metrics(metrics.to_payload())
        if emit_result:
            verification = getattr(result, "verification", None)
            emitted_result = (
                verification
                if args.command == "run-daily"
                and isinstance(verification, dict)
                else result
            )
            print(format_result(emitted_result))
    finally:
        await runner.close()


def _daily_run_approval_from_environment() -> DailyRunApproval:
    names = (
        "DIRECTIVE_APPROVED_VALIDATION_DIGEST",
        "DIRECTIVE_APPROVED_ENVIRONMENT_DIGEST",
        "DIRECTIVE_APPROVED_SOURCE_INVENTORY_DIGEST",
        "DIRECTIVE_APPROVED_VALIDATION_EVIDENCE_DIGEST",
    )
    values = {name: os.getenv(name, "").strip() for name in names}
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise ValueError(
            "run-daily requires nonempty " + ", ".join(missing)
        )
    return DailyRunApproval(
        validation_digest=values[names[0]],
        environment_digest=values[names[1]],
        source_inventory_digest=values[names[2]],
        validation_evidence_digest=values[names[3]],
    )


def _verify_validation_digest_from_environment() -> str:
    value = os.getenv("DIRECTIVE_APPROVED_VALIDATION_DIGEST", "").strip()
    if not value:
        raise ValueError(
            "verify requires nonempty DIRECTIVE_APPROVED_VALIDATION_DIGEST"
        )
    return value


def main() -> None:
    asyncio.run(_run(_parser().parse_args()))


if __name__ == "__main__":
    main()
