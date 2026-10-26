"""Packaged asset manifest: loading, filtering and reading asset files.

The manifest (`assets/manifest.json`) is the single source of truth for what
`lapbar install` copies and what `lapbar doctor` checks. Every function takes an
optional `root` so tests can point at a fake asset directory.
"""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Iterable

CATEGORIES: tuple[str, ...] = ("hooks", "scripts", "rules", "skills", "agents")
MANIFEST_NAME = "manifest.json"
HOOK_CATEGORY = "hooks"
PROBE_EXPECTATIONS = frozenset({"deny", "allow"})

AssetRoot = Traversable | Path


@dataclass(frozen=True)
class Probe:
    """A sample hook stdin payload and the decision the hook must answer with."""

    name: str
    input: dict = field(hash=False)
    expect: str = "deny"


@dataclass(frozen=True)
class Asset:
    """One file shipped by lapbar. `dst` is relative to the install target.

    A hook whose `event` is None is installed but not wired into settings.json;
    an agent definition's frontmatter runs it instead.
    """

    category: str
    src: str
    dst: str
    event: str | None = None
    matcher: str | None = None
    selftest: bool = False
    probes: tuple[Probe, ...] = ()

    @property
    def is_hook(self) -> bool:
        """Installed as an executable hook; subject to self-tests and probes."""
        return self.category == HOOK_CATEGORY

    @property
    def is_wired_hook(self) -> bool:
        """A hook that `install` wires into (and `doctor` expects in) settings.json."""
        return self.is_hook and self.event is not None


def default_root() -> Traversable:
    """Asset directory inside the installed `lapbar` package."""
    return resources.files("lapbar").joinpath("assets")


def _resolve(root: AssetRoot | None) -> AssetRoot:
    return default_root() if root is None else root


def _child(root: AssetRoot, rel: str) -> AssetRoot:
    # Join one segment at a time: multi-segment joinpath is not portable
    # across every Traversable implementation.
    node = root
    for part in rel.split("/"):
        node = node.joinpath(part)
    return node


def _check_relative(rel: str, field: str) -> str:
    parts = rel.split("/")
    if not rel or rel.startswith("/") or "\\" in rel or any(p in ("", ".", "..") for p in parts):
        raise ValueError(f"manifest {field} must be a plain relative path: {rel!r}")
    return rel


def _parse_probe(src: str, raw: dict) -> Probe:
    name, payload, expect = raw.get("name"), raw.get("input"), raw.get("expect")
    if not isinstance(name, str) or not name:
        raise ValueError(f"manifest probe of {src} needs a non-empty name")
    if not isinstance(payload, dict):
        raise ValueError(f"manifest probe {name!r} of {src}: input must be an object")
    if expect not in PROBE_EXPECTATIONS:
        raise ValueError(f"manifest probe {name!r} of {src}: expect must be one of "
                         f"{', '.join(sorted(PROBE_EXPECTATIONS))}, got {expect!r}")
    return Probe(name=name, input=copy.deepcopy(payload), expect=expect)


def _parse_probes(category: str, src: str, raw: dict) -> tuple[Probe, ...]:
    probes = raw.get("probes") or ()
    if probes and category != HOOK_CATEGORY:
        raise ValueError(f"manifest {src}: probes are only allowed on hooks")
    if not isinstance(probes, (list, tuple)):
        raise ValueError(f"manifest {src}: probes must be a list")
    return tuple(_parse_probe(src, p if isinstance(p, dict) else {}) for p in probes)


def _parse_entry(category: str, raw: dict) -> Asset:
    src = _check_relative(str(raw["src"]), "src")
    return Asset(
        category=category,
        src=src,
        dst=_check_relative(str(raw.get("dst", raw["src"])), "dst"),
        event=raw.get("event") or None,
        matcher=raw.get("matcher") or None,
        selftest=bool(raw.get("selftest", False)),
        probes=_parse_probes(category, src, raw),
    )


def load_manifest(root: AssetRoot | None = None) -> tuple[Asset, ...]:
    """Parse the manifest into an ordered, immutable tuple of assets."""
    text = _child(_resolve(root), MANIFEST_NAME).read_text(encoding="utf-8")
    data = json.loads(text)
    unknown = sorted(set(data) - set(CATEGORIES))
    if unknown:
        raise ValueError(f"manifest has unknown categories: {', '.join(unknown)}")
    return tuple(
        _parse_entry(category, raw)
        for category in CATEGORIES
        for raw in data.get(category, ())
    )


def parse_only(text: str | None) -> tuple[str, ...]:
    """Turn `--only hooks,rules` into a validated category tuple (empty = all)."""
    if not text:
        return CATEGORIES
    chosen = tuple(dict.fromkeys(p.strip() for p in text.split(",") if p.strip()))
    unknown = [c for c in chosen if c not in CATEGORIES]
    if unknown or not chosen:
        raise ValueError(f"unknown category in --only: {', '.join(unknown) or text!r} "
                         f"(choose from {', '.join(CATEGORIES)})")
    return chosen


def filter_assets(assets: Iterable[Asset], categories: Iterable[str]) -> tuple[Asset, ...]:
    wanted = frozenset(categories)
    return tuple(a for a in assets if a.category in wanted)


def read_asset(asset: Asset, root: AssetRoot | None = None) -> bytes | None:
    """Packaged bytes of an asset, or None when the file is not shipped yet."""
    node = _child(_resolve(root), asset.src)
    if not node.is_file():
        return None
    return node.read_bytes()
