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
// `wake` UN-THROTTLES the target tab WITHOUT touching focus — the non-intrusive
// answer to "the background tab never rendered". It attaches CDP to the own tab
// and applies Emulation.setFocusEmulationEnabled (+ Page.setWebLifecycleState for
// the frozen case), holds that for a bounded settle so the page gets real
// animation frames, then detaches. See the `wake` section lower in this file for
// the measured Chromium behaviour that dictates the design.
// `activate` foregrounds the target tab (chrome.tabs.update{active} +
// chrome.windows.update{focused}). It is the LAST-RESORT op: it STEALS the
// operator's screen, and it is only needed when something genuinely requires the
// real foreground — `wake` covers throttling. Tab-scoped; no new permission.
// `upload` populates an <input type=file> via CDP DOM.setFileInputFiles — Chrome
// reads the file BY PATH itself (same host as the browser), so NO file bytes
// cross the bridge. It is a bounded TYPED CDP op exactly like click/type/key:
// selector + path args, own-tab-scoped, #189-bounded, scheme-checked; there is
// NO raw-CDP passthrough. It IS a data-exfil-capable action (an explicit
// operator decision to let the autonomous agent read ANY path) — so the server
// AUDIT-LOGS every upload (op + target domain + path).
// `ping` is the BUILD-FRESHNESS TELL. It takes no tab, touches no page and does
// nothing but answer with this service worker's own manifest version + op set.
// Its whole point is the op NAME: a build that predates it does not know the
// name, so `validateCommand` rejects it with `unknown_op`. That turns "did my
// ↻ reload actually take?" — which cost three full Brave restarts to guess at,
// because the long-poll pins the old MV3 worker alive across a reload — into a
// yes/no. CONTRACT for any future extension change that must be provably loaded:
// bump `manifest.json` version AND add a new discriminator (a new op name, or a
// new field in `ping`'s reply), so the old build cannot fake a pass.
// `emulate` puts the SESSION'S OWN tab into a device-emulation mode (viewport +
// deviceScaleFactor, touch, mobile user-agent INCLUDING userAgentMetadata, and
// media/geolocation/timezone) so an agent can do real mobile testing. See the
// EMULATION section lower in this file for the persistence model and the safety
// property that falls out of it.
export const ALLOWED_OPS = [
  "getHtml", "text", "eval", "tabs", "nav", "screenshot", "open", "close",
  "frames", "click", "type", "key", "wake", "activate", "upload", "ping",
  "emulate", "context",
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

// Default max elements for `text --annotated`. Keeps the JSON payload bounded
// while covering most pages. The CLI or caller can override via `maxItems`.
export const ANNOTATED_TEXT_MAX_ITEMS_DEFAULT = 200;

// --- page context helpers (shared by `context` op + `text`/`html` enrichment) -- //
// Parse a URL string into its component parts. Returns { domain, path, searchParams }
// where domain is the hostname, path is the pathname, and searchParams is an object
// of parsed query parameters (empty object if none). Handles empty/unparseable URLs
// gracefully by returning empty strings and an empty object.
export function parsePageContext(url) {
  if (!url) return { domain: "", path: "", searchParams: {} };
  try {
    const u = new URL(String(url));
    const sp = {};
    for (const [k, v] of u.searchParams) sp[k] = v;
    return { domain: u.hostname || "", path: u.pathname || "/", searchParams: sp };
  } catch {
    return { domain: "", path: "", searchParams: {} };
  }
}

// Annotate a data object with parsed page context (domain, path, searchParams)
// from a URL. Does NOT overwrite existing fields on `data`. Returns the same
// object (mutated) for convenience.
export function annotatePageContext(data, url) {
  if (!data || typeof data !== "object") return data;
  const ctx = parsePageContext(url);
  if (!("domain" in data)) data.domain = ctx.domain;
  if (!("path" in data)) data.path = ctx.path;
  if (!("searchParams" in data)) data.searchParams = ctx.searchParams;
  return data;
}

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

// Byte-cap an annotated elements array: if total JSON exceeds maxBytes, pop
// elements from the end until it fits. Mutates `data` in place and returns it.
// `data.truncated` is set to the byte delta (0 when nothing was dropped).
export function byteCapElements(data, maxBytes) {
  const jsonBytes = new TextEncoder().encode(JSON.stringify(data.elements)).length;
  if (jsonBytes <= maxBytes) { data.truncated = 0; return data; }
  const els = [...data.elements];
  let total = 0;
  const enc = new TextEncoder();
  let kept = 0;
  for (let i = 0; i < els.length; i++) {
    const entryBytes = enc.encode(JSON.stringify(els[i])).length;
    if (total + entryBytes > maxBytes) break;
    total += entryBytes;
    kept = i + 1;
  }
  data.elements = els.slice(0, kept);
  data.count = data.elements.length;
  data.truncated = jsonBytes - total;
  return data;
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

// --- `wake` op: un-throttle a background tab WITHOUT stealing focus --------- //
// THE PROBLEM this fixes: a tab opened by `open` is background →
// document.visibilityState==="hidden" → Chromium throttles it (NO animation
// frames at all, timers clamped to ~1 Hz), so a heavy SPA never paints and every
// read returns a shell. The historical remedy was `activate`, which FOREGROUNDS
// the tab and therefore takes the operator's screen. Telemetry caught an agent
// calling `activate` 1–5×/minute while the operator was working.
//
// MEASURED CHROMIUM BEHAVIOUR (throwaway Brave 1.89 under Xvfb, real CDP, a
// background tab; the numbers below are observed, not assumed):
//
//   baseline (hidden)                       rAF   0 /s   timers   8 /s   vis hidden
//   + Page.setWebLifecycleState(active)     rAF   0 /s                   vis hidden
//   + Emulation.setFocusEmulationEnabled    rAF  62 /s   timers 247 /s   vis VISIBLE
//   after the CDP session DETACHES          rAF   0 /s   timers   8 /s   vis hidden
//
// Three durable findings encoded here:
//
//  1. `Emulation.setFocusEmulationEnabled({enabled:true})` is what actually
//     un-throttles: the renderer reports visibilityState "visible" and produces
//     real animation frames. It moves NO window/tab focus — it is a per-session
//     renderer override, invisible to the operator and to the window manager.
//  2. `Page.setWebLifecycleState({state:"active"})` alone changed NOTHING for a
//     merely-hidden tab. It is kept because it is the only lever for a page
//     Chromium has FROZEN (memory-saver lifecycle), which focus emulation does
//     not thaw — but it is best-effort and must never fail the op.
//  3. **The un-throttled state does NOT survive detach.** It reverted completely
//     the moment the session closed. Since every CDP op here is
//     attach→run→detach-in-`finally`, a "wake once, read later" op CANNOT hand a
//     later read an un-throttled tab. Hence BOTH shapes exist:
//       * `wake` — un-throttle, hold it for a bounded settle so the page does its
//         rendering work, detach. What PERSISTS is the DOM the page produced
//         during that window (measured: the fixture's rAF-gated content rendered
//         at 472 ms and was still there after detach). This is the cheap,
//         once-per-tab answer, and it leaves the default read path untouched.
//       * `--wake` on text/html/eval — un-throttle and perform THAT read inside
//         the SAME attached session, so the read itself observes a genuinely
//         un-throttled page. Required whenever the read must see live
//         un-throttled state rather than persisted DOM.
//
// DESIGN CONSTRAINT (load-bearing): the DEFAULT text/html/eval path stays on
// chrome.scripting with NO debugger attach. Routing every read through CDP would
// make Brave flash "an extension is debugging this browser" on every single read
// — trading focus theft for banner spam. Waking is opt-in, reached only when a
// read actually came back hidden/empty.

// Default settle held with the un-throttle applied. Long enough for a typical
// SPA's first paint (the rAF-gated fixture needed ~470 ms) without wasting a
// whole second of the operator's rate-limit budget on a page that is already up.
export const WAKE_SETTLE_DEFAULT_MS = 1500;
// Hard cap on the settle. DERIVED, not picked: unlike `activate` (whose wait is the
// whole op), the settle is only ONE PHASE of a CDP op that must ALSO fit the probe
// and, for a `--wake` read, the read itself — all inside CDP_OP_BUDGET_MS (15s).
// A naive 8s cap made `html --wake=8000` = 8s settle + a read bounded by
// CDP_COMMAND_TIMEOUT_MS (8s) = up to 16s > the budget, surfacing as an opaque
// `cdp_timeout:op` (it fails safe and still detaches, but the message tells the
// caller nothing). So the ceiling is CDP_OP_BUDGET_MS − CDP_COMMAND_TIMEOUT_MS −
// 1s margin = 6s, which keeps settle + one worst-case command inside the budget by
// construction. Clamping beats documenting a footgun.
//
// Written as a literal (CDP_OP_BUDGET_MS / CDP_COMMAND_TIMEOUT_MS are declared
// LOWER in this file, so referencing them here would hit the const TDZ at module
// evaluation). The relationship is not left to a comment: `wake.test.mjs` asserts
// WAKE_SETTLE_MAX_MS + CDP_COMMAND_TIMEOUT_MS < CDP_OP_BUDGET_MS, so changing a
// budget without re-deriving this fails CI.
export const WAKE_SETTLE_MAX_MS = 6000;

// Clamp a caller-supplied wake settle into [0, WAKE_SETTLE_MAX_MS]. undefined /
// null / "" → the default; non-finite or negative → 0 (apply the un-throttle,
// don't wait). Mirrors clampActivateWaitMs. Pure.
export function clampWakeMs(waitMs) {
  if (waitMs === undefined || waitMs === null || waitMs === "") {
    return WAKE_SETTLE_DEFAULT_MS;
  }
  const n = Number(waitMs);
  if (!Number.isFinite(n) || n <= 0) return 0;
  return Math.min(Math.floor(n), WAKE_SETTLE_MAX_MS);
}

// The FIXED, ordered CDP method sequence that un-throttles a tab. Exported as
// DATA (not a live call) so the "no raw-CDP passthrough / typed-op-only" property
// is unit-assertable: there is no caller-influenced method or param anywhere in
// it. `optional:true` marks a step whose failure is swallowed (see below).
//
// Order matters: lifecycle first (thaw a frozen page), then focus emulation (the
// step that actually un-throttles a merely-hidden one).
export const WAKE_CDP_STEPS = Object.freeze([
  Object.freeze({ method: "Page.setWebLifecycleState",
                  params: Object.freeze({ state: "active" }), optional: true }),
  Object.freeze({ method: "Emulation.setFocusEmulationEnabled",
                  params: Object.freeze({ enabled: true }), optional: false }),
]);

// The EXPLICIT teardown for the step above. It is NOT enough to rely on detach
// reverting focus emulation: that revert is an implementation detail of the
// Emulation domain, not a protocol contract, and there is a concrete path where
// detach does not happen promptly — a hung/failed `chrome.debugger.detach` (tab
// mid-crash, wedged renderer) is bounded and SWALLOWED by withCdpSession's
// safeDetach, so the attachment can outlive the op. A tab left permanently
// focus-emulated is a hidden tab that believes it is focused and visible:
// un-throttled indefinitely, stuck debug banner, nothing that knows to clean it up.
//
// It also closes a credential-adjacent risk that must not rest on a measured side
// effect: `navigator.clipboard.readText()` requires a FOCUSED document plus an
// already-granted `clipboard-read` permission, and the operator's clipboard
// routinely holds a password or token. On an origin that already has that grant, a
// document that believes it is focused may be able to read it. Narrowing the
// emulated-focus window to exactly the wake, deterministically, is the fix — not
// hoping the revert happens. (Pointer-lock/fullscreen/autoplay stay closed
// regardless: they additionally need transient user activation, which `wake` never
// synthesizes.)
//
// Best-effort by construction: this runs in a `finally`, so a failure here must
// never mask the op's real result or error.
export const WAKE_CDP_TEARDOWN = Object.freeze({
  method: "Emulation.setFocusEmulationEnabled",
  params: Object.freeze({ enabled: false }),
});

// Probe run INSIDE the wake window to report what the un-throttle achieved.
// Metadata only — no page content can leave via it.
//
// It is a FUNCTION (injected via chrome.scripting, ISOLATED world), not a CDP
// expression, and that is deliberate: a CDP Runtime.evaluate with no contextId runs
// in the page's MAIN world, where a hostile page can shadow `document.visibilityState`
// / `document.hasFocus` and lie to us — turning `woke` into a page-controlled claim.
// The isolated world sees the real values.
export function wakeProbeFn() {
  return {
    visibilityState: document.visibilityState,
    readyState: document.readyState,
    hasFocus: document.hasFocus(),
  };
}

// Apply WAKE_CDP_STEPS through `send`, tolerating an optional step's failure.
// Returns { applied:[method,…], skipped:[{method,error},…] } so the op can report
// honestly which levers actually took (a Chromium that drops
// Page.setWebLifecycleState must NOT fail the wake — finding 2 above says that
// step is a no-op for the common hidden-tab case anyway). A REQUIRED step's
// failure propagates: a wake that could not un-throttle must not claim success.
// Pure orchestration — `send` is injected, so this is unit-tested with no browser.
export async function applyWakeSteps(send, steps = WAKE_CDP_STEPS) {
  const applied = [];
  const skipped = [];
  for (const step of steps) {
    try {
      await send(step.method, step.params);
      applied.push(step.method);
    } catch (e) {
      if (!step.optional) throw e;
      skipped.push({ method: step.method, error: e && e.message ? e.message : String(e) });
    }
  }
  return { applied, skipped };
}

// --- EMULATION: device emulation for real mobile testing -------------------- //
//
// THE CENTRAL PROBLEM, and why this is shaped the way it is.
//
// `withCdpSession` ALWAYS detaches in its `finally` (that is invariant #2 — a
// leaked chrome.debugger attachment is a stuck banner plus an open surface). CDP
// Emulation overrides are SESSION-SCOPED: they die the instant the debugger
// detaches. So a naive `emulate` op that just sent Emulation.setDeviceMetricsOverride
// would set a viewport that has already evaporated by the time the NEXT command
// (`screenshot`, `click`, a `text` read) runs — a confident-wrong-answer of exactly
// the class this codebase keeps shipping.
//
// The design is therefore: `emulate` STORES a normalized emulation state per tab,
// and EVERY op that attaches CDP to that tab RE-APPLIES the overrides inside its
// own session, BEFORE doing its work. Apply-then-act, every session, no exceptions.
// The state lives in a module-level Map in service_worker.js (`emulationState`);
// everything in THIS file is the pure, browser-free half: the preset table, the
// validation, and the ordered CDP step list.
//
// 🔴 THE "DIE AT DETACH" PREMISE IS ONLY MOSTLY TRUE — measured, do not restate
// the old claim. This block used to assert that because every override dies at
// detach, the tab is never emulated between ops and a crashed agent CANNOT leave
// the operator's browser distorted. Issue #319 falsified it, and the follow-up
// measurement (2026-08-03, live Brave, extension 0.7.1, laptop, `iphone-15`,
// read via `js --wake`, against a never-emulated control tab in the same window)
// says exactly which half is which:
//
//   * setDeviceMetricsOverride's VIEWPORT SIZE does NOT die at detach. After
//     `--reset` — and after a further navigation — innerWidth×innerHeight was
//     still 393×852 while the control tab read 1124×1400. Interestingly
//     devicePixelRatio, set by the same call, DID revert to 1, which points at
//     the residue being the resized render widget rather than a live override.
//   * touch, UA/UA-CH, emulated media and the timezone override DO die at
//     detach: after `--reset` they all read as the real desktop values.
//
// So the honest statement of what the design buys: between ops the tab keeps the
// emulated VIEWPORT SIZE and nothing else. A crashed agent, a killed Claude
// session or an evicted MV3 service worker CAN leave a tab sized as a phone —
// closing the tab, or `emulate --reset` (which since #319 sends
// Emulation.clearDeviceMetricsOverride and the matching clears — see
// emulationResetCdpSteps below), is what undoes it.
//
// A held session would still have been worse to own: its failure mode adds a
// permanent debug banner and leaves the UA, touch and media overrides live too,
// none of which survive today.
//
// BLAST RADIUS: enforced SERVER-side (see server.py OWNED_TAB_ONLY_OPS). Only a
// tab the calling session opened via `open` may be emulated; anything else is
// refused with `not_owned_tab`. The operator's own tabs are not resizable by an
// agent, by construction, not by convention.

// Bounds on the raw (`--width/--height/--dsf/...`) override path. Deliberately
// generous but finite: the point is to refuse the nonsense values that would make
// Chromium itself misbehave (a 0-height viewport, a negative scale factor), not to
// curate a taste level. A preset always lands inside these by definition, which is
// what the preset-integrity test asserts.
export const EMULATION_LIMITS = Object.freeze({
  minDimension: 1,
  maxDimension: 10000,     // DevTools' own device-metrics inputs stop well below this
  minScaleFactor: 0.1,
  maxScaleFactor: 10,
  maxTouchPoints: 16,      // CDP Emulation.setTouchEmulationEnabled caps at 16
  maxUserAgentChars: 1024,
  maxTimezoneChars: 64,
});

// The screenOrientation values CDP accepts.
export const EMULATION_ORIENTATIONS = Object.freeze({
  portrait: Object.freeze({ type: "portraitPrimary", angle: 0 }),
  landscape: Object.freeze({ type: "landscapePrimary", angle: 90 }),
});

// The `prefers-color-scheme` values Emulation.setEmulatedMedia accepts. "" is not
// offered — `emulate --reset` is how you stop emulating.
export const EMULATION_COLOR_SCHEMES = Object.freeze(
  ["light", "dark", "no-preference"]);

// Every key a preset entry MUST carry. Asserted by the preset-integrity test so a
// half-filled preset (the classic "I added a device and forgot the UA metadata")
// cannot ship — it would silently emulate a phone-sized DESKTOP browser.
export const PRESET_REQUIRED_KEYS = Object.freeze([
  "label", "width", "height", "deviceScaleFactor", "mobile",
  "maxTouchPoints", "userAgent", "userAgentMetadata", "source",
]);

// UA-Client-Hints metadata for Chrome-on-Android. WITHOUT this, a site that reads
// navigator.userAgentData (which every modern Chromium-based stack does in
// preference to the UA string) still sees the operator's DESKTOP Linux Chrome —
// the most commonly missed half of "set a mobile user agent". `brands` mirrors the
// GREASE-plus-two-real-brands shape Chrome actually sends.
function androidUaMetadata(model, androidVersion, chromeMajor) {
  return Object.freeze({
    brands: Object.freeze([
      Object.freeze({ brand: "Not/A)Brand", version: "8" }),
      Object.freeze({ brand: "Chromium", version: String(chromeMajor) }),
      Object.freeze({ brand: "Google Chrome", version: String(chromeMajor) }),
    ]),
    platform: "Android",
    platformVersion: androidVersion,
    architecture: "",
    model,
    mobile: true,
  });
}

// UA-CH metadata for Apple devices. `brands: []` is CORRECT and deliberate, not an
// omission: Safari does NOT implement UA-Client-Hints and sends no Sec-CH-UA at
// all, so a site sniffing userAgentData on a real iPhone sees an empty brand list.
// Setting it explicitly still MATTERS — omitting userAgentMetadata entirely leaves
// Chrome reporting the operator's real desktop brands next to an iPhone UA string,
// which is a combination no real client ever produces and which trips bot
// detection on exactly the sites worth testing.
function appleUaMetadata(model, platformVersion) {
  return Object.freeze({
    brands: Object.freeze([]),
    platform: "iOS",
    platformVersion,
    architecture: "",
    model,
    mobile: true,
  });
}

// UA-CH metadata for a RAW `--ua` string, where there is no preset to copy from.
//
// The platform is DERIVED FROM THE UA STRING, never from the `--mobile` flag. An
// earlier version keyed it off `mobile` alone, which produced
// `platform: "Android"` for an iPhone UA — precisely the "combination no real
// client ever produces" that appleUaMetadata above exists to avoid. Guessing wrong
// is worse than not guessing: an inconsistent pair is a stronger bot-detection
// signal than a blank platform.
//
// When the UA names a platform we recognise, say so; otherwise leave platform ""
// (a client that declines to state it) rather than inventing one. Brands stay
// empty either way — we have no basis to claim a Chromium version from an
// arbitrary string, and a WRONG brand list is worse than none.
export function rawUaMetadata(userAgent, mobile) {
  const ua = String(userAgent || "");
  let platform = "";
  let model = "";
  if (/\biPhone\b/.test(ua)) { platform = "iOS"; model = "iPhone"; }
  else if (/\biPad\b/.test(ua)) { platform = "iOS"; model = "iPad"; }
  else if (/\bAndroid\b/.test(ua)) { platform = "Android"; }
  else if (/\bMac OS X\b|\bMacintosh\b/.test(ua)) { platform = "macOS"; }
  else if (/\bWindows\b/.test(ua)) { platform = "Windows"; }
  else if (/\bLinux\b|\bX11\b/.test(ua)) { platform = "Linux"; }
  return Object.freeze({
    brands: Object.freeze([]),
    platform,
    platformVersion: "",
    architecture: "",
    model,
    mobile: !!mobile,
  });
}

const IOS_UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
  + "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 "
  + "Safari/604.1";
const IPADOS_UA = "Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X) "
  + "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 "
  + "Safari/604.1";
const androidUa = (model, androidVersion, chromeMajor) =>
  `Mozilla/5.0 (Linux; Android ${androidVersion}; ${model}) `
  + "AppleWebKit/537.36 (KHTML, like Gecko) "
  + `Chrome/${chromeMajor}.0.0.0 Mobile Safari/537.36`;

// The curated preset table. Small ON PURPOSE: presets exist so an agent prompt can
// say `browser emulate iphone-15` and get a REPRODUCIBLE result, not so this file
// can mirror DevTools. Anything not here is covered by the raw
// `--width/--height/--dsf/--mobile/--ua/--touch` path.
//
// ⚠ PROVENANCE, stated honestly per preset in `source`. The CSS-pixel viewport and
// deviceScaleFactor values are the logical-resolution figures that Chrome DevTools'
// device list (`front_end/models/emulation/EmulatedDevices.ts` in Chromium) uses,
// which are in turn the vendors' published logical resolutions. They were sourced
// from those published specs and cross-checked for internal consistency
// (physical pixels ÷ deviceScaleFactor = the CSS viewport, asserted by the
// preset-integrity test where a physical resolution is recorded).
//
// ⚠ NOT VERIFIED against a live DevTools device list in the session that wrote
// this — there was no browser to open (this whole change was built without touching
// live Brave, deliberately). Treat the exact figures as "the published logical
// resolution", which is what matters for layout testing, rather than as "byte-equal
// to whatever DevTools ships this month". If a preset ever disagrees with DevTools,
// DevTools wins; fix it here and say so.
export const DEVICE_PRESETS = Object.freeze({
  "iphone-15": Object.freeze({
    label: "iPhone 15",
    width: 393, height: 852, deviceScaleFactor: 3, mobile: true,
    maxTouchPoints: 5,
    physical: Object.freeze({ width: 1179, height: 2556 }),
    userAgent: IOS_UA,
    userAgentMetadata: appleUaMetadata("iPhone", "17.0"),
    source: "Apple published logical resolution 393x852 @3x (1179x2556 physical); "
      + "matches the Chrome DevTools 'iPhone 15 Pro' entry.",
  }),
  "iphone-se": Object.freeze({
    label: "iPhone SE",
    width: 375, height: 667, deviceScaleFactor: 2, mobile: true,
    maxTouchPoints: 5,
    physical: Object.freeze({ width: 750, height: 1334 }),
    userAgent: IOS_UA,
    userAgentMetadata: appleUaMetadata("iPhone", "17.0"),
    source: "Chrome DevTools' long-standing 'iPhone SE' entry: 375x667 @2x "
      + "(750x1334 physical). The smallest viewport worth regression-testing.",
  }),
  "pixel-8": Object.freeze({
    label: "Pixel 8",
    width: 412, height: 915, deviceScaleFactor: 2.625, mobile: true,
    maxTouchPoints: 5,
    physical: Object.freeze({ width: 1080, height: 2400 }),
    userAgent: androidUa("Pixel 8", "14", 126),
    userAgentMetadata: androidUaMetadata("Pixel 8", "14", 126),
    source: "Google published 1080x2400 physical; Chrome's Android dsf for this "
      + "class is 2.625, giving the 412x915 CSS viewport DevTools uses for the "
      + "Pixel 7/8 generation.",
  }),
  // ⚠ TWO iPad Minis, deliberately. The 5th-gen (2019) 768×1024 figure is what
  // Chrome DevTools' long-standing "iPad Mini" entry uses, and it remains a
  // legitimate tablet breakpoint — so it keeps a name of its own rather than being
  // quietly re-pointed. `ipad-mini` (unqualified) tracks the CURRENT shipping
  // device, the 6th-gen, whose 744×1133 viewport is materially narrower and is the
  // one a "does this work on an iPad Mini" question actually means today.
  // Re-pointing a stable name would have silently changed every existing caller's
  // result AND made the older entry's honest `source` string false.
  "ipad-mini": Object.freeze({
    label: "iPad Mini (6th gen)",
    width: 744, height: 1133, deviceScaleFactor: 2, mobile: true,
    maxTouchPoints: 5,
    physical: Object.freeze({ width: 1488, height: 2266 }),
    userAgent: IPADOS_UA,
    userAgentMetadata: appleUaMetadata("iPad", "17.0"),
    source: "Apple published 1488x2266 physical @2x for the 6th-gen iPad Mini "
      + "(2021), giving a 744x1133 CSS viewport — the current shipping device, "
      + "and materially narrower than the 2019 model below.",
  }),
  "ipad-mini-2019": Object.freeze({
    label: "iPad Mini (5th gen, 2019)",
    width: 768, height: 1024, deviceScaleFactor: 2, mobile: true,
    maxTouchPoints: 5,
    physical: Object.freeze({ width: 1536, height: 2048 }),
    userAgent: IPADOS_UA,
    userAgentMetadata: appleUaMetadata("iPad", "17.0"),
    source: "Chrome DevTools' long-standing 'iPad Mini' entry: 768x1024 @2x "
      + "(1536x2048 physical). The classic tablet breakpoint — mobile:true but "
      + "NOT phone-width.",
  }),
  "galaxy-s24": Object.freeze({
    label: "Galaxy S24",
    width: 360, height: 780, deviceScaleFactor: 3, mobile: true,
    maxTouchPoints: 5,
    physical: Object.freeze({ width: 1080, height: 2340 }),
    userAgent: androidUa("SM-S921B", "14", 126),
    userAgentMetadata: androidUaMetadata("SM-S921B", "14", 126),
    source: "Samsung published 1080x2340 physical; Chrome reports dsf 3 on this "
      + "device, giving a 360x780 CSS viewport — the narrowest mainstream "
      + "Android width currently shipping.",
  }),
});

export const PRESET_NAMES = Object.freeze(Object.keys(DEVICE_PRESETS));

// A refusal an agent/operator is meant to READ and act on. Kept as plain Errors
// with NAMED, greppable messages, exactly like `unknown_key` / `element_not_found`.
function emuErr(name) { return new Error(name); }

function requireInt(value, field, min, max) {
  const n = Number(value);
  if (!Number.isFinite(n) || !Number.isInteger(n) || n < min || n > max) {
    throw emuErr(`invalid_emulation:${field}`);
  }
  return n;
}

function requireNumber(value, field, min, max) {
  const n = Number(value);
  if (!Number.isFinite(n) || n < min || n > max) {
    throw emuErr(`invalid_emulation:${field}`);
  }
  return n;
}

function requireBool(value, field) {
  if (typeof value === "boolean") return value;
  if (value === "true" || value === 1 || value === "1") return true;
  if (value === "false" || value === 0 || value === "0") return false;
  throw emuErr(`invalid_emulation:${field}`);
}

// A UA string is echoed into a request HEADER by Chromium. A CR/LF in it is the
// classic header-injection primitive, so it is REFUSED rather than stripped —
// silently sanitizing input is how you end up unsure what actually went on the wire.
function requireUserAgent(value) {
  if (typeof value !== "string") throw emuErr("invalid_emulation:ua");
  const s = value.trim();
  if (!s || s.length > EMULATION_LIMITS.maxUserAgentChars) {
    throw emuErr("invalid_emulation:ua");
  }
  // eslint-disable-next-line no-control-regex
  if (/[\x00-\x1f\x7f]/.test(s)) throw emuErr("invalid_emulation:ua");
  return s;
}

function requireTimezone(value) {
  if (typeof value !== "string") throw emuErr("invalid_emulation:tz");
  const s = value.trim();
  if (!s || s.length > EMULATION_LIMITS.maxTimezoneChars
      || !/^[A-Za-z0-9_+\-/]+$/.test(s)) {
    throw emuErr("invalid_emulation:tz");
  }
  return s;
}

// Normalize an `emulate` command into the STORED state (or a reset marker).
//
// Returns { reset: true } for `--reset`, else a frozen state object:
//   { preset, label, metrics, touch, ua, media, geolocation, timezone }
// where every sub-object is either the exact CDP params for its method or null.
// Storing CDP-SHAPED params (rather than loose fields re-assembled at each apply)
// is deliberate: the sticky re-application path then has no logic in it to drift.
//
// Pure — no chrome.*, no clock, no state. Throws a NAMED error on bad input.
export function normalizeEmulation(cmd) {
  const c = cmd || {};
  const wantsReset = c.reset === true || c.reset === "true";

  // Raw params are meaningful ONLY as a device description; combining them with
  // --reset is contradictory (do you want the viewport or not?) and is refused
  // rather than resolved by precedence, because either precedence would surprise
  // half the callers.
  const rawKeys = ["device", "width", "height", "dsf", "mobile", "ua", "touch",
                   "maxTouchPoints", "orientation", "colorScheme", "geo", "tz"];
  const supplied = rawKeys.filter((k) => c[k] !== undefined && c[k] !== null
                                          && c[k] !== "");
  if (wantsReset) {
    if (supplied.length) throw emuErr("invalid_emulation:reset_with_params");
    return Object.freeze({ reset: true });
  }
  if (!supplied.length) throw emuErr("emulate_needs_device_or_params");

  let base = null;
  let presetName = null;
  if (c.device !== undefined && c.device !== null && c.device !== "") {
    presetName = String(c.device);
    base = DEVICE_PRESETS[presetName];
    if (!base) throw emuErr(`unknown_preset:${presetName}`);
  }

  // --- viewport / device metrics ------------------------------------------- //
  const width = c.width !== undefined && c.width !== null && c.width !== ""
    ? requireInt(c.width, "width", EMULATION_LIMITS.minDimension,
                 EMULATION_LIMITS.maxDimension)
    : (base ? base.width : null);
  const height = c.height !== undefined && c.height !== null && c.height !== ""
    ? requireInt(c.height, "height", EMULATION_LIMITS.minDimension,
                 EMULATION_LIMITS.maxDimension)
    : (base ? base.height : null);
  const dsf = c.dsf !== undefined && c.dsf !== null && c.dsf !== ""
    ? requireNumber(c.dsf, "dsf", EMULATION_LIMITS.minScaleFactor,
                    EMULATION_LIMITS.maxScaleFactor)
    : (base ? base.deviceScaleFactor : null);
  const mobile = c.mobile !== undefined && c.mobile !== null && c.mobile !== ""
    ? requireBool(c.mobile, "mobile")
    : (base ? base.mobile : null);

  // A viewport needs BOTH dimensions. One alone is not a partial override you can
  // resolve — Emulation.setDeviceMetricsOverride takes both or neither, and
  // guessing the other from the real window would make the result depend on the
  // operator's window size, i.e. not reproducible.
  if ((width === null) !== (height === null)) {
    throw emuErr("invalid_emulation:width_and_height_together");
  }

  let orientation = null;
  if (c.orientation !== undefined && c.orientation !== null
      && c.orientation !== "") {
    const o = EMULATION_ORIENTATIONS[String(c.orientation)];
    if (!o) throw emuErr("invalid_emulation:orientation");
    orientation = o;
  }

  let metrics = null;
  if (width !== null && height !== null) {
    let w = width;
    let h = height;
    // `landscape` swaps the viewport as well as the reported orientation angle —
    // setting the angle alone leaves a portrait-shaped "landscape" device, which
    // is the least useful possible answer.
    if (orientation && orientation.angle === 90 && h > w) { w = height; h = width; }
    metrics = Object.freeze({
      width: w,
      height: h,
      deviceScaleFactor: dsf === null ? 1 : dsf,
      mobile: mobile === null ? false : mobile,
      screenOrientation: orientation
        || (w > h ? EMULATION_ORIENTATIONS.landscape
                  : EMULATION_ORIENTATIONS.portrait),
    });
  }

  // --- touch ---------------------------------------------------------------- //
  // `--touch` / `--no-touch` explicitly, else the preset's implied touch support.
  // maxTouchPoints without touch is a contradiction, refused rather than inferred.
  let touchEnabled = null;
  if (c.touch !== undefined && c.touch !== null && c.touch !== "") {
    touchEnabled = requireBool(c.touch, "touch");
  } else if (base) {
    touchEnabled = base.maxTouchPoints > 0;
  }
  let maxTouchPoints = base ? base.maxTouchPoints : 1;
  if (c.maxTouchPoints !== undefined && c.maxTouchPoints !== null
      && c.maxTouchPoints !== "") {
    if (touchEnabled === false) {
      throw emuErr("invalid_emulation:max_touch_points_without_touch");
    }
    maxTouchPoints = requireInt(c.maxTouchPoints, "maxTouchPoints", 1,
                                EMULATION_LIMITS.maxTouchPoints);
    if (touchEnabled === null) touchEnabled = true;
  }
  const touch = touchEnabled === null ? null : Object.freeze({
    enabled: touchEnabled,
    maxTouchPoints: touchEnabled ? maxTouchPoints : 1,
  });

  // --- user agent (+ the half everyone forgets) ----------------------------- //
  let ua = null;
  if (c.ua !== undefined && c.ua !== null && c.ua !== "") {
    const userAgent = requireUserAgent(c.ua);
    // A RAW --ua carries no client-hints metadata of its own. Rather than leave
    // the operator's real desktop brands showing through navigator.userAgentData,
    // derive a minimal, internally-consistent metadata block from the mobile flag.
    // Honest limitation, documented in reference/emulation.md: a raw UA gets
    // GENERIC metadata, not a faithful per-device one. Use a preset for that.
    ua = Object.freeze({
      userAgent,
      userAgentMetadata: base ? base.userAgentMetadata
        : rawUaMetadata(userAgent, mobile),
    });
  } else if (base) {
    ua = Object.freeze({
      userAgent: base.userAgent,
      userAgentMetadata: base.userAgentMetadata,
    });
  }

  // --- media / geolocation / timezone --------------------------------------- //
  let media = null;
  if (c.colorScheme !== undefined && c.colorScheme !== null
      && c.colorScheme !== "") {
    const cs = String(c.colorScheme);
    if (!EMULATION_COLOR_SCHEMES.includes(cs)) {
      throw emuErr("invalid_emulation:colorScheme");
    }
    media = Object.freeze({
      media: "",   // "" = don't override the media TYPE, only the features
      features: Object.freeze([
        Object.freeze({ name: "prefers-color-scheme", value: cs }),
      ]),
    });
  }

  let geolocation = null;
  if (c.geo !== undefined && c.geo !== null && c.geo !== "") {
    const g = c.geo;
    if (typeof g !== "object" || Array.isArray(g)) {
      throw emuErr("invalid_emulation:geo");
    }
    geolocation = Object.freeze({
      latitude: requireNumber(g.latitude, "geo.latitude", -90, 90),
      longitude: requireNumber(g.longitude, "geo.longitude", -180, 180),
      accuracy: g.accuracy === undefined || g.accuracy === null || g.accuracy === ""
        ? 10
        : requireNumber(g.accuracy, "geo.accuracy", 0, 100000),
    });
  }

  const timezone = c.tz !== undefined && c.tz !== null && c.tz !== ""
    ? requireTimezone(c.tz) : null;

  if (!metrics && !touch && !ua && !media && !geolocation && !timezone) {
    throw emuErr("emulate_needs_device_or_params");
  }

  return Object.freeze({
    preset: presetName,
    label: base ? base.label : null,
    metrics, touch, ua, media, geolocation, timezone,
  });
}

// The ORDERED CDP step list for a stored emulation state. Order is load-bearing
// and asserted by the tests, not left to a comment:
//
//   1. setDeviceMetricsOverride  — the viewport every later measurement is taken
//      against. It MUST precede anything that reads layout (Page.getLayoutMetrics
//      for a --fullpage clip, an element rect for a click), or the op measures the
//      real window and produces a confidently wrong coordinate.
//   2. setTouchEmulationEnabled  — flips `pointer: coarse` / `hover: none`, which
//      changes LAYOUT on responsive sites, so it lands before the page is asked
//      anything.
//   3. setUserAgentOverride      — a page that sniffs UA at load time must see it
//      before `nav` navigates (which is why `nav` on an emulated tab goes through
//      CDP at all).
//   4-6. setEmulatedMedia / setTimezoneOverride / setGeolocationOverride — the
//      soft ones, order-independent among themselves.
//
// NONE of these are `optional`. A half-applied emulation is worse than a refused
// one: it returns a plausible screenshot of the wrong thing. If a step fails, the
// op fails with a normal error envelope and the caller knows.
//
// ⏱ INTERACTION WITH THE POLL-LOOP BUDGETS (see EXEC_OP_BUDGET_MS above). The
// re-application runs INSIDE withCdpSession's `run`, so:
//   * each step goes through the WRAPPED `send` and is individually bounded by
//     CDP_COMMAND_TIMEOUT_MS, exactly like every other CDP command (asserted by
//     the "a hung emulation step is bounded" test — a hung override reports
//     `cdp_timeout:Emulation.<method>`, it does not park the loop);
//   * the apply plus the op's own work remain bounded together by
//     CDP_OP_BUDGET_MS, so the composed worst case (attach 8s + run 15s +
//     awaited safeDetach 8s, killed at the 18s EXEC bound) is UNCHANGED by this
//     feature — no term in the LOOP_STALL_MS derivation moves;
//   * the step COUNT is bounded by a CONSTANT — EMULATION_MAX_STEPS below — not
//     by anything the caller supplies. A caller cannot lengthen the apply by
//     sending a bigger payload, which is the property that keeps the cost
//     analysable at all.
// What this does NOT establish: how much of the 15s run budget a real apply
// actually consumes on a live tab. These are loopback chrome.debugger calls to a
// local renderer and are expected to be low-millisecond, but that is an
// EXPECTATION, not a measurement — there was no browser in the session that wrote
// this. If a legitimate op is ever seen timing out at 18s only when emulated,
// that is the number to go measure first.
export const EMULATION_MAX_STEPS = 6;

export function emulationCdpSteps(state) {
  if (!state || state.reset) return [];
  const steps = [];
  if (state.metrics) {
    steps.push({ method: "Emulation.setDeviceMetricsOverride",
                 params: state.metrics });
  }
  if (state.touch) {
    steps.push({ method: "Emulation.setTouchEmulationEnabled",
                 params: { enabled: state.touch.enabled,
                           maxTouchPoints: state.touch.maxTouchPoints } });
  }
  if (state.ua) {
    steps.push({ method: "Emulation.setUserAgentOverride",
                 params: { userAgent: state.ua.userAgent,
                           userAgentMetadata: state.ua.userAgentMetadata } });
  }
  if (state.media) {
    steps.push({ method: "Emulation.setEmulatedMedia", params: state.media });
  }
  if (state.timezone) {
    steps.push({ method: "Emulation.setTimezoneOverride",
                 params: { timezoneId: state.timezone } });
  }
  if (state.geolocation) {
    steps.push({ method: "Emulation.setGeolocationOverride",
                 params: state.geolocation });
  }
  return steps;
}

// Apply the steps through `send`, IN ORDER, failing loudly on the first failure.
// Deliberately NOT applyWakeSteps: that one swallows optional steps, and there is
// no such thing as an optional emulation step (see above). Returns the applied
// method names so the op can report exactly what took.
export async function applyEmulationSteps(send, steps) {
  const applied = [];
  for (const step of steps || []) {
    await send(step.method, step.params);
    applied.push(step.method);
  }
  return applied;
}

// --- UNDOING an emulation: the ordered CDP CLEAR list ----------------------- //
//
// 🔴 THIS EXISTS BECAUSE THE "OVERRIDES DIE AT DETACH" PREMISE IS ONLY MOSTLY
// TRUE. See the EMULATION header comment above for the measurement. The short
// version: the DEVICE-METRICS VIEWPORT SIZE survives the debugger detach, so
// dropping the `emulationState` entry — which is all `--reset` used to do — left
// the tab permanently sized as the phone, with nothing left that knew how to undo
// it. Issue #319.
//
// Which class needs which clear, and how strong the evidence is (measured
// 2026-08-03, live Brave, extension 0.7.1, laptop, `iphone-15` + `--color-scheme
// dark --tz Europe/London --geo …`, read through `js --wake`, against a control
// tab in the same window that was never emulated):
//
//   * Emulation.clearDeviceMetricsOverride — 🔴 MEASURED NECESSARY.
//     innerWidth×innerHeight stayed 393×852 after `--reset` AND after a further
//     navigation; the control tab read 1124×1400 throughout. (devicePixelRatio,
//     set by the SAME CDP call, DID revert to 1 — consistent with the residue
//     being the resized render widget rather than a live override.)
//   * the other five — MEASURED to clear themselves at detach: after `--reset`,
//     maxTouchPoints was 0, `pointer:coarse` false, `prefers-color-scheme: dark`
//     false, the UA was the real desktop one and the timezone was back to the
//     host's. They are cleared here ANYWAY, deliberately: the measurement is one
//     Chromium build on one host, the clears run inside a session that is already
//     open (so they cost a loopback round-trip each and nothing else), and a clear
//     on a domain that holds no override is a no-op. "Undo what was applied" is a
//     property worth having independent of which Chromium you are on.
//
// What a clear CANNOT undo: properties Chromium installs at DOCUMENT CREATION.
// `"ontouchstart" in window` was still true immediately after `--reset` (the
// document had been BUILT under touch emulation) and only went false after a
// re-navigation. Same asymmetry as the `documentPredatesEmulation` hint, in the
// other direction.
//
// Derived from the state that was in force, not from a fixed list, so `--reset`
// sends exactly the undo of what this bridge applied. A tab with NO stored state
// yields NO steps — reset stays a zero-CDP, zero-debugger-banner no-op there.
export const EMULATION_RESET_MAX_STEPS = 6;

export function emulationResetCdpSteps(state) {
  if (!state || state.reset) return [];
  const steps = [];
  // Metrics first: it is the one measured to actually be stuck, so it lands even
  // if a later clear fails (they are applied best-effort, see below).
  if (state.metrics) {
    steps.push({ method: "Emulation.clearDeviceMetricsOverride", params: {} });
  }
  if (state.touch) {
    // maxTouchPoints is meaningless with enabled:false and CDP rejects 0, so it
    // is omitted rather than sent as a placeholder.
    steps.push({ method: "Emulation.setTouchEmulationEnabled",
                 params: { enabled: false } });
  }
  if (state.ua) {
    // An EMPTY userAgent is how CDP spells "no override" — the same call DevTools
    // makes when you untick its custom-user-agent box. userAgentMetadata is
    // omitted so it reverts with it.
    steps.push({ method: "Emulation.setUserAgentOverride",
                 params: { userAgent: "" } });
  }
  if (state.media) {
    steps.push({ method: "Emulation.setEmulatedMedia",
                 params: { media: "", features: [] } });
  }
  if (state.timezone) {
    steps.push({ method: "Emulation.setTimezoneOverride",
                 params: { timezoneId: "" } });
  }
  if (state.geolocation) {
    steps.push({ method: "Emulation.clearGeolocationOverride", params: {} });
  }
  return steps;
}

// Apply the CLEAR steps through `send`, IN ORDER, BEST-EFFORT — the exact
// opposite policy to applyEmulationSteps, and the difference is deliberate.
//
// An apply must fail loudly: a half-applied emulation returns a plausible
// screenshot of the wrong thing. An UNDO must not: aborting on the first failure
// would leave the remaining overrides in place, which is the very state this
// function exists to escape. So every step is attempted and the outcome is
// REPORTED — { cleared: [method], failed: [{ method, error }] } — rather than
// thrown. The caller drops the stored state regardless.
export async function applyEmulationResetSteps(send, steps) {
  const cleared = [];
  const failed = [];
  for (const step of steps || []) {
    try {
      await send(step.method, step.params);
      cleared.push(step.method);
    } catch (e) {
      failed.push({ method: step.method, error: String((e && e.message) || e) });
    }
  }
  return { cleared, failed };
}

// The compact, METADATA-ONLY summary of a stored state — what `emulate` returns,
// what `tabs` annotates an emulated tab with, and what goes into telemetry. No
// URL, no page content; the UA string is deliberately EXCLUDED (it is long, and
// the preset name identifies it) but its presence is reported.
export function emulationSummary(state) {
  if (!state || state.reset) return null;
  return {
    preset: state.preset,
    label: state.label,
    width: state.metrics ? state.metrics.width : null,
    height: state.metrics ? state.metrics.height : null,
    deviceScaleFactor: state.metrics ? state.metrics.deviceScaleFactor : null,
    mobile: state.metrics ? state.metrics.mobile : null,
    touch: state.touch ? state.touch.enabled : false,
    maxTouchPoints: state.touch ? state.touch.maxTouchPoints : 0,
    userAgentOverridden: !!state.ua,
    colorScheme: state.media && state.media.features[0]
      ? state.media.features[0].value : null,
    timezone: state.timezone,
    geolocation: !!state.geolocation,
  };
}

// --- the NON-EMULATED READ trap, and the annotation that defuses it --------- //
//
// `text`, `getHtml` and the default `eval` read via chrome.scripting. That path
// NEVER attaches the debugger, so withCdp's re-application choke point never runs
// and the DOM they see is the tab's REAL, un-emulated one.
//
// That is correct-by-design, not a bug: between ops the tab is not emulated
// EXCEPT for the viewport size, which is measured to survive the detach (see the
// EMULATION header). But it is a TRAP, and it is this feature's characteristic
// failure mode on the read path: an agent screenshots a phone layout, then reads
// `text`/`html` and reasons about a DESKTOP DOM, with nothing in the envelope to
// tell it apart. navigator.userAgent, matchMedia and the touch surface come back
// REAL; innerWidth/innerHeight are the one pair that may still read emulated,
// which makes the mixture WORSE than a uniformly-desktop read, not better.
//
// So every read of a tab that HAS emulation state says which one it got. Same
// pattern, and the same reasoning, as HIDDEN_TAB_NOTE: the read self-announces
// rather than leaving a plausible-but-wrong answer to be discovered later.
//
// ⚠ THE WORDING IS LOAD-BEARING (see HIDDEN_TAB_NOTE). Whatever remedy this names
// becomes the reflex an agent learns, so it names `--wake` — the flag that routes
// the read through cdpWake → withCdp and therefore through the emulation apply.
export const NOT_EMULATED_READ_NOTE =
  "This tab has device emulation configured, but THIS read did not go through "
  + "CDP — it read the tab's REAL, un-emulated DOM. Most emulation overrides only "
  + "exist inside a CDP session (they die at detach), so navigator.userAgent, "
  + "matchMedia and the touch surface are the DESKTOP ones here — while "
  + "innerWidth/innerHeight may still be the EMULATED ones, because the device-"
  + "metrics viewport size is measured to survive the detach. A mixture, not a "
  + "clean desktop read. Re-run this read with --wake to read inside an emulated "
  + "session.";

// Annotate a READ result with whether it observed the emulated page.
//   * no emulation state for the tab → returns `data` UNTOUCHED (a plain tab's
//     envelope must not grow fields);
//   * viaCdp → { emulated:true, emulation:<summary> };
//   * otherwise → { emulated:false, notEmulatedRead:true, emulationNote }.
// Pure; `summary` is emulationSummary(state) (metadata only) and `viaCdp` is
// supplied per-CALL-SITE, because whether a read is emulated depends on the PATH
// (--wake / --frame eval go through CDP; the default reads do not), not on the op.
export function annotateEmulatedRead(data, summary, viaCdp) {
  if (!data || typeof data !== "object" || !summary) return data;
  if (viaCdp) {
    data.emulated = true;
    data.emulation = summary;
  } else {
    data.emulated = false;
    data.notEmulatedRead = true;
    data.emulationNote = NOT_EMULATED_READ_NOTE;
  }
  return data;
}

// --- the DOCUMENT-PREDATES-EMULATION trap, and its hint --------------------- //
//
// The read-path trap above has a twin on the APPLY path, and it is worse because
// the read annotation cannot see it: emulation applied to an ALREADY-LOADED page
// is only PARTIALLY effective, and the half that does not take is invisible.
//
// MEASURED on real Brave (laptop, extension 0.5.0, https://example.com in an owned
// tab) — this is the only live evidence this code rests on, do not extend it:
//   * `emulate iphone-15` on the already-loaded page →
//       "ontouchstart" in window  === false
//       typeof TouchEvent         === "undefined"
//     while navigator.maxTouchPoints === 5, matchMedia("(pointer:coarse)") === true,
//     matchMedia("(hover:none)") === true, navigator.userAgentData === {mobile:true,
//     platform:"iOS"} and innerWidth === 393 ALL applied correctly.
//   * `nav` to the SAME url UNDER emulation, then re-check →
//       "ontouchstart" in window === true, typeof TouchEvent === "function",
//       maxTouchPoints === 5, innerWidth === 393.
//
// Cause: those two properties are installed on the global at DOCUMENT CREATION, so
// a live override cannot retroactively add them; metrics/media/UA-CH are queried
// live and so apply immediately. ⚠ EXACTLY TWO properties were tested. Treat the
// create-time set as "at least these", never as exhaustive — the note below says
// so, and any future edit must keep saying so unless someone measures more.
//
// The consequence is a confident wrong answer: `open <url>` → `emulate` → read
// concludes the site has no touch support. PR #251 fixed the docs (emulate THEN
// nav); this is the same lesson as the F1 read annotation — the ENVELOPE is what
// actually protects a caller, because it reaches a model that read no docs at all.
//
// ⚠ THE WORDING IS LOAD-BEARING (see NOT_EMULATED_READ_NOTE). It names the two
// measured properties and the remedy (`nav`, re-navigating the same URL is enough),
// because "something may be wrong" teaches no reflex.
export const DOCUMENT_PREDATES_EMULATION_NOTE =
  "This tab already had a document loaded BEFORE these overrides were applied. "
  + "Properties Chromium installs at DOCUMENT CREATION cannot be added "
  + "retroactively: measured on Brave, `\"ontouchstart\" in window` stays false and "
  + "`typeof TouchEvent` stays \"undefined\" here, even though maxTouchPoints, "
  + "pointer:coarse / hover:none, navigator.userAgentData and innerWidth all did "
  + "apply. Those two are the only properties measured — assume there are others. "
  + "REMEDY: `browser nav <url>` now that emulation is on (re-navigating the SAME "
  + "url is enough), then read again; the new document is created with touch "
  + "installed.";

// URLs that are NOT a committed document worth warning about: there is no page
// whose create-time properties could be wrong, so a warning here would be pure
// noise. `about:blank#foo` / `about:blank?x` are the same empty document with a
// fragment/query.
//
// ⚠ DO NOT read this as "open at about:blank, then emulate" being the happy path —
// that sequence CANNOT run. chrome.debugger attaches only to http/https
// (CDP_ATTACHABLE_SCHEMES below), so `emulate` on an about:blank tab is refused
// with `cdp_attach_refused:about:` before any of this is reached, and an emulated
// `nav` cannot rescue it either (it attaches on the tab's CURRENT url, which is
// still about:blank). The workflow that works is `open <url>` → `emulate` (the
// hint FIRES) → re-`nav` under emulation. This branch is defensive: it keeps the
// predicate honest for any future caller that reaches it with a blank tab.
const NO_DOCUMENT_URLS = new Set([
  "", "about:blank", "about:newtab", "chrome://newtab/", "brave://newtab/",
]);

// Does this tab URL represent a real, committed document? ONE rule, ONE place —
// `emulate` is the only caller today, and a second copy at a future call site is
// how this grows a divergent second answer.
export function hasCommittedDocument(url) {
  if (typeof url !== "string") return false;
  const u = url.trim();
  if (NO_DOCUMENT_URLS.has(u)) return false;
  if (u === "about:blank" || u.startsWith("about:blank#")
      || u.startsWith("about:blank?")) return false;
  return true;
}

// The CREATE-TIME signature of a stored emulation state: the part of the state
// that a document can only pick up by being CREATED under it. Two states with the
// same signature produce a document with the same create-time properties, so
// re-emulating an identical device after a `nav` is silent rather than crying wolf.
//
// It is deliberately WIDER than the two measured properties (touch), and includes
// `mobile` and whether a UA override is in force. The measured set is explicitly
// NOT exhaustive, and a spurious hint costs a re-`nav` while a missed one costs a
// wrong conclusion about the site — so NARROWING this is the dangerous direction
// (it produces false SILENCE). Every component is pinned by a test.
//
// ⚠ Be honest about what each component is:
//   * touch/maxTouchPoints — the MEASURED create-time property (ontouchstart,
//     TouchEvent). This is the soundness claim.
//   * mobile — unmeasured, included conservatively.
//   * ua — CONSERVATIVE PADDING, not a soundness claim: `navigator.userAgentData`
//     was measured to apply LIVE on an already-loaded page, so the UA is not
//     create-time as far as anything measured here goes. It is one BIT on purpose
//     (two different UA strings share a signature); do not read the boolean as a
//     claim that UA content is create-time-relevant.
// `null`/reset → "none" (no emulation at create).
export function emulationCreateTimeSignature(state) {
  if (!state || state.reset) return "none";
  const touch = state.touch && state.touch.enabled
    ? String(state.touch.maxTouchPoints) : "off";
  const mobile = state.metrics ? String(!!state.metrics.mobile) : "null";
  return `touch:${touch}|mobile:${mobile}|ua:${state.ua ? 1 : 0}`;
}

// Should `emulate` warn? Pure: the tab's CURRENT url, the signature of the state
// being applied, and the signature of the state the tab's CURRENT document was
// created under — `undefined` when we have no record, which compares unequal to
// every real signature and so WARNS. That is the intended conservative default:
// silence is the unsafe direction here.
export function documentPredatesEmulation(url, stateSignature, documentSignature) {
  if (!hasCommittedDocument(url)) return false;
  return documentSignature !== stateSignature;
}

// Annotate an `emulate` SUCCESS envelope with the hint. Same idiom as
// annotateEmulatedRead — a boolean flag plus `emulationNote` — so a caller sees
// ONE annotation shape across the feature, not two. Absent entirely when the hint
// does not fire (an envelope must not grow a field to say "nothing to report").
export function annotateDocumentPredates(data, fires) {
  if (!data || typeof data !== "object" || !fires) return data;
  data.documentPredatesEmulation = true;
  data.emulationNote = DOCUMENT_PREDATES_EMULATION_NOTE;
  return data;
}

// Is this state's touch emulation ON? The predicate `click` branches on to decide
// between Input.dispatchTouchEvent and Input.dispatchMouseEvent. ONE rule, ONE
// place — a second copy of `state && state.touch && state.touch.enabled` at the
// call site is how this grows a divergent third behaviour.
export function isTouchEmulated(state) {
  return !!(state && !state.reset && state.touch && state.touch.enabled);
}

// The ordered Input.dispatchTouchEvent pair for a tap at (x, y) — what DevTools
// itself dispatches when touch emulation is on, and what a page's touch handlers
// (and every touch-first UI library) actually listen for. A mouse event on a
// touch-emulated tab is NOT equivalent: Chromium does synthesize compatibility
// mouse events from touch, but not the reverse, so a `touchstart`-only handler
// never fires. Pure data — unit-assertable, no caller-influenced method name.
export function touchTapEvents(x, y) {
  const point = { x, y, radiusX: 1, radiusY: 1, force: 1, id: 1 };
  return [
    { method: "Input.dispatchTouchEvent",
      params: { type: "touchStart", touchPoints: [point] } },
    { method: "Input.dispatchTouchEvent",
      params: { type: "touchEnd", touchPoints: [] } },
  ];
}

// --- hidden-tab self-announcing reads (prevent the "false outage") ---------- //
// A tab opened via `open` is BACKGROUND, so document.visibilityState==="hidden"
// and Chromium THROTTLES it — a heavy SPA never renders, and text/html/eval/frames
// return an empty shell that is indistinguishable from a broken site. So every
// read op reports the tab's visibilityState in its result, and when the tab is
// hidden it self-announces (data.hidden=true + a note) so an operator/agent is not
// fooled into declaring a false outage. Pure — the SW supplies the tab's
// document.visibilityState (which reflects the tab; an OOPIF's document follows
// the tab, so a --frame read reports it the same way).
//
// ⚠ THE WORDING IS LOAD-BEARING. This note is emitted on EVERY read of a hidden
// tab, so whatever remedy it names becomes the reflex an agent learns. It used to
// say "run 'browser activate'" — and telemetry then caught a session activating
// 1–5×/minute, yanking the operator's screen away on nearly every interaction.
// It now names the NON-INTRUSIVE remedy and states plainly that `activate` takes
// the screen. Do not reintroduce `activate` as the headline advice here.
export const HIDDEN_TAB_NOTE =
  "tab is hidden — background tabs are throttled, so SPA content may not have " +
  "rendered. Non-intrusive fix: run 'browser wake' (un-throttles the tab via CDP, " +
  "does NOT move focus), or re-run this read with --wake. Only use " +
  "'browser activate' if something genuinely needs the real foreground — it " +
  "STEALS the operator's screen.";

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
// `<prefix>:<label>` — by default `cdp_timeout:<label>` (the phase — attach / a
// CDP method / op / detach) so the caller sees WHICH call hung; the underlying
// promise is left pending (abandoned) but the returned promise SETTLES, so the
// awaiter is never blocked past `ms`. A
// promise that settles on its own (resolve OR reject) wins the race and its
// value/error passes through unchanged, and the timer is cleared so nothing lingers.
// `ms <= 0` disables the bound (returns the promise as-is). Timers injectable.
//
// `prefix` exists because this helper is no longer CDP-only: the poll loop uses
// it to bound the NON-CDP chrome.* ops (`frames` → webNavigation.getAllFrames,
// `screenshot` fast path → tabs.captureVisibleTab, targetTab → tabs.get/query)
// and its own fetches, and mislabelling those `cdp_timeout:` would send the next
// diagnosis straight back at the debugger — the one path that was already bounded.
export function promiseWithTimeout(promise, ms, label, timers = {},
                                   prefix = "cdp_timeout") {
  const p = Promise.resolve(promise);
  if (!(ms > 0)) return p;
  const setT = timers.setTimeout || setTimeout;
  const clearT = timers.clearTimeout || clearTimeout;
  let handle;
  const timeout = new Promise((_, reject) => {
    handle = setT(() => reject(new Error(`${prefix}:${label}`)), ms);
  });
  return Promise.race([p, timeout]).finally(() => clearT(handle));
}

// --- poll-loop wall-clock budgets (the no-wedge guarantee, generalized) ------ //
// The CDP path has been bounded since #—: withCdpSession races every
// chrome.debugger call. Everything ELSE the worker awaits was unbounded, and an
// unbounded await inside execute() parks the `while (true)` poll loop FOREVER —
// `running` (the loop's non-reentrant guard) is only cleared by the finally the
// parked loop can never reach, so the 1-minute keepalive alarm calls loop(), hits
// `if (running) return`, and does nothing. Only a fresh worker evaluation (the
// operator clicking ↻ in brave://extensions) recovered it.
//
// These budgets bound every await in the loop body (op, poll, result, and the
// chrome.storage.local reads via STORAGE_BUDGET_MS below).
//
// ⚠ The ordering is NOT the tidy `CDP 15s < exec 18s < server 20s` it looks like.
// `withCdpSession` composes its phases rather than sharing one wall clock:
// attach ≤ CDP_ATTACH_TIMEOUT_MS (8s) + run ≤ CDP_OP_BUDGET_MS (15s) + an AWAITED
// safeDetach ≤ CDP_COMMAND_TIMEOUT_MS (8s) = up to 31s, plus a frame resolve.
// Measured on a 10×-scaled probe: a hung `run` with a slow detach settles at the
// EXEC bound and reports `op_timeout:<op>`, NOT `cdp_timeout:op`.
//
// That mislabel is accepted deliberately, because the alternative is worse:
//   * The caller-visible ceiling is the SERVER's cmd_timeout (20s), not this one.
//     Raising EXEC_OP_BUDGET_MS above 31s to "let CDP finish labelling" would put
//     it past 20s, so the envelope could never reach the caller anyway — the
//     server would answer `cmd_timeout` first and the loop would stay parked ~11s
//     longer for nothing.
//   * Pre-change, that same case produced a bare server-side `cmd_timeout` with NO
//     phase at all. `op_timeout:frames` at 18s is strictly more information,
//     sooner, and it is the case where the loop is provably released.
//   * The attach-hang and per-CDP-command-hang cases still surface their precise
//     `cdp_timeout:attach` / `cdp_timeout:<method>`. ⚠ The margin on the attach
//     case is THIN, not comfortable: a hung attach is 8s attach + an AWAITED
//     safeDetach of up to 8s = **16s** against this 18s bound — 2s of headroom
//     (measured at 10× scale: settles at 16s reporting `cdp_timeout:attach`).
//     A per-command hang is roomier (8s + detach, reported as
//     `cdp_timeout:<method>`). Anyone lowering EXEC_OP_BUDGET_MS below 16s, or
//     raising CDP_ATTACH_TIMEOUT_MS / CDP_COMMAND_TIMEOUT_MS, converts the
//     attach case into a generic `op_timeout` — re-measure before touching either.
// Known cost: a slow-but-SUCCESSFUL CDP op in the 18–20s band is now killed at 18s
// where it previously had until the server's 20s. That band was already mostly
// lost to the server, so the trade is small and deliberate.
//
// ⚠ This 18s ceiling is HARD and silently caps the server's env-configurable
// BROWSER_BRIDGE_CMD_TIMEOUT (default 20s). Raising that env var to, say, 60s to
// accommodate a slow page buys nothing for any op routed through execute(): the
// extension still gives up at 18s and answers `op_timeout:<op>`. Only a matching
// EXEC_OP_BUDGET_MS bump (which needs a full Brave restart) actually extends the
// ceiling.
//
// Lowering the CDP composition below 18s instead would mean shrinking the attach
// or detach budgets, which are load-bearing for the debugger-leak invariants —
// not worth reopening without a live measurement.
export const EXEC_OP_BUDGET_MS = 18000;
// chrome.storage.local reads/writes on the loop's own path — config() (every
// iteration) and the superseded flag. Same unbounded-chrome.* class as the ops,
// and clearSuperseded() runs on EVERY healthy iteration, i.e. far more often than
// `frames`/`screenshot` ever did. Bounded so "every await in the loop body is
// bounded" is literally true rather than nearly true.
export const STORAGE_BUDGET_MS = 5000;
// A /poll blocks server-side for BROWSER_BRIDGE_POLL_TIMEOUT (default 25s) before
// its 204. 40s leaves generous headroom for a slow loopback round-trip plus the
// activeTabSnapshot() that precedes the fetch, while still being a bound.
export const POLL_BUDGET_MS = 40000;
// POST /result is a loopback write the server answers immediately.
export const RESULT_BUDGET_MS = 10000;
// If the loop has not stamped lastLoopTickAt in this long it is WEDGED, not slow.
//
// The worst LEGITIMATE iteration, derived term by term from the loop body as it
// actually stands (an earlier version of this comment said 68s — it counted only
// poll+exec+result and omitted the storage bounds this very block introduces):
//
//   config()                    STORAGE_BUDGET_MS      5.00s
//   pollOnce()                  POLL_BUDGET_MS        40.00s
//   clearSuperseded() get+set    2 × STORAGE_BUDGET_MS 10.00s
//   execute()                   EXEC_OP_BUDGET_MS     18.00s
//   postResult()                RESULT_BUDGET_MS      10.00s
//   backoff after a failure     nextBackoffMs cap +
//                               250ms jitter          30.25s
//                                                    -------
//                                                    113.25s
//
// (The supersede path is not additive with the command path — SUPERSEDE_BACKOFF_MS
// is 30s and `continue`s before execute/result, so it is strictly cheaper.)
//
// 180s is therefore **1.59×** the worst legitimate iteration, not the 2.5× claimed
// before. The conclusion is unchanged — 113.25s is a hard ceiling, every term in
// it is a bound rather than an expectation, and a real iteration is milliseconds —
// so a 180s stall still means a genuine wedge. But the margin is one-and-a-half
// times, not two-and-a-half: ADDING A NEW BOUNDED AWAIT TO THE LOOP BODY EATS INTO
// IT. Re-derive this sum when you do, and raise LOOP_STALL_MS if it approaches.
//
// This is the defence against the NEXT unbounded await, not the primary fix.
export const LOOP_STALL_MS = 180000;

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
//     OOPIF's target, surfacing a flat sessionId (matched by url); Runtime.evaluate is
//     then issued in THAT session's default context. setAutoAttach is NOT recursive, so
//     resolveOopifSession re-arms it on each attached child session to walk DOWN to a
//     NESTED OOPIF — bounded by depth/target caps + a bounded wait (see #211 below).
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

// --- NESTED OOPIFs: the recursive auto-attach cascade (#211) ------------------ //
// Target.setAutoAttach is NOT recursive: sent on a session it auto-attaches only that
// session's DIRECT child targets. So a GRANDCHILD cross-origin iframe (a cross-origin
// frame nested inside another cross-origin frame) never produced an attachedToTarget on
// the TAB's top session → the old single-level code found no session and the op failed
// `frame_not_found` (safe, but the capability was missing; text/html/click/type/key
// --frame reach such a frame because they go via chrome.scripting, not CDP).
//
// The fix: when a target is auto-attached, send Target.setAutoAttach AGAIN on THAT child
// session (CDP flat mode forwards a `sessionId`), so the attach cascade walks DOWN the
// frame tree until the wanted frame's target appears.
//
// The old single-level code drew its safety partly from an IMPLICIT boundary: it only
// ever looked one level down from a tab whose URL `assertCdpAttachable` had already
// validated. A recursive cascade REMOVES that boundary, so the boundary has to become
// explicit — every discovered target is filtered on THREE axes before it is ever
// considered (see `onEvt`), and only then bounded by the caps:
//   * OWN TAB      — chrome.debugger.onEvent is a GLOBAL listener; an event whose
//     `source.tabId` is not this op's tab is DROPPED (own-tab by construction, not by
//     luck of the serial command loop);
//   * TARGET TYPE  — only `iframe` targets. A page can `new Worker(location.href)` to
//     mint a target with a url IDENTICAL to a real frame's; without this filter that
//     both (a) lets any cross-origin frame make itself permanently un-eval-able by
//     forcing `ambiguous_frame`, and (b) could route the operator's JS into a WORKER
//     global after a navigation race. `OOPIF_AUTO_ATTACH_FILTER` additionally asks
//     Chrome not to attach non-iframe targets at all (belt); this check is the braces.
//   * ATTACHABLE SCHEME — `isCdpAttachableUrl` (http/https only), the SAME gate
//     `assertCdpAttachable` applies to the top tab. Without it a hostile page embeds
//     `<iframe src="chrome-extension://<id>/…">` (any extension with
//     web_accessible_resources) and a prompt-injected agent could run the operator's JS
//     inside ANOTHER EXTENSION'S ORIGIN — the top-tab guard bypassed by being one level
//     down.
//
// A hostile page can nest/spawn frames without limit, so the descent is HARD-BOUNDED:
//   * OOPIF_MAX_DEPTH   — how many levels below the tab's top session we descend;
//   * OOPIF_MAX_TARGETS — total distinct sessions we will ever track for one op;
//   * OOPIF_SETTLE_MS   — the QUIET window: attachedToTarget events arrive
//     ASYNCHRONOUSLY (a setAutoAttach reply does NOT mean its events have landed), so we
//     keep waiting while events are still arriving and give up once nothing new has
//     arrived for this long and no session is left to descend into;
//   * OOPIF_WAIT_MS     — the HARD ceiling on the WHOLE resolution. It is checked FIRST
//     each iteration, ABOVE the descend branch — an earlier revision checked it only
//     when nothing was left to descend into, so a page that kept the descend queue
//     non-empty never reached it and the real wall became CDP_OP_BUDGET_MS (surfacing as
//     `cdp_timeout:op`, not the clean op error the docs promised). Now the ceiling is a
//     true wall, kept well under CDP_OP_BUDGET_MS.
// Exceeding a cap is a LOUD error (oopif_depth_cap / oopif_target_cap), never a silent
// truncation; a match that is already in hand still wins over a cap that was just hit.
export const OOPIF_MAX_DEPTH = 5;
export const OOPIF_MAX_TARGETS = 50;
// The QUIET window. Raised from 300ms after the first live run showed the cascade never
// reaching level 2: a second round trip plus renderer work can plausibly exceed 300ms,
// and the window now RESTARTS on each newly-issued setAutoAttach (bounded by the hard
// deadline) so a legitimately slow level is never cut off mid-descend.
export const OOPIF_SETTLE_MS = 600;
export const OOPIF_WAIT_MS = 5000;
export const OOPIF_POLL_MS = 25;
// How many observed attachedToTarget events the failure DIAGNOSTIC keeps. Capped so a
// frame-spamming page cannot blow up the error string.
export const OOPIF_TRACE_MAX = 20;
// Only cross-origin IFRAME targets are of interest — never workers/service workers/
// pages. Chrome's Target.setAutoAttach `filter` is an EXPERIMENTAL parameter, so it may
// be rejected on the pinned 1.3 channel; the resolver sends it and TRANSPARENTLY RETRIES
// without it on rejection (fail-soft), because the authoritative control is the
// listener-side type check, which needs no protocol support at all.
export const OOPIF_TARGET_TYPES = Object.freeze(["iframe"]);
export const OOPIF_AUTO_ATTACH_FILTER = Object.freeze([Object.freeze({ type: "iframe" })]);
// The single auto-attach parameter set — flat mode (sessionId-addressed sub-sessions),
// never waitForDebuggerOnStart (that would PAUSE the page's frames).
export const OOPIF_AUTO_ATTACH_PARAMS = Object.freeze({
  autoAttach: true, flatten: true, waitForDebuggerOnStart: false,
  filter: OOPIF_AUTO_ATTACH_FILTER,
});
// The same params WITHOUT the experimental `filter` — the fail-soft retry.
export const OOPIF_AUTO_ATTACH_PARAMS_NOFILTER = Object.freeze({
  autoAttach: true, flatten: true, waitForDebuggerOnStart: false,
});

// Every attached session whose target url matches `targetUrl`, deduped by sessionId.
// Exact url equality is the STRONG tier; only when nothing matches exactly do we fall
// back to the trailing-slash-tolerant tier (an OOPIF target url can carry a trailing
// slash the frame url lacks, or vice-versa). Matching stays WITHIN one tier so a
// tolerant match can never be confused with an exact one. Pure.
export function matchOopifSessions(attached, targetUrl) {
  const want = String(targetUrl == null ? "" : targetUrl);
  if (!want) return [];
  const seen = new Set();
  const pick = (pred) => {
    const out = [];
    for (const a of attached || []) {
      if (!a || !a.sessionId || seen.has(a.sessionId)) continue;
      if (!pred(a)) continue;
      seen.add(a.sessionId);
      out.push(a);
    }
    return out;
  };
  const exact = pick((a) => a.url === want);
  if (exact.length) return exact;
  seen.clear();
  const norm = (u) => String(u || "").replace(/\/+$/, "");
  const w = norm(want);
  return pick((a) => norm(a.url) === w);
}

// Pick the flat sessionId of the auto-attached OOPIF target whose url matches
// `targetUrl`. `attached` is the list of {sessionId,url[,depth]} the resolver collected
// from Target.attachedToTarget events. Returns null when none matches (→
// frame_not_found).
// AMBIGUITY FAILS LOUD: with nesting, two DIFFERENT frames can carry the SAME url (a
// pixel/ad embed included twice, a nested copy of the same widget). Rather than silently
// picking the first — which would run the caller's JS in a frame they did not mean, on a
// live-cookie surface — this throws `ambiguous_frame:<n> [<sessionId>:<url>, …]`,
// mirroring resolveWebNavFrame's convention, so the caller re-issues against a
// distinguishable frame. Pure.
export function pickOopifSessionId(attached, targetUrl) {
  const matches = matchOopifSessions(attached, targetUrl);
  if (!matches.length) return null;
  if (matches.length > 1) {
    const listed = matches.map((a) => `${a.sessionId}:${a.url || ""}`).join(", ");
    throw new Error(`ambiguous_frame:${matches.length} [${listed}]`);
  }
  return matches[0].sessionId;
}

// Resolve the flat CDP sessionId of the (possibly DEEPLY NESTED) cross-origin OOPIF
// whose url is `targetUrl`, by driving the bounded recursive auto-attach cascade above.
// THE single shared resolver — `eval --frame` and `upload --frame` both call it, so the
// depth/cap/ambiguity/timeout semantics can never diverge between the two.
//
// `deps`: {
//   send(method, params, sessionId) — the ALREADY timeout-wrapped send from
//       withCdpSession (so each auto-attach is individually bounded too),
//   targetUrl,
//   tabId — THIS op's tab. chrome.debugger.onEvent is a GLOBAL listener, so every event
//       carrying a `source.tabId` is checked against it. When Chrome does NOT populate
//       `tabId` (observed/suspected for SUB-session events), ownership falls back to
//       SESSION PARENTAGE — the event's `source.sessionId` must be a session THIS
//       cascade attached. An event proving neither is dropped (fails closed).
//   label? — what to name the frame in a frame_not_found error (defaults to targetUrl).
//   addListener(fn) / removeListener(fn) — chrome.debugger.onEvent (fn is called as
//       (source, method, params); in flat mode `source.sessionId` names the PARENT
//       session an event came from, which is how depth is attributed),
//   limits?: {maxDepth,maxTargets,settleMs,waitMs,pollMs} — test/ops override,
//   timers?: {setTimeout, now} — injectable clock so the bounds are unit-testable.
// }
// Returns the sessionId. NEVER returns null — a frame that does not appear within the
// bounds THROWS `frame_not_found:<label>`, as do `ambiguous_frame:…` /
// `oopif_depth_cap:<n>` / `oopif_target_cap:<n>`. Every failure carries a bounded
// `cascade[…]` DIAGNOSTIC of what was actually observed (see formatCascadeTrace) — the
// readout that turns the next live iteration from guesswork into evidence.
// The listener is ALWAYS removed in a `finally`.
//
// ⚠ KNOWN DEGRADATION (unverified Chrome behaviour): depth attribution relies on flat
// mode tagging a sub-session's event `source` with the PARENT `sessionId`. If Chrome
// does NOT tag it, every target is attributed depth 1, `depthCapHit` is never set and
// OOPIF_MAX_DEPTH stops binding entirely — the descent is then bounded ONLY by
// OOPIF_MAX_TARGETS and OOPIF_WAIT_MS (still bounded, but not at the advertised depth).
// It canNOT mis-route JS: SELECTION is by URL equality alone and never consults `depth`.
// The `oopif-rig` DEEP variant settles this empirically — see its README.
export async function resolveOopifSession(deps) {
  const lim = deps.limits || {};
  const maxDepth = lim.maxDepth == null ? OOPIF_MAX_DEPTH : lim.maxDepth;
  const maxTargets = lim.maxTargets == null ? OOPIF_MAX_TARGETS : lim.maxTargets;
  const settleMs = lim.settleMs == null ? OOPIF_SETTLE_MS : lim.settleMs;
  const waitMs = lim.waitMs == null ? OOPIF_WAIT_MS : lim.waitMs;
  const pollMs = lim.pollMs == null ? OOPIF_POLL_MS : lim.pollMs;
  const timers = deps.timers || {};
  const now = timers.now || Date.now;
  const setT = timers.setTimeout || setTimeout;
  const sleep = (ms) => new Promise((r) => setT(r, ms));

  const attached = [];              // [{sessionId,url,depth,parentSessionId}]
  const depthOf = new Map();        // sessionId → depth (top session's children = 1)
  const pending = [];               // sessions we still owe a descend
  let capError = null;              // target cap → fail loud
  let depthCapHit = false;          // a branch was cut at maxDepth
  let lastEventAt = now();
  // --- diagnostics (caller-facing error only — NEVER telemetry) ---------------- //
  const trace = [];                 // capped record of what the cascade OBSERVED
  const attachSent = [];            // which sessions we issued setAutoAttach on
  let eventsSeen = 0;
  let filterMode = "on";
  let exit = "?";

  // Does this event belong to OUR cascade? Two independent proofs, in order:
  //   1. `source.tabId` present → it is authoritative, must equal this op's tab.
  //   2. `source.tabId` ABSENT → fall back to SESSION PARENTAGE: we know every session
  //      we attached, so an event whose `source.sessionId` is one of ours is ours. This
  //      is what makes the cascade work when Chrome does not populate `tabId` on
  //      sub-session events — the first live run showed the cascade going INERT at
  //      level 2 (frame_not_found, never a depth cap → no level-2 session recorded at
  //      all), and an over-strict tabId check is the prime suspect.
  //   3. Neither → REJECT (unprovable ownership fails closed).
  const ownership = (source) => {
    if (!source) return "drop:no-source";
    if (source.tabId != null) {
      return source.tabId === deps.tabId ? "" : "drop:foreign-tab";
    }
    if (source.sessionId && depthOf.has(source.sessionId)) return "";
    return "drop:unowned";
  };

  const onEvt = (source, method, params) => {
    if (method !== "Target.attachedToTarget" || !params || !params.targetInfo) return;
    const ti = params.targetInfo;
    const url = ti.url || "";
    eventsSeen += 1;
    const parent = (source && source.sessionId) || null;
    let decision;
    // (a) OWN TAB / own cascade.
    decision = ownership(source);
    // (b) TARGET TYPE — iframes only; never a worker/service_worker/page target.
    if (!decision && !OOPIF_TARGET_TYPES.includes(ti.type)) decision = "drop:type";
    // (c) ATTACHABLE SCHEME — the same http/https gate the TOP tab passed. A
    // chrome-extension:/file:/devtools: child must never become an eval target.
    if (!decision && !isCdpAttachableUrl(url)) decision = "drop:scheme";
    const sessionId = params.sessionId;
    if (!decision && (!sessionId || depthOf.has(sessionId))) decision = "drop:dup";
    // Flat mode tags an event from a sub-session with its sessionId; absent → the
    // tab's TOP session, i.e. a direct child (depth 1). See the DEGRADATION note above.
    const depth = parent && depthOf.has(parent) ? depthOf.get(parent) + 1 : 1;
    if (trace.length < OOPIF_TRACE_MAX) {
      trace.push({
        type: ti.type == null ? "?" : String(ti.type),
        url,
        tab: source && source.tabId != null
          ? (source.tabId === deps.tabId ? "match" : "foreign") : "absent",
        parent: parent ? (depthOf.has(parent) ? "known" : "unknown") : "absent",
        depth: decision ? null : depth,
        decision: decision || "accept",
      });
    }
    if (decision) return;
    lastEventAt = now();              // only a target WE accepted proves liveness
    depthOf.set(sessionId, depth);
    attached.push({ sessionId, url, depth, parentSessionId: parent });
    if (depthOf.size > maxTargets) { capError = `oopif_target_cap:${maxTargets}`; return; }
    if (depth < maxDepth) pending.push(sessionId);
    else depthCapHit = true;
  };

  // The compact, BOUNDED readout appended to every failure — the difference between
  // "it didn't work" and knowing WHY on the next live run. Caller-facing only.
  const diag = () => formatCascadeTrace({
    exit, eventsSeen, trace, attachSent, filterMode,
    accepted: attached.length, maxDepth, maxTargets,
  });

  deps.addListener(onEvt);
  try {
    const deadline = now() + waitMs;
    // `filter` is EXPERIMENTAL: send it, and on rejection fall back (for the rest of
    // this op) to the plain params. The listener-side type check covers us either way.
    let params = OOPIF_AUTO_ATTACH_PARAMS;
    const autoAttach = async (sessionId) => {
      attachSent.push(sessionId == null ? "top" : sessionId);
      let out;
      try {
        out = await deps.send("Target.setAutoAttach", params, sessionId);
      } catch (e) {
        if (params === OOPIF_AUTO_ATTACH_PARAMS_NOFILTER) throw e;
        params = OOPIF_AUTO_ATTACH_PARAMS_NOFILTER;
        filterMode = "rejected→off";
        out = await deps.send("Target.setAutoAttach", params, sessionId);
      }
      // RESTART the quiet window: we just asked for a new level, so "no events yet"
      // means "waiting", not "gone quiet". Still bounded by the hard deadline.
      lastEventAt = now();
      return out;
    };
    // Level 0: the tab's TOP session — auto-attaches its DIRECT child targets.
    await autoAttach(undefined);
    for (;;) {
      // A match already in hand ALWAYS wins — over a cap, over a further descend.
      const hit = pickOopifSessionId(attached, deps.targetUrl);   // throws on ambiguity
      if (hit) { exit = "match"; return hit; }
      if (capError) { exit = "target-cap"; throw new Error(`${capError} ${diag()}`); }
      // THE HARD CEILING, checked FIRST — above the descend branch, so a page that
      // keeps the queue non-empty can NEVER outrun it (the audit's Fix 5).
      const t = now();
      if (t >= deadline) { exit = "deadline"; break; }
      if (pending.length) {
        // Descend: re-arm auto-attach ON the child session (flat mode forwards it).
        await autoAttach(pending.shift());
        continue;
      }
      // Nothing left to descend into: give up once the cascade has gone QUIET.
      if (t - lastEventAt >= settleMs) { exit = "settle"; break; }
      await sleep(pollMs);
    }
    // Not found. If we deliberately stopped descending, say SO rather than pretending
    // the frame does not exist.
    if (depthCapHit) { exit = "depth-cap"; throw new Error(`oopif_depth_cap:${maxDepth} ${diag()}`); }
    throw new Error(`frame_not_found:${deps.label || deps.targetUrl} ${diag()}`);
  } finally {
    deps.removeListener(onEvt);
  }
}

// Render the cascade's observations as ONE compact line for a failure error. Bounded by
// construction (the trace is capped at OOPIF_TRACE_MAX entries upstream). Pure.
// Frame URLs appear here — same as the existing `ambiguous_frame` convention — because
// this is the CALLER's error text. It must never be fed to telemetry, which stays
// metadata-only (op / outcome / bare top-level domain).
export function formatCascadeTrace(s) {
  const head = `cascade[exit=${s.exit} attach=${(s.attachSent || []).join(">") || "-"}` +
    ` events=${s.eventsSeen} accepted=${s.accepted}` +
    ` filter=${s.filterMode} caps=d${s.maxDepth}/t${s.maxTargets}]`;
  const rows = (s.trace || []).map((e, i) =>
    `#${i + 1} ${e.decision} type=${e.type} tab=${e.tab} parent=${e.parent}` +
    `${e.depth == null ? "" : ` d=${e.depth}`} ${e.url}`);
  const more = (s.eventsSeen || 0) - (s.trace || []).length;
  if (more > 0) rows.push(`(+${more} more events not shown)`);
  return rows.length ? `${head} ${rows.join(" | ")}` : `${head} (no events observed)`;
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

// --- annotated text: structured element extraction for --annotated ---------- //
// Testable pure helpers that extract element metadata for the `text --annotated`
// feature. Each is independently unit-testable; `annotatedTextFn` composes them
// into a self-contained function for chrome.scripting injection.

// Generate a CSS selector path from `element` up to `document.documentElement`.
// Short-circuits on elements with an `id` (id is unique in the document).
// Uses :nth-child only when the tag alone isn't unique among siblings.
// Pure — operates on a DOM element.
export function generateCssPath(element) {
  if (!element || !element.tagName) return "";
  const tag = element.tagName.toLowerCase();
  // Short-circuit: id is unique in the document.
  if (element.id) return `#${element.id}`;
  const parts = [];
  let cur = element;
  while (cur && cur.nodeType === 1) {
    const t = cur.tagName.toLowerCase();
    if (cur.id) { parts.unshift(`#${cur.id}`); break; }
    const parent = cur.parentElement;
    if (parent) {
      const siblings = Array.from(parent.children).filter(
        (c) => c.tagName === cur.tagName
      );
      if (siblings.length > 1) {
        const idx = siblings.indexOf(cur) + 1;
        parts.unshift(`${t}:nth-child(${idx})`);
      } else {
        parts.unshift(t);
      }
    } else {
      parts.unshift(t);
    }
    cur = parent;
  }
  return parts.join(" > ");
}

// Extract identifying attributes from an element. Only includes attributes
// that are present and non-empty. Returns a plain object. Pure.
const IDENTIFYING_ATTRS = [
  "id", "class", "href", "src", "alt", "title", "name", "placeholder",
  "type", "role", "aria-label", "data-testid", "data-cy", "data-e2e",
];
export function extractIdentifyingAttrs(element) {
  if (!element || !element.getAttribute) return {};
  const attrs = {};
  for (const name of IDENTIFYING_ATTRS) {
    const val = element.getAttribute(name);
    if (val != null && val !== "") {
      attrs[name] = name === "class" ? val : val;
    }
  }
  return attrs;
}

// Get up to `maxLen` chars of text context from adjacent siblings. Pure.
export function getAdjacentText(element, maxLen = 40) {
  if (!element) return { precedingText: "", followingText: "" };
  let preceding = "";
  let following = "";
  // Previous sibling: walk backwards to find a text node or element with text.
  let prev = element.previousSibling;
  while (prev && !preceding) {
    if (prev.nodeType === 3) { // TEXT_NODE
      preceding = (prev.textContent || "").trim().slice(-maxLen);
    } else if (prev.nodeType === 1) { // ELEMENT_NODE
      preceding = (prev.textContent || "").trim().slice(-maxLen);
    }
    prev = prev.previousSibling;
  }
  // Next sibling.
  let next = element.nextSibling;
  while (next && !following) {
    if (next.nodeType === 3) {
      following = (next.textContent || "").trim().slice(0, maxLen);
    } else if (next.nodeType === 1) {
      following = (next.textContent || "").trim().slice(0, maxLen);
    }
    next = next.nextSibling;
  }
  return { precedingText: preceding, followingText: following };
}

// The self-contained injected function for `text --annotated`. Runs in the page
// via chrome.scripting.executeScript. MUST reference only its own parameters and
// page globals — no closures over module scope. Returns { elements, count }.
// `maxItems` defaults to ANNOTATED_TEXT_MAX_ITEMS_DEFAULT if not provided.
export function annotatedTextFn(selector, maxItems) {
  var cap = (typeof maxItems === "number" && maxItems > 0) ? maxItems : 200;
  var root = selector ? document.querySelector(selector) : document.body;
  if (!root) return { elements: [], count: 0 };

  var IDENTIFYING_ATTRS = [
    "id", "class", "href", "src", "alt", "title", "name", "placeholder",
    "type", "role", "aria-label", "data-testid", "data-cy", "data-e2e",
  ];

  function genPath(el) {
    if (!el || !el.tagName) return "";
    if (el.id) return "#" + el.id;
    var parts = [];
    var cur = el;
    while (cur && cur.nodeType === 1) {
      var t = cur.tagName.toLowerCase();
      if (cur.id) { parts.unshift("#" + cur.id); break; }
      var par = cur.parentElement;
      if (par) {
        var sibs = Array.prototype.filter.call(par.children, function(c) {
          return c.tagName === cur.tagName;
        });
        if (sibs.length > 1) {
          var idx = Array.prototype.indexOf.call(sibs, cur) + 1;
          parts.unshift(t + ":nth-child(" + idx + ")");
        } else {
          parts.unshift(t);
        }
      } else {
        parts.unshift(t);
      }
      cur = par;
    }
    return parts.join(" > ");
  }

  function getAttrs(el) {
    if (!el || !el.getAttribute) return {};
    var attrs = {};
    for (var i = 0; i < IDENTIFYING_ATTRS.length; i++) {
      var n = IDENTIFYING_ATTRS[i];
      var v = el.getAttribute(n);
      if (v != null && v !== "") attrs[n] = v;
    }
    return attrs;
  }

  function ownText(el) {
    var parts = [];
    for (var i = 0; i < el.childNodes.length; i++) {
      var c = el.childNodes[i];
      if (c.nodeType === 3) parts.push(c.textContent || "");
    }
    return parts.join("").trim().slice(0, 200);
  }

  function adjText(el, max) {
    var prev = "", next = "";
    var p = el.previousSibling;
    while (p && !prev) {
      if (p.nodeType === 3) prev = (p.textContent || "").trim().slice(-max);
      else if (p.nodeType === 1) prev = (p.textContent || "").trim().slice(-max);
      p = p.previousSibling;
    }
    var n = el.nextSibling;
    while (n && !next) {
      if (n.nodeType === 3) next = (n.textContent || "").trim().slice(0, max);
      else if (n.nodeType === 1) next = (n.textContent || "").trim().slice(0, max);
      n = n.nextSibling;
    }
    return { precedingText: prev, followingText: next };
  }

  // BFS walk limited to cap elements.
  var elements = [];
  var queue = [root];
  while (queue.length > 0 && elements.length < cap) {
    var el = queue.shift();
    // Skip the root container itself — only collect descendants.
    if (el !== root) {
      var text = ownText(el);
      var adj = adjText(el, 40);
      elements.push({
        text: text,
        path: genPath(el),
        tag: el.tagName.toLowerCase(),
        attrs: getAttrs(el),
        precedingText: adj.precedingText,
        followingText: adj.followingText,
      });
    }
    if (elements.length < cap) {
      for (var j = 0; j < el.children.length; j++) {
        queue.push(el.children[j]);
      }
    }
  }
  return { elements: elements, count: elements.length };
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
// `extId` is `chrome.runtime.id` — WHICH DIRECTORY Brave loaded this extension
// from. An unpacked extension's ID is derived from its absolute directory path,
// so the repo-path load and the ~/.local/share/browser-bridge-ext/ load have
// DIFFERENT ids while reporting the SAME version. It is the only field that can
// answer "did the migration off the git-mutable path actually take?".
// MEASURED (2026-08-01; Brave/Chromium on both NixOS hosts, unpacked
// extensions, two paths): id = sha256(absolute path), first 32 hex chars, each
// nibble 0-f mapped to a-p. PATH ONLY — no per-profile component. So the id is
// computable in advance; see extension/README.md "The path→id derivation".
export function pollHeaders(instanceId, label, active, extVersion, extId) {
  const h = { "X-Bridge-Instance-Id": String(instanceId || "") };
  if (label) h["X-Bridge-Label"] = encodeURIComponent(capHeaderValue(label));
  if (active && active.url) h["X-Bridge-Active-Url"] = encodeURIComponent(capHeaderValue(active.url));
  if (active && active.title) h["X-Bridge-Active-Title"] = encodeURIComponent(capHeaderValue(active.title));
  if (extVersion) h["X-Bridge-Ext-Version"] = encodeURIComponent(capHeaderValue(String(extVersion)));
  if (extId) h["X-Bridge-Ext-Id"] = encodeURIComponent(capHeaderValue(String(extId)));
  return h;
}

// Stamp the instanceId onto a result envelope so /result is instance-scoped.
export function resultWithInstance(envelope, instanceId) {
  return { ...envelope, instanceId: String(instanceId || "") };
}
