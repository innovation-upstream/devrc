---
# No clawgate task — session had no CLAUDE_CODE_SESSION_ID
---
# Handoff: clipboard-research — 2026-08-29

## Run this first — the index, one read-only command
```bash
python3 ~/workspace/devrc/scripts/lib/subsystem_recall.py --repo ~/workspace/devrc
```
Terse pointers this doc does not carry, curated by past sessions and outliving it.
🔴 RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
reading, and it may describe a gotcha already fixed. `scope-absent`/`scope-empty` means
nothing is recorded yet: ordinary, not an error, and not a clean bill of health.
Non-blocking: if it exits non-zero, print the stderr line and carry on.

## Goal
Research modern best practices for clipboard and terminal clipboard interaction on Linux/NixOS/i3wm. Determine what's current, what's optimal, and what (if anything) needs changing.

## State now — updated 2026-08-29 on resume
- **Research session (00:37–00:53):** report completed, no code changes. This doc
  landed on `handoff/clipboard-research` → PR #1014 (it could not be pushed to
  `main`: protected branch, 2 required checks).
- **Resume session:** both open decisions resolved, and a bug the research missed
  was found, fixed and gated.
  - `unnamedplus` — **declined**, no change.
  - `"+y` dead with no `DISPLAY` — fixed in **#1027**, which then turned out to
    be INERT in production until **#1043**. See the block below before trusting
    anything in this section.
- 🔴 **THE CLIPBOARD FIX (#1027) SHIPPED INERT. #1043 is what made it work.**
  Everything above this line was written before that was known; the paragraph
  that used to sit here claimed the change "goes live on `git pull` alone,
  no `home-manager switch`" and cited `$DEVRC_DIR` as the mechanism. Both
  halves were wrong, and it is quoted rather than deleted because the way it
  was wrong is the reusable part.

  Measured over real ssh to the laptop, against the deployed copy, AFTER #1027
  had merged and shipped:

  ```
  Error in /home/zach/.config/nvim/init.lua:
  E484: Can't open file /.config/nvim/config/native.vim
  clipboard: No provider. Try ":checkhealth" or ":h clipboard".
  ```

  `$DEVRC_DIR` was set in exactly ONE place — a systemd user service's
  `Environment=` block in `nix/graphical.nix` — so it existed only inside a
  graphical session. `init.vim` sourced every other config file through it, so
  off-session the first `source` raised E484 and **aborted the entire nvim
  config**: no options, no leader mappings, no lua half, no plugin config.
  neovim had been running unconfigured over ssh, on a bare TTY, in units and in
  cron — invisible because the only place anyone reads a config error is the
  terminal in front of them, which is the one place the variable was set.

  🔴 **Why no test caught it, which is the lesson worth keeping:** #1027's
  red/green harness **set `$DEVRC_DIR` itself**, manufacturing the one
  precondition that does not hold in production. **A fixture that supplies an
  environment cannot observe that environment being absent.** The fix was
  correct, merged, green, mutation-tested — and did nothing where it mattered.

- **Fixed in #1043**: nix substitutes the repo path into `init.vim` at BUILD
  time, `init.lua` self-locates via `debug.getinfo`, and `lazygit.lua` — found
  by the new guard, not by hand — stopped pointing at
  `nil/.config/lazygit/config.yml`. Guards: a hermetic relationship test that
  NO file under `.config/nvim` reads `$DEVRC_DIR` at runtime (comments
  stripped, mutation-tested), plus a dev-host red/green counting E484s from the
  real chain.

- 🔴 **Deploy: a `home-manager switch` IS required** (the corrected claim).
  `init.vim` is `builtins.readFile`'d into the store, so the substitution
  happens at build time. Files it sources — `native.lua`, `native.vim` — are
  still read from the `~/workspace/devrc` working tree at runtime, so edits to
  THOSE remain live on `git pull`. The two are different questions and the old
  paragraph collapsed them into one.

- ✅ **VERIFIED on the real path, 2026-08-29**, both hosts at `638959b4`:
  `ssh` → `nvim` → `"+yy` on the laptop went from `E484=9, No provider,
  OSC52=0` to `E484=0, No provider=0, OSC52=1`, payload decoding to the exact
  yanked line. Workbench resolves the same substituted store `init.vim`. This
  is the first claim in this effort verified on the path that actually failed
  rather than a reconstruction of it.

- **Session closed 2026-08-29 (late).** #1051 landed the doc correction above.
  Cleanup done: `claim-work --release clipboard-research-1` (rc=0), and the four
  worktrees this effort created (`devrc-nvim-osc52`, `devrc-handoff-clip`,
  `devrc-control-main`, `devrc-nvim-devrcdir`) removed. Feature branches were
  auto-deleted on merge. All four PRs merged: **#1014, #1027, #1043, #1051**.
- ⚠ **`/audit-pr 1043` was offered and never run** — blocked by this session's
  operating instructions (no subagent dispatch unless asked), not by circumstance.
  #1043 touches `nix/programs/`, which every future `home-manager switch` depends
  on. It is the one review this effort did not get.

## Research findings — clipboard/terminal clipboard best practices (2025-2026)

### tmux clipboard — ALREADY OPTIMAL, no changes
- `set -s set-clipboard on` makes copy-mode emit OSC 52 to the outer terminal
- `set -ga terminal-features '*:clipboard'` advertises capability regardless of TERM
- Works across SSH and nested tmux — no external tools needed
- tmux-yank plugin is superseded (pops to `xsel`/`xclip` locally; OSC 52 is better)

### Alacritty OSC 52 — ALREADY OPTIMAL, no changes
- `osc52 = "OnlyCopy"` is the correct security posture (prevents remote paste injection)
- All modern terminals (Kitty, WezTerm, Ghostty) support OSC 52 fully

### X11 clipboard tools — ALREADY OPTIMAL, no changes
- `xclip` installed and sufficient for X11 scripting
- `xsel` is an alternative with slightly different CLI but no advantage
- If migrating to Wayland: switch to `wl-copy`/`wl-paste` (2.4k stars, gold standard)

### Clipboard managers — NOT NEEDED
- `cliphist` (1.5k stars) is the modern gold standard but Wayland-only
- On X11/i3, tmux OSC 52 + xclip covers the use cases
- `clipcat` (X11) or `CopyQ` available if history is wanted, but optional

### Neovim clipboard — ONE REAL BUG (found on resume), ONE DECLINED IMPROVEMENT

🔴 **The original claim on this line was wrong, and the correction is the whole
value of this section.** It read "`"+y` / `"+p` work without config", full stop.
That is true **only while `DISPLAY` is set**. Measured 2026-08-29 on nvim 0.12.5
at two independent points — `--headless` with the real config, and a pty with
`-u NONE` — with no `DISPLAY` (any ssh session, any bare TTY):

```
clipboard: No provider. Try ":checkhealth" or ":h clipboard".
```

Neovim ≥0.10 ships an OSC 52 provider but does **not** auto-enable it here, so
`"+y` was dead off-display and took `:Absc` (`lua/config/native.lua`, which does
`setreg('+')`) down with it. The research missed it because every probe ran on
the local X11 session — **one measurement point, and the failure lives at the
other one.**

- **FIXED** — `.config/nvim/lua/config/native.lua` now installs an OSC 52
  provider when `DISPLAY` is absent. Red→green on the exact failing path.
- 🔴 **Paste is served from a local cache, deliberately.**
  `vim.ui.clipboard.osc52.paste` queries the terminal and waits 1s + 9s before
  giving up, and alacritty is configured `osc52 = "OnlyCopy"` so it never
  answers. Wiring it straight through — **which is what `:h clipboard`'s own
  example does** — hangs 10s on every `"+p`. The mutant that does exactly that
  took the suite from 2.7s to 12.7s.
- Guarded on `DISPLAY` being absent: with a display, neovim's xclip
  autodetection already works *and* supports a real paste, so it is left alone.

**`set clipboard=unnamedplus` — DECLINED 2026-08-29.** The doc framed the cost as
"vim muscle memory", which understates it: `unnamedplus` routes **every** delete
and change (`d`, `c`, `x`, `s`) through the `+` register, so `dd` clobbers the
system clipboard and the copy-from-browser → edit → paste workflow breaks. Zach
works via agents rather than long interactive nvim sessions, so the payoff is
small and the cost is not. `"+y`/`"+p` stay explicit.

### Espanso + clipboard — ALREADY WORKING, no changes
- `{{clip}}` variable reads X CLIPBOARD via espanso's own `espanso-clipboard` module
- No conflicts with tmux OSC 52 path
- Gotcha: `{{clip}}` reads whatever is in CLIPBOARD at expansion time — potential race with concurrent writes

## Open investigations — live diagnosis state

None open. ⚠ **But note what this section said before, and why it was wrong:**
*"None. Research is complete. The findings above are conclusive."* It was written
after a survey that measured only the local X11 session, and it read as a clean
bill of health for a setup with a dead clipboard register over ssh. **A research
doc that names no open question is making a claim, not reporting an absence** —
the honest version names the dimension it did not vary. Here that dimension was
`DISPLAY`.

### `main`'s branch protection keeps ending up OFF, and nothing detects it
- **Symptom + exact repro:** twice on 2026-08-29 `required_status_checks` on
  `innovation-upstream/devrc`'s `main` was found deleted. Read it with:
  `gh api /repos/innovation-upstream/devrc/branches/main/protection --jq '{checks:.required_status_checks.contexts, enforce_admins:.enforce_admins.enabled}'`
  Unprotected reads `{"checks":null,...}`; healthy reads
  `{"checks":["tekton/devrc-pytests","tekton/devrc-nodetests"],...}`.
- **Observed (with values):**
  - Occurrence 1 was MINE — a deliberate, operator-authorised break-glass at
    ~19:58Z to merge #1014 + #1027. Window ~2–4 min. See the gotcha below: my
    "unconditional restore" ran and FAILED.
  - Occurrence 2 was NOT mine. Protection read `checks: null` again at ~21:56Z
    when merging #1051, after I had restored and verified it at ~15:57 local
    (post-#1043 read: both contexts present). Independent evidence a window was
    open in between: **`837d3fde` (16:46 local) is `Merge branch 'main' of
    github.com:...` — a DIRECT PUSH to `main`, not a PR merge.** Required checks
    with `enforce_admins: true` would have rejected it.
- **Ruled out:** my own `gh pr merge` calls (they do not touch protection);
  `ship.sh` (never pushes). The #1051 merge itself cannot have caused it — the
  `checks: null` read was taken BEFORE that merge completed.
- **Leading hypothesis:** another session used the escape hatch that
  `devrc/CLAUDE.md` documents verbatim
  (`gh api -X DELETE .../branches/main/protection/required_status_checks`) and
  did not restore it. That is a *hypothesis about who*, not a measurement —
  GitHub's protection-change events are org-audit-log only and were not read.
- **Next probe:** the durable fix is a detector, not an attribution. Add an arm
  to `scripts/drift-check.sh` (it already has the rc vocabulary and runs on a
  timer) asserting `required_status_checks.contexts` is non-empty on `main`,
  reporting COULD NOT MEASURE when the API does not answer so a zero is never
  read as a pass. Attribution, if wanted separately:
  `gh api /orgs/innovation-upstream/audit-log --paginate` (needs org admin;
  unverified from here).

## Next steps (ranked)
**Closed by this effort — kept so a resume does not re-open them:**
- ~~adopt `set clipboard=unnamedplus`~~ — **DECLINED 2026-08-29**, reasoning in
  the neovim section above.
- ~~install a clipboard manager for history~~ — **investigated 2026-08-29,
  nothing installed**; the evidence and the retracted security argument moved
  under Gotchas below, where an APPEND heading keeps them.
- ~~migrate to Wayland~~ — **not applicable today**: measured
  `XDG_SESSION_TYPE=x11`, `WAYLAND_DISPLAY` unset, `wl-copy` absent.

**Open:**
1. **Add an unprotected-`main` arm to `scripts/drift-check.sh`** (devrc). The
   only thing that would catch someone else's break-glass window; today the
   detector is a human happening to look. Files: `scripts/drift-check.sh`,
   `scripts/tests/test_drift_check.py`. See the open investigation above.
2. **Correct `devrc/CLAUDE.md`'s escape-hatch note** (devrc). It hands over the
   `DELETE` and says nothing about restoring — and the obvious `PATCH` back
   SILENTLY FAILS (see gotchas). It should carry the `PUT` payload and say the
   restore must be read back, not trusted. Files: `CLAUDE.md`.
3. **Run `/audit-pr` over the merged #1043** (devrc) — the one review this effort
   never got, and it touches `nix/programs/`, which every `home-manager switch`
   depends on. Merged, shipped and verified on the real path, so this is
   confirmation rather than a gate.
4. **Consider recording the transferable lesson in `claude/RULES.md`**: *a
   fixture that supplies an environment cannot observe that environment being
   absent.* Gated — `RULES.md` has an enforced ceiling
   (`scripts/tests/test_rules_size.py`) and needs an eviction in the SAME
   commit, so this is an operator call, not a mechanical edit.

## Gotchas / decisions / dead-ends
- OSC 52 supersedes tmux-yank for this setup — no reason to install the plugin
- Wayland clipboard is a different ecosystem — `wl-clipboard` is the equivalent of `xclip`
- Espanso's clipboard access is separate from tmux's OSC 52 but reads the same X CLIPBOARD selection
- No clipboard manager installed — see the section above for the evidence, and
  note the retracted security argument so it is not re-derived
- 🔴 `greenclip` has NO top-level nixpkgs attribute; `nix ... nixpkgs#greenclip`
  fails. It is `haskellPackages.greenclip`. A version check that falls back to
  that attribute will report 4.3.1 and read as if the top-level one existed

- 🔴 **`gh api -X PATCH .../branches/main/protection/required_status_checks`
  CANNOT restore protection after the sub-resource has been DELETEd.** It
  returns non-zero and changes nothing. Restoring needs a full
  `PUT /branches/main/protection` with `required_status_checks`,
  `enforce_admins`, `required_pull_request_reviews: null`, `restrictions: null`
  and the boolean arms. Measured: my break-glass `restore()` ran correctly as an
  EXIT trap and still left `main` unprotected, because the command inside the
  trap had never been executed once. **A rollback path you have never run is a
  hypothesis** — and reading the state back is the only reason it was caught.
  Working payload: `scripts/../` n/a — rebuild it from the live config with
  `gh api .../protection` before deleting anything.
- 🔴 **The break-glass was AVOIDABLE.** #1023 fixed `main`'s red at 18:37Z; I
  deleted protection at 19:58Z on a measurement taken over an hour earlier. My
  *branch* was still red (cut before #1023, and `strict: false` means checks run
  on the branch, not the merged tree) — but the correct move was to **merge
  `main` into the branch** and let the gate go green. Re-measure at the moment
  you act on a destructive step, and re-check whether the cheaper route opened.
- 🔴 **`pkill`-class cleanup: matching `/proc/<pid>/cwd` against a PREFIX is not
  enough.** Filtering strays on `/tmp/claude-1000/*` killed an `nvim` belonging
  to a SIBLING session (`07b11b56…`, not this session's `ba80fa7b…`). Every
  agent scratchpad lives under that prefix. Match your OWN session id exactly.
- **Docs-only PRs**: `/audit-pr` was deliberately not offered for #1051 — an
  adversarial code audit has nothing to bite on in a prose diff.

### Clipboard managers — investigated on resume, still NOT installed

The original section concluded "NOT NEEDED — tmux OSC 52 + xclip covers the use
cases". That reasoning was about **transport** and did not address
**persistence**, which is a real and measured gap:

```
set CLIPBOARD from a process   ->  [OWNERSHIP-PROBE]
kill the owning process        ->  Error: target STRING not available
```

X11 selections are owned by the source client, and no manager is running to
hold them (verified: none of clipcat/copyq/greenclip/clipmenu/diodon/parcellite
is installed or running). Close the app you copied from and the clipboard is
empty. So the original conclusion was right by luck, not by argument.

**Nothing installed anyway, and the reason is NOT security.** An earlier draft
of this section argued against `greenclip` because its only exclusion mechanism
is `blacklisted_applications` (per-application, verified against the real
binary's generated config), so a vault paste out of Brave cannot be excluded
without excluding Brave itself, leaving up to 50 plaintext secrets in
`~/.cache/greenclip.history`. 🔴 **That argument is RETRACTED as overweighted**:
anyone who can read that file can already read `~/.ssh/id_*`, the age
identities and the `$KC_*` kubeconfigs in the same home directory, and
`~/.cache` is conventionally excluded from backups. The marginal exposure is
small. The per-application fact is true; the conclusion drawn from it was not.

The actual reason is **no measured need**. The probe above demonstrates the
MECHANISM; nothing establishes the FREQUENCY, and no instance of actually
losing a clipboard was observed. Against that: Zach works entirely via agents
and the standing direction is to modernize the agent-facing layer rather than
interactive-CLI ricing — a history picker on `$mod+Shift+v` is the latter — and
`MEMORY.md`'s "do we need it before hardening" entry exists because a 145 KB
webhook listener that had never run once was shipped and later retired.

Contrast with the neovim fix above, which shipped precisely because it was
**not** speculative: `"+y` was reproducibly dead on every ssh session.

**If it is ever wanted, the pick is `greenclip`** (4.3.1 in the pinned nixpkgs,
attribute `haskellPackages.greenclip` — NOT top-level), because it reuses the
rofi already bound to `$mod+d` and solves persistence AND history for the same
machinery, which strictly dominates a persistence-only daemon. `$mod+Shift+v`
is free. The trigger to revisit is an OBSERVED lost clipboard, not this note.

## How to verify
- tmux clipboard: copy in tmux copy-mode → paste in another app (OSC 52 path)
- 🔴 **Neovim off-display — the one that was broken.** From the *other* host:
  `ssh <host>` → `nvim <file>` → `"+yy` → paste locally. **Verify from a real
  ssh session, not by unsetting `DISPLAY` locally, and NOT by setting
  `DEVRC_DIR` in the probe** — supplying that variable is precisely what hid
  the breakage for a whole release. Counting E484s in the captured pty is the
  cheap discriminator: a config that did not load reports the clipboard symptom
  for a reason that has nothing to do with the clipboard.
- Neovim on the local X11 session must be UNCHANGED: `:lua print(vim.g.clipboard)`
  → `nil` (xclip autodetection, untouched by the fix).
- Espanso: type `:clip` → clipboard contents expand inline
