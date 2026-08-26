# Claim-by-push: a lock for the ranked next-step queue

**Status:** shipped. `scripts/claim-work.sh`, deployed on PATH as `claim-work`.
**Gate:** `scripts/tests/test_claim_work.py`.
**Rule:** `claude/RULES.md` → Git Workflow, "A ranked next-step list is a SHARED QUEUE WITH NO LOCK".
**Evidence:** `claude/RULES-ARCHIVE.md` → `shared-queue-lock`, and
`claude/skills/handoff/reference/shared-queue.md`.

---

## The problem, measured

A handoff doc's `## Next steps (ranked)` is a work queue with no lock. Every
`/resume` session draws from it and nothing marks an item taken. Measured
2026-08-24: three sessions collided across four items in one day, and one
collision cost an entire PR — homelab-infra **#388**, closed after two
adversarial audit rounds of work had already gone into it. Next-step 1 became
`#386`; next-step 4 became **both `#388` and `#389`**.

🔴 **A better ranked list makes this worse, not better.** Making the next action
obvious, specific and actionable is exactly what makes two sessions pick the same
one. The list is not the defect; the absence of a claim step is.

Three facts constrain any fix, all measured, all easy to get wrong:

1. **A pre-flight check alone cannot work.** Whoever moves FIRST cannot see the
   second session — it does not exist yet at branch-creation time. In the
   `#388`/`#389` pair no check on the first mover's side could have helped.
2. **Worktree isolation is refuted.** Every colliding session was already in its
   own worktree and no file was ever clobbered. This is a task-ALLOCATION
   collision, not a filesystem one, and isolation is what *hides* it: a shared
   tree would have shown the other branch.
3. **The colliding party was an `opencode` run, not a Claude session.** A fix
   reachable only from a Claude skill does not cover the collision that happened.

---

## The chosen approach: claim by push

**Publishing an ORPHAN commit to `refs/heads/claim/<slug>` on `origin` IS the
claim.** `claim-work <slug>` does exactly that:

```bash
claim-work --list                                            # every live claim, with age + subject
SLUG=$(claim-work --slug-for <handoff-doc> <rank>)            # canonical id — both sessions derive the SAME one
claim-work "$SLUG" --subject "<the item, in your own words>"  # 0 = yours · 10 = taken · 11 = taken but stale
claim-work --check "$SLUG"                                    # read-only: is it taken?
claim-work --release "$SLUG"                                  # when you finish or abandon it
claim-work --steal "$SLUG"                                    # take over a stale claim, deliberately
```

Run it as a **bare command from wherever you are** — the namespace is global and
the cwd is not consulted (see below). `--repo` changes the namespace and is for
tests, not routine use.

Exit codes (documented in the script's own header, and pinned by the test suite):
`0` won / free / degraded · `2` usage · `10` taken · `11` taken but stale ·
`20` degraded under `--strict`.

### 🔴 The claim namespace is GLOBAL — and it was not, for two days

The queue this locks is global: handoff docs live in `devrc/claudedocs/` while the
work they rank happens in homelab-infra, datapacket-talos, civitai and elsewhere.
The measured incident is literally that shape — the handoff doc was a **devrc**
artifact (`claudedocs/handoff-devrc-ci-gate.md`) while the colliding PRs were
**homelab-infra** `#386`/`#388`/`#389`.

🔴 **The first shipped version resolved the remote from `$PWD`, so the namespace
was PER-ORIGIN and the whole mechanism was inert cross-repo.** Measured
2026-08-26: the same canonical slug claimed from `~/workspace/devrc` and from
`~/workspace/homelab-talos` **both returned rc 0 CLAIMED**, one ref on each
origin, no warning — and the two claims carried different git identities, so even
the refusal text could not have disambiguated them. Natural usage produces exactly
that: derive the slug from the devrc handoff doc, then run `claim-work` in the
repo you are actually working in.

The fix resolves ONE canonical remote **from the location of the script itself**:
`readlink -f` the file (the deployed `claim-work` is a symlink onto
`devrc/scripts/claim-work.sh`), walk to that repo's root, read *its* `origin`.
That works from any cwd, in any repo, with no configuration. Precedence, in full:

| # | source | note |
|---|---|---|
| 1 | `--remote <url>` | explicit, wins |
| 2 | `--repo <path>`'s `origin` | explicit, and it **changes the namespace** — for tests and one-off cross-remote work, never routine use |
| 3 | `DEVRC_CLAIM_REMOTE` | explicit, environment |
| 4 | the script's own repo's `origin` | **the default** — global, cwd-independent |
| 5 | *degrade* | |

🔴 **There is deliberately NO cwd fallback.** A fallback is not a graceful
degradation here: it reinstates the per-origin namespace *and* reports CLAIMED
while doing it, which is strictly worse than saying "I could not find out". So an
unresolvable canonical remote degrades (exit 0, loud stderr, rc 20 under
`--strict`) and claims nothing. Pinned by
`test_the_claim_namespace_is_global_not_per_origin` (two repos, two origins, one
canonical remote ⇒ rc 0 then rc 10, and no stray ref on either cwd origin),
`test_the_canonical_remote_survives_the_symlink_the_command_is_deployed_as`, and
`test_an_unresolvable_canonical_remote_degrades_rather_than_using_the_cwd`.

### 🔴 What a claim publishes, and this repo is PUBLIC

A claim commit goes to the canonical origin, where anyone with read access sees
it. It carries the claimant's git name and email, the **hostname**, an
`owner-id`, a nonce, and the `--subject` text **verbatim**.

- The `owner-id` is a **hash**, not the absolute path it replaced: an absolute
  cwd leaks a client repo's directory name into a public remote, and a hash is
  all ownership needs (it only has to distinguish two sessions).
- ⚠ **"Opaque" was the wrong word and this doc used it.** Its predecessor
  `cwd-id` was `git hash-object` over a short, guessable absolute path — a
  target's token was recoverable in ONE command
  (`printf %s <path> | git hash-object --stdin | cut -c1-12`), which is what made
  the subject-injection forgery below *targeted* rather than theoretical. The
  current token mixes in `/etc/machine-id`, which is not readable from off-host,
  so it is no longer trivially recomputable. It is still a **discriminator, not a
  secret**, and it is **not an authorisation boundary**: `--force` bypasses the
  whole gate on purpose and anyone who can run the command can pass it. The gate
  stops an ACCIDENT, not an attacker.
- **The subject is public.** A claim commit is pushed to the canonical origin and
  this repo is PUBLIC: keep the subject generic — no client names, real hostnames,
  paths or captured text.
  `CLAUDE.md` → "This repo is PUBLIC" forbids real media paths, client directory
  names, third-party hostnames and captured text; a claim subject is a publish
  path like any other. This is stated in the script header, `usage()`, `/resume`
  step 6 and here, because the person typing the subject is the only control on
  it — pinned as a normalised whole SENTENCE by
  `test_every_surface_warns_that_a_claim_subject_is_PUBLIC`, since the earlier
  `"PUBLIC" in text` version was satisfied by paragraphs about something else.
- 🔴 **And the subject is VALIDATED, because it used to be a forgery vector.**
  A newline or any control character is rc 2. `--subject` text is interpolated
  ABOVE the trailer block, and `claim_field` used to take the first `^key:` line
  anywhere in the message, so
  `--subject $'legit work\nhost: attacker-host\ncwd-id: deadbeefcafe'` produced a
  claim reporting `where: host attacker-host, cwd-id deadbeefcafe` — and **the
  real holder was then refused `--release` on their own live claim at rc 10**
  (measured 2026-08-26). `claude/RULES.md`: *a guard can be SPELLED rather than
  STRUCTURAL.* Both halves are fixed: the subject is rejected, **and** the
  trailers are now read structurally with `git interpret-trailers --parse`, which
  only ever sees the message's last paragraph. Either alone closes the measured
  attack; both are kept because they fail differently — the validation covers
  refs this tool writes, the structural read covers refs it merely reads.
- ⚠ **Not covered:** the four repo content gates read `git ls-files` and are
  structurally blind to a ref-only commit that never enters a tree. Nothing
  mechanical checks a claim subject. If that becomes a real risk, the gate would
  have to read `refs/heads/claim/*` on the remote, which no existing test does.

### 🔴 Ownership, and why it is not the author

`--release` deletes and `--steal` overwrites a ref, and until 2026-08-26 neither
had any gate: **measured, session B released session A's zero-second-old live
claim (rc 0, silently) and stole it (rc 0)** — while rc 10 prints "DO NOT start
this item" with `--steal` one flag away, and `usage()` called `--steal` the verb
for a "stale/abandoned" claim. Nothing enforced either.

The git identity cannot decide this: both hosts run as the same author, and two
agents in two worktrees on one host are the same person to git. So ownership keys
off an **`owner-id`** recorded in the claim commit. Allowed without
`--force`: a claim that is yours, a claim past the TTL (that is what the stale
escape hatch is for), or a slug nobody holds. Refused (rc 10): a live claim
belonging to another session, **and one whose owner could not be read** — "could
not find out" must not authorise a destructive write on somebody else's lock.

#### 🔴 The token was `(uname -n, hash($PWD))` and it was wrong in BOTH directions

Measured on this fleet 2026-08-26, one round after the gate landed:

- **Too loose — it failed the case it exists for.** `uname -n` is `nixos` on
  **both** the workbench and the laptop, and `/home/zach/workspace/devrc` exists
  on both, so the two hosts computed the identical token
  `host nixos, cwd-id bff868bde328`. A laptop session was told
  `— THIS SESSION (you already hold it)` about a **workbench** claim and released
  it at rc 0 with no `--force`. The gate's own header said host + cwd is what
  tells the two hosts apart; it could not.
- **Too strict — it created the stuck lock it exists to prevent.** The cwd half
  hashed the literal `$PWD` string, so claiming from `~/workspace/devrc` and then
  `--release`ing from `~/workspace/devrc/scripts` was **rc 10, "NOT yours"**. Same
  across a worktree — this repo's *mandated* default for agent work — while
  `/resume` step 6 tells agents to run the bare command "from wherever you are".
  The legitimate owner was locked out of their own claim for the full TTL.
- 🔴 **The suite was structurally blind to both, which is the finding under the
  finding.** `_run()` always passed `--repo <path>`, so every ownership test
  derived the token from a repo path — cwd-invariant by construction. Production
  never passes `--repo` and derived it from `$PWD`. **The fixture pinned a token
  production did not use**, so fixing the code without fixing the fixture would
  have left both directions invisible. The round-3 tests run the bare command with
  an explicit `cwd`, and `_cwd_env` exists to make that hard to undo.

### 🔴 …and round 3's fix was too LOOSE again, in the worst direction (round 4)

Round 3 keyed the second half off `git rev-parse --git-common-dir` and wrote down,
as a deliberate decision, that two linked worktrees of one clone are the SAME
owner. **Every linked worktree of a clone reports the same `--git-common-dir`** —
measured on this clone, `devrc`, `devrc-claim-r2` and `devrc-integ2` all report
`/home/zach/workspace/devrc/.git` — so the token could not tell any of them apart.
And because `claim_is_mine` also decides `report_existing`'s exit code, an
UNRELATED sibling agent claiming a slug a peer already held was handed

```
rc 12  "✅ THIS IS YOURS — carry on with it. Nothing to do."
```

One clone + five linked worktrees, concurrent claim of one slug — **round 2:
1 CLAIMED, 5 × rc 10 STOP; round 3: 1 CLAIMED, 5 × rc 12 CARRY ON.** Nothing in
the output hints at a sibling: `who:` is the shared git identity and both tokens
match. 40+ agent worktrees are registered under this clone today, `/resume` step 6
runs `claim-work "$SLUG"` directly, and rc 12 is documented as CARRY ON in
`claude/RULES.md`, `resume/SKILL.md` and the script's own stdout. **The flagship
guarantee delivered its exact opposite, in the fan-out shape this repo mandates,
in the direction that costs a PR** — and this document's own fact for the
motivating incident is *"every colliding session was already in its own worktree."*

🔴 **The premise that licensed the widening was false in the same commit, and is
RETRACTED — do not re-derive it.** Point 1 below used to read "the token gates
only the two destructive verbs; a second worktree claiming the same slug is still
refused whatever the token says." `claim_is_mine` is read by `report_existing`
too, i.e. by the CLAIM and `--check` verdicts. The push CAS does refuse the second
PUSH — and the second session is then told the existing ref is its own.

**The token now:** `hash( machine-id || realpath(git-DIR of the ident dir) )`.

- *Host half* — `/etc/machine-id`, which is genuinely per-host (measured: the two
  hosts' ids differ; their `uname -n` does not). The FILE is overridable via
  `DEVRC_CLAIM_MACHINE_ID_FILE`, so a test can simulate two hosts on one machine;
  the VALUE is not, because a value override reads as a supported forgery knob.
  Absent ⇒ fall back to `uname -n`, tagged so the two cannot collide.
- *Worktree half* — `git rev-parse --path-format=absolute --git-dir`, realpath'd.
  **Not `--git-common-dir`.** Measured on this host:

  | invoked from | `--git-common-dir` | `--git-dir` |
  |---|---|---|
  | clone root | `<clone>/.git` | `<clone>/.git` |
  | `wt1` … `wt5` | `<clone>/.git` (all equal) | `<clone>/.git/worktrees/wt<n>` |

  and — the property round 3 actually needed — `--git-dir` is identical from a
  worktree's root and from any subdirectory of it, measured at three depths. So
  this keeps round 3's real fix (release from `<root>/scripts` used to be rc 10)
  while making siblings distinct owners. A different clone has a different git dir
  too, so two clones stay two owners.

🔴 **The residual, stated rather than hidden: two sessions in the SAME directory
still compute the same token** and can release each other's claims without
`--force`. Nothing in a path or a machine-id can separate them. That is accepted,
not overlooked — `claude/RULES.md` already forbids two file-modifying agents in one
checkout, and the isolation this repo mandates for agent work is exactly the case
the token now splits. Run two agents in one directory anyway and the ownership
gate is blind to the difference; the push CAS is all that is left.

⚠ **And a second residual: a SUBMODULE working directory is a different owner from
its superproject**, because its git dir is `<super>/.git/modules/<name>`
(measured). That is correct — a submodule is a different repository — but it is
not what "any subdirectory is the same owner" would lead you to expect. devrc has
no submodules, so nothing exercises it in anger; it is pinned by
`test_a_submodule_working_dir_is_a_different_owner_than_its_superproject` as an
invariant guard, labelled as one **in the docstring as well as here** — round 5
found the doc claiming the label and the docstring never using the words.

🔴 **A third residual, found in round 5 and NOT fixed: a claim made in a worktree
that is later `git worktree remove`d can never be released by anyone without
`--force`.** The token is `<clone>/.git/worktrees/<name>`; removing the worktree
deletes that admin directory, so no live directory computes it. Measured: rc 10
from the clone root *and* from a sibling worktree, for the full TTL, with the
refusal saying the claim "is NOT yours" about the owner's own lock. **This shape
is produced routinely** — this repo mandates a worktree per file-modifying agent
and auto-removes one that ends unchanged. It is not fixed because every fix
widens the token back towards the clone, which is round 3's bug; the escape
hatches are `--force`, the TTL, or recreating a worktree at the same path with
the same admin name (measured to restore ownership — the token is the admin
dir's PATH, not its inode). `git worktree move` is safe.

⚠ **A fourth: a `cp -a` copy of a worktree is the SAME owner as the original**,
because the copy's `.git` is a file holding `gitdir: <original admin dir>`.
Correct per git's semantics, and worth stating because `claude/RULES.md`
explicitly tells agents to make such copies.

🔴 **The residual list is OPEN, not a closed set of four.** Round 4 enumerated two
and read as complete; an audit then found these two. Add to it.

`test_two_worktrees_of_one_clone_are_DIFFERENT_owners` (round 3's
`…_are_the_same_owner`, inverted — this document named it as the test to flip) and
`test_a_concurrent_fanout_of_worktrees_gets_exactly_one_winner_and_no_carry_on`
pin the call. The fan-out test exists because the pre-existing concurrency test
uses `_session()`, i.e. separate **clones** — a topology agents in this repo never
have, which is exactly why a fully green suite could not see this.

⚠ **If it ever needs reversing** the wider unit is `--git-common-dir` again, and
those two tests are the ones to invert — but read the retraction above first.

🔴 **And it reads the LEGACY fields, because refs OUTLIVE a format change** —
twice now: `cwd-id:` (written for one day) and `cwd:` (pre-2026-08-26). Both are
read, never written, and both inherit the too-loose `uname -n` host comparison,
which is unfixable for an already-published ref and is why they are transitional
rather than carried forward.
Found by verifying live rather than by the suite: at the moment the gate landed,
**every claim live on the real origin had been made by the pre-hash version**,
each carrying an absolute `cwd:` and no `cwd-id:`. (Do not write the count down —
it was three when round 4 measured it, two when round 5's brief was written and
three again ten minutes later, because other sessions claim and release. Re-derive
with `git ls-remote origin 'refs/heads/claim/*'`.) A gate reading only the new field
would have locked their own holder out of `--release` without `--force` for the
rest of the TTL — the new guard becoming the stuck-lock it exists to prevent. So
ownership falls back to comparing the legacy `cwd:` (read only; never written),
and a transitional claim's `where:` line says which format it is in rather than
"unknown". Pinned at both points by
`test_a_claim_in_the_pre_cwd_id_format_is_still_recognised_as_its_holders_own` —
the matching cwd releases without `--force`, a different cwd is still refused.
⚠ Those published claims carry `cwd: /home/zach/workspace/devrc`, a path already
all over this public repo, so nothing needed redacting.

#### 🔴 Round 5: the legacy tier's widening is scoped to the DESTRUCTIVE verbs

Because the live refs were all taken at `<clone>`, round 4 let a legacy `cwd:`
also match the **clone root** — derived from `--git-common-dir`, deliberately
wider than the token. That accept was read by **both** callers of
`claim_is_mine`, so on 100% of the refs that actually exist a sibling worktree
asking "is this taken?" was told `rc 12  ✅ THIS IS YOURS — carry on with it`.
Measured read-only from a linked worktree against the real origin, 2026-08-26, on
every live claim — round 3's flagship bug verbatim, in a clone with 61 registered
worktrees, with `/resume` step 6 running the bare command and documenting rc 12
as CARRY ON.

`claim_is_mine` now takes the legacy scope as a second argument:

| caller | question | scope | why |
|---|---|---|---|
| `report_existing` (claim / `--check`, rc 10 vs 12) | "should I start this work?" | **strict** (default) | a wrong YES means two sessions build the same thing — silently, which is the whole reason this tool exists |
| `require_ownership_or_force` (`--release` / `--steal`) | "may I delete this ref?" | **`clone`** | a wrong YES costs one visible `--force`; a wrong NO strands the live refs for the full TTL |

The default is strict so a future caller that forgets the argument gets the
conservative answer. ⚠ **The price, stated:** a genuine legacy holder standing in
a subdirectory or a worktree of their own clone now reads rc 10 STOP rather than
rc 12 CARRY ON — nothing distinguishes them from a sibling, since a legacy ref
records a bare path. rc 10 is the safe side of that coin and `--release` still
works for them without `--force`. New (`owner-id:`) claims are unaffected.
Pinned by `test_a_sibling_worktree_is_told_STOP_not_carry_on_about_a_legacy_claim`
(four points: the sibling's `--check`, the sibling's bare claim, the holder's
rc 12 as positive control, and the sibling's `--release` still succeeding) and by
the `report-existing-gets-the-wide-legacy-scope` mutant.

Pinned by `test_release_refuses_another_sessions_live_claim_unless_forced`,
`test_steal_refuses_another_sessions_live_claim_unless_forced`,
`test_a_stale_claim_can_still_be_released_by_anyone_without_force` (the same
fixture at two TTLs, so the threshold is demonstrably what opened the gate) and
`test_a_claim_whose_owner_cannot_be_read_is_not_yours_by_default`.

### Why this protects the FIRST mover (acceptance criterion 2)

The claim happens at **draw time — before any work exists**, not at PR time and
not at review time. There is nothing for a later session to have made visible,
because the first mover publishes the only thing that matters (a ref) in under a
second, before writing a line of code.

Concretely, in the `#388`/`#389` shape: session A resumes, derives the slug for
next-step 4, claims it, and *then* starts. Session B resumes twenty minutes
later, derives the same slug, and is refused with A's name, timestamp and subject
on screen. A never had to see B. A pre-flight `gh pr list` cannot produce that
outcome, because at the moment A would run it there is nothing to find.

This is verified, not argued:
`test_six_concurrent_first_movers_resolve_to_exactly_one_winner` starts six
sessions simultaneously — none of which can see any other — and asserts exactly
one `rc 0` and five `rc 10`.

### Why it is atomic, and not a check-then-act

🔴 **This section said "rejected non-fast-forward" until 2026-08-26 and that was
the wrong name, in six places across the script, the tests and three docs.** It
matters because the next reader reasons from the name. Measured with real git:

| case | who refuses, and on what | the message git actually emits |
|---|---|---|
| **two TRUE concurrent first movers** | the **SERVER**. Both clients saw no ref, so both sent the update with `old = 0000…0` — a CREATE. The ref transaction is a **compare-and-swap on that expected old value**, so exactly one create can land. | `cannot lock ref '<ref>': reference already exists` |
| **a SERIALIZED second mover** | the **CLIENT**, before anything is sent. The connection advertises the ref at a sha the fresh scratch repo does not hold, so git cannot prove a fast-forward. | `! [rejected] <sha> -> claim/<slug> (fetch first)` |

The **first** row is the atomicity, and it is the half that covers the FIRST
mover. 🔴 **The orphan-root property plays NO part in it.** Every claim commit is
an unrelated root (`git commit-tree` with no `-p`, over the empty tree), and that
earns its keep in the *second* row only: a repo that DID hold the winner's object
would print `non-fast-forward` rather than `fetch first`, because an unrelated
root can never be a descendant. Neither refusal is what the docs used to claim.

Either way the lock is git's own **compare-and-swap**, not a read we take and then
act on. There is no TOCTOU window between "is it free?" and "take it", because the
script never asks the first question on the claim path: it just pushes, and reads
the remote afterwards only to explain a failure — and it branches on what the
remote says, never on the message.

Verified empirically rather than asserted, and now as **two** tests because one
could not cover both mechanisms:
`test_a_true_concurrent_create_is_refused_by_the_ref_transaction_cas` performs the
create twice with raw `update-ref` and an explicit `old = 0000…0` (the same
transaction a create-push runs, minus the transport) and pins the
`reference already exists` refusal;
`test_a_serialized_loser_is_refused_client_side_and_cannot_fast_forward` builds
the script's real shape — a fresh bare repo holding none of origin's objects — and
pins `fetch first`, asserting the CAS message is ABSENT so it cannot silently
become a duplicate of the other.

⚠ **The old single test is why this matters.** It asserted
`"non-fast-forward" in blob or "fetch first" in blob` while calling itself a test
of the compare-and-swap. It was green because it exercised the *serialized* path;
the CAS message contains neither string, so it would have gone red in exactly the
case its name claimed to cover.

🔴 **And the assertion is proved load-bearing by a mutation**, not by its own
greenness: `test_defeating_the_lock_with_force_lets_the_second_session_win`
rewrites the one push that is the lock to carry `--force` and asserts the second
session then wins. `test_the_lock_line_is_present_exactly_once` guards that
control against silently matching nothing.

### Why it fails open

This runs at the start of every resumed session, so a bug in it is felt by every
`/resume`. No canonical remote, no network, no auth, git missing, remote hung ⇒
**loud warning on stderr, exit 0**, degrading to exactly the behaviour we had
before the script existed. Every network call is wrapped in a bounded
`timeout` so a hung remote cannot hang a resume.

Two deliberate exceptions to "exit 0", both because failing open there would
defeat the tool rather than protect it:

- **A malformed slug is `rc 2`, loud.** Failing open on a typo would claim
  nothing while the caller believes it holds the item — the exact failure this
  tool removes. Pinned by `test_a_malformed_slug_is_a_usage_error_and_claims_nothing`.
- **A degraded `--check` never prints FREE.** "Could not find out" and "nobody
  has it" are different facts, and collapsing them is how a claim tool starts
  reporting everything as available. Pinned by
  `test_a_degraded_check_does_not_report_the_slug_as_free`.

`--strict` (rc 20) exists so tests and automation can tell "I hold the claim"
apart from "I could not find out", which an exit-0-either-way contract hides.

### Why it never touches the caller's repository

It reads `user.name` / `user.email` from the caller's repo — nothing else — and
does everything in a throwaway **bare** repo under `mktemp -d`, removed on exit.
No index, no working tree, no local branch, no `FETCH_HEAD`, no stash, no objects
in the caller's object database.

🔴 **And it never runs the operator's hooks.** The scratch repo is `git init`ed,
so it inherits `core.hooksPath` from `~/.gitconfig` — measured 2026-08-26, a
global `pre-push` FIRES on a claim push, and one that blocks makes the lock
silently inert (push fails ⇒ degrade ⇒ exit 0 ⇒ "unclaimed"). Every git call now
goes through a wrapper pinning `-c core.hooksPath=/dev/null`, and every push adds
`--no-verify`. The suite was structurally blind to this because
`testlib/hermetic_git.py` pins `GIT_CONFIG_GLOBAL=/dev/null` — *the exact surface
that carries the hazard* — so
`test_a_global_pre_push_hook_cannot_make_the_lock_inert` deliberately builds its
environment without that pin, with a real `$HOME/.gitconfig` and a real hook, and
carries a negative control proving the hook fires there. A claim tool that can perturb the tree it is
claiming work in would be worse than the collision it prevents. Pinned
behaviourally by `test_the_claim_never_touches_the_callers_repository`, which
content-hashes the whole caller tree (including `.git`) before and after.

### Cross-runtime reachability (acceptance criterion 3)

**The exact file both runtimes load is `claude/RULES.md`**, and the command is
named there. Verified live: `nix/home.nix` (the `.config/opencode/AGENTS.md`
entry) concatenates `PRINCIPLES.md` + `RULES.md` + `opencode-addendum.md` into
`~/.config/opencode/AGENTS.md`, and Claude Code imports the same `RULES.md`
through `~/.claude/CLAUDE.md`. opencode does **not** receive `devrc/CLAUDE.md`
and does not expand `@`-imports, so `RULES.md` is the only shared surface.

The command itself is deployed as `~/.local/bin/claim-work` (an
`mkOutOfStoreSymlink` onto `scripts/claim-work.sh`, same pattern as `dl-route`
and `opencode-dispatch`; `~/.local/bin` is on `home.sessionPath`). A **bare
command** matters: an agent working in `homelab-infra` cannot be expected to
know a devrc-relative path, and both runtimes resolve PATH the same way.

Because `RULES.md` is paid twice — once per Claude session and again inside
opencode's `AGENTS.md` — the rule there is one bullet: the imperative, the
command, the first-mover argument, the fail-open contract. Everything else lives
in the skill, this note, and the archive, which cost nothing until read.

`test_the_cross_runtime_pointer_is_in_the_one_file_both_runtimes_load` pins all
three legs (the mention in `RULES.md`, the AGENTS.md concatenation, the PATH
deployment) so this paragraph cannot quietly become false.

### Slug determinism — the crux, and the honest limitation

The hard lock engages on an **exact ref-name match**, so two independent sessions
must derive the same slug from the same item or nothing is locked at all. That is
why the derivation is **code both runtimes call** rather than a convention in
prose:

    claim-work --slug-for claudedocs/handoff-handoff-skill-hardening.md 1
      -> handoff-skill-hardening-1

basename → lowercase → strip `.md` → strip one leading `handoff-` → non-alphanumerics
collapse to `-` → append the rank. Every spelling of the path (absolute,
relative, `./`, `../`) collapses to the same answer, and the case-fold happens
*before* the prefix strip so `Handoff-x.md` and `handoff-x.md` do not derive two
different slugs. Pinned by
`test_slug_for_is_the_same_from_every_spelling_of_the_same_doc`.

🔴 **What it does NOT do, stated plainly rather than implied.** It cannot catch:

- a semantically identical item that two sessions word differently;
- one piece of work described by two different handoff docs;
- an item whose rank moved because the list was renumbered between the two draws.

For the first two, `--list` prints every claim's human **SUBJECT** — a SOFT
signal a reader scans for a near-duplicate whose slug differs. It is not a second
lock and must not be described as one. For the third, the handoff skill now says
to keep the numbering stable and to `--release` before renumbering.

### Claim expiry

A ref nobody deletes would block an item forever, so age is part of the verdict:
past `DEVRC_CLAIM_TTL_DAYS` (default 7) a claim reports **rc 11 / STALE** rather
than rc 10, `--list` flags it, and the message names both exits (`--steal` /
`--release`). Taking a claim over is a deliberate, separate verb and never
automatic — "the holder went quiet for a week" and "the holder is on a long piece
of work" are the same observable, so a human (or an agent that says so) decides.
`test_a_stale_claim_is_reported_separately_and_can_be_stolen` measures the same
ref at two TTLs so the threshold is demonstrably what produces rc 11.

### 🔴 rc 12 — "you already hold this", which used to be rc 10

`claim_is_mine` was computed, **printed** (`— THIS SESSION (you already hold it)`)
and then not branched on: the same run said you hold it and `DO NOT start this
item. Pick another, or coordinate with the claimer.` three lines apart, and exited
**10** — which `/resume` step 6 documents as **STOP**. So a session re-claiming its
own item after a context reset, or a second `/resume` over the same handoff doc,
was told to abandon work it legitimately held. Measured 2026-08-26.
`claude/RULES.md`: *a field that exists in a DTO is not a guard — only a BRANCH on
it is*, the same shape as the `existing == sha` bug the previous round fixed.

rc 12 is its own code with its own guidance ("THIS IS YOURS — carry on"). It
**outranks rc 11**: a stale claim of your own is still yours and "carry on" is the
actionable answer either way, so the STALE advisory prints and the verdict stays
12. Pinned at four points by
`test_re_claiming_your_own_item_is_its_own_rc_and_says_carry_on` — yours,
somebody else's, yours-and-stale, theirs-and-stale — because a single rc 12 is
also what a gate returning 12 for everything would produce.

---

## Alternatives rejected

**A pre-flight `gh pr list` alone.** Rejected as the *whole* fix because it
*structurally* cannot help the first mover — the second session does not exist
when the first decides to start. It was already the standing rule and it is what
failed: `#774` was public 22 minutes before its duplicate, `#388` 18 minutes
before the other side's first commit; both were visible to `gh pr list` and
neither was checked.

🔴 **But it was NOT rejected as a step, and demoting it to "for a degraded run
only" was a regression corrected 2026-08-26.** The sweep is the only thing that
can see a duplicate that was *never claimed* — a class this very document lists
under "What is NOT covered" — so it covers something the lock cannot, and a
mechanism that covers something else is not a fallback for it. `/resume` step 6
therefore runs **both**, unconditionally: the claim, plus `gh pr list --state
open` before starting and again immediately before `gh pr create`, plus pushing
the branch the moment it is created.

**Move next-step items into clawgate.** A genuinely stronger state model —
assignees, status, history. Rejected on two counts: it adds a **workbench-cluster
dependency to every `/resume`** (the one command that must work when everything
else is broken, and which this design deliberately makes fail open), and it
inverts the task's own stated assumption that the ranked list remains the primary
hand-off surface. Revisit only if the ranked list stops being where work is
handed over.

**More prose.** Explicit non-goal. The prose version of this rule was live in
`handoff/SKILL.md` and `resume/SKILL.md` **six minutes** before the next
collision. `claude/RULES.md`: *"Prefer deterministic/structural fixes over
prompt-tuning, prose instructions, or suffix/keyword heuristics."*

**A lockfile / a claims file in the repo.** Not seriously considered once the ref
approach was on the table: a file needs a commit, a push, and a merge — i.e. it
reintroduces exactly the read-modify-write race that a ref update does not have,
and it conflicts. The ref costs one empty-tree object and cannot conflict with
anything.

**`--force-with-lease=<ref>:` as the CAS.** Equivalent in effect and rejected as
the primary spelling for legibility: a plain non-forcing push makes "this line is
the lock" obvious to a reader, and makes the mutation control (`add --force`) an
unambiguous single-expression change.

---

## One source of truth

Criterion 5 was that the rule and the tool must not be able to drift apart. Three
prose sites now point at the command, and
`test_the_prose_that_used_to_carry_the_rule_now_points_at_the_command` fails if
any of them stops naming it:

| file | role |
|---|---|
| `claude/RULES.md` | the imperative, in the one file both runtimes load |
| `claude/skills/resume/SKILL.md` step 6 | the consuming side — the exact commands to run |
| `claude/skills/handoff/SKILL.md` → `## Next steps (ranked)` | the producing side — number the items, keep the numbering stable |
| `claude/skills/handoff/reference/shared-queue.md` | the mechanism up front, then the measurements |
| `claude/RULES-ARCHIVE.md` → `shared-queue-lock` | the incident evidence and the refutations |

## Cleanup

Nothing prunes `refs/heads/claim/*` automatically, and nothing ever will
implicitly: deleting somebody's claim is a decision, not maintenance. The
supported story is manual and two commands — `claim-work --list` flags everything
past the TTL with `[STALE]`, and `claim-work --release <slug>` drops one (your
own, or a stale one; anything else needs `--force`). **Release your own claims
when the work lands**; that is what keeps the namespace small.

⚠ **A `--prune-stale` verb was considered and deliberately NOT built.** origin
already carries 262 heads, so the concern is real — but a batch delete of other
people's claims is exactly the operation the ownership gate above exists to
prevent, and it would need its own confirm/force semantics and tests before it
could be safe. If the namespace does grow unmanageably, that verb is the shape to
build, gated the same way `--release` now is. Until then this section IS the
cleanup story, and its absence was half the finding.

## What is NOT covered, and how you would notice

- **A collision on work that was never claimed at all.** Nothing forces a session
  to run the command; the rule and the two skills ask it to. `--list` and the
  `gh pr list` sweep remain the only detectors for that case — which is exactly
  why the sweep is unconditional rather than a fallback.
- **Reworded duplicates**, per the limitation above.
- **A degraded run.** It exits 0 by design. The stderr warning is the only signal,
  and an agent that ignores stderr proceeds unclaimed believing otherwise —
  which is why the warning says so in those words and `--strict` exists.
- **Whether the claim is HONOURED.** The tool reports; it cannot stop a session
  that reads `rc 10` and starts anyway.
