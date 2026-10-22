from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from install_fixtures import BAD_HOOK, make_assets

from beacon import doctor
from beacon import install as inst


class DoctorTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
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

    def test_healthy_install_passes(self) -> None:
        checks = self.diagnose()
        self.assertEqual({c.result for c in checks}, {doctor.PASS})
        self.assertEqual(doctor.exit_code(checks), 0)
        kinds = [c.kind for c in checks]
        self.assertEqual(kinds.count("asset"), 5)
        self.assertEqual(kinds.count("wiring"), 2)
        self.assertEqual(kinds.count("selftest"), 1, "only selftest: true hooks run")

    def test_missing_wiring_fails(self) -> None:
        path = self.target / "settings.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        path.write_text(json.dumps({**data, "hooks": {"SessionStart": data["hooks"]["SessionStart"]}}),
                        encoding="utf-8")
        checks = self.diagnose()
        self.assertEqual(self.results(checks)[("wiring", "hooks/guard.py")], doctor.FAIL)
        self.assertEqual(self.results(checks)[("wiring", "hooks/start.py")], doctor.PASS)
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
        with mock.patch("beacon.assets.default_root", return_value=self.assets), \
                contextlib.redirect_stdout(out):
            healthy = doctor.run(argparse.Namespace(target=str(self.target)))
            (self.target / "settings.json").write_text("{}", encoding="utf-8")
            broken = doctor.run(argparse.Namespace(target=str(self.target)))
        self.assertEqual((healthy, broken), (0, 1))
        self.assertIn("selftest", out.getvalue())
        self.assertIn("FAIL", out.getvalue())


if __name__ == "__main__":
    unittest.main()
