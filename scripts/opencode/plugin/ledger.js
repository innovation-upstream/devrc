// Writer 2 of the agent activity ledger — opencode, local, in tmux.
//
// WHY: `session-manager` puts an AGE, a `stale` bucket and a session id on a
// row from a ledger record. Writer 1 (`scripts/claude-hooks/agent-ledger-hook.py`)
// covers Claude Code; without this, every opencode window is a row with no age
// and no session id — the #419 shape, just narrowed to one runtime.
// Spec: claudedocs/spec-agent-activity-ledger.md §3, "Writer 2 — opencode".
//
// 🔴 THIS FILE HOLDS NO SCHEMA. It shells out to `agent_ledger.py --write`,
// which is the same module `session-manager` reads the record shape from. A
// JavaScript re-implementation of the record is how writer 2 drifts from writer 1
// and from the reader while all three look correct — the failure the module's own
// docstring calls out. Same shape `guard.js` uses for `guard_core.py` and
// `activity-plugin.js` for its emit script: the JS carries arguments, never
// structure. It costs one interpreter start (~9 ms measured) per tool call that
// is not throttled, and that is the price of there being one definition.
//
// 🔴 `tool.execute.AFTER`, never `.before`. `.before` is where `guard.js` THROWS
// to block a command; a second handler on that event that can fail is a way to
// break the guard's gate. `.after` fires on a completed call and gates nothing.
//
// 🔴 IT MUST NEVER THROW. opencode carries a plugin's exception up; a ledger
// write is not worth a failed tool call, and the worst case of losing one is a
// row rendering `unknown` instead of `idle` for up to one throttle interval.
// Every path here is inside a try/catch that swallows, exactly like
// `activity-plugin.js`.
//
// 🔴 NOT MERGED INTO activity-plugin.js, though it has the same hook and the
// same identity. That plugin spools telemetry to ClickHouse; this one writes
// local state `session-manager` joins on. Different consumer, different
// retention, different failure mode — and a bug in either would otherwise take
// out the other.
import { execFileSync } from "node:child_process";
import { existsSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const PYTHON = process.env.DEVRC_LEDGER_PYTHON || "python3";

function moduleRelativeModule() {
  try {
    return fileURLToPath(new URL("../agent_ledger.py", import.meta.url));
  } catch {
    return null;
  }
}

// Resolved PER CALL, not once at module load — same reasoning as `guard.js`:
// resolving at load time freezes a path taken before a `home-manager switch`
// mid-session, and the cost is a couple of stat()s.
function resolveModule() {
  const override = process.env.DEVRC_LEDGER_MODULE;
  if (override) return existsSync(override) ? override : null;
  const candidates = [
    join(homedir(), ".config", "opencode", "agent_ledger.py"),
    join(homedir(), ".claude", "hooks", "agent_ledger.py"),
    join(homedir(), "workspace", "devrc", "scripts", "lib", "agent_ledger.py"),
    moduleRelativeModule(),
  ].filter(Boolean);
  for (const c of candidates) {
    if (existsSync(c)) return c;
  }
  return null;
}

export const LedgerPlugin = async () => ({
  "tool.execute.after": async (input, _output) => {
    try {
      if (process.env.DEVRC_LEDGER_DISABLE === "1") return;
      const session = input?.sessionID;
      // No session id means no ClickHouse join and no row identity — the record
      // would be the hollow one `build_record` refuses. Skip rather than write it.
      if (typeof session !== "string" || session === "") return;

      const mod = resolveModule();
      // 🔴 Absent module => SILENTLY skip. Unlike the guard, which refuses the
      // command rather than run it unchecked, a missing ledger writer is not a
      // safety question: the honest outcome is a row with no age, which the
      // reader already renders as "no writer has recorded this window".
      if (!mod) return;

      execFileSync(
        PYTHON,
        [
          mod,
          "--write",
          "--runtime", "opencode",
          "--session", session,
        ],
        { stdio: "ignore", timeout: 5000, shell: false },
      );
    } catch {
      // swallow — a ledger write must never fail a tool call
    }
  },
});
