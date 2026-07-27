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

import {
  ALLOWED_OPS, validateCommand, resultEnvelope, errorEnvelope, nextBackoffMs,
  pollHeaders, resultWithInstance,
  classifyPollStatus, POLL_COMMAND, POLL_IDLE, POLL_SUPERSEDED,
  POLL_UNAUTHORIZED, SUPERSEDE_BACKOFF_MS,
} from "./protocol.js";

const DEFAULT_PORT = 8788;
let running = false;

// The stable per-profile auto-id: generated ONCE and persisted in
// chrome.storage.local so it survives service-worker restarts/reloads within
// this Brave profile. It is the routing key when no user label is set, and the
// server treats a new auto-id on an existing key as a supersede.
async function instanceId() {
  let { instanceId } = await chrome.storage.local.get("instanceId");
  if (!instanceId) {
    instanceId = crypto.randomUUID();
    await chrome.storage.local.set({ instanceId });
  }
  return instanceId;
}

async function config() {
  const { port, token, label } = await chrome.storage.local.get(["port", "token", "label"]);
  return {
    port: port || DEFAULT_PORT,
    token: token || "",
    label: label || "",
    instanceId: await instanceId(),
  };
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
        // Decide expression-vs-statement form by whether the expression-wrapped
        // body PARSES — WITHOUT executing it — then call the chosen fn exactly
        // once. A construction SyntaxError → fall back to the statement form; a
        // runtime throw from calling the fn must propagate (never re-run a side
        // effect). Mirrors protocol.js compileEval — keep the two in sync.
        let fn;
        try {
          // eslint-disable-next-line no-new-func
          fn = new Function(`return (${src})`);
        } catch (e) {
          if (e instanceof SyntaxError) {
            // eslint-disable-next-line no-new-func
            fn = new Function(src);   // statement form (no return value)
          } else {
            throw e;
          }
        }
        return fn();
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

// Best-effort active-tab snapshot for cheap /health + `browser instances`
// enrichment. Never throws — a query failure just omits the tab info.
async function activeTabSnapshot() {
  try {
    const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
    if (tab) return { url: tab.url, title: tab.title };
  } catch (e) { /* ignore */ }
  return null;
}

// --- long-poll loop -------------------------------------------------------- //
// pollOnce returns a tagged result: { kind } where kind ∈ POLL_IDLE /
// POLL_SUPERSEDED, or { kind: POLL_COMMAND, cmd } — so the loop can tell the
// distinct "you were superseded" signal (409) apart from a normal idle 204.
async function pollOnce(cfg) {
  const active = await activeTabSnapshot();
  const res = await fetch(`${base(cfg.port)}/poll`, {
    // Identify this instance so the server routes only its commands here.
    headers: { ...authHeaders(cfg.token), ...pollHeaders(cfg.instanceId, cfg.label, active) },
  });
  const kind = classifyPollStatus(res.status);
  if (kind === POLL_COMMAND) return { kind, cmd: await res.json() };
  if (kind === POLL_IDLE) return { kind };          // idle timeout → re-poll
  if (kind === POLL_SUPERSEDED) return { kind };     // displaced → back off hard
  if (kind === POLL_UNAUTHORIZED) throw new Error("unauthorized");
  throw new Error(`poll_${res.status}`);
}

// Persist a "superseded" flag (for the options page to surface) + warn once.
// Never throws — a storage failure must not wedge the loop. We only WRITE on a
// state change so a steady-state loser doesn't spam storage.
async function setSuperseded(cfg) {
  try {
    const { superseded } = await chrome.storage.local.get("superseded");
    if (!superseded) {
      await chrome.storage.local.set({ superseded: true, supersededSince: Date.now() });
      // eslint-disable-next-line no-console
      console.warn(
        "[browser-bridge] superseded by another instance sharing this routing key" +
        (cfg.label ? ` ("${cfg.label}")` : "") +
        " — give each Brave profile a UNIQUE label in the extension options.");
    }
  } catch (e) { /* ignore */ }
}

async function clearSuperseded() {
  try {
    const { superseded } = await chrome.storage.local.get("superseded");
    if (superseded) await chrome.storage.local.set({ superseded: false, supersededSince: 0 });
  } catch (e) { /* ignore */ }
}

async function postResult(cfg, envelope) {
  await fetch(`${base(cfg.port)}/result`, {
    method: "POST",
    headers: authHeaders(cfg.token),
    // Stamp our instanceId so the server scopes the reply to this instance.
    body: JSON.stringify(resultWithInstance(envelope, cfg.instanceId)),
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
        const r = await pollOnce(cfg);
        attempt = 0;                             // healthy round-trip
        if (r.kind === POLL_SUPERSEDED) {
          // Another instance on this host claimed our routing key (a duplicate
          // LABEL, or a storage reset). Surface it and BACK OFF HARD instead of
          // hot re-registering — otherwise the two same-label workers mutually
          // supersede at loopback speed (a livelock). Auto-recovers if the other
          // instance goes away; the human fix is a unique label per profile.
          await setSuperseded(cfg);
          await sleep(SUPERSEDE_BACKOFF_MS + Math.floor(Math.random() * 1000));
          continue;
        }
        await clearSuperseded();
        if (r.kind === POLL_COMMAND && r.cmd) {
          const envelope = await execute(r.cmd);
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
