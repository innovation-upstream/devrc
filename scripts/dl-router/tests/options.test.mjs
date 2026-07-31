// The options page. It had ZERO coverage, and it shipped a port field that the
// manifest's `host_permissions` pin made unreachable: changing it stored a
// value the extension is not permitted to fetch, every request failed with a
// permissions error, and nothing in the UI said why.
import test from "node:test";
import assert from "node:assert/strict";

globalThis.DL_ROUTER_NO_AUTOSTART = true;

import { makeDoc } from "./fake_page.mjs";

const OPT = await import("../extension/options.js");

const IDS = ["port", "token", "enabled", "status", "save", "test"];

function fakeChrome(stored = {}, manifest = {
  host_permissions: ["http://127.0.0.1:8791/*"],
}) {
  const store = { ...stored };
  return {
    store,
    runtime: { getManifest: () => manifest },
    storage: {
      local: {
        get: async (keys) => {
          const out = {};
          for (const k of [].concat(keys)) out[k] = store[k];
          return out;
        },
        set: async (obj) => Object.assign(store, obj),
      },
    },
  };
}

// --- the port is the manifest's, not the user's ----------------------------- //
test("the port is read from host_permissions", () => {
  assert.equal(OPT.manifestPort(fakeChrome()), 8791);
  assert.equal(OPT.manifestPort(fakeChrome({}, {
    host_permissions: ["http://127.0.0.1:9002/*"],
  })), 9002);
});

test("an unusable manifest falls back to the default port", () => {
  for (const manifest of [{}, { host_permissions: [] },
    { host_permissions: ["https://example-site.test/*"] }, null]) {
    assert.equal(OPT.manifestPort(fakeChrome({}, manifest)),
      OPT.DEFAULT_PORT, JSON.stringify(manifest));
  }
  assert.equal(OPT.manifestPort({}), OPT.DEFAULT_PORT);
});

test("load shows the manifest port even when storage holds a stale one", async () => {
  const doc = makeDoc(IDS);
  const chromeApi = fakeChrome({ port: 9999, token: "tok", enabled: true });
  await OPT.load(doc, chromeApi);
  assert.equal(doc.getElementById("port").value, 8791,
    "a stored port the manifest forbids must never be presented as usable");
  assert.equal(doc.getElementById("token").value, "tok");
  assert.equal(doc.getElementById("enabled").checked, true);
});

test("saving never writes a port back to storage", async () => {
  const doc = makeDoc(IDS);
  const chromeApi = fakeChrome();
  doc.getElementById("token").value = "  tok  ";
  doc.getElementById("enabled").checked = true;
  await OPT.save(doc, chromeApi, async () => ({
    ok: true, status: 200, json: async () => ({ configured: true, dirs: 3, aliases: 1 }),
  }));
  assert.deepEqual(chromeApi.store, { token: "tok", enabled: true });
  assert.ok(!("port" in chromeApi.store));
});

// --- probe ------------------------------------------------------------------ //
test("probe reports a healthy configured sidecar", async () => {
  const doc = makeDoc(IDS);
  doc.getElementById("token").value = "tok";
  const out = await OPT.probe(doc, fakeChrome(), async (url, opts) => {
    assert.equal(url, "http://127.0.0.1:8791/healthz");
    assert.equal(opts.headers.Authorization, "Bearer tok");
    return { ok: true, status: 200,
      json: async () => ({ configured: true, dirs: 25, aliases: 4 }) };
  });
  assert.match(out, /25 directories, 4 aliases/);
});

test("probe distinguishes a rejected token from an unreachable sidecar", async () => {
  const doc = makeDoc(IDS);
  doc.getElementById("token").value = "tok";
  const rejected = await OPT.probe(doc, fakeChrome(),
    async () => ({ ok: false, status: 401, json: async () => ({}) }));
  assert.match(rejected, /token REJECTED/);

  const down = await OPT.probe(doc, fakeChrome(), async () => {
    throw new Error("ECONNREFUSED");
  });
  assert.match(down, /unreachable on port 8791/);
});

test("probe surfaces an unconfigured library root", async () => {
  const doc = makeDoc(IDS);
  doc.getElementById("token").value = "tok";
  const out = await OPT.probe(doc, fakeChrome(),
    async () => ({ ok: true, status: 200,
      json: async () => ({ configured: false }) }));
  assert.match(out, /library_root is not configured/);
});

test("probe surfaces a broken config instead of looking healthy", async () => {
  // The sidecar now degrades to 503 rather than crash-looping, so the options
  // page has to say WHY it is inert.
  const doc = makeDoc(IDS);
  doc.getElementById("token").value = "tok";
  const out = await OPT.probe(doc, fakeChrome(),
    async () => ({ ok: true, status: 200,
      json: async () => ({ configured: false, configError: "cannot read x" }) }));
  assert.match(out, /config is broken: cannot read x/);
});

test("probe says so plainly when no token has been pasted yet", async () => {
  const doc = makeDoc(IDS);
  let called = false;
  const out = await OPT.probe(doc, fakeChrome(), async () => { called = true; });
  assert.equal(called, false);
  assert.match(out, /dl-route token/);
});

test("probe reports any other HTTP status verbatim", async () => {
  const doc = makeDoc(IDS);
  doc.getElementById("token").value = "tok";
  const out = await OPT.probe(doc, fakeChrome(),
    async () => ({ ok: false, status: 503, json: async () => ({}) }));
  assert.match(out, /HTTP 503/);
});

// --- mount ------------------------------------------------------------------ //
test("mount wires both buttons and renders the stored settings", async () => {
  const doc = makeDoc(IDS);
  const chromeApi = fakeChrome({ token: "tok", enabled: true });
  const fetches = [];
  const fetchImpl = async (url) => {
    fetches.push(url);
    return { ok: true, status: 200,
      json: async () => ({ configured: true, dirs: 1, aliases: 0 }) };
  };
  await OPT.mount(doc, chromeApi, fetchImpl);
  assert.equal(doc.getElementById("token").value, "tok");

  doc.getElementById("test").fire("click");
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(fetches.length, 1);

  doc.getElementById("token").value = "newtok";
  doc.getElementById("save").fire("click");
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(chromeApi.store.token, "newtok");
  assert.equal(fetches.length, 2, "saving re-probes");
});
