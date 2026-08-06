"""insights — the QUANTITATIVE-HONESTY contract of the rendered report.

Three defect classes were measured in the built-in `/insights` output on
2026-08-05 and tested against OUR tool. This file pins the two that were REAL:

  H1  IMPOSSIBLE TIME TOTAL. Layer A's `duration_minutes` is
      `round((end_ts - start_ts) / 60)` — session-tailer.py:372 — an
      idle-inclusive span, unclipped to the report window. insights.py summed it
      and printed it as `wall-clock: Nh across sessions`. MEASURED over 2,393
      real transcripts touched in a trailing 14d window: Σ = 720,904 min =
      12,015.1 h against the 336 h that EXIST in 14 days (35.8x). Ten individual
      sessions had a span longer than the whole window.

  H2/H3  UNDISCLOSED WINDOW + DENOMINATOR. Layer B deliberately uses a WIDER
      window than Layer A (`max(--insight-days or 30, --days)`) and a different
      session population. `render()` labelled the window but never the
      denominator; `render_html()` labelled NEITHER — its subtitle said
      "trailing 14d · 2 sessions" directly above percentages computed over a
      30d Layer-B population.

Every expected value below is a LITERAL pinned from the fixture arithmetic
(40,320 min = 672.0 h; a 14d window = 336 h; 672/336 = 2.0x), never read back
out of the implementation.

The window is NOT applied inconsistently — every Layer-A query uses `win`, the
Layer-B query uses `iwin` by design, and write.py stamps a session-insight row's
`ts` with the session's own `end_ts` (write.py:15-18), so the built-in's
"pooled three older analysis batches" mechanism structurally cannot occur here.
That is asserted in test_insights.py::test_gather_uses_wider_insight_window.
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import importlib.util

_SPEC = importlib.util.spec_from_file_location(
    "insights", os.path.join(os.path.dirname(__file__), "..", "insights.py"))
I = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(I)


# --------------------------------------------------------------------------- #
# Fixtures — spans chosen so the arithmetic is exact and quotable.
#   25,000 + 15,320 = 40,320 minutes = 672.0 h
#   a 14-day window  = 336 h of wall clock
#   672.0 / 336      = 2.0x
# --------------------------------------------------------------------------- #
def _line(text, needle):
    """The ONE rendered line containing `needle` — with a named failure rather
    than a bare StopIteration when the line is absent entirely."""
    hits = [l for l in text.splitlines() if needle in l]
    assert hits, f"no rendered line contains {needle!r}"
    assert len(hits) == 1, f"{needle!r} appears on {len(hits)} lines: {hits}"
    return hits[0]


def _summary(session, host, project, payload):
    return {"session": session, "sess_host": host, "project": project,
            "ts": "2026-08-01 00:00:00.000", "payload": json.dumps(payload)}


def _spans(a_minutes, b_minutes):
    return [
        _summary("s1", "workbench", "devrc", {
            "user_message_count": 4, "assistant_message_count": 40,
            "duration_minutes": a_minutes, "unreadable": False}),
        _summary("s2", "laptop", "homelab-talos", {
            "user_message_count": 2, "assistant_message_count": 20,
            "duration_minutes": b_minutes, "unreadable": False}),
    ]


def _unreadable(session):
    return _summary(session, "workbench", "devrc", {"unreadable": True})


def _mixed_readability():
    """FIVE Layer-A rows: THREE readable (spans 20,160 + 13,440 + 6,720 =
    40,320 min = 672.0 h) and TWO unreadable.

    Every count in play is pairwise distinct — 5 rows / 2 unreadable / 3 spans /
    14 days — so an operand swap cannot masquerade as the right answer, and the
    three span values are distinct too so a summation bug cannot cancel out.
    The unreadable rows carry NO `duration_minutes`: the aggregate loop
    `continue`s on them before reaching it, which is precisely why the printed
    denominator must be 3 and not 5."""
    return [
        _summary("r1", "workbench", "devrc", {
            "user_message_count": 4, "assistant_message_count": 40,
            "duration_minutes": 20160, "unreadable": False}),
        _summary("r2", "laptop", "homelab-talos", {
            "user_message_count": 2, "assistant_message_count": 20,
            "duration_minutes": 13440, "unreadable": False}),
        _summary("r3", "workbench", "devrc", {
            "user_message_count": 1, "assistant_message_count": 10,
            "duration_minutes": 6720, "unreadable": False}),
        _unreadable("u1"),
        _unreadable("u2"),
    ]


def _one_insight():
    """ONE Layer-B row, used against a Layer-A population of a DIFFERENT size so
    the two denominators are never interchangeable in an assertion."""
    return [{"session": "s1", "payload": json.dumps({
        "outcome": "fully_achieved", "session_type": "feature_build",
        "goal_categories": ["infra"], "claude_helpfulness": 5,
        "friction_counts": {"wrong_approach": 2}, "friction_detail": [],
        "automation_opportunity": {"present": True, "description": "wrap deploy dance",
                                   "trigger": "hand-typed switch", "leverage": "high",
                                   "evidence": "did it by hand"},
        "recurring_toil": None, "workflow_gap": None, "unreadable": False})}]


def _four_readable():
    """FOUR readable Layer-A sessions, for the denominator/window tests. Paired
    with `_one_insight()` and `days=7` every number in the assertions is
    pairwise distinct: 4 Layer-A / 1 Layer-B / 7d Layer-A / 30d Layer-B."""
    return [_summary(f"q{i}", "workbench", "devrc", {
        "user_message_count": 1, "assistant_message_count": 1,
        "duration_minutes": 10 * (i + 1), "unreadable": False}) for i in range(4)]


# --------------------------------------------------------------------------- #
# H1 — the time figure
# --------------------------------------------------------------------------- #
def test_window_wall_clock_bound_is_reported():
    """REGRESSION. The report must carry the number of hours that ACTUALLY exist
    in its window, so a span sum can be checked against it without arithmetic."""
    d = I.aggregate(_spans(25000, 15320), [], [], 14, None)
    assert d["window_wall_clock_hours"] == 336          # 14 * 24, pinned literally
    assert d["session_span_hours"] == 672.0             # 40320 / 60, pinned literally


def test_span_total_is_not_called_wall_clock():
    """REGRESSION. `wall-clock` reads as elapsed time actually spent. The figure
    is Σ(last message − first message), idle-inclusive and overlapping."""
    d = I.aggregate(_spans(25000, 15320), [], [], 14, None)
    text = I.render(d)
    assert "wall-clock:" not in text
    assert "session span:" in text


def test_span_total_is_printed_beside_the_windows_real_bound():
    """REGRESSION. 672.0h cannot be time worked inside a window holding 336h —
    the report must print both numbers so that is visible without a calculator."""
    d = I.aggregate(_spans(25000, 15320), [], [], 14, None)
    line = _line(I.render(d), "session span:")
    assert "672.0h" in line
    assert "336h" in line
    assert "2.0x MORE THAN EXISTS" in line


def test_span_total_carries_the_idle_inclusive_caveat():
    """REGRESSION. The caveat naming BOTH mechanisms (idle + overlap) must ride
    with the number, not live only in a source comment."""
    text = I.render(I.aggregate(_spans(25000, 15320), [], [], 14, None))
    assert "IDLE-INCLUSIVE" in text
    assert "overlap across concurrent sessions" in text
    assert "no hours-worked figure" in text


def test_no_impossibility_marker_when_the_span_fits_the_window():
    """POSITIVE/NEGATIVE CONTROL PAIR for the marker above. A span sum that fits
    inside the window must NOT be flagged — otherwise the flag in the previous
    test would be indistinguishable from a marker that is always printed."""
    d = I.aggregate(_spans(30, 30), [], [], 14, None)   # 60 min = 1.0h << 336h
    line = _line(I.render(d), "session span:")
    assert "1.0h" in line and "336h" in line
    assert "MORE THAN EXISTS" not in line
    # …and the caveat still rides along, because idle/overlap apply either way.
    assert "IDLE-INCLUSIVE" in I.render(d)


def test_span_denominator_counts_only_sessions_that_contributed_a_span():
    """REGRESSION (audit round 2, finding 1).

    The span sum's divisor is NOT `len(summary_rows)`: aggregate()'s loop
    `continue`s on an unreadable row BEFORE reaching `duration_minutes`, so an
    unreadable session contributes nothing. Printing the all-rows count beside
    the sum made the report contradict itself two lines apart — `sessions: 5 (2
    unreadable)` above `summed over 5 session spans` — which is the
    undisclosed-denominator defect this file exists to close, re-created inside
    the fix for it.

    Fixture: 5 rows, 2 unreadable, 3 readable spans summing to 40,320 min. The
    right answer (3) and the wrong one (5) are distinct, as are the days (14)
    and the unreadable count (2), so no operand swap yields a passing value.

    Split from the render-level test below deliberately: asserting the data key
    FIRST would KeyError at the pre-fix tip and the rendered line — the thing a
    reader actually sees — would never be exercised."""
    d = I.aggregate(_mixed_readability(), [], [], 14, None)
    assert d["sessions"] == 5
    assert d["unreadable_sessions"] == 2
    assert d["session_span_hours"] == 672.0        # 40320 / 60
    assert d["totals"]["duration_minutes"] == 40320
    assert d["session_span_sessions"] == 3


def test_span_line_discloses_the_readable_denominator():
    """REGRESSION (audit round 2, finding 1) — the RENDERED half.

    Reaches the span line with NO dependency on the new data key, so it fails on
    the wrong printed number rather than on a KeyError from an earlier
    assertion. At the pre-fix tip this line read `summed over 5 session spans`
    two lines below `sessions: 5 (2 unreadable)` — self-contradicting on one
    screen."""
    text = I.render(I.aggregate(_mixed_readability(), [], [], 14, None))
    assert "sessions:   5  (2 unreadable)" in text
    line = _line(text, "session span:")
    assert "672.0h summed over 3 session spans" in line
    assert "5 session spans" not in line


def test_hours_figure_ledger():
    """SEAM GUARD (pins a RELATIONSHIP, fails if the set grows OR shrinks).

    Exactly ONE renderer emits an hours figure — `render`. `render_html` emits
    none, which is why it needs no span caveat. If a future change adds an hours
    stat tile to the HTML page this goes red, forcing the caveat decision to be
    made again rather than inherited silently."""
    d = I.aggregate(_spans(25000, 15320), [], _one_insight(), 14, None)
    hours_rx = re.compile(r"\d[\d,]*\.\d+\s*h\b")
    emitters = {name for name, out in (("render", I.render(d)),
                                       ("render_html", I.render_html(d)))
                if hours_rx.search(out)}
    assert emitters == {"render"}, (
        "a renderer's hours-figure status changed; if render_html now prints "
        "hours it MUST carry SESSION_SPAN_CAVEAT — see insights.py")


# --------------------------------------------------------------------------- #
# H2 / H3 — window + denominator disclosure
# --------------------------------------------------------------------------- #
# All four disclosure tests share one pairwise-distinct case so that NO pair of
# reported numbers is interchangeable: 4 Layer-A sessions · 1 Layer-B insight ·
# 7d Layer-A window · 30d Layer-B window.
def _disclosure_case():
    return I.aggregate(_four_readable(), [], _one_insight(), 7, None)


def test_disclosure_fixture_numbers_are_pairwise_distinct():
    """INVARIANT GUARD (not regression) — the precondition the four tests below
    rest on. If these ever collide, a swapped operand starts passing silently."""
    d = _disclosure_case()
    nums = [d["sessions"], d["insight_sessions"], d["days"], d["insight_days"]]
    assert nums == [4, 1, 7, 30]
    assert len(set(nums)) == len(nums)


def test_text_report_names_the_layer_b_denominator():
    """REGRESSION. The outcome percentages are over the Layer-B population (1),
    not the ACTIVITY `sessions:` figure (4). Both numbers must appear next to
    the percentages, and both are pinned by VALUE."""
    pop = _line(I.render(_disclosure_case()), "population:")
    assert "population: 1 session(s) with a Layer-B insight" in pop
    assert "in the trailing 30d window" in pop
    assert "NOT the 4 Layer-A session(s)" in pop
    assert "in the 7d window above" in pop


def test_html_report_labels_the_layer_b_window():
    """REGRESSION. The HTML page's subtitle claims a 7d window; its OUTCOMES
    section is drawn from 30d. The heading must say so."""
    heading = re.search(r"<h2>OUTCOMES[^<]*</h2>", I.render_html(_disclosure_case())).group(0)
    assert "trailing 30d" in heading
    assert "trailing 7d" not in heading


def test_html_report_names_the_layer_b_denominator():
    """REGRESSION (strengthened in audit round 2, finding 3).

    The first version asserted only the substring AFTER the count's `</b>`, so
    swapping `insight_sessions` for `sessions` in render_html left it green
    while the page printed the Layer-A count as the Layer-B population — the
    exact asymmetric-surface defect this PR set out to close, re-created in the
    guard added to close it. Both numbers are now pinned BY VALUE, inside one
    contiguous match so the count cannot drift away from its own label."""
    htm = I.render_html(_disclosure_case())
    assert ("Population: <b>1</b> session(s) with a Layer-B insight in the "
            "trailing <b>30d</b> window") in htm
    assert "not</b> the 4 Layer-A session(s) in the 7d window above" in htm


def test_html_subtitle_scopes_its_session_count_to_layer_a():
    """REGRESSION. A bare `4 sessions` in the subtitle is the ambiguity that let
    four populations share one document in the built-in's report."""
    sub = re.search(r'<p class="sub">(.*?)</p>', I.render_html(_disclosure_case()), re.S).group(1)
    assert "Layer A: trailing 7d" in sub
    assert "4 Layer-A sessions" in sub


def test_html_labels_model_surfaced_candidates_as_unmeasured():
    """REGRESSION. `render()` has always carried this NOTE; the HTML page
    rendered model-authored free text beside deterministic counts with no
    provenance label at all."""
    htm = I.render_html(_disclosure_case())
    assert "wrap deploy dance" in htm            # the free text IS rendered…
    assert "NOT measured savings" in htm         # …and IS labelled as unmeasured


def test_layer_b_freetext_is_never_rendered_without_a_provenance_label():
    """SEAM GUARD across BOTH renderers. Model-authored strings and measured
    counts share one document; whichever renderer emits the former must also
    emit the label. Each surface was individually 'fine' — the defect lived in
    the pair."""
    d = I.aggregate(_spans(25000, 15320), [], _one_insight(), 14, None)
    for name, out in (("render", I.render(d)), ("render_html", I.render_html(d))):
        if "wrap deploy dance" in out:
            assert "NOT measured savings" in out, f"{name} renders model free text unlabelled"


# --------------------------------------------------------------------------- #
# H4 — the anti-confabulation contract: a DOCUMENTED-LIMIT guard, NOT regression
# --------------------------------------------------------------------------- #
def test_anti_confabulation_contract_is_prompt_only_not_validated():
    """DOCUMENTED-LIMIT GUARD (deliberately not a regression test — nothing was
    changed here).

    `ANTI_CONFABULATION_CONTRACT` forbids inventing counts/limits, but
    `validate()` polices STRUCTURE (types, closed enums, the unreadable/reason
    invariant) — it does not and cannot read prose. A payload carrying the exact
    two claims the built-in confabulated on 2026-08-05 validates cleanly.

    This is pinned so the limit is discovered by a red test rather than by
    trusting the contract string, and so anyone adding a content check has to
    decide it here. A keyword scan was considered and REJECTED: it would pass
    while the same hazard appears in any other wording (a spelled guard, not a
    structural one). The real mitigation is the provenance labels asserted
    above — the report never presents this text as measured."""
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "session_insight"))
    import schema as S

    confabulated = {
        "session": "x", "underlying_goal": "g", "goal_categories": ["infra"],
        "outcome": "not_achieved", "session_type": "investigation",
        "claude_helpfulness": 1, "friction_counts": {},
        "friction_detail": ["at least 12 sessions produced nothing but "
                            "output-token-limit errors"],
        "primary_success": "",
        "brief_summary": "A 633-minute session produced no analyzable content.",
        "automation_opportunity": None, "recurring_toil": None,
        "workflow_gap": None, "unreadable": False, "unreadable_reason": "",
    }
    assert S.validate(confabulated) == []
    assert S.vocab_warnings(confabulated) == []
    # The contract text itself must still name the failure mode it forbids.
    assert "output-token maximum" in S.ANTI_CONFABULATION_CONTRACT
    assert "MUST NOT invent any count, limit, or metric" in S.ANTI_CONFABULATION_CONTRACT
