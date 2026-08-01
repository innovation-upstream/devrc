// loop_wedge.test.mjs — THE regression for the silent long-poll disconnect.
//
// The fault (diagnosed 2026-07-31, `claudedocs/browser-bridge-silent-drop-diagnosis-2026-07-31.md`):
// an UNBOUNDED await inside execute() permanently wedges the poll loop. loop() is
// guarded by a non-reentrant module global (`if (running) return`) whose reset
// lives in a `finally` that a loop parked on an await can never reach — so the
// 1-minute keepalive alarm fires on time, calls loop(), hits the guard and does
// nothing. Only a fresh service-worker evaluation (the operator clicking ↻ in
// brave://extensions) recovered the instance.
//
// Crucially the CDP path was NOT the culprit — it is the one path that was
// already bounded (withCdpSession, 8s/8s/15s). The unbounded awaits were the
// NON-CDP chrome.* calls: `frames` → webNavigation.getAllFrames, `screenshot`
// fast path → tabs.captureVisibleTab, targetTab() → tabs.get/query, and
// pollOnce's bare fetch. Both recorded drops are immediately preceded by a
// cmd_timeout on exactly those ops, while ops that timed out through the BOUNDED
// CDP path did not kill the instance.
//
// So these tests deliberately wedge a NON-CDP op with a promise that never
// settles — the exact shape of the real fault — and assert the loop RELEASES and
// KEEPS POLLING.
//
// MUTATION-TESTED (this is not a vacuous invariant guard): with the
// promiseWithTimeout removed from execute() — i.e. `await OPS[cmd.op](cmd)` as it
// was before this change — "a WEDGED op releases the loop" and "the loop keeps
// polling after a wedged op" both go RED (the loop never posts a result and never
// polls a second time; the test's own watchdog fires). Re-verify by reverting
// that single line before trusting this file.

import test from "node:test";
import assert from "node:assert/strict";

// Suppress the SW's auto-start (poll loop + chrome listeners) so importing the
// module does not begin networking — the tests drive loop() explicitly.
globalThis.BROWSER_BRIDGE_NO_AUTOSTART = true;
// Drive the real budgets at test speed. Production reads the protocol.js
// constants (18s exec / 40s poll / 10s result / 180s stall) unchanged; only the
// NUMBERS are injected here, never the mechanism.
globalThis.BROWSER_BRIDGE_LOOP_TIMING = {
  execMs: 40, pollMs: 200, resultMs: 200, stallMs: 1000, storageMs: 300,
};

const delay = (ms) => new Promise((r) => setTimeout(r, ms));
const NEVER = () => new Promise(() => {});   // the wedge: never settles, ever

// --- mock chrome + fetch ------------------------------------------------------ //
const store = { port: 8788, token: "test-token", label: "work",
                instanceId: "11111111-2222-3333-4444-555555555555" };
const breadcrumbs = [];
// When set, every storage READ parks on this promise — the handle used to hold
// the loop INSIDE config() so a retirement can land mid-iteration (the exact
// window a top-of-loop generation check cannot see).
let storageGate = null;
// Hang the next N storage READS outright (never settling). Separate from
// storageGate because a test that asserts the BOUND fires must not also have to
// release the hang — releasing it is what the bound is replacing.
let hangStorageReads = 0;
// Parks chrome.tabs.query — the await INSIDE pollOnce, between loop()'s
// generation check and the actual /poll fetch. This is the window that falsified
// the previous "duplicate polling is prevented" claim.
let tabsQueryGate = null;
// Park the `superseded` storage read (string key) to prove clearSuperseded()'s
// bound — not just config()'s — releases the loop. RELEASABLE rather than a bare
// NEVER(): under the mutant (bound gutted) the loop parks here, and an
// unreleasable hang would stall the whole test FILE instead of just failing the
// one test. The bound is what the assertion measures; the release only lets the
// mutant's loop unwind afterwards.
let supersededGate = null;
globalThis.chrome = {
  storage: {
    local: {
      async get(keys) {
        if (hangStorageReads > 0) { hangStorageReads -= 1; await NEVER(); }
        if (supersededGate && keys === "superseded") await supersededGate;
        // storageGate parks ONLY config()'s read (the array-form key list).
        // Gating every read parks at whichever chrome.storage call comes next —
        // which may be clearSuperseded()'s, i.e. AFTER the poll, making the
        // mid-iteration test vacuous (it passed against the mutant that way).
        if (storageGate && Array.isArray(keys)) await storageGate;
        const ks = Array.isArray(keys) ? keys : [keys];
        const out = {};
        for (const k of ks) if (k in store) out[k] = store[k];
        return out;
      },
      async set(obj) {
        Object.assign(store, obj);
        if (obj.lastExec) breadcrumbs.push(obj.lastExec);
      },
    },
  },
  runtime: { id: "testextid", getManifest: () => ({ version: "0.4.0" }) },
  tabs: {
    async query() {
      if (tabsQueryGate) await tabsQueryGate;
      return [{ id: 5, url: "https://example.com/", title: "T" }];
    },
    async get(id) { return { id, url: "https://example.com/", title: "T" }; },
  },
  webNavigation: { async getAllFrames() { return []; } },
};

const wire = { polls: 0, posted: [] };
function res(status, body) {
  return { status, async json() { return body; } };
}

// The fetch mock: the FIRST poll hands out one command, every later poll is a
// normal 204 idle. `stopAfter` retires the loop (the same generation mechanism
// keepaliveTick uses) so this `while (true)` has an exit in a test.
function installFetch({ command, stopAfter, onPoll }) {
  wire.polls = 0;
  wire.posted.length = 0;
  globalThis.fetch = async (url, opts) => {
    const u = String(url);
    if (u.endsWith("/poll")) {
      wire.polls += 1;
      if (onPoll) onPoll(wire.polls);
      if (wire.polls === 1 && command) return res(200, command);
      await delay(2);                       // don't hot-spin the idle path
      if (wire.polls >= stopAfter) loopState.retire();
      return res(204, null);
    }
    if (u.endsWith("/result")) {
      wire.posted.push(JSON.parse(opts.body));
      return res(200, { ok: true });
    }
    throw new Error(`unexpected fetch: ${u}`);
  };
}

const { OPS, loop, loopState, keepaliveTick, execute } =
  await import("../extension/service_worker.js");

// A hard watchdog: loop() only returns once retired. If the bound is missing the
// loop parks forever, and WITHOUT this the test would hang rather than fail —
// a hang is not a legible red.
// Same reasoning for a DIRECT execute() call: an unbounded execute() awaits a
// never-settling op forever, which would stall the whole test FILE instead of
// producing a red test. Turn the hang into an assertion failure.
async function withWatchdog(promise, ms = 3000) {
  const sentinel = Symbol("watchdog");
  const out = await Promise.race([promise, delay(ms).then(() => sentinel)]);
  assert.notEqual(out, sentinel,
    `never settled within ${ms}ms — execute() is unbounded again`);
  return out;
}

// ⚠ The watchdog timer MUST be cancelled once the loop returns. An uncancelled
// `setTimeout(() => loopState.retire())` fires during a LATER test and silently
// retires ITS loop — which cost a green-looking run here: the config-bound test
// saw zero polls and failed for a reason that had nothing to do with the code.
// Wait until `pred()` holds, or give up after `ms`. Returns true if it held.
//
// ⚠ Written as a SEQUENTIAL deadline loop on purpose. The obvious
// `Promise.race([delay(ms), (async () => { while (!pred()) await delay(20); })()])`
// leaves the inner IIFE spinning forever when the race is won by the timeout —
// a dangling promise that keeps node's event loop alive, so the whole test FILE
// hangs instead of reporting the one red test. Cost an interrupted run to find.
async function waitFor(pred, ms = 4000, step = 20) {
  const deadline = Date.now() + ms;
  while (Date.now() < deadline) {
    if (pred()) return true;
    await delay(step);
  }
  return pred();
}

async function runLoop(ms = 3000) {
  let timedOut = false;
  let handle;
  const watchdog = new Promise((resolve) => {
    handle = setTimeout(() => { timedOut = true; loopState.retire(); resolve(); }, ms);
  });
  try {
    await Promise.race([loop(), watchdog.then(() => delay(50))]);
  } finally {
    clearTimeout(handle);
  }
  return timedOut;
}

test.beforeEach(() => {
  loopState.reset();
  breadcrumbs.length = 0;
  storageGate = null;
  hangStorageReads = 0;
  tabsQueryGate = null;
  supersededGate = null;
});

// --- the wedge ---------------------------------------------------------------- //
test("REGRESSION: a WEDGED op (never-settling await) RELEASES the loop instead of parking it forever", async () => {
  const original = OPS.frames;
  OPS.frames = NEVER;                       // the exact real-world fault shape
  try {
    installFetch({ command: { id: "cmd-1", op: "frames" }, stopAfter: 4 });
    const timedOut = await runLoop();
    assert.equal(timedOut, false,
      "the loop never released — execute() is unbounded again");
    assert.equal(wire.posted.length, 1, "the wedged op must still post a result");
    const env = wire.posted[0];
    assert.equal(env.ok, false);
    assert.equal(env.id, "cmd-1");
    assert.equal(env.error, "op_timeout:frames",
      "a timed-out op must name the op, and must NOT be labelled cdp_timeout — "
      + "the CDP path is the one that was already bounded");
  } finally {
    OPS.frames = original;
  }
});

test("REGRESSION: the loop KEEPS POLLING after a wedged op (the instance survives)", async () => {
  const original = OPS.screenshot;
  OPS.screenshot = NEVER;                   // the other op the journal caught
  try {
    installFetch({ command: { id: "cmd-2", op: "screenshot" }, stopAfter: 4 });
    const timedOut = await runLoop();
    assert.equal(timedOut, false);
    assert.ok(wire.polls >= 3,
      `the loop must resume polling after a wedged op (saw ${wire.polls} polls)`);
  } finally {
    OPS.screenshot = original;
  }
});

test("a timed-out op does NOT throw past the loop — the envelope is a normal error", async () => {
  const original = OPS.frames;
  OPS.frames = NEVER;
  try {
    const env = await withWatchdog(execute({ id: "x", op: "frames" }));
    assert.equal(env.ok, false);
    assert.equal(env.error, "op_timeout:frames");
    assert.equal(env.id, "x");
  } finally {
    OPS.frames = original;
  }
});

test("a HEALTHY op is untouched by the bound (no false timeout, result passes through)", async () => {
  const original = OPS.frames;
  OPS.frames = async () => ({ frames: [{ frameId: 0 }] });
  try {
    installFetch({ command: { id: "cmd-3", op: "frames" }, stopAfter: 3 });
    const timedOut = await runLoop();
    assert.equal(timedOut, false);
    assert.equal(wire.posted.length, 1);
    assert.equal(wire.posted[0].ok, true);
    assert.deepEqual(wire.posted[0].data, { frames: [{ frameId: 0 }] });
  } finally {
    OPS.frames = original;
  }
});

// --- the detector breadcrumb --------------------------------------------------- //
test("execute() leaves a storage breadcrumb naming the op it is IN (start → timeout)", async () => {
  const original = OPS.frames;
  OPS.frames = NEVER;
  try {
    await withWatchdog(execute({ id: "bc-1", op: "frames" }));
    await delay(5);                          // breadcrumbs are fire-and-forget
    const phases = breadcrumbs.map((b) => b.phase);
    assert.deepEqual(phases, ["start", "timeout"]);
    assert.equal(breadcrumbs[0].op, "frames");
    assert.equal(breadcrumbs[0].id, "bc-1");
    assert.ok(typeof breadcrumbs[0].ts === "number");
  } finally {
    OPS.frames = original;
  }
});

test("the breadcrumb is ONE rolling slot — it cannot grow storage", async () => {
  const original = OPS.frames;
  OPS.frames = async () => ({ frames: [] });
  try {
    for (let i = 0; i < 5; i += 1) await execute({ id: `n${i}`, op: "frames" });
    await delay(5);
    // Many writes, but always to the same key.
    assert.equal(store.lastExec.op, "frames");
    assert.equal(store.lastExec.id, "n4");
    assert.equal(store.lastExec.phase, "done");
  } finally {
    OPS.frames = original;
  }
});

test("a breadcrumb write that REJECTS never breaks (or wedges) the op", async () => {
  const set = chrome.storage.local.set;
  chrome.storage.local.set = async () => { throw new Error("storage full"); };
  const original = OPS.frames;
  OPS.frames = async () => ({ frames: [] });
  try {
    const env = await execute({ id: "bc-2", op: "frames" });
    assert.equal(env.ok, true, "a failing breadcrumb must not fail the op");
  } finally {
    chrome.storage.local.set = set;
    OPS.frames = original;
  }
});

// --- the keepalive recovery (defence against the NEXT unbounded await) --------- //
test("keepaliveTick does NOT disturb a loop that is merely SLOW", async () => {
  loopState.reset();
  installFetch({ command: null, stopAfter: 3 });
  const running = loop();
  await delay(20);
  const genBefore = loopState.generation;
  keepaliveTick();                           // ticked recently → no restart
  assert.equal(loopState.generation, genBefore,
    "a healthy loop must never be retired — that would create two pollers");
  loopState.retire();
  await running;
});

test("keepaliveTick FORCE-RESTARTS a loop whose last tick is older than the stall threshold", async () => {
  loopState.reset();
  installFetch({ command: null, stopAfter: 2 });
  // Simulate the wedge the OLD code could not clear: the latch is set, no tick.
  const parked = loop();
  await delay(20);
  loopState.backdate(5000);                  // stallMs is 1000 in this file
  const genBefore = loopState.generation;
  keepaliveTick();
  assert.equal(loopState.generation, genBefore + 1,
    "a wedged loop must be retired so a fresh one can take the latch");
  await delay(30);
  assert.equal(loopState.running, true, "the replacement loop holds the latch");
  loopState.retire();
  await parked;
  await delay(20);
});

test("REGRESSION: a retired loop does NOT clear `running` out from under its replacement", async () => {
  // This is the duplicate-poller hazard the generation check exists for: if the
  // retired loop's finally cleared the shared latch, the NEXT alarm would start a
  // second loop alongside the replacement and the two would fight over /poll.
  loopState.reset();
  installFetch({ command: null, stopAfter: 100 });
  const first = loop();
  await delay(10);
  loopState.retire();                        // retire without starting a replacement
  await first;
  assert.equal(loopState.running, true,
    "the retired loop must leave the latch to whoever owns the current generation");
  // Now genuinely release it so the file exits clean.
  loopState.reset();
});

test("REGRESSION: a loop retired MID-ITERATION must not issue another /poll", async () => {
  // The concurrency half of the generation invariant, and the half a
  // top-of-iteration check does NOT cover. Park the loop inside config()'s
  // storage read, retire it there (keepaliveTick's exact mechanism), then
  // release: the retired loop resumes INSIDE the iteration, past the top check.
  //
  // Measured before the fix: polls went 8 → 9. The retired loop polled.
  loopState.reset();
  installFetch({ command: null, stopAfter: 1e9 });
  let release;
  const running = loop();
  await delay(30);                             // let it complete some iterations
  storageGate = new Promise((r) => { release = r; });
  await delay(30);                             // it is now parked inside config()
  const before = wire.polls;
  loopState.retire();                          // retired WHILE mid-iteration
  storageGate = null;
  release();
  await delay(60);                             // give the retired loop every chance
  assert.equal(wire.polls, before,
    `a retired loop issued ${wire.polls - before} extra poll(s) — the generation `
    + "check must sit immediately before the poll, not only at the top of the loop");
  await running;
});

test("REGRESSION: a hung POST /result releases the loop (bound is wired to loopTiming)", async () => {
  // 🟡-4: this race previously used the raw RESULT_BUDGET_MS constant, so the
  // test-injected resultMs did not apply and NOTHING exercised a hanging /result.
  loopState.reset();
  wire.polls = 0;
  wire.posted.length = 0;
  let resultHangs = true;
  globalThis.fetch = async (url) => {
    const u = String(url);
    if (u.endsWith("/poll")) {
      wire.polls += 1;
      if (wire.polls === 1) return res(200, { id: "cmd-r", op: "tabs" });
      await delay(2);
      if (wire.polls >= 4) loopState.retire();
      return res(204, null);
    }
    if (u.endsWith("/result")) {
      if (resultHangs) { resultHangs = false; return NEVER(); }
      return res(200, { ok: true });
    }
    throw new Error(`unexpected fetch: ${u}`);
  };
  const original = OPS.tabs;
  OPS.tabs = async () => ({ tabs: [] });
  try {
    const timedOut = await runLoop();
    assert.equal(timedOut, false,
      "a hung /result parked the loop — the result bound is not wired to loopTiming()");
    assert.ok(wire.polls >= 3,
      `the loop must keep polling after a hung /result (saw ${wire.polls})`);
  } finally {
    OPS.tabs = original;
  }
});

test("REGRESSION: a never-settling POLL fetch releases the loop and it retries", async () => {
  // The fourth unbounded await named in the diagnosis (pollOnce's bare fetch),
  // which also had no coverage.
  loopState.reset();
  wire.polls = 0;
  globalThis.fetch = async (url) => {
    const u = String(url);
    if (u.endsWith("/poll")) {
      wire.polls += 1;
      if (wire.polls === 1) return NEVER();    // the wedge
      await delay(2);
      if (wire.polls >= 3) loopState.retire();
      return res(204, null);
    }
    throw new Error(`unexpected fetch: ${u}`);
  };
  const timedOut = await runLoop(5000);        // 1 poll bound + a backoff sleep
  assert.equal(timedOut, false, "a hung /poll parked the loop");
  assert.ok(wire.polls >= 2,
    `the loop must re-poll after a hung poll fetch (saw ${wire.polls})`);
});

test("REGRESSION: a hung chrome.storage read in config() releases the loop", async () => {
  // 🟡-2: config() runs on EVERY iteration and is the same unbounded chrome.*
  // class the root cause blames. Bounded via STORAGE_BUDGET_MS.
  loopState.reset();
  installFetch({ command: null, stopAfter: 1e9 });
  hangStorageReads = 1;                        // config()'s FIRST read never settles
  const running = loop();
  await delay(80);
  assert.equal(wire.polls, 0, "precondition: parked before ever polling");
  // storageMs is 300ms here; the bound must expire, back off (~1s), and carry on.
  const recovered = await waitFor(() => wire.polls > 0);
  loopState.retire();                          // ALWAYS release the loop first —
  await delay(20);                             // an assert here must not hang the file
  assert.equal(recovered, true,
    "a hung storage read parked the loop — config() is unbounded");
  await running;
});

test("REGRESSION: a loop retired inside chrome.tabs.query must not reach the /poll fetch", async () => {
  // The window that falsified the previous guarantee. loop()'s generation check
  // sits before pollOnce(), but pollOnce AWAITS activeTabSnapshot()
  // (chrome.tabs.query) before its fetch — so a retirement landing there let the
  // retired loop sail on into the poll. Measured then: maxConcurrentPolls = 2.
  //
  // Driven through the PRODUCTION keepaliveTick path (backdate + tick), not a
  // bare loopState.retire(), so this pins what actually ships.
  loopState.reset();
  let inFlight = 0;
  let maxInFlight = 0;
  wire.polls = 0;
  globalThis.fetch = async (url) => {
    if (!String(url).endsWith("/poll")) throw new Error(`unexpected: ${url}`);
    wire.polls += 1;
    inFlight += 1;
    maxInFlight = Math.max(maxInFlight, inFlight);
    await delay(15);                        // a poll is in flight for a while
    inFlight -= 1;
    return res(204, null);
  };
  let release;
  const parked = loop();
  await delay(30);                          // a few healthy iterations
  tabsQueryGate = new Promise((r) => { release = r; });
  await delay(30);                          // now parked INSIDE chrome.tabs.query
  loopState.backdate(5000);                 // stallMs is 1000 in this file
  keepaliveTick();                          // → retires it AND starts a replacement
  await delay(40);                          // the replacement is polling
  tabsQueryGate = null;
  release();                                // the retired loop resumes...
  await delay(80);                          // ...and must NOT poll
  assert.equal(maxInFlight, 1,
    `two loops polled concurrently (max ${maxInFlight}) — the generation check `
    + "must sit inside pollOnce with NO await between it and the fetch");
  loopState.retire();
  await parked;
  await delay(40);                          // let the replacement exit too
});

test("REGRESSION: a retired loop must not stamp the shared lastLoopTickAt", async () => {
  // Why the TOP-of-iteration check is still load-bearing even though the binding
  // poll guard moved into pollOnce: a retired loop that reaches the top of its
  // next iteration would refresh the liveness clock, masking a genuine wedge of
  // its REPLACEMENT from the keepalive.
  loopState.reset();
  wire.polls = 0;
  globalThis.fetch = async () => { throw new Error("transport down"); };
  const parked = loop();
  await delay(60);                          // failed poll → now in the backoff sleep
  const before = loopState.lastLoopTickAt;
  assert.ok(before !== null, "precondition: the loop stamped at least once");
  loopState.retire();
  await delay(1500);                        // past the ~1s backoff → loop top
  assert.equal(loopState.lastLoopTickAt, before,
    "a retired loop refreshed the liveness clock — that masks a real wedge of "
    + "the loop that replaced it");
  await parked;
});

test("REGRESSION: a hung clearSuperseded() storage read releases the loop", async () => {
  // storageBounded covers config() AND the superseded flag. Only config()'s bound
  // was pinned; gutting storageBounded left this path untested even though
  // clearSuperseded() runs on EVERY healthy iteration — more often than the ops.
  loopState.reset();
  let release;
  supersededGate = new Promise((r) => { release = r; });
  installFetch({ command: null, stopAfter: 1e9 });
  const parked = loop();
  const keptPolling = await waitFor(() => wire.polls >= 3);
  // Unwind FIRST — an assertion that throws here must not strand the loop, and
  // under the mutant the loop is parked in the gate rather than past it.
  loopState.retire();
  supersededGate = null;
  release();
  await delay(40);
  assert.equal(keptPolling, true,
    "the loop stopped polling — clearSuperseded()'s storage read is unbounded");
  await parked;
});

test("the keepalive is wired to keepaliveTick, not to a bare loop() call", async () => {
  // The pre-0.4.0 alarm called loop() directly, which hits `if (running) return`
  // and is structurally incapable of clearing a wedge. Pin the mechanism.
  const src = await (await import("node:fs/promises"))
    .readFile(new URL("../extension/service_worker.js", import.meta.url), "utf8");
  assert.match(src, /bridge-keepalive"\)\s*keepaliveTick\(\)/,
    "the alarm handler must call keepaliveTick()");
});
