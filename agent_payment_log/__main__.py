"""Command line: check a payment log or print it as a readable list.

    agent-payment-log verify payments.jsonl [--head HASH]
    agent-payment-log show payments.jsonl

(or ``python -m agent_payment_log ...`` without installing)
Exit code: 0 when the log checks out, 1 when it does not, 2 on wrong usage.
"""

import argparse
import sys

from . import __version__
from .chain import ChainError, read_entries, verify_entries
from .ledger import LedgerError, check_ledger


def _show(entries):
    for e in entries:
        if e["type"] == "decision":
            what = f"{e['agent']} -> {e['recipient']}  {e['amount']} {e['asset']}  {e['outcome'].upper()}"
            if not e["reversible"]:
                what += "  (irreversible)"
            lines = [f"rule={e['rule_id']}  {e['reason']}"]
            if e["memo"]:
                lines.append(f"memo: {e['memo']}")
        elif e["type"] == "approval":
            what = f"#{e['decision_seq']} APPROVED by {e['approved_by']}"
            lines = [e["note"]] if e["note"] else []
        else:
            what = f"#{e['decision_seq']} REJECTED by {e['rejected_by']}"
            lines = [e["note"]] if e["note"] else []
        print(f"#{e['seq']:<4} {e['time']}  {what}")
        for line in lines:
            print(f"      {line}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="agent-payment-log",
                                 description="Check or print an agent-payment-log file.")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = ap.add_subparsers(dest="cmd", metavar="command")
    sub.required = True
    v = sub.add_parser("verify", help="check that the log was not edited and follows the ledger rules")
    v.add_argument("path", help="log file (.jsonl)")
    v.add_argument("--head", metavar="HASH", help="head hash the log must end at, as given to you earlier")
    s = sub.add_parser("show", help="verify the log, then print every entry")
    s.add_argument("path", help="log file (.jsonl)")
    args = ap.parse_args(argv)

    try:
        entries = read_entries(args.path, missing_ok=False)
        head = verify_entries(entries, getattr(args, "head", None))
        check_ledger(entries)
    except (ChainError, LedgerError) as e:
        print(f"FAILED: {e}", file=sys.stderr)
        return 1
    if args.cmd == "show":
        _show(entries)
    print(f"OK: {len(entries)} entries, head {head}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
