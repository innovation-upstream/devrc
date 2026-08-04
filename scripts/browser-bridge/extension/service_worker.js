// service_worker.js — MV3 background worker for the browser-bridge command
// channel. SIBLING to the activity collector's SW; this one is a *command*
// executor, not a telemetry emitter.
//
// It long-polls the loopback rendezvous server (GET /poll), executes each
// command against the ACTIVE Brave tab via chrome.* APIs, and POSTs the result
// back (POST /result). A pending /poll fetch keeps the MV3 worker alive, so the
// long-poll IS the keepalive — a chrome.alarms tick (every ~1 min) is a
// belt-and-suspenders restart in case the worker was suspended between polls.
//
// The pure protocol logic (op set, validation, envelopes, backoff) lives in
// protocol.js and is unit-tested with `node --test`; this file is only the thin
// chrome.* glue that genuinely needs a real browser.
//
// Config: the port + bearer token are read from chrome.storage.local
// ("port","token"), set once from the extension's options-free setup (see
// README — you paste the token from ~/.config/browser-bridge/token). Defaults
// to port 8788.

import {
  ALLOWED_OPS, validateCommand, resultEnvelope, errorEnvelope, nextBackoffMs,
  pollHeaders, resultWithInstance, normalizeText, TEXT_MAX_BYTES_DEFAULT,
  classifyPollStatus, POLL_COMMAND, POLL_IDLE, POLL_SUPERSEDED,
  POLL_UNAUTHORIZED, SUPERSEDE_BACKOFF_MS, captureWithRetry,
  // `activate` op: bounded wait-for-load after foregrounding (pure + unit-tested).
  waitForTabLoad,
  // CDP (chrome.debugger) helpers — still used for screenshots + TOP-frame trusted
  // input (the pure, unit-tested decision layer).
  CDP_VERSION, withCdpSession, assertTabCdpReady, keyEventParams,
  clickPoint, cdpExceptionText, elementRectExpression, focusExpression, fullPageClip,
  // OOPIF-capable frame enumeration/injection (chrome.webNavigation + chrome.scripting):
  // reaches cross-origin out-of-process iframes where CDP getFrameTree could not.
  normalizeWebNavFrames, resolveWebNavFrame,
  frameReadHtmlFn, frameReadTextFn,
  frameClickFn, frameTypeFn, frameKeyFn,
  // CDP `eval --frame`: run an arbitrary JS STRING in the target frame's execution
  // context (chrome.scripting can only run a serialized FUNC — the #190 null bug).
  frameEvalExpressions, isCdpSyntaxError, matchCdpFrameId, resolveOopifSession,
  evalValueOrThrow,
  // hidden-tab self-announce (Gap 2): reads report the tab's visibilityState so a
  // throttled background read can't masquerade as a real outage.
  annotateVisibility,
  // upload op (Gap 1): resolve a file input + hand its ABSOLUTE path to CDP
  // DOM.setFileInputFiles (Chrome reads the file itself — no bytes cross the bridge).
  fileInputSelectorExpression, FILE_INPUT_CHECK_FN, basenameOf,
  // `wake` op / `--wake` reads: un-throttle a background tab via CDP WITHOUT
  // touching focus (the non-intrusive replacement for reflexive `activate`).
  clampWakeMs, applyWakeSteps, wakeProbeFn, WAKE_CDP_TEARDOWN,
  // The no-wedge guarantee, generalized past CDP: every await in the poll loop
  // body is raced against a wall-clock budget (see protocol.js for why and for
  // the CDP < exec < server-cmd_timeout ordering).
  promiseWithTimeout, EXEC_OP_BUDGET_MS, POLL_BUDGET_MS, RESULT_BUDGET_MS,
  LOOP_STALL_MS, STORAGE_BUDGET_MS,
  // `emulate` op: device emulation (viewport/touch/UA+UA-CH/media/geo/tz) that is
  // STICKY per tab because CDP overrides die at detach — see the EMULATION section
  // in protocol.js for the central problem and the safety property it buys.
  normalizeEmulation, emulationCdpSteps, applyEmulationSteps, emulationSummary,
  isTouchEmulated, touchTapEvents, DEVICE_PRESETS, PRESET_NAMES,
  annotateEmulatedRead, emulationCreateTimeSignature, documentPredatesEmulation,
  annotateDocumentPredates,
  // page context (shared by `context` op + enriched `text`/`html` results).
  parsePageContext, annotatePageContext,
  // annotated text: structured element extraction for `text --annotated`.
  annotatedTextFn, ANNOTATED_TEXT_MAX_ITEMS_DEFAULT, byteCapElements,
} from "./protocol.js";

// The BUILD MARKER (#324) — a generated LITERAL that travels with THIS module
// graph. Imported (not fetched, not read off the manifest) on purpose: see the
// header comment in build_id.js for why every runtime-read alternative
// reproduces the bug it fixes.
import { BUILD_MARKER } from "./build_id.js";

const DEFAULT_PORT = 8788;
let running = false;
// Wall-clock (Date.now) stamped at the START of each poll-loop iteration — NOT
// at its completion. That is the right sense for stall detection: a loop parked
// forever mid-iteration never stamps again, so the age of this value IS how long
// the current iteration has been running. (An end-of-iteration stamp would read
// the same for a wedge and for a loop that simply has not finished yet.)
// `running` is a LATCH — it says a loop was started, not that one is alive — so
// the keepalive alarm needs this independent signal to tell "wedged" from
// "idle-polling". null = the loop was only just kicked and has not stamped yet.
let lastLoopTickAt = null;
// Monotonically increasing loop generation. The keepalive force-restart bumps it,
// which retires any loop still parked on an abandoned await: that loop exits at
// its next generation check and its finally declines to clear `running` (it no
// longer owns the flag), so a force-restart can never leave two pollers racing.
let loopGeneration = 0;

// The stable per-profile auto-id: generated ONCE and persisted in
// chrome.storage.local so it survives service-worker restarts/reloads within
// this Brave profile. It is the routing key when no user label is set, and the
// server treats a new auto-id on an existing key as a supersede.
async function instanceId() {
  let { instanceId } = await chrome.storage.local.get("instanceId");
  if (!instanceId) {
    instanceId = crypto.randomUUID();
    await chrome.storage.local.set({ instanceId });
  }
  return instanceId;
}

// The extension's own manifest version — reported on every /poll so `whoami` can
// show which BUILD is loaded per instance. Best-effort (never throws; "" if the
// manifest is somehow unreadable, e.g. under a bare unit-test chrome mock).
function extensionVersion() {
  try {
    return (chrome.runtime.getManifest && chrome.runtime.getManifest().version) || "";
  } catch (e) {
    return "";
  }
}

// The BUILD MARKER of the code that is ACTUALLY EXECUTING (#324) — the one
// freshness signal that is not a statement about the load directory.
// `extensionVersion()` above and `extensionId()` below both describe the
// DIRECTORY: the version is read from the on-disk manifest at call time, and
// the id is derived from the load path. Measured 2026-08-04 — two profiles on
// one directory reported the same id, the same version and `extension_stale:
// false` while running different code. BUILD_MARKER is a literal in an imported
// module, so a stale worker reports the STALE value by construction.
// Best-effort ("" if the import is somehow absent, e.g. under a unit-test
// module mock) — the server treats "" as UNDECIDABLE, never as "current".
function buildMarker() {
  try {
    return (typeof BUILD_MARKER === "string" && BUILD_MARKER) || "";
  } catch (e) {
    return "";
  }
}

// `chrome.runtime.id` — the extension's ID, which for an UNPACKED extension is
// derived from the absolute directory Brave loaded it from. That makes it the
// only signal that distinguishes a repo-path load from a
// ~/.local/share/browser-bridge-ext/ load: both report the same manifest
// version, so version alone cannot answer "did the migration take?".
// Best-effort ("" if unavailable), like extensionVersion().
function extensionId() {
  try {
    return (chrome.runtime && chrome.runtime.id) || "";
  } catch (e) {
    return "";
  }
}

async function config() {
  const { port, token, label } = await chrome.storage.local.get(["port", "token", "label"]);
  return {
    port: port || DEFAULT_PORT,
    token: token || "",
    label: label || "",
    instanceId: await instanceId(),
    extVersion: extensionVersion(),
    extId: extensionId(),
    extBuild: buildMarker(),
  };
}

function base(port) {
  return `http://127.0.0.1:${port}`;
}

function authHeaders(token) {
  return { Authorization: `Bearer ${token}`, "Content-Type": "application/json" };
}

// --- tab-targeting helpers ------------------------------------------------- //
async function activeTab() {
  const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  if (!tab) throw new Error("no_active_tab");
  return tab;
}

// The tab an op runs against. When the server injected a `tabId` (the caller
// owns a tab, or passed --tab), use THAT tab — this is the per-session isolation
// that stops two Claude sessions from clobbering one shared active tab. With no
// tabId (a one-shot read by a session that never `open`ed), fall back to the
// active tab — exactly the historical behaviour.
async function targetTab(cmd) {
  if (cmd && cmd.tabId != null) {
    try {
      return await chrome.tabs.get(cmd.tabId);
    } catch (e) {
      throw ownedTabGone(cmd.tabId);       // the tab was closed out-of-band
    }
  }
  return activeTab();
}

// THE single "this tab is gone" reaction: drop any emulation state for it, then
// produce the standard error. Two layers detect the same condition — targetTab (on
// every op) and withCdp's attach (the CDP-readiness check) — and the CLEANUP must
// not be written twice; one copy would inevitably be the one that got a later fix.
//
// Clearing here rather than relying on chrome.tabs.onRemoved matters because
// onRemoved is an event whose timing we do not control, and it does not fire at
// all for a tab that was already gone when we first looked. Chrome RECYCLES tabIds,
// so a stale entry is not merely untidy — the next tab to inherit the id would be
// silently emulated by a session that never asked for it.
function ownedTabGone(tabId) {
  forgetTab(tabId);
  return new Error("owned_tab_gone");
}

// Read the tab's `document.visibilityState` (top frame) so a read op can self-
// announce a hidden/background tab (Gap 2). visibilityState reflects the TAB, and
// an OOPIF's document follows the tab, so the top-frame read is correct for
// --frame reads too. Best-effort: any failure (discarded tab, no host permission)
// returns null and the read simply omits the field — it NEVER fails the read.
async function tabVisibilityState(tabId) {
  try {
    const [inj] = await chrome.scripting.executeScript({
      target: { tabId },
      func: () => document.visibilityState,
    });
    return inj ? inj.result : null;
  } catch (e) {
    return null;
  }
}

// --- CDP (chrome.debugger) glue -------------------------------------------- //
// The pure, security-relevant CDP logic (attach-scope validation, always-detach
// orchestration, frame/key/coord math, typed-op-only surface) lives in protocol.js
// and is unit-tested there. This is the thin chrome.debugger side-effect layer.
//
// Tabs we currently hold a chrome.debugger attach on. `withCdp` is the ONLY code
// that attaches and it ALWAYS detaches (withCdpSession's finally), so this set is
// normally empty between ops; chrome.debugger.onDetach clears it if Chrome detaches
// us out-of-band (tab crash/close, or the user hitting the debug banner's Cancel).
const cdpAttached = new Set();

// --- EMULATION state (the sticky half) -------------------------------------- //
// tabId -> the normalized emulation state from normalizeEmulation(). This is the
// ONLY mutable emulation state in the extension.
//
// It is IN-MEMORY on purpose (not chrome.storage): CDP overrides die at detach, so
// state that outlived the service worker would describe an emulation that is not
// actually applied anywhere — a lie that survives a restart is worse than a
// forgotten one. Losing it on worker eviction is the correct, safe failure: the
// operator's tab is already un-emulated at that point.
//
// Emptied by: `emulate --reset`, `close`, chrome.tabs.onRemoved, and a failed
// tabs.get during CDP attach (the tab went away out-of-band).
const emulationState = new Map();

function emulationFor(tabId) {
  return tabId == null ? null : (emulationState.get(tabId) || null);
}

function clearEmulation(tabId) {
  if (tabId != null) emulationState.delete(tabId);
}

// --- WHICH EMULATION THE TAB'S CURRENT DOCUMENT WAS CREATED UNDER ------------ //
// tabId -> emulationCreateTimeSignature() of the state in force when THIS bridge
// navigated the tab. Absent = "we did not create this document under emulation",
// which is the conservative reading (the `emulate` hint fires).
//
// It is a SEPARATE map from emulationState on purpose, and `clearEmulation` does
// NOT touch it: the two describe different things. `emulate --reset` stops
// emulating, but the document that a previous emulated `nav` created still HAS
// touch installed — forgetting that would make a later re-`emulate` cry wolf.
// It is dropped only when the DOCUMENT can no longer exist: the tab is gone,
// removed, or swapped (forgetTab below).
//
// ⚠ HONEST LIMITATION: only the bridge's own `nav` writes here. Every other
// navigation is unobserved, so the record can go stale and the hint stay silent.
// The one that bites is the bridge's OWN `click`/`key`: a click that follows a
// link commits its new document AFTER that op's CDP session detaches, so the
// document is created UN-emulated while this map still says "built emulated". An
// operator's hand navigation and a page-initiated one (meta-refresh, location=)
// are the same class. Documented in
// reference/emulation.md; chrome.tabs.onUpdated was deliberately not used, since
// it cannot distinguish our own CDP navigation from an out-of-band one without a
// second piece of mutable state to get wrong.
const documentEmulation = new Map();

function recordDocumentEmulation(tabId, state) {
  if (tabId != null) {
    documentEmulation.set(tabId, emulationCreateTimeSignature(state));
  }
}

// The tab itself is gone/retired — drop BOTH maps. Emulation state and the
// document record have different lifetimes everywhere else, but a recycled tabId
// must inherit neither.
function forgetTab(tabId) {
  clearEmulation(tabId);
  if (tabId != null) documentEmulation.delete(tabId);
}

// Drop the state of a tab that no longer exists. Registered at module scope so it
// runs for EVERY tab close, not only a `close` op — a tab the operator closed by
// hand must not leave an entry behind that a recycled tabId could later inherit.
try {
  if (typeof chrome !== "undefined" && chrome.tabs && chrome.tabs.onRemoved) {
    chrome.tabs.onRemoved.addListener((tabId) => forgetTab(tabId));
  }
  // onReplaced fires when a tab is SWAPPED for another (a prerender/instant
  // navigation activating): the old tabId is retired WITHOUT onRemoved firing. Left
  // unhandled that strands a Map entry on an id Chrome can recycle. Narrow — the
  // tab also loses its server-side ownership, and `ownedTabGone` catches the common
  // vanish — but it costs two lines and the restart is already being paid for.
  //
  // The emulation deliberately does NOT follow the tab to its new id: the swapped-in
  // document is a different page that was rendered WITHOUT the overrides, so
  // carrying the state across would claim an emulation that was never applied.
  if (typeof chrome !== "undefined" && chrome.tabs && chrome.tabs.onReplaced) {
    chrome.tabs.onReplaced.addListener((addedTabId, removedTabId) => {
      forgetTab(removedTabId);
      forgetTab(addedTabId);
    });
  }
} catch (e) { /* bare unit-test global with no chrome.tabs — nothing to hook */ }

function sendCdp(target, method, params) {
  return chrome.debugger.sendCommand(target, method, params || {});
}

// Attach chrome.debugger to `tabId`, run `run(send)`, and ALWAYS detach. `url` is
// the target tab's URL, validated BEFORE attach by withCdpSession (a privileged /
// other-surface tab is refused, never attached — the STRICT attach-scope invariant).
// The `send` handed to `run` takes an optional 3rd arg `sessionId` so a command can
// target a flat auto-attached sub-session (an OOPIF target) — still bounded by the
// #189 per-command timeout. `globalThis.BROWSER_BRIDGE_CDP_TIMEOUTS` is a TEST-ONLY
// hook to shrink the (8s) budgets so a no-wedge test settles in ms; undefined in
// production → the real CDP_* budgets.
async function withCdp(tabId, url, run) {
  const target = { tabId };
  return withCdpSession({
    url,
    timeouts: (typeof globalThis !== "undefined" && globalThis.BROWSER_BRIDGE_CDP_TIMEOUTS)
      || undefined,
    attach: async () => {
      // Fail fast on a discarded/unloaded tab (no live renderer → attach would
      // hang forever). withCdpSession's per-call timeouts are the backstop for any
      // other hang; this turns the common case into an immediate clear error.
      let tab;
      try { tab = await chrome.tabs.get(tabId); }
      catch (e) { throw ownedTabGone(tabId); }   // clears emulation state too
      assertTabCdpReady(tab);
      await chrome.debugger.attach(target, CDP_VERSION);
      cdpAttached.add(tabId);
    },
    detach: async () => {
      // ⚠ ORDER IS LOAD-BEARING: delete from the tracking set only AFTER the detach
      // actually resolves. The old order deleted first, so a HUNG or failing detach
      // (tab mid-crash, wedged renderer — withCdpSession's safeDetach bounds and
      // SWALLOWS it) left the tab genuinely attached while `cdpAttached` claimed it
      // was not: a leaked attachment invisible to every consumer of that set.
      // Failing to detach must leave the tab TRACKED so the leak is at least
      // observable. chrome.debugger.onDetach still clears it on an out-of-band
      // detach, so this cannot go stale in the normal case.
      await chrome.debugger.detach(target);
      cdpAttached.delete(tabId);
    },
    // Raw send — withCdpSession wraps it in the per-command timeout before handing
    // it to `run`, so a single hung CDP command can't wedge the SW. A non-null
    // `sessionId` targets a flat auto-attached OOPIF sub-session (Debuggee
    // {tabId, sessionId}); omitted → the tab's top session.
    send: (method, params, sessionId) =>
      sendCdp(sessionId != null ? { ...target, sessionId } : target, method, params),
    // ⚠ THE STICKY RE-APPLICATION CHOKE POINT. Every CDP op in this file goes
    // through withCdp, so applying the tab's emulation HERE — once, before `run`
    // — makes every one of them emulation-aware without a single call site
    // knowing about it. That is deliberate: patching `screenshot` and `click`
    // individually would fix today's two visible cases and regenerate the bug at
    // the next CDP op someone adds. One rule, one place.
    //
    // Apply-then-act ordering matters and is asserted by the tests: the overrides
    // must be live BEFORE anything measures layout (a --fullpage clip's
    // Page.getLayoutMetrics, a click's element rect) or navigates.
    //
    // A failing apply propagates normally — withCdpSession still detaches in its
    // finally and execute() turns it into an ordinary error envelope, so a bad
    // emulation can fail an op but can never wedge the poll loop.
    run: async (send) => {
      const steps = emulationCdpSteps(emulationFor(tabId));
      if (steps.length) await applyEmulationSteps(send, steps);
      return run(send);
    },
  });
}

// Evaluate `expression` in a specific execution context (an isolated world) via CDP
// Runtime.evaluate; returns its value. `contextId` undefined → the tab's DEFAULT
// (top-frame) context (JSON drops the undefined key). Throws on a runtime exception.
async function cdpEval(send, contextId, expression) {
  const res = await send("Runtime.evaluate",
    { expression, contextId, returnByValue: true, awaitPromise: true });
  if (res.exceptionDetails) throw new Error(cdpExceptionText(res.exceptionDetails));
  return res.result ? res.result.value : undefined;
}

// --- `wake`: un-throttle the owned tab via CDP, WITHOUT stealing focus ------- //
// Applies the fixed WAKE_CDP_STEPS (see protocol.js for the measured Chromium
// behaviour that dictates them), holds the un-throttle for a bounded settle so the
// page gets real animation frames, and probes what it achieved — all INSIDE one
// attached session, because the un-throttled state is measured NOT to survive
// detach. `run` (optional) is executed after the settle and still inside the woken
// session: that is how `--wake` reads get an un-throttled read. Returns
// { applied, skipped, settleMs, probe, value } where `value` is `run`'s result.
//
// Own-tab-scoped exactly like every other CDP op: the caller passes the tab the
// server routed to, withCdp refuses a privileged scheme BEFORE attaching and
// ALWAYS detaches in its `finally`. No raw-CDP passthrough — the method set is
// fixed data (WAKE_CDP_STEPS) plus the same Runtime.evaluate the read ops use.
async function cdpWake(tabId, tabUrl, waitMs, run) {
  const settleMs = clampWakeMs(waitMs);
  return withCdp(tabId, tabUrl, async (send) => {
    const { applied, skipped } = await applyWakeSteps(send);
    // The probe runs via chrome.scripting (ISOLATED world), not CDP — a main-world
    // probe could be shadowed by a hostile page, making `woke` a page-controlled
    // claim. Best-effort: a probe failure never fails the wake.
    const probeOnce = async () => {
      try {
        const [inj] = await chrome.scripting.executeScript(
          { target: { tabId }, func: wakeProbeFn });
        return inj ? inj.result : null;
      } catch (e) { return null; }
    };
    // EXPLICIT teardown in a `finally`: never rely on detach to revert focus
    // emulation (see WAKE_CDP_TEARDOWN — that revert is an Emulation-domain
    // implementation detail, and a hung detach is swallowed, so the emulated-focus
    // window could otherwise outlive the op indefinitely). This bounds the window
    // to exactly the settle+probe+read, on EVERY exit path including a throw.
    try {
      if (settleMs > 0) await sleep(settleMs);
      const probe = await probeOnce();
      const value = run ? await run(send) : undefined;
      return { applied, skipped, settleMs, probe, value };
    } finally {
      // Best-effort: a teardown failure must never mask the real result/error.
      try { await send(WAKE_CDP_TEARDOWN.method, WAKE_CDP_TEARDOWN.params); }
      catch (e) { /* detach is the backstop */ }
    }
  });
}

// --- OOPIF-capable frame glue (chrome.webNavigation + chrome.scripting) ------- //
// Enumerate ALL of `tabId`'s frames — same-process AND cross-origin OOPIFs — as the
// compact metadata list the `frames` op returns. getAllFrames is tab-scoped, so the
// list can only ever describe frames of THIS tab (the security scope for `--frame`).
async function framesForTab(tabId) {
  const raw = await chrome.webNavigation.getAllFrames({ tabId });
  return normalizeWebNavFrames(raw || []);
}

// Resolve a caller `--frame <sel>` to the FRAME OBJECT ({frameId,url,parentFrameId})
// within `tabId`. Throws frame_not_found / frame_not_specified. Confined to this tab by
// construction (framesForTab is tab-scoped). Callers use `.frameId` to inject and
// `.url` both to report the frame's own url AND (for eval) to locate the frame's CDP
// execution context by URL.
async function resolveFrame(tabId, frameSel) {
  const frames = await framesForTab(tabId);
  return resolveWebNavFrame(frames, frameSel);
}

// Inject `func(...args)` INTO one resolved frame (by numeric frameId) of `tabId` via
// chrome.scripting — the OOPIF-reaching path (works on a cross-origin frame given the
// extension's <all_urls> host permission), no chrome.debugger/banner. Returns the
// injected function's (structured-cloned) result. A frameId is confined to `tabId`;
// executeScript cannot escape the target tab's frames.
async function execInFrame(tabId, frameId, func, args) {
  const results = await chrome.scripting.executeScript({
    target: { tabId, frameIds: [frameId] },
    func,
    args: args || [],
  });
  const inj = Array.isArray(results) ? results[0] : undefined;
  return inj ? inj.result : undefined;
}

// THE single shared cross-origin-OOPIF session resolver — used by BOTH `eval --frame`
// and `upload --frame` so their depth/cap/ambiguity/timeout semantics can never diverge
// (they used to duplicate the auto-attach block verbatim, and only handled a DIRECT
// child). All the decision logic lives in protocol.js `resolveOopifSession` (pure +
// unit-testable); this is only the chrome.debugger listener glue. The listener is
// removed inside resolveOopifSession's own `finally`.
// `globalThis.BROWSER_BRIDGE_OOPIF_LIMITS` is a TEST-ONLY hook to shrink the caps/waits
// so a cap/propagation test settles in ms; undefined in production → the real bounds.
// A frame that never appears within the bounds → `frame_not_found:<url> cascade[…]` —
// the SAME error prefix the single-level code returned, now with a bounded diagnostic
// readout of what the cascade actually observed (types, tab/parent provenance, depths,
// which sessions we auto-attached, and which loop exit fired). Never a silent null,
// never a hang. The diagnostic is CALLER-facing only — telemetry stays metadata-only.
async function cdpOopifSession(send, tabId, frame) {
  return resolveOopifSession({
    send,
    tabId,                 // own-tab gate on the GLOBAL onEvent listener (fails closed)
    targetUrl: frame.url,
    label: frame.url || frame.frameId,
    addListener: (fn) => chrome.debugger.onEvent.addListener(fn),
    removeListener: (fn) => chrome.debugger.onEvent.removeListener(fn),
    limits: (typeof globalThis !== "undefined" && globalThis.BROWSER_BRIDGE_OOPIF_LIMITS)
      || undefined,
  });
}

// Evaluate an arbitrary JS STRING inside one resolved frame of `tabId` via CDP
// Runtime.evaluate — the RELIABLE path for `eval --frame` (chrome.scripting can only
// run a serialized FUNC, so it can't evaluate a user string: the #190 null-as-success
// bug). Works for a SAME-PROCESS frame AND a cross-origin OOPIF (a separate target):
//   1. attach chrome.debugger to the OWNED tab (withCdp → #187 own-tab-only scope +
//      #189 bounded timeouts + discarded-tab fail-fast);
//   2. SAME-PROCESS: the frame is in the top session's Page.getFrameTree → its CDP
//      frameId → Page.createIsolatedWorld → an executionContextId → Runtime.evaluate;
//   3. OOPIF: NOT in the top frame tree → the shared cdpOopifSession resolver drives a
//      BOUNDED RECURSIVE Target.setAutoAttach({autoAttach,flatten}) cascade (re-armed on
//      each attached child session, since setAutoAttach is not recursive) until the
//      wanted frame's target appears — so a NESTED/grandchild OOPIF resolves too →
//      Runtime.evaluate in that flat session's default context.
// NEVER SILENT-NULL: a genuine null/undefined result is returned AS a value, but a
// FAILURE to execute (frame not resolvable / exceptionDetails) is a CLEAR op error
// (frame_not_found / frame_eval_failed:<reason>) via evalValueOrThrow. `frame` is the
// resolved {frameId,url} object; matching is by `frame.url` (the numeric webNavigation
// frameId does not map 1:1 to a CDP frame/target).
async function cdpFrameEval(tabId, tabUrl, frame, src) {
  const { expression, fallback } = frameEvalExpressions(src);
  return withCdp(tabId, tabUrl, async (send) => {
    // Try `expression` (expression form); on a CDP SyntaxError retry `fallback` (the
    // statement form). One evaluate per form → a side effect never double-runs.
    const evaluate = async (sessionId, contextId) => {
      const params = { expression, returnByValue: true, awaitPromise: true };
      if (contextId != null) params.contextId = contextId;
      let res = await send("Runtime.evaluate", params, sessionId);
      if (res && res.exceptionDetails && isCdpSyntaxError(res.exceptionDetails)) {
        const p2 = { expression: fallback, returnByValue: true, awaitPromise: true };
        if (contextId != null) p2.contextId = contextId;
        res = await send("Runtime.evaluate", p2, sessionId);
      }
      return evalValueOrThrow(res);   // throws frame_eval_failed:<reason> on exception
    };

    // (2) SAME-PROCESS frame — locate it in the top session's frame tree by url.
    const { frameTree } = await send("Page.getFrameTree");
    const cdpFrameId = matchCdpFrameId(frameTree, frame.url);
    if (cdpFrameId) {
      const iso = await send("Page.createIsolatedWorld",
        { frameId: cdpFrameId, worldName: "browser-bridge-eval", grantUniveralAccess: false });
      return evaluate(undefined, iso.executionContextId);
    }

    // (3) CROSS-ORIGIN OOPIF (possibly NESTED) — the shared bounded recursive
    // auto-attach cascade resolves its flat session; evaluate in THAT session.
    const sessionId = await cdpOopifSession(send, tabId, frame);
    return await evaluate(sessionId, undefined);
  });
}

// Populate the file input matched by `selector` with the ABSOLUTE local `absPath`
// via CDP DOM.setFileInputFiles (Gap 1). Chrome reads the file itself by path (same
// host) — so NO file bytes cross the bridge. Attaches ONLY to the owned/target tab
// (withCdp → #187 own-tab scope + #189 bounded timeouts + discarded-tab fail-fast,
// ALWAYS detach). `frame` (optional) routes into a SAME-PROCESS iframe (isolated
// world) OR a cross-origin OOPIF (flat auto-attached session) — the SAME resolution
// `eval --frame` uses. The element is resolved to a RemoteObject and VERIFIED to be
// a real <input type=file> before anything is set. NO raw-CDP passthrough — a fixed
// typed sequence of CDP calls. Returns [basename] (the full path never returns).
async function cdpSetFileInput(tabId, tabUrl, frame, selector, absPath) {
  return withCdp(tabId, tabUrl, async (send) => {
    // Resolve the execution target for the element lookup: (sessionId, contextId).
    // No frame → the tab's top session/default context (both undefined).
    let sessionId;
    let contextId;
    if (frame) {
      // SAME-PROCESS frame → in the top session's frame tree → isolated-world context.
      const { frameTree } = await send("Page.getFrameTree");
      const cdpFrameId = matchCdpFrameId(frameTree, frame.url);
      if (cdpFrameId) {
        const iso = await send("Page.createIsolatedWorld",
          { frameId: cdpFrameId, worldName: "browser-bridge-upload", grantUniveralAccess: false });
        contextId = iso.executionContextId;
      } else {
        // CROSS-ORIGIN OOPIF (possibly NESTED) → NOT in the top frame tree → the SAME
        // shared bounded recursive auto-attach resolver `eval --frame` uses.
        sessionId = await cdpOopifSession(send, tabId, frame);
      }
    }
    // 1. Resolve the element to a CDP RemoteObject (returnByValue:false → objectId).
    const evalParams = { expression: fileInputSelectorExpression(selector), returnByValue: false };
    if (contextId != null) evalParams.contextId = contextId;
    const res = await send("Runtime.evaluate", evalParams, sessionId);
    if (res && res.exceptionDetails) throw new Error(`element_not_found:${selector}`);
    const objectId = res && res.result && res.result.objectId;
    if (!objectId) throw new Error(`element_not_found:${selector}`);
    // 2. VERIFY it is genuinely an <input type=file> before setting anything.
    const check = await send("Runtime.callFunctionOn",
      { objectId, functionDeclaration: FILE_INPUT_CHECK_FN, returnByValue: true }, sessionId);
    if (!(check && check.result && check.result.value === true)) {
      throw new Error(`not_a_file_input:${selector}`);
    }
    // 3. Hand the ABSOLUTE path to Chrome — it reads the file itself (no bytes here).
    await send("DOM.setFileInputFiles", { objectId, files: [absPath] }, sessionId);
    return [basenameOf(absPath)];
  });
}

// `--wake` un-throttles the TAB, and a --frame read already goes through its own
// resolution path; combining them would silently ignore one of the two. Refuse
// loudly with the exact remedy instead (`browser wake`, then the frame read).
// `isWakeOp` covers the `wake` OP itself: `--frame` is a GLOBAL CLI flag, so
// `browser --frame X wake` puts a `frame` on the wire that OPS.wake has no meaning
// for. Silently waking the whole tab while the caller believes they scoped it to a
// frame is exactly the quiet mismatch the `--wake`+`--frame` refusal exists to
// prevent, so it is refused the same way. Un-throttling is inherently TAB-level —
// there is no per-frame wake to offer.
function assertWakeNotFramed(cmd, isWakeOp) {
  if (cmd && cmd.frame && (isWakeOp || cmd.wake)) {
    throw new Error("wake_with_frame_unsupported: un-throttling is tab-level, not "
      + "per-frame — drop --frame (run 'browser wake' on the tab, then re-issue the "
      + "--frame read)");
  }
}

// Merge a cdpWake() outcome into a read result. The read happened INSIDE the woken
// session, so the visibilityState to report is the one the PROBE saw there — not a
// separate chrome.scripting probe of the (already re-throttled) tab. `woke` is
// true when the probe confirms the page was actually un-throttled during the read.
// Annotate a READ result with whether it actually observed the EMULATED page.
//
// `viaCdp` is passed per CALL SITE, not inferred from the op, because that is
// genuinely where the answer lives: `text --wake` and `eval --frame` route through
// withCdp (→ emulated), while the default `text`/`html`/`eval` take
// chrome.scripting (→ NOT emulated). Same op, different answer, depending on a
// flag — which is exactly why the envelope has to say so rather than leaving the
// caller to know this file's routing table. See NOT_EMULATED_READ_NOTE.
function emuAnnotate(data, tabId, viaCdp) {
  return annotateEmulatedRead(data, emulationSummary(emulationFor(tabId)), viaCdp);
}

function wakeAnnotate(data, w) {
  const vis = w && w.probe ? w.probe.visibilityState : null;
  annotateVisibility(data, vis);
  data.woke = vis === "visible";
  data.wake = { applied: w.applied, settleMs: w.settleMs };
  if (w.skipped && w.skipped.length) data.wake.skipped = w.skipped;
  return data;
}

// --- op executors ---------------------------------------------------------- //
// Each returns the op-specific `data` object; throws on failure (→ errorEnvelope).
const OPS = {
  async getHtml(cmd) {
    assertWakeNotFramed(cmd);   // --wake + --frame is refused before any work
    const tab = await targetTab(cmd);
    // --frame → read the outerHTML INSIDE the chosen (cross-origin OOPIF) frame via
    // chrome.scripting (reaches an out-of-process iframe; no debugger banner).
    if (cmd && cmd.frame) {
      const frame = await resolveFrame(tab.id, cmd.frame);
      const vis = await tabVisibilityState(tab.id);   // BEFORE the read → read stays last
      const html = await execInFrame(tab.id, frame.frameId, frameReadHtmlFn, []);
      // Report the FRAME's own url (not the top tab url) so the caller can confirm it
      // read the intended frame (#190 reported the top url for a frame read).
      // chrome.scripting into the frame: NO CDP -> the REAL, un-emulated DOM.
      return emuAnnotate(annotateVisibility(
        annotatePageContext({ url: frame.url || tab.url, title: tab.title, html, frame: cmd.frame, tabId: tab.id }, tab.url), vis),
        tab.id, false);
    }
    // --wake → un-throttle the tab and read INSIDE the same CDP session (the
    // un-throttled state does not survive detach — see protocol.js `wake`). This
    // is the OPT-IN path; the default read below stays on chrome.scripting.
    if (cmd && cmd.wake) {
      // The READ still goes through chrome.scripting (ISOLATED world) — only the
      // un-throttle is CDP. A CDP Runtime.evaluate with no contextId runs in the
      // page's MAIN world, where a hostile page can `Object.defineProperty` an
      // `outerHTML` getter and hand the reader text that is not in the DOM. That
      // would be a prompt-injection channel on exactly the path agents are told to
      // use when a read "came back empty". Running the read INSIDE the still-attached
      // wake session gives the same single-session guarantee with no main-world
      // exposure and no expression strings at all.
      const w = await cdpWake(tab.id, tab.url, cmd.waitMs, async () => {
        const [inj] = await chrome.scripting.executeScript({
          target: { tabId: tab.id },
          func: () => document.documentElement.outerHTML,
        });
        return inj ? inj.result : undefined;
      });
      return emuAnnotate(
        wakeAnnotate(annotatePageContext({ url: tab.url, title: tab.title, html: w.value, tabId: tab.id }, tab.url), w),
        tab.id, true);   // cdpWake -> withCdp -> emulation WAS applied
    }
    // No frame → the lighter chrome.scripting top-frame read (no debugger banner).
    const vis = await tabVisibilityState(tab.id);
    const [inj] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: () => document.documentElement.outerHTML,
    });
    return emuAnnotate(
      annotateVisibility(annotatePageContext({ url: tab.url, title: tab.title, html: inj.result, tabId: tab.id }, tab.url), vis),
      tab.id, false);   // chrome.scripting: NO CDP -> the REAL, un-emulated DOM
  },

  // Cheap read: the tab's VISIBLE innerText (optionally scoped to a CSS
  // selector), normalized + byte-capped in protocol.js. ~98% smaller than
  // getHtml's outerHTML — what the opencode browser-agent reads with. The
  // injected fn returns RAW innerText; normalizeText does the whitespace
  // collapse + cap out here (so it stays pure + unit-tested).
  async text(cmd) {
    assertWakeNotFramed(cmd);   // --wake + --frame is refused before any work
    const tab = await targetTab(cmd);
    const sel = (cmd && typeof cmd.selector === "string") ? cmd.selector : "";
    const cap = (cmd && cmd.maxBytes != null)
      ? cmd.maxBytes : TEXT_MAX_BYTES_DEFAULT;
    const annotated = !!(cmd && cmd.annotated);
    const maxItems = (cmd && typeof cmd.maxItems === "number")
      ? cmd.maxItems : ANNOTATED_TEXT_MAX_ITEMS_DEFAULT;
    // --frame → read INSIDE the chosen (cross-origin OOPIF) frame via
    // chrome.scripting (reaches an out-of-process iframe; no debugger banner).
    if (cmd && cmd.frame) {
      const frame = await resolveFrame(tab.id, cmd.frame);
      const vis = await tabVisibilityState(tab.id);   // BEFORE the read → read stays last
      if (annotated) {
        // Annotated inside a frame: inject annotatedTextFn, wrap in page context
        // with the frame URL, byte-cap, and return via emuAnnotate.
        const raw = await execInFrame(tab.id, frame.frameId, annotatedTextFn, [sel, maxItems]);
        const data = annotatePageContext({
          elements: raw ? raw.elements : [],
          count: raw ? raw.count : 0,
          url: frame.url || tab.url,
          title: tab.title,
          tabId: tab.id,
          frame: cmd.frame,
          truncated: 0,
        }, tab.url);
        byteCapElements(data, cap);
        return emuAnnotate(annotateVisibility(data, vis), tab.id, false);
      }
      // Plain text inside a frame.
      const raw = await execInFrame(tab.id, frame.frameId, frameReadTextFn, [sel]);
      const { text, truncated } = normalizeText(raw, cap);
      // Report the FRAME's own url (see getHtml) so the caller confirms the right frame.
      // chrome.scripting into the frame: NO CDP -> the REAL, un-emulated DOM.
      return emuAnnotate(annotateVisibility(
        annotatePageContext({ url: frame.url || tab.url, title: tab.title, text, truncated, frame: cmd.frame, tabId: tab.id }, tab.url),
        vis), tab.id, false);
    }
    // --wake → un-throttle + read in ONE CDP session (opt-in; see getHtml).
    if (cmd && cmd.wake) {
      if (annotated) {
        // Annotated inside a woken CDP session.
        const w = await cdpWake(tab.id, tab.url, cmd.waitMs, async () => {
          const [inj] = await chrome.scripting.executeScript({
            target: { tabId: tab.id },
            args: [sel, maxItems],
            func: annotatedTextFn,
          });
          return inj ? inj.result : undefined;
        });
        const data = annotatePageContext({
          elements: w.value ? w.value.elements : [],
          count: w.value ? w.value.count : 0,
          url: tab.url,
          title: tab.title,
          tabId: tab.id,
          truncated: 0,
        }, tab.url);
        byteCapElements(data, cap);
        return emuAnnotate(wakeAnnotate(data, w), tab.id, true);
      }
      // ISOLATED-world read inside the still-attached wake session — see getHtml's
      // note. A main-world read could be served shadowed `innerText`/`querySelector`.
      const w = await cdpWake(tab.id, tab.url, cmd.waitMs, async () => {
        const [inj] = await chrome.scripting.executeScript({
          target: { tabId: tab.id },
          args: [sel],
          func: (s) => {
            const el = s ? document.querySelector(s) : document.body;
            return el ? el.innerText : "";
          },
        });
        return inj ? inj.result : undefined;
      });
      const { text, truncated } = normalizeText(w.value, cap);
      return emuAnnotate(
        wakeAnnotate(annotatePageContext({ url: tab.url, title: tab.title, text, truncated, tabId: tab.id }, tab.url), w),
        tab.id, true);   // cdpWake -> withCdp -> emulation WAS applied
    }
    // --annotated path (no --frame, no --wake).
    if (annotated) {
      const [inj] = await chrome.scripting.executeScript({
        target: { tabId: tab.id },
        args: [sel, maxItems],
        func: annotatedTextFn,
      });
      const data = annotatePageContext({
        elements: inj.result ? inj.result.elements : [],
        count: inj.result ? inj.result.count : 0,
        url: tab.url,
        title: tab.title,
        tabId: tab.id,
        truncated: 0,
      }, tab.url);
      byteCapElements(data, cap);
      return emuAnnotate(
        annotateVisibility(data, await tabVisibilityState(tab.id)),
        tab.id, false);
    }
    const vis = await tabVisibilityState(tab.id);
    const [inj] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      args: [sel],
      func: (s) => {
        const el = s ? document.querySelector(s) : document.body;
        return el ? el.innerText : "";
      },
    });
    const { text, truncated } = normalizeText(inj.result, cap);
    return emuAnnotate(
      annotateVisibility(annotatePageContext({ url: tab.url, title: tab.title, text, truncated, tabId: tab.id }, tab.url), vis),
      tab.id, false);   // chrome.scripting: NO CDP -> the REAL, un-emulated DOM
  },

  async eval(cmd) {
    assertWakeNotFramed(cmd);   // --wake + --frame is refused before any work
    const tab = await targetTab(cmd);
    // --frame → evaluate the arbitrary JS STRING INSIDE the chosen frame (incl. a
    // cross-origin OOPIF) via CDP Runtime.evaluate. chrome.scripting can only run a
    // serialized FUNC (not a string), so the #190 chrome.scripting path executed
    // nothing meaningful and returned value:null-as-success — the bug this fixes.
    // cdpFrameEval resolves the frame's execution context (same-process isolated world
    // OR OOPIF flat session) and NEVER silent-nulls (frame_not_found / frame_eval_failed).
    if (cmd && cmd.frame) {
      const frame = await resolveFrame(tab.id, cmd.frame);
      // visibilityState is read on the TOP frame (it reflects the tab; the OOPIF's
      // document follows the tab) via chrome.scripting — the EVAL itself still runs
      // via CDP (the #190 fix), so this probe does not regress the eval mechanism.
      const vis = await tabVisibilityState(tab.id);
      const value = await cdpFrameEval(tab.id, tab.url, frame, cmd.js);
      return emuAnnotate(
        annotateVisibility({ url: frame.url || tab.url, value, frame: cmd.frame }, vis),
        tab.id, true);   // cdpFrameEval -> withCdp -> emulation WAS applied
    }
    // --wake → un-throttle + evaluate in ONE CDP session (opt-in; see getHtml).
    // Uses the SAME expression/statement pair + never-silent-null contract as
    // `eval --frame` (frameEvalExpressions/evalValueOrThrow), just in the tab's
    // default (top-frame) context.
    //
    // ⚠ WORLD: this is the tab's MAIN world — and that is CORRECT here, because the
    // DEFAULT (non-wake) `eval` path below is explicitly `world:"MAIN"` too. `eval`
    // means "run my JS with the page's own globals"; a caller asking for the page's
    // `window` must get it. So `eval --wake` has exactly the same world semantics as
    // `eval`, and adds no new exposure. This differs from `text`/`html --wake`, which
    // read via chrome.scripting's ISOLATED world precisely so a hostile page cannot
    // shadow the read. `eval` cannot use chrome.scripting at all — it can only run a
    // serialized FUNC, never a caller's JS STRING (the #190 null-as-success bug).
    if (cmd && cmd.wake) {
      const { expression, fallback } = frameEvalExpressions(cmd.js);
      const w = await cdpWake(tab.id, tab.url, cmd.waitMs, async (send) => {
        let res = await send("Runtime.evaluate",
          { expression, returnByValue: true, awaitPromise: true });
        if (res && res.exceptionDetails && isCdpSyntaxError(res.exceptionDetails)) {
          res = await send("Runtime.evaluate",
            { expression: fallback, returnByValue: true, awaitPromise: true });
        }
        return evalValueOrThrow(res);
      });
      return emuAnnotate(wakeAnnotate({ url: tab.url, value: w.value }, w),
                         tab.id, true);   // cdpWake -> withCdp -> emulated
    }
    const vis = await tabVisibilityState(tab.id);
    // chrome.scripting runs the top-frame eval in the page's MAIN world (world:
    // "MAIN" below) — so `js` sees the page's own globals — and its completion value
    // is returned. Wrapped so a bare expression or a statement block both work.
    // Result must be JSON-serialisable (structured clone).
    const [inj] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      world: "MAIN",
      args: [cmd.js],
      func: (src) => {
        // Decide expression-vs-statement form by whether the expression-wrapped
        // body PARSES — WITHOUT executing it — then call the chosen fn exactly
        // once. A construction SyntaxError → fall back to the statement form; a
        // runtime throw from calling the fn must propagate (never re-run a side
        // effect). Mirrors protocol.js compileEval — keep the two in sync.
        let fn;
        try {
          // eslint-disable-next-line no-new-func
          fn = new Function(`return (${src})`);
        } catch (e) {
          if (e instanceof SyntaxError) {
            // eslint-disable-next-line no-new-func
            fn = new Function(src);   // statement form (no return value)
          } else {
            throw e;
          }
        }
        return fn();
      },
    });
    return emuAnnotate(
      annotateVisibility({ url: tab.url, value: inj.result }, vis),
      tab.id, false);   // chrome.scripting MAIN world: NO CDP -> un-emulated
  },

  // Each tab additionally carries `emulation` when this service worker holds a
  // device-emulation state for it (absent otherwise, so the common listing is
  // unchanged). SURFACING IT HERE IS THE POINT: an emulation left on by a session
  // that wandered off is otherwise invisible, and "why is this page rendering at
  // 393px" becomes a mystery instead of a line in `browser tabs`. `emulatedTabs`
  // repeats the ids at the top level so a caller does not have to scan.
  async tabs() {
    const tabs = await chrome.tabs.query({});
    const emulated = [];
    const out = tabs.map((t) => {
      const entry = {
        id: t.id, url: t.url, title: t.title,
        active: t.active, windowId: t.windowId,
      };
      const summary = emulationSummary(emulationFor(t.id));
      if (summary) { entry.emulation = summary; emulated.push(t.id); }
      return entry;
    });
    return { tabs: out, emulatedTabs: emulated };
  },

  // Navigate the owned/target tab.
  //
  // TWO paths, and the split exists BECAUSE of emulation. The plain path is
  // chrome.tabs.update — cheap, no debugger banner, unchanged behaviour.
  //
  // On an EMULATED tab that is wrong: chrome.tabs.update navigates outside any CDP
  // session, so the page's first paint, its `@media` evaluation and — critically —
  // its server-side and client-side UA sniffing all happen with the REAL desktop
  // metrics and the REAL user agent. The emulated values would only appear on the
  // next op, by which time a UA-sniffing site has already served the desktop
  // bundle. So an emulated tab navigates via CDP Page.navigate INSIDE a session
  // that has already applied the overrides (withCdp's run wrapper does that before
  // this callback is reached).
  //
  // Honest limitation, documented in reference/emulation.md: the overrides still
  // die when this session detaches, moments after the navigation is committed. A
  // page that re-measures the viewport LATER (after load, on a resize listener)
  // will see the real window until the next op re-applies. Waking/reading with an
  // op is what re-establishes it — that is inherent to the detach-scoped design,
  // and it is the price of the safety property (see protocol.js EMULATION).
  async nav(cmd) {
    const tab = await targetTab(cmd);
    const emu = emulationFor(tab.id);
    if (emu) {
      await withCdp(tab.id, tab.url, async (send) => {
        await send("Page.navigate", { url: cmd.url });
      });
      // The new document WAS created inside the emulated session, so it carries
      // the create-time properties (touch et al). Recorded so a later `emulate`
      // with the same create-time signature stays silent instead of crying wolf.
      // Recorded only AFTER the navigate succeeded — a THROWING nav leaves the old
      // document in place, and its old record with it.
      //
      // ⚠ Known soft edge: `Page.navigate` can RESOLVE while reporting a failure in
      // its `errorText` (DNS failure, connection refused, …). This does not read it,
      // so the record is written for what is actually a Chrome error page. Low
      // impact — an error page has no create-time properties anyone is testing, and
      // the very next real `nav` overwrites the record — but it is a known way for
      // the record to be optimistic. Reading `errorText` would need a decision about
      // what `nav` should RETURN in that case, which is a wider change than a hint.
      recordDocumentEmulation(tab.id, emu);
      return { tabId: tab.id, url: cmd.url, via: "cdp",
               emulation: emulationSummary(emu) };
    }
    await chrome.tabs.update(tab.id, { url: cmd.url });
    // Un-emulated navigation: the new document was created with NO overrides, so
    // any later `emulate` on it MUST warn. Recording "none" explicitly (rather
    // than deleting) keeps one writer for this map.
    recordDocumentEmulation(tab.id, null);
    return { tabId: tab.id, url: cmd.url, via: "tabs.update" };
  },

  // Screenshot the owned/target tab. PRIMARY path is CDP Page.captureScreenshot,
  // which captures a BACKGROUND / occluded / non-foreground tab (the whole point —
  // it fixes the captureVisibleTab "can only grab the foreground tab" limitation,
  // and lets two profiles each screenshot their own tab). A FAST path keeps the
  // cheap, banner-free captureVisibleTab for a tab that IS already visible (and not
  // --fullpage); any failure there falls through to the CDP path. `--fullpage`
  // captures the whole scrollable document (CDP only). Attach is REFUSED on a
  // privileged tab (assertCdpAttachable inside withCdp) before any attach.
  //
  // ⚠ THE FAST PATH IS DISABLED ON AN EMULATED TAB, and that is not an
  // optimization detail — it is a correctness gate. chrome.tabs.captureVisibleTab
  // never attaches the debugger, so it captures the tab's REAL, un-emulated
  // rendering. On an emulated tab it would return a perfectly valid PNG of the
  // desktop layout in answer to "screenshot my iPhone viewport": a confident wrong
  // answer, indistinguishable from a correct one, on the single op whose entire
  // job is to show you what the device sees. Forcing CDP costs a debugger banner
  // and is unambiguously the right trade.
  async screenshot(cmd) {
    const tab = await targetTab(cmd);
    const fullpage = !!(cmd && cmd.fullpage);
    const emulated = !!emulationFor(tab.id);
    if (tab.active && !fullpage && !emulated) {
      // Fast path — no debugger attach/banner. Chrome throttles captureVisibleTab to
      // ~2/sec; captureWithRetry spaces the (rare) retry ≥ the quota window.
      try {
        const dataUrl = await captureWithRetry(() =>
          chrome.tabs.captureVisibleTab(tab.windowId, { format: "png" }));
        return { url: tab.url, dataUrl, via: "captureVisibleTab" };
      } catch (e) { /* fall through to the CDP path (works off-screen) */ }
    }
    const dataUrl = await withCdp(tab.id, tab.url, async (send) => {
      const params = { format: "png" };
      if (fullpage) {
        // Page.getLayoutMetrics runs AFTER withCdp's run wrapper has applied the
        // emulation (see the choke point in withCdp), so on an emulated tab these
        // metrics — and therefore the --fullpage clip — are the EMULATED ones. The
        // ordering is asserted by a test rather than trusted to this comment.
        const metrics = await send("Page.getLayoutMetrics");
        params.clip = fullPageClip(metrics);
        params.captureBeyondViewport = true;
      }
      const { data } = await send("Page.captureScreenshot", params);
      return `data:image/png;base64,${data}`;
    });
    const summary = emulationSummary(emulationFor(tab.id));
    return { url: tab.url, dataUrl, via: "cdp",
             ...(summary ? { emulation: summary } : {}) };
  },

  // List the target tab's frames ({frameId,url,parentFrameId}) via
  // chrome.webNavigation.getAllFrames — which, UNLIKE CDP Page.getFrameTree,
  // enumerates OUT-OF-PROCESS (cross-origin) iframes too. So a caller can discover a
  // cross-origin OOPIF and read/click INTO it with `--frame <frameId|url-substring>`.
  // Metadata only (numeric frameId + url + parent) — never frame content. No debugger.
  async frames(cmd) {
    const tab = await targetTab(cmd);
    const frames = await framesForTab(tab.id);
    const vis = await tabVisibilityState(tab.id);
    return annotateVisibility({ url: tab.url, title: tab.title, frames }, vis);
  },

  // Populate an <input type=file> matched by `selector` with the ABSOLUTE local
  // `path` via CDP DOM.setFileInputFiles (Gap 1). Chrome reads the file itself by
  // path (same host) — NO file bytes cross the bridge. Own-tab-scoped + #189-bounded
  // (cdpSetFileInput → withCdp). `--frame` routes into a same-process iframe OR a
  // cross-origin OOPIF exactly like `eval --frame`. Verifies the element is a real
  // file input (`not_a_file_input`) / exists (`element_not_found`). The RESULT
  // carries only the BASENAME(s) — the full path stays server/CLI-side + audit log.
  async upload(cmd) {
    const tab = await targetTab(cmd);
    const selector = String(cmd.selector);
    const absPath = String(cmd.path);
    if (cmd && cmd.frame) {
      const frame = await resolveFrame(tab.id, cmd.frame);
      const files = await cdpSetFileInput(tab.id, tab.url, frame, selector, absPath);
      return { ok: true, selector, frame: cmd.frame, url: frame.url || tab.url, files };
    }
    const files = await cdpSetFileInput(tab.id, tab.url, null, selector, absPath);
    return { ok: true, selector, frame: null, url: tab.url, files };
  },

  // Click `selector`. TWO paths, by design:
  //   * `--frame` (cross-origin OOPIF): inject a SYNTHETIC click into the resolved
  //     frame via chrome.scripting (the only path that reaches an OOPIF). The
  //     dispatched events are `isTrusted:false` — honestly reported as trusted:false —
  //     but drive the vast majority of apps (which listen for ordinary click/input).
  //   * TOP frame (no `--frame`): the CDP Input.dispatchMouseEvent path — a real
  //     `isTrusted` press+release the page can't tell from a human's (unchanged).
  // `selector` is validated present by server/SW REQUIRED_FIELDS.
  async click(cmd) {
    const tab = await targetTab(cmd);
    const selector = String(cmd.selector);
    if (cmd.frame) {
      const frame = await resolveFrame(tab.id, cmd.frame);
      const res = await execInFrame(tab.id, frame.frameId, frameClickFn, [selector]);
      if (!res || res.ok === false) throw new Error(`element_not_found:${selector}`);
      return { url: frame.url || tab.url, clicked: selector, x: res.x, y: res.y,
               frame: cmd.frame, trusted: false };
    }
    // On a TOUCH-EMULATED tab the top-frame path dispatches Input.dispatchTouchEvent
    // (touchStart/touchEnd) instead of mouse events — which is exactly what DevTools
    // does with touch emulation on. This is not cosmetic: Chromium synthesizes
    // compatibility MOUSE events from touch, but never touch events from mouse, so
    // a mobile UI whose handler is `touchstart` (or a library that binds pointer
    // events with `pointerType === "touch"`) simply never fires under a mouse click
    // — the tap "does nothing" and the agent reports a broken page that is not
    // broken. Mouse remains the behaviour on every non-touch-emulated tab.
    const emu = emulationFor(tab.id);
    const touch = isTouchEmulated(emu);
    const point = await withCdp(tab.id, tab.url, async (send) => {
      const rect = await cdpEval(send, undefined, elementRectExpression(selector));
      if (!rect) throw new Error(`element_not_found:${selector}`);
      const p = clickPoint(rect, { x: 0, y: 0 });
      if (touch) {
        for (const ev of touchTapEvents(p.x, p.y)) await send(ev.method, ev.params);
      } else {
        const mouse = (type) => send("Input.dispatchMouseEvent",
          { type, x: p.x, y: p.y, button: "left", buttons: 1, clickCount: 1 });
        await mouse("mousePressed");
        await mouse("mouseReleased");
      }
      return p;
    });
    return { url: tab.url, clicked: selector, x: point.x, y: point.y,
             frame: null, trusted: true, via: touch ? "touch" : "mouse" };
  },

  // Type `text` (optionally focus `--selector` first). `--frame` → SYNTHETIC input
  // (focus + set value + input/change) injected into the cross-origin OOPIF via
  // chrome.scripting (trusted:false — the reachable OOPIF path). TOP frame → CDP
  // Input.insertText, a trusted input event (unchanged). Returns only the LENGTH
  // typed, never echoes the text back (privacy + telemetry).
  async type(cmd) {
    const tab = await targetTab(cmd);
    const text = String(cmd.text);
    if (cmd.frame) {
      const frame = await resolveFrame(tab.id, cmd.frame);
      const res = await execInFrame(tab.id, frame.frameId, frameTypeFn,
        [cmd.selector || "", text]);
      // Surface the injected fn's SPECIFIC error: a missing selector → element_not_found
      // (with the selector), and an empty/non-editable target → no_editable_target
      // (never a false success claiming N chars typed — #190 audit).
      if (!res || res.ok === false) {
        const err = (res && res.error) || "type_failed";
        throw new Error(err === "element_not_found" ? `element_not_found:${cmd.selector}` : err);
      }
      return { url: frame.url || tab.url, typed: text.length, frame: cmd.frame, trusted: false };
    }
    await withCdp(tab.id, tab.url, async (send) => {
      if (cmd.selector) {
        const ok = await cdpEval(send, undefined, focusExpression(cmd.selector));
        if (!ok) throw new Error(`element_not_found:${cmd.selector}`);
      }
      await send("Input.insertText", { text });
    });
    return { url: tab.url, typed: text.length, frame: null, trusted: true };
  },

  // Dispatch one bounded key (Enter/Tab/Escape/arrows/…). The key name is resolved +
  // validated by keyEventParams FIRST — an unknown key is refused BEFORE any injection
  // or attach (the bounded-key surface is preserved on both paths). `--frame` →
  // SYNTHETIC keydown/keyup injected into the cross-origin OOPIF via chrome.scripting
  // (trusted:false — the reachable OOPIF path). TOP frame → CDP Input.dispatchKeyEvent,
  // a trusted key event (unchanged).
  async key(cmd) {
    const tab = await targetTab(cmd);
    const p = keyEventParams(cmd.key);   // throws unknown_key (no injection/attach on refusal)
    if (cmd.frame) {
      const frame = await resolveFrame(tab.id, cmd.frame);
      const res = await execInFrame(tab.id, frame.frameId, frameKeyFn, [cmd.selector || "", p]);
      if (!res || res.ok === false) throw new Error(`element_not_found:${cmd.selector}`);
      return { url: frame.url || tab.url, key: p.key, frame: cmd.frame, trusted: false };
    }
    await withCdp(tab.id, tab.url, async (send) => {
      if (cmd.selector) {
        const ok = await cdpEval(send, undefined, focusExpression(cmd.selector));
        if (!ok) throw new Error(`element_not_found:${cmd.selector}`);
      }
      const base = { key: p.key, code: p.code,
                     windowsVirtualKeyCode: p.keyCode, nativeVirtualKeyCode: p.keyCode };
      await send("Input.dispatchKeyEvent",
        { type: p.text ? "keyDown" : "rawKeyDown", ...base, ...(p.text ? { text: p.text } : {}) });
      await send("Input.dispatchKeyEvent", { type: "keyUp", ...base });
    });
    return { url: tab.url, key: p.key, frame: null, trusted: true };
  },

  // Create a NEW tab for the calling session to own. active:false so parallel
  // sessions don't fight over the foreground when each opens its own tab. The
  // server records this real tabId as the session's owned tab.
  //
  // Idempotent re-open: when the server passes `reuseTabId` (the session already
  // owns a tab), reuse THAT tab if it is still live instead of creating a second
  // one — otherwise a double `open` would orphan the first real tab (no ownership
  // → never closed → leaked). If the reuse tab is gone, fall through and open a
  // fresh one (open-after-owned-tab-gone).
  async open(cmd) {
    if (cmd && cmd.reuseTabId != null) {
      try {
        const existing = await chrome.tabs.get(cmd.reuseTabId);
        return { tabId: existing.id, url: existing.url || "about:blank",
                 reused: true };
      } catch (e) { /* owned tab gone → open a fresh one below */ }
    }
    const tab = await chrome.tabs.create({
      url: (cmd && cmd.url) ? cmd.url : "about:blank",
      active: false,
    });
    return { tabId: tab.id, url: tab.url || (cmd && cmd.url) || "about:blank" };
  },

  // Close the session's owned tab (the server injects its tabId). The server
  // drops the ownership mapping on success. Idempotent: if the tab was already
  // closed out-of-band, `chrome.tabs.remove` rejects — treat that as success
  // (the desired end-state, tab absent, already holds) so the session's stale
  // ownership is cleanly dropped instead of surfacing a spurious error.
  //
  // Drops any emulation state for the tab FIRST, on both paths. onRemoved would
  // normally do it, but it is an event we do not control the timing of (and does
  // not fire at all on the already-gone path), and a stale entry is inheritable by
  // a recycled tabId. Clearing here makes it deterministic.
  async close(cmd) {
    if (!cmd || cmd.tabId == null) throw new Error("missing_tabId");
    forgetTab(cmd.tabId);
    try {
      await chrome.tabs.remove(cmd.tabId);
      return { closed: cmd.tabId };
    } catch (e) {
      return { closed: cmd.tabId, alreadyGone: true };
    }
  },

  // --- `emulate`: device emulation for real mobile testing ------------------ //
  //
  // Stores (or clears) the tab's emulation state and applies it ONCE immediately so
  // the caller gets a straight yes/no on whether Chromium accepted the overrides,
  // rather than discovering a bad timezone id three ops later. Every subsequent op
  // that attaches CDP re-applies it (see the choke point in withCdp).
  //
  // `--reset` means "STOP RE-APPLYING", and NOTHING ELSE. Read the branch below:
  // it calls clearEmulation(), which is `emulationState.delete(tabId)` and no more
  // — no debugger is attached and NOT ONE CDP message is sent. It is NOT an undo,
  // and it must not be described as one.
  //
  // WHY IT LOOKS LIKE AN UNDO ANYWAY. The non-viewport overrides (UA, tz, dpr,
  // touch points, prefers-color-scheme) revert ON THEIR OWN, because a CDP
  // override lives only as long as the debugger session that set it and withCdp
  // always detaches. Reset does not clear them; it simply stops re-installing
  // them on the next attach. That distinction is load-bearing — anyone who
  // believes a clear was sent will mis-diagnose the viewport.
  //
  // THE VIEWPORT SIZE IS THE EXCEPTION and it does NOT come back: measured
  // 2026-08-03 (ext 0.7.2, fresh-tab control), 1124 → emulate → 393 → reset →
  // 393 → re-nav → 393. That build DID send `Emulation.clearDeviceMetricsOverride`
  // and the size survived it anyway, which is exactly why the clears were NOT
  // carried into this build: they are not the remedy. Mechanism unestablished
  // (#319); replacing the tab (`--reset --recreate`) is the only known remedy.
  // See protocol.js EMULATION.
  //
  // OWNERSHIP is enforced SERVER-side (server.py OWNED_TAB_ONLY_OPS → the named
  // `not_owned_tab` refusal), not here, because the server is the only side that
  // knows which session owns which tab. The extension still refuses a privileged
  // scheme before attaching, as every CDP op does.
  async emulate(cmd) {
    const tab = await targetTab(cmd);
    const state = normalizeEmulation(cmd);   // throws a NAMED error on bad input

    if (state.reset) {
      const had = emulationSummary(emulationFor(tab.id));
      clearEmulation(tab.id);
      return {
        tabId: tab.id, url: tab.url, reset: true,
        wasEmulating: had,
        // 🔴 PINNED, character for character, by tests/service_worker.test.mjs
        // ("emulate --reset: the runtime note"). That test also asserts NO
        // debugger call is made, so the "nothing was sent" sentence cannot rot
        // into a claim while the code does something else. If you change this
        // string, change the test in the SAME commit — that is the point.
        note: "emulation stopped: this tab will no longer have overrides re-applied. "
          + "NOTHING WAS SENT TO THE BROWSER — no debugger was attached and no CDP "
          + "clears were issued. THIS IS NOT AN UNDO. The UA, timezone, "
          + "devicePixelRatio, touch points and prefers-color-scheme revert on "
          + "their own, because CDP overrides die when the debugger detaches — not "
          + "because anything cleared them. The emulated VIEWPORT SIZE does NOT "
          + "come back: it survives the detach and a re-navigation (measured; "
          + "mechanism unknown, issue #319). Replacing the tab is the only known "
          + "remedy: `browser emulate --reset --recreate` opens a fresh tab at the "
          + "same url and closes this one (the tab id changes).",
      };
    }

    // Store BEFORE applying so the apply goes through the same withCdp choke point
    // every other op uses — one code path, no second copy of the apply logic. A
    // failed apply rolls the state back: leaving it stored would mean every later
    // op re-attempts an emulation the caller was told had failed.
    const previous = emulationState.get(tab.id) || null;
    emulationState.set(tab.id, state);
    let applied;
    try {
      applied = await withCdp(tab.id, tab.url, async () =>
        emulationCdpSteps(state).map((s) => s.method));
    } catch (e) {
      if (previous) emulationState.set(tab.id, previous);
      else emulationState.delete(tab.id);
      throw e;
    }
    // THE CREATE-TIME HINT (protocol.js DOCUMENT_PREDATES_EMULATION_NOTE). Fires
    // when the tab already holds a committed document that was NOT created under
    // an emulation with this same create-time signature — i.e. exactly the case
    // where `ontouchstart`/`TouchEvent` are measurably missing. Silent for a
    // re-`emulate` of the same device after an emulated `nav`.
    //
    // ⚠ The correct workflow is `open <url>` → `emulate` (fires) → re-`nav` under
    // emulation. It is NOT "open at about:blank, then emulate": chrome.debugger
    // attaches only to http/https (CDP_ATTACHABLE_SCHEMES), so `emulate` on an
    // about:blank tab is refused with `cdp_attach_refused:about:` before it ever
    // reaches this annotation — and an emulated `nav` cannot rescue it either,
    // since that attaches on the tab's CURRENT (about:blank) url. Pinned by a
    // test in tests/emulation.test.mjs.
    //
    // `tab.url` is the URL as of the START of this op, which is the document the
    // overrides just landed on. Deliberately not re-read after the apply: nothing
    // in `emulate` navigates.
    return annotateDocumentPredates({
      tabId: tab.id, url: tab.url,
      emulation: emulationSummary(state),
      applied,
      note: "sticky per tab: these overrides are re-applied inside every "
        + "subsequent op's CDP session. Between ops the tab is NOT emulated (the "
        + "overrides die at detach), so a crashed agent cannot leave your browser "
        + "distorted. `browser emulate --reset` stops re-applying.",
    }, documentPredatesEmulation(tab.url, emulationCreateTimeSignature(state),
                                 documentEmulation.get(tab.id)));
  },

  // Bring the target tab to the FOREGROUND. THE ONE INTRUSIVE OP — it takes the
  // OPERATOR'S SCREEN away from whatever they were doing.
  //
  // ⚠ LAST RESORT, NOT the remedy for a throttled/hidden tab. Un-throttling is
  // `wake`'s job (CDP focus emulation, no focus movement, measured equivalent for
  // rendering). Use `activate` only when something genuinely needs the REAL
  // foreground — a getUserMedia/permission prompt, a native picker, or verifying
  // with your own eyes. It is needed at most ONCE PER TAB; calling it per read is
  // the exact pattern that had an agent yanking the screen 1–5×/minute.
  //
  // Two steps, both permission-free for the extension's own use:
  //   * chrome.tabs.update(tabId,{active:true})   — make it the active tab of its
  //     window;
  //   * chrome.windows.update(windowId,{focused})  — request that window's focus.
  // i3 CAVEAT (honest): windows.update REQUESTS focus, but on a tiling WM Chrome
  // cannot force i3 to raise/switch-workspace to the window — so activation
  // reliably sets the tab active WITHIN its window and requests focus, but may
  // NOT raise the window if it is on another i3 workspace (best-effort).
  //
  // Then an OPTIONAL bounded wait-for-load (waitForTabLoad — pure, unit-tested):
  // wait (≤ ACTIVATE_WAIT_MAX_MS 8s, well under the 20s cmd_timeout) for the tab
  // to reach status:"complete" + a short paint settle, so the caller gets a
  // more-loaded tab. A discarded / never-completing tab returns PROMPTLY — the
  // #189 no-wedge guarantee (tabLoadSettled fail-fasts an unloaded tab). No CDP,
  // no debugger banner, no new permission. `globalThis.BROWSER_BRIDGE_ACTIVATE_
  // TIMING` is a TEST-ONLY seam to shrink the poll/settle so a test settles in ms.
  // UN-THROTTLE the owned/target tab WITHOUT touching focus — the non-intrusive
  // answer to "my background tab never rendered", and the op the autonomous agent
  // gets INSTEAD of `activate` (focus theft is now operator-only).
  //
  // Attaches CDP to the OWN tab only (withCdp: privileged-scheme refusal before
  // attach, always-detach in a `finally`), applies the fixed WAKE_CDP_STEPS, holds
  // them for a bounded settle (default 1.5s, cap 8s) so the page receives real
  // animation frames, probes the result, detaches. The un-throttled STATE reverts
  // on detach (measured) — what persists is the DOM the page rendered during the
  // window, which is exactly what a subsequent cheap non-CDP read wants.
  //
  // No i3-msg, no chrome.tabs.update{active}, no chrome.windows.update{focused}:
  // nothing here can move the operator's screen.
  async wake(cmd) {
    assertWakeNotFramed(cmd, true);   // `browser --frame X wake` is refused, not silently tab-wide
    const tab = await targetTab(cmd);
    const w = await cdpWake(tab.id, tab.url, cmd && cmd.waitMs, null);
    const fresh = await chrome.tabs.get(tab.id).catch(() => tab);
    return {
      tabId: tab.id, url: fresh.url || tab.url, title: fresh.title || tab.title,
      applied: w.applied, skipped: w.skipped, settleMs: w.settleMs,
      // What the page saw WHILE woken (visibilityState "visible" ⇒ un-throttled).
      visibilityState: w.probe ? w.probe.visibilityState : null,
      readyState: w.probe ? w.probe.readyState : null,
      woke: !!(w.probe && w.probe.visibilityState === "visible"),
      // The honest caveat, carried in-band so a caller can't miss it.
      note: "un-throttled without moving focus; the un-throttled state ends at "
        + "detach — rendered DOM persists. Use --wake on a read that must observe "
        + "live un-throttled state.",
    };
  },

  async activate(cmd) {
    const tab = await targetTab(cmd);   // owned/explicit/active tab (server-forced for the agent)
    // Steal focus: active tab of its window, then request the window's focus.
    await chrome.tabs.update(tab.id, { active: true });
    if (tab.windowId != null) {
      try { await chrome.windows.update(tab.windowId, { focused: true }); }
      catch (e) { /* best-effort — i3 may refuse to raise across workspaces */ }
    }
    // Optional bounded wait-for-load (default a modest wait; --wait/waitMs=0 skips).
    const timing = (typeof globalThis !== "undefined"
      && globalThis.BROWSER_BRIDGE_ACTIVATE_TIMING) || {};
    const { tab: t } = await waitForTabLoad(
      () => chrome.tabs.get(tab.id),
      { waitMs: cmd && cmd.waitMs, ...timing });
    const out = t || tab;
    return { tabId: out.id, windowId: out.windowId, url: out.url,
             title: out.title, active: out.active, status: out.status };
  },

  // `ping` — the deterministic "is the NEW build loaded?" probe. No tab, no page,
  // no chrome.* call beyond getManifest()/runtime.id: it answers with THIS
  // service worker's own manifest version, its extension ID and its op set. An
  // older build fails it at validateCommand with `unknown_op` (it has never
  // heard of the name), which the CLI already translates into reload/restart
  // guidance. See protocol.js ALLOWED_OPS for the contract this op enforces.
  //
  // `id` answers the OTHER half — WHICH DIRECTORY Brave loaded. An unpacked
  // extension's ID is derived from its absolute path, so a repo-path load and a
  // ~/.local/share/browser-bridge-ext/ load report the same VERSION but
  // different IDs. Version alone therefore cannot confirm the migration took.
  // The path→id derivation is MEASURED (2026-08-01; Brave/Chromium on both
  // NixOS hosts, unpacked extensions, two paths): sha256(absolute path), first
  // 32 hex chars, each nibble 0-f mapped to a-p, PATH ONLY — no per-profile
  // component, so two profiles on one directory report one id. The expected id
  // is therefore computable in advance; see extension/README.md "The path→id
  // derivation (MEASURED)".
  // `buildMarker` (#324) is the field that actually answers the question. Both
  // `extensionVersion` and `id` describe the DIRECTORY Brave loaded from, so
  // two profiles on one directory report identical values while running
  // different code (measured 2026-08-04). The marker is a literal in this
  // worker's own module graph, so it is a statement about the RUNNING code.
  // `extensionVersion` is kept — it is still a useful hint and older tooling
  // reads it — but the server's `extension_stale` verdict is computed from the
  // marker now, and fails closed (null) when either side lacks one.
  async ping() {
    return { pong: true, extensionVersion: extensionVersion(),
             buildMarker: buildMarker(),
             id: extensionId(), ops: ALLOWED_OPS.slice() };
  },

  // Return page metadata (domain, path, searchParams, title) without reading
  // DOM content. The envelope's instanceId already carries the instance identity,
  // so the data payload focuses on page-level context.
  async context(cmd) {
    const tab = await targetTab(cmd);
    const ctx = parsePageContext(tab.url);
    return {
      url: tab.url,
      domain: ctx.domain,
      path: ctx.path,
      searchParams: ctx.searchParams,
      title: tab.title,
      tabId: tab.id,
    };
  },
};

// The extension-side breadcrumb (the §2.2 detector). ONE rolling slot in
// chrome.storage.local, so it cannot grow: {op, id, phase, ts}. After a drop this
// answers "wedged in `frames` since 18:02:34" from OBSERVATION instead of from
// inference over the server's journal — the operator's second drop left no trace
// anywhere because nothing was dispatched while the instance was down.
//
// FIRE-AND-FORGET on purpose: it is never awaited. A storage write is itself a
// chrome.* call, and awaiting it inside execute() would add exactly the class of
// unbounded await this whole change exists to remove.
// The loop's wall-clock budgets, with a test-injectable override (the same
// `globalThis.BROWSER_BRIDGE_*_TIMING` convention the `activate` op already uses)
// so a unit test can drive a 20ms budget instead of waiting 18 real seconds.
// Production reads the protocol.js constants unchanged.
function loopTiming() {
  const t = (typeof globalThis !== "undefined"
    && globalThis.BROWSER_BRIDGE_LOOP_TIMING) || {};
  return {
    execMs: t.execMs == null ? EXEC_OP_BUDGET_MS : t.execMs,
    pollMs: t.pollMs == null ? POLL_BUDGET_MS : t.pollMs,
    resultMs: t.resultMs == null ? RESULT_BUDGET_MS : t.resultMs,
    stallMs: t.stallMs == null ? LOOP_STALL_MS : t.stallMs,
    storageMs: t.storageMs == null ? STORAGE_BUDGET_MS : t.storageMs,
  };
}

// Bound a chrome.storage.local call. These are the loop's remaining non-op
// chrome.* awaits; a hang in one is the same fault class as a hung `frames`.
function storageBounded(promise, label) {
  return promiseWithTimeout(promise, loopTiming().storageMs, label, {},
                            "op_timeout");
}

function breadcrumb(op, id, phase) {
  try {
    const p = chrome.storage.local.set(
      { lastExec: { op, id, phase, ts: Date.now() } });
    if (p && typeof p.catch === "function") p.catch(() => {});
  } catch (e) { /* storage unavailable — a breadcrumb must never break an op */ }
}

// execute — THE choke point where every op is bounded.
//
// The bound lives here and NOWHERE else on purpose. Patching `frames` and
// `screenshot` individually (the two ops the journal caught wedging) would fix
// today's two call sites and regenerate the bug at the next op someone adds —
// `targetTab()` alone is on the path of every single op. One rule, one place.
//
// A timed-out op returns a NORMAL error envelope (`op_timeout:<op>`) through the
// existing catch, so the poll loop takes the same path it takes for any op that
// threw in the page: post the result, iterate, keep polling. Nothing throws past
// the loop.
async function execute(cmd) {
  const v = validateCommand(cmd);
  if (!v.ok) return errorEnvelope(cmd.id, v.error);
  breadcrumb(cmd.op, cmd.id, "start");
  try {
    const data = await promiseWithTimeout(
      OPS[cmd.op](cmd), loopTiming().execMs, cmd.op, {}, "op_timeout");
    breadcrumb(cmd.op, cmd.id, "done");
    return resultEnvelope(cmd.id, data);
  } catch (e) {
    const msg = e && e.message ? e.message : e;
    breadcrumb(cmd.op, cmd.id,
               String(msg).startsWith("op_timeout:") ? "timeout" : "error");
    return errorEnvelope(cmd.id, msg);
  }
}

// Best-effort active-tab snapshot for cheap /health + `browser instances`
// enrichment. Never throws — a query failure just omits the tab info.
async function activeTabSnapshot() {
  try {
    const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
    if (tab) return { url: tab.url, title: tab.title };
  } catch (e) { /* ignore */ }
  return null;
}

// --- long-poll loop -------------------------------------------------------- //
// pollOnce returns a tagged result: { kind } where kind ∈ POLL_IDLE /
// POLL_SUPERSEDED, or { kind: POLL_COMMAND, cmd } — so the loop can tell the
// distinct "you were superseded" signal (409) apart from a normal idle 204.
// An AbortSignal that fires after `ms`, or undefined where AbortSignal.timeout is
// unavailable (a bare unit-test global). Aborting the SOCKET is complementary to
// racing the promise: promiseWithTimeout settles the awaiter but abandons the
// underlying fetch, which would leak a socket per wedged poll; the signal actually
// tears the request down.
function abortAfter(ms) {
  try {
    if (typeof AbortSignal !== "undefined" && AbortSignal.timeout) {
      return AbortSignal.timeout(ms);
    }
  } catch (e) { /* fall through — the promise race is still the hard bound */ }
  return undefined;
}

// A retired loop must never reach the `fetch` below. `pollOnce` is the last
// place that can be guaranteed, because it AWAITS activeTabSnapshot()
// (chrome.tabs.query) first: a check in loop() before calling pollOnce leaves
// that await as a window in which a keepalive force-restart can land, after
// which the retired loop sails on into the poll. Measured through the real
// keepaliveTick path with a gate inside chrome.tabs.query: maxConcurrentPolls=2.
//
// This is the THIRD position this check has occupied (top of iteration → before
// pollOnce → here). Each move was prompted by the guarantee being falsified one
// frame deeper, so state the rule rather than the location: the check belongs
// immediately before the side effect it guards, with NO await between them.
const POLL_RETIRED = "retired";

async function pollOnce(cfg, retired) {
  const active = await activeTabSnapshot();
  // NOTHING may be awaited between here and the fetch.
  if (retired && retired()) return { kind: POLL_RETIRED };
  const res = await fetch(`${base(cfg.port)}/poll`, {
    // Identify this instance so the server routes only its commands here.
    headers: { ...authHeaders(cfg.token),
               ...pollHeaders(cfg.instanceId, cfg.label, active, cfg.extVersion,
                              cfg.extId, cfg.extBuild) },
    signal: abortAfter(loopTiming().pollMs),
  });
  const kind = classifyPollStatus(res.status);
  if (kind === POLL_COMMAND) return { kind, cmd: await res.json() };
  if (kind === POLL_IDLE) return { kind };          // idle timeout → re-poll
  if (kind === POLL_SUPERSEDED) return { kind };     // displaced → back off hard
  if (kind === POLL_UNAUTHORIZED) throw new Error("unauthorized");
  throw new Error(`poll_${res.status}`);
}

// Persist a "superseded" flag (for the options page to surface) + warn once.
// Never throws — a storage failure must not wedge the loop. We only WRITE on a
// state change so a steady-state loser doesn't spam storage.
async function setSuperseded(cfg) {
  try {
    const { superseded } = await storageBounded(
      chrome.storage.local.get("superseded"), "superseded.get");
    if (!superseded) {
      await storageBounded(
        chrome.storage.local.set({ superseded: true, supersededSince: Date.now() }),
        "superseded.set");
      // eslint-disable-next-line no-console
      console.warn(
        "[browser-bridge] superseded by another instance sharing this routing key" +
        (cfg.label ? ` ("${cfg.label}")` : "") +
        " — give each Brave profile a UNIQUE label in the extension options.");
    }
  } catch (e) { /* ignore */ }
}

async function clearSuperseded() {
  try {
    const { superseded } = await storageBounded(
      chrome.storage.local.get("superseded"), "superseded.get");
    if (superseded) {
      await storageBounded(
        chrome.storage.local.set({ superseded: false, supersededSince: 0 }),
        "superseded.set");
    }
  } catch (e) { /* ignore — including our own bound expiring */ }
}

async function postResult(cfg, envelope) {
  await fetch(`${base(cfg.port)}/result`, {
    method: "POST",
    headers: authHeaders(cfg.token),
    // Stamp our instanceId so the server scopes the reply to this instance.
    body: JSON.stringify(resultWithInstance(envelope, cfg.instanceId)),
    signal: abortAfter(loopTiming().resultMs),
  });
}

// The alarm-side recovery. `running` alone cannot distinguish "a loop is polling
// happily" from "a loop is parked on an await that will never settle", so the
// alarm consults lastLoopTickAt: a loop that has not completed an iteration in
// LOOP_STALL_MS is wedged, and we retire it (bump the generation, clear the
// latch) before kicking a fresh one. Exported for unit tests.
//
// Duplicate POLLING is guarded by re-checking the generation with NO await
// between the check and the `fetch` — the check therefore lives inside
// pollOnce(), not in loop(), because `activeTabSnapshot()` sits between them.
// The retired loop's finally also declines to clear `running` unless it still
// owns the generation. So an abandoned await that settles LATER lets that loop
// exit without polling alongside the new one. It MAY still finish posting a
// result it had already dequeued, which is deliberate — see the note in loop().
//
// ⚠ Two earlier versions of this comment asserted the guarantee while the check
// sat one frame too shallow, and both were falsified by a probe. If you move it,
// re-measure with a gate parked in whatever the last await before the fetch is.
export function keepaliveTick(now = Date.now()) {
  if (running && lastLoopTickAt !== null
      && (now - lastLoopTickAt) > loopTiming().stallMs) {
    // eslint-disable-next-line no-console
    console.warn("[browser-bridge] poll loop wedged for "
      + Math.round((now - lastLoopTickAt) / 1000) + "s — force-restarting");
    loopGeneration += 1;      // retire the wedged loop
    running = false;          // release the latch it can never release itself
    lastLoopTickAt = now;     // don't re-fire on the very next alarm
    breadcrumb("(loop)", null, "force-restart");
  }
  loop();
}

async function loop() {
  if (running) return;
  running = true;
  const myGen = loopGeneration;
  lastLoopTickAt = Date.now();
  let attempt = 0;
  // Have we been retired by a keepalive force-restart? A generation check at the
  // TOP of the while body is NOT sufficient: it only fires BETWEEN iterations, so
  // a loop retired while parked mid-iteration resumes and completes that whole
  // iteration — including its /poll — alongside its replacement. Measured: parking
  // the loop inside config()'s storage read, retiring it, then releasing produced
  // one extra poll from the retired loop.
  //
  // The top-of-iteration check is still worth keeping for a SECOND reason: it
  // stops a retired loop stamping the shared `lastLoopTickAt`, which would
  // refresh the liveness clock and mask a genuine wedge of its replacement.
  const retired = () => myGen !== loopGeneration;
  try {
    // eslint-disable-next-line no-constant-condition
    while (true) {
      // A force-restart retired us: exit WITHOUT clearing `running` (the finally
      // checks the generation) so the replacement loop keeps the latch.
      if (retired()) return;
      lastLoopTickAt = Date.now();
      try {
        // config() reads chrome.storage.local — the SAME unbounded chrome.* class
        // this whole change exists to close, and it sits on EVERY iteration. It is
        // inside the try so a timed-out read becomes a backoff+retry rather than
        // an escape that kills the loop until the next alarm.
        const cfg = await promiseWithTimeout(config(), loopTiming().storageMs,
                                             "config", {}, "op_timeout");
        if (!cfg.token) { await sleep(5000); continue; }
        // An early bail so a retired loop does not even run activeTabSnapshot().
        // It is an OPTIMISATION, not the guarantee — the binding check lives
        // inside pollOnce(), immediately before the fetch, because there is an
        // await (chrome.tabs.query) between here and there.
        //
        // Deliberately NOT gating execute()/postResult(): a command already
        // dequeued from the server has no other owner, so dropping it would
        // strand the caller until its cmd_timeout. Posting a finished result from
        // a retired loop is harmless (the server correlates by command id, cids
        // are unique per submit, and a timed-out submit has already stripped the
        // outbox — so a late post is a no-op, never a misroute). POLLING from a
        // retired loop is the thing that must not happen.
        if (retired()) return;
        const r = await promiseWithTimeout(pollOnce(cfg, retired),
                                           loopTiming().pollMs, "poll", {},
                                           "op_timeout");
        if (r.kind === POLL_RETIRED) return;   // retired inside pollOnce
        attempt = 0;                             // healthy round-trip
        if (r.kind === POLL_SUPERSEDED) {
          // Another instance on this host claimed our routing key (a duplicate
          // LABEL, or a storage reset). Surface it and BACK OFF HARD instead of
          // hot re-registering — otherwise the two same-label workers mutually
          // supersede at loopback speed (a livelock). Auto-recovers if the other
          // instance goes away; the human fix is a unique label per profile.
          await setSuperseded(cfg);
          await sleep(SUPERSEDE_BACKOFF_MS + Math.floor(Math.random() * 1000));
          continue;
        }
        await clearSuperseded();
        if (r.kind === POLL_COMMAND && r.cmd) {
          // execute() self-bounds at EXEC_OP_BUDGET_MS and NEVER throws — it
          // always returns an envelope, so a timed-out op is reported to the
          // server as a normal error and the loop iterates.
          const envelope = await execute(r.cmd);
          await promiseWithTimeout(postResult(cfg, envelope),
                                   loopTiming().resultMs, "result", {},
                                   "op_timeout");
        }
      } catch (e) {
        // Transport error (server down, unauthorized, network, a poll/result
        // budget expiring) → backoff. Bounded: nextBackoffMs caps at 30s.
        const wait = nextBackoffMs(attempt++) + Math.floor(Math.random() * 250);
        await sleep(wait);
      }
    }
  } finally {
    // Only clear the latch if we still OWN it — a loop retired by a
    // keepalive force-restart must not clear the flag its replacement set.
    if (myGen === loopGeneration) running = false;
  }
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

// --- MV3 keepalive + background wiring -------------------------------------- //
// All the real-browser side effects (event listeners, the keepalive alarm, and the
// immediate loop kick) are grouped here so a unit test can import this module for its
// pure OPS glue WITHOUT starting the poll loop or requiring chrome.runtime/alarms:
// set `globalThis.BROWSER_BRIDGE_NO_AUTOSTART = true` before importing.
function startBackground() {
  chrome.runtime.onInstalled.addListener(() => loop());
  chrome.runtime.onStartup.addListener(() => loop());
  chrome.alarms.create("bridge-keepalive", { periodInMinutes: 1 });
  chrome.alarms.onAlarm.addListener((a) => {
    // keepaliveTick, NOT loop(): a bare loop() call hits `if (running) return`
    // and is structurally incapable of clearing a wedged loop — which is exactly
    // why the pre-0.4.0 alarm never recovered the instance and only a manual ↻ did.
    if (a.name === "bridge-keepalive") keepaliveTick();
  });
  // If Chrome detaches our debugger out-of-band (tab crash/close, DevTools opened, or
  // the user hitting the "an extension is debugging this browser" banner's Cancel),
  // drop the tracked attach so we never think we still hold it. withCdp already
  // always-detaches per op; this is the belt-and-braces for an external detach.
  if (chrome.debugger && chrome.debugger.onDetach) {
    chrome.debugger.onDetach.addListener((source) => {
      if (source && source.tabId != null) cdpAttached.delete(source.tabId);
    });
  }
  // Kick immediately when the worker is first evaluated.
  loop();
}

if (!(typeof globalThis !== "undefined" && globalThis.BROWSER_BRIDGE_NO_AUTOSTART)) {
  startBackground();
}

// Exported for reuse / unit tests (the frame glue is exercised against a mocked
// chrome in tests/service_worker.test.mjs).
// `cdpAttached` is exported for TESTS only — it is the leak-visibility invariant
// (a failed detach must leave the tab tracked), which cannot be asserted from the
// outside otherwise. Nothing in the extension imports it.
// `loop` + `loopState` are exported for TESTS only — the wedge regression
// (tests/loop_wedge.test.mjs) has to drive a real loop iteration against a
// never-settling op and observe that it releases, which cannot be asserted from
// the outside. Nothing in the extension imports them.
// `emulationState` is exported for TESTS only — it is the sticky-emulation Map, and
// asserting "close cleared it" / "a vanished tab dropped it" requires seeing it.
// Nothing in production reads it from outside this module.
export { execute, OPS, ALLOWED_OPS, cdpAttached, loop, emulationState,
         documentEmulation };

// A read/reset window onto the loop's private liveness state, for tests.
export const loopState = {
  get running() { return running; },
  get lastLoopTickAt() { return lastLoopTickAt; },
  get generation() { return loopGeneration; },
  // Reset between tests (each test file gets its own module instance, but a
  // single file may drive the loop more than once).
  reset() { running = false; lastLoopTickAt = null; loopGeneration = 0; },
  // Pretend the loop last ticked `ms` ago — lets a test exercise the stall
  // branch of keepaliveTick without waiting LOOP_STALL_MS of real time.
  backdate(ms) { lastLoopTickAt = Date.now() - ms; },
  // Retire the current loop (the same mechanism keepaliveTick uses): it exits at
  // the top of its next iteration. A test needs this because loop() is a
  // deliberate `while (true)` with no other exit.
  retire() { loopGeneration += 1; },
};
