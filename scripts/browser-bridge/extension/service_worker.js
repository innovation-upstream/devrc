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

import { ALLOWED_OPS, validateCommand, resultEnvelope, errorEnvelope, nextBackoffMs }
  from "./protocol.js";

const DEFAULT_PORT = 8788;
let running = false;

async function config() {
  const { port, token } = await chrome.storage.local.get(["port", "token"]);
  return { port: port || DEFAULT_PORT, token: token || "" };
}

function base(port) {
  return `http://127.0.0.1:${port}`;
}

function authHeaders(token) {
  return { Authorization: `Bearer ${token}`, "Content-Type": "application/json" };
}

// --- active-tab helpers ---------------------------------------------------- //
async function activeTab() {
  const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  if (!tab) throw new Error("no_active_tab");
  return tab;
}

// --- op executors ---------------------------------------------------------- //
// Each returns the op-specific `data` object; throws on failure (→ errorEnvelope).
const OPS = {
  async getHtml() {
    const tab = await activeTab();
    const [inj] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: () => document.documentElement.outerHTML,
    });
    return { url: tab.url, title: tab.title, html: inj.result };
  },

  async eval(cmd) {
    const tab = await activeTab();
    // chrome.scripting runs in an ISOLATED world; `js` is evaluated and its
    // completion value returned. Wrapped so a bare expression or a statement
    // block both work. Result must be JSON-serialisable (structured clone).
    const [inj] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      world: "MAIN",
      args: [cmd.js],
      func: (src) => {
        // eslint-disable-next-line no-new-func
        const v = new Function(`return (${src})`);
        try { return v(); } catch (_e) {
          // Fall back to statement execution (no return value).
          // eslint-disable-next-line no-new-func
          return new Function(src)();
        }
      },
    });
    return { url: tab.url, value: inj.result };
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
    const tab = await activeTab();
    await chrome.tabs.update(tab.id, { url: cmd.url });
    return { tabId: tab.id, url: cmd.url };
  },

  async screenshot() {
    const tab = await activeTab();
    const dataUrl = await chrome.tabs.captureVisibleTab(tab.windowId, {
      format: "png",
    });
    return { url: tab.url, dataUrl };
  },
};

async function execute(cmd) {
  const v = validateCommand(cmd);
  if (!v.ok) return errorEnvelope(cmd.id, v.error);
  try {
    const data = await OPS[cmd.op](cmd);
    return resultEnvelope(cmd.id, data);
  } catch (e) {
    return errorEnvelope(cmd.id, e && e.message ? e.message : e);
  }
}

// --- long-poll loop -------------------------------------------------------- //
async function pollOnce({ port, token }) {
  const res = await fetch(`${base(port)}/poll`, { headers: authHeaders(token) });
  if (res.status === 204) return null;          // idle timeout → re-poll
  if (res.status === 401) throw new Error("unauthorized");
  if (!res.ok) throw new Error(`poll_${res.status}`);
  return res.json();
}

async function postResult(cfg, envelope) {
  await fetch(`${base(cfg.port)}/result`, {
    method: "POST",
    headers: authHeaders(cfg.token),
    body: JSON.stringify(envelope),
  });
}

async function loop() {
  if (running) return;
  running = true;
  let attempt = 0;
  try {
    // eslint-disable-next-line no-constant-condition
    while (true) {
      const cfg = await config();
      if (!cfg.token) { await sleep(5000); continue; }
      try {
        const cmd = await pollOnce(cfg);
        attempt = 0;                             // healthy round-trip
        if (cmd) {
          const envelope = await execute(cmd);
          await postResult(cfg, envelope);
        }
      } catch (e) {
        // Transport error (server down, unauthorized, network) → backoff.
        const wait = nextBackoffMs(attempt++) + Math.floor(Math.random() * 250);
        await sleep(wait);
      }
    }
  } finally {
    running = false;
  }
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

// --- MV3 keepalive: restart the loop on install / startup / alarm ---------- //
chrome.runtime.onInstalled.addListener(() => loop());
chrome.runtime.onStartup.addListener(() => loop());
chrome.alarms.create("bridge-keepalive", { periodInMinutes: 1 });
chrome.alarms.onAlarm.addListener((a) => {
  if (a.name === "bridge-keepalive") loop();
});

// Kick immediately when the worker is first evaluated.
loop();

// Exported for reuse / potential future tests (no-op in the browser).
export { execute, OPS, ALLOWED_OPS };
