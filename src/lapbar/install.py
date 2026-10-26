"""`lapbar install`: copy packaged assets into a Claude Code home and wire hooks.

Rules: never overwrite a user's differing file without --force, back up
settings.json before every write, and write nothing at all under --dry-run.
"""
from __future__ import annotations

import argparse
import stat
import sys
from dataclasses import dataclass
from pathlib import Path

from lapbar import assets as asset_mod
from lapbar import settings_wiring as wiring
from lapbar.assets import Asset, AssetRoot
from lapbar.backups import write_backup
from lapbar.tables import format_table

DEFAULT_TARGET = "~/.claude"
HEADERS = ("file", "status", "detail")

INSTALLED, SAME, DIFFERS, MISSING = "installed", "same", "differs", "missing-asset"
DRY, WIRED, SKIPPED, ERROR = "dry", "wired", "skipped", "error"
FAILING = frozenset({MISSING, ERROR})
HOME_PREFIX = b"~/.claude/"
# The target path is pasted unquoted into skills and hook commands, so it must
# not need shell quoting.
UNSAFE_TARGET_CHARS = frozenset(" \t\n\r'\"`$\\")


@dataclass(frozen=True)
class Row:
    item: str
    status: str
    detail: str = ""


@dataclass(frozen=True)
class InstallResult:
    rows: tuple[Row, ...]
    warnings: tuple[str, ...]
    backup: Path | None

    @property
    def exit_code(self) -> int:
        return 1 if any(r.status in FAILING for r in self.rows) else 0


def add_parser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    parser = subparsers.add_parser("install", help="install hooks, rules, skills and scripts")
    parser.add_argument("--target", default=DEFAULT_TARGET,
                        help="Claude Code home to install into (default: ~/.claude)")
    parser.add_argument("--only", default=None,
                        help=f"comma-separated categories: {','.join(asset_mod.CATEGORIES)}")
    parser.add_argument("--force", action="store_true", help="overwrite files that differ")
    parser.add_argument("--dry-run", action="store_true", help="show the plan, write nothing")
    parser.set_defaults(func=run)
    return parser


def run(args: argparse.Namespace) -> int:
    try:
        categories = asset_mod.parse_only(args.only)
    except ValueError as exc:
        print(f"lapbar install: {exc}", file=sys.stderr)
        return 2
    target = Path(args.target).expanduser().resolve()
    result = install(target, categories, force=args.force, dry_run=args.dry_run)
    for warning in result.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    title = "plan (dry run, nothing written)" if args.dry_run else "install"
    print(f"lapbar {title} -> {target}")
    print(format_table(HEADERS, [(r.item, r.status, r.detail) for r in result.rows]))
    if result.backup is not None:
        print(f"settings backup: {result.backup}")
    return result.exit_code


def install(target: Path, categories: tuple[str, ...] = asset_mod.CATEGORIES, *,
            force: bool = False, dry_run: bool = False,
            assets_root: AssetRoot | None = None) -> InstallResult:
    """Install the selected categories. `assets_root` lets tests inject fake assets."""
    # Absolute wiring: a relative target would make hooks depend on Claude's cwd.
    target = target.expanduser().resolve()
    unsafe = sorted(UNSAFE_TARGET_CHARS & set(str(target)))
    if unsafe:
        reason = f"target path contains {unsafe!r}; skills and hook commands embed it unquoted"
        return InstallResult((Row(str(target), ERROR, reason),), (), None)
    chosen = asset_mod.filter_assets(asset_mod.load_manifest(assets_root), categories)
    try:
        settings = wiring.load_settings(target)
    except wiring.SettingsError as exc:
        return InstallResult((Row(wiring.SETTINGS_NAME, ERROR, str(exc)),), (), None)
    file_rows = tuple(_install_file(a, assets_root, target, force, dry_run) for a in chosen)
    deployed = frozenset(a.dst for a, r in zip(chosen, file_rows) if _deployed(r, dry_run))
    hooks = tuple(a for a in chosen if a.is_hook)
    wire_rows, backup = _wire_hooks(hooks, deployed, settings, target, dry_run)
    warnings = tuple(f"{r.item} differs from the packaged copy: {r.detail}"
                     for r in file_rows if r.status == DIFFERS)
    return InstallResult(file_rows + wire_rows, warnings, backup)


def _deployed(row: Row, dry_run: bool) -> bool:
    """Whether the target file holds (or would hold) the packaged content."""
    return row.status in (INSTALLED, SAME) or (dry_run and row.status == DRY)


def _install_file(asset: Asset, root: AssetRoot | None, target: Path,
                  force: bool, dry_run: bool) -> Row:
    packaged = asset_mod.read_asset(asset, root)
    if packaged is None:
        return Row(asset.dst, MISSING, f"asset {asset.src} is not packaged")
    payload = render_payload(asset, packaged, target)
    dest = target / asset.dst
    if dest.is_symlink():  # checked first so dangling links are caught too
        return Row(asset.dst, DIFFERS, "symlink, left untouched")
    if dest.exists() and not dest.is_file():
        return Row(asset.dst, DIFFERS, "not a regular file, left untouched")
    exists = dest.is_file()
    if exists and dest.read_bytes() == payload:
        return Row(asset.dst, SAME)
    if exists and not force:
        return Row(asset.dst, DIFFERS, "kept existing file (use --force)")
    if dry_run:
        return Row(asset.dst, DRY, "would back up and overwrite" if exists else "would create")
    backup = write_backup(dest) if exists else None
    _write_file(dest, payload, executable=asset.category == "hooks")
    detail = f"overwritten (--force), backup {backup.name}" if backup else ""
    return Row(asset.dst, INSTALLED, detail)


def render_payload(asset: Asset, payload: bytes, target: Path) -> bytes:
    """Skills reference scripts as `~/.claude/...`; pin them to the install target,
    since the Workflow tool is not guaranteed to expand `~`."""
    if asset.category != "skills":
        return payload
    return payload.replace(HOME_PREFIX, (str(target) + "/").encode("utf-8"))


def _write_file(dest: Path, payload: bytes, *, executable: bool) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(payload)
    if executable:
        mode = dest.stat().st_mode
        dest.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _wire_hooks(hooks: tuple[Asset, ...], deployed: frozenset[str], settings: dict,
                target: Path, dry_run: bool) -> tuple[tuple[Row, ...], Path | None]:
    rows: list[Row] = []
    current, changed = settings, False
    for hook in hooks:
        label = f"{wiring.SETTINGS_NAME} [{hook.event} {hook.matcher or '*'}] {hook.dst}"
        if hook.dst not in deployed:
            rows.append(Row(label, SKIPPED, "hook file not installed"))
            continue
        command = wiring.hook_command(target, hook.dst)
        current, added = wiring.merge_hook(current, hook.event, hook.matcher, command)
        changed = changed or added
        status = (DRY if dry_run else WIRED) if added else SAME
        rows.append(Row(label, status, "would wire" if added and dry_run else ""))
    backup = None
    if changed and not dry_run:
        backup = wiring.write_settings(target, current)
    return tuple(rows), backup
