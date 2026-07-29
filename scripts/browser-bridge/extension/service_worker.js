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
  POLL_UNAUTHORIZED, SUPERSEDE_BACKOFF_MS, captureWithRetry,
  // CDP (chrome.debugger) helpers — the pure, unit-tested decision layer.
  CDP_VERSION, withCdpSession, flattenFrameTree, resolveFrame, keyEventParams,
  clickPoint, boxModelOrigin, frameEvalExpressions, isCdpSyntaxError,
  cdpExceptionText, frameHtmlExpression, frameTextExpression,
  elementRectExpression, focusExpression, fullPageClip,
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

// --- CDP (chrome.debugger) glue -------------------------------------------- //
// The pure, security-relevant CDP logic (attach-scope validation, always-detach
// orchestration, frame/key/coord math, typed-op-only surface) lives in protocol.js
// and is unit-tested there. This is the thin chrome.debugger side-effect layer.
//
// Tabs we currently hold a chrome.debugger attach on. `withCdp` is the ONLY code
// that attaches and it ALWAYS detaches (withCdpSession's finally), so this set is
// normally empty between ops; chrome.debugger.onDetach clears it if Chrome detaches
// us out-of-band (tab crash/close, or the user hitting the debug banner's Cancel).
const cdpAttached = new Set();

function sendCdp(target, method, params) {
  return chrome.debugger.sendCommand(target, method, params || {});
}

// Attach chrome.debugger to `tabId`, run `run(send)`, and ALWAYS detach. `url` is
// the target tab's URL, validated BEFORE attach by withCdpSession (a privileged /
// other-surface tab is refused, never attached — the STRICT attach-scope invariant).
async function withCdp(tabId, url, run) {
  const target = { tabId };
  return withCdpSession({
    url,
    attach: async () => {
      await chrome.debugger.attach(target, CDP_VERSION);
      cdpAttached.add(tabId);
    },
    detach: async () => {
      cdpAttached.delete(tabId);
      await chrome.debugger.detach(target);
    },
    run: () => run((method, params) => sendCdp(target, method, params)),
  });
}

// Evaluate `expression` in a specific execution context (an isolated world) via CDP
// Runtime.evaluate; returns its value. `contextId` undefined → the tab's DEFAULT
// (top-frame) context (JSON drops the undefined key). Throws on a runtime exception.
async function cdpEval(send, contextId, expression) {
  const res = await send("Runtime.evaluate",
    { expression, contextId, returnByValue: true, awaitPromise: true });
  if (res.exceptionDetails) throw new Error(cdpExceptionText(res.exceptionDetails));
  return res.result ? res.result.value : undefined;
}

// Frame-scoped `eval`: try the expression form, fall back to the statement form on a
// SyntaxError (mirrors compileEval), running in the frame's isolated world.
async function cdpFrameEval(send, contextId, js) {
  const { expression, fallback } = frameEvalExpressions(js);
  let res = await send("Runtime.evaluate",
    { expression, contextId, returnByValue: true, awaitPromise: true });
  if (res.exceptionDetails && isCdpSyntaxError(res.exceptionDetails)) {
    res = await send("Runtime.evaluate",
      { expression: fallback, contextId, returnByValue: true, awaitPromise: true });
  }
  if (res.exceptionDetails) throw new Error(cdpExceptionText(res.exceptionDetails));
  return res.result ? res.result.value : undefined;
}

// Resolve a `--frame <sel>` into an isolated-world execution context so a read/click
// runs INSIDE that (possibly cross-origin) frame. Returns { executionContextId,
// frameId, isMain, ownerOffset } — ownerOffset is the frame's on-page origin (for a
// sub-frame, from DOM.getBoxModel on its owner element; {0,0} for the main frame).
async function resolveFrameContext(send, frameSel) {
  await send("Page.enable");
  const { frameTree } = await send("Page.getFrameTree");
  const frames = flattenFrameTree(frameTree);
  const frame = resolveFrame(frames, frameSel);           // throws frame_not_found
  const isMain = frames.length > 0 && frames[0].frameId === frame.frameId;
  const { executionContextId } = await send("Page.createIsolatedWorld",
    { frameId: frame.frameId, worldName: "browser_bridge" });
  let ownerOffset = { x: 0, y: 0 };
  if (!isMain) {
    // A sub-frame's element coords are frame-local; offset them by the iframe
    // element's on-page box so Input.dispatchMouseEvent lands in the right place.
    await send("DOM.enable");
    const { backendNodeId } = await send("DOM.getFrameOwner", { frameId: frame.frameId });
    const { model } = await send("DOM.getBoxModel", { backendNodeId });
    ownerOffset = boxModelOrigin(model);
  }
  return { executionContextId, frameId: frame.frameId, isMain, ownerOffset };
}

// --- op executors ---------------------------------------------------------- //
// Each returns the op-specific `data` object; throws on failure (→ errorEnvelope).
const OPS = {
  async getHtml(cmd) {
    const tab = await targetTab(cmd);
    // --frame → read the outerHTML INSIDE the chosen (cross-origin) frame via CDP.
    if (cmd && cmd.frame) {
      const html = await withCdp(tab.id, tab.url, async (send) => {
        const { executionContextId } = await resolveFrameContext(send, cmd.frame);
        return cdpEval(send, executionContextId, frameHtmlExpression());
      });
      return { url: tab.url, title: tab.title, html, frame: cmd.frame };
    }
    // No frame → the lighter chrome.scripting top-frame read (no debugger banner).
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
    const cap = (cmd && cmd.maxBytes != null)
      ? cmd.maxBytes : TEXT_MAX_BYTES_DEFAULT;
    // --frame → read innerText INSIDE the chosen (cross-origin) frame via CDP.
    if (cmd && cmd.frame) {
      const raw = await withCdp(tab.id, tab.url, async (send) => {
        const { executionContextId } = await resolveFrameContext(send, cmd.frame);
        return cdpEval(send, executionContextId, frameTextExpression(sel));
      });
      const { text, truncated } = normalizeText(raw, cap);
      return { url: tab.url, title: tab.title, text, truncated, frame: cmd.frame };
    }
    const [inj] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      args: [sel],
      func: (s) => {
        const el = s ? document.querySelector(s) : document.body;
        return el ? el.innerText : "";
      },
    });
    const { text, truncated } = normalizeText(inj.result, cap);
    return { url: tab.url, title: tab.title, text, truncated };
  },

  async eval(cmd) {
    const tab = await targetTab(cmd);
    // --frame → evaluate INSIDE the chosen (cross-origin) frame's isolated world
    // via CDP (DOM-capable; no access to that frame's page globals — documented).
    if (cmd && cmd.frame) {
      const value = await withCdp(tab.id, tab.url, async (send) => {
        const { executionContextId } = await resolveFrameContext(send, cmd.frame);
        return cdpFrameEval(send, executionContextId, cmd.js);
      });
      return { url: tab.url, value, frame: cmd.frame };
    }
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

  // Screenshot the owned/target tab. PRIMARY path is CDP Page.captureScreenshot,
  // which captures a BACKGROUND / occluded / non-foreground tab (the whole point —
  // it fixes the captureVisibleTab "can only grab the foreground tab" limitation,
  // and lets two profiles each screenshot their own tab). A FAST path keeps the
  // cheap, banner-free captureVisibleTab for a tab that IS already visible (and not
  // --fullpage); any failure there falls through to the CDP path. `--fullpage`
  // captures the whole scrollable document (CDP only). Attach is REFUSED on a
  // privileged tab (assertCdpAttachable inside withCdp) before any attach.
  async screenshot(cmd) {
    const tab = await targetTab(cmd);
    const fullpage = !!(cmd && cmd.fullpage);
    if (tab.active && !fullpage) {
      // Fast path — no debugger attach/banner. Chrome throttles captureVisibleTab to
      // ~2/sec; captureWithRetry spaces the (rare) retry ≥ the quota window.
      try {
        const dataUrl = await captureWithRetry(() =>
          chrome.tabs.captureVisibleTab(tab.windowId, { format: "png" }));
        return { url: tab.url, dataUrl, via: "captureVisibleTab" };
      } catch (e) { /* fall through to the CDP path (works off-screen) */ }
    }
    const dataUrl = await withCdp(tab.id, tab.url, async (send) => {
      const params = { format: "png" };
      if (fullpage) {
        const metrics = await send("Page.getLayoutMetrics");
        params.clip = fullPageClip(metrics);
        params.captureBeyondViewport = true;
      }
      const { data } = await send("Page.captureScreenshot", params);
      return `data:image/png;base64,${data}`;
    });
    return { url: tab.url, dataUrl, via: "cdp" };
  },

  // List the target tab's frames (frameId/url/name/parentId) via CDP
  // Page.getFrameTree — so a caller can discover a cross-origin iframe and then
  // read/click INTO it with `--frame <frameId|url-substring>`. Metadata only.
  async frames(cmd) {
    const tab = await targetTab(cmd);
    const frames = await withCdp(tab.id, tab.url, async (send) => {
      await send("Page.enable");
      const { frameTree } = await send("Page.getFrameTree");
      return flattenFrameTree(frameTree);
    });
    return { url: tab.url, title: tab.title, frames };
  },

  // TRUSTED click on `selector` (optionally inside `--frame`). Resolves the element
  // box via getBoundingClientRect (in the frame's isolated world), offsets by the
  // frame's on-page origin for a sub-frame, then dispatches a real press+release
  // via CDP Input.dispatchMouseEvent — an isTrusted click the page can't tell from
  // a human's. `selector` is validated present by server/SW REQUIRED_FIELDS.
  async click(cmd) {
    const tab = await targetTab(cmd);
    const selector = String(cmd.selector);
    const point = await withCdp(tab.id, tab.url, async (send) => {
      let ctxId, offset = { x: 0, y: 0 };
      if (cmd.frame) {
        const fc = await resolveFrameContext(send, cmd.frame);
        ctxId = fc.executionContextId;
        offset = fc.ownerOffset;
      }
      const rect = await cdpEval(send, ctxId, elementRectExpression(selector));
      if (!rect) throw new Error(`element_not_found:${selector}`);
      const p = clickPoint(rect, offset);
      const mouse = (type) => send("Input.dispatchMouseEvent",
        { type, x: p.x, y: p.y, button: "left", buttons: 1, clickCount: 1 });
      await mouse("mousePressed");
      await mouse("mouseReleased");
      return p;
    });
    return { url: tab.url, clicked: selector, x: point.x, y: point.y,
             frame: cmd.frame || null };
  },

  // Type `text` into the focused element (optionally focus `--selector` first,
  // optionally inside `--frame`) via CDP Input.insertText — a trusted input event.
  // Returns only the LENGTH typed, never echoes the text back (privacy + telemetry).
  async type(cmd) {
    const tab = await targetTab(cmd);
    const text = String(cmd.text);
    await withCdp(tab.id, tab.url, async (send) => {
      let ctxId;
      if (cmd.frame) ctxId = (await resolveFrameContext(send, cmd.frame)).executionContextId;
      if (cmd.selector) {
        const ok = await cdpEval(send, ctxId, focusExpression(cmd.selector));
        if (!ok) throw new Error(`element_not_found:${cmd.selector}`);
      }
      await send("Input.insertText", { text });
    });
    return { url: tab.url, typed: text.length, frame: cmd.frame || null };
  },

  // Dispatch a single bounded key (Enter/Tab/Escape/arrows/…) to the focused element
  // (optionally focus `--selector` first, optionally inside `--frame`) via CDP
  // Input.dispatchKeyEvent (keyDown+keyUp). An unknown key is refused BEFORE attach.
  async key(cmd) {
    const tab = await targetTab(cmd);
    const p = keyEventParams(cmd.key);   // throws unknown_key (no attach on refusal)
    await withCdp(tab.id, tab.url, async (send) => {
      let ctxId;
      if (cmd.frame) ctxId = (await resolveFrameContext(send, cmd.frame)).executionContextId;
      if (cmd.selector) {
        const ok = await cdpEval(send, ctxId, focusExpression(cmd.selector));
        if (!ok) throw new Error(`element_not_found:${cmd.selector}`);
      }
      const base = { key: p.key, code: p.code,
                     windowsVirtualKeyCode: p.keyCode, nativeVirtualKeyCode: p.keyCode };
      await send("Input.dispatchKeyEvent",
        { type: p.text ? "keyDown" : "rawKeyDown", ...base, ...(p.text ? { text: p.text } : {}) });
      await send("Input.dispatchKeyEvent", { type: "keyUp", ...base });
    });
    return { url: tab.url, key: p.key, frame: cmd.frame || null };
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

// If Chrome detaches our debugger out-of-band (tab crash/close, DevTools opened, or
// the user hitting the "an extension is debugging this browser" banner's Cancel),
// drop the tracked attach so we never think we still hold it. withCdp already
// always-detaches per op; this is the belt-and-braces for an external detach.
if (chrome.debugger && chrome.debugger.onDetach) {
  chrome.debugger.onDetach.addListener((source) => {
    if (source && source.tabId != null) cdpAttached.delete(source.tabId);
  });
}

// Kick immediately when the worker is first evaluated.
loop();

// Exported for reuse / potential future tests (no-op in the browser).
export { execute, OPS, ALLOWED_OPS };
