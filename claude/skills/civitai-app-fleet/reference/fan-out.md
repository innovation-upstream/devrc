# Rolling one change through the fleet

Written from a 2026-09-07 session that made ~30 repo-passes across the seven
app repos in an afternoon: a toolchain pass, a guard backport, a guard fix,
version bumps, and six submits. Everything below is a thing that went wrong or
nearly did.

## Before dispatching

Run `fleet.py`. Do not write repo shape into a brief from memory — the default
branch, the vitest project count and the guard's filename all differ between
repos and all moved during the session that discovered them.

Then write **one brief file** and point every agent at it, with a short
per-agent prompt carrying only what differs (repo path, base ref, known
hazards). Seven paraphrased prompts produce seven subtly different passes and
you cannot then tell a real finding from a wording difference.

## Isolation, and the trap

🔴 **Do NOT pass `isolation: "worktree"` when the target repo is not the cwd's
repo.** It creates a worktree of the CURRENT repo. Dispatching from repo A to
work on repos B–G gives every agent a worktree of A. Two failure modes, only one
loud: the agent reports a briefed file missing, or it silently works in the
wrong tree and your model of where the work happened is wrong.

Instead, have each agent do it itself:

```bash
git -C <TARGET> fetch origin
git -C <TARGET> worktree add <WT> -b <branch> origin/<default-branch>
```

🔴 **Off `origin/<default>`, never local `HEAD`.** Measured across the fleet on
one afternoon: three of seven base clones were on someone else's feature branch,
two carried uncommitted work, and one was 15 commits behind. A live session
switched a base clone's branch and committed to it **four separate times** while
the pass ran.

Related, from the same session: `sensei`'s default branch is `trunk`. A brief
that hardcoded `origin/main` was handed to seven agents; only the sensei agent
noticing saved it. Read the default branch, do not assume it.

## What the brief must demand

Not "done" — evidence, in the report:

- **before/after test counts**, measured by running, not computed. The strongest
  version deletes the new test file and re-runs, so the delta is observed rather
  than reasoned.
- **a mutation matrix** for any guard added: break each thing it claims to
  catch, one at a time, and confirm it fails with **that assertion's own error**
  while the others stay green. Restore with `cp`, and confirm byte-identity
  afterwards.
- **merge verified BY CONTENT** — `git cat-file -e origin/<base>:<path>` and
  read the values back. Never by ancestry: a squash merge never makes the branch
  head an ancestor of the base, so `merge-base --is-ancestor` is false forever
  after and reads as "not merged".
- **the base clone left byte-identical**, with its `git status --porcelain`
  quoted in the report.

Per-agent **scratch directories**. Subagents share one scratchpad path by
default, and two agents writing `mutate.sh` to the same location silently run
each other's script.

Forbid `git stash` explicitly — `refs/stash` is repo-global, so an agent
stashing in a shared clone can pop another session's work.

## Reading N reports

🔴 **When two agents disagree, that is the most valuable output of the run.**
On this session one agent argued the canonical guard the other six had adopted
could be walked around. Testing the claim instead of reconciling it by
preference found a real hole: the guard read what CI *installed* rather than
what it *ran*, and passed 5/5 against a workflow that never invoked pnpm.

Corollary: **a guard right in one repo and wrong in six is the normal case**,
because the fix propagates from whichever copy was written first. The majority
is not the evidence.

🔴 **Re-verify an agent's self-reported mutation results.** They are exactly the
claim most likely to be asserted without being run. One agent caught its own
false SURVIVED — its deletion regex had not fired, so the mutant never executed
— and said so; that is the standard to hold the others to.

## Attributing a suite failure

If the fleet's suite (or devrc's) goes red during a pass, compare failing
**files** against a pristine control run of the same suite on an untouched
checkout. Comparing test **ids** scraped from output is unreliable: on this
session that approach was picking names out of tracebacks rather than failures,
produced a different plausible set each run, and two confident wrong diagnoses
were built on it before a file-level view named the real cause in one step.

And read the runner's **counted output**, never an exit code — a trailing
command in a wrapper replaces the runner's status, which reported a red suite as
"exit code 0" twice in one session.
