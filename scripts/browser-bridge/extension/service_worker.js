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
  pollHeaders, resultWithInstance, normalizeText, TEXT_MAX_BYTES_DEFAULT,
  classifyPollStatus, POLL_COMMAND, POLL_IDLE, POLL_SUPERSEDED,
  POLL_UNAUTHORIZED, SUPERSEDE_BACKOFF_MS,
  captureWithRetry, waitForCaptureReady, screenshotWithRestore,
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

// --- tab-targeting helpers ------------------------------------------------- //
async function activeTab() {
  const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  if (!tab) throw new Error("no_active_tab");
  return tab;
}

// The tab an op runs against. When the server injected a `tabId` (the caller
// owns a tab, or passed --tab), use THAT tab — this is the per-session isolation
// that stops two Claude sessions from clobbering one shared active tab. With no
// tabId (a one-shot read by a session that never `open`ed), fall back to the
// active tab — exactly the historical behaviour.
async function targetTab(cmd) {
  if (cmd && cmd.tabId != null) {
    try {
      return await chrome.tabs.get(cmd.tabId);
    } catch (e) {
      throw new Error("owned_tab_gone");   // the tab was closed out-of-band
    }
  }
  return activeTab();
}

// --- op executors ---------------------------------------------------------- //
// Each returns the op-specific `data` object; throws on failure (→ errorEnvelope).
const OPS = {
  async getHtml(cmd) {
    const tab = await targetTab(cmd);
    const [inj] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: () => document.documentElement.outerHTML,
    });
    return { url: tab.url, title: tab.title, html: inj.result };
  },

  // Cheap read: the tab's VISIBLE innerText (optionally scoped to a CSS
  // selector), normalized + byte-capped in protocol.js. ~98% smaller than
  // getHtml's outerHTML — what the opencode browser-agent reads with. The
  // injected fn returns RAW innerText; normalizeText does the whitespace
  // collapse + cap out here (so it stays pure + unit-tested).
  async text(cmd) {
    const tab = await targetTab(cmd);
    const sel = (cmd && typeof cmd.selector === "string") ? cmd.selector : "";
    const [inj] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      args: [sel],
      func: (s) => {
        const el = s ? document.querySelector(s) : document.body;
        return el ? el.innerText : "";
      },
    });
    const cap = (cmd && cmd.maxBytes != null)
      ? cmd.maxBytes : TEXT_MAX_BYTES_DEFAULT;
    const { text, truncated } = normalizeText(inj.result, cap);
    return { url: tab.url, title: tab.title, text, truncated };
  },

  async eval(cmd) {
    const tab = await targetTab(cmd);
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
    const tab = await targetTab(cmd);
    await chrome.tabs.update(tab.id, { url: cmd.url });
    return { tabId: tab.id, url: cmd.url };
  },

  async screenshot(cmd) {
    const tab = await targetTab(cmd);
    // captureVisibleTab only ever captures the VISIBLE tab of its window. If the
    // caller's owned tab is in the background, capturing it requires briefly
    // bringing it to the foreground. We do exactly that — activate → SETTLE →
    // capture (with retry) → restore the previously-active tab — so we never
    // SILENTLY screenshot the wrong tab. This causes a brief visible flicker in
    // that window (documented).
    //
    // A JUST-activated background tab hasn't painted its first frame yet, so a
    // bare captureVisibleTab returns "image readback failed". So for the
    // background path we (1) wait for the tab to reach `status:"complete"` + a
    // paint settle (so the FIRST capture usually succeeds — no retry needed), and
    // (2) retry the capture on a transient error. CRITICAL: Chrome throttles
    // captureVisibleTab to ~2/sec (MAX_CAPTURE_VISIBLE_TAB_CALLS_PER_SECOND), so
    // the retry backoff spaces attempts ≥~600ms (a quota hit waits a full ~1s
    // window) — a faster retry would re-trip the quota. Both the settle and the
    // spaced retry, plus the activate→capture→restore orchestration (restore on
    // success AND failure), are pure + unit-tested in protocol.js — keep this in
    // sync with them.
    const capture = () =>
      chrome.tabs.captureVisibleTab(tab.windowId, { format: "png" });
    const dataUrl = await screenshotWithRestore({
      isActive: !!tab.active,
      targetId: tab.id,
      // The visible-tab path is a hot GPU capture too — retry it as well (cheap,
      // and it hardens against a rare transient readback even when already active).
      capture: () => captureWithRetry(capture),
      getPrevActiveId: async () => {
        const [prev] = await chrome.tabs.query({ active: true, windowId: tab.windowId });
        return prev ? prev.id : null;
      },
      activate: (id) => chrome.tabs.update(id, { active: true }),
      waitReady: () => waitForCaptureReady(async () => {
        try { return (await chrome.tabs.get(tab.id)).status; } catch (e) { return null; }
      }),
      restore: (id) => chrome.tabs.update(id, { active: true }),
    });
    return { url: tab.url, dataUrl };
  },

  // Create a NEW tab for the calling session to own. active:false so parallel
  // sessions don't fight over the foreground when each opens its own tab. The
  // server records this real tabId as the session's owned tab.
  //
  // Idempotent re-open: when the server passes `reuseTabId` (the session already
  // owns a tab), reuse THAT tab if it is still live instead of creating a second
  // one — otherwise a double `open` would orphan the first real tab (no ownership
  // → never closed → leaked). If the reuse tab is gone, fall through and open a
  // fresh one (open-after-owned-tab-gone).
  async open(cmd) {
    if (cmd && cmd.reuseTabId != null) {
      try {
        const existing = await chrome.tabs.get(cmd.reuseTabId);
        return { tabId: existing.id, url: existing.url || "about:blank",
                 reused: true };
      } catch (e) { /* owned tab gone → open a fresh one below */ }
    }
    const tab = await chrome.tabs.create({
      url: (cmd && cmd.url) ? cmd.url : "about:blank",
      active: false,
    });
    return { tabId: tab.id, url: tab.url || (cmd && cmd.url) || "about:blank" };
  },

  // Close the session's owned tab (the server injects its tabId). The server
  // drops the ownership mapping on success. Idempotent: if the tab was already
  // closed out-of-band, `chrome.tabs.remove` rejects — treat that as success
  // (the desired end-state, tab absent, already holds) so the session's stale
  // ownership is cleanly dropped instead of surfacing a spurious error.
  async close(cmd) {
    if (!cmd || cmd.tabId == null) throw new Error("missing_tabId");
    try {
      await chrome.tabs.remove(cmd.tabId);
      return { closed: cmd.tabId };
    } catch (e) {
      return { closed: cmd.tabId, alreadyGone: true };
    }
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
