# The ranked next-steps list is a work queue — and `claim-work` is its lock

Measurements behind the one-line rule in `SKILL.md` → `## Next steps (ranked)`,
and the consuming half in `/resume` step 6.

## 🔴 THE MECHANISM — read this first, the rest is why

There is exactly ONE source of truth for "is this item already taken?", and it is
a command, not any of the prose below:

```bash
claim-work --list                                            # every live claim, with age + subject
SLUG=$(claim-work --slug-for <handoff-doc> <rank>)            # the canonical id both sessions must derive
claim-work "$SLUG" --subject "<the item, in generic words>"   # 0 = yours · 10 = taken · 11 = stale
claim-work --release "$SLUG"                                  # when you finish or abandon it
```

🔴 **`--subject` is a NO-OP on a claim you ALREADY HOLD — there is no in-place
edit.** Re-running `claim-work "$SLUG" --subject "<new text>"` on your own live
claim prints `✅ THIS IS YOURS — carry on with it. Nothing to do.` at **rc 0** and
**silently keeps the OLD subject**. Measured 2026-08-31: an item whose premise had
just been *refuted* went on advertising that refuted premise to every other
session for the rest of the run, because rc 0 read as "updated". The subject is
the SOFT signal other sessions scan for a near-duplicate of their item (see the
EXACT-slug limitation below), so a stale one is worse than a terse one. **To
correct it: `--release "$SLUG"` then re-claim with the new `--subject`.** That
briefly opens the slug to a racing claimant — acceptable for a correction, and
the only route there is.

🔴 **The namespace is GLOBAL — ONE canonical remote, resolved from the script's
own location, never from the cwd's `origin`.** That is not a detail: the queue is
global (handoff docs live in devrc while the work happens in other repos), and it
was `$PWD` until 2026-08-26, which made the whole mechanism **inert cross-repo** —
measured, the same slug claimed from two repos returned rc 0 CLAIMED twice, one
ref per origin, no warning. Run the bare command from wherever you are; `--repo`
changes the namespace and is for tests, not routine use. If the canonical remote
cannot be resolved it DEGRADES rather than falling back — a fallback reinstates
the bug behind a confident CLAIMED.

It publishes an ORPHAN commit to `refs/heads/claim/<slug>` on that remote. 🔴 **A
second claimant is refused, but name the mechanism correctly — there are two, and
neither is spelled "non-fast-forward".** Measured 2026-08-26:

| case | who refuses | the actual message |
|---|---|---|
| two TRUE concurrent first movers | the SERVER's ref transaction: both sent `old=0000…`, and a create is a compare-and-swap on that value | `cannot lock ref '<ref>': reference already exists` |
| a SERIALIZED second mover | the CLIENT: its fresh scratch repo does not hold the winner's object, so git cannot prove a fast-forward | `! [rejected] <sha> -> claim/<slug> (fetch first)` |

The first row is the atomicity, and it is the half that covers the FIRST mover.
🔴 **The orphan-root property plays no part in it** — it is the second line of
defence in the serialized row, where an unrelated root can never be a descendant.

It **FAILS OPEN**: no canonical remote/network/auth ⇒ a loud stderr warning and
exit 0, so it can never block a `/resume`.

⚠ **What you publish is PUBLIC.** A claim commit is pushed to the canonical
origin and this repo is PUBLIC: keep the subject generic — no client names, real
hostnames, paths or captured text. A newline or any control character in
`--subject` is rc 2: free text sits ABOVE the ownership trailers in the commit
body, and until 2026-08-26+1 a newline in the subject let the caller write
trailers of their own — a claim then reported `host attacker-host` and **the real
holder was refused `--release` on their own live claim**. Both halves are closed
now (the subject is validated, and the trailers are read via `git
interpret-trailers --parse` rather than by scanning for the first `^key:` line).

⚠ **`--release` and `--steal` are gated.** A LIVE claim that is not yours is
refused (rc 10); `--force` overrides. **rc 12 means it is YOURS — carry on**, not
a refusal. Ownership keys off an `owner-id` in the claim commit, NOT the git
author — one identity covers both hosts and every agent on them, so the author can
never tell two sessions apart.

🔴 **The owner token is per HOST and per WORKTREE, and cwd-independent**:
`/etc/machine-id` + the realpath of `git rev-parse --git-dir`. Any subdirectory of
the worktree you claimed from counts as the same owner, at any depth; a SIBLING
worktree, a different clone, and the other host do not.

⚠ **ONE EXCEPTION, on `--release`/`--steal` only: a claim whose worktree was
`git worktree remove`d.** Its token is `<clone>/.git/worktrees/<name>`, so after
the removal NOTHING recomputed it and the slug was stuck for the whole TTL — a
shape this repo produces routinely, since a worktree per agent is mandated and an
unchanged one is auto-removed. Since 2026-08-27 the claim also carries a
`clone-id:`, and the destructive verbs grant when that id is yours AND the claim's
`owner-id` matches no worktree this clone still has REGISTERED (it warns on stderr
when it does). The claim/`--check` **verdict is unchanged** — a sibling still reads
rc 10 STOP either way. Refs published before that date carry no `clone-id:` and
still need `--force`.

⚠ **That describes NEW-format refs. LEGACY `cwd:` refs (pre-2026-08-26+1) carry a
SECOND predicate that differs BY VERB**: strict per-worktree for the claim/check
verdict (so a subdirectory of the claiming clone reads **rc 10**, not rc 12, even
though you hold it), but the old clone-wide accept for `--release`/`--steal` (so a
sibling worktree can release one without `--force`). Deliberate: narrowing the
destructive side would have made already-published refs unreleasable by anyone. It
ages out as those refs are released. Do not trust a count of affected refs written
here — re-derive it with `git ls-remote --heads origin 'refs/heads/claim/*'`.

It was `uname -n` +
`hash($PWD)` until 2026-08-26+1 and was wrong in both directions: both hosts answer
`nixos` to `uname -n` (so each read the other's claims as its own, and could
release them at rc 0), and hashing the literal `$PWD` meant `cd scripts/` locked
the legitimate owner out of their own claim for the whole TTL.

🔴 **And the first fix for that used `--git-common-dir`, which EVERY linked
worktree of a clone shares** — so for one day all 40+ agent worktrees under this
clone were one owner, and an unrelated sibling claiming a peer's live slug was
told **rc 12, "carry on"** rather than rc 10, STOP. Measured: one clone + five
worktrees, concurrent, 1 CLAIMED and 5 × rc 12. **Never widen this token without
checking `report_existing`** — `claim_is_mine` decides the CLAIM verdict too, not
just `--release`/`--steal`, and a note claiming otherwise is what licensed that
widening. The residual, accepted deliberately: two sessions in the SAME directory
are still one owner, which the rules already forbid.

`owner-id` is a DISCRIMINATOR, not a secret — `--force` bypasses the gate by
design.

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
covered by construction.

🔴 **But the sweep is NOT a degraded-run fallback, and calling it one was a
regression.** The lock only ever sees work somebody CLAIMED; a duplicate that was
never claimed is invisible to it and visible to `gh pr list` — a class the design
doc itself lists under "What is NOT covered". So both run, every time, and
neither substitutes for the other:

- **`gh pr list --state open` before you start, and AGAIN immediately before
  `gh pr create`.** Two moments, because the window is ~20 minutes and the second
  is where the sunk cost is highest.
- **Push the branch the moment you create it**, before doing the work (an empty
  commit is enough) — it is visible to `git ls-remote` the instant it lands.

## 🔴 A LETTERED SUB-RANK SILENTLY LOSES ITS RANK, so the lock cannot tell `8a`…`8d` apart

**Measured 2026-08-31** against `claudedocs/handoff-tmux-webapp.md`, whose rank 8
carried sub-items `8a`–`8e`:

```
8   -> tmux-webapp-8      12  -> tmux-webapp-12     13  -> tmux-webapp-13
8a  -> tmux-webapp        8c  -> tmux-webapp
```

Every lettered rank collapses to the **bare doc slug** — which is also what a
whole-doc claim derives — so `8a`, `8b`, `8c`, `8d` and "the tmux-webapp work"
are **one lock between them**. Claiming `8c` silently takes `8d` as well, and a
session claiming the doc as a whole collides with all four.

**Mechanism**, `scripts/claim-work.sh:357`:

```sh
if [ "$#" -gt 0 ] && [[ ${1} =~ ^[0-9]+$ ]]; then SLUG_RANK="$1"; shift; fi
```

`^[0-9]+$` accepts bare digits only. `8c` fails it, `SLUG_RANK` stays empty,
`derive_slug` takes its no-rank branch, and the argument is left unshifted to be
ignored. 🔴 **It is DISCARDED, not rejected** — no warning, exit 0, and a
well-formed slug on stdout. The comment there shows the intent was to avoid
swallowing a following *flag*; a lettered rank is neither a digit string nor a
flag, and falls into the gap between those two cases.

**Why this is the worst possible place for it:** the rank is half a claim's
identity, and sub-lettering is what a doc does to its *housekeeping* tier — the
small, parallelisable items most likely to be picked up by two sessions at once.
The lock is inert exactly where contention is highest.

⚠ **Do not "fix" this by hand-writing a slug** (`claim-work tmux-webapp-8c …`).
The slug's whole job is that both sessions derive the *same* string from the same
doc and rank; a hand-typed one is a lock only against someone who typed it
identically.

### ✅ FIXED 2026-09-01 — the closing condition is met and pinned

`--slug-for <doc> 8c` now returns `<doc>-8c`, distinct from `8d` and from `8`,
and a following flag is still not swallowed. Measured before the fix on the real
handoff doc: `8c` and `8d` **both** printed the bare `tmux-webapp`.

Two things beyond the literal condition, because widening the pattern alone
would have fixed the instance and left the class:

- **An unparseable rank is now a USAGE ERROR (rc 2), not a silent drop.** The
  hazard was never the spelling `8c` — it was a rank the caller typed and the
  script ignored at exit 0. `8-c`, `8.1` and `part2` would each still have
  derived the bare doc slug under a merely-wider regex.
- **The rank is case-folded**, for the reason the base already is: `8C` and `8c`
  are one item, and `validate_slug` is lowercase-only, so an unfolded `8C` would
  not merely split the namespace — it would be rc 2 at claim time.

Pinned by `scripts/tests/test_claim_work.py`; seven of the eight cases were
watched RED on the pre-change `origin/main`, and the eighth is labelled in place
as an invariant guard rather than counted as regression coverage.

## Producing side

When writing an item into `## Next steps (ranked)`, make "is this already taken?"
cheap to answer: name the repo and the files it will touch, and mark anything
already in progress as `IN FLIGHT: <repo>#<pr>` or `BRANCH: <name>`.

🔴 **And prefer plain integers for ranks while the defect above is open** — a
sub-lettered item is unclaimable in its own right.

🔴 **NEVER write a status marker from an INFERRED human action.** A marker asserts
somebody DID something, and a queue item is the one place where being wrong about
that CANCELS the work rather than merely misleading a reader.

MEASURED 2026-08-27: an operator said *"skip the reply, i handled it"*. That was
read as "the reply was sent", and the item was rewritten as `IN FLIGHT: replied to
by the operator` plus `DO NOT RAISE A THIRD TICKET`. Nothing had been sent. The
next session would have read a live blocker as already handled and skipped it —
the failure the `IN FLIGHT` marker exists to prevent, produced by the marker
itself. It was caught only because the operator happened to re-read the kickoff
block in the same session; nothing in the toolchain would have.

The tell is that the evidence is a *human acknowledgement* rather than an
artifact. `IN FLIGHT: <repo>#<pr>` is safe because a PR number is checkable;
"handled", "done", "sorted" and "I've got it" are not, and they routinely mean
"I have decided about it", not "I have performed it". **Either cite the artifact
or ask which you were told** — and when neither is available, write what you
actually know (`NOT SENT`), never the flattering reading.

🔴 **NUMBER the items, and keep the numbering STABLE across updates.** The rank is
half of `--slug-for`'s output, so re-ranking an existing list silently re-points
every live claim: item 4's holder now holds what item 4 has *become*. Append new
items at the end rather than renumbering, and if you must renumber, `--release`
the affected claims first.

## Claim expiry

A claim ref nobody deletes would block an item forever, which is why the age is
part of the verdict: past `DEVRC_CLAIM_TTL_DAYS` (default 7) a claim reports
**rc 11 / STALE** instead of rc 10, and `--list` flags it. (A stale claim of your
OWN still reports **rc 12** — it is yours either way — with the STALE advisory
printed alongside.) Taking one over is a
deliberate, separate verb (`--steal`) — never automatic, because "the holder went
quiet for a week" and "the holder is on a long piece of work" are the same
observable. Release your own claims when the work lands.
