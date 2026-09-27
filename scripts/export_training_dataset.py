"""Export reviewed EcoNest audit candidates into QLoRA-ready JSONL files."""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from pathlib import Path

from orchestrator.core.audit import read_recent_audit_events_async
from orchestrator.core.database import close_databases, init_databases
from orchestrator.training.dataset import (
    build_review_examples,
    partition_approved_examples,
    read_jsonl_examples,
    write_chat_jsonl,
    write_jsonl_examples,
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    export = subcommands.add_parser("candidates", help="Write privacy-filtered review candidates")
    export.add_argument("--output", type=Path, required=True)
    export.add_argument("--limit", type=int, default=10_000)
    split = subcommands.add_parser("split", help="Write approved train/evaluation chat JSONL")
    split.add_argument("--input", type=Path, required=True)
    split.add_argument("--train-output", type=Path, required=True)
    split.add_argument("--evaluation-output", type=Path, required=True)
    split.add_argument("--evaluation-percent", type=int, default=20)
    return parser.parse_args()


async def _write_candidates(output: Path, limit: int) -> None:
    await init_databases()
    try:
        examples = build_review_examples(await read_recent_audit_events_async(limit))
    finally:
        await close_databases()
    write_jsonl_examples(examples, output)
    _print_counts("Wrote review candidates", examples)
    print(f"Output: {output}")
    print("All candidates need a human review before they can be used for training.")


def _write_split(
    source: Path,
    train_output: Path,
    evaluation_output: Path,
    evaluation_percent: int,
) -> None:
    examples = read_jsonl_examples(source)
    split = partition_approved_examples(examples, evaluation_percent)
    if not split.train or not split.evaluation:
        raise ValueError(
            "Need approved examples in both train and evaluation splits before QLoRA training."
        )
    write_chat_jsonl(split.train, train_output)
    write_chat_jsonl(split.evaluation, evaluation_output)
    print(f"Wrote {len(split.train)} train examples: {train_output}")
    print(f"Wrote {len(split.evaluation)} evaluation examples: {evaluation_output}")


def _print_counts(label: str, examples: list[object]) -> None:
    task_counts = Counter(getattr(example, "task_type", "unknown") for example in examples)
    status_counts = Counter(getattr(example, "review_status", "unknown") for example in examples)
    print(f"{label}: {len(examples)}")
    print("By task type: " + ", ".join(f"{key}={value}" for key, value in sorted(task_counts.items())))
    print("By review status: " + ", ".join(f"{key}={value}" for key, value in sorted(status_counts.items())))


def main() -> None:
    """Run the explicit local export or approved-split workflow."""
    args = _arguments()
    if args.command == "candidates":
        asyncio.run(_write_candidates(args.output, max(1, min(args.limit, 10_000))))
    else:
        _write_split(args.input, args.train_output, args.evaluation_output, args.evaluation_percent)


if __name__ == "__main__":
    main()
