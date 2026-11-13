// Runs the browser code (docs/core.js) under Node for tests/test_web.py.
// Reads one JSON request on stdin and prints one JSON answer on stdout.
"use strict";
const fs = require("fs");
const path = require("path");
const APL = require(path.join(__dirname, "..", "docs", "core.js"));

const req = JSON.parse(fs.readFileSync(0, "utf8"));

function verify(file, head) {
  const r = APL.verifyLog(fs.readFileSync(file), file, head === undefined ? null : head);
  return { ok: r.ok, error: r.error, count: r.ok ? r.entries.length : null, head: r.ok ? r.head : null };
}

function attempt(fn) {
  try {
    return { ok: fn() };
  } catch (e) {
    if (!(e instanceof APL.PyError)) throw e;
    return { error: [e.pyType, e.message] };
  }
}

function session({ rules, ops }) {
  let now = new Date("2026-11-13T09:00:00Z");
  const makePolicy = (specs) => new APL.Policy(specs.map(APL.rule));
  const ledger = new APL.Ledger(makePolicy(rules), () => now);
  const out = ops.map((op) => attempt(() => {
    switch (op.op) {
      case "clock": now = new Date(op.t); return null;
      case "policy": ledger.policy = makePolicy(op.rules); return null;
      case "request": return ledger.request(APL.paymentRequest(op)).get("hash");
      case "approve": return ledger.approve(op.seq, op.approver, op.note).get("hash");
      case "reject": return ledger.reject(op.seq, op.approver, op.note).get("hash");
      case "status": return ledger.status(op.seq);
      case "pending": return ledger.pending().map((e) => Number(e.get("seq")));
      case "spent": return ledger.spentOn(op.agent, op.asset, op.day).toString();
      default: throw new Error("unknown op " + op.op);
    }
  }));
  return { results: out, log: ledger.text(), head: ledger.head };
}

const answers = {
  verify: () => req.files.map((f) => verify(f, req.head)),
  sessions: () => req.sessions.map(session),
  space: () => [...APL.PY_SPACE].sort((a, b) => a - b),
  floats: () => req.values.map((t) => APL.canonical(APL.parseJSON(t))),
  amounts: () => req.values.map((v) => {
    const r = attempt(() => APL.Dec.parse(v).toString());
    return { parsed: r, plain: APL.isPlainAmount(v) };
  }),
  sha: () => req.values.map(APL.sha256Hex),
  repr: () => req.values.map((v) => APL.repr(APL.parseJSON(v))),
  sort: () => [...req.values].sort(APL.cmpStr),
};

process.stdout.write(JSON.stringify(answers[req.mode]()));
