"""Command-line entry point for the managed ingestion job."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from .config import IngestionConfig
from .reconcile import DirectiveIngestionRunner, format_result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="directive-ingest")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("preflight")
    subparsers.add_parser("verify")
    subparsers.add_parser("bootstrap")
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
    runner = DirectiveIngestionRunner(config)
    try:
        if args.command == "preflight":
            print(format_result(await runner.preflight()))
        elif args.command == "verify":
            print(format_result(await runner.verify()))
        elif args.command == "bootstrap":
            await runner.bootstrap()
            print('{"status":"ready"}')
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
            print(
                format_result(
                    await runner.run_daily(args.source, args.mandates)
                )
            )
        else:
            raise AssertionError(f"Unknown command: {args.command}")
    finally:
        await runner.close()


def main() -> None:
    asyncio.run(_run(_parser().parse_args()))


if __name__ == "__main__":
    main()
