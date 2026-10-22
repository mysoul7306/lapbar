from __future__ import annotations

import argparse
import json
import os
import tempfile
import unittest
from pathlib import Path

from install_fixtures import FAKE_FILES, list_files, make_assets

from beacon import assets as asset_mod
from beacon import install as inst
from beacon import settings_wiring as wiring


class InstallTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.assets = make_assets(base / "assets")
        self.target = base / "home" / ".claude"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def install(self, **kwargs) -> inst.InstallResult:
        categories = kwargs.pop("categories", asset_mod.CATEGORIES)
        return inst.install(self.target, categories, assets_root=self.assets, **kwargs)

    def settings(self) -> dict:
        return json.loads((self.target / "settings.json").read_text(encoding="utf-8"))

    def statuses(self, result: inst.InstallResult) -> dict[str, str]:
        return {r.item: r.status for r in result.rows}

    def backups(self) -> list[Path]:
        return sorted(self.target.glob("settings.json.bak-*"))


class FreshInstallTest(InstallTestCase):
    def test_copies_files_marks_hooks_executable_and_wires_settings(self) -> None:
        result = self.install()
        self.assertEqual(result.exit_code, 0)
        for rel, text in FAKE_FILES.items():
            self.assertEqual((self.target / rel).read_text(encoding="utf-8"), text)
            self.assertEqual(self.statuses(result)[rel], inst.INSTALLED)
        self.assertTrue(os.access(self.target / "hooks/guard.py", os.X_OK))
        self.assertFalse(os.access(self.target / "rules/a.md", os.X_OK))
        hooks = self.settings()["hooks"]
        guard_cmd = wiring.hook_command(self.target, "hooks/guard.py")
        self.assertEqual(hooks["PreToolUse"], [
            {"matcher": "Bash", "hooks": [{"type": "command", "command": guard_cmd}]}])
        self.assertNotIn("matcher", hooks["SessionStart"][0])
        self.assertTrue(guard_cmd.startswith("python3 /"))
        self.assertEqual(self.backups(), [], "no backup when settings.json did not exist")

    def test_only_limits_categories(self) -> None:
        result = self.install(categories=asset_mod.parse_only("rules,skills"))
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(list_files(self.target), ["commands/lane.md", "rules/a.md"])

    def test_parse_only_rejects_unknown_category(self) -> None:
        with self.assertRaises(ValueError):
            asset_mod.parse_only("rules,memory")


class ReinstallTest(InstallTestCase):
    def test_same_content_reports_same_and_does_not_touch_settings(self) -> None:
        self.install()
        settings_before = (self.target / "settings.json").read_bytes()
        result = self.install()
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(set(self.statuses(result).values()), {inst.SAME})
        self.assertEqual((self.target / "settings.json").read_bytes(), settings_before)
        self.assertEqual(self.backups(), [])

    def test_differing_file_is_kept_without_force_and_replaced_with_force(self) -> None:
        self.install()
        rule = self.target / "rules/a.md"
        rule.write_text("# my own edits\n", encoding="utf-8")
        kept = self.install()
        self.assertEqual(self.statuses(kept)["rules/a.md"], inst.DIFFERS)
        self.assertEqual(rule.read_text(encoding="utf-8"), "# my own edits\n")
        self.assertEqual(len(kept.warnings), 1)
        self.assertEqual(kept.exit_code, 0)
        forced = self.install(force=True)
        self.assertEqual(self.statuses(forced)["rules/a.md"], inst.INSTALLED)
        self.assertEqual(rule.read_text(encoding="utf-8"), FAKE_FILES["rules/a.md"])

    def test_differing_hook_is_not_wired(self) -> None:
        hook = self.target / "hooks/guard.py"
        hook.parent.mkdir(parents=True)
        hook.write_text("# user's own guard\n", encoding="utf-8")
        result = self.install()
        label = next(r for r in result.rows if r.item.endswith("hooks/guard.py") and "[" in r.item)
        self.assertEqual(label.status, inst.SKIPPED)
        self.assertNotIn("PreToolUse", self.settings()["hooks"])


class SettingsMergeTest(InstallTestCase):
    def write_settings(self, data: dict) -> bytes:
        self.target.mkdir(parents=True)
        raw = json.dumps(data, indent=2).encode()
        (self.target / "settings.json").write_bytes(raw)
        return raw

    def test_idempotent_merge_into_existing_entry_with_backup(self) -> None:
        other = {"type": "command", "command": "python3 /elsewhere/other.py"}
        original = self.write_settings({"model": "x", "hooks": {
            "PreToolUse": [{"matcher": "Bash", "hooks": [other]}]}})
        self.install()
        self.install()
        data = self.settings()
        self.assertEqual(data["model"], "x")
        entries = data["hooks"]["PreToolUse"]
        self.assertEqual(len(entries), 1)
        commands = [h["command"] for h in entries[0]["hooks"]]
        guard_cmd = wiring.hook_command(self.target, "hooks/guard.py")
        self.assertEqual(commands, [other["command"], guard_cmd])
        self.assertEqual(len(data["hooks"]["SessionStart"]), 1)
        backups = self.backups()
        self.assertEqual(len(backups), 1, "second run changes nothing, so no second backup")
        self.assertEqual(backups[0].read_bytes(), original)

    def test_command_already_under_other_matcher_is_skipped(self) -> None:
        guard_cmd = wiring.hook_command(self.target, "hooks/guard.py")
        self.write_settings({"hooks": {"PreToolUse": [
            {"matcher": "Bash|Write", "hooks": [{"type": "command", "command": guard_cmd}]}]}})
        result = self.install()
        wired = [r for r in result.rows if "[PreToolUse" in r.item]
        self.assertEqual(wired[0].status, inst.SAME)
        self.assertEqual(len(self.settings()["hooks"]["PreToolUse"]), 1)

    def test_backup_and_new_settings_are_owner_only(self) -> None:
        self.install(categories=("hooks",))
        created = self.target / "settings.json"
        self.assertEqual(created.stat().st_mode & 0o777, 0o600, "new settings.json")
        created.write_text("{}\n", encoding="utf-8")
        created.chmod(0o644)
        self.install(categories=("hooks",))
        backups = self.backups()
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].stat().st_mode & 0o777, 0o600, "settings backup")
        self.assertEqual(backups[0].read_text(encoding="utf-8"), "{}\n")

    def test_merge_hook_does_not_mutate_input(self) -> None:
        before = {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": []}]}}
        snapshot = json.dumps(before, sort_keys=True)
        merged, changed = wiring.merge_hook(before, "PreToolUse", "Bash", "python3 /x.py")
        self.assertTrue(changed)
        self.assertEqual(json.dumps(before, sort_keys=True), snapshot)
        self.assertEqual(len(merged["hooks"]["PreToolUse"][0]["hooks"]), 1)

    def test_invalid_settings_aborts_before_copying(self) -> None:
        self.target.mkdir(parents=True)
        (self.target / "settings.json").write_text("{not json", encoding="utf-8")
        result = self.install()
        self.assertEqual(result.exit_code, 1)
        self.assertEqual(list_files(self.target), ["settings.json"])


class DryRunTest(InstallTestCase):
    def test_dry_run_writes_nothing(self) -> None:
        result = self.install(dry_run=True)
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(list_files(self.target), [])
        self.assertEqual(set(self.statuses(result).values()), {inst.DRY})

    def test_dry_run_leaves_existing_settings_untouched(self) -> None:
        self.target.mkdir(parents=True)
        (self.target / "settings.json").write_text("{}\n", encoding="utf-8")
        self.install(dry_run=True)
        self.assertEqual(list_files(self.target), ["settings.json"])
        self.assertEqual((self.target / "settings.json").read_text(encoding="utf-8"), "{}\n")


class MissingAssetTest(InstallTestCase):
    def test_unpackaged_asset_is_reported_and_exit_1(self) -> None:
        (self.assets / "rules/a.md").unlink()
        result = self.install()
        self.assertEqual(self.statuses(result)["rules/a.md"], inst.MISSING)
        self.assertEqual(result.exit_code, 1)
        self.assertFalse((self.target / "rules/a.md").exists())

    def test_packaged_manifest_lists_v1_assets(self) -> None:
        manifest = asset_mod.load_manifest()
        by_cat = {c: [a.dst for a in manifest if a.category == c] for c in asset_mod.CATEGORIES}
        self.assertEqual(len(by_cat["hooks"]), 3)
        self.assertEqual(len(by_cat["scripts"]), 3)
        self.assertEqual(len(by_cat["rules"]), 5)
        self.assertEqual(by_cat["skills"], ["commands/lane.md", "commands/review-lane.md"])
        selftests = {a.dst for a in manifest if a.selftest}
        self.assertEqual(selftests, {"hooks/subagent-model-guard.py", "hooks/git-push-guard.py"})

    def test_manifest_rejects_escaping_paths(self) -> None:
        bad = {"rules": [{"src": "rules/a.md", "dst": "../outside.md"}]}
        root = make_assets(Path(self._tmp.name) / "bad", manifest=bad, files={})
        with self.assertRaises(ValueError):
            asset_mod.load_manifest(root)


class ParserTest(unittest.TestCase):
    def test_add_parser_registers_flags_and_run(self) -> None:
        parser = argparse.ArgumentParser()
        inst.add_parser(parser.add_subparsers(dest="command"))
        args = parser.parse_args(["install", "--target", "/tmp/x", "--only", "hooks",
                                  "--force", "--dry-run"])
        self.assertIs(args.func, inst.run)
        self.assertEqual((args.target, args.only, args.force, args.dry_run),
                         ("/tmp/x", "hooks", True, True))


if __name__ == "__main__":
    unittest.main()
