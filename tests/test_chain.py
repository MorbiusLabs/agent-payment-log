import os
import tempfile
import unittest
from pathlib import Path

from agent_payment_log.chain import (GENESIS, ChainError, HashChainLog, canonical, entry_hash, read_entries,
                                     verify_entries, verify_file)


class ChainTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "log.jsonl"

    def tearDown(self):
        self.tmp.cleanup()

    def write_three(self):
        log = HashChainLog(self.path)
        for i in range(3):
            log.append({"n": i, "note": "ünïcode ok"})
        return log

    def lines(self):
        return self.path.read_text(encoding="utf-8").splitlines()

    def rewrite(self, entries):
        self.path.write_bytes("".join(canonical(e) + "\n" for e in entries).encode("utf-8"))

    # ---- normal use -----------------------------------------------------------------

    def test_new_log(self):
        log = HashChainLog(self.path)
        self.assertEqual(len(log), 0)
        self.assertEqual(log.head, GENESIS)
        self.assertFalse(self.path.exists())  # nothing is written until the first append

    def test_empty_file_is_empty_log(self):
        self.path.write_bytes(b"")
        self.assertEqual(verify_file(self.path), (0, GENESIS))

    def test_append_links_entries(self):
        log = self.write_three()
        e = log.entries
        self.assertEqual([x["seq"] for x in e], [0, 1, 2])
        self.assertEqual(e[0]["prev_hash"], GENESIS)
        self.assertEqual(e[1]["prev_hash"], e[0]["hash"])
        self.assertEqual(e[2]["prev_hash"], e[1]["hash"])
        self.assertEqual(log.head, e[2]["hash"])
        self.assertEqual(verify_file(self.path), (3, log.head))
        self.assertTrue(all(line == canonical(x) for line, x in zip(self.lines(), e)))

    def test_known_hash(self):
        # pins the format: if this changes, old logs would stop verifying
        e = HashChainLog(self.path).append({"a": 1})
        body = '{"a":1,"prev_hash":"' + GENESIS + '","seq":0}'
        import hashlib
        self.assertEqual(e["hash"], hashlib.sha256(body.encode()).hexdigest())

    def test_reopen_continues_chain(self):
        head = self.write_three().head
        log = HashChainLog(self.path)
        self.assertEqual(log.head, head)
        log.append({"n": 3})
        self.assertEqual(verify_file(self.path)[0], 4)

    def test_creates_parent_folder(self):
        nested = Path(self.tmp.name) / "a" / "b" / "log.jsonl"
        HashChainLog(nested).append({"x": 1})
        self.assertEqual(verify_file(nested)[0], 1)

    def test_hash_is_stable_across_key_order(self):
        a = HashChainLog(Path(self.tmp.name) / "a.jsonl").append({"x": 1, "y": 2})
        b = HashChainLog(Path(self.tmp.name) / "b.jsonl").append({"y": 2, "x": 1})
        self.assertEqual(a["hash"], b["hash"])

    def test_entries_are_copies(self):
        log = self.write_three()
        log.entries[0]["n"] = 42
        returned = log.append({"n": 3, "nested": {"k": [1]}})
        returned["n"] = 42
        returned["nested"]["k"].append(2)
        self.assertEqual(log.entries[0]["n"], 0)
        self.assertEqual(log.entries[3]["nested"], {"k": [1]})
        verify_entries(log.entries)

    # ---- tampering ------------------------------------------------------------------

    def test_edit_is_detected(self):
        self.write_three()
        entries = read_entries(self.path)
        entries[1]["n"] = 99
        self.rewrite(entries)
        with self.assertRaisesRegex(ChainError, "entry 1: content does not match"):
            verify_file(self.path)
        with self.assertRaises(ChainError):
            HashChainLog(self.path)  # refuses to open, so nothing can be appended

    def test_edit_with_recomputed_hash_breaks_next_link(self):
        self.write_three()
        entries = read_entries(self.path)
        entries[1]["n"] = 99
        entries[1]["hash"] = entry_hash({k: v for k, v in entries[1].items() if k != "hash"})
        self.rewrite(entries)
        with self.assertRaisesRegex(ChainError, "entry 2: prev_hash"):
            verify_file(self.path)

    def test_deleted_entry_is_detected(self):
        self.write_three()
        entries = read_entries(self.path)
        del entries[1]
        self.rewrite(entries)
        with self.assertRaisesRegex(ChainError, "entry 1: seq"):
            verify_file(self.path)

    def test_reordered_entries_are_detected(self):
        self.write_three()
        entries = read_entries(self.path)
        entries[1], entries[2] = entries[2], entries[1]
        self.rewrite(entries)
        with self.assertRaises(ChainError):
            verify_file(self.path)

    def test_bool_seq_is_not_zero(self):
        entries = [{"x": 1, "seq": False, "prev_hash": GENESIS}]
        entries[0]["hash"] = entry_hash(entries[0])
        with self.assertRaisesRegex(ChainError, "seq"):
            verify_entries(entries)

    def test_truncation_is_caught_by_expected_head(self):
        head = self.write_three().head
        self.path.write_bytes(("\n".join(self.lines()[:2]) + "\n").encode("utf-8"))
        self.assertEqual(verify_file(self.path)[0], 2)  # a shorter chain is still internally valid
        with self.assertRaisesRegex(ChainError, "expected head"):
            verify_file(self.path, expected_head=head)

    def test_expected_head_format(self):
        head = self.write_three().head
        self.assertEqual(verify_file(self.path, expected_head=head)[1], head)
        for bad in (head.upper(), head[:-1], "x" * 64, 123):
            with self.assertRaisesRegex(ChainError, "64 lowercase hex"):
                verify_file(self.path, expected_head=bad)

    def test_duplicate_key_forgery_is_refused(self):
        # a line with two "n" keys could show different values in different readers
        self.write_three()
        lines = self.lines()
        lines[0] = lines[0].replace("{", '{"n":99,', 1)
        self.path.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
        with self.assertRaisesRegex(ChainError, "line 1: invalid JSON .*duplicate key 'n'"):
            verify_file(self.path)

    def test_non_canonical_line_is_refused(self):
        self.write_three()
        lines = self.lines()
        lines[1] = lines[1].replace(",", ", ")
        self.path.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
        with self.assertRaisesRegex(ChainError, "line 2: not in canonical form"):
            verify_file(self.path)

    def test_blank_line_is_refused(self):
        self.write_three()
        lines = self.lines()
        self.path.write_bytes((lines[0] + "\n\n" + "\n".join(lines[1:]) + "\n").encode("utf-8"))
        with self.assertRaisesRegex(ChainError, "line 2: invalid JSON"):
            verify_file(self.path)

    def test_cut_off_last_line_is_refused(self):
        self.write_three()
        text = self.path.read_text(encoding="utf-8")
        self.path.write_bytes(text[:-1].encode("utf-8"))
        with self.assertRaisesRegex(ChainError, "incomplete"):
            verify_file(self.path)

    def test_crlf_is_refused_with_a_clear_message(self):
        self.write_three()
        self.path.write_bytes(self.path.read_bytes().replace(b"\n", b"\r\n"))
        with self.assertRaisesRegex(ChainError, "Windows line endings"):
            verify_file(self.path)

    def test_nan_in_file_is_refused(self):
        self.path.write_bytes(b'{"x":NaN}\n')
        with self.assertRaisesRegex(ChainError, "NaN is not allowed"):
            verify_file(self.path)

    def test_number_too_large_and_deep_nesting_are_refused(self):
        # 1e999 reads as infinity and very deep nesting overflows the parser; both must be a clean ChainError
        self.path.write_bytes(b'{"x":1e999}\n')
        with self.assertRaisesRegex(ChainError, "line 1: has a number too large to store"):
            verify_file(self.path)
        self.path.write_bytes(b'{"x":' + b"[" * 100000 + b"]" * 100000 + b"}\n")
        with self.assertRaisesRegex(ChainError, "line 1: invalid JSON"):
            verify_file(self.path)

    def test_invalid_json_line(self):
        self.write_three()
        self.path.write_bytes((self.lines()[0] + "\n{broken\n").encode("utf-8"))
        with self.assertRaisesRegex(ChainError, "line 2: invalid JSON"):
            verify_file(self.path)

    def test_non_object_entry(self):
        self.path.write_bytes(b"[1,2]\n")
        with self.assertRaisesRegex(ChainError, "not a JSON object"):
            verify_file(self.path)

    # ---- files that are not logs ----------------------------------------------------

    def test_missing_file(self):
        with self.assertRaisesRegex(ChainError, "file not found"):
            verify_file(self.path)
        self.assertEqual(read_entries(self.path), [])  # missing_ok for opening a new log

    def test_directory(self):
        with self.assertRaisesRegex(ChainError, "not a regular file"):
            verify_file(self.tmp.name)
        with self.assertRaises(ChainError):
            HashChainLog(self.tmp.name)

    def test_binary_file(self):
        self.path.write_bytes(b"\xca\xfe\xba\xbe\n")
        with self.assertRaisesRegex(ChainError, "not UTF-8"):
            verify_file(self.path)

    @unittest.skipIf(os.name == "nt" or os.geteuid() == 0, "permissions differ on Windows and for root")
    def test_unreadable_file(self):
        self.write_three()
        self.path.chmod(0)
        try:
            with self.assertRaisesRegex(ChainError, "cannot read"):
                verify_file(self.path)
        finally:
            self.path.chmod(0o600)

    # ---- append guards --------------------------------------------------------------

    def test_reserved_fields_refused(self):
        log = HashChainLog(self.path)
        for key in ("seq", "prev_hash", "hash"):
            with self.assertRaises(ValueError):
                log.append({key: "x"})
        self.assertFalse(self.path.exists())

    def test_bad_records_write_nothing(self):
        log = HashChainLog(self.path)
        for bad, err in (({"x": object()}, TypeError), ({"x": float("nan")}, ValueError),
                         ({"x": float("inf")}, ValueError), ({1: "x"}, TypeError), (["x"], TypeError)):
            with self.assertRaises(err, msg=repr(bad)):
                log.append(bad)
        self.assertEqual(len(log), 0)
        self.assertFalse(self.path.exists())

    def test_file_changed_on_disk_blocks_append(self):
        log = self.write_three()
        other = HashChainLog(self.path)
        other.append({"n": "from another process"})
        with self.assertRaisesRegex(ChainError, "changed on disk"):
            log.append({"n": 4})
        self.assertEqual(verify_file(self.path)[0], 4)  # the file was not forked

    def test_file_deleted_on_disk_blocks_append(self):
        log = self.write_three()
        self.path.unlink()
        with self.assertRaisesRegex(ChainError, "changed on disk"):
            log.append({"n": 4})
        self.assertFalse(self.path.exists())


if __name__ == "__main__":
    unittest.main()
