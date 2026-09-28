// base-clone-freshness.js — opencode plugin: refresh the primary clone's context
// files (CLAUDE.md, AGENTS.md, skills, hooks) from origin/main once per session.
//
// WHY THIS EXISTS. Claude Code sessions in this repo get a SessionStart hook
// (scripts/claude-hooks/base-clone-staleness.sh) that fetches origin and checks
// out refreshed copies of the context-loading files into the primary clone's
// working tree. opencode sessions get NO automatic refresh — the base clone
// serves stale instruction files into agent context, and the stale instructions
// are authoritative-looking. This plugin closes that gap.
//
// The Claude Code hook remains the richer harness: it reports into agent context
// as a system message and handles pruning of deleted-upstream paths. This plugin
// only fixes the tree and logs; nothing is surfaced into the conversation.
//
// --------------------------------------------------------------------------- #
// 🔴 `shell.env` BLAST RADIUS. This hook fires in the bash tool's PRE-SPAWN
// critical path — every bash tool call goes through it. The hook is
// fire-and-forget (spawn + unref, never awaited), so a stuck script or a hung
// network cannot wedge the bash tool. The 60-second timeout is a backstop beyond
// the script's own 12-second network bound.
//
// --------------------------------------------------------------------------- #
// 🔴 PTY PATH caveat. The pty path fires `shell.env` with `{cwd}` ONLY — no
// sessionID. When sessionID is missing we do nothing (cannot throttle
// meaningfully, and the pty path is not the critical one for freshness).
//
// --------------------------------------------------------------------------- #
// 🔴 Once-per-session semantics. A module-level Set tracks sessionIDs that have
// triggered a refresh. A session that hops repos refreshes only the repo of its
// first bash call — acceptable, documented: the script guards itself against
// running in a linked worktree (exit 0) and against absent upstream (exit 0), so
// a second repo that is not the primary clone is simply skipped.
//
// --------------------------------------------------------------------------- #
// DEPLOYMENT CONSTRAINTS (same as guard.js / ledger.js / session-env.js): the
// plugin glob is `{plugin,plugins}/*.{ts,js}` — NON-RECURSIVE, `.js`/`.ts` only.
// It must land directly at ~/.config/opencode/plugin/base-clone-freshness.js,
// and NEVER also in `plugins/` (plural), which the glob also reads — a copy in
// each loads the plugin twice.

import { spawn } from "node:child_process";
import { homedir } from "node:os";
import { join } from "node:path";

// Track which sessions have already triggered a refresh.
const refreshedSessions = new Set();

export const BaseCloneFreshnessPlugin = async () => ({
  "shell.env": async (input, output) => {
    try {
      // 🔴 The pty path fires this hook with `{cwd}` only — no sessionID.
      // Cannot throttle meaningfully without one; skip.
      const id = input && input.sessionID;
      if (typeof id !== "string" || id === "") return;

      // 🔴 Once per session — if we already triggered a refresh for this
      // sessionID, do nothing. The Set is module-level and persists for the
      // lifetime of the opencode process.
      if (refreshedSessions.has(id)) return;
      refreshedSessions.add(id);

      // 🔴 Honour BASE_CLONE_NO_REFRESH=1: skip even spawning.
      if (process.env.BASE_CLONE_NO_REFRESH === "1") return;

      // Resolve the deployed staleness script. It lives at the same level as
      // guard_core.py: $HOME/.config/opencode/base-clone-staleness.sh.
      const script = join(homedir(), ".config", "opencode", "base-clone-staleness.sh");

      // 🔴 Fire-and-forget: spawn + unref, never awaited. This sits in the bash
      // tool's pre-spawn critical path and must never run synchronously here.
      // Use `bash` explicitly so it works regardless of the executable bit.
      const child = spawn("bash", [script], {
        detached: true,
        stdio: "ignore",
        timeout: 60000,
      });
      child.unref();
    } catch {
      // best-effort — never break the bash tool
    }
  },
});