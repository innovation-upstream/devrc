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
  // CDP (chrome.debugger) helpers — still used for screenshots + TOP-frame trusted
  // input (the pure, unit-tested decision layer).
  CDP_VERSION, withCdpSession, assertTabCdpReady, keyEventParams,
  clickPoint, cdpExceptionText, elementRectExpression, focusExpression, fullPageClip,
  // OOPIF-capable frame enumeration/injection (chrome.webNavigation + chrome.scripting):
  // reaches cross-origin out-of-process iframes where CDP getFrameTree could not.
  normalizeWebNavFrames, resolveWebNavFrame,
  frameReadHtmlFn, frameReadTextFn,
  frameClickFn, frameTypeFn, frameKeyFn,
  // CDP `eval --frame`: run an arbitrary JS STRING in the target frame's execution
  // context (chrome.scripting can only run a serialized FUNC — the #190 null bug).
  frameEvalExpressions, isCdpSyntaxError, matchCdpFrameId, pickOopifSessionId,
  evalValueOrThrow,
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
// The `send` handed to `run` takes an optional 3rd arg `sessionId` so a command can
// target a flat auto-attached sub-session (an OOPIF target) — still bounded by the
// #189 per-command timeout. `globalThis.BROWSER_BRIDGE_CDP_TIMEOUTS` is a TEST-ONLY
// hook to shrink the (8s) budgets so a no-wedge test settles in ms; undefined in
// production → the real CDP_* budgets.
async function withCdp(tabId, url, run) {
  const target = { tabId };
  return withCdpSession({
    url,
    timeouts: (typeof globalThis !== "undefined" && globalThis.BROWSER_BRIDGE_CDP_TIMEOUTS)
      || undefined,
    attach: async () => {
      // Fail fast on a discarded/unloaded tab (no live renderer → attach would
      // hang forever). withCdpSession's per-call timeouts are the backstop for any
      // other hang; this turns the common case into an immediate clear error.
      let tab;
      try { tab = await chrome.tabs.get(tabId); }
      catch (e) { throw new Error("owned_tab_gone"); }
      assertTabCdpReady(tab);
      await chrome.debugger.attach(target, CDP_VERSION);
      cdpAttached.add(tabId);
    },
    detach: async () => {
      cdpAttached.delete(tabId);
      await chrome.debugger.detach(target);
    },
    // Raw send — withCdpSession wraps it in the per-command timeout before handing
    // it to `run`, so a single hung CDP command can't wedge the SW. A non-null
    // `sessionId` targets a flat auto-attached OOPIF sub-session (Debuggee
    // {tabId, sessionId}); omitted → the tab's top session.
    send: (method, params, sessionId) =>
      sendCdp(sessionId != null ? { ...target, sessionId } : target, method, params),
    run: (send) => run(send),
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

// --- OOPIF-capable frame glue (chrome.webNavigation + chrome.scripting) ------- //
// Enumerate ALL of `tabId`'s frames — same-process AND cross-origin OOPIFs — as the
// compact metadata list the `frames` op returns. getAllFrames is tab-scoped, so the
// list can only ever describe frames of THIS tab (the security scope for `--frame`).
async function framesForTab(tabId) {
  const raw = await chrome.webNavigation.getAllFrames({ tabId });
  return normalizeWebNavFrames(raw || []);
}

// Resolve a caller `--frame <sel>` to the FRAME OBJECT ({frameId,url,parentFrameId})
// within `tabId`. Throws frame_not_found / frame_not_specified. Confined to this tab by
// construction (framesForTab is tab-scoped). Callers use `.frameId` to inject and
// `.url` both to report the frame's own url AND (for eval) to locate the frame's CDP
// execution context by URL.
async function resolveFrame(tabId, frameSel) {
  const frames = await framesForTab(tabId);
  return resolveWebNavFrame(frames, frameSel);
}

// Inject `func(...args)` INTO one resolved frame (by numeric frameId) of `tabId` via
// chrome.scripting — the OOPIF-reaching path (works on a cross-origin frame given the
// extension's <all_urls> host permission), no chrome.debugger/banner. Returns the
// injected function's (structured-cloned) result. A frameId is confined to `tabId`;
// executeScript cannot escape the target tab's frames.
async function execInFrame(tabId, frameId, func, args) {
  const results = await chrome.scripting.executeScript({
    target: { tabId, frameIds: [frameId] },
    func,
    args: args || [],
  });
  const inj = Array.isArray(results) ? results[0] : undefined;
  return inj ? inj.result : undefined;
}

// Evaluate an arbitrary JS STRING inside one resolved frame of `tabId` via CDP
// Runtime.evaluate — the RELIABLE path for `eval --frame` (chrome.scripting can only
// run a serialized FUNC, so it can't evaluate a user string: the #190 null-as-success
// bug). Works for a SAME-PROCESS frame AND a cross-origin OOPIF (a separate target):
//   1. attach chrome.debugger to the OWNED tab (withCdp → #187 own-tab-only scope +
//      #189 bounded timeouts + discarded-tab fail-fast);
//   2. SAME-PROCESS: the frame is in the top session's Page.getFrameTree → its CDP
//      frameId → Page.createIsolatedWorld → an executionContextId → Runtime.evaluate;
//   3. OOPIF: NOT in the top frame tree → Target.setAutoAttach({autoAttach,flatten})
//      auto-attaches the OOPIF's target (flat sessionId, matched by url) → Runtime.
//      evaluate in that session's default context.
// NEVER SILENT-NULL: a genuine null/undefined result is returned AS a value, but a
// FAILURE to execute (frame not resolvable / exceptionDetails) is a CLEAR op error
// (frame_not_found / frame_eval_failed:<reason>) via evalValueOrThrow. `frame` is the
// resolved {frameId,url} object; matching is by `frame.url` (the numeric webNavigation
// frameId does not map 1:1 to a CDP frame/target).
async function cdpFrameEval(tabId, tabUrl, frame, src) {
  const { expression, fallback } = frameEvalExpressions(src);
  return withCdp(tabId, tabUrl, async (send) => {
    // Try `expression` (expression form); on a CDP SyntaxError retry `fallback` (the
    // statement form). One evaluate per form → a side effect never double-runs.
    const evaluate = async (sessionId, contextId) => {
      const params = { expression, returnByValue: true, awaitPromise: true };
      if (contextId != null) params.contextId = contextId;
      let res = await send("Runtime.evaluate", params, sessionId);
      if (res && res.exceptionDetails && isCdpSyntaxError(res.exceptionDetails)) {
        const p2 = { expression: fallback, returnByValue: true, awaitPromise: true };
        if (contextId != null) p2.contextId = contextId;
        res = await send("Runtime.evaluate", p2, sessionId);
      }
      return evalValueOrThrow(res);   // throws frame_eval_failed:<reason> on exception
    };

    // (2) SAME-PROCESS frame — locate it in the top session's frame tree by url.
    const { frameTree } = await send("Page.getFrameTree");
    const cdpFrameId = matchCdpFrameId(frameTree, frame.url);
    if (cdpFrameId) {
      const iso = await send("Page.createIsolatedWorld",
        { frameId: cdpFrameId, worldName: "browser-bridge-eval", grantUniveralAccess: false });
      return evaluate(undefined, iso.executionContextId);
    }

    // (3) CROSS-ORIGIN OOPIF — auto-attach flat, match the target session by url,
    // evaluate in THAT session. Collect Target.attachedToTarget events for the duration
    // of this op only (listener removed in finally), then match by frame url.
    const attached = [];
    const onEvt = (_source, method, params) => {
      if (method === "Target.attachedToTarget" && params && params.targetInfo) {
        attached.push({ sessionId: params.sessionId, url: params.targetInfo.url });
      }
    };
    chrome.debugger.onEvent.addListener(onEvt);
    try {
      await send("Target.setAutoAttach",
        { autoAttach: true, flatten: true, waitForDebuggerOnStart: false });
      const sessionId = pickOopifSessionId(attached, frame.url);
      if (!sessionId) throw new Error(`frame_not_found:${frame.url || frame.frameId}`);
      return await evaluate(sessionId, undefined);
    } finally {
      chrome.debugger.onEvent.removeListener(onEvt);
    }
  });
}

// --- op executors ---------------------------------------------------------- //
// Each returns the op-specific `data` object; throws on failure (→ errorEnvelope).
const OPS = {
  async getHtml(cmd) {
    const tab = await targetTab(cmd);
    // --frame → read the outerHTML INSIDE the chosen (cross-origin OOPIF) frame via
    // chrome.scripting (reaches an out-of-process iframe; no debugger banner).
    if (cmd && cmd.frame) {
      const frame = await resolveFrame(tab.id, cmd.frame);
      const html = await execInFrame(tab.id, frame.frameId, frameReadHtmlFn, []);
      // Report the FRAME's own url (not the top tab url) so the caller can confirm it
      // read the intended frame (#190 reported the top url for a frame read).
      return { url: frame.url || tab.url, title: tab.title, html, frame: cmd.frame };
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
    // --frame → read innerText INSIDE the chosen (cross-origin OOPIF) frame via
    // chrome.scripting (reaches an out-of-process iframe; no debugger banner).
    if (cmd && cmd.frame) {
      const frame = await resolveFrame(tab.id, cmd.frame);
      const raw = await execInFrame(tab.id, frame.frameId, frameReadTextFn, [sel]);
      const { text, truncated } = normalizeText(raw, cap);
      // Report the FRAME's own url (see getHtml) so the caller confirms the right frame.
      return { url: frame.url || tab.url, title: tab.title, text, truncated, frame: cmd.frame };
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
    // --frame → evaluate the arbitrary JS STRING INSIDE the chosen frame (incl. a
    // cross-origin OOPIF) via CDP Runtime.evaluate. chrome.scripting can only run a
    // serialized FUNC (not a string), so the #190 chrome.scripting path executed
    // nothing meaningful and returned value:null-as-success — the bug this fixes.
    // cdpFrameEval resolves the frame's execution context (same-process isolated world
    // OR OOPIF flat session) and NEVER silent-nulls (frame_not_found / frame_eval_failed).
    if (cmd && cmd.frame) {
      const frame = await resolveFrame(tab.id, cmd.frame);
      const value = await cdpFrameEval(tab.id, tab.url, frame, cmd.js);
      return { url: frame.url || tab.url, value, frame: cmd.frame };
    }
    // chrome.scripting runs the top-frame eval in the page's MAIN world (world:
    // "MAIN" below) — so `js` sees the page's own globals — and its completion value
    // is returned. Wrapped so a bare expression or a statement block both work.
    // Result must be JSON-serialisable (structured clone).
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

  // List the target tab's frames ({frameId,url,parentFrameId}) via
  // chrome.webNavigation.getAllFrames — which, UNLIKE CDP Page.getFrameTree,
  // enumerates OUT-OF-PROCESS (cross-origin) iframes too. So a caller can discover a
  // cross-origin OOPIF and read/click INTO it with `--frame <frameId|url-substring>`.
  // Metadata only (numeric frameId + url + parent) — never frame content. No debugger.
  async frames(cmd) {
    const tab = await targetTab(cmd);
    const frames = await framesForTab(tab.id);
    return { url: tab.url, title: tab.title, frames };
  },

  // Click `selector`. TWO paths, by design:
  //   * `--frame` (cross-origin OOPIF): inject a SYNTHETIC click into the resolved
  //     frame via chrome.scripting (the only path that reaches an OOPIF). The
  //     dispatched events are `isTrusted:false` — honestly reported as trusted:false —
  //     but drive the vast majority of apps (which listen for ordinary click/input).
  //   * TOP frame (no `--frame`): the CDP Input.dispatchMouseEvent path — a real
  //     `isTrusted` press+release the page can't tell from a human's (unchanged).
  // `selector` is validated present by server/SW REQUIRED_FIELDS.
  async click(cmd) {
    const tab = await targetTab(cmd);
    const selector = String(cmd.selector);
    if (cmd.frame) {
      const frame = await resolveFrame(tab.id, cmd.frame);
      const res = await execInFrame(tab.id, frame.frameId, frameClickFn, [selector]);
      if (!res || res.ok === false) throw new Error(`element_not_found:${selector}`);
      return { url: frame.url || tab.url, clicked: selector, x: res.x, y: res.y,
               frame: cmd.frame, trusted: false };
    }
    const point = await withCdp(tab.id, tab.url, async (send) => {
      const rect = await cdpEval(send, undefined, elementRectExpression(selector));
      if (!rect) throw new Error(`element_not_found:${selector}`);
      const p = clickPoint(rect, { x: 0, y: 0 });
      const mouse = (type) => send("Input.dispatchMouseEvent",
        { type, x: p.x, y: p.y, button: "left", buttons: 1, clickCount: 1 });
      await mouse("mousePressed");
      await mouse("mouseReleased");
      return p;
    });
    return { url: tab.url, clicked: selector, x: point.x, y: point.y,
             frame: null, trusted: true };
  },

  // Type `text` (optionally focus `--selector` first). `--frame` → SYNTHETIC input
  // (focus + set value + input/change) injected into the cross-origin OOPIF via
  // chrome.scripting (trusted:false — the reachable OOPIF path). TOP frame → CDP
  // Input.insertText, a trusted input event (unchanged). Returns only the LENGTH
  // typed, never echoes the text back (privacy + telemetry).
  async type(cmd) {
    const tab = await targetTab(cmd);
    const text = String(cmd.text);
    if (cmd.frame) {
      const frame = await resolveFrame(tab.id, cmd.frame);
      const res = await execInFrame(tab.id, frame.frameId, frameTypeFn,
        [cmd.selector || "", text]);
      // Surface the injected fn's SPECIFIC error: a missing selector → element_not_found
      // (with the selector), and an empty/non-editable target → no_editable_target
      // (never a false success claiming N chars typed — #190 audit).
      if (!res || res.ok === false) {
        const err = (res && res.error) || "type_failed";
        throw new Error(err === "element_not_found" ? `element_not_found:${cmd.selector}` : err);
      }
      return { url: frame.url || tab.url, typed: text.length, frame: cmd.frame, trusted: false };
    }
    await withCdp(tab.id, tab.url, async (send) => {
      if (cmd.selector) {
        const ok = await cdpEval(send, undefined, focusExpression(cmd.selector));
        if (!ok) throw new Error(`element_not_found:${cmd.selector}`);
      }
      await send("Input.insertText", { text });
    });
    return { url: tab.url, typed: text.length, frame: null, trusted: true };
  },

  // Dispatch one bounded key (Enter/Tab/Escape/arrows/…). The key name is resolved +
  // validated by keyEventParams FIRST — an unknown key is refused BEFORE any injection
  // or attach (the bounded-key surface is preserved on both paths). `--frame` →
  // SYNTHETIC keydown/keyup injected into the cross-origin OOPIF via chrome.scripting
  // (trusted:false — the reachable OOPIF path). TOP frame → CDP Input.dispatchKeyEvent,
  // a trusted key event (unchanged).
  async key(cmd) {
    const tab = await targetTab(cmd);
    const p = keyEventParams(cmd.key);   // throws unknown_key (no injection/attach on refusal)
    if (cmd.frame) {
      const frame = await resolveFrame(tab.id, cmd.frame);
      const res = await execInFrame(tab.id, frame.frameId, frameKeyFn, [cmd.selector || "", p]);
      if (!res || res.ok === false) throw new Error(`element_not_found:${cmd.selector}`);
      return { url: frame.url || tab.url, key: p.key, frame: cmd.frame, trusted: false };
    }
    await withCdp(tab.id, tab.url, async (send) => {
      if (cmd.selector) {
        const ok = await cdpEval(send, undefined, focusExpression(cmd.selector));
        if (!ok) throw new Error(`element_not_found:${cmd.selector}`);
      }
      const base = { key: p.key, code: p.code,
                     windowsVirtualKeyCode: p.keyCode, nativeVirtualKeyCode: p.keyCode };
      await send("Input.dispatchKeyEvent",
        { type: p.text ? "keyDown" : "rawKeyDown", ...base, ...(p.text ? { text: p.text } : {}) });
      await send("Input.dispatchKeyEvent", { type: "keyUp", ...base });
    });
    return { url: tab.url, key: p.key, frame: null, trusted: true };
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

// --- MV3 keepalive + background wiring -------------------------------------- //
// All the real-browser side effects (event listeners, the keepalive alarm, and the
// immediate loop kick) are grouped here so a unit test can import this module for its
// pure OPS glue WITHOUT starting the poll loop or requiring chrome.runtime/alarms:
// set `globalThis.BROWSER_BRIDGE_NO_AUTOSTART = true` before importing.
function startBackground() {
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
}

if (!(typeof globalThis !== "undefined" && globalThis.BROWSER_BRIDGE_NO_AUTOSTART)) {
  startBackground();
}

// Exported for reuse / unit tests (the frame glue is exercised against a mocked
// chrome in tests/service_worker.test.mjs).
export { execute, OPS, ALLOWED_OPS };
