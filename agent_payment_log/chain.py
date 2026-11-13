"""Append-only, hash-chained JSON Lines log.

Every entry stores the hash of the entry before it, so changing, removing or
reordering any past entry breaks every hash that follows. Anyone holding a copy
of the file can check this with ``verify_file`` and no secret key.

Each line must be exactly the canonical JSON of its entry (sorted keys, no
spaces, no duplicate keys, no NaN). That leaves one way to read every line, so
two programs can never show different contents for the same verified file.

A hash chain proves the history was not edited *after* someone saw the head
hash. It does not stop the writer from rebuilding the whole file from scratch,
so share the head hash with the counterparty (or anchor it somewhere public)
if you need that guarantee.
"""

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

GENESIS = "0" * 64
RESERVED = ("seq", "prev_hash", "hash")
HASH_RE = re.compile(r"^[0-9a-f]{64}$")

PathLike = Union[str, Path]


class ChainError(Exception):
    """The log is malformed, unreadable or has been tampered with."""


def canonical(obj: Any) -> str:
    """Deterministic JSON: sorted keys, no extra whitespace, UTF-8 kept as is, no NaN or Infinity."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def entry_hash(body: Dict[str, Any]) -> str:
    """SHA-256 of the canonical JSON of an entry without its own ``hash`` field."""
    return hashlib.sha256(canonical(body).encode("utf-8")).hexdigest()


def _no_duplicates(pairs):
    obj = {}
    for k, v in pairs:
        if k in obj:
            raise ValueError(f"duplicate key {k!r}")
        obj[k] = v
    return obj


def _reject_constant(name):
    raise ValueError(f"{name} is not allowed")


def parse_line(line: str, n: int) -> Dict[str, Any]:
    """Parse one log line strictly. ``n`` is the 1-based line number for error messages."""
    try:
        obj = json.loads(line, object_pairs_hook=_no_duplicates, parse_constant=_reject_constant)
    except ValueError as e:  # JSONDecodeError is a ValueError
        raise ChainError(f"line {n}: invalid JSON ({getattr(e, 'msg', e)})") from None
    except RecursionError:
        raise ChainError(f"line {n}: invalid JSON (too deeply nested)") from None
    if not isinstance(obj, dict):
        raise ChainError(f"line {n}: not a JSON object")
    try:
        text = canonical(obj)
    except ValueError:  # a number like 1e999 reads as infinity
        raise ChainError(f"line {n}: has a number too large to store") from None
    if line != text:
        raise ChainError(f"line {n}: not in canonical form (sorted keys, no spaces)")
    return obj


def verify_entries(entries: Iterable[Dict[str, Any]], expected_head: Optional[str] = None) -> str:
    """Check the whole chain and return the head hash.

    Raises ``ChainError`` naming the first entry that does not check out.
    If ``expected_head`` is given, the log must end exactly at that hash.
    """
    if expected_head is not None and not (isinstance(expected_head, str) and HASH_RE.match(expected_head)):
        raise ChainError("expected head must be 64 lowercase hex characters")
    prev = GENESIS
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ChainError(f"entry {i}: not a JSON object")
        seq = entry.get("seq")
        if isinstance(seq, bool) or seq != i:
            raise ChainError(f"entry {i}: seq is {seq!r}, expected {i}")
        if entry.get("prev_hash") != prev:
            raise ChainError(f"entry {i}: prev_hash does not match the previous entry")
        body = {k: v for k, v in entry.items() if k != "hash"}
        try:
            expected = entry_hash(body)
        except ValueError:
            raise ChainError(f"entry {i}: contains NaN or Infinity") from None
        if entry.get("hash") != expected:
            raise ChainError(f"entry {i}: content does not match its hash")
        prev = entry["hash"]
    if expected_head is not None and prev != expected_head:
        raise ChainError("log does not end at the expected head hash")
    return prev


def read_entries(path: PathLike, missing_ok: bool = True) -> List[Dict[str, Any]]:
    """Read and strictly parse a JSON Lines log.

    A missing file is an empty log when ``missing_ok`` is true, an error otherwise.
    Unreadable files raise ``ChainError`` instead of an OS or Unicode error.
    """
    p = Path(path)
    if not p.exists():
        if missing_ok:
            return []
        raise ChainError(f"{p}: file not found")
    if not p.is_file():
        raise ChainError(f"{p}: not a regular file")
    try:
        raw = p.read_bytes()
    except OSError as e:
        raise ChainError(f"{p}: cannot read ({e.strerror})") from None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ChainError(f"{p}: not UTF-8 text") from None
    if "\r" in text:
        raise ChainError(f"{p}: has Windows line endings (CRLF), the log must use LF only")
    if text and not text.endswith("\n"):
        raise ChainError(f"{p}: last line is incomplete (no trailing newline), the file may be cut off")
    entries = []
    for n, line in enumerate(text.split("\n")[:-1], 1):
        entries.append(parse_line(line, n))
    return entries


def verify_file(path: PathLike, expected_head: Optional[str] = None) -> Tuple[int, str]:
    """Verify an existing log file. Returns (number of entries, head hash)."""
    entries = read_entries(path, missing_ok=False)
    return len(entries), verify_entries(entries, expected_head)


class HashChainLog:
    """A log file that can only grow. It is verified on open and checked before every append.

    One process should write to a file at a time. If the file changes on disk
    behind this object's back, the next append refuses instead of forking the chain.
    """

    def __init__(self, path: PathLike):
        self.path = Path(path)
        self._entries = read_entries(self.path)
        self._head = verify_entries(self._entries)
        self._size = self._disk_size()

    def _disk_size(self) -> int:
        return self.path.stat().st_size if self.path.exists() else 0

    @property
    def entries(self) -> Tuple[Dict[str, Any], ...]:
        # deep copies, so callers cannot edit the in-memory chain
        return tuple(json.loads(canonical(e)) for e in self._entries)

    @property
    def head(self) -> str:
        return self._head

    def __len__(self) -> int:
        return len(self._entries)

    def append(self, record: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(record, dict):
            raise TypeError("record must be a dict")
        clash = [k for k in RESERVED if k in record]
        if clash:
            raise ValueError(f"record may not set reserved fields: {', '.join(clash)}")
        if any(not isinstance(k, str) for k in record):
            raise TypeError("record keys must be strings")
        body = json.loads(canonical(record))  # fails early on values JSON cannot hold
        if self._disk_size() != self._size:
            raise ChainError(f"{self.path} changed on disk since it was opened, reopen it before appending")
        body["seq"] = len(self._entries)
        body["prev_hash"] = self._head
        body["hash"] = entry_hash(body)
        data = (canonical(body) + "\n").encode("utf-8")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("ab") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        self._entries.append(body)
        self._head = body["hash"]
        self._size += len(data)
        return json.loads(canonical(body))
