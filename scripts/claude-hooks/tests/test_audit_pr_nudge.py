#!/usr/bin/env python3
"""Tests for audit-pr-nudge.py — the PostToolUse nudge fired when a PR is created.

🔴 WHY THIS FILE EXISTS, AND WHY IT EXISTS NOW. The hook shipped with NO
behavioural test: measured 2026-09-12 at db7bf3ff, `git grep -l audit-pr-nudge`
returned 9 files, of which the only two tests covered its FILENAME
(test_on_disk_artifact_names.py) and its REGISTRATION in settings.json
(test_register_nudge_hook.py). Nothing read the text it injects — so the message
was unpinned by construction, which is the same shape
`claudedocs/handoff-audit-pr-ladder.md` records for counts quoted in prose that
no assertion reads.

WHAT THE MESSAGE HAS TO DO, and why it is a correctness property rather than
wording. A round-0 audit (the requirements-and-deletion pass) is the only round
that can conclude *close this PR, do not audit it*, and its question — should
this change EXIST — is actionable ONLY before the merge decision is taken.
MEASURED over the round-0 trial (same handoff, rank 1): dispatched at audit time
rather than at PR-create time, the report landed after the decision in 3 of 4
trials — `#1523` merged 27 min BEFORE its audit was even dispatched, `#1510`
merged 6 min after, `#1518` was closed 5 min before the report returned. The
section worked whenever it arrived in time; the ROUTING was the defect. This
hook is the only thing in the system that fires at the moment a PR is born, so
the routing fix belongs here and nowhere else — one rule, one place.

Hence test 3: the nudge must NAME round 0. That is the regression guard, and it
was watched RED at db7bf3ff, where the message named only `/audit-pr <n>` (which
defaults to round 1).

⚠ The other tests here are INVARIANT GUARDS, not regression coverage — no shipped
bug violated them. They are labelled as such below so nobody counts them as
evidence of a fixed defect. They exist because test 3 alone could pass while the
hook had stopped firing at all, and a guard nobody can see fail is worthless.
"""
import json
import subprocess
import sys
from pathlib import Path

HOOK = Path(__file__).resolve().parents[1] / "audit-pr-nudge.py"

CREATE_CMD = "gh pr create --base main --head feat/x --title t --body b"
PR_URL = "https://github.com/innovation-upstream/devrc/pull/1542"


def run(payload):
    """Feed the hook a PostToolUse payload; return (rc, parsed-or-None, raw)."""
    p = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
    )
    out = p.stdout.strip()
    parsed = None
    if out:
        try:
            parsed = json.loads(out)
        except json.JSONDecodeError:
            parsed = None
    return p.returncode, parsed, out


def context(payload):
    """The injected text, or '' when the hook stayed silent."""
    _, parsed, _ = run(payload)
    if not parsed:
        return ""
    return parsed.get("hookSpecificOutput", {}).get("additionalContext", "")


def fired(cmd=CREATE_CMD, response=None):
    return {
        "tool_name": "Bash",
        "tool_input": {"command": cmd},
        "tool_response": response if response is not None else {"stdout": PR_URL},
    }


# --- 1. INVARIANT GUARD: it fires on a real creation, and names the PR --------
def test_it_fires_on_a_real_pr_creation_and_names_the_number():
    text = context(fired())
    assert text, "the hook stayed silent on a real `gh pr create` that printed a PR URL"
    assert "1542" in text, f"the nudge does not name the PR number: {text!r}"
    assert PR_URL in text, "the nudge does not carry the PR URL"


# --- 2. NEGATIVE CONTROL: can it stay silent? --------------------------------
# 🔴 This is the control that makes every other assertion here meaningful. A hook
# wired to print unconditionally would pass test 1 and test 3 while being useless.
def test_no_pr_url_in_the_output_means_silence():
    # The phrase is present in the COMMAND (a commit message can contain it — the
    # hook's own docstring records a real misfire of exactly this shape), but no
    # PR was created, so there is no URL and the hook must say nothing.
    assert context(fired(response={"stdout": "nothing was created here"})) == ""


def test_a_non_create_gh_command_is_ignored_even_with_a_pr_url_present():
    # `gh pr view` prints a PR URL too. The command gate must reject it.
    assert context(fired(cmd="gh pr view 1542 --json url", response={"stdout": PR_URL})) == ""


def test_a_pushs_create_a_pr_hint_does_not_fire():
    # GitHub's post-push hint is /pull/new/<branch> — no digits, must not match.
    hint = "remote: https://github.com/innovation-upstream/devrc/pull/new/feat/x"
    assert context(fired(response={"stdout": hint})) == ""


def test_a_non_bash_tool_is_ignored():
    assert context({"tool_name": "Read", "tool_input": {}, "tool_response": {}}) == ""


# --- 3. THE REGRESSION GUARD: round 0 must be ROUTED here --------------------
# 🔴 WATCHED RED at db7bf3ff, where the nudge named only `/audit-pr <n>`.
# Failure this prevents: a PR is created, the nudge recalls only the correctness
# audit, round 0 is never offered at the one moment its verdict can change the
# outcome, and it gets dispatched later — after the merge decision — which is the
# measured failure in 3 of 4 trials.
def _first_instruction(text):
    """The numbered step the reader is told to do FIRST, or '' if there is none.

    🔴 STRUCTURAL ON PURPOSE. An earlier version of this guard asserted only
    `"round 0" in text.lower()` and a mutation sweep SURVIVED it: stripping
    `ROUND 0 FIRST` out of step 1 left the tail caveat "Round 0 REPLACES
    nothing…" in place, so the phrase was still present while the routing was
    gone. A guard on a WORD is walkable by putting that word somewhere harmless;
    this one pins WHERE it has to appear.
    """
    for line in text.split("\n"):
        if line.lstrip().startswith("1."):
            return line
    return ""


def test_the_nudge_routes_round_0_IN_ITS_FIRST_INSTRUCTION():
    text = context(fired())
    step1 = _first_instruction(text)
    assert step1, f"the nudge has no numbered first step to route anything: {text!r}"
    assert "round 0" in step1.lower(), (
        "the nudge's FIRST instruction does not name round 0. This hook is the "
        "only thing that fires when a PR is born, and round 0's question (should "
        "this change EXIST) is only actionable before the merge decision — so a "
        "mention further down does not route it. Mutation-controlled: asserting "
        f"mere presence of the phrase SURVIVED a mutant. Step 1 was: {step1!r}"
    )


def test_the_nudge_says_round_0_comes_FIRST_not_merely_that_it_exists():
    # A mention is not routing. The reader has to learn the ORDER, because
    # spending the correctness axes on a change that should not exist is the
    # ordering failure round 0 exists to prevent.
    text = context(fired())
    low = text.lower()
    assert "first" in low, f"the nudge mentions round 0 but not that it comes first: {text!r}"
    # And round 0 must be named BEFORE the generic audit offer, in reading order.
    i_r0 = low.find("round 0")
    i_audit = low.find("/audit-pr")
    assert i_r0 != -1 and i_audit != -1
    assert i_r0 < i_audit or low.find("--round 0") < low.rfind("/audit-pr"), (
        "round 0 is mentioned after the plain audit offer, so a reader skimming "
        f"the first sentence still reaches for the correctness pass: {text!r}"
    )


def test_it_still_offers_the_full_correctness_audit():
    # Round 0 REPLACES nothing: it reports and cannot end a ladder. Dropping the
    # correctness offer while adding round 0 would be "wider on one axis,
    # narrower on another" — the shape the audit skill tells you to hunt for.
    text = context(fired())
    assert "/audit-pr" in text, f"the nudge no longer offers the audit at all: {text!r}"
    assert "1542" in text


# --- 4. INVARIANT GUARD: the hook never blocks -------------------------------
def test_it_exits_zero_on_every_input_including_garbage():
    # It is a nudge: it adds context and must never deny a tool call.
    for payload in (fired(), fired(response={"stdout": ""}), {"tool_name": "Bash"}):
        rc, _, _ = run(payload)
        assert rc == 0, f"the nudge exited {rc} — it must never block a tool call"
    p = subprocess.run(
        [sys.executable, str(HOOK)], input="not json at all",
        capture_output=True, text=True,
    )
    assert p.returncode == 0, "malformed stdin must not make the hook exit non-zero"


def test_tool_response_as_a_raw_string_is_handled():
    # The harness passes a dict or a bare string; both must work.
    assert "1542" in context(fired(response=f"created {PR_URL}"))
