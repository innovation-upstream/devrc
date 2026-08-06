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


def _one_insight():
    """ONE Layer-B row against TWO Layer-A sessions — so the two denominators
    are distinguishable in the output (1 vs 2)."""
    return [{"session": "s1", "payload": json.dumps({
        "outcome": "fully_achieved", "session_type": "feature_build",
        "goal_categories": ["infra"], "claude_helpfulness": 5,
        "friction_counts": {"wrong_approach": 2}, "friction_detail": [],
        "automation_opportunity": {"present": True, "description": "wrap deploy dance",
                                   "trigger": "hand-typed switch", "leverage": "high",
                                   "evidence": "did it by hand"},
        "recurring_toil": None, "workflow_gap": None, "unreadable": False})}]


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
def test_text_report_names_the_layer_b_denominator():
    """REGRESSION. The outcome percentages are over the Layer-B population (1
    here), not the ACTIVITY `sessions:` figure (2). Both numbers must appear
    next to the percentages."""
    d = I.aggregate(_spans(25000, 15320), [], _one_insight(), 14, None)
    text = I.render(d)
    assert d["sessions"] == 2 and d["insight_sessions"] == 1     # fixture sanity
    pop = _line(text, "population:")
    assert "1 session(s) with a Layer-B insight" in pop
    assert "trailing 30d window" in pop
    assert "2 Layer-A session(s)" in pop
    assert "in the 14d window above" in pop


def test_html_report_labels_the_layer_b_window():
    """REGRESSION. The HTML page's subtitle claims a 14d window; its OUTCOMES
    section is drawn from 30d. The heading must say so."""
    d = I.aggregate(_spans(25000, 15320), [], _one_insight(), 14, None)
    htm = I.render_html(d)
    heading = re.search(r"<h2>OUTCOMES[^<]*</h2>", htm).group(0)
    assert "trailing 30d" in heading


def test_html_report_names_the_layer_b_denominator():
    """REGRESSION. Same disclosure as the text renderer, on the page that had
    none of it."""
    htm = I.render_html(I.aggregate(_spans(25000, 15320), [], _one_insight(), 14, None))
    assert "Population:" in htm
    assert "session(s) with a Layer-B insight in the trailing <b>30d</b> window" in htm
    assert "2 Layer-A session(s) in the 14d window above" in htm


def test_html_subtitle_scopes_its_session_count_to_layer_a():
    """REGRESSION. A bare `2 sessions` in the subtitle is the ambiguity that let
    four populations share one document in the built-in's report."""
    htm = I.render_html(I.aggregate(_spans(25000, 15320), [], _one_insight(), 14, None))
    sub = re.search(r'<p class="sub">(.*?)</p>', htm, re.S).group(1)
    assert "Layer A: trailing 14d" in sub
    assert "2 Layer-A sessions" in sub


def test_html_labels_model_surfaced_candidates_as_unmeasured():
    """REGRESSION. `render()` has always carried this NOTE; the HTML page
    rendered model-authored free text beside deterministic counts with no
    provenance label at all."""
    htm = I.render_html(I.aggregate(_spans(25000, 15320), [], _one_insight(), 14, None))
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
