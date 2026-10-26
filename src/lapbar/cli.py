"""Command line entry point. Each subcommand lives in its own module and exposes `add_parser(subparsers)` and `run(args) -> int`."""
from __future__ import annotations

import argparse
import sys

from lapbar import __version__
from lapbar import bundle, doctor, install, report, session

COMMANDS = (install, doctor, report, session, bundle)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lapbar", description="Claude Code harness installer and cost-savings report.")
    parser.add_argument("--version", action="version", version=f"lapbar {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for module in COMMANDS:
        module.add_parser(subparsers)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
