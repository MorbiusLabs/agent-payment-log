"""The payment ledger: every decision an agent's payment goes through, in one verifiable log.

Entry types written to the log:

  decision   the policy's verdict on a payment request (allow, hold or deny)
  approval   a human released a held payment
  rejection  a human refused a held payment

Each entry says which rule decided and, for approvals, who signed off. That is
the trail a counterparty or auditor can check later with ``check_ledger``.
"""

import re
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Dict, List, Sequence, Union

from .chain import HashChainLog
from .policy import ALLOW, DENY, HOLD, PaymentRequest, Policy, exact, fmt, to_amount

Clock = Callable[[], datetime]

TIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
CHAIN_FIELDS = {"seq", "prev_hash", "hash"}
FIELDS = {
    "decision": {"type", "time", "agent", "recipient", "amount", "asset", "reversible", "memo",
                 "outcome", "rule_id", "reason"},
    "approval": {"type", "time", "decision_seq", "approved_by", "note"},
    "rejection": {"type", "time", "decision_seq", "rejected_by", "note"},
}


class LedgerError(Exception):
    """An action is not allowed, or a log does not follow the ledger's rules."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(t: datetime) -> str:
    if not isinstance(t, datetime) or t.tzinfo is None or t.utcoffset() is None:
        raise ValueError("clock must return timezone-aware datetimes")
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _valid_time(s: Any) -> bool:
    if not isinstance(s, str) or not TIME_RE.match(s):
        return False
    try:
        datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return False
    return True


def _name(v: Any) -> bool:
    return isinstance(v, str) and v.strip() != "" and v == v.strip()


def check_ledger(entries: Sequence[Dict[str, Any]]) -> None:
    """Check that hash-verified entries also make sense as a payment ledger.

    Catches logs that are internally consistent but impossible, for example an
    approval of a payment that was never held, two approvals of one payment,
    an agent approving itself, or time running backwards. Raises ``LedgerError``.
    """
    resolved = set()
    last_time = ""
    for i, e in enumerate(entries):
        if not isinstance(e, dict) or e.get("seq") != i or isinstance(e.get("seq"), bool):
            raise LedgerError(f"entry {i}: not a numbered log entry, run verify_entries first")
        seq = i
        kind = e.get("type")
        where = f"entry {seq}"
        if not isinstance(kind, str) or kind not in FIELDS:
            raise LedgerError(f"{where}: unknown type {kind!r}")
        fields = set(e) - CHAIN_FIELDS
        if fields != FIELDS[kind]:
            missing, extra = FIELDS[kind] - fields, fields - FIELDS[kind]
            detail = "; ".join(x for x in (
                f"missing {', '.join(sorted(missing))}" if missing else "",
                f"unexpected {', '.join(sorted(extra))}" if extra else "") if x)
            raise LedgerError(f"{where}: {kind} entry has wrong fields ({detail})")
        if not _valid_time(e["time"]):
            raise LedgerError(f"{where}: time {e['time']!r} is not YYYY-MM-DDTHH:MM:SSZ")
        if e["time"] < last_time:
            raise LedgerError(f"{where}: time goes backwards")
        last_time = e["time"]

        if kind == "decision":
            for f in ("agent", "recipient", "asset"):
                if not _name(e[f]):
                    raise LedgerError(f"{where}: {f} must be a non-empty string without outer spaces")
            try:
                amount = to_amount(e["amount"]) if isinstance(e["amount"], str) else None
            except ValueError:
                amount = None
            if amount is None or amount <= 0 or fmt(amount) != e["amount"]:
                raise LedgerError(f"{where}: amount {e['amount']!r} is not a positive plain decimal string")
            if not isinstance(e["reversible"], bool):
                raise LedgerError(f"{where}: reversible must be true or false")
            if e["outcome"] not in (ALLOW, HOLD, DENY):
                raise LedgerError(f"{where}: unknown outcome {e['outcome']!r}")
            if e["rule_id"] is None:
                if e["outcome"] != DENY:
                    raise LedgerError(f"{where}: only a deny can have no rule")
            elif not _name(e["rule_id"]):
                raise LedgerError(f"{where}: rule_id must be a non-empty string or null")
            for f in ("memo", "reason"):
                if not isinstance(e[f], str):
                    raise LedgerError(f"{where}: {f} must be a string")
        else:
            target = e["decision_seq"]
            person = e["approved_by" if kind == "approval" else "rejected_by"]
            if isinstance(target, bool) or not isinstance(target, int) or not 0 <= target < seq:
                raise LedgerError(f"{where}: decision_seq {target!r} does not point to an earlier entry")
            decision = entries[target]
            if decision["type"] != "decision" or decision["outcome"] != HOLD:
                raise LedgerError(f"{where}: entry {target} is not a held payment")
            if target in resolved:
                raise LedgerError(f"{where}: entry {target} was already approved or rejected")
            if not _name(person):
                raise LedgerError(f"{where}: approver must be a non-empty string without outer spaces")
            if person == decision["agent"]:
                raise LedgerError(f"{where}: agent {person!r} signed off its own payment")
            if not isinstance(e["note"], str):
                raise LedgerError(f"{where}: note must be a string")
            resolved.add(target)


class PaymentLedger:
    """Policy plus log. Opening checks the file both as a hash chain and as a ledger."""

    def __init__(self, path: Union[str, Path], policy: Policy, clock: Clock = utc_now):
        if not isinstance(policy, Policy):
            raise TypeError("policy must be a Policy")
        self.log = HashChainLog(path)
        check_ledger(self.log.entries)
        self.policy = policy
        self.clock = clock

    # ---- state derived from the log -------------------------------------------------

    def status(self, seq: int) -> str:
        """allowed, denied, pending, approved or rejected."""
        entries = self.log.entries
        decision = self._decision(entries, seq)
        if decision["outcome"] == ALLOW:
            return "allowed"
        if decision["outcome"] == DENY:
            return "denied"
        for e in entries[seq + 1:]:
            if e.get("decision_seq") == seq:
                return "approved" if e["type"] == "approval" else "rejected"
        return "pending"

    def spent_on(self, agent: str, asset: str, day: str) -> Decimal:
        """Money the agent was cleared to send in one UTC day, given as YYYY-MM-DD.

        Counts payments allowed that day plus held payments approved that day.
        """
        if not isinstance(day, str) or not DAY_RE.match(day):
            raise ValueError("day must look like YYYY-MM-DD")
        entries = self.log.entries
        total = Decimal(0)
        with exact():
            for e in entries:
                if e["time"][:10] != day:
                    continue
                if e["type"] == "decision" and e["outcome"] == ALLOW:
                    src = e
                elif e["type"] == "approval":
                    src = entries[e["decision_seq"]]
                else:
                    continue
                if src["agent"] == agent and src["asset"] == asset:
                    total += Decimal(src["amount"])
        return total

    def pending(self) -> List[Dict[str, Any]]:
        """Held payments still waiting for a human, oldest first."""
        entries = self.log.entries
        done = {e["decision_seq"] for e in entries if e["type"] != "decision"}
        return [e for e in entries if e["type"] == "decision" and e["outcome"] == HOLD and e["seq"] not in done]

    # ---- actions --------------------------------------------------------------------

    def request(self, req: PaymentRequest) -> Dict[str, Any]:
        """Run the policy on a payment and record the verdict."""
        if not isinstance(req, PaymentRequest):
            raise TypeError("req must be a PaymentRequest")
        now = self._now()
        spent = self.spent_on(req.agent, req.asset, now[:10])
        verdict = self.policy.evaluate(req, spent)
        return self.log.append({
            "type": "decision",
            "time": now,
            "agent": req.agent,
            "recipient": req.recipient,
            "amount": fmt(req.amount),
            "asset": req.asset,
            "reversible": req.reversible,
            "memo": req.memo,
            "outcome": verdict.outcome,
            "rule_id": verdict.rule_id,
            "reason": verdict.reason,
        })

    def approve(self, seq: int, approver: str, note: str = "") -> Dict[str, Any]:
        """A human releases a held payment. The daily limit is checked again at this moment."""
        decision = self._pending(seq, approver, note)
        now = self._now()
        try:
            rule = self.policy.rule(decision["rule_id"])
        except KeyError:
            raise LedgerError(f"rule {decision['rule_id']!r} is no longer in the policy") from None
        spent = self.spent_on(decision["agent"], decision["asset"], now[:10])
        with exact():
            total = spent + Decimal(decision["amount"])
        if total > rule.daily_limit:
            raise LedgerError(f"approving entry {seq} would bring today's total to {fmt(total)}, "
                              f"limit is {fmt(rule.daily_limit)}")
        return self.log.append({"type": "approval", "time": now, "decision_seq": seq,
                                "approved_by": approver, "note": note})

    def reject(self, seq: int, approver: str, note: str = "") -> Dict[str, Any]:
        """A human refuses a held payment."""
        self._pending(seq, approver, note)
        return self.log.append({"type": "rejection", "time": self._now(), "decision_seq": seq,
                                "rejected_by": approver, "note": note})

    # ---- helpers --------------------------------------------------------------------

    def _now(self) -> str:
        now = _stamp(self.clock())
        entries = self.log.entries
        if entries and now < entries[-1]["time"]:
            raise LedgerError(f"clock reads {now}, earlier than the last entry at {entries[-1]['time']}")
        return now

    @staticmethod
    def _decision(entries, seq) -> Dict[str, Any]:
        if isinstance(seq, bool) or not isinstance(seq, int) or not 0 <= seq < len(entries):
            raise LedgerError(f"no entry {seq!r}")
        if entries[seq]["type"] != "decision":
            raise LedgerError(f"entry {seq} is not a payment decision")
        return entries[seq]

    def _pending(self, seq: int, approver: str, note: str) -> Dict[str, Any]:
        if not _name(approver):
            raise LedgerError("approver must be a non-empty string without outer spaces")
        if not isinstance(note, str):
            raise LedgerError("note must be a string")
        decision = self._decision(self.log.entries, seq)
        state = self.status(seq)
        if state != "pending":
            raise LedgerError(f"entry {seq} is {state}, only pending payments can be approved or rejected")
        if decision["agent"] == approver:
            raise LedgerError("an agent cannot approve its own payment")
        return decision
