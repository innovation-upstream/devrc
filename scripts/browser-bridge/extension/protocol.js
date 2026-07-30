// protocol.js — the pure, browser-independent half of the browser-bridge
// extension. Everything here is testable with `node --test` (no chrome.* APIs),
// and the op set MIRRORS server.py's ALLOWED_OPS (the shared JSON contract —
// asserted by protocol.test.mjs and documented in ../../README.md).

// The command ops the bridge understands. MUST equal server.py ALLOWED_OPS.
// `open` (create a per-session tab) and `close` (remove it) back the per-session
// tab-isolation model; the server injects the target `tabId` on tab-scoped ops.
// `text` is a CHEAP read: it returns the tab's visible innerText (optionally
// scoped to a CSS selector) instead of full outerHTML — a ~98% token cut vs
// `getHtml`, so a cheap model (the opencode browser-agent) doesn't drown in HTML.
// `frames`/`click`/`type`/`key` are the CDP (chrome.debugger) ops added for
// any-frame reads + TRUSTED input (see the CDP section lower in this file):
// `frames` lists the tab's frames, `click`/`type`/`key` dispatch trusted input,
// and `--frame` routes a read (getHtml/text/eval) INTO a chosen cross-origin frame.
// `activate` foregrounds the target tab (chrome.tabs.update{active} +
// chrome.windows.update{focused}) so a foreground-REQUIRING web app (a heavy SPA
// Chrome throttles while backgrounded) actually loads and can then be driven. It
// is the ONE op that deliberately STEALS the user's focus; every other op is
// non-intrusive. Tab-scoped (it targets a specific tab); no new permission.
// `upload` populates an <input type=file> via CDP DOM.setFileInputFiles — Chrome
// reads the file BY PATH itself (same host as the browser), so NO file bytes
// cross the bridge. It is a bounded TYPED CDP op exactly like click/type/key:
// selector + path args, own-tab-scoped, #189-bounded, scheme-checked; there is
// NO raw-CDP passthrough. It IS a data-exfil-capable action (an explicit
// operator decision to let the autonomous agent read ANY path) — so the server
// AUDIT-LOGS every upload (op + target domain + path).
export const ALLOWED_OPS = [
  "getHtml", "text", "eval", "tabs", "nav", "screenshot", "open", "close",
  "frames", "click", "type", "key", "activate", "upload",
];

// Per-op required fields (mirrors server.py REQUIRED_FIELDS). The server already
// validates these, but the SW re-checks so a hand-crafted command can't wedge it.
export const REQUIRED_FIELDS = {
  eval: ["js"],
  nav: ["url"],
  click: ["selector"],
  type: ["text"],
  key: ["key"],
  upload: ["selector", "path"],   // the file-input selector + the ABSOLUTE local path
};

// Validate an inbound command dict. Returns { ok:true } or { ok:false, error }.
export function validateCommand(cmd) {
  if (!cmd || typeof cmd !== "object") return { ok: false, error: "body_not_object" };
  if (!ALLOWED_OPS.includes(cmd.op)) return { ok: false, error: "unknown_op" };
  for (const f of REQUIRED_FIELDS[cmd.op] || []) {
    if (!cmd[f]) return { ok: false, error: `missing_field:${f}` };
  }
  return { ok: true };
}

// A successful result envelope for command `id`. `data` is the op-specific
// payload the server hands back to the skill under result.data.
export function resultEnvelope(id, data) {
  return { id, ok: true, data };
}

// A failure envelope for command `id` (op threw / unsupported in this browser).
export function errorEnvelope(id, error) {
  return { id, ok: false, error: String(error) };
}

// --- `text` op: cheap innerText extraction + normalization ------------------ //
// Default byte cap for the `text` op. Generous for a read (~32 KB ≈ ~8K tokens)
// but bounds a pathological page so a cheap model isn't flooded. The CLI passes
// this by default; a caller can override via `--max-bytes` (0 → uncapped).
export const TEXT_MAX_BYTES_DEFAULT = 32 * 1024;

// Normalize raw innerText for return: collapse runs of blank lines (3+ newlines
// → a single blank line), strip trailing whitespace per line, trim the ends,
// THEN byte-cap. Pure + unit-tested (no chrome.* needed); the SW calls this on
// the injected innerText result. `maxBytes<=0` disables the cap. When truncation
// happens the returned text ends with a `\n…[truncated N bytes]` note and
// `truncated` is the number of bytes dropped (0 when untruncated).
//
// Byte-accurate truncation uses the UTF-8 encoding (a multi-byte char is never
// split): we cut at a code-point boundary at or under the byte budget.
export function normalizeText(raw, maxBytes = TEXT_MAX_BYTES_DEFAULT) {
  let s = String(raw == null ? "" : raw);
  // Normalize newlines, strip per-line trailing spaces, collapse blank runs.
  s = s.replace(/\r\n?/g, "\n")
       .replace(/[ \t]+\n/g, "\n")
       .replace(/\n{3,}/g, "\n\n")
       .trim();
  const cap = Number(maxBytes) || 0;
  if (cap <= 0) return { text: s, truncated: 0 };
  const enc = new TextEncoder();
  const full = enc.encode(s);
  if (full.length <= cap) return { text: s, truncated: 0 };
  // Largest prefix whose UTF-8 encoding fits the cap (binary search over the
  // string length → never splits a multi-byte char).
  let lo = 0, hi = s.length;
  while (lo < hi) {
    const mid = Math.ceil((lo + hi) / 2);
    if (enc.encode(s.slice(0, mid)).length <= cap) lo = mid; else hi = mid - 1;
  }
  const kept = s.slice(0, lo);
  const dropped = full.length - enc.encode(kept).length;
  return { text: `${kept}\n…[truncated ${dropped} bytes]`, truncated: dropped };
}

// Compile a user `eval` snippet into a single callable, choosing between the
// expression form (`return (src)`) and the statement form (`src`) WITHOUT ever
// executing a side effect twice.
//
// The distinction that matters: a *construction* SyntaxError means the
// expression-wrapped body could not be PARSED (e.g. `src` is a statement like
// `const x = 1;`), so we legitimately fall back to the statement form. A
// *runtime* throw only happens later, when the returned function is CALLED — it
// must propagate as the op error and must NOT trigger a second execution of an
// already-run side effect. By deciding the form at PARSE time and returning one
// function, the caller invokes it exactly once.
//
// `FunctionCtor` is injectable for unit testing; production passes the real
// `Function`. NOTE: service_worker.js's injected `eval` executor mirrors this
// logic inline (an injected function can't import this module) — keep in sync.
export function compileEval(src, FunctionCtor = Function) {
  try {
    // Parses the expression-wrapped form. A SyntaxError here is a *parse*
    // failure — never a side effect (the body is not executed by construction).
    return FunctionCtor(`return (${src})`);
  } catch (e) {
    if (e instanceof SyntaxError) {
      // Expression form is unparseable → compile the statement form instead.
      // (If THAT is also a SyntaxError it propagates — genuinely invalid JS.)
      return FunctionCtor(src);
    }
    throw e;
  }
}

// Reconnect / re-poll backoff after a transport error, capped. Attempt 0 → base.
// Deterministic (no jitter) so it is unit-testable; the SW adds a small random
// jitter at call time.
export function nextBackoffMs(attempt, baseMs = 1000, capMs = 30000) {
  const n = Math.max(0, attempt | 0);
  return Math.min(capMs, baseMs * Math.pow(2, n));
}

// --- screenshot capture: settle + retry (background-tab robustness) --------- //
// captureVisibleTab of a JUST-activated background tab can fail two ways, both
// TRANSIENT:
//   1. "Failed to capture tab: image readback failed" — the tab was made active
//      but Chrome hasn't painted its first frame yet, so the GPU read-back has
//      no pixels.
//   2. "This request exceeds the MAX_CAPTURE_VISIBLE_TAB_CALLS_PER_SECOND quota."
//      — Chrome throttles captureVisibleTab to ~2 calls/sec (≈500ms spacing). A
//      retry fired too soon after the first attempt trips this.
// CRITICAL SPACING INVARIANT: retries must be spaced ≥ the quota window, else the
// retry meant to recover a readback error just trips the quota error instead
// (the original PR #181 bug: a 150ms backoff fired a 2nd capture inside the same
// 1s quota window). So the retry backoff is ≥~600ms and a quota hit waits a full
// ~1s window before the next attempt.
// The decision logic (classify + spaced retry loop + the
// activate→settle→capture→restore orchestration) is pure and injectable here;
// service_worker.js supplies the real chrome.* side effects. Keep the SW's
// screenshot op in sync with this.

export const CAPTURE_MAX_ATTEMPTS = 3;      // total capture tries before giving up
// Linear backoff base. MUST be ≥ Chrome's captureVisibleTab quota window
// (~2/sec ≈ 500ms) with margin, so two capture attempts never land inside one
// quota window: attempt-1 retry waits base·1 (700ms), attempt-2 waits base·2.
export const CAPTURE_RETRY_BASE_MS = 700;
// After a quota error specifically, wait AT LEAST a full quota window (~1s) so
// the next attempt is safely outside it (belt-and-braces over the base spacing).
export const CAPTURE_QUOTA_WAIT_MS = 1000;
// Transient (retry-worthy) capture failures — the GPU/paint race and the
// per-second capture quota, NOT a permanent permission/URL error (retrying THOSE
// just burns the cmd_timeout). Matched case-insensitively against the error
// message. Kept deliberately narrow.
const TRANSIENT_CAPTURE_PATTERNS = [
  "image readback failed",   // the observed background-tab paint race
  "readback",                // any GPU read-back failure phrasing
  "max_capture_visible_tab_calls_per_second", // the ~2/sec capture quota
];

// The per-second capture quota substring (matched case-insensitively). A quota
// hit needs a longer wait (a full quota window) than a plain readback retry.
const CAPTURE_QUOTA_PATTERN = "max_capture_visible_tab_calls_per_second";

function errText(err) {
  const msg = (err && err.message != null) ? err.message : err;
  return String(msg == null ? "" : msg).toLowerCase();
}

// True when `err`'s message looks like a transient capture failure worth a retry.
// Accepts an Error, a string, or anything stringifiable; unknown/empty → false
// (fail closed: an unclassifiable error is treated as permanent, not retried).
export function isTransientCaptureError(err) {
  const s = errText(err);
  if (!s) return false;
  return TRANSIENT_CAPTURE_PATTERNS.some((p) => s.includes(p));
}

// True when `err` is specifically the per-second captureVisibleTab quota error.
export function isCaptureQuotaError(err) {
  const s = errText(err);
  return !!s && s.includes(CAPTURE_QUOTA_PATTERN);
}

const defaultSleep = (ms) => new Promise((r) => setTimeout(r, ms));

// Call `capture()` (→ Promise<dataUrl>) with a bounded retry on a TRANSIENT
// failure. A non-transient error propagates IMMEDIATELY (no wasted retries); a
// transient error backs off and retries up to `maxAttempts` total, then the last
// error propagates. The backoff SPACES attempts ≥ Chrome's captureVisibleTab
// quota window (linear base·attempt, base ≥~600ms) so a retry never re-trips the
// per-second quota; a quota error waits at least a full window (`quotaWaitMs`).
// `sleep` is injectable for tests. Returns whatever `capture()` resolves to on
// the first success.
export async function captureWithRetry(capture, opts = {}) {
  const maxAttempts = opts.maxAttempts || CAPTURE_MAX_ATTEMPTS;
  const baseMs = opts.baseMs == null ? CAPTURE_RETRY_BASE_MS : opts.baseMs;
  const quotaWaitMs = opts.quotaWaitMs == null ? CAPTURE_QUOTA_WAIT_MS : opts.quotaWaitMs;
  const sleep = opts.sleep || defaultSleep;
  let lastErr;
  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    try {
      return await capture();
    } catch (e) {
      lastErr = e;
      // Permanent error, or out of attempts → give up now.
      if (!isTransientCaptureError(e) || attempt === maxAttempts) throw e;
      // Space the next attempt ≥ the quota window. Linear base·attempt is already
      // ≥ a window; a quota hit waits at least a full window on top of that.
      let delay = baseMs * attempt;
      if (isCaptureQuotaError(e)) delay = Math.max(delay, quotaWaitMs);
      await sleep(delay);
    }
  }
  throw lastErr; // unreachable (loop always returns or throws) — belt & braces
}

// --- `activate` op: foreground the tab + bounded wait-for-load -------------- //
// `activate` brings the target tab to the FOREGROUND (see service_worker.js's
// executor: chrome.tabs.update{active} + chrome.windows.update{focused}) so a
// foreground-REQUIRING SPA — one Chrome throttles while the tab is backgrounded
// (#175 keeps the agent's tab backgrounded by design) — actually boots. After
// foregrounding, we OPTIONALLY wait (bounded) for the tab to finish loading so
// the caller gets a more-loaded tab to drive. ALL of the timing/decision logic
// is pure + unit-tested here; the SW supplies the chrome.tabs.get side effect.
//
// #189 NO-WEDGE DISCIPLINE: everything is bounded WELL under the server's
// cmd_timeout (20s) — the wait is capped at ACTIVATE_WAIT_MAX_MS (8s) and a
// tab that has NO live renderer (discarded / unloaded by Chrome's memory saver)
// or that never reaches "complete" returns PROMPTLY, never hangs the poll loop.

// Default wait after foregrounding (modest — most SPAs paint their shell fast).
export const ACTIVATE_WAIT_DEFAULT_MS = 3000;
// Hard cap on the wait, kept well under the server cmd_timeout (20s) so the SW
// always answers BEFORE the server gives up (and before the next /poll starves).
export const ACTIVATE_WAIT_MAX_MS = 8000;
// How often to re-check the tab's load status while waiting.
export const ACTIVATE_POLL_MS = 150;
// A short paint settle after status:"complete" so a just-booted SPA has a beat
// to render its first controls before the caller reads/drives it.
export const ACTIVATE_SETTLE_MS = 250;

// Clamp a caller-supplied waitMs into [0, ACTIVATE_WAIT_MAX_MS]. undefined/null/
// "" → the modest default; <=0 or non-finite → 0 (no wait). Never exceeds the
// cap (the #189 bound). Pure.
export function clampActivateWaitMs(waitMs) {
  if (waitMs === undefined || waitMs === null || waitMs === "") {
    return ACTIVATE_WAIT_DEFAULT_MS;
  }
  const n = Number(waitMs);
  if (!Number.isFinite(n) || n <= 0) return 0;
  return Math.min(Math.floor(n), ACTIVATE_WAIT_MAX_MS);
}

// True when we should STOP waiting on a tab: it finished loading
// (status:"complete"), OR it has no live renderer to ever finish (discarded /
// unloaded → fail-fast, never wait for a "complete" that cannot come), OR it is
// gone (null). This is the #189 discarded-tab guard for the activate wait. Pure.
export function tabLoadSettled(tab) {
  if (!tab) return true;                                   // gone → stop
  if (tab.discarded || tab.status === "unloaded") return true; // no renderer → stop
  return tab.status === "complete";
}

// Poll `getTab()` until the tab is settled (loaded, or discarded/gone) or the
// bounded wait elapses; then, when it settled by LOADING (not discard), a short
// paint settle. Returns { tab, waited, timedOut } — `tab` is the freshest tab
// object. EVERYTHING is bounded (waitMs is clamped ≤ ACTIVATE_WAIT_MAX_MS) so a
// never-completing / discarded tab returns promptly and can NEVER wedge the SW
// poll loop (#189). getTab/sleep/now are injected so this is unit-tested with no
// real clock and no real browser. Pure orchestration.
export async function waitForTabLoad(getTab, opts = {}) {
  const waitMs = clampActivateWaitMs(opts.waitMs);
  const pollMs = opts.pollMs == null ? ACTIVATE_POLL_MS : opts.pollMs;
  const settleMs = opts.settleMs == null ? ACTIVATE_SETTLE_MS : opts.settleMs;
  const sleep = opts.sleep || defaultSleep;
  const now = opts.now || Date.now;
  let tab = await getTab();
  if (waitMs <= 0) return { tab, waited: false };
  const deadline = now() + waitMs;
  while (!tabLoadSettled(tab)) {
    const remaining = deadline - now();
    if (remaining <= 0) return { tab, waited: true, timedOut: true };
    await sleep(Math.min(pollMs, remaining));
    tab = await getTab();
  }
  // Settled by a real load (not a discard) → a brief paint settle, then re-read.
  if (tab && tab.status === "complete" && settleMs > 0) {
    await sleep(settleMs);
    tab = await getTab();
  }
  return { tab, waited: true };
}

// --- hidden-tab self-announcing reads (prevent the "false outage") ---------- //
// A tab opened via `open` is BACKGROUND, so document.visibilityState==="hidden"
// and Chromium THROTTLES it — a heavy SPA never renders, and text/html/eval/frames
// return an empty shell that is indistinguishable from a broken site. So every
// read op reports the tab's visibilityState in its result, and when the tab is
// hidden it self-announces (data.hidden=true + a note pointing at `activate`) so
// an operator/agent is not fooled into declaring a false outage. Pure — the SW
// supplies the tab's document.visibilityState (which reflects the tab; an OOPIF's
// document follows the tab, so a --frame read reports it the same way).
export const HIDDEN_TAB_NOTE =
  "tab is hidden — background tabs are throttled, so SPA content may not have " +
  "rendered; run 'browser activate' or expect a shell-only DOM.";

// Merge the tab's visibilityState into a read result's `data`. A truthy
// visibilityState is recorded; when it is exactly "hidden", the result ALSO
// carries data.hidden=true + data.note so the read self-announces. A null/absent
// visibilityState (the probe failed / a mock omitted it) adds nothing — the read
// still returns its content. Returns the same object (mutated) for convenience.
export function annotateVisibility(data, visibilityState) {
  if (data && typeof data === "object" && visibilityState) {
    data.visibilityState = visibilityState;
    if (visibilityState === "hidden") {
      data.hidden = true;
      data.note = HIDDEN_TAB_NOTE;
    }
  }
  return data;
}

// --- upload op: populate an <input type=file> via CDP DOM.setFileInputFiles --- //
// The element is resolved to a CDP RemoteObject (objectId) via Runtime.evaluate,
// verified to be a real file input, then handed to DOM.setFileInputFiles with the
// ABSOLUTE path — Chrome reads the file itself, so no bytes cross the bridge. All
// of the pure string/probe pieces live here so the SW stays thin + unit-tested.

// Expression that resolves the file-input element to a RemoteObject (returnByValue
// MUST be false so CDP hands back the node's objectId, not a serialized clone).
export function fileInputSelectorExpression(selector) {
  return `document.querySelector(${JSON.stringify(String(selector))})`;
}

// Function declaration for Runtime.callFunctionOn(objectId,...): true iff the
// resolved node is genuinely an <input type=file> (else `not_a_file_input`). Kept
// as a STRING (callFunctionOn takes a function-declaration string) and self-
// contained (references only `this`).
export const FILE_INPUT_CHECK_FN =
  "function(){return this.tagName==='INPUT'&&this.type==='file';}";

// The basename of an absolute path (POSIX). The RESULT returns ONLY the basename
// (the full path stays server/CLI-side + in the audit log) — a trailing slash is
// ignored; "" when nothing usable remains. Pure.
export function basenameOf(path) {
  const s = String(path == null ? "" : path).replace(/\/+$/, "");
  const i = s.lastIndexOf("/");
  return i >= 0 ? s.slice(i + 1) : s;
}

// --- CDP (chrome.debugger) ops: any-frame reads + trusted input ------------- //
// The `debugger` permission is the biggest-blast-radius permission the bridge
// holds, so ALL of the security-relevant CDP decision logic is pure + unit-tested
// HERE (the SW is only the thin chrome.debugger glue). The three invariants this
// section enforces, all provable without a real browser:
//   1. STRICT attach scope — a CDP op only ever attaches to a real WEB page
//      (http/https); a chrome://, extension, devtools, or file: tab is REFUSED
//      before any attach (assertCdpAttachable). Combined with the server routing a
//      CDP op only to the caller's owned/target tab (and the agent's tab being
//      FORCED), the model can never attach chrome.debugger to another tab/profile.
//   2. ALWAYS detach — withCdpSession runs attach→op→detach with the detach in a
//      finally, so a thrown op still detaches (no leaked attachment / stuck banner).
//   3. TYPED ops only — there is NO generic "run this CDP method" surface here; the
//      SW maps each bounded op (frames/click/type/key/frame-read/screenshot) to a
//      FIXED set of CDP commands. The model supplies only typed scalars, never a
//      CDP method/params — so arbitrary CDP (Page.navigate file://, Browser.*, …)
//      is unreachable. Keep it that way: never add a passthrough executor.

// The CDP version the bridge attaches with (Chrome's stable protocol channel).
export const CDP_VERSION = "1.3";

// The ONLY URL schemes chrome.debugger may attach to: real web content. The
// browser's own pages (chrome:/brave:/edge:/about:), other extensions
// (chrome-extension:), the devtools (devtools:), and local files (file:) are
// privileged surfaces we must never let the autonomous, hostile-page-reading agent
// drive — and Chrome blocks most of them anyway. Enforced BEFORE attach.
export const CDP_ATTACHABLE_SCHEMES = Object.freeze(["http:", "https:"]);

// The lowercased scheme of a URL (incl. trailing ":"), or "" when unparseable.
export function cdpSchemeOf(url) {
  try { return (new URL(String(url)).protocol || "").toLowerCase(); }
  catch { return ""; }
}

// True iff `url` is a real web page the bridge may attach chrome.debugger to.
export function isCdpAttachableUrl(url) {
  return CDP_ATTACHABLE_SCHEMES.includes(cdpSchemeOf(url));
}

// Throw a clear refusal (BEFORE any attach) when the target tab is not an
// attachable web page. The message names the offending scheme so the caller sees
// WHY (e.g. the active tab was chrome://newtab). This is invariant #1 above and is
// asserted by the unit tests without needing a real browser.
export function assertCdpAttachable(url) {
  if (!isCdpAttachableUrl(url)) {
    throw new Error(`cdp_attach_refused:${cdpSchemeOf(url) || "<no-scheme>"}`);
  }
}

// The actionable error when the target tab has no live renderer to attach to.
export const TAB_DISCARDED_MESSAGE =
  "tab_discarded: the target tab was unloaded by Chrome (memory saver) and has no " +
  "live renderer to attach to — reload the tab or bring it to the foreground, then retry.";

// Fail FAST (before any chrome.debugger.attach) when the target tab has no live
// renderer. A DISCARDED / unloaded tab (Chrome's memory saver evicts background
// tabs) has no renderer process, so chrome.debugger.attach / Page.getFrameTree
// would NEVER resolve — the observed root cause of the SW wedge. Detecting it here
// turns an unbounded hang into an immediate, clear, actionable error (and the
// per-call timeouts in withCdpSession are the backstop for any OTHER hang cause).
// `owned_tab_gone` when the tab is missing entirely. Pure — the SW passes the
// chrome.tabs.get(tabId) result; unit-tested without a real browser.
export function assertTabCdpReady(tab) {
  if (!tab) throw new Error("owned_tab_gone");
  if (tab.discarded || tab.status === "unloaded") throw new Error(TAB_DISCARDED_MESSAGE);
}

// --- SW-side CDP timeouts: never let a hung chrome.debugger call wedge the SW - //
// A chrome.debugger.attach / sendCommand / detach that NEVER resolves (the
// classic case: the tab was DISCARDED by Chrome's memory saver, so it has no live
// renderer and Page.* never answers) would leave the SW's command handler blocked
// on an unresolved await FOREVER — the /poll loop never resumes and the whole
// instance silently drops (the bug this module fixes). So EVERY chrome.debugger
// call is raced against a bounded timeout: on timeout the op SETTLES (rejects)
// and control returns to the poll loop; the hung underlying promise is abandoned.
//
// Deadlines are chosen WELL UNDER the server's cmd_timeout (default 20s) so the
// SW returns a clear error BEFORE the server gives up AND before the next /poll is
// starved: attach ≤ ATTACH, each command ≤ COMMAND, whole op ≤ BUDGET (a cumulative
// backstop). All injectable via `deps.timeouts`/`deps.timers` for deterministic
// unit tests (no real clock needed).
export const CDP_ATTACH_TIMEOUT_MS = 8000;
export const CDP_COMMAND_TIMEOUT_MS = 8000;
export const CDP_OP_BUDGET_MS = 15000;

// Race `promise` against a `ms` deadline. On timeout, REJECT with
// `cdp_timeout:<label>` (the phase — attach / a CDP method / op / detach) so the
// caller sees WHICH call hung; the underlying promise is left pending (abandoned)
// but the returned promise SETTLES, so the awaiter is never blocked past `ms`. A
// promise that settles on its own (resolve OR reject) wins the race and its
// value/error passes through unchanged, and the timer is cleared so nothing lingers.
// `ms <= 0` disables the bound (returns the promise as-is). Timers injectable.
export function promiseWithTimeout(promise, ms, label, timers = {}) {
  const p = Promise.resolve(promise);
  if (!(ms > 0)) return p;
  const setT = timers.setTimeout || setTimeout;
  const clearT = timers.clearTimeout || clearTimeout;
  let handle;
  const timeout = new Promise((_, reject) => {
    handle = setT(() => reject(new Error(`cdp_timeout:${label}`)), ms);
  });
  return Promise.race([p, timeout]).finally(() => clearT(handle));
}

// Orchestrate a CDP op with a per-op attach→run→ALWAYS-detach lifecycle, every
// chrome.debugger side effect INJECTED so the invariants are unit-testable without
// a real browser:
//   * the target URL is validated BEFORE attach — a privileged/other tab is refused
//     and `attach` is NEVER called (invariant #1);
//   * every chrome.debugger call is TIME-BOUNDED (attach ≤ attachMs, each command ≤
//     commandMs via the wrapped `send` handed to `run`, whole op ≤ budgetMs) so a
//     hung CDP call can never wedge the SW's poll loop — the op settles with a
//     `cdp_timeout:<phase>` error instead (the no-wedge guarantee);
//   * `detach` ALWAYS runs — on success, on any error `run` throws, AND after a
//     timed-out/failed attach (best-effort) — so a debugger attachment can never
//     leak (a leak = a stuck banner + an open surface) even when attach hung
//     (invariant #2);
//   * a `detach` failure/HANG (tab already gone / already detached / no renderer)
//     is bounded + swallowed so it never masks the real result/error or re-wedges
//     the finally.
// `deps`: { url, attach():Promise, detach():Promise, send?(method,params):Promise,
//   run(send):Promise<result>, timeouts?:{attachMs,commandMs,budgetMs}, timers? }.
// `send` is OPTIONAL: when present, `run` is handed a timeout-WRAPPED send so each
// CDP command is individually bounded; when absent, `run` receives undefined
// (back-compat with call sites that don't issue commands). The wrapped send takes an
// OPTIONAL 3rd arg `sessionId` — forwarded to the raw send so a command can target a
// flat auto-attached sub-session (an OOPIF target) while STILL being bounded by the
// per-command timeout; call sites that don't use flat sessions simply omit it.
export async function withCdpSession(deps) {
  assertCdpAttachable(deps.url);        // BEFORE attach — refuse privileged/other tab
  const timers = deps.timers || {};
  const t = deps.timeouts || {};
  const attachMs = t.attachMs == null ? CDP_ATTACH_TIMEOUT_MS : t.attachMs;
  const commandMs = t.commandMs == null ? CDP_COMMAND_TIMEOUT_MS : t.commandMs;
  const budgetMs = t.budgetMs == null ? CDP_OP_BUDGET_MS : t.budgetMs;
  // Bounded, best-effort detach — used in the finally AND after a failed attach so
  // a late-completing attach can't leak a session. Its own timeout means a hung
  // detach can't re-wedge the handler.
  const safeDetach = async () => {
    try { await promiseWithTimeout(deps.detach(), commandMs, "detach", timers); }
    catch (e) { /* already detached / tab gone / detach timed out — best effort */ }
  };
  // Attach, time-bounded. On timeout/failure, best-effort detach (the attach may
  // have half-completed / may complete late) then rethrow so the op fails cleanly.
  try {
    await promiseWithTimeout(deps.attach(), attachMs, "attach", timers);
  } catch (e) {
    await safeDetach();
    throw e;
  }
  try {
    const rawSend = deps.send;
    const send = rawSend
      ? (method, params, sessionId) =>
          promiseWithTimeout(rawSend(method, params, sessionId), commandMs, method, timers)
      : undefined;
    return await promiseWithTimeout(
      Promise.resolve(deps.run(send)), budgetMs, "op", timers);
  } finally {
    await safeDetach();
  }
}

// Flatten a CDP Page.getFrameTree result into a compact [{frameId,url,name,parentId}]
// list (depth-first, main frame first). METADATA only — frame id/url/name, never
// frame CONTENT. Pure; used by matchCdpFrameId to map a target frame url → its CDP
// (string) frameId for the `eval --frame` SAME-PROCESS path. (Frame ENUMERATION for
// the `frames` op / `--frame` resolution moved to chrome.webNavigation — OOPIF-aware —
// see normalizeWebNavFrames below.)
export function flattenFrameTree(frameTree) {
  const out = [];
  const walk = (node, parentId) => {
    if (!node || !node.frame) return;
    const f = node.frame;
    out.push({ frameId: f.id, url: f.url || "", name: f.name || "",
               parentId: parentId || null });
    for (const child of node.childFrames || []) walk(child, f.id);
  };
  walk(frameTree);
  return out;
}

// The bounded key NAME → CDP Input.dispatchKeyEvent params map. Deliberately small:
// the nav/edit keys an agent needs to drive an app (submit, tab between fields,
// dismiss, delete, arrows/paging). An unknown key is REFUSED (keeps the surface
// bounded — no arbitrary key injection). Printable text goes through `type`, not here.
export const KEY_EVENTS = Object.freeze({
  Enter:      { key: "Enter", code: "Enter", keyCode: 13, text: "\r" },
  Tab:        { key: "Tab", code: "Tab", keyCode: 9 },
  Escape:     { key: "Escape", code: "Escape", keyCode: 27 },
  Backspace:  { key: "Backspace", code: "Backspace", keyCode: 8 },
  Delete:     { key: "Delete", code: "Delete", keyCode: 46 },
  ArrowUp:    { key: "ArrowUp", code: "ArrowUp", keyCode: 38 },
  ArrowDown:  { key: "ArrowDown", code: "ArrowDown", keyCode: 40 },
  ArrowLeft:  { key: "ArrowLeft", code: "ArrowLeft", keyCode: 37 },
  ArrowRight: { key: "ArrowRight", code: "ArrowRight", keyCode: 39 },
  Home:       { key: "Home", code: "Home", keyCode: 36 },
  End:        { key: "End", code: "End", keyCode: 35 },
  PageUp:     { key: "PageUp", code: "PageUp", keyCode: 33 },
  PageDown:   { key: "PageDown", code: "PageDown", keyCode: 34 },
});

// A few case-insensitive aliases so a caller need not match the exact casing.
const KEY_ALIASES = { esc: "Escape", del: "Delete", return: "Enter" };

// Resolve a caller key name to its CDP event params (exact → alias → case-insensitive
// canonical). Throws `unknown_key:<name>` for anything outside the bounded set.
export function keyEventParams(name) {
  const raw = String(name == null ? "" : name).trim();
  if (!raw) throw new Error("unknown_key:<none>");
  if (KEY_EVENTS[raw]) return KEY_EVENTS[raw];
  const low = raw.toLowerCase();
  if (KEY_ALIASES[low] && KEY_EVENTS[KEY_ALIASES[low]]) return KEY_EVENTS[KEY_ALIASES[low]];
  for (const k of Object.keys(KEY_EVENTS)) if (k.toLowerCase() === low) return KEY_EVENTS[k];
  throw new Error(`unknown_key:${raw}`);
}

// The click point for an element given its bounding rect (viewport coords in the
// element's OWN frame) plus the origin offset of that frame within the top-level
// viewport ({x,y}; {0,0} for the top frame). getBoundingClientRect is frame-local,
// while Input.dispatchMouseEvent takes TOP-LEVEL viewport coords — so a click inside
// a cross-origin iframe must add the iframe's on-page origin. Returns the element
// CENTER, rounded. Pure; the SW supplies the rect (Runtime.evaluate in the frame)
// and the frame origin (DOM.getBoxModel on the frame owner).
export function clickPoint(rect, frameOffset) {
  const off = frameOffset || { x: 0, y: 0 };
  const x = (off.x || 0) + (rect.x || 0) + (rect.width || 0) / 2;
  const y = (off.y || 0) + (rect.y || 0) + (rect.height || 0) / 2;
  return { x: Math.round(x), y: Math.round(y) };
}

// Extract the top-left origin {x,y} of a CDP DOM.getBoxModel content quad
// ([x1,y1,x2,y2,x3,y3,x4,y4], clockwise from top-left, top-level viewport CSS px).
// Used to offset a click into a sub-frame. Pure.
export function boxModelOrigin(model) {
  const q = model && model.content;
  if (Array.isArray(q) && q.length >= 2) return { x: q[0], y: q[1] };
  return { x: 0, y: 0 };
}

// Wrap a user `eval` snippet for CDP Runtime.evaluate (which takes an EXPRESSION
// string, not a function). Returns { expression, fallback }: try `expression`
// first; if CDP reports a SyntaxError, retry `fallback` (the statement form).
// Mirrors compileEval's expression-vs-statement duality for the frame path (the
// non-frame path still uses chrome.scripting). Pure + unit-tested.
export function frameEvalExpressions(src) {
  const s = String(src == null ? "" : src);
  return {
    expression: `(function(){ return (${s}) })()`,
    fallback: `(function(){ ${s} })()`,
  };
}

// True when a CDP Runtime.evaluate exceptionDetails describes a SyntaxError (so the
// caller retries the statement-form fallback). Checks the structured className then
// the text, case-insensitively. Pure.
export function isCdpSyntaxError(exceptionDetails) {
  if (!exceptionDetails) return false;
  const ex = exceptionDetails.exception || {};
  if (String(ex.className || "").toLowerCase() === "syntaxerror") return true;
  const t = `${exceptionDetails.text || ""} ${ex.description || ""}`.toLowerCase();
  return t.includes("syntaxerror");
}

// Extract a human error string from a CDP Runtime.evaluate exceptionDetails.
export function cdpExceptionText(exceptionDetails) {
  if (!exceptionDetails) return "eval_failed";
  const ex = exceptionDetails.exception || {};
  return String(ex.description || exceptionDetails.text || "eval_failed");
}

// --- CDP `eval --frame`: run an arbitrary JS STRING in a target frame ---------- //
// WHY the CDP path (not chrome.scripting) for `eval --frame`: chrome.scripting.
// executeScript runs a SERIALIZED FUNCTION, not an arbitrary JS string. The fixed-func
// frame ops (text/html/click/type/key) work that way, but `eval` is an arbitrary user
// STRING — routing it through a `func` that `new Function(src)`s inside the frame's
// ISOLATED world hits the extension CSP / returns a null-as-success, so it never truly
// evaluates (the #190 regression). The reliable way to run a JS STRING in a SPECIFIC
// frame — including a cross-origin OOPIF (a separate renderer/target under site
// isolation) — is CDP `Runtime.evaluate` in that frame's execution context.
//
// The numeric webNavigation frameId does NOT map 1:1 to a CDP frame id/target, so we
// locate the target frame by URL (resolveWebNavFrame gives the frame's url). Two paths:
//   * SAME-PROCESS frame → it appears in the top session's Page.getFrameTree; grab its
//     CDP frameId (matchCdpFrameId), Page.createIsolatedWorld → an executionContextId,
//     Runtime.evaluate({contextId}).
//   * CROSS-ORIGIN OOPIF → it is NOT in the top session's frame tree (getFrameTree from
//     the top target omits OOPIFs — the same reason #190 moved enumeration to
//     webNavigation). Target.setAutoAttach({autoAttach,flatten}) auto-attaches to the
//     OOPIF's target, surfacing a flat sessionId (pickOopifSessionId matches it by url);
//     Runtime.evaluate is then issued in THAT session's default context.
// The SW glue (service_worker.js) supplies the chrome.debugger side effects; ALL of it
// is time-bounded by withCdpSession (#189) so a bad frame fails fast, never wedges.

// Match a target frame URL against a CDP Page.getFrameTree result, returning the CDP
// (string) frameId of the SAME-PROCESS frame whose url equals `targetUrl`, else null
// (→ the frame is an OOPIF in a separate target, take the auto-attach path). Exact url
// equality (getFrameTree urls mirror webNavigation urls for same-process frames); the
// main frame (frameId 0 / the top url) matches its root node. Pure.
export function matchCdpFrameId(frameTree, targetUrl) {
  const want = String(targetUrl == null ? "" : targetUrl);
  if (!want) return null;
  for (const f of flattenFrameTree(frameTree)) {
    if (f.url === want) return f.frameId;
  }
  return null;
}

// Pick the flat sessionId of the auto-attached OOPIF target whose url matches
// `targetUrl`. `attached` is the list of {sessionId,url} the SW collected from
// Target.attachedToTarget events after Target.setAutoAttach. Exact url match first,
// then a suffix/prefix-tolerant fallback (an OOPIF target url can carry a trailing
// slash the frame url lacks, or vice-versa). Returns null when none matches (→
// frame_not_found). Pure.
export function pickOopifSessionId(attached, targetUrl) {
  const want = String(targetUrl == null ? "" : targetUrl);
  if (!want) return null;
  for (const a of attached || []) if (a && a.url === want) return a.sessionId;
  // Tolerant fallback: ignore a single trailing slash difference.
  const norm = (u) => String(u || "").replace(/\/+$/, "");
  const w = norm(want);
  for (const a of attached || []) if (a && norm(a.url) === w) return a.sessionId;
  return null;
}

// Interpret a CDP Runtime.evaluate result under the NEVER-SILENT-NULL contract:
//   * an exceptionDetails (a thrown error / CSP violation) → THROW
//     `frame_eval_failed:<reason>` — a failure to execute must be a CLEAR op error,
//     never a value:null masquerading as success (the exact #190 bug).
//   * otherwise return the result's value verbatim — a genuine null/undefined result
//     IS a legitimate value and is returned AS such (distinct from a failure). When
//     returnByValue was set, `result.value` is the structured value; a bare handle
//     (no value key) → undefined.
// Pure — the SW passes the raw Runtime.evaluate reply.
export function evalValueOrThrow(cdpResult) {
  const r = cdpResult || {};
  if (r.exceptionDetails) {
    throw new Error(`frame_eval_failed:${cdpExceptionText(r.exceptionDetails)}`);
  }
  return r.result ? r.result.value : undefined;
}

// Frame-scoped read/probe expression builders (run in the frame's isolated world
// via CDP Runtime.evaluate). Pure string builders so the SW stays thin + testable.
export function frameHtmlExpression() {
  return "document.documentElement.outerHTML";
}
export function frameTextExpression(selector) {
  const sel = selector ? JSON.stringify(String(selector)) : '""';
  return `(function(s){var el=s?document.querySelector(s):document.body;` +
         `return el?el.innerText:"";})(${sel})`;
}
// Element-rect probe for click: scroll into view, return the frame-local bounding
// rect (or null when the selector matches nothing).
export function elementRectExpression(selector) {
  const sel = JSON.stringify(String(selector));
  return `(function(s){var el=document.querySelector(s);if(!el)return null;` +
         `el.scrollIntoView({block:"center",inline:"center"});` +
         `var r=el.getBoundingClientRect();` +
         `return {x:r.x,y:r.y,width:r.width,height:r.height};})(${sel})`;
}
// Focus probe for `type` with a selector: focus the element, return whether it existed.
export function focusExpression(selector) {
  const sel = JSON.stringify(String(selector));
  return `(function(s){var el=document.querySelector(s);if(!el)return false;` +
         `el.focus();return true;})(${sel})`;
}

// --- OOPIF-capable frame ops: chrome.webNavigation + chrome.scripting --------- //
// WHY this replaced the CDP `Page.getFrameTree` frame path (the OOPIF bug):
// `Page.getFrameTree` from the TOP tab target only enumerates SAME-PROCESS frames.
// Under Chrome's site isolation a CROSS-ORIGIN iframe is an OUT-OF-PROCESS iframe
// (OOPIF) living in a SEPARATE renderer/target, so getFrameTree from the top tab
// silently OMITS it — `frames` could never list it and `--frame` could never
// target it (the whole point of a cross-origin embed like model-benchmarking.civit.ai).
//
// The fix uses the two chrome.* APIs that ARE OOPIF-aware:
//   * chrome.webNavigation.getAllFrames({tabId}) enumerates EVERY frame in the tab
//     — same-process AND cross-origin OOPIFs — as {frameId:<number>, parentFrameId,
//     url}. This is the enumeration `frames` returns and `--frame` resolves against.
//   * chrome.scripting.executeScript({target:{tabId, frameIds:[id]}}) injects INTO a
//     specific frame — including a cross-origin OOPIF, given the extension's
//     <all_urls> host permission — so a frame read/click/type/key reaches the OOPIF
//     where CDP could not, and WITHOUT the chrome.debugger banner.
//
// The frame IDENTIFIER is therefore the NUMERIC webNavigation frameId (NOT the old
// CDP string frame id). Enumeration + resolution are pure + unit-tested here; the SW
// is the thin chrome.webNavigation/chrome.scripting glue.

// Map a chrome.webNavigation.getAllFrames() result to the compact, METADATA-ONLY
// list the `frames` op returns: [{frameId:<number>, url, parentFrameId:<number>}].
// The top frame is frameId 0 / parentFrameId -1. Entries without a numeric frameId
// are dropped defensively. NEVER includes frame CONTENT — id/url/parent only. Pure.
export function normalizeWebNavFrames(frames) {
  const out = [];
  for (const f of frames || []) {
    if (!f || typeof f.frameId !== "number") continue;
    out.push({
      frameId: f.frameId,
      url: f.url || "",
      parentFrameId: (typeof f.parentFrameId === "number") ? f.parentFrameId : -1,
    });
  }
  return out;
}

// The lowercased hostname of a URL, or "" when unparseable. Used to prefer a HOST
// match over a bare path match when resolving a `--frame` url substring. Pure.
export function frameHostOf(url) {
  try { return (new URL(String(url)).hostname || "").toLowerCase(); }
  catch { return ""; }
}

// Resolve a caller `--frame <sel>` against a normalized getAllFrames list, returning
// the matched FRAME OBJECT ({frameId,url,parentFrameId}). `sel` may be an exact
// numeric frameId (e.g. "0" or 5 — the webNavigation id) OR a URL substring
// (case-insensitive). Resolution order (deterministic, no silent first-match):
//   1. An exact NUMERIC frameId always WINS.
//   2. A url SUBSTRING prefers a HOST match over a bare path match — the substring is
//      tested against each frame's HOSTNAME first. This disambiguates the civitai
//      self-shadow: `--frame model-benchmarking` matches the TOP frame's PATH
//      (civitai.com/apps/run/model-benchmarking) but the OOPIF's HOST
//      (model-benchmarking.civit.ai) — the host match is the intended frame.
//   3. If the substring still matches MULTIPLE frames ambiguously (all host, or all
//      path), throw `ambiguous_frame:<n> [<frameId>:<url>, …]` listing the candidates
//      so the caller re-issues with a NUMERIC frameId, INSTEAD of silently choosing
//      the first (the #190/#192 "silently wrong frame" bug).
// Throws `frame_not_found:<sel>` when nothing matches and `frame_not_specified` for an
// empty sel (a caller bug — the SW only calls this when a frame IS targeted). Tab-scoped
// by construction: `frames` comes from ONE tab's getAllFrames, so a resolved frame can
// only ever reference a frame of THAT tab — a frameId belonging to another tab is simply
// absent → frame_not_found. Returning the OBJECT (not just the id) lets the SW both
// inject by frameId AND report the FRAME'S OWN url in the op result (so a caller can
// confirm it read the intended frame, not the top document), and — for `eval --frame` —
// map the numeric webNavigation frameId to the frame's URL so the CDP path can locate
// its execution context (see cdp_protocol: matchCdpFrameId / pickOopifSessionId). Pure.
export function resolveWebNavFrame(frames, sel) {
  const s = String(sel == null ? "" : sel).trim();
  if (!s) throw new Error("frame_not_specified");
  const list = frames || [];
  // 1. exact numeric frameId wins outright.
  if (/^\d+$/.test(s)) {
    const n = Number(s);
    for (const f of list) if (f.frameId === n) return f;
  }
  const low = s.toLowerCase();
  // 2. prefer HOST matches over PATH matches.
  const hostMatches = list.filter((f) => frameHostOf(f.url).includes(low));
  const candidates = hostMatches.length
    ? hostMatches
    : list.filter((f) => (f.url || "").toLowerCase().includes(low));
  if (candidates.length === 1) return candidates[0];
  // 3. ambiguous → a clear error listing the candidates (never a silent first-match).
  if (candidates.length > 1) {
    const listed = candidates.map((f) => `${f.frameId}:${f.url || ""}`).join(", ");
    throw new Error(`ambiguous_frame:${candidates.length} [${listed}]`);
  }
  throw new Error(`frame_not_found:${s}`);
}

// Back-compat convenience: resolve `--frame <sel>` to just the NUMERIC frameId (the
// fixed-func frame ops inject by id). Delegates to resolveWebNavFrame so the two can
// never diverge. Pure.
export function resolveWebNavFrameId(frames, sel) {
  return resolveWebNavFrame(frames, sel).frameId;
}

// --- injected page functions (run INSIDE the resolved frame via executeScript) --- //
// These are handed to chrome.scripting.executeScript as `func` (with `args`), so
// Chrome serializes each via Function.prototype.toString and runs it in the target
// frame — INCLUDING a cross-origin OOPIF. HARD REQUIREMENT: each must be SELF-
// CONTAINED — reference ONLY its own parameters and page globals (document / window /
// MouseEvent / KeyboardEvent / Event). It must close over NOTHING from this module
// (a closed-over reference is undefined once serialized into the page). They return
// STRUCTURED-CLONEABLE values (executeScript deep-clones the result across the
// process boundary). Exported so their behaviour is unit-tested directly.
//
// TRUST NOTE (documented, honest): events these dispatch are SYNTHETIC
// (`isTrusted === false`), NOT the CDP `Input.*` trusted events. A trusted event
// from the top tab target cannot easily reach an OOPIF, so synthetic-in-frame is the
// REACHABLE path for cross-origin-frame input — and it drives the vast majority of
// web apps (which listen for ordinary click/input/keydown). The top-frame (no
// `--frame`) input path keeps using CDP trusted events (see service_worker.js).

// outerHTML of the frame's document. Self-contained.
export function frameReadHtmlFn() {
  return document.documentElement.outerHTML;
}

// Visible innerText of `selector` (or the whole body when empty). Self-contained;
// the SW normalizes + byte-caps the returned raw text via normalizeText.
export function frameReadTextFn(selector) {
  var el = selector ? document.querySelector(selector) : document.body;
  return el ? el.innerText : "";
}

// Evaluate `src` in the frame's (isolated-world) context and return its completion
// value. Mirrors compileEval's expression-vs-statement duality WITHOUT double-running
// a side effect: build the chosen form ONCE at parse time, then call it once. A
// runtime throw propagates (executeScript rejects → the op errors). Self-contained.
export function frameEvalFn(src) {
  var fn;
  try {
    fn = new Function("return (" + src + ")");
  } catch (e) {
    if (e instanceof SyntaxError) fn = new Function(src);
    else throw e;
  }
  return fn();
}

// Synthetic click on `selector`: scroll into view, then dispatch a realistic
// pointerdown/mousedown → pointerup/mouseup → a SINGLE `click` sequence. Returns
// {ok:true,x,y} (frame-local center) or {ok:false,error} when the selector matches
// nothing. Self-contained.
//
// CLICK-EXACTLY-ONCE (the live-observed 0→2 double-fire fix): a SYNTHETIC
// mousedown/mouseup does NOT synthesize a `click` the way a TRUSTED press/release
// does — only real user input (or the CDP Input.* path) auto-generates the click.
// So we dispatch the `click` event ONCE ourselves. We deliberately do NOT ALSO call
// el.click(): dispatching a `click` event AND calling el.click() fired the target's
// onclick handler TWICE from one op (the bug). One dispatched `click` = one handler.
export function frameClickFn(selector) {
  var el = document.querySelector(selector);
  if (!el) return { ok: false, error: "element_not_found" };
  el.scrollIntoView({ block: "center", inline: "center" });
  var r = el.getBoundingClientRect();
  var x = Math.round(r.left + r.width / 2);
  var y = Math.round(r.top + r.height / 2);
  var opts = { bubbles: true, cancelable: true, view: window,
               clientX: x, clientY: y, button: 0 };
  el.dispatchEvent(new MouseEvent("pointerdown", opts));
  el.dispatchEvent(new MouseEvent("mousedown", opts));
  el.dispatchEvent(new MouseEvent("pointerup", opts));
  el.dispatchEvent(new MouseEvent("mouseup", opts));
  el.dispatchEvent(new MouseEvent("click", opts));   // exactly one click — NOT el.click() too
  return { ok: true, x: x, y: y };
}

// Synthetic type into `selector` (or the frame's activeElement when empty): focus,
// set .value (or textContent for a contenteditable), then dispatch input+change so a
// framework's listeners see the update. Returns {ok:true,typed:<len>} or
// {ok:false,error} when there is no valid target. NEVER returns the text. Self-contained.
//
// REQUIRE A REAL EDITABLE TARGET (#190 audit): an <input>/<textarea> (has a settable
// `value`) or a contenteditable element. With an empty selector the target is
// document.activeElement, which DEFAULTS to <body> when nothing is focused — <body>
// has no `value` and is not contenteditable, so the old code set nothing yet still
// dispatched input/change and returned {ok:true,typed:N}: a FALSE success claiming N
// chars were written when nothing was. Now that case returns {ok:false,
// error:"no_editable_target"} — no false `typed`.
export function frameTypeFn(selector, text) {
  var el = selector ? document.querySelector(selector) : document.activeElement;
  if (selector && !el) return { ok: false, error: "element_not_found" };
  var editable = !!el && (el.isContentEditable === true || ("value" in el));
  if (!editable) return { ok: false, error: "no_editable_target" };
  if (typeof el.focus === "function") el.focus();
  if ("value" in el) el.value = text;
  else el.textContent = text;   // contenteditable
  el.dispatchEvent(new Event("input", { bubbles: true }));
  el.dispatchEvent(new Event("change", { bubbles: true }));
  return { ok: true, typed: text.length };
}

// Synthetic key dispatch to `selector` (or the frame's activeElement): keydown→
// (keypress if the key is printable)→keyup with the pre-resolved `keyParams`
// (from keyEventParams, so the bounded-key gate + unknown_key refusal happen in the
// SW BEFORE injection). Returns {ok:true,key} or {ok:false,error} when a given
// selector matches nothing. Self-contained.
export function frameKeyFn(selector, keyParams) {
  var el = selector ? document.querySelector(selector) : document.activeElement;
  if (selector && !el) return { ok: false, error: "element_not_found" };
  var target = el || document.body;
  if (el && typeof el.focus === "function") el.focus();
  var init = { bubbles: true, cancelable: true, key: keyParams.key,
               code: keyParams.code, keyCode: keyParams.keyCode,
               which: keyParams.keyCode };
  target.dispatchEvent(new KeyboardEvent("keydown", init));
  if (keyParams.text) target.dispatchEvent(new KeyboardEvent("keypress", init));
  target.dispatchEvent(new KeyboardEvent("keyup", init));
  return { ok: true, key: keyParams.key };
}

// The full-page screenshot clip from a CDP Page.getLayoutMetrics result. Uses the
// css content size (the full scrollable document) so `--fullpage` captures beyond
// the viewport. Pure; returns a clip suitable for Page.captureScreenshot.
export function fullPageClip(layoutMetrics) {
  const m = layoutMetrics || {};
  const size = m.cssContentSize || m.contentSize || {};
  return { x: 0, y: 0,
           width: Math.ceil(size.width || 0),
           height: Math.ceil(size.height || 0),
           scale: 1 };
}

// --- poll-response classification ----------------------------------------- //
// The server answers GET /poll with exactly one of these shapes; classifying by
// status keeps the SW loop's branching pure + unit-testable:
//   200 + command JSON        → execute it                       (POLL_COMMAND)
//   204 (no body)             → idle keepalive; re-poll promptly  (POLL_IDLE)
//   409 + {error:"superseded"}→ THIS connection was displaced by a newer one
//        sharing its routing key (a duplicate LABEL on this host, or a storage
//        reset). Must NOT hot re-poll — back off hard, else two same-label
//        profiles mutually supersede at loopback speed (a livelock). POLL_SUPERSEDED
//   401                       → bad/absent bearer token          (POLL_UNAUTHORIZED)
//   anything else             → transport error (null → generic nextBackoffMs)
export const POLL_COMMAND = "command";
export const POLL_IDLE = "idle";
export const POLL_SUPERSEDED = "superseded";
export const POLL_UNAUTHORIZED = "unauthorized";

export function classifyPollStatus(status) {
  switch (status) {
    case 200: return POLL_COMMAND;
    case 204: return POLL_IDLE;
    case 409: return POLL_SUPERSEDED;
    case 401: return POLL_UNAUTHORIZED;
    default: return null;
  }
}

// How long a SUPERSEDED instance waits before re-attempting registration. Long
// enough that two same-label profiles can't hot-loop (loopback re-polls would be
// microseconds apart); short enough to auto-recover if the other instance goes
// away. The real fix is a UNIQUE label per profile — surfaced in the options/log.
export const SUPERSEDE_BACKOFF_MS = 30000;

// --- multi-instance registration (mirrors server.py) ---------------------- //
// The server keeps a registry of connected instances keyed by a routing key =
// label (if set) else the stable auto-id. The extension identifies itself on
// EVERY /poll via headers, and echoes its instanceId in the /result body so the
// server can scope the reply. These helpers keep that wire shape pure + testable.

// The logical registration payload: a stable auto-id + the optional user label.
// `label` is normalised to "" when unset so the shape is stable.
export function pollRequestPayload(instanceId, label) {
  return { instanceId: String(instanceId || ""), label: label ? String(label) : "" };
}

// Cap a header-bound string BEFORE percent-encoding. A pathological multi-KB
// active-tab url/title could otherwise blow past http.server's ~64KB header-line
// limit and fail the whole poll; 2048 raw chars is generous and safe. This is a
// LENGTH bound only — encodeURIComponent still does the header-injection defence
// (no raw CR/LF can survive), so the two are orthogonal.
export const MAX_HEADER_VALUE_CHARS = 2048;
export function capHeaderValue(s, max = MAX_HEADER_VALUE_CHARS) {
  const str = String(s == null ? "" : s);
  return str.length > max ? str.slice(0, max) : str;
}

// Request headers the SW sends on /poll to identify its instance. Label +
// active-tab + ext-version strings are capped then URL-encoded (HTTP header
// values must be ASCII-safe AND bounded); the server decodes them. Empty values
// are omitted so a bare instance registers cleanly. `active` is an optional
// { url, title } for cheap /health enrichment. `extVersion` is the extension's
// own manifest version (chrome.runtime.getManifest().version) — surfaced by
// `whoami` so an operator can see which extension BUILD is loaded per instance;
// omitted (→ null in whoami) by a legacy build that predates version reporting.
export function pollHeaders(instanceId, label, active, extVersion) {
  const h = { "X-Bridge-Instance-Id": String(instanceId || "") };
  if (label) h["X-Bridge-Label"] = encodeURIComponent(capHeaderValue(label));
  if (active && active.url) h["X-Bridge-Active-Url"] = encodeURIComponent(capHeaderValue(active.url));
  if (active && active.title) h["X-Bridge-Active-Title"] = encodeURIComponent(capHeaderValue(active.title));
  if (extVersion) h["X-Bridge-Ext-Version"] = encodeURIComponent(capHeaderValue(String(extVersion)));
  return h;
}

// Stamp the instanceId onto a result envelope so /result is instance-scoped.
export function resultWithInstance(envelope, instanceId) {
  return { ...envelope, instanceId: String(instanceId || "") };
}
