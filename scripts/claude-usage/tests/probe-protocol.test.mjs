// probe-protocol.test.mjs -- the CS<->SW contract.
//
// Pins the message shapes on BOTH sides of the seam: what the content probe
// builds (report shape, error classification, the credentials:"include"
// page-context fetch rule, the org cap), what the service worker does with
// each failure class (401/403 mark stale silently; network errors leave the
// stored record alone), and account-switch detection end to end.
import test from "node:test";
import assert from "node:assert/strict";

import { makeChromeMock } from "./sw-mock.mjs";

const { chrome, storage, calls } = makeChromeMock();
globalThis.chrome = chrome;
globalThis.CLAUDE_USAGE_NO_AUTOSTART = true;

const NOW = Date.parse("2026-09-19T13:00:00Z");
const ORG_A = "11111111-1111-4111-8111-111111111111";
const ORG_B = "22222222-2222-4222-8222-222222222222";
const ORG_C = "33333333-3333-4333-8333-333333333333";
const NAME_A = "user@example.com's Organization";
const NAME_B = "Work Org <work@example.com>";

const SW = await import("../extension/service_worker.js");
await import("../extension/content_probe.js");
const PROBE = globalThis.__CU_PROBE__;
assert.ok(PROBE, "content_probe.js did not expose its test surface");

// --- probe side: error classification ----------------------------------------- //

test("status -> kind mapping covers every failure class the SW must tell apart", () => {
  assert.equal(PROBE.classifyStatus(401), "unauthorized");
  assert.equal(PROBE.classifyStatus(403), "forbidden");
  assert.equal(PROBE.classifyStatus(0), "network");
  assert.equal(PROBE.classifyStatus(200), "bad-schema");
  assert.equal(PROBE.classifyStatus(500), "http");
});

test("validateOrgs keeps only rows with a uuid, carrying the activity claim", () => {
  const orgs = PROBE.validateOrgs([
    { uuid: ORG_A, name: NAME_A, extra: "junk" },
    { name: "no uuid" },
    null,
    42,
    { uuid: "" },
    { uuid: ORG_B },                                  // name may be null
    { uuid: ORG_C, is_active: true },                 // claim carried, not dropped
  ]);
  assert.deepEqual(orgs, [
    { uuid: ORG_A, name: NAME_A, isActive: false },
    { uuid: ORG_B, name: null, isActive: false },
    { uuid: ORG_C, name: null, isActive: true },
  ]);
  assert.deepEqual(PROBE.validateOrgs("nope"), []);
  assert.deepEqual(PROBE.validateOrgs(null), []);
});

test("pickActiveOrg prefers the row claiming activity, falls back to first", () => {
  // Operates on the VALIDATED shape ({uuid, name, isActive}); a raw-API
  // is_active field is not seen here — that claim survives only through
  // validateOrgs' isActive, so picking and validating cannot diverge.
  assert.equal(PROBE.pickActiveOrg([
    { uuid: ORG_A, isActive: false }, { uuid: ORG_B, isActive: true },
  ]), ORG_B);
  assert.equal(PROBE.pickActiveOrg([{ uuid: ORG_B, isActive: true }, { uuid: ORG_A, isActive: false }]), ORG_B);
  assert.equal(PROBE.pickActiveOrg([{ uuid: ORG_A, isActive: false }, { uuid: ORG_B, isActive: false }]), ORG_A,
    "no claim -> first org (the safe fallback, never a throw)");
  assert.equal(PROBE.pickActiveOrg([]), null);
  assert.equal(PROBE.pickActiveOrg(null), null);
});

test("an org that validates away can never be picked as active (F4)", () => {
  // The empty-uuid row is dropped by validateOrgs BEFORE the pick, so an
  // empty-uuid row claiming activity cannot leave lastActiveOrg pointing at
  // an org with no stored record.
  const raw = [{ uuid: "", is_active: true }, { uuid: ORG_A, is_active: false }];
  const orgs = PROBE.validateOrgs(raw);
  assert.equal(PROBE.pickActiveOrg(orgs), ORG_A);
});

// --- probe side: runProbe against a mocked page fetch -------------------------- //

const okJson = (body) => ({ ok: true, status: 200, json: async () => body });
const usageOk = okJson({ five_hour: { utilization: 10 } });

// Routes are (predicate, response) pairs: the usage URL CONTAINS the orgs
// URL as a prefix, so a substring match table would misroute it.
function mockFetch(routes, log) {
  globalThis.fetch = async (url, opts) => {
    log.push({ url, opts });
    for (const [pred, res] of routes) {
      if (pred(url)) return typeof res === "function" ? res() : res;
    }
    return { ok: false, status: 404 };
  };
}

test("runProbe fetches orgs then per-org usage, IN PAGE CONTEXT with credentials", async () => {
  const log = [];
  mockFetch([
    [(u) => u === "/api/organizations",
      okJson([{ uuid: ORG_A, name: NAME_A }, { uuid: ORG_B, name: NAME_B }])],
    [(u) => u.endsWith("/usage"), usageOk],
  ], log);
  const rep = await PROBE.runProbe();
  assert.equal(rep.type, "cu:usage-report");
  assert.equal(rep.orgs.length, 2);
  assert.ok(rep.activeUuid);
  assert.equal(rep.results.length, 2);
  assert.ok(rep.results.every((r) => r.ok));
  for (const call of log) {
    assert.deepEqual(call.opts, { credentials: "include" },
      "every usage fetch MUST be a page-context credentialed fetch");
  }
});

test("runProbe maps the orgs-list failure classes", async () => {
  const cases = [
    [401, "unauthorized"], [403, "forbidden"], [500, "http"],
  ];
  for (const [status, kind] of cases) {
    mockFetch([[(u) => u === "/api/organizations", { ok: false, status }]], []);
    const rep = await PROBE.runProbe();
    assert.deepEqual(rep.orgsError, { status, kind }, `status ${status}`);
    assert.deepEqual(rep.results, []);
  }
  // network throw
  globalThis.fetch = async () => { throw new Error("net down"); };
  let rep = await PROBE.runProbe();
  assert.deepEqual(rep.orgsError, { status: 0, kind: "network" });
  // 200 with non-JSON body
  globalThis.fetch = async () => ({ ok: true, status: 200, json: async () => { throw new Error("nope"); } });
  rep = await PROBE.runProbe();
  assert.deepEqual(rep.orgsError, { status: 200, kind: "bad-schema" });
});

test("a per-org usage failure is a per-org result, never a thrown error", async () => {
  mockFetch([
    [(u) => u.includes(ORG_A), { ok: false, status: 403 }],
    [(u) => u.endsWith("/usage"), usageOk],
    [(u) => u === "/api/organizations", okJson([{ uuid: ORG_A }, { uuid: ORG_B }])],
  ], []);
  const rep = await PROBE.runProbe();
  assert.equal(rep.results.length, 2);
  const a = rep.results.find((r) => r.orgUuid === ORG_A);
  const b = rep.results.find((r) => r.orgUuid === ORG_B);
  assert.deepEqual(a, { orgUuid: ORG_A, ok: false, status: 403, kind: "forbidden" });
  assert.equal(b.ok, true);
});

test("runProbe caps how many orgs it fetches usage for", async () => {
  const many = [];
  for (let i = 0; i < 20; i++) many.push({ uuid: `org-${i}` });
  const log = [];
  mockFetch([
    [(u) => u === "/api/organizations", okJson(many)],
    [(u) => u.endsWith("/usage"), usageOk],
  ], log);
  const rep = await PROBE.runProbe();
  assert.equal(rep.results.length, PROBE.ORG_FETCH_CAP);
});

// --- SW side: what each failure class MEANS ------------------------------------ //

const report = (overrides = {}) => ({
  type: "cu:usage-report",
  fetchedAt: NOW,
  orgs: [{ uuid: ORG_A, name: NAME_A }],
  activeUuid: ORG_A,
  results: [{ orgUuid: ORG_A, ok: true, usage: { five_hour: { utilization: 10 } } }],
  ...overrides,
});

test("a 403 result marks that account stale SILENTLY", async () => {
  storage.accounts = {};
  calls.notifications.length = 0;
  // Seed the account, then deny it.
  await SW.handleReport(report());
  calls.notifications.length = 0;
  await SW.handleReport(report({
    activeUuid: null,
    results: [{ orgUuid: ORG_A, ok: false, status: 403, kind: "forbidden" }],
  }));
  const stored = storage.accounts[ORG_A];
  assert.ok(stored.staleSince, "the 403'd account is stale");
  assert.deepEqual(calls.notifications, [], "no error toast spam");
  // The stored usage snapshot was not replaced by the failure.
  assert.equal(stored.session.utilization, 10);
});

test("a network-failed result leaves the stored record untouched", async () => {
  storage.accounts = {};
  await SW.handleReport(report());
  const before = JSON.parse(JSON.stringify(storage.accounts[ORG_A]));
  await SW.handleReport(report({
    results: [{ orgUuid: ORG_A, ok: false, status: 0, kind: "network" }],
  }));
  assert.deepEqual(storage.accounts[ORG_A], before);
});

test("a successful result CLEARS staleness", async () => {
  storage.accounts = { [ORG_A]: { orgUuid: ORG_A, staleSince: NOW - 1000 } };
  await SW.handleReport(report());
  assert.equal(storage.accounts[ORG_A].staleSince, null);
});

// --- account-switch detection --------------------------------------------------- //

test("detectSwitch is exact", () => {
  assert.equal(SW.detectSwitch(ORG_A, ORG_B), true);
  assert.equal(SW.detectSwitch(ORG_A, ORG_A), false);
  assert.equal(SW.detectSwitch(null, ORG_A), false, "first sighting is not a switch");
  assert.equal(SW.detectSwitch(ORG_A, null), false);
});

test("an account switch toasts with its own kind and updates lastActiveOrg", async () => {
  storage.accounts = {};
  storage.lastToast = {};
  await SW.handleReport(report());                  // establishes ORG_A active
  calls.notifications.length = 0;
  await SW.handleReport(report({
    orgs: [{ uuid: ORG_B, name: NAME_B }, { uuid: ORG_A, name: NAME_A }],
    activeUuid: ORG_B,
    results: [{ orgUuid: ORG_B, ok: true, usage: { five_hour: { utilization: 20 } } }],
  }));
  assert.equal(storage.lastActiveOrg, ORG_B);
  assert.ok(calls.notifications.some((n) => n.title.startsWith("Account switched")),
    JSON.stringify(calls.notifications));
  assert.ok(storage.lastToast[`${ORG_B}:switch`], "the switch kind is recorded for dedup");

  // The same switch reported again within the dedup window is silent.
  calls.notifications.length = 0;
  await SW.handleReport(report({
    orgs: [{ uuid: ORG_B, name: NAME_B }],
    activeUuid: ORG_B,
    results: [{ orgUuid: ORG_B, ok: true, usage: { five_hour: { utilization: 20 } } }],
  }));
  assert.deepEqual(calls.notifications, [],
    "no switch toast re-fire within the dedup window");
});

test("onMessage answers synchronously and routes both message types", () => {
  let answered = null;
  const keepOpen = SW.onMessage({ type: "cu:usage-report", results: [] }, {}, (r) => { answered = r; });
  assert.equal(keepOpen, false, "synchronous answer -- the channel closes");
  assert.deepEqual(answered, { ok: true });

  answered = null;
  SW.onMessage({ type: "cu:probe-request" }, {}, (r) => { answered = r; });
  assert.deepEqual(answered, { ok: true });

  answered = null;
  SW.onMessage({ type: "mystery" }, {}, (r) => { answered = r; });
  assert.equal(answered, null, "unknown messages are ignored, not answered");
});
