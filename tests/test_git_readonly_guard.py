"""Tests for the scout-git read-only git guard hook and the scout-git agent file.

The hook is a standalone script (hyphenated file name), so it is loaded by path.
Decisions are pure string checks; only `-C` containment touches the file system,
and that uses a temporary directory. No real git, no network, no real ~/.claude.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ASSETS = Path(__file__).resolve().parents[1] / "src" / "lapbar" / "assets"
HOOK_PATH = ASSETS / "hooks" / "git-readonly-guard.py"
AGENT_PATH = ASSETS / "agents" / "scout-git.md"


def _load_hook():
    """Load the hook by path without leaving bytecode inside the packaged assets."""
    spec = importlib.util.spec_from_file_location("git_readonly_guard", HOOK_PATH)
    module = importlib.util.module_from_spec(spec)
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


def _payload(command, cwd, agent_type="scout-git", tool_name="Bash", **extra_input):
    payload = {"tool_name": tool_name, "cwd": cwd, "tool_input": {"command": command, **extra_input}}
    return payload if agent_type is None else {**payload, "agent_type": agent_type}


def _decision(output):
    return None if output is None else output["hookSpecificOutput"]["permissionDecision"]


ALLOW_COMMANDS = (
    "git log -n 20 --oneline",
    "git show HEAD~2 -- src/a.py",
    "git blame -L 10,20 f",
    "git diff main...HEAD --stat",
    "git -C sub log",
    "git log --format='%H %s' -n 5",
    "git log main..HEAD",
    "git log HEAD~2 origin/main",
    "git diff main...HEAD -- src",
    "git --no-optional-locks status",
    "git shortlog -s -n HEAD",
    "git shortlog -s -n HEAD -- src",
    "git branch --list 'feat/*'",
    "git branch",
    "git branch -a --contains HEAD",
    "git tag --list 'v*'",
    "git stash list",
    "git stash show --stat",
    "git worktree list",
    "git remote -v",
    "git reflog show -n 5",
    "git config --get user.name",
    "git config --list",
    "git --no-pager log -n 3",
    "git grep -n TODO -- src",
    "git grep -e TODO -- src",
    "/usr/bin/git status --short",
    "git rev-parse --abbrev-ref HEAD",
    "git log --since '2 weeks' --author alice -- src",
    "git ls-files -- '*.py'",
    "git grep -n '/api/v1' -- src",
    "git grep -n ../x -- src",
    "git shortlog -s -n --all",
    "git tag --no-contains HEAD",
)

DENY_COMMANDS = (
    "git diff | cat",
    "git log > /tmp/x",
    "git log -n 3 ; rm -rf x",
    "git log && git status",
    "git log || true",
    "git log &",
    "git diff --output=/tmp/x",
    "git diff --output /tmp/x",
    "git diff --output-indicator-new=x",
    "git -c core.pager=less log",
    "git --exec-path=/tmp log",
    "git --git-dir=/tmp/x log",
    "git --work-tree=/tmp log",
    "git -p log",
    "git --paginate log",
    "git push origin main",
    "git stash pop",
    "git stash",
    "git config user.name x",
    "git config -e",
    "git config --edit",
    "git -C .. log",
    "git -C /etc log",
    "ls",
    "cat README.md",
    "git log $(cat x)",
    "git log `cat x`",
    'git log "a | b"',
    "git branch foo",
    "git branch -d foo",
    "git branch -D foo",
    "git branch -m a b",
    "git branch -f foo HEAD",
    "git branch --delete foo",
    "git branch --force foo",
    "git branch --set-upstream-to=origin/x",
    "git tag v1",
    "git tag -a v1",
    "git show --ext-diff HEAD",
    "git diff --textconv HEAD",
    "git grep -O TODO",
    "git grep --open-files-in-pager TODO",
    "git log -p --exec x",
    "git log 'unclosed",
    "git log\nrm -rf x",
    "git log -n --output=x",
    "git commit -m x",
    "git checkout main",
    "git worktree add x",
    "git remote add x y",
    "git reflog expire --all",
    "git -C ~ log",
    "git log -- *",
    "git log # --oneline",
    'git reflog show --grep "x\\"" * "README.md"',
    "git log =x",
    "git log --grep '=x'",
    "git status '=x'",
    "/tmp/nowhere/git log -n 3",
    "/usr/bin/../../tmp/x/git status",
    "git diff --stat /etc/hosts /dev/null",
    "git log -- ../other",
    "git show HEAD -- sub/../../other",
    "git log -n 1 --format=%GK",
    "git log -n 1 --format='%G?'",
    "git log --pretty=format:%GS",
    "git log --format '%G?'",
    "git shortlog -s -n",
    "git shortlog -s -n -- src",
    "git log -n 1 --format=%+GK",
    "git log -n 1 --format='% G?'",
    "git log --pretty=format:%-GS",
    "git grep -n TODO ../other -- src",
    "git grep -n TODO -- /etc",
    "git grep -e TODO /etc/hosts",
    "git status --no-optional-locks",
    "",
)


class GuardDecisionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hook = _load_hook()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.cwd = self._tmp.name
        os.mkdir(os.path.join(self.cwd, "sub"))

    def tearDown(self):
        self._tmp.cleanup()

    def test_allow_commands(self):
        for command in ALLOW_COMMANDS:
            with self.subTest(command=command):
                self.assertEqual(_decision(self.hook.evaluate(_payload(command, self.cwd))), "allow")

    def test_deny_commands(self):
        for command in DENY_COMMANDS:
            with self.subTest(command=command):
                output = self.hook.evaluate(_payload(command, self.cwd))
                self.assertEqual(_decision(output), "deny")
                reason = output["hookSpecificOutput"]["permissionDecisionReason"]
                self.assertIn("use Read/Grep/Glob for files", reason)

    def test_missing_agent_type_is_denied(self):
        output = self.hook.evaluate(_payload("git log", self.cwd, agent_type=None))
        self.assertEqual(_decision(output), "deny")
        self.assertIn("agent-scoped", output["hookSpecificOutput"]["permissionDecisionReason"])

    def test_other_agent_type_is_denied(self):
        output = self.hook.evaluate(_payload("git log", self.cwd, agent_type="general-purpose"))
        self.assertEqual(_decision(output), "deny")

    def test_review_git_agent_is_allowed(self):
        self.assertEqual(_decision(self.hook.evaluate(_payload("git log", self.cwd, "review-git"))), "allow")

    def test_non_bash_tool_is_ignored(self):
        self.assertIsNone(self.hook.evaluate(_payload("git log", self.cwd, tool_name="Read")))

    def test_symlink_escape_via_dash_c_is_denied(self):
        outside = tempfile.mkdtemp()
        try:
            os.symlink(outside, os.path.join(self.cwd, "link"))
            output = self.hook.evaluate(_payload("git -C link log", self.cwd))
            self.assertEqual(_decision(output), "deny")
        finally:
            os.rmdir(outside)

    def _reason(self, command):
        return self.hook.evaluate(_payload(command, self.cwd))["hookSpecificOutput"]["permissionDecisionReason"]

    def test_signature_placeholder_with_format_modifier_reason(self):
        for command in ("git log -n 1 --format=%+GK", "git log -n 1 --format='% G?'", "git log --pretty=format:%-GS"):
            with self.subTest(command=command):
                self.assertIn("%G format placeholders run gpg", self._reason(command))

    def test_grep_pattern_is_not_a_path_but_later_paths_are(self):
        self.assertIn("path ../other is outside", self._reason("git grep -n TODO ../other -- src"))
        self.assertIn("path /etc is outside", self._reason("git grep -n TODO -- /etc"))

    def test_no_optional_locks_after_subcommand_reason(self):
        self.assertIn("--no-optional-locks is a global option; put it before the subcommand "
                      "(status gets it automatically)", self._reason("git status --no-optional-locks"))

    def test_diff_gets_no_ext_diff_and_keeps_other_keys(self):
        payload = _payload("git diff --stat", self.cwd, description="d", timeout=5000)
        updated = self.hook.evaluate(payload)["hookSpecificOutput"]["updatedInput"]
        self.assertEqual(updated, {"command": "git diff --no-ext-diff --no-textconv --stat",
                                   "description": "d", "timeout": 5000})

    def test_no_ext_diff_goes_after_subcommand_with_globals(self):
        updated = self.hook.evaluate(_payload("git --no-pager -C sub show HEAD", self.cwd))
        command = updated["hookSpecificOutput"]["updatedInput"]["command"]
        self.assertEqual(command, "git --no-pager -C sub show --no-ext-diff --no-textconv --no-show-signature HEAD")

    def test_no_ext_diff_not_duplicated(self):
        output = self.hook.evaluate(_payload("git log --no-ext-diff -n 1", self.cwd))
        self.assertEqual(output["hookSpecificOutput"]["updatedInput"]["command"],
                         "git log --no-textconv --no-show-signature --no-ext-diff -n 1")

    def test_status_gets_no_optional_locks_and_keeps_other_keys(self):
        output = self.hook.evaluate(_payload("git status", self.cwd, run_in_background=False))
        self.assertEqual(output["hookSpecificOutput"]["updatedInput"],
                         {"command": "git --no-optional-locks status", "run_in_background": False})

    def _rewritten(self, command):
        output = self.hook.evaluate(_payload(command, self.cwd))
        self.assertEqual(_decision(output), "allow", command)
        return output["hookSpecificOutput"]["updatedInput"]["command"]

    def test_rewrites(self):
        cases = (
            ("git diff --stat", "git diff --no-ext-diff --no-textconv --stat"),
            ("git log --no-ext-diff -n 1", "git log --no-textconv --no-show-signature --no-ext-diff -n 1"),
            ("git log --no-textconv -n 1", "git log --no-ext-diff --no-show-signature --no-textconv -n 1"),
            ("git show --no-ext-diff --no-textconv --no-show-signature HEAD",
             "git show --no-ext-diff --no-textconv --no-show-signature HEAD"),
            ("git --no-optional-locks status -s", "git --no-optional-locks status -s"),
            ("git --no-pager status", "git --no-optional-locks --no-pager status"),
            ('git ls-files -- "*.py"', "git ls-files -- '*.py'"),
            ('git reflog show --grep "a b"',
             "git reflog show --no-ext-diff --no-textconv --no-show-signature --grep 'a b'"),
            ("git stash list -p", "git stash list --no-ext-diff --no-textconv --no-show-signature -p"),
            ("git stash show --stat", "git stash show --no-ext-diff --no-textconv --no-show-signature --stat"),
            ("git reflog show -p -n 3", "git reflog show --no-ext-diff --no-textconv --no-show-signature -p -n 3"),
            ("git blame -L 10,20 f", "git blame --no-textconv -L 10,20 f"),
            ("git blame --no-textconv f", "git blame --no-textconv f"),
            ("git -C sub stash list", "git -C sub stash list --no-ext-diff --no-textconv --no-show-signature"),
            ("git reflog", "git reflog"),
        )
        for command, expected in cases:
            with self.subTest(command=command):
                self.assertEqual(self._rewritten(command), expected)

    def test_absolute_path_inside_cwd_is_allowed(self):
        inside = os.path.join(os.path.realpath(self.cwd), "sub", "a.py")
        self.assertEqual(self._rewritten("git log -- " + inside),
                         "git log --no-ext-diff --no-textconv --no-show-signature -- " + inside)

    def test_dotdot_path_resolves_from_dash_c_directory(self):
        self.assertEqual(self._rewritten("git -C sub log -- ../a.py"),
                         "git -C sub log --no-ext-diff --no-textconv --no-show-signature -- ../a.py")

    def test_symlinked_absolute_git_to_real_git_is_allowed(self):
        link = os.path.join(self.cwd, "git")
        os.symlink("/usr/bin/git", link)
        self.assertEqual(_decision(self.hook.evaluate(_payload(link + " status", self.cwd))), "allow")

    def test_shortlog_without_revision_reason(self):
        output = self.hook.evaluate(_payload("git shortlog -s -n", self.cwd))
        self.assertIn("shortlog needs a revision such as HEAD", output["hookSpecificOutput"]["permissionDecisionReason"])

    def test_escaped_quote_does_not_hide_glob(self):
        output = self.hook.evaluate(_payload('git reflog show --grep "x\\"" * "README.md"', self.cwd))
        self.assertIn("'*'", output["hookSpecificOutput"]["permissionDecisionReason"])

    def test_payload_is_not_mutated(self):
        payload = _payload("git diff", self.cwd, description="d")
        snapshot = json.dumps(payload, sort_keys=True)
        self.hook.evaluate(payload)
        self.assertEqual(json.dumps(payload, sort_keys=True), snapshot)

    def test_internal_error_is_denied_with_class_name(self):
        payload = {"tool_name": "Bash", "agent_type": "scout-git", "cwd": self.cwd, "tool_input": "not-a-dict"}
        output = self.hook.evaluate(payload)
        self.assertEqual(_decision(output), "deny")


class GuardProcessTest(unittest.TestCase):
    def _run(self, stdin_text, *args):
        return subprocess.run([sys.executable, str(HOOK_PATH), *args], input=stdin_text,
                              capture_output=True, text=True, timeout=30)

    def test_stdin_allow_prints_json(self):
        with tempfile.TemporaryDirectory() as cwd:
            result = self._run(json.dumps(_payload("git log -n 3", cwd)))
        self.assertEqual(result.returncode, 0)
        self.assertEqual(_decision(json.loads(result.stdout.strip().splitlines()[-1])), "allow")

    def test_stdin_deny_prints_json(self):
        result = self._run(json.dumps(_payload("git diff | cat", ".")))
        self.assertEqual(result.returncode, 0)
        self.assertEqual(_decision(json.loads(result.stdout.strip().splitlines()[-1])), "deny")

    def test_non_bash_prints_nothing(self):
        result = self._run(json.dumps(_payload("git log", ".", tool_name="Read")))
        self.assertEqual((result.returncode, result.stdout), (0, ""))

    def test_garbage_stdin_is_denied(self):
        result = self._run("not json")
        self.assertEqual(_decision(json.loads(result.stdout.strip().splitlines()[-1])), "deny")

    def test_self_test_passes(self):
        result = self._run("", "--self-test")
        self.assertEqual(result.returncode, 0, result.stdout)
        last = result.stdout.strip().splitlines()[-1]
        self.assertRegex(last, r"^self-test: (\d+)/\1 PASS$")
        self.assertGreaterEqual(int(last.split()[1].split("/")[0]), 40)


class ScoutGitAgentTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = AGENT_PATH.read_text(encoding="utf-8")
        _, cls.front, cls.body = cls.text.split("---\n", 2)

    def test_frontmatter_fields(self):
        lines = self.front.splitlines()
        self.assertIn("name: scout-git", lines)
        self.assertIn("tools: Read, Grep, Glob, Bash", lines)
        self.assertIn("model: opus", lines)
        self.assertTrue(any(line.startswith("description: ") for line in lines))

    def test_frontmatter_hook_block(self):
        expected = ("hooks:\n"
                    "  PreToolUse:\n"
                    "    - matcher: \"Bash\"\n"
                    "      hooks:\n"
                    "        - type: command\n"
                    "          command: \"python3 ~/.claude/hooks/git-readonly-guard.py\"\n")
        self.assertIn(expected, self.front)

    def test_text_is_ascii_english(self):
        self.assertTrue(self.text.isascii())

    def test_block_list_names_parentheses_and_pickaxe_advice(self):
        self.assertIn("parentheses", self.body)
        self.assertIn("for -S/-G pick a substring without these characters", self.body)

    def test_body_length_is_compact(self):
        self.assertLessEqual(len(self.body.strip().splitlines()), 45)


if __name__ == "__main__":
    unittest.main()
