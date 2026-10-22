"""Tests for the scrub table, the scan gate and `beacon bundle` (stdlib unittest only)."""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from beacon import bundle, scrub



def categories(text: str, rel_path: str = "rules/x.md", terms=scrub.HASHED_TERMS) -> set[str]:
    return {h.category for h in scrub.scan_text(text, rel_path, terms)}


class ApplyLiteralsTest(unittest.TestCase):
    def test_longest_pattern_wins(self):
        pairs = (("ab", "SHORT"), ("abc", "LONG"))
        self.assertEqual(scrub.apply_literals("abcd ab", pairs), "LONGd SHORT")

    def test_case_insensitive_single_pass(self):
        pairs = (("foo", "bar"), ("bar", "baz"))
        self.assertEqual(scrub.apply_literals("FOO bar", pairs), "bar baz")

    def test_empty_table_is_identity(self):
        self.assertEqual(scrub.apply_literals("keep", ()), "keep")


class FileEditsTest(unittest.TestCase):
    def setUp(self):
        self.smap = scrub.parse_map({
            "files": {"rules/a.md": [["exact old", "exact new"], {"pattern": r"n(\d)", "repl": r"N\1"}, ["missing", "x"]]},
            "regex": [{"pattern": "alpha", "repl": "beta"}],
            "literal": [["beta", "gamma"]],
        })

    def test_passes_run_in_order(self):
        result = scrub.scrub_text("exact old n1 alpha", "rules/a.md", self.smap)
        self.assertEqual(result.text, "exact new N1 gamma")

    def test_unmatched_edit_is_reported_as_stale(self):
        self.assertEqual(scrub.scrub_text("nothing", "rules/a.md", self.smap).stale, (0, 1, 2))

    def test_other_files_get_only_global_passes(self):
        result = scrub.scrub_text("exact old alpha", "rules/b.md", self.smap)
        self.assertEqual((result.text, result.stale), ("exact old gamma", ()))


class ScanTest(unittest.TestCase):
    FAKE_TERMS = {
        scrub.term_digest("zzacme"): "org",
        scrub.term_digest("zz-proj-x"): "project",
        scrub.term_digest("zz_user"): "account",
        scrub.term_digest("가나다라"): "real-name",
        scrub.term_digest("zzwrap"): "wrapper",
        scrub.term_digest("zz-private"): "private-workspace",
    }

    def test_pattern_categories_positive(self):
        samples = {
            "home-path": "cd /Users/someone/x",
            "jwt": "token eyJhbGciOiJIUzI1NiJ9.payload",
            "email": "mail someone@example.com",
            "credential": "API_KEY=abcdef123456",
            "wikilink": "see [[feedback_some_slug]]",
            "memory-slug": "global feedback_some_slug",
            "public-ip": "host 198.51.100.7",
            "internal-ip": "host 10.1.2.3",
        }
        for category, line in samples.items():
            with self.subTest(category=category):
                self.assertIn(category, categories(line))

    def test_hashed_categories_positive(self):
        samples = {
            "org": "the zzacmeClient secret key name",
            "project": "cd ~/src/zz-proj-x/app",
            "account": "owner zz_user here",
            "real-name": "작성자 가나다라님",
            "wrapper": "export ZZWRAP_HOST=x",
            "private-workspace": "/tmp/.zz-private/notes",
        }
        for category, line in samples.items():
            with self.subTest(category=category):
                self.assertEqual(categories(line, terms=self.FAKE_TERMS), {category})

    def test_hashed_terms_respect_word_atoms(self):
        self.assertEqual(categories("zzacmex and xzzacme", terms=self.FAKE_TERMS), set())

    def test_builtin_digests_cover_every_hashed_category(self):
        self.assertEqual(set(scrub.HASHED_TERMS.values()),
                         {"real-name", "account", "project", "org", "wrapper", "private-workspace"})

    def test_builtin_real_name_and_its_exemption(self):
        line = "Copyright " + "Geun" + "rok Kim"
        self.assertIn("real-name", categories(line, "rules/x.md"))
        self.assertNotIn("real-name", categories(line, "LICENSE"))
        self.assertNotIn("real-name", categories(line, "README.md"))
        self.assertNotIn("real-name", categories(line, "pyproject.toml"))

    def test_clean_text_is_negative(self):
        text = "\n".join([
            "목록, 기록, 등록 은 그대로, 사용자 결정, [owner-approved: fable]",
            "zsh `[[ $a < $b ]]`, `/home/<u>/run.sh`, eyJ…\\.…, 8.8.8.8, 127.0.0.1, v3.14.8",
            "feedback_<slug>.md, reference_env_<topic>.md, beacon, workspace, staging",
        ])
        self.assertEqual(scrub.scan_text(text, "rules/clean.md"), ())

    def test_output_never_contains_the_value(self):
        secret_line = "x zzacme 198.51.100.7 someone@example.com"
        hits = scrub.scan_text("ok\n" + secret_line, "rules/x.md", self.FAKE_TERMS)
        rendered = scrub.summarize(hits)
        self.assertEqual(rendered, ("email -> rules/x.md:2", "org -> rules/x.md:2", "public-ip -> rules/x.md:2"))
        for token in ("zzacme", "198.51", "someone", "example.com"):
            self.assertFalse(any(token in line for line in rendered))


class BundleTest(unittest.TestCase):
    def run_cli(self, *argv: str) -> tuple[int, str]:
        parser = argparse.ArgumentParser()
        bundle.configure(parser)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = bundle.run(parser.parse_args(list(argv)))
        return code, out.getvalue()

    def test_check_clean_assets_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "rules").mkdir()
            (Path(tmp) / "rules" / "ok.md").write_text("사용자 결정, ~/.claude\n", encoding="utf-8")
            code, out = self.run_cli("--check", "--assets", tmp)
        self.assertEqual(code, 0)
        self.assertIn("scan: 0 hit(s)", out)

    def test_check_dirty_assets_fails_without_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "hooks").mkdir()
            (Path(tmp) / "hooks" / "bad.py").write_text("# fine\nHOME = '/Users/someone'\n", encoding="utf-8")
            code, out = self.run_cli("--check", "--assets", tmp)
        self.assertEqual(code, 1)
        self.assertIn("home-path -> hooks/bad.py:2", out)
        self.assertNotIn("someone", out)

    def test_manifest_entries_use_src_as_asset_and_dst_as_live_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = {"hooks": [{"src": "hooks/a.py", "dst": "hooks/a.py", "event": "PreToolUse"}],
                        "skills": [{"src": "commands/b.md"}], "version": 1}
            (Path(tmp) / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            entries = bundle.manifest_entries(Path(tmp))
        self.assertEqual(entries, (bundle.Entry("hooks/a.py", "hooks/a.py"), bundle.Entry("commands/b.md", "commands/b.md")))

    def test_bundle_writes_only_when_scan_is_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            live, assets = Path(tmp) / "live", Path(tmp) / "assets"
            (live / "rules").mkdir(parents=True)
            assets.mkdir()
            (assets / "manifest.json").write_text(json.dumps({"rules": [{"src": "rules/r.md"}]}), encoding="utf-8")
            smap = Path(tmp) / "map.json"
            smap.write_text(json.dumps({"regex": [{"pattern": "zzowner", "repl": "user"}],
                                        "scan": [{"category": "honorific", "pattern": "zzowner"}]}), encoding="utf-8")
            (live / "rules" / "r.md").write_text("zzowner decision\n", encoding="utf-8")
            code, _ = self.run_cli("--from", str(live), "--assets", str(assets), "--map", str(smap))
            self.assertEqual(code, 0)
            self.assertEqual((assets / "rules" / "r.md").read_text(encoding="utf-8"), "user decision\n")
            (live / "rules" / "r.md").write_text("ping 198.51.100.7\n", encoding="utf-8")
            code, out = self.run_cli("--from", str(live), "--assets", str(assets), "--map", str(smap))
            self.assertEqual(code, 1)
            self.assertIn("public-ip -> rules/r.md:1", out)
            self.assertEqual((assets / "rules" / "r.md").read_text(encoding="utf-8"), "user decision\n")
            (assets / "rules" / "r.md").write_text("zzowner slipped through\n", encoding="utf-8")
            code, out = self.run_cli("--check", "--assets", str(assets), "--map", str(smap))
            self.assertEqual(code, 1)
            self.assertIn("honorific -> rules/r.md:1", out)


if __name__ == "__main__":
    unittest.main()
