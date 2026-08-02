// guard.js — opencode plugin: the DETERMINISTIC half of the bash permission
// system. Deployed by nix/home.nix to ~/.config/opencode/plugin/guard.js.
//
// 🔴 WHY THIS EXISTS — read scripts/opencode/README.md ("two layers") and
// scripts/claude-hooks/guard_core.py's docstring before changing anything here.
//
// opencode's `permission.bash` block matches GLOB PATTERNS against a command
// node's full text. Two rounds of patching those patterns each closed the
// spellings we thought of and left the ones we did not — measured at c1e4c02,
// `talosctl -n <ip> reset` (a node wipe) resolved ALLOW because the ask/deny
// pattern required the tool and the verb to be ADJACENT. Globs cannot express
// "this command wipes a node"; the set of spellings is unbounded. So the hard
// denies moved to guard_core.py, which TOKENISES: it splits on `;`/`&&`/`||`/
// `|`/`&`, strips `VAR=…` prefixes and sudo/doas/env/timeout wrappers, recurses
// into `bash -c '…'`, and reasons about argv.
//
// The globs still do a real job — broad `ask` for FRICTION on mutation-ish
// families — but they are no longer the only thing between an agent and an
// irreversible action.
//
// 🔴 WHY `tool.execute.before` AND NOT `permission.ask` (measured on opencode
// 1.18.4, this host, 2026-08-02):
//   * `permission.ask` IS in the Hooks type and its `output.status` is typed
//     `"ask" | "deny" | "allow"`, so returning an *ask* decision LOOKS
//     expressible. It is not, for a guard: the hook never fired in any probe —
//     not on the allow path, and not on the ask path either (a `*probe-beta*:
//     ask` rule under `opencode run` printed "auto-rejecting" without the hook
//     logging a single line). A hook that does not run cannot upgrade an
//     allow into an ask, which is the one thing a guard would need it for.
//   * `tool.execute.before` DID fire on every bash call, and THROWING from it
//     hard-blocks the call: opencode reports the thrown message to the model as
//     a tool error and the command never runs (verified — the model then tried
//     `printf`, `type echo` and a `PROBE_THROW='' …` re-spelling, and every
//     matching variant was blocked).
//   So: DENY is expressible here, ASK is not. Ask-grade families stay in
//   opencode.jsonc as globs. Globs are acceptable for friction and unacceptable
//   as the only thing guarding an irreversible action.
//
// 🔴 FAILS CLOSED. If python is missing, the core is unreadable, the subprocess
// times out, or the output is unparseable, this throws. A guard that silently
// degrades to "allow" is worse than no guard: it reports safety it is not
// providing.
//
// COST: one python3 subprocess per bash call. Measured 26 ms average over 10
// runs on a representative command on this host — a rounding error next to a
// model round-trip, and `spawnSync` keeps the ordering unambiguous (no chance
// of the tool starting before the verdict lands).
//
// Test seams: DEVRC_GUARD_CORE (path to guard_core.py), DEVRC_GUARD_PYTHON
// (interpreter), DEVRC_GUARD_POLICY (policy name), DEVRC_GUARD_DISABLE=1.

import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

// guard_core.py is deployed NEXT TO the config dir (nix/home.nix links it to
// ~/.config/opencode/guard_core.py from the same source file bash-guard.py
// imports). Resolving it relative to this module keeps opencode's guard from
// depending on ~/.claude/ — one source file, two independent deployments.
const CORE =
  process.env.DEVRC_GUARD_CORE ||
  fileURLToPath(new URL("../guard_core.py", import.meta.url));
const PYTHON = process.env.DEVRC_GUARD_PYTHON || "python3";
const POLICY = process.env.DEVRC_GUARD_POLICY || "opencode";

export const GuardPlugin = async () => ({
  "tool.execute.before": async (input, output) => {
    if (input.tool !== "bash") return;
    if (process.env.DEVRC_GUARD_DISABLE === "1") return;
    const command = output?.args?.command;
    if (typeof command !== "string" || command === "") return;

    let res;
    try {
      res = spawnSync(PYTHON, [CORE, "--policy", POLICY], {
        input: JSON.stringify({ command }),
        encoding: "utf8",
        timeout: 10000,
      });
    } catch (e) {
      throw new Error(
        `bash guard could not run (${e}). Refusing the command rather than ` +
          `running it unchecked. Fix: ${PYTHON} must be on PATH and ${CORE} readable.`,
      );
    }
    if (res.error || res.status !== 0 || !res.stdout) {
      throw new Error(
        `bash guard failed (status=${res.status}, error=${res.error}, ` +
          `stderr=${(res.stderr || "").slice(0, 300)}). Refusing the command ` +
          `rather than running it unchecked.`,
      );
    }
    let verdict;
    try {
      verdict = JSON.parse(res.stdout);
    } catch {
      throw new Error(
        `bash guard emitted unparseable output (${res.stdout.slice(0, 200)}). ` +
          `Refusing the command rather than running it unchecked.`,
      );
    }
    if (verdict.decision === "deny") {
      throw new Error(`BLOCKED by the devrc bash guard: ${verdict.reason}`);
    }
    if (verdict.decision !== "allow") {
      throw new Error(
        `bash guard returned an unknown decision ${JSON.stringify(verdict)}. ` +
          `Refusing the command rather than running it unchecked.`,
      );
    }
  },
});
