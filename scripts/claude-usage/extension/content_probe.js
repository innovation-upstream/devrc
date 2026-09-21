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
// 🔴 AND IT WATCHES FOR AN IN-TAB ACCOUNT SWITCH, because nothing else does.
// REPORTED BY THE OPERATOR: switching accounts left the widget showing the
// previous account until he did a full page reload, and cycling through
// several accounts changed nothing at all. The widget was never the fault --
// it already repaints on a 30s tick and on chrome.storage.onChanged. The data
// under it was stale, because EVERY trigger that refreshes it needs a
// document load:
//
//   autoRun (document_idle)           a real page load
//   SW onTabUpdated status=complete   a real navigation completing
//   SW onTabActivated                 switching to a different TAB
//   SW alarm cu-reprobe               every 15 minutes
//
// claude.ai is an SPA, so switching accounts inside one tab fires none of the
// first three, and the numbers can only move on the 15-minute alarm. That is
// precisely "requires a full page reload".
//
// The watcher below closes it WITHOUT a new permission and without polling the
// expensive endpoint. `/api/organizations` is one cheap request and it names
// the active org; the per-org `/usage` fan-out is the costly part. So a trigger
// runs the CHEAP half, compares the active org against the last one we
// reported, and only then pays for a full probe -- handing over the org list it
// already fetched, so a detected switch costs exactly what a page load costs
// today and not one request more.
//
// 🔴 THE BASELINE IS IN-MEMORY ON PURPOSE, not read back from storage. It is
// set in ONE place (`runAndReport`), which is also the only place a report is
// ever sent, so the comparison cannot drift from what the worker was actually
// told. Module scope dies with the page and a page load runs a full probe
// anyway, so there is nothing to persist. A null baseline means "no successful
// probe yet" and deliberately ALLOWS a probe, which is what makes the watcher
// self-healing after a failed initial fetch.
//
// Every failure is REPORTED, never thrown and never toasted from here: a 401
// (logged out) or 403 must mark accounts stale SILENTLY, and only the service
// worker decides what a failure means. 🔴 The cheap check is the ONE exception
// that reports nothing: when its org fetch fails it has learned nothing, and
// marking accounts stale off a probe we chose to make would turn a blip into a
// fake logout. Silence there is the conservative answer, not a dropped error.
//
// Test hooks: under node there is no `document`, so nothing auto-runs; the
// pure protocol functions are exposed as `globalThis.__CU_PROBE__`.

(function () {
  "use strict";

  // Don't hammer: an account list beyond this is fetched no further (defensive
  // cap -- recon saw a handful of orgs per user).
  var ORG_FETCH_CAP = 8;

  // --- the account-switch watcher's budget ---------------------------------- //
  //
  // These three numbers are the whole cost of the feature, so they are stated
  // together rather than scattered at their use sites.
  //
  // MIN_INTERVAL is the floor between two CHEAP checks. It exists because the
  // triggers legitimately overlap -- `visibilitychange` and `focus` both fire
  // when a tab is raised -- and because an href change on claude.ai is a noisy
  // signal: every new chat and every conversation click moves the URL. The
  // floor converts that noise into a bounded rate, so heavy clicking costs at
  // most one `/api/organizations` per 10s and nothing else.
  var ACTIVE_CHECK_MIN_INTERVAL_MS = 10 * 1000;

  // The unattended ceiling: with no interaction at all, a switch is still
  // noticed within a minute. This is what makes the 15-minute alarm no longer
  // the fastest path to a correct number.
  var ACTIVE_CHECK_TICK_MS = 60 * 1000;

  // 🔴 A STRING COMPARE, NOT A FETCH. This tick reads `location.href` and
  // nothing else; it reaches the network only when the URL actually changed
  // AND the MIN_INTERVAL floor allows it. Polling is used here because a
  // content script cannot see the page's own `history.pushState` calls -- an
  // isolated world shares the DOM but not the page's globals, so patching
  // history from here observes nothing, and `popstate` covers only back and
  // forward. Two seconds is what makes a switch feel immediate rather than
  // "eventually"; it is affordable precisely because it costs no request.
  var HREF_POLL_MS = 2 * 1000;

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
   *
   * `pre` is an optional already-validated `{orgs, activeUuid}` from a caller
   * that has just fetched `/api/organizations` itself -- today only
   * `checkActiveOrg`. Passing it SKIPS the org fetch, which is the entire
   * reason a detected account switch costs no more than a page load does.
   * 🔴 It must be the VALIDATED shape, never a raw API body: `activeUuid` is
   * required to have been picked from the same validated list, or the probe
   * can report an active org it then drops (the round-1 `pickActiveOrg` bug,
   * reintroduced through a side door).
   *
   * `fetchedAt` is stamped HERE even when `pre` is supplied, because it is the
   * snapshot time of the USAGE numbers -- which are fetched below either way.
   * Carrying the cheap check's earlier clock forward would backdate every
   * switch-triggered record by however long the comparison took.
   */
  async function runProbe(pre) {
    const fetchedAt = Date.now();
    let orgs;
    let activeUuid;
    if (pre && Array.isArray(pre.orgs)) {
      orgs = pre.orgs;
      activeUuid = typeof pre.activeUuid === "string" ? pre.activeUuid : null;
    } else {
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
      orgs = validateOrgs(orgsRes.body);
      activeUuid = pickActiveOrg(orgs);
    }
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

  // --- the account-switch watcher ------------------------------------------- //

  // The last active org we successfully REPORTED. null = no successful probe
  // yet, which allows a check (see the header).
  var lastSeenActiveUuid = null;
  var lastCheckAt = 0;          // floor for the cheap check, not for the probe
  var probeInFlight = false;    // a check must not race the page-load probe
  var lastHref = null;
  var timers = [];

  /**
   * The ONLY place a report is sent, and therefore the only place the
   * switch baseline is updated. Keeping those two facts in one function is
   * what makes `lastSeenActiveUuid` mean "what the worker was last told"
   * rather than "what some code path happened to observe".
   *
   * 🔴 A FAILED ORG FETCH MUST NOT MOVE THE BASELINE. `orgsError` reports
   * `activeUuid: null`; writing that through would make the next cheap check
   * see a null baseline and fire a full probe, so a flaky network would
   * escalate itself into a probe on every tick. The old value is kept, which
   * leaves the next check comparing against the last thing we actually knew.
   */
  function runAndReport(pre) {
    probeInFlight = true;
    return runProbe(pre).then(function (r) {
      probeInFlight = false;
      if (r && !r.orgsError && typeof r.activeUuid === "string" && r.activeUuid) {
        lastSeenActiveUuid = r.activeUuid;
      }
      report(r);
      return r;
    }, function () {
      probeInFlight = false;    // never let a probe reject, and never wedge
    });
  }

  /**
   * The cheap half: has the active account changed since our last report?
   *
   * Resolves to a `{skipped: <reason>}` or `{probed: true}` object rather than
   * a bare boolean, so a test can tell the reasons APART -- "we were rate
   * limited", "the fetch failed" and "the account genuinely did not change"
   * are three different states that all mean "no probe ran", and a boolean
   * would collapse them into one unfalsifiable answer.
   */
  async function checkActiveOrg(now) {
    if (probeInFlight) return { skipped: "probe-in-flight" };
    if (now - lastCheckAt < ACTIVE_CHECK_MIN_INTERVAL_MS) return { skipped: "rate-limited" };
    lastCheckAt = now;
    const res = await fetchJson("/api/organizations");
    // Silent by design; see the header. The SW's alarm is still the backstop.
    if (!res.ok) return { skipped: "orgs-failed", status: res.status, kind: res.kind };
    const orgs = validateOrgs(res.body);
    const activeUuid = pickActiveOrg(orgs);
    // No orgs at all: there is nothing to probe, and probing anyway would make
    // an empty account fetch on every single tick.
    if (!activeUuid) return { skipped: "no-active-org" };
    if (activeUuid === lastSeenActiveUuid) return { skipped: "unchanged" };
    await runAndReport({ orgs, activeUuid });
    return { probed: true, activeUuid };
  }

  /**
   * Is the extension still there? `chrome.runtime` goes away when the
   * extension is reloaded, updated or uninstalled with this page open, which
   * is a NORMAL event during development, not an error.
   *
   * 🔴 THE WATCHER MUST NOTICE, because it is the first thing here that
   * outlives its own usefulness. `report()` already swallows the resulting
   * throw, so without this check a page left open across an extension reload
   * would keep fetching `/api/organizations` every 60s for the rest of the
   * tab's life and post every result into a void. content_widget.js has the
   * same problem and solves it with `retire()`; this is that, for the prober.
   */
  function extAlive() {
    try {
      return Boolean(typeof chrome !== "undefined" && chrome.runtime && chrome.runtime.id);
    } catch (e) { return false; }
  }

  function onMaybeSwitch() {
    if (!extAlive()) { stopWatchers(); return; }
    try {
      const p = checkActiveOrg(Date.now());
      if (p && typeof p.catch === "function") p.catch(function () { /* never throw into the page */ });
    } catch (e) { /* ditto */ }
  }

  function stopWatchers() {
    timers.forEach(function (t) { clearInterval(t); });
    timers = [];
  }

  /**
   * Wire the triggers. Split out of module scope so node can import this file
   * without starting timers, and so a test can drive the watcher against a
   * fake document.
   */
  function startWatchers() {
    if (typeof document === "undefined") return;
    try {
      document.addEventListener("visibilitychange", function () {
        if (!document.hidden) onMaybeSwitch();
      });
    } catch (e) { /* no document events; the ticks below still run */ }
    try {
      if (typeof window !== "undefined") window.addEventListener("focus", onMaybeSwitch);
    } catch (e) { /* ditto */ }

    try { lastHref = String(location.href); } catch (e) { lastHref = null; }
    timers.push(setInterval(function () {
      var href = null;
      try { href = String(location.href); } catch (e) { return; }
      if (href === lastHref) return;          // the common case: no request
      lastHref = href;
      if (typeof document !== "undefined" && document.hidden) return;
      onMaybeSwitch();
    }, HREF_POLL_MS));

    timers.push(setInterval(function () {
      if (typeof document !== "undefined" && document.hidden) return;
      onMaybeSwitch();
    }, ACTIVE_CHECK_TICK_MS));
  }

  function autoRun() {
    runAndReport();
  }

  if (typeof chrome !== "undefined" && chrome.runtime && chrome.runtime.onMessage) {
    chrome.runtime.onMessage.addListener(function (msg, sender, sendResponse) {
      if (msg && msg.type === "cu:probe") {
        runAndReport();
        sendResponse({ ok: true });
      }
      return false; // always a synchronous answer
    });
  }

  if (typeof document !== "undefined" && !globalThis.CLAUDE_USAGE_NO_AUTOSTART) {
    autoRun();
    startWatchers();
  }

  // --- node test surface ------------------------------------------------
  globalThis.__CU_PROBE__ = {
    ORG_FETCH_CAP,
    ACTIVE_CHECK_MIN_INTERVAL_MS,
    ACTIVE_CHECK_TICK_MS,
    HREF_POLL_MS,
    classifyStatus,
    validateOrgs,
    pickActiveOrg,
    fetchJson,
    runProbe,
    runAndReport,
    checkActiveOrg,
    startWatchers,
    // Test-only state control. `checkActiveOrg` is deliberately stateful (the
    // baseline and the rate-limit floor are the feature), so a fixture needs a
    // way to put it in a known state rather than depending on test order.
    __setState: function (s) {
      if (!s || typeof s !== "object") return;
      if ("lastSeenActiveUuid" in s) lastSeenActiveUuid = s.lastSeenActiveUuid;
      if ("lastCheckAt" in s) lastCheckAt = s.lastCheckAt;
      if ("probeInFlight" in s) probeInFlight = s.probeInFlight;
    },
    __getState: function () {
      return {
        lastSeenActiveUuid: lastSeenActiveUuid,
        lastCheckAt: lastCheckAt,
        probeInFlight: probeInFlight,
      };
    },
    extAlive,
    stopWatchers,
    onMaybeSwitch,
    __timerCount: function () { return timers.length; },
  };
})();
