/*
 * agent-payment-log in the browser.
 *
 * The same checks and the same ledger as the Python package, written so that a
 * log made by one is read exactly the same way by the other. Python behaviour
 * is copied on purpose, down to error messages, and tests/test_web.py runs both
 * on the same files to prove it.
 *
 * JSON values are kept the way Python's json module sees them:
 *   object -> Map (key order kept)   integer -> BigInt   other number -> Number
 *   string -> string   true/false -> boolean   null -> null   array -> Array
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.APL = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const GENESIS = "0".repeat(64);
  const HASH_RE = /^[0-9a-f]{64}$/;
  const ALLOW = "allow", HOLD = "hold", DENY = "deny";
  const MAX_DECIMALS = 18;

  // ---- errors, named after their Python counterparts -------------------------------

  class PyError extends Error {
    constructor(type, message, index) {
      super(message);
      this.pyType = type;      // ChainError, LedgerError, ValueError, TypeError
      this.index = index;      // entry (or line - 1) the problem is in, when known
    }
  }
  const chainError = (m, i) => new PyError("ChainError", m, i);
  const ledgerError = (m, i) => new PyError("LedgerError", m, i);
  const valueError = (m) => new PyError("ValueError", m);
  const typeError = (m) => new PyError("TypeError", m);

  // ---- SHA-256 (synchronous, so it also works from file:// and in Node) ---------------

  const K = new Uint32Array([
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
  ]);

  function sha256Hex(text) {
    const msg = new TextEncoder().encode(text);
    const len = msg.length;
    const total = Math.ceil((len + 9) / 64) * 64;
    const buf = new Uint8Array(total);
    buf.set(msg);
    buf[len] = 0x80;
    const dv = new DataView(buf.buffer);
    const bits = len * 8;
    dv.setUint32(total - 8, Math.floor(bits / 0x100000000));
    dv.setUint32(total - 4, bits >>> 0);
    const H = new Uint32Array([0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
                               0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19]);
    const w = new Uint32Array(64);
    const rotr = (x, n) => (x >>> n) | (x << (32 - n));
    for (let off = 0; off < total; off += 64) {
      for (let i = 0; i < 16; i++) w[i] = dv.getUint32(off + i * 4);
      for (let i = 16; i < 64; i++) {
        const a = w[i - 15], b = w[i - 2];
        const s0 = rotr(a, 7) ^ rotr(a, 18) ^ (a >>> 3);
        const s1 = rotr(b, 17) ^ rotr(b, 19) ^ (b >>> 10);
        w[i] = (w[i - 16] + s0 + w[i - 7] + s1) | 0;
      }
      let a = H[0], b = H[1], c = H[2], d = H[3], e = H[4], f = H[5], g = H[6], h = H[7];
      for (let i = 0; i < 64; i++) {
        const S1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
        const ch = (e & f) ^ (~e & g);
        const t1 = (h + S1 + ch + K[i] + w[i]) | 0;
        const S0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
        const maj = (a & b) ^ (a & c) ^ (b & c);
        const t2 = (S0 + maj) | 0;
        h = g; g = f; f = e; e = (d + t1) | 0; d = c; c = b; b = a; a = (t1 + t2) | 0;
      }
      H[0] += a; H[1] += b; H[2] += c; H[3] += d; H[4] += e; H[5] += f; H[6] += g; H[7] += h;
    }
    let out = "";
    for (let i = 0; i < 8; i++) out += H[i].toString(16).padStart(8, "0");
    return out;
  }

  // ---- Python string helpers -------------------------------------------------------

  // str.isspace() in Python; str.strip() removes exactly these
  const PY_SPACE = new Set([0x09, 0x0a, 0x0b, 0x0c, 0x0d, 0x1c, 0x1d, 0x1e, 0x1f, 0x20, 0x85, 0xa0,
    0x1680, 0x2000, 0x2001, 0x2002, 0x2003, 0x2004, 0x2005, 0x2006, 0x2007, 0x2008, 0x2009, 0x200a,
    0x2028, 0x2029, 0x202f, 0x205f, 0x3000]);

  function pyStrip(s) {
    let a = 0, b = s.length;
    while (a < b && PY_SPACE.has(s.charCodeAt(a))) a++;
    while (b > a && PY_SPACE.has(s.charCodeAt(b - 1))) b--;
    return s.slice(a, b);
  }

  // a non-empty string without outer whitespace (ledger._name / policy._text)
  const isName = (v) => typeof v === "string" && pyStrip(v) !== "" && v === pyStrip(v);

  const hasLoneSurrogate = (s) => /\p{Cs}/u.test(s);

  // sort order of Python strings: by code point, not by UTF-16 unit
  function cmpStr(a, b) {
    const ia = a[Symbol.iterator](), ib = b[Symbol.iterator]();
    for (;;) {
      const x = ia.next(), y = ib.next();
      if (x.done || y.done) return x.done === y.done ? 0 : (x.done ? -1 : 1);
      const cx = x.value.codePointAt(0), cy = y.value.codePointAt(0);
      if (cx !== cy) return cx < cy ? -1 : 1;
    }
  }

  const NOT_PRINTABLE = /[\p{Cc}\p{Cf}\p{Cs}\p{Co}\p{Cn}\p{Zl}\p{Zp}\p{Zs}]/u;
  const hex = (n, w) => n.toString(16).padStart(w, "0");

  function reprStr(s) {
    const q = s.includes("'") && !s.includes('"') ? '"' : "'";
    let out = q;
    for (const ch of s) {
      const cp = ch.codePointAt(0);
      if (ch === q || ch === "\\") out += "\\" + ch;
      else if (ch === "\t") out += "\\t";
      else if (ch === "\n") out += "\\n";
      else if (ch === "\r") out += "\\r";
      else if (cp < 0x20 || cp === 0x7f) out += "\\x" + hex(cp, 2);
      else if (cp < 0x7f) out += ch;
      else if (NOT_PRINTABLE.test(ch)) {
        out += cp <= 0xff ? "\\x" + hex(cp, 2) : cp <= 0xffff ? "\\u" + hex(cp, 4) : "\\U" + hex(cp, 8);
      } else out += ch;
    }
    return out + q;
  }

  // float.__repr__: shortest digits, fixed notation for 1e-4 <= |x| < 1e16
  function floatRepr(x) {
    if (Number.isNaN(x)) return "nan";
    if (!Number.isFinite(x)) return x > 0 ? "inf" : "-inf";
    if (x === 0) return Object.is(x, -0) ? "-0.0" : "0.0";
    const sign = x < 0 ? "-" : "";
    const [mant, e] = String(Math.abs(x)).split("e");
    const [ip, fp = ""] = mant.split(".");
    let digits = ip + fp;
    let point = ip.length + (e ? parseInt(e, 10) : 0);
    const lead = digits.length - digits.replace(/^0+/, "").length;
    digits = digits.slice(lead).replace(/0+$/, "");
    point -= lead;
    const exp = point - 1;
    if (exp >= -4 && exp < 16) {
      if (exp < 0) return sign + "0." + "0".repeat(-exp - 1) + digits;
      if (digits.length <= exp + 1) return sign + digits + "0".repeat(exp + 1 - digits.length) + ".0";
      return sign + digits.slice(0, exp + 1) + "." + digits.slice(exp + 1);
    }
    const m = digits.length > 1 ? digits[0] + "." + digits.slice(1) : digits;
    return sign + m + "e" + (exp < 0 ? "-" : "+") + String(Math.abs(exp)).padStart(2, "0");
  }

  // repr() of a JSON value, for error messages
  function repr(v) {
    if (v === null || v === undefined) return "None";
    if (v === true) return "True";
    if (v === false) return "False";
    if (typeof v === "bigint") return v.toString();
    if (typeof v === "number") return floatRepr(v);
    if (typeof v === "string") return reprStr(v);
    if (Array.isArray(v)) return "[" + v.map(repr).join(", ") + "]";
    if (v instanceof Map) return "{" + [...v].map(([k, x]) => reprStr(k) + ": " + repr(x)).join(", ") + "}";
    return String(v);
  }

  // ---- JSON: strict parser and canonical writer ----------------------------------------

  class JsonError extends Error {}
  const MAX_DEPTH = 500;
  const NUMBER = /-?(?:0|[1-9]\d*)(\.\d+)?([eE][-+]?\d+)?/y;

  function parseJSON(s) {
    if (s.charCodeAt(0) === 0xfeff) throw new JsonError("Unexpected UTF-8 BOM (decode using utf-8-sig)");
    const p = { s, i: 0 };
    ws(p);
    const v = value(p, 0);
    ws(p);
    if (p.i !== s.length) throw new JsonError("Extra data");
    return v;
  }

  function ws(p) {
    const s = p.s;
    while (p.i < s.length) {
      const c = s.charCodeAt(p.i);
      if (c === 0x20 || c === 0x09 || c === 0x0a || c === 0x0d) p.i++;
      else break;
    }
  }

  function value(p, depth) {
    const s = p.s, c = s[p.i];
    if (c === '"') return string(p);
    if (c === "{") return object(p, depth + 1);
    if (c === "[") return array(p, depth + 1);
    if (c === "n" && s.startsWith("null", p.i)) { p.i += 4; return null; }
    if (c === "t" && s.startsWith("true", p.i)) { p.i += 4; return true; }
    if (c === "f" && s.startsWith("false", p.i)) { p.i += 5; return false; }
    if (c === "N" && s.startsWith("NaN", p.i)) throw new JsonError("NaN is not allowed");
    if (c === "I" && s.startsWith("Infinity", p.i)) throw new JsonError("Infinity is not allowed");
    if (c === "-" && s.startsWith("-Infinity", p.i)) throw new JsonError("-Infinity is not allowed");
    NUMBER.lastIndex = p.i;
    const m = NUMBER.exec(s);
    if (!m) throw new JsonError("Expecting value");
    p.i += m[0].length;
    return m[1] || m[2] ? Number(m[0]) : BigInt(m[0]);
  }

  function string(p) {
    const s = p.s;
    let out = "";
    p.i++;
    for (;;) {
      if (p.i >= s.length) throw new JsonError("Unterminated string starting at");
      const c = s.charCodeAt(p.i);
      if (c === 0x22) { p.i++; return out; }
      if (c < 0x20) throw new JsonError("Invalid control character at");
      if (c !== 0x5c) { out += s[p.i++]; continue; }
      const e = s[p.i + 1];
      if (e === undefined) throw new JsonError("Unterminated string starting at");
      if (e === "u") {
        const h = s.slice(p.i + 2, p.i + 6);
        if (!/^[0-9a-fA-F]{4}$/.test(h)) throw new JsonError("Invalid \\uXXXX escape");
        out += String.fromCharCode(parseInt(h, 16));
        p.i += 6;
        continue;
      }
      const simple = { '"': '"', "\\": "\\", "/": "/", b: "\b", f: "\f", n: "\n", r: "\r", t: "\t" }[e];
      if (simple === undefined) throw new JsonError("Invalid \\escape");
      out += simple;
      p.i += 2;
    }
  }

  function object(p, depth) {
    if (depth > MAX_DEPTH) throw new JsonError("too deeply nested");
    const s = p.s, pairs = [];
    p.i++;
    ws(p);
    if (s[p.i] === "}") { p.i++; return new Map(); }
    for (;;) {
      if (s[p.i] !== '"') throw new JsonError("Expecting property name enclosed in double quotes");
      const k = string(p);
      ws(p);
      if (s[p.i] !== ":") throw new JsonError("Expecting ':' delimiter");
      p.i++;
      ws(p);
      pairs.push([k, value(p, depth)]);
      ws(p);
      if (s[p.i] === "}") { p.i++; break; }
      if (s[p.i] !== ",") throw new JsonError("Expecting ',' delimiter");
      p.i++;
      ws(p);
      if (s[p.i] === "}") throw new JsonError("Illegal trailing comma before end of object");
    }
    const obj = new Map();
    for (const [k, v] of pairs) {
      if (obj.has(k)) throw new JsonError(`duplicate key ${reprStr(k)}`);
      obj.set(k, v);
    }
    return obj;
  }

  function array(p, depth) {
    if (depth > MAX_DEPTH) throw new JsonError("too deeply nested");
    const s = p.s, out = [];
    p.i++;
    ws(p);
    if (s[p.i] === "]") { p.i++; return out; }
    for (;;) {
      out.push(value(p, depth));
      ws(p);
      if (s[p.i] === "]") { p.i++; return out; }
      if (s[p.i] !== ",") throw new JsonError("Expecting ',' delimiter");
      p.i++;
      ws(p);
      if (s[p.i] === "]") throw new JsonError("Illegal trailing comma before end of array");
    }
  }

  function quote(s) {
    let out = '"';
    for (let i = 0; i < s.length; i++) {
      const c = s.charCodeAt(i);
      if (c === 0x22) out += '\\"';
      else if (c === 0x5c) out += "\\\\";
      else if (c === 0x0a) out += "\\n";
      else if (c === 0x0d) out += "\\r";
      else if (c === 0x09) out += "\\t";
      else if (c === 0x08) out += "\\b";
      else if (c === 0x0c) out += "\\f";
      else if (c < 0x20) out += "\\u" + hex(c, 4);
      else out += s[i];
    }
    return out + '"';
  }

  // json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
  function canonical(v) {
    if (v === null) return "null";
    if (v === true) return "true";
    if (v === false) return "false";
    if (typeof v === "string") return quote(v);
    if (typeof v === "bigint") return v.toString();
    if (typeof v === "number") {
      if (!Number.isFinite(v)) throw new RangeError("Out of range float values are not JSON compliant");
      return floatRepr(v);
    }
    if (Array.isArray(v)) return "[" + v.map(canonical).join(",") + "]";
    if (v instanceof Map) {
      const keys = [...v.keys()].sort(cmpStr);
      return "{" + keys.map((k) => quote(k) + ":" + canonical(v.get(k))).join(",") + "}";
    }
    throw typeError(`cannot write ${typeof v} as JSON`);
  }

  const entryHash = (body) => sha256Hex(canonical(body));

  // ---- reading and verifying a log ------------------------------------------------------

  function parseLine(line, n) {
    let obj;
    try {
      obj = parseJSON(line);
    } catch (e) {
      if (e instanceof JsonError) throw chainError(`line ${n}: invalid JSON (${e.message})`, n - 1);
      throw e;
    }
    if (!(obj instanceof Map)) throw chainError(`line ${n}: not a JSON object`, n - 1);
    let text;
    try {
      text = canonical(obj);
    } catch (e) {
      throw chainError(`line ${n}: has a number too large to store`, n - 1);
    }
    if (line !== text) throw chainError(`line ${n}: not in canonical form (sorted keys, no spaces)`, n - 1);
    return obj;
  }

  // read_entries for bytes; `name` stands in for the file path in messages.
  // `onEntry`, if given, receives every line parsed before an error.
  function readEntries(bytes, name, parsed) {
    let text;
    try {
      text = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(bytes);
    } catch (e) {
      throw chainError(`${name}: not UTF-8 text`);
    }
    if (text.includes("\r")) throw chainError(`${name}: has Windows line endings (CRLF), the log must use LF only`);
    if (text && !text.endsWith("\n")) {
      throw chainError(`${name}: last line is incomplete (no trailing newline), the file may be cut off`);
    }
    const entries = parsed || [];
    const lines = text.split("\n").slice(0, -1);
    lines.forEach((line, i) => entries.push(parseLine(line, i + 1)));
    return entries;
  }

  function sameNumber(v, i) {
    return (typeof v === "bigint" && v === BigInt(i)) || (typeof v === "number" && v === i);
  }

  function checkHead(expected) {
    if (expected !== null && expected !== undefined && !(typeof expected === "string" && HASH_RE.test(expected))) {
      throw chainError("expected head must be 64 lowercase hex characters");
    }
  }

  function verifyEntries(entries, expectedHead) {
    checkHead(expectedHead);
    let prev = GENESIS;
    entries.forEach((entry, i) => {
      if (!(entry instanceof Map)) throw chainError(`entry ${i}: not a JSON object`, i);
      const seq = entry.has("seq") ? entry.get("seq") : null;
      if (typeof seq === "boolean" || !sameNumber(seq, i)) {
        throw chainError(`entry ${i}: seq is ${repr(seq)}, expected ${i}`, i);
      }
      if (entry.get("prev_hash") !== prev) throw chainError(`entry ${i}: prev_hash does not match the previous entry`, i);
      const body = new Map([...entry].filter(([k]) => k !== "hash"));
      let expected;
      try {
        expected = entryHash(body);
      } catch (e) {
        throw chainError(`entry ${i}: contains NaN or Infinity`, i);
      }
      if (entry.get("hash") !== expected) throw chainError(`entry ${i}: content does not match its hash`, i);
      prev = expected;
    });
    if (expectedHead !== null && expectedHead !== undefined && prev !== expectedHead) {
      throw chainError("log does not end at the expected head hash");
    }
    return prev;
  }

  // ---- ledger rules (ledger.check_ledger) ---------------------------------------------

  const CHAIN_FIELDS = new Set(["seq", "prev_hash", "hash"]);
  const FIELDS = {
    decision: ["type", "time", "agent", "recipient", "amount", "asset", "reversible", "memo",
               "outcome", "rule_id", "reason"],
    approval: ["type", "time", "decision_seq", "approved_by", "note"],
    rejection: ["type", "time", "decision_seq", "rejected_by", "note"],
  };
  const TIME_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/;
  const DAY_RE = /^\d{4}-\d{2}-\d{2}$/;

  // datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ"): a real calendar date and time, year 1 or later
  function validTime(s) {
    if (typeof s !== "string" || !TIME_RE.test(s)) return false;
    const [y, mo, d, h, mi, se] = [s.slice(0, 4), s.slice(5, 7), s.slice(8, 10), s.slice(11, 13),
                                   s.slice(14, 16), s.slice(17, 19)].map(Number);
    if (y < 1 || mo < 1 || mo > 12 || d < 1 || h > 23 || mi > 59 || se > 59) return false;
    const leap = (y % 4 === 0 && y % 100 !== 0) || y % 400 === 0;
    const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][mo - 1];
    return d <= days;
  }

  function hasKey(m, k) { return m instanceof Map && m.has(k); }

  function checkLedger(entries) {
    const resolved = new Set();
    let lastTime = "";
    entries.forEach((e, i) => {
      const seq = e instanceof Map ? e.get("seq") : undefined;
      if (!(e instanceof Map) || !sameNumber(seq, i) || typeof seq === "boolean") {
        throw ledgerError(`entry ${i}: not a numbered log entry, run verify_entries first`, i);
      }
      const where = `entry ${i}`;
      const kind = e.get("type");
      if (typeof kind !== "string" || !Object.prototype.hasOwnProperty.call(FIELDS, kind)) {
        throw ledgerError(`${where}: unknown type ${repr(e.has("type") ? kind : null)}`, i);
      }
      const want = new Set(FIELDS[kind]);
      const have = new Set([...e.keys()].filter((k) => !CHAIN_FIELDS.has(k)));
      const missing = [...want].filter((k) => !have.has(k)).sort(cmpStr);
      const extra = [...have].filter((k) => !want.has(k)).sort(cmpStr);
      if (missing.length || extra.length) {
        const detail = [missing.length ? `missing ${missing.join(", ")}` : "",
                        extra.length ? `unexpected ${extra.join(", ")}` : ""].filter(Boolean).join("; ");
        throw ledgerError(`${where}: ${kind} entry has wrong fields (${detail})`, i);
      }
      const time = e.get("time");
      if (!validTime(time)) throw ledgerError(`${where}: time ${repr(time)} is not YYYY-MM-DDTHH:MM:SSZ`, i);
      if (time < lastTime) throw ledgerError(`${where}: time goes backwards`, i);
      lastTime = time;

      if (kind === "decision") {
        for (const f of ["agent", "recipient", "asset"]) {
          if (!isName(e.get(f))) throw ledgerError(`${where}: ${f} must be a non-empty string without outer spaces`, i);
        }
        if (!isPlainAmount(e.get("amount"))) {
          throw ledgerError(`${where}: amount ${repr(e.get("amount"))} is not a positive plain decimal string`, i);
        }
        if (typeof e.get("reversible") !== "boolean") throw ledgerError(`${where}: reversible must be true or false`, i);
        const outcome = e.get("outcome");
        if (outcome !== ALLOW && outcome !== HOLD && outcome !== DENY) {
          throw ledgerError(`${where}: unknown outcome ${repr(outcome)}`, i);
        }
        const rid = e.get("rule_id");
        if (rid === null) {
          if (outcome !== DENY) throw ledgerError(`${where}: only a deny can have no rule`, i);
        } else if (!isName(rid)) {
          throw ledgerError(`${where}: rule_id must be a non-empty string or null`, i);
        }
        for (const f of ["memo", "reason"]) {
          if (typeof e.get(f) !== "string") throw ledgerError(`${where}: ${f} must be a string`, i);
        }
      } else {
        const target = e.get("decision_seq");
        const person = e.get(kind === "approval" ? "approved_by" : "rejected_by");
        if (typeof target !== "bigint" || target < 0n || target >= BigInt(i)) {
          throw ledgerError(`${where}: decision_seq ${repr(target)} does not point to an earlier entry`, i);
        }
        const t = Number(target);
        const decision = entries[t];
        if (decision.get("type") !== "decision" || decision.get("outcome") !== HOLD) {
          throw ledgerError(`${where}: entry ${t} is not a held payment`, i);
        }
        if (resolved.has(t)) throw ledgerError(`${where}: entry ${t} was already approved or rejected`, i);
        if (!isName(person)) throw ledgerError(`${where}: approver must be a non-empty string without outer spaces`, i);
        if (person === decision.get("agent")) {
          throw ledgerError(`${where}: agent ${repr(person)} signed off its own payment`, i);
        }
        if (typeof e.get("note") !== "string") throw ledgerError(`${where}: note must be a string`, i);
        resolved.add(t);
      }
    });
  }

  // ---- exact decimal amounts (policy.to_amount / fmt) --------------------------------------

  // value = units * 10^-scale, like a Decimal with exponent -scale
  class Dec {
    constructor(units, scale) { this.units = units; this.scale = scale; }
    static zero() { return new Dec(0n, 0); }
    static parse(value, field = "amount") {
      if (typeof value === "bigint") return new Dec(value, 0);
      if (typeof value !== "string") {
        const t = typeof value === "number" ? (Number.isInteger(value) ? "int" : "float")
          : typeof value === "boolean" ? "bool" : value === null ? "NoneType" : typeof value;
        if (t === "int") return new Dec(BigInt(value), 0);
        throw typeError(`${field} must be a str, int or Decimal, not ${t}`);
      }
      const s = pyStrip(value);
      const m = /^([+-]?)(?:(\d+)(?:\.(\d*))?|\.(\d+))(?:[eE]([+-]?\d+))?$/.exec(s);
      if (!m) {
        if (/^[+-]?(inf|infinity|nan|snan)$/i.test(s)) throw valueError(`${field} must be a finite number`);
        throw valueError(`${field} is not a number: ${reprStr(value)}`);
      }
      const ip = m[2] !== undefined ? m[2] : "";
      const fp = m[2] !== undefined ? (m[3] || "") : m[4];
      let units = BigInt((ip + fp) || "0");
      if (m[1] === "-") units = -units;
      const scale = fp.length - (m[5] ? parseInt(m[5], 10) : 0);
      const d = new Dec(units, scale);
      if (scale > MAX_DECIMALS) throw valueError(`${field} has more than ${MAX_DECIMALS} decimal places`);
      if (d.abs().cmp(new Dec(10n ** 30n, 0)) >= 0) throw valueError(`${field} is too large`);
      return d;
    }
    abs() { return new Dec(this.units < 0n ? -this.units : this.units, this.scale); }
    _at(scale) { return scale >= this.scale ? this.units * 10n ** BigInt(scale - this.scale) : null; }
    add(o) {
      const s = Math.max(this.scale, o.scale);
      return new Dec(this._at(s) + o._at(s), s);
    }
    cmp(o) {
      const s = Math.max(this.scale, o.scale);
      const a = this._at(s), b = o._at(s);
      return a < b ? -1 : a > b ? 1 : 0;
    }
    sign() { return this.units > 0n ? 1 : this.units < 0n ? -1 : 0; }
    toString() {   // format(d, "f")
      if (this.scale <= 0) return (this.units * 10n ** BigInt(-this.scale)).toString();
      const neg = this.units < 0n;
      const digits = (neg ? -this.units : this.units).toString().padStart(this.scale + 1, "0");
      return (neg ? "-" : "") + digits.slice(0, -this.scale) + "." + digits.slice(-this.scale);
    }
  }

  // what check_ledger accepts as an amount: positive, and already written in plain form
  function isPlainAmount(v) {
    if (typeof v !== "string") return false;
    let d;
    try { d = Dec.parse(v); } catch (e) { return false; }
    return d.sign() > 0 && d.toString() === v;
  }

  // ---- spending rules (policy.py) ------------------------------------------------------

  function text(value, field) {
    if (typeof value !== "string" || !pyStrip(value)) throw valueError(`${field} must be a non-empty string`);
    if (value !== pyStrip(value)) throw valueError(`${field} must not start or end with spaces: ${reprStr(value)}`);
    if (hasLoneSurrogate(value)) throw valueError(`${field} contains a broken character`);
    return value;
  }

  function paymentRequest({ agent, recipient, amount, asset, reversible, memo = "" }) {
    text(agent, "agent");
    text(recipient, "recipient");
    text(asset, "asset");
    const a = Dec.parse(amount);
    if (a.sign() <= 0) throw valueError("amount must be greater than zero");
    if (typeof reversible !== "boolean") throw typeError("reversible must be True or False");
    if (typeof memo !== "string") throw typeError("memo must be a string");
    if (hasLoneSurrogate(memo)) throw valueError("memo contains a broken character");
    return Object.freeze({ agent, recipient, amount: a, asset, reversible, memo });
  }

  function rule({ id, asset, max_amount, daily_limit, approval_above = null, recipients = null,
                  irreversible_needs_approval = true }) {
    text(id, "rule id");
    text(asset, "asset");
    const lim = {};
    for (const [name, v] of [["max_amount", max_amount], ["daily_limit", daily_limit]]) {
      lim[name] = Dec.parse(v, name);
      if (lim[name].sign() <= 0) throw valueError(`${name} must be greater than zero`);
    }
    if (lim.max_amount.cmp(lim.daily_limit) > 0) throw valueError("max_amount cannot be larger than daily_limit");
    let above = null;
    if (approval_above !== null && approval_above !== undefined) {
      above = Dec.parse(approval_above, "approval_above");
      if (above.sign() < 0) throw valueError("approval_above cannot be negative");
    }
    let names = null;
    if (recipients !== null && recipients !== undefined) {
      if (typeof recipients === "string") throw typeError("recipients must be a collection of strings, not one string");
      names = new Set([...recipients].map((r) => text(r, "recipient")));
      if (!names.size) throw valueError("recipients is empty, use None to allow any recipient");
    }
    if (typeof irreversible_needs_approval !== "boolean") {
      throw typeError("irreversible_needs_approval must be True or False");
    }
    return Object.freeze({ id, asset, max_amount: lim.max_amount, daily_limit: lim.daily_limit,
                           approval_above: above, recipients: names, irreversible_needs_approval });
  }

  const covers = (r, req) => req.asset === r.asset && (r.recipients === null || r.recipients.has(req.recipient));

  class Policy {
    constructor(rules) {
      this.rules = Object.freeze([...rules]);
      const ids = this.rules.map((r) => r.id);
      if (new Set(ids).size !== ids.length) throw valueError("rule ids must be unique");
    }
    rule(id) { return this.rules.find((r) => r.id === id) || null; }
    evaluate(req, spentToday) {
      const r = this.rules.find((x) => covers(x, req));
      if (!r) return { outcome: DENY, rule_id: null, reason: `no rule covers ${req.asset} to ${req.recipient}` };
      const total = spentToday.add(req.amount);
      if (req.amount.cmp(r.max_amount) > 0) {
        return { outcome: DENY, rule_id: r.id,
                 reason: `${req.amount} is above the single-payment limit of ${r.max_amount}` };
      }
      if (total.cmp(r.daily_limit) > 0) {
        return { outcome: DENY, rule_id: r.id,
                 reason: `would bring today's total to ${total}, limit is ${r.daily_limit}` };
      }
      if (r.approval_above !== null && req.amount.cmp(r.approval_above) > 0) {
        return { outcome: HOLD, rule_id: r.id, reason: `${req.amount} is above ${r.approval_above}, needs a human` };
      }
      if (!req.reversible && r.irreversible_needs_approval) {
        return { outcome: HOLD, rule_id: r.id, reason: "payment cannot be reversed, needs a human" };
      }
      return { outcome: ALLOW, rule_id: r.id, reason: "within limits" };
    }
  }

  // ---- the ledger (ledger.PaymentLedger) -----------------------------------------------

  function stamp(t) {
    if (!(t instanceof Date) || Number.isNaN(t.getTime())) throw valueError("clock must return a valid Date");
    return t.toISOString().slice(0, 19) + "Z";
  }

  class Ledger {
    // `text` is an existing log; it is verified as a chain and as a ledger first
    constructor(policy, clock = () => new Date(), text = "") {
      const entries = readEntries(new TextEncoder().encode(text), "log");
      this.head = verifyEntries(entries);
      checkLedger(entries);
      this.entries = entries;
      this.lines = entries.map(canonical);
      this.policy = policy;
      this.clock = clock;
    }

    text() { return this.lines.map((l) => l + "\n").join(""); }

    status(seq) {
      const d = this._decision(seq);
      if (d.get("outcome") === ALLOW) return "allowed";
      if (d.get("outcome") === DENY) return "denied";
      for (const e of this.entries.slice(seq + 1)) {
        if (e.get("decision_seq") === BigInt(seq)) return e.get("type") === "approval" ? "approved" : "rejected";
      }
      return "pending";
    }

    spentOn(agent, asset, day) {
      if (typeof day !== "string" || !DAY_RE.test(day)) throw valueError("day must look like YYYY-MM-DD");
      let total = Dec.zero();
      for (const e of this.entries) {
        if (e.get("time").slice(0, 10) !== day) continue;
        let src;
        if (e.get("type") === "decision" && e.get("outcome") === ALLOW) src = e;
        else if (e.get("type") === "approval") src = this.entries[Number(e.get("decision_seq"))];
        else continue;
        if (src.get("agent") === agent && src.get("asset") === asset) total = total.add(Dec.parse(src.get("amount")));
      }
      return total;
    }

    pending() {
      const done = new Set(this.entries.filter((e) => e.get("type") !== "decision").map((e) => Number(e.get("decision_seq"))));
      return this.entries.filter((e, i) => e.get("type") === "decision" && e.get("outcome") === HOLD && !done.has(i));
    }

    request(req) {
      const now = this._now();
      const spent = this.spentOn(req.agent, req.asset, now.slice(0, 10));
      const v = this.policy.evaluate(req, spent);
      return this._append(new Map([
        ["type", "decision"], ["time", now], ["agent", req.agent], ["recipient", req.recipient],
        ["amount", req.amount.toString()], ["asset", req.asset], ["reversible", req.reversible],
        ["memo", req.memo], ["outcome", v.outcome], ["rule_id", v.rule_id], ["reason", v.reason],
      ]));
    }

    approve(seq, approver, note = "") {
      const d = this._pending(seq, approver, note);
      const now = this._now();
      const r = this.policy.rule(d.get("rule_id"));
      if (!r) throw ledgerError(`rule ${repr(d.get("rule_id"))} is no longer in the policy`);
      const total = this.spentOn(d.get("agent"), d.get("asset"), now.slice(0, 10)).add(Dec.parse(d.get("amount")));
      if (total.cmp(r.daily_limit) > 0) {
        throw ledgerError(`approving entry ${seq} would bring today's total to ${total}, limit is ${r.daily_limit}`);
      }
      return this._append(new Map([["type", "approval"], ["time", now], ["decision_seq", BigInt(seq)],
                                   ["approved_by", approver], ["note", note]]));
    }

    reject(seq, approver, note = "") {
      this._pending(seq, approver, note);
      return this._append(new Map([["type", "rejection"], ["time", this._now()], ["decision_seq", BigInt(seq)],
                                   ["rejected_by", approver], ["note", note]]));
    }

    _now() {
      const now = stamp(this.clock());
      const last = this.entries[this.entries.length - 1];
      if (last && now < last.get("time")) {
        throw ledgerError(`clock reads ${now}, earlier than the last entry at ${last.get("time")}`);
      }
      return now;
    }

    _decision(seq) {
      if (typeof seq !== "number" || !Number.isInteger(seq) || seq < 0 || seq >= this.entries.length) {
        throw ledgerError(`no entry ${typeof seq === "number" ? String(seq) : repr(seq)}`);
      }
      if (this.entries[seq].get("type") !== "decision") throw ledgerError(`entry ${seq} is not a payment decision`);
      return this.entries[seq];
    }

    _pending(seq, approver, note) {
      if (!isName(approver) || hasLoneSurrogate(approver)) {
        throw ledgerError("approver must be a non-empty string without outer spaces");
      }
      if (typeof note !== "string" || hasLoneSurrogate(note)) throw ledgerError("note must be a string");
      const d = this._decision(seq);
      const state = this.status(seq);
      if (state !== "pending") {
        throw ledgerError(`entry ${seq} is ${state}, only pending payments can be approved or rejected`);
      }
      if (d.get("agent") === approver) throw ledgerError("an agent cannot approve its own payment");
      return d;
    }

    _append(record) {
      const body = new Map(record);
      body.set("seq", BigInt(this.entries.length));
      body.set("prev_hash", this.head);
      body.set("hash", entryHash(body));
      this.entries.push(body);
      this.lines.push(canonical(body));
      this.head = body.get("hash");
      return body;
    }
  }

  // ---- one call for the page: read, verify as a chain, verify as a ledger ----------------

  // Same steps and messages as `agent-payment-log verify`. Never throws for a bad file.
  function verifyLog(bytes, name, expectedHead) {
    const entries = [];
    const result = { ok: false, error: null, stage: null, index: null, entries, head: null };
    try {
      checkHead(expectedHead);
    } catch (e) {
      return Object.assign(result, { error: e.message, stage: "head" });
    }
    try {
      readEntries(bytes, name, entries);
    } catch (e) {
      if (!(e instanceof PyError)) throw e;
      const stage = e.index === undefined ? "read" : "parse";
      return Object.assign(result, { error: e.message, stage, index: e.index === undefined ? null : e.index });
    }
    try {
      result.head = verifyEntries(entries, expectedHead);
    } catch (e) {
      if (!(e instanceof PyError)) throw e;
      return Object.assign(result, { error: e.message, stage: e.index === undefined ? "head" : "chain",
                                     index: e.index === undefined ? null : e.index });
    }
    try {
      checkLedger(entries);
    } catch (e) {
      if (!(e instanceof PyError)) throw e;
      return Object.assign(result, { error: e.message, stage: "ledger", index: e.index === undefined ? null : e.index });
    }
    result.ok = true;
    return result;
  }

  // re-sign every entry from scratch, as a forger who controls the whole file would
  function rehash(entries) {
    let prev = GENESIS;
    return entries.map((e, i) => {
      const body = new Map(e);
      body.delete("hash");
      body.set("seq", BigInt(i));
      body.set("prev_hash", prev);
      body.set("hash", entryHash(body));
      prev = body.get("hash");
      return body;
    });
  }

  return {
    GENESIS, ALLOW, HOLD, DENY, HASH_RE, PyError,
    sha256Hex, canonical, entryHash, parseJSON, parseLine, readEntries, verifyEntries, checkLedger, verifyLog,
    repr, reprStr, floatRepr, pyStrip, isName, PY_SPACE, cmpStr, validTime, isPlainAmount,
    Dec, paymentRequest, rule, Policy, Ledger, stamp, rehash,
  };
});
