# The ranked next-steps list is a work queue — and `claim-work` is its lock

Measurements behind the one-line rule in `SKILL.md` → `## Next steps (ranked)`,
and the consuming half in `/resume` step 6.

## 🔴 THE MECHANISM — read this first, the rest is why

There is exactly ONE source of truth for "is this item already taken?", and it is
a command, not any of the prose below:

```bash
claim-work --list                                            # every live claim, with age + subject
SLUG=$(claim-work --slug-for <handoff-doc> <rank>)            # the canonical id both sessions must derive
claim-work "$SLUG" --subject "<the item, in your own words>"  # 0 = yours · 10 = taken · 11 = stale
claim-work --release "$SLUG"                                  # when you finish or abandon it
```

It publishes an ORPHAN commit to `refs/heads/claim/<slug>` on origin. Because
each claim is an unrelated root, a push to an already-claimed ref is rejected
NON-FAST-FORWARD — git's own atomic ref compare-and-swap, decided on the server,
with no check-then-act window. It **FAILS OPEN**: no origin/network/auth ⇒ a
loud stderr warning and exit 0, so it can never block a `/resume`.

Source: `scripts/claim-work.sh` (deployed on PATH as `claim-work`), named in
`claude/RULES.md` so **both** runtimes get it — Claude Code imports that file and
`nix/home.nix` concatenates it into opencode's `AGENTS.md`. That matters because
the party that collided was an opencode run. Design argument, rejected
alternatives and the honest limitation: `claudedocs/design-claim-by-push.md`.
Gate: `scripts/tests/test_claim_work.py`.

⚠ **The hard lock is an EXACT slug match.** `--slug-for` is what makes two
independent sessions derive the same ref from the same item; it cannot see a
semantically identical item that two sessions word differently. `--list` prints
each claim's human SUBJECT for exactly that gap — a SOFT signal a reader scans,
never a second lock. Do not claim it catches reworded duplicates.

## What was measured — 2026-08-24

Three sessions collided on the same work in one day. One collision cost an
entire PR: **homelab-infra `#388`**, closed after two adversarial audit rounds of
work had already gone into it.

The cause is the handoff's own ranked list. Every `/resume` session draws from
it and nothing marks an item taken. It mapped onto the PR series nearly 1:1:

| handoff next-step | PR |
|---|---|
| 1. finally-on-timeout for the other pipelines | `#386` |
| 4. right-size requests — *"gitops-validate alone asks 4.65 CPU / 4.688Gi"* | **`#388` AND `#389`** |
| 5. watch for a `Preempted` event | `#389` too |

🔴 **The uncomfortable implication: a BETTER ranked list produces MORE of this,
not less.** Making the next action obvious, specific and actionable is exactly
what makes two sessions pick the same one. The list is not the problem — the
absence of a claim step is.

## 🔴 Worktree isolation is REFUTED as the explanation

This is the intuitive theory and it is wrong twice over:

1. **Every colliding session WAS using a worktree.** `hi-finally-fix` (`#385`),
   `homelab-renderdiff` (`#390`) and `tekton-devrc` were all live worktrees on
   their own branches at the time. They isolated correctly.
2. **No file was ever clobbered.** Each colliding PR was internally clean and
   independently passed its own review.

Worktrees prevent a **filesystem** collision — two agents editing one tree. This
is a **task-allocation** collision. They are orthogonal, and isolation is in fact
what *hides* the duplication: a shared tree would have shown the other branch.

## The timing, which is what makes it fixable

Each collision had a window in which the other PR was **already public**:

- `#774` was public **22 minutes** before its duplicate `#775` was opened.
- `#388` was public **18 minutes** before the other side's first commit.

Both were visible to `gh pr list`. Neither was checked. A PR becomes public
seconds after the first push, while the work itself takes ~20 minutes — so the
information exists for nearly the whole window.

## Why a pre-flight check alone is NOT sufficient

🔴 **Whoever moves FIRST cannot see the second session at all.** In the
`#388`/`#389` pair, no check on the first mover's side could have helped: at
branch-creation time the other session had not started, and at PR-creation time
it still had not. The duplication was created entirely by the later session.

**That is the whole reason `claim-work` claims at DRAW time rather than checking
at start time.** The claim exists before any work does, so the first mover is
covered by construction. A `gh pr list` sweep remains useful — it is the fallback
when the claim degrades, and it is the only thing that can see a duplicate that
was *never claimed* — but it is a second-best signal, not the lock.

Manual fallback, for a degraded run only: check `gh pr list --state open` before
starting and again immediately before `gh pr create`, and push the branch the
moment you create it (an empty commit is enough).

## Producing side

When writing an item into `## Next steps (ranked)`, make "is this already taken?"
cheap to answer: name the repo and the files it will touch, and mark anything
already in progress as `IN FLIGHT: <repo>#<pr>` or `BRANCH: <name>`.

🔴 **NUMBER the items, and keep the numbering STABLE across updates.** The rank is
half of `--slug-for`'s output, so re-ranking an existing list silently re-points
every live claim: item 4's holder now holds what item 4 has *become*. Append new
items at the end rather than renumbering, and if you must renumber, `--release`
the affected claims first.

## Claim expiry

A claim ref nobody deletes would block an item forever, which is why the age is
part of the verdict: past `DEVRC_CLAIM_TTL_DAYS` (default 7) a claim reports
**rc 11 / STALE** instead of rc 10, and `--list` flags it. Taking one over is a
deliberate, separate verb (`--steal`) — never automatic, because "the holder went
quiet for a week" and "the holder is on a long piece of work" are the same
observable. Release your own claims when the work lands.
