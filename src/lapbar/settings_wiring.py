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

from lapbar.backups import write_backup

SETTINGS_NAME = "settings.json"
# settings.json may carry env tokens: a newly created file is owner-only.
DEFAULT_MODE = 0o600
HOME_PREFIXES = ("~/", "$HOME/", "${HOME}/")


class SettingsError(Exception):
    """settings.json exists but cannot be safely read or merged."""


def hook_command(target: Path, dst: str) -> str:
    """Command string wired into settings.json; doctor looks for the same string."""
    return f"python3 {shlex.quote(str(target / dst))}"


def _check_shape(data: Any, path: Path) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise SettingsError(f"{path} must contain a JSON object")
    if "hooks" not in data:
        return data
    hooks = data["hooks"]
    if not isinstance(hooks, dict):
        raise SettingsError(f"{path}: 'hooks' must be an object, got {type(hooks).__name__}")
    bad = sorted(event for event, entries in hooks.items() if not isinstance(entries, list))
    if bad:
        raise SettingsError(f"{path}: hooks.{bad[0]} must be a list")
    return data


def load_settings(target: Path) -> dict[str, Any]:
    """Read and shape-check settings.json so callers fail before touching any file."""
    path = target / SETTINGS_NAME
    if path.is_symlink() and not path.exists():
        raise SettingsError(f"{path} is a broken symlink")
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
    except (OSError, json.JSONDecodeError) as exc:
        raise SettingsError(f"cannot parse {path}: {exc}") from exc
    return _check_shape(data, path)


def _event_entries(settings: dict[str, Any], event: str) -> list[Any]:
    # Shape is validated by load_settings; the fallback only covers in-memory dicts.
    hooks = settings.get("hooks") or {}
    entries = hooks.get(event, []) if isinstance(hooks, dict) else []
    return entries if isinstance(entries, list) else []


def _commands_of(entry: Any) -> list[str]:
    if not isinstance(entry, dict) or not isinstance(entry.get("hooks"), list):
        return []
    return [h.get("command") for h in entry["hooks"] if isinstance(h, dict)]


def _normalize_token(token: str, home: str) -> str:
    for prefix in HOME_PREFIXES:
        if token.startswith(prefix):
            token = f"{home}/{token[len(prefix):]}"
            break
    return os.path.realpath(token) if token.startswith("/") else token


def _normalize_command(command: Any, home: str) -> tuple[str, ...] | None:
    """Tokenize and expand `~/`, `$HOME/` so hand-written wiring compares equal."""
    if not isinstance(command, str):
        return None
    try:
        tokens = shlex.split(command)
    except ValueError:
        return (command,)
    return tuple(_normalize_token(t, home) for t in tokens)


def _same_matcher(entry: Any, matcher: str | None) -> bool:
    return isinstance(entry, dict) and (entry.get("matcher") or None) == matcher \
        and isinstance(entry.get("hooks"), list)


def is_wired(settings: dict[str, Any], event: str, matcher: str | None, command: str) -> bool:
    """True when an entry with the same matcher already runs an equivalent command."""
    home = str(Path.home())
    wanted = _normalize_command(command, home)
    return any(
        _normalize_command(existing, home) == wanted
        for entry in _event_entries(settings, event) if _same_matcher(entry, matcher)
        for existing in _commands_of(entry)
    )


def _new_entry(matcher: str | None, command: str) -> dict[str, Any]:
    spec = {"type": "command", "command": command}
    return {"hooks": [spec]} if matcher is None else {"matcher": matcher, "hooks": [spec]}


def merge_hook(settings: dict[str, Any], event: str, matcher: str | None,
               command: str) -> tuple[dict[str, Any], bool]:
    """Return (new settings, changed). Never mutates the input mapping."""
    if is_wired(settings, event, matcher, command):
        return settings, False
    entries = _event_entries(settings, event)
    spec = {"type": "command", "command": command}
    index = next((i for i, e in enumerate(entries) if _same_matcher(e, matcher)), None)
    if index is None:
        new_entries = [*entries, _new_entry(matcher, command)]
    else:
        old = entries[index]
        updated = {**old, "hooks": [*old["hooks"], spec]}
        new_entries = [*entries[:index], updated, *entries[index + 1:]]
    new_hooks = {**(settings.get("hooks") or {}), event: new_entries}
    return {**settings, "hooks": new_hooks}, True


def write_settings(target: Path, settings: dict[str, Any],
                   now: datetime | None = None) -> Path | None:
    """Back up the existing file (if any), then write atomically. Returns the backup path.

    A symlinked settings.json is written through to its real file so the link survives.
    """
    link = target / SETTINGS_NAME
    target.mkdir(parents=True, exist_ok=True)
    path = link.resolve() if link.is_symlink() else link
    backup, mode = None, DEFAULT_MODE
    if path.exists():
        mode = path.stat().st_mode & 0o777
        backup = write_backup(link, now)
    text = json.dumps(settings, indent=2, ensure_ascii=False) + "\n"
    fd, tmp = tempfile.mkstemp(prefix=".settings.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return backup
