"""Command line interface for openpulse-analysis."""

from __future__ import annotations

import argparse


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
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.version:
        from openpulse_analysis import __version__

        print(__version__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
