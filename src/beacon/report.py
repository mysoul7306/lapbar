"""`beacon report`: actual vs all-on-head-model cost from local Claude Code transcripts.

Costs are list price x tokens (an estimate, not billing). Nothing leaves the machine.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Iterable, Mapping

from beacon import report_render
from beacon.pricing import PriceTable, cost_usd, load_prices
from beacon.transcripts import ROLE_HEAD, ROLE_SUBAGENT, TOKEN_KEYS, Record, iter_records, list_transcripts

DEFAULT_PROJECTS_DIR = "~/.claude/projects"
DEFAULT_HEAD_MODEL = "claude-fable-5-1"
EXIT_USAGE_ERROR = 2


@dataclass(frozen=True)
class Usage:
    requests: int = 0
    tokens: tuple[int, ...] = (0, 0, 0, 0)

    def add(self, tokens: tuple[int, ...]) -> "Usage":
        return Usage(self.requests + 1, tuple(a + b for a, b in zip(self.tokens, tokens)))

    def as_dict(self) -> dict[str, int]:
        return dict(zip(TOKEN_KEYS, self.tokens))


@dataclass(frozen=True)
class Totals:
    by_role_model: Mapping[tuple[str, str], Usage] = field(default_factory=dict)
    workflow: Mapping[str, Usage] = field(default_factory=dict)
    effort: Mapping[str, int] = field(default_factory=dict)
    sessions: int = 0
    first_day: str | None = None
    last_day: str | None = None


def iso_date(value: str) -> str:
    """argparse type: validate YYYY-MM-DD and return it unchanged."""
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"expected YYYY-MM-DD, got '{value}'") from exc
    return value


def add_parser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "report",
        help="estimate what subagents on cheaper models saved",
        description="Read local Claude Code transcripts and compare actual cost with pricing every "
        "subagent token at the head model's rate. Dates are UTC days, both ends inclusive.",
    )
    parser.add_argument("--projects-dir", default=DEFAULT_PROJECTS_DIR, help=f"default {DEFAULT_PROJECTS_DIR}")
    parser.add_argument("--since", type=iso_date, help="first day to include (YYYY-MM-DD, UTC)")
    parser.add_argument("--until", type=iso_date, help="last day to include (YYYY-MM-DD, UTC)")
    parser.add_argument("--head", default=DEFAULT_HEAD_MODEL, help=f"head model for the counterfactual (default {DEFAULT_HEAD_MODEL})")
    parser.add_argument("--prices", help="JSON price file that adds or replaces models in the packaged table")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    parser.set_defaults(func=run)
    return parser


def aggregate(records: Iterable[Record]) -> Totals:
    """Fold records into per (role, model) usage, workflow usage, subagent effort counts."""
    by_role_model: dict[tuple[str, str], Usage] = {}
    workflow: dict[str, Usage] = {}
    effort: Counter[str] = Counter()
    sessions: set[str] = set()
    days: list[str] = []
    for record in records:
        key = (record.role, record.model)
        by_role_model[key] = by_role_model.get(key, Usage()).add(record.tokens)
        if record.workflow:
            workflow[record.model] = workflow.get(record.model, Usage()).add(record.tokens)
        if record.role == ROLE_SUBAGENT:
            effort[record.effort] += 1
        sessions.add(record.session)
        day = record.timestamp[:10]
        if day:
            days = [min(days[0], day), max(days[1], day)] if days else [day, day]
    return Totals(
        by_role_model=dict(by_role_model),
        workflow=dict(workflow),
        effort=dict(effort),
        sessions=len(sessions),
        first_day=days[0] if days else None,
        last_day=days[1] if days else None,
    )


def _model_rows(totals: Totals, role: str, prices: PriceTable) -> list[dict]:
    rows = []
    for (row_role, model), usage in sorted(totals.by_role_model.items()):
        if row_role != role:
            continue
        rate = prices.rate(model)
        rows.append({
            "model": model,
            "requests": usage.requests,
            **usage.as_dict(),
            "cost_usd": round(cost_usd(usage.as_dict(), rate), 4),
            "priced": rate is not None,
        })
    return sorted(rows, key=lambda row: -row["cost_usd"])


def counterfactual(totals: Totals, prices: PriceTable, head_model: str) -> dict[str, float]:
    """Reprice every priced subagent token at the head rate. Unpriced models stay at 0 on both
    sides so they cannot inflate the saving."""
    head_rate = prices.rate(head_model)
    actual = 0.0
    if_all_head = 0.0
    for (role, model), usage in totals.by_role_model.items():
        rate = prices.rate(model)
        cost = cost_usd(usage.as_dict(), rate)
        actual += cost
        repriced = role == ROLE_SUBAGENT and rate is not None
        if_all_head += cost_usd(usage.as_dict(), head_rate) if repriced else cost
    saved = if_all_head - actual
    pct = (saved / if_all_head * 100) if if_all_head > 0 else 0.0
    return {
        "actual_usd": round(actual, 4),
        "if_all_head_usd": round(if_all_head, 4),
        "saved_usd": round(saved, 4),
        "saved_pct": round(pct, 1),
    }


def _period(totals: Totals, since: str | None, until: str | None) -> dict:
    start = since or totals.first_day
    end = until or totals.last_day
    days = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1 if start and end else 0
    return {"since": start, "until": end, "days": max(days, 0)}


def _effort_shares(effort: Mapping[str, int]) -> dict[str, dict]:
    total = sum(effort.values())
    ordered = sorted(effort.items(), key=lambda item: (-item[1], item[0]))
    return {name: {"requests": count, "pct": round(count / total * 100, 1)} for name, count in ordered}


def _workflow_summary(totals: Totals, prices: PriceTable) -> dict:
    requests = sum(usage.requests for usage in totals.workflow.values())
    cost = sum(cost_usd(usage.as_dict(), prices.rate(model)) for model, usage in totals.workflow.items())
    return {"requests": requests, "cost_usd": round(cost, 4)}


def summary_line(cf: Mapping[str, float], head_model: str, days: int) -> str:
    saved = cf["saved_usd"]
    verb = f"saved ${saved:,.2f}" if saved >= 0 else f"cost ${-saved:,.2f} more"
    return (
        f"subagents on cheaper models {verb} ({abs(cf['saved_pct']):.1f}%) vs running everything "
        f"on {head_model} over {days} days [list-price estimate]"
    )


def build_report(totals: Totals, prices: PriceTable, head_model: str, since: str | None, until: str | None) -> dict:
    """Plain-data report; the JSON output and the text renderer both read this."""
    head_rows = _model_rows(totals, ROLE_HEAD, prices)
    sub_rows = _model_rows(totals, ROLE_SUBAGENT, prices)
    period = _period(totals, since, until)
    cf = counterfactual(totals, prices, head_model)
    workflow = _workflow_summary(totals, prices)
    head_requests = sum(row["requests"] for row in head_rows)
    sub_requests = sum(row["requests"] for row in sub_rows)
    return {
        "estimate": "list price x tokens; not actual billing",
        "currency": "USD",
        "period": period,
        "sessions": totals.sessions,
        "requests": {
            "total": head_requests + sub_requests,
            "head": head_requests,
            "subagent": sub_requests,
            "workflow": workflow["requests"],
        },
        "head_model": head_model,
        "prices": {"as_of": prices.as_of, "source": prices.source, "note": prices.note},
        "models": {"head": head_rows, "subagent": sub_rows},
        "workflow": workflow,
        "counterfactual": cf,
        "subagent_effort": _effort_shares(totals.effort),
        "unpriced_models": sorted({row["model"] for row in head_rows + sub_rows if not row["priced"]}),
        "summary": summary_line(cf, head_model, period["days"]),
    }


def _warn_unpriced(models: Iterable[str]) -> None:
    for model in models:
        print(f"WARN: no price for model '{model}'; costed as $0 and marked [unpriced]", file=sys.stderr)


def run(args: argparse.Namespace) -> int:
    started = time.monotonic()
    if args.since and args.until and args.since > args.until:
        print("ERROR: --since is after --until", file=sys.stderr)
        return EXIT_USAGE_ERROR
    try:
        prices = load_prices(args.prices)
    except (OSError, ValueError) as exc:
        print(f"ERROR: cannot load prices: {exc}", file=sys.stderr)
        return EXIT_USAGE_ERROR
    head_model = prices.resolve(args.head)
    if head_model is None:
        print(f"ERROR: head model '{args.head}' has no price entry", file=sys.stderr)
        return EXIT_USAGE_ERROR
    projects_dir = Path(args.projects_dir).expanduser()
    if not projects_dir.is_dir():
        print(f"ERROR: projects dir not found: {projects_dir}", file=sys.stderr)
        return EXIT_USAGE_ERROR
    listing = list_transcripts(projects_dir, args.since)
    totals = aggregate(iter_records(projects_dir, listing.files, args.since, args.until))
    report = build_report(totals, prices, head_model, args.since, args.until)
    _warn_unpriced(report["unpriced_models"])
    scan = {
        "files_read": len(listing.files),
        "files_skipped_by_mtime": listing.skipped_by_mtime,
        "seconds": round(time.monotonic() - started, 2),
    }
    full = {**report, "scan": scan}
    print(json.dumps(full, indent=2) if args.json else report_render.render_text(full))
    return 0
