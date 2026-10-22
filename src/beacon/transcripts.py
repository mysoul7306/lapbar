"""Stream Claude Code transcript files and yield one usage record per API request."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator

ASSISTANT_MARKER = b'"type":"assistant"'
SYNTHETIC_MODEL = "<synthetic>"
SUBAGENT_DIR = "subagents"
WORKFLOW_DIR = "workflows"
ROLE_HEAD = "head"
ROLE_SUBAGENT = "subagent"
EFFORT_UNSET = "unset"
TOKEN_KEYS = (
    "input_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
    "output_tokens",
)


@dataclass(frozen=True)
class Record:
    model: str
    role: str
    workflow: bool
    session: str
    effort: str
    timestamp: str
    tokens: tuple[int, int, int, int]

    def usage(self) -> dict[str, int]:
        return dict(zip(TOKEN_KEYS, self.tokens))


@dataclass(frozen=True)
class FileOrigin:
    role: str
    workflow: bool
    session: str


@dataclass(frozen=True)
class FileListing:
    files: tuple[Path, ...]
    skipped_by_mtime: int


def classify(relative: Path) -> FileOrigin:
    """Classify a transcript by its path relative to the projects dir.

    <project>/<session>.jsonl                              -> head
    <project>/<session>/subagents/agent-*.jsonl            -> subagent
    <project>/<session>/subagents/workflows/*/agent-*.jsonl -> subagent + workflow
    """
    parts = relative.parts
    if SUBAGENT_DIR in parts[:-1]:
        index = parts.index(SUBAGENT_DIR)
        session = "/".join(parts[:index]) or relative.stem
        workflow = WORKFLOW_DIR in parts[index + 1:-1]
        return FileOrigin(role=ROLE_SUBAGENT, workflow=workflow, session=session)
    return FileOrigin(role=ROLE_HEAD, workflow=False, session="/".join(parts[:-1] + (relative.stem,)))


def _since_epoch(since: str | None) -> float | None:
    if not since:
        return None
    return datetime.strptime(since, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()


def list_transcripts(projects_dir: Path, since: str | None) -> FileListing:
    """All *.jsonl under <projects_dir>/*/; files last modified before `since` cannot hold
    newer records, so they are left unopened."""
    cutoff = _since_epoch(since)
    kept: list[Path] = []
    skipped = 0
    for project in sorted(p for p in projects_dir.iterdir() if p.is_dir()):
        for root, _dirs, names in os.walk(project):
            for name in names:
                if not name.endswith(".jsonl"):
                    continue
                path = Path(root, name)
                if cutoff is not None and path.stat().st_mtime < cutoff:
                    skipped += 1
                    continue
                kept.append(path)
    return FileListing(files=tuple(sorted(kept)), skipped_by_mtime=skipped)


def _parse_assistant(raw: bytes) -> dict | None:
    if ASSISTANT_MARKER not in raw:
        return None
    try:
        obj = json.loads(raw)
    except ValueError:
        return None
    return obj if isinstance(obj, dict) and obj.get("type") == "assistant" else None


def _in_range(timestamp: str, since: str | None, until: str | None) -> bool:
    day = timestamp[:10]
    if not day:
        return since is None and until is None
    return not ((since and day < since) or (until and day > until))


def to_record(obj: dict, origin: FileOrigin, since: str | None, until: str | None) -> Record | None:
    message = obj.get("message") or {}
    model = message.get("model") or ""
    if not model or model == SYNTHETIC_MODEL:
        return None
    timestamp = obj.get("timestamp") or ""
    if not _in_range(timestamp, since, until):
        return None
    usage = message.get("usage") or {}
    return Record(
        model=model,
        role=origin.role,
        workflow=origin.workflow,
        session=origin.session,
        effort=obj.get("effort") or EFFORT_UNSET,
        timestamp=timestamp,
        tokens=tuple(int(usage.get(key) or 0) for key in TOKEN_KEYS),
    )


def _request_key(obj: dict) -> str | None:
    return obj.get("requestId") or (obj.get("message") or {}).get("id")


def _read_file(path: Path, origin: FileOrigin, since: str | None, until: str | None) -> Iterator[tuple[str | None, Record]]:
    """Per file, keep one record per request: streaming writes one assistant record per
    content block and only the final one carries the full output_tokens, so keep the max."""
    by_request: dict[str, Record] = {}
    with path.open("rb") as handle:
        for raw in handle:
            obj = _parse_assistant(raw)
            if obj is None:
                continue
            record = to_record(obj, origin, since, until)
            if record is None:
                continue
            key = _request_key(obj)
            if key is None:
                yield None, record
                continue
            previous = by_request.get(key)
            if previous is None or record.tokens[3] >= previous.tokens[3]:
                # Local accumulator that never escapes this generator; in-place for speed.
                by_request[key] = record
    yield from by_request.items()


def iter_records(projects_dir: Path, files: Iterable[Path], since: str | None, until: str | None) -> Iterator[Record]:
    """Yield deduplicated records across all files (first file wins on a cross-file repeat)."""
    seen: set[str] = set()
    for path in files:
        origin = classify(path.relative_to(projects_dir))
        for key, record in _read_file(path, origin, since, until):
            if key is not None:
                if key in seen:
                    continue
                seen.add(key)
            yield record
