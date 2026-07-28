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
} from "../extension/protocol.js";

test("op set mirrors the server contract", () => {
  // If this changes, server.py ALLOWED_OPS must change in lockstep. `open`/`close`
  // back the per-session tab-isolation model (`release` is server-side only, so
  // it is deliberately NOT in the shared op set).
  assert.deepEqual(
    [...ALLOWED_OPS].sort(),
    ["close", "eval", "getHtml", "nav", "open", "screenshot", "tabs"],
  );
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
