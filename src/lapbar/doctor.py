"""`lapbar doctor`: verify assets exist, hooks are wired, every settings.json hook
command points at real files, and hooks pass their self-tests and probes.

A probe feeds a hook a sample stdin payload from the manifest and checks that the
hook really answers `deny` or `allow`, so a guard that silently stopped guarding
shows up as a failure.
"""
from __future__ import annotations

import argparse
import json
import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from itertools import dropwhile, groupby, takewhile
from pathlib import Path

from lapbar import assets as asset_mod
from lapbar import settings_wiring as wiring
from lapbar.assets import Asset, AssetRoot, Probe
from lapbar.tables import format_table

DEFAULT_TARGET = "~/.claude"
HEADERS = ("check", "item", "result", "detail")
PASS, FAIL = "PASS", "FAIL"
SELFTEST_TIMEOUT_SECONDS = 60
PROBE_TIMEOUT_SECONDS = 10
DETAIL_MAX = 80
PYTHON_LAUNCHER = "python3"
# Claude Code hook protocol: exit 2 blocks the tool call; exit 0 may carry a JSON reply.
HOOK_BLOCK_EXIT = 2
DECISION_ALLOW, DECISION_DENY = "allow", "deny"
NOT_JSON_NOTE = "stdout is not a single JSON object"
HOME_TOKENS = ("~", "$HOME", "${HOME}")
PYTHON_SUFFIX = ".py"
# Settings check: in every simple command of a hook command, the executed file is its first
# word, or the first operand after an interpreter (`uv` only after `run`, `env` is skipped).
PYTHON_KIND, SHELL_KIND, NODE_KIND, UV_KIND = "python", "shell", "node", "uv"
PYTHON_NAME = re.compile(r"python[0-9.]*")
SHELL_NAMES = frozenset({"bash", "sh", "zsh"})
ENV_LAUNCHER, UV_RUN = "env", "run"
# Flags after which inline code or a module follows instead of a script file. A shell's `-e`
# is errexit, so a script still follows it.
INLINE_CODE_FLAGS = {
    PYTHON_KIND: frozenset({"-c", "-m"}),
    NODE_KIND: frozenset({"-e", "-p", "--eval"}),
    SHELL_KIND: frozenset({"-c"}),
    UV_KIND: frozenset({"-m", "--module"}),
}
SHELL_BUNDLED_CODE_FLAG = re.compile(r"-[A-Za-z]*c[A-Za-z]*")  # -lc, -ec, -ic
ENV_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
SHELL_OPERATOR_CHARS = frozenset("();<>|&")
REDIRECT_CHARS = frozenset("<>")
COMMENT_START = "#"
QUOTED_HOME_NOTE = "quoted ~ is not expanded by the shell"


@dataclass(frozen=True)
class Check:
    kind: str
    item: str
    result: str
    detail: str = ""


def add_parser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "doctor", help="check installed assets, wiring, settings hook commands, self-tests and probes")
    parser.add_argument("--target", default=DEFAULT_TARGET,
                        help="Claude Code home to check (default: ~/.claude)")
    parser.set_defaults(func=run)
    return parser


def run(args: argparse.Namespace) -> int:
    target = Path(args.target).expanduser().resolve()
    checks = diagnose(target)
    print(f"lapbar doctor -> {target}")
    print(format_table(HEADERS, [(c.kind, c.item, c.result, c.detail) for c in checks]))
    failed = sum(c.result == FAIL for c in checks)
    print(f"{len(checks) - failed} passed, {failed} failed")
    return exit_code(checks)


def exit_code(checks: tuple[Check, ...]) -> int:
    return 1 if any(c.result == FAIL for c in checks) else 0


def diagnose(target: Path, assets_root: AssetRoot | None = None,
             python: str | None = None) -> tuple[Check, ...]:
    """Run every check. `python` overrides the interpreter used for self-tests and probes."""
    target = target.expanduser().resolve()  # same absolute form install wires
    manifest = asset_mod.load_manifest(assets_root)
    hooks = tuple(a for a in manifest if a.is_hook)
    exist = tuple(_check_exists(a, target) for a in manifest)
    wired = _check_wiring(tuple(h for h in hooks if h.is_wired_hook), target)
    settings = check_settings(target)
    interpreter = python or shutil.which(PYTHON_LAUNCHER)
    selftests = tuple(_check_selftest(h, target, interpreter) for h in hooks if h.selftest)
    probes = tuple(c for h in hooks for c in check_probes(h, target, interpreter))
    return exist + wired + settings + selftests + probes


def _check_exists(asset: Asset, target: Path) -> Check:
    found = (target / asset.dst).is_file()
    return Check("asset", asset.dst, PASS if found else FAIL, "" if found else "missing")


def _check_wiring(hooks: tuple[Asset, ...], target: Path) -> tuple[Check, ...]:
    try:
        settings = wiring.load_settings(target)
    except wiring.SettingsError as exc:
        return (Check("wiring", wiring.SETTINGS_NAME, FAIL, _short(str(exc))),)
    return tuple(_wiring_check(h, settings, target) for h in hooks)


def _wiring_check(hook: Asset, settings: dict, target: Path) -> Check:
    command = wiring.hook_command(target, hook.dst)
    ok = wiring.is_wired(settings, hook.event, hook.matcher, command)
    where = f"hooks.{hook.event} [{hook.matcher or '*'}]"
    detail = where if ok else f"command not found under {where}"
    return Check("wiring", hook.dst, PASS if ok else FAIL, _short(detail))


def _check_selftest(hook: Asset, target: Path, interpreter: str | None) -> Check:
    path = target / hook.dst
    if interpreter is None:
        return Check("selftest", hook.dst, FAIL, "python3 not found on PATH")
    if not path.is_file():
        return Check("selftest", hook.dst, FAIL, "hook file missing")
    try:
        proc = subprocess.run([interpreter, str(path), "--self-test"], stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, timeout=SELFTEST_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        return Check("selftest", hook.dst, FAIL, f"timed out after {SELFTEST_TIMEOUT_SECONDS}s")
    if proc.returncode == 0:
        return Check("selftest", hook.dst, PASS)
    return Check("selftest", hook.dst, FAIL, _short(f"exit {proc.returncode}: {_last_line(proc)}"))


def check_probes(hook: Asset, target: Path, interpreter: str | None) -> tuple[Check, ...]:
    """Run each manifest probe through the hook exactly as settings.json would launch it."""
    return tuple(_run_probe(hook, probe, target, interpreter) for probe in hook.probes)


def _probe_argv(hook: Asset, target: Path, interpreter: str) -> list[str]:
    argv = shlex.split(wiring.hook_command(target, hook.dst))
    if argv and argv[0] == PYTHON_LAUNCHER:
        return [interpreter, *argv[1:]]
    return argv


def _run_probe(hook: Asset, probe: Probe, target: Path, interpreter: str | None) -> Check:
    item = f"{hook.dst} :: {probe.name}"
    if interpreter is None:
        return Check("probe", item, FAIL, "python3 not found on PATH")
    if not (target / hook.dst).is_file():
        return Check("probe", item, FAIL, "hook file missing")
    try:
        proc = subprocess.run(_probe_argv(hook, target, interpreter), input=json.dumps(probe.input),
                              capture_output=True, text=True, timeout=PROBE_TIMEOUT_SECONDS,
                              cwd=target)
    except subprocess.TimeoutExpired:
        return Check("probe", item, FAIL, f"timed out after {PROBE_TIMEOUT_SECONDS}s")
    except OSError as exc:
        return Check("probe", item, FAIL, f"cannot run hook: {type(exc).__name__}")
    decision, error, note = _decision(proc)
    if error is not None:
        return Check("probe", item, FAIL, _short(error))
    suffix = f" ({note})" if note else ""
    if decision == probe.expect:
        return Check("probe", item, PASS, _short(decision + suffix))
    return Check("probe", item, FAIL, _short(f"expected {probe.expect}, got {decision}{suffix}"))


def _decision(proc: subprocess.CompletedProcess) -> tuple[str | None, str | None, str]:
    """(decision, error, note). Exit 2 blocks; exit 0 answers with JSON or allows by silence."""
    if proc.returncode == HOOK_BLOCK_EXIT:
        return DECISION_DENY, None, ""
    if proc.returncode != 0:
        return None, f"exit {proc.returncode}: {_last_line(proc)}", ""
    reply, note = _reply(proc.stdout or "")
    output = reply.get("hookSpecificOutput") if reply else None
    decision = output.get("permissionDecision") if isinstance(output, dict) else None
    return (decision if isinstance(decision, str) else DECISION_ALLOW), None, note


def _reply(stdout: str) -> tuple[dict | None, str]:
    """Claude Code reads stdout as one JSON object; anything else carries no decision."""
    text = stdout.strip()
    if not text:
        return None, ""
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return None, NOT_JSON_NOTE
    return (value, "") if isinstance(value, dict) else (None, NOT_JSON_NOTE)


def check_settings(target: Path) -> tuple[Check, ...]:
    """Every hook command in settings.json, lapbar's or not, must point at real files."""
    try:
        settings = wiring.load_settings(target)
    except wiring.SettingsError as exc:
        return (Check("settings", wiring.SETTINGS_NAME, FAIL, _short(str(exc))),)
    commands = _all_commands(settings)
    bad = tuple(Check("settings", short_tail(repr(c)), FAIL, "command is not a string")
                for c in commands if not isinstance(c, str))
    unique = tuple(dict.fromkeys(c for c in commands if isinstance(c, str)))
    return tuple(_settings_check(c) for c in unique) + bad


def _all_commands(settings: dict) -> tuple[object, ...]:
    hooks = settings.get("hooks") or {}
    return tuple(
        spec["command"]
        for entries in hooks.values() for entry in entries
        if isinstance(entry, dict) and isinstance(entry.get("hooks"), list)
        for spec in entry["hooks"] if isinstance(spec, dict) and "command" in spec
    )


def _settings_check(command: str) -> Check:
    item = short_tail(command)
    try:
        tokens = split_command(command)
    except ValueError:
        return Check("settings", item, FAIL, "cannot split command (unclosed quote)")
    targets = exec_targets(tokens)
    argument_paths = sum(_is_path_candidate(t) and t not in targets for t in tokens)
    quoted = [t for t in targets if _is_quoted_home(command, t)]
    expanded = [(t, _expand_home(t)) for t in targets if t not in quoted]
    unchecked = [t for t, path in expanded if "$" in path or not path.startswith("/")]
    checked = [(t, Path(path)) for t, path in expanded if t not in unchecked]
    problems = ([f"{QUOTED_HOME_NOTE}: {t}" for t in quoted]
                + [p for p in (_path_problem(t, path) for t, path in checked) if p])
    if problems:
        return Check("settings", item, FAIL, "; ".join(problems))
    notes = unchecked + ([f"{argument_paths} argument path(s)"] if argument_paths else [])
    detail = f"{len(checked)} path(s) ok" if checked else "no file paths"
    if notes:
        detail += f", not checked: {', '.join(notes)}"
    return Check("settings", item, PASS, _short(detail))


def split_command(command: str) -> list[str]:
    """Shell-like split that also cuts `>>`, `|`, `;` and `&&` out of the words around them.

    A `#` inside a word is a plain character; a word that starts with `#` begins a comment,
    and the rest of the command is dropped (quoting is not tracked for that word).
    """
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    return list(takewhile(lambda t: not t.startswith(COMMENT_START), lexer))


def exec_targets(tokens: list[str]) -> tuple[str, ...]:
    """The files a hook command runs: in each simple command, its first word when that is a
    path, or the script an interpreter is given. Arguments and redirect targets are not."""
    found = (t for words in simple_commands(tokens) for t in _command_targets(words))
    return tuple(dict.fromkeys(found))


def simple_commands(tokens: list[str]) -> tuple[tuple[str, ...], ...]:
    """Words of each simple command between control operators, redirections removed
    (the operator, its target, and an fd number right before it)."""
    redirects = [i for i, t in enumerate(tokens) if _is_redirect(t)]
    fds = {i - 1 for i in redirects if i > 0 and tokens[i - 1].isdigit()}
    dropped = fds | {j for i in redirects for j in (i, i + 1)}
    kept = [t for i, t in enumerate(tokens) if i not in dropped]
    return tuple(tuple(group) for is_op, group in groupby(kept, key=_is_operator) if not is_op)


def _command_targets(words: tuple[str, ...]) -> tuple[str, ...]:
    words = tuple(dropwhile(lambda t: ENV_ASSIGNMENT.match(t) is not None, words))
    if not words:
        return ()
    head, args = words[0], list(words[1:])
    own = (head,) if _is_path_candidate(head) else ()
    name = Path(head).name
    if name == ENV_LAUNCHER:
        rest = dropwhile(lambda t: t.startswith("-") or ENV_ASSIGNMENT.match(t) is not None, args)
        return own + _command_targets(tuple(rest))
    kind = _interpreter_kind(name)
    if kind is None:
        return own
    if kind == UV_KIND:
        args = args[args.index(UV_RUN) + 1:] if UV_RUN in args else []
    script = _first_operand(args, kind)
    return own + ((script,) if script is not None and _is_path_candidate(script) else ())


def _interpreter_kind(name: str) -> str | None:
    if PYTHON_NAME.fullmatch(name):
        return PYTHON_KIND
    if name in SHELL_NAMES:
        return SHELL_KIND
    return name if name in (NODE_KIND, UV_KIND) else None


def _first_operand(args: list[str], kind: str) -> str | None:
    for token in args:
        if _is_inline_code_flag(token, kind):
            return None
        if not token.startswith("-"):
            return token
    return None


def _is_inline_code_flag(token: str, kind: str) -> bool:
    if token in INLINE_CODE_FLAGS[kind]:
        return True
    return kind == SHELL_KIND and SHELL_BUNDLED_CODE_FLAG.fullmatch(token) is not None


def _is_redirect(token: str) -> bool:
    return _is_operator(token) and any(ch in REDIRECT_CHARS for ch in token)


def _is_operator(token: str) -> bool:
    return bool(token) and all(ch in SHELL_OPERATOR_CHARS for ch in token)


def _is_path_candidate(token: str) -> bool:
    if token.startswith("-") or "://" in token:
        return False
    return "/" in token or token.startswith(HOME_TOKENS)


def _is_quoted_home(command: str, token: str) -> bool:
    """The shell expands neither a quoted `~` nor a single-quoted `$HOME`."""
    if not token.startswith(HOME_TOKENS):
        return False
    quotes = "\"'" if token.startswith("~") else "'"
    return any(quote + token in command for quote in quotes)


def _expand_home(token: str) -> str:
    for prefix in HOME_TOKENS:
        if token == prefix or token.startswith(prefix + "/"):
            return str(Path.home()) + token[len(prefix):]
    return token


def _path_problem(token: str, path: Path) -> str | None:
    """Why this path breaks the hook command, or None when it is usable."""
    if not path.exists():
        return f"missing: {token}"
    if path.suffix != PYTHON_SUFFIX or not path.is_file():
        return None
    try:
        compile(path.read_text(encoding="utf-8"), str(path), "exec")
    except SyntaxError as exc:
        return f"syntax error: {token}:{exc.lineno}"
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        return f"unreadable: {token} ({type(exc).__name__})"
    return None


def _last_line(proc: subprocess.CompletedProcess) -> str:
    lines = [ln for ln in (proc.stderr or proc.stdout or "").splitlines() if ln.strip()]
    return lines[-1].strip() if lines else "no output"


def _short(text: str) -> str:
    return text if len(text) <= DETAIL_MAX else text[: DETAIL_MAX - 3] + "..."


def short_tail(text: str) -> str:
    """Keep the end of a long command: the script name is what tells rows apart."""
    return text if len(text) <= DETAIL_MAX else "..." + text[-(DETAIL_MAX - 3):]
