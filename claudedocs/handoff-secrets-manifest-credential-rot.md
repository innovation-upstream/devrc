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

- ✅ **RANK 1 IS CLOSED — `devrc#1805` MERGED** (squash `eaf4e99a`, 2026-09-20T04:51:13Z,
  branch deleted). `SECRETS.md` now carries the row for
  `~/.local/share/opencode/auth.json` at line 28, a pointer from the `repo-cos` row, and
  bootstrap step 7 (list 1..10).
  **Verified by CONTENT with a negative control, never by ancestry:** the row's unique
  string is present once at `origin/main:SECRETS.md` and **0 times** at `origin/main~1`;
  numbering re-derives to **10**; `grep -c 'sk-or-v1-'` on the merged file is **0**.
  ⚠ The obvious control here is MUDDIED and the first run of it looked wrong: grepping for
  the PATH `local/share/opencode/auth.json` gives **3 vs 1**, not 3 vs 0, because `#1797`
  had already mentioned that path once inside the `repo-cos` row's rotation-coupling
  sentence. **Grep a string unique to the NEW row**, not the path.
- ✅ **`devrc#1797` MERGED** (`12e6a10f`) — unchanged, still verified by content.
- 🔴 **THE GATE WENT RED ON THE PR AND IT WAS NOT THE KNOWN BASE-RED — the control is what
  separated them.** At head `0e74f435`, `tekton/devrc-pytests` FAILED
  (`24022 collected / 24017 passed / 1 failed`) on
  `test_engine_is_the_version_every_measurement_is_keyed_to`, while `origin/main` at
  `7ef01c05` was GREEN on the same selection (`24022 / 24018 / 0 failed`). Same collection
  count, one extra failure. The branch simply predated **`#1804`**. Merging `origin/main`
  into the branch (clean, no conflicts) produced head `24cdf34c`, all **4** statuses green,
  and that is what was merged. **This arc merged `#1797` through a red gate; `#1805` was
  NOT merged through one.**
- ⚠ **ANOTHER SESSION IS EDITING `SECRETS.md` RIGHT NOW, UNCOMMITTED, AND IT IS REAL WORK
  — DO NOT CLOBBER OR "RESCUE" IT.** The primary clone carries `M SECRETS.md` (+1 line, a
  row for `~/.config/stt/env` / `STT_API_URL` + `STT_API_TOKEN`) and `M nix/home.nix`
  (+8, a `.local/bin/stt` symlink). It differs from `origin/main` and that content appears
  nowhere upstream, so it is genuine WIP, **not** the base-clone refresh hook. No
  `claim-work` ref names it. Note what it means for this arc: a NEW credential is being
  given a manifest row as a matter of course, which is the behaviour the arc exists to
  produce.
- ⚠ **No `clawgate-task:` field — re-resolved, same answer.** `clawgate_handoff.sh resolve`
  exited **5** again with its positive control passing (the endpoint answered 2 links for a
  different session). A correct id WOULD have resolved; that is narrower than a clean bill
  of health, and no field was written.
- ⚠ **The two untracked `claudedocs/scope-chief-*.md` files** are still present, still
  another session's. Left alone.
- **Claim state:** `secrets-manifest-credential-rot-1` was RELEASED on merge. Nothing in
  this arc is claimed right now.

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

### RETRACTION — the opencode fix went the OPPOSITE way, and my own earlier block called the direction wrong
- as-of: 2026-09-20

🔴 **This RETRACTS one line of the block above it, "The opencode pin: the DISCRIMINATING
check has now been RUN, and rank 3 is held by another session" (as-of 2026-09-20).** That
block's MEASUREMENTS all stand. What is WRONG in it is its second "Ruled out" bullet —
*"Ruled out — that the pin should be left at 1.18.29 … the pin must move to 1.18.30 for
`main` to go green"* — and the "Consequence worth carrying" paragraph that follows from it.
**Do not act on either.** (`Open investigations` appends and the tool cannot edit an earlier
heading, so this paragraph is the retirement marker; grep that heading and read the two
blocks together.)

- **What actually happened:** **`#1804`** — *"fix(nix): pin opencode to 1.18.29 — 1.18.30
  cannot run a single prompt"* — merged to `main` as `7ef01c05`. It added a dedicated
  flake input `nixpkgs-opencode-1_18_29` and pinned the **BINARY** down, leaving
  `PINNED_VERSION = "1.18.29"` untouched. Verified: that constant reads `1.18.29` at
  `origin/main` and read `1.18.29` on my branch too — so the pin was never the difference.
- **Why the wrong inference was reachable from correct data:** the measurement (login shell
  1.18.29, dev shell 1.18.30, the gate runs in the dev shell) is true and reproducible, and
  it does license "the assertion and the binary disagree". It does **not** license *which
  one should move*. That question is decided by a fact no version comparison can see —
  **opencode 1.18.30 cannot run a prompt at all.** `via: change` (`#1804`, read at
  `origin/main`)
- **Ruled out — that `#1803` fixed this.** `#1803` (`fix/opencode-pin-1.18.30`) is still
  **OPEN** and proposes the direction `#1804` did not take. It is probably moot now; that
  is its author's call, not this arc's. `via: measurement` (`gh pr view 1803`)
- 🔴 **The reusable lesson, and it is the generalisable half:** a correct measurement plus a
  plausible inference is still a guess. **The discriminating fact was behavioural (does the
  new binary work?), not comparative (which version is newer?)** — and the check I ran
  could not reach it. When two artefacts disagree about a version, ask whether either one
  is BROKEN before deciding which to move.
- **Next probe:** none for this arc. Rank 3 stays another session's.

## Next steps (ranked)

1. ✅ **DONE — merged as `devrc#1805`** (squash `eaf4e99a`). The `SECRETS.md` row for
   `~/.local/share/opencode/auth.json`. Kept numbered so released claim slugs still point
   at what they were taken for.
   forcing: security — satisfied; the credential is documented
2. 🔴 **THE ONLY ITEM LEFT BEFORE THE CLOSING CONDITION — the drafter's Gmail/SMTP
   credential is documented NOWHERE in `SECRETS.md`.**
   `REPO_COS_SMTP_USER` / `REPO_COS_SMTP_PASSWORD` (`scripts/task-spec-drafter/email_send.py:131-132`)
   over SOPS `mailbox-gmail-imap` key `IMAP_APP_PASSWORD` (`:16`, `:46`).
   Re-measured 2026-09-20 against the MERGED tree: `grep -ic 'gmail\|IMAP\|SMTP'` on
   `origin/main:SECRETS.md` returns **0**. `SECRETS.md:33` already has the pattern for a
   credential with no local file. ⚠ Coordinate with the live `stt` WIP noted in State now —
   it is editing the same table.
   forcing: security — an undocumented credential, same class as rank 1
3. **The opencode pin — RESOLVED ON `main` BY ANOTHER SESSION, NOT BY THIS ARC.** `#1804`
   pinned the binary to 1.18.29 and `main`'s pytests is green. `#1803` is still OPEN
   proposing the opposite direction and is probably moot. **Read the RETRACTION block
   before touching this.** Nothing here for this arc to do.
   forcing: gate — satisfied; `main` is green
4. **Three `test_analyze_service_index_escrow_verify.py` tests are base-red**
   (`…PASSES_when_every_module_resolves`, `…missing_bw…`, `…missing_identity…`), failing
   `AssertionError: 'DECRYPT-DEPS-MISSING' == 'IDENTITY-MISSING'`. ⚠ Re-check before
   working: `main` ran **24018 passed / 0 failed** on 2026-09-20, which does NOT obviously
   square with three base-red tests in that tier — the two readings may be scoped
   differently (full suite vs the 8-file `SECRETS.md` selection). Establish which before
   filing anything.
   forcing: gate — a red the pytests tier carries
5. **`nix/agent-handles.nix:27` exports `CIVITAI_CLI` at a clone stuck at 2026-06-18**
   with no `scripts/dogfood/`, so a cross-repo reference written against that handle does
   not resolve. The live checkout is `~/workspace/civit/cli`.
   forcing: none
6. **104 stale `.claude/worktrees/agent-*` checkouts in this clone** poison every recursive
   grep here — measured, `grep -rn 'repo-cos/env'` returned **208 hits, all** inside those
   worktrees, zero in the tracked tree.
   forcing: none
7. **Decide whether any mechanical check on `SECRETS.md` claims is worth building.**
   Writing "not worth it" closes this.
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

### Added 2026-09-20 (second session) — a right measurement, a wrong inference, and a stale branch

- 🔴 **A CORRECT MEASUREMENT DOES NOT LICENSE THE INFERENCE DRAWN FROM IT, AND I SHIPPED
  THE WRONG ONE INTO A HANDOFF.** Measuring that the login shell had opencode 1.18.29 and
  the dev shell 1.18.30 is true, reproducible, and establishes only that the two disagree.
  I reported that it established **which** should move. The deciding fact — 1.18.30 cannot
  run a prompt — is BEHAVIOURAL and invisible to any version comparison, and `#1804` acted
  on it by pinning the binary DOWN. **When two artefacts disagree about a version, ask
  whether either is BROKEN before deciding which one to change.**
- 🔴 **"IT IS THE KNOWN BASE-RED" IS THE MOST EXPENSIVE ASSUMPTION AVAILABLE ON A PR WHOSE
  ARC HAS ALREADY MERGED THROUGH A RED GATE ONCE.** `#1805` went red on exactly the test
  `#1797` was waved through on, which is precisely the shape that invites a second
  override. It was NOT the same situation: `main` had gone green in between. The control
  cost two API calls — read the failing status description for its counts, read `main`'s
  for the same — and returned `24017/1` against `24018/0`, same collection count. **A
  precedent for overriding a red is not a licence; re-run the control every time.**
- 🔴 **A PR BRANCH IS NOT THE MERGED TREE, AND HERE THE GAP WAS THE ENTIRE DEFECT.** The
  branch was green-able only after taking upstream: `main` carried the fix (`#1804`) for
  the very red the branch was showing. **MERGE upstream in rather than rebasing** (a rebase
  destroys an audit ladder's range boundary), then let the gate run on the merge commit —
  the resulting head is the thing that will actually land.
- ⚠ **A path-based negative control can be muddied by an EARLIER commit in the same arc.**
  Verifying `#1805` by grepping for `local/share/opencode/auth.json` gives **3 vs 1**, not
  3 vs 0, because `#1797` already mentioned that path once in the `repo-cos` row. The first
  read of that looks like a failed control. **Pick a string unique to the new content**,
  not the identifier the arc has been discussing all along.
- ⚠ **`gh pr list --repo ZacxDev/devrc` fails with `Could not resolve to a Repository`** —
  this repo is `innovation-upstream/devrc`. The error reads like auth or network.
  `git -C "$DEVRC" remote get-url origin` settles it in one command.
- ⚠ **`handoff_doc.py` cannot retire a superseded heading in an APPEND section.**
  `Open investigations` appends by design and no flag edits an existing block, so a
  correction must open with a retirement paragraph naming the old heading verbatim. That is
  weaker than an edit: a reader going top-to-bottom still meets the stale heading first.
  This doc now contains two such markers.

## How to verify

**The closing condition — enumerate credentials, then check each has a row:**

```bash
# 1. every file under the usual config roots holding an OpenRouter-shaped secret
find ~/.config ~/.claude ~/.local/share -maxdepth 4 -type f -print0 2>/dev/null \
  | xargs -0 grep -lI 'sk-or-v1-' 2>/dev/null
# 2. for each hit that is NOT a transcript, confirm SECRETS.md has a row for it
grep -n 'repo-cos/env\|opencode/auth.json' "$DEVRC/SECRETS.md"
```
Measured 2026-09-20 **after** `#1805` merged: the enumeration returns **7** hits — 4
transcripts, the 2 real credential files (**both now have rows**, `SECRETS.md:27` and
`:28`), and **one false positive**, a `memory/*.md` file holding a 17-char TRUNCATED prefix
against 73 for a real key. Rule that one out by token LENGTH, never by looks.
🔴 Use `find | xargs grep`, NOT `grep -r` — `grep` here is a ugrep function that honours
`.gitignore`, and this clone's 104 agent worktrees make a recursive search misleading.

🔴 **The OpenRouter half of the condition is MET. The condition as frozen is NOT**, because
it names the drafter's SMTP pair too. The one command that grades what is left:

```bash
git -C "$DEVRC" show origin/main:SECRETS.md | grep -ic 'gmail\|IMAP\|SMTP'   # 0 today; non-zero closes the arc
```

**The merge of `#1805`, by CONTENT with a negative control** — never by ancestry, and NOT
by the path (see the muddied-control note in Gotchas):

```bash
git -C "$DEVRC" show origin/main:SECRETS.md   | grep -c 'A SECOND, DIFFERENT live OpenRouter key'  # 1
git -C "$DEVRC" show origin/main~1:SECRETS.md | grep -c 'A SECOND, DIFFERENT live OpenRouter key'  # 0
git -C "$DEVRC" show origin/main:SECRETS.md   | grep -cE '^[0-9]+\. \*\*'                          # 10
git -C "$DEVRC" show origin/main:SECRETS.md   | grep -c 'sk-or-v1-'                                # 0
```
⚠ `origin/main~1` is only the right control while `main` has not moved past the squash;
pin `eaf4e99a^` instead once it has.

**Both keys live — re-derive rather than trusting any figure here.** Ask the issuer's own
free endpoint with each key in an `Authorization: Bearer` header:
`https://openrouter.ai/api/v1/key` returns `label`, `limit`, `usage`, `is_free_tier`.
Compare the two `usage` values. Do not print or record the keys or their digests.

**Row claims, re-derived (each must hold):**

```bash
stat -c '%a %s' ~/.config/repo-cos/env                     # 600, 215
grep -c '^[A-Z_]*=' ~/.config/repo-cos/env                 # 1  (the ONLY assignment)
stat -c '%a %s' ~/.local/share/opencode/auth.json          # 600, 131
git -C "$DEVRC" show bac41175 -- SECRETS.md | grep -c 'NO CREDENTIAL FILE ANY MORE'   # 1
git -C "$DEVRC" grep -n 'OPENROUTER_API_KEY' origin/main -- scripts/mail-actions       # the in-repo consumers
```

**Every test that reads this file:**

```bash
python3 -m pytest $(git -C "$DEVRC" grep -ln 'SECRETS\.md' -- scripts/tests \
  | sed "s|^|$DEVRC/|" | tr '\n' ' ') -q
```
🔴 In zsh that `$(...)` does **not** word-split — the whole list arrives as ONE argument,
pytest collects nothing and exits 0. Use `${=FILES}` or a real array, and **read the file
count (it must be 8)** before believing the result. Measured 2026-09-20 in the `#1805`
worktree: **897 passed / 3 failed**, the 3 being the base-red `escrow_verify` tests.
🔴 Before attributing ANY failure here to a change, re-run the same selection in a clean
worktree at `origin/main` — and read the FULL-suite counts from the Tekton status
description too, which is what separated `#1805`'s real red from the assumed base-red.
