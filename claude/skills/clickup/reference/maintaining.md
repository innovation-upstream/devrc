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

## Tests

The hermetic gates are `node:test` suites, run by devrc's node gate
(`bash scripts/run-node-tests.sh .` from the devrc checkout, and
`nix build .#checks.x86_64-linux.nodetests` in CI). Standalone still works:

```bash
node test/help-coverage.test.mjs      # hermetic; pins showUsage() completeness
node test/state-paths.test.mjs        # hermetic; pins state OUT of the skill dir
node test/js-source.test.mjs          # hermetic; controls for the source scanner
node test/smoke-test.mjs --readonly   # live API, needs credentials — NOT in any gate
```

**Adding a command? Add it to `showUsage()`** — `test/help-coverage.test.mjs`
fails if any dispatchable command is missing from the help, or if any printed
command cannot dispatch. That test is why `SKILL.md` does not list commands.
