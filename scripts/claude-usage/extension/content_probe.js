// content_probe.js -- page-context fetcher for claude.ai usage (classic CS).
//
// WHY A CONTENT SCRIPT: the usage API is cookie-auth'd and the session cookie
// is SameSite-protected, so the fetch MUST run in the page's own context with
// credentials:"include". A service-worker fetch may drop those cookies
// (unmeasured, so we design for the safe path) -- the service worker NEVER
// fetches claude.ai; it only relays and stores what this probe reports.
//
// On every page load (document_idle) it fetches the org list and then each
// org's usage (capped), and sends one `cu:usage-report` message to the
// service worker. It also answers `cu:probe` requests, which is how the
// worker's 15-minute alarm re-probes while a claude.ai tab exists.
//
// Every failure is REPORTED, never thrown and never toasted from here: a 401
// (logged out) or 403 must mark accounts stale SILENTLY, and only the service
// worker decides what a failure means.
//
// Test hooks: under node there is no `document`, so nothing auto-runs; the
// pure protocol functions are exposed as `globalThis.__CU_PROBE__`.

(function () {
  "use strict";

  // Don't hammer: an account list beyond this is fetched no further (defensive
  // cap -- recon saw a handful of orgs per user).
  var ORG_FETCH_CAP = 8;

  // 🔴 ONE PAGE LOAD USED TO RUN THE WHOLE PROBE TWICE. autoRun() fires at
  // document_idle, and independently the service worker's onTabUpdated fires
  // at status "complete" and sends `cu:probe` -- so every claude.ai open cost
  // 2x(/api/organizations + one /usage per org), roughly four requests where
  // two would do, against a cookie-authed endpoint with no documented rate
  // budget.
  //
  // The worker's own PROBE_MIN_INTERVAL_MS gates its ASK against other asks;
  // it cannot see this auto-run at all, because the two live in different
  // contexts. So the dedup has to be here, where both paths converge.
  //
  // Matches the worker's constant deliberately. The 15-minute alarm re-probe
  // is far outside this window and is unaffected -- which is the case to
  // check first if this number is ever raised.
  var MIN_RUN_GAP_MS = 30 * 1000;
  var lastRunAt = 0;

  /** True when a probe ran recently enough that another would be redundant. */
  function tooSoon(now) {
    return lastRunAt !== 0 && now - lastRunAt < MIN_RUN_GAP_MS;
  }

  function classifyStatus(status) {
    if (status === 401) return "unauthorized";
    if (status === 403) return "forbidden";
    if (!status) return "network";
    if (status === 200) return "bad-schema"; // HTTP fine, JSON/shape not
    return "http";
  }

  /** /api/organizations body -> [{uuid, name}] (defensive; junk rows dropped). */
  function validateOrgs(body) {
    if (!Array.isArray(body)) return [];
    const out = [];
    for (const row of body) {
      if (row && typeof row === "object" && typeof row.uuid === "string" && row.uuid) {
        out.push({
          uuid: row.uuid,
          name: typeof row.name === "string" ? row.name : null,
          // Preserved so pickActiveOrg can run on the VALIDATED list —
          // picking from the raw body could select an org that validation
          // then drops (empty uuid), leaving lastActiveOrg with no record.
          isActive: row.is_active === true || row.active === true,
        });
      }
    }
    return out;
  }

  /**
   * Which org claude.ai is currently operating as. Runs on the VALIDATED
   * org list (rows all carry a real uuid): the API row may claim activity
   * (is_active / active); when it does not, the first valid org is the
   * fallback. Never throws on a weird body.
   */
  function pickActiveOrg(orgs) {
    if (!Array.isArray(orgs)) return null;
    for (const o of orgs) {
      if (o && o.isActive === true) return o.uuid;
    }
    return orgs.length ? orgs[0].uuid : null;
  }

  /**
   * One page-context fetch with the session cookie. Resolves to
   * {ok:true, body} or {ok:false, status, kind}. JSON parse / shape failures
   * on a 200 are "bad-schema", not success.
   */
  async function fetchJson(url) {
    let res;
    try {
      res = await fetch(url, { credentials: "include" });
    } catch (e) {
      return { ok: false, status: 0, kind: "network" };
    }
    if (!res.ok) return { ok: false, status: res.status, kind: classifyStatus(res.status) };
    try {
      return { ok: true, status: 200, body: await res.json() };
    } catch (e) {
      return { ok: false, status: 200, kind: "bad-schema" };
    }
  }

  /**
   * The whole probe. Runs BOTH endpoints from page context, never throws, and
   * always resolves to a report-shaped object.
   */
  async function runProbe() {
    const fetchedAt = Date.now();
    const orgsRes = await fetchJson("/api/organizations");
    if (!orgsRes.ok) {
      return {
        type: "cu:usage-report",
        fetchedAt,
        orgs: [],
        activeUuid: null,
        results: [],
        orgsError: { status: orgsRes.status, kind: orgsRes.kind },
      };
    }
    const orgs = validateOrgs(orgsRes.body);
    const activeUuid = pickActiveOrg(orgs);
    const list = orgs.slice(0, ORG_FETCH_CAP);
    const results = [];
    for (const org of list) {
      const usageRes = await fetchJson(`/api/organizations/${encodeURIComponent(org.uuid)}/usage`);
      if (usageRes.ok) {
        results.push({ orgUuid: org.uuid, ok: true, usage: usageRes.body });
      } else {
        results.push({ orgUuid: org.uuid, ok: false, status: usageRes.status, kind: usageRes.kind });
      }
    }
    return { type: "cu:usage-report", fetchedAt, orgs: list, activeUuid, results };
  }

  function report(report0) {
    try {
      const sending = chrome.runtime.sendMessage(report0);
      if (sending && typeof sending.catch === "function") {
        sending.catch(function () { /* worker asleep or gone; next event retries */ });
      }
    } catch (e) { /* extension context invalidated (reload) -- nothing to do */ }
  }

  /** The ONE place a probe is started. Stamps lastRunAt BEFORE the await so a
   * second caller in the same turn is already gated -- stamping after the
   * fetch would leave the whole request window unguarded, which is exactly
   * the interval the two callers land in. */
  function startProbe(now) {
    lastRunAt = now;
    return runProbe().then(report, function () { /* never let a probe reject */ });
  }

  function autoRun() {
    startProbe(Date.now());
  }

  if (typeof chrome !== "undefined" && chrome.runtime && chrome.runtime.onMessage) {
    chrome.runtime.onMessage.addListener(function (msg, sender, sendResponse) {
      if (msg && msg.type === "cu:probe") {
        var now = Date.now();
        // Answer synchronously either way: the worker only needs to know the
        // message was received, and telling it `skipped` lets a future
        // caller distinguish "no content script" from "already fresh".
        if (tooSoon(now)) {
          sendResponse({ ok: true, skipped: "too-soon" });
          return false;
        }
        startProbe(now);
        sendResponse({ ok: true });
      }
      return false; // always a synchronous answer
    });
  }

  if (typeof document !== "undefined") {
    autoRun();
  }

  // --- node test surface ------------------------------------------------
  globalThis.__CU_PROBE__ = {
    ORG_FETCH_CAP,
    MIN_RUN_GAP_MS,
    classifyStatus,
    validateOrgs,
    pickActiveOrg,
    fetchJson,
    runProbe,
    tooSoon,
    startProbe,
    // Read/reset the gate so a test can drive the window explicitly rather
    // than sleeping through it.
    _lastRunAt: function () { return lastRunAt; },
    _setLastRunAt: function (v) { lastRunAt = v; },
  };
})();
