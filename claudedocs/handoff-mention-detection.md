# Handoff: mention-detection — 2026-08-30

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
Detect clawgate/GitHub/ClickUp references in agent output, emit telemetry, and make
them clickable in the terminal the way a URL already is. **SHIPPED AND VERIFIED LIVE.**

## State now
- Branch: `main`, clean. `origin/main` was `e0e29e7b` when this doc was written and is
  `3b1a0477` as of 2026-08-30 — the moves since are other threads' handoff docs, none
  touching the mention feature
- **Both hosts deployed and converged** (`ship.sh` → `ad5274b6`, cross-host agreement,
  0 dangling artifacts on either)

**DONE — merged:**
- `0493e612` (#1011) — the feature. Scanner `scripts/collector/mention_scan.py`;
  detection folded into the EXISTING `scripts/collector/claude/session-tailer.py`;
  handler `scripts/mention-open.py`; two hints in
  `nix/programs/alacritty/default.nix`; tests `scripts/tests/test_mention_scan.py`,
  `test_mention_open.py`, `test_alacritty_hints.py`
- `31cd214d` (#1060) — espanso `ask`/`clarify` collision that had `main` red repo-wide.
  NOT authored here: a competing PR beat this session's #1058, which was **closed** in
  its favour (see Gotchas)
- `e0e29e7b` (#1067) — `claudedocs/mention-detection-as-built.md`
- `686d6ff0` in **homelab-talos** — `tekton-ci` PodSecurity label; live, Flux-reconciled

**VERIFIED LIVE, not inferred:**
- deployed `alacritty.toml` resolves (`readlink -f`) to a store path carrying BOTH hints
- hint mode labels all three shapes in a real terminal: `devrc#1011`, `#370`, `868abc123`
- activating a label DISPATCHES: `#370` raised the rofi picker with both candidates
- handler resolution: `devrc#1011` → GitHub, `868abc123` → ClickUp, `#282828`/`#ff00ff`
  **rejected**
- **operator confirmed `Ctrl+Shift+M` works in a 3-day-old window** — `live_config_reload`
  picked up the symlink swap; no restart needed (I predicted otherwise; wrong)

**BOTH REMAINING INTERACTIONS NOW VERIFIED (2026-08-30), off-screen.** Driven on an
isolated `Xvfb :99` — same `alacritty` binary, same DEPLOYED config
(`/nix/store/9jmni…-alacritty.toml`, reached through a probe `XDG_CONFIG_HOME` holding
only a symlink to `~/.config/alacritty`, so `readlink -f` lands on the identical store
file), real XTEST input, zero impact on the operator's screen. Observable was an
`xdg-open` **capture handler**, not a browser: a probe `XDG_DATA_HOME` +
`mimeapps.list` binding `x-scheme-handler/http{,s}` to a `.desktop` whose `Exec`
appends the URL to a log. Both handlers under test — the URL hint's `xdg-open` and
`mention-open.py`'s (`scripts/mention-open.py:208`) — funnel through it.
- **`mouse.enabled` — hover underlines.** Pointer over `devrc#1011` → that text alone
  renders underlined; the URL on the line above and the plain word below do not.
  Screenshot, not inference.
- **`mouse.enabled` — plain left-click dispatches.** Click on the hovered
  `devrc#1011` → `https://github.com/innovation-upstream/devrc/issues/1011` captured.
- **The mouse path reaches the AMBIGUOUS branch too.** Left-click on a bare `#370`
  raised the rofi picker with both rows; arrowing to row **2** and pressing Return
  opened `…/devrc/issues/370` — so the picker resolves by row CONTENT, not by index.
- **`Ctrl+Shift+O` still works on a plain URL** — the interaction the `hints.enabled`
  array replacement could have silently killed. Hint mode labelled the URL `j` (and
  labelled the mention NOT at all, i.e. the `O` binding is scoped to its own hint);
  pressing `j` captured `https://example.com/URL-HINT-CLICK` — the whole URL, with the
  terminal's line WRAP reassembled.
- **Instrument controls, both watched:** positive — a bare `xdg-open` under the probe
  env captured a line, so the handler can fire at all; negative — hover+click on
  `plainwordnothint` left the log at its previous length, so a captured line means a
  hint fired and not ambient activity.

**Still NOT verified:** that these gestures behave the same in Zach's own
long-running alacritty windows. `live_config_reload` picking up the symlink swap was
confirmed for `Ctrl+Shift+M` in a 3-day-old window, and the config is the same file,
but no mouse gesture has been made in one of those windows.

**IN FLIGHT:** `innovation-upstream/devrc#1057` — someone else's rescue PR. This
session TRIMMED the two superseded mention drafts out of it (`9a09ad58`, plain
fast-forward). Net diff is now 5 legitimate files (`scripts/cleanup-disk.sh` + its gate
test + 3 registrations). Not mine to merge.

## Open investigations — live diagnosis state

### clawgate deeplink `#task-N` is inert — filed as clawgate task #440 (open)
- **Symptom + exact repro:** open `https://clawgate.zacx.dev/tasks#task-370`. The board
  loads; the page does NOT scroll to or focus task 370. Reachable from the shipped
  feature: click a bare `#N` mention → rofi picker → choose the clawgate candidate.
- **Observed (with values):**
  - the DOM id is correct — `internal/ui/notes.go:459` emits `ID("task-"+ids)`
  - `GET /tasks` → `handleIndex` (`internal/api/server.go:384`) serves only the
    **document shell**; cards arrive in a LATER htmx fetch, URL built client-side at
    `internal/ui/components.go:2875-2876` (`/ui/tasks`, optional `?tag=` filters)
  - `grep` for `location.hash|hashchange|scrollIntoView` across `internal/` + `static/`
    returns **exactly one hit**, `components.go:2176`, an unrelated dropdown option
- **Ruled out:** an auth problem (the fragment survives the `login.zacx.dev` redirect);
  a wrong-selector problem (the id is right).
- **Leading hypothesis:** the browser resolves the fragment at initial document load,
  before any card exists, and nothing re-applies it after htmx settles.
- **Next probe:** none needed for diagnosis — the work is the fix. #440 carries 6
  acceptance criteria, non-goals pinning the operator's two decisions (scroll+highlight,
  client-side), and the verifier. Criterion 4 is the sharp one: a fragment naming a task
  NOT in the rendered set (tag filter active) must not fail silently.

## Next steps (ranked)
1. ~~**Verify the two unverified interactions**~~ — **DONE 2026-08-30**, see "BOTH
   REMAINING INTERACTIONS NOW VERIFIED" above. Closed by machine observation on an
   isolated Xvfb rather than by an operator report, because the operator was mid-game
   when the check came due (see Gotchas). No repo change; nothing left open here.
2. **CI capacity — the durable fix.** `ZacxDev/homelab-infra`,
   `clusters/homelab/apps/tekton-pipelines/`. Gate pods request far more than they use
   (nodes at 28–36% CPU while 6 gate pods sat `Pending` on `ExceededNodeResources`).
   Either cap concurrent `devrc-ci` PipelineRuns or right-size the requests. Closing
   condition: a `devrc-ci` run scheduling promptly with ≥6 others active.
3. **Fix `clawgate` deeplink** — clawgate task **#440**, `ZacxDev/homelab-infra`,
   `containers/clawgate`. Client-side hash handler + `hashchange`, reusing `.card-enter`.
   Closing condition: #440's 6 criteria, verified by a new spec in
   `e2e/tests/tasks.spec.ts` shown RED before / GREEN after.
4. **Document the branch-protection escape hatch's asymmetry** in `devrc/CLAUDE.md` —
   it currently names the `DELETE` but not that restoring needs a full `PUT` (see
   Gotchas). Closing condition: merged PR touching that paragraph.

## Gotchas / decisions / dead-ends
- 🔴 **A terminal-UI interaction can be verified WITHOUT taking the operator's screen —
  `Xvfb` + the deployed config + an `xdg-open` capture handler.** This is the pattern
  that closed rank 1, and it generalises to any alacritty/hint/`xdg-open` behaviour.
  Three pieces: (1) `Xvfb :99` and launch the REAL binary on it, so XTEST input is real
  input and no `--window` false-negative is possible; (2) a probe `XDG_CONFIG_HOME`
  containing **only** `mimeapps.list` + a **symlink** to `~/.config/alacritty`, so the
  config under test is provably the deployed store file (`readlink -f` it and say so) —
  copying the config would have tested a copy; (3) a probe `XDG_DATA_HOME` with a
  `.desktop` whose `Exec` appends `%u` to a log, bound to `x-scheme-handler/http{,s}`,
  so "it opened the right URL" is a grep instead of a browser tab. Run the positive
  control (`xdg-open` under the probe env) BEFORE trusting any silence from it.
  ⚠ It does NOT cover the operator's already-running windows — say that separately.
- 🔴 **`PREV_WS` went stale inside two minutes, and the first attempt put a window on
  the operator's game.** The recorded `PREV_WS=1`/`PREV_WIN=…` were read, then the
  probe terminal was launched ~2 min later — by which point the focused workspace was
  **4** with a fullscreen game on it and `xprintidle` reporting **1 ms**, i.e. hands on
  the keyboard. The new window landed on THAT workspace (i3 opens on the focused one,
  not the one you remembered) at 0×0 behind the game. Restoring to the remembered
  workspace would have been a SECOND theft — yanking them out of the game — so the
  right move was to kill the probe window and leave the workspace alone. **Re-read
  focus/idle immediately before the raise, not in the survey that motivated it, and
  check `xprintidle` before driving XTEST at all** — synthetic keys would otherwise
  have gone into whatever they were doing.
- 🔴 **`hints.enabled` is an ARRAY — declaring it REPLACES alacritty's built-in default.**
  No merge. Adding the mention hint without re-declaring the URL hint verbatim would
  silently kill URL clicking, with no error. `test_alacritty_hints.py` pins it.
- 🔴 **The hint regex is deliberately LOOSER than the scanner.** Rust's regex crate has
  no lookaround, so `{1,6}` swallows a whole hex colour rather than `{1,5}` matching five
  digits of `#282828` and offering "task 28282". The handler is the authority.
- 🔴 **`xdotool key --window <id>` uses `XSendEvent`, which winit/Alacritty IGNORES.**
  Two automated click tests reported **false negatives** before the third worked; it even
  echoed a stray `^A` into the shell. Only XTEST (`xdotool key`, no `--window`, against
  the FOCUSED window) delivers real input. Stopping at attempt two would have reported a
  working feature as broken.
- 🔴 **The branch-protection escape hatch does NOT round-trip.** `DELETE
  …/protection/required_status_checks` cannot be undone with `PATCH` — it 404s
  "Required status checks not enabled". Re-enabling needs a full `PUT …/protection`
  with the ENTIRE object reconstructed (`enforce_admins`, `strict`, and the `app_id`
  pinning). A restore-in-a-trap reported OK and had silently failed; `main` would have
  been left permanently unprotected. Capture the full config BEFORE deleting.
- **#1058 was closed in favour of #1060** — a competing fix. Mine relaxed the guard to
  tolerate ambiguity; #1060 removed the ambiguity via `_AMBIGUOUS_TERM_OWNER`, keeping
  BOTH picker rows and attribution. Decider: `ask` is the highest-traffic term in the
  config (58 fires); #1058 would have permanently blinded telemetry on it. `search_terms`
  serves two consumers with opposite needs — the picker wants recall, `_attribute` wants
  precision.
- **Two merges went AROUND the gate**, not through it (#1060, #1011) — via the escape
  hatch, with a local both-tier `nix build` substituting. #1067 is the only one that
  passed the real gate; Tekton and the local run agreed to the exact test counts.
- 🔴 **My own error worth not repeating:** #1011 verified the `#task-N` URL *resolved*
  and the DOM id *existed*, then INFERRED navigation and recorded "anchor: verified and
  used". Nothing ever loaded the page and watched it move. **An id that exists is not an
  anchor that works.** That is what #440 now is.
- **Alacritty `keyboard.bindings` `chars` serialise as TOML LITERAL strings** (`''`)
  on the current flake pin, where the older deployed copy had basic strings. Proven
  pre-existing by a before/after control, NOT caused by #1011. Whether Alacritty still
  honours `chars` in that form is **unverified** — that's Ctrl+Backspace and word-motion.

## How to verify
```bash
# the shipped scanner + handler, no browser opened
/nix/store/*-alacritty-mention-open --print '#370'          # -> both candidates
/nix/store/*-alacritty-mention-open --print 'devrc#1011'    # -> the GitHub issue
/nix/store/*-alacritty-mention-open --print '#282828'       # -> "no mention in the clicked text"

# both required tiers, the tier Tekton actually gates on
cd ~/workspace/devrc && nix build .#checks.x86_64-linux.pytests .#checks.x86_64-linux.nodetests --no-link
```
In a terminal: `Ctrl+Shift+M` labels every mention; `Ctrl+Shift+O` is the URL hint.
🔴 Read the `TOTAL collected=` / `RESULT:` lines out of `nix log <drv>` — an exit code
through a pipe is `tail`'s, not the gate's.
