from __future__ import annotations

import argparse
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from install_fixtures import FAKE_FILES, list_files, make_assets

from beacon import assets as asset_mod
from beacon import install as inst
from beacon import settings_wiring as wiring


class InstallTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name).resolve()
        self.base = base
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
        self.assertFalse(os.access(self.target / "agents/reviewer.md", os.X_OK))
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

    def test_skills_pin_home_prefix_to_target(self) -> None:
        skill = "Workflow({ scriptPath: \"~/.claude/scripts/x.js\" })\n"
        (self.assets / "commands/lane.md").write_text(skill, encoding="utf-8")
        (self.assets / "rules/a.md").write_text("see ~/.claude/rules/\n", encoding="utf-8")
        self.install()
        installed = (self.target / "commands/lane.md").read_text(encoding="utf-8")
        self.assertIn(f"{self.target}/scripts/x.js", installed)
        self.assertEqual(installed.count("~/.claude/"), 0)
        rule = (self.target / "rules/a.md").read_text(encoding="utf-8")
        self.assertEqual(rule, "see ~/.claude/rules/\n", "only skills are rendered")
        again = self.install()
        self.assertEqual(self.statuses(again)["commands/lane.md"], inst.SAME)

    def test_only_agents_installs_agent_files(self) -> None:
        result = self.install(categories=asset_mod.parse_only("agents"))
        self.assertEqual(self.statuses(result), {"agents/reviewer.md": inst.INSTALLED})

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

    def test_command_under_other_matcher_is_not_counted(self) -> None:
        guard_cmd = wiring.hook_command(self.target, "hooks/guard.py")
        self.write_settings({"hooks": {"PreToolUse": [
            {"matcher": "Bash|Write", "hooks": [{"type": "command", "command": guard_cmd}]}]}})
        result = self.install()
        wired = [r for r in result.rows if "[PreToolUse" in r.item]
        self.assertEqual(wired[0].status, inst.WIRED)
        matchers = [e["matcher"] for e in self.settings()["hooks"]["PreToolUse"]]
        self.assertEqual(matchers, ["Bash|Write", "Bash"])

    def test_home_relative_wiring_counts_as_wired(self) -> None:
        hand_written = {"type": "command", "command": "python3 ~/.claude/hooks/guard.py"}
        self.write_settings({"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [hand_written]},
            {"matcher": "Bash", "hooks": [{"type": "command",
                                           "command": "python3 $HOME/.claude/hooks/x.py"}]}]}})
        with mock.patch.dict(os.environ, {"HOME": str(self.base / "home")}):
            result = self.install()
        wired = [r for r in result.rows if "[PreToolUse" in r.item]
        self.assertEqual(wired[0].status, inst.SAME)
        entries = self.settings()["hooks"]["PreToolUse"]
        self.assertEqual(entries[0]["hooks"], [hand_written], "no second guard entry")
        self.assertEqual(len(entries[1]["hooks"]), 1)

    def test_malformed_hooks_shape_aborts_before_copying(self) -> None:
        shapes = ({"hooks": None}, {"hooks": []}, {"hooks": {"PreToolUse": {"matcher": "Bash"}}})
        self.write_settings({})
        for shape in shapes:
            for dry_run in (False, True):
                with self.subTest(shape=shape, dry_run=dry_run):
                    (self.target / "settings.json").write_text(json.dumps(shape), encoding="utf-8")
                    result = self.install(dry_run=dry_run)
                    self.assertEqual(result.exit_code, 1)
                    self.assertEqual([r.status for r in result.rows], [inst.ERROR])
                    self.assertEqual(list_files(self.target), ["settings.json"])

    def test_symlinked_settings_is_written_through(self) -> None:
        real = self.base / "dotfiles" / "settings.json"
        real.parent.mkdir()
        real.write_text('{"model": "x"}\n', encoding="utf-8")
        self.target.mkdir(parents=True)
        (self.target / "settings.json").symlink_to(real)
        self.install()
        link = self.target / "settings.json"
        self.assertTrue(link.is_symlink(), "link must stay a link")
        data = json.loads(real.read_text(encoding="utf-8"))
        self.assertEqual(data["model"], "x")
        self.assertIn("PreToolUse", data["hooks"])
        self.assertEqual(len(self.backups()), 1)

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


class SafetyTest(InstallTestCase):
    def test_force_backs_up_the_file_it_overwrites(self) -> None:
        self.install()
        rule = self.target / "rules/a.md"
        rule.write_text("# my own edits\n", encoding="utf-8")
        rule.chmod(0o640)
        result = self.install(force=True)
        backups = sorted(rule.parent.glob("a.md.bak-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(encoding="utf-8"), "# my own edits\n")
        self.assertEqual(backups[0].stat().st_mode & 0o777, 0o600)
        row = next(r for r in result.rows if r.item == "rules/a.md")
        self.assertIn(backups[0].name, row.detail)
        self.assertEqual(rule.read_text(encoding="utf-8"), FAKE_FILES["rules/a.md"])

    def test_symlink_destinations_are_left_untouched_even_with_force(self) -> None:
        outside = self.base / "dotfiles" / "a.md"
        outside.parent.mkdir()
        outside.write_text("# dotfiles copy\n", encoding="utf-8")
        (self.target / "rules").mkdir(parents=True)
        (self.target / "rules/a.md").symlink_to(outside)
        (self.target / "commands").mkdir()
        (self.target / "commands/lane.md").symlink_to(self.base / "nowhere.md")  # dangling
        result = self.install(force=True)
        for rel in ("rules/a.md", "commands/lane.md"):
            row = next(r for r in result.rows if r.item == rel)
            self.assertEqual((row.status, row.detail), (inst.DIFFERS, "symlink, left untouched"))
            self.assertTrue((self.target / rel).is_symlink())
        self.assertEqual(outside.read_text(encoding="utf-8"), "# dotfiles copy\n")
        self.assertFalse((self.base / "nowhere.md").exists())
        self.assertEqual(sorted(outside.parent.iterdir()), [outside], "no backup next to the link target")

    def test_relative_target_is_wired_as_absolute_path(self) -> None:
        cwd = os.getcwd()
        os.chdir(self.base)
        try:
            inst.install(Path("home/.claude"), assets_root=self.assets)
        finally:
            os.chdir(cwd)
        command = self.settings()["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
        self.assertEqual(command, f"python3 {self.target}/hooks/guard.py")

    def test_target_needing_shell_quoting_is_refused(self) -> None:
        for name in ("my claude", "it's"):
            with self.subTest(name=name):
                target = self.base / name / ".claude"
                for dry_run in (False, True):
                    result = inst.install(target, assets_root=self.assets, dry_run=dry_run)
                    self.assertEqual(result.exit_code, 1)
                    self.assertEqual([r.status for r in result.rows], [inst.ERROR])
                self.assertFalse((self.base / name).exists())


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
        self.assertEqual(by_cat["agents"], ["agents/review-readonly.md"])
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
