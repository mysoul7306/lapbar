"""Tests for `beacon report`: scanning, dedupe, classification, counterfactual, filters, pricing."""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from beacon import report  # noqa: E402
from beacon.pricing import cost_usd, load_prices, normalize_model_id  # noqa: E402

FABLE = "claude-fable-5-1"
OPUS = "claude-opus-5-5"
SONNET = "claude-sonnet-5-5"
HAIKU = "claude-haiku-5-5"
MYSTERY = "claude-mystery-9"


def assistant(rid, model, ts, inp=0, cw=0, cr=0, out=0, effort=None):
    record = {
        "type": "assistant",
        "requestId": rid,
        "timestamp": ts,
        "message": {"model": model, "usage": {
            "input_tokens": inp, "cache_creation_input_tokens": cw,
            "cache_read_input_tokens": cr, "output_tokens": out,
        }},
    }
    return {**record, "effort": effort} if effort else record


def write_jsonl(path: Path, records, mtime: str | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, separators=(",", ":")) + "\n" for r in records), encoding="utf-8")
    if mtime:
        epoch = datetime.fromisoformat(mtime).replace(tzinfo=timezone.utc).timestamp()
        os.utime(path, (epoch, epoch))


def build_fixture(root: Path) -> None:
    session = root / "-proj-a"
    write_jsonl(session / "s1.jsonl", [
        {"type": "user", "message": {"content": 'text with "type":"assistant" inside'}},
        assistant("r1", FABLE, "2026-10-14T01:00:00Z", inp=100, cw=1000, cr=10000, out=5),
        assistant("r1", FABLE, "2026-10-14T01:00:01Z", inp=100, cw=1000, cr=10000, out=20),
        assistant("syn", "<synthetic>", "2026-10-14T01:00:02Z", inp=999, out=999),
        assistant("blank", "", "2026-10-14T01:00:03Z", inp=999, out=999),
        assistant("r0", FABLE, "2026-10-12T23:59:59Z", inp=1_000_000),
        "not json at all",
    ])
    write_jsonl(session / "s1" / "subagents" / "agent-a.jsonl", [
        assistant("r3", OPUS, "2026-10-14T02:00:00Z", inp=1000, cr=2000, out=300, effort="high"),
        assistant("r4", HAIKU, "2026-10-14T02:01:00Z", inp=1000, out=100, effort="low"),
        assistant("r5", MYSTERY, "2026-10-14T02:02:00Z", inp=1000, out=100, effort="high"),
    ])
    write_jsonl(session / "s1" / "subagents" / "workflows" / "wf_1" / "agent-b.jsonl", [
        assistant("r6", SONNET, "2026-10-15T00:00:00Z", inp=500, cw=500, out=50, effort="medium"),
        assistant("r6", SONNET, "2026-10-15T00:00:00Z", inp=500, cw=500, out=50, effort="medium"),
    ])
    write_jsonl(root / "-proj-b" / "old.jsonl", [
        assistant("r7", FABLE, "2026-10-20T00:00:00Z", inp=7),
    ], mtime="2026-10-01T00:00:00")


def parse(argv):
    parser = argparse.ArgumentParser()
    report.add_parser(parser.add_subparsers(dest="command"))
    return parser.parse_args(["report", *argv])


def run_report(root: Path, *argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = report.run(parse(["--projects-dir", str(root), *argv]))
    return code, out.getvalue(), err.getvalue()


def rows_by_model(data, role):
    return {row["model"]: row for row in data["models"][role]}


class ReportTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        build_fixture(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def report_json(self, *argv):
        code, out, err = run_report(self.root, "--json", *argv)
        self.assertEqual(code, 0, err)
        return json.loads(out), err

    def test_dedupe_keeps_final_content_block(self):
        data, _ = self.report_json("--since", "2026-10-13")
        fable = rows_by_model(data, "head")[FABLE]
        self.assertEqual(fable["requests"], 1)
        self.assertEqual(fable["output_tokens"], 20)
        self.assertEqual(rows_by_model(data, "subagent")[SONNET]["requests"], 1)

    def test_synthetic_and_blank_models_excluded(self):
        data, _ = self.report_json("--since", "2026-10-13")
        models = set(rows_by_model(data, "head")) | set(rows_by_model(data, "subagent"))
        self.assertNotIn("<synthetic>", models)
        self.assertNotIn("", models)

    def test_classification_by_path(self):
        data, _ = self.report_json("--since", "2026-10-13")
        self.assertEqual(set(rows_by_model(data, "head")), {FABLE})
        self.assertEqual(set(rows_by_model(data, "subagent")), {OPUS, HAIKU, MYSTERY, SONNET})
        self.assertEqual(data["requests"], {"total": 5, "head": 1, "subagent": 4, "workflow": 1})
        self.assertEqual(data["sessions"], 1)

    def test_counterfactual_formula(self):
        data, _ = self.report_json("--since", "2026-10-13")
        prices = load_prices()
        fable, opus = prices.models[FABLE], prices.models[OPUS]
        head = cost_usd({"input_tokens": 100, "cache_creation_input_tokens": 1000,
                         "cache_read_input_tokens": 10000, "output_tokens": 20}, fable)
        subs = [
            ({"input_tokens": 1000, "cache_read_input_tokens": 2000, "output_tokens": 300}, OPUS),
            ({"input_tokens": 1000, "output_tokens": 100}, HAIKU),
            ({"input_tokens": 500, "cache_creation_input_tokens": 500, "output_tokens": 50}, SONNET),
        ]
        actual = head + sum(cost_usd(tokens, prices.models[model]) for tokens, model in subs)
        if_all_head = head + sum(cost_usd(tokens, fable) for tokens, _model in subs)
        cf = data["counterfactual"]
        self.assertAlmostEqual(cf["actual_usd"], actual, delta=1e-4)
        self.assertAlmostEqual(cf["if_all_head_usd"], if_all_head, delta=1e-4)
        self.assertAlmostEqual(cf["saved_usd"], if_all_head - actual, delta=1e-4)
        self.assertAlmostEqual(cf["saved_pct"], round((if_all_head - actual) / if_all_head * 100, 1), places=1)
        # Spot check one price: 1000 input + 2000 cache_read + 300 output on Opus.
        self.assertAlmostEqual(cost_usd(subs[0][0], opus), (1000 * 4 + 2000 * 0.2 + 300 * 20) / 1e6)

    def test_since_filters_by_timestamp_and_mtime(self):
        data, _ = self.report_json("--since", "2026-10-13")
        self.assertEqual(data["scan"]["files_skipped_by_mtime"], 1)
        self.assertEqual(data["period"], {"since": "2026-10-13", "until": "2026-10-15", "days": 3})
        unfiltered, _ = self.report_json()
        self.assertEqual(unfiltered["scan"]["files_skipped_by_mtime"], 0)
        self.assertEqual(rows_by_model(unfiltered, "head")[FABLE]["requests"], 3)
        self.assertEqual(unfiltered["period"]["since"], "2026-10-12")

    def test_until_is_inclusive(self):
        data, _ = self.report_json("--since", "2026-10-13", "--until", "2026-10-14")
        self.assertNotIn(SONNET, rows_by_model(data, "subagent"))
        self.assertEqual(data["period"]["days"], 2)

    def test_effort_distribution_counts_subagent_requests(self):
        data, _ = self.report_json("--since", "2026-10-13")
        effort = {name: share["requests"] for name, share in data["subagent_effort"].items()}
        self.assertEqual(effort, {"high": 2, "low": 1, "medium": 1})

    def test_unknown_model_warns_once_and_costs_zero(self):
        code, out, err = run_report(self.root, "--since", "2026-10-13")
        self.assertEqual(code, 0)
        self.assertEqual(err.count(f"WARN: no price for model '{MYSTERY}'"), 1)
        self.assertIn(f"{MYSTERY} [unpriced]", out)
        self.assertIn("Summary: subagents on cheaper models saved $", out)
        data, _ = self.report_json("--since", "2026-10-13")
        mystery = rows_by_model(data, "subagent")[MYSTERY]
        self.assertEqual((mystery["priced"], mystery["cost_usd"]), (False, 0.0))
        self.assertEqual(data["unpriced_models"], [MYSTERY])

    def test_prices_override_adds_model(self):
        override = self.root / "prices.json"
        override.write_text(json.dumps({"models": {MYSTERY: {
            "input": 1, "output": 1, "cache_read": 1, "cache_write": 1}}}), encoding="utf-8")
        data, err = self.report_json("--since", "2026-10-13", "--prices", str(override))
        self.assertEqual(err, "")
        self.assertAlmostEqual(rows_by_model(data, "subagent")[MYSTERY]["cost_usd"], 1100 / 1e6)

    def test_unknown_head_model_is_usage_error(self):
        code, _out, err = run_report(self.root, "--head", "claude-nothing-1")
        self.assertEqual(code, 2)
        self.assertIn("no price entry", err)


class PricingTest(unittest.TestCase):
    def test_normalize_and_resolve(self):
        prices = load_prices()
        self.assertEqual(normalize_model_id("Opus-5-5"), OPUS)
        cases = {
            "opus": OPUS, "fable": FABLE, "sonnet": SONNET, "haiku": HAIKU,
            "claude-sonnet-5-5-20261001": SONNET, "claude-fable-5-1[1m]": FABLE,
            "claude-opus-5": "claude-opus-5", "claude-opus-4-8-20260301": "claude-opus-4-8",
            "claude-opus-4": "claude-opus-4-8", "claude-opus-3": None, "gpt-x": None, "": None,
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(prices.resolve(raw), expected)

    def test_packaged_table(self):
        prices = load_prices()
        self.assertEqual(prices.as_of, "2026-10-06")
        self.assertEqual(prices.models[FABLE]["cache_write"], prices.models[FABLE]["input"] * 1.25)
        self.assertIn("estimate", prices.note)


if __name__ == "__main__":
    unittest.main()
