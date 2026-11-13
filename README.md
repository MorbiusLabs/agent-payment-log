# agent-payment-log

Spending rules and a tamper-evident decision log for AI agents that make payments.

An agent that can pay needs two things a human treasury already has: limits that are checked before money moves, and a record of why each payment was allowed. This library gives you both in plain Python with no dependencies.

- **Rules before money moves.** Single-payment cap, daily cap per agent, recipient allowlist, and a human sign-off for large or irreversible payments.
- **Every decision is logged.** Allowed, held, denied, approved, rejected. Each entry names the rule that decided and the person who signed off.
- **Anyone can check the log.** Entries are hash-chained, so editing, deleting or reordering a past entry is detected. Checking needs only the file, no keys.

It does not move money. Your agent asks the ledger first and pays only when the answer is `allow` or a human approved the payment.

## Quick start

Requires Python 3.9 or newer. No dependencies.

```bash
git clone https://github.com/MorbiusLabs/agent-payment-log
cd agent-payment-log
python examples/demo.py
```

Output:

```text
#0  12.40 USDC -> api.marketdata.example   ALLOW (data-vendors: within limits)
#1     75 USDC -> api.news.example         HOLD  (data-vendors: 75 is above 50, needs a human)
#2      3 USDC -> tips.example             ALLOW (small-anything: within limits)
#3     40 USDC -> unknown-wallet.example   DENY  (small-anything: 40 is above the single-payment limit of 5)
#4     20 USDC -> api.marketdata.example   HOLD  (data-vendors: payment cannot be reversed, needs a human)
#5 approval of #1 by ops-lead
#6 rejection of #4 by ops-lead

demo_payments.jsonl: 7 entries, head 69f9256f6f17bcca...
demo_payments_tampered.jsonl: entry 1: content does not match its hash
```

The demo writes the real log to `demo_payments.jsonl`, then makes a copy where one past amount is changed from 75 to 7.5. The copy fails the check.

### Try the command line

Install the package to get the `agent-payment-log` command (or run `python -m agent_payment_log` instead):

```bash
pip install .
```

```text
$ agent-payment-log show demo_payments.jsonl
#0    2026-11-13T09:00:00Z  research-agent -> api.marketdata.example  12.40 USDC  ALLOW
      rule=data-vendors  within limits
      memo: price feed, 1 day
#1    2026-11-13T09:01:00Z  research-agent -> api.news.example  75 USDC  HOLD
      rule=data-vendors  75 is above 50, needs a human
      memo: news archive, 1 month
#2    2026-11-13T09:02:00Z  research-agent -> tips.example  3 USDC  ALLOW
      rule=small-anything  within limits
      memo: tip for a dataset
#3    2026-11-13T09:03:00Z  research-agent -> unknown-wallet.example  40 USDC  DENY
      rule=small-anything  40 is above the single-payment limit of 5
      memo: agent wanted to buy a report
#4    2026-11-13T09:04:00Z  research-agent -> api.marketdata.example  20 USDC  HOLD  (irreversible)
      rule=data-vendors  payment cannot be reversed, needs a human
      memo: onchain transfer, cannot be undone
#5    2026-11-13T09:05:00Z  #1 APPROVED by ops-lead
      known vendor, monthly plan
#6    2026-11-13T09:06:00Z  #4 REJECTED by ops-lead
      use the card plan instead
OK: 7 entries, head 69f9256f6f17bccaa5557503e45665467d6a907cd5a79f565d072ae25dfbd95c
$ agent-payment-log verify demo_payments_tampered.jsonl
FAILED: entry 1: content does not match its hash
```

## Usage

```python
from agent_payment_log import PaymentLedger, PaymentRequest, Policy, Rule

policy = Policy([
    Rule(id="data-vendors", asset="USDC", max_amount="200", daily_limit="300",
         approval_above="50", recipients=["api.marketdata.example"]),
    Rule(id="small-anything", asset="USDC", max_amount="5", daily_limit="20"),
])
ledger = PaymentLedger("payments.jsonl", policy)

entry = ledger.request(PaymentRequest(
    agent="research-agent", recipient="api.marketdata.example",
    amount="75", asset="USDC", reversible=True, memo="news archive",
))

if entry["outcome"] == "allow":
    ...  # pay now
elif entry["outcome"] == "hold":
    ledger.approve(entry["seq"], approver="ops-lead", note="known vendor")
    ...  # pay after approval
```

Other calls:

| Call | What it does |
|---|---|
| `ledger.reject(seq, approver, note="")` | a human refuses a held payment |
| `ledger.status(seq)` | `allowed`, `denied`, `pending`, `approved` or `rejected` |
| `ledger.pending()` | held payments still waiting for a human, oldest first |
| `ledger.spent_on(agent, asset, "2026-11-13")` | what the agent was cleared to spend that UTC day |
| `ledger.log.head` | current head hash, to share with a counterparty |
| `verify_file(path, expected_head=None)` | check a log file, returns `(number of entries, head hash)` |
| `check_ledger(entries)` | check that verified entries also make sense as a ledger |

Mistakes raise `LedgerError` (for example approving twice), `ChainError` (a broken or edited file), or `ValueError`/`TypeError` (bad input).

### How rules are applied

Rules are checked in order. The first rule whose asset and recipient match decides, like a firewall.

| Check | Result |
|---|---|
| No rule covers the asset and recipient | `deny` |
| Amount above `max_amount` | `deny` |
| Today's total plus this payment above `daily_limit` | `deny` |
| Amount above `approval_above` | `hold` |
| `reversible=False` and `irreversible_needs_approval` (on by default) | `hold` |
| Otherwise | `allow` |

Details:

- Amounts are exact decimals with up to 18 decimal places. Pass strings, ints or `Decimal`. Floats are refused, because `0.1 + 0.2` is not `0.3` in binary.
- The daily total is per agent and per asset, by UTC day. It counts payments allowed that day and held payments approved that day.
- The daily limit is checked again at the moment a held payment is approved.
- An agent cannot approve its own payment. A held payment can be approved or rejected once.
- Entry times never go backwards. If the clock reads earlier than the last entry, the ledger refuses to write.

### Log format

One JSON object per line, written as canonical JSON: sorted keys, no spaces, UTF-8, no duplicate keys, no `NaN`. Lines in any other form are refused, so every program reads a verified file the same way.

Each entry has `seq`, `prev_hash` and `hash`. `hash` is the SHA-256 of the entry's canonical JSON without the `hash` field. The first entry links to 64 zeros. Entry types are `decision`, `approval` and `rejection`.

First line of the demo log:

```json
{"agent":"research-agent","amount":"12.40","asset":"USDC","hash":"b14d2ecf990e4973b1ff87db5e580a3b612f5e89539a097c9d11e21ad05d8963","memo":"price feed, 1 day","outcome":"allow","prev_hash":"0000000000000000000000000000000000000000000000000000000000000000","reason":"within limits","recipient":"api.marketdata.example","reversible":true,"rule_id":"data-vendors","seq":0,"time":"2026-11-13T09:00:00Z","type":"decision"}
```

### What `verify` checks

1. The file is UTF-8 text, every line is canonical JSON, and the last line is complete.
2. Every hash matches its entry and links to the one before it.
3. With `--head`, the log ends exactly at the hash you were given.
4. The entries make sense as a ledger: only held payments get approved or rejected, each at most once, never by the paying agent, and time never runs backwards.

Exit code 0 means the log checks out, 1 means it does not, 2 means the command was used wrong.

## What the hash chain does and does not prove

It proves the history was not changed after you saw it. If a counterparty saved the head hash yesterday, any edit to yesterday's entries makes `verify --head` fail today.

It does not prove who wrote the log, and it does not stop the writer from rebuilding the whole file before anyone saw it. For that, share the head hash with the other side regularly or publish it somewhere you do not control, for example in the memo of an onchain transaction.

Other limits:

- One writer per log file. If the file changes on disk behind an open ledger, the next write is refused instead of forking the chain, but there is no locking between processes.
- The ledger decides and records. It cannot force an agent to ask it before paying.
- `check_ledger` cannot re-check spending limits, because the rules are not stored in the log.

## Tests

```bash
python -m unittest discover -s tests -v
```

101 tests. They also run the demo and the examples in this README and compare the output line by line. CI runs them on Linux with Python 3.9 to 3.13, and on macOS and Windows with Python 3.13, then installs the package and runs the command line.

## License

MIT. Built by [Morbius Labs](https://x.com/MorbiusLabs).
