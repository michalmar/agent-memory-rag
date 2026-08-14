"""Command-line entry point for the managed ingestion job."""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

from .config import IngestionConfig
from .reconcile import (
    DailyRunApproval,
    DirectiveIngestionRunner,
    format_result,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="directive-ingest")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("preflight")
    subparsers.add_parser("verify")
    subparsers.add_parser("bootstrap")
    subparsers.add_parser("maintenance")
    validate = subparsers.add_parser("validate")
    validate.add_argument("--source", type=Path)
    validate.add_argument("--mandates", type=Path)
    documents = subparsers.add_parser("reconcile-documents")
    documents.add_argument("--source", type=Path)
    mandates = subparsers.add_parser("publish-mandates")
    mandates.add_argument("--csv", type=Path)
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
    try:
        if args.command == "preflight":
            print(format_result(await runner.preflight()))
        elif args.command == "verify":
            print(format_result(await runner.verify()))
        elif args.command == "bootstrap":
            await runner.bootstrap()
            print('{"status":"ready"}')
        elif args.command == "maintenance":
            return
        elif args.command == "validate":
            result = await runner.validate_inputs(
                args.source, args.mandates
            )
            print(format_result(result))
        elif args.command == "reconcile-documents":
            print(
                format_result(
                    await runner.reconcile_documents(args.source)
                )
            )
        elif args.command == "publish-mandates":
            snapshot, changed = await runner.publish_mandates(args.csv)
            print(
                format_result(
                    {
                        "snapshot_id": snapshot.snapshot_id,
                        "changed": changed,
                    }
                )
            )
        elif args.command == "run-daily":
            if approval is None:
                raise AssertionError("run-daily approval was not initialized")
            print(
                format_result(
                    await runner.run_daily(
                        args.source,
                        args.mandates,
                        approved_validation_digest=approval.validation_digest,
                        approved_environment_digest=approval.environment_digest,
                        approved_source_inventory_digest=(
                            approval.source_inventory_digest
                        ),
                    )
                )
            )
        else:
            raise AssertionError(f"Unknown command: {args.command}")
    finally:
        await runner.close()


def _daily_run_approval_from_environment() -> DailyRunApproval:
    names = (
        "DIRECTIVE_APPROVED_VALIDATION_DIGEST",
        "DIRECTIVE_APPROVED_ENVIRONMENT_DIGEST",
        "DIRECTIVE_APPROVED_SOURCE_INVENTORY_DIGEST",
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
    )


def main() -> None:
    asyncio.run(_run(_parser().parse_args()))


if __name__ == "__main__":
    main()
