#!/usr/bin/env node
// activity-plugin.js — OpenCode plugin that emits activity telemetry events
// to the activity collector pipeline via the `emit` CLI.
//
// Placed in ~/.config/opencode/plugins/ (symlinked by deploy-plugin.sh).
// OpenCode loads plugins from that directory and calls the exported async
// handler factory with { project, client, directory, $ }.

import { execSync } from "child_process";
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

    execSync(`${emit} ${args.join(" ")}`, {
      stdio: "ignore",
      timeout: 5000,
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
// Plugin export
// --------------------------------------------------------------------------- #

export const ActivityPlugin = async ({ project, directory }) => {
  let currentSession = null;
  const seen = loadState();
  let dirty = false;

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
    "tool.execute.after": async (input, output) => {
      try {
        const toolName = input?.tool?.name || input?.name || "unknown";
        const argsStr = input?.args
          ? JSON.stringify(input.args).slice(0, MAX_ARGS_SUMMARY)
          : "";
        const durationMs =
          output?.duration_ms || output?.durationMs || 0;
        const success = output?.error ? false : true;

        emitEvent({
          kind: "tool-call",
          text: toolName,
          project: project?.name || "",
          cwd: directory || "",
          session: currentSession,
          app: "opencode",
          payload: {
            duration_ms: durationMs,
            success,
            args_summary: argsStr,
          },
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
