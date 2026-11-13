/* The page: playground, log checker and guide. All checks come from core.js. */
(function () {
  "use strict";
  const A = window.APL;
  const $ = (sel, root) => (root || document).querySelector(sel);
  const MAX_FILE = 50 * 1024 * 1024;
  const PAGE = 200;

  // ---- small helpers ----------------------------------------------------------------

  function h(tag, attrs, ...kids) {
    const el = document.createElement(tag);
    if (attrs) {
      for (const [k, v] of Object.entries(attrs)) {
        if (v === null || v === undefined || v === false) continue;
        if (k === "class") el.className = v;
        else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
        else el.setAttribute(k, v === true ? "" : String(v));
      }
    }
    for (const c of kids.flat(Infinity)) {
      if (c === null || c === undefined || c === false) continue;
      el.append(c instanceof Node ? c : document.createTextNode(String(c)));
    }
    return el;
  }

  const SVG_NS = "http://www.w3.org/2000/svg";
  function icon(d) {
    const s = document.createElementNS(SVG_NS, "svg");
    s.setAttribute("viewBox", "0 0 24 24");
    s.setAttribute("aria-hidden", "true");
    for (const part of [].concat(d)) {
      const p = document.createElementNS(SVG_NS, "path");
      p.setAttribute("d", part);
      s.append(p);
    }
    return s;
  }
  const I = {
    check: "M5 12.5l4.5 4.5L19 7.5",
    cross: ["M6 6l12 12", "M18 6L6 18"],
    pause: ["M9 6v12", "M15 6v12"],
    warn: ["M12 8v5", "M12 16.5v.5"],
    up: "M6 15l6-6 6 6",
    down: "M6 9l6 6 6-6",
    del: ["M6 6l12 12", "M18 6L6 18"],
    edit: ["M4 20h4L19 9l-4-4L4 16v4z", "M13.5 6.5l4 4"],
  };

  const store = {
    get(k) { try { return window.localStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { window.localStorage.setItem(k, v); } catch (e) { /* private mode, full disk */ } },
  };

  let toastTimer = null;
  function toast(msg) {
    const t = $("#toast");
    t.textContent = msg;
    t.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => t.classList.remove("show"), 2200);
  }

  async function copy(text, what) {
    let done = false;
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(text);
        done = true;
      }
    } catch (e) { /* fall back below */ }
    if (!done) {
      const ta = h("textarea", { readonly: true, class: "offscreen" });
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.left = "-9999px";
      document.body.append(ta);
      ta.select();
      try { done = document.execCommand("copy"); } catch (e) { done = false; }
      ta.remove();
    }
    toast(done ? `${what || "Text"} copied` : "Could not copy. Select the text and copy it by hand.");
  }

  function download(text, name) {
    const url = URL.createObjectURL(new Blob([text], { type: "application/x-ndjson" }));
    const a = h("a", { href: url, download: name });
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  function showError(el, msg) {
    el.textContent = msg || "";
    el.hidden = !msg;
  }

  const get = (e, k) => (e instanceof Map ? e.get(k) : undefined);
  const disp = (v) => (typeof v === "string" ? v : A.repr(v === undefined ? null : v));
  const short = (hash) => (typeof hash === "string" && hash.length > 20 ? hash.slice(0, 12) + "…" + hash.slice(-6) : disp(hash));
  const plural = (n, one, many) => `${n} ${n === 1 ? one : many || one + "s"}`;
  const encoder = new TextEncoder();

  function hashChip(hash, label) {
    return [h("span", { class: "hash", title: hash }, hash),
            h("button", { class: "copy-btn", type: "button", onclick: () => copy(hash, label || "Head hash") }, "Copy")];
  }

  // ---- theme ----------------------------------------------------------------------

  const media = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;
  function isDark() {
    const t = document.documentElement.getAttribute("data-theme");
    return t ? t === "dark" : !!(media && media.matches);
  }
  function syncThemeIcon() {
    const dark = isDark();
    document.documentElement.setAttribute("data-dark", dark ? "1" : "0");
    $("#theme").setAttribute("aria-label", dark ? "Switch to light theme" : "Switch to dark theme");
  }
  function initTheme() {
    const saved = store.get("apl-theme");
    if (saved === "light" || saved === "dark") document.documentElement.setAttribute("data-theme", saved);
    syncThemeIcon();
    if (media && media.addEventListener) media.addEventListener("change", syncThemeIcon);
    $("#theme").addEventListener("click", () => {
      const next = isDark() ? "light" : "dark";
      document.documentElement.setAttribute("data-theme", next);
      store.set("apl-theme", next);
      syncThemeIcon();
    });
  }

  // ---- tabs -----------------------------------------------------------------------

  const TABS = ["playground", "verify", "guide"];
  function tabFromHash() {
    const t = location.hash.replace("#", "");
    return TABS.includes(t) ? t : "playground";
  }
  function showTab(tab, focus) {
    for (const t of TABS) {
      const on = t === tab;
      const b = $("#tab-" + t);
      b.setAttribute("aria-selected", on ? "true" : "false");
      b.tabIndex = on ? 0 : -1;
      $("#panel-" + t).hidden = !on;
      if (on && focus) b.focus();
    }
    if (location.hash !== "#" + tab) history.replaceState(null, "", "#" + tab);
  }
  function initTabs() {
    for (const t of TABS) {
      $("#tab-" + t).addEventListener("click", () => {
        showTab(t);
        window.scrollTo({ top: 0 });
      });
    }
    $(".tabs").addEventListener("keydown", (ev) => {
      const i = TABS.indexOf(tabFromHash());
      let j = null;
      if (ev.key === "ArrowRight") j = (i + 1) % TABS.length;
      else if (ev.key === "ArrowLeft") j = (i + TABS.length - 1) % TABS.length;
      else if (ev.key === "Home") j = 0;
      else if (ev.key === "End") j = TABS.length - 1;
      if (j !== null) { ev.preventDefault(); showTab(TABS[j], true); }
    });
    window.addEventListener("hashchange", () => {
      if (!TABS.includes(location.hash.replace("#", ""))) return;   // e.g. the skip link
      showTab(tabFromHash());
      window.scrollTo({ top: 0 });
    });
    showTab(tabFromHash());
  }

  // ---- drawing log entries --------------------------------------------------------------

  // mark: null, "broken" (the entry that fails), "after" (cannot be trusted), "rule" (breaks ledger rules)
  function entryView(e, i, opts) {
    const o = opts || {};
    const type = get(e, "type");
    let kind = "";
    const parts = [];
    const headBits = [h("span", { class: "seq" }, "#" + i), h("time", null, disp(get(e, "time")))];

    if (type === "decision") {
      const outcome = get(e, "outcome");
      kind = outcome === A.ALLOW ? "allow" : outcome === A.HOLD ? "hold" : outcome === A.DENY ? "deny" : "";
      parts.push(h("div", { class: "entry-main" },
        h("span", { class: "pill " + kind }, kind || disp(outcome)),
        h("span", { class: "amount" }, `${disp(get(e, "amount"))} ${disp(get(e, "asset"))}`),
        h("span", { class: "flow" }, h("b", null, disp(get(e, "agent"))), " → ", h("b", null, disp(get(e, "recipient")))),
        get(e, "reversible") === false ? h("span", { class: "tag" }, "irreversible") : null));
      const rid = get(e, "rule_id");
      parts.push(h("div", { class: "entry-sub" },
        rid === null ? "no rule matched" : h("span", { class: "rule-id" }, disp(rid)), " · ", disp(get(e, "reason"))));
      const memo = get(e, "memo");
      if (memo) parts.push(h("div", { class: "entry-sub" }, "memo: ", disp(memo)));
      if (o.state && kind === "hold") headBits.push(h("span", { class: "state " + o.state }, o.state));
    } else if (type === "approval" || type === "rejection") {
      const ok = type === "approval";
      kind = "sign";
      parts.push(h("div", { class: "entry-main" },
        h("span", { class: "pill " + (ok ? "allow" : "deny") }, ok ? "approved" : "rejected"),
        h("span", { class: "flow" }, "payment #", disp(get(e, "decision_seq")), " by ",
          h("b", null, disp(get(e, ok ? "approved_by" : "rejected_by"))))));
      const note = get(e, "note");
      if (note) parts.push(h("div", { class: "entry-sub" }, "note: ", disp(note)));
    } else {
      parts.push(h("div", { class: "entry-main" }, h("span", { class: "pill" }, type === undefined ? "entry" : disp(type))));
      parts.push(h("div", { class: "raw" }, clip(A.canonical(e), 400)));
    }

    if (o.note) parts.push(h("p", { class: "entry-note" }, o.note));
    parts.push(h("details", null, h("summary", null, "hashes"),
      h("dl", null, h("dt", null, "prev"), h("dd", null, disp(get(e, "prev_hash"))),
                    h("dt", null, "hash"), h("dd", null, disp(get(e, "hash"))))));

    const cls = ["entry", kind ? "k-" + kind : "", o.mark ? "m-" + o.mark : "", o.flash ? "flash" : ""].join(" ");
    return h("li", { class: cls.trim() }, h("div", { class: "entry-head" }, headBits), parts);
  }

  function clip(s, n) { return s.length > n ? s.slice(0, n) + "…" : s; }

  // How each entry looks after a check: which one broke and which come after it
  function marksFor(result) {
    const marks = new Map();
    if (result.ok || result.index === null) return marks;
    const i = result.index;
    if (result.stage === "chain") {
      marks.set(i, { mark: "broken", note: plainChainError(result.error) });
      for (let j = i + 1; j < result.entries.length; j++) {
        marks.set(j, { mark: "after", note: "Comes after a broken link, so it cannot be trusted either." });
      }
    } else if (result.stage === "ledger") {
      marks.set(i, { mark: "rule", note: result.error.replace(/^entry \d+: /, "") });
    }
    return marks;
  }

  function plainChainError(msg) {
    const m = msg.replace(/^entry \d+: /, "");
    if (m === "content does not match its hash") return "This entry was changed after it was written. Its hash no longer matches.";
    if (m === "prev_hash does not match the previous entry") return "This entry no longer links to the one before it. Something was removed, added or reordered.";
    if (m.startsWith("seq is None")) return "This line has no entry number, so it is not an agent-payment-log entry.";
    if (m.startsWith("seq is")) return `Entry numbers are out of order (${m}). An entry was removed, added or moved.`;
    return m;
  }

  // ---- PLAYGROUND -------------------------------------------------------------------

  const EXAMPLE_RULES = [
    { id: "data-vendors", asset: "USDC", max_amount: "200", daily_limit: "300", approval_above: "50",
      recipients: "api.marketdata.example, api.news.example", irreversible: true },
    { id: "small-anything", asset: "USDC", max_amount: "5", daily_limit: "20", approval_above: "",
      recipients: "", irreversible: true },
  ];
  const SCENARIOS = [
    { label: "Price feed, 12.40", recipient: "api.marketdata.example", amount: "12.40", reversible: true, memo: "price feed, 1 day", hint: "allow" },
    { label: "News archive, 75", recipient: "api.news.example", amount: "75", reversible: true, memo: "news archive, 1 month", hint: "hold" },
    { label: "Tip, 3", recipient: "tips.example", amount: "3", reversible: true, memo: "tip for a dataset", hint: "allow" },
    { label: "Unknown wallet, 40", recipient: "unknown-wallet.example", amount: "40", reversible: true, memo: "agent wanted to buy a report", hint: "deny" },
    { label: "Onchain transfer, 20", recipient: "api.marketdata.example", amount: "20", reversible: false, memo: "onchain transfer, cannot be undone", hint: "hold" },
  ];
  const KEY = "apl-playground-v1";

  const pg = { rules: [], log: "", tampered: null, sharedHead: null, approver: "ops-lead", lastSeq: null };
  let cheatTouched = false;

  function loadPlayground() {
    let saved = null;
    try { saved = JSON.parse(store.get(KEY) || "null"); } catch (e) { saved = null; }
    pg.rules = EXAMPLE_RULES.map((r) => Object.assign({}, r));
    if (saved && typeof saved === "object") {
      if (Array.isArray(saved.rules) && saved.rules.every(validRuleShape)) pg.rules = saved.rules;
      if (typeof saved.log === "string") {
        try { new A.Ledger(new A.Policy([]), () => new Date(), saved.log); pg.log = saved.log; } catch (e) { pg.log = ""; }
      }
      if (typeof saved.tampered === "string" && pg.log) pg.tampered = saved.tampered;
      if (typeof saved.sharedHead === "string" && A.HASH_RE.test(saved.sharedHead)) pg.sharedHead = saved.sharedHead;
      if (typeof saved.approver === "string") pg.approver = saved.approver;
    }
  }
  function validRuleShape(r) {
    return r && typeof r === "object" && ["id", "asset", "max_amount", "daily_limit", "approval_above", "recipients"]
      .every((k) => typeof r[k] === "string") && typeof r.irreversible === "boolean";
  }
  function savePlayground() {
    store.set(KEY, JSON.stringify({ rules: pg.rules, log: pg.log, tampered: pg.tampered,
                                    sharedHead: pg.sharedHead, approver: pg.approver }));
  }

  // rule specs from the form, turned into a Policy; errors per rule
  function buildPolicy() {
    const errors = pg.rules.map(() => null);
    const rules = pg.rules.map((r, i) => {
      try {
        return A.rule({
          id: r.id.trim(), asset: r.asset.trim(), max_amount: r.max_amount, daily_limit: r.daily_limit,
          approval_above: r.approval_above.trim() === "" ? null : r.approval_above,
          recipients: r.recipients.trim() === "" ? null : r.recipients.split(",").map((x) => x.trim()).filter(Boolean),
          irreversible_needs_approval: r.irreversible,
        });
      } catch (e) {
        errors[i] = friendly(e.message);
        return null;
      }
    });
    if (errors.some(Boolean)) return { policy: null, errors, general: null };
    try {
      return { policy: new A.Policy(rules), errors, general: null };
    } catch (e) {
      return { policy: null, errors, general: friendly(e.message) };
    }
  }

  function friendly(msg) {
    return msg
      .replace("rule id", "Name")
      .replace(/^max_amount/, "Max per payment").replace("than daily_limit", "than the daily limit")
      .replace(/^daily_limit/, "Daily limit").replace(/^approval_above/, "Approval threshold")
      .replace("rule ids must be unique", "Two rules have the same name. Each rule needs its own name.")
      .replace("recipients is empty, use None to allow any recipient", "Add at least one recipient, or leave it empty for anyone")
      .replace(/^./, (c) => c.toUpperCase());
  }

  function ledgerFor(policy) {
    return new A.Ledger(policy || new A.Policy([]), () => new Date(), pg.log);
  }

  // ---- rules editor ----

  const editing = new Set();   // rules open for editing; the rest show a one-line summary

  function ruleSummary(r) {
    const bits = [`${r.asset.trim() || "?"}`, `up to ${r.max_amount.trim() || "?"} per payment`,
                  `${r.daily_limit.trim() || "?"} a day per agent`];
    bits.push(r.approval_above.trim() ? `a human approves above ${r.approval_above.trim()}` : "no size limit for approval");
    if (r.irreversible) bits.push("irreversible needs a human");
    const to = r.recipients.split(",").map((x) => x.trim()).filter(Boolean);
    bits.push(to.length ? `only to ${to.join(", ")}` : "to anyone");
    return bits.join(" · ");
  }

  function renderRules() {
    const list = $("#rules");
    list.textContent = "";
    const built = buildPolicy();
    pg.rules.forEach((r, i) => {
      const open = editing.has(i) || !!built.errors[i];
      const input = (key, label, opts) => h("label", { class: "field" + (opts && opts.full ? " full" : "") }, label,
        opts && opts.opt ? h("span", { class: "opt" }, " " + opts.opt) : null,
        h("input", { value: r[key], autocomplete: "off", spellcheck: "false", inputmode: opts && opts.num ? "decimal" : null,
                     placeholder: opts && opts.ph, "data-key": key,
                     oninput: (ev) => { r[key] = ev.target.value; onRulesChanged(i); } }));
      const err = h("p", { class: "err", role: "alert", id: "rule-err-" + i }, built.errors[i] || "");
      err.hidden = !built.errors[i];
      const tools = h("div", { class: "rule-tools" },
        open ? null : h("button", { class: "mini", type: "button", "aria-label": `Edit rule ${i + 1}`, onclick: () => { editing.add(i); renderRules(); focusRule(i); } }, icon(I.edit)),
        h("button", { class: "mini", type: "button", "aria-label": `Move rule ${i + 1} up`, disabled: i === 0, onclick: () => moveRule(i, -1) }, icon(I.up)),
        h("button", { class: "mini", type: "button", "aria-label": `Move rule ${i + 1} down`, disabled: i === pg.rules.length - 1, onclick: () => moveRule(i, 1) }, icon(I.down)),
        h("button", { class: "mini", type: "button", "aria-label": `Remove rule ${i + 1}`, onclick: () => removeRule(i) }, icon(I.del)));
      if (!open) {
        list.append(h("li", { class: "rule compact", id: "rule-" + i },
          h("div", { class: "rule-top" }, h("span", { class: "num" }, String(i + 1)), h("b", { class: "rule-name" }, r.id.trim()), tools),
          h("p", { class: "rule-sum" }, ruleSummary(r))));
        return;
      }
      list.append(h("li", { class: "rule" + (built.errors[i] ? " invalid" : ""), id: "rule-" + i },
        h("div", { class: "rule-top" }, h("span", { class: "num" }, String(i + 1)), input("id", "Name"), tools),
        h("div", { class: "rule-grid" },
          input("asset", "Asset"),
          input("max_amount", "Max per payment", { num: true }),
          input("daily_limit", "Daily limit per agent", { num: true }),
          input("approval_above", "Human approval above", { num: true, ph: "never" }),
          input("recipients", "Allowed recipients", { full: true, opt: "comma separated, empty = anyone", ph: "anyone" })),
        h("label", { class: "check" },
          h("input", { type: "checkbox", checked: r.irreversible, onchange: (ev) => { r.irreversible = ev.target.checked; onRulesChanged(i); } }),
          h("span", null, "Irreversible payments need a human")),
        err,
        h("div", { class: "row" }, h("button", { class: "btn small", type: "button", onclick: () => {
          if (buildPolicy().errors[i]) return;   // the error under the fields says what to fix
          editing.delete(i);
          renderRules();
          const b = $(`#rule-${i} .mini`);
          if (b) b.focus();
        } }, "Done"))));
    });
    if (!pg.rules.length) list.append(h("li", { class: "hint" }, "No rules: every payment will be denied. Add one."));
    showError($("#rules-error"), built.general);
    updateDatalist();
    updateAskState(built);
  }

  function focusRule(i) {
    const inp = $(`#rule-${i} input`);
    if (inp) inp.focus();
  }

  // typing in a field: refresh errors without rebuilding inputs (keeps focus)
  function onRulesChanged() {
    const built = buildPolicy();
    pg.rules.forEach((_, i) => {
      const li = $("#rule-" + i);
      const err = $("#rule-err-" + i);
      if (!li || !err) return;
      li.classList.toggle("invalid", !!built.errors[i]);
      showError(err, built.errors[i]);
    });
    showError($("#rules-error"), built.general);
    updateDatalist();
    updateAskState(built);
    savePlayground();
  }

  function moveRule(i, d) {
    const j = i + d;
    [pg.rules[i], pg.rules[j]] = [pg.rules[j], pg.rules[i]];
    const wasI = editing.has(i), wasJ = editing.has(j);
    editing.delete(i); editing.delete(j);
    if (wasI) editing.add(j);
    if (wasJ) editing.add(i);
    renderRules();
    savePlayground();
    const b = $(`#rule-${j} .mini[aria-label^="Move rule ${j + 1} ${d < 0 ? "up" : "down"}"]`);
    if (b && !b.disabled) b.focus();
  }
  function removeRule(i) {
    pg.rules.splice(i, 1);
    const shifted = [...editing].filter((k) => k !== i).map((k) => (k > i ? k - 1 : k));
    editing.clear();
    shifted.forEach((k) => editing.add(k));
    renderRules();
    savePlayground();
  }

  function updateDatalist() {
    const dl = $("#recipient-list");
    dl.textContent = "";
    const names = new Set(["api.marketdata.example", "api.news.example", "tips.example", "unknown-wallet.example"]);
    for (const r of pg.rules) r.recipients.split(",").map((x) => x.trim()).filter(Boolean).forEach((x) => names.add(x));
    for (const n of names) dl.append(h("option", { value: n }));
  }

  function updateAskState(built) {
    const b = built || buildPolicy();
    const blocked = !b.policy || pg.tampered !== null;
    $("#ask").disabled = blocked;
    for (const c of document.querySelectorAll("#scenarios .chip")) c.disabled = blocked;
    let why = null;
    if (pg.tampered !== null) why = "The log file was edited outside the ledger, so the ledger refuses to write. Undo the edits in step 5 first.";
    else if (!b.policy) why = "Fix the rules above first.";
    showError($("#ask-error"), why);
  }

  // ---- asking and signing ----

  function readForm() {
    const f = $("#ask-form");
    return {
      agent: f.agent.value.trim(), recipient: f.recipient.value.trim(), amount: f.amount.value,
      asset: f.asset.value.trim(), memo: f.memo.value, reversible: f.reversible.checked,
    };
  }

  function ask(data) {
    const built = buildPolicy();
    if (!built.policy || pg.tampered !== null) { updateAskState(built); return; }
    let req;
    try {
      req = A.paymentRequest(data);
    } catch (e) {
      showError($("#ask-error"), friendlyRequest(e.message));
      return;
    }
    try {
      const ledger = ledgerFor(built.policy);
      const e = ledger.request(req);
      pg.log = ledger.text();
      pg.lastSeq = Number(e.get("seq"));
      savePlayground();
      showError($("#ask-error"), null);
      showVerdict(e);
      renderLog();
      // on one-column layouts the answer is below the form, so bring it into view
      if (window.matchMedia && window.matchMedia("(max-width: 960px)").matches) {
        $("#verdict").scrollIntoView({ behavior: "smooth", block: "nearest" });
      }
    } catch (e) {
      showError($("#ask-error"), e.message);
    }
  }

  function friendlyRequest(msg) {
    return msg.charAt(0).toUpperCase() + msg.slice(1);
  }

  function showVerdict(e) {
    const outcome = get(e, "outcome");
    const words = {
      allow: ["Allowed", "The agent can pay now.", I.check],
      hold: ["On hold", "A human has to approve it first.", I.pause],
      deny: ["Denied", "The agent must not pay.", I.cross],
    }[outcome];
    const rid = get(e, "rule_id");
    const box = $("#verdict");
    box.textContent = "";
    box.append(h("div", { class: "verdict pop " + outcome },
      h("div", { class: "badge" }, icon(words[2])),
      h("div", null,
        h("div", { class: "word" }, `${words[0]} · #${Number(get(e, "seq"))}`),
        h("div", { class: "say" }, `${get(e, "amount")} ${get(e, "asset")} to ${get(e, "recipient")}. ${words[1]}`),
        h("div", { class: "why" }, rid === null ? "No rule covers this asset and recipient, so it is denied." :
          `Rule "${rid}": ${get(e, "reason")}.`))));
  }

  function sign(seq, approve, noteInput, errEl) {
    const built = buildPolicy();
    if (pg.tampered !== null) { showError(errEl, "Undo the edits in step 5 first."); return; }
    const who = $("#approver").value.trim();
    try {
      const ledger = ledgerFor(built.policy);
      if (approve && !built.policy) throw new Error("Fix the rules first: approving checks today's limit again.");
      const e = approve ? ledger.approve(seq, who, noteInput.value) : ledger.reject(seq, who, noteInput.value);
      pg.log = ledger.text();
      pg.lastSeq = Number(e.get("seq"));
      savePlayground();
      toast(approve ? `Payment #${seq} approved. The agent can pay now.` : `Payment #${seq} rejected.`);
      renderLog();
    } catch (e) {
      showError(errEl, friendlyRequest(e.message));
    }
  }

  // ---- log, pending, cheat ----

  function currentText() { return pg.tampered !== null ? pg.tampered : pg.log; }

  function renderLog() {
    let ledger;
    try {
      ledger = ledgerFor(null);
    } catch (e) {
      pg.log = "";
      ledger = ledgerFor(null);
    }
    const n = ledger.entries.length;
    $("#log-count").textContent = plural(n, "entry", "entries");

    const headBox = $("#pg-head");
    headBox.textContent = "";
    headBox.append(h("span", null, "Head hash"), ...hashChip(ledger.head));

    // pending
    const pend = $("#pending");
    pend.textContent = "";
    const waiting = ledger.pending();
    if (!waiting.length) pend.append(h("li", { class: "empty" }, "Nothing is waiting. Payments that need a human show up here."));
    for (const e of waiting) {
      const seq = Number(get(e, "seq"));
      const note = h("input", { placeholder: "note (optional)", "aria-label": `Note for payment #${seq}`, autocomplete: "off" });
      const err = h("p", { class: "err", role: "alert", hidden: true });
      pend.append(h("li", { class: "pend" },
        h("div", { class: "pend-top" }, h("span", { class: "seq mono" }, "#" + seq),
          h("span", { class: "amt" }, `${get(e, "amount")} ${get(e, "asset")}`),
          h("span", null, `${get(e, "agent")} → ${get(e, "recipient")}`),
          get(e, "reversible") ? null : h("span", { class: "tag" }, "irreversible")),
        h("div", { class: "why" }, `${get(e, "reason")}${get(e, "memo") ? " · memo: " + get(e, "memo") : ""}`),
        h("div", { class: "pend-act" }, note,
          h("button", { class: "btn small ok", type: "button", onclick: () => sign(seq, true, note, err) }, "Approve"),
          h("button", { class: "btn small no", type: "button", onclick: () => sign(seq, false, note, err) }, "Reject")),
        err));
    }

    // timeline: the file as it is now (edited or not), newest first
    const text = currentText();
    const result = A.verifyLog(encoder.encode(text), "playground.jsonl", null);
    const marks = marksFor(result);
    const list = $("#pg-log");
    list.textContent = "";
    const shown = result.entries;
    if (!shown.length) list.append(h("li", { class: "empty" }, "Empty. Ask the ledger to pay something."));
    for (let i = shown.length - 1; i >= 0; i--) {
      const e = shown[i];
      const m = marks.get(i) || {};
      let state = null;
      if (pg.tampered === null && get(e, "type") === "decision" && get(e, "outcome") === A.HOLD) state = ledger.status(i);
      list.append(entryView(e, i, { mark: m.mark, note: m.note, state, flash: pg.tampered === null && i === pg.lastSeq }));
    }
    pg.lastSeq = null;

    $("#download").disabled = !text;
    $("#to-verify").disabled = !text;
    $("#clear-log").disabled = !pg.log && pg.tampered === null;
    renderCheat(ledger);
    updateAskState();
  }

  function renderCheat(ledger) {
    $("#shared-head").textContent = pg.sharedHead || "Not shared yet";
    $("#share").disabled = !ledger.entries.length || pg.tampered !== null;

    const sel = $("#cheat-entry");
    const keep = sel.value;
    sel.textContent = "";
    const source = parseLoose(currentText());
    source.forEach((e, i) => {
      const t = get(e, "type");
      const label = t === "decision"
        ? `#${i} ${disp(get(e, "outcome"))} ${disp(get(e, "amount"))} ${disp(get(e, "asset"))} → ${disp(get(e, "recipient"))}`
        : `#${i} ${disp(t)} of #${disp(get(e, "decision_seq"))}`;
      sel.append(h("option", { value: String(i) }, label));
    });
    if (!source.length) sel.append(h("option", { value: "" }, "The log is empty"));
    if (cheatTouched && [...sel.options].some((o) => o.value === keep)) sel.value = keep;
    else if (source.length) {
      // until the user picks one, suggest the first held payment: the one a cheater would shrink
      const held = source.findIndex((e) => get(e, "type") === "decision" && get(e, "outcome") === A.HOLD);
      sel.value = String(held !== -1 ? held : 0);
    }
    const has = source.length > 0;
    const isDecision = has && get(source[Number(sel.value)], "type") === "decision";
    $("#cheat-change").disabled = !isDecision;
    $("#cheat-delete").disabled = !has;
    $("#cheat-entry").disabled = !has;
    $("#cheat-undo").disabled = pg.tampered === null;

    const out = $("#cheat-result");
    out.textContent = "";
    if (pg.tampered === null) {
      if (has) {
        out.append(checkLine("good", "The log is untouched", `Every hash matches and links up. ${plural(ledger.entries.length, "entry", "entries")}.`));
      }
      return;
    }
    const r = A.verifyLog(encoder.encode(pg.tampered), "playground.jsonl", null);
    if (!r.ok) {
      out.append(checkLine("bad", "Caught by the check", `${r.error}. ${r.stage === "chain" ? plainChainError(r.error) : ""}`.trim()));
    } else {
      out.append(checkLine("warn", "The file checks out on its own",
        "The edited file is still consistent with itself, so checking the file alone finds nothing. Only a hash saved earlier can expose it."));
    }
    if (pg.sharedHead) {
      // the counterparty checks that the history up to the head they saved is still intact
      const entries = parseLoose(pg.tampered);
      const at = entries.findIndex((e) => get(e, "hash") === pg.sharedHead);
      let intact = false;
      if (at !== -1) {
        try { A.verifyEntries(entries.slice(0, at + 1), pg.sharedHead); intact = true; } catch (e) { intact = false; }
      }
      if (!intact) {
        out.append(checkLine("bad", "Caught by the counterparty",
          `The history up to their saved head ${short(pg.sharedHead)} no longer checks out, so something they already saw was changed.`));
      } else if (r.ok) {
        out.append(checkLine("warn", "Not caught by the counterparty yet",
          `The edit is after entry #${at}, the last one they saw. Share the head again and the next edit is caught.`));
      } else {
        out.append(checkLine("good", "What the counterparty saw is unchanged",
          `Everything up to entry #${at} still checks out against their saved head. The edit came later and the check above caught it.`));
      }
    } else if (r.ok) {
      out.append(checkLine("bad", "Nobody can tell",
        "No head hash was shared before the edit. This is why you send the head to the other side regularly. Undo, share the head, and try again."));
    }
  }

  function checkLine(kind, title, text) {
    const ic = kind === "good" ? I.check : kind === "bad" ? I.cross : I.warn;
    return h("div", { class: "check-line " + kind }, h("span", { class: "ic" }, icon(ic)),
      h("div", null, h("b", null, title), h("p", null, text)));
  }

  // lines of a log that may be broken; unreadable lines are skipped
  function parseLoose(text) {
    const out = [];
    for (const line of text.split("\n")) {
      if (!line) continue;
      try {
        const v = A.parseJSON(line);
        if (v instanceof Map) out.push(v);
      } catch (e) { /* skip */ }
    }
    return out;
  }

  function tamper(action) {
    const entries = parseLoose(currentText());
    const i = Number($("#cheat-entry").value);
    if (!entries.length || !(i >= 0 && i < entries.length)) return;
    if (action === "change") {
      const amount = $("#cheat-amount").value.trim();
      if (!amount) { toast("Type the new amount first"); return; }
      const e = new Map(entries[i]);
      e.set("amount", amount);
      entries[i] = e;
    } else {
      entries.splice(i, 1);
    }
    const out = $("#cheat-rehash").checked ? A.rehash(entries) : entries;
    pg.tampered = out.map((e) => A.canonical(e) + "\n").join("");
    savePlayground();
    renderLog();
    toast(action === "change" ? `Entry #${i} edited in the file` : `Entry #${i} deleted from the file`);
  }

  function initPlayground() {
    loadPlayground();
    $("#approver").value = pg.approver;
    $("#approver").addEventListener("input", (ev) => {
      pg.approver = ev.target.value;
      savePlayground();
      // an error about the previous name no longer applies
      for (const e of document.querySelectorAll("#pending .err")) showError(e, null);
    });

    const chips = $("#scenarios");
    for (const s of SCENARIOS) {
      chips.append(h("button", {
        class: "chip", type: "button", onclick: () => {
          const f = $("#ask-form");
          f.agent.value = f.agent.value.trim() || "research-agent";
          f.recipient.value = s.recipient;
          f.amount.value = s.amount;
          f.asset.value = "USDC";
          f.memo.value = s.memo;
          f.reversible.checked = s.reversible;
          ask(readForm());
        },
      }, h("span", { class: "dot t-" + s.hint, "aria-hidden": "true" }), s.label));
    }

    $("#ask-form").addEventListener("submit", (ev) => { ev.preventDefault(); ask(readForm()); });
    $("#add-rule").addEventListener("click", () => {
      let n = pg.rules.length + 1;
      while (pg.rules.some((r) => r.id === "rule-" + n)) n++;
      pg.rules.push({ id: "rule-" + n, asset: "USDC", max_amount: "10", daily_limit: "50", approval_above: "",
                      recipients: "", irreversible: true });
      editing.add(pg.rules.length - 1);
      renderRules();
      savePlayground();
      focusRule(pg.rules.length - 1);
    });
    $("#reset-rules").addEventListener("click", () => {
      pg.rules = EXAMPLE_RULES.map((r) => Object.assign({}, r));
      editing.clear();
      renderRules();
      savePlayground();
      toast("Example rules restored");
    });
    $("#download").addEventListener("click", () => download(currentText(), pg.tampered !== null ? "payments-edited.jsonl" : "payments.jsonl"));
    $("#to-verify").addEventListener("click", () => {
      verifySource = { bytes: encoder.encode(currentText()), name: pg.tampered !== null ? "playground-edited.jsonl" : "playground.jsonl" };
      showTab("verify");
      runVerify();
      revealResult();
    });
    $("#clear-log").addEventListener("click", () => {
      if (!window.confirm("Clear the playground log? All entries here will be deleted.")) return;
      pg.log = "";
      pg.tampered = null;
      pg.sharedHead = null;
      savePlayground();
      $("#verdict").textContent = "";
      $("#verdict").append(h("div", { class: "verdict-empty" }, h("p", { class: "verdict-title" }, "The log is empty again."),
        h("p", { class: "hint" }, "Ask the ledger to pay something.")));
      renderLog();
    });
    $("#share").addEventListener("click", () => {
      pg.sharedHead = ledgerFor(null).head;
      savePlayground();
      renderLog();
      toast("Head hash saved as the counterparty's copy");
    });
    $("#cheat-entry").addEventListener("change", () => { cheatTouched = true; renderLog(); });
    $("#cheat-change").addEventListener("click", () => tamper("change"));
    $("#cheat-delete").addEventListener("click", () => tamper("delete"));
    $("#cheat-undo").addEventListener("click", () => {
      pg.tampered = null;
      savePlayground();
      renderLog();
      toast("Edits undone");
    });

    renderRules();
    renderLog();
  }

  // ---- VERIFY -------------------------------------------------------------------------

  let verifySource = null;   // { bytes, name }
  let verifyFilter = "all";
  let verifyShown = PAGE;

  function expectedHead() {
    const raw = $("#expected-head").value.trim().toLowerCase();
    const input = $("#expected-head");
    if (!raw) { input.classList.remove("bad"); showError($("#head-error"), null); return { head: null }; }
    if (!A.HASH_RE.test(raw)) {
      input.classList.add("bad");
      showError($("#head-error"), "A head hash is 64 characters, 0-9 and a-f. Checking without it for now.");
      return { head: null, bad: true };
    }
    input.classList.remove("bad");
    showError($("#head-error"), null);
    return { head: raw };
  }

  function runVerify() {
    const out = $("#verify-out");
    out.textContent = "";
    if (!verifySource) return;
    const { head } = expectedHead();
    const r = A.verifyLog(verifySource.bytes, verifySource.name, head);
    out.append(banner(r, verifySource.name, head));
    if (r.ok && r.entries.length) out.append(stats(r.entries));
    if (r.entries.length) out.append(timeline(r));
    else if (r.stage === "parse") out.append(badLine(verifySource.bytes, r));
  }

  function banner(r, name, head) {
    const cmd = `$ agent-payment-log verify ${name}${head ? " --head " + head : ""}`;
    const output = r.ok ? `OK: ${r.entries.length} entries, head ${r.head}` : `FAILED: ${r.error}`;
    const term = h("div", { class: "terminal", "aria-label": "Same check on the command line" },
      h("span", { class: "prompt" }, cmd), "\n", h("span", { class: r.ok ? "ok" : "fail" }, output));
    if (r.ok) {
      const empty = r.entries.length === 0;
      return h("div", { class: "banner good" }, h("div", { class: "badge" }, icon(I.check)),
        h("div", null,
          h("h3", null, empty ? "An empty log" : "This log checks out"),
          h("p", { class: "say" }, empty ? "The file has no entries yet. There is nothing to check." :
            `${plural(r.entries.length, "entry", "entries")}, none edited, removed or reordered. Every approval and rejection follows the ledger rules.` +
            (head ? " It ends exactly at the head hash you gave." : "")),
          h("div", { class: "meta" }, h("span", null, "Head"), ...hashChip(r.head)),
          term));
    }
    return h("div", { class: "banner bad" }, h("div", { class: "badge" }, icon(I.cross)),
      h("div", null, h("h3", null, "This log does not check out"), h("p", { class: "say" }, explain(r, name)), term));
  }

  function explain(r, name) {
    switch (r.stage) {
      case "read": {
        const m = r.error.startsWith(name + ": ") ? r.error.slice(name.length + 2) : r.error;
        if (m === "not UTF-8 text") return "The file is not UTF-8 text, so it is not a log or it was damaged.";
        if (m.startsWith("has Windows line endings")) return "The file has Windows line endings (CRLF). A log uses LF only, so it was re-saved by another program.";
        if (m.startsWith("last line is incomplete")) return "The last line has no newline at the end, so the file may have been cut off.";
        return m + ".";
      }
      case "parse": return `Line ${r.index + 1} is not a valid log line, so the file was damaged or edited by hand.`;
      case "chain": return `Entry #${r.index}: ${plainChainError(r.error)} Entries after it cannot be trusted.`;
      case "ledger": return `The hashes are intact, but entry #${r.index} breaks the ledger rules: ${r.error.replace(/^entry \d+: /, "")}.`;
      case "head": return "Every hash links up, but the log does not end at the head hash you gave. Entries were added after it was shared, or the history was rewritten.";
      default: return r.error;
    }
  }

  function stats(entries) {
    let allow = 0, hold = 0, deny = 0, approved = 0, rejected = 0;
    const resolved = new Map();
    for (const e of entries) {
      if (get(e, "type") !== "decision") resolved.set(Number(get(e, "decision_seq")), get(e, "type"));
    }
    const cleared = new Map();
    const add = (asset, amount) => cleared.set(asset, (cleared.get(asset) || A.Dec.zero()).add(A.Dec.parse(amount)));
    entries.forEach((e, i) => {
      if (get(e, "type") !== "decision") return;
      const o = get(e, "outcome");
      if (o === A.ALLOW) { allow++; add(get(e, "asset"), get(e, "amount")); }
      if (o === A.DENY) deny++;
      if (o === A.HOLD) {
        hold++;
        const r = resolved.get(i);
        if (r === "approval") { approved++; add(get(e, "asset"), get(e, "amount")); }
        if (r === "rejection") rejected++;
      }
    });
    const pending = hold - approved - rejected;
    const stat = (n, l, s) => h("div", { class: "stat" }, h("div", { class: "n" }, String(n)), h("div", { class: "l" }, l), s ? h("div", { class: "s" }, s) : null);
    const box = h("div", { class: "card" },
      h("div", { class: "stats" },
        stat(entries.length, "entries"),
        stat(allow, "allowed"),
        stat(hold, "held", `${approved} approved · ${rejected} rejected · ${pending} waiting`),
        stat(deny, "denied")));
    if (cleared.size) {
      box.append(h("div", { class: "spend row" }, h("span", null, "Cleared to pay (allowed + approved):"),
        [...cleared].sort((a, b) => A.cmpStr(a[0], b[0])).map(([asset, d]) => h("span", null, h("b", null, d.toString()), " ", asset))));
    }
    return box;
  }

  function timeline(r) {
    const marks = marksFor(r);
    const wrap = h("div", { class: "card" });
    const ok = r.ok;
    const kindOf = (e) => {
      const t = get(e, "type");
      if (t === "decision") return get(e, "outcome");
      return t === "approval" || t === "rejection" ? "sign" : "other";
    };
    const counts = { all: r.entries.length, allow: 0, hold: 0, deny: 0, sign: 0 };
    for (const e of r.entries) { const k = kindOf(e); if (k in counts) counts[k]++; }
    if (!(verifyFilter in counts) || (verifyFilter !== "all" && !counts[verifyFilter])) verifyFilter = "all";

    const filters = h("div", { class: "filters", role: "group", "aria-label": "Show entries" });
    for (const [k, label] of [["all", "All"], ["allow", "Allowed"], ["hold", "Held"], ["deny", "Denied"], ["sign", "Sign-offs"]]) {
      if (k !== "all" && !counts[k]) continue;
      filters.append(h("button", { type: "button", "aria-pressed": verifyFilter === k ? "true" : "false",
        onclick: () => { verifyFilter = k; verifyShown = PAGE; runVerify(); } }, `${label} ${counts[k]}`));
    }
    wrap.append(h("div", { class: "card-head split" }, h("h2", null, r.stage === "parse" ? "Lines read before the problem" : "Entries"), filters));

    const list = h("ol", { class: "timeline" });
    const picked = [];
    r.entries.forEach((e, i) => { if (verifyFilter === "all" || kindOf(e) === verifyFilter || marks.has(i) && marks.get(i).mark !== "after") picked.push(i); });
    let states = null;
    if (ok) {
      states = new Map();
      for (const e of r.entries) {
        if (get(e, "type") !== "decision") states.set(Number(get(e, "decision_seq")), get(e, "type") === "approval" ? "approved" : "rejected");
      }
    }
    for (const i of picked.slice(0, verifyShown)) {
      const e = r.entries[i];
      const m = marks.get(i) || {};
      let state = null;
      if (states && get(e, "type") === "decision" && get(e, "outcome") === A.HOLD) state = states.get(i) || "pending";
      list.append(entryView(e, i, { mark: m.mark, note: m.note, state }));
    }
    wrap.append(list);
    if (r.stage === "parse") wrap.append(badLine(verifySource.bytes, r));
    if (picked.length > verifyShown) {
      wrap.append(h("div", { class: "more" }, h("button", { class: "btn ghost", type: "button",
        onclick: () => { verifyShown += PAGE; runVerify(); } }, `Show more (${picked.length - verifyShown} left)`)));
    }
    return wrap;
  }

  function revealResult() {
    const first = $("#verify-out").firstElementChild;
    if (first) first.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function badLine(bytes, r) {
    let line = "";
    try {
      line = new TextDecoder("utf-8").decode(bytes).split("\n")[r.index] || "";
    } catch (e) { line = ""; }
    return h("div", { class: "card" }, h("ol", { class: "timeline" },
      h("li", { class: "entry m-broken" }, h("div", { class: "entry-head" }, h("span", { class: "seq" }, `line ${r.index + 1}`)),
        h("p", { class: "entry-note" }, r.error.replace(/^line \d+: /, "")),
        line ? h("div", { class: "raw" }, clip(line, 600)) : h("div", { class: "raw" }, "(empty line)"))));
  }

  function loadFile(file) {
    if (!file) return;
    if (file.size > MAX_FILE) {
      verifySource = null;
      $("#verify-out").textContent = "";
      $("#verify-out").append(h("p", { class: "err" }, `${file.name} is larger than 50 MB. Check it on the command line: agent-payment-log verify ${file.name}`));
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      verifySource = { bytes: new Uint8Array(reader.result), name: file.name };
      verifyFilter = "all";
      verifyShown = PAGE;
      runVerify();
      revealResult();
    };
    reader.onerror = () => {
      $("#verify-out").textContent = "";
      $("#verify-out").append(h("p", { class: "err" }, `Could not read ${file.name}.`));
    };
    reader.readAsArrayBuffer(file);
  }

  async function loadSample(name) {
    try {
      const res = await fetch("samples/" + name, { cache: "no-cache" });
      if (!res.ok) throw new Error(String(res.status));
      verifySource = { bytes: new Uint8Array(await res.arrayBuffer()), name };
      verifyFilter = "all";
      verifyShown = PAGE;
      runVerify();
      revealResult();
    } catch (e) {
      $("#verify-out").textContent = "";
      $("#verify-out").append(h("p", { class: "err" }, "Could not load the sample. Open the page from a web server, or download the sample from GitHub."));
    }
  }

  function initVerify() {
    const drop = $("#drop");
    const file = $("#file");
    file.addEventListener("change", () => { loadFile(file.files[0]); file.value = ""; });
    for (const ev of ["dragenter", "dragover"]) {
      drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); });
    }
    for (const ev of ["dragleave", "dragend"]) drop.addEventListener(ev, () => drop.classList.remove("over"));
    drop.addEventListener("drop", (e) => {
      e.preventDefault();
      drop.classList.remove("over");
      loadFile(e.dataTransfer && e.dataTransfer.files[0]);
    });
    // a file dropped anywhere else would make the browser navigate away
    window.addEventListener("dragover", (e) => e.preventDefault());
    window.addEventListener("drop", (e) => {
      e.preventDefault();
      if (!drop.contains(e.target) && e.dataTransfer && e.dataTransfer.files[0]) {
        showTab("verify");
        loadFile(e.dataTransfer.files[0]);
      }
    });
    $("#load-demo").addEventListener("click", () => loadSample("demo_payments.jsonl"));
    $("#load-tampered").addEventListener("click", () => loadSample("demo_payments_tampered.jsonl"));
    let t = null;
    $("#expected-head").addEventListener("input", () => { clearTimeout(t); t = setTimeout(() => { expectedHead(); runVerify(); }, 150); });
  }

  // ---- GUIDE ---------------------------------------------------------------------------

  function initGuide() {
    for (const b of document.querySelectorAll("[data-copy]")) {
      b.addEventListener("click", () => copy($("#code-" + b.getAttribute("data-copy")).textContent, "Code"));
    }
  }

  // ---- start ----------------------------------------------------------------------------

  function start() {
    if (!A) return;
    initTheme();
    initTabs();
    initPlayground();
    initVerify();
    initGuide();
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
