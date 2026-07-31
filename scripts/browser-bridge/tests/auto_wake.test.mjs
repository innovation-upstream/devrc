// auto_wake.test.mjs — the DETERMINISTIC hidden-tab auto-wake in
// `opencode/tools/browser_tool_impl.mjs`.
//
// WHY this exists (measured, claudedocs/browser-bridge-deepseek-measurement-
// 2026-07-31.md §3.2): the browser-agent's tab is ALWAYS opened `active:false`, so
// it is ALWAYS hidden and throttled. The server self-announces that on every read
// (`data.hidden:true` + the throttle note), but `summarizeResult` used to return a
// BARE `data.text`/`data.html` and discard both. On a throttled SPA that rendered a
// sparse-but-plausible shell, the model read the shell, could not tell the tab was
// hidden, and returned a CONFIDENT WRONG answer with `status:"ok"` whose evidence
// quoted the shell verbatim — 1 of 13 successful runs (7.7%), and the only failure
// mode found.
//
// These tests pin the fix's four properties:
//   1. hidden → the tool ITSELF issues `wake` and returns the RE-READ.
//   2. wake ONCE PER PAGE (a second read of the same page does NOT re-wake), and a
//      `nav` (new document → throttled again) RESETS that.
//   3. a wake failure FAILS OPEN — the original read comes back, loudly labelled,
//      never as an error.
//   4. a non-hidden read is byte-identical to before (no wake, no banner).
//
// ⚠ Green here is a PREREQUISITE, not verification: whether auto-wake actually
// fixes the measured F2 case can only be settled against a live browser.

import test, { beforeEach } from "node:test";
import assert from "node:assert/strict";
import {
  runBrowserOp, summarizeResult, resetAutoWakeState, hiddenNotice,
  AUTO_WAKE_OPS, HIDDEN_SIGNAL_OPS, AUTO_WAKE_PAGE_CAP, AUTO_WAKE_TAB_CAP,
  PAGE_INVALIDATING_OPS, pageKeyOf, hasWokenPage, getPageWake,
  markPageWakeAttempt, recordPageWakeOutcome, wakeCountForTab, autoWakeTabCap,
  forgetPageWakes, autoWakeEnabled,
} from "../opencode/tools/browser_tool_impl.mjs";
import { HIDDEN_TAB_NOTE } from "../extension/protocol.js";
import { readFileSync, unlinkSync } from "node:fs";
import { tmpdir } from "node:os";

const TOK = "secret-token";
const TAB = "4242";
const PAGE = "https://app.test/dashboard";

function baseEnv(extra = {}) {
  return { BROWSER_AGENT_TAB: TAB, BROWSER_BRIDGE_HOST: "127.0.0.1",
    BROWSER_BRIDGE_PORT: "8788", BROWSER_AGENT_SESSION_ID: "claude:sess", ...extra };
}

const run = (args, env, fetchImpl) =>
  runBrowserOp(args, { env, fetchImpl, readToken: () => TOK });

// A bridge stub that models the ONE behaviour that matters: the tab is hidden, and
// a `wake` makes the page render. Before any wake a read returns the SHELL; after a
// wake it returns the RENDERED content. `hidden` stays true throughout, because it
// does — the tab is still a background tab; only the DOM changed.
function bridge(opts = {}) {
  const st = {
    shell: opts.shell ?? "SHELL (waiting for frames)",
    rendered: opts.rendered ?? "RENDERED ANSWER",
    hidden: opts.hidden ?? true,
    woke: opts.woke ?? true,
    url: opts.url ?? PAGE,
    wakeStatus: opts.wakeStatus ?? 200,     // non-200 → transport-level wake failure
    wakeEnvelopeError: opts.wakeEnvelopeError ?? null, // op-level wake failure
    reReadError: opts.reReadError ?? null,  // op-level failure on the RE-read only
    // click/key/eval replace the document AT THE SAME URL (form POST, reload,
    // same-URL SPA remount) → freshly throttled, invisible to the page key.
    replacesDocument: opts.replacesDocument ?? false,
    awake: false,
    reads: 0,
  };
  const calls = [];
  const impl = async (url, init) => {
    const body = JSON.parse(init.body);
    calls.push(body);
    if (body.op === "wake") {
      if (st.wakeStatus !== 200) {
        return { status: st.wakeStatus, async text() { return "bridge exploded"; } };
      }
      if (st.wakeEnvelopeError) {
        return { status: 200, async text() {
          return JSON.stringify({ ok: true,
            result: { id: "c", ok: false, error: st.wakeEnvelopeError } });
        } };
      }
      if (st.woke) st.awake = true;
      return { status: 200, async text() {
        return JSON.stringify({ ok: true, result: { id: "c", ok: true, data: {
          tabId: 4242, woke: st.woke, visibilityState: st.woke ? "visible" : "hidden",
          readyState: "complete", settleMs: 1500, url: st.url, title: "T" } } });
      } };
    }
    if (body.op === "nav") {
      st.awake = false;                       // a new document is throttled again
      st.url = body.url;
      return { status: 200, async text() {
        return JSON.stringify({ ok: true,
          result: { id: "c", ok: true, data: { url: body.url, title: "T" } } });
      } };
    }
    if (body.op === "click" || body.op === "key" || body.op === "type") {
      // Models the case the page key CANNOT see: the document is replaced (a form
      // POST / reload / same-URL SPA remount) and is throttled again, at the SAME url.
      if (st.replacesDocument) st.awake = false;
      return { status: 200, async text() {
        return JSON.stringify({ ok: true, result: { id: "c", ok: true,
          data: { url: st.url, clicked: body.selector ?? null, key: body.key ?? null,
                  typed: body.text ? body.text.length : null } } });
      } };
    }
    if (body.op === "eval") {
      // The value comes from the CURRENT document; any replacement it triggers
      // lands after — which is exactly why the banner is decided before the
      // invalidation in runBrowserOp.
      const data = { url: st.url, value: st.awake ? st.rendered : st.shell };
      if (st.replacesDocument) st.awake = false;
      if (st.hidden) { data.hidden = true; data.visibilityState = "hidden";
                       data.note = HIDDEN_TAB_NOTE; }
      return { status: 200, async text() {
        return JSON.stringify({ ok: true, result: { id: "c", ok: true, data } });
      } };
    }
    // text / getHtml
    st.reads += 1;
    if (st.reReadError && st.reads > 1) {
      return { status: 200, async text() {
        return JSON.stringify({ ok: true,
          result: { id: "c", ok: false, error: st.reReadError } });
      } };
    }
    const content = st.awake ? st.rendered : st.shell;
    const data = { url: st.url, title: "T" };
    if (body.op === "getHtml") data.html = `<div id=app>${content}</div>`;
    else data.text = content;
    if (st.hidden) { data.hidden = true; data.visibilityState = "hidden";
                     data.note = HIDDEN_TAB_NOTE; }
    else { data.visibilityState = "visible"; }
    return { status: 200, async text() {
      return JSON.stringify({ ok: true, result: { id: "c", ok: true, data } });
    } };
  };
  impl.calls = calls;
  impl.state = st;
  return impl;
}

const ops = (f) => f.calls.map((c) => c.op);

// A note WITHOUT the server's own `activate` caveat, so the "never says activate"
// assertion tests THIS module's wording rather than the extension's (HIDDEN_TAB_NOTE
// deliberately mentions `activate` in order to say DON'T).
const HIDDEN_FALLBACK_NOTE_PROBE = "tab is hidden — background tabs are throttled.";

beforeEach(() => resetAutoWakeState());

// --------------------------------------------------------------------------- //
// 1. hidden → auto-wake → the RE-READ is what the model sees
// --------------------------------------------------------------------------- //
test("hidden text read: the tool auto-issues `wake` and returns the RE-READ, not the shell", async () => {
  const f = bridge();
  const out = await run({ op: "text" }, baseEnv(), f);

  assert.deepEqual(ops(f), ["text", "wake", "text"],
    "read → wake → re-read, all inside ONE model tool call");
  assert.match(out, /RENDERED ANSWER/, "the model must receive the POST-wake content");
  assert.doesNotMatch(out, /SHELL \(waiting for frames\)/,
    "REGRESSION: the unrendered shell must NOT be what the model reads");
  assert.match(out, /auto-wake|AUTOMATICALLY/i, "the auto-wake must be disclosed");
});

test("hidden html read auto-wakes the same way", async () => {
  const f = bridge();
  const out = await run({ op: "html" }, baseEnv(), f);
  assert.deepEqual(ops(f), ["getHtml", "wake", "getHtml"]);
  assert.match(out, /RENDERED ANSWER/);
});

test("the wake it issues is `wake` — NEVER `activate` (which steals the operator's screen)", async () => {
  const f = bridge();
  await run({ op: "text" }, baseEnv(), f);
  assert.ok(!ops(f).includes("activate"),
    "activate is unreachable by the agent by design and must stay that way");
  const wake = f.calls.find((c) => c.op === "wake");
  assert.equal(wake.tab, 4242, "the wake is scoped to the agent's OWN forced tab");
});

test("the auto-wake `wake` carries no model-supplied field (typed, tab forced by env)", async () => {
  const f = bridge();
  await run({ op: "text", selector: "#app", waitMs: 999999 }, baseEnv(), f);
  const wake = f.calls.find((c) => c.op === "wake");
  assert.deepEqual(Object.keys(wake).sort(), ["op", "tab"],
    "no waitMs/selector smuggled into the auto-wake");
  // …and the RE-read reuses the caller's ORIGINAL typed args.
  const reads = f.calls.filter((c) => c.op === "text");
  assert.equal(reads.length, 2);
  assert.equal(reads[1].selector, "#app", "the re-read repeats the original read exactly");
});

// --------------------------------------------------------------------------- //
// 2. wake ONCE PER PAGE
// --------------------------------------------------------------------------- //
test("wake-once-per-page: a SECOND hidden read of the same page does NOT re-wake", async () => {
  const f = bridge();
  await run({ op: "text" }, baseEnv(), f);
  f.calls.length = 0;

  const out2 = await run({ op: "text" }, baseEnv(), f);
  assert.deepEqual(ops(f), ["text"], "one read, ZERO wakes — the rendered DOM persists");
  assert.match(out2, /RENDERED ANSWER/);
  assert.match(out2, /already SUCCEEDED for this page/,
    "the hidden signal must still survive on the cheap follow-up read");
});

test("wake-once-per-page holds across read SHAPES (text then html) on one page", async () => {
  const f = bridge();
  await run({ op: "text" }, baseEnv(), f);
  f.calls.length = 0;
  await run({ op: "html" }, baseEnv(), f);
  assert.deepEqual(ops(f), ["getHtml"], "the page, not the op, is what was woken");
});

test("a `nav` RESETS the per-page wake — the new document is throttled again", async () => {
  const f = bridge();
  await run({ op: "text" }, baseEnv(), f);              // wakes page 1
  await run({ op: "nav", url: "https://app.test/other" }, baseEnv(), f);
  f.calls.length = 0;

  await run({ op: "text" }, baseEnv(), f);
  assert.deepEqual(ops(f), ["text", "wake", "text"],
    "REGRESSION: after a nav the tool MUST wake again — the new page is a new document");
});

test("an in-page navigation (the read's url changed) also re-wakes, without a `nav`", async () => {
  const f = bridge();
  await run({ op: "text" }, baseEnv(), f);
  f.state.url = "https://app.test/detail";   // e.g. a click-driven SPA route change
  f.state.awake = false;
  f.calls.length = 0;

  await run({ op: "text" }, baseEnv(), f);
  assert.deepEqual(ops(f), ["text", "wake", "text"]);
});

test("BROWSER_AGENT_AUTO_WAKE=0 disables the auto-wake but KEEPS the warning", async () => {
  const f = bridge();
  const out = await run({ op: "text" }, baseEnv({ BROWSER_AGENT_AUTO_WAKE: "0" }), f);
  assert.deepEqual(ops(f), ["text"], "the kill switch must issue no wake at all");
  assert.match(out, /SHELL \(waiting for frames\)/);
  assert.match(out, /WARNING: your tab is HIDDEN/,
    "turning auto-wake off must NOT return us to a silent wrong answer");
});

// --------------------------------------------------------------------------- //
// 3. fail-open
// --------------------------------------------------------------------------- //
test("wake FAILURE (op-level) falls back to the original read + a loud warning", async () => {
  const f = bridge({ wakeEnvelopeError: "cdp_timeout:wake" });
  const out = await run({ op: "text" }, baseEnv(), f);
  assert.deepEqual(ops(f), ["text", "wake"], "no re-read after a failed wake");
  assert.match(out, /SHELL \(waiting for frames\)/, "FAIL-OPEN: the original read survives");
  assert.match(out, /automatic 'wake' FAILED/);
  assert.match(out, /cdp_timeout:wake/, "the reason must be nameable by the model");
});

test("wake FAILURE (transport/HTTP) also fails open — never turns a read into an error", async () => {
  const f = bridge({ wakeStatus: 503 });
  const out = await run({ op: "text" }, baseEnv(), f);
  assert.match(out, /SHELL \(waiting for frames\)/);
  assert.match(out, /automatic 'wake' FAILED/);
});

test("a wake the operator's op-allowlist forbids fails open (no bypass, no crash)", async () => {
  const f = bridge();
  const out = await run({ op: "text" },
    baseEnv({ BROWSER_AGENT_ALLOWED_OPS: "text,html" }), f);
  assert.deepEqual(ops(f), ["text"], "a forbidden wake must never reach the bridge");
  assert.match(out, /automatic 'wake' FAILED/);
  assert.match(out, /op_not_allowed:wake/);
});

test("a failed wake is NOT retried on every subsequent read of the same page", async () => {
  const f = bridge({ wakeEnvelopeError: "cdp_timeout:wake" });
  await run({ op: "text" }, baseEnv(), f);
  f.calls.length = 0;
  await run({ op: "text" }, baseEnv(), f);
  assert.deepEqual(ops(f), ["text"], "one failed wake per page, not one per read");
});

// 🔴 THE REGRESSION THIS SECTION EXISTS FOR: not retrying a failed wake must not
// mean FORGETTING that it failed. The first cut stored only the page key, so every
// later read of that page was graded `note: … the content below is post-wake` —
// a throttled shell plus an authoritative assurance that it was rendered. That is
// a REASSURED wrong answer, strictly worse than the silent one this PR removes.
test("REGRESSION: after a FAILED wake, later reads keep the WARNING — never 'post-wake'", async () => {
  const f = bridge({ wakeEnvelopeError: "cdp_timeout:wake" });
  await run({ op: "text" }, baseEnv(), f);           // wake fails, page recorded
  f.calls.length = 0;

  for (const args of [{ op: "text" }, { op: "html" }, { op: "eval", js: "x" }]) {
    const out = await run(args, baseEnv(), f);
    assert.match(out, /^\[browser-tool\] WARNING:/,
      `${args.op}: a page whose wake FAILED must stay a WARNING, not a note:`);
    assert.match(out, /'wake' for this page FAILED earlier/);
    assert.match(out, /cdp_timeout:wake/, "the recorded REASON must survive, not just the key");
    assert.match(out, /may be an unrendered shell/);
    assert.doesNotMatch(out, /the content below is post-wake/,
      `${args.op}: REGRESSION — a failed wake was described as a successful one`);
    assert.doesNotMatch(out, /^\[browser-tool\] note:/);
  }
  assert.deepEqual(ops(f), ["text", "getHtml", "eval"], "and still no retry");
});

test("REGRESSION: an allowlist-refused wake also stays a WARNING on later reads", async () => {
  const f = bridge();
  const env = baseEnv({ BROWSER_AGENT_ALLOWED_OPS: "text,html" });
  await run({ op: "text" }, env, f);
  const out = await run({ op: "text" }, env, f);
  assert.match(out, /WARNING/);
  assert.match(out, /op_not_allowed:wake/, "the reason must still be nameable much later");
});

test("a wake that reported woke:false is recorded as UNVOUCHED, not as a success", async () => {
  const f = bridge({ woke: false });
  await run({ op: "text" }, baseEnv(), f);
  const out = await run({ op: "text" }, baseEnv(), f);
  assert.match(out, /WARNING/, "woke:false is not something the tool may vouch for later");
  assert.doesNotMatch(out, /already SUCCEEDED/);
});

test("a RE-READ failure falls back to the original read too", async () => {
  const f = bridge({ reReadError: "owned_tab_gone" });
  const out = await run({ op: "text" }, baseEnv(), f);
  assert.deepEqual(ops(f), ["text", "wake", "text"]);
  assert.match(out, /SHELL \(waiting for frames\)/);
  assert.match(out, /re-read failed: owned_tab_gone/);
});

test("wake reporting woke:false is surfaced honestly, and the re-read is not trusted blindly", async () => {
  const f = bridge({ woke: false });
  const out = await run({ op: "text" }, baseEnv(), f);
  assert.deepEqual(ops(f), ["text", "wake", "text"], "the re-read still happens");
  assert.match(out, /could NOT confirm the tab un-throttled/);
  assert.match(out, /woke:false/);
});

// --------------------------------------------------------------------------- //
// 4. a non-hidden read is UNCHANGED
// --------------------------------------------------------------------------- //
test("a VISIBLE tab: no wake is issued and the summary is the bare content (unchanged)", async () => {
  const f = bridge({ hidden: false, rendered: "RENDERED ANSWER" });
  f.state.awake = true;
  const out = await run({ op: "text" }, baseEnv(), f);
  assert.deepEqual(ops(f), ["text"], "a visible tab must never be woken");
  assert.equal(out, "RENDERED ANSWER", "byte-identical to the pre-fix summary");
});

test("summarizeResult keeps its exact old output for every non-hidden payload", () => {
  assert.equal(summarizeResult("text", { data: { text: "hi" } }), "hi");
  assert.equal(summarizeResult("html", { data: { html: "<b>x</b>" } }), "<b>x</b>");
  assert.equal(summarizeResult("eval", { data: { value: "42" } }), "42");
});

// --------------------------------------------------------------------------- //
// signal preservation (the "stop discarding it regardless" half)
// --------------------------------------------------------------------------- //
test("summarizeResult no longer DISCARDS data.hidden + the server note on text/html", () => {
  for (const [op, data] of [
    ["text", { text: "SHELL", hidden: true, note: HIDDEN_TAB_NOTE }],
    ["html", { html: "<i>SHELL</i>", hidden: true, note: HIDDEN_TAB_NOTE }],
  ]) {
    const s = summarizeResult(op, { data });
    assert.match(s, /HIDDEN/, `${op}: REGRESSION — the hidden flag was dropped again`);
    assert.match(s, /background tabs are throttled/, `${op}: the server note was dropped`);
    assert.match(s, /SHELL/, `${op}: the content must still be returned`);
  }
});

test("`eval` gets the hidden SIGNAL even for a STRING value (the F2 hole)", () => {
  // F2's eval returned a STRING, so the old code's `typeof v === 'string'` fast path
  // stripped the note — the accident that leaked it only fired on a non-string value.
  const s = summarizeResult("eval",
    { data: { value: "WAKE-RIG-SHELL", hidden: true, note: HIDDEN_TAB_NOTE } });
  assert.match(s, /WARNING: your tab is HIDDEN/);
  assert.match(s, /WAKE-RIG-SHELL/);
});

test("`eval` is NOT auto-re-run (not idempotent) but IS told the page was already woken", async () => {
  const f = bridge();
  await run({ op: "text" }, baseEnv(), f);   // wakes the page
  f.calls.length = 0;
  const out = await run({ op: "eval", js: "document.title" }, baseEnv(), f);
  assert.deepEqual(ops(f), ["eval"], "an eval must never be silently re-executed");
  assert.match(out, /already SUCCEEDED for this page/);
  assert.ok(!AUTO_WAKE_OPS.includes("eval"));
  assert.ok(HIDDEN_SIGNAL_OPS.includes("eval"));
});

test("the banner is a PREFIX — the page content is never truncated or reordered", async () => {
  const f = bridge();
  const out = await run({ op: "text" }, baseEnv(), f);
  const [banner, blank, ...rest] = out.split("\n");
  assert.match(banner, /^\[browser-tool\]/);
  assert.equal(blank, "");
  assert.equal(rest.join("\n"), "RENDERED ANSWER");
});

// --------------------------------------------------------------------------- //
// the woke:true banner must not OVER-claim
// --------------------------------------------------------------------------- //
// `woke` is probed by reading visibilityState INSIDE the CDP attach — it proves
// the tab was un-throttled and says NOTHING about whether the SPA finished
// rendering inside the bounded settle. A heavy app needing >1.5s yields woke:true
// AND an unrendered shell.
test("the woke:true banner HEDGES on the settle and never says 'no further wake is needed'", async () => {
  const f = bridge();
  const out = await run({ op: "text" }, baseEnv(), f);
  assert.match(out, /bounded settle/);
  assert.match(out, /loading\/placeholder state, it IS one/);
  assert.doesNotMatch(out, /No further wake is needed/i,
    "the tool cannot promise the render completed — only that the tab was un-throttled");
});

test("the already-woke banner hedges the same way (same claim, same limits)", async () => {
  const f = bridge();
  await run({ op: "text" }, baseEnv(), f);
  const out = await run({ op: "text" }, baseEnv(), f);
  assert.match(out, /bounded settle/);
});

// --------------------------------------------------------------------------- //
// same-URL document replacement (invisible to the page key)
// --------------------------------------------------------------------------- //
test("a click that REPLACES the document at the SAME url re-wakes (key cannot see it)", async () => {
  const f = bridge({ replacesDocument: true });
  await run({ op: "text" }, baseEnv(), f);                      // wakes the page
  await run({ op: "click", selector: "#submit" }, baseEnv(), f); // form POST, same url
  f.calls.length = 0;

  await run({ op: "text" }, baseEnv(), f);
  assert.deepEqual(ops(f), ["text", "wake", "text"],
    "REGRESSION: a same-url document replacement must void the page verdict");
  assert.equal(f.state.url, PAGE, "…and the url genuinely did NOT change");
});

test("`key` and `eval` invalidate the page too (reload / same-url SPA remount)", async () => {
  for (const drive of [{ op: "key", key: "Enter" }, { op: "eval", js: "location.reload()" }]) {
    resetAutoWakeState();
    const f = bridge({ replacesDocument: true });
    await run({ op: "text" }, baseEnv(), f);
    await run(drive, baseEnv(), f);
    f.calls.length = 0;
    await run({ op: "text" }, baseEnv(), f);
    assert.deepEqual(ops(f), ["text", "wake", "text"], `${drive.op} must invalidate`);
  }
});

test("an eval's OWN banner is decided BEFORE its invalidation (its value predates it)", async () => {
  const f = bridge({ replacesDocument: true });
  await run({ op: "text" }, baseEnv(), f);
  const out = await run({ op: "eval", js: "location.reload()" }, baseEnv(), f);
  assert.match(out, /already SUCCEEDED for this page/,
    "the value came from the OLD, woken document — describe it against that verdict");
});

test("PAGE_INVALIDATING_OPS is exactly the document-replacing set", () => {
  assert.deepEqual([...PAGE_INVALIDATING_OPS].sort(), ["click", "eval", "key", "nav"]);
});

// --------------------------------------------------------------------------- //
// the per-tab wake budget (URL-churn backstop)
// --------------------------------------------------------------------------- //
// On a page that rewrites its url on every read (hash routing, `?t=` cache
// busters, history.replaceState on infinite scroll) every read yields a FRESH key,
// so wake-once-per-page degrades to wake-per-read: 3 bridge calls + a ~1.5s settle
// each against a 5/s + burst-20 per-instance limiter.
test("URL churn: the wake budget caps the injected traffic and says so LOUDLY", async () => {
  const f = bridge();
  const env = baseEnv({ BROWSER_AGENT_AUTO_WAKE_MAX: "3" });
  let out;
  for (let i = 0; i < 6; i++) {
    f.state.url = `${PAGE}?t=${i}`;   // a fresh page key on every single read
    f.state.awake = false;
    out = await run({ op: "text" }, env, f);
  }
  assert.equal(f.calls.filter((c) => c.op === "wake").length, 3,
    "exactly the budget — never one wake per read");
  assert.match(out, /^\[browser-tool\] WARNING:/, "past the cap it must NOT go quiet");
  assert.match(out, /wake budget/);
  assert.match(out, /3\/3 wakes used/);
  assert.match(out, /may be an unrendered shell/);
});

test("the default budget is 8 and it is per TAB, not per page", () => {
  assert.equal(AUTO_WAKE_TAB_CAP, 8);
  assert.equal(wakeCountForTab(11), 0);
  markPageWakeAttempt(11, "https://a");
  markPageWakeAttempt(11, "https://b");
  assert.equal(wakeCountForTab(11), 2);
  assert.equal(wakeCountForTab(12), 0, "budgets do not leak between tabs");
});

// --------------------------------------------------------------------------- //
// a MANUAL wake counts as the page's wake
// --------------------------------------------------------------------------- //
test("a wake the MODEL called marks the page — the next read does not wake again", async () => {
  const f = bridge();
  await run({ op: "wake" }, baseEnv(), f);
  f.calls.length = 0;
  const out = await run({ op: "text" }, baseEnv(), f);
  assert.deepEqual(ops(f), ["text"], "REGRESSION: a manual wake must not be paid for twice");
  assert.match(out, /already SUCCEEDED for this page/);
  // the recorded verdict names its provenance, so the audit/debug story is clear
  assert.match(getPageWake(4242, PAGE).detail, /called directly/);
});

test("a manual wake does NOT charge the auto-wake budget (that bounds injected traffic)", async () => {
  const f = bridge();
  await run({ op: "wake" }, baseEnv(), f);
  assert.equal(wakeCountForTab(4242), 0);
});

test("a manual wake that reported woke:false does NOT vouch for the page", async () => {
  const f = bridge({ woke: false });
  await run({ op: "wake" }, baseEnv(), f);
  f.calls.length = 0;
  const out = await run({ op: "text" }, baseEnv(), f);
  assert.deepEqual(ops(f), ["text"], "still not retried");
  assert.match(out, /WARNING/);
});

// --------------------------------------------------------------------------- //
// audit trail + bounded details
// --------------------------------------------------------------------------- //
test("the audit log distinguishes a SUCCESSFUL auto-wake from a FAILED one", async () => {
  const path = `${tmpdir()}/browser-auto-wake-audit-${process.pid}-${Math.random()}.jsonl`;
  const env = baseEnv({ BROWSER_AGENT_AUDIT: path });
  try {
    await run({ op: "text" }, env, bridge());
    resetAutoWakeState();
    await run({ op: "text" }, env, bridge({ wakeEnvelopeError: "cdp_timeout:wake" }));
    const lines = readFileSync(path, "utf8").trim().split("\n").map((l) => JSON.parse(l));
    const decisions = lines.map((l) => l.decision);
    // The injected wake AND the injected re-read are both visible as exec lines…
    assert.equal(decisions.filter((d) => d === "auto_wake_exec").length, 3,
      "wake+re-read on the success path, wake only on the failure path");
    // …and the OUTCOME is recorded, which is the whole point (a discarded failure
    // verdict is what 🔴-1 was about).
    assert.ok(decisions.includes("auto_wake_ok"));
    assert.ok(decisions.includes("auto_wake_failed"));
    const failed = lines.find((l) => l.decision === "auto_wake_failed");
    assert.match(failed.detail, /cdp_timeout:wake/);
    // Metadata only — no page content may reach the audit log.
    for (const l of lines) {
      assert.doesNotMatch(JSON.stringify(l), /SHELL \(waiting|RENDERED ANSWER/);
    }
  } finally {
    try { unlinkSync(path); } catch { /* best-effort */ }
  }
});

test("a hostile/huge server error is BOUNDED before it reaches the banner", async () => {
  const f = bridge({ wakeEnvelopeError: "E".repeat(50000) });
  const out = await run({ op: "text" }, baseEnv(), f);
  const banner = out.split("\n")[0];
  assert.ok(banner.length < 1500,
    `the banner must not be inflatable by a server-supplied error (got ${banner.length})`);
  assert.match(banner, /automatic 'wake' FAILED/);
});

test("a huge RE-READ error is bounded the same way", async () => {
  const f = bridge({ reReadError: "R".repeat(50000) });
  const out = await run({ op: "text" }, baseEnv(), f);
  assert.ok(out.split("\n")[0].length < 1500);
});

// --------------------------------------------------------------------------- //
// the per-page bookkeeping, directly
// --------------------------------------------------------------------------- //
test("pageKeyOf uses the read's url; a urlless payload degrades to a stable ''", () => {
  assert.equal(pageKeyOf({ url: PAGE }), PAGE);
  assert.equal(pageKeyOf({}), "");
  assert.equal(pageKeyOf(null), "");
  assert.equal(pageKeyOf({ url: 7 }), "");
});

test("mark/record/forget: forgetPageWakes drops the tab's VERDICTS (what a nav does)", () => {
  assert.equal(hasWokenPage(1, PAGE), false);
  markPageWakeAttempt(1, PAGE);
  assert.equal(hasWokenPage(1, PAGE), true);
  assert.equal(hasWokenPage(1, "https://other"), false, "keyed per PAGE, not per tab");
  assert.equal(hasWokenPage(2, PAGE), false, "keyed per TAB too");
  forgetPageWakes(1);
  assert.equal(hasWokenPage(1, PAGE), false);
});

test("the RESERVED verdict is a FAILURE — an interrupted wake must not read as success", () => {
  markPageWakeAttempt(1, PAGE);
  assert.deepEqual(getPageWake(1, PAGE), { ok: false, detail: "the wake did not complete" });
  recordPageWakeOutcome(1, PAGE, true, "woke:true settleMs:1500");
  assert.equal(getPageWake(1, PAGE).ok, true);
  recordPageWakeOutcome(1, PAGE, false, "boom");
  assert.deepEqual(getPageWake(1, PAGE), { ok: false, detail: "boom" });
});

test("forgetPageWakes does NOT refund the wake budget (churn navigates a lot)", () => {
  markPageWakeAttempt(3, "https://a");
  markPageWakeAttempt(3, "https://b");
  assert.equal(wakeCountForTab(3), 2);
  forgetPageWakes(3);
  assert.equal(wakeCountForTab(3), 2, "the cap is a per-RUN backstop, not per-page");
  assert.equal(hasWokenPage(3, "https://a"), false);
});

test("the per-tab page-key map is BOUNDED (a long run cannot grow it without limit)", () => {
  for (let i = 0; i < AUTO_WAKE_PAGE_CAP * 3; i++) markPageWakeAttempt(9, `https://p/${i}`);
  assert.equal(hasWokenPage(9, `https://p/${AUTO_WAKE_PAGE_CAP * 3 - 1}`), true,
    "the most recent page is always remembered");
});

test("autoWakeTabCap defaults to AUTO_WAKE_TAB_CAP; a numeric env overrides it", () => {
  assert.equal(autoWakeTabCap({}), AUTO_WAKE_TAB_CAP);
  assert.equal(autoWakeTabCap({ BROWSER_AGENT_AUTO_WAKE_MAX: "3" }), 3);
  assert.equal(autoWakeTabCap({ BROWSER_AGENT_AUTO_WAKE_MAX: "0" }), 0);
  assert.equal(autoWakeTabCap({ BROWSER_AGENT_AUTO_WAKE_MAX: "junk" }), AUTO_WAKE_TAB_CAP,
    "a junk value must fall back to the default, never to NaN (NaN >= n is false → uncapped)");
  assert.equal(autoWakeTabCap({ BROWSER_AGENT_AUTO_WAKE_MAX: "-2" }), AUTO_WAKE_TAB_CAP);
});

test("autoWakeEnabled defaults ON; only an explicit '0' turns it off", () => {
  assert.equal(autoWakeEnabled({}), true);
  assert.equal(autoWakeEnabled({ BROWSER_AGENT_AUTO_WAKE: "1" }), true);
  assert.equal(autoWakeEnabled({ BROWSER_AGENT_AUTO_WAKE: "" }), true);
  assert.equal(autoWakeEnabled({ BROWSER_AGENT_AUTO_WAKE: "0" }), false);
  assert.equal(autoWakeEnabled({ BROWSER_AGENT_AUTO_WAKE: " 0 " }), false);
});

test("hiddenNotice never recommends `activate` (the wording is load-bearing)", () => {
  const data = { hidden: true, note: HIDDEN_FALLBACK_NOTE_PROBE };
  for (const aw of [null, { state: "woke", woke: true, detail: "woke:true settleMs:1500" },
                    { state: "woke", woke: false, detail: "woke:false settleMs:1500" },
                    { state: "already-woke", woke: true, detail: "" },
                    { state: "already-failed", woke: false, detail: "wake failed: boom" },
                    { state: "capped", woke: null, detail: "8/8 wakes used" },
                    { state: "failed", woke: null, detail: "wake failed: boom" }]) {
    const s = hiddenNotice(data, aw);
    assert.ok(s.length > 0);
    assert.doesNotMatch(s, /\bactivate\b/,
      "the tool's OWN banner must never teach the model to steal the screen");
  }
});
