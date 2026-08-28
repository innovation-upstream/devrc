#!/usr/bin/env python3
"""Assemble an adversarial-audit brief for a PR — the INVARIANT half as CODE.

    scripts/audit-dispatch.py <pr-number> [--round N] [--repo owner/name]
    scripts/audit-dispatch.py <pr-number> --round 3 --emit-claims

It PRINTS a brief to stdout for pasting into an `Agent` dispatch. It dispatches
nothing, merges nothing and writes nothing to the repository under audit.

WHY IT EXISTS — the measurement
-------------------------------
`claude/skills/audit-pr/SKILL.md` tells an operator to "dispatch a subagent …
against this checklist" and supplies no procedure, so the brief is reassembled
from prose every time. Measured over one session that ran 14 audit dispatches:
**60,100 characters** of hand-written brief, mean 4,292 each; **42%** mean
similarity between consecutive briefs; **zero** lines longer than 25 chars
identical across all 14.

Hard-won clauses ACCRETED rather than being present from the start, and three
were LOST after being learned:

    instruction                      present in dispatch #
                                     1  2  3  4  5  6  7  8  9 10 11 12 13 14
    do NOT git fetch            5/14 ·  ·  ·  ·  ·  ·  ·  ·  ✓  ✓  ·  ✓  ✓  ✓
    shared checkout warning     9/14 ·  ·  ·  ·  ·  ✓  ✓  ✓  ✓  ✓  ✓  ✓  ✓  ✓
    'ending it is CORRECT'      6/14 ·  ·  ·  ·  ·  ·  ·  ✓  ✓  ✓  ·  ✓  ✓  ✓
    'a nit is not a finding'    9/14 ·  ·  ·  ·  ✓  ✓  ✓  ✓  ✓  ✓  ·  ✓  ✓  ✓
    bare pytest = wrong shell  10/14 ✓  ✓  ✓  ✓  ✓  ✓  ✓  ✓  ✓  ·  ✓  ·  ·  ·
    payload/scaffolding label  10/14 ·  ·  ✓  ✓  ✓  ✓  ✓  ✓  ✓  ✓  ·  ·  ✓  ✓
    sandbox tier named         11/14 ·  ·  ✓  ✓  ✓  ✓  ✓  ✓  ✓  ·  ✓  ✓  ✓  ✓

Consequences that actually happened: the auditor at dispatch 8 ran `git fetch`
in a repo the brief called read-only (the clause first appears at 9); the first
five auditors reported the shared checkout moving as if it might be their fault;
the seven rounds before dispatch 8 ran under a stop rule that never said
stopping was the correct outcome.

So `INVARIANT_CLAUSES` below is the SECTION THAT CANNOT BE FORGOTTEN. It is one
module-level list, rendered verbatim into every brief this script emits, and
pinned two-way by `scripts/tests/test_audit_dispatch.py` against that module's
own independent ledger — so deleting a clause here goes red there, and a bullet
appearing in the rendered section with no clause behind it goes red too.

🔴 THE FRAMING CONSTRAINT — WHY PROSE IS NEVER PARSED
-----------------------------------------------------
A delta round must be framed on WHAT WAS CLAIMED FIXED, never on WHY IT IS
CORRECT. The skill records that three successive FRAMED audits confirmed a claim
purely because the prompt handed them the answer, while one BLIND audit refuted
it in a single pass.

A PR comment contains both — the claims and the reasoning that argues for them.
So this script reads ONLY the fenced block:

    ```audit-claims round=3 audited=997375ec..9f638fd4
    1. run_move collapsed WRONG-KILLER into NOT EXCLUSIVE — now two branches
    2. the "97% / most" quantifier over-stated what the measurement supports
    ```

and reproduces only its numbered lines. Everything else in the comment —
including the paragraph directly under the fence explaining why each fix is
right — is dropped on the floor. `--emit-claims` prints a correctly-formed
skeleton for the operator to paste into the round's PR comment, so the NEXT
round has something to read.

🔴 TWO REFUSALS, AND THEY ARE NOT THE SAME KIND
------------------------------------------------
1. **`--round N` for N ≥ 2 with no parseable claims block REFUSES to emit**
   (exit 2), naming what it looked for and where. An empty "what was claimed
   fixed" section silently turns a delta re-audit into a blind full audit — a
   different thing, which would then read as covered. Same shape as
   `scripts/ladder-depth-sweep.py`'s refused zero: the failure and the
   legitimate case produce the SAME observable, so neither may be reported.
2. **A brief missing an invariant clause WARNS and never blocks.** That check
   exists for a hand-edited `--out` file, and `claude/RULES.md` is explicit that
   a permanently-red gate trains everyone to click through it. Warn-only is the
   right severity for a channel whose failure is cosmetic and whose false
   positive rate is set by whatever a human typed.

🔴 WHAT THIS SCRIPT DOES NOT DO
--------------------------------
* **It does not classify payload vs scaffolding.** The skill says that is a
  human judgement, and records that a pathspec is wrong in BOTH directions on
  ordinary names (`':!*test*'` swallows `attestation/`, `latest/`,
  `inspector/`; it keeps `FooTest.java` and `login.cy.ts`). So the ledger prints
  the changed-file list and leaves X blank for a human.
* **It does not `git fetch`.** The brief it writes forbids the auditor from
  writing to the shared checkout; doing it here would be the same write. `<base>`
  is therefore only as current as the operator's last fetch, and the brief SAYS
  SO rather than quoting a number it cannot vouch for.
* **It does not dispatch, comment, push or merge.**
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections import namedtuple
from datetime import datetime, timezone
from pathlib import Path

# --------------------------------------------------------------------------- #
# THE INVARIANT CLAUSES — the whole point of the script.
# --------------------------------------------------------------------------- #
# 🔴 ONE list, rendered verbatim into every brief. Each entry is a single line
# (no embedded newlines) so the rendered section is trivially parseable as
# bullets, which is what makes the two-way pin in the test module mechanical
# rather than a prose comparison.
#
# `id` is the stable handle the test module's independent ledger names. Renaming
# an id is a deliberate act that fails that ledger; rewording `text` is not
# caught here (see the test module's "what this does NOT enforce" note).

Clause = namedtuple("Clause", "id text")

INVARIANT_CLAUSES = (
    Clause(
        "read-only",
        "**READ-ONLY — you modify nothing in the repository under audit.** If "
        "you must mutate something to test a theory, do it in a `cp -a` copy "
        "and run `rm -f <copy>/.git` FIRST: a worktree's `.git` is a FILE "
        "pointing at the real git dir, so a commit inside the copy lands on the "
        "branch you are auditing.",
    ),
    Clause(
        "no-fetch",
        "**Do NOT `git fetch`, `pull`, `checkout` or otherwise write to the "
        "shared checkout named in THE SHARED CHECKOUT section of this brief.** "
        "Other sessions are in it; a fetch there is a write with cross-session "
        "blast radius, and every ref you need is already resolved for you "
        "here.",
    ),
    Clause(
        "stop-rule",
        "**A clean round ENDS the ladder — ending it is the CORRECT outcome, "
        "not a failure.** Rounds continue only while the previous round "
        "produced a finding that needed fixing. Do not manufacture findings to "
        "justify the round, and do not run another round to confirm a clean "
        "one.",
    ),
    Clause(
        "nit-is-not-a-finding",
        "**A nit that changes nothing a reader does is NOT a finding.** If the "
        "fix would be a reword with no behavioural, decision or "
        "correctness consequence, leave it out of the findings and say so in "
        "one line under the verdict instead.",
    ),
    Clause(
        "reverify-self-reported",
        "**Re-verify the fix commit's own self-reported numbers rather than "
        "accepting them.** Counts, byte sizes, mutation-sweep results and "
        "\"watched red at <sha>\" claims in a commit message or PR body are "
        "claims to check against the tree, never evidence.",
    ),
    Clause(
        "finding-format",
        "**Report each finding with `file:line`, a concrete failure scenario "
        "(the input, the path taken, the wrong output) and a `payload` or "
        "`scaffolding` label** — payload is what the PR exists to ship; "
        "scaffolding is the tests, fixtures and notes a round wrote to guard "
        "it.",
    ),
    Clause(
        "do-not-merge",
        "**Do not merge — report only.** No pushes, no PR comments, no "
        "`gh pr merge`. Hand the findings back and let the operator act on "
        "them.",
    ),
)

INVARIANTS_HEADING = "## 🔴 NON-NEGOTIABLE — every audit, every round"

# The two mutually exclusive worktree directives. Which one a brief carries is
# decided by a FACT (the PR's repo vs the cwd's repo), never by the author's
# memory — that decision is the single highest-value generated field here.
ISOLATION_RECOMMEND = 'Dispatch with `isolation: "worktree"`'
ISOLATION_FORBID = 'Do NOT use `isolation: "worktree"`'

# --------------------------------------------------------------------------- #
# The claims block — the ONLY thing read out of a PR comment.
# --------------------------------------------------------------------------- #
# Tolerant on the header's trailing content and on the fence length, strict on
# the two fields that matter. A block whose header does not parse is reported as
# MALFORMED rather than skipped: skipping it silently would produce the same
# observable as "no block at all", and those need different fixes.
_CLAIMS_FENCE = re.compile(
    r"^(?P<fence>`{3,})audit-claims(?P<header>[^\n]*)\n"
    r"(?P<body>.*?)^(?P=fence)\s*$",
    re.M | re.S,
)
_HEADER_ROUND = re.compile(r"\bround=(\d+)\b")
_HEADER_AUDITED = re.compile(r"\baudited=(\S+)")
_CLAIM_ITEM = re.compile(r"^\s*(\d+)[.)]\s+(.+?)\s*$")

ClaimsBlock = namedtuple("ClaimsBlock", "round_no audited_from audited_to items")


def parse_claims_blocks(texts):
    """-> (blocks, malformed_reasons) over an iterable of comment bodies.

    Pure and independently testable: the refusal below is only trustworthy if
    this can be driven with no network and no PR.
    """
    blocks, malformed = [], []
    for text in texts:
        for m in _CLAIMS_FENCE.finditer(text or ""):
            header = m.group("header")
            r = _HEADER_ROUND.search(header)
            a = _HEADER_AUDITED.search(header)
            if not r or not a:
                malformed.append(
                    "an `audit-claims` fence whose header does not carry both "
                    f"`round=<n>` and `audited=<sha>..<sha>` (header was:"
                    f" `{header.strip() or '<empty>'}`)"
                )
                continue
            spec = a.group(1)
            frm, _, to = spec.partition("..")
            if not to:
                # A bare sha is accepted as the audited TIP; the round-1 anchor
                # is then simply unknown, which the ledger reports rather than
                # guesses.
                frm, to = "", frm
            items = [
                mm.group(2)
                for mm in (_CLAIM_ITEM.match(ln) for ln in m.group("body").splitlines())
                if mm
            ]
            if not items:
                malformed.append(
                    f"an `audit-claims round={r.group(1)}` block with no "
                    "numbered claim lines in its body"
                )
                continue
            blocks.append(ClaimsBlock(int(r.group(1)), frm, to, items))
    return blocks, malformed


def newest_block(blocks):
    """The highest `round=` present. Ties resolve to the last one seen."""
    best = None
    for b in blocks:
        if best is None or b.round_no >= best.round_no:
            best = b
    return best


def round_one_anchor(blocks):
    """The left side of the LOWEST-numbered block's range, or None.

    That is the only sha in the corpus that can anchor the ledger's cumulative
    "since round 1" figure. When no block carries one, the figure is reported
    NOT MEASURED — never derived from the base, which is a different quantity.
    """
    ordered = sorted((b for b in blocks if b.audited_from), key=lambda b: b.round_no)
    return ordered[0].audited_from if ordered else None


# --------------------------------------------------------------------------- #
# Process boundary — ONE runner, injected, so every test is hermetic.
# --------------------------------------------------------------------------- #

def real_runner(cmd, cwd=None):
    """-> (rc, stdout, stderr). The only place this module spawns anything."""
    p = subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True, check=False
    )
    return p.returncode, p.stdout, p.stderr


# --------------------------------------------------------------------------- #
# Facts gathered per invocation
# --------------------------------------------------------------------------- #

LedgerReport = namedtuple("LedgerReport", "files added deleted commits reason cumulative")
Facts = namedtuple(
    "Facts",
    "pr repo title base_ref url round_no cwd_repo_dir cwd_repo_slug cross_repo "
    "branch dirty prev_sha claims claims_round checklist ledger assembled_at "
    "claims_source",
)


def _slug_from_remote(url):
    """`owner/name` out of any git remote URL spelling, or None."""
    if not url:
        return None
    url = url.strip().removesuffix(".git")
    m = re.search(r"[:/]([^/:]+)/([^/]+)$", url)
    return f"{m.group(1)}/{m.group(2)}" if m else None


def gather_repo_facts(runner, cwd):
    """(repo_dir, slug) for the checkout the operator is standing in."""
    rc, out, _ = runner(["git", "-C", cwd, "rev-parse", "--show-toplevel"])
    repo_dir = out.strip() if rc == 0 and out.strip() else str(cwd)
    rc, out, _ = runner(["git", "-C", repo_dir, "remote", "get-url", "origin"])
    slug = _slug_from_remote(out) if rc == 0 else None
    return repo_dir, slug


def gather_checkout_state(runner, repo_dir):
    """(branch, dirty-path-count). Both READS; nothing here writes."""
    rc, out, _ = runner(["git", "-C", repo_dir, "rev-parse", "--abbrev-ref", "HEAD"])
    branch = out.strip() if rc == 0 else "UNKNOWN"
    rc, out, _ = runner(["git", "-C", repo_dir, "status", "--porcelain"])
    dirty = len([ln for ln in out.splitlines() if ln.strip()]) if rc == 0 else -1
    return branch, dirty


def gh_pr_facts(runner, pr, repo=None):
    """PR metadata + comment bodies, via `gh`. -> (dict, [comment bodies])."""
    cmd = ["gh", "pr", "view", str(pr), "--json",
           "title,url,baseRefName,headRepository,headRepositoryOwner,comments"]
    if repo:
        cmd += ["--repo", repo]
    rc, out, err = runner(cmd)
    if rc != 0:
        return {"_error": (err or out).strip() or f"gh exited {rc}"}, []
    try:
        data = json.loads(out)
    except ValueError as e:
        return {"_error": f"gh returned unparseable JSON: {e}"}, []
    comments = [
        c.get("body") or "" for c in (data.get("comments") or [])
        if isinstance(c, dict)
    ]
    return data, comments


def pr_slug(data, override=None):
    if override:
        return override
    owner = (data.get("headRepositoryOwner") or {}).get("login")
    name = (data.get("headRepository") or {}).get("name")
    return f"{owner}/{name}" if owner and name else None


# --------------------------------------------------------------------------- #
# The ledger — the skill's own command, with the skill's own read rules
# --------------------------------------------------------------------------- #

def measure_ledger(runner, repo_dir, prev_sha, base):
    """`git log --numstat --format= --remerge-diff <prev>..HEAD --not <base>`.

    🔴 The skill's read rules are enforced here, not assumed: **rc 0, silent
    stderr, and a non-empty range**. A missing ref or a git without
    `--remerge-diff` exits 128 with empty output; an unwritable object store
    makes `--remerge-diff` UNDER-count, exit 0 and print a plausible number,
    announcing itself only on stderr; and a range whose commits are not in this
    checkout prints nothing at all, silently, with rc 0. Each of those returns a
    `reason` and NO number — a failed command is not a zero.
    """
    def fail(reason):
        return LedgerReport(None, None, None, None, reason, None)

    rc, out, err = runner(
        ["git", "-C", repo_dir, "rev-list", "--count", f"{prev_sha}..HEAD"]
    )
    if rc != 0:
        return fail(f"`git rev-list {prev_sha}..HEAD` exited {rc}: "
                    f"{(err or out).strip() or 'no output'}")
    if err.strip():
        return fail(f"`git rev-list` wrote to stderr: {err.strip()}")
    try:
        commits = int(out.strip())
    except ValueError:
        return fail(f"`git rev-list --count` printed {out.strip()!r}, not a number")
    if commits == 0:
        return fail(
            f"the range `{prev_sha}..HEAD` is EMPTY — nothing has landed since "
            "the sha that round audited, so there is no delta to audit. Either "
            "the fixes are not committed yet, or this checkout does not have "
            "them."
        )

    rc, out, err = runner([
        "git", "-C", repo_dir, "log", "--numstat", "--format=",
        "--remerge-diff", f"{prev_sha}..HEAD", "--not", base,
    ])
    if rc != 0:
        return fail(f"the numstat command exited {rc}: "
                    f"{(err or out).strip() or 'no output'}")
    if err.strip():
        return fail(
            "the numstat command exited 0 but wrote to STDERR, so its number is "
            f"not trustworthy: {err.strip()}"
        )

    files, added, deleted = {}, 0, 0
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        a, d, path = parts[0], parts[1], parts[-1]
        na = 0 if a == "-" else int(a) if a.isdigit() else 0
        nd = 0 if d == "-" else int(d) if d.isdigit() else 0
        cur = files.get(path, (0, 0))
        files[path] = (cur[0] + na, cur[1] + nd)
        added += na
        deleted += nd
    return LedgerReport(files, added, deleted, commits, None, None)


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #

def _bar():
    return "-" * 76


def render_invariants():
    out = [INVARIANTS_HEADING, ""]
    out += [f"- {c.text}" for c in INVARIANT_CLAUSES]
    return "\n".join(out)


def render_worktree_directive(facts):
    """🔴 The generated field that closes the skill's 🔴 cross-repo hazard.

    `isolation: "worktree"` worktrees the CWD's repo. For a PR in a DIFFERENT
    repo that is the wrong tree, and the failure is quiet: the agent either
    reports a briefed file missing, or silently audits the wrong repository and
    reports findings about it.
    """
    if facts.cross_repo:
        return "\n".join([
            "## WHERE TO WORK — 🔴 CROSS-REPO",
            "",
            f"The PR lives in `{facts.repo}`. This session's checkout is "
            f"`{facts.cwd_repo_dir}`"
            + (f" (`{facts.cwd_repo_slug}`)" if facts.cwd_repo_slug else "")
            + " — a DIFFERENT repository.",
            "",
            f"🔴 {ISOLATION_FORBID} for this dispatch: that flag builds its "
            "worktree from the CWD's repo, which is the wrong one here, and it "
            "fails quietly — the agent either reports a briefed file missing or "
            "silently audits the wrong tree.",
            "",
            "Create the worktree yourself, against the PR's own clone:",
            "",
            "```",
            f"git -C <your local clone of {facts.repo}> worktree add "
            f"/tmp/audit-pr{facts.pr}-r{facts.round_no} <the PR's head branch>",
            "```",
            "",
            "That worktree is YOURS: fetching and checking out inside it is "
            "fine. The no-write rule below is about the SHARED checkout.",
        ])
    return "\n".join([
        "## WHERE TO WORK",
        "",
        f"The PR lives in `{facts.repo}`, which is this session's own repository "
        f"(`{facts.cwd_repo_dir}`).",
        "",
        f"{ISOLATION_RECOMMEND} — the flag worktrees the CWD's repo, and here "
        "that is the right one.",
    ])


def render_claims(facts):
    if facts.round_no < 2:
        return ""
    lines = [
        "## WHAT WAS CLAIMED FIXED",
        "",
        "🔴 This is WHAT WAS CLAIMED, never WHY IT IS CORRECT — nothing here is "
        "established. Three successive FRAMED audits confirmed a claim purely "
        "because the prompt handed them the answer; one BLIND audit refuted it "
        "in a single pass. Verify each item against the diff and state, per "
        "item: **actually fixed / partially / not / made worse**.",
        "",
    ]
    lines += [f"{i}. {c}" for i, c in enumerate(facts.claims, 1)]
    lines += [
        "",
        f"(Read from the `audit-claims round={facts.claims_round}` block in "
        f"{facts.claims_source}. Nothing else from those comments is reproduced "
        "here — the reasoning beside a claim is exactly what a framed audit "
        "goes on to confirm.)",
    ]
    return "\n".join(lines)


def render_range(facts):
    if facts.round_no < 2:
        return "\n".join([
            "## THE RANGE",
            "",
            f"A FIRST, FULL audit: read the whole PR diff (`gh pr diff "
            f"{facts.pr}`) and the code it touches, not just the PR "
            "description.",
            "",
            f"Base branch: `{facts.base_ref}`.",
        ])
    return "\n".join([
        "## THE RANGE",
        "",
        f"A DELTA re-audit, round {facts.round_no}. Diff **`{facts.prev_sha}"
        "..HEAD`** — the fix commits made since the tip round "
        f"{facts.claims_round} audited. Do not re-audit the whole PR.",
        "",
        f"Base branch: `{facts.base_ref}`.",
        "",
        "Also hunt for **regressions this fix round itself introduced** — the "
        "guard that is now too strict, the branch that is now unreachable, the "
        "narrowed check that now rejects a legitimate case, the rule reworded "
        "wider on one axis and narrower on another.",
    ])


def render_checkout(facts):
    dirty = (
        "could not read (`git status` failed)" if facts.dirty < 0
        else f"{facts.dirty} uncommitted path(s)"
    )
    return "\n".join([
        "## THE SHARED CHECKOUT — state at assembly time",
        "",
        f"    path   : {facts.cwd_repo_dir}",
        f"    branch : {facts.branch}",
        f"    dirty  : {dirty}",
        f"    read at: {facts.assembled_at}",
        "",
        "🔴 **This checkout is SHARED with other sessions and agents. It MOVES "
        "UNDER YOU** — the branch can change, files can appear and vanish, and "
        "commits can land mid-audit. That is expected and is NOT your fault and "
        "NOT a finding. **Report what you observed moving and carry on; do not "
        "chase it, and do not try to restore it.**",
    ])


def render_toolchain(facts):
    r = facts.cwd_repo_dir
    return "\n".join([
        "## TOOLCHAIN — the exact commands, and the two ways they lie",
        "",
        "Run a SUBSET of the suite:",
        "",
        "```",
        f"nix develop {r} -c python3 -m pytest <paths> -q -p no:cacheprovider",
        "```",
        "",
        "🔴 A bare `python3 -m pytest` failing with **`No module named "
        "pytest`** means you are in the WRONG SHELL, not that the suite is "
        "broken. This repo's `.envrc` is `use opencode`, which does not put "
        "pytest on PATH; a loaded direnv is not the dev shell. Do not report "
        "that as a finding and do not build an ad-hoc `nix-shell` around it.",
        "",
        "Run the whole dev-host gate (its EXIT STATUS is authoritative; also "
        "read each runner's own `RESULT:` line):",
        "",
        "```",
        f"nix develop {r} -c bash {r}/scripts/gate.sh --tier both",
        "```",
        "",
        "🔴 The gate above is the DEV-HOST tier. **The tier the merge actually "
        "gates on is the sandbox one**, which builds from a store copy with no "
        "`.git` and is therefore blind to different things:",
        "",
        "```",
        f"nix build {r}#checks.x86_64-linux.pytests",
        f"nix build {r}#checks.x86_64-linux.nodetests",
        "```",
        "",
        "Name the tier and the base sha in any claim you make about the gate — "
        "\"the gate passed\" is true of one run, one tier, one base, and reads "
        "as a property of the change.",
        "",
        "`git --version` before you trust a range: `--remerge-diff` needs git "
        "≥ 2.35, and a git without it exits 128 with EMPTY output, which reads "
        "exactly like a clean zero.",
    ])


def render_ledger(facts):
    lines = ["## THE LEDGER — payload attribution for this round", ""]
    led = facts.ledger
    if led is None:
        lines += [
            "Not measured: a first, full audit has no previous round to "
            "attribute against. Start the ledger at your round 2.",
        ]
        return "\n".join(lines)
    if led.reason:
        lines += [
            "🔴 **COULD NOT MEASURE** — and a failed command is NOT a zero, so "
            "no number is printed here:",
            "",
            f"    {led.reason}",
            "",
            "Re-run the command by hand, and require rc 0, silent stderr and a "
            "non-empty range before believing any figure it prints.",
        ]
        return "\n".join(lines)

    lines += [
        f"`git log --numstat --format= --remerge-diff {facts.prev_sha}..HEAD "
        f"--not {facts.base_ref}` over {led.commits} commit(s):",
        "",
        "```",
        f"{'added':>7} {'deleted':>8}  path",
    ]
    for path, (a, d) in sorted(led.files.items(), key=lambda kv: -sum(kv[1])):
        lines.append(f"{a:>7} {d:>8}  {path}")
    lines += [
        f"{led.added:>7} {led.deleted:>8}  = {len(led.files)} file(s), "
        f"{led.added + led.deleted} line(s) changed",
        "```",
        "",
    ]
    if not led.files:
        lines += [
            "🔴 Zero changed lines over a NON-EMPTY range. That is a real "
            "measurement, not a failure — but check that `--not "
            f"{facts.base_ref}` is not swallowing this round's own commits "
            "before you act on it.",
            "",
        ]
    lines += [
        "🔴 **Classify these files yourself — this script deliberately does "
        "not.** Payload is what the PR exists to ship; scaffolding is the "
        "tests, fixtures and notes a round wrote to guard it. For a code change "
        "the payload is source and a `.md` is not; **for a docs or skill PR the "
        "payload IS the `.md`**. A pathspec cannot do this — `':!*test*'` "
        "swallows `attestation/`, `latest/` and `inspector/` while keeping "
        "`FooTest.java` and `login.cy.ts`. **Ambiguous is not zero**: the gate "
        "does not fire and the ladder continues.",
        "",
        "Then carry this line in your summary, with X filled in from the "
        "classification above:",
        "",
        "```",
        f"round {facts.round_no} · payload lines changed THIS round: X "
        f"(since round 1: {led.cumulative if led.cumulative is not None else 'Y'}"
        ") · elapsed: Z",
        "```",
        "",
        "⚠ `<base>` here is `" + facts.base_ref + "` **as it stands in this "
        "checkout**. This script does not fetch (that would be a write to the "
        "shared checkout), so a stale base re-reports upstream work as this "
        "round's payload. If the number looks large, that is the first thing to "
        "check.",
    ]
    if led.cumulative is None:
        lines += [
            "",
            "⚠ The cumulative figure (Y, since round 1) is **NOT MEASURED**: no "
            "`audit-claims` block carried a round-1 anchor sha. It is not "
            "derivable from the base, which is a different quantity.",
        ]
    return "\n".join(lines)


def render_checklist(facts):
    """The checklist, READ from the skill rather than restated here.

    🔴 One rule, one place. An earlier draft paraphrased the axes inline for
    delta rounds ("risks, regressions, assumptions, …") — a second copy of a
    block that is pinned in `scripts/tests/test_audit_ladder_stop_rule.py` and
    that has already been silently shaved once, with a commit message claiming
    the opposite. The paraphrase would have drifted with nothing watching.
    """
    lines = ["## AUDIT FOR", ""]
    if facts.round_no >= 2:
        lines += [
            "First, per prior claim above: **actually fixed / partially / not "
            "/ made worse**. Then work the same axes as a full audit, scoped "
            "to the range:",
            "",
        ]
    lines.append(facts.checklist or (
        "⚠ COULD NOT INLINE the checklist — "
        "`~/.claude/skills/audit-pr/SKILL.md` was not readable from here. Read "
        "its **Audit for:** section and work through every numbered item."
    ))
    return "\n".join(lines)


def render_output_contract(facts):
    return "\n".join([
        "## OUTPUT",
        "",
        "Findings by severity (🔴 deploy-blocking / 🟡 should-fix / 🟢 nit), "
        "each in the format required above. Then the ledger line. Then a "
        "**verdict** — safe to merge / merge after fixing 🔴 / needs rework — "
        "which is advisory for the human and is **not** the ladder's stop "
        "signal: a round can return \"safe to merge\" and still report real "
        "defects. Flag anything you could not verify, and say so plainly rather "
        "than reporting it as covered.",
    ])


def render_brief(facts):
    kind = (
        "FIRST, FULL adversarial audit" if facts.round_no < 2
        else f"DELTA re-audit — ROUND {facts.round_no}"
    )
    head = "\n".join([
        f"# {kind} — PR #{facts.pr} in `{facts.repo}`",
        "",
        f"**{facts.title}**",
        f"{facts.url}",
        "",
        "Assembled by `scripts/audit-dispatch.py`. The sections below are "
        "generated from live facts; the NON-NEGOTIABLE block is verbatim and "
        "identical in every brief this script emits.",
    ])
    parts = [
        head,
        render_worktree_directive(facts),
        render_range(facts),
        render_claims(facts),
        render_checkout(facts),
        render_toolchain(facts),
        render_invariants(),
        render_checklist(facts),
        render_ledger(facts),
        render_output_contract(facts),
    ]
    return f"\n\n{_bar()}\n\n".join(p for p in parts if p) + "\n"


def emit_claims_skeleton(facts, head_sha):
    """A correctly-formed block for the operator to paste into the PR comment.

    Emitted rather than described, because the next round's assembler reads ONLY
    this shape and a hand-typed near-miss is refused.
    """
    frm = facts.prev_sha or "<the sha round 1 audited>"
    return "\n".join([
        f"```audit-claims round={facts.round_no} audited={frm}..{head_sha}",
        "1. <one line per thing this round's fixes CLAIM to have addressed — "
        "WHAT was claimed, never WHY it is correct>",
        "2. <one line, same rule>",
        "```",
    ])


# --------------------------------------------------------------------------- #
# The warn-only completeness check
# --------------------------------------------------------------------------- #

def missing_clauses(brief):
    """Clause ids whose text is not present verbatim in `brief`.

    🔴 WARN-ONLY BY DESIGN. It exists for a `--out` file a human then edits, and
    `claude/RULES.md` says a permanently-red gate trains everyone to click
    through it. Blocking here would make the tool refuse to run over an edit
    that was deliberate.
    """
    return [c.id for c in INVARIANT_CLAUSES if c.text not in brief]


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

REFUSAL_HEADER = "🔴 REFUSING TO EMIT a delta re-audit brief"


def _read_checklist(repo_dir):
    """The nine-item checklist, read from the skill rather than duplicated.

    One rule, one place: restating the checklist here would give it a second
    copy to drift from, and the block has already been silently shaved once.
    """
    for cand in (
        Path(repo_dir) / "claude" / "skills" / "audit-pr" / "SKILL.md",
        Path.home() / ".claude" / "skills" / "audit-pr" / "SKILL.md",
    ):
        try:
            body = cand.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        m = re.search(r"^\*\*Audit for:\*\*\n(.*?)(?=\n## )", body, re.M | re.S)
        if m:
            return "**Audit for:**\n" + m.group(1).rstrip()
    return None


def build_parser():
    ap = argparse.ArgumentParser(
        prog="audit-dispatch.py",
        description="Assemble an adversarial-audit brief for a PR and print it.",
    )
    ap.add_argument("pr", type=int, help="the PR number")
    ap.add_argument("--round", dest="round_no", type=int, default=1,
                    help="round number; >=2 assembles a DELTA re-audit brief")
    ap.add_argument("--repo", help="owner/name, when the PR is not in the cwd's repo")
    ap.add_argument("--out", help="write the brief to this file instead of stdout")
    ap.add_argument("--emit-claims", action="store_true",
                    help="also print an audit-claims block skeleton to paste "
                         "into this round's PR comment")
    ap.add_argument("--claims-file",
                    help="read claims-block text from this file instead of the "
                         "PR's comments (offline/testing seam)")
    return ap


def main(argv=None, runner=real_runner, cwd=None, stdout=None, stderr=None,
         checklist_reader=None):
    # `checklist_reader` is injected by the test suite so no test depends on
    # whether THIS HOST happens to have the skill deployed under ~/.claude —
    # a suite that reads the ambient home is not hermetic, and the sandbox gate
    # tier runs with a different one.
    checklist_reader = checklist_reader or _read_checklist
    args = build_parser().parse_args(argv)
    out_stream = stdout or sys.stdout
    err_stream = stderr or sys.stderr
    cwd = cwd or os.getcwd()

    repo_dir, cwd_slug = gather_repo_facts(runner, cwd)
    branch, dirty = gather_checkout_state(runner, repo_dir)

    if args.claims_file:
        try:
            comment_texts = [Path(args.claims_file).read_text(encoding="utf-8")]
        except OSError as e:
            print(f"cannot read --claims-file: {e}", file=err_stream)
            return 2
        claims_source = f"`{args.claims_file}`"
        data = {"title": f"PR #{args.pr}", "url": "", "baseRefName": "main"}
    else:
        data, comment_texts = gh_pr_facts(runner, args.pr, args.repo)
        claims_source = f"PR #{args.pr}'s comments"
        if data.get("_error"):
            print(f"🔴 `gh pr view {args.pr}` failed: {data['_error']}",
                  file=err_stream)
            return 3

    repo = pr_slug(data, args.repo) or cwd_slug or "UNKNOWN/UNKNOWN"
    blocks, malformed = parse_claims_blocks(comment_texts)
    newest = newest_block(blocks)

    # ------------------------------------------------------------------ #
    # 🔴 REFUSAL 1 — a delta round with nothing to be framed on.
    # ------------------------------------------------------------------ #
    if args.round_no >= 2 and newest is None:
        why = (
            "  reason: " + "\n          ".join(malformed)
            if malformed else
            f"  reason: no `audit-claims` block in any of the "
            f"{len(comment_texts)} comment(s) read"
        )
        print("\n".join([
            f"{REFUSAL_HEADER} for round {args.round_no} of PR #{args.pr}.",
            "",
            "  looked for: a fenced block of the form",
            "",
            "      ```audit-claims round=<n> audited=<sha>..<sha>",
            "      1. <what was claimed fixed>",
            "      ```",
            "",
            f"  looked in : {claims_source}",
            why,
            "",
            "  An empty \"what was claimed fixed\" section silently turns a "
            "DELTA re-audit into a blind full audit — a different thing, which "
            "would then read as covered. So this is refused, not emitted.",
            "",
            f"  Fix: run `audit-dispatch.py {args.pr} --round "
            f"{args.round_no - 1} --emit-claims`, fill the skeleton in, and "
            "post it as a comment on the PR. Or run this round as an explicit "
            "first, full audit with no --round.",
        ]), file=err_stream)
        return 2

    prev_sha = newest.audited_to if newest else None
    claims = list(newest.items) if newest else []
    claims_round = newest.round_no if newest else None
    if newest is not None and newest.round_no >= args.round_no:
        print(
            f"⚠ the newest claims block says round={newest.round_no}, and you "
            f"asked for round {args.round_no}. Using it anyway — but check you "
            "are not re-auditing a round that already ran.",
            file=err_stream,
        )

    ledger = None
    base_ref = data.get("baseRefName") or "main"
    base_for_range = f"origin/{base_ref}"
    if args.round_no >= 2 and prev_sha:
        ledger = measure_ledger(runner, repo_dir, prev_sha, base_for_range)
        anchor = round_one_anchor(blocks)
        if ledger.reason is None and anchor:
            cum = measure_ledger(runner, repo_dir, anchor, base_for_range)
            if cum.reason is None:
                ledger = ledger._replace(cumulative=cum.added + cum.deleted)

    facts = Facts(
        pr=args.pr,
        repo=repo,
        title=data.get("title") or "(no title)",
        base_ref=base_ref if args.round_no < 2 else base_for_range,
        url=data.get("url") or "",
        round_no=args.round_no,
        cwd_repo_dir=repo_dir,
        cwd_repo_slug=cwd_slug,
        cross_repo=bool(cwd_slug and repo and cwd_slug != repo),
        branch=branch,
        dirty=dirty,
        prev_sha=prev_sha,
        claims=claims,
        claims_round=claims_round,
        checklist=checklist_reader(repo_dir),
        ledger=ledger,
        assembled_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%MZ"),
        claims_source=claims_source,
    )

    brief = render_brief(facts)

    # ------------------------------------------------------------------ #
    # REFUSAL 2's opposite number: WARN, never block.
    # ------------------------------------------------------------------ #
    gone = missing_clauses(brief)
    if gone:
        print(
            "⚠ the assembled brief is missing invariant clause(s): "
            + ", ".join(gone)
            + "\n  This is a WARNING, not a refusal — the brief is still "
              "emitted. Re-add them by hand, or re-run without --out edits.",
            file=err_stream,
        )

    if args.out:
        Path(args.out).write_text(brief, encoding="utf-8")
        print(f"wrote {len(brief):,} chars to {args.out}", file=err_stream)
    else:
        print(brief, file=out_stream)

    if args.emit_claims:
        rc, head, _ = runner(["git", "-C", repo_dir, "rev-parse", "--short", "HEAD"])
        head_sha = head.strip() if rc == 0 and head.strip() else "<HEAD sha>"
        print("\n" + _bar(), file=out_stream)
        print("Paste this into the PR comment for this round, so the NEXT "
              "round can read it:\n", file=out_stream)
        print(emit_claims_skeleton(facts, head_sha), file=out_stream)
    return 0


if __name__ == "__main__":
    sys.exit(main())
