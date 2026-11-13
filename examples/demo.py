"""A research agent pays for data APIs in USDC under two rules.

Run from the repository root:

    python examples/demo.py

It writes two files to the current folder:

    demo_payments.jsonl           the real log
    demo_payments_tampered.jsonl  a copy where one past amount was edited

then shows that the edited copy fails verification.
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_payment_log import ChainError, PaymentLedger, PaymentRequest, Policy, Rule, verify_file  # noqa: E402

LOG = Path("demo_payments.jsonl")
TAMPERED = Path("demo_payments_tampered.jsonl")
for f in (LOG, TAMPERED):
    if f.exists():
        f.unlink()

policy = Policy([
    Rule(id="data-vendors", asset="USDC", max_amount="200", daily_limit="300", approval_above="50",
         recipients=["api.marketdata.example", "api.news.example"]),
    Rule(id="small-anything", asset="USDC", max_amount="5", daily_limit="20"),
])

# fixed clock, one minute per entry, so the output is the same on every run
start = datetime(2026, 11, 13, 9, 0, tzinfo=timezone.utc)
ticks = (start + timedelta(minutes=m) for m in range(10_000))
ledger = PaymentLedger(LOG, policy, clock=lambda: next(ticks))


def pay(recipient, amount, reversible=True, memo=""):
    e = ledger.request(PaymentRequest(agent="research-agent", recipient=recipient, amount=amount,
                                      asset="USDC", reversible=reversible, memo=memo))
    print(f"#{e['seq']} {amount:>6} USDC -> {recipient:<24} {e['outcome'].upper():<5} ({e['rule_id']}: {e['reason']})")
    return e


pay("api.marketdata.example", "12.40", memo="price feed, 1 day")
held = pay("api.news.example", "75", memo="news archive, 1 month")
pay("tips.example", "3", memo="tip for a dataset")
pay("unknown-wallet.example", "40", memo="agent wanted to buy a report")
irreversible = pay("api.marketdata.example", "20", reversible=False, memo="onchain transfer, cannot be undone")

a = ledger.approve(held["seq"], approver="ops-lead", note="known vendor, monthly plan")
print(f"#{a['seq']} approval of #{held['seq']} by {a['approved_by']}")
r = ledger.reject(irreversible["seq"], approver="ops-lead", note="use the card plan instead")
print(f"#{r['seq']} rejection of #{irreversible['seq']} by {r['rejected_by']}")

n, head = verify_file(LOG)
print(f"\n{LOG}: {n} entries, head {head[:16]}...")

# someone edits entry #1 in a copy to hide that the payment was 75, not 7.5
TAMPERED.write_bytes(LOG.read_bytes().replace(b'"amount":"75"', b'"amount":"7.5"', 1))
try:
    verify_file(TAMPERED)
    print(f"{TAMPERED}: passed, this should not happen")
except ChainError as e:
    print(f"{TAMPERED}: {e}")
