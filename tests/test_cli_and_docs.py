"""The command line, the demo and the README examples must all behave exactly as documented."""

import io
import os
import re
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

from agent_payment_log import HashChainLog, PaymentLedger, PaymentRequest, Policy, Rule, __version__
from agent_payment_log.__main__ import main

ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text(encoding="utf-8")
POLICY = Policy([Rule(id="vendors", asset="USDC", max_amount="100", daily_limit="150", approval_above="50")])


def readme_block(after_heading, lang="text", index=0):
    section = README.split(after_heading, 1)[1]
    blocks = re.findall(r"```" + lang + r"\n(.*?)```", section, re.S)
    return blocks[index]


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "p.jsonl"
        ledger = PaymentLedger(self.path, POLICY, clock=lambda: datetime(2026, 11, 13, 9, 0, tzinfo=timezone.utc))
        held = ledger.request(PaymentRequest(agent="bot", recipient="api.example", amount="80", asset="USDC",
                                             reversible=True, memo="monthly plan"))
        ledger.approve(held["seq"], "alice", note="ok")
        ledger.request(PaymentRequest(agent="bot", recipient="api.example", amount="500", asset="USDC",
                                      reversible=False))
        self.head = ledger.log.head

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            try:
                code = main(list(args))
            except SystemExit as e:
                code = e.code
        return code, out.getvalue(), err.getvalue()

    def test_verify_ok(self):
        self.assertEqual(self.run_cli("verify", str(self.path)), (0, f"OK: 3 entries, head {self.head}\n", ""))
        self.assertEqual(self.run_cli("verify", str(self.path), "--head", self.head)[0], 0)

    def test_show(self):
        code, out, err = self.run_cli("show", str(self.path))
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out, (
            "#0    2026-11-13T09:00:00Z  bot -> api.example  80 USDC  HOLD\n"
            "      rule=vendors  80 is above 50, needs a human\n"
            "      memo: monthly plan\n"
            "#1    2026-11-13T09:00:00Z  #0 APPROVED by alice\n"
            "      ok\n"
            "#2    2026-11-13T09:00:00Z  bot -> api.example  500 USDC  DENY  (irreversible)\n"
            "      rule=vendors  500 is above the single-payment limit of 100\n"
            f"OK: 3 entries, head {self.head}\n"))

    def test_show_rejection(self):
        p = Path(self.tmp.name) / "r.jsonl"
        ledger = PaymentLedger(p, POLICY)
        held = ledger.request(PaymentRequest(agent="bot", recipient="x", amount="60", asset="USDC", reversible=True))
        ledger.reject(held["seq"], "alice")
        out = self.run_cli("show", str(p))[1]
        self.assertIn("#0 REJECTED by alice\n", out)
        self.assertNotIn("memo:", out)

    def test_tampered(self):
        text = self.path.read_text(encoding="utf-8").replace('"amount":"80"', '"amount":"8"')
        self.path.write_bytes(text.encode("utf-8"))
        for cmd in ("verify", "show"):
            self.assertEqual(self.run_cli(cmd, str(self.path)),
                             (1, "", "FAILED: entry 0: content does not match its hash\n"))

    def test_wrong_head(self):
        code, _, err = self.run_cli("verify", str(self.path), "--head", "f" * 64)
        self.assertEqual((code, err), (1, "FAILED: log does not end at the expected head hash\n"))
        self.assertEqual(self.run_cli("verify", str(self.path), "--head", self.head.upper())[0], 0)

    def test_malformed_head_is_a_usage_error(self):
        for bad in ("abc", "g" * 64, "f" * 65, ""):
            code, out, err = self.run_cli("verify", str(self.path), "--head", bad)
            self.assertEqual((code, out), (2, ""))
            self.assertIn("--head: must be 64 hex characters", err)

    def test_missing_file_and_directory(self):
        missing = Path(self.tmp.name) / "nope.jsonl"
        self.assertEqual(self.run_cli("verify", str(missing)), (1, "", f"FAILED: {missing}: file not found\n"))
        self.assertEqual(self.run_cli("show", self.tmp.name),
                         (1, "", f"FAILED: {self.tmp.name}: not a regular file\n"))

    def test_chain_that_is_not_a_ledger(self):
        other = Path(self.tmp.name) / "other.jsonl"
        HashChainLog(other).append({"hello": 1})
        self.assertEqual(self.run_cli("show", str(other)),
                         (1, "", "FAILED: entry 0: unknown type None\n"))

    def test_usage_errors_exit_2(self):
        for args in ([], ["verify"], ["nonsense", "x"], ["verify", "x", "--bogus"]):
            self.assertEqual(self.run_cli(*args)[0], 2, args)

    def test_version_and_help(self):
        code, out, _ = self.run_cli("--version")
        self.assertEqual((code, out), (0, f"agent-payment-log {__version__}\n"))
        for args in (["--help"], ["verify", "--help"], ["show", "--help"]):
            code, out, _ = self.run_cli(*args)
            self.assertEqual(code, 0)
            self.assertIn("usage: agent-payment-log", out)

    def test_python_dash_m(self):
        env = dict(os.environ, PYTHONPATH=str(ROOT))
        r = subprocess.run([sys.executable, "-m", "agent_payment_log", "verify", str(self.path)],
                           capture_output=True, text=True, env=env)
        self.assertEqual((r.returncode, r.stdout), (0, f"OK: 3 entries, head {self.head}\n"))


class DocsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = dict(os.environ, PYTHONPATH=str(ROOT))

    def tearDown(self):
        self.tmp.cleanup()

    def run_in_tmp(self, *args):
        return subprocess.run(list(args), capture_output=True, text=True, cwd=self.tmp.name, env=self.env)

    def test_demo_output_matches_readme(self):
        r = self.run_in_tmp(sys.executable, str(ROOT / "examples" / "demo.py"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, readme_block("## Quick start", index=0))
        # running it twice gives the same result
        self.assertEqual(self.run_in_tmp(sys.executable, str(ROOT / "examples" / "demo.py")).stdout, r.stdout)

    def test_cli_output_matches_readme(self):
        self.run_in_tmp(sys.executable, str(ROOT / "examples" / "demo.py"))
        section = readme_block("### Try the command line", lang="text")
        for cmd, expected in re.findall(r"^\$ (.+)\n((?:(?!\$ ).*\n)*)", section, re.M):
            args = cmd.split()
            self.assertEqual(args[0], "agent-payment-log")
            r = self.run_in_tmp(sys.executable, "-m", "agent_payment_log", *args[1:])
            self.assertEqual(r.stdout + r.stderr, expected, cmd)

    def test_usage_example_runs(self):
        code = readme_block("## Usage", lang="python")
        code = re.sub(r"\.\.\.  # .*", "pass", code)
        r = self.run_in_tmp(sys.executable, "-c", code + "\nprint(entry['outcome'], ledger.status(entry['seq']))")
        self.assertEqual((r.returncode, r.stdout), (0, "hold approved\n"), r.stderr)

    def test_test_count_in_readme(self):
        n = unittest.defaultTestLoader.discover(str(ROOT / "tests")).countTestCases()
        self.assertIn(f"\n{n} tests.", README)


if __name__ == "__main__":
    unittest.main()
