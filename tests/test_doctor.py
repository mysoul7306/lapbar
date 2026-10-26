from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from install_fixtures import BAD_HOOK, make_assets

from lapbar import assets as asset_mod

from lapbar import doctor
from lapbar import install as inst


class DoctorCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name).resolve()
        self.base = base
        self.assets = make_assets(base / "assets")
        self.target = base / "home" / ".claude"
        result = inst.install(self.target, assets_root=self.assets)
        self.assertEqual(result.exit_code, 0)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def diagnose(self) -> tuple[doctor.Check, ...]:
        return doctor.diagnose(self.target, assets_root=self.assets, python=sys.executable)

    def results(self, checks: tuple[doctor.Check, ...]) -> dict[tuple[str, str], str]:
        return {(c.kind, c.item): c.result for c in checks}


class DoctorTest(DoctorCase):
    def test_healthy_install_passes(self) -> None:
        checks = self.diagnose()
        self.assertEqual({c.result for c in checks}, {doctor.PASS})
        self.assertEqual(doctor.exit_code(checks), 0)
        kinds = [c.kind for c in checks]
        self.assertEqual(kinds.count("asset"), 7, "includes the agents asset")
        self.assertEqual(kinds.count("wiring"), 2, "event-null hook is not a wiring check")
        self.assertEqual(kinds.count("settings"), 2, "one row per wired command")
        self.assertEqual(kinds.count("selftest"), 2, "selftest: true hooks, wired or not")
        self.assertEqual(kinds.count("probe"), 4)

    def test_diagnose_order_is_asset_wiring_settings_selftest_probe(self) -> None:
        kinds = [c.kind for c in self.diagnose()]
        order = ["asset", "wiring", "settings", "selftest", "probe"]
        self.assertEqual(kinds, sorted(kinds, key=order.index))

    def test_missing_wiring_fails(self) -> None:
        path = self.target / "settings.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        path.write_text(json.dumps({**data, "hooks": {"SessionStart": data["hooks"]["SessionStart"]}}),
                        encoding="utf-8")
        checks = self.diagnose()
        self.assertEqual(self.results(checks)[("wiring", "hooks/guard.py")], doctor.FAIL)
        self.assertEqual(self.results(checks)[("wiring", "hooks/start.py")], doctor.PASS)
        self.assertEqual(doctor.exit_code(checks), 1)

    def rewire_guard(self, matcher: str, command: str) -> None:
        path = self.target / "settings.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        guard = {"matcher": matcher, "hooks": [{"type": "command", "command": command}]}
        path.write_text(json.dumps({"hooks": {**data["hooks"], "PreToolUse": [guard]}}),
                        encoding="utf-8")

    def test_home_relative_wiring_passes(self) -> None:
        self.rewire_guard("Bash", "python3 ~/.claude/hooks/guard.py")
        with mock.patch.dict(os.environ, {"HOME": str(self.base / "home")}):
            checks = self.diagnose()
        self.assertEqual(self.results(checks)[("wiring", "hooks/guard.py")], doctor.PASS)

    def test_wiring_under_other_matcher_fails(self) -> None:
        self.rewire_guard("Write", f"python3 {self.target}/hooks/guard.py")
        checks = self.diagnose()
        self.assertEqual(self.results(checks)[("wiring", "hooks/guard.py")], doctor.FAIL)
        self.assertEqual(doctor.exit_code(checks), 1)

    def test_failing_selftest_fails_with_reason(self) -> None:
        (self.target / "hooks/guard.py").write_text(BAD_HOOK, encoding="utf-8")
        checks = self.diagnose()
        failed = [c for c in checks if c.kind == "selftest"][0]
        self.assertEqual(failed.result, doctor.FAIL)
        self.assertIn("exit 3", failed.detail)
        self.assertEqual(doctor.exit_code(checks), 1)

    def test_missing_asset_file_fails(self) -> None:
        (self.target / "rules/a.md").unlink()
        checks = self.diagnose()
        self.assertEqual(self.results(checks)[("asset", "rules/a.md")], doctor.FAIL)
        self.assertEqual(doctor.exit_code(checks), 1)

    def test_run_prints_table_and_returns_exit_code(self) -> None:
        out = io.StringIO()
        with mock.patch("lapbar.assets.default_root", return_value=self.assets), \
                contextlib.redirect_stdout(out):
            healthy = doctor.run(argparse.Namespace(target=str(self.target)))
            (self.target / "settings.json").write_text("{}", encoding="utf-8")
            broken = doctor.run(argparse.Namespace(target=str(self.target)))
        self.assertEqual((healthy, broken), (0, 1))
        self.assertIn("selftest", out.getvalue())
        self.assertIn("FAIL", out.getvalue())


class ProbeTest(DoctorCase):
    def probes(self, checks: tuple[doctor.Check, ...]) -> dict[str, doctor.Check]:
        return {c.item: c for c in checks if c.kind == "probe"}

    def replace_hook(self, rel: str, source: str) -> None:
        (self.target / rel).write_text(source, encoding="utf-8")

    def test_probes_pass_and_name_items(self) -> None:
        probes = self.probes(self.diagnose())
        self.assertEqual(sorted(probes), [
            "hooks/agent-guard.py :: deny word is denied",
            "hooks/agent-guard.py :: plain command passes",
            "hooks/guard.py :: deny word is denied",
            "hooks/guard.py :: plain command passes"])
        self.assertEqual({c.result for c in probes.values()}, {doctor.PASS})

    def test_unexpected_decision_fails(self) -> None:
        self.replace_hook("hooks/agent-guard.py", "import sys\nsys.stdin.read()\n")
        probes = self.probes(self.diagnose())
        denied = probes["hooks/agent-guard.py :: deny word is denied"]
        self.assertEqual(denied.result, doctor.FAIL)
        self.assertIn("expected deny, got allow", denied.detail)
        self.assertEqual(probes["hooks/agent-guard.py :: plain command passes"].result, doctor.PASS)

    def test_exit_2_without_json_counts_as_deny(self) -> None:
        self.replace_hook("hooks/agent-guard.py", "import sys\nsys.stdin.read()\nsys.exit(2)\n")
        probes = self.probes(self.diagnose())
        self.assertEqual(probes["hooks/agent-guard.py :: deny word is denied"].result, doctor.PASS)
        self.assertEqual(probes["hooks/agent-guard.py :: plain command passes"].result, doctor.FAIL)

    def test_plain_line_before_deny_json_is_not_a_decision(self) -> None:
        noisy = ("import json, sys\nsys.stdin.read()\nprint('debug')\n"
                 "print(json.dumps({'hookSpecificOutput': {'permissionDecision': 'deny'}}))\n")
        self.replace_hook("hooks/agent-guard.py", noisy)
        probes = self.probes(self.diagnose())
        denied = probes["hooks/agent-guard.py :: deny word is denied"]
        self.assertEqual(denied.result, doctor.FAIL)
        self.assertIn("expected deny, got allow", denied.detail)
        self.assertIn("stdout is not a single JSON object", denied.detail)

    def test_pretty_printed_deny_json_counts_as_deny(self) -> None:
        pretty = ("import json, sys\nsys.stdin.read()\n"
                  "print(json.dumps({'hookSpecificOutput': {'permissionDecision': 'deny'}}, indent=2))\n")
        self.replace_hook("hooks/agent-guard.py", pretty)
        probes = self.probes(self.diagnose())
        self.assertEqual(probes["hooks/agent-guard.py :: deny word is denied"].result, doctor.PASS)
        self.assertEqual(probes["hooks/agent-guard.py :: plain command passes"].result, doctor.FAIL)

    def test_crashing_hook_fails_with_exit_code_and_stderr(self) -> None:
        crash = "import sys\nprint('boom here', file=sys.stderr)\nsys.exit(3)\n"
        self.replace_hook("hooks/agent-guard.py", crash)
        checks = self.diagnose()
        failed = self.probes(checks)["hooks/agent-guard.py :: deny word is denied"]
        self.assertEqual(failed.result, doctor.FAIL)
        self.assertIn("exit 3", failed.detail)
        self.assertIn("boom here", failed.detail)
        self.assertEqual(doctor.exit_code(checks), 1)

    def test_missing_hook_file_fails_every_probe(self) -> None:
        (self.target / "hooks/agent-guard.py").unlink()
        probes = self.probes(self.diagnose())
        for name in ("deny word is denied", "plain command passes"):
            check = probes[f"hooks/agent-guard.py :: {name}"]
            self.assertEqual((check.result, check.detail), (doctor.FAIL, "hook file missing"))

    def test_timeout_fails_and_runs_with_stdin_cwd_and_limit(self) -> None:
        expired = subprocess.TimeoutExpired(cmd="hook", timeout=doctor.PROBE_TIMEOUT_SECONDS)
        hook = next(a for a in asset_mod.load_manifest(self.assets) if a.dst == "hooks/guard.py")
        with mock.patch("lapbar.doctor.subprocess.run", side_effect=expired) as run:
            checks = doctor.check_probes(hook, self.target, sys.executable)
        self.assertEqual({c.result for c in checks}, {doctor.FAIL})
        self.assertIn("timed out", checks[0].detail)
        argv = run.call_args.args[0]
        kwargs = run.call_args.kwargs
        self.assertEqual(argv, [sys.executable, str(self.target / "hooks/guard.py")])
        self.assertEqual([json.loads(c.kwargs["input"]) for c in run.call_args_list],
                         [p.input for p in hook.probes])
        self.assertEqual((kwargs["cwd"], kwargs["timeout"]),
                         (self.target, doctor.PROBE_TIMEOUT_SECONDS))


class SettingsCheckTest(DoctorCase):
    def add_commands(self, *commands: str) -> None:
        path = self.target / "settings.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        extra = {"matcher": "Write", "hooks": [{"type": "command", "command": c} for c in commands]}
        hooks = {**data["hooks"], "PostToolUse": [extra]}
        path.write_text(json.dumps({**data, "hooks": hooks}), encoding="utf-8")

    def settings_checks(self) -> dict[str, doctor.Check]:
        """Settings rows keyed by the full command they were built from."""
        return {c.item: c for c in self.diagnose() if c.kind == "settings"}

    def row(self, checks: dict[str, doctor.Check], command: str) -> doctor.Check:
        return checks[doctor.short_tail(command)]

    def write(self, rel: str, text: str) -> Path:
        path = self.base / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def test_every_command_is_checked_including_foreign_ones(self) -> None:
        good = self.write("other/ok.py", "print('ok')\n")
        broken = self.write("other/broken.py", "def f(:\n")
        shell = self.write("other/run.sh", "echo hi\n")
        missing = self.base / "other/gone.py"
        self.add_commands(f"python3 {good}", f"python3 {broken}", f"bash {shell} --flag",
                          f"python3 {missing}")
        checks = self.settings_checks()
        self.assertEqual(self.row(checks, f"python3 {good}").result, doctor.PASS)
        self.assertEqual(self.row(checks, f"bash {shell} --flag").result, doctor.PASS)
        failed = self.row(checks, f"python3 {broken}")
        self.assertEqual((failed.result, failed.detail), (doctor.FAIL, f"syntax error: {broken}:1"))
        gone = self.row(checks, f"python3 {missing}")
        self.assertEqual((gone.result, gone.detail), (doctor.FAIL, f"missing: {missing}"))
        lapbar_cmd = f"python3 {self.target}/hooks/guard.py"
        self.assertEqual(self.row(checks, lapbar_cmd).result, doctor.PASS)
        self.assertEqual(len(checks), 6, "2 lapbar commands + 4 foreign ones")

    def test_home_tokens_are_expanded(self) -> None:
        self.write("home/tools/hook.py", "print('ok')\n")
        self.add_commands("python3 ~/tools/hook.py", "python3 $HOME/tools/nope.py")
        with mock.patch.dict(os.environ, {"HOME": str(self.base / "home")}):
            checks = self.settings_checks()
        self.assertEqual(checks["python3 ~/tools/hook.py"].result, doctor.PASS)
        nope = checks["python3 $HOME/tools/nope.py"]
        self.assertEqual((nope.result, nope.detail), (doctor.FAIL, "missing: $HOME/tools/nope.py"))

    def test_unexpandable_and_relative_tokens_are_not_failed(self) -> None:
        self.add_commands("bash $CLAUDE_PROJECT_DIR/.claude/x.sh", "node hooks/rel.js")
        checks = self.settings_checks()
        for command in ("bash $CLAUDE_PROJECT_DIR/.claude/x.sh", "node hooks/rel.js"):
            self.assertEqual(checks[command].result, doctor.PASS)
            self.assertIn("not checked", checks[command].detail)

    def test_only_the_executed_file_is_checked(self) -> None:
        good = self.write("other/hook.py", "print('ok')\n")
        log = self.base / "logs/none.log"
        passing = (f"python3 {good} >> {log}", f"python3 {good} --log {log}",
                   f"python3 {good} >> {self.base}/x",
                   f"jq -c . >> {log}", "bash -c 'cd /nowhere && run'", f"FLAG=1 python3 {good}")
        self.add_commands(*passing, f"uv run --quiet {self.base}/gone.py", f"{self.base}/gone.sh arg")
        checks = self.settings_checks()
        for command in passing:
            row = self.row(checks, command)
            self.assertEqual(row.result, doctor.PASS, (command, row.detail))
        detail = self.row(checks, f"python3 {good} >> {self.base}/x").detail
        self.assertEqual(detail, "1 path(s) ok, not checked: 1 argument path(s)")
        for command, token in ((f"uv run --quiet {self.base}/gone.py", f"{self.base}/gone.py"),
                               (f"{self.base}/gone.sh arg", f"{self.base}/gone.sh")):
            row = self.row(checks, command)
            self.assertEqual((row.result, row.detail), (doctor.FAIL, f"missing: {token}"))

    def test_long_command_item_is_truncated(self) -> None:
        long_path = self.write("d/" + "x" * 90 + ".py", "pass\n")
        self.add_commands(f"python3 {long_path}")
        items = [i for i in self.settings_checks() if i.endswith("xxxx.py")]
        self.assertEqual(len(items), 1)
        self.assertEqual(len(items[0]), doctor.DETAIL_MAX)
        self.assertTrue(items[0].startswith("..."), "the script name survives truncation")

    def test_unreadable_settings_is_one_failure(self) -> None:
        (self.target / "settings.json").write_text("{not json", encoding="utf-8")
        checks = [c for c in self.diagnose() if c.kind == "settings"]
        self.assertEqual([c.result for c in checks], [doctor.FAIL])


class PackagedProbeTest(unittest.TestCase):
    def test_packaged_guards_answer_their_manifest_probes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp).resolve() / ".claude"
            with mock.patch.dict(os.environ, {"HOME": tmp}):
                inst.install(target, ("hooks",))
                hooks = [a for a in asset_mod.load_manifest() if a.is_hook and a.probes]
                checks = [c for h in hooks for c in doctor.check_probes(h, target, sys.executable)]
        self.assertEqual(len(checks), 6, [c.item for c in checks])
        self.assertEqual([c.result for c in checks], [doctor.PASS] * 6,
                         [(c.item, c.detail) for c in checks])


if __name__ == "__main__":
    unittest.main()
