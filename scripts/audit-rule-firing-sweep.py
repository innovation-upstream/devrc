#!/usr/bin/env python3
"""Has each rule in `claude/skills/audit-pr/SKILL.md` FIRED since it was written?

    scripts/audit-rule-firing-sweep.py                  # every rule, full corpus
    scripts/audit-rule-firing-sweep.py --rule prose-escape-hatch
    scripts/audit-rule-firing-sweep.py --samples 2      # quote the matching text
    scripts/audit-rule-firing-sweep.py --json out.json

WHY THIS EXISTS
---------------
`claudedocs/handoff-audit-pr-ladder.md` rank 14: most rules in that skill cite the
PR that bought them, and **origin is not evidence of continued value**. Two rules
had been verified as firing again (the prose escape hatch, the attribution gate);
for every other rule "has it fired since" was UNMEASURED, and the audit that found
that said so rather than implying coverage. `/adoption-scan` does not take a prose
file as a target, so this is the instrument or there is none.

🔴 WHAT IT MEASURES IS *APPLIED*, NOT *CAUGHT* — and those are different claims.
A rule is counted FIRED when an assistant-authored message applies it. Whether the
application CAUGHT a defect needs the finding read, so the sweep reports a second,
narrower count — `in-finding`, a match sitting within PROXIMITY chars of a severity
marker — and prints samples so a human closes the gap. Do not quote `fired` as
"the rule caught something".

🔴 THE NAIVE SWEEP IS A FALSE INSTRUMENT, MEASURED. Grepping the corpus for a
rule's own wording counts SKILL LOADS, not firings: the skill body and the
dispatched brief are injected into every transcript that uses them, so
`payload lines changed THIS round` matched **1,022 transcript files** while only
the assistant-authored subset (83 of 340 blocks in the first 120 files) was a rule
being applied. The separation is PROVENANCE, and it is deterministic — not a
similarity heuristic:

    assistant text block            -> the model APPLYING the rule        = SIGNAL
    tool_result of Read/Skill/Bash  -> the skill body or the brief        = noise
    user-role text                  -> the dispatch prompt quoting it     = noise

`tool_use_id` -> tool name is resolved per file, so the classification is read off
the transcript rather than guessed from the text.

BLIND SPOTS, so no count is read wider than it is
-------------------------------------------------
1. **Origin is an INTERVAL, not a date, and matches inside it are reported as
   UNATTRIBUTABLE rather than as firings.** `early` = the earliest version carrying the
   rule's SECTION HEADING; `late` = the earliest carrying its own SENTENCE. Both
   single-bound versions were MEASURED WRONG, in opposite directions: `wording` alone
   dated 44 of 49 rules into 2026-08/09 (the reword, not the rule) and withheld 32 of 49
   rows; `section` alone recovered 13 of those but sent TWO the other way, because
   HEADINGS GET REWORDED TOO so a section is not reliably older than its own contents.
   The interval fixed 13 with 0 regressions — but it is a disclosure, not a resolution:
   `ambig` can be large (263 for `round0-first`), and those matches are genuinely
   unattributable. ⚠ Neither bound is the rule's cited origin INCIDENT, which only the
   rule's own prose names and nothing here parses.
   ⚠ **The `AMBIGUOUS` verdict is pinned by tests but has never fired on the real
   corpus** — it needs `fired == 0` while `ambig > 0`, and every real rule with
   in-interval matches also has later ones. Treat it as an untested-in-production branch.
2. **A meta-session confounder is reported, not removed.** Sessions editing this
   skill discuss its rules in assistant text; those are not firings. The report
   splits every count by project dir and prints `devrc` separately, because a firing
   in another repo's project dir cannot be the skill being edited.
3. **A pattern matching before its rule existed is NOT reported as a count.** The
   pre-origin hit count is the specificity control: non-zero means the pattern
   matched language that predates the rule, so the rule's row is marked UNRELIABLE
   and its number withheld. A withheld row is not a zero.
4. **Silence is not deadness.** A rule nobody has violated since does not fire. The
   report says UNFIRED, never "dead".
5. Claude Code only. opencode audits are outside the corpus and uncounted.

SELF-CHECKS (it refuses to print a verdict unless both pass)
------------------------------------------------------------
* POSITIVE control — a phrase that MUST appear in the injected class (the skill
  heading). Zero there means the walk is wired to nothing, and the run exits 3.
* NEGATIVE control — a sentinel that MUST appear nowhere. Non-zero means the
  matcher is matching anything, and the run exits 3.

Exit codes: 0 report printed · 3 a control failed · 4 no transcripts walked ·
5 the rule ledger disagrees with SKILL.md (a probe no longer present).
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SKILL_REL = "claude/skills/audit-pr/SKILL.md"
CORPUS = Path(os.environ.get("AUDIT_SWEEP_CORPUS",
                             Path.home() / ".claude" / "projects"))

# Chars either side of a match that are searched for a severity marker before the
# match is also counted as `in-finding`.
PROXIMITY = 600
SEVERITY = re.compile(r"🔴|🟡|🟢|\bfinding\s*\d|\bF\d\b|\bseverity\b", re.I)

POS_CONTROL = ("skill-heading", r"/audit-pr — adversarial PR audit")
NEG_CONTROL = ("sentinel", r"zqxj-audit-sweep-negative-control-7f3a")

# ---------------------------------------------------------------------------
# THE RULE LEDGER.
# `probe` is verbatim text that must still be in SKILL.md — it dates the rule and
# is pinned two-way by scripts/tests/test_audit_rule_firing_sweep.py, so a reworded
# rule fails the suite instead of silently dating itself to the wrong commit.
# `apply` is what an assistant writes when APPLYING the rule, which is deliberately
# not always the rule's own wording.
# ---------------------------------------------------------------------------
RULES: list[dict] = [
    dict(id="brief-assembler", name="assemble the brief with audit-dispatch.py",
         probe="Assemble the brief with", apply=r"audit-dispatch\.py"),
    dict(id="delta-no-block-refused", name="a delta round with no parseable block is REFUSED",
         probe="A delta round with no parseable block is REFUSED",
         apply=r"no parseable\b[^.]{0,40}block|REFUS\w+[^.]{0,60}claims block|claims block[^.]{0,40}REFUS"),
    dict(id="missing-intermediate-block", name="a MISSING INTERMEDIATE block silently widens the range",
         probe="does NOT cover a MISSING INTERMEDIATE one",
         apply=r"missing intermediate|silently WIDEN|the newest claims block says round="),
    dict(id="review-comments-invisible", name="gh pr view --json comments omits REVIEW comments",
         probe="does NOT return REVIEW comments",
         apply=r"not return REVIEW comments|review comments? (are|is)[^.]{0,40}invisible|post the block as an ISSUE comment"),
    dict(id="emit-claims-audited", name="post the block with --emit-claims --audited <tip>",
         probe="--emit-claims --audited", apply=r"--emit-claims|--audited\b"),
    dict(id="round0-first", name="run --round 0 first",
         probe="RUN `--round 0` FIRST", apply=r"--round 0\b|\bround 0\b|round-0\b"),
    dict(id="round0-at-pr-create", name="run round 0 at PR-CREATE time",
         probe="Run it at PR-CREATE time",
         apply=r"at PR-CREATE time|audit-pr-nudge"),
    dict(id="high-yield-classes", name="always run on high-yield change-classes",
         probe="high-yield change-classes", apply=r"high-yield change.class"),
    dict(id="invariant-clauses", name="the brief carries the environment/cleanup invariant clauses",
         probe="cold-checkout-is-not-the-diff",
         apply=r"cold-checkout-is-not-the-diff|own-what-you-spawn"),
    dict(id="round0-author-of-record", name="name each requirement's AUTHOR OF RECORD",
         probe="author of record", apply=r"author of record|unattributed requirement"),
    dict(id="round0-prior-round-scrutiny", name="a requirement authored by a prior round is highest-scrutiny",
         probe="highest-scrutiny class",
         apply=r"highest.scrutiny|requirements from smart people"),
    dict(id="round0-delete-pass", name="round 0 step 2 — the deletion pass (running? ever caught? already checked?)",
         probe="is it RUNNING",
         apply=r"deletion candidate|is it RUNNING\b|ever caught a real problem"),
    dict(id="round0-not-revert-test", name="the deletion pass is NOT the revert test",
         probe="NOT the REVERT TEST below",
         apply=r"NOT the REVERT TEST|revert test[^.]{0,80}wrong here"),
    dict(id="round0-simplify-is-operators", name="step 3 names /simplify + /code-review, for the OPERATOR to run",
         probe="Owned by `/simplify` and `/code-review`",
         apply=r"/simplify\b"),
    dict(id="round0-no-accelerate", name="do not accelerate or automate what steps 1-2 have not cleared",
         probe="Do not accelerate or automate",
         apply=r"accelerate or automate"),
    dict(id="round0-reports-only", name="ROUND 0 REPORTS; it does not move the ladder",
         probe="ROUND 0 REPORTS; IT DOES NOT MOVE THE LADDER",
         apply=r"round 0 reports|not a finding for the[^.]{0,40}stop rule|round 0[^.]{0,60}cannot end a ladder"),
    dict(id="round0-ledger", name="the round-0 ledger line",
         probe="requirements: N (unattributed: U)",
         apply=r"round 0 · requirements|requirements: \d+ \(unattributed"),
    dict(id="nine-axes", name="the nine correctness axes",
         probe="Second-order consequences", apply=r"second.order consequence"),
    dict(id="delta-vs-audited-tip", name="re-audit the DELTA against the previously-audited tip",
         probe="previously-audited tip",
         apply=r"previously.audited tip|delta re.audit"),
    dict(id="per-prior-finding-status", name="state per prior finding: fixed / partial / not / made worse",
         probe="made worse", apply=r"made worse"),
    dict(id="behaviour-or-guard-label", name="label every finding behaviour or guard",
         probe="label every finding `behaviour` or `guard`",
         apply=r"`(behaviour|guard)`[^a-z]{0,4}(·|,|$)|label:?\s*`?(behaviour|guard)`?"),
    dict(id="round-ledger-line", name="carry the payload ledger in every round's summary",
         probe="payload lines changed THIS round",
         apply=r"payload lines changed THIS round"),
    dict(id="fix-prose-is-next-finding", name="the fix round's own PROSE is the likeliest next finding",
         probe="THE FIX ROUND'S OWN PROSE IS THE LIKELIEST NEXT FINDING",
         apply=r"fix round.s own prose|claim the previous round[^.]{0,60}(wrote|written)"),
    dict(id="guard-lost-its-reason", name="if a guard has lost its reason, write that it has NONE",
         probe="WRITE THAT IT HAS NONE",
         apply=r"has none|nothing justifies this|lost its reason"),
    dict(id="names-own-missing-variable", name="a sentence that names its own missing variable then asserts a value",
         probe="NAMES its own missing variable",
         apply=r"names its own missing variable|delete the comparative"),
    dict(id="sweep-every-claim", name="sweep every claim in the commit the way you swept the hardest one",
         probe="Sweep every claim in the commit",
         apply=r"sweep every claim"),
    dict(id="sweep-human-surface-first", name="sweep the surface a HUMAN reads first",
         probe="Sweep the surface a HUMAN reads FIRST",
         apply=r"surface a human reads"),
    dict(id="count-in-prose-is-a-claim", name="a count in prose is a claim",
         probe="A count in prose is a claim",
         apply=r"count in prose is a claim"),
    dict(id="clean-round-ends-ladder", name="a clean round ENDS the ladder",
         probe="A clean round ENDS the ladder",
         apply=r"clean round (ends|is the (last|stop))|a clean round[^.]{0,40}stop"),
    dict(id="nits-are-a-stopping-round", name="a round of nits that change nothing is a stopping round",
         probe="nit-is-not-a-finding",
         apply=r"nit-is-not-a-finding|nits? that change nothing"),
    dict(id="severity-cannot-end-ladder", name="no SEVERITY class ends a ladder; deploy-blocking-only was rejected",
         probe="deploy-blocking only", apply=r"deploy-blocking only"),
    dict(id="verdict-is-not-the-stop", name="a 'safe to merge' verdict is not the stop signal",
         probe="is not the stop signal",
         apply=r"not the stop signal|verdict[^.]{0,50}advisory"),
    dict(id="not-a-round-cap", name="this is NOT a round cap",
         probe="This is NOT a round cap", apply=r"not a (round )?cap\b"),
    dict(id="fix-the-form-not-the-number", name="when a fix is renumbering your own prose, fix the FORM",
         probe="fix the FORM, not the number",
         apply=r"fix the form, not the number"),
    dict(id="say-stop-rule-to-reauditor", name="say the stop rule to the re-auditor",
         probe="Say the stop rule to the re-auditor", apply=r"do not invent findings"),
    dict(id="dispatch-blind", name="a framed audit verifies the frame — dispatch the next one BLIND",
         probe="A FRAMED AUDIT VERIFIES THE FRAME",
         apply=r"framed audit|dispatch\w*[^.]{0,30}blind|blind (re.)?audit"),
    dict(id="frame-includes-should-exist", name="the frame includes whether the work should EXIST",
         probe="WHETHER THE WORK SHOULD EXIST AT ALL",
         apply=r"should (this|the) (change|work) exist|state the PREMISE"),
    dict(id="attribution-gate", name="a round that changes no PAYLOAD is auditing the LADDER",
         probe="auditing the LADDER, not the PR",
         apply=r"auditing the ladder|attribution gate|zero payload"),
    dict(id="payload-not-extension", name="the unit is THIS PR's payload, never a file extension",
         probe="never a file extension",
         apply=r"never a file extension|ambiguous is not zero"),
    dict(id="decide-once-revert-test", name="decide payload/scaffolding ONCE at round 1 — the REVERT TEST",
         probe="REVERT TEST", apply=r"revert test"),
    dict(id="one-number-one-name", name="ONE NUMBER, ONE NAME",
         probe="ONE NUMBER, ONE NAME", apply=r"one number, one name"),
    dict(id="remerge-diff-flags", name="the range form: --not <base> and --remerge-diff",
         probe="--remerge-diff", apply=r"--remerge-diff"),
    dict(id="base-is-current-tip", name="<base> is the current tip — fetch it, and earn the zero",
         probe="not a zero", apply=r"did not watch the command EARN|a zero you did not"),
    dict(id="stale-claim-out-of-range", name="a delta round cannot see a claim the PR's own earlier commit staled",
         probe="re-derive every COUNT, VERSION and CROSS-REFERENCE",
         apply=r"re-derive every count|earlier commit staled|outside (every|no) round.s range"),
    dict(id="prose-escape-hatch", name="when the payload is prose the gate cannot fire — stop on a stated criterion",
         probe="WHEN THE PAYLOAD IS PROSE THE GATE CANNOT FIRE",
         apply=r"payload is prose|stated criterion|structurally inert"),
    dict(id="mutation-deletion-easy-half", name="deletion-mutants are the EASY half",
         probe="deletion-mutants are the EASY half", apply=r"deletion.mutant"),
    dict(id="pin-whole-normalised-statement", name="when you can only assert on TEXT, pin the WHOLE normalised statement",
         probe="pin the WHOLE normalised statement",
         apply=r"whole normalised (statement|string)"),
    dict(id="price-from-consuming-code", name="price a defect from the CONSUMING code",
         probe="Price a defect from the CONSUMING code",
         apply=r"consuming code|what its absence costs"),
    dict(id="pr-description-corrected-publicly", name="a finding about the PR description gets corrected PUBLICLY",
         probe="gets corrected PUBLICLY",
         apply=r"corrected publicly|misstates what the change does"),
]


# ---------------------------------------------------------------------------
def fail(code: int, msg: str) -> None:
    print(f"\nREFUSED: {msg}", file=sys.stderr)
    sys.exit(code)


def skill_path() -> Path:
    """The skill being measured. `AUDIT_SWEEP_SKILL` exists so the ledger gate and
    the controls can be watched RED against a copy — a gate nothing can aim at a
    broken input is a gate nobody has seen fail."""
    override = os.environ.get("AUDIT_SWEEP_SKILL")
    return Path(override) if override else REPO / SKILL_REL


def check_ledger_against_skill() -> str:
    skill = skill_path()
    if not skill.exists():
        fail(5, f"{SKILL_REL} not found under {REPO}")
    body = skill.read_text(errors="replace")
    missing = [r["id"] for r in RULES if r["probe"] not in body]
    if missing:
        fail(5, "these rules' `probe` text is no longer in SKILL.md, so they cannot "
                "be dated — reword the probe in the same commit that reworded the "
                "rule: " + ", ".join(missing))
    return body


def parse_ts(raw: str):
    """ISO-8601 -> aware datetime, or None.

    🔴 NOT a string compare. Transcript stamps end in `Z`; `git log %aI` carries a
    numeric offset, so `"2026-09-11T04:11:19.123Z" >= "2026-09-11T04:11:19-05:00"`
    is lexicographic nonsense and would mis-sort every rule's origin window.
    """
    if not raw:
        return None
    s = raw.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def skill_versions() -> list[tuple[str, str, str]]:
    """(sha, iso, content) for every commit that touched the skill, OLDEST FIRST.

    🔴 THIS REPLACES `git log -S<probe>`, WHICH DATED THE REWORD RATHER THAN THE RULE.
    MEASURED on the sweep's first real run: 44 of 49 origins landed in 2026-08/09 because
    the skill reworded nearly every rule inside six weeks, so ordinary earlier use fell
    before the window and tripped the specificity control — **32 of 49 rows withheld**, and
    the sweep could not answer its own question for two-thirds of its ledger.

    Scanning every version is both cheaper and more exact than one `-S` call per rule: ~24
    `git show`s total against 49 subprocesses, and it yields the earliest version that
    CONTAINS the text rather than the commit that changed its count. `--follow` is required
    — this file lived under the retired commands tree until the commands/skills merge, and
    a path-pinned log silently stops at the rename.

    🔴 `--reverse` IS NOT USED, AND MUST NOT BE: `git log --follow --reverse` returns
    **ONE** commit, silently. MEASURED on this file — `--follow` alone 23, `--follow
    --reverse` 1 (with or without `--name-only`), `--reverse` alone 19 (it misses the
    pre-rename history). The 1-commit scan did not look broken: it dated every rule whose
    section existed in that single old version and called the rest UNDATED, which reads
    exactly like a working dater with some gaps. So the order is reversed in PYTHON, and
    `_assert_scan_reaches_current` refuses rather than degrading quietly.
    """
    cmd = ["git", "-C", str(REPO), "log", "--follow",
           "--format=%x01%H\t%aI", "--name-only", "--", SKILL_REL]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    except Exception:
        return []
    versions: list[tuple[str, str, str]] = []
    for chunk in out.stdout.split("\x01"):
        lines = [l for l in chunk.splitlines() if l.strip()]
        if not lines:
            continue
        try:
            sha, iso = lines[0].split("\t", 1)
        except ValueError:
            continue
        paths = [l for l in lines[1:] if l.endswith(".md")]
        for p in paths:
            try:
                show = subprocess.run(["git", "-C", str(REPO), "show", f"{sha}:{p}"],
                                      capture_output=True, text=True, timeout=60)
            except Exception:
                continue
            if show.returncode == 0 and show.stdout:
                versions.append((sha[:8], iso.strip(), show.stdout))
                break
    versions.reverse()          # git gave newest-first; callers want OLDEST first
    return versions


def scan_reaches_current(versions) -> str | None:
    """Why this scan cannot be trusted, or None if it can.

    🔴 THE CONTROL THAT CATCHES A TRUNCATED HISTORY. A scan that reached only some
    versions produces a report that looks ordinary: rules whose section existed in the
    versions it DID reach get dated, and every other rule reads `UNDATED`. Nothing in the
    output says the scan was short. This caught exactly that — a `--reverse` that silently
    returned 1 of 23 versions — and it is a cheap, total check: the NEWEST version scanned
    must be the skill as it is now, so it must contain every ledger probe.
    """
    if not versions:
        return "no versions scanned at all"
    newest = versions[-1][2]
    if len(versions) < 2:
        return (f"only {len(versions)} version scanned — a one-version history cannot "
                "date anything relative to anything")
    missing = [r["id"] for r in RULES if r["probe"] not in newest]
    if missing:
        return ("the NEWEST version scanned is not the current skill — it is missing "
                f"{len(missing)} ledger probe(s) ({', '.join(missing[:4])}"
                f"{'…' if len(missing) > 4 else ''}). The history scan is TRUNCATED, so "
                "every UNDATED row would be an artefact of the scan, not a fact about "
                "the rule.")
    return None


def section_heading_for(probe: str, body: str) -> str | None:
    """The `## ` heading of the section the probe sits in, in the CURRENT skill.

    A rule's sentence gets reworded; the section it lives under is far more stable, so
    this is what makes the origin survive an edit.
    """
    idx = body.find(probe)
    if idx < 0:
        return None
    head = None
    for m in re.finditer(r"^##+ .*$", body[:idx], re.M):
        head = m.group(0).strip()
    return head


def origin_of(probe: str, versions=None, body: str = "") -> tuple:
    """(sha, iso, basis) for a rule's origin — the earliest version that carries it.

    Two bases, and the report prints which was used, because they answer subtly
    different questions and one of them can over-count:
      `section`  the earliest version containing the rule's SECTION HEADING. Preferred:
                 it survives a reword of the rule's own sentence. ⚠ It can be EARLIER
                 than the rule itself when a rule was added to an existing section, so it
                 widens the window — the opposite error to the one it fixes, and disclosed
                 rather than hidden.
      `wording`  the earliest version containing the probe itself. Used when no section
                 heading can be resolved.
    """
    forced = os.environ.get("AUDIT_SWEEP_ORIGIN")
    if forced:
        # Test/what-if hook: date every rule at one instant, so the pre-origin
        # withholding path can be watched work. Printed in the header when set.
        return ("forced", forced, "forced")
    if os.environ.get("AUDIT_SWEEP_SKILL"):
        # A copied skill has no history of its own; dating it against the real
        # file would attribute a fixture's rules to real commits. Say UNDATED.
        return (None, None, "undatable")
    versions = versions if versions is not None else skill_versions()
    if not versions:
        return (None, None, "undatable")
    heading = section_heading_for(probe, body) if body else None
    for needle, basis in ((heading, "section"), (probe, "wording")):
        if not needle:
            continue
        for sha, iso, content in versions:          # oldest first
            if needle in content:
                return (sha, iso, basis)
    return (None, None, "undatable")


def origin_bounds(probe: str, versions, body: str) -> tuple:
    """(early_iso, late_iso) — the rule's origin is somewhere BETWEEN these.

    🔴 NEITHER BOUND IS THE ORIGIN, AND PICKING ONE IS HOW THIS WENT WRONG TWICE.
      `late`  = earliest version containing the rule's own SENTENCE. Too LATE whenever the
                rule was reworded: MEASURED, that dated 44 of 49 rules into 2026-08/09 and
                withheld 32 of 49 rows.
      `early` = earliest version containing the rule's SECTION HEADING. Usually earlier —
                but NOT reliably, because HEADINGS GET REWORDED TOO. MEASURED on the
                section basis alone: 13 rules recovered, and **2 went the other way**
                (`dispatch-blind` 0 -> 437 pre-origin hits, `nine-axes` 0 -> 29) because
                their current heading is NEWER than their sentence. It is also too EARLY
                for a rule added to an already-old section.

    So the window is reported as an INTERVAL and the ambiguity is surfaced rather than
    resolved by fiat: applications after `late` are unambiguous; applications between the
    two cannot be attributed to the rule rather than to language that predates it; and
    only applications before `early` — before even the section existed — are evidence the
    pattern is not specific to the rule.
    """
    forced = os.environ.get("AUDIT_SWEEP_ORIGIN")
    if forced:
        # `AUDIT_SWEEP_ORIGIN_LATE` makes the INTERVAL itself settable, so the
        # three-bucket logic is testable without a git history — the sandbox tier has
        # none, and tests that reached for the real history were red there. Defaults to
        # a degenerate interval so the single-value hook keeps its old meaning.
        return (forced, os.environ.get("AUDIT_SWEEP_ORIGIN_LATE") or forced)
    if os.environ.get("AUDIT_SWEEP_SKILL") or not versions:
        return (None, None)
    heading = section_heading_for(probe, body) if body else None

    def first_with(needle):
        if not needle:
            return None
        for _sha, iso, content in versions:         # oldest first
            if needle in content:
                return iso
        return None

    sec, word = first_with(heading), first_with(probe)
    cands = [c for c in (sec, word) if c]
    if not cands:
        return (None, None)
    dated = [(parse_ts(c), c) for c in cands]
    dated = [(d, c) for d, c in dated if d is not None]
    if not dated:
        return (None, None)
    dated.sort(key=lambda pair: pair[0])
    return (dated[0][1], dated[-1][1])


def iter_corpus() -> list[Path]:
    """BOTH tiers, deliberately.

    `scripts/lib/transcript_search.py` EXCLUDES `subagents/` because a subagent is
    not a resumable session — correct for that question and wrong for this one: an
    auditor IS a subagent, so its own transcript is where the rule gets applied.
    """
    if not CORPUS.is_dir():
        fail(4, f"corpus {CORPUS} is not a directory")
    return sorted(CORPUS.rglob("*.jsonl"))


def ascii_tolerant(pat: str) -> str:
    """Widen every non-ASCII char to `.{1,6}`.

    🔴 THE PREFILTER READS RAW JSON; THE MATCHER READS DECODED TEXT. A writer using
    `ensure_ascii` stores `—` as the six characters `\\u2014`, so a prefilter pattern
    containing the literal em-dash matches NOTHING and the file is dropped before it
    is ever parsed — a silent under-select that reads as "the rule never fired".
    Found by `test_the_prefilter_still_finds_an_ascii_escaped_transcript`, which was
    watched RED: four tests failed because the sweep's own POSITIVE control carries
    an em-dash. The prefilter only has to OVER-select, so widening is safe.
    """
    return "".join(c if ord(c) < 128 else ".{1,6}" for c in pat)


def prefilter(files: list[Path], patterns: list[str]) -> list[Path]:
    """One ripgrep pass to pick candidate files. --no-ignore because the corpus is
    outside git and this host's rg honours .gitignore."""
    alt = "|".join(f"(?:{ascii_tolerant(p)})" for p in patterns)
    cmd = ["rg", "-l", "--no-ignore", "--no-messages", "-i", "-e", alt, str(CORPUS)]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    except FileNotFoundError:
        return files          # no rg: parse everything
    hit = {Path(l) for l in out.stdout.splitlines() if l.strip()}
    return [f for f in files if f in hit]


def blocks_of(path: Path):
    """Yield (provenance, text, iso_ts) for every text-bearing block in a file."""
    recs = []
    try:
        with path.open(errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    recs.append(json.loads(line))
                except Exception:
                    continue
    except Exception:
        return
    toolname: dict[str, str] = {}
    for d in recs:
        m = d.get("message")
        if not isinstance(m, dict):
            continue
        c = m.get("content")
        if isinstance(c, list):
            for b in c:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    tid = b.get("id")
                    if isinstance(tid, str):
                        toolname[tid] = b.get("name") or "?"
    for d in recs:
        m = d.get("message")
        if not isinstance(m, dict):
            continue
        ts = d.get("timestamp") or ""
        role = m.get("role")
        c = m.get("content")
        blocks = c if isinstance(c, list) else (
            [{"type": "text", "text": c}] if isinstance(c, str) else [])
        for b in blocks:
            if not isinstance(b, dict):
                continue
            bt = b.get("type")
            if bt == "text":
                txt = b.get("text") or ""
                prov = "assistant" if role == "assistant" else "injected"
            elif bt == "tool_result":
                cc = b.get("content")
                txt = cc if isinstance(cc, str) else json.dumps(cc)
                ref = b.get("tool_use_id")
                tn = (toolname.get(ref) if isinstance(ref, str) else None) or "?"
                prov = "agent-result" if tn in ("Agent", "Task") else "injected"
            elif bt == "thinking":
                txt = b.get("thinking") or ""
                prov = "thinking"
            else:
                continue
            if txt:
                yield prov, txt, ts


def project_of(path: Path) -> str:
    try:
        return path.relative_to(CORPUS).parts[0]
    except Exception:
        return "?"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rule", action="append", default=[],
                    help="only this rule id (repeatable)")
    ap.add_argument("--samples", type=int, default=0,
                    help="quote N matching excerpts per fired rule")
    ap.add_argument("--json", dest="json_out", help="also write the rows as JSON")
    args = ap.parse_args()

    body = check_ledger_against_skill()
    rules = [r for r in RULES if not args.rule or r["id"] in args.rule]
    if not rules:
        fail(5, "no rule matched --rule")

    print(f"audit-rule-firing-sweep — run {datetime.now(timezone.utc).isoformat(timespec='seconds')}")
    print(f"corpus {CORPUS}   skill {skill_path()}")
    if os.environ.get("AUDIT_SWEEP_ORIGIN"):
        print("⚠ AUDIT_SWEEP_ORIGIN is set — every rule is dated at "
              f"{os.environ['AUDIT_SWEEP_ORIGIN']}, NOT at its own commit. "
              "This is a what-if run; do not quote it as a measurement.")

    # ONE scan of the skill's history, shared by every rule (see skill_versions).
    versions = ([] if os.environ.get("AUDIT_SWEEP_ORIGIN")
                or os.environ.get("AUDIT_SWEEP_SKILL") else skill_versions())
    if versions:
        print(f"skill history scanned: {len(versions)} version(s), "
              f"{versions[0][1][:10]} .. {versions[-1][1][:10]}")
        why = scan_reaches_current(versions)
        if why:
            fail(3, "the skill-history scan cannot be trusted: " + why)
    for r in rules:
        sha, iso, basis = origin_of(r["probe"], versions, body)
        early, late = origin_bounds(r["probe"], versions, body)
        r["origin_sha"], r["origin_iso"], r["origin_basis"] = sha, iso, basis
        r["origin_early"], r["origin_late"] = early, late
        r["early_dt"], r["late_dt"] = parse_ts(early or ""), parse_ts(late or "")
        r["origin_dt"] = parse_ts(iso or "")
        r["rx"] = re.compile(r["apply"], re.I)
    undated = [r["id"] for r in rules if not r["origin_iso"]]

    pos_rx = re.compile(POS_CONTROL[1], re.I)
    neg_rx = re.compile(NEG_CONTROL[1], re.I)

    files = iter_corpus()
    print(f"corpus files (both tiers): {len(files)}")
    cands = prefilter(files, [r["apply"] for r in rules] + [POS_CONTROL[1], NEG_CONTROL[1]])
    print(f"candidate files after prefilter: {len(cands)}")
    if not files:
        fail(4, "walked zero transcripts")

    # counters
    fired = collections.Counter()          # rule -> assistant/agent matches at-or-after origin
    in_find = collections.Counter()
    pre = collections.Counter()            # matches before the SECTION existed
    ambig = collections.Counter()          # matches INSIDE the origin interval
    ambig_sessions = collections.defaultdict(set)
    noise = collections.Counter()          # rule -> injected matches
    projects = collections.defaultdict(collections.Counter)
    # 🔴 BLOCKS are not SESSIONS. One verbose auditor restating a rule eight times in
    # its report is eight blocks and ONE application, so a block count rewards
    # verbosity. `sessions` is the unit to read; `fired` is kept beside it because the
    # two disagreeing is itself informative.
    sessions = collections.defaultdict(set)
    first_last = {}
    samples = collections.defaultdict(list)
    pos_injected = 0
    neg_hits = 0

    for f in cands:
        proj = project_of(f)
        for prov, txt, ts in blocks_of(f):
            low = txt
            tdt = parse_ts(ts)
            if pos_rx.search(low):
                pos_injected += 1
            if neg_rx.search(low):
                neg_hits += 1
            signal = prov in ("assistant", "agent-result")
            for r in rules:
                mo = r["rx"].search(low)
                if not mo:
                    continue
                if not signal:
                    noise[r["id"]] += 1
                    continue
                # THREE buckets against the origin INTERVAL, not two against a point.
                # `early` = the section existed; `late` = this wording existed.
                early, late = r["early_dt"], r["late_dt"]
                if tdt is None or early is None or late is None:
                    bucket = "fired"      # undated / unstamped: counted, flagged
                elif tdt < early:
                    bucket = "pre"        # before even the SECTION -> pattern too broad
                elif tdt < late:
                    bucket = "ambig"      # inside the interval -> unattributable
                else:
                    bucket = "fired"
                if bucket == "pre":
                    pre[r["id"]] += 1
                    continue
                if bucket == "ambig":
                    ambig[r["id"]] += 1
                    ambig_sessions[r["id"]].add(str(f))
                    continue
                fired[r["id"]] += 1
                projects[r["id"]][proj] += 1
                sessions[r["id"]].add(str(f))
                seg = low[max(0, mo.start() - PROXIMITY): mo.end() + PROXIMITY]
                if SEVERITY.search(seg):
                    in_find[r["id"]] += 1
                lo, hi = first_last.get(r["id"], (ts, ts))
                first_last[r["id"]] = (min(lo, ts) if ts else lo,
                                       max(hi, ts) if ts else hi)
                if len(samples[r["id"]]) < args.samples:
                    samples[r["id"]].append((proj, ts[:19],
                                             " ".join(seg.split())[:420]))

    # ---- controls ---------------------------------------------------------
    print("\nCONTROLS")
    print(f"  POSITIVE (skill heading seen in any class): {pos_injected}"
          f"   {'ok' if pos_injected else 'FAILED'}")
    print(f"  NEGATIVE (sentinel, must be 0):             {neg_hits}"
          f"   {'ok' if neg_hits == 0 else 'FAILED'}")
    if not pos_injected or neg_hits:
        fail(3, "a control failed — the sweep is a claim about the instrument only. "
                "Do not quote any row above.")

    # ---- report ----------------------------------------------------------
    rows = []
    print("\nPER-RULE — `fired` = assistant-authored applications at/after origin. "
          "NOT 'caught'.")
    print(f"{'rule':38} {'origin interval':22} {'sess':>5} {'fired':>6} "
          f"{'in-fnd':>6} {'ambig':>6} {'nondevrc':>8}  verdict")
    for r in sorted(rules, key=lambda x: -fired[x["id"]]):
        rid = r["id"]
        nondevrc = sum(v for k, v in projects[rid].items() if "devrc" not in k)
        if pre[rid]:
            verdict = (f"UNRELIABLE ({pre[rid]}x before the section existed — "
                       "pattern not specific to this rule)")
        elif not r["origin_iso"]:
            verdict = "UNDATED — origin not in git history"
        elif fired[rid] == 0 and ambig[rid]:
            verdict = (f"AMBIGUOUS ({ambig[rid]}x inside the origin interval, 0 after it "
                       "— cannot attribute to the rule vs earlier language)")
        elif fired[rid] == 0:
            verdict = "UNFIRED (not 'dead' — see blind spot 4)"
        elif nondevrc == 0:
            verdict = "FIRED — devrc only (meta-session confounder unresolved)"
        else:
            verdict = "FIRED"
        if pre[rid]:
            # 🔴 ACTUALLY withhold it. The verdict says the number is withheld, so
            # printing it anyway would make the label wider than the implementation
            # — the exact shape this skill's own rules tell an auditor to hunt.
            cols = f"{'—':>5} {'—':>6} {'—':>6} {'—':>6} {'—':>8}"
        else:
            cols = (f"{len(sessions[rid]):5d} {fired[rid]:6d} {in_find[rid]:6d} "
                    f"{ambig[rid]:6d} {nondevrc:8d}")
        e, l = (r['origin_early'] or '')[:10], (r['origin_late'] or '')[:10]
        interval = f"{e}..{l}" if e and l and e != l else (e or l or "?")
        print(f"{rid:38} {interval:22} {cols}  {verdict}")
        withheld = bool(pre[rid])
        rows.append(dict(id=rid, name=r["name"], origin_sha=r["origin_sha"],
                         origin=r["origin_iso"], origin_basis=r["origin_basis"],
                         origin_early=r["origin_early"], origin_late=r["origin_late"],
                         ambiguous=None if withheld else ambig[rid],
                         withheld=withheld,
                         sessions=None if withheld else len(sessions[rid]),
                         fired=None if withheld else fired[rid],
                         in_finding=None if withheld else in_find[rid],
                         non_devrc=None if withheld else nondevrc,
                         injected_noise=noise[rid], pre_origin=pre[rid],
                         projects=None if withheld else dict(projects[rid]),
                         verdict=verdict,
                         first_seen=None if withheld else first_last.get(rid, ("", ""))[0],
                         last_seen=None if withheld else first_last.get(rid, ("", ""))[1]))

    n_fired = sum(1 for x in rows if x["verdict"].startswith("FIRED"))
    n_unf = sum(1 for x in rows if x["verdict"].startswith("UNFIRED"))
    n_unrel = sum(1 for x in rows if x["verdict"].startswith("UNRELIABLE"))
    print(f"\nSUMMARY  rules={len(rows)}  FIRED={n_fired}  UNFIRED={n_unf}  "
          f"UNRELIABLE={n_unrel}  UNDATED={len(undated)}")
    print("🔴 `fired` counts APPLICATIONS, not catches. `in-fnd` is the narrower "
          "count — a match beside a severity marker — and is the column to read "
          "when the question is whether the rule caught anything.")
    if n_unrel:
        print("🔴 UNRELIABLE rows have NO number: the pattern matched text predating "
              "the rule, so it is not specific to it. Withheld is not zero.")

    if args.samples:
        print("\nSAMPLES")
        for r in rows:
            for proj, ts, seg in samples[r["id"]]:
                print(f"\n[{r['id']}] {proj} {ts}\n  ...{seg}...")

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(
            dict(run=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 corpus=str(CORPUS), files=len(files), candidates=len(cands),
                 rows=rows), indent=2))
        print(f"\nwrote {args.json_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
