"""`lapbar session`: one session's hook injection, instruction load, subagent calls and tokens.

Reads one head transcript plus its subagent transcripts. Text sizes are converted to tokens
with a fixed chars-per-token ratio (an estimate); token counts from API usage are exact.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Mapping

from lapbar import session_render
from lapbar.transcripts import ROLE_HEAD, SUBAGENT_DIR, USAGE_KEYS, Record, iter_records

DEFAULT_PROJECTS_DIR = "~/.claude/projects"
SESSION_ENV = "CLAUDE_CODE_SESSION_ID"
EXIT_USAGE_ERROR = 2
CHARS_PER_TOKEN_EST = 3
OVER_CHARS = 10_000
ESTIMATE_NOTE = f"estimate: tokens ~ chars/{CHARS_PER_TOKEN_EST} for hook and instruction text; usage tokens are exact"
TRANSCRIPT_SUFFIX = ".jsonl"
SUBAGENT_GLOB = "agent-*.jsonl"
MISSING = "-"
SUBAGENT_TOOLS = frozenset({"Agent", "Task"})
WORKFLOW_TOOL = "Workflow"
HOOK_ATTACHMENTS = frozenset({"hook_success", "hook_additional_context", "hook_system_message"})
STOP_SUMMARY = "stop_hook_summary"
STOP_EVENTS = frozenset({"Stop", "SubagentStop"})
HOOK_NAME_SEPARATOR = ":"
HOOKS_SCOPE = "head"


@dataclass(frozen=True)
class HookStat:
    count: int = 0
    chars: int = 0
    over: int = 0

    def add(self, chars: int) -> "HookStat":
        return HookStat(self.count + 1, self.chars + chars, self.over + (chars > OVER_CHARS))

    def as_dict(self) -> dict[str, int]:
        return {"count": self.count, "chars": self.chars, "tokens_est": estimate_tokens(self.chars),
                "over_10k": self.over}


@dataclass(frozen=True)
class TimeStat:
    count: int = 0
    total_ms: int = 0

    def add(self, ms: int) -> "TimeStat":
        return TimeStat(self.count + 1, self.total_ms + ms)

    def as_dict(self) -> dict:
        mean = round(self.total_ms / self.count, 1) if self.count else 0.0
        return {"count": self.count, "total_ms": self.total_ms, "mean_ms": mean}


@dataclass(frozen=True)
class HeadScan:
    hooks: Mapping[str, HookStat]
    hook_time: Mapping[str, TimeStat]
    instructions: tuple[tuple[str, int], ...]
    instruction_loads: int
    calls: Mapping[tuple[str, str, str], int]
    workflow_calls: int


def estimate_tokens(chars: int) -> int:
    return chars // CHARS_PER_TOKEN_EST


def text_length(value: object) -> int:
    """Characters a hook payload puts into context: strings as is, lists summed, dicts by
    their `text` field or else their JSON form."""
    if value is None:
        return 0
    if isinstance(value, str):
        return len(value)
    if isinstance(value, list):
        return sum(text_length(item) for item in value)
    if isinstance(value, dict) and isinstance(value.get("text"), str):
        return len(value["text"])
    return len(json.dumps(value, ensure_ascii=False, default=str))


def _as_ms(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def iter_objects(path: Path) -> Iterator[dict]:
    """JSON objects of a jsonl file; broken lines and non-objects are skipped."""
    with path.open("rb") as handle:
        for raw in handle:
            try:
                obj = json.loads(raw)
            except ValueError:
                continue
            if isinstance(obj, dict):
                yield obj


def _is_json_object(text: object) -> bool:
    if not isinstance(text, str):
        return False
    try:
        return isinstance(json.loads(text.strip()), dict)
    except ValueError:
        return False


def _hook_chars(attachment: Mapping) -> int:
    """Injected characters of one hook attachment. A JSON stdout is the hook's decision envelope:
    its additionalContext and systemMessage arrive as their own attachments, so only plain-text
    stdout is counted here."""
    if attachment.get("type") == "hook_success":
        stdout = attachment.get("stdout")
        plain = 0 if _is_json_object(stdout) else text_length(stdout)
        return plain + text_length(attachment.get("content"))
    return text_length(attachment.get("content"))


def _hook_event(attachment: Mapping) -> str:
    event = attachment.get("hookEvent")
    if isinstance(event, str) and event:
        return event
    return str(attachment.get("hookName") or "").split(HOOK_NAME_SEPARATOR, 1)[0]


def _tool_uses(obj: Mapping) -> Iterator[Mapping]:
    content = (obj.get("message") or {}).get("content")
    if not isinstance(content, list):
        return
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            yield block


def _call_key(block: Mapping) -> tuple[str, str, str]:
    tool_input = block.get("input") if isinstance(block.get("input"), dict) else {}
    return tuple(str(tool_input.get(key) or MISSING) for key in ("subagent_type", "model", "effort"))


def _timings(obj: Mapping) -> Iterator[tuple[str, int]]:
    """(command, durationMs) pairs from a hook_success attachment or a stop hook summary. Stop
    and SubagentStop runs are taken from the summary only, which also lists hooks without output."""
    attachment = obj.get("attachment") if obj.get("type") == "attachment" else None
    if obj.get("type") == "system" and obj.get("subtype") == STOP_SUMMARY:
        infos = obj.get("hookInfos") if isinstance(obj.get("hookInfos"), list) else []
        entries = [info for info in infos if isinstance(info, dict)]
    elif (isinstance(attachment, dict) and attachment.get("type") == "hook_success"
          and _hook_event(attachment) not in STOP_EVENTS):
        entries = [attachment]
    else:
        entries = []
    for entry in entries:
        ms = _as_ms(entry.get("durationMs"))
        if ms is not None:
            yield str(entry.get("command") or MISSING), ms


def _instruction_files(attachment: Mapping) -> tuple[tuple[str, int], ...]:
    files = attachment.get("files") if isinstance(attachment.get("files"), list) else []
    pairs = [(str(f.get("path") or MISSING), text_length(f.get("content"))) for f in files if isinstance(f, dict)]
    return tuple(sorted(pairs, key=lambda pair: (-pair[1], pair[0])))


def scan_head(path: Path) -> HeadScan:
    """Fold the non-usage parts of a head transcript (usage comes from lapbar.transcripts)."""
    hooks: dict[str, HookStat] = {}
    hook_time: dict[str, TimeStat] = {}
    calls: Counter[tuple[str, str, str]] = Counter()
    seen_tool_uses: set[str] = set()
    instructions: tuple[tuple[str, int], ...] = ()
    loads = workflows = 0
    for obj in iter_objects(path):
        for command, ms in _timings(obj):
            hook_time[command] = hook_time.get(command, TimeStat()).add(ms)
        attachment = obj.get("attachment") if obj.get("type") == "attachment" else None
        if isinstance(attachment, dict) and attachment.get("type") in HOOK_ATTACHMENTS:
            name = str(attachment.get("hookName") or MISSING)
            hooks[name] = hooks.get(name, HookStat()).add(_hook_chars(attachment))
        elif isinstance(attachment, dict) and attachment.get("type") == "instructions":
            instructions, loads = _instruction_files(attachment), loads + 1
        for block in _tool_uses(obj) if obj.get("type") == "assistant" else ():
            block_id = block.get("id")
            if block_id is not None:
                if block_id in seen_tool_uses:
                    continue
                seen_tool_uses.add(block_id)
            if block.get("name") in SUBAGENT_TOOLS:
                calls[_call_key(block)] += 1
            elif block.get("name") == WORKFLOW_TOOL:
                workflows += 1
    return HeadScan(dict(hooks), dict(hook_time), instructions, loads, dict(calls), workflows)


def _usage_block(records: Iterable[Record]) -> dict:
    by_model: dict[str, dict[str, int]] = {}
    for record in records:
        row = by_model.get(record.model, {"requests": 0, **{key: 0 for key in USAGE_KEYS}})
        added = {key: row[key] + value for key, value in zip(USAGE_KEYS, record.tokens)}
        by_model = {**by_model, record.model: {"requests": row["requests"] + 1, **added}}
    ordered = sorted(by_model.items(), key=lambda item: (-item[1]["requests"], item[0]))
    return {
        "requests": sum(row["requests"] for row in by_model.values()),
        "by_model": {model: _ordered_usage(row) for model, row in ordered},
    }


def _ordered_usage(row: Mapping[str, int]) -> dict[str, int]:
    keys = ("requests", "input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
    return {key: row[key] for key in keys}


def _effort_counts(records: Iterable[Record]) -> dict[str, int]:
    counts = Counter(record.effort for record in records)
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def subagent_files(head: Path) -> tuple[Path, ...]:
    root = head.with_suffix("") / SUBAGENT_DIR
    return tuple(sorted(root.rglob(SUBAGENT_GLOB))) if root.is_dir() else ()


def _hooks_block(scan: HeadScan) -> tuple[dict, dict]:
    ordered = sorted(scan.hooks.items(), key=lambda item: (-item[1].chars, item[0]))
    total = HookStat(
        count=sum(stat.count for stat in scan.hooks.values()),
        chars=sum(stat.chars for stat in scan.hooks.values()),
        over=sum(stat.over for stat in scan.hooks.values()),
    )
    return {name: stat.as_dict() for name, stat in ordered}, total.as_dict()


def _calls_block(scan: HeadScan) -> list[dict]:
    ordered = sorted(scan.calls.items(), key=lambda item: (-item[1], item[0]))
    return [{"subagent_type": kind, "model": model, "effort": effort, "count": count}
            for (kind, model, effort), count in ordered]


def build_report(head: Path) -> dict:
    """Plain-data report for one session; the JSON output and the text renderer read this."""
    scan = scan_head(head)
    extra = subagent_files(head)
    records = tuple(iter_records(head.parent.parent, (head, *extra), None, None))
    head_records = [record for record in records if record.role == ROLE_HEAD]
    sub_records = [record for record in records if record.role != ROLE_HEAD]
    hooks, hook_totals = _hooks_block(scan)
    instruction_chars = sum(chars for _path, chars in scan.instructions)
    timing = sorted(scan.hook_time.items(), key=lambda item: (-item[1].total_ms, item[0]))
    return {
        "estimate": ESTIMATE_NOTE,
        "chars_per_token_est": CHARS_PER_TOKEN_EST,
        "scope": {"hooks": HOOKS_SCOPE},
        "session": {"id": head.stem, "project_dir": head.parent.name, "transcript": str(head),
                    "subagent_files": len(extra)},
        "instructions": {
            "loads": scan.instruction_loads,
            "total_chars": instruction_chars,
            "tokens_est": estimate_tokens(instruction_chars),
            "files": [{"path": path, "chars": chars} for path, chars in scan.instructions],
        },
        "hooks": hooks,
        "hook_totals": hook_totals,
        "hook_time": {command: stat.as_dict() for command, stat in timing},
        "subagent_calls": _calls_block(scan),
        "workflow_calls": scan.workflow_calls,
        "subagent_usage": {**_usage_block(sub_records), "by_effort": _effort_counts(sub_records)},
        "head_usage": _usage_block(head_records),
    }


def add_parser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "session",
        help="break down one session: hook injection, instructions, subagent calls, tokens",
        description="Read one session transcript (and its subagent transcripts) and report hook "
        "injection size, loaded instruction files, subagent calls by type x model x effort, and "
        f"token usage. With no transcript: the session in ${SESSION_ENV}, else the latest one.",
    )
    parser.add_argument("transcript", nargs="?", help="session head file (<projects-dir>/<project>/<session>.jsonl)")
    parser.add_argument("--projects-dir", default=DEFAULT_PROJECTS_DIR, help=f"default {DEFAULT_PROJECTS_DIR}")
    parser.add_argument("--latest", action="store_true", help="use the most recently modified session")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    parser.set_defaults(func=run)
    return parser


def head_files(projects_dir: Path) -> tuple[Path, ...]:
    """Session head files: <projects-dir>/<project>/<session>.jsonl (one level, no subagents)."""
    return tuple(
        path
        for project in sorted(p for p in projects_dir.iterdir() if p.is_dir())
        for path in sorted(project.glob(f"*{TRANSCRIPT_SUFFIX}"))
        if path.is_file()
    )


def latest_head(projects_dir: Path) -> Path | None:
    heads = head_files(projects_dir)
    return max(heads, key=lambda path: (path.stat().st_mtime, str(path))) if heads else None


def find_session(projects_dir: Path, session_id: str) -> Path | None:
    """The head file named <session_id>.jsonl in any project dir; the id must be a bare name."""
    if not session_id or session_id != Path(session_id).name or session_id.startswith("."):
        return None
    matches = [path for path in head_files(projects_dir) if path.name == session_id + TRANSCRIPT_SUFFIX]
    return max(matches, key=lambda path: path.stat().st_mtime) if matches else None


def resolve_transcript(args: argparse.Namespace) -> Path | str:
    """The head file to read, or an error message."""
    if args.transcript and args.latest:
        return "pass a transcript path or --latest, not both"
    if args.transcript:
        path = Path(args.transcript).expanduser().absolute()
        if not path.is_file():
            return f"transcript not found: {path}"
        if SUBAGENT_DIR in path.parts[:-1]:
            return f"not a session head file (it is a subagent transcript): {path}"
        return path
    projects_dir = Path(args.projects_dir).expanduser()
    if not projects_dir.is_dir():
        return f"projects dir not found: {projects_dir}"
    session_id = os.environ.get(SESSION_ENV, "")
    if session_id and not args.latest:
        found = find_session(projects_dir, session_id)
        return found or f"no transcript for session '{session_id}' under {projects_dir}; pass a path or --latest"
    latest = latest_head(projects_dir)
    return latest or f"no session transcripts under {projects_dir}"


def _fail(message: str) -> int:
    print(f"ERROR: {message}", file=sys.stderr)
    return EXIT_USAGE_ERROR


def run(args: argparse.Namespace) -> int:
    try:
        head = resolve_transcript(args)
    except OSError as exc:
        return _fail(f"cannot list transcripts: {exc}")
    if isinstance(head, str):
        return _fail(head)
    try:
        report = build_report(head)
    except OSError as exc:
        return _fail(f"cannot read transcript: {exc}")
    print(json.dumps(report, indent=2) if args.json else session_render.render_text(report))
    return 0
