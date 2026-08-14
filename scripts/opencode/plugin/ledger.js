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
// structure.
//
// 🔴 THE COST, MEASURED RATHER THAN ASSUMED, and an earlier version of this
// comment was wrong twice. It said "~9 ms per tool call that is not throttled":
// 9 ms is a BARE interpreter start, and the throttle lives INSIDE the Python
// process — so every `tool.execute.after` paid a full spawn, measured at
// **24.5 ms** of synchronously blocked node event loop, on top of
// `activity-plugin.js`'s own `execFileSync` on the same event.
//
// So the throttle is memoised HERE, in front of the spawn. This is the only
// change that actually removes it: `lastWrite` holds one timestamp per session,
// and a repeat inside the interval returns without touching the process table.
// It is a CACHE, not logic — the Python throttle stays authoritative and is
// still consulted on every call that gets through, so the worst a wrong memo
// can do is skip a write the writer would have skipped anyway. Keyed on the
// session, never on the pane, for the same reason the Python rule is: a
// different session taking the pane over must claim it immediately.
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

// Must match `agent_ledger.DEFAULT_THROTTLE`. It is a memo in front of the real
// rule rather than a second copy of it: too LOW only costs a spawn the Python
// side then skips, and too HIGH is bounded by the same interval the writer uses.
const THROTTLE_MS = 30 * 1000;

// sessionID -> Date.now() of the last spawn. Module scope, so it lives as long
// as the opencode process — which is exactly the window in which repeat tool
// calls happen.
const lastWrite = new Map();

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

      // 🔴 BEFORE `resolveModule()` and before the spawn — the whole point.
      // A `stat` and a Map lookup instead of a 24.5 ms subprocess.
      const now = Date.now();
      const previous = lastWrite.get(session);
      if (previous !== undefined && now - previous < THROTTLE_MS) return;

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
          // Reaping is bounded here BECAUSE of the memo above: this runs at
          // most once per session per interval, not once per tool call. Writer
          // 1 prunes on its session boundaries; without this a host running
          // opencode and NOT Claude Code would grow the ledger without bound —
          // the fuzzyclaw rot (401 files, ~90% stale) the module cites.
          "--prune",
        ],
        { stdio: "ignore", timeout: 5000, shell: false },
      );
      // Recorded only on a spawn that RETURNED. A throw leaves the map
      // untouched, so a transient failure retries on the next call rather than
      // being memoised into a 30 s hole.
      lastWrite.set(session, now);
    } catch {
      // swallow — a ledger write must never fail a tool call
    }
  },
});
