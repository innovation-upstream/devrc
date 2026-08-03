// browser.js — the opencode custom tool that IS the browser-agent's ONLY
// capability. opencode 1.18.4 loads `*.{ts,js}` under a project's `.opencode/tools/`
// (verified via `opencode debug agent`); the filename becomes the tool name, so
// this registers as the tool `browser`. The `browser-agent` wrapper copies this
// file (plus its sibling `browser_tool_impl.mjs`) into the per-run scratch project
// and denies EVERY other tool (bash included) in the agent def, so the model's
// entire action surface is this one TYPED tool.
//
// SECURITY (PR #180 RCE fix): the model calls this with structured arguments —
// never a shell command string — so there is no `>`/`>>`/`;`/`|`/`$()`/backtick
// surface for the redirect-to-dotfile RCE that the raw bash tool exposed. The tab,
// instance, and domain policy are FORCED by env the wrapper sets; the model cannot
// influence them. All enforcement lives in `browser_tool_impl.mjs` (unit-tested).
import { tool } from "@opencode-ai/plugin"
import { runBrowserOp } from "./browser_tool_impl.mjs"

export default tool({
  description:
    "Read, navigate, or DRIVE your ONE assigned browser tab. Call with a typed " +
    "`op` (no shell; there is NO raw-CDP/command surface). ops: 'text' (visible " +
    "innerText — PREFER THIS), 'html' (raw outerHTML — huge, last resort), 'eval' " +
    "(run a small JS expression, pass `js`), 'nav' (navigate, pass `url`), " +
    "'screenshot', 'frames' (list the tab's frames incl. cross-origin iframes — " +
    "pick one for `frame`), 'click' (TRUSTED click, pass `selector`), 'type' " +
    "(TRUSTED text input, pass `text`, optional `selector`), 'key' (one key: " +
    "Enter/Tab/Escape/Backspace/Delete/Arrow*/Home/End/Page*, optional `selector`), " +
    "'wake' (UN-THROTTLE your background tab so a throttled SPA actually renders " +
    "— does NOT move the user's focus; optional `waitMs` settle. You almost NEVER " +
    "need to call this: a 'text'/'html' read of a hidden tab already wakes and " +
    "re-reads AUTOMATICALLY, and that includes the read right after a " +
    "nav/click/key/eval. Do NOT spend a step on 'wake' — just read, and trust what " +
    "the reply tells you about the wake), " +
    "'whoami' (read-only host/instance/version diagnostics — metadata only), " +
    "'emulate' (put YOUR tab into DEVICE EMULATION so you can check a page at a " +
    "real phone/tablet viewport instead of guessing from a desktop DOM: pass a " +
    "`device` preset (iphone-15, iphone-se, pixel-8, ipad-mini, ipad-mini-2019, " +
    "galaxy-s24) OR raw `width`+`height` (both together) with optional " +
    "`deviceScaleFactor`/`mobile`/`maxTouchPoints`/" +
    "`orientation` (portrait|landscape)/`colorScheme` (light|dark|no-preference). " +
    "You CANNOT set a raw User-Agent or timezone — use a `device` preset, which " +
    "sets a matching UA for you; " +
    "`reset:true` stops emulating. The overrides are re-applied inside every " +
    "later op, so 'text'/'html'/'screenshot' after this see the emulated page — " +
    "but touch-dependent behaviour only becomes real for a document created " +
    "UNDER emulation, so re-'nav' after emulating if the reply says so). " +
    "There is NO 'upload' op: file upload is off by default for the autonomous " +
    "agent. text/html/eval/click/type/key accept `frame` (a frameId or " +
    "url-substring from `frames`) to act INSIDE a cross-origin iframe. You cannot " +
    "choose the tab — it is fixed to the one you were given. Stay on the allowed " +
    "domains.",
  args: {
    // MUST stay in lockstep with ALLOWED_OPS_DEFAULT (browser_tool_impl.mjs), the
    // agent-md capability table, and the README op list — tests/browser_tool.test.mjs
    // parses all four and fails on drift. `upload` is deliberately absent from all
    // of them (operator-only, opt-in via BROWSER_AGENT_ALLOWED_OPS).
    op: tool.schema
      .enum(["text", "html", "eval", "nav", "screenshot",
             "frames", "click", "type", "key", "wake", "context", "emulate",
             "whoami"])
      .describe("the operation to perform on your tab"),
    selector: tool.schema.string().optional()
      .describe("op=text/click/type/key: CSS selector (click target / focus / scope)"),
    url: tool.schema.string().optional()
      .describe("op=nav: the URL to navigate your tab to"),
    js: tool.schema.string().optional()
      .describe("op=eval: a small JS expression to evaluate in the page"),
    text: tool.schema.string().optional()
      .describe("op=type: the text to insert into the focused/selected element"),
    key: tool.schema.string().optional()
      .describe("op=key: one key name (Enter, Tab, Escape, ArrowDown, …)"),
    frame: tool.schema.string().optional()
      .describe("optional: a frameId or url-substring (from op=frames) to act INSIDE that frame"),
    maxBytes: tool.schema.number().optional()
      .describe("op=text: cap the returned text in bytes (default 32768, 0=uncapped)"),
    waitMs: tool.schema.number().optional()
      .describe("op=wake: bounded ms to hold the un-throttle so the page can render (0=no wait)"),
    // op=emulate. Without these fields declared the model literally cannot pass a
    // viewport, so the op would be listed and unusable. Names are the descriptive
    // ones; browser_tool_impl.mjs maps them onto the wire's dsf/ua/tz and enforces
    // protocol.js's bounds before anything is sent.
    device: tool.schema.string().optional()
      .describe("op=emulate: a device preset (iphone-15, iphone-se, pixel-8, ipad-mini, ipad-mini-2019, galaxy-s24)"),
    width: tool.schema.number().optional()
      .describe("op=emulate: viewport width in CSS px (must be paired with height unless a device preset is given)"),
    height: tool.schema.number().optional()
      .describe("op=emulate: viewport height in CSS px (must be paired with width unless a device preset is given)"),
    deviceScaleFactor: tool.schema.number().optional()
      .describe("op=emulate: device pixel ratio, 0.1-10 (default 1)"),
    mobile: tool.schema.boolean().optional()
      .describe("op=emulate: emulate a mobile device (meta viewport, overlay scrollbars, text autosizing)"),
    maxTouchPoints: tool.schema.number().optional()
      .describe("op=emulate: enable touch with this many contact points, 1-16"),
    // `userAgent` and `timezone` are DELIBERATELY ABSENT (operator CLI only) —
    // see EMULATE_OPERATOR_ONLY_FIELDS in browser_tool_impl.mjs for the reasoning
    // (identity a page can read, chosen by a model that reads untrusted pages,
    // and sticky for the whole run so it rides later navs to authenticated
    // sites). A `device` preset sets a matching UA server-side, so mobile testing
    // is unaffected. Passing either is refused: emulation_field_operator_only:<f>.
    // `geo`/`touch` are likewise absent, for the reasons documented there.
    orientation: tool.schema.string().optional()
      .describe("op=emulate: 'portrait' or 'landscape' (landscape also swaps the viewport)"),
    colorScheme: tool.schema.string().optional()
      .describe("op=emulate: prefers-color-scheme — 'light', 'dark' or 'no-preference'"),
    reset: tool.schema.boolean().optional()
      .describe("op=emulate: stop emulating (cannot be combined with any other emulate field)"),
  },
  async execute(args) {
    // runBrowserOp reads the forced tab / instance / domain policy / token from
    // process.env, enforces the op allowlist + domain deny in-process, and POSTs
    // to the loopback bridge. It returns a compact string (or throws a refusal
    // the model sees). Never returns raw huge blobs uninvited (screenshot → note).
    return await runBrowserOp(args)
  },
})
