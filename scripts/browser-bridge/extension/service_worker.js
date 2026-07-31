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
} from "./protocol.js";

const DEFAULT_PORT = 8788;
let running = false;
// Wall-clock (Date.now) of the last completed poll-loop iteration. `running` is a
// LATCH — it says a loop was started, not that one is alive — so the keepalive
// alarm needs an independent liveness signal to tell "wedged" from "idle-polling".
// null = no iteration has completed yet (the loop was only just kicked).
let lastLoopTickAt = null;
// Monotonically increasing loop generation. The keepalive force-restart bumps it,
// which retires any loop still parked on an abandoned await: that loop exits at
// its next iteration and its finally declines to clear `running` (it no longer
// owns the flag), so a force-restart can never leave two pollers racing.
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
      throw new Error("owned_tab_gone");   // the tab was closed out-of-band
    }
  }
  return activeTab();
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
      catch (e) { throw new Error("owned_tab_gone"); }
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
    run: (send) => run(send),
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
      return annotateVisibility(
        { url: frame.url || tab.url, title: tab.title, html, frame: cmd.frame }, vis);
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
      return wakeAnnotate({ url: tab.url, title: tab.title, html: w.value }, w);
    }
    // No frame → the lighter chrome.scripting top-frame read (no debugger banner).
    const vis = await tabVisibilityState(tab.id);
    const [inj] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: () => document.documentElement.outerHTML,
    });
    return annotateVisibility({ url: tab.url, title: tab.title, html: inj.result }, vis);
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
    // --frame → read innerText INSIDE the chosen (cross-origin OOPIF) frame via
    // chrome.scripting (reaches an out-of-process iframe; no debugger banner).
    if (cmd && cmd.frame) {
      const frame = await resolveFrame(tab.id, cmd.frame);
      const vis = await tabVisibilityState(tab.id);   // BEFORE the read → read stays last
      const raw = await execInFrame(tab.id, frame.frameId, frameReadTextFn, [sel]);
      const { text, truncated } = normalizeText(raw, cap);
      // Report the FRAME's own url (see getHtml) so the caller confirms the right frame.
      return annotateVisibility(
        { url: frame.url || tab.url, title: tab.title, text, truncated, frame: cmd.frame }, vis);
    }
    // --wake → un-throttle + read in ONE CDP session (opt-in; see getHtml).
    if (cmd && cmd.wake) {
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
      return wakeAnnotate({ url: tab.url, title: tab.title, text, truncated }, w);
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
    return annotateVisibility({ url: tab.url, title: tab.title, text, truncated }, vis);
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
      return annotateVisibility({ url: frame.url || tab.url, value, frame: cmd.frame }, vis);
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
      return wakeAnnotate({ url: tab.url, value: w.value }, w);
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
    return annotateVisibility({ url: tab.url, value: inj.result }, vis);
  },

  async tabs() {
    const tabs = await chrome.tabs.query({});
    return {
      tabs: tabs.map((t) => ({
        id: t.id, url: t.url, title: t.title,
        active: t.active, windowId: t.windowId,
      })),
    };
  },

  async nav(cmd) {
    const tab = await targetTab(cmd);
    await chrome.tabs.update(tab.id, { url: cmd.url });
    return { tabId: tab.id, url: cmd.url };
  },

  // Screenshot the owned/target tab. PRIMARY path is CDP Page.captureScreenshot,
  // which captures a BACKGROUND / occluded / non-foreground tab (the whole point —
  // it fixes the captureVisibleTab "can only grab the foreground tab" limitation,
  // and lets two profiles each screenshot their own tab). A FAST path keeps the
  // cheap, banner-free captureVisibleTab for a tab that IS already visible (and not
  // --fullpage); any failure there falls through to the CDP path. `--fullpage`
  // captures the whole scrollable document (CDP only). Attach is REFUSED on a
  // privileged tab (assertCdpAttachable inside withCdp) before any attach.
  async screenshot(cmd) {
    const tab = await targetTab(cmd);
    const fullpage = !!(cmd && cmd.fullpage);
    if (tab.active && !fullpage) {
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
        const metrics = await send("Page.getLayoutMetrics");
        params.clip = fullPageClip(metrics);
        params.captureBeyondViewport = true;
      }
      const { data } = await send("Page.captureScreenshot", params);
      return `data:image/png;base64,${data}`;
    });
    return { url: tab.url, dataUrl, via: "cdp" };
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
    const point = await withCdp(tab.id, tab.url, async (send) => {
      const rect = await cdpEval(send, undefined, elementRectExpression(selector));
      if (!rect) throw new Error(`element_not_found:${selector}`);
      const p = clickPoint(rect, { x: 0, y: 0 });
      const mouse = (type) => send("Input.dispatchMouseEvent",
        { type, x: p.x, y: p.y, button: "left", buttons: 1, clickCount: 1 });
      await mouse("mousePressed");
      await mouse("mouseReleased");
      return p;
    });
    return { url: tab.url, clicked: selector, x: point.x, y: point.y,
             frame: null, trusted: true };
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
  async close(cmd) {
    if (!cmd || cmd.tabId == null) throw new Error("missing_tabId");
    try {
      await chrome.tabs.remove(cmd.tabId);
      return { closed: cmd.tabId };
    } catch (e) {
      return { closed: cmd.tabId, alreadyGone: true };
    }
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
  // ⚠ The path→id derivation is INFERRED from documented Chromium behaviour and
  // is NOT measured here — confirm it live by recording the id before and after
  // re-pointing a profile.
  async ping() {
    return { pong: true, extensionVersion: extensionVersion(),
             id: extensionId(), ops: ALLOWED_OPS.slice() };
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

async function pollOnce(cfg) {
  const active = await activeTabSnapshot();
  const res = await fetch(`${base(cfg.port)}/poll`, {
    // Identify this instance so the server routes only its commands here.
    headers: { ...authHeaders(cfg.token),
               ...pollHeaders(cfg.instanceId, cfg.label, active, cfg.extVersion,
                              cfg.extId) },
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
// Safety against duplicate POLLING is structural, not timing-based: the retired
// loop re-checks its generation immediately before each /poll (see `retired()` in
// loop() — a top-of-iteration check alone is NOT enough, it only fires between
// iterations) and its finally only clears `running` if it still owns the
// generation. So even if the abandoned await settles LATER, that loop exits
// without polling alongside the new one. It MAY still finish posting a result it
// had already dequeued, which is deliberate — see the note in loop().
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
        // ⚠ THE check that actually prevents duplicate pollers. It must sit
        // immediately before the poll, not merely at the top of the iteration:
        // being retired mid-`config()` is the COMMON case (after this change the
        // storage reads are the loop's slowest awaits), and a retired loop that
        // polls is exactly the concurrency the generation mechanism promises to
        // prevent.
        //
        // Deliberately NOT gating execute()/postResult(): a command already
        // dequeued from the server has no other owner, so dropping it would
        // strand the caller until its cmd_timeout. Posting a finished result from
        // a retired loop is harmless (the server correlates by command id) —
        // POLLING from one is not.
        if (retired()) return;
        const r = await promiseWithTimeout(pollOnce(cfg), loopTiming().pollMs,
                                           "poll", {}, "op_timeout");
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
export { execute, OPS, ALLOWED_OPS, cdpAttached, loop };

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
