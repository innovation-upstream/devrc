// switch-watcher.test.mjs -- the in-tab account-switch watcher.
//
// 🔴 WHY THIS FILE EXISTS. REPORTED BY THE OPERATOR, on the first real run of
// the widget in his browser: switching accounts left the card showing the
// PREVIOUS account until he did a full page reload, and cycling through
// several accounts changed nothing at all.
//
// The widget was not the fault and a widget-level test could never have found
// this. `content_widget.js` already repaints on a 30s tick and on
// chrome.storage.onChanged; it was faithfully repainting stale STORED data.
// Every trigger that refreshes that data needed a document load -- autoRun at
// document_idle, the SW's onTabUpdated(complete), onTabActivated, and a
// 15-minute alarm -- and an SPA account switch fires none of the first three.
// This is the isolation seam in its usual shape: two surfaces each correct on
// its own, with the defect in the relationship nobody owned.
//
// So these tests assert the RELATIONSHIP: that a change of active account,
// with no navigation of any kind, produces a `cu:usage-report`. And they pin
// the cost, because the fix is only acceptable if it is nearly free -- the
// cheap `/api/organizations` check must not turn into a per-org usage poll.
import test from "node:test";
import assert from "node:assert/strict";

import { makeChromeMock } from "./sw-mock.mjs";
import { ORG_A, ORG_B, NAME_A, NAME_B, NOW } from "./fixtures.mjs";

const { chrome } = makeChromeMock();

// sw-mock's runtime.sendMessage discards its argument; the whole point here is
// WHAT gets reported, so capture it.
const sent = [];
chrome.runtime.sendMessage = async (msg) => { sent.push(msg); return {}; };

// sw-mock's runtime carries no `id`, and a real one always does. `id` is the
// documented liveness tell -- it is what disappears on an extension reload --
// so without this the fixture models a PERMANENTLY DEAD context and the
// teardown test below could never observe the live case it contrasts against.
chrome.runtime.id = "cu-test-extension-id";

globalThis.chrome = chrome;
globalThis.CLAUDE_USAGE_NO_AUTOSTART = true;   // no timers, no probe on import

await import("../extension/content_probe.js");
const PROBE = globalThis.__CU_PROBE__;
assert.ok(PROBE, "content_probe.js did not expose its test surface");
assert.equal(typeof PROBE.checkActiveOrg, "function",
  "the switch watcher is missing — this suite is asserting nothing");

// Same route-table shape as probe-protocol.test.mjs: the usage URL CONTAINS
// the orgs URL as a prefix, so a substring table would misroute it.
const okJson = (body) => ({ ok: true, status: 200, json: async () => body });
const usageOk = () => okJson({ five_hour: { utilization: 10 } });

function mockFetch(routes, log) {
  globalThis.fetch = async (url, opts) => {
    log.push({ url, opts });
    for (const [pred, res] of routes) {
      if (pred(url)) return typeof res === "function" ? res() : res;
    }
    return { ok: false, status: 404 };
  };
}

const isOrgs = (u) => u === "/api/organizations";
const isUsage = (u) => u.endsWith("/usage");

/** Orgs route returning exactly one org, marked active. */
function oneOrg(uuid, name, log) {
  mockFetch([
    [isOrgs, () => okJson([{ uuid, name, is_active: true }])],
    [isUsage, usageOk],
  ], log);
}

/** Put the stateful watcher in a known state; it is never test-order safe. */
function reset(state) {
  PROBE.__setState(Object.assign(
    { lastSeenActiveUuid: null, lastCheckAt: 0, probeInFlight: false }, state));
  sent.length = 0;
}

// --- the regression: a switch with no navigation ------------------------------ //

test("an in-tab account switch reports, with NO page load of any kind", async () => {
  const log = [];
  reset({ lastSeenActiveUuid: ORG_A });
  oneOrg(ORG_B, NAME_B, log);

  const out = await PROBE.checkActiveOrg(NOW);

  assert.deepEqual(out, { probed: true, activeUuid: ORG_B });
  assert.equal(sent.length, 1, "the switch must produce exactly one report");
  assert.equal(sent[0].type, "cu:usage-report");
  assert.equal(sent[0].activeUuid, ORG_B);
  assert.equal(sent[0].results.length, 1, "the new account's usage must be fetched");
  assert.equal(PROBE.__getState().lastSeenActiveUuid, ORG_B,
    "the baseline must advance, or the next tick re-probes the same switch");
});

test("a detected switch costs ONE org fetch — the list is handed over, not re-fetched", () => {
  // The whole design rests on this: the cheap check already paid for
  // /api/organizations, so runProbe(pre) must not pay again. If this number
  // is ever 2, a switch costs more than a page load and the `pre` path has
  // been broken or bypassed.
  return (async () => {
    const log = [];
    reset({ lastSeenActiveUuid: ORG_A });
    oneOrg(ORG_B, NAME_B, log);

    await PROBE.checkActiveOrg(NOW);

    assert.equal(log.filter((c) => isOrgs(c.url)).length, 1,
      "/api/organizations must be fetched exactly once across check + probe");
    assert.equal(log.filter((c) => isUsage(c.url)).length, 1);
    for (const call of log) {
      assert.deepEqual(call.opts, { credentials: "include" },
        "every fetch the watcher makes is still a page-context credentialed fetch");
    }
  })();
});

test("no switch, no cost: an unchanged active org never reaches the usage endpoint", async () => {
  const log = [];
  reset({ lastSeenActiveUuid: ORG_A });
  oneOrg(ORG_A, NAME_A, log);

  const out = await PROBE.checkActiveOrg(NOW);

  assert.deepEqual(out, { skipped: "unchanged" });
  assert.equal(sent.length, 0, "an unchanged account must not report");
  assert.equal(log.filter((c) => isUsage(c.url)).length, 0,
    "the expensive per-org fan-out must not run when nothing changed");
  assert.equal(log.filter((c) => isOrgs(c.url)).length, 1);
});

// --- the cost floor ----------------------------------------------------------- //

test("the rate floor collapses overlapping triggers, and lifts exactly at the boundary", async () => {
  const log = [];
  reset({ lastSeenActiveUuid: ORG_A });
  oneOrg(ORG_A, NAME_A, log);

  await PROBE.checkActiveOrg(NOW);
  const afterFirst = log.length;

  // visibilitychange and focus both fire when a tab is raised; one check.
  const inside = await PROBE.checkActiveOrg(NOW + PROBE.ACTIVE_CHECK_MIN_INTERVAL_MS - 1);
  assert.deepEqual(inside, { skipped: "rate-limited" });
  assert.equal(log.length, afterFirst,
    "a rate-limited check must not reach the network at all");

  // Measured at the boundary AND inside it: a floor that never lifts would
  // pass the assertion above and break the feature.
  const at = await PROBE.checkActiveOrg(NOW + PROBE.ACTIVE_CHECK_MIN_INTERVAL_MS);
  assert.deepEqual(at, { skipped: "unchanged" },
    "at the boundary the check must actually run");
  assert.equal(log.length, afterFirst + 1);
});

test("a check must not race the page-load probe", async () => {
  const log = [];
  reset({ lastSeenActiveUuid: ORG_A, probeInFlight: true });
  oneOrg(ORG_B, NAME_B, log);

  const out = await PROBE.checkActiveOrg(NOW);

  assert.deepEqual(out, { skipped: "probe-in-flight" });
  assert.equal(log.length, 0, "an in-flight probe must suppress the check entirely");
  assert.equal(sent.length, 0);
});

// --- failure handling --------------------------------------------------------- //

test("a failed org fetch is SILENT and leaves the baseline alone — no fake logout", async () => {
  const log = [];
  reset({ lastSeenActiveUuid: ORG_A });
  globalThis.fetch = async (url, opts) => { log.push({ url, opts }); return { ok: false, status: 500 }; };

  const out = await PROBE.checkActiveOrg(NOW);

  assert.equal(out.skipped, "orgs-failed");
  assert.equal(out.status, 500);
  assert.equal(sent.length, 0,
    "a check we chose to make must never report a failure — that is how a blip "
    + "becomes a logged-out week of stale-marked accounts");
  assert.equal(PROBE.__getState().lastSeenActiveUuid, ORG_A);
});

test("a probe whose org fetch failed REPORTS but must not clear the baseline", async () => {
  // Both halves matter and they pull in opposite directions. The report must
  // go out (the SW marks accounts stale off a 401/403). The baseline must NOT
  // be cleared to null, because a null baseline permits a probe — so writing
  // the failure's `activeUuid: null` through would make a flaky network
  // escalate itself into a full probe on every subsequent tick.
  reset({ lastSeenActiveUuid: ORG_A });
  globalThis.fetch = async () => ({ ok: false, status: 401 });

  await PROBE.runAndReport();

  assert.equal(sent.length, 1, "a failed probe is still reported");
  assert.deepEqual(sent[0].orgsError, { status: 401, kind: "unauthorized" });
  assert.equal(PROBE.__getState().lastSeenActiveUuid, ORG_A,
    "the baseline must survive a failed probe");
  assert.equal(PROBE.__getState().probeInFlight, false,
    "a failed probe must not wedge the in-flight latch");
});

test("a null baseline permits a probe — the watcher self-heals after a failed first probe", async () => {
  const log = [];
  reset({ lastSeenActiveUuid: null });
  oneOrg(ORG_A, NAME_A, log);

  const out = await PROBE.checkActiveOrg(NOW);

  assert.equal(out.probed, true);
  assert.equal(PROBE.__getState().lastSeenActiveUuid, ORG_A);
});

test("an empty org list is skipped, not probed on every tick", async () => {
  const log = [];
  reset({ lastSeenActiveUuid: null });
  mockFetch([[isOrgs, () => okJson([])], [isUsage, usageOk]], log);

  const out = await PROBE.checkActiveOrg(NOW);

  assert.deepEqual(out, { skipped: "no-active-org" });
  assert.equal(log.filter((c) => isUsage(c.url)).length, 0);
  assert.equal(sent.length, 0);
  assert.equal(PROBE.__getState().lastSeenActiveUuid, null);
});

test("the four skip reasons are pairwise distinct", () => {
  // A boolean return would collapse "rate limited", "fetch failed", "nothing
  // changed" and "a probe is running" into one unfalsifiable "no". Each of
  // the tests above asserts one of them by name; this pins that they are in
  // fact different strings, so a future refactor cannot quietly merge two.
  const reasons = ["rate-limited", "orgs-failed", "unchanged", "no-active-org", "probe-in-flight"];
  assert.equal(new Set(reasons).size, reasons.length);
});

// --- teardown on a dead extension context -------------------------------------- //

test("a dead extension context stops the watcher instead of polling forever", () => {
  // 🔴 THIS TEST MUST FIRST MAKE THE TIMERS EXIST. `startWatchers()` returns
  // immediately when there is no `document`, which is the normal node case --
  // so asserting "0 timers" without a document passes identically with the
  // whole teardown deleted. The guard has to be REACHED, not just breakable.
  const log = [];
  reset({ lastSeenActiveUuid: ORG_A });
  oneOrg(ORG_B, NAME_B, log);

  const savedDoc = globalThis.document;
  const savedWin = globalThis.window;
  const savedLoc = globalThis.location;
  const live = globalThis.chrome;
  globalThis.document = { hidden: false, addEventListener() {} };
  globalThis.window = { addEventListener() {} };
  globalThis.location = { href: "https://claude.ai/new" };

  try {
    PROBE.startWatchers();
    const running = PROBE.__timerCount();
    assert.ok(running > 0,
      "precondition: the watcher must actually be running before we kill it");

    // Exactly how Brave presents an extension reload to a still-open page.
    globalThis.chrome = { runtime: {} };          // no `id` => context invalidated
    assert.equal(PROBE.extAlive(), false);

    PROBE.onMaybeSwitch();

    assert.equal(PROBE.__timerCount(), 0,
      `the ${running} timers must be cleared, not left polling for the tab's life`);
    assert.equal(log.length, 0,
      "a dead context must not reach the network — every report would be discarded");
    assert.equal(sent.length, 0);
  } finally {
    PROBE.stopWatchers();                          // never leak a timer into the runner
    globalThis.chrome = live;
    globalThis.document = savedDoc;
    globalThis.window = savedWin;
    globalThis.location = savedLoc;
  }
  assert.equal(PROBE.extAlive(), true, "and a live context is still recognised as live");
});

// --- runProbe's new `pre` door ------------------------------------------------- //

test("runProbe(pre) skips the org fetch and stamps its OWN snapshot clock", async () => {
  const log = [];
  reset({});
  mockFetch([[isUsage, usageOk]], log);

  const before = Date.now();
  const rep = await PROBE.runProbe({
    orgs: [{ uuid: ORG_A, name: NAME_A, isActive: true }],
    activeUuid: ORG_A,
  });

  assert.equal(log.filter((c) => isOrgs(c.url)).length, 0, "the org list was supplied");
  assert.equal(rep.activeUuid, ORG_A);
  assert.equal(rep.results.length, 1);
  assert.ok(rep.results[0].ok);
  // fetchedAt is the USAGE snapshot time, not the cheap check's earlier clock:
  // carrying that forward would backdate every switch-triggered record.
  assert.ok(rep.fetchedAt >= before,
    "fetchedAt must be stamped when the usage fetch runs, not inherited");
});

test("runProbe with no `pre` still fetches the org list itself", async () => {
  const log = [];
  reset({});
  oneOrg(ORG_A, NAME_A, log);

  const rep = await PROBE.runProbe();

  assert.equal(log.filter((c) => isOrgs(c.url)).length, 1,
    "the page-load path must be unchanged by the `pre` door");
  assert.equal(rep.activeUuid, ORG_A);
});
