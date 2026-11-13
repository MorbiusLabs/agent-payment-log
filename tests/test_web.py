"""The web app in docs/ must read logs exactly like the Python package.

docs/core.js is run under Node (tests/web_check.js) on the same files and the
same ledger sessions as the Python code, and every answer is compared: verdict,
error message, entry count, head hash and the bytes of every log written.
These tests are skipped when Node is not installed; CI always has it.
"""

import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent_payment_log import ChainError, LedgerError, PaymentLedger, PaymentRequest, Policy, Rule  # noqa: E402
from agent_payment_log.chain import GENESIS, canonical, entry_hash, read_entries, verify_entries  # noqa: E402
from agent_payment_log.ledger import check_ledger  # noqa: E402
from agent_payment_log.policy import fmt, to_amount  # noqa: E402

NODE = shutil.which("node")
if os.environ.get("CI") and not NODE:
    raise RuntimeError("CI must run the web tests: install Node.js")
DOCS = ROOT / "docs"


def node(request):
    r = subprocess.run([NODE, str(ROOT / "tests" / "web_check.js")], input=json.dumps(request),
                       capture_output=True, text=True, encoding="utf-8", timeout=120)
    if r.returncode != 0:
        raise AssertionError(f"node failed:\n{r.stderr}")
    return json.loads(r.stdout)


def py_verify(path, head=None):
    try:
        entries = read_entries(path, missing_ok=False)
        h = verify_entries(entries, head)
        check_ledger(entries)
    except (ChainError, LedgerError) as e:
        return {"ok": False, "error": str(e), "count": None, "head": None}
    return {"ok": True, "error": None, "count": len(entries), "head": h}


def demo_log():
    """The demo's log, made by running examples/demo.py in a temporary folder."""
    with tempfile.TemporaryDirectory() as d:
        subprocess.run([sys.executable, str(ROOT / "examples" / "demo.py")], cwd=d, check=True,
                       capture_output=True)
        return (Path(d) / "demo_payments.jsonl").read_bytes(), (Path(d) / "demo_payments_tampered.jsonl").read_bytes()


def lines_of(data):
    return [json.loads(l) for l in data.decode("utf-8").splitlines()]


def forge(entries):
    """Re-sign entries so the hash chain is valid, as someone who controls the file could."""
    out, prev = [], GENESIS
    for i, e in enumerate(entries):
        body = {k: v for k, v in e.items() if k != "hash"}
        body["seq"], body["prev_hash"] = i, prev
        body["hash"] = prev = entry_hash(body)
        out.append(canonical(body))
    return ("\n".join(out) + "\n").encode("utf-8") if out else b""


def corpus(good):
    """(name, bytes) pairs: every way a log can be broken that we could think of, plus valid ones."""
    text = good.decode("utf-8")
    lines = text.splitlines(True)
    E = lines_of(good)

    def edit(i, **changes):
        es = [dict(e) for e in E]
        es[i].update(changes)
        return es

    def drop_field(i, field):
        es = [dict(e) for e in E]
        del es[i][field]
        return es

    cases = [
        ("good", good),
        ("empty", b""),
        ("only newline", b"\n"),
        ("bom", b"\xef\xbb\xbf" + good),
        ("crlf", good.replace(b"\n", b"\r\n")),
        ("lone cr", good.replace(b"\n", b"\r", 1)),
        ("no final newline", good[:-1]),
        ("cut mid line", good[:-30]),
        ("latin-1 byte", good + b"\xff\n"),
        ("utf-8 surrogate bytes", b'{"a":"\xed\xa0\x80"}\n'),
        ("blank line", good + b"\n"),
        ("blank line in middle", (lines[0] + "\n" + "".join(lines[1:])).encode()),
        ("edited amount", good.replace(b'"amount":"75"', b'"amount":"7.5"', 1)),
        ("deleted entry", "".join(lines[:2] + lines[3:]).encode()),
        ("swapped entries", "".join([lines[1], lines[0]] + lines[2:]).encode()),
        ("last entry dropped", "".join(lines[:-1]).encode()),
        ("duplicated last line", (text + lines[-1]).encode()),
        ("spaces after colon", text.replace('":"', '": "', 1).encode()),
        ("unsorted keys", (json.dumps(E[0], separators=(",", ":")) + "\n" + "".join(lines[1:])).encode()),
        ("duplicate key", text.replace('{"agent"', '{"agent":"x","agent"', 1).encode()),
        ("escaped slash", text.replace("ops-lead", "ops\\/lead", 1).encode()),
        ("ascii escape", text.replace("research-agent", "research\\u002dagent", 1).encode()),
        ("not an object", b"[1,2]\n"),
        ("string line", b'"hello"\n'),
        ("number line", b"12\n"),
        ("NaN", b'{"a":NaN}\n'),
        ("Infinity", b'{"a":Infinity}\n'),
        ("-Infinity", b'{"a":-Infinity}\n'),
        ("huge float", b'{"a":1e999}\n'),
        ("deep nesting", b'{"a":' + b"[" * 5000 + b"]" * 5000 + b"}\n"),
        ("trailing comma", b'{"a":1,}\n'),
        ("trailing garbage", b'{"a":1}x\n'),
        ("leading space", b' {"a":1}\n'),
        ("single quotes", b"{'a':1}\n"),
        ("control char in string", b'{"a":"x\x01y"}\n'),
        ("tab in string", b'{"a":"x\ty"}\n'),
        ("bad escape", b'{"a":"\\x41"}\n'),
        ("short unicode escape", b'{"a":"\\u12"}\n'),
        ("lone surrogate escape", b'{"a":"\\ud800"}\n'),
        ("leading zero", b'{"a":01}\n'),
        ("minus zero", b'{"a":-0}\n'),
        ("unterminated string", b'{"a":"abc}\n'),
        ("seq as string", text.replace('"seq":0', '"seq":"0"', 1).encode()),
        ("seq true", text.replace('"seq":0', '"seq":true', 1).encode()),
        ("missing seq", forge(drop_field(0, "seq"))),
        ("wrong prev_hash", text.replace(GENESIS, "1" * 64, 1).encode()),
        ("missing hash", "".join([json.dumps({k: v for k, v in E[0].items() if k != "hash"},
                                             sort_keys=True, separators=(",", ":")) + "\n"] + lines[1:]).encode()),
        # hash chain intact, ledger rules broken
        ("forged good", forge(E)),
        ("forged edited amount", forge(edit(1, amount="7.5"))),
        ("unknown type", forge(edit(0, type="refund"))),
        ("type is a list", forge(edit(0, type=[1]))),
        ("type missing", forge(drop_field(0, "type"))),
        ("extra field", forge(edit(0, extra=1))),
        ("missing memo", forge(drop_field(0, "memo"))),
        ("bad time", forge(edit(0, time="2026-11-13 09:00:00"))),
        ("impossible date", forge(edit(0, time="2026-02-30T09:00:00Z"))),
        ("leap second", forge(edit(0, time="2026-11-13T09:00:60Z"))),
        ("year zero", forge(edit(0, time="0000-11-13T09:00:00Z"))),
        ("time backwards", forge(edit(2, time="2026-11-13T08:00:00Z"))),
        ("time as number", forge(edit(0, time=5))),
        ("empty agent", forge(edit(0, agent=""))),
        ("agent with space", forge(edit(0, agent=" research-agent"))),
        ("agent with nbsp", forge(edit(0, agent="research-agent\u00a0"))),
        ("agent with BOM char", forge(edit(0, agent="\ufeffagent"))),
        ("agent with x1c", forge(edit(0, agent="agent\x1c"))),
        ("agent with x85", forge(edit(0, agent="agent\x85"))),
        ("agent number", forge(edit(0, agent=7))),
        ("amount number", forge(edit(0, amount=12.4))),
        ("amount int", forge(edit(0, amount=12))),
        ("amount zero", forge(edit(0, amount="0"))),
        ("amount zero decimals", forge(edit(0, amount="0.000"))),
        ("amount negative", forge(edit(0, amount="-1"))),
        ("amount plus", forge(edit(0, amount="+1"))),
        ("amount leading zero", forge(edit(0, amount="012"))),
        ("amount trailing dot", forge(edit(0, amount="12."))),
        ("amount leading dot", forge(edit(0, amount=".5"))),
        ("amount exponent", forge(edit(0, amount="1e3"))),
        ("amount spaces", forge(edit(0, amount=" 5"))),
        ("amount 18 decimals", forge(edit(0, amount="0.000000000000000001"))),
        ("amount 19 decimals", forge(edit(0, amount="0.0000000000000000010"))),
        ("amount 30 digits", forge(edit(0, amount="9" * 30))),
        ("amount 31 digits", forge(edit(0, amount="1" + "0" * 30))),
        ("amount nan", forge(edit(0, amount="NaN"))),
        ("amount words", forge(edit(0, amount="twelve"))),
        ("amount underscore", forge(edit(0, amount="1_000"))),
        ("amount arabic digits", forge(edit(0, amount="\u0661\u0662"))),
        ("reversible string", forge(edit(0, reversible="yes"))),
        ("reversible int", forge(edit(0, reversible=1))),
        ("unknown outcome", forge(edit(0, outcome="maybe"))),
        ("outcome list", forge(edit(0, outcome=["allow"]))),
        ("allow without rule", forge(edit(0, rule_id=None))),
        ("deny without rule", forge(edit(3, rule_id=None))),
        ("rule id empty", forge(edit(0, rule_id=""))),
        ("rule id number", forge(edit(0, rule_id=5))),
        ("memo null", forge(edit(0, memo=None))),
        ("reason object", forge(edit(0, reason={"b": 1, "a": [True, None, 1.5]}))),
        ("approval of allowed", forge(edit(5, decision_seq=0))),
        ("approval of itself", forge(edit(5, decision_seq=5))),
        ("approval of later", forge(edit(5, decision_seq=6))),
        ("approval negative", forge(edit(5, decision_seq=-1))),
        ("approval as string", forge(edit(5, decision_seq="1"))),
        ("approval as float", forge(edit(5, decision_seq=1.0))),
        ("approval as bool", forge(edit(5, decision_seq=True))),
        ("approval huge", forge(edit(5, decision_seq=10 ** 30))),
        ("self approval", forge(edit(5, approved_by="research-agent"))),
        ("approver empty", forge(edit(5, approved_by=""))),
        ("approver with quote", forge(edit(5, approved_by="o'neil "))),
        ("note number", forge(edit(5, note=3))),
        ("rejection of approved", forge(edit(6, decision_seq=1))),
        ("approval of rejection", forge(edit(5, decision_seq=6))),
        ("seq float", forge(E).replace(b'"seq":1,', b'"seq":1.0,', 1)),
        ("unicode everywhere", forge(edit(0, agent="агент-ü😀", memo="日本 \u2028 tab\t", recipient="𝔞.example"))),
        ("keys astral vs bmp", forge(edit(0, **{"\U0001f600": 1, "\uffff": 2}))),
        ("odd strings in reason", forge(edit(0, reason="\x00\x1f\x7f\u200b\u00ad'\"\\"))),
    ]
    return cases


@unittest.skipUnless(NODE, "Node.js is not installed")
class WebMatchesPython(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.good, cls.tampered = demo_log()

    def compare_files(self, cases, head=None):
        with tempfile.TemporaryDirectory() as d:
            paths = []
            for i, (name, data) in enumerate(cases):
                p = Path(d) / f"{i:03d}.jsonl"
                p.write_bytes(data)
                paths.append(str(p))
            js = node({"mode": "verify", "files": paths, "head": head})
            for (name, _), p, j in zip(cases, paths, js):
                py = py_verify(p, head)
                with self.subTest(case=name):
                    if py["error"] and "invalid JSON (" in py["error"]:
                        # Python's own wording of JSON syntax errors changes between versions,
                        # so only the place and the kind of error must match
                        self.assertFalse(j["ok"])
                        self.assertEqual(py["error"].split(" (")[0], j["error"].split(" (")[0])
                    else:
                        self.assertEqual(py, j)
        return js

    def test_samples_are_the_demo_logs(self):
        self.assertEqual((DOCS / "samples" / "demo_payments.jsonl").read_bytes(), self.good)
        self.assertEqual((DOCS / "samples" / "demo_payments_tampered.jsonl").read_bytes(), self.tampered)

    def test_every_broken_log_gets_the_same_verdict(self):
        cases = corpus(self.good)
        js = self.compare_files(cases)
        verdicts = dict(zip([n for n, _ in cases], js))
        self.assertTrue(verdicts["good"]["ok"])
        self.assertFalse(verdicts["edited amount"]["ok"])
        self.assertGreater(len(cases), 100)

    def test_expected_head(self):
        head = py_verify_bytes(self.good)["head"]
        for h in (head, "f" * 64, "F" * 64, "abc", ""):
            self.compare_files([("good", self.good), ("tampered", self.tampered)], head=h)

    def test_same_ledger_sessions_write_the_same_bytes(self):
        sessions = [random_session(random.Random(seed)) for seed in range(40)]
        sessions.append(demo_session())
        js = node({"mode": "sessions", "sessions": sessions})
        for n, (s, j) in enumerate(zip(sessions, js)):
            py = run_session(s)
            with self.subTest(session=n):
                self.assertEqual(py["results"], j["results"])
                self.assertEqual(py["log"], j["log"])
                self.assertEqual(py["head"], j["head"])
        self.assertEqual(js[-1]["log"].encode("utf-8"), self.good)
        outcomes = " ".join(json.dumps(r) for s in js for r in s["results"])
        for word in ("allow", "LedgerError", "ValueError", "pending", "approved", "rejected"):
            self.assertIn(word, outcomes + json.dumps([s["log"] for s in js]))

    def test_python_whitespace(self):
        expected = [c for c in range(0x110000) if chr(c).isspace()]
        self.assertEqual(node({"mode": "space"}), expected)

    def test_float_and_int_spelling(self):
        rnd = random.Random(1)
        values = ["0.0", "-0.0", "1.0", "1e16", "1e15", "1.5e-5", "0.0001", "0.00001", "123456789012345678",
                  "1e22", "1e-7", "5e-324", "1.7976931348623157e308", "100.0", "-2.5", "12345.6789", "0", "-0",
                  "1E5", "1e+05", "9007199254740993", str(10 ** 40)]
        for _ in range(3000):
            x = rnd.choice([rnd.uniform(-1e3, 1e3), rnd.random() * 10 ** rnd.randint(-30, 30),
                            float(rnd.getrandbits(64)) * 2.0 ** rnd.randint(-1100, 900)])
            values.append(repr(x))
        expected = [json.dumps(json.loads(v)) for v in values]
        self.assertEqual(node({"mode": "floats", "values": values}), expected)

    def test_amounts(self):
        values = ["12.40", "75", "0", "0.0", "-1", "+1", "012", "12.", ".5", "1e3", "1.50e1", " 5 ", "5.0e-18",
                  "1e-19", "0.000000000000000001", "0.0000000000000000001", "9" * 30, "1" + "0" * 30, "abc", "",
                  "NaN", "inf", "-Infinity", "1.2.3", "1e", "e5", "--1", "0x10", "1 000", "\u00a07\u3000"]
        js = node({"mode": "amounts", "values": values})
        for v, j in zip(values, js):
            try:
                d = to_amount(v)
                parsed = {"ok": fmt(d)}
                plain = d > 0 and fmt(d) == v
            except (ValueError, TypeError) as e:
                parsed = {"error": [type(e).__name__, str(e)]}
                plain = False
            with self.subTest(value=v):
                self.assertEqual({"parsed": parsed, "plain": plain}, j)

    def test_sha256(self):
        import hashlib
        values = ["", "abc", "a" * 55, "a" * 56, "a" * 64, "a" * 1000, "日本語😀", "\x00"]
        self.assertEqual(node({"mode": "sha", "values": values}),
                         [hashlib.sha256(v.encode("utf-8")).hexdigest() for v in values])

    def test_repr_in_messages(self):
        values = ['"plain"', '"it\'s"', '"say \\"hi\\""', '"both \' and \\""', '"\\u0000\\t\\n\\r\\u007f"',
                  '"\\u00a0\\u00ad\\u200b\\u2028\\ue000\\ufeff"', '"日本😀"', '"\\ud800"', '"\\\\"',
                  "null", "true", "false", "12", "-0", "1.5", "1e100", "[1,\"a\",null]", '{"b":1,"a":{"c":[]}}',
                  '{"1":1,"0":2}']
        expected = [repr(json.loads(v)) for v in values]
        self.assertEqual(node({"mode": "repr", "values": values}), expected)

    def test_key_order(self):
        rnd = random.Random(2)
        pool = ["a", "B", "\uffff", "\U0001f600", "\ue000", "é", "", "aa", "\U00010000", "\ud7ff"]
        values = ["".join(rnd.choice(pool) for _ in range(rnd.randint(0, 3))) for _ in range(300)]
        self.assertEqual(node({"mode": "sort", "values": values}), sorted(values))


def py_verify_bytes(data):
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "x.jsonl"
        p.write_bytes(data)
        return py_verify(p)


# ---- ledger sessions, run the same way in both languages ----------------------------------

def make_policy(specs):
    return Policy([Rule(**{k: (frozenset(v) if k == "recipients" and isinstance(v, list) else v)
                           for k, v in s.items()}) for s in specs])


def run_session(s):
    now = [datetime(2026, 11, 13, 9, 0, tzinfo=timezone.utc)]
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "s.jsonl"
        ledger = PaymentLedger(path, make_policy(s["rules"]), clock=lambda: now[0])
        results = []
        for op in s["ops"]:
            try:
                kind = op["op"]
                if kind == "clock":
                    now[0] = datetime.strptime(op["t"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                    r = None
                elif kind == "policy":
                    ledger.policy = make_policy(op["rules"])
                    r = None
                elif kind == "request":
                    r = ledger.request(PaymentRequest(**{k: op[k] for k in op if k != "op"}))["hash"]
                elif kind in ("approve", "reject"):
                    r = getattr(ledger, kind)(op["seq"], op["approver"], op["note"])["hash"]
                elif kind == "status":
                    r = ledger.status(op["seq"])
                elif kind == "pending":
                    r = [e["seq"] for e in ledger.pending()]
                elif kind == "spent":
                    r = fmt(ledger.spent_on(op["agent"], op["asset"], op["day"]))
                results.append({"ok": r})
            except (ValueError, TypeError, LedgerError, ChainError) as e:
                results.append({"error": [type(e).__name__, str(e)]})
        log = path.read_bytes().decode("utf-8") if path.exists() else ""
        return {"results": results, "log": log, "head": ledger.log.head}


AGENTS = ["research-agent", "trader", "ops-lead", " spaced", ""]
RECIPIENTS = ["api.data.example", "api.news.example", "tips.example", "unknown.example"]
ASSETS = ["USDC", "ETH"]
AMOUNTS = ["1", "3", "5", "12.40", "20", "49.99", "50", "50.01", "75", "120", "250", "0.000000000000000001",
           "0", "-3", "abc", "1e2", " 7 ", ".5", "5.", "007", "1.0000000000000000001", "1" + "0" * 30]


def random_rules(rnd):
    rules = []
    for i in range(rnd.randint(0, 3)):
        mx = rnd.choice(["5", "50", "100", "200", "300"])
        rules.append({
            "id": rnd.choice([f"r{i}", f"r{i}", "dup", " bad"]),
            "asset": rnd.choice(ASSETS),
            "max_amount": mx,
            "daily_limit": rnd.choice([mx, "100", "300", "1000", "4"]),
            "approval_above": rnd.choice([None, None, "0", "10", "50", "-1"]),
            "recipients": rnd.choice([None, None, RECIPIENTS[:2], [RECIPIENTS[2]], []]),
            "irreversible_needs_approval": rnd.choice([True, True, False]),
        })
    return rules


def valid_rules(rnd):
    while True:
        rules = random_rules(rnd)
        try:
            make_policy(rules)
            return rules
        except (ValueError, TypeError):
            continue


def random_session(rnd):
    t = datetime(2026, 11, 13, 9, 0)
    ops = []
    for _ in range(rnd.randint(20, 60)):
        k = rnd.random()
        if k < 0.5:
            ops.append({"op": "request", "agent": rnd.choice(AGENTS[:2] if rnd.random() < 0.9 else AGENTS),
                        "recipient": rnd.choice(RECIPIENTS), "amount": rnd.choice(AMOUNTS),
                        "asset": rnd.choice(ASSETS), "reversible": rnd.random() < 0.7,
                        "memo": rnd.choice(["", "data", "日本"])})
        elif k < 0.7:
            ops.append({"op": rnd.choice(["approve", "reject"]), "seq": rnd.randint(-1, len(ops)),
                        "approver": rnd.choice(["ops-lead", "research-agent", "trader", " x", ""]),
                        "note": rnd.choice(["", "ok"])})
        elif k < 0.8:
            t += timedelta(minutes=rnd.choice([1, 30, 600, 1440]) if rnd.random() < 0.9 else -5)
            ops.append({"op": "clock", "t": t.strftime("%Y-%m-%dT%H:%M:%SZ")})
        elif k < 0.85:
            ops.append({"op": "policy", "rules": random_rules(rnd)})
        elif k < 0.9:
            ops.append({"op": "status", "seq": rnd.randint(-1, len(ops))})
        elif k < 0.95:
            ops.append({"op": "pending"})
        else:
            ops.append({"op": "spent", "agent": rnd.choice(AGENTS[:2]), "asset": rnd.choice(ASSETS),
                        "day": rnd.choice([t.strftime("%Y-%m-%d"), "2026-11-13", "13-11-2026"])})
    return {"rules": valid_rules(rnd), "ops": ops}


def demo_session():
    """examples/demo.py, step by step."""
    rules = [
        {"id": "data-vendors", "asset": "USDC", "max_amount": "200", "daily_limit": "300", "approval_above": "50",
         "recipients": ["api.marketdata.example", "api.news.example"]},
        {"id": "small-anything", "asset": "USDC", "max_amount": "5", "daily_limit": "20"},
    ]
    pays = [("api.marketdata.example", "12.40", True, "price feed, 1 day"),
            ("api.news.example", "75", True, "news archive, 1 month"),
            ("tips.example", "3", True, "tip for a dataset"),
            ("unknown-wallet.example", "40", True, "agent wanted to buy a report"),
            ("api.marketdata.example", "20", False, "onchain transfer, cannot be undone")]
    ops, minute = [], 0

    def tick():
        nonlocal minute
        ops.append({"op": "clock", "t": f"2026-11-13T09:{minute:02d}:00Z"})
        minute += 1

    for recipient, amount, reversible, memo in pays:
        tick()
        ops.append({"op": "request", "agent": "research-agent", "recipient": recipient, "amount": amount,
                    "asset": "USDC", "reversible": reversible, "memo": memo})
    tick()
    ops.append({"op": "approve", "seq": 1, "approver": "ops-lead", "note": "known vendor, monthly plan"})
    tick()
    ops.append({"op": "reject", "seq": 4, "approver": "ops-lead", "note": "use the card plan instead"})
    return {"rules": rules, "ops": ops}


class WebFiles(unittest.TestCase):
    """Checks that need no Node: the page loads only its own files."""

    def test_page_loads_only_local_files(self):
        html = (DOCS / "index.html").read_text(encoding="utf-8")
        self.assertIn("Content-Security-Policy", html)
        for ref in ("core.js", "app.js", "style.css"):
            self.assertIn(ref, html)
        for f in DOCS.glob("*.*"):
            if f.suffix in (".html", ".js", ".css"):
                body = f.read_text(encoding="utf-8")
                self.assertNotRegex(body, r"(src|href)=\"https?://(?!github\.com|x\.com)", f.name)
                self.assertNotIn("<script>", body, f.name)


if __name__ == "__main__":
    unittest.main()
