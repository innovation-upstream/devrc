// protocol.js — the pure, browser-independent half of the browser-bridge
// extension. Everything here is testable with `node --test` (no chrome.* APIs),
// and the op set MIRRORS server.py's ALLOWED_OPS (the shared JSON contract —
// asserted by protocol.test.mjs and documented in ../../README.md).

// The command ops the bridge understands. MUST equal server.py ALLOWED_OPS.
export const ALLOWED_OPS = ["getHtml", "eval", "tabs", "nav", "screenshot"];

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

// Request headers the SW sends on /poll to identify its instance. Label +
// active-tab strings are URL-encoded (HTTP header values must be ASCII-safe);
// the server decodes them. Empty values are omitted so a bare instance registers
// cleanly. `active` is an optional { url, title } for cheap /health enrichment.
export function pollHeaders(instanceId, label, active) {
  const h = { "X-Bridge-Instance-Id": String(instanceId || "") };
  if (label) h["X-Bridge-Label"] = encodeURIComponent(String(label));
  if (active && active.url) h["X-Bridge-Active-Url"] = encodeURIComponent(String(active.url));
  if (active && active.title) h["X-Bridge-Active-Title"] = encodeURIComponent(String(active.title));
  return h;
}

// Stamp the instanceId onto a result envelope so /result is instance-scoped.
export function resultWithInstance(envelope, instanceId) {
  return { ...envelope, instanceId: String(instanceId || "") };
}
