// Tests for the pure extension protocol logic (no chrome.* runtime needed).
//
// Covers: the op-set contract (MUST mirror server.py ALLOWED_OPS), command
// validation, result/error envelope shapes, and the reconnect backoff curve.
// The chrome.* glue in service_worker.js genuinely needs a real browser and is
// covered by the manual checklist in ../extension/README.md.
//
// Run: nix-shell -p nodejs --run "node --test scripts/browser-bridge/tests/protocol.test.mjs"
import test from "node:test";
import assert from "node:assert/strict";
import {
  ALLOWED_OPS, REQUIRED_FIELDS, validateCommand, resultEnvelope, errorEnvelope,
  nextBackoffMs, compileEval,
  pollRequestPayload, pollHeaders, resultWithInstance,
  classifyPollStatus, POLL_COMMAND, POLL_IDLE, POLL_SUPERSEDED,
  POLL_UNAUTHORIZED, SUPERSEDE_BACKOFF_MS,
  capHeaderValue, MAX_HEADER_VALUE_CHARS,
  normalizeText, TEXT_MAX_BYTES_DEFAULT,
  isTransientCaptureError, isCaptureQuotaError, captureWithRetry,
  waitForCaptureReady, screenshotWithRestore, CAPTURE_MAX_ATTEMPTS,
  CAPTURE_RETRY_BASE_MS, CAPTURE_QUOTA_WAIT_MS, CAPTURE_SETTLE_MS,
  isOcclusionCaptureError, mapCaptureFailure, SCREENSHOT_NOT_VISIBLE_MESSAGE,
} from "../extension/protocol.js";

test("op set mirrors the server contract", () => {
  // If this changes, server.py ALLOWED_OPS must change in lockstep. `open`/`close`
  // back the per-session tab-isolation model (`release` is server-side only, so
  // it is deliberately NOT in the shared op set). `text` is the cheap-read op.
  assert.deepEqual(
    [...ALLOWED_OPS].sort(),
    ["close", "eval", "getHtml", "nav", "open", "screenshot", "tabs", "text"],
  );
});

test("validateCommand accepts the cheap-read `text` op (bare + selector)", () => {
  // `text` has no required field — a bare op validates; selector/maxBytes are
  // optional command fields the server passes through to the extension.
  assert.deepEqual(validateCommand({ op: "text" }), { ok: true });
  assert.deepEqual(validateCommand({ op: "text", selector: "main", maxBytes: 1024 }),
    { ok: true });
  assert.deepEqual(validateCommand({ op: "text", tabId: 9 }), { ok: true });
});

test("validateCommand accepts the tab-isolation ops (open/close)", () => {
  // open takes an OPTIONAL url; close takes no skill-supplied field (the server
  // injects the owned tabId) — so a bare op validates for both.
  assert.deepEqual(validateCommand({ op: "open" }), { ok: true });
  assert.deepEqual(validateCommand({ op: "open", url: "https://x.test" }), { ok: true });
  assert.deepEqual(validateCommand({ op: "close" }), { ok: true });
  // A server-injected tabId on a tab-scoped op is accepted (never rejected).
  assert.deepEqual(validateCommand({ op: "getHtml", tabId: 42 }), { ok: true });
  assert.deepEqual(validateCommand({ op: "nav", url: "https://x", tabId: 7 }), { ok: true });
});

test("open/close result envelopes carry their tab payloads", () => {
  // The wire shapes the SW returns for the new ops.
  assert.deepEqual(resultEnvelope("cid", { tabId: 101, url: "about:blank" }),
    { id: "cid", ok: true, data: { tabId: 101, url: "about:blank" } });
  assert.deepEqual(resultEnvelope("cid", { closed: 101 }),
    { id: "cid", ok: true, data: { closed: 101 } });
});

test("validateCommand accepts a bare op", () => {
  assert.deepEqual(validateCommand({ op: "tabs" }), { ok: true });
  assert.deepEqual(validateCommand({ op: "getHtml" }), { ok: true });
});

test("validateCommand enforces required fields", () => {
  assert.equal(validateCommand({ op: "eval" }).error, "missing_field:js");
  assert.equal(validateCommand({ op: "nav" }).error, "missing_field:url");
  assert.deepEqual(validateCommand({ op: "eval", js: "1+1" }), { ok: true });
  assert.deepEqual(validateCommand({ op: "nav", url: "https://x" }), { ok: true });
});

test("validateCommand rejects unknown op / non-object", () => {
  assert.equal(validateCommand({ op: "rm_rf" }).error, "unknown_op");
  assert.equal(validateCommand(null).error, "body_not_object");
  assert.equal(validateCommand("nope").error, "body_not_object");
});

test("REQUIRED_FIELDS matches the ops that need args", () => {
  assert.deepEqual(REQUIRED_FIELDS.eval, ["js"]);
  assert.deepEqual(REQUIRED_FIELDS.nav, ["url"]);
});

test("result / error envelopes carry the id", () => {
  assert.deepEqual(resultEnvelope("abc", { html: "x" }),
    { id: "abc", ok: true, data: { html: "x" } });
  const e = errorEnvelope("abc", new Error("boom"));
  assert.equal(e.id, "abc");
  assert.equal(e.ok, false);
  assert.match(e.error, /boom/);
});

test("backoff is exponential and capped", () => {
  assert.equal(nextBackoffMs(0), 1000);
  assert.equal(nextBackoffMs(1), 2000);
  assert.equal(nextBackoffMs(3), 8000);
  assert.equal(nextBackoffMs(99), 30000); // capped
});

// --- compileEval: the expression/statement decision must never double-run a
// side effect (regression for the old try/catch-then-fallback executor) ------ //
test("compileEval evaluates a bare expression and returns its value", () => {
  assert.equal(compileEval("2 * 21")(), 42);
  assert.equal(compileEval("[1,2,3].length")(), 3);
});

test("compileEval falls back to the statement form when the expression can't parse", () => {
  // `return (const x = 41; …)` is a *construction* SyntaxError → statement form.
  globalThis.__ce_r = 0;
  const fn = compileEval("const x = 41; globalThis.__ce_r = x + 1;");
  fn();
  assert.equal(globalThis.__ce_r, 42);
  delete globalThis.__ce_r;
});

test("compileEval runs an expression's side effect EXACTLY ONCE even when it throws at runtime", () => {
  // Expression parses fine, so no fallback — the runtime throw must propagate
  // and the side effect must fire only once (the bug this fix closes).
  globalThis.__ce_hits = 0;
  const src = "(globalThis.__ce_hits++, (function () { throw new Error('boom'); })())";
  const fn = compileEval(src);          // construction only — no side effect yet
  assert.equal(globalThis.__ce_hits, 0, "construction must not execute the body");
  assert.throws(() => fn(), /boom/);     // one call → one throw
  assert.equal(globalThis.__ce_hits, 1, "side effect must run exactly once");
  delete globalThis.__ce_hits;
});

test("compileEval routes a construction SyntaxError to the statement form (injectable ctor)", () => {
  const calls = [];
  const fakeCtor = (body) => {
    calls.push(body);
    if (body.startsWith("return (")) {
      throw new SyntaxError("cannot wrap a statement as an expression");
    }
    return () => "stmt";
  };
  const fn = compileEval("var x = 1;", fakeCtor);
  assert.equal(fn(), "stmt");
  assert.deepEqual(calls, ["return (var x = 1;)", "var x = 1;"]);
});

test("compileEval propagates a non-SyntaxError construction failure without falling back", () => {
  const calls = [];
  const fakeCtor = (body) => {
    calls.push(body);
    throw new RangeError("nope");   // not a SyntaxError → must NOT fall back
  };
  assert.throws(() => compileEval("whatever", fakeCtor), RangeError);
  assert.deepEqual(calls, ["return (whatever)"], "must not try the statement form");
});

// --- multi-instance registration payload shape (mirrors server.py) ---------- //
test("pollRequestPayload carries a stable {instanceId,label} shape", () => {
  assert.deepEqual(pollRequestPayload("uuid-1", "work"),
    { instanceId: "uuid-1", label: "work" });
  // Empty/absent label normalises to "" (the server then keys on the auto-id).
  assert.deepEqual(pollRequestPayload("uuid-2", ""),
    { instanceId: "uuid-2", label: "" });
  assert.deepEqual(pollRequestPayload("uuid-3"),
    { instanceId: "uuid-3", label: "" });
});

test("pollHeaders identifies the instance and URL-encodes the label", () => {
  const bare = pollHeaders("uuid-1", "");
  assert.deepEqual(bare, { "X-Bridge-Instance-Id": "uuid-1" });
  // A label with a space/unicode must be percent-encoded (header values are ASCII).
  const labelled = pollHeaders("uuid-1", "my work");
  assert.equal(labelled["X-Bridge-Instance-Id"], "uuid-1");
  assert.equal(labelled["X-Bridge-Label"], "my%20work");
  assert.equal(labelled["X-Bridge-Active-Url"], undefined);
});

test("pollHeaders includes an encoded active tab when provided", () => {
  const h = pollHeaders("uuid-1", "work",
    { url: "https://x.test/a b", title: "Hi & Bye" });
  assert.equal(h["X-Bridge-Active-Url"], encodeURIComponent("https://x.test/a b"));
  assert.equal(h["X-Bridge-Active-Title"], encodeURIComponent("Hi & Bye"));
});

// --- poll-response classification: the distinct supersede signal ------------ //
test("classifyPollStatus distinguishes command / idle / superseded / auth", () => {
  assert.equal(classifyPollStatus(200), POLL_COMMAND);
  assert.equal(classifyPollStatus(204), POLL_IDLE);
  // 409 must be its OWN kind — NOT lumped with idle 204 (that was the livelock).
  assert.equal(classifyPollStatus(409), POLL_SUPERSEDED);
  assert.notEqual(POLL_SUPERSEDED, POLL_IDLE);
  assert.equal(classifyPollStatus(401), POLL_UNAUTHORIZED);
  // Anything else is a transport error → generic backoff, not a poll signal.
  assert.equal(classifyPollStatus(500), null);
  assert.equal(classifyPollStatus(503), null);
});

test("SUPERSEDE_BACKOFF_MS is a hard back-off, not a hot loop", () => {
  // Must be far above the sub-millisecond re-poll cadence that livelocks two
  // same-label profiles; at/above the transport backoff cap is comfortably safe.
  assert.ok(SUPERSEDE_BACKOFF_MS >= 10000,
    `SUPERSEDE_BACKOFF_MS too small: ${SUPERSEDE_BACKOFF_MS}`);
});

// --- Fix 2: active-tab header length cap ------------------------------------ //
test("capHeaderValue truncates only when over the cap", () => {
  assert.equal(capHeaderValue("short"), "short");
  assert.equal(capHeaderValue("x".repeat(3000)).length, MAX_HEADER_VALUE_CHARS);
  assert.equal(capHeaderValue(null), "");
  assert.equal(capHeaderValue(undefined), "");
});

test("pollHeaders caps a pathological url/title before encoding", () => {
  const bigUrl = "https://x.test/" + "a".repeat(5000);
  const bigTitle = "T".repeat(5000);
  const h = pollHeaders("uuid-1", "", { url: bigUrl, title: bigTitle });
  // Decoded length is bounded so the header line can't exceed http.server's limit.
  assert.equal(decodeURIComponent(h["X-Bridge-Active-Url"]).length, MAX_HEADER_VALUE_CHARS);
  assert.equal(decodeURIComponent(h["X-Bridge-Active-Title"]).length, MAX_HEADER_VALUE_CHARS);
  // Still header-injection-safe: percent-encoded, no raw CR/LF survives.
  assert.ok(!/[\r\n]/.test(h["X-Bridge-Active-Url"]));
  assert.ok(!/[\r\n]/.test(h["X-Bridge-Active-Title"]));
});

test("resultWithInstance stamps the instanceId onto the envelope", () => {
  const env = resultEnvelope("cid-1", { html: "x" });
  const stamped = resultWithInstance(env, "uuid-9");
  assert.equal(stamped.instanceId, "uuid-9");
  assert.equal(stamped.id, "cid-1");
  assert.equal(stamped.ok, true);
  assert.deepEqual(stamped.data, { html: "x" });
  // Original envelope is not mutated.
  assert.equal(env.instanceId, undefined);
});

// --- `text` op: innerText normalization + byte cap (pure logic) ------------- //
test("normalizeText collapses blank-line runs and trims", () => {
  // 3+ consecutive newlines collapse to ONE blank line; trailing per-line spaces
  // are stripped; the whole thing is trimmed. CRLF is normalized to LF.
  const { text, truncated } = normalizeText("  hi  \n\n\n\n\nthere  \r\n\r\n\r\n end  \n\n");
  assert.equal(text, "hi\n\nthere\n\n end");
  assert.equal(truncated, 0);
});

test("normalizeText leaves short text unchanged (no truncation note)", () => {
  const { text, truncated } = normalizeText("short body", 32 * 1024);
  assert.equal(text, "short body");
  assert.equal(truncated, 0);
  assert.ok(!text.includes("truncated"));
});

test("normalizeText truncates over the byte cap and appends the note", () => {
  const raw = "x".repeat(1000);
  const { text, truncated } = normalizeText(raw, 100);
  assert.ok(text.startsWith("x".repeat(100)), "keeps a 100-byte prefix");
  assert.match(text, /\n…\[truncated 900 bytes\]$/);
  assert.equal(truncated, 900);
});

test("normalizeText byte-cap never splits a multi-byte char", () => {
  // 'é' is 2 UTF-8 bytes. A cap of 3 must keep exactly 1 'é' (2 bytes), not a
  // half char. The kept prefix must still decode cleanly.
  const raw = "é".repeat(10);           // 20 UTF-8 bytes
  const { text, truncated } = normalizeText(raw, 3);
  const body = text.split("\n…")[0];    // the kept prefix, before the note
  assert.equal(body, "é");              // 2 bytes kept (≤3), not a split char
  assert.equal(new TextEncoder().encode(body).length, 2);
  assert.equal(truncated, 18);
});

test("normalizeText with a zero/undefined cap does not truncate", () => {
  const raw = "y".repeat(5000);
  assert.equal(normalizeText(raw, 0).truncated, 0);
  assert.equal(normalizeText(raw, 0).text.length, 5000);
});

test("TEXT_MAX_BYTES_DEFAULT is the documented 32 KB default", () => {
  assert.equal(TEXT_MAX_BYTES_DEFAULT, 32 * 1024);
});

// --- screenshot: settle + retry decision logic (background-tab robustness) --- //
// A never-sleep clock/sleep so the retry + settle loops run instantly under test.
const noSleep = async () => {};

test("isTransientCaptureError classifies the readback race as retry-worthy", () => {
  // The exact observed message, its bare form, and any readback phrasing → true.
  assert.equal(isTransientCaptureError(new Error("Failed to capture tab: image readback failed")), true);
  assert.equal(isTransientCaptureError("image readback failed"), true);
  assert.equal(isTransientCaptureError("GPU readback error"), true);
  // Case-insensitive.
  assert.equal(isTransientCaptureError("IMAGE READBACK FAILED"), true);
});

test("isTransientCaptureError classifies the per-second capture quota as retry-worthy", () => {
  // The observed live failure (PR #181 regression): a too-fast retry trips the
  // ~2/sec captureVisibleTab quota. It is TRANSIENT — waiting out the window
  // recovers it, so it must be retried (with the spaced backoff below).
  assert.equal(isTransientCaptureError(new Error(
    "This request exceeds the MAX_CAPTURE_VISIBLE_TAB_CALLS_PER_SECOND quota.")), true);
  assert.equal(isTransientCaptureError(
    "This request exceeds the MAX_CAPTURE_VISIBLE_TAB_CALLS_PER_SECOND quota."), true);
  // Case-insensitive (Chrome could vary the surrounding prose).
  assert.equal(isTransientCaptureError(
    "exceeds the max_capture_visible_tab_calls_per_second quota"), true);
});

test("isCaptureQuotaError isolates the quota error from other transient errors", () => {
  // Only the quota error is a quota error — a plain readback race is NOT (it
  // takes the shorter backoff, not the full quota-window wait).
  assert.equal(isCaptureQuotaError(new Error(
    "This request exceeds the MAX_CAPTURE_VISIBLE_TAB_CALLS_PER_SECOND quota.")), true);
  assert.equal(isCaptureQuotaError("IMAGE readback FAILED as MAX_CAPTURE_VISIBLE_TAB_CALLS_PER_SECOND"), true);
  assert.equal(isCaptureQuotaError("image readback failed"), false);
  assert.equal(isCaptureQuotaError("Cannot access contents of the page"), false);
  assert.equal(isCaptureQuotaError(""), false);
  assert.equal(isCaptureQuotaError(null), false);
});

test("isTransientCaptureError treats permanent/unknown errors as NON-transient (fail closed)", () => {
  // A permission/URL error must NOT be retried (it would just burn cmd_timeout).
  assert.equal(isTransientCaptureError(new Error(
    "Cannot access contents of the page. Extension manifest must request permission")), false);
  assert.equal(isTransientCaptureError("owned_tab_gone"), false);
  // Empty / nullish / unclassifiable → false.
  assert.equal(isTransientCaptureError(""), false);
  assert.equal(isTransientCaptureError(null), false);
  assert.equal(isTransientCaptureError(undefined), false);
});

test("capture spacing constants respect Chrome's ~2/sec captureVisibleTab quota", () => {
  // The core regression guard as a constant invariant: the FIRST retry (base·1)
  // and the quota-window wait must BOTH be ≥~600ms — faster than that re-trips
  // MAX_CAPTURE_VISIBLE_TAB_CALLS_PER_SECOND (the ~2/sec ≈ 500ms window).
  assert.ok(CAPTURE_RETRY_BASE_MS >= 600, `first-retry gap ${CAPTURE_RETRY_BASE_MS}ms must be ≥600ms`);
  assert.ok(CAPTURE_QUOTA_WAIT_MS >= 600, `quota wait ${CAPTURE_QUOTA_WAIT_MS}ms must be ≥600ms`);
  // And the whole retry budget stays well under the server cmd_timeout (20s):
  // worst case = base·1 + base·2 (+ settle) with 3 attempts.
  const worst = CAPTURE_RETRY_BASE_MS * 1 + CAPTURE_RETRY_BASE_MS * 2;
  assert.ok(worst < 20000, `retry budget ${worst}ms must stay under the 20s cmd_timeout`);
});

test("captureWithRetry: a transient failure then success resolves (retries, then wins)", async () => {
  let calls = 0;
  const sleeps = [];
  const capture = async () => {
    calls++;
    if (calls < 2) throw new Error("image readback failed");
    return "data:png;ok";
  };
  const out = await captureWithRetry(capture, { sleep: (ms) => { sleeps.push(ms); } });
  assert.equal(out, "data:png;ok");
  assert.equal(calls, 2, "one retry after the transient failure");
  assert.deepEqual(sleeps, [CAPTURE_RETRY_BASE_MS], "one spaced backoff before the retry");
  // The regression guard: the retry is spaced ≥ the quota window, so a 2nd
  // capture never fires inside the ~2/sec captureVisibleTab quota window.
  assert.ok(sleeps[0] >= 600, `retry gap ${sleeps[0]}ms must be ≥600ms (quota window)`);
});

test("captureWithRetry: every retry gap is ≥ the quota window (core spacing regression)", async () => {
  // Persistent readback race → drains the whole attempt budget. Assert EACH gap
  // between capture attempts is ≥~600ms — proving no two captures can land in
  // one ~2/sec quota window (the PR #181 bug fired a 2nd capture 150ms later).
  const sleeps = [];
  const capture = async () => { throw new Error("image readback failed"); };
  await assert.rejects(
    () => captureWithRetry(capture, { sleep: (ms) => { sleeps.push(ms); } }),
    /image readback failed/);
  // maxAttempts=3 → two inter-attempt gaps.
  assert.equal(sleeps.length, CAPTURE_MAX_ATTEMPTS - 1);
  for (const gap of sleeps) {
    assert.ok(gap >= 600, `every capture-attempt gap (${gap}ms) must be ≥600ms`);
  }
  // Linear spacing: base·1 then base·2.
  assert.deepEqual(sleeps, [CAPTURE_RETRY_BASE_MS, CAPTURE_RETRY_BASE_MS * 2]);
});

test("captureWithRetry: a quota error waits a FULL quota window, then the retry succeeds", async () => {
  // The exact live failure: attempt 1 trips the ~2/sec quota; the retry must
  // wait out the window (≥~1s) so it doesn't immediately re-trip it, then win.
  let calls = 0;
  const sleeps = [];
  const capture = async () => {
    calls++;
    if (calls < 2) throw new Error(
      "This request exceeds the MAX_CAPTURE_VISIBLE_TAB_CALLS_PER_SECOND quota.");
    return "data:png;ok";
  };
  const out = await captureWithRetry(capture, { sleep: (ms) => { sleeps.push(ms); } });
  assert.equal(out, "data:png;ok");
  assert.equal(calls, 2, "one retry after the quota error");
  assert.deepEqual(sleeps, [CAPTURE_QUOTA_WAIT_MS], "waited a full quota window before retrying");
  assert.ok(sleeps[0] >= 1000, `quota wait ${sleeps[0]}ms must be ≥~1s window`);
});

test("captureWithRetry: a persistent transient error fails after exactly N attempts", async () => {
  let calls = 0;
  const capture = async () => { calls++; throw new Error("image readback failed"); };
  await assert.rejects(() => captureWithRetry(capture, { sleep: noSleep }), /image readback failed/);
  assert.equal(calls, CAPTURE_MAX_ATTEMPTS, "gives up after the bounded attempt budget");
});

test("captureWithRetry: a NON-transient error propagates immediately (no retries)", async () => {
  let calls = 0;
  const capture = async () => { calls++; throw new Error("Cannot access contents of the page"); };
  await assert.rejects(() => captureWithRetry(capture, { sleep: noSleep }), /Cannot access/);
  assert.equal(calls, 1, "a permanent error is not retried");
});

test("captureWithRetry: first-try success does not sleep or retry", async () => {
  let calls = 0, slept = 0;
  const out = await captureWithRetry(async () => { calls++; return "ok"; },
    { sleep: () => { slept++; } });
  assert.equal(out, "ok");
  assert.equal(calls, 1);
  assert.equal(slept, 0);
});

test("waitForCaptureReady polls until 'complete', then applies the paint settle", async () => {
  const statuses = ["loading", "loading", "complete"];
  let i = 0;
  const sleeps = [];
  await waitForCaptureReady(async () => statuses[i++],
    { sleep: (ms) => { sleeps.push(ms); }, pollMs: 100, settleMs: 150, now: () => 0 });
  // Two poll waits (while loading) + one final settle wait.
  assert.deepEqual(sleeps, [100, 100, 150]);
  assert.equal(i, 3, "stopped polling as soon as it read 'complete'");
});

test("waitForCaptureReady applies a real paint settle by DEFAULT (first-capture success → no retry)", async () => {
  // The paint settle exists so the FIRST capture usually succeeds — dodging a
  // retry (hence any quota risk). Lock the default settle at a meaningful ≥250ms
  // (Chrome fires 'complete' before first paint) and prove it is actually waited.
  assert.ok(CAPTURE_SETTLE_MS >= 250, `default paint settle ${CAPTURE_SETTLE_MS}ms must be ≥250ms`);
  const sleeps = [];
  await waitForCaptureReady(async () => "complete",
    { sleep: (ms) => { sleeps.push(ms); }, now: () => 0 });
  assert.deepEqual(sleeps, [CAPTURE_SETTLE_MS], "already-complete tab still waits the default settle");
});

test("waitForCaptureReady stops at the timeout even if never 'complete' (no hang)", async () => {
  let t = 0;                                  // fake monotonic clock
  const now = () => t;
  const sleeps = [];
  await waitForCaptureReady(async () => "loading", {
    now, pollMs: 100, settleMs: 0, timeoutMs: 250,
    sleep: (ms) => { sleeps.push(ms); t += ms; },   // advance the clock per sleep
  });
  // Polls at t=0,100,200 (each <250 → sleep); at t=300≥250 it bails — bounded,
  // never hangs on a stuck tab.
  assert.deepEqual(sleeps, [100, 100, 100]);
});

test("waitForCaptureReady tolerates a getStatus that throws (treats as not-ready)", async () => {
  let t = 0;
  await assert.doesNotReject(() => waitForCaptureReady(
    async () => { throw new Error("owned_tab_gone"); },
    { now: () => t, pollMs: 50, settleMs: 0, timeoutMs: 100,
      sleep: (ms) => { t += ms; } }));
});

// --- screenshotWithRestore: activate→capture→restore orchestration ---------- //
function spyDeps(overrides = {}) {
  const log = [];
  const deps = {
    isActive: false,
    targetId: 42,
    capture: async () => { log.push("capture"); return "data:png"; },
    getPrevActiveId: async () => { log.push("getPrev"); return 7; },
    activate: async (id) => { log.push(`activate:${id}`); },
    waitReady: async () => { log.push("wait"); },
    restore: async (id) => { log.push(`restore:${id}`); },
    ...overrides,
  };
  return { deps, log };
}

test("screenshotWithRestore (visible tab): captures with NO activate/restore flicker", async () => {
  const { deps, log } = spyDeps({ isActive: true });
  const out = await screenshotWithRestore(deps);
  assert.equal(out, "data:png");
  assert.deepEqual(log, ["capture"], "already-visible → direct capture only");
});

test("screenshotWithRestore (background tab): activate→wait→capture→restore in order", async () => {
  const { deps, log } = spyDeps();
  const out = await screenshotWithRestore(deps);
  assert.equal(out, "data:png");
  assert.deepEqual(log, ["getPrev", "activate:42", "wait", "capture", "restore:7"]);
});

test("screenshotWithRestore restores the previous tab even when capture FAILS", async () => {
  const { deps, log } = spyDeps({
    capture: async () => { log.push("capture"); throw new Error("image readback failed"); },
  });
  await assert.rejects(() => screenshotWithRestore(deps), /image readback failed/);
  // The restore MUST still run on the failure path (finally), and the original
  // error is what propagates.
  assert.deepEqual(log, ["getPrev", "activate:42", "wait", "capture", "restore:7"]);
});

test("screenshotWithRestore does not restore when there was no previous tab", async () => {
  const { deps, log } = spyDeps({ getPrevActiveId: async () => { log.push("getPrev"); return null; } });
  await screenshotWithRestore(deps);
  assert.ok(!log.some((l) => l.startsWith("restore")), "nothing to restore → no restore call");
});

test("screenshotWithRestore does not restore when the previous tab IS the target", async () => {
  const { deps, log } = spyDeps({ getPrevActiveId: async () => { log.push("getPrev"); return 42; } });
  await screenshotWithRestore(deps);
  assert.ok(!log.some((l) => l.startsWith("restore")), "prev===target → no needless restore");
});

test("screenshotWithRestore swallows a restore failure but returns the capture result", async () => {
  const { deps } = spyDeps({ restore: async () => { throw new Error("restore boom"); } });
  // A failing restore must NOT mask the successful capture.
  const out = await screenshotWithRestore(deps);
  assert.equal(out, "data:png");
});

// --- exhausted-retry error mapping: the fundamental captureVisibleTab/i3 limit -- //
// captureVisibleTab needs an ON-SCREEN composited tab. On i3 an owned/background
// tab's window may never be raised, so a readback error PERSISTS past every spaced
// retry. Once exhausted, that must surface as a CLEAR, actionable message — not the
// opaque "image readback failed" — while a genuinely transient readback (recovered
// by a retry) and the #182 quota error keep their existing behaviour.

test("isOcclusionCaptureError matches a persistent readback failure but NOT the quota error", () => {
  assert.equal(isOcclusionCaptureError(new Error("Failed to capture tab: image readback failed")), true);
  assert.equal(isOcclusionCaptureError("image readback failed"), true);
  assert.equal(isOcclusionCaptureError("GPU readback error"), true);
  assert.equal(isOcclusionCaptureError("IMAGE READBACK FAILED"), true); // case-insensitive
  // The per-second quota error is its OWN thing (#182) — never re-mapped to occlusion,
  // even when a message happens to mention both (quota wins).
  assert.equal(isOcclusionCaptureError(new Error(
    "This request exceeds the MAX_CAPTURE_VISIBLE_TAB_CALLS_PER_SECOND quota.")), false);
  assert.equal(isOcclusionCaptureError(
    "IMAGE readback FAILED as MAX_CAPTURE_VISIBLE_TAB_CALLS_PER_SECOND"), false);
  // Permanent/unknown/empty errors are not occlusion errors either.
  assert.equal(isOcclusionCaptureError("Cannot access contents of the page"), false);
  assert.equal(isOcclusionCaptureError("owned_tab_gone"), false);
  assert.equal(isOcclusionCaptureError(""), false);
  assert.equal(isOcclusionCaptureError(null), false);
  assert.equal(isOcclusionCaptureError(undefined), false);
});

test("SCREENSHOT_NOT_VISIBLE_MESSAGE is actionable and points at the working ops", () => {
  // The message must be human/agent-actionable: name the cause AND the alternative.
  assert.match(SCREENSHOT_NOT_VISIBLE_MESSAGE, /not visible on-screen/);
  assert.match(SCREENSHOT_NOT_VISIBLE_MESSAGE, /background or occluded/);
  assert.match(SCREENSHOT_NOT_VISIBLE_MESSAGE, /captureVisibleTab can only capture the foreground tab/);
  assert.match(SCREENSHOT_NOT_VISIBLE_MESSAGE, /'text'\/'html'\/'eval'/);
  // It is NOT the raw chrome error.
  assert.doesNotMatch(SCREENSHOT_NOT_VISIBLE_MESSAGE, /image readback failed/i);
});

test("mapCaptureFailure: a persistent readback → the actionable not-visible message", () => {
  assert.equal(mapCaptureFailure(new Error("Failed to capture tab: image readback failed")),
    SCREENSHOT_NOT_VISIBLE_MESSAGE);
  assert.equal(mapCaptureFailure("image readback failed"), SCREENSHOT_NOT_VISIBLE_MESSAGE);
});

test("mapCaptureFailure: the quota error passes through UNCHANGED (#182 stays)", () => {
  const quota = "This request exceeds the MAX_CAPTURE_VISIBLE_TAB_CALLS_PER_SECOND quota.";
  assert.equal(mapCaptureFailure(new Error(quota)), quota);
  assert.equal(mapCaptureFailure(quota), quota);
});

test("mapCaptureFailure: permission / owned_tab_gone errors pass through UNCHANGED", () => {
  assert.equal(mapCaptureFailure(new Error("Cannot access contents of the page")),
    "Cannot access contents of the page");
  assert.equal(mapCaptureFailure("owned_tab_gone"), "owned_tab_gone");
});

// Mirror the SW screenshot op: capture wrapped in captureWithRetry inside
// screenshotWithRestore, and the exhausted error mapped in the op's catch. This
// proves the DECISION path end-to-end (no chrome.* needed) — the piece that turns
// a real occluded-tab capture into the clear message.
async function screenshotOp(deps, retryOpts = {}) {
  try {
    return await screenshotWithRestore({
      ...deps,
      capture: () => captureWithRetry(deps.capture, { sleep: noSleep, ...retryOpts }),
    });
  } catch (e) {
    throw new Error(mapCaptureFailure(e));
  }
}

test("screenshot op: persistent readback (retries exhausted) → the actionable not-visible error", async () => {
  let calls = 0;
  const { deps } = spyDeps({
    capture: async () => { calls++; throw new Error("Failed to capture tab: image readback failed"); },
  });
  await assert.rejects(() => screenshotOp(deps), (e) => {
    assert.equal(e.message, SCREENSHOT_NOT_VISIBLE_MESSAGE, "maps to the actionable message");
    assert.doesNotMatch(e.message, /image readback failed/, "not the raw chrome error");
    return true;
  });
  // It only claims "unavailable" AFTER the full retry budget is spent.
  assert.equal(calls, CAPTURE_MAX_ATTEMPTS, "exhausted every retry before giving up");
});

test("screenshot op: a transient readback that a retry recovers still SUCCEEDS (no premature 'unavailable')", async () => {
  let calls = 0;
  const { deps } = spyDeps({
    capture: async () => {
      calls++;
      if (calls < 2) throw new Error("image readback failed"); // one transient miss
      return "data:png;ok";
    },
  });
  const out = await screenshotOp(deps);
  assert.equal(out, "data:png;ok", "the spaced retry wins → real screenshot, not the error");
  assert.equal(calls, 2, "recovered on the retry");
});

test("screenshot op: a persistent quota error surfaces the QUOTA message, not the not-visible one", async () => {
  const quota = "This request exceeds the MAX_CAPTURE_VISIBLE_TAB_CALLS_PER_SECOND quota.";
  const { deps } = spyDeps({ capture: async () => { throw new Error(quota); } });
  await assert.rejects(() => screenshotOp(deps), (e) => {
    assert.equal(e.message, quota, "quota error is preserved (#182)");
    assert.notEqual(e.message, SCREENSHOT_NOT_VISIBLE_MESSAGE);
    return true;
  });
});
