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
Chrome/Brave record unpacked extensions in each profile's `Preferences` JSON with `location: 4`:

```bash
for p in ~/.config/BraveSoftware/Brave-Browser/*/Preferences; do
  prof=$(basename "$(dirname "$p")")
  out=$(jq -r '.extensions.settings // {} | to_entries[]
        | select(.value.location==4)
        | "\(.value.path)  disabled=\(.value.disable_reasons // [] | length)"' "$p" 2>/dev/null | grep -i clawgate)
  [ -n "$out" ] && printf '[%s] %s\n' "$prof" "$out"
done
jq -r '.profile.last_active_profiles' ~/.config/BraveSoftware/Brave-Browser/"Local State"
```
Quote `"$(dirname "$p")"` — profile dirs contain spaces (`Profile 2`), and an unquoted command
substitution silently reports it as `Profile`.

Cross-check the version actually on disk at each path that comes back:
`jq -r .version <path>/manifest.json`. An extension's ID is `sha256` of its absolute load path, so
two profiles pointing at different paths are genuinely two different installs.

## Deploying a merged extension change

```bash
# BOTH hosts — the worktree is clean and dedicated, so this always fast-forwards
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
extensions, so advancing the checkout without a ↻ leaves the OLD build running. After adding a
`commands` entry, check `brave://extensions/shortcuts` too: Brave routinely leaves newly-added
hotkeys unbound on an in-place reload. Confirm with the profile sweep above, not by eye.

## 🔴 Never "fix" a stale checkout by restoring a subtree from another ref

The tempting shortcut when a checkout is behind is
`git restore --source=origin/trunk --worktree -- <subdir>`. **Do not.** It looks self-healing and is
the opposite:

- Git compares worktree ↔ **index**, never worktree ↔ merge target. Content that is byte-identical
  to `origin/trunk` is still "a local change", so `git merge --ff-only origin/trunk` **aborts**
  (`Your local changes to the following files would be overwritten by merge`) — it **blocks** the
  very re-sync `homelab-talos/CLAUDE.md` prescribes, rather than dissolving into it. Verified in an
  isolated repo with both controls (ff proven to work clean first, content proven identical).
- It is **silently revertible**: any later `git restore`/`checkout` over that path drops the tree
  back to the stale version, rc 0, no output — i.e. redeploys the old build with no error.
- It permanently trips the out-of-band check above, so a "correctly deployed" host reads as
  hand-tampered forever.

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

## ⚠ Historical: `~/clawgate-extension` and `sync-clawgate-extension.sh` are RETIRED
Delivery used to be an rsync into a flat copy at `~/clawgate-extension` via
`homelab-talos/scripts/sync-clawgate-extension.sh`. **Both are gone** (flat copy deleted
2026-08-12). The failure worth remembering: the script's `--check` kept reporting a confident
`in sync — matches origin/trunk` about a directory **no profile loaded**, so a green check coexisted
with every browser running an older build. If you find a doc or script still referencing either,
it is stale. **A tool's clean verdict is a fact about the tool's target, not about what is running.**

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
