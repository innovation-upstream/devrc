# Handoff: discord-embed-ext-rescue — 2026-08-24

## Run this first — the index, one read-only command
```bash
python3 ~/workspace/devrc/scripts/lib/subsystem_recall.py --repo /home/zach/workspace/devrc
```
Terse pointers this doc does not carry, curated by past sessions and outliving it.
🔴 RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
reading, and it may describe a gotcha already fixed. `scope-absent`/`scope-empty` means
nothing is recorded yet: ordinary, not an error, and not a clean bill of health.
Non-blocking: if it exits non-zero, print the stderr line and carry on.

⚠ **No `clawgate-task:` field on purpose.** `clawgate_handoff.sh resolve` exited **5**
(NOTHING RESOLVED, 0 tasks). An unknown session id answers 200 with an empty array, so
that cannot distinguish "this session touched no task" from "the id is wrong" — it is not
a clean bill of health, and the skill forbids minting a task to fill the blank.

## Goal
Decide what to do with `scripts/discord-embed-ext/` — 13 untracked files plus one
uncommitted line in `scripts/run-node-tests.sh`, orphaned in the shared base clone by a
dead session. Outcome: preserve everything, land the salvageable half properly, discard
the part that was measurably dead.

## State now
- **Branch:** `feat/discord-embed-enlarge`, head `8635f168`, 10 commits.
- **PR #804 — OPEN, `mergeable=MERGEABLE`, `mergeStateStatus=CLEAN`.** Both required
  checks green: `tekton/devrc-nodetests` SUCCESS, `tekton/devrc-pytests` SUCCESS.
  Merges clean into `origin/main` `324693fd` (re-checked at handoff time; main moved
  four times during this work).
- **NOT MERGED, deliberately.** See "Open investigations" — a second, independent
  implementation of the same feature is in flight and does not know this PR exists.
- **Preserved, nothing discarded:**
  - `rescue/discord-embed-ext` `42563d57` — the original 13-file orphan, verbatim. It had
    never been rescued before; this was the only copy.
  - `rescue/discord-embed-ext-concurrent` `14be8300` — two snapshots of a *different*,
    concurrent rebuild found in the shared clone (see below).
  - `rescue/initiative-scan-resolved-filter` — a third stranded item (`initiative-scan.py`,
    67 insertions) that turned out to be already preserved by an earlier session; my
    duplicate branch was deleted.
- **Shared base clone left exactly as found**: `scripts/run-node-tests.sh` modified and
  `scripts/discord-embed-ext/` untracked are **another agent's live WIP**, not mine.
  I hold no worktrees.
- **What landed in the PR:** the enlarge + lightbox half only. 118 tests, floor 113.
  Merged-tree runner: 1320 tests, 0 fail. Nix deploy path added
  (`home.activation.discordEmbedExtension`), which the code never had.

## Open investigations — live diagnosis state

### A second, independent implementation of this feature is in flight
- **Symptom + exact repro:** `git -C /home/zach/workspace/devrc status -s` shows
  `M scripts/run-node-tests.sh` and `?? scripts/discord-embed-ext/`. That is **not** the
  orphan this PR rescued — it is different code being actively written.
- **Observed (with values):**
  - 13 files, layout `tests/fixtures/discord_embeds.html` (this PR uses
    `tests/discord_embeds.html`); `extension/save_button.js` and
    `tests/save_button.test.mjs` present.
  - First sighting 18:06–18:12; re-checked 21:57 — **6 of 13 files had changed**
    (mtimes 19:27–19:30), so it is active, not abandoned.
  - Its `SUITES` line is `"scripts/discord-embed-ext/tests|3|50"`; this PR's is
    `"scripts/discord-embed-ext/tests|2|113"`. That table is pinned two-way, so only one
    can be right and whichever lands second must reconcile by hand.
  - **No branch and no PR on origin.** Verified by enumerating every remote head for
    files under `scripts/discord-embed-ext`: only `feat/discord-embed-enlarge` (11),
    `rescue/discord-embed-ext` (13), `rescue/discord-embed-ext-concurrent` (13).
- **Ruled out:**
  - "it is my old orphan restored" — 8 of 13 files match neither the rescue branch nor
    the PR; the directory layout differs.
  - "a Claude session is writing it" — no transcript under `~/.claude/projects` (772
    files, searched all project dirs, 8h window and unbounded) references
    `discord-embed-ext/tests/fixtures` or `save_button.test.mjs`. Three `opencode`
    processes have the base clone as cwd.
- **Leading hypothesis:** an **opencode** agent is rebuilding it. That is why it is not
  addressable via `SendMessage` — `ListAgents` shows 30 Claude peers, none of them it.
- **Next probe:** ask the operator who dispatched it, or
  `pgrep -af opencode` and read that agent's own session log. Do **not** delete or modify
  the working-tree copy; it is preserved on `rescue/discord-embed-ext-concurrent` but the
  live copy is someone's in-progress work.

### The extension has never been loaded in Brave
- **Symptom + exact repro:** "installs cleanly as an unpacked extension" is untested.
  `brave://extensions` → Developer mode → **Load unpacked** →
  `~/.local/share/discord-embed-ext` has never been performed.
- **Observed (with values):** everything else WAS verified against a real Chromium by
  injecting the shipped sources into a live page: attachment enlarged
  (`data-dee-enlarged="1"`), avatar in the same row untouched (`null`), the 400px cap
  actually gone (computed `max-width: none`), lightbox opened by a real bubbling click,
  `position: fixed`, `z-index: 2147483647`, covers viewport, Escape closes. The container
  used a **hashed** class (`imageWrapper__74e4d`), so class-name independence is measured,
  not assumed.
- **Ruled out:** nothing — this is a gap, not a failure. No evidence of a problem.
- **Leading hypothesis:** it will load; the manifest is MV3-valid, zero `permissions`,
  no `host_permissions`, no `background`.
- **Next probe:** after a `home-manager switch`, do the three-step Load unpacked above and
  open a Discord channel that has an image attachment.

## Next steps (ranked)
1. **Reconcile with the concurrent rebuild before merging #804.** `IN FLIGHT: devrc#804`.
   Touches `scripts/discord-embed-ext/**` and `scripts/run-node-tests.sh` SUITES. Merging
   first strands a live effort; the two measurements that apply to *either*
   implementation are in the PR thread (comment `#issuecomment-5404745780`).
2. **Merge #804** once (1) is settled — it is `MERGEABLE`/`CLEAN` with both required
   checks green, and merges clean into `324693fd`.
3. **`scripts/ship.sh`**, then the manual `Load unpacked` step, then open a Discord
   channel with an image to close the second investigation above.
4. **Decide `rescue/initiative-scan-resolved-filter`** — a stranded
   `--exclude-slugs`/`--include-resolved` feature for `initiative-scan.py`, 67 insertions,
   **zero tests**, and it changes DEFAULT behaviour: an initiative whose handoff merely
   contains the word "DONE" is hidden unless `--include-resolved` is passed. Needs a
   regression test shown red before it goes anywhere.
5. **Prune the rescue branches** once each is landed or explicitly abandoned:
   `rescue/discord-embed-ext`, `rescue/discord-embed-ext-concurrent`.

🔴 **This list is a WORK QUEUE WITH NO LOCK** — every `/resume` session draws
from it, so a *better* ranked list produces *more* duplicate work, not less.
Make each item cheap to check: name the repo and the files it will touch, and
**mark anything in flight `IN FLIGHT: <repo>#<pr>`** rather than leaving it
looking unclaimed. Worktrees do NOT prevent this. 📖 the measurements and the
refutation: `~/.claude/skills/handoff/reference/shared-queue.md`.

## Gotchas / decisions / dead-ends

- 🔴 **The save button was DEAD ON ARRIVAL, not unfinished — this is why the PR drops it
  rather than fixing it.** `save_button.js` posts to dl-router's sidecar with no
  `Authorization` header; `server.py` calls `_guard()` first in both `do_GET` and
  `do_POST` and `_auth_ok()` requires `Bearer`, `/healthz` included. Probed live:
  `GET /healthz` no token → **401**, with token → **200**. So `checkSidecar()` always
  resolves `{available:false}`, the observer never starts, the button never appears. And
  `POST /match` is **classify-only** — it returns a suggested directory and saves nothing
  — so setting `"Saved!"` on `resp.ok` reports a save that never happened.
- 🔴 **A host-only media pattern enlarges avatars.** Measured against two real logged-in
  Discord channels: the original pattern matched **59 of 60** `<img>`/`<video>` (avatars
  24, server icons 35, attachments **0**) and would have enlarged **10 user avatars**.
  The fix requires an `/attachments/` or `/external/` path prefix. Positive control: in a
  second channel the narrowed pattern matched **1** — a real attachment in a container
  capped at `max-height: 350px`. A bare zero would not have distinguished "correctly
  excludes chrome" from "matches nothing".
- 🔴 **A fixture that normalises away what the code normalises is not a test of that
  code.** `fake_discord_dom.mjs` lowercased `tagName`; a real HTML document reports it
  UPPERCASE. That voided **seven** `.toLowerCase()` guards at once — each deletable with a
  fully green suite, each deletion making the extension **completely inert in Brave**.
  Three earlier audit rounds had already fixed three *other* instances of this same class
  in that same file (a no-op `addEventListener`, a readable closed shadow root, a missing
  `nodeType`) and still did not go looking for the fourth.
- 🔴 **A defect can be a SHAPE, not a site.** Fixed one content script's module-scope
  entry point and left the other open; pinned `didDrag`'s SET and not its RELEASE; pinned
  the backdrop's `click` arm while `wheel` and `mousedown` stayed silently deletable. Each
  fix landed one level short of its own class, and each was caught by the *next* round.
- 🔴 **The recurring defect across nine rounds was never a logic bug — it was claims
  stated wider than what was measured.** "Six regression tests" (five: the RED control
  carried all six defects at once, so one test failed on a *different* assertion and the
  transform half was credited from the same red line). "25 mutants, 24 killed" (an
  independent battery found eight more survivors). "Four voided guards" (seven). A floor
  comment whose own arithmetic contradicted its stated result. Two files disagreeing about
  one measurement. A survivor count attributed to the wrong sha. **The count regrew wrong
  twice inside the paragraph warning about it** — which is why that list is now NUMBERED
  and the prose tells the reader to count it rather than trust a total.
- **Nine audit rounds, each finding less than the last; the ninth found nothing.** That
  was the stop condition. Rounds 1–8 each found something the previous had missed.
- **`strict` is false on branch protection**, so a green check on the branch is not a
  claim about the tree the merge creates. The merged tree was gated separately twice —
  once through a real `run-node-tests.sh` conflict (`main` raised clickup's floor to 163
  while this PR appended its own entry) that the PR's own green could never have shown.
- **The base clone is shared by 8+ live agents.** The original handoff's claim that "no
  process has the base clone as cwd" was **stale** by the time I acted on it. Never stage
  in that index; use a worktree. Its dirty state is other people's work.
- **`node --test <dir>` does not work here** — it yields a bogus `# tests 1` and a
  failure. Pass explicit files. Node 26 prints `ℹ tests N`, not TAP `# pass`.
- **zsh does not word-split unquoted parameters** — bit me mid-verification: a
  `set -- $t` loop printed blanks for all three gate results instead of erroring.

## How to verify
```bash
# 1. the PR is still mergeable and both required checks are green
gh pr view 804 --json mergeable,mergeStateStatus,statusCheckRollup \
  --jq '"\(.mergeable) \(.mergeStateStatus)", (.statusCheckRollup[]|"\(.name): \(.conclusion)")'

# 2. this suite, on the branch
git -C /home/zach/workspace/devrc worktree add /tmp/dee-verify 8635f168
node --test /tmp/dee-verify/scripts/discord-embed-ext/tests/*.test.mjs   # expect 118 pass, 0 fail

# 3. the MERGED tree, which is the claim that matters (strict=false)
git -C /tmp/dee-verify merge --no-commit --no-ff origin/main   # expect clean
nix develop /tmp/dee-verify --command bash /tmp/dee-verify/scripts/run-node-tests.sh
#    expect: PASS scripts/discord-embed-ext/tests (files=2 tests=118 floor=113), RESULT: PASS

# 4. the GATED tier — the one Tekton runs and the merge depends on
nix build /tmp/dee-verify#checks.x86_64-linux.nodetests --no-link
nix build /tmp/dee-verify#checks.x86_64-linux.pytests  --no-link
git -C /home/zach/workspace/devrc worktree remove --force /tmp/dee-verify

# 5. the save-button finding, if you doubt it (needs the sidecar running)
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8791/healthz            # 401
curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $(cat ~/.config/dl-router/token)" \
  http://127.0.0.1:8791/healthz                                                   # 200
```
