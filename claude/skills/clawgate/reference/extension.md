# clawgate — the browser extension: delivery, reload, and verifying a build

Read when: you changed the clawgate browser extension, or you need to know **which build is actually
loaded** in Zach's Brave.

## 🔴 The extension does NOT ship via Flux — merging to `trunk` deploys NOTHING
It runs on **TWO hosts**, and each Brave loads it **unpacked, in place, from a git checkout** —
never a flat copy. Deploying it therefore means *advancing that checkout* and reloading Brave.

| host | ssh | Brave's "Loaded from" | what that path is |
|---|---|---|---|
| **workbench** | this host / `192.168.50.250` | `~/workspace/homelab-talos/containers/clawgate/extension` | the **permanently-dirty base clone** |
| **laptop** | `zach@192.168.50.155` | `~/workspace/clawgate-extension/containers/clawgate/extension` | a **linked worktree** of the laptop's `homelab-talos`, branch `clawgate-ext-local` |

**Both hosts must be updated — updating one leaves the other on the old build.** Measured
2026-08-12: PR #305 (ext 1.5.0) was merged to `trunk` and **both** hosts were still running 1.4.0.

### 🔴 `~/clawgate-extension` and `scripts/sync-clawgate-extension.sh` are a DEAD path — do not use them
The script mirrors trunk into a flat `~/clawgate-extension`. **Nothing loads that directory.** It
exists only on the workbench (the laptop has no such dir at all), and its `.synced-from` stamp keeps
reporting a tidy `in sync — matches origin/trunk` **while both browsers run something else entirely**
— a green check on a directory no browser reads. It cost a full sync + reload cycle that changed
nothing before the real paths were found. Treat a clean `--check` as **no evidence** about what is
loaded; the only authority is Brave's own "Loaded from" line on `brave://extensions`.
(The `.synced-from` provenance rule below still applies to any flat copy that *is* wired up.)

### Deploying a merged extension change
```bash
# LAPTOP — clean worktree, just fast-forward it
ssh zach@192.168.50.155 'git -C ~/workspace/clawgate-extension fetch -q origin trunk &&
  git -C ~/workspace/clawgate-extension merge --ff-only origin/trunk'

# WORKBENCH — the base clone usually CANNOT be fast-forwarded (see below), so take the
# subtree only. --worktree keeps the index clean so it can't be committed by accident.
git -C ~/workspace/homelab-talos fetch -q origin trunk
git -C ~/workspace/homelab-talos restore --source=origin/trunk --worktree -- containers/clawgate/extension

# VERIFY per host — version at the REAL load path, and identity against trunk
jq -r .version ~/workspace/homelab-talos/containers/clawgate/extension/manifest.json
git -C ~/workspace/homelab-talos diff --stat origin/trunk -- containers/clawgate/extension  # empty = identical
```
Then **reload on `brave://extensions` on BOTH hosts** — Brave does not hot-reload unpacked
extensions, so a checkout update without a ↻ leaves the OLD build running. After adding a
`commands` entry, also check `brave://extensions/shortcuts`: Brave routinely leaves newly-added
hotkeys unbound on an in-place reload.

⚠ **Why the workbench gets the surgical `restore` and not a merge:** `~/workspace/homelab-talos`
there is the base clone CLAUDE.md documents as chronically behind (14 commits on 2026-08-12), and
`merge --ff-only` **refuses** whenever incoming commits touch files that are locally modified or
would overwrite untracked ones — on 2026-08-12 that was 4 collisions with live media-stack WIP.
Check before assuming a merge is available:
```bash
git -C ~/workspace/homelab-talos diff --name-only HEAD..origin/trunk | sort > /tmp/in.txt
comm -12 /tmp/in.txt <(git -C ~/workspace/homelab-talos status -s | awk '{print $2}' | sort)  # any output = merge will refuse
```
The `restore` leaves the subtree showing as ` M` against a stale HEAD. That is expected and
**self-healing** — the content already equals `origin/trunk`, so the modification disappears the
moment the base clone catches up. 🔴 But it is also **silently revertible**: any later
`git restore`/`checkout` over that path drops the extension back to the stale HEAD version, i.e.
back to the old build, with no error. Re-check the manifest version after any such operation.

🟡 **The workbench arrangement is the fragile one and is worth fixing**: because Brave loads the
base clone directly, the deployed extension is hostage to that clone's drift. The laptop's
dedicated-worktree setup is the coherent pattern. Repointing workbench Brave at its own worktree
needs Zach in the Brave UI (remove + re-add unpacked, then re-check shortcuts) — **open item, not
yet done.**

## 🔴 A missing `.synced-from` stamp means someone hand-copied it out of band
That is exactly how a build that never passed CI ran live for ~11 hours while `trunk` looked clean —
copied straight from a working tree, so `clawgate-ci` never saw it. For a git-checkout load path the
equivalent check is the checkout's own commit and cleanliness:

```bash
git -C ~/workspace/homelab-talos log --oneline -1
git -C ~/workspace/homelab-talos status -s -- containers/clawgate/extension   # empty = no out-of-band edits
ssh zach@192.168.50.155 'git -C ~/workspace/clawgate-extension log --oneline -1;
  git -C ~/workspace/clawgate-extension status -s'
```

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
