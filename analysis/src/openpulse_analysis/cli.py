"""Command line interface for openpulse-analysis."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from openpulse_analysis import __version__
from openpulse_analysis.orchestrator import OrchestrationError, run_sequential
from openpulse_analysis.registry import get_registered_tasks

DEFAULT_CHECKPOINT = ".openpulse-analysis.checkpoint.json"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="openpulse-analysis",
        description="Open Pulse analysis command line interface.",
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="Print the openpulse-analysis package version.",
    )

    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser(
        "run",
        help="Run registered analysis tasks sequentially.",
    )
    run_parser.add_argument(
        "--checkpoint-path",
        default=DEFAULT_CHECKPOINT,
        help="Checkpoint file path used to save/restore run state.",
    )
    run_parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from checkpoint by skipping completed tasks.",
    )

    subparsers.add_parser(
        "list-tasks",
        help="List all registered tasks in execution order.",
    )

    doctor_parser = subparsers.add_parser(
        "doctor",
        help="Validate task registry and local execution prerequisites.",
    )
    doctor_parser.add_argument(
        "--checkpoint-path",
        default=DEFAULT_CHECKPOINT,
        help="Checkpoint file path used to validate write access.",
    )
    return parser


def _do_run(checkpoint_path: str, *, resume: bool) -> int:
    tasks = get_registered_tasks()
    path = Path(checkpoint_path)

    try:
        completed = run_sequential(tasks, path, resume=resume)
    except OrchestrationError as exc:
        print(f"Run failed on task: {exc.failed_task}")
        return 1

    print(f"Run completed successfully ({len(completed)} task(s)).")
    return 0


def _do_list_tasks() -> int:
    for task in get_registered_tasks():
        print(task.name)
    return 0


def _do_doctor(checkpoint_path: str) -> int:
    tasks = get_registered_tasks()
    errors: list[str] = []

    if not tasks:
        errors.append("No tasks are registered.")

    task_names = [task.name for task in tasks]
    if len(set(task_names)) != len(task_names):
        errors.append("Duplicate task names detected in registry.")

    checkpoint_parent = Path(checkpoint_path).expanduser().resolve().parent
    if not checkpoint_parent.exists():
        errors.append(f"Checkpoint directory does not exist: {checkpoint_parent}")
    elif not os.access(checkpoint_parent, os.W_OK):
        errors.append(f"Checkpoint directory is not writable: {checkpoint_parent}")

    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1

    print("OK: task registry and checkpoint configuration look healthy.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.version:
        print(__version__)
        return 0

    if args.command == "run":
        return _do_run(args.checkpoint_path, resume=args.resume)
    if args.command == "list-tasks":
        return _do_list_tasks()
    if args.command == "doctor":
        return _do_doctor(args.checkpoint_path)

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
