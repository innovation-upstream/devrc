# clawgate — the browser extension: delivery, reload, and verifying a build

Read when: you changed the clawgate browser extension, or you need to know **which build is actually
loaded** in Zach's Brave.

## 🔴 The extension does NOT ship via Flux — merging to `trunk` deploys NOTHING

Brave loads it **unpacked, in place, from a git checkout**. Deploying it means *advancing that
checkout* and reloading Brave. It runs on **two hosts**, at the **same path on both**:

```
~/workspace/clawgate-extension/containers/clawgate/extension
```

That path is a **linked worktree** of the host's `homelab-talos` clone, on a local branch
`clawgate-ext-local` tracking `origin/trunk`. Hosts: **workbench** (this host / `192.168.50.250`)
and **laptop** (`ssh zach@192.168.50.155`). Both must be updated — doing one leaves the other on the
old build.

A worktree is used deliberately, **not** the `homelab-talos` base clone. The base clone is
permanently dirty and chronically behind (14 commits on 2026-08-12), so loading it directly makes
the deployed extension hostage to that drift — and it cannot be fast-forwarded while its WIP
collides. The worktree advances with a plain `merge --ff-only` regardless of what the base clone is
doing.

## 🔴 Brave has MULTIPLE PROFILES, and each loads extensions independently

Checking one profile proves nothing about the others. On 2026-08-12 the workbench had **4 profiles,
2 of them active** (`Default` + `Profile 2`), loading the clawgate extension **from two different
paths** — one from the worktree, one from the base clone. Repointing `Default` left `Profile 2`
(then the `last_used` one) still on the old path. Symptom: the version you see depends on which
profile's window you happen to be looking at, and "I reloaded it and it's still old" is true and
false at the same time.

**Every profile must be repointed and reloaded, not just the one in front of you.**

### The machine-readable check — this is the authority for an agent
Agents **cannot read `brave://` pages**, so "look at brave://extensions" is not a check you can run.
Chrome/Brave record unpacked extensions in each profile's `Preferences` JSON with `location: 4`.

🔴 **Do NOT filter this by a path substring, and do not try to filter it by extension NAME.** An
unpacked entry in `Preferences` carries **no manifest at all** — the keys are `path`, `location`,
`commands`, `permissions`… and nothing else identifying (measured 2026-08-12; a `.value.manifest.name`
filter matches **zero** entries and reports a confident "not loaded" for every profile). And a
`grep clawgate` on the path is exactly blind to the case you most need to catch: the extension loaded
from an unexpected directory. So **list every unpacked extension in every profile** and read the
paths yourself:

```bash
for p in ~/.config/BraveSoftware/Brave-Browser/*/Preferences; do
  prof=$(basename "$(dirname "$p")")
  if ! out=$(jq -r '.extensions.settings // {} | to_entries[]
        | select(.value.location==4)
        | "    \(.value.path)  disabled=\(.value.disable_reasons // [] | length)"' "$p"); then
    printf '[%s] !! Preferences unreadable\n' "$prof"
  elif [ -n "$out" ]; then printf '[%s]\n%s\n' "$prof" "$out"
  else printf '[%s] (no unpacked extensions)\n' "$prof"
  fi
done
jq -r '.profile.last_active_profiles' ~/.config/BraveSoftware/Brave-Browser/"Local State"
```
Every profile prints a line, so a profile that is silently missing the extension is visible as such
rather than absent from the output. Don't add `2>/dev/null` — a locked or half-written `Preferences`
must not read as "no extensions". Quote `"$(dirname "$p")"`: profile dirs contain spaces
(`Profile 2`), and an unquoted command substitution silently reports it as `Profile`.

For the laptop, pipe the same script over ssh rather than trying to escape it inline:
```bash
ssh zach@192.168.50.155 'bash -s' <<'SWEEP'
  ...same loop...
SWEEP
```

Cross-check the version on disk at each path that comes back: `jq -r .version <path>/manifest.json`.
An extension's ID is `sha256` of its absolute load path (hex mapped `0-f`→`a-p`), so two profiles
pointing at different paths are genuinely two separate installs, not one shared one.

### Verifying the HOTKEYS survived — also machine-readable
Brave records per-extension bindings on the same entry, and this is where the "reload silently
dropped a hotkey" trap becomes visible. `was_assigned: true` means the accelerator is actually bound;
a command **missing** from the map was never registered in that profile at all.

```bash
jq -r '.extensions.settings // {} | to_entries[]
  | select((.value.path? // "")|test("clawgate-extension"))
  | .value.commands // {} | to_entries[]
  | "\(.key): key=\(.value.suggested_key // "UNSET") assigned=\(.value.was_assigned // false)"' \
  ~/.config/BraveSoftware/Brave-Browser/*/Preferences
```
Expect `open-capture` = `Ctrl+Shift+K` and `open-capture-pick` = `Ctrl+Shift+E`, both
`assigned=true`, **in every profile that loads the extension**. Measured 2026-08-12 right after a
repoint: workbench `Default` had `open-capture-pick` at `assigned=false` and laptop `Profile 1` was
missing the command entirely — i.e. the picker hotkey was dead in two places while the extension
itself reported perfectly healthy. Re-bind at `brave://extensions/shortcuts`.

## Deploying a merged extension change

```bash
# BOTH hosts — the worktree is dedicated and normally clean, so this fast-forwards
git -C ~/workspace/clawgate-extension fetch -q origin trunk
git -C ~/workspace/clawgate-extension merge --ff-only origin/trunk
ssh zach@192.168.50.155 'git -C ~/workspace/clawgate-extension fetch -q origin trunk &&
  git -C ~/workspace/clawgate-extension merge --ff-only origin/trunk'

# VERIFY per host — version on disk + identity against trunk + tree is clean
jq -r .version ~/workspace/clawgate-extension/containers/clawgate/extension/manifest.json
git -C ~/workspace/clawgate-extension diff --stat origin/trunk -- containers/clawgate/extension  # empty = identical
git -C ~/workspace/clawgate-extension status -s     # empty = no out-of-band edits
ssh zach@192.168.50.155 'jq -r .version ~/workspace/clawgate-extension/containers/clawgate/extension/manifest.json;
  git -C ~/workspace/clawgate-extension status -s'
```

Then **reload in every Brave profile on both hosts** — Brave does not hot-reload unpacked
extensions, so advancing the checkout without a ↻ leaves the OLD build running. Confirm with the
profile sweep and the hotkey check above, not by eye.

**If `merge --ff-only` refuses**, someone committed on `clawgate-ext-local` or edited the extension
in place; the worktree is no longer a pure mirror of `trunk`. Do NOT reach for a subtree `restore`
(see the trap below). Diagnose and pick one:
```bash
git -C ~/workspace/clawgate-extension status -s                                  # uncommitted edits?
git -C ~/workspace/clawgate-extension log --oneline origin/trunk..HEAD           # local commits?
```
Uncommitted junk you don't want → `git restore -- <paths>` those paths, then ff. Local commits worth
keeping → push them as a PR and ff once merged. Either genuinely stale → recreate the worktree from
scratch (see below); it holds no state worth preserving.

## 🔴 Never "fix" a stale checkout by restoring a subtree from another ref

The tempting shortcut when a checkout is behind is
`git restore --source=origin/trunk --worktree -- <subdir>`. **Do not.** It looks self-healing and is
the opposite:

- Git compares worktree ↔ **index**, never worktree ↔ merge target. Content that is byte-identical
  to `origin/trunk` is still "a local change", so `git merge --ff-only origin/trunk` **aborts**
  (`Your local changes to the following files would be overwritten by merge`) — it **blocks** the
  very re-sync `homelab-talos/CLAUDE.md` prescribes, rather than dissolving into it. Verified in an
  isolated repo with both controls (ff proven to work clean first, content proven identical).
  ⚠ **Precisely:** it blocks a merge that *touches the restored path*. A merge whose incoming range
  leaves that path alone still fast-forwards, and `restore --source=<ref> --staged --worktree`
  (index moved too) does not block at all. That is cold comfort — in the case that motivates the
  shortcut, the path changed upstream **by definition**, which is why you were restoring it.
- It is **silently revertible**: any later `git restore`/`checkout` over that path drops the tree
  back to the stale version, rc 0, no output — i.e. redeploys the old build with no error.
- It permanently trips a clean-tree check on the clone you did it to, so a "correctly deployed" host
  reads as hand-tampered forever.

If a checkout is behind, advance the checkout. If it can't be advanced, load from one that can —
which is why the worktree exists.

## Setting up a new host (or a replacement worktree)

```bash
git -C ~/workspace/homelab-talos fetch -q origin trunk
git -C ~/workspace/homelab-talos worktree add ~/workspace/clawgate-extension \
    -b clawgate-ext-local origin/trunk
```
`.envrc` is **tracked in `homelab-talos`**, so it arrives with the checkout — do NOT copy one in or
overwrite it (the global "worktrees don't inherit `.envrc`" rule does not apply to this repo). Then
add it in Brave: `brave://extensions` → Load unpacked →
`~/workspace/clawgate-extension/containers/clawgate/extension`, **in each profile**, then re-check
`brave://extensions/shortcuts`.

## ⚠ Historical: `~/clawgate-extension` and `sync-clawgate-extension.sh` are NOT the delivery path
Delivery used to be an rsync into a flat copy at `~/clawgate-extension` via
`homelab-talos/scripts/sync-clawgate-extension.sh`.

- The **flat copy is deleted** (2026-08-12, both hosts). Nothing recreates it.
- The **script is deleted too** — removed from `trunk` in `a39ed0c5` along with its test
  (`<homelab-talos>/scripts/tests/test-sync-clawgate-extension.sh`). Retiring it is **DONE, not pending**.
  `scripts/README.md` and `containers/clawgate/extension/README.md` now describe the removal
  correctly; the only reference that still reads as live is the historical
  `<homelab-talos>/claudedocs/handoff-clawgate-ext-2026-07-30.md`, which is a dated record — don't run its commands.

The failure worth remembering: the script's `--check` kept reporting a confident
`in sync — matches origin/trunk` about a directory **no profile loaded**, so a green check coexisted
with every browser running an older build. **A tool's clean verdict is a fact about the tool's
target, not about what is running.**

## ⚠ Serving a scratch test page TO that Brave
The workbench firewall `allowedTCPPorts` is a short allowlist
(80/443/6443/7844/8110/8180/25565/58012), so an ad-hoc port is unreachable over the LAN — serve it on
the workbench and open **`http://localhost:<port>`**, never open a firewall port for a throwaway.

Verifying the picker's privacy guard by hand has a trap that makes the obvious test vacuous →
`/home/zach/workspace/devrc/claude/skills/clawgate/reference/element-references.md`.

## CI scope
`clawgate-ci` (Tekton — see the `tekton` skill) runs `go build`/`vet`/`test -race` + extension
coverage + hook bats. It does **NOT** run Playwright, so the browser layer is UNGATED by CI. Run
`make e2e` locally and **count** the results: `tasks.spec.ts` `test.skip`s the whole file without
Docker, so a "green" run can mean 17 tests never executed.
