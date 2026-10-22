"""Shared helpers for install/doctor tests: a fake asset tree and a temp target."""
from __future__ import annotations

import json
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

GOOD_HOOK = "import sys\nsys.exit(0 if '--self-test' in sys.argv else 1)\n"
BAD_HOOK = "import sys\nprint('self-test: 1 case failed', file=sys.stderr)\nsys.exit(3)\n"

FAKE_MANIFEST = {
    "hooks": [
        {"src": "hooks/guard.py", "dst": "hooks/guard.py", "event": "PreToolUse",
         "matcher": "Bash", "selftest": True},
        {"src": "hooks/start.py", "dst": "hooks/start.py", "event": "SessionStart",
         "matcher": None, "selftest": False},
    ],
    "scripts": [{"src": "scripts/lint.py", "dst": "scripts/lint.py"}],
    "rules": [{"src": "rules/a.md", "dst": "rules/a.md"}],
    "skills": [{"src": "commands/lane.md", "dst": "commands/lane.md"}],
    "agents": [{"src": "agents/reviewer.md", "dst": "agents/reviewer.md"}],
}

FAKE_FILES = {
    "hooks/guard.py": GOOD_HOOK,
    "hooks/start.py": "print('start')\n",
    "scripts/lint.py": "print('lint')\n",
    "rules/a.md": "# rule a\n",
    "commands/lane.md": "# lane\n",
    "agents/reviewer.md": "# reviewer agent\n",
}


def make_assets(root: Path, manifest: dict | None = None,
                files: dict[str, str] | None = None) -> Path:
    """Write a fake asset tree (manifest + files) under `root` and return it."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "manifest.json").write_text(json.dumps(manifest or FAKE_MANIFEST), encoding="utf-8")
    for rel, text in (FAKE_FILES if files is None else files).items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def list_files(root: Path) -> list[str]:
    if not root.exists():
        return []
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())
