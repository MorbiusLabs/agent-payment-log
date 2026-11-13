import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from agent_payment_log import (ChainError, HashChainLog, LedgerError, PaymentLedger, PaymentRequest, Policy, Rule,
                               check_ledger, verify_file)

POLICY = Policy([Rule(id="vendors", asset="USDC", max_amount="100", daily_limit="150", approval_above="50")])


class FakeClock:
    def __init__(self):
        self.t = datetime(2026, 11, 13, 23, 50, tzinfo=timezone.utc)

    def __call__(self):
        return self.t

    def advance(self, **kw):
        self.t += timedelta(**kw)


def pay(amount, reversible=True, agent="agent-1"):
    return PaymentRequest(agent=agent, recipient="api.example", amount=amount, asset="USDC", reversible=reversible)


class LedgerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "payments.jsonl"
        self.clock = FakeClock()
        self.ledger = PaymentLedger(self.path, POLICY, clock=self.clock)

    def tearDown(self):
        self.tmp.cleanup()

    def test_decision_entry_contents(self):
        e = self.ledger.request(PaymentRequest(agent="agent-1", recipient="api.example", amount="12.50",
                                               asset="USDC", reversible=True, memo="price feed"))
        self.assertEqual({k: v for k, v in e.items() if k not in ("hash", "prev_hash")}, {
            "seq": 0, "type": "decision", "time": "2026-11-13T23:50:00Z", "agent": "agent-1",
            "recipient": "api.example", "amount": "12.50", "asset": "USDC", "reversible": True,
            "memo": "price feed", "outcome": "allow", "rule_id": "vendors", "reason": "within limits"})
        self.assertEqual(self.ledger.status(0), "allowed")

    def test_amount_stored_in_plain_notation(self):
        self.assertEqual(self.ledger.request(pay("1E+1"))["amount"], "10")

    def test_denied(self):
        e = self.ledger.request(pay("101"))
        self.assertEqual(self.ledger.status(e["seq"]), "denied")
        self.assertEqual(self.ledger.spent_on("agent-1", "USDC", "2026-11-13"), Decimal(0))

    def test_hold_then_approve(self):
        e = self.ledger.request(pay("80"))
        self.assertEqual(self.ledger.status(e["seq"]), "pending")
        self.assertEqual([p["seq"] for p in self.ledger.pending()], [e["seq"]])
        a = self.ledger.approve(e["seq"], "alice", note="known vendor")
        self.assertEqual((a["type"], a["approved_by"], a["decision_seq"], a["note"]),
                         ("approval", "alice", e["seq"], "known vendor"))
        self.assertEqual(self.ledger.status(e["seq"]), "approved")
        self.assertEqual(self.ledger.pending(), [])
        self.assertEqual(self.ledger.spent_on("agent-1", "USDC", "2026-11-13"), Decimal("80"))

    def test_hold_then_reject(self):
        e = self.ledger.request(pay("1", reversible=False))
        r = self.ledger.reject(e["seq"], "alice", note="wrong address")
        self.assertEqual((r["type"], r["rejected_by"]), ("rejection", "alice"))
        self.assertEqual(self.ledger.status(e["seq"]), "rejected")
        self.assertEqual(self.ledger.pending(), [])
        self.assertEqual(self.ledger.spent_on("agent-1", "USDC", "2026-11-13"), Decimal("0"))

    def test_cannot_resolve_twice_or_resolve_non_pending(self):
        held = self.ledger.request(pay("80"))
        self.ledger.approve(held["seq"], "alice")
        for action in (self.ledger.approve, self.ledger.reject):
            with self.assertRaisesRegex(LedgerError, "is approved"):
                action(held["seq"], "bob")
        allowed = self.ledger.request(pay("1"))
        with self.assertRaisesRegex(LedgerError, "is allowed"):
            self.ledger.approve(allowed["seq"], "bob")
        denied = self.ledger.request(pay("500"))
        with self.assertRaisesRegex(LedgerError, "is denied"):
            self.ledger.reject(denied["seq"], "bob")
        with self.assertRaisesRegex(LedgerError, "not a payment decision"):
            self.ledger.approve(held["seq"] + 1, "bob")
        for bad in (99, -1, True, "0", None):
            with self.assertRaisesRegex(LedgerError, "no entry"):
                self.ledger.approve(bad, "bob")
            with self.assertRaisesRegex(LedgerError, "no entry"):
                self.ledger.status(bad)

    def test_approver_and_note_checks(self):
        e = self.ledger.request(pay("80"))
        with self.assertRaisesRegex(LedgerError, "own payment"):
            self.ledger.approve(e["seq"], "agent-1")
        for bad in ("", "  ", " alice", None, 5):
            with self.assertRaisesRegex(LedgerError, "approver"):
                self.ledger.reject(e["seq"], bad)
        with self.assertRaisesRegex(LedgerError, "note"):
            self.ledger.approve(e["seq"], "alice", note=None)
        self.assertEqual(self.ledger.status(e["seq"]), "pending")
        self.assertEqual(len(self.ledger.log), 1)

    def test_request_type_checked(self):
        with self.assertRaises(TypeError):
            self.ledger.request({"amount": "1"})
        with self.assertRaises(TypeError):
            PaymentLedger(self.path, [Rule(id="r", asset="USDC", max_amount="1", daily_limit="1")])

    def test_daily_limit_counts_allowed_and_approved(self):
        self.ledger.request(pay("50"))                       # allowed, 50
        held = self.ledger.request(pay("90"))                # held
        self.ledger.approve(held["seq"], "alice")            # 140
        self.assertEqual(self.ledger.request(pay("10.01"))["outcome"], "deny")
        self.assertEqual(self.ledger.request(pay("10"))["outcome"], "allow")  # exactly 150

    def test_daily_limit_rechecked_at_approval(self):
        held = self.ledger.request(pay("90"))
        self.ledger.request(pay("50"))
        self.ledger.request(pay("10"))                       # 60 spent, 90 more would be 150: ok
        self.ledger.request(pay("0.01"))                     # now 60.01
        with self.assertRaisesRegex(LedgerError, "today's total to 150.01, limit is 150"):
            self.ledger.approve(held["seq"], "alice")
        self.assertEqual(self.ledger.status(held["seq"]), "pending")
        self.ledger.reject(held["seq"], "alice", note="over budget")   # can still be closed out

    def test_limits_reset_at_utc_midnight_and_are_per_agent_and_asset(self):
        for _ in range(3):
            self.ledger.request(pay("50"))
        self.assertEqual(self.ledger.request(pay("1"))["outcome"], "deny")
        self.assertEqual(self.ledger.request(pay("1", agent="agent-2"))["outcome"], "allow")
        self.assertEqual(self.ledger.spent_on("agent-1", "EURC", "2026-11-13"), Decimal(0))
        self.clock.advance(minutes=10)                       # 2026-11-14 00:00 UTC
        self.assertEqual(self.ledger.request(pay("1"))["outcome"], "allow")

    def test_approval_counts_on_the_day_it_happens(self):
        held = self.ledger.request(pay("90"))
        self.clock.advance(minutes=20)
        self.ledger.approve(held["seq"], "alice")
        self.assertEqual(self.ledger.spent_on("agent-1", "USDC", "2026-11-13"), Decimal("0"))
        self.assertEqual(self.ledger.spent_on("agent-1", "USDC", "2026-11-14"), Decimal("90"))

    def test_spent_on_day_format(self):
        for bad in ("2026-11", "2026-11-13T00", "13.11.2026", None):
            with self.assertRaises(ValueError):
                self.ledger.spent_on("agent-1", "USDC", bad)

    def test_rule_removed_before_approval(self):
        held = self.ledger.request(pay("80"))
        other = Policy([Rule(id="other", asset="USDC", max_amount="1", daily_limit="1")])
        ledger2 = PaymentLedger(self.path, other, clock=self.clock)
        with self.assertRaisesRegex(LedgerError, "no longer in the policy"):
            ledger2.approve(held["seq"], "alice")

    def test_clock_checks(self):
        for bad in (lambda: datetime(2026, 1, 1), lambda: "2026-11-13"):
            ledger = PaymentLedger(self.path, POLICY, clock=bad)
            with self.assertRaises(ValueError):
                ledger.request(pay("1"))
        self.ledger.request(pay("1"))
        self.clock.advance(seconds=-1)
        with self.assertRaisesRegex(LedgerError, "earlier than the last entry"):
            self.ledger.request(pay("1"))
        self.assertEqual(len(self.ledger.log), 1)

    def test_same_second_is_fine(self):
        self.ledger.request(pay("1"))
        self.ledger.request(pay("1"))
        self.assertEqual(len(self.ledger.log), 2)

    def test_non_utc_clock_is_converted(self):
        kyiv = timezone(timedelta(hours=2))
        ledger = PaymentLedger(self.path, POLICY, clock=lambda: datetime(2026, 11, 14, 1, 30, tzinfo=kyiv))
        self.assertEqual(ledger.request(pay("1"))["time"], "2026-11-13T23:30:00Z")

    def test_default_clock_is_utc_now(self):
        ledger = PaymentLedger(self.path, POLICY)
        before = datetime.now(timezone.utc).replace(microsecond=0)
        t = datetime.strptime(ledger.request(pay("1"))["time"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        self.assertLessEqual(abs((t - before).total_seconds()), 2)

    def test_log_survives_reopen_and_verifies(self):
        held = self.ledger.request(pay("80"))
        self.ledger.approve(held["seq"], "alice")
        reopened = PaymentLedger(self.path, POLICY, clock=self.clock)
        self.assertEqual(reopened.status(held["seq"]), "approved")
        self.assertEqual(verify_file(self.path), (2, reopened.log.head))

    def test_refuses_to_open_a_tampered_file(self):
        self.ledger.request(pay("80"))
        text = self.path.read_text(encoding="utf-8").replace('"amount":"80"', '"amount":"8"')
        self.path.write_bytes(text.encode("utf-8"))
        with self.assertRaises(ChainError):
            PaymentLedger(self.path, POLICY)

    def test_refuses_to_open_a_log_that_is_not_a_ledger(self):
        HashChainLog(self.path).append({"hello": 1})
        with self.assertRaisesRegex(LedgerError, "unknown type"):
            PaymentLedger(self.path, POLICY)


class CheckLedgerTest(unittest.TestCase):
    """Logs with valid hashes that are still impossible as a ledger."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "p.jsonl"
        self.clock = FakeClock()
        ledger = PaymentLedger(self.path, POLICY, clock=self.clock)
        ledger.request(pay("80"))            # 0 held
        ledger.request(pay("1"))             # 1 allowed
        self.good = list(ledger.log.entries)

    def tearDown(self):
        self.tmp.cleanup()

    def forge(self, *records):
        """Append records through the raw chain, so hashes are valid but the ledger may not be."""
        log = HashChainLog(self.path)
        for r in records:
            log.append(r)
        return log.entries

    def test_unverified_input_is_refused_cleanly(self):
        for bad in ([["not", "a", "dict"]], [{"type": "decision"}], [dict(self.good[1])]):
            with self.assertRaisesRegex(LedgerError, "run verify_entries first"):
                check_ledger(bad)

    def approval(self, **kw):
        r = {"type": "approval", "time": "2026-11-13T23:55:00Z", "decision_seq": 0, "approved_by": "alice", "note": ""}
        r.update(kw)
        return r

    def assertBad(self, pattern, *records):
        with self.assertRaisesRegex(LedgerError, pattern):
            check_ledger(self.forge(*records))

    def test_good_log_passes(self):
        check_ledger(self.good)
        check_ledger(self.forge(self.approval()))

    def test_approval_of_missing_entry(self):
        self.assertBad("does not point to an earlier entry", self.approval(decision_seq=7))

    def test_approval_of_allowed_payment(self):
        self.assertBad("entry 1 is not a held payment", self.approval(decision_seq=1))

    def test_double_approval(self):
        self.assertBad("already approved or rejected", self.approval(), self.approval(approved_by="bob"))

    def test_reject_after_approve(self):
        rej = {"type": "rejection", "time": "2026-11-13T23:56:00Z", "decision_seq": 0, "rejected_by": "bob", "note": ""}
        self.assertBad("already approved or rejected", self.approval(), rej)

    def test_self_approval(self):
        self.assertBad("signed off its own payment", self.approval(approved_by="agent-1"))

    def test_time_backwards(self):
        self.assertBad("time goes backwards", self.approval(time="2026-11-13T00:00:00Z"))

    def test_bad_time(self):
        self.assertBad("not YYYY-MM-DDTHH:MM:SSZ", self.approval(time="2026-11-13 23:55"))
        self.path.unlink()
        decision = {k: v for k, v in self.good[0].items() if k not in ("seq", "prev_hash", "hash")}
        self.assertBad("not YYYY-MM-DDTHH:MM:SSZ", dict(decision, time="2026-13-45T99:99:99Z"))

    def test_unknown_or_unhashable_type(self):
        for value in ("refund", [1], {"a": 1}, None):
            self.path.unlink()
            with self.subTest(type=value):
                self.assertBad("unknown type", dict(self.approval(), type=value))

    def test_wrong_fields(self):
        self.assertBad("missing note", {k: v for k, v in self.approval().items() if k != "note"})
        self.path.unlink()
        self.assertBad("unexpected extra", dict(self.approval(decision_seq=0), extra=1))

    def test_bad_decision_values(self):
        base = {k: v for k, v in self.good[1].items() if k not in ("seq", "prev_hash", "hash")}
        cases = [
            ("amount", "1E+1", "plain decimal"), ("amount", "-5", "plain decimal"), ("amount", 5, "plain decimal"),
            ("amount", "abc", "plain decimal"), ("agent", " x", "agent"), ("reversible", "yes", "reversible"),
            ("outcome", "maybe", "unknown outcome"), ("rule_id", "", "rule_id"), ("memo", None, "memo"),
        ]
        for field, value, pattern in cases:
            self.path.unlink()
            with self.subTest(field=field, value=value):
                self.assertBad(pattern, dict(base, **{field: value}))
        self.path.unlink()
        self.assertBad("only a deny can have no rule", dict(base, rule_id=None))


if __name__ == "__main__":
    unittest.main()
