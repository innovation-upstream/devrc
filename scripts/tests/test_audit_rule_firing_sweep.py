#!/usr/bin/env python3
"""Pins `scripts/audit-rule-firing-sweep.py` — the rank-14 instrument that asks, per
rule in `claude/skills/audit-pr/SKILL.md`, whether it has FIRED since it was written.

🔴 THIS FILE OWNS THE LEDGER'S REVERSE DIRECTION AND THE EXEMPTION LIST. The sweep
checks one direction itself (every `probe` is still in the skill, so a reworded rule
cannot silently date itself to the wrong commit). The other direction — *every rule
in the skill has a ledger entry* — is checked here, because a rule is not a
machine-visible unit in prose and the naive version of this gate is exactly the
similarity heuristic the operator's standing rule forbids.

What is mechanical: a 🔴 PARAGRAPH. Every blank-line-separated paragraph carrying a
🔴 must either contain a ledger probe, or be enumerated below as not-a-rule with its
reason. So adding a new 🔴 rule to the skill without a ledger entry fails this suite;
it cannot be covered by accident. The exemption list is an ENUMERATION, not a
pattern — an unrecognised 🔴 paragraph is a failure by default — and it is
deliberately not env-overridable.

Measured when this landed: 70 paragraphs, 30 carrying 🔴, 22 covered by a probe and
8 enumerated. A strict no-exemptions version of this gate would have been RED on the
day it landed, which `claude/RULES.md` forbids outright (a permanently-red gate
trains everyone to click through).
"""
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
SCRIPT = REPO / "scripts" / "audit-rule-firing-sweep.py"
SKILL = REPO / "claude" / "skills" / "audit-pr" / "SKILL.md"

# 🔴-bearing paragraphs that are NOT rules this sweep can measure. Each is keyed by a
# short distinctive anchor and carries its reason. A reword of one of these fails this
# test — that is the cost of the enumeration, and the failure message says what to do.
NOT_A_RULE = {
    "A number → that GitHub PR":
        "the skill's TARGET spec — how to resolve $ARGUMENTS, not an audit rule",
    "TRIAL RECORD — CLOSED":
        "a closed trial's record. Rank 1 closed it and the handoff says do not "
        "re-open it; a record is not a rule that can fire",
    "LOAD-BEARING HEADING, NOT NAVIGATION":
        "an HTML comment to the skill's editors, pinned already by "
        "test_the_round_zero_section_the_script_reads_is_the_one_the_skill_ships",
    "The delta bullet above says to hunt regressions":
        "the prose lead-in to fix-prose-is-next-finding, whose probe sits in the "
        "heading paragraph above it",
    "A DELTA ROUND CANNOT SEE A CLAIM":
        "the section heading for stale-claim-out-of-range; the probe sits in the "
        "paragraph carrying the actionable instruction",
    "Stop on this — but ONLY once you can NAME":
        "the criteria paragraph of prose-escape-hatch, whose probe is in its heading",
    "WRITING IT DOWN IS THE WHOLE POINT":
        "the history of one reword of prose-escape-hatch. The skill itself says this "
        "paragraph is pinned by nothing and may be edited freely",
    "Findings by severity (🔴 deploy-blocking":
        "the OUTPUT FORMAT. The 🔴 here is a severity glyph being defined, not a rule",
}


def load_module():
    spec = importlib.util.spec_from_file_location("sweep", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def mod():
    return load_module()


# --------------------------------------------------------------- ledger, two ways
def test_every_ledger_probe_is_still_verbatim_in_the_skill(mod):
    body = SKILL.read_text()
    missing = [r["id"] for r in mod.RULES if r["probe"] not in body]
    assert not missing, (
        "these ledger probes are no longer in SKILL.md, so those rules would be "
        f"dated at the wrong commit (or not at all): {missing}. Reword the probe in "
        "the SAME commit that reworded the rule.")


def test_rule_ids_and_probes_are_unique(mod):
    ids = [r["id"] for r in mod.RULES]
    assert len(ids) == len(set(ids)), "duplicate rule id in the ledger"
    probes = [r["probe"] for r in mod.RULES]
    dupes = {p for p in probes if probes.count(p) > 1}
    assert not dupes, f"two rules share a probe, so they cannot be dated apart: {dupes}"


def test_every_rule_apply_pattern_compiles_and_is_not_trivially_wide(mod):
    for r in mod.RULES:
        rx = re.compile(r["apply"], re.I)
        # A pattern that matches the empty string matches every block ever written.
        assert not rx.search(""), f"{r['id']}: apply pattern matches empty text"
        assert len(r["apply"]) >= 6, f"{r['id']}: apply pattern is suspiciously short"


def test_every_red_paragraph_is_a_ledger_rule_or_enumerated_as_not_one(mod):
    """The REVERSE direction: a new 🔴 rule with no ledger entry fails here."""
    paras = re.split(r"\n\s*\n", SKILL.read_text())
    probes = [r["probe"] for r in mod.RULES]
    uncovered = []
    for p in paras:
        if "🔴" not in p:
            continue
        if any(pr in p for pr in probes):
            continue
        if any(anchor in p for anchor in NOT_A_RULE):
            continue
        uncovered.append(" ".join(p.split())[:140])
    assert not uncovered, (
        "these 🔴 paragraphs in SKILL.md are neither covered by a ledger probe nor "
        "enumerated as not-a-rule in NOT_A_RULE, so the sweep would silently report "
        "no measurement for them:\n  " + "\n  ".join(uncovered) +
        "\n\nEither add a RULES entry (id/name/probe/apply) to "
        "scripts/audit-rule-firing-sweep.py, or add an anchor to NOT_A_RULE here "
        "with the reason it cannot fire.")


def test_the_exemption_list_is_not_stale():
    """Every NOT_A_RULE anchor must still match a 🔴 paragraph. An anchor matching
    nothing is dead weight that makes the enumeration read wider than it is."""
    paras = [p for p in re.split(r"\n\s*\n", SKILL.read_text()) if "🔴" in p]
    dead = [a for a in NOT_A_RULE if not any(a in p for p in paras)]
    assert not dead, (
        f"these NOT_A_RULE anchors match no 🔴 paragraph any more: {dead}. Remove "
        "them, or re-anchor them on the reworded paragraph.")


# ------------------------------------------------------- the timestamp bug, pinned
def test_parse_ts_orders_a_Z_stamp_against_an_offset_stamp(mod):
    """Watched RED before the fix: the sweep compared raw ISO strings.

    A transcript stamps `...Z`; `git log %aI` stamps a numeric offset. These two
    instants are 5 hours apart and the STRING compare gets the order backwards, so
    every rule's origin window would have been wrong.
    """
    transcript = "2026-09-11T04:11:19.123Z"          # 04:11:19 UTC
    origin = "2026-09-11T04:11:19-05:00"            # 09:11:19 UTC — LATER
    assert transcript >= origin, "precondition: the naive string compare says 'after'"
    t, o = mod.parse_ts(transcript), mod.parse_ts(origin)
    assert t is not None and o is not None
    assert t < o, "parsed instants must order the other way round"


def test_parse_ts_returns_None_rather_than_raising_on_junk(mod):
    for junk in ("", "not-a-date", "2026-13-45T99:99:99Z"):
        assert mod.parse_ts(junk) is None


# ------------------------------------------------------------------- the mechanism
HEADING = "/audit-pr — adversarial PR audit"   # the sweep's POSITIVE control


def _rec(role, blocks, ts="2026-09-01T00:00:00.000Z"):
    return json.dumps({"type": role, "timestamp": ts,
                       "message": {"role": role, "content": blocks}})


def _corpus(tmp_path, records, name="proj/s.jsonl"):
    p = tmp_path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(records) + "\n")
    return tmp_path


def _run(corpus, *args, env=None, expect=None):
    e = dict(os.environ)
    e["AUDIT_SWEEP_CORPUS"] = str(corpus)
    e.update(env or {})
    r = subprocess.run([sys.executable, str(SCRIPT), *args],
                       capture_output=True, text=True, cwd=str(REPO), env=e,
                       timeout=300)
    if expect is not None:
        assert r.returncode == expect, (
            f"expected exit {expect}, got {r.returncode}\n{r.stdout}\n{r.stderr}")
    return r


SENTENCE = "round 4 · payload lines changed THIS round: 0"


def _row(json_path, rule_id):
    """Read one rule's row off the JSON surface.

    Numbers are asserted HERE and not scraped out of the aligned text table: the
    columns shift whenever a column is added, and the `origin` field contains
    digits, so a regex over the row matched the date and passed for the wrong
    reason. Both of those actually happened while writing this file.
    """
    data = json.loads(Path(json_path).read_text())
    rows = [r for r in data["rows"] if r["id"] == rule_id]
    assert rows, f"{rule_id} not in {[r['id'] for r in data['rows']]}"
    return rows[0]


def test_only_assistant_authored_text_counts_as_fired(tmp_path):
    """🔴 The naive sweep's whole defect, pinned.

    The same sentence is present twice: once as an assistant message (the auditor
    APPLYING the rule) and once as a Read tool_result (the skill body being loaded).
    Exactly one of those is a firing.
    """
    recs = [
        _rec("assistant", [{"type": "text", "text": "heading " + HEADING}]),
        _rec("assistant", [{"type": "text", "text": "My ledger: " + SENTENCE}]),
        _rec("assistant", [{"type": "tool_use", "id": "t1", "name": "Read",
                            "input": {}}]),
        _rec("user", [{"type": "tool_result", "tool_use_id": "t1",
                       "content": "SKILL.md says " + SENTENCE}]),
    ]
    out_path = tmp_path / "rows.json"
    _run(_corpus(tmp_path, recs), "--rule", "round-ledger-line",
         "--json", str(out_path), expect=0)
    row = _row(out_path, "round-ledger-line")
    assert row["fired"] == 1, f"expected fired=1 (the assistant block only): {row}"
    assert row["in_finding"] == 0, f"no severity marker sits near it: {row}"
    assert row["injected_noise"] == 1, (
        f"the Read tool_result must be counted as noise, not as a firing: {row}")
    assert row["verdict"].startswith("FIRED"), row


def test_a_tool_result_only_corpus_reports_UNFIRED_not_FIRED(tmp_path):
    """The negative arm of the test above: the skill body alone must never read as a
    firing, however many times it is loaded."""
    recs = [_rec("assistant", [{"type": "text", "text": HEADING}]),
            _rec("assistant", [{"type": "tool_use", "id": "t1", "name": "Read",
                                "input": {}}])]
    recs += [_rec("user", [{"type": "tool_result", "tool_use_id": "t1",
                            "content": SENTENCE}]) for _ in range(25)]
    out = _run(_corpus(tmp_path, recs), "--rule", "round-ledger-line",
               expect=0).stdout
    row = next(l for l in out.splitlines() if l.startswith("round-ledger-line"))
    assert "UNFIRED" in row, row


def test_an_agent_tool_result_does_count_as_a_firing(tmp_path):
    """A dispatched auditor's report reaches the parent as an Agent tool_result. That
    IS the auditor's own words, so it is signal — unlike every other tool result."""
    recs = [
        _rec("assistant", [{"type": "text", "text": HEADING}]),
        _rec("assistant", [{"type": "tool_use", "id": "a1", "name": "Agent",
                            "input": {}}]),
        _rec("user", [{"type": "tool_result", "tool_use_id": "a1",
                       "content": "🔴 Finding 2: " + SENTENCE}]),
    ]
    out = _run(_corpus(tmp_path, recs), "--rule", "round-ledger-line",
               expect=0).stdout
    row = next(l for l in out.splitlines() if l.startswith("round-ledger-line"))
    assert "FIRED" in row, row
    nums = [int(x) for x in re.findall(r"\s(\d+)\s", row)]
    assert nums[1] == 1, f"the 🔴 beside it should make in-finding 1: {row}"


# ----------------------------------------------------------------- the controls
def test_the_positive_control_refuses_a_corpus_with_no_skill_text(tmp_path):
    """A corpus that never saw the skill cannot be distinguished from a walk wired to
    nothing, so the sweep must refuse rather than print zeros."""
    recs = [_rec("assistant", [{"type": "text", "text": "unrelated work"}])]
    r = _run(_corpus(tmp_path, recs), "--rule", "round-ledger-line", expect=3)
    assert "POSITIVE" in r.stdout and "FAILED" in r.stdout
    assert "claim about the instrument" in r.stderr


def test_the_negative_control_refuses_when_the_sentinel_appears(tmp_path, mod):
    """If the sentinel is ever matched the matcher is matching anything."""
    sentinel = mod.NEG_CONTROL[1]
    recs = [_rec("assistant", [{"type": "text", "text": HEADING}]),
            _rec("assistant", [{"type": "text", "text": "x " + sentinel + " y"}])]
    r = _run(_corpus(tmp_path, recs), "--rule", "round-ledger-line", expect=3)
    assert "NEGATIVE" in r.stdout and "FAILED" in r.stdout


def test_an_empty_corpus_directory_exits_4_not_a_clean_zero(tmp_path):
    """A directory that exists and holds no transcripts walked nothing. That is not
    the same claim as 'no rule fired', so it gets its own code."""
    (tmp_path / "empty").mkdir()
    r = _run(tmp_path / "empty", "--rule", "round-ledger-line", expect=4)
    assert "walked zero transcripts" in r.stderr


def test_the_prefilter_still_finds_an_ascii_escaped_transcript(tmp_path):
    """🔴 Watched RED — this broke four tests before `ascii_tolerant` existed.

    `json.dumps` defaults to ensure_ascii, so `—` lands in the file as `\\u2014`.
    The rg prefilter reads the RAW bytes while the matcher reads DECODED text, so a
    pattern carrying a literal em-dash — which the sweep's own POSITIVE control does
    — selected zero files and every rule read UNFIRED off a corpus it never parsed.
    """
    recs = [
        json.dumps({"type": "assistant", "timestamp": "2026-09-01T00:00:00.000Z",
                    "message": {"role": "assistant",
                                "content": [{"type": "text", "text": HEADING}]}},
                   ensure_ascii=True),
        json.dumps({"type": "assistant", "timestamp": "2026-09-01T00:00:00.000Z",
                    "message": {"role": "assistant",
                                "content": [{"type": "text", "text": SENTENCE}]}},
                   ensure_ascii=True),
    ]
    assert "\\u2014" in recs[0], "precondition: the em-dash really is escaped on disk"
    out = _run(_corpus(tmp_path, recs), "--rule", "round-ledger-line",
               expect=0).stdout
    assert "candidate files after prefilter: 1" in out, out
    row = next(l for l in out.splitlines() if l.startswith("round-ledger-line"))
    assert "FIRED" in row, row


def test_a_missing_corpus_exits_4(tmp_path):
    r = _run(tmp_path / "nope", "--rule", "round-ledger-line", expect=4)
    assert "not a directory" in r.stderr


# ------------------------------------------------- the ledger gate, watched RED
def test_the_ledger_gate_exits_5_when_a_probe_goes_missing(tmp_path):
    """Mutation: delete one rule's probe from a COPY of the skill. The sweep must
    refuse with its own exit code 5 and name the rule — not proceed and date that
    rule wrongly. Asserted on exit code AND on the rule name, so a different
    refusal cannot pass this for the wrong reason."""
    body = SKILL.read_text()
    probe = "ONE NUMBER, ONE NAME"
    assert probe in body, "precondition: the probe is in the real skill"
    copy = tmp_path / "SKILL.md"
    copy.write_text(body.replace(probe, "ONE COUNT, ONE LABEL"))
    recs = [_rec("assistant", [{"type": "text", "text": HEADING}])]
    r = _run(_corpus(tmp_path / "c", recs), expect=5,
             env={"AUDIT_SWEEP_SKILL": str(copy)})
    assert "one-number-one-name" in r.stderr, r.stderr
    assert "no longer in SKILL.md" in r.stderr


def test_an_unmodified_skill_copy_passes_the_ledger_gate(tmp_path):
    """The positive half of the mutation above: the gate is not simply always red
    when AUDIT_SWEEP_SKILL is set."""
    copy = tmp_path / "SKILL.md"
    copy.write_text(SKILL.read_text())
    recs = [_rec("assistant", [{"type": "text", "text": HEADING}])]
    _run(_corpus(tmp_path / "c", recs), "--rule", "one-number-one-name", expect=0,
         env={"AUDIT_SWEEP_SKILL": str(copy)})


# ------------------------------------------------- pre-origin specificity control
def test_a_pre_origin_match_withholds_the_number_instead_of_reporting_it(tmp_path):
    """A pattern that matched before its rule existed is not specific to the rule, so
    the row must read UNRELIABLE with no count — a withheld row is not a zero."""
    recs = [_rec("assistant", [{"type": "text", "text": HEADING}],
                 ts="2026-01-01T00:00:00.000Z"),
            _rec("assistant", [{"type": "text", "text": SENTENCE}],
                 ts="2026-01-01T00:00:00.000Z")]
    out = _run(_corpus(tmp_path, recs), "--rule", "round-ledger-line", expect=0,
               env={"AUDIT_SWEEP_ORIGIN": "2026-06-01T00:00:00+00:00"}).stdout
    row = next(l for l in out.splitlines() if l.startswith("round-ledger-line"))
    assert "UNRELIABLE" in row, row
    assert "pre-origin" in row
    assert "Withheld is not zero" in out


def test_a_withheld_row_prints_no_number_at_all_not_a_zero(tmp_path):
    """🔴 The label must not be wider than the implementation.

    An earlier revision printed the real post-origin counts on a row whose verdict
    said the number was withheld — so a reader could quote a figure the sweep had
    just declared unreliable. A withheld row carries dashes, and the JSON carries
    nulls, so neither surface can be read as a measurement.
    """
    recs = [
        # one match BEFORE the forced origin (makes the row unreliable) ...
        _rec("assistant", [{"type": "text", "text": HEADING}],
             ts="2026-01-01T00:00:00.000Z"),
        _rec("assistant", [{"type": "text", "text": SENTENCE}],
             ts="2026-01-01T00:00:00.000Z"),
        # ... and two AFTER it, which a naive report would print as fired=2
        _rec("assistant", [{"type": "text", "text": SENTENCE}],
             ts="2026-08-01T00:00:00.000Z"),
        _rec("assistant", [{"type": "text", "text": SENTENCE}],
             ts="2026-08-02T00:00:00.000Z"),
    ]
    out_path = tmp_path / "rows.json"
    out = _run(_corpus(tmp_path, recs), "--rule", "round-ledger-line",
               "--json", str(out_path), expect=0,
               env={"AUDIT_SWEEP_ORIGIN": "2026-06-01T00:00:00+00:00"}).stdout
    row = next(l for l in out.splitlines() if l.startswith("round-ledger-line"))
    assert "UNRELIABLE" in row, row
    # the numeric columns, sliced by position rather than regex-scraped
    cols = row[38:].split()
    assert cols[1:5] == ["—", "—", "—", "—"], (
        f"a withheld row must print dashes in sess/fired/in-fnd/nondevrc: {row}")
    r0 = _row(out_path, "round-ledger-line")
    assert r0["withheld"] is True
    for k in ("fired", "in_finding", "sessions", "non_devrc", "last_seen"):
        assert r0[k] is None, f"{k} leaked a value on a withheld row: {r0[k]}"
    assert r0["pre_origin"] == 1, r0


def test_the_forced_origin_hook_labels_itself_as_a_what_if_run(tmp_path):
    """AUDIT_SWEEP_ORIGIN changes what every number means, so a run using it must say
    so in its own output rather than reading as a measurement."""
    recs = [_rec("assistant", [{"type": "text", "text": HEADING}])]
    out = _run(_corpus(tmp_path, recs), "--rule", "round-ledger-line", expect=0,
               env={"AUDIT_SWEEP_ORIGIN": "2026-06-01T00:00:00+00:00"}).stdout
    assert "do not quote it as a measurement" in out


# --------------------------------------------------------------- honesty of wording
def test_the_report_never_claims_the_rule_CAUGHT_something(tmp_path):
    """The measurement is APPLIED, not CAUGHT. The report must say so on every run,
    because `fired` is the column a reader will quote."""
    recs = [_rec("assistant", [{"type": "text", "text": HEADING}]),
            _rec("assistant", [{"type": "text", "text": SENTENCE}])]
    out = _run(_corpus(tmp_path, recs), "--rule", "round-ledger-line",
               expect=0).stdout
    assert "NOT 'caught'" in out
    assert "counts APPLICATIONS, not catches" in out


def test_an_unfired_row_is_not_called_dead(tmp_path):
    """Blind spot 4: a rule nobody has violated since does not fire. The report must
    not read that as deadness."""
    recs = [_rec("assistant", [{"type": "text", "text": HEADING}])]
    out = _run(_corpus(tmp_path, recs), "--rule", "count-in-prose-is-a-claim",
               expect=0).stdout
    row = next(l for l in out.splitlines()
               if l.startswith("count-in-prose-is-a-claim"))
    assert "UNFIRED" in row and "dead" in row.lower(), row
    assert "not 'dead'" in row
