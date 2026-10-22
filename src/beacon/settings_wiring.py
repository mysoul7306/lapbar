"""settings.json hook wiring: pure merge helpers plus backup-then-write I/O.

Shape written per hook (Claude Code settings schema):
    {"hooks": {"<event>": [{"matcher": "<m>", "hooks": [{"type": "command", "command": "..."}]}]}}
Entries without a matcher (SessionStart) omit the "matcher" key.
"""
from __future__ import annotations

import json
import os
import shlex
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

SETTINGS_NAME = "settings.json"
# settings.json may carry env tokens: new files and backups are owner-only.
DEFAULT_MODE = 0o600
BACKUP_MODE = 0o600


class SettingsError(Exception):
    """settings.json exists but cannot be safely read or merged."""


def hook_command(target: Path, dst: str) -> str:
    """Command string wired into settings.json; doctor looks for the same string."""
    return f"python3 {shlex.quote(str(target / dst))}"


def load_settings(target: Path) -> dict[str, Any]:
    path = target / SETTINGS_NAME
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
    except (OSError, json.JSONDecodeError) as exc:
        raise SettingsError(f"cannot parse {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise SettingsError(f"{path} must contain a JSON object")
    return data


def _event_entries(settings: dict[str, Any], event: str) -> list[Any]:
    hooks = settings.get("hooks", {})
    if not isinstance(hooks, dict):
        raise SettingsError("settings.json 'hooks' must be an object")
    entries = hooks.get(event, [])
    if not isinstance(entries, list):
        raise SettingsError(f"settings.json hooks.{event} must be a list")
    return entries


def _commands_of(entry: Any) -> list[str]:
    if not isinstance(entry, dict) or not isinstance(entry.get("hooks"), list):
        return []
    return [h.get("command") for h in entry["hooks"] if isinstance(h, dict)]


def is_wired(settings: dict[str, Any], event: str, command: str) -> bool:
    """True when `command` appears in any entry under hooks[event]."""
    return any(command in _commands_of(e) for e in _event_entries(settings, event))


def _same_matcher(entry: Any, matcher: str | None) -> bool:
    return isinstance(entry, dict) and (entry.get("matcher") or None) == matcher \
        and isinstance(entry.get("hooks"), list)


def _new_entry(matcher: str | None, command: str) -> dict[str, Any]:
    spec = {"type": "command", "command": command}
    return {"hooks": [spec]} if matcher is None else {"matcher": matcher, "hooks": [spec]}


def merge_hook(settings: dict[str, Any], event: str, matcher: str | None,
               command: str) -> tuple[dict[str, Any], bool]:
    """Return (new settings, changed). Never mutates the input mapping."""
    entries = _event_entries(settings, event)
    if is_wired(settings, event, command):
        return settings, False
    spec = {"type": "command", "command": command}
    index = next((i for i, e in enumerate(entries) if _same_matcher(e, matcher)), None)
    if index is None:
        new_entries = [*entries, _new_entry(matcher, command)]
    else:
        old = entries[index]
        updated = {**old, "hooks": [*old["hooks"], spec]}
        new_entries = [*entries[:index], updated, *entries[index + 1:]]
    new_hooks = {**settings.get("hooks", {}), event: new_entries}
    return {**settings, "hooks": new_hooks}, True


def _backup_candidates(path: Path, now: datetime):
    base = path.with_name(f"{path.name}.bak-{now.strftime('%Y%m%d-%H%M%S')}")
    yield base
    n = 1
    while True:
        yield base.with_name(f"{base.name}-{n}")
        n += 1


def _write_backup(path: Path, mode: int, now: datetime) -> Path:
    """Copy `path` to a fresh owner-only backup; O_EXCL never reuses a name."""
    payload = path.read_bytes()
    backup_mode = BACKUP_MODE & mode  # follow the original if it is narrower
    for candidate in _backup_candidates(path, now):
        try:
            fd = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL, backup_mode)
        except FileExistsError:
            continue
        with os.fdopen(fd, "wb") as fh:
            os.fchmod(fh.fileno(), backup_mode)  # exact mode regardless of umask
            fh.write(payload)
        return candidate
    raise AssertionError("unreachable: candidate generator is infinite")


def write_settings(target: Path, settings: dict[str, Any],
                   now: datetime | None = None) -> Path | None:
    """Back up the existing file (if any), then write atomically. Returns the backup path."""
    path = target / SETTINGS_NAME
    target.mkdir(parents=True, exist_ok=True)
    backup, mode = None, DEFAULT_MODE
    if path.exists():
        mode = path.stat().st_mode & 0o777
        backup = _write_backup(path, mode, now or datetime.now())
    text = json.dumps(settings, indent=2, ensure_ascii=False) + "\n"
    fd, tmp = tempfile.mkstemp(prefix=".settings.", suffix=".tmp", dir=target)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return backup
