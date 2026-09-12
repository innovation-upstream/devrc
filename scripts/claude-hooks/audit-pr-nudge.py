#!/usr/bin/env python3
"""PostToolUse nudge: when a PR is created via `gh pr create`, inject context so
Claude proactively OFFERS the pre-merge audit — ROUND 0 FIRST, then the nine
correctness axes — instead of waiting for the user to hand-type "dispatch a
subagent to audit this PR …".

Why this exists: a transcript audit found that exact request typed by hand ≥14x
across 6 sessions while the matching `/audit-pr` skill sat unused — recall at the
right moment was the gap, not the command. This fires at the moment a PR is born,
which is when the audit is most actionable. Deterministic (matches the literal
`gh pr create` command), non-blocking (it only adds context — never denies).

🔴 WHY THE MESSAGE NAMES ROUND 0, AND NAMES IT FIRST. Round 0 is the
requirements-and-deletion pass: the only round that can conclude *close this PR,
do not audit it*. Its question — should this change EXIST — is actionable only
while the merge decision is open, so a round 0 that arrives after the merge is
not a slow audit, it is a wasted one. MEASURED over the round-0 trial
(`claudedocs/handoff-audit-pr-ladder.md`, rank 1): `ran: 6 · changed the
outcome: 3`, and every zero was a post-decision dispatch — `#1523` merged 27 min
BEFORE its audit was dispatched, `#1510` merged 6 min after, `#1518` closed 5 min
before the report returned. Audit runtimes were 459/920/776 s, so no speedup
reaches any of them: the section worked whenever it arrived in time and the
ROUTING was the defect. THIS HOOK is the only thing in the system that fires when
a PR is born, so the fix belongs here and nowhere else — one rule, one place.
The message is pinned by `tests/test_audit_pr_nudge.py`, which was watched RED at
`db7bf3ff`; before that commit nothing read this text at all.

Coverage note: this catches PRs CREATED in-session. Auditing a pre-existing PR
(e.g. reviewing someone else's) is still a manual `/audit-pr <n>` — by design,
to avoid nagging on every `gh pr view`.
"""
import sys, json, re


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)
    if data.get("tool_name") != "Bash":
        sys.exit(0)
    cmd = (data.get("tool_input") or {}).get("command", "")
    # Sanity gate: the command must invoke `gh pr create` (not `gh pr view/list`).
    # NOTE: this can match the phrase inside a commit message / echo / grep pattern
    # (it once misfired on a commit whose message described this very hook), so it
    # is only a gate — the real trigger below is the PR URL in the OUTPUT.
    if not re.search(r"\bgh\s+pr\s+create\b", cmd):
        sys.exit(0)

    # The decisive signal: a real `gh pr create` prints the new PR URL
    # (.../pull/<number>) to stdout. A commit/echo that merely mentions the phrase
    # does not. Require that URL — no URL => no PR was actually created => stay
    # silent. (GitHub's `git push` "create a PR" hint uses /pull/new/<branch>, which
    # has no digits and won't match.) tool_response may be a dict or a raw string.
    resp = data.get("tool_response")
    text = ""
    if isinstance(resp, dict):
        text = " ".join(str(resp.get(k, "")) for k in ("stdout", "output", "stderr"))
    elif isinstance(resp, str):
        text = resp
    m = re.search(r"https://github\.com/[^\s]+/pull/(\d+)", text)
    if not m:
        sys.exit(0)
    target = f"PR #{m.group(1)} ({m.group(0)})"
    arg = m.group(1)

    nudge = (
        f"A PR was just created: {target}. Before moving on (and before merging), "
        f"proactively OFFER the pre-merge audit — in this order, because the order "
        f"is the mechanism:\n"
        f"1. 🔴 ROUND 0 FIRST — `/audit-pr {arg}`, worked as its ROUND 0 section "
        f"(requirements & deletion). It is the only round that can conclude *close "
        f"this PR, do not audit it*, and that question is actionable ONLY while the "
        f"merge decision is open. MEASURED: dispatched later instead of now, the "
        f"report landed AFTER the decision in 3 of 4 trials.\n"
        f"2. Then the nine correctness axes — `/audit-pr {arg}`.\n"
        f"Round 0 REPLACES nothing: it reports and cannot end a ladder. Don't "
        f"silently skip either; if now isn't the right moment, say so."
    )
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": nudge,
        }
    }))
    sys.exit(0)


main()
