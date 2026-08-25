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
🔴 **ALL LANDED AND DEPLOYED.** Everything the previous version of this doc listed as
open is merged, shipped to both hosts, and verified by content.

- **Branch:** none — `feat/discord-embed-enlarge` merged and deleted. Base clone on
  `main` at `origin/main`.
- **Merged (verified by CONTENT, never ancestry — a squash is never an ancestor):**
  - `#804` → squash `eaf68c96` — the extension. 11 files on `origin/main`, SUITES entry
    `scripts/discord-embed-ext/tests|2|113` present.
  - `#818` → squash `5d6ebb43` — this handoff doc.
  - `#832` → squash `4500b88c` — `claude/RULES.md` worktree mandate reworded (below).
- **Deployed and verified on BOTH hosts** — `scripts/ship.sh`, cross-host agreement
  CONFIRMED (not the one-host `NOT COMPARED` case): both at `f82272c3`.

  | check | workbench | laptop |
  |---|---|---|
  | `~/.local/share/discord-embed-ext` | `embed_enlarge.js lightbox.js manifest.json icons` | same |
  | `save_button.js` present? | **no** — correct build | no |
  | reworded rule in `~/.claude/RULES.md` | ✓ | ✓ |
  | reworded rule in `~/.config/opencode/AGENTS.md` | ✓ | ✓ |

- **clawgate task 357 FILED (open, untouched)** — "Handoff next-steps is a work queue
  with no claim step". Solution design is deliberately IN SCOPE for the pickup. Its body
  was updated post-creation with two measured facts (see Gotchas).
- **Four rescue branches on origin, nothing discarded:**
  `rescue/discord-embed-ext` `42563d57` (the original orphan) ·
  `rescue/discord-embed-ext-concurrent` `63b8e22c` (three snapshots of the concurrent
  rebuild) · `rescue/discord-embed-ext-committed-to-main` `74f3a389` (that rebuild as it
  was finally committed) · `rescue/initiative-scan-resolved-filter` (pre-existing; the
  `--exclude-slugs` work has since landed independently as `#824`).
- **Carried forward from the pre-merge version of this section** (still true, and the
  REPLACE would otherwise drop it): what landed is the enlarge + lightbox half only —
  118 tests, floor 113, merged-tree runner 1320/0 — and the nix deploy path
  `home.activation.discordEmbedExtension` in `nix/home.nix`, which the code never had.
- **Base clone tree:** two untracked files that are NOT mine and NOT this work —
  `nix/system/apply-nebula-443.sh.LOCAL-preserved-2026-08-02`,
  `scripts/dl-router/tests/load_test_store.sh`. Left alone.

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

### RESOLVED — the concurrent rebuild ended up COMMITTED TO `main` in the shared clone
- **Symptom + exact repro:** `git -C ~/workspace/devrc merge --ff-only origin/main` →
  `hint: Diverging branches can't be fast-forwarded`. HEAD `74f3a389`, origin `b242fc2d`.
- **Observed (with values):** `git rev-list --count origin/main..HEAD` = **1**;
  `HEAD..origin/main` = **9**. The one local commit was
  `74f3a389 feat(discord): add discord-embed-enlarge extension` — 13 files including
  `extension/save_button.js`, `tests/save_button.test.mjs` and
  `tests/fixtures/discord_embeds.html`, plus `scripts/run-node-tests.sh`. That is the
  OTHER agent's duplicate implementation, committed onto the deploy-target branch.
- **Why it mattered:** this is the 🔴 failure `devrc/CLAUDE.md` records twice
  (2026-08-06, 2026-08-09) — `ship.sh` converges with `merge --ff-only`, so a diverged
  host is skipped and left as found, then silently stops receiving every future change
  while still looking healthy.
- **Resolution — preserve, verify, THEN move the pointer.** Never reset first:
  `git branch rescue/discord-embed-ext-committed-to-main 74f3a389` → push → verify on
  origin from an independent read (same sha **and** same tree OID `2fe66a1a`, 13 files)
  → `git reset --keep origin/main` (`--keep` refuses rather than destroys). Confirmed
  after: HEAD == origin/main, `merge --ff-only` → `Already up to date`, and the
  preserved commit still reachable.

### STILL OPEN — the extension has never been registered in Brave
- **Symptom + exact repro:** "installs cleanly as an unpacked extension" is UNTESTED.
  `brave://extensions` → Developer mode → **Load unpacked** →
  `~/.local/share/discord-embed-ext` has never been performed on either host.
- **Observed (with values):** the directory now EXISTS on both hosts with exactly
  `embed_enlarge.js lightbox.js manifest.json icons` (verified post-`ship.sh`).
  Everything else was verified against a real Chromium by injecting the shipped sources
  into a live page: attachment enlarged (`data-dee-enlarged="1"`), avatar in the same row
  untouched (`null`), 400px cap gone (computed `max-width: none`), lightbox opened by a
  real bubbling click, `position: fixed`, `z-index: 2147483647`, covers viewport, Escape
  closes — against a container using a HASHED class (`imageWrapper__74e4d`).
- **Ruled out:** nothing — this is a gap, not a failure. No evidence of a problem.
- **Leading hypothesis:** it loads. MV3-valid manifest, zero `permissions`, no
  `host_permissions`, no `background`.
- **Next probe:** do the three-step Load unpacked above, then open a Discord channel
  that has an **image attachment** (not just avatars — a channel with only avatars/icons
  will correctly show NO enlargement and reads as a failure).

## Next steps (ranked)
1. **The manual `Load unpacked` step** — the only untested claim in the chain. Three
   clicks, `~/.local/share/discord-embed-ext`, then a Discord channel with a real image
   attachment. Nothing else can close it; nix cannot register an unpacked extension.
2. **clawgate task 357** — the queue-collision cause. `IN FLIGHT: none`, unclaimed,
   solution design in scope. devrc, lands in `claude/skills/` + possibly `RULES.md` +
   `scripts/`. 🔴 Read its Non-goals first: "do not add more prose and call it done".
3. **Decide the four `rescue/*` branches** — all four are backups of work that is either
   landed or superseded. `rescue/initiative-scan-resolved-filter` is now redundant
   (`#824` landed that work). Prune what is genuinely dead; each is one `git push
   --delete`.
4. **Reconcile the duplicate implementation, or close it out** — `#804` landed the
   enlarge+lightbox half; `rescue/discord-embed-ext-committed-to-main` holds the other
   agent's full version including the save button. The save button is measurably dead
   (401, below), so the realistic outcome is "delete the branch", but that is the other
   author's call.

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

- 🔴 **A clawgate `PATCH /api/tasks/{id}` with `{"content": …}` returns HTTP 200, bumps
  `updatedAt`, and CHANGES NOTHING.** The write key is **`body`**. `task-api.md` line 14
  says "content + dispatch config", so the DOCUMENTED field silently no-ops. Measured on
  task 357: `{content:…}` → 200, body stayed 6687 bytes; `{body:…}` → body became 7870.
  A `-o /dev/null` on that first call would have shipped "task updated" as a false claim.
  The route returns the FULL updated task — read `.body|length` back, never the status.
- 🔴 **`error` is not `failure` on a required check.** `#818`'s two Tekton checks came
  back `error` with `COULD NOT RUN: <leg> — the gate stopped before this leg reported`,
  and NO PipelineRun existed in `tekton-ci` for that sha at all. That is a broken gate,
  not a bad diff — do not debug your change against it. The discriminator: `#804` showed
  `SUCCESS` on the same gate at the same moment. Remedy is a **fresh push** (an empty
  commit is enough); the checks then moved `ERROR → PENDING`, i.e. a real run existed.
- ⚠ **A gate that sits `PENDING` with a frozen log tail is usually NOT wedged.** Read
  CONTAINER STATE, not the log: `kubectl -n tekton-ci get pod <run>-gate-pod -o json |
  jq '.status.containerStatuses[]'` plus `kubectl top pod --containers`. On `#804`,
  `step-pytests` was at 1053m CPU while `kubectl logs` showed a tail from a *different,
  already-terminated* container. Pod-relative elapsed time is the honest clock — the gate
  is a documented 25–43 min check and I twice miscounted from the push instead.
- 🔴 **`AGENTS.md` is CONCATENATED, not imported, and that is deliberate.**
  `nix/home.nix:1414` builds `~/.config/opencode/AGENTS.md` from `PRINCIPLES.md` +
  `RULES.md` + `opencode-addendum.md`. **opencode does NOT expand `@`-imports** (measured
  on v1.18.4 with an all-tools-denied agent: an imported passphrase came back NONE, the
  same content inline came back verbatim), and `~/.claude/CLAUDE.md` is ~1.5 KB of import
  lines — so pointing opencode at it would deliver ZERO rules. Adding `devrc/CLAUDE.md`
  to that concat is NOT free: `test_opencode_config.py` pins a 100 KB ceiling and a
  331 KB `AGENTS.md` causes a permanent compaction loop.
- **So the earlier claim "opencode may never load the rule" was WRONG.** `RULES.md` IS
  delivered to opencode. The real gap was narrower: the worktree mandate's SUBJECT said
  "any **subagent**" and its only MECHANISM was `isolation: "worktree"` — a Claude
  Agent-tool parameter opencode cannot pass. A rule you can read but cannot execute reads
  as one not addressed to you. Reworded in `#832` to name ANY agent and give
  `git worktree add` as the runtime-neutral mechanism. `+172` bytes, **197 left** under
  the ceiling `scripts/tests/test_rules_size.py` owns — hence a reword in place, not a
  new paragraph.
- ⚠ **`#832` does NOT fix the duplicate WORK** and must not be read as progress on 357.
  `shared-queue.md` refutes worktrees for task-allocation collisions — every session
  colliding there was already correctly isolated. Two independent failures happened
  together: a task-allocation collision (357) and a filesystem one (`#832`).
- **Why the two agents collided, measured:** `handoff-handoff-skill-hardening.md`
  next-step #1 was drawn twice. The `IN FLIGHT` rule + `shared-queue.md` merged at
  **14:18**, home-manager generation **556** switched them live at **18:00:50**, and the
  collision started at **18:06** — six minutes later. I opened `#804` at 16:00, two hours
  before the rule was deployed, and never marked the item in flight afterwards. The only
  `IN FLIGHT` string in that doc is a section LABEL, not a claim on any item.
- **The `/handoff` writeback guard fires on a task you AUTHORED.** Reading task 357 back
  to verify the create linked this session to it; the Stop hook then demanded a
  write-back for work that was never 357's. Correct action is `--dismiss <id> --session
  <uuid>`, NOT a comment: any `claude-code` comment permanently silences that guard for
  the card. To add findings to a task you filed, **PATCH the body** — authoring — rather
  than commenting.

## How to verify
```bash
# 1. all three merges are on main, by CONTENT (a squash is never an ancestor)
cd /home/zach/workspace/devrc && git fetch origin -q
git ls-tree -r --name-only origin/main -- scripts/discord-embed-ext | wc -l      # 11
git show origin/main:scripts/run-node-tests.sh | grep 'discord-embed-ext/tests|' # |2|113
git show origin/main:claude/RULES.md | grep -c 'standing default for ANY agent'  # 1
test -f claudedocs/handoff-discord-embed-ext-rescue.md && echo "handoff doc present"

# 2. BOTH hosts deployed — nix decides this, git does not
for h in "" "ssh zach@10.42.0.100"; do
  $h ls ~/.local/share/discord-embed-ext/                      # 4 entries, NO save_button.js
  $h grep -c 'standing default for ANY agent' ~/.claude/RULES.md          # 1
  $h grep -c 'standing default for ANY agent' ~/.config/opencode/AGENTS.md # 1  <- the point of #832
done

# 3. the suite, on main
node --test scripts/discord-embed-ext/tests/*.test.mjs        # 118 pass, 0 fail

# 4. the save-button finding, if ever doubted (needs the sidecar up)
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8791/healthz   # 401
curl -s -o /dev/null -w '%{http_code}\n' \
  -H "Authorization: Bearer $(cat ~/.config/dl-router/token)" \
  http://127.0.0.1:8791/healthz                                          # 200
```
