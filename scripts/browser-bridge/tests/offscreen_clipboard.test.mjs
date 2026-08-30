// The OFFSCREEN CLIPBOARD SEAM — the only code path in this subsystem with no
// wire surface at all, and (before this file) no coverage either.
//
// WHY THIS FILE EXISTS. An adversarial audit of PR #1063 mutated the offscreen
// clipboard writer eight ways and **seven survived** the entire browser-bridge
// suite — 869 pytest cases and 549 node tests, with the build marker regenerated
// each time so a kill would have been behavioural:
//
//   * offscreen.js's TARGET renamed          → nobody answers the worker  SURVIVED
//   * the execCommand return value ignored   → ✓ over an empty clipboard  SURVIVED
//   * the textarea never select()ed          → copies nothing            SURVIVED
//   * switched to navigator.clipboard        → rejects every time        SURVIVED
//   * the `offscreen` permission removed     → createDocument throws     SURVIVED
//   * offscreen.html stops loading its js    → no listener exists        SURVIVED
//   * offscreen.html drops the #sink textarea→ nothing to select         SURVIVED
//
// The reason is structural, not an oversight of one assertion: `action_click`
// tests the WORKER against a mocked chrome, and its mock answers the clipboard
// message itself. Nothing had ever loaded `offscreen.js`. So this file drives
// the real listener, and pins the four cross-file literals that only another
// file can honour — a target string, a page url, a permission list, and the
// script/element the page must contain.
//
// 🔴 THE PERMISSION IS PART OF THE CODE PATH. `document.execCommand("copy")` is
// allowed without `clipboardWrite` ONLY inside a short-lived handler for a user
// action. This copy runs after config(), a tab query, a NETWORK /whoami round
// trip and createDocument(), in a document that never had transient activation —
// so the permission is load-bearing, and without it execCommand returns FALSE
// rather than throwing. That is a feature that ships inert while every test is
// green, which is exactly why it is asserted here and not left to prose.

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const EXT = join(dirname(fileURLToPath(import.meta.url)), "..", "extension");
const read = (f) => readFileSync(join(EXT, f), "utf8");

const manifest = JSON.parse(read("manifest.json"));
const offscreenHtml = read("offscreen.html");
const offscreenSrc = read("offscreen.js");

// --------------------------------------------------------------------------- //
// The cross-file literals
// --------------------------------------------------------------------------- //
test("the manifest declares BOTH permissions the clipboard path needs", () => {
  const p = manifest.permissions;
  assert.ok(p.includes("offscreen"),
    "chrome.offscreen.createDocument() throws without the `offscreen` permission");
  assert.ok(p.includes("clipboardWrite"),
    "execCommand('copy') outside a short-lived user-action handler needs " +
    "`clipboardWrite` — without it it returns FALSE and every click copies nothing");
});

test("README's prose permission list is the manifest's, exactly", () => {
  // 🔴 A prose list is what a reader consults to judge whether this extension is
  // over-permissioned, and it had already gone stale once — it still claimed the
  // pre-`offscreen` set after the permission landed. Pinned as a SET so the
  // order in either file is free, but membership is not.
  const readme = readFileSync(join(EXT, "..", "README.md"), "utf8");
  const m = readme.match(/`permissions` is `\[([^\]]*)\]`/s);
  assert.ok(m, "the README's `permissions` claim vanished — re-point this guard");
  const claimed = m[1].split(",").map((s) => s.trim().replace(/^"|"$/g, ""))
    .filter(Boolean);
  assert.deepEqual(new Set(claimed), new Set(manifest.permissions),
    `README claims [${[...claimed].sort()}], manifest has [${[...manifest.permissions].sort()}]`);
});

test("the page the worker asks for is the page that exists, and it loads its script", () => {
  // OFFSCREEN_URL is a string in the worker; nothing made it name a real file.
  assert.match(offscreenHtml, /<script\s+src="offscreen\.js">/,
    "offscreen.html must load offscreen.js — without it the document exists, " +
    "registers no listener, and sendMessage resolves to undefined");
  assert.match(offscreenHtml, /id="sink"/,
    "offscreen.html must carry the #sink textarea the listener selects");
});

test("the worker's target string and the page's are the SAME string", async () => {
  // Two literals, two files, no shared module — the same shape as the
  // CLI↔protocol.js seam that already has a guard.
  globalThis.BROWSER_BRIDGE_NO_AUTOSTART = true;
  const { OFFSCREEN_CLIPBOARD_TARGET, OFFSCREEN_URL } =
    await import("../extension/service_worker.js");
  const m = offscreenSrc.match(/const TARGET = "([^"]+)"/);
  assert.ok(m, "offscreen.js's TARGET literal vanished — re-point this guard");
  assert.equal(m[1], OFFSCREEN_CLIPBOARD_TARGET,
    "the worker addresses a target the offscreen document does not answer to");
  assert.equal(OFFSCREEN_URL, "offscreen.html");
});

// The file's own header NAMES `navigator.clipboard.writeText()` to explain why it
// is not used, so a guard over the raw text matches the warning and can never
// fail. Strip `//` comments first and assert against CODE only — with a positive
// control below proving the stripper still leaves code behind to match.
const offscreenCode = offscreenSrc
  .split("\n").filter((l) => !/^\s*\/\//.test(l)).join("\n");

test("offscreen.js does NOT reach for navigator.clipboard", () => {
  // Its own header says writeText() rejects in an unfocused document EVERY time.
  // A refactor to the "modern" API is the plausible mistake, and it is silent:
  // the promise rejects, the catch reports it, and the badge shows ✗ forever.
  assert.match(offscreenCode, /document\.execCommand\("copy"\)/,
    "POSITIVE CONTROL: comment-stripping left no code to match — the assertion " +
    "below would pass vacuously");
  assert.doesNotMatch(offscreenCode, /navigator\s*\.\s*clipboard/,
    "an offscreen document is never focused — writeText() cannot work here");
  // ...and the raw file DOES mention it, which is what makes the strip necessary.
  assert.match(offscreenSrc, /navigator\.clipboard/,
    "if the header no longer explains the choice, this guard's premise moved");
});

// --------------------------------------------------------------------------- //
// The listener, driven for real
// --------------------------------------------------------------------------- //
// The module registers its listener at import and reads `document` from the
// global at CALL time, so it is imported ONCE and the DOM is swapped per case.
// (An earlier draft re-imported with a cache-busting query per case; the listener
// captured from the fresh instance was not the one under test in later cases and
// the file went red for a harness reason. One import, swapped globals, is both
// simpler and closer to how the real document behaves.)
let LISTENER = null;
globalThis.chrome = {
  runtime: { onMessage: { addListener(fn) { LISTENER = fn; } } },
};
globalThis.document = { getElementById() { return null; }, execCommand() { return false; } };
await import("../extension/offscreen.js");

test("HARNESS: importing offscreen.js registers exactly one listener", () => {
  // Anti-vacuity. Every behavioural case below calls LISTENER; if the import had
  // registered nothing they would all fail with "not a function" — a harness
  // error that reads like seven product defects. Assert the instrument first.
  assert.equal(typeof LISTENER, "function",
    "offscreen.js registered no chrome.runtime.onMessage listener");
});

function mockDom({ copyResult = true, throwOn = null } = {}) {
  const calls = { select: 0, exec: [], value: [] };
  const sink = {
    set value(v) { calls.value.push(v); this._v = v; },
    get value() { return this._v; },
    select() { calls.select += 1; },
  };
  globalThis.document = {
    getElementById(id) { return id === "sink" ? sink : null; },
    execCommand(cmd) {
      calls.exec.push({ cmd, selectedAt: calls.select });
      if (throwOn === cmd) throw new Error("execCommand exploded");
      return copyResult;
    },
  };
  return { listener: LISTENER, calls, sink };
}

function send(listener, msg) {
  let reply;
  const ret = listener(msg, {}, (r) => { reply = r; });
  return { reply, ret };
}

test("a copy message selects the textarea and reports execCommand's TRUE", () => {
  const { listener, calls } = mockDom({ copyResult: true });
  const { reply } = send(listener, { target: "offscreen-clipboard", type: "copy", text: "bw://workbench/main/12345" });
  assert.deepEqual(reply, { ok: true });
  assert.deepEqual(calls.value, ["bw://workbench/main/12345"]);
  assert.equal(calls.select, 1, "the textarea must be selected before the copy");
  assert.equal(calls.exec.length, 1);
  assert.equal(calls.exec[0].cmd, "copy");
  // 🔴 ORDERING, not just occurrence: execCommand must run AFTER select(), or it
  // copies whatever the previous selection was. `selectedAt` is the select count
  // at the moment execCommand ran.
  assert.equal(calls.exec[0].selectedAt, 1,
    "execCommand('copy') ran before select() — it would copy the wrong thing");
});

test("🔴 execCommand returning FALSE is a FAILURE, not a completed call", () => {
  // The whole clipboardWrite failure mode surfaces here and nowhere else:
  // execCommand REPORTS refusal by return value and never throws.
  const { listener } = mockDom({ copyResult: false });
  const { reply } = send(listener, { target: "offscreen-clipboard", type: "copy", text: "x" });
  assert.equal(reply.ok, false);
  assert.match(reply.error, /execCommand refused/);
});

test("a throw is caught and reported, not left to reject", () => {
  const { listener } = mockDom({ throwOn: "copy" });
  const { reply } = send(listener, { target: "offscreen-clipboard", type: "copy", text: "x" });
  assert.equal(reply.ok, false);
  assert.match(reply.error, /execCommand exploded/);
});

test("a message for ANOTHER target is left entirely alone", () => {
  // sendMessage fans out to every extension context except the sender, so an
  // options page's traffic passes through here. Answering it would make this
  // document a liar about someone else's message.
  const { listener, calls } = mockDom();
  let replied = false;
  const ret = listener({ target: "something-else", type: "copy", text: "x" }, {}, () => { replied = true; });
  assert.equal(replied, false, "it must not answer another context's message");
  assert.equal(ret, false, "returning true would hold the channel open");
  assert.equal(calls.exec.length, 0);
});

test("a malformed/absent message does not throw", () => {
  const { listener } = mockDom();
  for (const msg of [undefined, null, {}, { target: "offscreen-clipboard" }]) {
    assert.doesNotThrow(() => listener(msg, {}, () => {}));
  }
});

test("an unknown type on OUR target is refused by name", () => {
  const { listener, calls } = mockDom();
  const { reply } = send(listener, { target: "offscreen-clipboard", type: "paste" });
  assert.equal(reply.ok, false);
  assert.match(reply.error, /unknown type: paste/);
  assert.equal(calls.exec.length, 0);
});

test("a null text copies the empty string rather than the literal 'null'", () => {
  const { listener, calls } = mockDom();
  send(listener, { target: "offscreen-clipboard", type: "copy", text: null });
  assert.deepEqual(calls.value, [""]);
});
