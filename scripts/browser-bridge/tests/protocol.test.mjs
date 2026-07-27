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
} from "../extension/protocol.js";

test("op set mirrors the server contract", () => {
  // If this changes, server.py ALLOWED_OPS must change in lockstep.
  assert.deepEqual(
    [...ALLOWED_OPS].sort(),
    ["eval", "getHtml", "nav", "screenshot", "tabs"],
  );
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
