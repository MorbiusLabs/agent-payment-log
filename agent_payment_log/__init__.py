"""agent-payment-log: spending rules and a verifiable decision log for AI agents that pay."""

from .chain import GENESIS, ChainError, HashChainLog, verify_entries, verify_file
from .ledger import LedgerError, PaymentLedger, check_ledger
from .policy import ALLOW, DENY, HOLD, PaymentRequest, Policy, Rule, Verdict

__version__ = "0.1.0"

__all__ = [
    "ALLOW", "DENY", "HOLD", "GENESIS",
    "ChainError", "HashChainLog", "LedgerError", "PaymentLedger",
    "PaymentRequest", "Policy", "Rule", "Verdict",
    "check_ledger", "verify_entries", "verify_file",
]
