#!/usr/bin/env python3
"""PostToolUse nudge: when a PR is created via `gh pr create`, inject context so
Claude proactively OFFERS the adversarial pre-merge audit (`/audit-pr <n>`) instead
of waiting for the user to hand-type "dispatch a subagent to audit this PR …".

Why this exists: a transcript audit found that exact request typed by hand ≥14x
across 6 sessions while the matching `/audit-pr` skill sat unused — recall at the
right moment was the gap, not the command. This fires at the moment a PR is born,
which is when the audit is most actionable. Deterministic (matches the literal
`gh pr create` command), non-blocking (it only adds context — never denies).

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
    # Match an actual PR-create invocation, not `gh pr view/list/diff/checkout`.
    if not re.search(r"\bgh\s+pr\s+create\b", cmd):
        sys.exit(0)

    # Best-effort: pull the PR URL/number from the command's stdout so the nudge
    # is specific. tool_response may be a dict ({stdout:...}) or a raw string.
    resp = data.get("tool_response")
    text = ""
    if isinstance(resp, dict):
        text = " ".join(str(resp.get(k, "")) for k in ("stdout", "output", "stderr"))
    elif isinstance(resp, str):
        text = resp
    m = re.search(r"https://github\.com/[^\s]+/pull/(\d+)", text)
    target = f"PR #{m.group(1)} ({m.group(0)})" if m else "the PR you just created"
    arg = m.group(1) if m else "<n>"

    nudge = (
        f"A PR was just created: {target}. Before moving on (and before merging), "
        f"proactively OFFER to run `/audit-pr {arg}` — the adversarial pre-merge "
        f"audit (bugs, regressions, race conditions, security, backward-compat, "
        f"second-order effects). Don't silently skip it; if now isn't the right "
        f"moment, say so. This is the reflexive substitute for hand-typing an audit request."
    )
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": nudge,
        }
    }))
    sys.exit(0)


main()
