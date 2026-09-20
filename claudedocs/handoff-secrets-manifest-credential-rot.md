# Handoff: secrets-manifest-credential-rot — 2026-09-19

## Run this first — the index, one command
```bash
cairn recall --repo /home/zach/workspace/devrc
```
Terse pointers this doc does not carry, curated by past sessions and outliving it.
🔴 RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
reading, and it may describe a gotcha already fixed. `scope-absent`/`scope-empty` means
nothing is recorded yet: ordinary, not an error, and not a clean bill of health.
Non-blocking: if it exits non-zero, print the stderr line and carry on.

## Goal

`SECRETS.md` exists to make a new-host bootstrap deterministic instead of manual
archaeology (`SECRETS.md:7`). A row in it told a reader to `rm` a **live, paid**
credential. This arc is about the class, not that one row: every credential this host
holds should appear in that manifest, and no row should assert something false about one.

- **closing-condition:** `check` — for every OpenRouter-class credential on this host,
  `SECRETS.md` carries a row, and every factual claim in the `~/.config/repo-cos/env` row
  re-derives TRUE by running the commands in "How to verify". Concretely, the
  enumeration in that section must return **no file holding a live credential that has
  no row**. 🔴 Currently **NOT met** — `~/.local/share/opencode/auth.json` and the
  drafter's SMTP pair are both undocumented (ranks 1 and 2).
  This is frozen as the condition this arc was opened on.

## State now

- ✅ **`devrc#1797` MERGED** (`12e6a10f`, squash) — verified by CONTENT with a negative
  control: the corrected reasoning and the restored bootstrap step are on `origin/main`
  and absent at `main~1`. (Unchanged from the previous session; kept because the rows
  below build on it.)
- 🔶 **RANK 1 IS IN FLIGHT, NOT DONE — `devrc#1805` is OPEN.**
  `docs/secrets-opencode-auth-row`, head `0e74f435`, `MERGEABLE`/`UNSTABLE`. It adds the
  missing `SECRETS.md` row for `~/.local/share/opencode/auth.json`, a pointer to it from
  the `repo-cos/env` row, and a new bootstrap step 7 (list renumbered 1..10, no gaps).
  As of 2026-09-20T03:38Z all **4** Tekton statuses on that sha are `pending` — nothing
  has reported, so this carries NO verdict yet. 🔴 Read it SHA-pinned
  (`gh api repos/innovation-upstream/devrc/commits/0e74f435.../status`), not with a bare
  `gh pr checks` — a rollup resolves its sha at call time and can answer for the
  pre-push head.
- 🔴 **The finding rank 1 was filed on was UNDERSTATED, and the correction is the point.**
  The undocumented key is not a spare — it is the one carrying the spend. Measured
  2026-09-20 on workbench against the issuer's own free `/api/v1/key` endpoint:
  `~/.local/share/opencode/auth.json` → HTTP 200, `is_free_tier: false`, `usage`
  **71.27** of `limit` 50; `~/.config/repo-cos/env` → HTTP 200, `is_free_tier: false`,
  `usage` **7.02** of 50. Different sha256 digests (deliberately NOT recorded in
  `SECRETS.md` or here — devrc is public; re-derive them). So "rotate the OpenRouter
  key" was not merely ambiguous: it was likely to resolve to the WRONG file, moving ~9%
  of the traffic and leaving the rest on the old credential.
- **Deploy/verify status:** nothing deployed; this is documentation. `#1805` is verified
  by test run and by content in its own branch, and is **NOT merged** — do not report it
  as landed.
- ⚠ **No `clawgate-task:` field, again.** `clawgate_handoff.sh resolve` exited **5** with
  its positive control passing (the same endpoint answered 2 links for a different
  session, so the board is reachable and the token is accepted). That proves a CORRECT
  id would have resolved; it does NOT prove the id under test is right. Not a clean bill
  of health, and no field was written.
- ⚠ **The two untracked `claudedocs/scope-chief-*.md` files are still in the primary
  clone**, still another session's. Left alone.
- **Claim state:** `secrets-manifest-credential-rot-1` is HELD by this session (rank 1,
  released only when `#1805` merges or is abandoned). Rank 3 is held by a DIFFERENT
  session under its own slug — see the investigation block.

## Open investigations — live diagnosis state

### ⚠ OPEN — `tekton/devrc-pytests` is RED on `main`, and it is version drift, not a defect
- as-of: 2026-09-19

- **Symptom + exact repro:**
  ```bash
  git -C "$DEVRC" worktree add --detach /tmp/ctl origin/main
  python3 -m pytest /tmp/ctl/scripts/tests/test_opencode_engine.py \
    -k test_engine_is_the_version_every_measurement_is_keyed_to -q
  git -C "$DEVRC" worktree remove --force /tmp/ctl
  ```
- **Observed (with values):** `1 failed, 24 deselected`. The assertion, verbatim:
  *"opencode on PATH is '1.18.30', but every 'measured on v1.18.29' claim in
  `scripts/opencode/opencode.jsonc`, `scripts/opencode/README.md` and
  `scripts/tests/test_opencode_config.py` is keyed to '1.18.29'."*
  The Tekton status description at `dc7bbfdb` reports
  `TOTAL collected=24011 passed=24006 skipped=4` with this as the single failure.
- **Ruled out — that `devrc#1797` caused it.** The same test fails at `origin/main`
  (`a67db5b5`) with the change absent, and it reads `opencode --version` off the host
  while the PR touches only `SECRETS.md`. `via: measurement` (control worktree)
- **Ruled out — that it is one of the three base-red `escrow_verify` failures found
  earlier in the session.** Different file, different test; those three are a separate
  pre-existing red. `via: measurement`
- **Leading hypothesis:** the host's `opencode` was upgraded 1.18.29 → 1.18.30 and the
  pinned measurement claims were never re-keyed.
- **Next probe:** 🔴 the test's own message says there are TWO causes needing OPPOSITE
  fixes and warns *"FIRST run the DISCRIMINATING check … do not guess"*. Read that message
  in full and run the check it names before editing any pin:
  ```bash
  python3 -m pytest "$DEVRC/scripts/tests/test_opencode_engine.py" \
    -k test_engine_is_the_version_every_measurement_is_keyed_to -q 2>&1 | sed -n '1,60p'
  ```

### ⚠ OPEN — nothing mechanically checks any factual claim in `SECRETS.md`
- as-of: 2026-09-19

- **Symptom + exact repro:** three false assertions shipped in this file across one
  ladder (a uniqueness claim about host credentials, a citation blind to its own commit, a
  bootstrap step whose remedy did not prevent the error it named). All three were caught
  by human/agent reading. None could have been caught by a gate.
- **Observed (with values):** 8 test files reference `SECRETS.md`
  (`git grep -ln 'SECRETS\.md' -- scripts/tests`). The ones that scan its content are
  **content-leak scanners** — `test_no_client_hostnames.py`, `test_no_public_ips.py` —
  which catch a pasted key or a client hostname and are blind to every factual claim.
  `test_doc_path_rot.py` **excludes** `SECRETS.md` by name: `CORPUS_DIRS = ("claude",
  "CLAUDE.md")` at `:170`, with the exclusion stated at `:165-169` — *"`docs/`,
  `README.md` and `SECRETS.md` are out … they are read by humans, who notice a 404."*
- **Ruled out — that adding `SECRETS.md` to the doc-rot corpus would have caught this.**
  The defect was never a dead path: the path existed and the *assertion about it* was
  false. Doc-rot cannot see that. `via: code` (the guard resolves paths, nothing more)
- **Ruled out — that a numbering/structure guard is worth adding.** No numbering defect
  has ever shipped in this file — `bac41175` renumbered correctly and so did `20cf2503` —
  so such a guard would be an invariant guard, not regression coverage. `via: measurement`
- **Leading hypothesis:** this class is not mechanically guardable, and the honest move is
  to record the gap rather than paper it with a green check. The one thing that DID work
  was an adversarial read with a control.
- **Next probe:** decide explicitly — either build a check that could have caught one of
  this ladder's three real defects, or write down that it is not worth building. Either
  closes rank 7.

### The opencode pin: the DISCRIMINATING check has now been RUN, and rank 3 is held by another session
- as-of: 2026-09-20

🔴 **This supersedes the status half of the earlier block
"⚠ OPEN — `tekton/devrc-pytests` is RED on `main`, and it is version drift, not a
defect" (as-of 2026-09-19). That block's diagnosis stands — it IS version drift, not a
defect. What is now stale in it is its `Next probe`, which has been executed, and its
implicit assumption that the reader should act. DO NOT act on it: the work is claimed
and has an open PR.** (The tool appends to this section and cannot edit the older
heading, so this paragraph is the retirement marker — grep that heading and read this
block beside it.)

- **Symptom + exact repro:** unchanged from the earlier block; that block's repro is
  still correct.
- **Observed (with values), 2026-09-20:**
  - **Login shell:** `command -v opencode` → `~/.nix-profile/bin/opencode`;
    `readlink -f` → `/nix/store/6pw7n475sa1d4scq8sy1qkdn2bcy0glc-opencode-1.18.29/bin/opencode`;
    `opencode --version` → **`1.18.29`**.
  - **Dev shell** — the one the gate runs in:
    `nix develop "$DEVRC" --command opencode --version` → **`1.18.30`**.
  - `nix profile list | grep -i opencode` → **no entry**. Both
    `/nix/store/*-opencode-1.18.29` and `*-opencode-1.18.30` are realised.
  - The test **PASSES** in the login shell (`1 passed, 24 deselected`) and the two
    versions disagree only across shells.
- **Ruled out — that this is the HOST problem the assertion message names first**
  (an imperative `nix profile` entry winning PATH). There is no profile entry at all,
  and PATH resolves into a store path. It is the LOCK MOVEMENT arm.
  `via: measurement`
- **Ruled out — that the pin should be left at 1.18.29.** The gate runs in the dev
  shell and the dev shell is 1.18.30, so the pin must move to 1.18.30 for `main` to go
  green. `via: measurement` (the `nix develop` read above)
- **Leading hypothesis:** `flake.lock` moved opencode 1.18.29 → 1.18.30 (the lock bump
  `b724646e`), the dev shell picked it up immediately, and `~/.nix-profile` has not been
  switched to it. **Consequence worth carrying:** bumping `PINNED_VERSION` to 1.18.30
  greens the gate and makes the test RED in the login shell on this host until a
  `home-manager switch` lands 1.18.30 in the profile. Both halves are expected; say so
  rather than treating the login-shell red as a regression.
- 🔴 **UNEXPLAINED, and recorded rather than guessed at:** the FIRST run of this test in
  this session (≈03:31Z, plain login shell, no `nix develop`) reported `1.18.30`; later
  runs from the same shell reported `1.18.29`. The home-manager profile moved twice
  tonight (`~/.local/state/nix/profiles/`, gens 2359 and 2360, 22:29 local). I could not
  establish which switch produced which read, and I did not dig further because the item
  is another session's. **Do not adopt a cause for this; it has none yet.**
- **Next probe:** none from here — **the item is CLAIMED**. `claim-work --list` shows
  `opencode-version-pin-1-18-30` (Zachary Lowden) and **`devrc#1803`
  (`fix/opencode-pin-1.18.30`) is OPEN**. The useful action is to hand the
  login-shell/dev-shell split above to that session, not to re-derive it.

## Next steps (ranked)

1. **Merge `devrc#1805`** — the `SECRETS.md` row for
   `~/.local/share/opencode/auth.json`. **Carried forward from the original rank 1, so
   the values survive the replace:** measured 2026-09-19 and re-measured 2026-09-20 —
   mode **600**, 131 bytes, one provider object `openrouter` with `type`/`key`, key
   **73 chars**, a DIFFERENT value from `~/.config/repo-cos/env` (also 73 chars).
   IN FLIGHT: `innovation-upstream/devrc#1805`,
   head `0e74f435`. Gate was all-`pending` at 03:38Z; read it SHA-pinned before
   merging. Consider `/audit-pr 1805` first — this arc's own history is that two of
   three defects in the last ladder were INTRODUCED by the previous round's fix, and
   both were prose. Merging this closes HALF the closing condition.
   forcing: security — an undocumented live credential on the host
2. **The drafter's Gmail/SMTP credential is documented NOWHERE in `SECRETS.md`.**
   `REPO_COS_SMTP_USER` / `REPO_COS_SMTP_PASSWORD` (`scripts/task-spec-drafter/email_send.py:131-132`)
   over SOPS `mailbox-gmail-imap` key `IMAP_APP_PASSWORD` (`:16`, `:46`). `grep -in
   'gmail\|IMAP\|SMTP' SECRETS.md` returns **0**. Hole predates this arc (`bac41175`);
   `SECRETS.md:33` already has the pattern for a credential with no local file.
   **This is now the ONLY item between this arc and its closing condition.**
   forcing: security — an undocumented credential, same class as rank 1
3. **Re-key the opencode measurement pins — CLAIMED BY ANOTHER SESSION, DO NOT START.**
   IN FLIGHT: `innovation-upstream/devrc#1803` (`fix/opencode-pin-1.18.30`); claim
   `opencode-version-pin-1-18-30`. The discriminating check has been run — see the
   investigation block — and the direction (pin → 1.18.30) is confirmed correct for the
   gate. What is left for whoever holds it: relay the login-shell/dev-shell split so the
   expected login-shell red is not misread as a regression.
   forcing: gate — `tekton/devrc-pytests` is failing on `main`
4. **Three `test_analyze_service_index_escrow_verify.py` tests are base-red**
   (`test_the_preflight_PASSES_when_every_module_resolves`, `…missing_bw…`,
   `…missing_identity…`). Re-confirmed 2026-09-20 in the `#1805` worktree: the failure
   is `AssertionError: 'DECRYPT-DEPS-MISSING' == 'IDENTITY-MISSING'`, and the selection
   runs **897 passed / 3 failed**, identical to the count this doc recorded a day
   earlier. Unrelated to this arc.
   forcing: gate — a red the pytests tier carries
5. **`nix/agent-handles.nix:27` exports `CIVITAI_CLI` at a clone stuck at 2026-06-18**
   with no `scripts/dogfood/`, so a cross-repo reference written against that handle does
   not resolve. The live checkout is `~/workspace/civit/cli`.
   forcing: none
6. **104 stale `.claude/worktrees/agent-*` checkouts in this clone.** Each is a full
   working tree; together they poison every recursive grep here — measured, a
   `grep -rn 'repo-cos/env'` returned **208 hits, all of them** in those worktrees, and
   the tracked tree has none.
   forcing: none
7. **Decide whether any mechanical check on `SECRETS.md` claims is worth building** —
   see the investigation block. Writing "not worth it" closes this.
   forcing: none

## Gotchas / decisions / dead-ends

### Added 2026-09-20 — one row, three audit rounds, and two defects introduced by the fixes

- 🔴 **RETIRING A CONSUMER DOES NOT RETIRE A CREDENTIAL.** `bac41175` removed the timer
  that used this key and recorded the *file* as dead. Check for a NEW reader before
  recording any credential file as dead — one had arrived (`<civitai/cli>/scripts/dogfood/runner.py`)
  and an older one had never left (`scripts/mail-actions/`).
- 🔴 **AN UNREFERENCED CREDENTIAL FILE IS THE ONE YOU MUST NOT DELETE ON AN
  UNREFERENCED-NESS ARGUMENT — and I got this exactly backwards for a full round.** My
  first fix argued *"nothing reads that file"* was FALSE. It was **TRUE**: `bac41175`
  deleted `scripts/repo-cos/run-weekly.sh:36` (`set -a; . "$ENV_FILE"; set +a`), the only
  thing that ever sourced it, and nothing sources it today. **That is what makes `rm`
  dangerous, not what makes it safe** — no consumer opens the file, so deleting it breaks
  nothing *loudly*; the key just stops existing and surfaces later as a human who cannot
  find a credential. The row contradicted itself for a round: it refuted a claim about the
  FILE with evidence about the ENVIRONMENT VARIABLE, then conceded 40 words later that
  *"nothing sources this file automatically"*.
- 🔴 **A RESTORED STEP MUST RESTORE THE MECHANISM, NOT JUST THE ARTEFACT.** The
  re-added bootstrap step said creating the file prevents `ERROR: OPENROUTER_API_KEY not
  set`. It does not — nothing sources the file, so an operator who follows the step
  *exactly* still hits the error, **with the documented cause ruled out by the documented
  remedy**. Before `bac41175` the step worked only because a sourcer existed.
- 🔴 **TWO OF THE THREE DEFECTS THIS LADDER FIXED WERE INTRODUCED BY THE PREVIOUS
  ROUND'S FIX**, and both were prose, not code. Budget for that shape; it is the norm on a
  docs change, not an accident.
- 🔴 **I CORRECTED AN UNDERCOUNT AND WAS STILL WRONG, ONE ROUND LATER, IN THE SAME
  DIRECTION.** "one gate reads this file" → corrected to "two" → actually **eight** files
  reference it. Sampling reported as a population, twice, inside a PR about a false claim.
  **Enumerate and name the population: `git grep -ln '<file>' -- scripts/tests`.**
- 🔴 **A POSITIVE CONTROL BUILT FROM A TEXTBOOK FIXTURE PROVES NOTHING.** Planting
  `10.1.2.3 nas.internal.example` into `SECRETS.md` left the hostname gate GREEN — the
  string was outside its vocabulary, not evidence the gate was blind. Rebuilt from the
  module's own `planted_host()` shape (a realistic internal subdomain): **red,
  `1 failed, 17 passed`, naming `SECRETS.md`**. Restore verified **byte-identical by
  sha256**, never by `git checkout --`.
- 🔴 **`git log -S '<string>' -- <file>` EMITS NO DIFF** — without `-p` you get a commit
  header and its message, so a citation offering it as "recovers the old text" returns
  nothing a reader wants. **Worse: if the doc EMBEDS the search string, the occurrence
  count never changes, so the pickaxe cannot see the commit that fixed the doc — the
  citation is permanently blind to itself.** Pin the sha: `git show <sha> -- <file>`.
- 🔴 **A RECURSIVE GREP IN THIS CLONE IS POISONED BY 104 AGENT WORKTREES.**
  `grep -rn 'repo-cos/env'` → **208 hits, every one** inside `.claude/worktrees/agent-*`,
  zero in the tracked tree. It read as "many things source this file"; the truth is
  nothing does. **Use `git grep` (tracked tree only) or a clean worktree** before drawing
  any conclusion about what this repo references.
- 🔴 **RUN THE CONTROL BEFORE THE THEORY.** Three tests failed right after my edit; a
  clean worktree at `origin/main` failed identically. Then CI failed on a FOURTH test I
  had never run — my "base-red" claim had been a targeted subset reported as the
  population. **A subset is not a population, and that error appears twice in this doc.**
- ⚠ **`docs(handoff):` commits land DIRECT on `main` in this repo** — measured over the
  recent history; the ones carrying `(#N)` are feature PRs that happened to touch a doc.
  A handoff does not need a PR here.

### Carried forward 2026-09-20 from `State now` — durable history the replace would have eaten

These were live-status bullets under a REPLACE heading and would have been deleted by
this update. They are history, not status, so they belong here.

- **What the `repo-cos/env` row said BEFORE `#1797`:** 🔴 *"NO CREDENTIAL FILE ANY MORE …
  nothing reads that file … Safe to `rm ~/.config/repo-cos/env`"*. Measured 2026-09-19
  against the key's own free `/api/v1/key` endpoint: the file exists (3 lines, mode 600,
  ONE assignment) and the key is valid and paid. **The instruction would have destroyed a
  working credential whose only other copy is the OpenRouter dashboard.**
- **Why it rotted:** `bac41175` retired the weekly `repo-cos` timer — the credential's
  *consumer* — and recorded the *credential* as dead. It also deleted the bootstrap step
  that creates the file, and renumbered.
- 🔴 **`#1797` WAS MERGED THROUGH A RED GATE, deliberately and on the record.**
  `tekton/devrc-pytests` was FAILING at `dc7bbfdb`; the other three statuses passed. The
  failure was
  `test_opencode_engine.py::test_engine_is_the_version_every_measurement_is_keyed_to`,
  reproducing identically in a clean worktree at `origin/main` with the change nowhere in
  the tree. The override and its control were posted on the PR before the merge. **That
  same red is rank 3** — now diagnosed (see the 2026-09-20 investigation block) and
  claimed by another session. Keep this record: it is the precedent a future session will
  cite, and it should be cited with the fact that the red was real and unrelated, not
  waved through.

### Added 2026-09-20 — the credential that had no row was the one spending the money

- 🔴 **AN UNDOCUMENTED CREDENTIAL IS NOT AUTOMATICALLY THE MINOR ONE — MEASURE USAGE
  BEFORE YOU RANK IT.** Rank 1 was filed as a completeness gap ("a file with no row").
  The issuer's own free `/api/v1/key` endpoint put **71.27 of 50** on the undocumented
  key and **7.02 of 50** on the documented one — ~91% of combined usage on the row that
  did not exist. The ambiguity was therefore not symmetric: the DEFAULT resolution of
  "rotate the OpenRouter key" was the wrong file. **One cheap read against the issuer
  reclassified the item from hygiene to security.**
- 🔴 **TWO CREDENTIAL FILES CAN FAIL IN OPPOSITE DIRECTIONS, AND ONE ROW CANNOT SAY SO.**
  `~/.config/repo-cos/env` is sourced by NOTHING, so deleting it breaks nothing *loudly*
  — that is exactly what made the row this arc corrected so dangerous.
  `~/.local/share/opencode/auth.json` is read by the tool that owns it, so losing it
  fails at the next dispatch with a provider-auth error: loud, immediate, recoverable
  with `opencode auth login`. Their consumers are **disjoint** (opencode +
  `scripts/browser-bridge/browser-agent` vs `scripts/mail-actions/` + the `civitai/cli`
  dogfood runner, which read the ENV VAR). A single row would have had to assert both
  failure modes at once.
- 🔴 **DO NOT PUT A KEY FINGERPRINT IN A PUBLIC REPO JUST BECAUSE IT IS NOT THE KEY.**
  The natural way to write "these are two different keys" is to paste both sha256
  prefixes. `SECRETS.md`'s own header forbids real secret values; a truncated digest is
  not a value, but it is a confirmation oracle for a guessed key. The row states the
  distinction and tells the reader to re-derive the digests instead. Same decision applies
  to this doc.
- 🔴 **THE CLOSING-CONDITION ENUMERATION HAS A THIRD HIT, AND IT IS A FALSE POSITIVE —
  RULE IT OUT BY LENGTH, NOT BY LOOKS.**
  `~/.claude/projects/-home-zach-workspace-civit-datapacket-talos/memory/support_a2_status_surfacing_2026_06_01.md`
  matches `sk-or-v1-`, is mode 0644, and is NOT a transcript, so it reads as a third
  undocumented credential. It holds a **17-character truncated prefix** (`sk-or-v1-` +
  8 chars, followed by `…`) against **73** for a real key. It needs no row. The
  discriminator is the token LENGTH; measure it rather than eyeballing the match.
- 🔴 **THE RANKED LIST'S LOCK ONLY SEES WHAT SOMEBODY CLAIMED — THE PR SWEEP IS WHAT
  CAUGHT THE DUPLICATE.** Rank 3 was claimed 19 minutes before this session looked, under
  a slug (`opencode-version-pin-1-18-30`) that is NOT derived from this doc, so
  `claim-work --slug-for <doc> 3` would never have collided with it. `claim-work --list`
  plus `gh pr list --state open` found it; either alone would not have. **Run both, and
  read the list for subject text, not just for your own slug.**
- ⚠ **`gh pr list --repo ZacxDev/devrc` fails — this repo is `innovation-upstream/devrc`.**
  The error is `Could not resolve to a Repository`, which reads as a permissions or
  network problem rather than a wrong owner. `git -C "$DEVRC" remote get-url origin`
  settles it in one command.
- ⚠ **`handoff_doc.py` has NO mechanism to retire a superseded heading in an APPEND
  section.** `Open investigations` appends by design, and there is no flag that edits an
  existing block. The reference (`~/.claude/skills/handoff/reference/supersede.md`) asks
  for the old heading to be edited in the same delta, and the tool cannot do it. The
  workaround used here is an explicit retirement paragraph at the top of the superseding
  block naming the old heading verbatim so a grep joins them — weaker than an edit,
  because a reader going top-to-bottom still meets the old heading first.

## How to verify

**The closing condition — enumerate credentials, then check each has a row:**

```bash
# 1. every file under the usual config roots holding an OpenRouter-shaped secret
find ~/.config ~/.claude ~/.local/share -maxdepth 4 -type f -print0 2>/dev/null \
  | xargs -0 grep -lI 'sk-or-v1-' 2>/dev/null
# 2. for each hit that is NOT a transcript, confirm SECRETS.md has a row for it
grep -n 'repo-cos/env\|opencode/auth.json' "$DEVRC/SECRETS.md"
```
Measured 2026-09-20 the enumeration returns **7** hits: 4 transcripts
(`~/.claude/history.jsonl` + 3 `projects/*.jsonl`), the 2 real credential files, and
**one false positive** — a `memory/*.md` file holding a 17-char TRUNCATED prefix, ruled
out by length (see Gotchas). Condition is met when every non-transcript hit that holds a
FULL key has a row. 🔴 Use `find | xargs grep`, NOT `grep -r` — `grep` here is a ugrep
function that honours `.gitignore`, and this clone's 104 agent worktrees make a
recursive search actively misleading.
🔴 **After `#1805` merges, step 2 returns rows for BOTH files and rank 1 is closed. The
condition is still NOT met until rank 2 (the SMTP pair) has a row.**

**Both keys are live — re-derive rather than trusting the figures above.** Ask the
issuer's own free endpoint with each key in an `Authorization: Bearer` header:
`https://openrouter.ai/api/v1/key` returns `label`, `limit`, `usage`, `is_free_tier`.
Compare the two `usage` values; do not print or record the keys or their digests.

**The corrected `repo-cos/env` row's own claims, re-derived (each must hold):**

```bash
stat -c '%a %s' ~/.config/repo-cos/env                     # 600, 215
grep -c '^[A-Z_]*=' ~/.config/repo-cos/env                 # 1  (the ONLY assignment)
git -C "$DEVRC" show bac41175 -- SECRETS.md | grep -c 'NO CREDENTIAL FILE ANY MORE'   # 1
git -C "$DEVRC" grep -n 'OPENROUTER_API_KEY' -- scripts/mail-actions   # the in-repo consumer
grep -cE '^[0-9]+\. \*\*' "$DEVRC/SECRETS.md"              # 9 before #1805, 10 after
```

**The new `auth.json` row's own claims:**

```bash
stat -c '%a %s' ~/.local/share/opencode/auth.json          # 600, 131
python3 -c 'import json;d=json.load(open("'"$HOME"'/.local/share/opencode/auth.json"));print(sorted(d))'   # ['openrouter']
grep -c 'sk-or-v1-' "$DEVRC/SECRETS.md"                    # 0 — no key material in the doc
```

**Every test that reads this file (expect 897 passed / 3 failed, the 3 base-red):**

```bash
python3 -m pytest $(git -C "$DEVRC" grep -ln 'SECRETS\.md' -- scripts/tests \
  | sed "s|^|$DEVRC/|" | tr '\n' ' ') -q
```
🔴 In zsh that `$(...)` does NOT word-split — the whole list arrives as ONE argument and
pytest collects nothing while exiting 0. Use `${=FILES}` or a real array, and **read the
file count** (it must be **8**) before believing the result.
🔴 Before attributing ANY failure here to a change, re-run the same selection in a clean
worktree at `origin/main`. Four pre-existing failures live in this tier.
