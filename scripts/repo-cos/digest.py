#!/usr/bin/env python3
"""Digest formatting — one text renderer shared by --dry-run (stdout) and --email.

Keeping a single formatter means the email body and the dry-run output are byte-identical,
so validating signal quality on a dry-run genuinely validates what would be emailed.
"""
from __future__ import annotations

from datetime import date

EFFORT_LABEL = {"S": "small", "M": "medium", "L": "large"}

# ASCII-stable core of the digest subject. The full subject carries a leading emoji (🧭)
# and an em-dash (—), both non-ASCII and flaky in IMAP SEARCH — so feedback.py matches a
# reply against THIS fragment instead of the whole subject. Keep `subject()` embedding it
# verbatim (asserted in tests) so the reply-matcher stays in lockstep with what we send.
SUBJECT_CORE = "Repo proposals"


def subject(today: date | None = None) -> str:
    d = today or date.today()
    return f"🧭 {SUBJECT_CORE} — week of {d.isoformat()}"


def _excluded_footer(excluded_repos: list | None) -> str | None:
    """The digest footer line that surfaces the deterministic exclusion state so Zach can
    SEE what's paused and undo it. None when nothing is excluded."""
    names = [str(r).strip() for r in (excluded_repos or []) if str(r).strip()]
    if not names:
        return None
    return (f'Excluded (paused/not-yours): {", ".join(names)} — '
            'reply "resume <repo>" to re-enable.')


def _dismissed_footer(dismissed_count: int | None) -> str | None:
    """Terse footer line surfacing how many past proposals were dismissed (skipped) — the
    repos stay in scope, only those specific recommendations are suppressed. None when zero."""
    n = int(dismissed_count or 0)
    if n <= 0:
        return None
    return f"Dismissed {n} past proposal(s) (repos kept in scope)."


def render(proposals: list, *, today: date | None = None,
           candidate_count: int | None = None,
           approx_tokens: int | None = None,
           excluded_repos: list | None = None,
           dismissed_count: int | None = None) -> str:
    """Render proposals (llm.Proposal objects) into a compact skimmable digest.

    Empty proposal list is handled explicitly (honest 'nothing surfaced' message rather
    than a blank email). `excluded_repos` (deterministically dropped from the scan) is
    surfaced as a footer so Zach sees the state and can reply "resume <repo>" to undo it.
    `dismissed_count` (per-recommendation dismissals) is surfaced as a terse footer line."""
    d = today or date.today()
    excl_footer = _excluded_footer(excluded_repos)
    dism_footer = _dismissed_footer(dismissed_count)
    lines: list[str] = [subject(d), ""]
    if candidate_count is not None:
        meta = f"From {candidate_count} deterministic signal(s)"
        if approx_tokens is not None:
            meta += f" · ~{approx_tokens} prompt tokens"
        if excluded_repos:
            n = len([r for r in excluded_repos if str(r).strip()])
            if n:
                meta += f" · {n} repo(s) excluded"
        lines.append(meta)
        lines.append("")

    if not proposals:
        lines.append("No bounded, evidence-backed proposals surfaced this run.")
        lines.append("(That is a valid outcome — the bar is deliberately high.)")
        if excl_footer or dism_footer:
            lines.append("")
            if excl_footer:
                lines.append(excl_footer)
            if dism_footer:
                lines.append(dism_footer)
        return "\n".join(lines)

    for i, p in enumerate(proposals, 1):
        ci = "✅ CI-verifiable" if p.ci_verifiable else "○ needs judgement"
        eff = EFFORT_LABEL.get(p.effort, p.effort)
        lines.append(f"{i}. {p.title}  [{p.repo}]")
        lines.append(f"   why:      {p.why}")
        lines.append(f"   effort:   {eff}   {ci}")
        if p.approach:
            lines.append(f"   approach: {p.approach}")
        lines.append("   evidence:")
        for ref in p.evidence:
            lines.append(f"     - {ref}")
        lines.append("")

    if excl_footer or dism_footer:
        if excl_footer:
            lines.append(excl_footer)
        if dism_footer:
            lines.append(dism_footer)
        lines.append("")

    lines.append("—")
    lines.append("repo-cos v0 · deterministic pre-scan → LLM synthesis · reply to steer.")
    return "\n".join(lines).rstrip() + "\n"
