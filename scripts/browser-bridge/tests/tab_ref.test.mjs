// Unit tests for the `bw://` tab-reference BUILDER (extension/protocol.js).
//
// The reference is the whole wire format between the toolbar click and the CLI:
// nothing else carries it, and there is no envelope, op or server route to catch
// a mistake in it. So the format is pinned as a WHOLE NORMALISED STRING here,
// and the same literal is asserted from the consuming end in
// tests/test_browser_tab_ref.py. A change to either side alone turns one of the
// two red.
//
// The refusals matter more than the happy path. A reference is pasted onto a
// shell command line and split on `/` by a bash CLI, so every malformed field
// this builder lets through becomes a command that runs against SOME tab rather
// than none — the silent-wrong-answer shape, at the one point in this subsystem
// where there is no envelope to inspect afterwards.

import test from "node:test";
import assert from "node:assert/strict";

const { buildTabRef, tabRefKey, TAB_REF_SCHEME, TAB_REF_FIELD_RE } =
  await import("../extension/protocol.js");

// The canonical example. Also the literal in the CLI test and in SKILL.md.
const CANONICAL = "bw://workbench/main/12345";

test("the canonical reference is built byte-for-byte", () => {
  assert.equal(
    buildTabRef({ host: "workbench", label: "main", instanceId: "ignored-when-labelled", tabId: 12345 }),
    CANONICAL);
});

test("scheme constant is the prefix of what the builder emits", () => {
  assert.ok(CANONICAL.startsWith(TAB_REF_SCHEME));
});

test("an UNLABELLED profile carries the FULL auto-id, never a prefix of it", () => {
  // 🔴 This is the whole reason tabRefKey exists. server.py resolves a target
  // with `target in (inst.key, inst.instance_id)` — an EXACT match — so an
  // abbreviated id does not route and the reference would fail as
  // `unknown_instance` on its first use. The fixture id is a real UUID shape:
  // any prefix of it is a DIFFERENT string, which is what makes this test able
  // to see a truncation at all.
  const id = "9f1c7b2e-4a55-4c31-8de0-6b0f2a7c1d34";
  const ref = buildTabRef({ host: "laptop", label: "", instanceId: id, tabId: 7 });
  assert.equal(ref, `bw://laptop/${id}/7`);
  assert.ok(ref.includes(id), "the ENTIRE instance id must survive into the ref");
});

test("a label WINS over the auto-id (that is what the server keys on)", () => {
  assert.equal(tabRefKey("work", "9f1c7b2e-4a55"), "work");
  assert.equal(tabRefKey("  work  ", "9f1c7b2e-4a55"), "work", "surrounding space is trimmed");
  assert.equal(tabRefKey("", "9f1c7b2e-4a55"), "9f1c7b2e-4a55");
  assert.equal(tabRefKey("   ", "9f1c7b2e-4a55"), "9f1c7b2e-4a55",
    "a whitespace-only label is not a label");
});

// --- refusals --------------------------------------------------------------- //
const REFUSED = [
  ["an unknown host", { host: "unknown", label: "main", instanceId: "i", tabId: 1 },
   /could not identify this host/],
  ["an empty host", { host: "", label: "main", instanceId: "i", tabId: 1 },
   /could not identify this host/],
  ["a label containing a separator", { host: "workbench", label: "a/b", instanceId: "i", tabId: 1 },
   /not ref-safe/],
  ["a label containing a space", { host: "workbench", label: "my profile", instanceId: "i", tabId: 1 },
   /not ref-safe/],
  ["a label containing a quote", { host: "workbench", label: "it's", instanceId: "i", tabId: 1 },
   /not ref-safe/],
  ["a label containing a shell metachar", { host: "workbench", label: "a;rm", instanceId: "i", tabId: 1 },
   /not ref-safe/],
  ["no label and no id", { host: "workbench", label: "", instanceId: "", tabId: 1 },
   /no label and no instance id/],
  ["TAB_ID_NONE", { host: "workbench", label: "main", instanceId: "i", tabId: -1 },
   /no routable tab id/],
  ["a non-integer tab id", { host: "workbench", label: "main", instanceId: "i", tabId: 1.5 },
   /no routable tab id/],
  ["a missing tab id", { host: "workbench", label: "main", instanceId: "i" },
   /no routable tab id/],
  ["a string tab id", { host: "workbench", label: "main", instanceId: "i", tabId: "12345" },
   /no routable tab id/],
];

for (const [name, args, pattern] of REFUSED) {
  test(`REFUSES ${name}`, () => {
    assert.throws(() => buildTabRef(args), pattern);
  });
}

test("no arguments at all is a refusal, not a reference", () => {
  assert.throws(() => buildTabRef(), /could not identify this host/);
});

// A separator inside a field is the failure the whole character class exists to
// prevent; assert on the CONSEQUENCE, not just that the class rejects it.
test("a label with a separator cannot produce a ref that splits into 3 fields", () => {
  assert.throws(() => buildTabRef(
    { host: "workbench", label: "a/b", instanceId: "i", tabId: 12 }), /not ref-safe/);
  // and the class itself agrees, so the CLI's mirror of it has something to match
  assert.equal(TAB_REF_FIELD_RE.test("a/b"), false);
  assert.equal(TAB_REF_FIELD_RE.test("main"), true);
  assert.equal(TAB_REF_FIELD_RE.test("9f1c7b2e-4a55-4c31-8de0-6b0f2a7c1d34"), true);
});
