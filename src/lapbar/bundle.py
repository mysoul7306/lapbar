"""`lapbar bundle` (maintainer only): build the public assets from a live `~/.claude`.

    lapbar bundle --from ~/.claude            # scrub -> scan -> self-test -> write src/lapbar/assets/
    lapbar bundle --check                     # scan the asset directory only (CI gate)
    lapbar bundle --from ~/.claude --preview DIR   # also write scrubbed copies to DIR for review
    lapbar bundle --from ~/.claude --map FILE      # use a substitution table kept outside the repo

Fail-closed: nothing is written to the asset directory unless the in-memory scan is clean and every
hook that ships a `--self-test` passes on the scrubbed copy.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from lapbar import scrub

ASSETS_DIR = Path(__file__).with_name("assets")
MANIFEST_NAME = "manifest.json"
SELF_TEST_FLAG = "--self-test"
SELF_TEST_TIMEOUT = 120
HOME_FORM = "~/.claude/"
# Asset folders whose `~/.claude/` references `lapbar install` pins to the absolute target.
RENDERED_PREFIXES = ("agents/", "commands/")

# Fallback when assets/manifest.json does not exist yet: (asset path, path in the live ~/.claude).
# The live layout equals the install layout, so both sides are the same relative path.
DEFAULT_ASSETS: tuple[str, ...] = (
    "hooks/subagent-model-guard.py",
    "hooks/git-push-guard.py",
    "hooks/memory-lint-hook.py",
    "scripts/memory-lint.py",
    "scripts/lane-workflow.js",
    "scripts/review-lane-workflow.js",
    "rules/zsh-pitfalls.md",
    "rules/security.md",
    "rules/memory-scope.md",
    "rules/subagent-review.md",
    "rules/git-workflow.md",
    "commands/lane.md",
    "commands/review-lane.md",
)


@dataclass(frozen=True)
class Entry:
    asset: str  # path under the asset directory
    live: str   # path under the live ~/.claude


@dataclass(frozen=True)
class Built:
    entry: Entry
    text: str
    stale: tuple[int, ...]
    skipped: bool = False  # no live original: `text` is the existing asset, kept as it is


def manifest_entries(assets_dir: Path) -> tuple[Entry, ...]:
    """Entries from `manifest.json` (any list of objects with `src`, optional `dst`), else the defaults."""
    path = assets_dir / MANIFEST_NAME
    if not path.is_file():
        return tuple(Entry(p, p) for p in DEFAULT_ASSETS)
    raw = json.loads(path.read_text(encoding="utf-8"))
    entries = []
    for group in raw.values():
        if not isinstance(group, list):
            continue
        for item in group:
            if isinstance(item, dict) and item.get("src"):
                entries.append(Entry(item["src"], item.get("dst") or item["src"]))
    return tuple(dict.fromkeys(entries))


def build(entries: tuple[Entry, ...], live_root: Path, smap: scrub.ScrubMap,
          assets_dir: Path = ASSETS_DIR) -> tuple[Built, ...]:
    """Read and scrub every entry.

    An entry with no live original keeps its existing asset (`skipped`); when the asset is
    missing too, FileNotFoundError is raised (the list is the contract).
    """
    return tuple(_build_one(e, live_root, smap, assets_dir) for e in entries)


def _build_one(entry: Entry, live_root: Path, smap: scrub.ScrubMap, assets_dir: Path) -> Built:
    live = live_root / entry.live
    if not live.is_file():
        kept = (assets_dir / entry.asset).read_text(encoding="utf-8")
        return Built(entry, kept, (), skipped=True)
    text = unrender(live.read_text(encoding="utf-8"), entry.asset, live_root)
    result = scrub.scrub_text(text, entry.asset, smap)
    return Built(entry, result.text, result.stale)


def unrender(text: str, asset: str, live_root: Path) -> str:
    """Undo `lapbar install` rendering: the live root's absolute prefix goes back to `~/.claude/`."""
    if not asset.startswith(RENDERED_PREFIXES):
        return text
    roots = {str(live_root.absolute()), str(live_root.resolve())}
    for root in sorted(roots, key=len, reverse=True):
        text = text.replace(root.rstrip("/") + "/", HOME_FORM)
    return text


def write_all(built: tuple[Built, ...], root: Path) -> None:
    for item in built:
        target = root / item.entry.asset
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(item.text, encoding="utf-8")
        if item.text.startswith("#!"):
            target.chmod(0o755)


def run_self_tests(root: Path, built: tuple[Built, ...]) -> tuple[tuple[str, bool, str], ...]:
    """(asset, passed, last output line) for every python asset that ships a self-test."""
    results = []
    for item in built:
        if not item.entry.asset.endswith(".py") or SELF_TEST_FLAG not in item.text:
            continue
        proc = subprocess.run(
            [sys.executable, str(root / item.entry.asset), SELF_TEST_FLAG],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=SELF_TEST_TIMEOUT,
        )
        tail = (proc.stdout.strip().splitlines() or ["(no output)"])[-1]
        results.append((item.entry.asset, proc.returncode == 0, tail))
    return tuple(results)


def report_hits(hits: tuple[scrub.Hit, ...]) -> int:
    lines = scrub.summarize(hits)
    for line in lines:
        print("SCAN FAIL", line)
    print(f"scan: {len(lines)} hit(s)")
    return 1 if lines else 0


def run_check(assets_dir: Path, map_path: Path = scrub.MAP_PATH, require_map: bool = False) -> int:
    """Scan the asset directory only; nothing is written. The private map adds owner rules when present."""
    if map_path.is_file():
        smap = scrub.load_map(map_path)
        print(f"owner rules: loaded ({len(smap.terms)} terms, {len(smap.scan)} patterns)")
    else:
        if require_map:
            print(f"owner rules: REQUIRED but absent ({map_path})")
            return 1
        smap = scrub.parse_map({})
        print("owner rules: absent (generic patterns only)")
    return report_hits(scrub.scan_tree(assets_dir, smap.terms, smap.scan))


def run_bundle(live_root: Path, assets_dir: Path, preview: Path | None, map_path: Path) -> int:
    smap = scrub.load_map(map_path)
    built = build(manifest_entries(assets_dir), live_root, smap, assets_dir)
    fresh = tuple(item for item in built if not item.skipped)
    for item in built:
        if item.skipped:
            print(f"SKIP {item.entry.asset} (no live original, asset kept)")
        for index in item.stale:
            print(f"WARN stale per-file edit #{index} in scrub_map.json for {item.entry.asset}")
    if preview is not None:
        write_all(fresh, preview)
        print(f"preview written to {preview}")
    hits = tuple(h for item in built for h in scrub.scan_text(item.text, item.entry.asset, smap.terms, smap.scan))
    if report_hits(hits):
        return 1
    with tempfile.TemporaryDirectory(prefix="lapbar-bundle-") as tmp:
        write_all(built, Path(tmp))
        tests = run_self_tests(Path(tmp), built)
    for asset, passed, tail in tests:
        print(f"{'PASS' if passed else 'FAIL'} self-test {asset}: {tail}")
    if not all(passed for _, passed, _ in tests):
        return 1
    write_all(fresh, assets_dir)
    print(f"wrote {len(fresh)} asset(s) to {assets_dir}, kept {len(built) - len(fresh)}")
    return 0


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser("bundle", help="(maintainer) scrub the live ~/.claude into package assets")
    configure(parser)
    parser.set_defaults(func=run)
    return parser


def configure(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--from", dest="from_dir", default="~/.claude", help="live Claude Code directory to read")
    parser.add_argument("--check", action="store_true", help="scan the asset directory only, write nothing")
    parser.add_argument("--require-map", action="store_true",
                        help="with --check: fail when the private map (owner rules) is absent")
    parser.add_argument("--assets", default=str(ASSETS_DIR), help="asset directory (default: the package's)")
    parser.add_argument("--preview", default=None, help="also write scrubbed copies here for review")
    parser.add_argument("--map", dest="map_path", default=str(scrub.MAP_PATH),
                        help="substitution table kept outside the repo (default: $LAPBAR_SCRUB_MAP or ~/.config/lapbar/scrub_map.json)")


def run(args: argparse.Namespace) -> int:
    assets_dir = Path(args.assets).expanduser()
    if args.check:
        return run_check(assets_dir, Path(args.map_path).expanduser(), args.require_map)
    preview = Path(args.preview).expanduser() if args.preview else None
    return run_bundle(Path(args.from_dir).expanduser(), assets_dir, preview, Path(args.map_path).expanduser())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m lapbar.bundle", description=__doc__.splitlines()[0])
    configure(parser)
    return run(parser.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
