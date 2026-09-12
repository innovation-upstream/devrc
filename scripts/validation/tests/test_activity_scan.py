"""Unit tests for activity-scan: pure logic + query shapes + render (mocked client).

No test hits a real ClickHouse — the script is SQL-builders + formatting, so we test:
  - the pure helpers (window math, numeric coercion, formatting, sequence hint, bar)
  - that every SQL builder is well-formed (balanced parens, scoped to the window/host,
    correct source/filters) — guards against a copy-paste regression in the SQL
  - gather()/render() end-to-end against a fake CHClient (no network)
"""
import json
import re
import sys
from pathlib import Path

import pytest

# activity-scan lives in scripts/session-analysis; import it as a module.
SCRIPT_DIR = Path(__file__).resolve().parent.parent.parent / "session-analysis"
sys.path.insert(0, str(SCRIPT_DIR))
# also make chquery importable the way the script does
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location("activity_scan", SCRIPT_DIR / "activity-scan.py")
A = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(A)


# --------------------------------------------------------------------------- #
# window_seconds
# --------------------------------------------------------------------------- #
def test_window_seconds():
    assert A.window_seconds(7) == 604800
    assert A.window_seconds(1) == 86400
    assert A.window_seconds(30) == 2592000


def test_window_seconds_rejects_nonpositive():
    with pytest.raises(ValueError):
        A.window_seconds(0)
    with pytest.raises(ValueError):
        A.window_seconds(-3)


# --------------------------------------------------------------------------- #
# num: ClickHouse JSON field coercion (UInt64 comes back as a quoted string)
# --------------------------------------------------------------------------- #
def test_num_coerces_string_ints_and_floats():
    assert A.num("42") == 42 and isinstance(A.num("42"), int)
    assert A.num("3.5") == 3.5
    assert A.num(7) == 7
    assert A.num(2.0) == 2.0
    assert A.num("-5") == -5


def test_num_handles_none_and_garbage():
    assert A.num(None) == 0
    assert A.num(None, default=-1) == -1
    assert A.num("not-a-number") == 0
    assert A.num("") == 0


# --------------------------------------------------------------------------- #
# formatting
# --------------------------------------------------------------------------- #
def test_fmt_min_and_s():
    assert A.fmt_min(429.1) == "429.1m"
    assert A.fmt_min(5) == "5.0m"
    assert A.fmt_min(None) == "-"
    assert A.fmt_s(52.2) == "52.2s"
    assert A.fmt_s(None) == "-"


def test_bar_scales_and_handles_edges():
    assert A.bar(10, 10) == "█" * 20          # full
    assert A.bar(0, 10) == ""                  # zero value -> empty
    assert A.bar(5, 0) == ""                   # no peak -> empty
    # a tiny non-zero value still gets at least one block
    assert A.bar(1, 1000) == "█"
    # accepts CH string fields
    assert A.bar("10", "10") == "█" * 20


# --------------------------------------------------------------------------- #
# sequence_hint (deterministic substring match)
# --------------------------------------------------------------------------- #
def test_sequence_hint_fires_on_civitai_dogfood():
    cmds = ["g pull", "civitai app create dogfood-manual", "rm -r dogfood-manual"]
    hint = A.sequence_hint(cmds)
    assert hint and "civitai dogfood" in hint


def test_sequence_hint_silent_when_no_match():
    assert A.sequence_hint(["g pull", "npm run dev", "ls"]) is None
    assert A.sequence_hint([]) is None


# --------------------------------------------------------------------------- #
# SQL builders: well-formed + correctly scoped
# --------------------------------------------------------------------------- #
def _balanced(sql: str) -> bool:
    depth = 0
    for c in sql:
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


ALL_BUILDERS = [
    lambda: A.q_repeated_commands(604800),
    lambda: A.q_top_binaries(604800),
    lambda: A.q_binaries_by_wait(604800),
    lambda: A.q_context_switches(604800, "laptop"),
    lambda: A.q_attention_by_app(604800, "laptop"),
    lambda: A.q_browser_by_domain(604800, "laptop"),
    lambda: A.q_deep_work(604800, "laptop"),
]


def test_all_builders_balanced_parens():
    for b in ALL_BUILDERS:
        sql = b()
        assert _balanced(sql), f"unbalanced parens in: {sql[:80]}"


def test_window_is_applied_to_every_builder():
    for b in ALL_BUILDERS:
        assert "now()-604800" in b()


def test_zsh_builders_scope_to_zsh():
    for b in (A.q_repeated_commands, A.q_top_binaries, A.q_binaries_by_wait):
        sql = b(604800)
        assert "source='zsh'" in sql
        assert "kind='command'" in sql


def test_i3_builders_scope_to_i3_and_host():
    sql = A.q_context_switches(604800, "laptop")
    assert "source='i3'" in sql and "host='laptop'" in sql
    sql = A.q_attention_by_app(604800, "laptop")
    assert "source='i3'" in sql and "leadInFrame" in sql
    sql = A.q_deep_work(604800, "laptop")
    assert "app='Alacritty'" in sql and "run_s>=600" in sql and "run_s>=1500" in sql


def test_browser_builder_uses_i3_derived_cte():
    sql = A.q_browser_by_domain(604800, "laptop")
    # i3 Brave-focus ∩ nav-domain overlap, domain via text column with netloc fallback
    assert "app='Brave-browser'" in sql
    assert "domain(text)" in sql and "netloc(text)" in sql


# --------------------------------------------------------------------------- #
# q_browser_by_domain: it must stay a boundary SWEEP, never become a join again.
#
# These assert properties of the GENERATED SQL — that is all a test with no live
# ClickHouse can see. The claim that the sweep returns the SAME NUMBERS as the join it
# replaced was established live (byte-identical at --days 7/14/21/30, 12 paired runs
# each) and is recorded in q_browser_by_domain's docstring, not here.
# --------------------------------------------------------------------------- #
def test_browser_builder_never_joins_the_two_interval_sets():
    """No join operator of any spelling, and no comma-join.

    `brave CROSS JOIN dom` with an overlap predicate is the obvious way to write this
    intersection and it is what shipped until 2026-09-12. Its candidate-pair count is
    |brave| x |dom| — QUADRATIC in --days: 727 x 831 = 604k pairs at 7 days against
    2,799 x 3,180 = 8.9M at 30 days, measured against the live server. The allocator
    churn from streaming those pairs pushed the (3 GiB pod / 2.5 GiB
    max_server_memory_usage) ClickHouse over its server-wide ceiling, and the
    OvercommitTracker killed the query: interleaved so both arms saw the same
    conditions, `--days 30` failed 9 of 225 runs with `Code: 241 … While executing
    JoiningTransform`, against 0 of 225 for the sweep (plain count() control: 0 of 145).
    """
    sql = A.q_browser_by_domain(2592000, "laptop")
    hit = re.search(r"\bJOIN\b", sql, re.I)
    assert hit is None, (
        "q_browser_by_domain combines the interval sets with a join again "
        f"({sql[max(0, hit.start() - 60):hit.end() + 30]!r}) — the candidate-pair count "
        "is |brave| x |dom|, quadratic in --days, and that is what made --days 30 die "
        "with MEMORY_LIMIT_EXCEEDED. Sweep the merged boundary stream instead."
    )
    comma = re.search(r"\bFROM\s+\w+\s*,", sql)
    assert comma is None, (
        "q_browser_by_domain has a comma-join "
        f"({sql[max(0, comma.start() - 40):comma.end() + 30]!r}) — a cartesian product "
        "spelled without the word JOIN, same quadratic hazard."
    )


def test_browser_builder_reads_each_interval_family_exactly_twice():
    """The linearity property the fix rests on.

    A sweep touches each interval exactly twice — once per endpoint — so the merged
    stream is 2 * (|brave| + |dom|) rows (about 12k at --days 30) rather than the
    join's |brave| x |dom| pairs. A third read of either CTE means something other
    than the two boundary branches is consuming it.
    """
    sql = A.q_browser_by_domain(2592000, "laptop")
    assert sql.count("FROM brave") == 2, (
        f"expected the `brave` interval CTE to be read exactly twice (one row per "
        f"interval endpoint), got {sql.count('FROM brave')} — the boundary stream is "
        "no longer linear in |brave|"
    )
    assert sql.count("FROM dom") == 2, (
        f"expected the `dom` interval CTE to be read exactly twice (one row per "
        f"interval endpoint), got {sql.count('FROM dom')} — the boundary stream is "
        "no longer linear in |dom|"
    )
    assert sql.count("UNION ALL") == 3, (
        f"expected exactly 4 boundary branches (3 UNION ALL), got "
        f"{sql.count('UNION ALL') + 1} — the merged stream is not the 2-per-interval "
        "shape the linear cost depends on"
    )


def test_browser_builder_sweep_state_machine_is_intact():
    """The three carried-state expressions that make the sweep compute the same measure.

    Charge each gap between consecutive boundaries to the domain in force, but only
    while Brave holds focus. Drop any one of these and the section silently changes
    meaning rather than failing.
    """
    sql = A.q_browser_by_domain(2592000, "laptop")
    # gap to the next boundary
    assert ("leadInFrame(t,1,t) OVER (PARTITION BY host ORDER BY t ASC, ord ASC "
            "ROWS BETWEEN CURRENT ROW AND UNBOUNDED FOLLOWING))-t AS ms") in sql, \
        "the segment length (gap to the next boundary) is gone from the sweep"
    # running Brave-focus depth
    assert ("sum(bd) OVER (PARTITION BY host ORDER BY t ASC, ord ASC "
            "ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)) AS brave_on") in sql, \
        "the running Brave-focus depth is gone — segments would be charged with the " \
        "browser unfocused"
    # domain in force, carried forward by a running max over (tie-broken instant, domain)
    assert ("max((dseq, dm)) OVER (PARTITION BY host ORDER BY t ASC, ord ASC "
            "ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)") in sql, \
        "the carried-forward active domain is gone from the sweep"
    # and the charge predicate that consumes all three
    assert "WHERE brave_on>0 AND ms>0 AND d!=''" in sql, \
        "the charge predicate no longer requires (Brave focused) AND (a domain in force)"


def test_browser_builder_measure_contract_is_unchanged():
    """The section is still dwell-MINUTES by domain over the same two event streams.

    Pins the inputs (both source scans, both filtered to the host and the window and
    capped at the same 30 min) and the output shape (`domain`, `attention_min`, the
    same HAVING/ORDER BY/LIMIT), so a rewrite cannot quietly change what the report's
    "Browser attention by domain" column means.
    """
    sql = A.q_browser_by_domain(604800, "laptop")
    assert "source='i3'" in sql and "kind='window-focus'" in sql
    assert "source='browser'" in sql and "kind='nav'" in sql
    # the time predicate is pushed into BOTH source scans, not applied after the merge
    assert sql.count("ts>now()-604800") == 2
    assert sql.count("host IN ('laptop')") == 2
    # the 30-minute cap: once on the i3 dwell, twice on the nav interval (leadInFrame
    # default for the newest row, then the least() clamp). Matched with digit
    # boundaries, NOT `sql.count("1800000")` — a bare substring count survives a cap
    # widened to 18000000 (mutation-tested 2026-09-12: that mutant SURVIVED the count
    # version and is killed by this one).
    caps = re.findall(r"(?<!\d)1800000(?!\d)", sql)
    assert len(caps) == 3, (
        f"expected the 30-minute (1800000 ms) cap exactly 3 times — once on the i3 "
        f"dwell, twice on the nav interval — got {len(caps)}; the dwell/gap cap that "
        "keeps an idle window out of the total has moved"
    )
    assert "round(sum(ms)/60000,1) AS attention_min" in sql
    assert sql.endswith(
        "SELECT d AS domain, round(sum(ms)/60000,1) AS attention_min "
        "FROM seg WHERE brave_on>0 AND ms>0 AND d!='' "
        "GROUP BY d HAVING attention_min>0 ORDER BY attention_min DESC LIMIT 15"
    )


def test_host_is_sql_quoted():
    # a host value is interpolated through sql_quote, not raw-concatenated
    sql = A.q_context_switches(604800, "o'brien")
    assert "host='o\\'brien'" in sql


# --------------------------------------------------------------------------- #
# gather() + render() against a fake client (no network)
# --------------------------------------------------------------------------- #
class FakeClient:
    """Returns canned rows keyed by a marker substring found in the SQL."""

    def __init__(self, mapping):
        self.mapping = mapping

    def rows(self, sql):
        for marker, rows in self.mapping.items():
            if marker in sql:
                return rows
        return []


def _sample_mapping():
    return {
        "substring(text,1,60)": [
            {"n": "28", "host": "laptop", "cmd": "g pull"},
            {"n": "23", "host": "laptop", "cmd": "civitai app create dogfood-manual"},
        ],
        "splitByChar(' ', trim(BOTH ' ' FROM text))[1] bin "
        "FROM activity.events WHERE source='zsh' AND kind='command' AND ts>now()-604800 AND text!='' "
        "GROUP BY bin": [
            {"n": "89", "bin": "civitai"},
            {"n": "70", "bin": "npm"},
        ],
        "duration_ms<7200000": [
            {"bin": "npm", "n": "66", "tot_min": 429.1, "med_s": 17.0, "max_s": 5724.4},
        ],
        "avg(sw)": [{"avg_per_hr": 100.4, "peak": "298"}],
        "WHERE kind='window-focus' AND app!='' GROUP BY app": [
            {"app": "Alacritty", "dwell_min": 1953.8},
            {"app": "Brave-browser", "dwell_min": 458.8},
        ],
        # marker for q_browser_by_domain: the sweep's charge predicate
        "FROM seg WHERE brave_on>0": [
            {"domain": "github.com", "attention_min": 83.3},
        ],
        "WHERE app='Alacritty' AND run_s<3600": [
            {"b10": "41", "b25": "11", "longest_min": 51.6},
        ],
    }


def test_gather_assembles_all_sections():
    client = FakeClient(_sample_mapping())
    data = A.gather(client, days=7, host="laptop")
    assert data["days"] == 7 and data["host"] == "laptop"
    assert len(data["automation"]["repeated_commands"]) == 2
    assert data["automation"]["sequence_hint"]  # dogfood cmd present -> hint fires
    assert data["bottlenecks"]["binaries_by_wait"][0]["bin"] == "npm"
    assert data["signal_noise"]["context_switches"]["peak"] == "298"
    assert data["signal_noise"]["deep_work"]["b10"] == "41"


def test_render_is_skimmable_and_includes_key_numbers():
    client = FakeClient(_sample_mapping())
    data = A.gather(client, days=7, host="laptop")
    text = A.render(data)
    # the three section headers are present
    assert "## AUTOMATION CANDIDATES" in text
    assert "## BOTTLENECKS" in text
    assert "## SIGNAL vs NOISE" in text
    # honesty note present
    assert "human/LLM call" in text
    # representative numbers rendered
    assert "g pull" in text
    assert "429.1m" in text
    assert "avg 100.4" in text
    assert "Alacritty" in text
    assert "github.com" in text
    assert ">=10min: 41" in text


def test_render_handles_empty_i3_data():
    # a host with no GUI data -> graceful messages, no crash
    client = FakeClient({})
    data = A.gather(client, days=7, host="workbench")
    text = A.render(data)
    assert "no i3 data" in text
    assert "(none crossed n>=4)" in text


# --------------------------------------------------------------------------- #
# PER-SECTION DEGRADATION
#
# The report is seven independent queries and the trigger for a failure is SERVER LOAD,
# not anything wrong with the query: activity's ClickHouse runs in a 3 GiB pod with
# max_server_memory_usage 2.5 GiB and CH 25.x corrects that server-wide tracker against
# container RSS, so the OvercommitTracker stops whichever query happens to be allocating.
# Measured 2026-09-12 over 25 whole-report rounds, q_top_binaries and q_context_switches
# were EACH killed independently while the other six sections were fine. Before this,
# any one CHQueryError aborted gather() and the user got a traceback and no report.
#
# 🔴 The distinction these tests exist to protect: a section that returned ZERO ROWS and a
# section whose query FAILED are different facts, and neither may be silently omitted.
# --------------------------------------------------------------------------- #
# One distinctive substring per query, used to make a fake client fail exactly one
# section. Pinned two-way against A.SECTION_LABELS below, and each marker is checked to
# match exactly ONE of the seven built statements — a marker that matched two would make
# every "only this section failed" assertion vacuous.
SECTION_MARKERS = {
    "repeated_commands": "substring(text,1,60)",
    "top_binaries": "GROUP BY bin ORDER BY n DESC",
    "binaries_by_wait": "duration_ms<7200000",
    "context_switches": "avg(sw)",
    "attention_by_app": "WHERE kind='window-focus' AND app!='' GROUP BY app",
    "browser_by_domain": "FROM seg WHERE brave_on>0",
    "deep_work": "WHERE app='Alacritty' AND run_s<3600",
}

# What each section contributes to a rendered report when it SUCCEEDS, from
# _sample_mapping(). Used to prove the other six survive one section's death.
SECTION_EVIDENCE = {
    "repeated_commands": "g pull",
    "top_binaries": "civitai",
    "binaries_by_wait": "429.1m",
    "context_switches": "avg 100.4",
    "attention_by_app": "Alacritty",
    "browser_by_domain": "github.com",
    "deep_work": ">=10min: 41",
}

MEMORY_LIMIT_ERROR = (
    "ClickHouse HTTP 500: Code: 241. DB::Exception: Memory limit (total) exceeded: "
    "would use 2.33 GiB, maximum: 2.33 GiB. OvercommitTracker decision: Query was "
    "selected to stop by OvercommitTracker."
)


class FlakyClient(FakeClient):
    """FakeClient that raises for any SQL containing one of `failing`'s markers."""

    def __init__(self, mapping, failing=(), exc_factory=None):
        super().__init__(mapping)
        self.failing = tuple(failing)
        self.exc_factory = exc_factory or (
            lambda: A.Q.CHQueryError(MEMORY_LIMIT_ERROR, code=241, http_status=500))

    def rows(self, sql):
        for marker in self.failing:
            if marker in sql:
                raise self.exc_factory()
        return super().rows(sql)


def test_section_markers_are_pinned_to_the_ledger_and_are_unambiguous():
    """Validate the instrument before reading any verdict off it.

    Two ways these tests could pass while testing nothing: a marker naming a section
    gather() no longer runs (the failure is never induced), or a marker that appears in
    more than one statement (killing two sections while the test claims one).
    """
    assert set(SECTION_MARKERS) == set(A.SECTION_LABELS), (
        "SECTION_MARKERS and activity-scan's SECTION_LABELS ledger disagree: "
        f"markers-only={sorted(set(SECTION_MARKERS) - set(A.SECTION_LABELS))}, "
        f"ledger-only={sorted(set(A.SECTION_LABELS) - set(SECTION_MARKERS))}. Every "
        "section must be degradable and every degradable section must be exercised here."
    )
    assert set(SECTION_EVIDENCE) == set(A.SECTION_LABELS)
    statements = [b() for b in ALL_BUILDERS]
    for key, marker in SECTION_MARKERS.items():
        hits = [s for s in statements if marker in s]
        assert len(hits) == 1, (
            f"marker for {key!r} ({marker!r}) matches {len(hits)} of the seven "
            "statements, not exactly 1 — a per-section failure induced with it would not "
            "be per-section"
        )


@pytest.mark.parametrize("failed", sorted(SECTION_MARKERS))
def test_one_failed_section_still_yields_the_other_six(failed):
    """A single CHQueryError degrades ONE section; the rest of the report survives."""
    client = FlakyClient(_sample_mapping(), failing=[SECTION_MARKERS[failed]])
    try:
        data = A.gather(client, days=7, host="laptop")
    except A.Q.CHQueryError as e:
        pytest.fail(
            f"gather() let a CHQueryError from section {failed!r} escape ({e}) — that "
            "section is not wrapped, so one unlucky query still destroys the whole report"
        )
    assert set(data["failures"]) == {failed}, (
        f"expected exactly the {failed!r} section to be recorded as failed, got "
        f"{sorted(data['failures'])}"
    )
    text = A.render(data)
    for other, evidence in SECTION_EVIDENCE.items():
        if other == failed:
            continue
        assert evidence in text, (
            f"{other!r} vanished from the report when {failed!r} failed — its evidence "
            f"{evidence!r} is missing; a per-section failure must cost exactly one section"
        )


@pytest.mark.parametrize("failed", sorted(SECTION_MARKERS))
def test_a_failed_section_is_explicitly_marked_and_names_a_reason(failed):
    """Never silently omitted, and never mistakable for an empty section."""
    client = FlakyClient(_sample_mapping(), failing=[SECTION_MARKERS[failed]])
    data = A.gather(client, days=7, host="laptop")
    text = A.render(data)

    assert text.count(A.SECTION_UNAVAILABLE_MARK) == 1, (
        f"expected exactly one {A.SECTION_UNAVAILABLE_MARK!r} line for the failed "
        f"{failed!r} section, got {text.count(A.SECTION_UNAVAILABLE_MARK)} — a failed "
        "section that prints nothing turns a holed report into one that reads as complete"
    )
    marked = [ln for ln in text.splitlines() if A.SECTION_UNAVAILABLE_MARK in ln][0]
    assert A.SECTION_LABELS[failed] in marked, (
        f"the unavailable line does not name which section died: {marked!r}"
    )
    # 🔴 it must NAME A REASON, not just say "unavailable"
    assert "241" in marked and "Memory limit" in marked, (
        f"the unavailable line does not carry the failure reason: {marked!r} — a reader "
        "cannot tell an OvercommitTracker kill from a broken query without it"
    )
    # ...and the banner must count it with digit boundaries ("1 of 7" is a substring of
    # "11 of 7", so a bare `in` check would survive a miscount mutant).
    assert re.search(r"(?<!\d)1 of 7 sections could not be computed", text), (
        f"expected a PARTIAL banner counting exactly 1 of 7 failed sections; got:\n"
        f"{text.splitlines()[1] if len(text.splitlines()) > 1 else '(no banner line)'}"
    )


@pytest.mark.parametrize("section", sorted(SECTION_MARKERS))
def test_failed_reads_differently_from_empty(section):
    """🔴 The core distinction: zero rows and a dead query must not render the same.

    Same section, same fake client shape, two runs: one where the query returns nothing
    and one where it raises. The rendered lines for that section must differ, the empty
    run must carry no unavailable marker at all, and the failed run must not be quietly
    presented with the section's "(none…)" wording.
    """
    empty = A.render(A.gather(FakeClient({}), days=7, host="laptop"))
    failed = A.render(A.gather(
        FlakyClient({}, failing=[SECTION_MARKERS[section]]), days=7, host="laptop"))

    assert A.SECTION_UNAVAILABLE_MARK not in empty, (
        "a report where every section legitimately returned zero rows must contain NO "
        f"{A.SECTION_UNAVAILABLE_MARK!r} marker — empty is not failed"
    )
    assert "PARTIAL REPORT" not in empty, (
        "an all-empty report is a COMPLETE report and must not be banner-flagged partial"
    )
    assert A.SECTION_UNAVAILABLE_MARK in failed, (
        f"the {section!r} section failed and the report does not say so — indistinguishable "
        "from the empty run above"
    )
    assert empty != failed, (
        f"a failed {section!r} renders byte-identically to an empty {section!r}: the "
        "report is asserting 'no data' about a query that never answered"
    )


def test_json_payload_records_the_failure():
    """--json is a machine-readable report; the failure must be IN the payload."""
    client = FlakyClient(_sample_mapping(), failing=[SECTION_MARKERS["top_binaries"]])
    data = A.gather(client, days=7, host="laptop")

    assert data["partial"] is True, (
        "data['partial'] must be True when a section failed — a consumer branching on it "
        "would treat a holed report as whole"
    )
    rec = data["failures"]["top_binaries"]
    assert rec["code"] == 241, f"the ClickHouse error code is not in the payload: {rec}"
    assert rec["http_status"] == 500
    assert "Memory limit" in rec["error"]
    assert rec["section"] == A.SECTION_LABELS["top_binaries"]
    # and it must survive serialisation the way main() emits it
    round_tripped = json.loads(json.dumps(data, indent=2, default=str))
    assert round_tripped["failures"]["top_binaries"]["code"] == 241

    clean = A.gather(FakeClient(_sample_mapping()), days=7, host="laptop")
    assert clean["partial"] is False and clean["failures"] == {}, (
        "a fully successful report must still carry the keys, set to 'nothing failed' — "
        f"got partial={clean['partial']!r} failures={clean['failures']!r}"
    )


def test_failure_reason_is_bounded_to_one_line():
    """CH error bodies are multi-line and long; a report line must stay a line."""
    huge = A.Q.CHQueryError("Code: 241.\nDB::Exception: " + "x" * 900, code=241)
    client = FlakyClient(_sample_mapping(),
                         failing=[SECTION_MARKERS["deep_work"]],
                         exc_factory=lambda: huge)
    data = A.gather(client, days=7, host="laptop")
    reason = data["failures"]["deep_work"]["error"]
    assert "\n" not in reason, f"the recorded reason spans lines: {reason!r}"
    assert len(reason) <= 300, f"the recorded reason is {len(reason)} chars, unbounded"
    assert A.render(data).count("\n" + "  " + A.SECTION_UNAVAILABLE_MARK) == 1


def test_ch_unreachable_is_not_degraded():
    """🔴 The guard must not over-catch.

    CHUnreachable means the server could not be reached AT ALL — nothing can be said
    about any query, so retrying a different one is pointless (chquery's taxonomy says
    exactly this). Degrading it would print six empty sections over a dead pipeline and
    call the result a report. Broadening the except clause to CHError must fail here.
    """
    client = FlakyClient(
        _sample_mapping(), failing=[SECTION_MARKERS["context_switches"]],
        exc_factory=lambda: A.Q.CHUnreachable("URLError: connection refused"))
    try:
        data = A.gather(client, days=7, host="laptop")
    except A.Q.CHUnreachable:
        return
    pytest.fail(
        "gather() swallowed a CHUnreachable and returned a report "
        f"(failures={sorted(data['failures'])}) — an unreachable server is not a "
        "degraded section, it is a run that cannot say anything about any section"
    )


# --------------------------------------------------------------------------- #
# main(): a partial report must be distinguishable from a whole one by EXIT STATUS
# --------------------------------------------------------------------------- #
def _run_main(monkeypatch, client, argv):
    monkeypatch.setattr(A.Q.CHConn, "from_env", classmethod(
        lambda cls, env=None: A.Q.CHConn(url="http://x", user="u", password="p")))
    monkeypatch.setattr(A.Q, "CHClient", lambda conn: client)
    return A.main(argv)


def test_main_exit_status_separates_partial_from_whole(monkeypatch, capsys):
    rc = _run_main(monkeypatch, FakeClient(_sample_mapping()), ["--days", "7"])
    assert rc == 0, f"a report with every section computed must exit 0, got {rc}"
    capsys.readouterr()

    flaky = FlakyClient(_sample_mapping(), failing=[SECTION_MARKERS["top_binaries"]])
    rc = _run_main(monkeypatch, flaky, ["--days", "7"])
    assert rc == A.EXIT_PARTIAL, (
        f"a report that lost a section must exit {A.EXIT_PARTIAL} (PARTIAL), got {rc} — "
        "a caller redirecting stdout cannot otherwise tell a whole report from a holed one"
    )
    assert A.EXIT_PARTIAL != 0, (
        "EXIT_PARTIAL is 0, so the assertion above compares a partial run against a clean "
        "one and cannot tell them apart — PARTIAL must be its own non-zero status"
    )
    err = capsys.readouterr().err
    assert "top_binaries" in err and "241" in err, (
        f"the partial run said nothing on stderr about which section died: {err!r}"
    )


def test_main_json_mode_emits_the_failure_and_still_exits_partial(monkeypatch, capsys):
    flaky = FlakyClient(_sample_mapping(), failing=[SECTION_MARKERS["browser_by_domain"]])
    rc = _run_main(monkeypatch, flaky, ["--days", "7", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["partial"] is True
    assert payload["failures"]["browser_by_domain"]["code"] == 241
    assert rc == A.EXIT_PARTIAL, (
        f"--json lost a section and exited {rc}; PARTIAL must be reported the same way "
        "in both output modes"
    )
