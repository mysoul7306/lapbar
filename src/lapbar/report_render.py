"""Fixed-width plain-text rendering of the `lapbar report` data (no colors)."""
from __future__ import annotations

from typing import Mapping, Sequence

MODEL_WIDTH = 26
NUMBER_WIDTH = 15
UNPRICED_TAG = " [unpriced]"
TABLE_COLUMNS = (
    ("requests", "requests"),
    ("input_tokens", "input"),
    ("cache_creation_input_tokens", "cache_write"),
    ("cache_read_input_tokens", "cache_read"),
    ("output_tokens", "output"),
)
COST_HEADER = "cost_usd"


def _money(value: float) -> str:
    return f"${value:,.2f}"


def _display_name(row: Mapping) -> str:
    return row["model"] + ("" if row["priced"] else UNPRICED_TAG)


def _model_width(rows: Sequence[Mapping]) -> int:
    return max([MODEL_WIDTH, *(len(_display_name(row)) + 1 for row in rows)])


def _header(width: int) -> str:
    cells = [f"{label:>{NUMBER_WIDTH}}" for _key, label in TABLE_COLUMNS]
    return f"  {'model':<{width}}" + "".join(cells) + f"{COST_HEADER:>{NUMBER_WIDTH}}"


def _row(row: Mapping, width: int) -> str:
    cells = [f"{row[key]:>{NUMBER_WIDTH},}" for key, _label in TABLE_COLUMNS]
    return f"  {_display_name(row):<{width}}" + "".join(cells) + f"{_money(row['cost_usd']):>{NUMBER_WIDTH}}"


def _table(title: str, rows: Sequence[Mapping], width: int) -> list[str]:
    if not rows:
        return [f"{title}: (none)"]
    total = sum(row["cost_usd"] for row in rows)
    cost_span = NUMBER_WIDTH * (len(TABLE_COLUMNS) + 1)
    return [title, _header(width), *(_row(row, width) for row in rows), f"  {'total':<{width}}{_money(total):>{cost_span}}"]


def _overview(report: Mapping) -> list[str]:
    period, requests, prices = report["period"], report["requests"], report["prices"]
    return [
        f"lapbar report [{report['currency']}, {report['estimate']}]",
        f"Period    : {period['since']} .. {period['until']} ({period['days']} days, UTC)",
        f"Sessions  : {report['sessions']:,}",
        f"Requests  : {requests['total']:,} (head {requests['head']:,}, subagent {requests['subagent']:,}, "
        f"workflow {requests['workflow']:,})",
        f"Prices    : as of {prices['as_of']} ({prices['source']}); {prices['note']}",
    ]


def _counterfactual(report: Mapping) -> list[str]:
    cf = report["counterfactual"]
    inferred = " (inferred)" if report.get("head_model_source") == "inferred" else ""
    return [
        f"Counterfactual: every subagent token priced as {report['head_model']}{inferred}",
        f"  actual      {_money(cf['actual_usd']):>{NUMBER_WIDTH}}",
        f"  if_all_head {_money(cf['if_all_head_usd']):>{NUMBER_WIDTH}}",
        f"  saved       {_money(cf['saved_usd']):>{NUMBER_WIDTH}}  "
        f"({cf['saved_pct_of_if_all_head']:.1f}% of if_all_head, {cf['saved_pct_of_actual']:.1f}% of actual)",
    ]


def _cache_write(report: Mapping) -> list[str]:
    split = report["cache_write"]
    return [
        f"Cache writes: 5m {split['tokens_5m']:,} tokens, 1h {split['tokens_1h']:,} tokens "
        f"({split['share_1h_pct']:.1f}% 1h, priced at input x 2)"
    ]


def _effort(report: Mapping) -> list[str]:
    shares = report["subagent_effort"]
    if not shares:
        return ["Subagent effort: (none)"]
    lines = [f"  {name:<10}{share['requests']:>{NUMBER_WIDTH},}  ({share['pct']:.1f}%)" for name, share in shares.items()]
    return ["Subagent effort (requests)", *lines]


def _footer(report: Mapping) -> list[str]:
    scan = report.get("scan")
    if not scan:
        return []
    return [
        f"Scanned {scan['files_read']:,} files ({scan['files_skipped_by_mtime']:,} skipped by mtime) "
        f"in {scan['seconds']:.1f}s"
    ]


def render_text(report: Mapping) -> str:
    models = report["models"]
    width = _model_width([*models["head"], *models["subagent"]])
    sections = [
        _overview(report),
        _table("Head (session) requests", models["head"], width),
        _table("Subagent requests", models["subagent"], width),
        [f"Workflow subset: {report['workflow']['requests']:,} requests, {_money(report['workflow']['cost_usd'])}"],
        _counterfactual(report),
        _cache_write(report),
        _effort(report),
        [f"Summary: {report['summary']}"],
        _footer(report),
    ]
    return "\n\n".join("\n".join(lines) for lines in sections if lines)
