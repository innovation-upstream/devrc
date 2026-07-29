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
  OP_TO_SERVER, ALLOWED_OPS_DEFAULT, TEXT_MAX_BYTES_DEFAULT,
  BrowserToolRefusal, hostOf, hostDenied, allowedOpsFromEnv, forcedTab,
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

test("OP_TO_SERVER maps only the allowlisted read/nav ops (no lifecycle ops)", () => {
  assert.deepEqual(Object.keys(OP_TO_SERVER).sort(),
    ["eval", "html", "nav", "screenshot", "text"]);
  for (const forbidden of ["open", "close", "tabs", "release"]) {
    assert.ok(!(forbidden in OP_TO_SERVER), `${forbidden} must not be mappable`);
  }
});
