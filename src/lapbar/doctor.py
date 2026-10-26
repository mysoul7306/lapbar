"""`lapbar doctor`: verify assets exist, hooks are wired and hook self-tests pass."""
from __future__ import annotations

import argparse
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from lapbar import assets as asset_mod
from lapbar import settings_wiring as wiring
from lapbar.assets import Asset, AssetRoot
from lapbar.tables import format_table

DEFAULT_TARGET = "~/.claude"
HEADERS = ("check", "item", "result", "detail")
PASS, FAIL = "PASS", "FAIL"
SELFTEST_TIMEOUT_SECONDS = 60
DETAIL_MAX = 80


@dataclass(frozen=True)
class Check:
    kind: str
    item: str
    result: str
    detail: str = ""


def add_parser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    parser = subparsers.add_parser("doctor", help="check installed assets, wiring and hook self-tests")
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
    """Run every check. `python` overrides the interpreter used for self-tests."""
    target = target.expanduser().resolve()  # same absolute form install wires
    manifest = asset_mod.load_manifest(assets_root)
    hooks = tuple(a for a in manifest if a.is_hook)
    exist = tuple(_check_exists(a, target) for a in manifest)
    wired = _check_wiring(hooks, target)
    interpreter = python or shutil.which("python3")
    selftests = tuple(_check_selftest(h, target, interpreter) for h in hooks if h.selftest)
    return exist + wired + selftests


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


def _last_line(proc: subprocess.CompletedProcess) -> str:
    lines = [ln for ln in (proc.stderr or proc.stdout or "").splitlines() if ln.strip()]
    return lines[-1].strip() if lines else "no output"


def _short(text: str) -> str:
    return text if len(text) <= DETAIL_MAX else text[: DETAIL_MAX - 3] + "..."

