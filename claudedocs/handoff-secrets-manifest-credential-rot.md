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
  and absent at `main~1`.
- **What the row said before:** 🔴 *"NO CREDENTIAL FILE ANY MORE … nothing reads that
  file … Safe to `rm ~/.config/repo-cos/env`"*. Measured 2026-09-19 against the key's own
  free `/api/v1/key` endpoint: the file exists (3 lines, mode 600, ONE assignment) and the
  key is valid and paid. The instruction would have destroyed a working credential whose
  only other copy is the OpenRouter dashboard.
- **Why it rotted:** `bac41175` retired the weekly `repo-cos` timer — the credential's
  *consumer* — and recorded the *credential* as dead. It also deleted the bootstrap step
  that creates the file, and renumbered.
- 🔴 **MERGED THROUGH A RED GATE, deliberately and on the record.**
  `tekton/devrc-pytests` was FAILING at `dc7bbfdb`; the other three statuses passed. The
  failure is `test_opencode_engine.py::test_engine_is_the_version_every_measurement_is_keyed_to`
  and it reproduces identically in a clean worktree at `origin/main` with the change
  nowhere in the tree. The override and its control are posted on the PR, before the merge.
- **Deploy/verify status:** nothing deployed; this is documentation. Verified by content
  on `origin/main`, not merely by the merge reporting success.
- ⚠ **No `clawgate-task:` field**: `clawgate_handoff.sh resolve` exited **5**. An unknown
  session id answers 200 with an empty array, so that zero cannot distinguish "touched no
  task" from "wrong id". Not a clean bill of health.
- ⚠ **The primary clone carries two untracked `claudedocs/scope-chief-*.md` files** from
  another session. Not mine; left alone.

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

## Next steps (ranked)

1. **`~/.local/share/opencode/auth.json` holds a live OpenRouter key and has NO row in
   `SECRETS.md`.** Measured 2026-09-19: mode 600, `openrouter.key`, 73 chars,
   sha256[:12] `5574a4ff` — a DIFFERENT value from `~/.config/repo-cos/env`
   (`d575b6fe`). Until it has a row, "rotate the OpenRouter key" is ambiguous and a
   rotation covers a fraction of the blast radius. This is half of the closing condition.
   forcing: security — an undocumented live credential on the host
2. **The drafter's Gmail/SMTP credential is documented NOWHERE in `SECRETS.md`.**
   `REPO_COS_SMTP_USER` / `REPO_COS_SMTP_PASSWORD` (`scripts/task-spec-drafter/email_send.py:131-132`)
   over SOPS `mailbox-gmail-imap` key `IMAP_APP_PASSWORD` (`:16`, `:46`). `grep -in
   'gmail\|IMAP\|SMTP' SECRETS.md` returns **0**. Hole predates this arc (`bac41175`);
   `SECRETS.md:33` already has the pattern for a credential with no local file.
   forcing: security — an undocumented credential, same class as rank 1
3. **Re-key or roll back the opencode measurement pins** — see the investigation block.
   `main`'s pytests gate is RED until this is done, and this arc already merged through
   it once. 🔴 Run the discriminating check first; the two causes need opposite fixes.
   forcing: gate — `tekton/devrc-pytests` is failing on `main`
4. **Three `test_analyze_service_index_escrow_verify.py` tests are base-red**
   (`test_the_preflight_PASSES_when_every_module_resolves`, `…missing_bw…`,
   `…missing_identity…`). Confirmed at `origin/main` by control worktree; unrelated to
   this arc.
   forcing: gate — a red the pytests tier carries
5. **`nix/agent-handles.nix:27` exports `CIVITAI_CLI` at a clone stuck at 2026-06-18**
   with no `scripts/dogfood/`, so a cross-repo reference written against that handle does
   not resolve. The live checkout is `~/workspace/civit/cli`.
   forcing: none
6. **104 stale `.claude/worktrees/agent-*` checkouts in this clone.** Each is a full
   working tree; together they poison every recursive grep here — measured, a
   `grep -rn 'repo-cos/env'` returned **208 hits, all of them** in those worktrees, and
   the tracked tree has none. That produced a wrong conclusion in this session until an
   audit run against a clean checkout contradicted it.
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

## How to verify

**The closing condition — enumerate credentials, then check each has a row:**

```bash
# 1. every file under the usual config roots holding an OpenRouter-shaped secret
find ~/.config ~/.claude ~/.local/share -maxdepth 4 -type f -print0 2>/dev/null \
  | xargs -0 grep -lI 'sk-or-v1-' 2>/dev/null
# 2. for each hit that is NOT a transcript, confirm SECRETS.md has a row for it
grep -n 'repo-cos/env\|opencode/auth.json' "$DEVRC/SECRETS.md"
```
Condition is met when every non-transcript hit has a row. 🔴 Use `find | xargs grep`, NOT
`grep -r` — `grep` here is a ugrep function that honours `.gitignore`, and this clone's
104 agent worktrees make a recursive search actively misleading.

**The corrected row's own claims, re-derived (each must hold):**

```bash
stat -c '%a %s' ~/.config/repo-cos/env                     # 600, 215
grep -c '^[A-Z_]*=' ~/.config/repo-cos/env                 # 1  (the ONLY assignment)
git -C "$DEVRC" show bac41175 -- SECRETS.md | grep -c 'NO CREDENTIAL FILE ANY MORE'   # 1
git -C "$DEVRC" grep -n 'OPENROUTER_API_KEY' -- scripts/mail-actions   # the in-repo consumer
grep -cE '^[0-9]+\. \*\*' "$DEVRC/SECRETS.md"              # bootstrap list is 1..9, no gaps
```

**The merge, by CONTENT with a negative control — never by ancestry (a squash merge never
makes the branch head an ancestor):**

```bash
git -C "$DEVRC" show origin/main:SECRETS.md   | grep -c 'An unreferenced credential file'  # 1
git -C "$DEVRC" show origin/main~1:SECRETS.md | grep -c 'An unreferenced credential file'  # 0
```

**Every test that reads this file (expect 897 passed / 3 failed, the 3 base-red):**

```bash
python3 -m pytest $(git -C "$DEVRC" grep -ln 'SECRETS\.md' -- scripts/tests \
  | sed "s|^|$DEVRC/|" | tr '\n' ' ') -q
```
🔴 Before attributing ANY failure here to a change, re-run the same selection in a clean
worktree at `origin/main`. Four pre-existing failures live in this tier.
