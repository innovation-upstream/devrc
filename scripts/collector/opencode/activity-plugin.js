#!/usr/bin/env node
// activity-plugin.js — OpenCode plugin that emits activity telemetry events
// to the activity collector pipeline via the `emit` CLI.
//
// DEPLOYMENT: home-manager, `home.file.".config/opencode/plugin/activity.js"`
// in nix/home.nix — the same declarative mechanism as guard.js and env.js.
// There is deliberately no deploy script: a hand-run one left the laptop
// without the plugin for its entire existence (2026-07-29 → 2026-08-02, zero
// kind=tool-call rows ever recorded from that host).
//
// opencode's plugin glob is `{plugin,plugins}/*.{ts,js}` — non-recursive, both
// the singular and plural directory, `.mjs` will NOT load. We deploy to the
// SINGULAR `plugin/` only; a copy in `plugins/` would load a SECOND time and
// double-emit every event.

import { execFileSync } from "child_process";
import { readFileSync, writeFileSync, mkdirSync, existsSync } from "fs";
import { join, dirname } from "path";
import { homedir } from "os";

// --------------------------------------------------------------------------- #
// Constants
// --------------------------------------------------------------------------- #

const STATE_DIR = join(
  process.env.XDG_STATE_HOME || join(homedir(), ".local", "state"),
  "activity"
);
const STATE_FILE = join(STATE_DIR, "opencode-plugin-state.json");
const MAX_SEEN = 1000;
const MAX_TEXT_LEN = 4096;
const MAX_ARGS_SUMMARY = 200;

// Sentinel written to `text` when the tool NAME could not be read off the hook
// input. It must NOT be a plausible tool name: `unknown` was, and 2,699 rows
// (100% of source=opencode kind=tool-call on the workbench, measured 2026-08-02)
// carried it because the plugin read `input.tool.name` while the OpenCode hook
// contract passes `input.tool` as a STRING. A capture failure has to be
// distinguishable from a genuinely-unnamed tool, so the sentinel is namespaced
// and the payload carries a machine-readable reason + the raw shape we saw.
const NAME_CAPTURE_FAILED = "__name_capture_failed__";
const MAX_INFLIGHT_CALLS = 256;

// 🔴 DO NOT ADD A NAMED EXPORT TO THIS FILE. `ActivityPlugin` must be the ONLY
// one. opencode's loader iterates EVERY named export of a plugin module and
// (a) throws "Plugin export is not a function" for any non-function — which
// aborts the WHOLE module, so one stray `export const` silently disables all
// telemetry — and (b) CALLS every function export as a plugin factory with
// { client, project, worktree, directory, experimental_workspace, serverUrl, $ }.
// Both measured on opencode 1.18.4, 2026-08-02, via probe plugins in
// ~/.config/opencode/plugin/ (see tests/test_activity_plugin_exports.py).
//
// That is exactly how #298 killed this plugin: it added
// `export const _internals = {...}` plus three exported helpers, and emission
// stopped dead at the moment ship.sh deployed it. Helpers stay module-private;
// if a test needs one, hang it off ActivityPlugin rather than exporting it.

// --------------------------------------------------------------------------- #
// State management (ring buffer of seen message IDs)
// --------------------------------------------------------------------------- #

function loadState() {
  try {
    const raw = readFileSync(STATE_FILE, "utf-8");
    const data = JSON.parse(raw);
    if (data && data.version === 1 && Array.isArray(data.seen)) {
      return data.seen;
    }
  } catch {
    // corrupt or missing — start fresh
  }
  return [];
}

function saveState(seen) {
  try {
    mkdirSync(dirname(STATE_FILE), { recursive: true });
    // Ring buffer: keep last MAX_SEEN entries
    const trimmed = seen.slice(-MAX_SEEN);
    writeFileSync(
      STATE_FILE,
      JSON.stringify({ version: 1, seen: trimmed }),
      "utf-8"
    );
  } catch {
    // best-effort, never crash
  }
}

// --------------------------------------------------------------------------- #
// Emit helper
// --------------------------------------------------------------------------- #

function resolveEmitPath() {
  // 1. Env var override
  if (process.env.EMIT_PATH) {
    return process.env.EMIT_PATH;
  }
  // 2. Deployed location
  const deployed = join(homedir(), ".config", "activity-collector", "emit");
  if (existsSync(deployed)) {
    return deployed;
  }
  // 3. Relative to this file (dev location: scripts/collector/emit)
  const here = dirname(new URL(import.meta.url).pathname);
  const dev = join(here, "..", "..", "emit");
  if (existsSync(dev)) {
    return dev;
  }
  // 4. Fallback: assume on PATH
  return "emit";
}

function emitEvent({ kind, text, project, cwd, session, app, payload }) {
  try {
    const emit = resolveEmitPath();
    const args = ["source=opencode", `kind=${kind}`];

    if (text != null) {
      args.push(`b64:text=${String(text)}`);
    }
    if (project != null) {
      args.push(`b64:project=${String(project)}`);
    }
    if (cwd != null) {
      args.push(`b64:cwd=${String(cwd)}`);
    }
    if (session != null) {
      args.push(`b64:session=${String(session)}`);
    }
    if (app != null) {
      args.push(`b64:app=${String(app)}`);
    }
    if (payload != null) {
      const p = typeof payload === "string" ? payload : JSON.stringify(payload);
      args.push(`b64:payload=${p}`);
    }

    // 🔴 execFileSync with an ARGV ARRAY — never execSync with a joined string.
    // The old `execSync(\`${emit} ${args.join(" ")}\`)` ran the arguments through
    // a shell, so every value was word-split on spaces and had its quoting
    // characters EATEN before `emit` ever saw it. A payload of
    //   {"duration_ms":0,"success":true,"args_summary":"{\"name\":\"x\"}"}
    // reached emit as the single mangled token
    //   {duration_ms:0,success:true,args_summary:{"name":"x"}}
    // (not valid JSON), and any `text`/`cwd` containing a space was split into
    // several bogus key=value args. execFileSync passes argv straight to
    // execve(2) — no shell, no splitting, no quote removal.
    execFileSync(emit, args, {
      stdio: "ignore",
      timeout: 5000,
      shell: false,
    });
  } catch {
    // swallow — never crash OpenCode
  }
}

// --------------------------------------------------------------------------- #
// Extract text from message parts
// --------------------------------------------------------------------------- #

function extractText(msg) {
  if (!msg) return "";
  const parts = msg.parts || msg.content;
  if (!parts) return "";
  if (typeof parts === "string") return parts;
  if (!Array.isArray(parts)) return "";
  return parts
    .filter((p) => p && p.type === "text")
    .map((p) => p.text || "")
    .join("\n")
    .trim();
}

// --------------------------------------------------------------------------- #
// Tool-call field extraction
// --------------------------------------------------------------------------- #

/** Read the tool NAME off a `tool.execute.{before,after}` hook input.
 *
 * The OpenCode plugin contract (@opencode-ai/plugin, dist/index.d.ts) is:
 *     "tool.execute.after"?: (input: { tool: string, sessionID: string,
 *                                      callID: string, args: any }, output) => …
 * `input.tool` is a plain STRING. The original code read `input.tool.name`,
 * which is `undefined` on a string, then fell through to the literal
 * `"unknown"` — so the name was lost on 100% of events.
 *
 * Returns { name, ok, shape }. `ok=false` means capture FAILED (callers must
 * record that distinguishably); `shape` is the typeof/ctor we actually saw, so
 * a future contract change is diagnosable from the row itself.
 */
function extractToolName(input) {
  const t = input?.tool;
  if (typeof t === "string" && t.length > 0) {
    return { name: t, ok: true, shape: "string" };
  }
  // Tolerate an object form in case the contract ever moves back to `{ name }`.
  if (t && typeof t === "object" && typeof t.name === "string" && t.name) {
    return { name: t.name, ok: true, shape: "object.name" };
  }
  if (typeof input?.name === "string" && input.name) {
    return { name: input.name, ok: true, shape: "input.name" };
  }
  return {
    name: NAME_CAPTURE_FAILED,
    ok: false,
    shape: t === undefined ? "undefined" : Array.isArray(t) ? "array" : typeof t,
  };
}

/** Serialize the tool arguments to a BOUNDED JSON string.
 *
 * `args_summary` must stay valid JSON after truncation, so we do NOT slice a
 * JSON string mid-token (which is what produced unparseable payloads). We
 * serialize, and if the result is over budget we replace it with a structured
 * marker that names the keys and reports the dropped size.
 */
function summarizeArgs(args) {
  if (args == null) return { args_summary: null, args_truncated: false };
  let s;
  try {
    s = JSON.stringify(args);
  } catch {
    return { args_summary: null, args_truncated: false, args_unserializable: true };
  }
  if (typeof s !== "string") return { args_summary: null, args_truncated: false };
  if (s.length <= MAX_ARGS_SUMMARY) {
    return { args_summary: s, args_truncated: false };
  }
  const keys =
    args && typeof args === "object" && !Array.isArray(args) ? Object.keys(args) : [];
  return {
    args_summary: JSON.stringify({ _truncated: true, keys, bytes: s.length }),
    args_truncated: true,
  };
}

// --------------------------------------------------------------------------- #
// Plugin export
// --------------------------------------------------------------------------- #

export const ActivityPlugin = async ({ project, directory }) => {
  let currentSession = null;
  const seen = loadState();
  let dirty = false;
  // callID → Date.now() at `tool.execute.before`, so `.after` can report a real
  // duration. Bounded (insertion-ordered Map, oldest evicted) so an abandoned
  // call can never leak memory in a long-lived OpenCode process.
  const callStarts = new Map();

  return {
    // --- Session lifecycle ---
    "session.created": async (_input, output) => {
      try {
        currentSession = output?.session?.id || null;
        const title = output?.session?.title || "";
        emitEvent({
          kind: "session-create",
          text: title,
          project: project?.name || "",
          cwd: directory || "",
          session: currentSession,
          app: "opencode",
          payload: {
            agent: output?.session?.agent || "",
            model: output?.session?.model || "",
            title,
          },
        });
      } catch {
        // best-effort
      }
    },

    // --- User messages ---
    "message.updated": async (input, _output) => {
      try {
        const msg = input?.message || input;
        if (!msg) return;
        if (msg.role !== "user") return;

        const id = msg.id;
        if (!id) return;
        if (seen.includes(id)) return;

        const text = extractText(msg);
        if (!text || text.length < 2) return;

        const truncated =
          text.length > MAX_TEXT_LEN ? text.slice(0, MAX_TEXT_LEN) : text;
        const kind = truncated.startsWith("/") ? "command" : "prompt";

        emitEvent({
          kind,
          text: truncated,
          project: project?.name || "",
          cwd: directory || "",
          session: currentSession,
          app: "opencode",
          payload: { role: "user", messageId: id },
        });

        seen.push(id);
        dirty = true;
        if (seen.length > MAX_SEEN) {
          saveState(seen);
          dirty = false;
        }
      } catch {
        // best-effort
      }
    },

    // --- Tool calls ---
    // `tool.execute.before` only records a start time so `.after` can report a
    // REAL duration. The hook `output` carries {title, output, metadata} — it
    // has no duration and no error field, so the old code's `output.duration_ms`
    // was always undefined (→ 0) and `success` was always hard-coded true.
    "tool.execute.before": async (input, _output) => {
      try {
        const id = input?.callID;
        if (!id) return;
        callStarts.set(id, Date.now());
        // Bound the map — a crashed/never-completed call must not leak.
        if (callStarts.size > MAX_INFLIGHT_CALLS) {
          const oldest = callStarts.keys().next().value;
          callStarts.delete(oldest);
        }
      } catch {
        // best-effort
      }
    },

    "tool.execute.after": async (input, output) => {
      try {
        const name = extractToolName(input);
        const args = summarizeArgs(input?.args);

        const id = input?.callID;
        const startedAt = id ? callStarts.get(id) : undefined;
        if (id) callStarts.delete(id);
        // duration is only known when we saw the matching `.before`; null
        // (absent) is honest, 0 would be a measurement claim we cannot make.
        const durationMs =
          startedAt === undefined ? null : Math.max(0, Date.now() - startedAt);

        const payload = {
          // `tool.execute.after` fires only on a COMPLETED call — OpenCode
          // throws past it on tool failure — so this hook cannot observe an
          // error. Say that, rather than asserting success:true on every row.
          outcome: "completed",
          name_captured: name.ok,
          ...(name.ok ? {} : { name_capture_shape: name.shape }),
          ...(durationMs === null ? {} : { duration_ms: durationMs }),
          ...args,
          call_id: id || null,
        };

        emitEvent({
          kind: "tool-call",
          text: name.name,
          project: project?.name || "",
          cwd: directory || "",
          // sessionID is on the hook input; `currentSession` is only set by the
          // session.created handler, which the plugin contract never calls.
          session: input?.sessionID || currentSession,
          app: "opencode",
          payload,
        });
      } catch {
        // best-effort
      }
    },

    // --- Session end ---
    "session.idle": async (_input, _output) => {
      try {
        emitEvent({
          kind: "session-idle",
          text: "",
          project: project?.name || "",
          cwd: directory || "",
          session: currentSession,
          app: "opencode",
        });
        currentSession = null;
      } catch {
        // best-effort
      }
    },

    // --- Cleanup: persist state if dirty ---
    _cleanup: () => {
      if (dirty) {
        saveState(seen);
        dirty = false;
      }
    },
  };
};
