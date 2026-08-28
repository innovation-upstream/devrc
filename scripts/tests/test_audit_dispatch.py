#!/usr/bin/env python3
"""Tests for `scripts/audit-dispatch.py` — the audit-brief assembler.

WHAT THE SCRIPT IS FOR, so these tests are read at the right scope
------------------------------------------------------------------
The `/audit-pr` skill tells an operator to dispatch a subagent and supplies no
procedure, so the brief was reassembled from prose every time. Measured over one
session's 14 dispatches: 60,100 chars written by hand, 42% mean similarity
between consecutive briefs, ZERO lines over 25 chars identical across all 14 —
and three hard-won clauses present in early dispatches and LOST by later ones.
The script exists to make the invariant sections impossible to forget; this
module exists to make them impossible to delete quietly.

🔴 WHICH TESTS ARE REGRESSION COVERAGE AND WHICH ARE NOT
---------------------------------------------------------
`claude/RULES.md` requires the distinction, and requires naming the base a test
was watched red at. Here the honest answer is uncomfortable and is stated rather
than dressed up:

  RED_AT_BASE is **EMPTY, on purpose.** `scripts/audit-dispatch.py` did not
  exist at `3b79a35a` (this branch's base). Every test below would ERROR at that
  ref for want of the module, which is not evidence of anything — a test that
  cannot import the thing it tests is not "red at base" in the sense the rule
  means. NOTHING here is regression coverage for a bug that shipped.

  Everything here is therefore an INVARIANT GUARD or a STRUCTURAL LEDGER. What
  makes them non-vacuous is the MUTATION MATRIX below: each was watched to fail
  against a deliberately mutated copy of the script, and the mutant is named
  beside the test that caught it. A guard nobody watched go red is a guard
  nobody has evidence for, whatever its base ref.

  Both facts are asserted mechanically at the bottom of this module
  (`test_the_two_ledgers_partition_this_modules_tests`), so a test cannot
  quietly join or leave either list.

🔴 THE NEGATIVE-CONTROL MATRIX — measured, and RE-DERIVABLE
-------------------------------------------------------------
🔴 The rows below are transcribed from a harness that is IN THE TREE, and that
harness is the authority on which rows exist — count them there, not here:

    nix develop ~/workspace/devrc -c python3 scripts/tests/mutants-audit-dispatch.py

It exists rather than a scratchpad script for the reason
`scripts/tests/mutants-audit-ladder.sh` records: devrc #900 wrote ~30 mutants
into a docstring and every run of them happened in a session directory that no
longer exists, so not one row could be re-checked by anyone else — including the
rows that justified adding a pin. Each row there names the EXACT killer set and
reports WRONG-KILLER (an expected pin did not fire) separately from EXTRA-KILLER
(the row no longer isolates what it names).

Run 2026-08-27 against HEAD of this branch, each mutant applied to a COPY of
`scripts/audit-dispatch.py` in a scratch tree (never the worktree), under
`PYTHONDONTWRITEBYTECODE=1 -p no:cacheprovider`, with the unmutated copy as the
positive control. Every mutant asserted its target string present before
editing — a mutation that silently fails to apply reports "the guard held",
which is the most flattering possible wrong answer.

  POS  unmutated copy .............................. 37 passed  <- control

  D1   delete the `read-only` clause ............... 4 failed
  D3   delete the `stop-rule` clause ............... 4 failed
  D4   delete the `nit-is-not-a-finding` clause .... 4 failed
  D5   delete the `reverify-self-reported` clause .. 4 failed
  D6   delete the `finding-format` clause .......... 4 failed
  D7   delete the `do-not-merge` clause ............ 4 failed
         all six kill the same four: ledger_is_pinned_two_way /
         carries_the_instruction_its_ledger_entry_names /
         rendered_section_holds_exactly /
         control_a_clause_deleted_from_the_constant_is_detected
  D2   delete the `no-fetch` clause ................ 4 failed
         the three ledger tests, plus
         missing_clause_check_warns_and_never_blocks — that one looks up
         `no-fetch` BY ID, so deleting that particular clause makes it error.
         Recorded rather than tidied away: it is a real coupling, and it is why
         the deleted-clause CONTROL is a different test from the warn test.
  A1   ADD an eighth clause with no ledger entry ... 3 failed
         ledger_is_pinned_two_way / rendered_section_holds_exactly /
         control_a_clause_deleted_from_the_constant_is_detected
  R1   reword `stop-rule` to drop "ending it is the
       CORRECT outcome, not a failure" — the clause
       is still present and still emitted .......... 1 failed
         carries_the_instruction_its_ledger_entry_names ALONE
         🔴 THE REACHABILITY CONTROL. Every presence pin stays GREEN, so a red
         here proves the FRAGMENT ledger executes and is not a second spelling
         of the id ledger beside it.
  F1   render the invariant section from a hardcoded
       copy of the bullets, ignoring the constant ... 3 failed
         ledger_is_pinned_two_way stays GREEN (the ids are untouched);
         rendered_section_holds_exactly, emitted_verbatim_in_both_kinds and the
         deleted-clause control go red. This is why the RENDERED section is
         compared and not only the constant.
  C1   the delta refusal removed (a round-N brief is
       emitted with no claims block) ............... 3 failed
         delta_refusal_exits_non_zero / refusal_names_what_it_looked_for /
         refusal_fires_for_a_malformed_block
  C2   claims read from the WHOLE comment body
       instead of the fenced block ................. 3 failed
         brief_carries_the_claims_and_not_the_reasoning (the framing
         guarantee) / newest_claims_block_wins / emit_claims round-trip
  C3   cross-repo decision inverted (`!=` -> `==`) . 3 failed
         cross_repo_tells_the_agent_to_worktree_the_prs_repo /
         same_repo_recommends_the_isolation_flag / decision_comes_from_the_repos
  C4   the numstat command's non-zero exit reads as
       a clean zero instead of COULD NOT MEASURE ... 1 failed
         ledger_refuses_a_failed_command_rather_than_printing_zero
  C5   the ledger classifies by a `test`-substring
       pathspec and prints a number for X .......... 1 failed
         ledger_shows_the_files_and_refuses_to_classify_them
  C6   the missing-clause warning BLOCKS
       (returns non-zero) .......................... 1 failed
         missing_clause_check_warns_and_never_blocks

🔴 ONE MEASURED NON-RESULT, RECORDED BECAUSE IT LOOKS LIKE COVERAGE:
`test_every_clause_is_emitted_verbatim_in_both_kinds_of_brief` does **NOT** go
red for D1-D7. It iterates `INVARIANT_CLAUSES` and asserts each entry reaches
the brief, so a clause deleted from the constant is deleted from both sides of
its comparison. It is the POSITIVE control (a zero-length or clause-free brief
fails loudly) and F1's second killer — it is not a deletion detector, and
reading it as one is exactly the "guard whose description is wider than its
implementation" failure. The deletion detectors are the ledger tests.

🔴 WHAT THIS MODULE DOES **NOT** ENFORCE
-----------------------------------------
1. **It cannot see a clause REWORDED into something weaker** beyond the one
   fragment its ledger names. `CLAUSE_LEDGER` pins one load-bearing phrase per
   clause, not the whole sentence, because the whole sentence would live in two
   places and drift. So a rewrite that keeps the fragment and guts the rest is
   invisible. Mitigation is review, not this file.
2. **It proves nothing about BEHAVIOUR.** Nothing here shows any auditor read
   the brief, obeyed the no-fetch clause, or stopped at a clean round. The
   measurement that motivated the script was over transcripts; the verifier for
   whether it CHANGED anything is a re-measurement over future dispatches, and
   that claim is NOT made here.
3. **It does not test the real `gh` or `git`.** Every test injects a runner. So
   a wrong `gh --json` field name, or a `git` flag this host's version does not
   have, is out of scope — the seam is tested, not the tools behind it. That is
   deliberate (hermetic), and it is a real blind spot, not a covered one.
4. **It does not check that the classification a human writes is CORRECT.** The
   script deliberately refuses to classify payload vs scaffolding; nothing
   mechanical can grade the answer.

HERMETIC: no test touches the network, spawns a process, or reads a real PR.
`test_nothing_here_spawns_a_subprocess` proves the first two by making
`subprocess.run` raise for the duration of a full `main()` run.
"""
from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "audit-dispatch.py"


def _load():
    spec = importlib.util.spec_from_file_location("audit_dispatch", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ad = _load()

# --------------------------------------------------------------------------- #
# 🔴 THE INDEPENDENT LEDGER — this is what makes the pin two-way.
# --------------------------------------------------------------------------- #
# If this ledger were derived from `ad.INVARIANT_CLAUSES` the whole test would
# be vacuous: deleting a clause would delete it from both sides and the
# comparison would stay green. So the ids and the load-bearing fragment of each
# clause are RESTATED here by hand, and the duplication is the point.
#
# Each fragment is the phrase a dispatch measurably LOST when the clause was
# written from memory (see the module docstring's table). Pinning the phrase
# rather than the sentence is a deliberate trade: a reword that keeps the phrase
# is invisible (blind spot 1), and a sentence pinned in two files drifts.
CLAUSE_LEDGER = {
    "read-only": "rm -f <copy>/.git",
    "no-fetch": "Do NOT `git fetch`",
    "stop-rule": "ending it is the CORRECT outcome, not a failure",
    "nit-is-not-a-finding": "changes nothing a reader does is NOT a finding",
    "reverify-self-reported": "self-reported numbers rather than accepting them",
    "finding-format": "`payload` or `scaffolding` label",
    "do-not-merge": "Do not merge — report only",
}

# Restated, not imported, for the same reason: the bullet extractor below must
# not be steerable by the module it audits.
EXPECTED_INVARIANTS_HEADING = "## 🔴 NON-NEGOTIABLE — every audit, every round"


def bullets_in_invariant_section(brief: str) -> list[str]:
    """Every `- ` bullet between the invariants heading and the next `## `.

    A second, independent implementation on purpose — the script renders that
    section, so a shared parser could agree with a broken renderer.
    """
    lines = brief.splitlines()
    assert EXPECTED_INVARIANTS_HEADING in lines, (
        "the rendered brief has no invariants heading spelled exactly "
        f"{EXPECTED_INVARIANTS_HEADING!r} — every pin below is looking in a "
        "section that does not exist, so they would all pass or all fail for "
        "the wrong reason."
    )
    start = lines.index(EXPECTED_INVARIANTS_HEADING)
    out = []
    for line in lines[start + 1:]:
        if line.startswith("## "):
            break
        if line.startswith("- "):
            out.append(line[2:])
    return out


# --------------------------------------------------------------------------- #
# Fakes for the ONE process boundary
# --------------------------------------------------------------------------- #

DEFAULT_PR = {
    "title": "a synthetic PR title",
    "url": "https://example.invalid/pulls/900",
    "baseRefName": "main",
    "headRepository": {"name": "devrc"},
    "headRepositoryOwner": {"login": "example-org"},
}

FAKE_REPO_DIR = "/fake/checkout/devrc"
FAKE_ORIGIN = "git@github.com:example-org/devrc.git"

CHECKLIST = (
    "**Audit for:**\n1. **Risks** — what breaks in production.\n"
    "9. **Second-order consequences** — ripple effects."
)


def fake_checklist(_repo_dir):
    return CHECKLIST


def make_runner(
    *,
    comments=(),
    pr=None,
    gh_rc=0,
    gh_stdout=None,
    origin=FAKE_ORIGIN,
    toplevel=FAKE_REPO_DIR,
    branch="feat/some-branch",
    status=" M scripts/a.py\n?? scripts/b.py\n",
    rev_list=(0, "4\n", ""),
    numstat=(0, "10\t2\tscripts/foo.py\n3\t0\tscripts/tests/test_foo.py\n", ""),
    head_sha="deadbee",
):
    """A closed-world stand-in for `gh` and `git`. No process is spawned."""
    payload = dict(pr or DEFAULT_PR)
    payload["comments"] = [{"body": c} for c in comments]

    def runner(cmd, cwd=None):
        if cmd[0] == "gh":
            if gh_rc:
                return gh_rc, "", "gh: synthetic failure"
            return 0, gh_stdout if gh_stdout is not None else json.dumps(payload), ""
        if cmd[:2] == ["git", "-C"]:
            verb = cmd[3]
            if verb == "rev-parse" and "--show-toplevel" in cmd:
                return 0, toplevel + "\n", ""
            if verb == "rev-parse" and "--abbrev-ref" in cmd:
                return 0, branch + "\n", ""
            if verb == "rev-parse" and "--short" in cmd:
                return 0, head_sha + "\n", ""
            if verb == "remote":
                return (0, origin + "\n", "") if origin else (1, "", "no origin")
            if verb == "status":
                return 0, status, ""
            if verb == "rev-list":
                return rev_list
            if verb == "log":
                return numstat
        raise AssertionError(f"unexpected command in a hermetic test: {cmd}")

    return runner


def run_main(argv, **kw):
    """-> (rc, stdout, stderr). Always with an injected runner."""
    out, err = io.StringIO(), io.StringIO()
    rc = ad.main(
        argv,
        runner=kw.pop("runner", make_runner(**kw)),
        stdout=out,
        stderr=err,
        checklist_reader=fake_checklist,
    )
    return rc, out.getvalue(), err.getvalue()


CLAIMS_BLOCK_R2 = (
    "```audit-claims round=2 audited=aaaa1111..bbbb2222\n"
    "1. the collapsed branch was split so each reports its own state\n"
    "2. the over-stated quantifier was replaced with the measured floor\n"
    "```"
)

# Prose that surrounds the block in a real PR comment. 🔴 This is the material a
# framed audit would go on to CONFIRM, and it must never reach the brief.
SURROUNDING_PROSE = (
    "Round 2 fixes are pushed. WHY THIS IS CORRECT: the earlier guard already "
    "rejects that input, so the second branch is unreachable and the split is "
    "safe. I re-ran the sweep and every row is green.\n\n"
    "{block}\n\n"
    "Happy to walk through the reasoning if useful."
)


def comment_with_prose():
    return SURROUNDING_PROSE.format(block=CLAIMS_BLOCK_R2)


# --------------------------------------------------------------------------- #
# THE TWO-WAY PIN
# --------------------------------------------------------------------------- #

def test_the_invariant_clause_ledger_is_pinned_two_way():
    """A clause with no ledger entry, or a ledger entry naming no clause, fails.

    🔴 Both directions, and the messages differ, because the fixes differ. A
    clause deleted from the script is the LOSS this whole script exists to
    prevent; a clause added without a ledger entry is an unreviewed instruction
    riding into every future brief.
    """
    in_script = {c.id for c in ad.INVARIANT_CLAUSES}
    in_ledger = set(CLAUSE_LEDGER)

    dropped = in_ledger - in_script
    assert not dropped, (
        "\n\nscripts/audit-dispatch.py: invariant clause(s) "
        f"{sorted(dropped)} are named in this module's ledger but no longer "
        "exist in INVARIANT_CLAUSES.\n"
        "  These are the clauses that were MEASURED to go missing when a brief "
        "was written from memory — one of them (`no-fetch`) first appeared at "
        "dispatch 9 of 14, and an auditor at dispatch 8 fetched in a repo the "
        "brief had called read-only.\n"
        "  If you removed one deliberately, delete its CLAUSE_LEDGER entry in "
        "the same commit and say in the message which dispatch failure that "
        "clause was closing."
    )
    added = in_script - in_ledger
    assert not added, (
        "\n\nscripts/audit-dispatch.py: invariant clause(s) "
        f"{sorted(added)} are emitted into every brief but are not in this "
        "module's ledger.\n"
        "  Add a CLAUSE_LEDGER entry naming the load-bearing phrase, so a "
        "later reword that guts the clause goes red here."
    )


def test_each_clause_carries_the_instruction_its_ledger_entry_names():
    """🔴 The reachability control for the ledger above.

    The id pin passes for a clause whose text has been reworded into something
    weaker. This is the assertion that sees that — and mutant R1 (reword
    `stop-rule` to drop "ending it is the CORRECT outcome, not a failure") kills
    THIS test alone, with every presence pin still green.
    """
    by_id = {c.id: c.text for c in ad.INVARIANT_CLAUSES}
    for cid, fragment in CLAUSE_LEDGER.items():
        assert cid in by_id, f"clause {cid!r} is gone; see the two-way pin above"
        assert fragment in by_id[cid], (
            f"\n\nclause {cid!r} no longer contains the phrase this module "
            f"pins:\n    {fragment!r}\n"
            f"  It now reads:\n    {by_id[cid]!r}\n"
            "  That phrase is the part a hand-written brief measurably lost. "
            "If the reword is deliberate, update CLAUSE_LEDGER in the same "
            "commit — which is the moment to notice you are rewriting an "
            "instruction rather than reformatting one."
        )


@pytest.mark.parametrize("argv,kw", [
    (["900"], {}),
    (["900", "--round", "3"], {"comments": [CLAIMS_BLOCK_R2]}),
])
def test_every_clause_is_emitted_verbatim_in_both_kinds_of_brief(argv, kw):
    """POSITIVE CONTROL: a real assembled brief carries every clause.

    A zero-length or clause-free brief must fail loudly. Both round shapes are
    covered because they take different render paths, and a clause section
    wired into only one of them would otherwise look complete.
    """
    rc, out, err = run_main(argv, **kw)
    assert rc == 0, f"assembly failed: {err}"
    assert len(out) > 3_000, (
        f"the assembled brief is only {len(out)} chars — a brief that short is "
        "not a brief, and every 'clause is present' assertion below would be a "
        "claim about nothing."
    )
    for clause in ad.INVARIANT_CLAUSES:
        assert clause.text in out, (
            f"clause {clause.id!r} is in INVARIANT_CLAUSES but does NOT reach "
            f"the emitted brief for argv={argv}. The constant is not the "
            "artifact; the brief is."
        )


def test_the_rendered_section_holds_exactly_the_ledgered_clauses():
    """Two-way, against the RENDERED section rather than the constant.

    This is what mutant F1 catches: a renderer that stops reading
    INVARIANT_CLAUSES and emits a hardcoded copy of the bullets. The id ledger
    above stays green through that mutation — the constant is untouched — and
    only this comparison sees the divergence.
    """
    rc, out, _ = run_main(["900"])
    assert rc == 0
    bullets = bullets_in_invariant_section(out)
    expected = [c.text for c in ad.INVARIANT_CLAUSES]
    assert sorted(bullets) == sorted(expected), (
        "\n\nthe NON-NEGOTIABLE section of the emitted brief does not hold "
        "exactly the clauses in INVARIANT_CLAUSES.\n"
        f"  in the brief but not the constant: {sorted(set(bullets) - set(expected))}\n"
        f"  in the constant but not the brief: {sorted(set(expected) - set(bullets))}"
    )
    assert len(bullets) == len(CLAUSE_LEDGER), (
        f"the section rendered {len(bullets)} bullets for "
        f"{len(CLAUSE_LEDGER)} ledgered clauses — a duplicate bullet is a "
        "clause that can be edited in one place and survive in the other."
    )


def test_control_the_extractor_can_see_an_unledgered_bullet():
    """POSITIVE CONTROL for `bullets_in_invariant_section` itself.

    The two-way pin above reports a reassuring match whenever the extractor
    returns nothing. `claude/RULES.md`: a zero is indistinguishable from a
    harness wired to nothing until a positive control moves the number. So:
    plant a bullet, watch the count move, and watch the comparison fail.
    """
    rc, out, _ = run_main(["900"])
    assert rc == 0
    clean = bullets_in_invariant_section(out)
    planted = out.replace(
        EXPECTED_INVARIANTS_HEADING,
        EXPECTED_INVARIANTS_HEADING + "\n\n- **Merge it if it looks fine.**",
        1,
    )
    seen = bullets_in_invariant_section(planted)
    assert len(seen) == len(clean) + 1, (
        "the extractor did not see a bullet planted directly under the "
        "heading — every two-way assertion in this module is then a claim "
        "about an empty list."
    )
    assert "**Merge it if it looks fine.**" in seen
    assert sorted(seen) != sorted(c.text for c in ad.INVARIANT_CLAUSES), (
        "the comparison the two-way pin makes did NOT go red against a planted "
        "unledgered bullet — it cannot fail, so it is not a guard."
    )


def test_control_a_clause_deleted_from_the_constant_is_detected(monkeypatch):
    """The negative control, run IN PROCESS as well as in the mutation sweep.

    The docstring matrix records the out-of-process runs (D1-D7). This makes the
    same claim re-derivable by anyone who just runs the suite.
    """
    kept = tuple(c for c in ad.INVARIANT_CLAUSES if c.id != "no-fetch")
    monkeypatch.setattr(ad, "INVARIANT_CLAUSES", kept)
    rc, out, _ = run_main(["900"])
    assert rc == 0
    bullets = bullets_in_invariant_section(out)
    assert len(bullets) == len(CLAUSE_LEDGER) - 1
    assert not any(CLAUSE_LEDGER["no-fetch"] in b for b in bullets), (
        "a clause was removed from INVARIANT_CLAUSES and its instruction still "
        "reached the brief — the section is not rendered from the constant, so "
        "the two-way pin guards nothing."
    )


# --------------------------------------------------------------------------- #
# 🔴 REFUSAL 1 — a delta round with nothing to be framed on
# --------------------------------------------------------------------------- #

def test_the_delta_refusal_exits_non_zero_and_emits_no_brief():
    rc, out, err = run_main(["900", "--round", "3"], comments=[])
    assert rc != 0, (
        "a round-3 brief was emitted with NO claims block. An empty 'what was "
        "claimed fixed' section silently turns a delta re-audit into a blind "
        "full audit — a different thing, which then reads as covered."
    )
    assert out.strip() == "", (
        "the refusal still printed something on stdout; an operator pasting "
        "stdout would dispatch a brief the script refused to vouch for."
    )


def test_the_refusal_names_what_it_looked_for_and_where():
    """Assert on the MESSAGE, not just the exit code.

    A refusal an operator cannot act on gets worked around, and the workaround
    is 'run it without --round', which is exactly the silent downgrade the
    refusal exists to prevent.
    """
    _, _, err = run_main(["900", "--round", "3"], comments=[])
    assert ad.REFUSAL_HEADER in err
    assert "audit-claims round=<n> audited=<sha>..<sha>" in err, (
        "the refusal does not show the block shape it looked for"
    )
    assert "PR #900's comments" in err, "the refusal does not say where it looked"
    assert "no `audit-claims` block" in err, "the refusal does not say what it found"
    assert "--emit-claims" in err, (
        "the refusal does not tell the operator how to produce the block it "
        "wants — a refusal with no exit is a refusal people route around"
    )


@pytest.mark.parametrize("body,expected", [
    ("```audit-claims round=3\n1. a thing\n```", "does not carry both"),
    ("```audit-claims audited=aa..bb\n1. a thing\n```", "does not carry both"),
    ("```audit-claims round=3 audited=aa..bb\nfixed some stuff\n```",
     "no numbered claim lines"),
])
def test_the_refusal_fires_for_a_malformed_block_and_says_which_way(body, expected):
    """A near-miss must be reported as a near-miss.

    Silently skipping an unparseable fence produces the SAME observable as 'no
    block at all' — and those need opposite fixes (repair the header vs write
    the block). `claude/RULES.md`: an empty result cannot distinguish two
    mechanisms.
    """
    rc, out, err = run_main(["900", "--round", "3"], comments=[body])
    assert rc != 0 and out.strip() == ""
    assert expected in err, (
        f"the refusal message does not name the malformation. Got:\n{err}"
    )


def test_a_first_round_needs_no_claims_block():
    """The refusal must not fire where a blind full audit is the CORRECT thing."""
    rc, out, err = run_main(["900"], comments=[])
    assert rc == 0, err
    assert "FIRST, FULL adversarial audit" in out
    assert "WHAT WAS CLAIMED FIXED" not in out, (
        "a first audit must not carry a claims section at all — an empty one "
        "reads as 'nothing was claimed', which is a different statement"
    )


# --------------------------------------------------------------------------- #
# 🔴 THE FRAMING GUARANTEE
# --------------------------------------------------------------------------- #

def test_the_brief_carries_the_claims_and_not_the_reasoning_around_them():
    """🔴 The rule that keeps a delta round from verifying its own frame.

    The skill records three successive FRAMED audits confirming a claim purely
    because the prompt handed them the answer, against one BLIND audit that
    refuted it in a single pass. A PR comment holds BOTH the claims and the
    argument for them, so the assembler reads only the fenced block.

    Mutant C2 (read the whole comment body instead of the fence) kills this test
    alone.
    """
    rc, out, err = run_main(
        ["900", "--round", "3"], comments=[comment_with_prose()]
    )
    assert rc == 0, err

    assert "the collapsed branch was split so each reports its own state" in out
    assert "the over-stated quantifier was replaced with the measured floor" in out

    for leaked in (
        "WHY THIS IS CORRECT",
        "so the second branch is unreachable and the split is safe",
        "I re-ran the sweep and every row is green",
        "Happy to walk through the reasoning if useful",
    ):
        assert leaked not in out, (
            f"the brief reproduced reasoning from the PR comment: {leaked!r}.\n"
            "  That is the material a framed audit goes on to CONFIRM. Only "
            "the fenced block's numbered lines may reach the brief."
        )
    assert "never WHY IT IS CORRECT" in out, (
        "the claims section does not tell the auditor that these are claims"
    )


def test_the_newest_claims_block_wins():
    older = (
        "```audit-claims round=2 audited=aaaa1111..bbbb2222\n"
        "1. an older claim\n```"
    )
    newer = (
        "```audit-claims round=4 audited=bbbb2222..cccc3333\n"
        "1. a newer claim\n```"
    )
    rc, out, err = run_main(["900", "--round", "5"], comments=[older, newer])
    assert rc == 0, err
    assert "a newer claim" in out
    assert "an older claim" not in out
    assert "cccc3333..HEAD" in out, (
        "the range must be anchored at the NEWEST audited tip, not the oldest"
    )


def test_emit_claims_prints_a_block_this_scripts_own_parser_accepts():
    """A round trip, so the skeleton cannot drift away from the reader.

    The next round REFUSES on an unparseable block, so a skeleton the parser
    rejects would turn `--emit-claims` into a trap.
    """
    rc, out, err = run_main(
        ["900", "--round", "3", "--emit-claims"], comments=[CLAIMS_BLOCK_R2]
    )
    assert rc == 0, err
    tail = out[out.index("```audit-claims"):]
    blocks, malformed = ad.parse_claims_blocks([tail])
    assert not malformed, malformed
    assert len(blocks) == 1
    assert blocks[0].round_no == 3
    assert blocks[0].audited_from == "bbbb2222"
    assert blocks[0].audited_to == "deadbee"
    assert len(blocks[0].items) >= 1


# --------------------------------------------------------------------------- #
# 🔴 THE CROSS-REPO BRANCH — both directions
# --------------------------------------------------------------------------- #

def _isolation_lines(brief):
    return [ln for ln in brief.splitlines() if 'isolation: "worktree"' in ln]


def test_cross_repo_tells_the_agent_to_worktree_the_prs_repo_itself():
    """🔴 The highest-value generated field.

    `isolation: "worktree"` builds from the CWD's repo. For a PR in a different
    repo that is the WRONG tree and the failure is quiet — the agent either
    reports a briefed file missing, or silently audits the wrong repository.
    """
    pr = dict(DEFAULT_PR, headRepository={"name": "otherproj"},
              headRepositoryOwner={"login": "someone-else"})
    rc, out, err = run_main(["900"], pr=pr)
    assert rc == 0, err

    assert "git -C" in out and "worktree add" in out, (
        "the cross-repo brief does not tell the agent to create the worktree "
        "itself"
    )
    assert "someone-else/otherproj" in out, (
        "the cross-repo brief does not name the repo the worktree must come from"
    )
    lines = _isolation_lines(out)
    assert lines, "the brief says nothing about the isolation flag at all"
    for line in lines:
        assert "Do NOT" in line, (
            "\n\nthe cross-repo brief mentions `isolation: \"worktree\"` on a "
            f"line that does not forbid it:\n    {line}\n"
            "  That flag worktrees the CWD's repo, which is the wrong one here."
        )
    assert ad.ISOLATION_RECOMMEND not in out


def test_same_repo_recommends_the_isolation_flag_and_does_not_hand_roll():
    """The reverse direction — measured, not assumed to follow from the first."""
    rc, out, err = run_main(["900"])  # DEFAULT_PR is example-org/devrc, == origin
    assert rc == 0, err
    assert ad.ISOLATION_RECOMMEND in out
    lines = _isolation_lines(out)
    assert lines and not any("Do NOT" in ln for ln in lines), (
        "the same-repo brief forbids the flag that is correct here"
    )
    where = out[out.index("## WHERE TO WORK"):out.index("## THE RANGE")]
    assert "worktree add" not in where, (
        "the same-repo brief tells the agent to hand-roll a worktree; that is "
        "the cross-repo remedy and it is noise here"
    )


def test_the_cross_repo_decision_comes_from_the_repos_not_from_prose():
    """The same run, differing only in the PR's repo, must flip the section."""
    same = run_main(["900"])[1]
    other = run_main(["900"], pr=dict(
        DEFAULT_PR, headRepository={"name": "otherproj"},
        headRepositoryOwner={"login": "someone-else"}))[1]
    assert (ad.ISOLATION_FORBID in other) and (ad.ISOLATION_FORBID not in same)
    assert (ad.ISOLATION_RECOMMEND in same) and (ad.ISOLATION_RECOMMEND not in other)


# --------------------------------------------------------------------------- #
# The generated facts
# --------------------------------------------------------------------------- #

def test_the_shared_checkout_state_is_reported_with_the_it_moves_warning():
    rc, out, err = run_main(["900"], branch="feat/thing", status=" M a\n M b\n M c\n")
    assert rc == 0, err
    assert "feat/thing" in out
    assert "3 uncommitted path(s)" in out
    assert "MOVES UNDER YOU" in out
    assert "NOT a finding" in out


def test_the_toolchain_section_names_the_tier_the_merge_gates_on():
    """Two of the three clauses the measurement showed decaying live here.

    They are GENERATED (they interpolate the repo path), so they are not in
    INVARIANT_CLAUSES — which means nothing else in this module would notice
    them going missing.
    """
    rc, out, err = run_main(["900"])
    assert rc == 0, err
    assert "nix develop /fake/checkout/devrc -c python3 -m pytest" in out
    assert "-p no:cacheprovider" in out
    assert "No module named pytest" in out and "WRONG SHELL" in out, (
        "the wrong-shell diagnosis is missing — it was present in 9 of the "
        "first 9 measured dispatches and absent from 3 of the last 5"
    )
    assert "nix build /fake/checkout/devrc#checks.x86_64-linux.pytests" in out
    assert "nix build /fake/checkout/devrc#checks.x86_64-linux.nodetests" in out
    assert "gate.sh --tier both" in out
    assert "git --version" in out


def test_the_range_is_generated_from_the_previous_rounds_audited_sha():
    rc, out, err = run_main(["900", "--round", "3"], comments=[CLAIMS_BLOCK_R2])
    assert rc == 0, err
    assert "`bbbb2222..HEAD`" in out, (
        "the delta range is not anchored at the sha the previous round audited"
    )


# --------------------------------------------------------------------------- #
# THE LEDGER
# --------------------------------------------------------------------------- #

def test_the_ledger_shows_the_files_and_refuses_to_classify_them():
    """🔴 The classification is a HUMAN judgement and the script says so.

    A pathspec is wrong in both directions on ordinary names — `':!*test*'`
    swallows `attestation/`, `latest/` and `inspector/` and keeps
    `FooTest.java`. Mutant C5 (classify by pathspec and print an X) kills this.
    """
    rc, out, err = run_main(["900", "--round", "3"], comments=[CLAIMS_BLOCK_R2])
    assert rc == 0, err
    assert "scripts/foo.py" in out and "scripts/tests/test_foo.py" in out
    assert "Classify these files yourself" in out
    assert "payload lines changed THIS round: X" in out, (
        "the ledger line must leave X for the human — a number here would be a "
        "classification the script is not entitled to make"
    )
    assert "attestation" in out and "login.cy.ts" in out, (
        "the counter-evidence for why a pathspec cannot do this is missing"
    )


@pytest.mark.parametrize("kw,expect", [
    ({"rev_list": (128, "", "fatal: bad revision")}, "exited 128"),
    ({"rev_list": (0, "0\n", "")}, "is EMPTY"),
    ({"numstat": (128, "", "unknown option --remerge-diff")}, "exited 128"),
    ({"numstat": (0, "1\t1\tx\n", "warning: unable to write object")},
     "wrote to STDERR"),
])
def test_the_ledger_refuses_a_failed_command_rather_than_printing_zero(kw, expect):
    """🔴 rc 0, silent stderr, non-empty range — all three, or no number.

    Each of these produces a PLAUSIBLE zero: a git without `--remerge-diff`
    exits 128 with empty output; an unwritable object store makes it UNDER-count
    while exiting 0 and announcing itself only on stderr; a range whose commits
    are not in this checkout prints nothing at all with rc 0.
    """
    rc, out, err = run_main(
        ["900", "--round", "3"], comments=[CLAIMS_BLOCK_R2], **kw
    )
    assert rc == 0, err
    assert "COULD NOT MEASURE" in out, (
        "the ledger reported a number from a command that did not earn it"
    )
    assert expect in out, f"the reason does not name the failure. Got:\n{out}"
    assert "payload lines changed THIS round" not in out, (
        "a ledger line was printed under a COULD NOT MEASURE — the two must "
        "not appear together, or the operator copies the line anyway"
    )


def test_the_cumulative_figure_is_not_measured_without_a_round_one_anchor():
    """An unmeasurable quantity is reported as unmeasured, never substituted."""
    bare = (
        "```audit-claims round=2 audited=bbbb2222\n1. something\n```"
    )
    rc, out, err = run_main(["900", "--round", "3"], comments=[bare])
    assert rc == 0, err
    assert "(since round 1: Y)" in out
    assert "NOT MEASURED" in out


def test_the_ledger_says_the_base_was_not_fetched():
    """The script cannot fetch (that would be a write), so it says so.

    A stale `<base>` re-reports upstream work as this round's payload — measured
    at 201 lines where the truth was 1.
    """
    rc, out, err = run_main(["900", "--round", "3"], comments=[CLAIMS_BLOCK_R2])
    assert rc == 0, err
    assert "does not fetch" in out and "stale base" in out


# --------------------------------------------------------------------------- #
# REFUSAL 2's opposite number: WARN, NEVER BLOCK
# --------------------------------------------------------------------------- #

def test_missing_clause_check_warns_and_never_blocks(monkeypatch, tmp_path):
    """🔴 Warn-only, deliberately.

    This check exists for a hand-edited `--out` file. `claude/RULES.md`: a
    permanently-red gate is worse than no gate — it trains everyone to click
    through. Blocking here would refuse to run over an edit that was deliberate.
    Mutant C6 (raise instead of return) kills this test.
    """
    real_render = ad.render_brief
    # By ID, not by index: an index makes this test's outcome depend on which
    # OTHER clause a mutation deleted, which shows up in a sweep as this test
    # dying for the wrong reason.
    target = next(c for c in ad.INVARIANT_CLAUSES if c.id == "no-fetch")

    def lossy(facts):
        text = real_render(facts)
        return text.replace(target.text, "(hand-edited away)")

    monkeypatch.setattr(ad, "render_brief", lossy)
    out_file = tmp_path / "brief.md"
    rc, _, err = run_main(["900", "--out", str(out_file)])

    assert rc == 0, "the missing-clause check BLOCKED; it must only warn"
    assert "missing invariant clause(s)" in err and "no-fetch" in err
    assert "WARNING, not a refusal" in err
    assert out_file.read_text(encoding="utf-8"), (
        "the brief was not written — a warning must not suppress the output"
    )


def test_missing_clauses_is_a_pure_function_over_the_text():
    full = "\n".join(c.text for c in ad.INVARIANT_CLAUSES)
    assert ad.missing_clauses(full) == []
    assert ad.missing_clauses("") == [c.id for c in ad.INVARIANT_CLAUSES]
    assert ad.missing_clauses(
        full.replace(ad.INVARIANT_CLAUSES[0].text, "")
    ) == [ad.INVARIANT_CLAUSES[0].id]


# --------------------------------------------------------------------------- #
# The parser, driven directly
# --------------------------------------------------------------------------- #

def test_parse_claims_blocks_reads_only_the_fence():
    blocks, malformed = ad.parse_claims_blocks([comment_with_prose()])
    assert not malformed
    assert len(blocks) == 1
    assert blocks[0].items == [
        "the collapsed branch was split so each reports its own state",
        "the over-stated quantifier was replaced with the measured floor",
    ]
    assert blocks[0].audited_from == "aaaa1111"
    assert blocks[0].audited_to == "bbbb2222"


def test_parse_claims_blocks_reports_a_bad_header_rather_than_skipping_it():
    blocks, malformed = ad.parse_claims_blocks(
        ["```audit-claims round=oops audited=aa..bb\n1. x\n```"]
    )
    assert blocks == []
    assert malformed and "does not carry both" in malformed[0]


def test_a_comment_with_no_fence_yields_nothing_and_no_false_malformation():
    blocks, malformed = ad.parse_claims_blocks(
        ["just prose, mentioning audit-claims and round=3 in passing"]
    )
    assert blocks == [] and malformed == []


# --------------------------------------------------------------------------- #
# HERMETICITY + the ledger-of-ledgers
# --------------------------------------------------------------------------- #

def test_nothing_here_spawns_a_subprocess(monkeypatch):
    """Proves the injected seam is the ONLY process boundary.

    If `main` ever reaches around the injected runner — a bare `subprocess.run`
    for one convenient extra fact — this suite would silently start depending on
    the host, the network and a real PR.
    """
    def explode(*a, **kw):
        raise AssertionError(
            "audit-dispatch.py spawned a process during a test that injected a "
            f"runner: {a!r}"
        )

    monkeypatch.setattr(ad.subprocess, "run", explode)
    rc, out, err = run_main(
        ["900", "--round", "3", "--emit-claims"], comments=[CLAIMS_BLOCK_R2]
    )
    assert rc == 0, err
    assert out


def test_a_gh_failure_is_reported_and_not_papered_over():
    rc, out, err = run_main(["900"], gh_rc=1)
    assert rc != 0 and out.strip() == ""
    assert "gh pr view 900" in err and "failed" in err


# The two ledgers the module docstring commits to. RED_AT_BASE is empty ON
# PURPOSE and that emptiness is asserted, so nobody can later read this module
# as carrying regression coverage it does not have.
RED_AT_BASE: frozenset[str] = frozenset()

INVARIANT_GUARDS_AND_LEDGERS = frozenset({
    "test_the_invariant_clause_ledger_is_pinned_two_way",
    "test_each_clause_carries_the_instruction_its_ledger_entry_names",
    "test_every_clause_is_emitted_verbatim_in_both_kinds_of_brief",
    "test_the_rendered_section_holds_exactly_the_ledgered_clauses",
    "test_control_the_extractor_can_see_an_unledgered_bullet",
    "test_control_a_clause_deleted_from_the_constant_is_detected",
    "test_the_delta_refusal_exits_non_zero_and_emits_no_brief",
    "test_the_refusal_names_what_it_looked_for_and_where",
    "test_the_refusal_fires_for_a_malformed_block_and_says_which_way",
    "test_a_first_round_needs_no_claims_block",
    "test_the_brief_carries_the_claims_and_not_the_reasoning_around_them",
    "test_the_newest_claims_block_wins",
    "test_emit_claims_prints_a_block_this_scripts_own_parser_accepts",
    "test_cross_repo_tells_the_agent_to_worktree_the_prs_repo_itself",
    "test_same_repo_recommends_the_isolation_flag_and_does_not_hand_roll",
    "test_the_cross_repo_decision_comes_from_the_repos_not_from_prose",
    "test_the_shared_checkout_state_is_reported_with_the_it_moves_warning",
    "test_the_toolchain_section_names_the_tier_the_merge_gates_on",
    "test_the_range_is_generated_from_the_previous_rounds_audited_sha",
    "test_the_ledger_shows_the_files_and_refuses_to_classify_them",
    "test_the_ledger_refuses_a_failed_command_rather_than_printing_zero",
    "test_the_cumulative_figure_is_not_measured_without_a_round_one_anchor",
    "test_the_ledger_says_the_base_was_not_fetched",
    "test_missing_clause_check_warns_and_never_blocks",
    "test_missing_clauses_is_a_pure_function_over_the_text",
    "test_parse_claims_blocks_reads_only_the_fence",
    "test_parse_claims_blocks_reports_a_bad_header_rather_than_skipping_it",
    "test_a_comment_with_no_fence_yields_nothing_and_no_false_malformation",
    "test_nothing_here_spawns_a_subprocess",
    "test_a_gh_failure_is_reported_and_not_papered_over",
    "test_the_two_ledgers_partition_this_modules_tests",
})


def test_the_two_ledgers_partition_this_modules_tests():
    """A test cannot quietly join or leave either ledger.

    Same shape as `scripts/tests/test_transcript_search.py`'s two lists, and for
    the same reason: "watched red" is a claim about a specific base, and a
    module whose docstring says "these are invariant guards" while quietly
    growing an unlisted test is asserting coverage nobody checked.
    """
    here = {n for n in globals() if n.startswith("test_")}
    assert RED_AT_BASE == frozenset(), (
        "RED_AT_BASE is documented as empty because scripts/audit-dispatch.py "
        "did not exist at 3b79a35a. If a test here IS regression coverage for "
        "a shipped bug, name the base you watched it red at in the docstring "
        "before adding it."
    )
    unlisted = here - INVARIANT_GUARDS_AND_LEDGERS - RED_AT_BASE
    assert not unlisted, (
        f"tests not in either ledger: {sorted(unlisted)}. Add each to "
        "INVARIANT_GUARDS_AND_LEDGERS (or to RED_AT_BASE with its base ref)."
    )
    stale = (INVARIANT_GUARDS_AND_LEDGERS | RED_AT_BASE) - here
    assert not stale, (
        f"ledger entries naming no test: {sorted(stale)}. A ledger that names "
        "a deleted test reads as coverage that no longer runs."
    )
