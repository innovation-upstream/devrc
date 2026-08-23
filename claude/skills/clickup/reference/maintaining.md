# Maintaining the clickup skill — deploy, dependencies, tests

**Load this when:** you are CHANGING this skill (adding a command, touching a
dependency, editing `query.mjs`) · a test failed · an edit to
`~/.claude/skills/clickup/` was rejected as read-only · you added a dependency
and need the `npmDepsHash`.

Nothing here is needed to USE the skill — that is `SKILL.md`.

## Where to edit, and why an edit "does nothing"

Like every skill, this one is **deployed by home-manager from
`~/workspace/devrc/claude/skills/clickup/`**. Edit THERE, then
`home-manager switch --flake ~/workspace/devrc --impure` (or `scripts/ship.sh`
for both hosts).

What lands at `~/.claude/skills/clickup/` is a tree of read-only `/nix/store`
symlinks — editing it directly is impossible, and a `git pull` alone changes
nothing until you switch. 🔴 A **new** file must be `git add`ed or the flake
silently omits it from the deploy: the switch succeeds and the file is just not
there.

## Dependencies

`node_modules` is **BUILT by nix**, not installed:
`~/workspace/devrc/nix/pkgs/clickup-node-modules.nix` materialises it from
`package-lock.json` and links it in at the skill root. To change a dependency,
edit `package.json` + `package-lock.json`, set `npmDepsHash` to `lib.fakeHash`,
build, then copy the `got:` hash back — never guess it.

All mutable state lives in `$XDG_STATE_HOME/clickup` (fallback
`~/.local/state/clickup`), credentials included; a write next to the code is
`EROFS`. Pinned by `test/state-paths.test.mjs`.

## The agent-object stamp (`claw:obj`)

`lib/agent-marker.mjs` is the ONE definition of the marker grammar on this side.
`api/tasks.mjs` applies it in exactly one place — `applyAgentStamp()`, called by
both `createTask` and `createSubtask`, which is every path that creates a ClickUp
task here. **Do not stamp at a call site**: a per-call-site stamp regenerates the
same omission at every new caller, which is the defect this replaced.

🔴 **It is a SECOND implementation of one grammar.** The canonical one is Python,
in another repo — `<talos-infra>/scripts/lib/agent_obj_marker.py` — byte-mirrored
into the in-cluster CronJob producers and drift-gated there. Python and JS cannot share
a byte-mirror, so what keeps these two in step is the `VECTORS` block in
`test/agent-marker.test.mjs` — literal markers, fingerprints and the `cond`
allowlist computed on the **Python** side and pinned here. **Change the grammar in
both, and move the vectors with it.** Never regenerate the vectors from this file:
a self-referential pin agrees with any drift.

Two gotchas worth keeping:

- `fingerprint()` sorts keys **recursively**, matching Python's
  `json.dumps(sort_keys=True)`. `JSON.stringify` preserves insertion order, so a
  shallow sort silently fingerprints two identical claims apart whenever a nested
  object's keys were built in a different order. Mutation-verified: only the
  nested vector catches it.
- `agentIdentity()` **sanitises rather than trusts**, and an unusable
  `CLAW_AGENT_COND` degrades to `manual`, never to silence. `manual` is an honest
  "no machine closes this"; dropping the marker would make the object invisible
  again, which is the whole defect.

## Tests

The hermetic gates are `node:test` suites, run by devrc's node gate
(`bash scripts/run-node-tests.sh .` from the devrc checkout, and
`nix build .#checks.x86_64-linux.nodetests` in CI). Standalone still works:

```bash
node test/help-coverage.test.mjs      # hermetic; pins showUsage() completeness
node test/state-paths.test.mjs        # hermetic; pins state OUT of the skill dir
node test/js-source.test.mjs          # hermetic; controls for the source scanner
node test/awaiting.test.mjs           # hermetic; the awaiting predicate, cap,
                                      # pacing + the inbox cursor loop (fake transport)
node test/smoke-test.mjs --readonly   # live API, needs credentials — NOT in any gate
```

Adding a command is governed by `SKILL.md` → "Finding a command", not restated
here — one rule, one place.
