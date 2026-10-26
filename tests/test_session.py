"""Tests for `lapbar session`: one-session hook, instruction, subagent and token breakdown."""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from lapbar import cli, session, session_render  # noqa: E402

FABLE = "claude-fable-5-1"
OPUS = "claude-opus-5-5"
SONNET = "claude-sonnet-5-5"
SESSION_ENV = "CLAUDE_CODE_SESSION_ID"
MAX_LINES = 40
AGENT_MTIME = 2_200_000_000
READ_CONTEXT = "z" * 3000
JSON_STDOUT = json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": READ_CONTEXT}})


def attachment(kind, **fields):
    return {"type": "attachment", "attachment": {"type": kind, **fields}}


def hook_success(name, stdout, command, ms, content=""):
    return attachment("hook_success", hookName=name, hookEvent=name.split(":")[0], toolUseID="t",
                      content=content, stdout=stdout, stderr="", exitCode=0, command=command, durationMs=ms)


def assistant(rid, model, out=0, inp=0, cw=0, cr=0, effort=None, blocks=()):
    record = {
        "type": "assistant",
        "requestId": rid,
        "timestamp": "2026-10-26T01:00:00Z",
        "message": {"model": model, "content": list(blocks), "usage": {
            "input_tokens": inp, "output_tokens": out,
            "cache_creation_input_tokens": cw, "cache_read_input_tokens": cr,
        }},
    }
    return {**record, "effort": effort} if effort else record


def tool_use(block_id, tool_name, **tool_input):
    return {"type": "tool_use", "id": block_id, "name": tool_name, "input": tool_input}


def write_jsonl(path: Path, records, mtime: float | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [r if isinstance(r, str) else json.dumps(r, separators=(",", ":")) for r in records]
    path.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime, mtime))


AGENT_1 = tool_use("tu1", "Agent", description="d", prompt="p", model="opus", effort="high",
                   subagent_type="general-purpose")
AGENT_2 = tool_use("tu2", "Agent", description="d", prompt="p", model="opus", subagent_type="review-readonly")
WORKFLOW = tool_use("tu3", "Workflow", name="lane")


def head_records():
    return [
        hook_success("SessionStart:startup", "x" * 3600, "python3 /h/a.py", 40),
        hook_success("PreToolUse:Bash", "y" * 12000, "python3 /h/b.py", 10, content="ab"),
        attachment("hook_additional_context", hookName="SessionStart:startup", hookEvent="SessionStart",
                   toolUseID="t", content=["c" * 3405, {"text": "d" * 100}, {"other": 1}]),
        hook_success("PreToolUse:Read", JSON_STDOUT, "python3 /h/c.py", 15),
        attachment("hook_additional_context", hookName="PreToolUse:Read", hookEvent="PreToolUse",
                   toolUseID="t", content=[READ_CONTEXT]),
        hook_success("Stop", json.dumps({"systemMessage": "warn" * 5}), "X", 40),
        attachment("hook_system_message", hookName="Stop", hookEvent="Stop", toolUseID="t", content="warn" * 5),
        attachment("instructions", files=[{"path": "/p/CLAUDE.md", "content": "a" * 500}]),
        attachment("instructions", files=[
            {"path": "/p/CLAUDE.md", "content": "a" * 800},
            {"path": "/p/rules/x.md", "content": "b" * 1200},
        ]),
        {"type": "system", "subtype": "stop_hook_summary", "hookCount": 3, "hookInfos": [
            {"command": "python3 /h/b.py", "durationMs": 30},
            {"command": "stop-ledger", "durationMs": 5},
            {"command": "X", "durationMs": 40},
        ]},
        assistant("r1", FABLE, out=5, inp=10, blocks=[AGENT_1]),
        assistant("r1", FABLE, out=20, inp=10, blocks=[AGENT_1]),
        assistant("r2", FABLE, out=3, cr=500, blocks=[AGENT_2, WORKFLOW]),
        assistant("r3", FABLE, out=1, cw=70, blocks=[{"type": "text", "text": "hi"}]),
        assistant("syn", "<synthetic>", out=999, inp=999),
        {"type": "mystery", "payload": 1},
        "not json at all",
    ]


def build_fixture(root: Path) -> Path:
    head = root / "-proj-a" / "s1.jsonl"
    write_jsonl(head, head_records(), mtime=2_000_000_000)
    write_jsonl(head.parent / "s1" / "subagents" / "agent-1.jsonl", [
        {**assistant("r10", OPUS, out=50, inp=100, cr=1000, effort="high"), "agentId": "a1"},
        {**assistant("r10", OPUS, out=60, inp=100, cr=1000, effort="high"), "agentId": "a1"},
        {**assistant("r11", SONNET, out=7, effort="low"), "agentId": "a1"},
        {**assistant("r3", OPUS, out=77, cw=5, effort="high"), "agentId": "a1"},
    ], mtime=AGENT_MTIME)
    write_jsonl(head.parent / "s1" / "subagents" / "workflows" / "wf_1" / "agent-2.jsonl", [
        {**assistant("r12", OPUS, out=9, cw=20, effort="medium"), "agentId": "a2"},
    ], mtime=AGENT_MTIME)
    write_jsonl(root / "-proj-b" / "old.jsonl", [assistant("o1", FABLE, out=1)], mtime=1_000_000_000)
    return head


def parse(argv):
    parser = argparse.ArgumentParser()
    session.add_parser(parser.add_subparsers(dest="command"))
    return parser.parse_args(["session", *argv])


def run_session(root: Path, *argv, env_id: str | None = None):
    out, err = io.StringIO(), io.StringIO()
    environ = {k: v for k, v in os.environ.items() if k != SESSION_ENV}
    if env_id is not None:
        environ = {**environ, SESSION_ENV: env_id}
    with mock.patch.dict(os.environ, environ, clear=True), redirect_stdout(out), redirect_stderr(err):
        code = session.run(parse(["--projects-dir", str(root), *argv]))
    return code, out.getvalue(), err.getvalue()


class SessionAggregateTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.head = build_fixture(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def report_json(self, *argv, env_id=None):
        code, out, err = run_session(self.root, "--json", *argv, env_id=env_id)
        self.assertEqual(code, 0, err)
        return json.loads(out)

    def test_hook_injection_by_name(self):
        data = self.report_json(str(self.head))
        self.assertEqual(data["hooks"], {
            "SessionStart:startup": {"count": 2, "chars": 3600 + 3405 + 100 + 12, "tokens_est": 7117 // 3,
                                     "over_10k": 0},
            "PreToolUse:Bash": {"count": 1, "chars": 12002, "tokens_est": 4000, "over_10k": 1},
            "PreToolUse:Read": {"count": 2, "chars": 3000, "tokens_est": 1000, "over_10k": 0},
            "Stop": {"count": 2, "chars": 20, "tokens_est": 6, "over_10k": 0},
        })
        self.assertEqual(data["hook_totals"], {"count": 7, "chars": 22139, "tokens_est": 22139 // 3,
                                               "over_10k": 1})
        self.assertIn("estimate", data["estimate"])

    def test_json_stdout_is_not_counted_on_top_of_its_attachments(self):
        hooks = self.report_json(str(self.head))["hooks"]
        self.assertEqual(hooks["PreToolUse:Read"]["chars"], len(READ_CONTEXT))
        self.assertEqual(hooks["Stop"]["chars"], len("warn" * 5))

    def test_hooks_scope_is_head_only(self):
        self.assertEqual(self.report_json(str(self.head))["scope"], {"hooks": "head"})
        code, out, err = run_session(self.root, str(self.head))
        self.assertEqual(code, 0, err)
        self.assertIn("Hooks (head session only, injected text by hook name)", out)
        self.assertIn("Hook time (head session only, by total ms)", out)

    def test_hook_time_from_attachments_and_stop_summary(self):
        data = self.report_json(str(self.head))
        self.assertEqual(data["hook_time"], {
            "python3 /h/a.py": {"count": 1, "total_ms": 40, "mean_ms": 40.0},
            "python3 /h/b.py": {"count": 2, "total_ms": 40, "mean_ms": 20.0},
            "python3 /h/c.py": {"count": 1, "total_ms": 15, "mean_ms": 15.0},
            "stop-ledger": {"count": 1, "total_ms": 5, "mean_ms": 5.0},
            "X": {"count": 1, "total_ms": 40, "mean_ms": 40.0},
        })

    def test_last_instructions_attachment_wins(self):
        data = self.report_json(str(self.head))
        self.assertEqual(data["instructions"]["loads"], 2)
        self.assertEqual(data["instructions"]["total_chars"], 2000)
        self.assertEqual(data["instructions"]["files"], [
            {"path": "/p/rules/x.md", "chars": 1200},
            {"path": "/p/CLAUDE.md", "chars": 800},
        ])

    def test_subagent_calls_deduped_by_tool_use_id(self):
        data = self.report_json(str(self.head))
        self.assertEqual(data["subagent_calls"], [
            {"subagent_type": "general-purpose", "model": "opus", "effort": "high", "count": 1},
            {"subagent_type": "review-readonly", "model": "opus", "effort": "-", "count": 1},
        ])
        self.assertEqual(data["workflow_calls"], 1)

    def test_head_usage_dedupes_requests_and_drops_synthetic(self):
        data = self.report_json(str(self.head))
        self.assertEqual(data["head_usage"]["requests"], 3)
        self.assertEqual(data["head_usage"]["by_model"], {FABLE: {
            "requests": 3, "input_tokens": 10, "output_tokens": 24,
            "cache_creation_input_tokens": 70, "cache_read_input_tokens": 500,
        }})

    def test_subagent_usage_by_model_and_effort(self):
        data = self.report_json(str(self.head))
        usage = data["subagent_usage"]
        self.assertEqual(usage["requests"], 3)
        self.assertEqual(usage["by_model"], {
            OPUS: {"requests": 2, "input_tokens": 100, "output_tokens": 69,
                   "cache_creation_input_tokens": 20, "cache_read_input_tokens": 1000},
            SONNET: {"requests": 1, "input_tokens": 0, "output_tokens": 7,
                     "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
        })
        self.assertEqual(usage["by_effort"], {"high": 1, "low": 1, "medium": 1})

    def test_session_block(self):
        data = self.report_json(str(self.head))
        self.assertEqual(data["session"], {"id": "s1", "project_dir": "-proj-a",
                                           "transcript": str(self.head), "subagent_files": 2})

    def test_head_record_wins_over_subagent_record_with_same_request_id(self):
        data = self.report_json(str(self.head))
        self.assertEqual(data["head_usage"]["by_model"][FABLE]["cache_creation_input_tokens"], 70)
        self.assertEqual(data["subagent_usage"]["requests"], 3)
        self.assertEqual(data["subagent_usage"]["by_model"][OPUS]["output_tokens"], 69)

    def test_latest_picks_newest_head_file_not_newer_subagent_file(self):
        self.assertGreater(AGENT_MTIME, self.head.stat().st_mtime)
        data = self.report_json("--latest")
        self.assertEqual(data["session"]["id"], "s1")

    def test_relative_transcript_path_keeps_project_dir(self):
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(self.head.parent)
        info = self.report_json("s1.jsonl")["session"]
        self.assertEqual(info["project_dir"], "-proj-a")
        self.assertTrue(Path(info["transcript"]).is_absolute(), info["transcript"])

    def test_transcript_and_latest_together_is_usage_error(self):
        code, out, err = run_session(self.root, str(self.head), "--latest")
        self.assertEqual((code, out), (2, ""))
        self.assertTrue(err.startswith("ERROR: "), err)

    def test_subagent_file_as_transcript_is_rejected(self):
        agent = self.head.parent / "s1" / "subagents" / "agent-1.jsonl"
        code, out, err = run_session(self.root, str(agent))
        self.assertEqual((code, out), (2, ""))
        self.assertIn("subagent", err)

    def test_os_error_while_finding_transcript_is_one_error_line(self):
        with mock.patch.object(session, "head_files", side_effect=PermissionError("denied")):
            code, out, err = run_session(self.root, "--latest")
        self.assertEqual((code, out), (2, ""))
        self.assertTrue(err.startswith("ERROR: "), err)
        self.assertEqual(len(err.strip().splitlines()), 1)

    def test_no_argument_uses_session_env(self):
        write_jsonl(self.root / "-proj-c" / "newer.jsonl", [assistant("n1", FABLE, out=1)], mtime=2_100_000_000)
        self.assertEqual(self.report_json(env_id="s1")["session"]["id"], "s1")
        self.assertEqual(self.report_json()["session"]["id"], "newer")

    def test_unknown_env_session_is_usage_error(self):
        code, out, err = run_session(self.root, env_id="nope")
        self.assertEqual((code, out), (2, ""))
        self.assertTrue(err.startswith("ERROR: "), err)
        self.assertEqual(len(err.strip().splitlines()), 1)

    def test_env_session_id_with_path_parts_is_rejected(self):
        code, _out, err = run_session(self.root, env_id="../-proj-a/s1")
        self.assertEqual(code, 2)
        self.assertIn("ERROR: ", err)

    def test_missing_transcript_is_usage_error(self):
        code, out, err = run_session(self.root, str(self.root / "absent.jsonl"))
        self.assertEqual((code, out), (2, ""))
        self.assertTrue(err.startswith("ERROR: "), err)

    def test_missing_projects_dir_is_usage_error(self):
        code, _out, err = run_session(self.root / "nowhere", "--latest")
        self.assertEqual(code, 2)
        self.assertIn("ERROR: ", err)

    def test_session_without_subagents_reports_zero(self):
        data = self.report_json(str(self.root / "-proj-b" / "old.jsonl"))
        self.assertEqual(data["session"]["subagent_files"], 0)
        self.assertEqual(data["subagent_usage"], {"requests": 0, "by_model": {}, "by_effort": {}})
        self.assertEqual(data["hooks"], {})
        self.assertEqual(data["instructions"], {"loads": 0, "total_chars": 0, "tokens_est": 0, "files": []})

    def test_text_output_fits_and_ends_with_estimate_note(self):
        code, out, err = run_session(self.root, str(self.head))
        self.assertEqual(code, 0, err)
        lines = out.rstrip("\n").splitlines()
        self.assertLessEqual(len(lines), MAX_LINES)
        self.assertEqual(lines[-1], "estimate: tokens ~ chars/3")
        self.assertIn("s1", lines[0])
        self.assertIn("over 10k", out)


def heavy_report() -> dict:
    stat = {"count": 3, "chars": 30000, "tokens_est": 10000, "over_10k": 1}
    usage = {"requests": 2, "input_tokens": 1, "output_tokens": 2,
             "cache_creation_input_tokens": 3, "cache_read_input_tokens": 4}
    return {
        "estimate": "tokens ~ chars/3",
        "chars_per_token_est": 3,
        "session": {"id": "s" * 60, "project_dir": "p" * 80, "transcript": "/t", "subagent_files": 30},
        "instructions": {"loads": 4, "total_chars": 99, "tokens_est": 33,
                         "files": [{"path": f"/p/rules/{i}.md", "chars": 100 - i} for i in range(25)]},
        "hooks": {f"PreToolUse:Tool{i}" + "x" * 50: stat for i in range(20)},
        "hook_totals": stat,
        "hook_time": {f"python3 /hooks/h{i}.py --flag " + "y" * 80: {"count": 2, "total_ms": 9, "mean_ms": 4.5}
                      for i in range(20)},
        "subagent_calls": [{"subagent_type": f"t{i}", "model": "opus", "effort": "high", "count": 1}
                           for i in range(20)],
        "workflow_calls": 4,
        "subagent_usage": {"requests": 40, "by_model": {f"claude-m{i}": usage for i in range(20)},
                           "by_effort": {"high": 20, "low": 20}},
        "head_usage": {"requests": 40, "by_model": {f"claude-h{i}": usage for i in range(20)}},
    }


class SessionRenderTest(unittest.TestCase):
    def test_heavy_report_is_cut_to_forty_lines(self):
        text = session_render.render_text(heavy_report())
        lines = text.splitlines()
        self.assertLessEqual(len(lines), MAX_LINES)
        self.assertIn("(+", text)
        self.assertEqual(lines[-1], "estimate: tokens ~ chars/3")
        for title in ("Instructions", "Hooks", "Hook time", "Subagent calls", "Subagent usage", "Head usage"):
            self.assertTrue(any(line.startswith(title) for line in lines), title)

    def test_multiline_names_render_on_one_line(self):
        base = heavy_report()
        stat = {"count": 2, "total_ms": 9, "mean_ms": 4.5}
        files = [{"path": "/p/a\nb.md", "chars": 500}, *base["instructions"]["files"]]
        report = {
            **base,
            "instructions": {**base["instructions"], "files": files},
            "hooks": {"Pre\nTool": base["hook_totals"], **base["hooks"]},
            "hook_time": {"bash -c 'a\nb'": stat, **base["hook_time"]},
            "subagent_calls": [{"subagent_type": "t\nx", "model": "opus", "effort": "high", "count": 9},
                               *base["subagent_calls"]],
        }
        text = session_render.render_text(report)
        self.assertLessEqual(len(text.splitlines()), MAX_LINES)
        for shown in ("bash -c 'a b'", "Pre Tool", "/p/a b.md", "t x x opus"):
            self.assertIn(shown, text)

    def test_render_does_not_mutate_report(self):
        report = heavy_report()
        snapshot = json.dumps(report, sort_keys=True)
        session_render.render_text(report)
        self.assertEqual(json.dumps(report, sort_keys=True), snapshot)


class CliRegistrationTest(unittest.TestCase):
    def test_session_command_registered_in_order(self):
        names = [module.__name__.rsplit(".", 1)[-1] for module in cli.COMMANDS]
        self.assertEqual(names, ["install", "doctor", "report", "session", "bundle"])
        args = cli.build_parser().parse_args(["session", "--latest", "--projects-dir", "/x"])
        self.assertTrue(args.latest)


if __name__ == "__main__":
    unittest.main()
