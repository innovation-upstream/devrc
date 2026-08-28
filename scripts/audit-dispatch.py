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
   exists for a hand-edited brief, and `claude/RULES.md` is explicit that a
   permanently-red gate trains everyone to click through it. Warn-only is the
   right severity for a channel whose failure is cosmetic and whose false
   positive rate is set by whatever a human typed. It runs over `--check FILE`
   and over the READ-BACK of `--out` — never over the in-memory string, where
   it was unreachable by construction and could not have fired for any input.

🔴 EVERY NUMBER HERE IS ABOUT THE PR, NOT ABOUT YOUR CHECKOUT
-------------------------------------------------------------
The delta half of a brief — the ledger, the `<prev>..HEAD` range, the `audited=`
sha `--emit-claims` writes for the NEXT round to anchor on — was resolved
against `HEAD` in the operator's own SHARED checkout, with nothing checking that
HEAD was the PR at all. From a clone standing on an unrelated branch that
produced rc 0, silent stderr, a non-empty range and a confident ledger of the
wrong branch's files; from `main` it produced the banner "🔴 Zero changed lines
over a NON-EMPTY range. That is a real measurement, not a failure."

So `git rev-parse HEAD == headRefOid` is a FOURTH read rule beside rc 0, silent
stderr and a non-empty range, and all three consumers emit COULD NOT MEASURE
naming that cause when it does not hold.

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
#
# 🔴 THAT COMMENT WAS FALSE FOR FOUR SHAPES, so the parser is line-based rather
# than one regex. The old `^(?P=fence)\s*$` backreference required the CLOSING
# fence to be byte-identical to the opener, and anything it could not match was
# dropped with no report at all:
#   * an UNCLOSED fence — the refusal then said "no `audit-claims` block in any
#     of the 1 comment(s) read", which is false and points at the wrong fix;
#   * a 4-backtick opener closed with 3 — not a close under CommonMark either,
#     so this really is unclosed and is now named as such;
#   * a 3-backtick opener closed with 4 — which IS a valid CommonMark close and
#     renders closed on GitHub, so it is now PARSED rather than dropped;
#   * a nested fence inside the body, which ends the block early and silently
#     drops every claim after it.
# A claim that WRAPS onto a continuation line was silently truncated to its
# first line, changing the claim; continuation lines are now appended.
_FENCE_OPEN = re.compile(r"^(?P<fence>`{3,})audit-claims(?P<header>[^\n]*)$")
_FENCE_BARE = re.compile(r"^(?P<fence>`{3,})\s*$")
_HEADER_ROUND = re.compile(r"\bround=(\d+)\b")
_HEADER_AUDITED = re.compile(r"\baudited=(\S+)")
_CLAIM_ITEM = re.compile(r"^\s*(\d+)[.)]\s+(.+?)\s*$")

ClaimsBlock = namedtuple("ClaimsBlock", "round_no audited_from audited_to items")


def _items_from_body(body_lines):
    """Numbered claim lines, with CONTINUATION lines folded into the item above.

    A claim wrapped over two lines used to lose everything after the first —
    which does not fail, it changes the claim, and the next round is framed on
    the truncated text.
    """
    items = []
    for line in body_lines:
        m = _CLAIM_ITEM.match(line)
        if m:
            items.append(m.group(2))
        elif items and line.strip():
            items[-1] = f"{items[-1]} {line.strip()}"
    return items


def parse_claims_blocks(texts):
    """-> (blocks, malformed_reasons) over an iterable of comment bodies.

    Pure and independently testable: the refusal below is only trustworthy if
    this can be driven with no network and no PR.

    🔴 `malformed` is NOT only populated when the block is unusable. A structural
    problem that still leaves a parseable block (a nested fence that cut the
    claims in half) is reported too, and `main` warns about it rather than
    letting a half-read block pass as a whole one.
    """
    blocks, malformed = [], []
    for text in texts:
        lines = (text or "").splitlines()
        i = 0
        while i < len(lines):
            om = _FENCE_OPEN.match(lines[i])
            if om is None:
                i += 1
                continue
            opener, header = om.group("fence"), om.group("header")

            body, close_at, j = [], None, i + 1
            while j < len(lines):
                bm = _FENCE_BARE.match(lines[j])
                # CommonMark: a closing fence must be AT LEAST as long as the
                # opener. A shorter run of backticks is content, not a close.
                if bm and len(bm.group("fence")) >= len(opener):
                    close_at = j
                    break
                body.append(lines[j])
                j += 1

            if close_at is None:
                malformed.append(
                    "an `audit-claims` fence that is never CLOSED — no line of "
                    f"{len(opener)} or more backticks follows it (header was: "
                    f"`{header.strip() or '<empty>'}`). A shorter closing fence "
                    "does not close it, under CommonMark or here."
                )
                i += 1
                continue

            r = _HEADER_ROUND.search(header)
            a = _HEADER_AUDITED.search(header)
            if not r or not a:
                malformed.append(
                    "an `audit-claims` fence whose header does not carry both "
                    f"`round=<n>` and `audited=<sha>..<sha>` (header was:"
                    f" `{header.strip() or '<empty>'}`)"
                )
                i = close_at + 1
                continue

            spec = a.group(1)
            frm, _, to = spec.partition("..")
            if not to:
                # A bare sha is accepted as the audited TIP; for a round-1 block
                # that tip IS the cumulative anchor (see `round_one_anchor`),
                # and for any later round the anchor is simply unknown, which
                # the ledger reports rather than guesses.
                frm, to = "", frm

            items = _items_from_body(body)
            if not items:
                malformed.append(
                    f"an `audit-claims round={r.group(1)}` block with no "
                    "numbered claim lines in its body"
                )
                i = close_at + 1
                continue

            # 🔴 The nested-fence report. A fence inside the body ends the block
            # early — CommonMark and GitHub agree — so the claims after it are
            # outside it and were dropped with no signal. The tell is claim
            # line(s) AND a stray fence in the region after the close, which is
            # what a cut-in-half block leaves behind. Deliberately a report and
            # not a rejection: the block that DID parse is still used, and a
            # comment whose trailing prose merely happens to contain both would
            # otherwise lose a usable block to a heuristic.
            k = close_at + 1
            tail = []
            while k < len(lines) and _FENCE_OPEN.match(lines[k]) is None:
                tail.append(lines[k])
                k += 1
            if any(_CLAIM_ITEM.match(t) for t in tail) and any(
                _FENCE_BARE.match(t) for t in tail
            ):
                malformed.append(
                    f"an `audit-claims round={r.group(1)}` block that looks CUT "
                    "SHORT by a nested fence: numbered claim line(s) and a "
                    "stray fence follow its closing fence, so claims after the "
                    f"nested fence were not read (read {len(items)}). Indent "
                    "any code inside a claim, or open the block with four "
                    "backticks."
                )

            blocks.append(ClaimsBlock(int(r.group(1)), frm, to, items))
            i = close_at + 1
    return blocks, malformed


def newest_block(blocks):
    """The highest `round=` present. Ties resolve to the last one seen."""
    best = None
    for b in blocks:
        if best is None or b.round_no >= best.round_no:
            best = b
    return best


def round_one_anchor(blocks):
    """The sha ROUND 1 audited, or None.

    That is the only sha in the corpus that can anchor the ledger's cumulative
    "since round 1" figure. When no block carries one, the figure is reported
    NOT MEASURED — never derived from the base, which is a different quantity.

    Two spellings carry it, and both are read:
      * `round=2 audited=A..B` — A is the tip round 1 audited (the usual case);
      * `round=1 audited=A`    — the bare form `--emit-claims` writes for a
        round-1 comment, where the audited TIP is itself that same quantity.
    A bare sha on any LATER round is NOT an anchor: it says what that round
    audited, and the round-1 tip is then genuinely unknown.
    """
    candidates = []
    for b in blocks:
        if b.audited_from:
            candidates.append((b.round_no, b.audited_from))
        elif b.round_no == 1 and b.audited_to:
            candidates.append((b.round_no, b.audited_to))
    # Stable sort on the round number alone, so a tie resolves to the block seen
    # first rather than to whichever sha sorts lower.
    candidates.sort(key=lambda c: c[0])
    return candidates[0][1] if candidates else None


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

LedgerReport = namedtuple(
    "LedgerReport",
    "files added deleted commits reason cumulative cumulative_reason",
)
# 🔴 `head_check` is the FOURTH read rule, beside rc 0 / silent stderr /
# non-empty range. See `verify_head_is_the_pr`.
HeadCheck = namedtuple("HeadCheck", "ok reason local_sha pr_sha")
Facts = namedtuple(
    "Facts",
    "pr repo title base_ref url round_no cwd_repo_dir cwd_repo_slug repo_relation "
    "branch dirty prev_sha claims claims_round checklist ledger assembled_at "
    "claims_source head_check",
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
    """PR metadata + comment bodies, via `gh`. -> (dict, [comment bodies]).

    🔴 `headRefOid` and `isCrossRepository` are read because the two decisions
    that were WRONG without them are the two most expensive ones this script
    makes: which tree the ledger measured, and which repository the PR lives in.
    🔴 There is NO `baseRepository` field on `gh pr view --json` (checked
    against `gh`'s own field list) — `url` is what carries the base repo, and
    `pr_slug` reads it.
    """
    cmd = ["gh", "pr", "view", str(pr), "--json",
           "title,url,baseRefName,headRefOid,isCrossRepository,"
           "headRepository,headRepositoryOwner,comments"]
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


# `https://<host>/<owner>/<name>/pull/<n>` — the PR's own URL, which names the
# repository the PR LIVES IN. Host-agnostic on purpose (GHES spells the host
# differently and the path shape is the same).
_PR_URL = re.compile(r"^https?://[^/]+/([^/]+)/([^/]+)/pull/\d+")


def pr_slug(data, override=None):
    """The repo the PR LIVES IN — NOT the repo its head branch lives in.

    🔴 This read `headRepositoryOwner`/`headRepository` and was therefore wrong
    for every FORK PR. Verified against real `gh` output for a fork PR against
    `cli/cli`: `headRepository.nameWithOwner` is `ylfeng250/cli` while `url` is
    `https://github.com/cli/cli/pull/14280` and `isCrossRepository` is `true`.
    A fork PR opened against THIS repo therefore computed a slug that differs
    from the cwd's, took the CROSS-REPO branch, and told the agent to worktree
    "your local clone of <contributor>/<repo>" — a clone that does not exist,
    for a repository the PR is not in.

    `gh pr view --json` has no `baseRepository` field, so `url` is the authority
    and `isCrossRepository` is what says whether the head fields may stand in
    for it: they are the same repo only when it is false.
    """
    if override:
        return override
    m = _PR_URL.match((data.get("url") or "").strip())
    if m:
        return f"{m.group(1)}/{m.group(2)}"
    if data.get("isCrossRepository") is False:
        owner = (data.get("headRepositoryOwner") or {}).get("login")
        name = (data.get("headRepository") or {}).get("name")
        if owner and name:
            return f"{owner}/{name}"
    # 🔴 None, never a guess. `main` renders a third WHERE-TO-WORK branch for
    # this, because collapsing "cannot tell" into "same repo" recommends the
    # flag that is dangerous in exactly the case it cannot rule out.
    return None


def verify_head_is_the_pr(runner, repo_dir, pr_head_sha):
    """🔴 The FOURTH read rule: is this checkout's HEAD the PR's head commit?

    Everything the delta half of this brief says — the ledger's numbers, the
    `<prev>..HEAD` range it hands the auditor, and the `audited=` sha
    `--emit-claims` stamps for the NEXT round to anchor on — is computed against
    `HEAD` in the operator's own checkout. That checkout is SHARED and moves;
    nothing here ever checked that it was standing on the PR at all.

    Reproduced from a clone standing on an unrelated feature branch: rc 0,
    silent stderr, a non-empty range — all three advertised read rules satisfied
    — and a ledger of that branch's files. Standing on `main` instead produced
    the confident banner "🔴 Zero changed lines over a NON-EMPTY range. That is
    a real measurement, not a failure."

    So a failed check returns a `reason` and NO number, exactly like the other
    three: an unverifiable measurement is not a measurement.
    """
    if not pr_head_sha:
        return HeadCheck(
            False,
            "the PR's head sha is not known here (`gh` was not consulted — "
            "`--claims-file` mode — or reported no `headRefOid`), so nothing "
            "can confirm this checkout is standing on the PR",
            None,
            None,
        )
    rc, out, err = runner(["git", "-C", repo_dir, "rev-parse", "HEAD"])
    local = out.strip()
    if rc != 0 or not local:
        return HeadCheck(
            False,
            f"`git rev-parse HEAD` in {repo_dir} exited {rc}: "
            f"{(err or out).strip() or 'no output'}",
            None,
            pr_head_sha,
        )
    if local != pr_head_sha:
        return HeadCheck(
            False,
            f"this checkout's HEAD is `{local}`, but the PR's head is "
            f"`{pr_head_sha}` — the two are DIFFERENT COMMITS, so anything "
            "measured against `HEAD` here is a measurement of another tree",
            local,
            pr_head_sha,
        )
    return HeadCheck(True, None, local, pr_head_sha)


# --------------------------------------------------------------------------- #
# The ledger — the skill's own command, with the skill's own read rules
# --------------------------------------------------------------------------- #

def measure_ledger(runner, repo_dir, prev_sha, base, head_check=None):
    """`git log --numstat --format= --remerge-diff <prev>..HEAD --not <base>`.

    🔴 FOUR read rules are enforced here, not assumed: **the checkout's HEAD is
    the PR's head, rc 0, silent stderr, and a non-empty range**.

    The fourth one came last and is the one that lets the other three pass while
    the answer is entirely wrong: `HEAD` is resolved in the operator's own
    SHARED checkout, which is not necessarily standing on the PR. Reproduced
    from a clone on an unrelated branch — rc 0, silent stderr, a non-empty range
    and a file list belonging to that branch. `head_check` is
    `verify_head_is_the_pr`'s verdict; passing None skips it, which is only for
    a caller that has no PR to compare against.

    A missing ref or a git without `--remerge-diff` exits 128 with empty output;
    an unwritable object store makes `--remerge-diff` UNDER-count, exit 0 and
    print a plausible number, announcing itself only on stderr; and a range
    whose commits are not in this checkout prints nothing at all, silently, with
    rc 0. Each of those returns a `reason` and NO number — a failed command is
    not a zero, and neither is a measurement of the wrong tree.
    """
    def fail(reason):
        return LedgerReport(None, None, None, None, reason, None, None)

    if head_check is not None and not head_check.ok:
        return fail(
            "this checkout is not standing on the PR, so `..HEAD` does not "
            f"mean what this ledger would claim it means: {head_check.reason}"
        )

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
    return LedgerReport(files, added, deleted, commits, None, None, None)


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

    🔴 THREE states, not two. The decision used to be `bool(cwd_slug and repo
    and cwd_slug != repo)`, so "could not determine either side" evaluated
    FALSE and collapsed into the SAME-REPO branch — the one that recommends the
    flag. With no `origin` remote the output contradicted itself in one
    paragraph: "The PR lives in `<org>/<other-repo>`, which is this session's
    own repository (`/home/…/devrc`)", followed by the recommendation. Not
    knowing is its own answer, and it is the answer that must NOT recommend a
    flag whose failure mode is silent.
    """
    if facts.repo_relation == "unknown":
        unknown_side = (
            "this checkout has no `origin` remote to resolve a slug from"
            if not facts.cwd_repo_slug else
            "`gh` did not report which repository the PR lives in"
        )
        return "\n".join([
            "## WHERE TO WORK — 🔴 COULD NOT DETERMINE — decide by hand",
            "",
            "This script could not establish whether the PR is in THIS "
            f"session's repository or another one: {unknown_side}.",
            "",
            f"    the PR's repo     : {facts.repo}",
            f"    this checkout     : {facts.cwd_repo_dir}"
            + (f" (`{facts.cwd_repo_slug}`)" if facts.cwd_repo_slug else
               " (no `origin` remote)"),
            "",
            f"🔴 {ISOLATION_FORBID} until that is answered. The flag builds its "
            "worktree from the CWD's repo; if that is the wrong repo it fails "
            "quietly — the agent either reports a briefed file missing or "
            "silently audits the wrong tree — and this script cannot currently "
            "rule that out. **Not knowing is not the same as same-repo**, and "
            "the same-repo branch is the one that recommends the flag.",
            "",
            "Answer it, then follow the matching directive:",
            "",
            "```",
            f"gh pr view {facts.pr} --json url          # the repo the PR lives in",
            f"git -C {facts.cwd_repo_dir} remote get-url origin",
            "```",
            "",
            "Or re-run this script with `--repo owner/name` to state the PR's "
            "repository outright.",
        ])
    if facts.repo_relation == "cross":
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
    # 🔴 `..HEAD` is only meaningful once HEAD has been shown to BE the PR's
    # head. Unverified, the range handed to the auditor points at whatever tree
    # the shared checkout happens to be standing on — reproduced against an
    # unrelated feature branch, where it read as an ordinary non-empty range.
    hc = facts.head_check
    if hc is not None and hc.ok:
        tip = "HEAD"
        note = (
            f"This checkout's HEAD is `{hc.local_sha}`, verified at assembly "
            f"time to be PR #{facts.pr}'s head commit — which is what makes "
            "`..HEAD` mean the PR here."
        )
    else:
        tip = hc.pr_sha if (hc is not None and hc.pr_sha) else "<the PR's head sha>"
        note = (
            "🔴 **COULD NOT VERIFY that this checkout is standing on the PR**, "
            "so `..HEAD` is NOT used above — it would name whatever tree the "
            "shared checkout is on:\n\n"
            f"    {hc.reason if hc is not None else 'no check was made'}\n\n"
            "Resolve the range in a tree that CONTAINS the PR's head before "
            "trusting any number derived from it."
        )
    return "\n".join([
        "## THE RANGE",
        "",
        f"A DELTA re-audit, round {facts.round_no}. Diff **`{facts.prev_sha}"
        f"..{tip}`** — the fix commits made since the tip round "
        f"{facts.claims_round} audited. Do not re-audit the whole PR.",
        "",
        note,
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
    """🔴 The operator's checkout resolves the TOOLCHAIN and nothing else.

    Every command here used to interpolate `facts.cwd_repo_dir`, including the
    two that name the tree UNDER TEST. `scripts/gate.sh` resolves its own `ROOT`
    from `BASH_SOURCE`, so `nix develop <shared> -c bash <shared>/scripts/
    gate.sh` runs the suite in the SHARED CHECKOUT, on whatever branch it is
    standing on; `nix build <shared>#checks…` builds that flake ref's tree, not
    the auditor's. Both contradicted WHERE TO WORK three bars above, and the
    auditor then obeyed the very next sentence — "name the tier and the base
    sha" — and named the wrong sha.

    `nix develop {r}` is deliberately left pointing at the operator's checkout:
    that one is only resolving a dev shell (the toolchain), and it is the door
    the repo's own CLAUDE.md tells everyone to use.
    """
    r = facts.cwd_repo_dir
    return "\n".join([
        "## TOOLCHAIN — the exact commands, and the two ways they lie",
        "",
        "🔴 `<your worktree>` below is **your own copy** — the one WHERE TO WORK "
        f"told you to make. `{r}` appears ONLY as the argument to `nix develop`, "
        "where it resolves the dev shell and nothing else. Never point the gate "
        "or a `nix build` at it: `gate.sh` resolves its root from its own path, "
        "so running the shared copy runs the suite in the SHARED CHECKOUT on "
        "whatever branch it is standing on, and a `nix build <ref>#…` builds "
        "that ref's tree, not yours.",
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
        f"nix develop {r} -c bash <your worktree>/scripts/gate.sh --tier both",
        "```",
        "",
        "🔴 The gate above is the DEV-HOST tier. **The tier the merge actually "
        "gates on is the sandbox one**, which builds from a store copy with no "
        "`.git` and is therefore blind to different things:",
        "",
        "```",
        "nix build <your worktree>#checks.x86_64-linux.pytests",
        "nix build <your worktree>#checks.x86_64-linux.nodetests",
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
        # 🔴 TWO mechanisms, and they need opposite fixes: there was no anchor
        # sha to measure from, or there WAS one and the measurement failed. The
        # no-anchor sentence used to be printed for both, sending an operator
        # who already had a round-1 anchor off to add one — reproduced with a
        # round-1 block whose `audited=` sha makes `rev-list` exit 128. Same
        # empty-result trap this module refuses everywhere else.
        if led.cumulative_reason:
            lines += [
                "",
                "⚠ The cumulative figure (Y, since round 1) is **NOT "
                "MEASURED** — an anchor sha WAS found, and measuring from it "
                "failed:",
                "",
                f"    {led.cumulative_reason}",
                "",
                "So this is a broken measurement, not a missing anchor: adding "
                "another `audit-claims` block will not fix it.",
            ]
        else:
            lines += [
                "",
                "⚠ The cumulative figure (Y, since round 1) is **NOT "
                "MEASURED**: no `audit-claims` block carried a round-1 anchor "
                "sha. It is not derivable from the base, which is a different "
                "quantity.",
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

    🔴 TWO defects lived in one line here.

    1. With no prior block — a round 1 `--emit-claims`, which is *the remedy the
       refusal advertises* — `prev_sha` was None and the placeholder
       `<the sha round 1 audited>` was interpolated INTO the header. The parser
       then read `audited=<the`, found no `..`, and yielded `audited_from=''`,
       `audited_to='<the'`; the next round's brief said ``Diff `<the..HEAD` ``
       with rc 0 and no refusal at all. The parser already accepts a BARE sha as
       the audited tip, and for round 1 that tip is exactly the quantity the
       header carries, so the bare form is what is emitted.
    2. `head_sha` used to be the operator checkout's `git rev-parse --short
       HEAD` — which is only the PR's head if the shared checkout happens to be
       standing on it, and is otherwise a record of some other branch's tip
       going into the field the NEXT round anchors on. The caller now passes the
       PR's own head sha.
    """
    if facts.prev_sha:
        audited = f"{facts.prev_sha}..{head_sha}"
    else:
        # Round 1: no previous tip exists, so the header carries the audited tip
        # ALONE. `round_one_anchor` reads a round-1 bare sha as the cumulative
        # anchor, so the ledger stays measurable from here on.
        audited = head_sha
    return "\n".join([
        f"```audit-claims round={facts.round_no} audited={audited}",
        "1. <one line per thing this round's fixes CLAIM to have addressed — "
        "WHAT was claimed, never WHY it is correct>",
        "2. <one line, same rule>",
        "```",
    ])


# --------------------------------------------------------------------------- #
# The warn-only completeness check
# --------------------------------------------------------------------------- #

def _norm(text):
    """Whitespace-normalised, so a re-wrap is not a loss but a reword is.

    A hand-edited brief gets re-wrapped by editors and by paste; a clause that
    survived the edit intact should not be reported missing because a line
    break moved.
    """
    return " ".join((text or "").split())


def missing_clauses(brief):
    """Clause ids whose text is not present in `brief` (whitespace-normalised).

    🔴 WARN-ONLY BY DESIGN. `claude/RULES.md` says a permanently-red gate trains
    everyone to click through it, and a brief a human deliberately edited is not
    a defect. Blocking here would make the tool refuse to run over that edit.

    🔴 IT WAS ALSO UNREACHABLE. The docstring said "it exists for a hand-edited
    `--out` file" and nothing ever read an `--out` file back: the only caller
    passed the string `render_brief` had just built out of `INVARIANT_CLAUSES`,
    so every clause was present BY CONSTRUCTION and the check could not fail for
    any input a user could supply. The suite reached it only by monkeypatching
    `render_brief` to a lossy stub — which is the `unreachable-guards` shape in
    `claude/RULES.md`, and it mattered: the hand-written brief that dispatched
    the audit of this very script HAD been edited, several clauses shortened and
    the checklist paraphrased, and the shipped check could not notice.

    Two real inputs now reach it: `--check FILE` (a brief someone edited) and
    the READ-BACK of `--out` (which also catches a write that lost bytes).
    """
    haystack = _norm(brief)
    return [c.id for c in INVARIANT_CLAUSES if _norm(c.text) not in haystack]


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
    # `nargs="?"` exists ONLY so `--check FILE` can run with no PR at all — it
    # inspects a file and consults neither `gh` nor `git`. Every other path
    # still requires the number, and `main` says so rather than proceeding with
    # `pr=None`.
    ap.add_argument("pr", type=int, nargs="?", help="the PR number")
    ap.add_argument("--round", dest="round_no", type=int, default=1,
                    help="round number; >=2 assembles a DELTA re-audit brief")
    ap.add_argument("--repo", help="owner/name, when the PR is not in the cwd's repo")
    ap.add_argument("--out", help="write the brief to this file instead of stdout")
    ap.add_argument("--check", metavar="FILE",
                    help="check an EXISTING brief file for missing invariant "
                         "clauses and exit; consults no PR and no git")
    ap.add_argument("--emit-claims", action="store_true",
                    help="also print an audit-claims block skeleton to paste "
                         "into this round's PR comment")
    ap.add_argument("--claims-file",
                    help="read claims-block text from this file instead of the "
                         "PR's comments (offline/testing seam)")
    return ap


def check_brief_file(path, out_stream, err_stream):
    """`--check FILE` — run the clause check over a brief someone EDITED.

    🔴 This is what makes `missing_clauses` reachable in production. Warn-only,
    like its in-process sibling: it reports and returns 0, because the operator
    who edited the brief may have meant to.
    """
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as e:
        print(f"cannot read --check file: {e}", file=err_stream)
        return 2
    gone = missing_clauses(text)
    if gone:
        print(
            f"⚠ {path} is missing invariant clause(s): " + ", ".join(gone)
            + "\n  This is a WARNING, not a refusal. Each is a clause a "
              "hand-written brief was MEASURED to lose; re-add them, or "
              "re-generate the brief and edit less.",
            file=err_stream,
        )
    else:
        print(
            f"{path}: all {len(INVARIANT_CLAUSES)} invariant clause(s) present.",
            file=out_stream,
        )
    return 0


def main(argv=None, runner=real_runner, cwd=None, stdout=None, stderr=None,
         checklist_reader=None):
    # `checklist_reader` is injected by the test suite so no test depends on
    # whether THIS HOST happens to have the skill deployed under ~/.claude —
    # a suite that reads the ambient home is not hermetic, and the sandbox gate
    # tier runs with a different one.
    checklist_reader = checklist_reader or _read_checklist
    parser = build_parser()
    args = parser.parse_args(argv)
    out_stream = stdout or sys.stdout
    err_stream = stderr or sys.stderr
    cwd = cwd or os.getcwd()

    if args.check:
        return check_brief_file(args.check, out_stream, err_stream)
    if args.pr is None:
        parser.error("the PR number is required (or use --check FILE)")

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

    # 🔴 THREE states. `pr_repo` is None when neither `url`, `isCrossRepository`
    # nor `--repo` could answer it; falling back to `cwd_slug` there (as this
    # did) makes the comparison compare the cwd with ITSELF and silently yields
    # "same repo" — the branch that recommends `isolation: "worktree"`.
    pr_repo = pr_slug(data, args.repo)
    if pr_repo and cwd_slug:
        repo_relation = "same" if pr_repo == cwd_slug else "cross"
    else:
        repo_relation = "unknown"
    repo = pr_repo or "UNKNOWN (not reported by `gh`; pass --repo owner/name)"

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
            "  ⚠ `gh pr view --json comments` returns ISSUE comments only. A "
            "block posted as a REVIEW comment, as a reply inside a review "
            "thread, or in the PR's own DESCRIPTION is invisible to this "
            "script and is not counted above — check there before concluding "
            "nobody posted one.",
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

    # 🔴 A structural problem that still yielded a usable block must not be
    # silent either. The refusal above only fires when there is NO block, so a
    # comment holding one readable block and one unreadable fence would
    # otherwise pass as complete.
    if malformed and newest is not None:
        print(
            "⚠ the claims text held "
            f"{len(malformed)} thing(s) this script could not read cleanly:\n  "
            + "\n  ".join(malformed)
            + "\n  A block WAS found and is used below — but check that the "
              "claims it carries are all of them.",
            file=err_stream,
        )

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

    # 🔴 The FOURTH read rule, computed once and used by all three consumers
    # that were measuring the operator's checkout instead of the PR: the ledger,
    # the range section, and the `audited=` sha `--emit-claims` stamps.
    head_check = verify_head_is_the_pr(runner, repo_dir, data.get("headRefOid"))

    ledger = None
    base_ref = data.get("baseRefName") or "main"
    base_for_range = f"origin/{base_ref}"
    if args.round_no >= 2 and prev_sha:
        ledger = measure_ledger(
            runner, repo_dir, prev_sha, base_for_range, head_check
        )
        anchor = round_one_anchor(blocks)
        if ledger.reason is None and anchor:
            cum = measure_ledger(
                runner, repo_dir, anchor, base_for_range, head_check
            )
            if cum.reason is None:
                ledger = ledger._replace(cumulative=cum.added + cum.deleted)
            else:
                # 🔴 Carry the REASON. Dropping it made the brief print one
                # specific, false cause ("no block carried a round-1 anchor")
                # for a measurement that failed with an anchor in hand.
                ledger = ledger._replace(cumulative_reason=cum.reason)

    facts = Facts(
        pr=args.pr,
        repo=repo,
        title=data.get("title") or "(no title)",
        base_ref=base_ref if args.round_no < 2 else base_for_range,
        url=data.get("url") or "",
        round_no=args.round_no,
        cwd_repo_dir=repo_dir,
        cwd_repo_slug=cwd_slug,
        repo_relation=repo_relation,
        branch=branch,
        dirty=dirty,
        prev_sha=prev_sha,
        claims=claims,
        claims_round=claims_round,
        checklist=checklist_reader(repo_dir),
        ledger=ledger,
        assembled_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%MZ"),
        claims_source=claims_source,
        head_check=head_check,
    )

    brief = render_brief(facts)

    if args.out:
        Path(args.out).write_text(brief, encoding="utf-8")
        print(f"wrote {len(brief):,} chars to {args.out}", file=err_stream)
    else:
        print(brief, file=out_stream)

    # ------------------------------------------------------------------ #
    # REFUSAL 2's opposite number: WARN, never block.
    # ------------------------------------------------------------------ #
    # 🔴 Checked against what is ON DISK when there is a disk copy, not against
    # the string just built. Checking the in-memory brief could only ever pass:
    # it was rendered FROM `INVARIANT_CLAUSES` a few lines earlier, so every
    # clause was present by construction and this warning was unreachable for
    # any input a user could supply. The read-back also catches a write that
    # lost bytes. `--check FILE` is the other real input.
    checked_what, checked_text = "the assembled brief", brief
    if args.out:
        try:
            checked_text = Path(args.out).read_text(encoding="utf-8")
            checked_what = args.out
        except OSError as e:
            print(f"⚠ could not re-read {args.out} to check it: {e}",
                  file=err_stream)
    gone = missing_clauses(checked_text)
    if gone:
        print(
            f"⚠ {checked_what} is missing invariant clause(s): "
            + ", ".join(gone)
            + "\n  This is a WARNING, not a refusal — the brief is still "
              "emitted. Re-add them by hand, or re-run without --out edits.",
            file=err_stream,
        )

    if args.emit_claims:
        # 🔴 The PR's OWN head, never the shared checkout's. This sha is what
        # the NEXT round anchors its range and its ledger on, so stamping the
        # local HEAD here recorded whatever branch this checkout was standing
        # on — `main`'s tip, in the reproduction — as the sha this round
        # audited.
        head_sha = (data.get("headRefOid") or "")[:8]
        if not head_sha:
            head_sha = "<the PR's head sha>"
            print(
                "⚠ --emit-claims could not read the PR's head sha "
                "(`headRefOid`), so the block below carries a PLACEHOLDER. "
                "Replace it with the sha this round actually audited before "
                "posting — the next round reads this field and refuses on a "
                "near-miss.",
                file=err_stream,
            )
        elif not head_check.ok:
            print(
                "⚠ --emit-claims stamped the PR's head sha, which is correct — "
                "but this checkout is NOT standing on it, so re-read the claims "
                f"you are about to write: {head_check.reason}",
                file=err_stream,
            )
        print("\n" + _bar(), file=out_stream)
        print("Paste this into the PR comment for this round, so the NEXT "
              "round can read it:\n", file=out_stream)
        print(emit_claims_skeleton(facts, head_sha), file=out_stream)
    return 0


if __name__ == "__main__":
    sys.exit(main())
