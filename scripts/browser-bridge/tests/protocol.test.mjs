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
  nextBackoffMs,
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
