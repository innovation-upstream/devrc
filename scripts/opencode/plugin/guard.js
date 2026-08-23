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
// 1.18.4, this host, 2026-08-02 — and STILL 1.18.4 on purpose: the 2026-08-13
// re-derivation to 1.18.16, and the 2026-08-19 one after it, covered resolved
// permissions, tool sets and resolver ordering, but NOT hook firing, which needs
// a running hook to observe. Last confirmed on 1.18.4; see PINNED_VERSION in scripts/tests/test_opencode_engine.py):
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
// 🔴 FAILS CLOSED. If the core cannot be FOUND, python is missing, the core is
// unreadable, the subprocess times out, or the output is unparseable, this
// throws — naming the paths it tried. A guard that silently degrades to "allow"
// is worse than no guard: it reports safety it is not providing. (Fail-closed
// is also why a path bug here is a TOTAL outage rather than a silent hole — see
// the resolution comment below, which is the bug that actually shipped.)
//
// COST: one python3 subprocess per bash call. Measured 26 ms average over 10
// runs on a representative command on this host — a rounding error next to a
// model round-trip, and `spawnSync` keeps the ordering unambiguous (no chance
// of the tool starting before the verdict lands).
//
// Test seams: DEVRC_GUARD_CORE (exact path to guard_core.py — an override, NOT
// a hint: when set it is the ONLY candidate), DEVRC_GUARD_PYTHON (interpreter),
// DEVRC_GUARD_POLICY (policy name), DEVRC_GUARD_DISABLE=1.

import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

// 🔴 HOW guard_core.py IS LOCATED — and why NOT relative to this module.
//
// This file used to do, and ONLY do:
//     fileURLToPath(new URL("../guard_core.py", import.meta.url))
// on the reasoning that it lives at ~/.config/opencode/plugin/guard.js, so `..`
// is ~/.config/opencode/ — exactly where nix/home.nix links the core.
//
// That reasoning is about the DEPLOY path, and the deploy path is a SYMLINK.
// home-manager's `home.file` links ~/.config/opencode/plugin/guard.js into the
// nix store, and node resolves `import.meta.url` through the symlink to the REAL
// store path. The store is FLAT — the file lands as a single
// /nix/store/<hash>-hm_guard.js, with no `plugin/` directory above it. MEASURED
// on this host:
//
//     readlink -f ~/.config/opencode/plugin/guard.js
//       -> /nix/store/5m6y63cj512ksn783j5nddlrchkca92p-hm_guard.js
//     import.meta.url dir : /nix/store
//     ../guard_core.py    -> /nix/guard_core.py          ← does not exist
//
// So the guard failed closed on EVERY bash call, with
// "python3: can't open file '/nix/guard_core.py'". The logic was correct; only
// the path was wrong. This is the hazard RULES.md names for home-manager-managed
// dotfiles ("`readlink -f` is the arbiter") applied to a program reasoning about
// its OWN location: a store-resolved `import.meta.url` invalidates every
// relative-path assumption, and nothing about the source file reveals that.
//
// The fix resolves the core INDEPENDENTLY of where this module happens to sit:
//
//   1. DEVRC_GUARD_CORE, if set — an EXPLICIT override, used exactly as given
//      with NO fallback. Silently ignoring a wrong override and quietly checking
//      some other file is worse than failing: the operator would believe they
//      had pointed the guard at their file.
//   2. $HOME/.config/opencode/guard_core.py — the home-manager deploy target
//      (itself a store symlink, which is fine: we open it, we don't `..` off it).
//   3. `../guard_core.py` relative to this module — LAST RESORT, so a plain
//      (non-home-manager) checkout that keeps the two files in their repo layout
//      still works. It cannot weaken anything: it is only ever reached when 2
//      does not exist, and a candidate is used only if it EXISTS.
//
// If none exists we throw, naming every path tried. Still fail-closed.
function moduleRelativeCore() {
  try {
    return fileURLToPath(new URL("../guard_core.py", import.meta.url));
  } catch {
    return null;
  }
}

function coreCandidates() {
  const override = process.env.DEVRC_GUARD_CORE;
  if (override) return [override];
  return [
    join(homedir(), ".config", "opencode", "guard_core.py"),
    moduleRelativeCore(),
  ].filter(Boolean);
}

// Resolved per call, not once at module load. The cost is a couple of stat()s
// against a ~26 ms python subprocess, and resolving at load time would freeze a
// verdict taken before a `home-manager switch` mid-session. 🔴 It must also NOT
// throw at import time: a plugin that fails to LOAD is not a plugin that denies
// — opencode would carry on without the hook, which is fail-OPEN. The throw
// belongs inside `tool.execute.before`, where it hard-blocks the call.
function resolveCore() {
  const tried = coreCandidates();
  for (const candidate of tried) {
    if (existsSync(candidate)) return { core: candidate, tried };
  }
  return { core: null, tried };
}

const PYTHON = process.env.DEVRC_GUARD_PYTHON || "python3";
const POLICY = process.env.DEVRC_GUARD_POLICY || "opencode";

export const GuardPlugin = async () => ({
  "tool.execute.before": async (input, output) => {
    if (input.tool !== "bash") return;
    if (process.env.DEVRC_GUARD_DISABLE === "1") return;
    const command = output?.args?.command;
    if (typeof command !== "string" || command === "") return;

    const { core: CORE, tried } = resolveCore();
    if (!CORE) {
      throw new Error(
        `bash guard cannot find guard_core.py — tried: ${tried.join(", ")}. ` +
          `Refusing the command rather than running it unchecked. Fix: run ` +
          `\`home-manager switch\` (it deploys ~/.config/opencode/guard_core.py ` +
          `from devrc/scripts/claude-hooks/guard_core.py), or point ` +
          `DEVRC_GUARD_CORE at the file.`,
      );
    }

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
