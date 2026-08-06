"""Unit tests for espanso-usage.py — the verdict classifier + every silent zero.

No live ClickHouse and no PyYAML: fake `activity.events` rows go through a
FakeClient (the test_adoption_scan.py style), and trigger sets are built from
pre-loaded dicts, which `espanso_triggers.load_triggers` accepts natively.

🔴 The reassuring answer this tool produces is a ZERO ("0 fires", "0 demand",
"no findings"), and a zero is indistinguishable from a harness wired to nothing.
So every counting/classification section here is exercised with a POSITIVE
CONTROL in the same test as its zero: the assertions come in pairs, `N` on a
case that MUST count and `0` on the case under test.
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "espanso-usage.py"
_spec = importlib.util.spec_from_file_location("espanso_usage", SCRIPT)
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)

ET = M.ET
Q = M.Q


# --------------------------------------------------------------------------- #
# Fixtures — the real 2026-08-05 snippet SHAPES (values are placeholders; the
# fields are pairwise distinct so a wrong-field bug cannot pass by coincidence).
# --------------------------------------------------------------------------- #
EOS = {"trigger": ":eos",
       "replace": "reflect on work done and key learnings this session, then "
                  "write the handoff FIRST",
       "label": "End-of-session ritual: handoff first",
       "search_terms": ["end", "ritual", "wrap"]}
ACQ = {"trigger": ":acq",
       "replace": "dispatch subagent to process feedback\nask me clarifying "
                  "questions and recommend anything you think would be useful",
       "label": "Process feedback: dispatch subagent",
       "search_terms": ["feedback", "clarify"]}
SSHWN = {"trigger": ":sshwn", "replace": "ssh user@198.51.100.10",
         "label": "SSH workbench (nebula)",
         "search_terms": ["ssh", "workbench", "nebula"]}
SSHWL = {"trigger": ":sshwl", "replace": "ssh user@198.51.100.11",
         "label": "SSH workbench (LAN)",
         "search_terms": ["ssh", "workbench", "lan"]}
SSHLN = {"trigger": ":sshln", "replace": "ssh user@198.51.100.12",
         "label": "SSH laptop (nebula)",
         "search_terms": ["ssh", "laptop", "nebula"]}
SSHLL = {"trigger": ":sshll", "replace": "ssh user@198.51.100.13",
         "label": "SSH laptop (LAN)",
         "search_terms": ["ssh", "laptop", "lan"]}
DATE = {"trigger": ":date", "replace": "{{mydate}}", "label": "Today's date",
        "search_terms": ["today", "calendar"]}
TYPO = {"trigger": "dashbaord", "replace": "dashboard"}
DEADSNIP = {"trigger": ":zqx", "replace": "an expansion nobody has ever wanted",
            "label": "Dead snippet", "search_terms": ["zqx"]}
PATHSNIP = {"trigger": ":hlt", "replace": "/w/homelab-talos ",
            "label": "homelab-talos path", "search_terms": ["infra"]}

ALL_MATCHES = [EOS, ACQ, SSHWN, SSHWL, SSHLN, SSHLL, DATE, TYPO, DEADSNIP,
               PATHSNIP]
SSH_FAMILY = [SSHWN, SSHWL, SSHLN, SSHLL]


def _ts(matches=None):
    return ET.load_triggers({"matches": list(matches or ALL_MATCHES)})


def _fire(trigger, method, fires, host="workbench", inferred=False):
    return {"trigger": trigger, "method": method, "inferred": inferred,
            "fires": fires, "host": host}


def _term(trigger, term, n, host="workbench"):
    return {"trigger": trigger, "term": term, "n": n, "host": host}


class FakeClient:
    """Answers the two keylog queries from fabricated rows, and HONOURS the
    `host = '...'` predicate so a host test is behavioural, not just textual."""

    def __init__(self, fire_rows=(), term_rows=()):
        self.fire_rows = list(fire_rows)
        self.term_rows = list(term_rows)
        self.seen = []

    def rows(self, sql):
        self.seen.append(sql)
        src = self.fire_rows if "GROUP BY trigger, method, inferred" in sql \
            else self.term_rows
        import re as _re
        m = _re.search(r"host = '([^']*)'", sql)
        if m:
            src = [r for r in src if r.get("host") == m.group(1)]
        return [{k: v for k, v in r.items() if k != "host"} for r in src]


class BoomClient:
    def __init__(self, exc):
        self.exc = exc

    def rows(self, sql):
        raise self.exc


def _patch_client(monkeypatch, client):
    conn = Q.CHConn(url="http://x", user="u", password="p")
    monkeypatch.setattr(M, "open_client", lambda: (client, conn))


# --------------------------------------------------------------------------- #
# expansion_kind / is_text_detectable
# --------------------------------------------------------------------------- #
def test_expansion_kind_covers_every_non_detectable_shape():
    assert M.expansion_kind("{{mydate}}") == "template"
    assert M.expansion_kind("ssh user@198.51.100.10") == "shell"
    assert M.expansion_kind("dashboard") == "typo"
    assert M.expansion_kind("/w/homelab-talos ") == "text"
    assert M.expansion_kind("recommend next actions") == "text"
    # positive control on the predicate: exactly one of these is detectable
    kinds = [M.is_text_detectable(x["replace"]) for x in
             (DATE, SSHWN, TYPO, PATHSNIP)]
    assert kinds == [False, False, False, True]


# --------------------------------------------------------------------------- #
# classify() — one literal expectation per verdict class
# --------------------------------------------------------------------------- #
def test_classify_healthy_when_it_fires():
    assert M.classify(fires=72, demand=0, term_evidence=0,
                      text_detectable=True) == "HEALTHY"


def test_classify_unfindable_is_the_acq_case():
    """:acq 2026-08-05 — 0 fires, 36 real pastes. Pruning it would be wrong."""
    assert M.classify(fires=0, demand=36, term_evidence=0,
                      text_detectable=True) == "UNFINDABLE"


def test_classify_unattributable_is_the_sshwn_case():
    """:sshwn — not text-detectable, 0 fires, but 13 unattributed terms match
    it. Checked BEFORE the KEYLOG-ONLY branch, or it reads as 'no signal'."""
    assert M.classify(fires=0, demand=None, term_evidence=13,
                      text_detectable=False) == "UNATTRIBUTABLE"


def test_classify_keylog_only_is_the_date_case():
    assert M.classify(fires=0, demand=None, term_evidence=0,
                      text_detectable=False) == "KEYLOG-ONLY"


def test_classify_dead_needs_every_signal_measured_and_zero():
    assert M.classify(fires=0, demand=0, term_evidence=0,
                      text_detectable=True) == "DEAD"


def test_classify_unprobed_when_demand_was_never_measured():
    """A text-detectable snippet with no DEMAND_TEXTS entry is UNMEASURED, and
    must never be reported DEAD — that is how a new snippet gets pruned."""
    assert M.classify(fires=0, demand=None, term_evidence=0,
                      text_detectable=True) == "UNPROBED"


def test_classify_retired_for_a_trigger_no_longer_in_the_config():
    assert M.classify(fires=1, demand=None, term_evidence=0,
                      text_detectable=False, in_config=False) == "RETIRED"


def test_every_verdict_has_an_action_string():
    for v in (M.HEALTHY, M.UNFINDABLE, M.UNATTRIBUTABLE, M.KEYLOG_ONLY,
              M.UNPROBED, M.DEAD, M.RETIRED):
        assert M.VERDICT_ACTION[v]


def test_classify_branch_order_each_branch_is_reachable():
    """Every branch must be reachable — an earlier one always winning would make
    a later verdict dead code that no single-case test can detect."""
    seen = {
        M.classify(fires=1, demand=5, term_evidence=5, text_detectable=True),
        M.classify(fires=0, demand=5, term_evidence=5, text_detectable=True),
        M.classify(fires=0, demand=0, term_evidence=5, text_detectable=True),
        M.classify(fires=0, demand=None, term_evidence=0, text_detectable=False),
        M.classify(fires=0, demand=None, term_evidence=0, text_detectable=True),
        M.classify(fires=0, demand=0, term_evidence=0, text_detectable=True),
        M.classify(fires=0, demand=0, term_evidence=0, text_detectable=True,
                   in_config=False),
    }
    assert seen == {"HEALTHY", "UNFINDABLE", "UNATTRIBUTABLE", "KEYLOG-ONLY",
                    "UNPROBED", "DEAD", "RETIRED"}


# --------------------------------------------------------------------------- #
# term_evidence — POSITIVE CONTROL on a counting section
# --------------------------------------------------------------------------- #
def test_term_evidence_counts_real_multiword_terms_and_ignores_noise():
    ts = _ts()
    # positive control: 'ssh work' is a real observed query and matches both
    # workbench ssh snippets -> non-zero for each.
    weight, detail = M.term_evidence([("ssh work", 5)], ts)
    assert weight[":sshwn"] == 5 and weight[":sshwl"] == 5
    assert detail[":sshwn"][0][2] == 2          # it matched 2 snippets
    # under test: a 1-char term matches half the config through search_terms
    # substrings and is noise -> zero, NOT because the harness is inert.
    noise, _ = M.term_evidence([("c", 9)], ts)
    assert sum(noise.values()) == 0


def test_term_evidence_drops_terms_that_match_too_many_snippets():
    """Measured at 4 snippets and at 5 — LITERAL counts, not `cap` and `cap+1`.

    Two earlier drafts of this test were vacuous: the first used a 1-char term,
    which MIN_LEN rejected before the cap ever ran; the second expressed both
    points as `cap`/`cap+1`, so raising the constant to 9999 still passed.
    """
    def _shared(n):
        return ET.load_triggers({"matches": [
            {"trigger": f":w{i}", "replace": "a phrase", "label": "x",
             "search_terms": ["sharedterm"]} for i in range(n)]})

    assert sum(M.term_evidence([("sharedterm", 7)], _shared(4))[0].values()) == 28
    assert sum(M.term_evidence([("sharedterm", 7)], _shared(5))[0].values()) == 0
    # and a real observed term at the boundary is still counted
    assert M.term_evidence([("ssh", 4)], _ts())[0][":sshwn"] == 4


def test_evidence_thresholds_are_pinned_to_the_real_config_shape():
    """Literal values, tied to WHY they are those values: the largest legitimate
    ambiguity in the live config is the 4-member :ssh* family, and the shortest
    real observed query is 3 chars ('ssh', 'gpu', 'pro')."""
    assert M.UNATTR_EVIDENCE_MAX_MATCHES == 4
    assert M.UNATTR_EVIDENCE_MIN_LEN == 3


# --------------------------------------------------------------------------- #
# aggregate_fires / split_terms — POSITIVE CONTROL
# --------------------------------------------------------------------------- #
def test_aggregate_fires_splits_direct_and_search():
    per, total = M.aggregate_fires([
        _fire(":eos", "search", 72, inferred=True),
        _fire("dashbaord", "direct", 5),
        _fire("", "search", 46, inferred=True),
    ])
    assert total == 123
    assert per[":eos"] == {"direct": 0, "search": 72, "inferred": True}
    assert per["dashbaord"]["direct"] == 5
    assert per[M.UNATTRIBUTED]["search"] == 46
    # zero under test, with the positive control above proving it can move
    empty_per, empty_total = M.aggregate_fires([])
    assert empty_total == 0 and empty_per == {}


def test_split_terms_separates_attributed_from_unattributed():
    attributed, unattributed = M.split_terms([
        _term(":eos", "rit", 72), _term("", "ssh work", 5)])
    assert attributed == [("rit", ":eos", 72)]
    assert unattributed == [("ssh work", 5)]


# --------------------------------------------------------------------------- #
# 🔴 B1 — an unreachable/rejecting ClickHouse must NEVER read as zero fires
# --------------------------------------------------------------------------- #
def test_gather_fires_raises_on_unreachable_instead_of_returning_zero():
    with pytest.raises(M.FiresUnmeasured) as ei:
        M.gather_fires(BoomClient(Q.CHUnreachable("connection refused")))
    assert ei.value.reason == "unreachable"


def test_gather_fires_distinguishes_a_rejected_query():
    with pytest.raises(M.FiresUnmeasured) as ei:
        M.gather_fires(BoomClient(Q.CHQueryError("Code: 241", code=241)))
    assert ei.value.reason == "query"


def test_main_unreachable_is_loud_and_is_not_the_no_events_wording(
        monkeypatch, capsys):
    _patch_client(monkeypatch, BoomClient(Q.CHUnreachable("refused")))
    rc = M.main(["--source", "keys"])
    out = capsys.readouterr().out
    assert rc == 3
    assert "FIRES UNMEASURED" in out and "UNREACHABLE" in out
    assert "Do NOT prune" in out
    # the exact wording the predecessor printed for a DOWN server
    assert "no keylog espanso events yet" not in out


def test_main_rejected_query_reads_differently_from_unreachable(
        monkeypatch, capsys):
    _patch_client(monkeypatch, BoomClient(Q.CHQueryError("Code: 241")))
    rc = M.main(["--source", "keys"])
    out = capsys.readouterr().out
    assert rc == 3 and "REJECTED" in out and "UNREACHABLE" not in out


def test_main_measured_zero_says_forward_only_and_exits_zero(
        monkeypatch, capsys):
    """POSITIVE-CONTROL PAIR: the same code path with rows present prints a
    table and a total; with no rows it prints the forward-only note. A measured
    zero and an unmeasured one must not look alike."""
    _patch_client(monkeypatch, FakeClient([_fire(":eos", "search", 7)], []))
    assert M.main(["--source", "keys"]) == 0
    hot = capsys.readouterr().out
    assert "total fires: 7" in hot

    _patch_client(monkeypatch, FakeClient([], []))
    assert M.main(["--source", "keys"]) == 0
    cold = capsys.readouterr().out
    assert "0 espanso rows in this window" in cold and "FIRES UNMEASURED" not in cold


# --------------------------------------------------------------------------- #
# B2 — --host reaches the SQL *and* changes the answer
# --------------------------------------------------------------------------- #
def test_host_predicate_reaches_both_queries():
    assert "host = 'laptop'" in M.q_fires(host="laptop")
    assert "host = 'laptop'" in M.q_terms(host="laptop")
    assert "host" not in M.q_fires()


def test_host_filter_is_behavioural_not_just_spelled():
    rows = [_fire(":eos", "search", 11, host="laptop"),
            _fire(":eos", "search", 3, host="workbench")]
    client = FakeClient(rows, [])
    lap = M.gather_fires(client, host="laptop")
    wb = M.gather_fires(client, host="workbench")
    none = M.gather_fires(client, host="nosuchhost")
    assert lap["total"] == 11 and wb["total"] == 3        # positive controls
    assert none["total"] == 0                              # zero under test


def test_since_is_quoted_into_the_where_clause():
    assert "ts >= '2026-07-25'" in M.q_fires(since="2026-07-25")


def test_host_mismatch_refuses_rather_than_mislabelling(monkeypatch, capsys):
    """--host used to be printed in the header and then ignored, so
    `--host laptop` stamped a workbench-local transcript scan as laptop data."""
    monkeypatch.setattr(M, "local_host_label", lambda *a, **k: "workbench")
    rc = M.main(["--host", "laptop"])
    assert rc == 2
    assert "reads the LOCAL transcripts" in capsys.readouterr().err


def test_host_matching_local_label_is_allowed(monkeypatch, capsys):
    monkeypatch.setattr(M, "local_host_label", lambda *a, **k: "workbench")
    _patch_client(monkeypatch, FakeClient([_fire(":eos", "search", 2)], []))
    assert M.main(["--host", "workbench", "--source", "keys"]) == 0
    assert "fires filtered to host=workbench" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# B3/B4/B5 — boilerplate filtering, sharing the collector's constant
# --------------------------------------------------------------------------- #
def test_boilerplate_prefixes_come_from_the_shared_constant():
    for shared in M._SHARED_BOILERPLATE:
        assert shared.lower() in M.BOILERPLATE_PREFIXES


def test_capital_r_request_interrupted_is_filtered():
    """Measured 2026-08-05: 389 capital-R vs 47 lowercase in the transcripts.
    The old filter tested the lowercase spelling only."""
    assert M.is_boilerplate("[Request interrupted by user]")
    assert M.is_boilerplate("[Request interrupted by user for tool use]")
    assert M.is_boilerplate("[request interrupted by user]")


def test_capital_i_image_marker_is_filtered():
    assert M.is_boilerplate(
        "[Image: original 3434x1346, displayed at 2000x784. Multiply "
        "coordinates by 1.717]")


def test_boilerplate_filter_keeps_real_messages():
    """Positive control: the filter must not eat ordinary short messages."""
    assert not M.is_boilerplate("recommend next actions")
    assert not M.is_boilerplate("merge it")


def _write_transcript(dirpath, name, messages):
    p = Path(dirpath) / name
    lines = []
    for i, m in enumerate(messages):
        obj = {"type": "user", "uuid": f"{name}-{i}",
               "timestamp": m.get("ts", "2026-08-01T10:00:00Z"),
               "message": {"role": "user", "content": m["text"]}}
        for k in ("isMeta", "isSidechain"):
            if m.get(k):
                obj[k] = True
        lines.append(json.dumps(obj))
    p.write_text("\n".join(lines) + "\n")
    return p


def test_scan_transcripts_counts_demand_and_excludes_boilerplate(tmp_path):
    _write_transcript(tmp_path, "s1.jsonl", [
        {"text": "recommend next actions"},                    # :rna demand
        {"text": "recommend next actions"},
        {"text": "[Request interrupted by user]"},             # capital-R noise
        {"text": "[Image: original 100x100, displayed at 50x50.]"},
        {"text": "merge it"},                                   # add-candidate
    ])
    scan = M.scan_transcripts(str(tmp_path))
    # POSITIVE CONTROL: the demand counter moved for a snippet that IS present
    assert scan["demand"][":rna"] == 2
    # ...and stayed at zero for one that is not (harness proven live above)
    assert scan["demand"][":mt"] == 0
    assert scan["messages"] == 3          # 5 lines - 2 boilerplate
    short = dict(scan["short"])
    assert short.get("merge it") == 1
    assert not any("request interrupted" in k for k in short)
    assert not any(k.startswith("[image:") for k in short)


def test_scan_transcripts_skips_meta_and_sidechain_copies(tmp_path):
    _write_transcript(tmp_path, "s2.jsonl", [
        {"text": "tee up what we can do in the meantime"},
        {"text": "tee up what we can do in the meantime", "isSidechain": True},
        {"text": "tee up what we can do in the meantime", "isMeta": True},
    ])
    scan = M.scan_transcripts(str(tmp_path))
    assert scan["demand"][":mt"] == 1     # 1 real, 2 copies dropped


def test_scan_transcripts_honours_since(tmp_path):
    _write_transcript(tmp_path, "s3.jsonl", [
        {"text": "recommend next actions", "ts": "2026-07-01T10:00:00Z"},
        {"text": "recommend next actions", "ts": "2026-08-01T10:00:00Z"},
    ])
    assert M.scan_transcripts(str(tmp_path))["demand"][":rna"] == 2
    assert M.scan_transcripts(str(tmp_path), since="2026-07-15")["demand"][":rna"] == 1


def test_scan_transcripts_excludes_the_containing_expansion(tmp_path):
    """:cdp's path is a prefix of :cpk's — a :cpk paste must not count as :cdp."""
    _write_transcript(tmp_path, "s4.jsonl", [
        {"text": "look at /w/civit/datapacket-talos/prod-kubeconfig please"},
    ])
    scan = M.scan_transcripts(str(tmp_path))
    assert scan["demand"][":cpk"] == 1
    assert scan["demand"][":cdp"] == 0


# --------------------------------------------------------------------------- #
# build_verdicts over the real 2026-08-05 shapes
# --------------------------------------------------------------------------- #
def test_build_verdicts_reproduces_the_2026_08_05_reading():
    ts = _ts()
    fires = {":eos": {"direct": 0, "search": 72},
             "dashbaord": {"direct": 5, "search": 0},
             ":rns": {"direct": 0, "search": 1},        # pruned -> RETIRED
             "": {"direct": 0, "search": 46}}
    demand = {":eos": 71, ":acq": 36, ":zqx": 0, ":hlt": 344}
    evidence, _ = M.term_evidence([("ssh work", 13)], ts)
    rows = {r["trigger"]: r for r in
            M.build_verdicts(ts, fires, demand, evidence)}
    assert rows[":eos"]["verdict"] == "HEALTHY" and rows[":eos"]["fires"] == 72
    assert rows["dashbaord"]["verdict"] == "HEALTHY"
    assert rows[":acq"]["verdict"] == "UNFINDABLE"
    assert rows[":sshwn"]["verdict"] == "UNATTRIBUTABLE"
    assert rows[":sshwn"]["term_evidence"] == 13
    assert rows[":date"]["verdict"] == "KEYLOG-ONLY"
    assert rows[":zqx"]["verdict"] == "DEAD"
    assert rows[":hlt"]["verdict"] == "HEALTHY" or rows[":hlt"]["fires"] == 0
    assert rows[":rns"]["verdict"] == "RETIRED"
    assert M.UNATTRIBUTED not in rows      # the unattributed bucket is not a snippet


def test_build_verdicts_marks_a_missing_demand_probe_unprobed():
    ts = _ts([PATHSNIP])
    rows = M.build_verdicts(ts, {}, {}, {})
    assert rows[0]["verdict"] == "UNPROBED"


def test_render_verdicts_prints_the_action_column():
    ts = _ts([ACQ])
    text = "\n".join(M.render_verdicts(M.build_verdicts(ts, {}, {":acq": 36}, {})))
    assert "UNFINDABLE" in text and "do NOT prune" in text


# --------------------------------------------------------------------------- #
# 🔴 config silent zero — an unparseable config is not "0 snippets"
# --------------------------------------------------------------------------- #
class _FakeYaml:
    @staticmethod
    def safe_load(text):
        return json.loads(text)


def test_parse_config_text_raises_when_pyyaml_is_missing(monkeypatch):
    monkeypatch.setattr(M, "_yaml", None)
    with pytest.raises(M.ConfigUnavailable) as ei:
        M.parse_config_text("matches: []")
    assert ei.value.reason == "pyyaml"


def test_parse_config_text_raises_on_zero_triggers(monkeypatch):
    """`load_triggers` degrades to an empty TriggerSet by design (the keylogger
    must not crash). For an AUDIT that degradation IS the silent zero."""
    monkeypatch.setattr(M, "_yaml", _FakeYaml)
    with pytest.raises(M.ConfigUnavailable) as ei:
        M.parse_config_text(json.dumps({"matches": []}))
    assert ei.value.reason == "empty"


def test_parse_config_text_positive_control(monkeypatch):
    monkeypatch.setattr(M, "_yaml", _FakeYaml)
    ts = M.parse_config_text(json.dumps({"matches": [EOS, DATE]}))
    assert ts.triggers == [":eos", ":date"]


def test_load_config_raises_on_a_missing_file(tmp_path):
    with pytest.raises(M.ConfigUnavailable) as ei:
        M.load_config(str(tmp_path / "nope.yml"))
    assert ei.value.reason == "io"


def test_main_lint_reports_config_unavailable_loudly(monkeypatch, capsys):
    monkeypatch.setattr(M, "load_config",
                        lambda p=None: (_ for _ in ()).throw(
                            M.ConfigUnavailable("boom", reason="pyyaml")))
    rc = M.main(["--lint"])
    assert rc == 3
    assert "CONFIG UNMEASURED" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# lint
# --------------------------------------------------------------------------- #
def _kinds(findings):
    return {f["kind"] for f in findings}


def test_lint_flags_a_family_with_no_uniquely_resolving_term():
    """The :ssh* case: in the real 4-way family every declared term is shared
    with a sibling ('ssh' with all, 'workbench'/'lan'/'nebula'/'laptop' with
    one), so `_attribute` returns None for all of them and none can ever fire
    from the search UI. Two of the four would still each have a unique term —
    the defect only exists at the full family size."""
    findings = M.lint(_ts(SSH_FAMILY))
    unreachable = {f["trigger"] for f in findings if f["kind"] == "unreachable"}
    assert unreachable == {":sshwn", ":sshwl", ":sshln", ":sshll"}
    pair = {f["trigger"] for f in M.lint(_ts([SSHWN, SSHWL]))
            if f["kind"] == "unreachable"}
    assert pair == set()


def test_lint_positive_control_and_clean_config():
    # positive control: the ambiguity check DOES fire on a colliding pair
    assert "ambiguous" in _kinds(M.lint(_ts([SSHWN, SSHWL])))
    # under test: a config whose terms are disjoint yields nothing
    assert M.lint(_ts([EOS, PATHSNIP])) == []


def test_lint_flags_a_snippet_with_no_label_and_no_search_terms():
    findings = M.lint(_ts([TYPO]))
    assert [f["kind"] for f in findings] == ["undiscoverable"]


def test_lint_flags_a_trigger_that_is_a_prefix_of_another():
    findings = M.lint(_ts([DATE, {"trigger": ":datetime", "replace": "x y",
                                  "label": "Date and time",
                                  "search_terms": ["stamp"]}]))
    assert "prefix" in _kinds(findings)


def test_lint_uses_the_detectors_own_label_tokenizer():
    """A hyphenated label word must not be reported as a self-miss: the detector
    splits the label on non-alphanumerics, so 'homelab-talos' is never a token."""
    assert "self-miss" not in _kinds(M.lint(_ts([PATHSNIP])))


# --------------------------------------------------------------------------- #
# replay
# --------------------------------------------------------------------------- #
def test_replay_resolves_zero_one_and_many_matches():
    ts = _ts()
    rows = {r["term"]: r for r in M.replay_terms(
        [("ritual", 3), ("ssh work", 5), ("qqqqq", 1)], ts)}
    assert rows["ritual"]["resolves_to"] == ":eos"      # positive control
    assert rows["ritual"]["n_matches"] == 1
    assert rows["ssh work"]["n_matches"] == 2 and rows["ssh work"]["resolves_to"] is None
    assert rows["qqqqq"]["n_matches"] == 0 and rows["qqqqq"]["resolves_to"] is None


def test_replay_against_a_candidate_config_is_a_preship_gate():
    """The point of --config: prove a NEW search_terms list resolves BEFORE
    shipping it. The same term is ambiguous under the old config and unique
    under the candidate."""
    old = _ts([SSHWN, SSHWL])
    candidate = _ts([SSHWN, dict(SSHWL, search_terms=["lan"], label="LAN box")])
    assert M.replay_terms([("workbench", 1)], old)[0]["resolves_to"] is None
    assert M.replay_terms([("workbench", 1)], candidate)[0]["resolves_to"] == ":sshwn"


def test_render_replay_reports_how_many_fires_land_nowhere():
    text = "\n".join(M.render_replay(
        M.replay_terms([("ritual", 3), ("ssh work", 5)], _ts()), "cfg"))
    assert "resolves: 3/8 fires" in text and "5 would still land nowhere" in text


# --------------------------------------------------------------------------- #
# --verify-deploy, incl. the 🔴 keylog staleness guard
# --------------------------------------------------------------------------- #
CFG_TEXT = json.dumps({"matches": [EOS, DATE]})


def _runner(*, cfg_mtime, keylog_start, espanso="active", keylog_sub="running",
            cfg_text=CFG_TEXT, fail=()):
    def run(argv):
        if argv[0] in fail:
            return 1, ""
        if argv[0] == "stat":
            return 0, str(cfg_mtime)
        if argv[0] == "cat":
            return 0, cfg_text
        if argv[:3] == ["systemctl", "--user", "is-active"]:
            return 0, espanso
        if "ActiveEnterTimestamp" in argv:
            return 0, f"@{keylog_start}"
        if "SubState" in argv:
            return 0, keylog_sub
        return 1, ""
    return run


def _problems(res):
    return " | ".join(res["problems"])


def test_verify_deploy_stale_detector_is_a_loud_problem(monkeypatch):
    """POSITIVE-CONTROL PAIR for the staleness guard: a detector that started
    BEFORE the config is a problem; one that started after is not."""
    monkeypatch.setattr(M, "_yaml", _FakeYaml)
    stale = M.check_host("h", _runner(cfg_mtime=2000, keylog_start=1000), "cfg")
    assert "STALE DETECTOR" in _problems(stale)
    assert "restart keylog" in _problems(stale)
    fresh = M.check_host("h", _runner(cfg_mtime=1000, keylog_start=2000), "cfg")
    assert fresh["problems"] == []
    assert any("1000s after" in n for n in fresh["notes"])


def test_verify_deploy_flags_a_stopped_espanso_and_keylog(monkeypatch):
    monkeypatch.setattr(M, "_yaml", _FakeYaml)
    res = M.check_host("h", _runner(cfg_mtime=1, keylog_start=2,
                                    espanso="inactive", keylog_sub="dead"), "cfg")
    assert "espanso.service is 'inactive'" in _problems(res)
    assert "not 'running'" in _problems(res)


def test_verify_deploy_expectations_are_structural(monkeypatch):
    monkeypatch.setattr(M, "_yaml", _FakeYaml)
    run = _runner(cfg_mtime=1, keylog_start=2)
    ok = M.check_host("h", run, "cfg", expect=[":eos"], expect_absent=[":gone"])
    assert ok["problems"] == []                      # positive control
    bad = M.check_host("h", run, "cfg", expect=[":mt"], expect_absent=[":date"])
    assert "MISSING: :mt" in _problems(bad)
    assert "still deployed: :date" in _problems(bad)


def test_verify_deploy_unparseable_config_is_not_zero_triggers(monkeypatch):
    monkeypatch.setattr(M, "_yaml", None)
    res = M.check_host("h", _runner(cfg_mtime=1, keylog_start=2), "cfg")
    assert res["triggers"] is None
    assert "CONFIG UNPARSEABLE" in _problems(res)


def test_verify_deploy_unknown_timestamps_are_a_problem_not_a_pass(monkeypatch):
    monkeypatch.setattr(M, "_yaml", _FakeYaml)
    res = M.check_host("h", _runner(cfg_mtime=1, keylog_start=2, fail=("stat",)),
                       "cfg")
    assert "staleness UNKNOWN" in _problems(res)


def test_unix_ts_parses_systemd_output():
    assert M._unix_ts("@1785986153") == 1785986153
    assert M._unix_ts("1785986153") == 1785986153
    assert M._unix_ts("n/a") is None


def test_render_verify_reports_host_divergence(monkeypatch):
    monkeypatch.setattr(M, "_yaml", _FakeYaml)
    a = M.check_host("a", _runner(cfg_mtime=1, keylog_start=2), "cfg")
    b = M.check_host("b", _runner(cfg_mtime=1, keylog_start=2,
                                  cfg_text=json.dumps({"matches": [EOS]})), "cfg")
    text = "\n".join(M.render_verify([a, b]))
    assert "host DIVERGENCE" in text and ":date" in text
    same = "\n".join(M.render_verify([a, a]))
    assert "both hosts carry the SAME 2 triggers" in same


def test_main_verify_deploy_exit_code_signals_a_problem(monkeypatch, capsys):
    monkeypatch.setattr(M, "_yaml", _FakeYaml)
    monkeypatch.setattr(M, "make_local_runner",
                        lambda *a, **k: _runner(cfg_mtime=2000, keylog_start=1))
    assert M.main(["--verify-deploy", "--no-remote"]) == 4
    assert "STALE DETECTOR" in capsys.readouterr().out
    monkeypatch.setattr(M, "make_local_runner",
                        lambda *a, **k: _runner(cfg_mtime=1, keylog_start=2000))
    assert M.main(["--verify-deploy", "--no-remote"]) == 0


# --------------------------------------------------------------------------- #
# misc
# --------------------------------------------------------------------------- #
def test_local_host_label_prefers_env_then_collector_file(tmp_path):
    assert M.local_host_label(env={"ACTIVITY_HOST": "LapTop"}) == "laptop"
    f = tmp_path / "env"
    f.write_text('FOO=1\nACTIVITY_HOST="workbench"\n')
    assert M.local_host_label(env={}, env_file=str(f)) == "workbench"
    assert M.local_host_label(env={}, env_file=str(tmp_path / "absent")) == ""


def test_parse_args_keeps_every_legacy_flag():
    a = M.parse_args(["--since", "2026-01-01", "--source", "transcript",
                      "--root", "/tmp/x", "--host", "laptop"])
    assert (a.since, a.source, a.root, a.host) == \
        ("2026-01-01", "transcript", "/tmp/x", "laptop")


def test_bad_source_is_rejected():
    with pytest.raises(SystemExit):
        M.parse_args(["--source", "nope"])
