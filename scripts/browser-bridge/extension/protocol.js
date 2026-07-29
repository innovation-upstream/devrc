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
export const ALLOWED_OPS = [
  "getHtml", "text", "eval", "tabs", "nav", "screenshot", "open", "close",
];

// Per-op required fields (mirrors server.py REQUIRED_FIELDS). The server already
// validates these, but the SW re-checks so a hand-crafted command can't wedge it.
export const REQUIRED_FIELDS = {
  eval: ["js"],
  nav: ["url"],
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
// Paint settle after 'complete'. Chrome fires "complete" slightly BEFORE the
// first paint, so this settle (≈350ms) lets the FIRST capture usually succeed —
// avoiding a retry (hence any quota risk) in the common case.
export const CAPTURE_SETTLE_MS = 350;
export const CAPTURE_READY_TIMEOUT_MS = 2000; // cap on the status poll
export const CAPTURE_READY_POLL_MS = 100;   // status re-poll cadence

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

// --- fundamental limitation: captureVisibleTab needs an ON-SCREEN tab -------- //
// captureVisibleTab captures the on-screen COMPOSITED pixels of a window's
// visible tab. On the user's i3 tiling WM an owned/agent tab is intentionally
// NOT foregrounded, and activating it does NOT guarantee its Brave WINDOW is
// raised/composited (Chrome can't force i3 to raise a window). So the tab may
// never composite and the GPU read-back keeps returning "image readback failed"
// no matter how many times we retry — this is a PERMANENT condition for that tab,
// NOT the paint race the settle+retry recovers. Once the spaced retries are spent
// (a genuinely transient readback would already have succeeded on a retry), a
// readback error means the tab is simply not visible on-screen.
const OCCLUSION_CAPTURE_PATTERNS = ["image readback failed", "readback"];

// The actionable error surfaced to the caller when a screenshot cannot be
// captured because the target tab is not visible on-screen (background / occluded
// window). Names the alternative ops that DO work on a background tab so the
// caller/agent acts instead of blindly retrying a capture that cannot succeed.
export const SCREENSHOT_NOT_VISIBLE_MESSAGE =
  "screenshot unavailable: the target tab is not visible on-screen (background " +
  "or occluded window). captureVisibleTab can only capture the foreground tab; " +
  "bring the tab to the foreground, or use 'text'/'html'/'eval' which work on " +
  "background tabs.";

// True when `err` looks like the readback/occlusion failure (a GPU read-back with
// no pixels) — but NOT the per-second quota error, which is a distinct, genuinely
// transient throttle that keeps its own message (#182). Matched case-insensitively.
// Intended to be checked only AFTER the retry budget is exhausted: a transient
// readback that a spaced retry could recover has already succeeded by then, so a
// match here means the tab is persistently not compositing → the limitation above.
export function isOcclusionCaptureError(err) {
  const s = errText(err);
  if (!s) return false;
  if (isCaptureQuotaError(err)) return false; // a quota hit is not an occlusion
  return OCCLUSION_CAPTURE_PATTERNS.some((p) => s.includes(p));
}

// Map a POST-RETRY (exhausted-budget) capture failure to the error string the
// caller sees. A readback/occlusion failure that survived every spaced retry is
// the fundamental "tab not visible on-screen" limitation → the actionable
// SCREENSHOT_NOT_VISIBLE_MESSAGE. Everything else (the quota error, a
// permission/URL error, owned_tab_gone, …) passes through UNCHANGED so its own
// specific cause is preserved. MUST be applied ONLY after the retries are
// exhausted — never on an error a retry could still recover (that would wrongly
// claim "unavailable"). Pure + unit-tested; service_worker.js applies it in the
// screenshot op's catch, which by construction runs only post-retry.
export function mapCaptureFailure(err) {
  if (isOcclusionCaptureError(err)) return SCREENSHOT_NOT_VISIBLE_MESSAGE;
  return String((err && err.message != null) ? err.message : err);
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

// Wait until a just-activated tab is painted enough to capture. Polls
// `getStatus()` (→ the tab's `status`, e.g. "complete"/"loading") until it reads
// "complete" or the timeout elapses, THEN waits a short settle — because
// chrome fires "complete" slightly BEFORE the first paint, so a tab that is
// already "complete" can still read back empty without the settle. All timing +
// the status read are injectable so this is unit-testable with a fake clock.
export async function waitForCaptureReady(getStatus, opts = {}) {
  const timeoutMs = opts.timeoutMs == null ? CAPTURE_READY_TIMEOUT_MS : opts.timeoutMs;
  const pollMs = opts.pollMs == null ? CAPTURE_READY_POLL_MS : opts.pollMs;
  const settleMs = opts.settleMs == null ? CAPTURE_SETTLE_MS : opts.settleMs;
  const sleep = opts.sleep || defaultSleep;
  const now = opts.now || (() => Date.now());
  const start = now();
  // eslint-disable-next-line no-constant-condition
  while (true) {
    let status;
    try { status = await getStatus(); } catch (e) { status = null; }
    if (status === "complete") break;
    if (now() - start >= timeoutMs) break; // don't hang past the budget
    await sleep(pollMs);
  }
  if (settleMs > 0) await sleep(settleMs);
}

// Orchestrate a screenshot with the activate→settle→capture→restore dance, with
// every chrome.* side effect INJECTED so the (important) invariant — the
// previously-active tab is restored on BOTH the success AND the failure path,
// and an already-visible tab is captured WITHOUT any activate/flicker — is
// unit-testable. `deps`:
//   isActive          : boolean  — is the target tab already the visible tab
//   targetId          : the target tab's id
//   capture()         : Promise<dataUrl> (the caller wraps this in captureWithRetry)
//   getPrevActiveId() : Promise<id|null> — the tab active BEFORE we activate
//   activate(id)      : Promise — bring the target tab to the foreground
//   waitReady()       : Promise — settle until painted (waitForCaptureReady)
//   restore(id)       : Promise — re-activate the previously-active tab
// service_worker.js builds `deps` from the real chrome.* APIs.
export async function screenshotWithRestore(deps) {
  if (deps.isActive) {
    // Already visible → capture directly, no needless activate/flicker.
    return deps.capture();
  }
  let prevActiveId = null;
  try {
    prevActiveId = await deps.getPrevActiveId();
    await deps.activate(deps.targetId);
    await deps.waitReady();
    return await deps.capture();
  } finally {
    // Restore focus to the previously-active tab on EVERY path (success AND
    // failure) so a background screenshot never leaves the user's foreground
    // changed. Never let a restore failure mask the real result/error.
    if (prevActiveId != null && prevActiveId !== deps.targetId) {
      try { await deps.restore(prevActiveId); } catch (e) { /* ignore */ }
    }
  }
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
// active-tab strings are capped then URL-encoded (HTTP header values must be
// ASCII-safe AND bounded); the server decodes them. Empty values are omitted so
// a bare instance registers cleanly. `active` is an optional { url, title } for
// cheap /health enrichment.
export function pollHeaders(instanceId, label, active) {
  const h = { "X-Bridge-Instance-Id": String(instanceId || "") };
  if (label) h["X-Bridge-Label"] = encodeURIComponent(capHeaderValue(label));
  if (active && active.url) h["X-Bridge-Active-Url"] = encodeURIComponent(capHeaderValue(active.url));
  if (active && active.title) h["X-Bridge-Active-Title"] = encodeURIComponent(capHeaderValue(active.title));
  return h;
}

// Stamp the instanceId onto a result envelope so /result is instance-scoped.
export function resultWithInstance(envelope, instanceId) {
  return { ...envelope, instanceId: String(instanceId || "") };
}
