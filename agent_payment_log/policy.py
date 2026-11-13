"""Spending rules for an AI agent and the verdict they give on each payment."""

from dataclasses import dataclass
from decimal import Context, Decimal, InvalidOperation, localcontext
from typing import FrozenSet, Iterable, Optional, Tuple, Union

ALLOW = "allow"   # the agent may pay now
HOLD = "hold"     # a human has to approve first
DENY = "deny"     # no rule lets this payment through

MAX_DECIMALS = 18             # the most any common token uses (ETH and most ERC-20s)
MAX_AMOUNT = Decimal(10) ** 30

Amount = Union[str, int, Decimal]


def exact() -> "localcontext":
    """Decimal context wide enough that sums of valid amounts are never rounded."""
    return localcontext(Context(prec=100))


def to_amount(value: Amount, field: str = "amount") -> Decimal:
    """Exact decimal amount. Floats are refused because 0.1 + 0.2 != 0.3 in binary."""
    if isinstance(value, (bool, float)):
        raise TypeError(f"{field} must be a str, int or Decimal, not {type(value).__name__}")
    if not isinstance(value, (str, int, Decimal)):
        raise TypeError(f"{field} must be a str, int or Decimal, not {type(value).__name__}")
    try:
        d = Decimal(value.strip() if isinstance(value, str) else value)
    except (InvalidOperation, ValueError):
        raise ValueError(f"{field} is not a number: {value!r}") from None
    if not d.is_finite():
        raise ValueError(f"{field} must be a finite number")
    if d.as_tuple().exponent < -MAX_DECIMALS:
        raise ValueError(f"{field} has more than {MAX_DECIMALS} decimal places")
    if d.copy_abs() >= MAX_AMOUNT:  # abs() would round to 28 digits first
        raise ValueError(f"{field} is too large")
    return d


def fmt(d: Decimal) -> str:
    """Plain notation, never scientific: Decimal('1E+3') -> '1000', Decimal('12.40') -> '12.40'."""
    return format(d, "f")


def _text(value, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    if value != value.strip():
        raise ValueError(f"{field} must not start or end with spaces: {value!r}")
    return value


@dataclass(frozen=True)
class PaymentRequest:
    agent: str
    recipient: str
    amount: Decimal
    asset: str
    reversible: bool
    memo: str = ""

    def __post_init__(self):
        for name in ("agent", "recipient", "asset"):
            _text(getattr(self, name), name)
        amount = to_amount(self.amount)
        if amount <= 0:
            raise ValueError("amount must be greater than zero")
        object.__setattr__(self, "amount", amount)
        if not isinstance(self.reversible, bool):
            raise TypeError("reversible must be True or False")
        if not isinstance(self.memo, str):
            raise TypeError("memo must be a string")


@dataclass(frozen=True)
class Rule:
    """One spending rule. Limits apply per agent and per asset.

    max_amount      largest single payment the rule allows
    daily_limit     total one agent may spend in one UTC day in this asset
    approval_above  payments above this need a human (None = size never needs one)
    recipients      allowlist of recipients (None = any recipient)
    irreversible_needs_approval  payments that cannot be undone need a human
    """

    id: str
    asset: str
    max_amount: Decimal
    daily_limit: Decimal
    approval_above: Optional[Decimal] = None
    recipients: Optional[FrozenSet[str]] = None
    irreversible_needs_approval: bool = True

    def __post_init__(self):
        _text(self.id, "rule id")
        _text(self.asset, "asset")
        for name in ("max_amount", "daily_limit"):
            value = to_amount(getattr(self, name), name)
            if value <= 0:
                raise ValueError(f"{name} must be greater than zero")
            object.__setattr__(self, name, value)
        if self.max_amount > self.daily_limit:
            raise ValueError("max_amount cannot be larger than daily_limit")
        if self.approval_above is not None:
            value = to_amount(self.approval_above, "approval_above")
            if value < 0:
                raise ValueError("approval_above cannot be negative")
            object.__setattr__(self, "approval_above", value)
        if self.recipients is not None:
            if isinstance(self.recipients, (str, bytes)):
                raise TypeError("recipients must be a collection of strings, not one string")
            names = frozenset(_text(r, "recipient") for r in self.recipients)
            if not names:
                raise ValueError("recipients is empty, use None to allow any recipient")
            object.__setattr__(self, "recipients", names)
        if not isinstance(self.irreversible_needs_approval, bool):
            raise TypeError("irreversible_needs_approval must be True or False")

    def covers(self, req: PaymentRequest) -> bool:
        return req.asset == self.asset and (self.recipients is None or req.recipient in self.recipients)


@dataclass(frozen=True)
class Verdict:
    outcome: str
    rule_id: Optional[str]
    reason: str


class Policy:
    """Ordered rules. The first rule that covers the asset and recipient decides."""

    def __init__(self, rules: Iterable[Rule]):
        self.rules: Tuple[Rule, ...] = tuple(rules)
        for r in self.rules:
            if not isinstance(r, Rule):
                raise TypeError("every rule must be a Rule")
        ids = [r.id for r in self.rules]
        if len(ids) != len(set(ids)):
            raise ValueError("rule ids must be unique")

    def rule(self, rule_id: str) -> Rule:
        for r in self.rules:
            if r.id == rule_id:
                return r
        raise KeyError(rule_id)

    def evaluate(self, req: PaymentRequest, spent_today: Decimal) -> Verdict:
        rule = next((r for r in self.rules if r.covers(req)), None)
        if rule is None:
            return Verdict(DENY, None, f"no rule covers {req.asset} to {req.recipient}")
        with exact():
            total = spent_today + req.amount
        if req.amount > rule.max_amount:
            return Verdict(DENY, rule.id,
                           f"{fmt(req.amount)} is above the single-payment limit of {fmt(rule.max_amount)}")
        if total > rule.daily_limit:
            return Verdict(DENY, rule.id,
                           f"would bring today's total to {fmt(total)}, limit is {fmt(rule.daily_limit)}")
        if rule.approval_above is not None and req.amount > rule.approval_above:
            return Verdict(HOLD, rule.id, f"{fmt(req.amount)} is above {fmt(rule.approval_above)}, needs a human")
        if not req.reversible and rule.irreversible_needs_approval:
            return Verdict(HOLD, rule.id, "payment cannot be reversed, needs a human")
        return Verdict(ALLOW, rule.id, "within limits")
