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

Exit codes (documented in the script's own header, and pinned by the test suite):
`0` won / free / degraded · `2` usage · `10` taken · `11` taken but stale ·
`20` degraded under `--strict`.

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

Every claim commit is an **unrelated orphan root**: `git commit-tree` with no
`-p`, over the empty tree. It can never be a descendant of whatever is already on
the ref, so a second push to an already-claimed ref is rejected
**non-fast-forward** by the receiving git — under its own ref transaction lock,
evaluated on the server at update time.

That means the lock is git's own **compare-and-swap**, not a read we take and
then act on. There is no TOCTOU window between "is it free?" and "take it",
because the script never asks the first question on the claim path: it just
pushes, and reads the remote afterwards only to explain a failure.

Verified empirically rather than asserted:
`test_the_lock_is_gits_own_ref_compare_and_swap_not_a_check_then_act` pushes two
distinct orphan commits at one ref through **raw git** (so the property belongs
to git, not to our shell), asserts the second is refused, asserts the rejection
is specifically a non-fast-forward, and asserts the loser's commit did not
overwrite the winner's.

🔴 **And the assertion is proved load-bearing by a mutation**, not by its own
greenness: `test_defeating_the_lock_with_force_lets_the_second_session_win`
rewrites the one push that is the lock to carry `--force` and asserts the second
session then wins. `test_the_lock_line_is_present_exactly_once` guards that
control against silently matching nothing.

### Why it fails open

This runs at the start of every resumed session, so a bug in it is felt by every
`/resume`. No origin, not a git repo, no network, no auth, git missing, remote
hung ⇒ **loud warning on stderr, exit 0**, degrading to exactly the behaviour we
had before the script existed. Every network call is wrapped in a bounded
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

It reads one thing from the caller's repo — `remote.origin.url` — and does
everything else in a throwaway **bare** repo under `mktemp -d`, removed on exit.
No index, no working tree, no local branch, no `FETCH_HEAD`, no stash, no objects
in the caller's object database. A claim tool that can perturb the tree it is
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

---

## Alternatives rejected

**A pre-flight `gh pr list` alone.** Rejected because it *structurally* cannot
help the first mover — the second session does not exist when the first decides
to start. It was already the standing rule and it is what failed: `#774` was
public 22 minutes before its duplicate, `#388` 18 minutes before the other side's
first commit; both were visible to `gh pr list` and neither was checked. It
survives as the documented fallback for a degraded run, and as the only thing
that can see a duplicate that was never claimed.

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

## What is NOT covered, and how you would notice

- **A collision on work that was never claimed at all.** Nothing forces a session
  to run the command; the rule and the two skills ask it to. `--list` and
  `gh pr list` remain the only detectors for that case.
- **Reworded duplicates**, per the limitation above.
- **A degraded run.** It exits 0 by design. The stderr warning is the only signal,
  and an agent that ignores stderr proceeds unclaimed believing otherwise —
  which is why the warning says so in those words and `--strict` exists.
- **Whether the claim is HONOURED.** The tool reports; it cannot stop a session
  that reads `rc 10` and starts anyway.
