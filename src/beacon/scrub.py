"""Scrub personal traces out of harness files and scan the result for leftovers.

Two halves, both pure:
- `scrub_text` applies the substitution table (`scrub_map.json`) in three passes:
  per-file edits (hand polish: exact text or a regex, in listed order), then global regex rules
  (in listed order), then global literals (one case-insensitive pass, longest pattern first).
- `scan_text` / `scan_tree` are the publish gate. A hit reports only `category -> file:line`;
  the matched value is never returned or printed.

Names that must not appear (people, employer, clients, internal projects, wrappers) are kept here only
as salted digests, so the gate itself does not publish the list it guards. This hides the words from
a casual reader; it is not protection against a dictionary search.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator, Mapping

# The substitution table holds the maintainer's plain-text originals, so it lives
# outside the repository: $BEACON_SCRUB_MAP or ~/.config/beacon/scrub_map.json.
MAP_PATH = Path(os.environ.get("BEACON_SCRUB_MAP", "~/.config/beacon/scrub_map.json")).expanduser()

_HANGUL = "가-힣"
DIGEST_SALT = "beacon-scan:"
DIGEST_LEN = 20

# Owner-specific terms ship only as salted digests, and only in the private map (`terms` section).
# Add one with `term_digest("<term>")`; never commit the plain term.
HASHED_TERMS: Mapping[str, str] = {}
# Hangul terms are matched as substrings of Hangul runs of these lengths.
HANGUL_TERM_LENGTHS = (2, 3, 4)

# Pattern categories (generic shapes, safe to publish).
# The honorific pattern skips words that merely end in the same syllable (common nouns).
# Owner-specific rules (nickname, honorifics) live in the private map's `scan` section.
SCAN_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("home-path", re.compile(r"/Users/|/home/[a-z]", re.IGNORECASE)),
    ("partial-ip", re.compile(r"(?<![\d.])\d{1,3}\.\d{1,3}\.\d{1,3}\.(?:x|\*|<)")),
    ("jwt", re.compile(r"eyJ[A-Za-z0-9_-]+\.")),
    ("email", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")),
    ("credential", re.compile(
        r"(?i)(password|passwd|pgpassword|_pw\b|secret|token|api[_-]?key|access[_-]?key)"
        r"\s*[=:]\s*['\"]?[A-Za-z0-9!@#$%^&*._/+-]{6,}")),
    ("wikilink", re.compile(r"\[\[[A-Za-z0-9_./-]+\]\]")),
    # Private memory file names (feedback_x_y, todo_x_y ...); `feedback_<slug>` style templates do not match.
    ("memory-slug", re.compile(r"(?<![A-Za-z])(feedback|reference|todo|project)_[a-z0-9]+_[a-z0-9_]+")),
)
_IPV4 = re.compile(r"(?<![\d.])(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})(?![\d.])")
_ASCII_RUN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")
_CAMEL_ATOM = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|[0-9]+")
_HANGUL_RUN = re.compile(rf"[{_HANGUL}]+")
# Well-known public resolvers used as harmless defaults in the shipped scripts.
ALLOWED_IPS = frozenset({"8.8.8.8", "8.8.4.4", "1.1.1.1"})
# Only the public license and readme may carry the author's real name.
NAME_EXEMPT_PREFIXES = ("license", "readme", "pyproject")

# A per-file edit: exact text (str) or a compiled pattern, and its replacement.
FileEdit = tuple["str | re.Pattern[str]", str]


@dataclass(frozen=True)
class ScrubMap:
    files: Mapping[str, tuple[FileEdit, ...]]
    regex: tuple[tuple[re.Pattern[str], str], ...]
    literal: tuple[tuple[str, str], ...]
    scan: tuple[tuple[str, re.Pattern[str]], ...] = ()  # extra scan rules: (category, pattern)
    terms: Mapping[str, str] = field(default_factory=dict)  # digest -> category


@dataclass(frozen=True)
class ScrubResult:
    text: str
    stale: tuple[int, ...]  # indexes of per-file edits that matched nothing


@dataclass(frozen=True)
class Hit:
    category: str
    path: str
    line: int

    def render(self) -> str:
        return f"{self.category} -> {self.path}:{self.line}"


# ----------------------------------------------------------------------------- substitution

def _file_edit(entry) -> FileEdit:
    """`[old, new]` is an exact edit; `{"pattern": ..., "repl": ...}` is a per-file regex edit."""
    if isinstance(entry, dict):
        return re.compile(entry["pattern"]), entry["repl"]
    old, new = entry
    return old, new


def parse_map(raw: Mapping) -> ScrubMap:
    """Build an immutable ScrubMap from the decoded JSON table."""
    files = {key: tuple(_file_edit(e) for e in edits) for key, edits in (raw.get("files") or {}).items()}
    regex = tuple((re.compile(r["pattern"]), r["repl"]) for r in raw.get("regex") or ())
    literal = tuple(sorted(((o, n) for o, n in raw.get("literal") or ()), key=lambda p: -len(p[0])))
    scan = tuple((r["category"], re.compile(r["pattern"])) for r in raw.get("scan") or ())
    terms = dict(raw.get("terms") or {})
    return ScrubMap(files=files, regex=regex, literal=literal, scan=scan, terms=terms)


def load_map(path: Path = MAP_PATH) -> ScrubMap:
    if not path.is_file():
        raise FileNotFoundError(f"scrub map not found: {path} (set BEACON_SCRUB_MAP or pass --map)")
    return parse_map(json.loads(path.read_text(encoding="utf-8")))


def apply_file_edits(text: str, edits: Iterable[FileEdit]) -> ScrubResult:
    """Per-file edits in listed order (exact ones are case-sensitive). Edits that match nothing are reported."""
    stale = []
    for index, (old, new) in enumerate(edits):
        if isinstance(old, re.Pattern):
            text, count = old.subn(new, text)
        else:
            count = text.count(old)
            text = text.replace(old, new)
        if not count:
            stale.append(index)
    return ScrubResult(text=text, stale=tuple(stale))


def apply_regex(text: str, rules: Iterable[tuple[re.Pattern[str], str]]) -> str:
    for pattern, repl in rules:
        text = pattern.sub(repl, text)
    return text


def apply_literals(text: str, pairs: tuple[tuple[str, str], ...]) -> str:
    """One case-insensitive pass; the longest pattern wins where several start at the same place."""
    if not pairs:
        return text
    ordered = sorted(pairs, key=lambda p: -len(p[0]))
    table = {old.lower(): new for old, new in ordered}
    pattern = re.compile("|".join(re.escape(old) for old, _ in ordered), re.IGNORECASE)
    return pattern.sub(lambda m: table[m.group(0).lower()], text)


def scrub_text(text: str, rel_path: str, smap: ScrubMap) -> ScrubResult:
    """Scrub one file's text. `rel_path` selects the per-file edits (posix, relative to the asset root)."""
    first = apply_file_edits(text, smap.files.get(rel_path, ()))
    second = apply_regex(first.text, smap.regex)
    return ScrubResult(text=apply_literals(second, smap.literal), stale=first.stale)


# ----------------------------------------------------------------------------- scan gate

def term_digest(term: str) -> str:
    return hashlib.sha256((DIGEST_SALT + term.lower()).encode("utf-8")).hexdigest()[:DIGEST_LEN]


def _ascii_candidates(line: str) -> Iterator[str]:
    """Every contiguous span of word atoms (split at `-`, `_`, camelCase, digits), lowercased."""
    for run in _ASCII_RUN.findall(line):
        pieces = re.split(r"([_-])", run)
        atoms: list[tuple[str, str]] = []  # (separator before the atom, atom)
        for index, word in enumerate(pieces[0::2]):
            sep = pieces[2 * index - 1] if index else ""
            for position, atom in enumerate(_CAMEL_ATOM.findall(word) or [word]):
                atoms.append((sep if position == 0 else "", atom))
        for start in range(len(atoms)):
            span = atoms[start][1]
            yield span.lower()
            for sep, atom in atoms[start + 1:]:
                span = span + sep + atom
                yield span.lower()


def _hangul_candidates(line: str) -> Iterator[str]:
    for run in _HANGUL_RUN.findall(line):
        for size in HANGUL_TERM_LENGTHS:
            for start in range(len(run) - size + 1):
                yield run[start:start + size]


def term_categories(line: str, terms: Mapping[str, str] = HASHED_TERMS) -> frozenset[str]:
    """Categories of digest-listed terms that occur in `line`."""
    candidates = set(_ascii_candidates(line)) | set(_hangul_candidates(line))
    return frozenset(terms[d] for d in map(term_digest, candidates) if d in terms)


def _ip_category(match: re.Match[str]) -> str | None:
    octets = [int(x) for x in match.groups()]
    if any(o > 255 for o in octets) or match.group(0) in ALLOWED_IPS:
        return None
    first, second = octets[0], octets[1]
    if first in (0, 127, 255) or (first == 169 and second == 254):
        return None
    if first == 10 or (first == 172 and 16 <= second <= 31) or (first == 192 and second == 168):
        return "internal-ip"
    return "public-ip"


ScanRules = Iterable[tuple[str, "re.Pattern[str]"]]


def scan_text(text: str, rel_path: str, terms: Mapping[str, str] = HASHED_TERMS,
              extra_rules: ScanRules = ()) -> tuple[Hit, ...]:
    """Every category hit in `text` as (category, path, line). Values are never kept."""
    name_exempt = Path(rel_path).name.lower().startswith(NAME_EXEMPT_PREFIXES)
    rules = SCAN_RULES + tuple(extra_rules)
    hits = []
    for number, line in enumerate(text.splitlines(), 1):
        found = {category for category, pattern in rules if pattern.search(line)}
        found |= term_categories(line, terms)
        found |= {c for c in map(_ip_category, _IPV4.finditer(line)) if c}
        if name_exempt:
            found.discard("real-name")
        hits.extend(Hit(category, rel_path, number) for category in sorted(found))
    return tuple(hits)


def scan_tree(root: Path, terms: Mapping[str, str] = HASHED_TERMS, extra_rules: ScanRules = ()) -> tuple[Hit, ...]:
    """Scan every file under `root`. Undecodable files are reported as a hit, not skipped."""
    hits: list[Hit] = []
    for path in sorted(p for p in root.rglob("*") if p.is_file() and "__pycache__" not in p.parts):
        rel = path.relative_to(root).as_posix()
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            hits.append(Hit("unreadable", rel, 0))
            continue
        hits.extend(scan_text(text, rel, terms, extra_rules))
    return tuple(hits)


def summarize(hits: Iterable[Hit]) -> tuple[str, ...]:
    """Printable `category -> file:line` lines, sorted and de-duplicated."""
    return tuple(sorted({h.render() for h in hits}))
