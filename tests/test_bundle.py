"""`lapbar bundle` build step: live originals that are absent, and un-rendering install paths.

Every test uses a tempfile live directory; the real Claude Code home is never read.
"""
from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from lapbar import bundle, scrub

AGENT_ASSET = "agents/scout.md"
HOOK_ASSET = "hooks/kept.py"
AGENT_TEMPLATE = "command: \"python3 {prefix}hooks/guard.py\"\nsee {prefix}rules/a.md\n"
KEPT_HOOK = "print('kept')\n"


class BundleBuildCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.live = base / "live" / ".claude"
        self.assets = base / "assets"
        self.map_path = base / "map.json"
        self.map_path.write_text("{}", encoding="utf-8")
        manifest = {"hooks": [{"src": HOOK_ASSET, "dst": HOOK_ASSET, "event": None}],
                    "agents": [{"src": AGENT_ASSET, "dst": AGENT_ASSET}]}
        self.write(self.assets / "manifest.json", json.dumps(manifest))
        self.write(self.assets / HOOK_ASSET, KEPT_HOOK)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write(self, path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def write_live_agent(self, prefix: str) -> None:
        self.write(self.live / AGENT_ASSET, AGENT_TEMPLATE.format(prefix=prefix))

    def build(self) -> tuple[bundle.Built, ...]:
        entries = bundle.manifest_entries(self.assets)
        return bundle.build(entries, self.live, scrub.parse_map({}), assets_dir=self.assets)


class SkipTest(BundleBuildCase):
    def test_missing_live_original_is_skipped_and_asset_kept(self) -> None:
        self.write_live_agent("~/.claude/")
        built = {b.entry.asset: b for b in self.build()}
        self.assertTrue(built[HOOK_ASSET].skipped)
        self.assertEqual(built[HOOK_ASSET].text, KEPT_HOOK)
        self.assertFalse(built[AGENT_ASSET].skipped)

    def test_missing_live_original_and_asset_raises(self) -> None:
        self.write_live_agent("~/.claude/")
        (self.assets / HOOK_ASSET).unlink()
        with self.assertRaises(FileNotFoundError):
            self.build()

    def test_run_bundle_reports_skip_and_leaves_asset_untouched(self) -> None:
        self.write_live_agent("~/.claude/")
        before = (self.assets / HOOK_ASSET).stat().st_mtime_ns
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = bundle.run_bundle(self.live, self.assets, None, self.map_path)
        self.assertEqual(code, 0, out.getvalue())
        self.assertIn(f"SKIP {HOOK_ASSET} (no live original, asset kept)", out.getvalue())
        self.assertEqual((self.assets / HOOK_ASSET).read_text(encoding="utf-8"), KEPT_HOOK)
        self.assertEqual((self.assets / HOOK_ASSET).stat().st_mtime_ns, before)


class UnrenderTest(BundleBuildCase):
    def test_installed_absolute_prefix_is_turned_back_into_home_form(self) -> None:
        self.write_live_agent(f"{self.live.absolute()}/")
        built = {b.entry.asset: b for b in self.build()}
        self.assertEqual(built[AGENT_ASSET].text, AGENT_TEMPLATE.format(prefix="~/.claude/"))

    def test_resolved_prefix_is_also_turned_back(self) -> None:
        self.write_live_agent(f"{self.live.resolve()}/")
        built = {b.entry.asset: b for b in self.build()}
        self.assertEqual(built[AGENT_ASSET].text, AGENT_TEMPLATE.format(prefix="~/.claude/"))

    def test_unrendered_categories_keep_absolute_paths(self) -> None:
        self.write_live_agent("~/.claude/")
        self.write(self.live / HOOK_ASSET, f"ROOT = '{self.live.absolute()}/'\n")
        built = {b.entry.asset: b for b in self.build()}
        self.assertIn(str(self.live.absolute()), built[HOOK_ASSET].text)

    def test_run_bundle_writes_home_form_into_assets(self) -> None:
        self.write_live_agent(f"{self.live.absolute()}/")
        self.write(self.live / HOOK_ASSET, KEPT_HOOK)
        with contextlib.redirect_stdout(io.StringIO()):
            code = bundle.run_bundle(self.live, self.assets, None, self.map_path)
        self.assertEqual(code, 0)
        text = (self.assets / AGENT_ASSET).read_text(encoding="utf-8")
        self.assertEqual(text, AGENT_TEMPLATE.format(prefix="~/.claude/"))


if __name__ == "__main__":
    unittest.main()
