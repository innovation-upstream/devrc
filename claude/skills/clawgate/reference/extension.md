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
and **laptop**. Both must be updated — doing one leaves the other on the old build.

🔴 **The laptop's LAN address is not reliable — reach it at `ssh zach@10.42.0.100` (mesh).**
Measured 2026-09-12: the LAN address `zach@192.168.50.155` (which this file used to prescribe
everywhere) returned **no route to host**, while the mesh address `zach@10.42.0.100`
answered immediately. An unreachable host reads as "checked, nothing there" if you let the ssh
failure scroll past — it is **unmeasured**, not clean. Say which of the two you got.

A worktree is used deliberately, **not** the `homelab-talos` base clone. The base clone is
permanently dirty and chronically behind (14 commits on 2026-08-12), so loading it directly makes
the deployed extension hostage to that drift — and it cannot be fast-forwarded while its WIP
collides. The worktree advances with a plain `merge --ff-only` regardless of what the base clone is
doing.

### 🔴 The worktree can VANISH, and the base clone silently takes over
Measured 2026-09-12 on workbench: `~/workspace/clawgate-extension` **did not exist** — no worktree
record, though the `clawgate-ext-local` branch survived, orphaned and stale at `a39ed0c5`. `Default`
had been re-pointed at the **base clone** (`~/workspace/homelab-talos/containers/clawgate/extension`)
and was the only enabled install; `Profile 2` still named the missing worktree path and sat
**disabled** (`disable_reasons: [4]`).

This is the failure this file exists to prevent, and it announces itself **not at all**: the loaded
build was byte-identical to `trunk` that day, so every version check read correct. The hazard is
latent — it detonates on the next extension commit landed while the base clone stays behind.

So the sweep below is **not only** "which version" — read the **paths**, and treat *any* path that
is not the worktree as a finding even when the version matches. The cheap standing check:
```bash
ls -d ~/workspace/clawgate-extension || echo "!! worktree GONE — base clone is probably loaded"
git -C ~/workspace/homelab-talos diff --stat origin/trunk -- containers/clawgate/extension
#   non-empty while the base clone is what Brave loads = the deployed extension is NOT trunk
```
Recreating it: the branch usually still exists, so `worktree add -b` fails. Prove the branch holds
no unique commits (`git log --oneline origin/trunk..clawgate-ext-local` → empty), then reset it:
```bash
git -C ~/workspace/homelab-talos worktree add ~/workspace/clawgate-extension \
    -B clawgate-ext-local origin/trunk      # -B resets the orphaned branch
```

### 🔴 Re-pointing a profile STRANDS that install's storage — it is not a free move
The extension ID is derived from the **load path**, so moving a profile from the base clone to the
worktree creates a **different install with empty `chrome.storage.local`**: the configured
`baseURL`, the **`hookToken`**, `defaultTags`/`defaultPrivileges`/`defaultRepo`, `projectOrigins`
and **every saved draft** stay behind under the old ID and simply stop existing for the new one.
Measured 2026-09-12 on workbench `Default`: 748K of storage, 4 live `clawgate.draft.*` keys, all
config keys set — all of it keyed to the base-clone path's ID.

A re-point that skips this reads as "the extension broke" (submits 401 with no token) plus silent
draft loss. Either accept re-entering the config, or migrate the storage **with Brave fully closed**
— it is a LevelDB directory per ID:
```bash
# Brave CLOSED. IDs: sha256 of the absolute load path, hex mapped 0-f -> a-p.
printf '%s' "<load-path>" | sha256sum | cut -c1-32 | tr '0-9a-f' 'a-p'
cp -a ~/.config/BraveSoftware/Brave-Browser/<profile>/"Local Extension Settings"/<OLD_ID> \
      ~/.config/BraveSoftware/Brave-Browser/<profile>/"Local Extension Settings"/<NEW_ID>
```
🔴 **Do not hand-edit `Preferences` to re-point a profile.** Brave rewrites it on exit (so an edit
made while it runs is discarded), and `extensions.settings` is covered by preference MACs — a
mismatch gets the entry disabled or reset. Re-pointing is a `brave://extensions` UI action, and
agents cannot drive `brave://`: hand it to Zach with the exact path.

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
ssh zach@10.42.0.100 'bash -s' <<'SWEEP'
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

⚠ **This is not a one-off that got fixed — re-measured 2026-09-12, laptop `Profile 1` is STILL
`open-capture-pick: assigned=false`.** A dead picker hotkey is the steady state unless someone
re-binds it, and nothing in a version check or a profile sweep surfaces it. Run the command map
every time, on **both** hosts.

⚠ **Also read `_execute_action`, not just the two named commands.** Laptop `Default` has it bound to
**`Ctrl+Shift+K` — the same accelerator as `open-capture`** (workbench `Default` has it `UNSET`,
which is the sane state). Two commands claiming one chord means one of them loses; which one is
**unmeasured from `Preferences` alone**, and the loser fails silently. Check it at
`brave://extensions/shortcuts` before concluding a hotkey "works".

## Deploying a merged extension change

```bash
# BOTH hosts — the worktree is dedicated and normally clean, so this fast-forwards
git -C ~/workspace/clawgate-extension fetch -q origin trunk
git -C ~/workspace/clawgate-extension merge --ff-only origin/trunk
ssh zach@10.42.0.100 'git -C ~/workspace/clawgate-extension fetch -q origin trunk &&
  git -C ~/workspace/clawgate-extension merge --ff-only origin/trunk'

# VERIFY per host — version on disk + identity against trunk + tree is clean
jq -r .version ~/workspace/clawgate-extension/containers/clawgate/extension/manifest.json
git -C ~/workspace/clawgate-extension diff --stat origin/trunk -- containers/clawgate/extension  # empty = identical
git -C ~/workspace/clawgate-extension status -s     # empty = no out-of-band edits
ssh zach@10.42.0.100 'jq -r .version ~/workspace/clawgate-extension/containers/clawgate/extension/manifest.json;
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
coverage + hook bats, and does **NOT** run Playwright.

🔴 **That is not the same as "the browser layer is UNGATED by CI", which this file used to say and
which is false.** A SEPARATE Tekton check, `clawgate-e2e`, runs `make e2e` on the PR's own specs.
Measured on homelab-infra#564: `tekton/clawgate-e2e` → `clawgate e2e passed — 122 tests, 2 skipped`.
Its Postgres is REAL rather than Docker, because Tekton pods have no daemon — see
`clawgate-e2e-pipeline.yaml`, which documents that a Docker-based gate would have self-skipped every
full-mode spec and gone green having tested almost nothing.

⚠ **RUNS is not BLOCKS.** Whether `clawgate-e2e` is a required check is **unmeasured**:
`GET /repos/ZacxDev/homelab-infra/branches/trunk/protection` 403s on this private repo without
GitHub Pro, so the required-check list is unreadable from here. Never read its green as "the merge
was protected".

The local skip trap is still real and still yours to defeat: without Docker, `test.skip` on
`!dockerAvailable()` drops the full-mode spec files silently. So run `make e2e` locally and **count**
— then compare that total against the check's own `N tests, M skipped` string, rather than trusting
either number alone.
