# Handoff: skill-listing-budget — 2026-08-23

> 🔴 **SUPERSEDED 2026-08-24. Its central claim was FALSE and `/resume` points here — read this
> box before anything below it.** The corrected model lives in
> `claudedocs/proposal-skill-listing-tiers.md` and in the docstring of
> `scripts/tests/test_skill_descriptions.py`. Do **not** re-derive what this doc argues.
>
> - 🔴 **"No tokenizer makes it fit" / the break-even divisor argument is MOOT.** Claude Code
>   never tokenizes the listing. The budget is characters:
>   `floor(contextWindow × zx(model) × skillListingBudgetFraction)`, and **`zx` is not a
>   constant** — 4 for models up to 4.6, **3 for `claude-opus-5`+**. So 6,000 chars at 200k and
>   30,000 at 1M on the model actually in use, not the 8,680 this doc assumed.
> - 🔴 **"Descriptions are being silently DROPPED today" was FALSE for the live configuration.**
>   Measured 2026-08-24: the whole listing fits at **0.69× of budget**; nothing was being
>   truncated, and the operator is always on 1M. The urgency this doc conveys was never real.
>   The honest framing is runway (~1.9 months at current growth), not an emergency.
> - 🔴 **"Built-in skills are ADDITIONAL" is backwards.** *Bundled* skills are EXEMPT from the
>   truncation pass — they spend the budget first. (And "bundled" ≠ "builtin": `init` and
>   `security-review` are `builtin` and are *not* exempt.) Measured at 7,007 chars, not ~6,000.
> - 🔴 **"Closing the gap needs skills RETIRED or MERGED" was wrong about the mechanism.**
>   `skillOverrides: name-only` lists a skill at `name + 2` chars while it stays fully
>   invocable — verified live. Retiring the three skills this doc proposed bought 772 chars
>   ≈ **4.5 days** of growth; the problem is the growth RATE, not the total.
> - The gate's own measure undercounts the real charge by **`5n − 1`**, not by a fixed number.
>
> **Everything under "Next steps" below is DONE**: the three skills were retired (#785), the
> analysis landed (#784), and the tier mechanism shipped unadopted (#792). The `GIT_DIR`
> investigation below was NOT revisited and its status is unchanged.


> No `clawgate-task:` front matter: `clawgate_handoff.sh resolve` returned **NOTHING RESOLVED
> (0 tasks)**. An unknown session id answers 200 with an empty array, so that result cannot
> distinguish "touched no task" from "wrong id". It is not a clean bill of health, and no task
> was created to fill the gap.

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
Get devrc's **always-on skill listing** under its budget, and close the repo-corruption class
that surfaced while doing it. The listing loads every session under ~1% of the context window;
on overflow Claude Code **silently drops descriptions**, starting with the least-invoked skills
— stripping the trigger keywords that make a skill auto-fire, with no error.

## State now
- Branch: `main`, clean apart from another session's WIP (`scripts/session-analysis/initiative-scan.py`).
- **All work MERGED and DEPLOYED.** `ship.sh` converged both hosts; artifacts byte-verified
  against `origin/main` (`clawgate/SKILL.md` 15,088 · `flows/task-pickup.md` 6,224 ·
  `session-manager/SKILL.md` 16,191 — each identical to main). `drift-check` = **rc 0**.

| PR | what |
|---|---|
| #660 | clawgate task-pickup ritual → `flows/task-pickup.md`; SKILL.md 18,858 → 15,088 B |
| #662 | clickup description → routing surface (175 → 648 chars) + `test_skill_descriptions.py` |
| #663 | session-manager prune 23,233 → 16,159 B (+ audit + 10-finding fix round) |
| #674 | clawgate `task-api.md`: clickup-mirror is LIVE, not suspended (verified on the cluster) |
| #675 | clickup `awaiting` command + inbox cursor pagination |
| #720 | GUARD 9 audit follow-ups — FP rate 98.3% → 0%, protect-env bypass closed |
| #736 | clickup ↔ check-clickup-addressed disambiguation + two-way sibling ledger pin |
| #749 | listing measurement, total-size ratchet, out-of-tree ledger bug |
| #757 | six description shrinks, 1,981 → 1,547 chars |

Closed as superseded: **#678**, **#688** (both fixed concurrently by other sessions).

**Config changes outside git** (per-host `settings.json`, unmanaged by design):
- `cloudflare@cloudflare` plugin **disabled** — 5,221 chars, 26% of the listing, **0 invocations
  across 5,582 transcript files** (controls: handoff 46, clickup 36, tekton 9).
- `extraKnownMarketplaces` **removed** — it held only `cloudflare`; all three LSP plugins come
  from `claude-plugins-official`, which is not declared there. Restore JSON saved at
  `/tmp/claude-1000/.../scratchpad/cloudflare-marketplace-restore.json`; backup of the original
  settings at `scratchpad/settings.workbench.bak`.
- Both were **pre-existing host drift** — the laptop never had either. Removing them took
  `drift-check` from **rc 15 → rc 0**.

**Also landed structurally** (not mine alone, but load-bearing for anyone continuing):
`main` is branch-protected (`enforce_admins: true`, no force-push, no deletions), and
`tekton/devrc-nodetests` is now a **required** check — devrc has a real merge gate for the
first time. `CLAUDE.md`'s `<!-- merge-gate: -->` marker correctly reads `other`.

## Open investigations — live diagnosis state

### ✅ CLOSED 2026-08-24 — and the reasoning below is RETRACTED (see the box at the top)
### The skill listing cannot be brought under the 200k budget by editing devrc's skills
- **Symptom + exact repro:** listing total exceeds ~1% of a 200k context. Reproduce:
  ```bash
  cd <standalone clone> && nix develop /home/zach/workspace/devrc --command \
    python3 -m pytest scripts/tests/test_skill_descriptions.py -q -p no:cacheprovider
  grep -n '^LISTING_TOTAL_CEILING_CHARS' scripts/tests/test_skill_descriptions.py
  ```
- **Observed (with values):**
  - `LISTING_TOTAL_CEILING_CHARS = 13_741` on `origin/main`; 21 tests pass.
  - Listing after #757: **13,491 chars**. Budget at 200k ≈ **8,680 chars** ⇒ **1.56× over,
    ~4,800 chars short**. At 1M it fits comfortably.
  - Trajectory: 2.30× (start) → 2.21× (#749 trims) → **1.60×** (cloudflare plugin off) →
    **1.56×** (#757). The plugin removal alone did **4× more than 15 description rewrites**.
  - Break-even divisor (tokenizer-independent): fitting 20,013 chars in 2,000 tokens needs
    **>10.0 chars/token**; real prose runs 3.5–4.5. **No tokenizer makes it fit.**
  - Built-in skills are **not disk-readable** and are ADDITIONAL — every figure here is a floor.
- **Ruled out:**
  - *Trimming prose closes it* — three rounds moved 2.30× → 1.56×; everything left on the table
    is 772 chars against a ~4,800-char gap.
  - *`/doctor` reports listing cost* — it does not. `claude doctor` is install-health only.
    The claim in `CLAUDE.md` was wrong and was corrected in #749.
  - *`tiktoken` gives a usable number* — it is OpenAI's tokenizer, not Claude's, and is not
    importable on this host. Chars are exact; use break-even divisors instead.
- **Leading hypothesis:** the gap is structural. Closing it needs **fewer skills**, not shorter
  descriptions — or an explicit decision that 200k sessions lose routing while 1M sessions are
  unaffected.
  🔴 **RETRACTED 2026-08-24 — this is the sentence that sent the next session deleting working
  skills.** "Fewer skills" was the wrong lever: `skillOverrides: name-only` keeps a skill
  installed and invocable at `name + 2` chars, so the fix is per-skill LISTING COST, not skill
  COUNT. The second clause is right by accident — 1M sessions really are unaffected — but for
  the wrong reason, and the numbers it rests on are wrong (see the box at the top). Retiring
  three skills bought 4.5 days of growth; the growth RATE was always the problem.
- **Next probe:** the only remaining evidence-backed retirements, zero on **all three** reads,
  worth **772 chars** total:
  ```bash
  # ux-sweep (372) · gpu-operator-check (314) · session-audit (313)
  # Each holds a real capability; #757 chose to SHRINK rather than delete. Deleting them
  # reaches ~1.47x — still over. Decide the product question first.
  ```

### The test tier still mutates the repo it runs from — root cause UNIDENTIFIED
- **Symptom + exact repro:** running devrc's pytest tier writes into the git repo it runs from —
  refs, `.git/config`, `HEAD`. On 2026-08-21 it force-overwrote **`main` on the public GitHub
  remote** with ~63 fixture commits authored `T <t@example.com>`, set `core.bare=true` on the
  operator's clone, deleted `refs/heads/main`, and repointed `origin` at a `/tmp` path.
- **Observed (with values):**
  - Mechanism found (#683): **`GIT_DIR` overrides `git -C`.** Every fixture builds its
    subprocess env as `dict(os.environ)` + overrides, so ONE inherited repo-pointer variable
    retargets all fourteen fixture files at once. "Every call passes `-C`" confers no safety.
  - Reproduced: with `GIT_DIR` exported, `git -C <tmp>/work branch -D main` deletes the
    **clone's** `main`; `checkout -b topic` creates it there; `config user.name T` writes the
    clone's config.
  - Before/after on the same two test files with a poisoned `GIT_DIR`: **165 passed / 230 ERRORS**
    → **395 passed / 0 errors**, refs + config byte-identical after.
  - 🔴 **Who exported `GIT_DIR` is still unknown.** Live scan reported as a pair:
    **46 processes carry some `GIT_*`, 0 carry `GIT_DIR`**, 13 unreadable (UNMEASURED). No
    tracked file assigns one.
  - Still observed AFTER the guards: a PASS → FAIL → PASS sequence on an **identical tree** in an
    **isolated standalone clone**, the FAIL being GUARD 10 catching `scripts/tests` writing to
    that clone's `.git/config`, `0 failed` tests. Writer unattributed.
- **Ruled out:**
  - *`githooks/pre-push` hands `GIT_DIR` down* — **FALSE, and it was published as fact before
    being measured.** On git 2.55.0 `git push` exports `GIT_EXEC_PATH`, `GIT_PREFIX` and
    `GIT_EDITOR` to `pre-push` — **not `GIT_DIR`**. The rename in #683 was a route only if an
    outer caller had already exported it. Corrected in #720; do not re-derive.
  - *A worktree is isolated* — it is not. `git rev-parse --git-common-dir` inside a linked
    worktree resolves to the real `.git`, so a `git config` without `--global` writes the
    operator's config. Every session that got burned believed it was complying.
- **Leading hypothesis:** something transient exported `GIT_DIR` into the runner's environment on
  2026-08-21 and no longer does. GUARD 9 (#683/#720) and GUARD 10 (#673) now **contain** the
  class — prevention (11 vars stripped at runner top and plugin import) plus detection (a
  fingerprint of `refs/`, `HEAD`, `packed-refs`, `config` compared around every test).
- **Next probe:** the unattributed writer inside an isolated clone is the live thread:
  ```bash
  # Reproduce the PASS -> FAIL -> PASS on an identical tree, then bisect by target:
  cd <standalone clone with origin REMOVED>
  for i in 1 2 3; do nix develop . --command bash scripts/gate.sh > run$i.log 2>&1; \
    echo "run$i rc=$?"; grep -c 'DEVRC-GITENV-VIOLATION' run$i.log; done
  ```

## Next steps (ranked) — 1 and 3 are DONE; only 2 remains
1. ~~**Decide the listing question**~~ — **DONE 2026-08-24.** The three skills were retired
   (#785). It reached ~1.46× of a figure that was itself wrong; see the box at the top. The
   listing question is closed by `#792`'s tier mechanism, which is **merged but NOT adopted** —
   `skillOverrides` is absent from both hosts and `claude/skill-tiers.json` is inert until
   someone runs `scripts/sync-skill-tiers.py --apply`.
   **The standing recommendation is to leave it unadopted**: nothing truncates at 1M, and
   `LISTING_TOTAL_CEILING_CHARS` has **68 chars of headroom**, so the next skill of any size
   reds that gate loudly and forces the decision then, with the mechanism already built.
2. **Find the `GIT_DIR` exporter**, or formally close the incident as contained-but-unattributed
   and say so in `CLAUDE.md` rather than leaving it implicitly open. ← **the only item still
   open in this doc**; not revisited on 2026-08-24.
3. ~~Re-pin `LISTING_TOTAL_CEILING_CHARS`~~ — **DONE** (`13_741` → `12_929` in #785, in the same
   commit as the cut). ⚠ Do NOT re-pin it upward: headroom is now 68 against a live measure of
   12,861, and that constant's own comment forbids raising it.

## Residual risk (2026-08-24)
🔴 **The 1M context is not guaranteed.** `kT` silently returns 200,000 instead of 1e6 when 1M
credits are blocked — no error, no visible change. In that state the protected bundled skills
(~6,850) exceed the entire 6,000-char budget on their own, so **every devrc description drops at
once**. It presents only as "Claude stopped picking the right skill today." This is the one path
by which the silent-truncation failure this doc was written about can still actually occur.

## Gotchas / decisions / dead-ends
- 🔴 **NEVER run the tier from anything sharing the base clone's git common dir.** The invariant
  is not "don't run the tier" — a worktree fails the test. Use a standalone
  `git clone --no-hardlinks`, **`git remote remove origin`** before any run (a contained clone
  with a live push path is not contained — that is how the remote got wiped), and assert
  `git rev-parse --path-format=absolute --git-common-dir` lands inside your scratch dir.
- **Three of my own instrument readings were confidently wrong this session**, each looking like
  a finding: `gh pr view --json files | head -12` truncated a file list I then asserted from
  (concluded #673 didn't touch guard code — it did, +314 lines); `stat -c%s` on a **symlink**
  reported the link-target length (107 B) as the file size; and a listing count measured against
  a **stale local `main`** rather than `origin/main`. Pair every zero or anomaly with a control.
- **Zero Skill-tool invocations is necessary but NOT sufficient** for "dead". Read 3 (direct
  script/service usage) rescued two of six: `adoption-scan` is LIVE (20,494 tool-invocation
  events), `dl-router` is LIVE (`dl-router.service` active, routed a file 2026-08-20).
  `vetr-mailbox` has never sent — no send log, no credentials.
- **Prompt mentions need CLASSIFYING, not counting.** All 164 mentions across the six candidates
  were devrc's own skill-infrastructure work naming each skill as a **file** (audits, the
  commands→skills migration, path-rot gates). Genuine demand: **0 for all six**. A raw count read
  as "residual demand" is wrong in the opposite direction from a raw zero.
- **With ~20–30 sessions live, a fix taking >30 min is likely to be landed by someone else
  first.** Two PRs were closed as superseded (#678, #688) after concurrent sessions shipped the
  same fixes. `gh pr list` before dispatching is cheap insurance.
- `EXPECTED_SKIPS` in `scripts/run-tests.sh`: a conditional entry **must** live inside the array
  literal. `runner_patch.py` empties it by regex on the LITERAL, so an appended
  `EXPECTED_SKIPS+=(…)` survives into patched copies and fails 8 tests in three files that never
  name the array. `main` now has a third-field mechanism for conditional pins — use that.

## How to verify
```bash
# Listing under its ratchet, on a standalone clone of origin/main:
nix develop /home/zach/workspace/devrc --command \
  python3 -m pytest scripts/tests/test_skill_descriptions.py -q -p no:cacheprovider   # 21 passed

# Host parity + managed artifacts (READ-ONLY, never fixes):
bash ~/workspace/devrc/scripts/drift-check.sh    # expect rc 0

# Full gate — standalone clone with origin REMOVED, never a worktree:
nix develop . --command bash scripts/gate.sh     # GATE: RESULT=PASS exit=0
```
