"""Unit tests for activity-scan: pure logic + query shapes + render (mocked client).

No test hits a real ClickHouse — the script is SQL-builders + formatting, so we test:
  - the pure helpers (window math, numeric coercion, formatting, sequence hint, bar)
  - that every SQL builder is well-formed (balanced parens, scoped to the window/host,
    correct source/filters) — guards against a copy-paste regression in the SQL
  - gather()/render() end-to-end against a fake CHClient (no network)
"""
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
