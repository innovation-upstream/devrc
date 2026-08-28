#!/usr/bin/env python3
"""Mutation battery for `scripts/audit-dispatch.py` — the audit-brief assembler.

    nix develop ~/workspace/devrc -c python3 scripts/tests/mutants-audit-dispatch.py

Not run by CI. An author/reviewer instrument, kept IN THE TREE so
"mutation-verified" can be RE-DERIVED instead of believed — the same reason
`scripts/tests/mutants-audit-ladder.sh` exists, and it records the incident:
devrc #900 ran ten rounds and recorded ~30 mutants in a module docstring, every
one of them inside a session scratchpad that no longer exists, so not a single
row could be re-checked by anyone else. This follows its conventions.

🔴 EACH MUTANT NAMES THE EXACT SET OF TESTS THAT MUST KILL IT — not "a test
failed". These pins overlap by design (a clause deletion trips the id ledger,
the fragment ledger and the rendered-section comparison), so a mutant can die to
a DIFFERENT test's error and be scored as covered while its own assertion is
unreachable. Two distinct failures are reported separately because the responses
differ:
  * an expected killer ABSENT   -> 🔴 WRONG-KILLER: that pin is DEAD.
  * an UNEXPECTED extra killer  -> 🔴 EXTRA-KILLER: something else now fires;
                                   the row no longer isolates what it names.
Collapsing them into one comparison routes the operator away from the first,
which is the failure this battery exists to catch.

🔴 EACH MUTATION IS ASSERTED TO APPLY BEFORE IT RUNS. A target string that no
longer matches — a line re-wrapped, a word changed — leaves the file UNMUTATED
and reports "the guard held", the most flattering possible wrong answer.

🔴 IT NEVER TOUCHES YOUR WORKING TREE. Everything happens in a `mktemp` copy
built by naming TWO INDIVIDUAL FILES. That selective copy, not the assertion
below it, is what keeps a `.git` out; the assertion is an INVARIANT GUARD
against the future refactor that replaces the file list with `cp -a`, which
would carry a worktree's `.git` POINTER FILE and let a git command inside the
copy act on the real repository.

🔴 PYTHONDONTWRITEBYTECODE=1, and `-p no:cacheprovider`. A stale `.pyc` keyed on
mtime-in-whole-seconds plus size is how a same-length edit gets scored SURVIVED
without ever executing.

🔴 THE BASELINE IS THE POSITIVE CONTROL. If the unmutated copy is not green,
every row below is meaningless and this exits 2 without running any of them —
and it distinguishes "the tree is red" from "pytest is not on PATH", because the
second one blames the tree for the shell you are in.

🔴 A ROW MAY EXPECT `SURVIVES`. Same convention as
`scripts/tests/mutants-audit-ladder.sh`: the clause ledger pins WHOLE
whitespace-normalised strings, so a pure RE-WRAP must stay green. A re-wrap that
went red would mean the pins are keyed to line breaks and every reflow of the
source becomes a test failure — which is the permanently-red gate
`claude/RULES.md` says trains people to click through. A `SURVIVES` row that
KILLS something is a finding, reported as such.

COVERAGE IS DELIBERATELY PARTIAL. What is here: one deletion per invariant
clause, an addition, one REWORD that leaves the clause present (the reachability
control for the clause ledger), five rewords that INVERT their clause, a
re-wrap control, one renderer bypass, and one inversion per generated decision —
including the four that shipped and were caught in round 2's audit (the head
check, the toolchain's tree-under-test, the fork-PR repo read, and the
three-state repo decision). What is NOT here: the `gh`/`git` boundary itself
(the suite injects a runner and says so), and whether a human classifies the
ledger's files correctly (nothing mechanical can grade that).
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT_REL = "scripts/audit-dispatch.py"
TEST_REL = "scripts/tests/test_audit_dispatch.py"
# 🔴 THIS FILE IS A THIRD INPUT TO THE SUITE, not only its driver. Round 4 made
# the fix matrix's mutants column an evidence claim graded against `ROWS`
# below, so the test module IMPORTS this file — and a scratch tree without it
# fails at collection, which reads as "the mutant killed everything".
HARNESS_REL = "scripts/tests/mutants-audit-dispatch.py"

# 🔴 A COLLAPSE floor, not a growth floor — same rule as
# `mutants-audit-ladder.sh`. A suite that never ran yields zero FAILED lines,
# i.e. "clean", so a harness wired to nothing would score every mutant SURVIVED.
MIN_TESTS = 50

# A row may name this instead of a killer set: the mutation MUST leave the suite
# green. See the module docstring — the clause ledger pins whole normalised
# strings, and a re-wrap that went red would make every reflow a test failure.
SURVIVES = "SURVIVES"

# 🔴 A THIRD expectation, and it is NOT a synonym for SURVIVES. `SURVIVES` is a
# CONTROL: the pin must not be keyed to layout, and a red there is a defect in
# the pin. `HOLE` is a MEASURED GAP: the mutation survives because nothing
# guards that sentence, which the script's own ledger comment says out loud
# rather than letting three unguarded blocks read as covered. The two print
# differently on purpose — "SURVIVED as required (control)" over a documented
# hole would be the flattering wrong answer this whole file exists to refuse.
#
# A HOLE that starts being KILLED is good news and is reported as an ACTION:
# promote the row to the killer set that now fires, and delete its line from
# the ledger comment in `scripts/audit-dispatch.py`.
HOLE = "HOLE"


# --------------------------------------------------------------------------- #
# Mutations. Each takes the script source and returns it changed, or raises.
# --------------------------------------------------------------------------- #

def drop_entry(kind, cid):
    """Delete a whole `Clause(...)` / `Directive(...)` entry from its tuple."""
    def f(t):
        pat = re.compile(
            r"    " + kind + r"\(\n        \"" + re.escape(cid)
            + r"\",\n(?:.*?\n)*?    \),\n"
        )
        new, n = pat.subn("", t, count=1)
        if n != 1:
            raise AssertionError(
                f"{kind.lower()} {cid!r} not found in its expected shape"
            )
        return new
    return f


def drop_clause(cid):
    return drop_entry("Clause", cid)


def _swap(t, old, new):
    if old not in t:
        raise AssertionError(f"target absent: {old[:70]!r}…")
    return t.replace(old, new, 1)


def add_unledgered_clause(t):
    marker = ")\n\nINVARIANTS_HEADING"
    return _swap(t, marker,
                 '    Clause("unledgered", "**An eighth instruction nobody '
                 'reviewed.**"),\n' + marker)


def reword_stop_rule(t):
    return _swap(
        t,
        '"**A clean round ENDS the ladder — ending it is the CORRECT outcome, "\n'
        '        "not a failure.** ',
        '"**A clean round ends the ladder.** ',
    )


def hardcode_bullets(t):
    return _swap(
        t,
        '    out += [f"- {c.text}" for c in INVARIANT_CLAUSES]',
        '    out += ["- **READ-ONLY.**", "- **Do not merge.**"]',
    )


def remove_the_refusal(t):
    return _swap(t,
                 "    if args.round_no >= 2 and newest is None:",
                 "    if False:")


def claims_from_the_whole_comment(t):
    return _swap(
        t,
        "    claims = list(newest.items) if newest else []",
        '    claims = ([ln for ln in (comment_texts[0] if comment_texts else "")'
        ".splitlines() if ln.strip()] if newest else [])",
    )


def invert_cross_repo(t):
    # Re-targeted when the two-state `cross_repo` flag became the three-state
    # `repo_relation`. Same mutation: the two KNOWN states swap, and the
    # "unknown" state is left alone so this row stays distinct from P2.
    return _swap(t,
                 '        repo_relation = "same" if pr_repo == cwd_slug else "cross"',
                 '        repo_relation = "cross" if pr_repo == cwd_slug else "same"')


def numstat_failure_reads_zero(t):
    return _swap(
        t,
        '    if rc != 0:\n'
        '        return fail(f"the numstat command exited {rc}: "\n'
        '                    f"{(err or out).strip() or \'no output\'}")\n',
        '    if rc != 0:\n        out = ""\n',
    )


def classify_by_pathspec(t):
    return _swap(
        t,
        '        f"round {facts.round_no} · payload lines changed THIS round: X "\n',
        '        f"round {facts.round_no} · payload lines changed THIS round: "\n'
        "        f\"{sum(a + d for p, (a, d) in led.files.items() "
        "if 'test' not in p)} \"\n",
    )


def the_warning_blocks(t):
    old = ('              "emitted. Re-add them by hand, or re-run without --out edits.",\n'
           "            file=err_stream,\n"
           "        )\n")
    return _swap(t, old, old + "        return 4\n")


# --------------------------------------------------------------------------- #
# 🔴 W1-W5 — the five clause REWORDS that INVERT the instruction.
# --------------------------------------------------------------------------- #
# Every one of these passed a fully green 37-test suite while `CLAUSE_LEDGER`
# pinned a FRAGMENT per clause: the fragment stayed present and the instruction
# around it said the opposite. W5 is the sharp one — it makes every future brief
# tell auditors to do what `claude/RULES.md` forbids.
#
# Each replaces the whole `Clause(...)` entry, so the clause is still PRESENT,
# still emitted, and still ledgered by id. Only the whole-string pin can see it.

def reword_entry(kind, cid, new_text):
    """Replace an entry's TEXT, leaving it present, ledgered by id and emitted.

    Only a WHOLE-STRING pin can see this. Every presence check stays green.
    """
    def f(t):
        pat = re.compile(
            r"(    " + kind + r"\(\n        \"" + re.escape(cid) + r"\",\n)"
            r"(?:.*?\n)*?(    \),\n)"
        )
        new, n = pat.subn(
            lambda m: m.group(1) + f'        "{new_text}",\n' + m.group(2),
            t, count=1,
        )
        if n != 1:
            raise AssertionError(
                f"{kind.lower()} {cid!r} not found in its expected shape"
            )
        return new
    return f


def reword_clause(cid, new_text):
    return reword_entry("Clause", cid, new_text)


# 🔴 The SURVIVES control. Whitespace inside a clause changes; the instruction
# does not. `_norm` on both sides is what must absorb it — so a RED here means
# the pins are keyed to layout, and every reflow of the source becomes a
# failure.
def rewrap_a_clause(t):
    # 🔴 RE-TARGETED in round 4 — the `no-fetch` clause was reworded when THE
    # CHECKOUT section became three-state, and this row immediately reported
    # MUTATION DID NOT APPLY. Third time an assert-before-editing has caught a
    # row that would otherwise have scored as "the guard held".
    # 🔴 RE-TARGETED AGAIN in round 5, for the fourth such catch: the clause
    # went back to being unconditional ("write to any checkout that is not the
    # copy YOU made"), and this row reported MUTATION DID NOT APPLY on the
    # first run after that edit.
    return _swap(
        t,
        '"**Do NOT `git fetch`, `pull`, `checkout` or otherwise write to any "',
        '"**Do NOT  `git fetch`,  `pull`,  `checkout`  or otherwise write to  any "',
    )


# --------------------------------------------------------------------------- #
# The four decisions round 2's audit found WRONG, one mutant per site.
# --------------------------------------------------------------------------- #

def ledger_ignores_the_head_check(t):
    return _swap(t,
                 "    if head_check is not None and not head_check.ok:",
                 "    if False:")


def range_ignores_the_head_check(t):
    # 🔴 RE-TARGETED in round 3, and the reason is the one C3 records: the
    # branch became `elif` when the degenerate-self-range case was inserted
    # ahead of it, and this row immediately reported MUTATION DID NOT APPLY.
    # Without that assert it would have scored as "the guard held" — the most
    # flattering possible wrong answer.
    return _swap(t, "    elif hc is not None and hc.ok:", "    elif True:")


def emit_claims_uses_the_local_head(t):
    return _swap(
        t,
        '        head_sha = (data.get("headRefOid") or "")[:8]\n',
        '        _rc, _h, _ = runner(["git", "-C", repo_dir, "rev-parse",\n'
        '                             "--short", "HEAD"])\n'
        '        head_sha = _h.strip() if _rc == 0 else ""\n',
    )


def emit_claims_interpolates_the_placeholder(t):
    return _swap(
        t,
        "        audited = head_sha\n",
        '        audited = f"<the sha round 1 audited>..{head_sha}"\n',
    )


def gate_points_at_the_shared_checkout(t):
    return _swap(
        t,
        'f"nix develop {r} -c bash <your worktree>/scripts/gate.sh --tier both",',
        'f"nix develop {r} -c bash {r}/scripts/gate.sh --tier both",',
    )


def nix_build_points_at_the_shared_checkout(t):
    return _swap(
        t,
        '        "nix build <your worktree>#checks.x86_64-linux.pytests",\n'
        '        "nix build <your worktree>#checks.x86_64-linux.nodetests",\n',
        '        f"nix build {r}#checks.x86_64-linux.pytests",\n'
        '        f"nix build {r}#checks.x86_64-linux.nodetests",\n',
    )


def pr_slug_reads_the_head_repo(t):
    return _swap(
        t,
        '    m = _PR_URL.match((data.get("url") or "").strip())\n'
        "    if m:\n"
        '        return f"{m.group(1)}/{m.group(2)}"\n'
        '    if data.get("isCrossRepository") is False:\n',
        "    if True:\n",
    )


def unknown_repo_collapses_to_same(t):
    return _swap(
        t,
        "    if pr_repo and cwd_slug:\n"
        '        repo_relation = "same" if pr_repo == cwd_slug else "cross"\n'
        "    else:\n"
        '        repo_relation = "unknown"\n',
        '    repo_relation = "cross" if (\n'
        "        pr_repo and cwd_slug and pr_repo != cwd_slug\n"
        '    ) else "same"\n',
    )


def the_check_command_reports_clean(t):
    return _swap(t, "    gone = missing_clauses(text)\n", "    gone = []\n")


def the_out_file_is_not_read_back(t):
    return _swap(
        t,
        '            checked_text = Path(args.out).read_text(encoding="utf-8")\n'
        "            checked_what = args.out\n",
        "            pass\n",
    )


def missing_clauses_is_not_normalised(t):
    return _swap(
        t,
        "    haystack = _norm(brief)\n"
        "    return [c.id for c in INVARIANT_CLAUSES if _norm(c.text) not in haystack]",
        '    return [c.id for c in INVARIANT_CLAUSES if c.text not in (brief or "")]',
    )


def the_cumulative_reason_is_dropped(t):
    return _swap(
        t,
        "                ledger = ledger._replace(cumulative_reason=cum.reason)",
        "                pass",
    )


def an_unclosed_fence_is_skipped_silently(t):
    return _swap(
        t,
        "            if close_at is None:\n                malformed.append(",
        "            if close_at is None:\n"
        "                i += 1\n"
        "                continue\n"
        "            if False:\n"
        "                malformed.append(",
    )


def a_longer_closing_fence_stops_closing(t):
    return _swap(
        t,
        'if bm and len(bm.group("fence")) >= len(opener):',
        'if bm and len(bm.group("fence")) == len(opener):',
    )


def continuation_lines_are_dropped(t):
    return _swap(
        t,
        "        elif items and line.strip():\n"
        '            items[-1] = f"{items[-1]} {line.strip()}"\n',
        "",
    )


def the_nested_fence_report_is_removed(t):
    return _swap(
        t,
        "            if any(_CLAIM_ITEM.match(t) for t in tail) and any(",
        "            if False and any(",
    )


def the_refusal_drops_the_comment_kinds_note(t):
    return _swap(
        t,
        '            "  ⚠ `gh pr view --json comments` returns ISSUE comments only. A "',
        '            "  ⚠ A "',
    )


# --------------------------------------------------------------------------- #
# 🔴 N1-N7 — the round-3 findings, one mutant per site.
# --------------------------------------------------------------------------- #
# N1 restores the shipped defect outright: `audited=` meant `<to>` to the
# WRITER and `<to>` to the READER, so `<to>..HEAD` had HEAD on both ends and the
# delta range was EMPTY BY CONSTRUCTION. N3 is its mirror image — the "one
# anchor for everything" simplification, which is wrong in the other direction
# and would NOT show up as an empty range, only as a superset.

def range_anchor_reads_the_audited_tip(t):
    return _swap(
        t,
        "    return block.audited_from or block.audited_to or None",
        "    return block.audited_to or None",
    )


def range_anchor_loses_the_bare_fallback(t):
    return _swap(
        t,
        "    return block.audited_from or block.audited_to or None",
        "    return block.audited_from or None",
    )


def emit_claims_writes_the_range_anchor(t):
    return _swap(
        t,
        "    if facts.emit_from:\n"
        '        audited = f"{facts.emit_from}..{head_sha}"\n',
        "    if facts.prev_sha:\n"
        '        audited = f"{facts.prev_sha}..{head_sha}"\n',
    )


def the_range_ignores_a_self_range(t):
    return _swap(t, "    if anchor_is_head(facts.prev_sha, hc):", "    if False:")


def the_ledger_ignores_a_self_range(t):
    return _swap(t, "        if anchor_is_head(prev_sha, head_check):",
                 "        if False:")


def emit_claims_drops_the_self_range_warning(t):
    # 🔴 RE-TARGETED in round 4: the predicate became
    # `same_commit(next_anchor, head_sha)` when the guard was moved onto the
    # anchor the NEXT round will use. Disabling it now silences BOTH spellings
    # of the warning, which is why this row gained a second killer.
    return _swap(t, "        if same_commit(next_anchor, head_sha):",
                 "        if False:")


def the_empty_range_names_the_refuted_causes(t):
    return _swap(t, "        if head_check is not None and head_check.ok:\n",
                 "        if False:\n")


def the_unknown_head_reason_names_both_causes(t):
    """The THIRD false-cause site: ignore what the caller knows and list both."""
    return _swap(
        t,
        "        why = no_sha_reason or (\n",
        "        why = None or (\n",
    )


# --------------------------------------------------------------------------- #
# 🔴 X1-X3 — the three verbatim instruction blocks that shipped UNPINNED.
# --------------------------------------------------------------------------- #
# Each of these, applied alone, left the round-2 suite fully green at 58 passed.
# X1 and X3 preserve the fragment an existing test pins and invert the sentence
# around it — the walkable-by-rewording shape from `claude/RULES.md`. Only the
# whole-string DIRECTIVE_LEDGER can see them.

def add_unledgered_directive(t):
    marker = ")\n\nDIRECTIVE = {d.id: d.text for d in SECTION_DIRECTIVES}"
    return _swap(t, marker,
                 '    Directive("unledgered", "**A fourth instruction nobody '
                 'reviewed.**"),\n' + marker)


# --------------------------------------------------------------------------- #
# 🔴 Y1-Y14 — round 4.
# --------------------------------------------------------------------------- #
# Y1 and Y2 are the two OPERATIVE verbatim blocks that were still unpinned after
# round 3's sweep — each inverted alone against all 199 audit-related tests, all
# green. Y3-Y5 are the three that remain unpinned by choice, and they are
# recorded as DOCUMENTED HOLES rather than as controls: a hole that survives is
# not a guard doing its job.

def the_audited_flag_is_ignored(t):
    return _swap(t,
                 "    emit_from = args.audited or emit_anchor(newest)",
                 "    emit_from = emit_anchor(newest)")


def the_ignored_audited_flag_is_silent(t):
    return _swap(t,
                 "    if args.audited and not args.emit_claims:",
                 "    if False:")


def the_round_one_emit_warning_is_unreachable_again(t):
    """The round-3 spelling restored: ask `emit_from`, which is None at round 1."""
    return _swap(
        t,
        "        next_anchor = range_anchor(\n"
        '            ClaimsBlock(args.round_no, facts.emit_from or "", head_sha, [])\n'
        "        )\n",
        "        next_anchor = facts.emit_from\n",
    )


def the_ledger_hand_rolls_its_degenerate_causes(t):
    return _swap(
        t,
        '                "anything. " + directive("degenerate-range-causes")\n',
        '                "anything. The round\'s fix commits are not in this "\n'
        '                "checkout, or the `audited=` field was written with "\n'
        '                "the fix tip in the `<from>` position."\n',
    )


def the_range_hand_rolls_its_degenerate_causes(t):
    return _swap(
        t,
        '            + directive("degenerate-range-causes")\n',
        '            + "Either the round\'s fix commits are not in this "\n'
        '            "checkout, or the `audited=` block was written with the "\n'
        '            "fix tip in its `<from>` position."\n',
    )


def same_commit_is_case_sensitive(t):
    return _swap(t,
                 '    a, b = (a or "").lower(), (b or "").lower()',
                 '    a, b = (a or ""), (b or "")')


def the_git_dirs_are_compared_as_strings(t):
    return _swap(
        t,
        "    git_dir, common_dir = _resolved(lines[0]), _resolved(lines[1])",
        "    git_dir, common_dir = lines[0], lines[1]",
    )


def the_unknown_worktree_state_collapses_to_shared(t):
    return _swap(t,
                 '        }.get(wt.kind, "checkout-unknown")),',
                 '        }.get(wt.kind, "checkout-moves")),')


def a_private_worktree_is_called_shared(t):
    return _swap(t,
                 '            "private": "checkout-private",',
                 '            "private": "checkout-moves",')


def where_to_work_drops_the_private_note(t):
    return _swap(t,
                 '    if facts.worktree.kind == "private":',
                 "    if False:")


# 🔴 Y3-Y5 — the DOCUMENTED HOLES. Each inverts an operative sentence that is
# deliberately NOT pinned, and each must SURVIVE. A row that starts being killed
# is GOOD NEWS and is reported as an action, not as a pass: promote it to a
# killer set and delete its line from the ledger comment in the script.

def the_output_contract_drops_the_per_finding_format(t):
    return _swap(
        t,
        '        "Findings by severity (🔴 deploy-blocking / 🟡 should-fix / 🟢 nit), "\n'
        '        "each in the format required above. ',
        '        "Findings by severity, a one-paragraph summary is enough. ',
    )


def the_toolchain_drops_name_the_tier(t):
    return _swap(
        t,
        '        "Name the tier and the base sha in any claim you make about the gate — "',
        '        "There is no need to name the tier or the base sha. "',
    )


def the_round_one_range_points_at_the_pr_description(t):
    return _swap(
        t,
        '            f"A FIRST, FULL audit: read the whole PR diff (`gh pr diff "\n'
        '            f"{facts.pr}`) and the code it touches, not just the PR "\n'
        '            "description.",\n',
        '            "A FIRST, FULL audit: the PR description is the fastest "\n'
        '            "way in.",\n',
    )


# --------------------------------------------------------------------------- #
# 🔴 Z1-Z8 — round 5.
# --------------------------------------------------------------------------- #
# Round 4 removed the false SHARED claim from the private checkout state and
# replaced it with a WRITE GRANT over a tree that is not the auditor's, and
# reworded the `no-fetch` clause from unconditional to conditional on a state
# `gather_worktree_kind` cannot know (it measures the ASSEMBLER's cwd). Z1-Z3
# restore each half. Z4 puts the toolchain's refuted rationale back, Z5-Z7 the
# two `--emit-claims` holes, Z8 the unscoped cause clause.


def the_private_state_grants_writing_again(t):
    """Round 4's text, verbatim — 'yours alone … Writing here is fine'."""
    return reword_entry(
        "Directive", "checkout-private",
        "🔴 **This is a PRIVATE worktree — yours alone.** Its `.git` is a link "
        "into a shared repository, but the WORKING TREE is not shared, so "
        "files appearing, vanishing or changing under you is **NOT expected "
        "here and IS worth reporting** — that is the sibling-agent clobber "
        "`claude/RULES.md` describes, not background noise. **If something "
        "moves, report it with what moved and when.** Writing here is fine: "
        "the no-write clause below is about a SHARED checkout, and this is "
        "not one.")(t)


def the_shared_state_drops_the_no_write_rule(t):
    """Round 4's `checkout-moves`, which stated no rule of its own."""
    return reword_entry(
        "Directive", "checkout-moves",
        "🔴 **This checkout is SHARED with other sessions and agents. It "
        "MOVES UNDER YOU** — the branch can change, files can appear and "
        "vanish, and commits can land mid-audit. That is expected and is NOT "
        "your fault and NOT a finding. **Report what you observed moving and "
        "carry on; do not chase it, and do not try to restore it.**")(t)


def a_fourth_checkout_state_arrives_with_no_rule(t):
    """A new `checkout-*` state nobody checked carries the no-write rule."""
    marker = ")\n\nDIRECTIVE = {d.id: d.text for d in SECTION_DIRECTIVES}"
    return _swap(t, marker,
                 '    Directive("checkout-detached", "🔴 **This checkout is on '
                 'a DETACHED HEAD.** Nobody wrote a rule for it."),\n' + marker)


def the_no_fetch_clause_is_conditional_again(t):
    """Round 4's spelling: armed only when the section says SHARED."""
    return reword_entry(
        "Clause", "no-fetch",
        "**Do NOT `git fetch`, `pull`, `checkout` or otherwise write to a "
        "SHARED checkout — THE CHECKOUT section of this brief says whether "
        "the one it names is shared.** Other sessions are in a shared tree; a "
        "fetch there is a write with cross-session blast radius, and every "
        "ref you need is already resolved for you here.")(t)


def the_toolchain_reason_names_the_shared_checkout_again(t):
    return _swap(
        t,
        '        "so running that copy runs the suite in a checkout that is NOT yours — "\n'
        '        "the one this brief was assembled in, on whatever branch it is standing "\n'
        '        "on, holding none of your mutations — and a `nix build <ref>#…` builds "\n',
        '        "so running the shared copy runs the suite in the SHARED CHECKOUT on "\n'
        '        "whatever branch it is standing on, and a `nix build <ref>#…` builds "\n',
    )


def the_emitted_block_loses_its_legend(t):
    return _swap(
        t,
        '        "  legend: `<from>` = the tip THIS round\'s audit READ · `<to>` = the "\n'
        '        "head THIS round\'s FIXES produced. Different shas — `<from>` is older.",\n'
        '        "",\n',
        "",
    )


def the_emitted_block_is_not_round_tripped(t):
    return _swap(t,
                 "        if facts.emit_from:\n"
                 "            why = emitted_block_reads_back_as_written(",
                 "        if False:\n"
                 "            why = emitted_block_reads_back_as_written(")


def an_empty_audited_is_ignored_again(t):
    return _swap(t,
                 "    if args.audited is not None and not args.audited.strip():",
                 "    if False:")


def the_degenerate_causes_stop_scoping_the_omission(t):
    """Round 4's clause: the omission is blamed on every round."""
    return _swap(
        t,
        '        "`<from>` position — which is what a bare round-1 `audited=<sha>` "\n'
        '        "always means, and what `--emit-claims` records when `--audited` is "\n'
        '        "omitted AT ROUND 1; from round 2 on, omitting it is correct, because "\n'
        '        "`<from>` is then recovered from the previous block\'s `<to>`. "\n'
        '        "`<from>` is the tip the PREVIOUS round AUDITED, so "\n'
        '        "re-emit that block with `--audited <that round\'s actual tip sha>` "\n'
        '        "and re-assemble — or nothing has been committed and pushed to "\n',
        '        "`<from>` position — which is what `--emit-claims` records when "\n'
        '        "`--audited` is omitted, and what a bare round-1 `audited=<sha>` "\n'
        '        "always means; `<from>` is the tip the PREVIOUS round AUDITED, so "\n'
        '        "re-emit that block with `--audited <the tip that round actually "\n'
        '        "read>` and re-assemble — or nothing has been committed and pushed to "\n',
    )


# 🔴 The SURVIVES control for the directive ledger. Whitespace changes; the
# instruction does not. A RED here means the new pin is keyed to layout.
def rewrap_a_directive(t):
    return _swap(
        t,
        '"Also hunt for **regressions this fix round itself introduced** — the "',
        '"Also  hunt  for **regressions this fix round itself introduced**  — the "',
    )


LEDGER_TRIO = {
    "test_the_invariant_clause_ledger_is_pinned_two_way",
    "test_each_clause_carries_the_instruction_its_ledger_entry_names",
    "test_the_rendered_section_holds_exactly_the_ledgered_clauses",
}
DELETION_CONTROL = "test_control_a_clause_deleted_from_the_constant_is_detected"

# (label, expected killer set, mutation)
ROWS = [
    ("D1  delete clause read-only",
     LEDGER_TRIO | {DELETION_CONTROL}, drop_clause("read-only")),
    # 🔴 D2 is the one asymmetric row, and it is recorded rather than tidied
    # away: `test_missing_clause_check_warns_and_never_blocks` looks the
    # `no-fetch` clause up BY ID, so deleting that particular clause makes it
    # error. A real coupling, and the reason the deleted-clause CONTROL is a
    # separate test from the warn test.
    ("D2  delete clause no-fetch",
     LEDGER_TRIO | {"test_missing_clause_check_warns_and_never_blocks"},
     drop_clause("no-fetch")),
    ("D3  delete clause stop-rule",
     LEDGER_TRIO | {DELETION_CONTROL}, drop_clause("stop-rule")),
    ("D4  delete clause nit-is-not-a-finding",
     LEDGER_TRIO | {DELETION_CONTROL}, drop_clause("nit-is-not-a-finding")),
    ("D5  delete clause reverify-self-reported",
     LEDGER_TRIO | {DELETION_CONTROL}, drop_clause("reverify-self-reported")),
    ("D6  delete clause finding-format",
     LEDGER_TRIO | {DELETION_CONTROL}, drop_clause("finding-format")),
    # 🔴 D7 is the SECOND asymmetric row, and the same shape as D2:
    # `test_the_out_file_is_read_back_and_checked` looks the `do-not-merge`
    # clause up BY ID to model a lossy write, so deleting that particular clause
    # makes it error. Recorded rather than tidied away — it is a real coupling,
    # and by-id lookup is still the right thing there (an INDEX would make the
    # test's outcome depend on which OTHER clause a mutation deleted, which
    # shows up in a sweep as dying for the wrong reason).
    ("D7  delete clause do-not-merge",
     LEDGER_TRIO | {DELETION_CONTROL,
                    "test_the_out_file_is_read_back_and_checked"},
     drop_clause("do-not-merge")),
    ("A1  add an unledgered eighth clause",
     {"test_the_invariant_clause_ledger_is_pinned_two_way",
      "test_the_rendered_section_holds_exactly_the_ledgered_clauses",
      DELETION_CONTROL},
     add_unledgered_clause),
    # 🔴 THE REACHABILITY CONTROL. The clause survives and is still emitted, so
    # every presence pin stays GREEN; only the FRAGMENT ledger can see it. A
    # WRONG-KILLER here means that ledger is dead code.
    ("R1  reword stop-rule (clause still present)",
     {"test_each_clause_carries_the_instruction_its_ledger_entry_names"},
     reword_stop_rule),
    # The `--check` round trip fires here too, and correctly: with the bullets
    # hardcoded, a REAL generated brief written to a REAL file genuinely holds
    # none of the clauses, which is precisely what that command reports. It is
    # the only row where the two reach the same conclusion by different routes.
    ("F1  render bullets from a hardcoded copy",
     {"test_the_rendered_section_holds_exactly_the_ledgered_clauses",
      "test_every_clause_is_emitted_verbatim_in_both_kinds_of_brief",
      "test_the_clause_check_runs_over_a_file_that_can_actually_be_lossy",
      DELETION_CONTROL},
     hardcode_bullets),
    ("C1  the delta refusal removed",
     {"test_the_delta_refusal_exits_non_zero_and_emits_no_brief",
      "test_the_refusal_names_what_it_looked_for_and_where",
      "test_the_refusal_fires_for_a_malformed_block_and_says_which_way",
      # Asserts on the refusal MESSAGE, so no refusal means no message.
      "test_the_refusal_says_which_comment_kinds_it_cannot_see"},
     remove_the_refusal),
    ("C2  claims read from the whole comment",
     {"test_the_brief_carries_the_claims_and_not_the_reasoning_around_them",
      "test_the_newest_claims_block_wins",
      "test_emit_claims_prints_a_block_this_scripts_own_parser_accepts"},
     claims_from_the_whole_comment),
    ("C3  cross-repo decision inverted",
     {"test_cross_repo_tells_the_agent_to_worktree_the_prs_repo_itself",
      "test_same_repo_recommends_the_isolation_flag_and_does_not_hand_roll",
      "test_the_cross_repo_decision_comes_from_the_repos_not_from_prose",
      # A fork PR against THIS repo is a same-repo PR, so inverting the two
      # known states flips it to cross as well. Listed because it is the row
      # that PROVES the fork case rides the same decision as the others — the
      # fixture used to encode a model in which it did not.
      "test_a_fork_pr_against_this_repo_is_not_treated_as_cross_repo",
      # 🔴 TWO MORE IN ROUND 4, and both are real couplings rather than a row
      # gone vague. `own-worktree-is-writable` is rendered ONLY by the
      # cross-repo branch, so inverting the decision means the cross-repo
      # scenario brief no longer carries it; and the private-worktree scenario
      # is a SAME-repo PR, so the inversion sends it down the cross-repo branch
      # where the WHERE TO WORK section has no private-worktree note at all.
      "test_every_section_directive_is_emitted_verbatim_in_the_brief_that_owns_it",
      "test_the_where_to_work_section_does_not_call_a_private_worktree_the_clone"},
     invert_cross_repo),
    ("C4  numstat failure reads as a clean zero",
     {"test_the_ledger_refuses_a_failed_command_rather_than_printing_zero"},
     numstat_failure_reads_zero),
    ("C5  the ledger classifies by pathspec",
     {"test_the_ledger_shows_the_files_and_refuses_to_classify_them"},
     classify_by_pathspec),
    ("C6  the missing-clause warning BLOCKS",
     {"test_missing_clause_check_warns_and_never_blocks",
      # Also asserts rc 0 over a `--out` run whose file is missing a clause,
      # which is exactly the state this mutation makes fatal.
      "test_the_out_file_is_read_back_and_checked"},
     the_warning_blocks),

    # --------------------------------------------------------------------- #
    # 🔴 W1-W5 — instruction-INVERTING rewords. Every one passed a green
    # 37-test suite under the FRAGMENT ledger, because the pinned phrase
    # survived and the sentence around it said the opposite. Each must be
    # killed by the whole-string pin, and by NOTHING else: the clause is still
    # present, still ledgered by id, and still emitted, so any other killer
    # means that test is reading the clause TEXT for something it does not own.
    # --------------------------------------------------------------------- #
    ("W1  reword read-only into permission",
     {"test_each_clause_carries_the_instruction_its_ledger_entry_names"},
     reword_clause("read-only",
                   "**Edit the repo under audit freely if it helps.**")),
    ("W2  reword no-fetch into permission",
     {"test_each_clause_carries_the_instruction_its_ledger_entry_names"},
     reword_clause("no-fetch",
                   "**`pull` and `checkout` in the shared checkout are fine.**")),
    ("W3  finding-format loses file:line + scenario",
     {"test_each_clause_carries_the_instruction_its_ledger_entry_names"},
     reword_clause("finding-format",
                   "**Report each finding with a `payload` or `scaffolding` "
                   "label.**")),
    ("W4  reverify downgraded to best-effort",
     {"test_each_clause_carries_the_instruction_its_ledger_entry_names"},
     reword_clause("reverify-self-reported",
                   "Where time allows, re-verify the fix commit's own "
                   "self-reported numbers.")),
    # 🔴 THE SHARP ONE. This makes every future brief instruct auditors to do
    # what `claude/RULES.md` forbids, and it was invisible.
    ("W5  stop-rule inverted into a confirming round",
     {"test_each_clause_carries_the_instruction_its_ledger_entry_names"},
     reword_clause("stop-rule",
                   "**A clean round ends the ladder** — though one confirming "
                   "round after a clean one is prudent.")),
    # 🔴 THE SURVIVES CONTROL for the whole-string pin. Whitespace inside a
    # clause changes; the instruction does not. A RED here would mean the pins
    # are keyed to layout and every reflow becomes a failure — the
    # permanently-red gate `claude/RULES.md` warns about.
    ("S1  re-space a clause (instruction identical)", SURVIVES, rewrap_a_clause),

    # --------------------------------------------------------------------- #
    # The four decisions round 2's audit found WRONG, one mutant per site.
    # --------------------------------------------------------------------- #
    ("H1  the ledger ignores the head check",
     {"test_the_ledger_refuses_to_measure_a_checkout_that_is_not_the_pr"},
     ledger_ignores_the_head_check),
    # 🔴 H2 GAINED A SECOND KILLER in round 3, and it is a real coupling rather
    # than a row gone vague: the COULD-NOT-VERIFY branch is the ONLY place a
    # `head_check.reason` is rendered, so a mutation that never takes that
    # branch necessarily hides the reason text that
    # `..._unknown_head_sha_reason_names_one_cause_not_two` reads. Recorded in
    # the same spirit as D2 and D7 — the alternative would be to weaken that
    # test into asserting nothing about the brief.
    ("H2  the range hands out ..HEAD regardless",
     {"test_the_range_does_not_hand_out_a_head_that_is_not_the_prs_head",
      "test_the_unknown_head_sha_reason_names_one_cause_not_two"},
     range_ignores_the_head_check),
    ("H3  --emit-claims stamps the LOCAL head",
     {"test_emit_claims_stamps_the_prs_head_not_the_local_checkouts",
      "test_emit_claims_prints_a_block_this_scripts_own_parser_accepts"},
     emit_claims_uses_the_local_head),
    # 🔴 H4 BRIEFLY GAINED A KILLER IN ROUND 4 AND GAVE IT BACK, recorded
    # because the give-back is the evidence. The round-1 assumption test first
    # asserted `audited_to == <the PR head>`; that made mutant H3 (read the
    # LOCAL head) kill it for someone else's reason, so the assertion was
    # weakened to "the block is the BARE spelling". H4 then stopped killing it
    # — correctly: the placeholder header parses to `audited_from=""`,
    # `audited_to="<the"`, which IS bare-shaped, and the warning still fires
    # because it reads `facts.emit_from`, not the emitted text. The row that
    # owns the round-1 header's parseability is the one below.
    ("H4  round-1 placeholder back in the header",
     {"test_emit_claims_prints_a_block_this_scripts_own_parser_accepts"},
     emit_claims_interpolates_the_placeholder),
    ("T1  gate.sh points at the shared checkout",
     {"test_the_toolchain_gates_the_auditors_copy_not_the_shared_checkout"},
     gate_points_at_the_shared_checkout),
    ("T2  nix build points at the shared checkout",
     {"test_the_toolchain_gates_the_auditors_copy_not_the_shared_checkout",
      "test_the_toolchain_section_names_the_tier_the_merge_gates_on"},
     nix_build_points_at_the_shared_checkout),
    ("P1  pr_slug reads the HEAD repo again",
     {"test_a_fork_pr_against_this_repo_is_not_treated_as_cross_repo",
      "test_the_prs_repo_is_read_from_the_url_not_from_the_head_repo"},
     pr_slug_reads_the_head_repo),
    ("P2  'cannot determine' collapses to same-repo",
     {"test_an_undeterminable_repo_gets_its_own_branch_not_the_same_repo_one"},
     unknown_repo_collapses_to_same),
    ("K1  --check always reports clean",
     {"test_the_clause_check_runs_over_a_file_that_can_actually_be_lossy"},
     the_check_command_reports_clean),
    ("K2  --out is not read back",
     {"test_the_out_file_is_read_back_and_checked"},
     the_out_file_is_not_read_back),
    ("K3  missing_clauses stops normalising whitespace",
     {"test_missing_clauses_is_a_pure_function_over_the_text"},
     missing_clauses_is_not_normalised),
    ("L1  the cumulative failure reason is dropped",
     {"test_a_failed_cumulative_measurement_does_not_print_a_false_cause"},
     the_cumulative_reason_is_dropped),
    ("B1  an unclosed fence is skipped silently",
     {"test_a_fence_the_parser_cannot_read_is_reported_not_skipped",
      "test_a_malformed_fence_beside_a_readable_block_still_warns"},
     an_unclosed_fence_is_skipped_silently),
    ("B2  a longer closing fence stops closing",
     {"test_a_longer_closing_fence_is_a_valid_close_and_is_read"},
     a_longer_closing_fence_stops_closing),
    ("B3  continuation lines are dropped",
     {"test_a_claim_that_wraps_onto_a_continuation_line_keeps_its_tail"},
     continuation_lines_are_dropped),
    ("B4  the nested-fence report is removed",
     {"test_a_block_cut_short_by_a_nested_fence_is_reported"},
     the_nested_fence_report_is_removed),
    ("B5  the refusal drops the comment-kinds note",
     {"test_the_refusal_says_which_comment_kinds_it_cannot_see"},
     the_refusal_drops_the_comment_kinds_note),

    # --------------------------------------------------------------------- #
    # 🔴 N1-N7 — round 3. The two anchors, the self-range guards, and the
    # empty-range reason that named causes `head_check` refutes.
    # --------------------------------------------------------------------- #
    # 🔴 N1 IS THE SHIPPED DEFECT RESTORED, and its killer set is deliberately
    # WIDE: reading `<to>` as the delta anchor moves the range for every
    # consumer at once, and a narrow expectation here would be the wrong
    # claim. It is listed in full rather than trimmed to the "main" one.
    ("N1  the range anchors on `<to>` again",
     {"test_the_range_is_generated_from_the_previous_rounds_audited_sha",
      "test_the_ledger_measures_from_the_tip_the_previous_round_audited",
      "test_the_newest_claims_block_wins",
      "test_the_range_says_head_was_verified_when_it_was",
      "test_a_degenerate_self_range_is_reported_not_rendered_as_a_clean_diff",
      "test_a_degenerate_self_range_is_named_by_the_ledger_too",
      "test_a_bare_round_one_audited_sha_still_anchors_the_next_round",
      # Round 4's four. All four read a range that N1 moves: two assert the
      # degenerate banner (which no longer fires when the anchor changes) and
      # two assert the anchor a `--audited` block produces.
      "test_a_degenerate_range_does_not_blame_a_checkout_it_verified",
      "test_an_uppercase_audited_sha_still_trips_the_degenerate_guard",
      "test_audited_supplies_the_tip_a_round_one_emit_cannot_derive",
      "test_audited_without_emit_claims_says_it_changed_nothing"},
     range_anchor_reads_the_audited_tip),
    # The OTHER wrong fix: `audited_from` alone. A round-1 bare `audited=<sha>`
    # then anchors nothing, and the remedy chain the delta refusal advertises
    # dead-ends.
    # 🔴 N2 GAINED A KILLER IN ROUND 4, and it is the one that shows the two
    # fixes are the SAME mechanism: the round-1 assumption warning asks
    # `range_anchor` what the next round will anchor on, so dropping the bare
    # fallback makes that anchor None and the warning silent again.
    ("N2  the bare round-1 fallback is dropped",
     {"test_a_bare_round_one_audited_sha_still_anchors_the_next_round",
      "test_the_cumulative_figure_is_not_measured_without_a_round_one_anchor",
      "test_a_round_one_emit_claims_says_head_is_an_assumption_not_a_measurement"},
     range_anchor_loses_the_bare_fallback),
    # 🔴 THE MIRROR IMAGE, and the reason the two readers are separate
    # functions: "one anchor everywhere" is wrong on the WRITER's side, and it
    # produces a superset rather than an empty range, so nothing else notices.
    # `--audited` routes through `emit_from`, so swapping the writer's anchor
    # to `prev_sha` breaks the flag as well — round 4's addition.
    # 🔴 KILLER SET GREW IN ROUND 5, for the same STRUCTURAL reason as Y7 and
    # recorded rather than engineered away: this mutation makes the round-1
    # branch fall through to the bare spelling, so a `--audited` value never
    # reaches the header at all — and a guard that reads the PRINTED header
    # correctly stays silent, because there is nothing wrong with what was
    # printed. No end-to-end test of "a bad `--audited` is refused" can survive
    # a mutant that stops the value being emitted. The alternative — asserting
    # the printed `<from>` EQUALS `emit_from` — was measured and rejected: it
    # made this row kill five tests and would answer a wrong-field bug in
    # production with a misleading "it does not parse".
    ("N3  --emit-claims writes the RANGE anchor as `<from>`",
     {"test_emit_claims_records_the_tip_this_round_audited_not_the_range_anchor",
      "test_emit_claims_prints_a_block_this_scripts_own_parser_accepts",
      "test_audited_supplies_the_tip_a_round_one_emit_cannot_derive",
      "test_emit_claims_refuses_an_audited_value_its_own_parser_cannot_read"},
     emit_claims_writes_the_range_anchor),
    ("N4  THE RANGE renders a self-range with no banner",
     {"test_a_degenerate_self_range_is_reported_not_rendered_as_a_clean_diff",
      # Round 4: both of these read THE RANGE's degenerate banner — one for
      # its cause list, one for an uppercase anchor.
      "test_a_degenerate_range_does_not_blame_a_checkout_it_verified",
      "test_an_uppercase_audited_sha_still_trips_the_degenerate_guard"},
     the_range_ignores_a_self_range),
    ("N5  the ledger calls a self-range merely empty",
     {"test_a_degenerate_self_range_is_named_by_the_ledger_too",
      "test_a_degenerate_range_does_not_blame_a_checkout_it_verified"},
     the_ledger_ignores_a_self_range),
    # 🔴 N6 now silences BOTH spellings of the warning — see the re-targeting
    # note on its mutation. Recorded rather than narrowed: one predicate
    # guards both, and pretending otherwise would mean asserting less.
    ("N6  --emit-claims writes `X..X` in silence",
     {"test_emit_claims_warns_when_the_block_it_writes_is_a_self_range",
      "test_a_round_one_emit_claims_says_head_is_an_assumption_not_a_measurement"},
     emit_claims_drops_the_self_range_warning),
    ("N7  the empty-range reason names the refuted causes",
     {"test_an_empty_range_does_not_name_a_cause_the_head_check_refutes"},
     the_empty_range_names_the_refuted_causes),
    ("N8  the unknown-head reason ignores what the caller knows",
     {"test_the_unknown_head_sha_reason_names_one_cause_not_two"},
     the_unknown_head_reason_names_both_causes),

    # --------------------------------------------------------------------- #
    # 🔴 X1-X3 — the three unpinned verbatim blocks. Each passed a fully green
    # 58-test suite before `SECTION_DIRECTIVES` existed.
    # --------------------------------------------------------------------- #
    # 🔴 X1 KEEPS the fragment `test_the_brief_carries_the_claims_...` pins
    # ("never WHY IT IS CORRECT") and inverts the sentence around it into the
    # framed audit the module's headline rule forbids. Any other killer would
    # mean some test is reading this text for something it does not own.
    ("X1  claims-framing inverted into 'take them as established'",
     {"test_each_section_directive_carries_the_instruction_its_ledger_entry_names"},
     reword_entry(
         "Directive", "claims-framing",
         "🔴 This is WHAT WAS CLAIMED, never WHY IT IS CORRECT. The fix round "
         "already verified each of these, so take them as established unless "
         "something obvious contradicts one.")),
    ("X2  the delta-regressions instruction deleted outright",
     {"test_the_section_directive_ledger_is_pinned_two_way",
      "test_each_section_directive_carries_the_instruction_its_ledger_entry_names",
      # Round 4: the scenario map and the render check both name it too.
      "test_the_directive_render_scenario_ledger_is_pinned_two_way",
      "test_every_section_directive_is_emitted_verbatim_in_the_brief_that_owns_it"},
     drop_entry("Directive", "delta-regressions")),
    # 🔴 X3 KEEPS "MOVES UNDER YOU" and "NOT a finding" — both pinned by
    # `test_the_shared_checkout_state_is_reported_with_the_it_moves_warning` —
    # and inverts the operative sentence into an instruction to WRITE to the
    # shared checkout, contradicting the `no-fetch` clause two bars later.
    # 🔴 KILLER SET GREW IN ROUND 5, and the growth is the news: this
    # replacement text also drops the no-write sentence round 5 added to every
    # checkout state, so the per-state rule ledger fires as well. Leaving the
    # old one-element set would have reported EXTRA-KILLER forever; narrowing
    # the mutation to avoid it would remove the second hazard from the row.
    ("X3  checkout-moves inverted into 'restore anything you see move'",
     {"test_each_section_directive_carries_the_instruction_its_ledger_entry_names",
      "test_every_checkout_state_carries_the_no_write_rule"},
     reword_entry(
         "Directive", "checkout-moves",
         "🔴 **This checkout is SHARED with other sessions and agents. It "
         "MOVES UNDER YOU** — the branch can change, files can appear and "
         "vanish, and commits can land mid-audit. That is NOT a finding in "
         "itself, but it is worth reporting. Restore anything you see move.")),
    # 🔴 RE-TARGETED KILLER SET in round 4, and the swap is the interesting
    # part. The "it reaches no brief" hazard used to be caught by the emitted
    # test, which iterated the SCRIPT's directives; that test is now driven by
    # the scenario map, so an unledgered directive is caught one step earlier —
    # by the scenario pin, which reads `SECTION_DIRECTIVES` and finds an id
    # nobody said where to render. The hazard is still covered; the guard that
    # covers it moved, and pretending otherwise would leave a DEAD pin here.
    ("XA  add an unledgered fourth directive",
     {"test_the_section_directive_ledger_is_pinned_two_way",
      "test_the_directive_render_scenario_ledger_is_pinned_two_way"},
     add_unledgered_directive),
    # 🔴 The SURVIVES control for the directive ledger — the sibling of S1. A
    # RED here means the new pin is keyed to layout, and every reflow of the
    # source becomes a test failure.
    ("XS  re-space a directive (instruction identical)",
     SURVIVES, rewrap_a_directive),

    # --------------------------------------------------------------------- #
    # 🔴 Y1-Y14 — round 4.
    # --------------------------------------------------------------------- #
    # 🔴 Y1 INVERTS THE SENTENCE THAT DEFINES THE DELTA SCOPE. It lived inside
    # a generated f-string, where no whole-string pin could reach it, and the
    # inversion "Re-audit the whole PR as well" passed every audit-related
    # test. Killed by the whole-string ledger ALONE — the directive is still
    # present, still ledgered by id and still emitted, so any other killer
    # would mean some test is reading this text for something it does not own.
    ("Y1  delta-scope inverted into 're-audit the whole PR'",
     {"test_each_section_directive_carries_the_instruction_its_ledger_entry_names"},
     reword_entry(
         "Directive", "delta-scope",
         "**Audit that range, and re-audit the whole PR as well.** A delta "
         "round is cheap and re-reading everything costs little.")),
    # 🔴 Y2 leaves a CROSS-REPO auditor with nowhere to work: the brief has
    # just told them to build that worktree themselves precisely so they can
    # fetch in it.
    ("Y2  own-worktree-is-writable inverted into 'do not fetch'",
     {"test_each_section_directive_carries_the_instruction_its_ledger_entry_names"},
     reword_entry(
         "Directive", "own-worktree-is-writable",
         "Do not fetch or check out inside it either. The no-write rule "
         "covers every checkout you touch.")),
    # 🔴 Y3-Y5 — DOCUMENTED HOLES, not controls. See `HOLE` above.
    ("Y3  OUTPUT drops the per-finding format (NO GUARD)",
     HOLE, the_output_contract_drops_the_per_finding_format),
    ("Y4  toolchain drops 'name the tier and base sha' (NO GUARD)",
     HOLE, the_toolchain_drops_name_the_tier),
    ("Y5  round-1 range points at the PR description (NO GUARD)",
     HOLE, the_round_one_range_points_at_the_pr_description),
    # 🔴 Y5b is the one measurement that makes the three-state worktree read
    # correct: git answers `/abs/.git` and `../.git` in a repo SUBDIRECTORY —
    # different strings, the same directory — so a string compare calls every
    # subdirectory of an ordinary clone a PRIVATE worktree.
    ("Y5b the two git dirs compared as strings",
     {"test_gather_worktree_kind_resolves_paths_before_comparing_them"},
     the_git_dirs_are_compared_as_strings),
    ("Y6  the UNKNOWN worktree state collapses to SHARED",
     {"test_an_unreadable_worktree_state_is_its_own_answer_and_keeps_the_no_write",
      # The `checkout-unknown` directive then reaches no brief at all.
      "test_every_section_directive_is_emitted_verbatim_in_the_brief_that_owns_it"},
     the_unknown_worktree_state_collapses_to_shared),
    # 🔴 KILLER SET GREW IN ROUND 5, and the growth is a real second
    # consequence rather than a row losing its focus: with `--audited` ignored,
    # `emit_from` is None at round 1, the round-trip refusal is never reached,
    # and a value this script's own parser cannot read is emitted in silence.
    # No test of "a bad `--audited` is refused" can survive the flag being
    # dropped on the floor, so the coupling is in the code, not in the pins.
    ("Y7  --audited is parsed and ignored",
     {"test_audited_supplies_the_tip_a_round_one_emit_cannot_derive",
      "test_emit_claims_refuses_an_audited_value_its_own_parser_cannot_read"},
     the_audited_flag_is_ignored),
    ("Y8  --audited without --emit-claims is silent again",
     {"test_audited_without_emit_claims_says_it_changed_nothing"},
     the_ignored_audited_flag_is_silent),
    # 🔴 Y9 RESTORES THE UNREACHABLE SPELLING: asked of `emit_from`, which is
    # None for the round-1 case the warning exists to cover.
    ("Y9  the round-1 emit warning goes back to `emit_from`",
     {"test_a_round_one_emit_claims_says_head_is_an_assumption_not_a_measurement"},
     the_round_one_emit_warning_is_unreachable_again),
    ("Y10 the LEDGER hand-rolls the refuted cause again",
     {"test_a_degenerate_range_does_not_blame_a_checkout_it_verified",
      "test_the_degenerate_range_causes_have_exactly_one_writer_per_consumer"},
     the_ledger_hand_rolls_its_degenerate_causes),
    ("Y11 THE RANGE hand-rolls the refuted cause again",
     {"test_a_degenerate_range_does_not_blame_a_checkout_it_verified",
      "test_the_degenerate_range_causes_have_exactly_one_writer_per_consumer"},
     the_range_hand_rolls_its_degenerate_causes),
    ("Y12 same_commit goes back to case-sensitive",
     {"test_an_uppercase_audited_sha_still_trips_the_degenerate_guard"},
     same_commit_is_case_sensitive),
    ("Y13 WHERE TO WORK drops the private-worktree note",
     {"test_the_where_to_work_section_does_not_call_a_private_worktree_the_clone"},
     where_to_work_drops_the_private_note),
    ("Y14 a PRIVATE worktree is described as SHARED again",
     {"test_a_private_worktree_is_not_described_as_shared_and_is_not_absolved",
      "test_every_section_directive_is_emitted_verbatim_in_the_brief_that_owns_it"},
     a_private_worktree_is_called_shared),

    # --------------------------------------------------------------------- #
    # 🔴 Z1-Z8 — round 5.
    # --------------------------------------------------------------------- #
    # 🔴 Z1 IS THE INVERSION OF THE CORRECTED WRITE RULE, restored verbatim from
    # round 4. Three pins fire, each a different claim: the whole-string ledger
    # (the text moved), the permission probe (a grant appeared) and the
    # per-state no-write ledger (the rule vanished).
    #
    # 🔴 A FOURTH WAS PREDICTED AND DID NOT FIRE, recorded because the
    # prediction was wrong and the harness is what said so:
    # `..._private_worktree_is_not_described_as_shared_and_is_not_absolved`
    # stays GREEN under Z1, correctly. Round 4's text carries neither of its two
    # negatives, and both of its positives survive — "PRIVATE linked worktree"
    # comes from the `kind :` LINE, which this mutation does not touch, and
    # "report it with what moved and when" is in round 4's text verbatim. That
    # test covers the SHARED/PRIVATE description; the write GRANT is a claim it
    # never made, which is exactly why round 5 needed a new probe.
    ("Z1  the private state grants writing again",
     {"test_each_section_directive_carries_the_instruction_its_ledger_entry_names",
      "test_no_checkout_state_grants_write_permission_over_the_assembly_checkout",
      "test_every_checkout_state_carries_the_no_write_rule"},
     the_private_state_grants_writing_again),
    ("Z2  the SHARED state drops its own no-write rule",
     {"test_each_section_directive_carries_the_instruction_its_ledger_entry_names",
      "test_every_checkout_state_carries_the_no_write_rule"},
     the_shared_state_drops_the_no_write_rule),
    ("Z2b a fourth checkout state arrives with no rule",
     {"test_the_section_directive_ledger_is_pinned_two_way",
      "test_the_directive_render_scenario_ledger_is_pinned_two_way",
      "test_every_checkout_state_carries_the_no_write_rule"},
     a_fourth_checkout_state_arrives_with_no_rule),
    # 🔴 Z3 is the other half of round 4's regression: the clause that forbids
    # the write was made conditional on a state this script cannot know. Killed
    # by the whole-string CLAUSE ledger alone — the clause is still present,
    # still ledgered by id and still emitted, which is exactly how the reword
    # shipped.
    ("Z3  no-fetch goes back to conditional-on-SHARED",
     {"test_each_clause_carries_the_instruction_its_ledger_entry_names"},
     the_no_fetch_clause_is_conditional_again),
    ("Z4  TOOLCHAIN's reason names the SHARED CHECKOUT again",
     {"test_the_toolchain_reason_is_true_in_both_checkout_states"},
     the_toolchain_reason_names_the_shared_checkout_again),
    ("Z5  the emitted block loses its legend",
     {"test_the_emitted_skeleton_carries_a_legend_for_its_two_fields"},
     the_emitted_block_loses_its_legend),
    ("Z6  the emitted block is not round-tripped",
     {"test_emit_claims_refuses_an_audited_value_its_own_parser_cannot_read"},
     the_emitted_block_is_not_round_tripped),
    ("Z7  an EMPTY --audited is ignored again",
     {"test_emit_claims_refuses_an_audited_value_its_own_parser_cannot_read"},
     an_empty_audited_is_ignored_again),
    ("Z8  the degenerate causes stop scoping the omission",
     {"test_each_section_directive_carries_the_instruction_its_ledger_entry_names",
      "test_the_degenerate_cause_list_scopes_the_omitted_flag_to_round_one",
      "test_a_degenerate_range_does_not_blame_a_checkout_it_verified"},
     the_degenerate_causes_stop_scoping_the_omission),
]


def failing(root: Path):
    """-> (killer test names, raw output). Reads the CONTENT, never an exit code."""
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    p = subprocess.run(
        [sys.executable, "-m", "pytest", str(root / TEST_REL),
         "-q", "--no-header", "--tb=no", "-p", "no:cacheprovider"],
        capture_output=True, text=True, env=env, cwd=str(root), check=False,
    )
    # stderr is CAPTURED, not discarded: the commonest way to get "0 tests ran"
    # on this host is running outside `nix develop` (`.envrc` is `use opencode`,
    # so a loaded direnv has no pytest), and discarding it turns that one-line
    # diagnosis into a headline that blames the TREE.
    out = p.stdout + p.stderr
    if "No module named pytest" in out:
        return None, ("pytest is not on PATH — run this as `nix develop "
                      "~/workspace/devrc -c python3 scripts/tests/"
                      "mutants-audit-dispatch.py`, not in a bare shell")
    passed = sum(int(n) for n in re.findall(r"(\d+) passed", out))
    failed = sum(int(n) for n in re.findall(r"(\d+) failed", out))
    if passed + failed < MIN_TESTS:
        return None, (f"only {passed + failed} test(s) ran (floor {MIN_TESTS}) — "
                      f"the harness, not the tree:\n{out[-800:]}")
    return set(re.findall(r"^FAILED [^:]*::([A-Za-z0-9_]+)", out, re.M)), out


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="audit-dispatch-mut-"))
    try:
        root = tmp / "tree"
        (root / "scripts" / "tests").mkdir(parents=True)
        shutil.copy(REPO / SCRIPT_REL, root / SCRIPT_REL)
        shutil.copy(REPO / TEST_REL, root / TEST_REL)
        shutil.copy(REPO / HARNESS_REL, root / HARNESS_REL)
        if (root / ".git").exists():
            print("🔴 the copy carries a .git — refusing to run")
            return 2
        original = (root / SCRIPT_REL).read_text(encoding="utf-8")

        killers, raw = failing(root)
        if killers is None:
            print(f"🔴 THE HARNESS could not run: {raw}")
            print("   Nothing was measured. This says nothing about the tree.")
            return 2
        if killers:
            print("🔴 the UNMUTATED tree is already failing: "
                  + ", ".join(sorted(killers)))
            print("   every row below would be meaningless. Fix that first.")
            return 2
        n = sum(int(x) for x in re.findall(r"(\d+) passed", raw))
        print(f"POS  unmutated copy .......................... {n} passed")
        print()

        bad = 0
        for label, want, mutate in ROWS:
            try:
                mutated = mutate(original)
            except AssertionError as e:
                print(f"  🔴 {label:44s} MUTATION DID NOT APPLY — {e}")
                bad += 1
                continue
            if mutated == original:
                print(f"  🔴 {label:44s} MUTATION DID NOT APPLY (no change)")
                bad += 1
                continue
            (root / SCRIPT_REL).write_text(mutated, encoding="utf-8")
            got, raw2 = failing(root)
            (root / SCRIPT_REL).write_text(original, encoding="utf-8")

            if got is None:
                print(f"  🔴 {label:44s} HARNESS BROKE — {raw2}")
                bad += 1
                continue
            # 🔴 A row that must SURVIVE. Reported through the same code path as
            # everything else, so a control cannot quietly stop being checked.
            # 🔴 A DOCUMENTED HOLE. Same code path, a different verdict line,
            # because "SURVIVED as required (control)" printed over an
            # unguarded sentence is the flattering wrong answer.
            if want is HOLE:
                if got:
                    print(f"  ⚠ {label:44s} NOW KILLED by {sorted(got)} — a "
                          "guard covers this now. Promote the row to that "
                          "killer set and delete its line from the ledger "
                          "comment in scripts/audit-dispatch.py.")
                    bad += 1
                else:
                    print(f"  ok {label:44s} SURVIVED — DOCUMENTED HOLE "
                          "(nothing guards it; the script's ledger says so)")
                continue
            if want is SURVIVES:
                if got:
                    print(f"  🔴 {label:44s} KILLED by {sorted(got)} — this row "
                          "must SURVIVE; the pins are keyed to layout, not to "
                          "the instruction")
                    bad += 1
                else:
                    print(f"  ok {label:44s} SURVIVED as required (control)")
                continue
            if not got:
                print(f"  🔴 {label:44s} SURVIVED — no test failed")
                bad += 1
                continue
            missing = want - got
            extra = got - want
            if missing:
                print(f"  🔴 {label:44s} WRONG-KILLER: {sorted(missing)} did NOT "
                      f"fire (got {sorted(got)})")
                bad += 1
                continue
            if extra:
                print(f"  🔴 {label:44s} EXTRA-KILLER: {sorted(extra)} also fired "
                      "— the row no longer isolates what it names")
                bad += 1
                continue
            print(f"  ok {label:44s} killed by exactly {len(got)}: "
                  + ", ".join(sorted(got)))

        print()
        if bad:
            print(f"🔴 {bad} of {len(ROWS)} row(s) not as expected")
            return 1
        print(f"✅ {len(ROWS)} row(s), all as expected")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
