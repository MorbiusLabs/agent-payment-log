import unittest
from decimal import Decimal

from agent_payment_log import ALLOW, DENY, HOLD, PaymentRequest, Policy, Rule
from agent_payment_log.policy import fmt, to_amount


def req(amount="10", recipient="api.example", asset="USDC", reversible=True, **kw):
    return PaymentRequest(agent=kw.get("agent", "agent-1"), recipient=recipient, amount=amount, asset=asset,
                          reversible=reversible, memo=kw.get("memo", ""))


def rule(**kw):
    base = dict(id="r", asset="USDC", max_amount="10", daily_limit="10")
    base.update(kw)
    return Rule(**base)


POLICY = Policy([
    Rule(id="vendors", asset="USDC", max_amount="100", daily_limit="250", approval_above="50",
         recipients=["api.example", "data.example"]),
    Rule(id="anyone-small", asset="USDC", max_amount="5", daily_limit="20"),
])


class AmountTest(unittest.TestCase):
    def test_exact(self):
        self.assertEqual(req("0.10").amount + req("0.20").amount, Decimal("0.30"))

    def test_accepted_forms(self):
        self.assertEqual(to_amount(" 12.40 "), Decimal("12.40"))
        self.assertEqual(to_amount(7), Decimal(7))
        self.assertEqual(to_amount(Decimal("1E+3")), Decimal(1000))
        self.assertEqual(to_amount("0." + "0" * 17 + "1"), Decimal("1E-18"))  # 18 places is fine
        # largest allowed value, 30 digits; must not be rounded up to 1E+30 and refused
        self.assertEqual(to_amount("9" * 30), Decimal("9" * 30))
        self.assertEqual(to_amount("-" + "9" * 30), Decimal("-" + "9" * 30))

    def test_refused_types(self):
        for bad in (0.1, True, None, [1], b"1"):
            with self.assertRaises(TypeError, msg=repr(bad)):
                to_amount(bad)

    def test_refused_values(self):
        for bad in ("", "abc", "NaN", "sNaN", "Infinity", "-Infinity", "1,5",
                    "0." + "0" * 18 + "1", "1e30"):
            with self.assertRaises(ValueError, msg=bad):
                to_amount(bad)

    def test_plain_format(self):
        self.assertEqual(fmt(Decimal("1E+3")), "1000")
        self.assertEqual(fmt(Decimal("12.40")), "12.40")
        self.assertEqual(fmt(Decimal("1E-18")), "0.000000000000000001")


class RequestTest(unittest.TestCase):
    def test_non_positive(self):
        for bad in ("0", "-1", "-0.01"):
            with self.assertRaises(ValueError, msg=bad):
                req(bad)

    def test_names(self):
        for bad in ("", " ", " agent", "agent ", 5, None):
            with self.assertRaises(ValueError, msg=repr(bad)):
                req(agent=bad)
        with self.assertRaises(ValueError):
            req(recipient="")
        with self.assertRaises(ValueError):
            req(asset=" USDC")

    def test_flags(self):
        with self.assertRaises(TypeError):
            req(reversible="yes")
        with self.assertRaises(TypeError):
            req(reversible=1)
        with self.assertRaises(TypeError):
            req(memo=None)

    def test_frozen(self):
        r = req()
        with self.assertRaises(Exception):
            r.amount = Decimal(1)


class RuleTest(unittest.TestCase):
    def test_valid(self):
        r = rule(approval_above="0", recipients=("a", "b", "a"))
        self.assertEqual(r.approval_above, Decimal(0))
        self.assertEqual(r.recipients, frozenset({"a", "b"}))

    def test_refused(self):
        cases = [
            (dict(id=5), ValueError), (dict(id=""), ValueError), (dict(asset=" USDC"), ValueError),
            (dict(max_amount="0"), ValueError), (dict(daily_limit="-1"), ValueError),
            (dict(max_amount="11"), ValueError),                   # above daily_limit
            (dict(approval_above="-1"), ValueError), (dict(max_amount=1.5), TypeError),
            (dict(recipients="api.example"), TypeError), (dict(recipients=[]), ValueError),
            (dict(recipients=["ok", ""]), ValueError), (dict(irreversible_needs_approval="no"), TypeError),
        ]
        for kw, err in cases:
            with self.assertRaises(err, msg=repr(kw)):
                rule(**kw)

    def test_policy_checks(self):
        r = rule()
        with self.assertRaises(ValueError):
            Policy([r, r])
        with self.assertRaises(TypeError):
            Policy([{"id": "r"}])
        self.assertIs(POLICY.rule("vendors"), POLICY.rules[0])
        with self.assertRaises(KeyError):
            POLICY.rule("missing")


class EvaluateTest(unittest.TestCase):
    def check(self, request, spent, outcome, rule_id):
        v = POLICY.evaluate(request, Decimal(spent))
        self.assertEqual((v.outcome, v.rule_id), (outcome, rule_id), v.reason)
        return v

    def test_allow_within_limits(self):
        v = self.check(req("50"), "0", ALLOW, "vendors")  # exactly at approval_above is still allowed
        self.assertEqual(v.reason, "within limits")

    def test_hold_above_approval_threshold(self):
        v = self.check(req("50.01"), "0", HOLD, "vendors")
        self.assertEqual(v.reason, "50.01 is above 50, needs a human")

    def test_hold_irreversible(self):
        v = self.check(req("1", reversible=False), "0", HOLD, "vendors")
        self.assertEqual(v.reason, "payment cannot be reversed, needs a human")

    def test_irreversible_allowed_when_rule_says_so(self):
        p = Policy([rule(irreversible_needs_approval=False)])
        self.assertEqual(p.evaluate(req("1", reversible=False), Decimal(0)).outcome, ALLOW)

    def test_deny_above_single_limit(self):
        self.check(req("100"), "0", HOLD, "vendors")          # exactly max: allowed by size, held by approval
        v = self.check(req("100.01"), "0", DENY, "vendors")
        self.assertEqual(v.reason, "100.01 is above the single-payment limit of 100")

    def test_daily_limit_boundary(self):
        self.check(req("10"), "240", ALLOW, "vendors")   # 250 exactly is fine
        v = self.check(req("10.01"), "240", DENY, "vendors")
        self.assertEqual(v.reason, "would bring today's total to 250.01, limit is 250")

    def test_reasons_never_use_scientific_notation(self):
        p = Policy([rule(max_amount="1E+3", daily_limit="1E+3")])
        self.assertEqual(p.evaluate(req("2E+3"), Decimal(0)).reason,
                         "2000 is above the single-payment limit of 1000")

    def test_unknown_recipient_falls_to_second_rule(self):
        self.check(req("5", recipient="stranger"), "0", ALLOW, "anyone-small")
        self.check(req("6", recipient="stranger"), "0", DENY, "anyone-small")

    def test_no_rule_for_asset(self):
        v = self.check(req("1", asset="EURC"), "0", DENY, None)
        self.assertEqual(v.reason, "no rule covers EURC to api.example")

    def test_empty_policy_denies_everything(self):
        self.assertEqual(Policy([]).evaluate(req("1"), Decimal(0)).outcome, DENY)

    def test_first_matching_rule_decides(self):
        # vendors covers api.example, so the payment is not judged by anyone-small
        self.check(req("60"), "0", HOLD, "vendors")

    def test_large_sums_are_not_rounded(self):
        big = "9" * 29 + ".999999999999999999"   # 47 significant digits
        almost = "9" * 29 + ".999999999999999998"
        p = Policy([rule(max_amount=big, daily_limit=big)])
        tiny = req("0.000000000000000001")
        self.assertEqual(p.evaluate(tiny, Decimal(big)).outcome, DENY)
        # exactly at the limit: rounding to 28 digits would turn this into 1E+29 and wrongly deny
        self.assertEqual(p.evaluate(tiny, Decimal(almost)).outcome, ALLOW)


if __name__ == "__main__":
    unittest.main()
