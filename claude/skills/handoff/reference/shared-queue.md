# The ranked next-steps list is a work queue with no lock

Measurements behind the one-line rule in `SKILL.md` → `## Next steps (ranked)`,
and the consuming half in `/resume` step 6.

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

That is why the rule has two halves, and why the second one matters most:

- **Check** `gh pr list --state open` before starting **and again immediately
  before `gh pr create`** — two moments, because the second is where the sunk
  cost is highest. This protects you from being the *later* session.
- **Push the branch the moment you create it, before doing the work** (an empty
  commit is enough). That is the claim. It costs seconds, collapses the invisible
  window from ~20 minutes to ~0, and is the **only** half that protects you when
  you are the *first* session.

## Producing side

When writing an item into `## Next steps (ranked)`, make "is this already taken?"
cheap to answer: name the repo and the files it will touch, and mark anything
already in progress as `IN FLIGHT: <repo>#<pr>` or `BRANCH: <name>`.
