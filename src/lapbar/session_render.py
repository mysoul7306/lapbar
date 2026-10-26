"""Fixed-width plain-text rendering of the `lapbar session` data (no colors, at most MAX_LINES)."""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Mapping, Sequence

MAX_LINES = 40
MIN_ROWS = 1
NAME_WIDTH = 32
MODEL_WIDTH = 26
PATH_WIDTH = 64
COMMAND_WIDTH = 56
ELLIPSIS = "..."
INSTRUCTION_ROWS = 5
HOOK_ROWS = 8
HOOK_TIME_ROWS = 5
CALL_ROWS = 8
SUBAGENT_MODEL_ROWS = 6
HEAD_MODEL_ROWS = 2


@dataclass(frozen=True)
class Section:
    title: str
    rows: tuple[str, ...] = ()
    tail: tuple[str, ...] = ()
    cap: int = 0

    def shown(self) -> int:
        return min(self.cap, len(self.rows))

    def lines(self) -> list[str]:
        hidden = len(self.rows) - self.shown()
        more = [f"  (+{hidden} more)"] if hidden else []
        return [self.title, *self.rows[: self.shown()], *more, *self.tail]


def one_line(text: str) -> str:
    """Collapse newlines and runs of whitespace so one row stays one physical line."""
    return " ".join(text.split())


def clip_right(text: str, width: int) -> str:
    flat = one_line(text)
    return flat if len(flat) <= width else flat[: width - len(ELLIPSIS)] + ELLIPSIS


def clip_left(text: str, width: int) -> str:
    """Keep the end of a path, where the file name is."""
    flat = one_line(text)
    return flat if len(flat) <= width else ELLIPSIS + flat[len(flat) - width + len(ELLIPSIS):]


def _instructions(report: Mapping) -> Section:
    block = report["instructions"]
    if not block["files"]:
        return Section("Instructions: (none)")
    title = (f"Instructions: {block['total_chars']:,} chars (~{block['tokens_est']:,} tokens est) in "
             f"{len(block['files'])} files, loaded {block['loads']}x")
    rows = tuple(f"  {f['chars']:>10,}  {clip_left(f['path'], PATH_WIDTH)}" for f in block["files"])
    return Section(title, rows, cap=INSTRUCTION_ROWS)


def _hook_row(name: str, stat: Mapping) -> str:
    over = f"  ({stat['over_10k']} over 10k)" if stat["over_10k"] else ""
    return (f"  {clip_right(name, NAME_WIDTH):<{NAME_WIDTH}} x{stat['count']:<5,} {stat['chars']:>11,} chars "
            f"~{stat['tokens_est']:>9,} tok{over}")


def _hooks(report: Mapping) -> Section:
    hooks = report["hooks"]
    if not hooks:
        return Section("Hooks: (none)")
    rows = tuple(_hook_row(name, stat) for name, stat in hooks.items())
    return Section("Hooks (head session only, injected text by hook name)", rows, (_hook_row("total", report["hook_totals"]),),
                   cap=HOOK_ROWS)


def _hook_time(report: Mapping) -> Section:
    timing = report["hook_time"]
    if not timing:
        return Section("Hook time: (none)")
    rows = tuple(
        f"  {stat['total_ms']:>9,} ms  x{stat['count']:<5,} mean {stat['mean_ms']:>9,.1f} ms  "
        f"{clip_right(command, COMMAND_WIDTH)}"
        for command, stat in timing.items()
    )
    return Section("Hook time (head session only, by total ms)", rows, cap=HOOK_TIME_ROWS)


def _calls(report: Mapping) -> Section:
    calls = report["subagent_calls"]
    total = sum(call["count"] for call in calls)
    title = f"Subagent calls: {total:,} (type x model x effort), workflow calls {report['workflow_calls']:,}"
    rows = tuple(
        f"  {call['count']:>6,}  {one_line(call['subagent_type'])} x {one_line(call['model'])} x "
        f"{one_line(call['effort'])}"
        for call in calls
    )
    return Section(title, rows, cap=CALL_ROWS)


def _usage_rows(by_model: Mapping[str, Mapping]) -> tuple[str, ...]:
    return tuple(
        f"  {clip_right(model, MODEL_WIDTH):<{MODEL_WIDTH}} {row['requests']:>7,} req  output "
        f"{row['output_tokens']:>12,}  cache_read {row['cache_read_input_tokens']:>14,}"
        for model, row in by_model.items()
    )


def _subagent_usage(report: Mapping) -> Section:
    usage = report["subagent_usage"]
    efforts = ", ".join(f"{name} {count:,}" for name, count in usage["by_effort"].items()) or "-"
    files = report["session"]["subagent_files"]
    title = f"Subagent usage: {usage['requests']:,} requests in {files:,} files; effort {efforts}"
    return Section(title, _usage_rows(usage["by_model"]), cap=SUBAGENT_MODEL_ROWS)


def _head_usage(report: Mapping) -> Section:
    usage = report["head_usage"]
    return Section(f"Head usage: {usage['requests']:,} requests", _usage_rows(usage["by_model"]),
                   cap=HEAD_MODEL_ROWS)


def _total(fixed: int, sections: Sequence[Section]) -> int:
    return fixed + sum(len(section.lines()) for section in sections)


def fit(sections: tuple[Section, ...], fixed: int, limit: int = MAX_LINES) -> tuple[Section, ...]:
    """Lower the cap of the section showing the most rows, one row at a time, until it fits."""
    current = sections
    while _total(fixed, current) > limit:
        widest = max(range(len(current)), key=lambda index: current[index].shown())
        if current[widest].shown() <= MIN_ROWS:
            break
        trimmed = replace(current[widest], cap=current[widest].shown() - 1)
        current = tuple(trimmed if index == widest else section for index, section in enumerate(current))
    return current


def render_text(report: Mapping) -> str:
    info = report["session"]
    header = f"lapbar session {info['id']} [project {info['project_dir']}]"
    footer = f"estimate: tokens ~ chars/{report['chars_per_token_est']}"
    sections = (
        _instructions(report),
        _hooks(report),
        _hook_time(report),
        _calls(report),
        _subagent_usage(report),
        _head_usage(report),
    )
    body = [line for section in fit(sections, fixed=2) for line in section.lines()]
    return "\n".join([header, *body, footer])
