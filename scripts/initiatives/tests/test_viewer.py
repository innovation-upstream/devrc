"""Unit tests for the PURE render + HTTP-routing layers of viewer.py.

Offline: no live DB, no live tmux, no sockets. We feed `build_model` fixture store
rows and assert on the grouped/sorted render model (grouping by repo, momentum
ordering, rel-age formatting, the "updated Xm ago" from captured_at), assert the HTML
render given a fixture model (slug / momentum badge / tmux tag / footer), and smoke-test
`route_request` with a fake provider (/healthz ok; / and /api return 200 + markers).
The store read + tmux overlay (the I/O) are exercised only via a fake provider — mirroring
how sync.py/route.py separate the pure transform from infra."""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import viewer  # noqa: E402

NOW = datetime(2026, 7, 22, 12, 0, 0, tzinfo=timezone.utc)


def _row(**over):
    """A representative `initiatives.latest` row (the shape the viewer consumes)."""
    r = {
        "slug": "initiatives-viewer",
        "repo": "/home/zach/workspace/devrc",
        "title": "Initiatives consolidation Phase 3",
        "summary": "A live web viewer over the initiatives store.",
        "momentum": "active",
        "last_touch": NOW - timedelta(minutes=30),
        "next_step": "wire the systemd unit",
        "commits": 7,
        "commits_unknown": False,
        "merged_prs": 2,
        "open_prs": [{"number": 138, "title": "feat: viewer"}],
        "session_count": 3,
        "telem_events": 42,
        "current_doc": "/home/zach/workspace/devrc/claudedocs/handoff-x.md",
        "open_investigations": ["does the tmux overlay hold under refresh churn?"],
        "docs": [{"path": "/home/zach/workspace/devrc/claudedocs/handoff-x.md",
                  "date": "2026-07-22"}],
        "captured_at": NOW - timedelta(minutes=6),
    }
    r.update(over)
    return r


# --- rel_age ---------------------------------------------------------------- #
def test_rel_age_buckets():
    assert viewer.rel_age(NOW - timedelta(seconds=10), NOW) == "now"
    assert viewer.rel_age(NOW - timedelta(minutes=5), NOW) == "5m"
    assert viewer.rel_age(NOW - timedelta(hours=3), NOW) == "3h"
    assert viewer.rel_age(NOW - timedelta(days=2), NOW) == "2d"
    assert viewer.rel_age(NOW - timedelta(days=21), NOW) == "3w"


def test_rel_age_none_is_dash():
    assert viewer.rel_age(None, NOW) == "—"
    assert viewer.rel_age("not-a-datetime", NOW) == "—"


def test_rel_age_future_clamps_to_now():
    # clock skew: a captured_at slightly ahead of `now` must not go negative
    assert viewer.rel_age(NOW + timedelta(seconds=30), NOW) == "now"


def test_rel_age_coerces_naive_datetime_to_utc():
    naive = (NOW - timedelta(hours=2)).replace(tzinfo=None)
    assert viewer.rel_age(naive, NOW) == "2h"


# --- momentum_badge --------------------------------------------------------- #
def test_momentum_badge_known_and_unknown():
    assert viewer.momentum_badge("active") == ("●", "active")
    assert viewer.momentum_badge("slowing") == ("◐", "slowing")
    assert viewer.momentum_badge("stalled") == ("○", "stalled")
    assert viewer.momentum_badge(None) == ("·", "unknown")
    assert viewer.momentum_badge("bogus") == ("·", "unknown")


# --- build_model: grouping + ordering + freshness --------------------------- #
def test_build_model_groups_by_repo():
    rows = [
        _row(slug="a", repo="/home/zach/workspace/devrc"),
        _row(slug="b", repo="/home/zach/workspace/devrc"),
        _row(slug="c", repo="/home/zach/workspace/homelab"),
    ]
    model = viewer.build_model(rows, now=NOW)
    assert model["total"] == 3
    assert model["repo_count"] == 2
    names = {g["name"]: [i["slug"] for i in g["initiatives"]] for g in model["repos"]}
    assert names == {"devrc": ["a", "b"], "homelab": ["c"]}


def test_build_model_orders_initiatives_by_momentum_then_recency():
    rows = [
        _row(slug="stalled-old", momentum="stalled", last_touch=NOW - timedelta(days=10)),
        _row(slug="active-older", momentum="active", last_touch=NOW - timedelta(hours=5)),
        _row(slug="active-newer", momentum="active", last_touch=NOW - timedelta(minutes=5)),
        _row(slug="slowing-mid", momentum="slowing", last_touch=NOW - timedelta(days=3)),
    ]
    model = viewer.build_model(rows, now=NOW)
    order = [i["slug"] for i in model["repos"][0]["initiatives"]]
    # active (newest first), then slowing, then stalled
    assert order == ["active-newer", "active-older", "slowing-mid", "stalled-old"]


def test_build_model_orders_repos_by_best_momentum():
    rows = [
        _row(slug="q", repo="/ws/quietrepo", momentum="stalled",
             last_touch=NOW - timedelta(days=9)),
        _row(slug="h", repo="/ws/hotrepo", momentum="active",
             last_touch=NOW - timedelta(minutes=2)),
    ]
    model = viewer.build_model(rows, now=NOW)
    assert [g["name"] for g in model["repos"]] == ["hotrepo", "quietrepo"]


def test_build_model_captured_age_from_newest_captured_at():
    rows = [
        _row(slug="a", captured_at=NOW - timedelta(minutes=6)),
        _row(slug="b", captured_at=NOW - timedelta(minutes=6)),
    ]
    model = viewer.build_model(rows, now=NOW)
    assert model["captured_at"] == NOW - timedelta(minutes=6)
    assert model["captured_age"] == "6m"


def test_build_model_empty_rows_is_wellformed():
    model = viewer.build_model([], now=NOW)
    assert model["total"] == 0
    assert model["repos"] == []
    assert model["captured_at"] is None
    assert model["captured_age"] is None


def test_build_model_carries_tmux_sessions_into_view():
    rows = [_row(slug="a")]
    rows[0]["tmux_sessions"] = {"Vapor-2", "main:8-1"}
    model = viewer.build_model(rows, now=NOW)
    v = model["repos"][0]["initiatives"][0]
    assert v["tmux_sessions"] == ["Vapor-2", "main:8-1"]  # sorted


def test_build_model_none_repo_becomes_unknown_group():
    model = viewer.build_model([_row(slug="a", repo=None)], now=NOW)
    assert model["repos"][0]["name"] == "(unknown repo)"


# --- HTML render (JSON island + inline JS; cards are rendered client-side) --- #
def test_render_html_embeds_data_and_controls():
    rows = [_row(slug="initiatives-viewer")]
    rows[0]["tmux_sessions"] = {"Vapor-2"}
    model = viewer.build_model(rows, now=NOW)
    html = viewer.render_html(model)
    assert html.startswith("<!doctype html>")
    # the data the page builds from is embedded as a JSON island
    assert 'id="idata"' in html
    assert "initiatives-viewer" in html            # a slug (in the payload)
    assert "feat: viewer" in html                  # the OPEN PR TITLE, not a bare number
    assert "A live web viewer over the initiatives store." in html  # the summary
    assert "Vapor-2" in html                        # a tmux session in the payload
    # the flat/grouped toggle + search + refresh chrome
    assert 'id="view-flat"' in html and 'id="view-grouped"' in html
    assert 'id="search"' in html and 'id="refresh"' in html
    # inline JS (no external assets) with the auto-refresh interval interpolated
    assert "localStorage" in html
    assert str(viewer.REFRESH_SECONDS * 1000) in html


def test_render_html_escapes_untrusted_text_in_json_island():
    # Untrusted text is embedded in a <script type=application/json> island; markup must
    # be neutralized so it can't break out of the script element (\uXXXX is valid JSON).
    model = viewer.build_model([_row(title="<script>alert(1)</script>")], now=NOW)
    html = viewer.render_html(model)
    assert "<script>alert(1)</script>" not in html   # never raw
    assert "u003cscript" in html                      # neutralized as <


def test_render_html_footer_split_is_honest_not_hourly():
    # The confusing "updated 1 hour ago / hourly sync" footer is gone; the JS renders a
    # live-vs-snapshot split from captured_age.
    model = viewer.build_model([_row(slug="a")], now=NOW)
    html = viewer.render_html(model)
    assert "hourly sync" not in html
    assert "store synced" in html and "live sessions" in html


def test_render_html_error_page_when_store_unreachable():
    html = viewer.render_html(None, error="OperationalError: connection refused")
    assert "store unreachable" in html
    assert "connection refused" in html
    assert html.startswith("<!doctype html>")


def test_render_html_empty_model_embeds_empty_payload():
    model = viewer.build_model([], now=NOW)
    html = viewer.render_html(model)
    # payload reflects an empty snapshot; the JS renders the "No initiatives" message
    assert '"total": 0' in html or '"total":0' in html
    assert "No initiatives" in html  # the client-side empty message string


# --- JSON payload ----------------------------------------------------------- #
def test_model_to_json_ok_and_error():
    model = viewer.build_model([_row(slug="a")], now=NOW)
    ok = viewer.model_to_json(model, None)
    assert ok["ok"] is True and ok["total"] == 1 and ok["repo_count"] == 1
    err = viewer.model_to_json(None, "boom")
    assert err["ok"] is False and err["error"] == "boom" and err["repos"] == []


# --- HTTP routing (fake provider — no server, no DB) ------------------------ #
class _FakeProvider:
    def __init__(self, model=None, error=None):
        self._model = model
        self._error = error
        self.calls = 0

    def snapshot(self):
        self.calls += 1
        return self._model, self._error


def test_route_healthz_is_ok_and_store_independent():
    prov = _FakeProvider(error="db down")  # even with the store down, healthz is ok
    status, ctype, body = viewer.route_request("/healthz", prov)
    assert status == 200
    assert body == b"ok\n"
    assert prov.calls == 0  # healthz must NOT touch the store


def test_route_root_returns_html_200():
    model = viewer.build_model([_row(slug="routed-slug")], now=NOW)
    status, ctype, body = viewer.route_request("/", _FakeProvider(model=model))
    assert status == 200
    assert "text/html" in ctype
    assert b"routed-slug" in body


def test_route_root_renders_error_page_on_store_failure():
    status, ctype, body = viewer.route_request("/", _FakeProvider(error="OperationalError"))
    assert status == 200  # still a valid page, degrades gracefully
    assert b"store unreachable" in body


def test_route_api_json_200_and_parseable():
    model = viewer.build_model([_row(slug="api-slug")], now=NOW)
    status, ctype, body = viewer.route_request("/api/initiatives.json",
                                               _FakeProvider(model=model))
    assert status == 200
    assert "application/json" in ctype
    payload = json.loads(body)
    assert payload["ok"] is True
    assert payload["repos"][0]["initiatives"][0]["slug"] == "api-slug"


def test_route_unknown_path_404():
    status, _ctype, body = viewer.route_request("/nope", _FakeProvider())
    assert status == 404
    assert b"not found" in body


# --- DataProvider (caching + graceful error, fake loader/tmux) -------------- #
def test_provider_caches_within_ttl():
    calls = {"n": 0}

    def loader():
        calls["n"] += 1
        return [_row(slug="cached")]

    prov = viewer.DataProvider(ttl=60, loader=loader, tmux=lambda rows: True,
                               now_fn=lambda: NOW)
    m1, e1 = prov.snapshot()
    m2, e2 = prov.snapshot()
    assert e1 is None and e2 is None
    assert calls["n"] == 1  # second call served from cache
    assert m1 is m2


def test_provider_returns_error_tuple_on_loader_failure():
    def boom():
        raise RuntimeError("port-forward exited early")

    prov = viewer.DataProvider(ttl=60, loader=boom, tmux=lambda rows: True,
                               now_fn=lambda: NOW)
    model, error = prov.snapshot()
    assert model is None
    assert "port-forward exited early" in error
    assert error.startswith("RuntimeError")


def test_attach_tmux_is_best_effort_on_scan_failure(monkeypatch):
    # The live tmux overlay is a nicety: if importing/using the scan blows up, attach_tmux
    # must swallow it, return [] (no unmatched), and leave the rows untouched (overlay absent).
    def boom():
        raise RuntimeError("scan import failed")

    monkeypatch.setattr(viewer, "_scan", boom)
    rows = [_row(slug="ok")]
    assert viewer.attach_tmux(rows) == []
    assert "tmux_sessions" not in rows[0]  # untouched


def test_attach_tmux_absent_when_no_tmux_server(monkeypatch):
    # No panes = no tmux server on this host → overlay absent ([] unmatched), not an error.
    class _FakeScan:
        collect_tmux_panes = staticmethod(lambda: [])

    monkeypatch.setattr(viewer, "_scan", lambda: _FakeScan)
    assert viewer.attach_tmux([_row(slug="ok")]) == []


def test_attach_tmux_returns_unmatched_from_scan(monkeypatch):
    # attach_tmux passes through the unmatched list `match_tmux_to_initiatives` returns
    # (the live claude panes that mapped to no initiative) — verbatim, no reimplementation.
    unmatched = [{"id": "Pool-6", "title": "some uncovered work", "repo": "/r"}]

    class _FakeScan:
        collect_tmux_panes = staticmethod(lambda: [{"session": "x"}])
        discover_repos = staticmethod(lambda: ["/r"])
        worktree_canonical_map = staticmethod(lambda repos: {})
        load_scratch_codenames = staticmethod(lambda: {})
        match_tmux_to_initiatives = staticmethod(
            lambda inis, panes, repos, wt, cn: unmatched)

    monkeypatch.setattr(viewer, "_scan", lambda: _FakeScan)
    assert viewer.attach_tmux([_row(slug="ok")]) == unmatched


# --- flat view + enriched view fields (Feedback 1 + 3) ---------------------- #
def test_build_model_flat_orders_by_last_touch_desc():
    rows = [
        _row(slug="old", repo="/ws/a", last_touch=NOW - timedelta(days=3)),
        _row(slug="newest", repo="/ws/b", last_touch=NOW - timedelta(minutes=2)),
        _row(slug="mid", repo="/ws/a", last_touch=NOW - timedelta(hours=5)),
    ]
    model = viewer.build_model(rows, now=NOW)
    assert [v["slug"] for v in model["flat"]] == ["newest", "mid", "old"]


def test_flat_view_carries_repo_label_summary_and_pr_titles():
    model = viewer.build_model([_row(slug="s")], now=NOW)
    v = model["flat"][0]
    assert v["repo"] == "/home/zach/workspace/devrc"
    assert v["repo_name"] == "devrc"                      # repo label for the flat card
    assert v["summary"] == "A live web viewer over the initiatives store."
    assert v["open_prs"] == [{"number": 138, "title": "feat: viewer"}]  # titles, not bare #
    assert v["docs"] == [{"path": "/home/zach/workspace/devrc/claudedocs/handoff-x.md",
                          "date": "2026-07-22"}]


def test_flat_none_last_touch_sorts_last():
    rows = [_row(slug="dated", last_touch=NOW - timedelta(days=1)),
            _row(slug="undated", last_touch=None)]
    model = viewer.build_model(rows, now=NOW)
    assert [v["slug"] for v in model["flat"]] == ["dated", "undated"]


def test_model_to_json_includes_flat():
    model = viewer.build_model([_row(slug="a")], now=NOW)
    j = viewer.model_to_json(model, None)
    assert "flat" in j and j["flat"][0]["slug"] == "a"
    err = viewer.model_to_json(None, "boom")
    assert err["flat"] == [] and err["repos"] == []


# --- detail parse + path-traversal guard (Feedback 3.3) --------------------- #
def test_parse_doc_detail_extracts_sections():
    text = ("# Handoff — thing, 2026-07-22\n\n"
            "**Goal:** build the thing.\n\n"
            "## Next steps\n1. first step\n2. second step\n\n"
            "## Open investigations\n### an open bug\n")
    d = viewer.parse_doc_detail(text)
    assert d["summary"] == "build the thing."
    assert d["next_steps"] == ["first step", "second step"]  # FULL list, not just the lead
    assert d["open_investigations"] == ["an open bug"]


def test_read_doc_detail_live_reads_a_fixture_handoff(tmp_path):
    repo = tmp_path / "repo"
    (repo / "claudedocs").mkdir(parents=True)
    doc = repo / "claudedocs" / "handoff-x-2026-07-22.md"
    doc.write_text("# X\n\n**Goal:** do X.\n\n## Next steps\n1. a\n2. b\n")
    out = viewer.read_doc_detail_live(str(repo), str(doc), repos=[str(repo)])
    assert out["summary"] == "do X." and out["next_steps"] == ["a", "b"]


def test_read_doc_detail_live_resolves_repo_allowlist_when_repos_omitted(tmp_path, monkeypatch):
    # With repos omitted, the reader must resolve the known-repo allowlist (not skip it):
    # an EMPTY allowlist -> the repo isn't allowed -> None (the guard actually runs).
    repo = tmp_path / "repo"
    (repo / "claudedocs").mkdir(parents=True)
    doc = repo / "claudedocs" / "handoff-x.md"
    doc.write_text("# X\n\n**Goal:** do X.\n")
    monkeypatch.setattr(viewer, "_discover_repos_safe", lambda: [])
    assert viewer.read_doc_detail_live(str(repo), str(doc)) is None
    # and when discovery includes the repo, the read succeeds
    monkeypatch.setattr(viewer, "_discover_repos_safe", lambda: [str(repo)])
    assert viewer.read_doc_detail_live(str(repo), str(doc))["summary"] == "do X."


def test_read_doc_detail_live_caps_read_size(tmp_path, monkeypatch):
    # A pathological file is truncated at MAX_DOC_BYTES so it can't spike memory: content
    # beyond the cap (here a Next-steps section) is not parsed.
    repo = tmp_path / "repo"
    (repo / "claudedocs").mkdir(parents=True)
    doc = repo / "claudedocs" / "handoff-x.md"
    doc.write_text("**Goal:** short goal.\n" + ("X" * 5000) +
                   "\n## Next steps\n1. SHOULD_NOT_APPEAR\n")
    monkeypatch.setattr(viewer, "MAX_DOC_BYTES", 40)
    out = viewer.read_doc_detail_live(str(repo), str(doc), repos=[str(repo)])
    assert out["summary"] == "short goal."
    assert out["next_steps"] == []  # truncated away before the Next-steps section


def test_safe_doc_path_containment_and_traversal(tmp_path):
    repo = tmp_path / "repo"
    (repo / "claudedocs").mkdir(parents=True)
    doc = repo / "claudedocs" / "handoff-x.md"
    doc.write_text("hi")
    # a real file under <repo>/claudedocs/ from a known repo resolves
    assert viewer.safe_doc_path(str(repo), str(doc), [str(repo)]) is not None
    # a traversal out of claudedocs/ is rejected
    escape = str(repo / "claudedocs" / ".." / ".." / "etc" / "passwd")
    assert viewer.safe_doc_path(str(repo), escape, [str(repo)]) is None
    # an unknown repo is rejected
    assert viewer.safe_doc_path(str(repo), str(doc), ["/some/other/repo"]) is None
    # a missing file is rejected
    assert viewer.safe_doc_path(str(repo), str(repo / "claudedocs" / "nope.md"),
                                [str(repo)]) is None


def test_build_detail_overlays_live_over_snapshot():
    model = viewer.build_model([_row(slug="s")], now=NOW)
    live = {"summary": "fresh summary", "next_steps": ["live 1", "live 2"],
            "open_investigations": ["live inv"]}
    d = viewer.build_detail(model, None, "/home/zach/workspace/devrc", "s",
                            doc_reader=lambda repo, doc: live)
    assert d["ok"] is True and d["live"] is True
    assert d["summary"] == "fresh summary"
    assert d["next_steps"] == ["live 1", "live 2"]          # FULL live list
    assert d["open_investigations"] == ["live inv"]
    assert d["open_prs"] == [{"number": 138, "title": "feat: viewer"}]


def test_build_detail_falls_back_to_snapshot_when_live_read_fails():
    model = viewer.build_model([_row(slug="s")], now=NOW)
    d = viewer.build_detail(model, None, "/home/zach/workspace/devrc", "s",
                            doc_reader=lambda repo, doc: None)
    assert d["live"] is False
    assert d["next_steps"] == ["wire the systemd unit"]     # snapshot's single next-step
    assert d["open_investigations"] == ["does the tmux overlay hold under refresh churn?"]


def test_build_detail_unknown_initiative_is_not_ok():
    model = viewer.build_model([_row(slug="s")], now=NOW)
    assert viewer.build_detail(model, None, "/nope", "nope")["ok"] is False
    assert viewer.build_detail(None, "db down", "/r", "s")["ok"] is False


# --- RefreshController: single-flight + debounce (Feedback 2) --------------- #
def _clock():
    c = {"t": 1000.0}
    return c, (lambda: c["t"])


def test_refresh_runs_then_debounces_within_window():
    c, now_fn = _clock()
    calls = {"n": 0}

    def runner(script, timeout):
        calls["n"] += 1
        return 0, ""

    rc = viewer.RefreshController(runner=runner, now_fn=now_fn, min_interval=60)
    r1 = rc.refresh()
    assert r1["status"] == "synced" and calls["n"] == 1
    c["t"] = 1005.0
    r2 = rc.refresh()                       # 5s later → debounced, NOT re-run
    assert r2["status"] == "debounced" and calls["n"] == 1
    assert "just synced" in r2["message"]
    c["t"] = 1100.0
    r3 = rc.refresh()                       # past the window → runs again
    assert r3["status"] == "synced" and calls["n"] == 2


def test_refresh_single_flighted_while_running():
    c, now_fn = _clock()
    rc = viewer.RefreshController(runner=lambda s, t: (0, ""), now_fn=now_fn)
    rc._running = True                      # simulate an in-flight sync
    assert rc.refresh()["status"] == "in_progress"


def test_refresh_reports_error_on_nonzero_rc_without_leaking_stderr():
    c, now_fn = _clock()
    rc = viewer.RefreshController(runner=lambda s, t: (1, "secret /path/to/key stderr"),
                                 now_fn=now_fn)
    r = rc.refresh()
    assert r["ok"] is False and r["status"] == "error"
    # the runner's stderr tail must NOT be returned to the (unauthenticated) client
    assert "detail" not in r
    assert "secret" not in json.dumps(r)


def test_refresh_swallows_runner_exception():
    c, now_fn = _clock()

    def runner(script, timeout):
        raise RuntimeError("spawn failed")

    rc = viewer.RefreshController(runner=runner, now_fn=now_fn)
    r = rc.refresh()
    assert r["status"] == "error" and "detail" not in r
    # after a failure, _running is reset so a later refresh can proceed
    assert rc._running is False


def test_refresh_timeout_from_runner_is_error_and_resets_running():
    import subprocess as _sp
    c, now_fn = _clock()

    def runner(script, timeout):
        raise _sp.TimeoutExpired(cmd="bash", timeout=timeout)

    rc = viewer.RefreshController(runner=runner, now_fn=now_fn)
    assert rc.refresh()["status"] == "error"
    assert rc._running is False


# --- routing: POST /refresh + GET /api/initiative -------------------------- #
class _FakeProviderWithInvalidate:
    def __init__(self, model=None, error=None):
        self._model = model
        self._error = error
        self.invalidated = 0

    def snapshot(self):
        return self._model, self._error

    def invalidate(self):
        self.invalidated += 1


class _CountingController:
    def __init__(self, result):
        self._result = result
        self.calls = 0

    def refresh(self):
        self.calls += 1
        return self._result


def test_route_refresh_synced_invalidates_provider():
    prov = _FakeProviderWithInvalidate()
    ctrl = _CountingController({"ok": True, "status": "synced", "message": "sync complete"})
    status, ctype, body = viewer.route_request("/refresh", prov, method="POST",
                                               refresh_controller=ctrl)
    assert status == 200 and "application/json" in ctype
    assert ctrl.calls == 1 and prov.invalidated == 1
    assert json.loads(body)["status"] == "synced"


def test_route_refresh_debounced_does_not_invalidate():
    prov = _FakeProviderWithInvalidate()
    ctrl = _CountingController({"ok": True, "status": "debounced",
                               "message": "just synced 5s ago"})
    status, _ctype, body = viewer.route_request("/refresh", prov, method="POST",
                                                refresh_controller=ctrl)
    assert status == 200 and prov.invalidated == 0


def test_route_refresh_in_progress_is_409():
    prov = _FakeProviderWithInvalidate()
    ctrl = _CountingController({"ok": False, "status": "in_progress", "message": "busy"})
    status, _ctype, _body = viewer.route_request("/refresh", prov, method="POST",
                                                 refresh_controller=ctrl)
    assert status == 409


def test_route_refresh_without_controller_is_503():
    prov = _FakeProviderWithInvalidate()
    status, _ctype, _body = viewer.route_request("/refresh", prov, method="POST",
                                                 refresh_controller=None)
    assert status == 503


def test_route_get_on_refresh_path_is_404():
    # /refresh is POST-only; a GET falls through to the 404 (not the refresh handler).
    prov = _FakeProviderWithInvalidate()
    status, _c, _b = viewer.route_request("/refresh", prov, method="GET")
    assert status == 404


def test_route_detail_endpoint_returns_initiative():
    model = viewer.build_model([_row(slug="s")], now=NOW)
    prov = _FakeProviderWithInvalidate(model=model)
    status, ctype, body = viewer.route_request(
        "/api/initiative", prov, method="GET",
        query={"repo": ["/home/zach/workspace/devrc"], "slug": ["s"]})
    assert status == 200 and "application/json" in ctype
    payload = json.loads(body)
    assert payload["ok"] is True and payload["slug"] == "s"


def test_route_detail_unknown_is_404():
    model = viewer.build_model([_row(slug="s")], now=NOW)
    prov = _FakeProviderWithInvalidate(model=model)
    status, _c, body = viewer.route_request("/api/initiative", prov, method="GET",
                                            query={"repo": ["/x"], "slug": ["y"]})
    assert status == 404 and json.loads(body)["ok"] is False


# --- DataProvider.invalidate ------------------------------------------------ #
def test_provider_invalidate_forces_reload():
    calls = {"n": 0}

    def loader():
        calls["n"] += 1
        return [_row(slug="x")]

    prov = viewer.DataProvider(ttl=999, loader=loader, tmux=lambda rows: True,
                               now_fn=lambda: NOW)
    prov.snapshot()
    prov.snapshot()
    assert calls["n"] == 1          # cached
    prov.invalidate()
    prov.snapshot()
    assert calls["n"] == 2          # re-read after invalidate


# --- Phase A card-legibility fields: recent messages / commits / live task -- #
def test_view_carries_recent_messages_commits_and_live_task():
    rows = [_row(slug="s",
                 recent_messages=[{"text": "enrich the cards with my prompts", "ts": 300.0},
                                  {"text": "older prompt", "ts": 100.0}],
                 recent_commits=["feat: enrich cards", "fix: dedupe turns"])]
    rows[0]["tmux_tasks"] = ["Bring the conversation onto the card"]
    v = viewer.build_model(rows, now=NOW)["flat"][0]
    assert v["recent_messages"] == [
        {"text": "enrich the cards with my prompts", "ts": 300.0},
        {"text": "older prompt", "ts": 100.0}]
    assert v["recent_commits"] == ["feat: enrich cards", "fix: dedupe turns"]
    assert v["live_task"] == "Bring the conversation onto the card"


def test_view_defaults_recent_fields_when_absent():
    v = viewer.build_model([_row(slug="s")], now=NOW)["flat"][0]
    assert v["recent_messages"] == []
    assert v["recent_commits"] == []
    assert v["live_task"] == ""


def test_view_live_task_is_first_tmux_task():
    rows = [_row(slug="s")]
    rows[0]["tmux_tasks"] = ["primary task", "secondary task"]
    v = viewer.build_model(rows, now=NOW)["flat"][0]
    assert v["live_task"] == "primary task"


def test_view_recent_messages_coerces_and_drops_non_dicts():
    rows = [_row(slug="s",
                 recent_messages=[{"text": 123, "ts": None}, "junk", {"nope": 1}])]
    v = viewer.build_model(rows, now=NOW)["flat"][0]
    # non-dicts dropped; text str-coerced; a dict without text -> ""
    assert v["recent_messages"] == [{"text": "123", "ts": None}, {"text": "", "ts": None}]


def test_render_html_embeds_recent_message_and_commit():
    rows = [_row(slug="s",
                 recent_messages=[{"text": "the most recent prompt line", "ts": 1.0}],
                 recent_commits=["feat: a recent commit subject"])]
    html = viewer.render_html(viewer.build_model(rows, now=NOW))
    assert "the most recent prompt line" in html       # latest message in the JSON island
    assert "feat: a recent commit subject" in html      # commit subject in the payload
    assert "you \\u203a" in html or "you ›" in html      # card-face renders the you › line


def test_render_html_neutralizes_untrusted_prompt_text():
    # a prompt containing markup must be neutralized in the JSON island (never raw).
    rows = [_row(slug="s",
                 recent_messages=[{"text": "<img src=x onerror=alert(1)>", "ts": 1.0}])]
    html = viewer.render_html(viewer.build_model(rows, now=NOW))
    assert "<img src=x onerror=alert(1)>" not in html   # never raw markup
    assert "u003cimg" in html                            # neutralized as <


def test_build_detail_carries_recent_fields_and_live_task():
    rows = [_row(slug="s",
                 recent_messages=[{"text": "detail prompt", "ts": 5.0}],
                 recent_commits=["chore: bump"])]
    rows[0]["tmux_tasks"] = ["open live task"]
    model = viewer.build_model(rows, now=NOW)
    d = viewer.build_detail(model, None, "/home/zach/workspace/devrc", "s",
                            doc_reader=lambda repo, doc: None)  # no live overlay
    assert d["recent_messages"] == [{"text": "detail prompt", "ts": 5.0}]
    assert d["recent_commits"] == ["chore: bump"]
    assert d["live_task"] == "open live task"


def test_model_to_json_flat_includes_recent_fields():
    rows = [_row(slug="a",
                 recent_messages=[{"text": "m", "ts": 1.0}], recent_commits=["c"])]
    j = viewer.model_to_json(viewer.build_model(rows, now=NOW), None)
    v = j["flat"][0]
    assert v["recent_messages"] == [{"text": "m", "ts": 1.0}]
    assert v["recent_commits"] == ["c"]


# --- Phase B: identity (primary) + status (secondary) recap split ----------- #
def test_view_carries_identity_status_recap_and_defaults_empty():
    v = viewer.build_model(
        [_row(slug="s", identity="A durable Postgres store for the initiatives ledger.",
              status="finishing the recap identity/status split.",
              recap="legacy recap line")],
        now=NOW)["flat"][0]
    assert v["identity"] == "A durable Postgres store for the initiatives ledger."
    assert v["status"] == "finishing the recap identity/status split."
    assert v["recap"] == "legacy recap line"
    # all three normalize to "" when absent (so the JS fallback chain is clean)
    v2 = viewer.build_model([_row(slug="s2")], now=NOW)["flat"][0]
    assert v2["identity"] == "" and v2["status"] == "" and v2["recap"] == ""


def test_model_to_json_flat_includes_identity_and_status():
    j = viewer.model_to_json(
        viewer.build_model([_row(slug="a", identity="what it is", status="what's now")],
                           now=NOW), None)
    assert j["flat"][0]["identity"] == "what it is"
    assert j["flat"][0]["status"] == "what's now"


def test_js_detail_leads_with_identity_fallback():
    # The identity ("what this is") line moved OFF the two-line collapsed card into the expanded
    # detail, which leads with the recap identity and falls back to the handoff summary.
    assert "d.identity || (v && v.identity) || d.summary" in viewer._JS


def test_js_renders_status_as_current_line_in_detail():
    # The volatile "current ›" status line moved into the expanded detail (off the collapsed card).
    assert "current ›" in viewer._JS
    assert "d.status || (v && v.status)" in viewer._JS
    assert ".status .lbl" in viewer._CSS


def test_js_stat_strip_is_removed():
    # The numeric stat strip (N commits · N merged · N sess · N ev) is gone from the card.
    assert "tag stat" not in viewer._JS
    assert " commits · " not in viewer._JS
    assert " merged · " not in viewer._JS
    assert " sess · " not in viewer._JS
    # …and its dead CSS rule is removed too.
    assert ".tag.stat" not in viewer._CSS


def test_render_html_leads_with_identity_and_shows_status():
    rows = [_row(slug="s",
                 identity="A homelab-served recap of what this fundamentally is.",
                 status="currently blocked on the vLLM endpoint.",
                 summary="the deterministic summary")]
    html = viewer.render_html(viewer.build_model(rows, now=NOW))
    assert "A homelab-served recap of what this fundamentally is." in html  # identity (in island)
    assert "currently blocked on the vLLM endpoint." in html                # status (in island)
    assert "d.identity || (v && v.identity) || d.summary" in html           # detail lead chain


def test_render_html_falls_back_to_summary_when_no_identity_or_recap():
    rows = [_row(slug="s", summary="deterministic summary fallback line")]
    html = viewer.render_html(viewer.build_model(rows, now=NOW))
    assert "deterministic summary fallback line" in html   # summary still embedded
    # identity/status/recap keys present-but-empty (never breaks the fallback)
    j = viewer.model_to_json(viewer.build_model(rows, now=NOW), None)
    v = j["flat"][0]
    assert v["identity"] == "" and v["status"] == "" and v["recap"] == ""


def test_render_html_neutralizes_untrusted_identity_and_status_text():
    rows = [_row(slug="s", identity="<script>alert('id')</script>",
                 status="<script>alert('st')</script>")]
    html = viewer.render_html(viewer.build_model(rows, now=NOW))
    assert "<script>alert('id')</script>" not in html   # never raw
    assert "<script>alert('st')</script>" not in html
    assert "u003cscript" in html                         # neutralized as <


def test_regression_remix_identity_is_platform_status_carries_cloudflare():
    # The remix case: the LLM identity (from the handoff) is about the video-remix
    # platform; the tangential "cloudflare" workstream lives ONLY in status. The card's
    # PRIMARY line is the platform identity; cloudflare never appears in the primary.
    rows = [_row(
        slug="remix-session",
        identity="A video-remix platform where users explore, stash, and render clips.",
        status="reducing cloudflare reliance in the render pipeline.",
        summary="Active development focusing on cloudflare reliance.")]
    v = viewer.build_model(rows, now=NOW)["flat"][0]
    # primary line (identity) is the platform; cloudflare is NOT in it
    assert "video-remix platform" in v["identity"]
    assert "cloudflare" not in v["identity"].lower()
    # cloudflare only appears in the (secondary) status line
    assert "cloudflare" in v["status"].lower()


# --- attach_recaps (I/O: LEFT-JOIN the standalone recaps cache) -------------- #
class _RecapCursor:
    def __init__(self, conn):
        self._conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        norm = " ".join(sql.split())
        self._conn.executed.append(norm)
        if self._conn.raise_on and self._conn.raise_on in norm:
            import psycopg2
            raise psycopg2.Error("recaps read failed")

    def fetchone(self):
        return (self._conn.regclass,)

    def fetchall(self):
        return list(self._conn.recap_rows)


class _RecapConn:
    def __init__(self, regclass="initiatives.recaps", recap_rows=(), raise_on=None):
        self.regclass = regclass
        self.recap_rows = recap_rows
        self.raise_on = raise_on
        self.executed = []
        self.rollbacks = 0

    def cursor(self, cursor_factory=None):
        return _RecapCursor(self)

    def rollback(self):
        self.rollbacks += 1


def test_attach_recaps_joins_identity_status_and_recap():
    rows = [{"repo": "/r", "slug": "a"}, {"repo": "/r", "slug": "b"}]
    conn = _RecapConn(recap_rows=[
        {"repo": "/r", "slug": "a", "identity": "what a is",
         "status": "a in progress", "recap": "legacy a"},
        {"repo": "/r", "slug": "b", "identity": None,
         "status": None, "recap": None},   # a row with nothing generated yet
    ])
    ok = viewer.attach_recaps(conn, rows)
    assert ok is True
    assert rows[0]["identity"] == "what a is"
    assert rows[0]["status"] == "a in progress"
    assert rows[0]["recap"] == "legacy a"
    # NULLs stay None → card falls back to summary / omits status
    assert rows[1]["identity"] is None and rows[1]["status"] is None


def test_attach_recaps_absent_table_leaves_fields_none():
    rows = [{"repo": "/r", "slug": "a"}]
    conn = _RecapConn(regclass=None)   # to_regclass → NULL (table not created yet)
    ok = viewer.attach_recaps(conn, rows)
    assert ok is False
    assert rows[0]["identity"] is None and rows[0]["status"] is None
    assert rows[0]["recap"] is None    # never blank/missing → fallback to summary works


def test_attach_recaps_falls_back_to_legacy_recap_only_schema():
    # A store written by the PRE-SPLIT code has no identity/status columns: the primary
    # SELECT errors, and attach transparently falls back to selecting just `recap`.
    rows = [{"repo": "/r", "slug": "a"}]
    conn = _RecapConn(
        raise_on="SELECT repo, slug, identity, status, recap",
        recap_rows=[{"repo": "/r", "slug": "a", "recap": "old-schema recap"}])
    ok = viewer.attach_recaps(conn, rows)
    assert ok is True
    assert rows[0]["recap"] == "old-schema recap"   # legacy recap still shown
    assert rows[0]["identity"] is None and rows[0]["status"] is None
    assert conn.rollbacks == 1                        # rolled back before the fallback


def test_attach_recaps_db_error_rolls_back_and_is_fail_soft():
    # BOTH selects fail (table unreadable entirely) → fail-soft, all fields None.
    rows = [{"repo": "/r", "slug": "a"}]
    conn = _RecapConn(raise_on="FROM initiatives.recaps")
    ok = viewer.attach_recaps(conn, rows)
    assert ok is False
    assert conn.rollbacks >= 1
    assert rows[0]["identity"] is None and rows[0]["status"] is None
    assert rows[0]["recap"] is None


# --- Card-FACE substantive-prompt selection (Problem 2) --------------------- #
def test_is_trivial_prompt_flags_boilerplate_and_short():
    for triv in ["dispatch", "Proceed.", "yes", "go", " submitted ", "OK", "merged",
                 "continue", "done", "y", ""]:
        assert viewer._is_trivial_prompt(triv), triv
    for real in ["relabel the node as web", "fix bad-eyes then launch round 3",
                 "wire the comfy cloud scaffold"]:
        assert not viewer._is_trivial_prompt(real), real


def test_pick_face_message_skips_boilerplate_for_first_substantive():
    # newest-first list whose newest entries are boilerplate → face is the first real one.
    msgs = [
        {"text": "dispatch", "ts": 500.0},
        {"text": "proceed", "ts": 400.0},
        {"text": "relabel the node as web", "ts": 300.0},
        {"text": "older substantive prompt here", "ts": 200.0},
    ]
    assert viewer.pick_face_message(msgs) == {"text": "relabel the node as web", "ts": 300.0}


def test_pick_face_message_falls_back_when_all_trivial():
    msgs = [{"text": "dispatch", "ts": 500.0}, {"text": "yes", "ts": 400.0}]
    # every message trivial → fall back to the most-recent (never blank).
    assert viewer.pick_face_message(msgs) == {"text": "dispatch", "ts": 500.0}


def test_pick_face_message_empty_is_none():
    assert viewer.pick_face_message([]) is None
    assert viewer.pick_face_message(None) is None


def test_view_face_message_is_substantive_but_full_list_intact():
    # The card FACE skips the boilerplate; the stored recent_messages list stays COMPLETE
    # (unfiltered) for the expand + Phase B.
    rows = [_row(slug="s", recent_messages=[
        {"text": "dispatch", "ts": 500.0},
        {"text": "submitted", "ts": 450.0},
        {"text": "close the review arc for app-blocks", "ts": 300.0},
    ])]
    v = viewer.build_model(rows, now=NOW)["flat"][0]
    assert v["face_message"] == {"text": "close the review arc for app-blocks", "ts": 300.0}
    # full list preserved verbatim (all three, boilerplate included), newest-first.
    assert [m["text"] for m in v["recent_messages"]] == [
        "dispatch", "submitted", "close the review arc for app-blocks"]


def test_view_face_message_none_when_no_messages():
    v = viewer.build_model([_row(slug="s")], now=NOW)["flat"][0]
    assert v["face_message"] is None


def test_render_html_face_shows_substantive_not_boilerplate():
    # The card FACE line (you › …) must render the substantive prompt, while the boilerplate
    # still rides along in the JSON island (for the expand). We assert the JS reads
    # face_message for the face, and the substantive text is present in the payload.
    rows = [_row(slug="s", recent_messages=[
        {"text": "dispatch", "ts": 500.0},
        {"text": "wire the comfy cloud scaffold", "ts": 300.0},
    ])]
    html = viewer.render_html(viewer.build_model(rows, now=NOW))
    assert "wire the comfy cloud scaffold" in html           # substantive prompt in payload
    assert '"face_message"' in html                            # the face field is embedded
    assert "v.face_message" in html                            # the card reads it for the face


# --- live_unmatched: the "everything else running" catch-all ----------------- #
def _um(id_, title="some work", repo="/home/zach/workspace/devrc", activity_ts=None):
    """One `match_tmux_to_initiatives` unmatched pane (id/title/repo/activity_ts)."""
    return {"id": id_, "title": title, "repo": repo, "activity_ts": activity_ts}


def test_build_live_unmatched_shape_dedup_and_sort():
    um = [
        _um("main:8-4", "civ work", repo="/home/zach/workspace/civitai"),
        _um("Pool-6", "devrc work A"),
        _um("Pool-6", "devrc work A"),          # exact dup (id+title) → dropped
        _um("main:8-2", "devrc work B"),
    ]
    out = viewer.build_live_unmatched(um)
    # de-duped
    assert len(out) == 3
    # view shape
    assert out[0].keys() >= {"id", "title", "repo", "repo_name"}
    # sorted by repo_name then the scan's natural session key: civitai first, then within
    # devrc capitalized codenames sort ahead of the lowercase `main:` sessions (mirrors the
    # scan's `_tmux_session_sort_key`, so the CLI + viewer order sessions identically).
    assert [(v["repo_name"], v["id"]) for v in out] == [
        ("civitai", "main:8-4"),
        ("devrc", "Pool-6"),
        ("devrc", "main:8-2"),
    ]


def test_build_live_unmatched_natural_numeric_order():
    # window numbers sort by VALUE, not lexically (8-2 before 8-10; Pool2 before Pool10);
    # capitalized codenames (Pool…) sort ahead of lowercase `main:` — the scan's ordering.
    um = [_um("main:8-10"), _um("main:8-2"), _um("Pool10"), _um("Pool2")]
    ids = [v["id"] for v in viewer.build_live_unmatched(um)]
    assert ids == ["Pool2", "Pool10", "main:8-2", "main:8-10"]


def test_build_live_unmatched_none_repo_becomes_unknown():
    out = viewer.build_live_unmatched([_um("x-1", "orphan", repo=None)])
    assert out[0]["repo"] == ""
    assert out[0]["repo_name"] == "(unknown repo)"


def test_build_live_unmatched_coerces_non_list_and_junk():
    # a fake tmux hook returning a bool (or None) → [] (no section); non-dict entries dropped.
    assert viewer.build_live_unmatched(True) == []
    assert viewer.build_live_unmatched(None) == []
    assert viewer.build_live_unmatched(["junk", 3, _um("ok-1")]) == [
        {"id": "ok-1", "title": "some work", "repo": "/home/zach/workspace/devrc",
         "repo_name": "devrc", "activity_ts": None}]


def test_build_model_carries_live_unmatched():
    model = viewer.build_model([_row(slug="s")], now=NOW,
                               unmatched=[_um("Pool-6", "uncovered thread")])
    assert model["live_unmatched"] == [
        {"id": "Pool-6", "title": "uncovered thread",
         "repo": "/home/zach/workspace/devrc", "repo_name": "devrc", "activity_ts": None}]


def test_build_model_live_unmatched_defaults_empty():
    # no unmatched arg (and a non-list) → empty list, never missing/raising.
    assert viewer.build_model([_row(slug="s")], now=NOW)["live_unmatched"] == []
    assert viewer.build_model([_row(slug="s")], now=NOW,
                              unmatched=True)["live_unmatched"] == []


def test_model_to_json_includes_live_unmatched_ok_and_error():
    model = viewer.build_model([_row(slug="a")], now=NOW, unmatched=[_um("Vapor-1", "t")])
    j = viewer.model_to_json(model, None)
    assert j["live_unmatched"] == [
        {"id": "Vapor-1", "title": "t", "repo": "/home/zach/workspace/devrc",
         "repo_name": "devrc", "activity_ts": None}]
    # error branch always carries an empty list so the JS never sees undefined.
    assert viewer.model_to_json(None, "store down")["live_unmatched"] == []


def test_render_html_livenow_strip_pinned_and_escapes():
    # The pinned "Live now" strip: its data rides the JSON island (live_unmatched here) and it is
    # rendered client-side by renderLiveNow/buildLiveNow into the #livenow container at the top.
    model = viewer.build_model([_row(slug="s")], now=NOW, unmatched=[
        _um("Pool-6", "<script>alert('u')</script>", repo="/home/zach/workspace/civitai")])
    html = viewer.render_html(model)
    assert 'id="livenow"' in html                        # the pinned container element
    assert "function renderLiveNow(" in html             # rendered client-side
    assert "function buildLiveNow(" in html              # union+dedup builder shipped
    assert "renderUnmatched" not in html                 # the old catch-all is gone
    assert "Live sessions — not tied to an initiative" not in html
    assert '"live_unmatched"' in html
    assert "Pool-6" in html                              # session id in the payload
    assert "<script>alert('u')</script>" not in html     # untrusted title never raw
    assert "u003cscript" in html                         # neutralized in the island
    # #livenow appears in the body BEFORE the sticky triage bar (first thing seen).
    assert html.index('id="livenow"') < html.index('id="triage"')


def test_render_html_livenow_hides_when_no_live_sessions():
    # No live panes → renderLiveNow hides the strip (buildLiveNow returns []) — no empty section.
    model = viewer.build_model([_row(slug="s")], now=NOW, unmatched=[])
    j = viewer.model_to_json(model, None)
    assert j["live_unmatched"] == []
    html = viewer.render_html(model)
    assert '"live_unmatched": []' in html or '"live_unmatched":[]' in html
    # the renderer hides the strip when there are no rows
    assert "if(!rows.length){ liveNowEl.style.display = 'none'; return; }" in viewer._JS


# --- multi-pane cosmetic: show ALL matched live tasks, not just the first ----- #
def test_view_live_tasks_lists_all_matched_panes():
    # An initiative matched by MORE than one live pane must surface EVERY task, while
    # live_task (first) stays for the detail endpoint / back-compat.
    rows = [_row(slug="next-session")]
    rows[0]["tmux_tasks"] = ["Continue dp-prod performance…", "Pick up dp-prod 500 arc…"]
    v = viewer.build_model(rows, now=NOW)["flat"][0]
    assert v["live_task"] == "Continue dp-prod performance…"
    assert v["live_tasks"] == ["Continue dp-prod performance…", "Pick up dp-prod 500 arc…"]


def test_view_live_tasks_defaults_empty():
    v = viewer.build_model([_row(slug="s")], now=NOW)["flat"][0]
    assert v["live_tasks"] == []


def test_js_detail_renders_all_live_tasks():
    # the live tmux session tasks moved into the expanded detail (one line per session).
    assert "d.live_tasks" in viewer._JS
    assert "dtasks.forEach" in viewer._JS


def test_build_detail_carries_live_tasks():
    rows = [_row(slug="s")]
    rows[0]["tmux_tasks"] = ["task one", "task two"]
    model = viewer.build_model(rows, now=NOW)
    d = viewer.build_detail(model, None, "/home/zach/workspace/devrc", "s",
                            doc_reader=lambda repo, doc: None)
    assert d["live_tasks"] == ["task one", "task two"]


# --- activity_ts on the live overlay (Live-now freshness sort) --------------- #
def test_view_live_tasks_meta_carries_activity_ts_and_is_backcompat():
    rows = [_row(slug="s")]
    rows[0]["tmux_tasks"] = ["fresh task", "no-activity task"]
    rows[0]["tmux_task_activity"] = {"fresh task": 1722000000}   # 2nd task has no entry
    v = viewer.build_model(rows, now=NOW)["flat"][0]
    # BACK-COMPAT: the string list + first-task field the ● live badge / detail read are unchanged.
    assert v["live_tasks"] == ["fresh task", "no-activity task"]
    assert v["live_task"] == "fresh task"
    assert v["live"] is True                                     # the live overlay badge still fires
    # meta aligned + ordered the same; a task with no activity entry → None.
    assert v["live_tasks_meta"] == [
        {"task": "fresh task", "activity_ts": 1722000000},
        {"task": "no-activity task", "activity_ts": None}]


def test_view_live_tasks_meta_defaults_empty_and_coerces_bad_activity():
    # No tmux overlay → empty meta. A non-integer activity value coerces to None (never raises).
    assert viewer.build_model([_row(slug="s")], now=NOW)["flat"][0]["live_tasks_meta"] == []
    rows = [_row(slug="s")]
    rows[0]["tmux_tasks"] = ["t"]
    rows[0]["tmux_task_activity"] = {"t": "not-an-int"}
    v = viewer.build_model(rows, now=NOW)["flat"][0]
    assert v["live_tasks_meta"] == [{"task": "t", "activity_ts": None}]


def test_unmatched_view_carries_activity_ts():
    out = viewer.build_live_unmatched([_um("Pool-6", "below-floor", activity_ts=1722000000)])
    assert out[0]["activity_ts"] == 1722000000
    # a non-integer / missing activity coerces to None.
    out2 = viewer.build_live_unmatched([_um("Pool-7", "x", activity_ts="bad")])
    assert out2[0]["activity_ts"] is None


def test_model_to_json_flat_carries_live_tasks_meta():
    rows = [_row(slug="a")]
    rows[0]["tmux_tasks"] = ["live one"]
    rows[0]["tmux_task_activity"] = {"live one": 1722000000}
    j = viewer.model_to_json(viewer.build_model(rows, now=NOW), None)
    assert j["flat"][0]["live_tasks_meta"] == [{"task": "live one", "activity_ts": 1722000000}]


def test_provider_passes_unmatched_through_to_model():
    # the DataProvider must thread attach_tmux's unmatched return into build_model so the
    # section is populated from the live tmux read (not silently dropped, as it was before).
    def tmux(rows):
        return [_um("Vapor-9", "a live uncovered thread")]

    prov = viewer.DataProvider(ttl=60, loader=lambda: [_row(slug="s")], tmux=tmux,
                               now_fn=lambda: NOW)
    model, error = prov.snapshot()
    assert error is None
    assert model["live_unmatched"] == [
        {"id": "Vapor-9", "title": "a live uncovered thread",
         "repo": "/home/zach/workspace/devrc", "repo_name": "devrc", "activity_ts": None}]


# --------------------------------------------------------------------------- #
# "By recency" view — the 3rd toggle mode (and the DEFAULT view). Bucketing is CLIENT-SIDE JS,
# ROLLING now-relative windows on the age `now - last_touch` (tz-independent duration math),
# factored into the DOM-free `viewer._RECENCY_JS` snippet so these tests exercise the REAL code
# via node — not a Python replica. The Python side (build_model/model_to_json) is UNCHANGED;
# `last_touch` already ships to the client as an ISO string, so no server-side change was needed.
# --------------------------------------------------------------------------- #
import os as _os                # noqa: E402
import shutil as _shutil        # noqa: E402
import subprocess as _subprocess  # noqa: E402


def _epoch_ms(y, mo, d, h=0, mi=0, s=0):
    """A UTC wall-clock -> epoch milliseconds (an absolute instant, tz-independent)."""
    return int(datetime(y, mo, d, h, mi, s, tzinfo=timezone.utc).timestamp() * 1000)


def _iso(y, mo, d, h=0, mi=0, s=0):
    """A UTC wall-clock -> the ISO-8601 string the JSON island carries for `last_touch`."""
    return datetime(y, mo, d, h, mi, s, tzinfo=timezone.utc).isoformat()


def _node_recency(body, tz="UTC"):
    """Eval `viewer._RECENCY_JS` + `body` (which console.logs a JSON value) under node with a
    fixed TZ; return the parsed stdout. Skips if node isn't on PATH — the bucketing is JS, so
    node is the only way to exercise the ACTUAL page code rather than re-implementing it."""
    node = _shutil.which("node")
    if not node:
        import pytest
        pytest.skip("node not on PATH — recency-bucketing JS untested this run")
    src = viewer._RECENCY_JS + "\n" + body
    out = _subprocess.run([node, "-e", src], capture_output=True, text=True,
                          env=dict(_os.environ, TZ=tz), timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def _bucket_of(ts_ms, now_ms, tz="UTC"):
    body = "console.log(JSON.stringify(recencyBucketKey(%s, %d)));" % (
        "null" if ts_ms is None else str(ts_ms), now_ms)
    return _node_recency(body, tz=tz)


_H_MS = 3600000       # 1 hour in ms
_D_MS = 86400000      # 1 day in ms


def test_recency_bucket_boundaries_rolling():
    # Rolling now-relative windows on the AGE (now - ts): each edge is EXCLUSIVE on the narrow
    # side, so exactly-1h/24h/72h/7d ages fall to the NEXT-wider bucket. Assert both sides of
    # every boundary (just-under stays, exactly-on tips over).
    now = _epoch_ms(2026, 7, 22, 12)
    assert _bucket_of(now, now) == "hour"                        # age 0
    assert _bucket_of(now - (_H_MS - 1000), now) == "hour"       # 59m59s -> still < 1h
    assert _bucket_of(now - _H_MS, now) == "day"                 # exactly 1h -> 24h window
    assert _bucket_of(now - (24 * _H_MS - 1000), now) == "day"   # just under 24h
    assert _bucket_of(now - 24 * _H_MS, now) == "three_days"     # exactly 24h -> 72h window
    assert _bucket_of(now - (72 * _H_MS - 1000), now) == "three_days"  # just under 72h
    assert _bucket_of(now - 72 * _H_MS, now) == "week"           # exactly 72h -> 7d window
    assert _bucket_of(now - (7 * _D_MS - 1000), now) == "week"   # just under 7d
    assert _bucket_of(now - 7 * _D_MS, now) == "older"           # exactly 7d -> older
    assert _bucket_of(now - 30 * _D_MS, now) == "older"          # well older
    assert _bucket_of(None, now) == "unknown"                    # missing last_touch


def test_recency_bucket_is_tz_independent():
    # Rolling windows are pure now-ts DURATION math, so the SAME absolute instants bucket
    # IDENTICALLY regardless of the viewer's local tz — the old calendar/local-midnight scheme
    # was tz-sensitive; this proves that sensitivity is gone (no local-midnight/DST math left).
    now = _epoch_ms(2026, 7, 22, 3)
    ts = now - 22 * _H_MS   # 22h old -> 'day' in every timezone
    for tz in ("UTC", "America/New_York", "Asia/Kolkata"):
        assert _bucket_of(ts, now, tz=tz) == "day"


def test_recency_bucketize_omits_empty_and_preserves_input_order():
    # bucketize a search-filtered, already-DESC flat list: assert (1) buckets come out in
    # hour->day->three_days->week->older->unknown order, (2) empty buckets (no 'day'/'week'
    # item here) are OMITTED, (3) within-bucket order preserves the DESC input (the caller feeds
    # data.flat, which build_model already sorts last_touch-DESC), (4) null last_touch -> unknown, last.
    now = _epoch_ms(2026, 7, 22, 12)
    views = [
        {"slug": "h1", "last_touch": _iso(2026, 7, 22, 11, 50)},  # hour       (10m, newer)
        {"slug": "h2", "last_touch": _iso(2026, 7, 22, 11, 10)},  # hour       (50m, older)
        {"slug": "t1", "last_touch": _iso(2026, 7, 20, 12)},      # three_days (48h)
        {"slug": "o1", "last_touch": _iso(2026, 7, 1, 12)},       # older
        {"slug": "u1", "last_touch": None},                       # unknown (null)
    ]
    body = ("var NOW=%d; var VIEWS=%s;"
            "console.log(JSON.stringify(bucketizeRecency(VIEWS, NOW).map(function(g){"
            "return {key:g.key, slugs:g.items.map(function(v){return v.slug;})};})));"
            ) % (now, json.dumps(views))
    got = _node_recency(body)
    # 'day' and 'week' omitted (no items); order is narrowest->widest then unknown last.
    assert [g["key"] for g in got] == ["hour", "three_days", "older", "unknown"]
    assert got[0]["slugs"] == ["h1", "h2"]  # within-bucket DESC input order preserved
    assert got[1]["slugs"] == ["t1"]
    assert got[2]["slugs"] == ["o1"]
    assert got[3]["slugs"] == ["u1"]        # unknown bucket last


def test_recency_bucketize_parses_space_separated_last_touch():
    # last_touch ships as json default=str → a SPACE-separated "YYYY-MM-DD HH:MM:SS...+00:00"
    # (NOT ISO 'T'). bucketize must normalize it so it buckets correctly in EVERY engine (not
    # just V8) — a naive new Date(space-string) returns NaN in Firefox → everything 'unknown'.
    now = _epoch_ms(2026, 7, 22, 12)
    views = [{"slug": "s", "last_touch": "2026-07-22 10:00:00.123456+00:00"}]  # 2h old, space-sep
    body = ("console.log(JSON.stringify(bucketizeRecency(%s, %d).map(function(g){"
            "return g.key;})));") % (json.dumps(views), now)
    assert _node_recency(body) == ["day"]   # 2h -> 'day' (Past 24 hours); normalized correctly


def test_recency_bucketize_all_empty_returns_no_buckets():
    # No initiatives -> no bucket sections at all (the render then shows the "No initiatives"
    # empty state, same as flat/grouped).
    now = _epoch_ms(2026, 7, 22, 12)
    body = "console.log(JSON.stringify(bucketizeRecency([], %d)));" % now
    assert _node_recency(body) == []


# --- the 3-way toggle: default + persistence (client-side markers) ---------- #
def test_render_html_has_three_way_toggle():
    # The toggle grew a third button; flat + grouped keep their ids/labels unchanged.
    html = viewer.render_html(viewer.build_model([_row(slug="s")], now=NOW))
    assert 'id="view-flat"' in html
    assert 'id="view-grouped"' in html
    assert 'id="view-recency"' in html
    # Rolling-bucket labels are embedded (RECENCY_BUCKETS in the inlined snippet).
    assert "Past hour" in html and "Past 24 hours" in html and "Older" in html


def test_js_grouped_is_default_and_storage_key_bumped():
    js = viewer._JS
    # The resolved default view is now 'grouped' (Phase-1 board redesign; was 'recency'); an
    # unknown/legacy stored value falls back via the VALID_VIEWS allowlist to grouped.
    assert "VALID_VIEWS" in js and "VALID_VIEWS[storedView] ? storedView : 'grouped'" in js
    assert "VALID_VIEWS[storedView] ? storedView : 'recency'" not in js
    assert "VALID_VIEWS[storedView] ? storedView : 'flat'" not in js
    # The storage key was bumped to v3: a browser that persisted an OLD default ('flat'/'recency')
    # under the v1/v2 key reads NOTHING under the v3 key, so storedView is null and it falls to
    # the grouped default rather than being pinned to a stale view.
    assert "VIEW_KEY = 'initiatives-view-v3'" in js
    assert "VIEW_KEY = 'initiatives-view-v2'" not in js
    assert "VIEW_KEY = 'initiatives-view'" not in js   # the v1 key is fully gone (no stale read)
    # the choice is still persisted, and all three views stay selectable + sticky.
    assert "localStorage.setItem(VIEW_KEY, 'recency')" in js
    assert "localStorage.setItem(VIEW_KEY, 'flat')" in js
    assert "localStorage.setItem(VIEW_KEY, 'grouped')" in js
    # the snippet was inlined (placeholder substituted), so the page can call it.
    assert "__RECENCY_JS__" not in js
    assert "bucketizeRecency" in js and "recencyBucketKey" in js


def test_js_recency_render_branch_present():
    # render() has a dedicated recency branch that buckets the filtered flat stream and
    # renders one section per non-empty bucket (label + count header, .repo styling).
    assert "state.view === 'recency'" in viewer._JS
    assert "bucketizeRecency(rviews, Date.now())" in viewer._JS
    # the repo label shows in recency too (repo isn't the section header there).
    assert "state.view !== 'grouped'" in viewer._JS


# --------------------------------------------------------------------------- #
# "Emerging / undocumented" lane — session-only cards (undocumented=True) are carried
# through the model and segregated into a separate collapsed lane in the SPA.
# --------------------------------------------------------------------------- #
def test_view_carries_undocumented_and_source():
    # A session-only card (the v4 discovery flags present on the row) flows through
    # build_model -> the flat view dict verbatim.
    rows = [_row(slug="s", undocumented=True, source="session")]
    v = viewer.build_model(rows, now=NOW)["flat"][0]
    assert v["undocumented"] is True
    assert v["source"] == "session"


def test_view_undocumented_defaults_false_when_absent():
    # GRACEFUL DEGRADATION: an un-migrated row (pre-v4, no `undocumented`/`source` keys) is
    # treated as DOCUMENTED (undocumented False, source "") — so it stays in the main board.
    row = _row(slug="s")
    assert "undocumented" not in row and "source" not in row
    v = viewer.build_model([row], now=NOW)["flat"][0]
    assert v["undocumented"] is False
    assert v["source"] == ""


def test_view_undocumented_coerces_truthy_to_bool():
    # The store column is a real bool, but coerce defensively (a 1/None/"" row never leaks a
    # non-bool into the JSON island).
    assert viewer.build_model([_row(slug="a", undocumented=1)], now=NOW)["flat"][0][
        "undocumented"] is True
    assert viewer.build_model([_row(slug="b", undocumented=None)], now=NOW)["flat"][0][
        "undocumented"] is False


def test_model_to_json_includes_undocumented_and_source():
    # The /api/initiatives.json payload carries the flags on every flat entry (the SPA reads
    # them to partition the board vs. the lane).
    rows = [_row(slug="doc", undocumented=False, source="doc"),
            _row(slug="emg", undocumented=True, source="session")]
    j = viewer.model_to_json(viewer.build_model(rows, now=NOW), None)
    flags = {e["slug"]: (e["undocumented"], e["source"]) for e in j["flat"]}
    assert flags["doc"] == (False, "doc")
    assert flags["emg"] == (True, "session")


# --------------------------------------------------------------------------- #
# opening_message — the thread's origin (genesis) prompt on the card's `start ›` line
# --------------------------------------------------------------------------- #
def test_view_carries_opening_message():
    rows = [_row(slug="s", opening_message="//image-cacher investigate why this 404s")]
    v = viewer.build_model(rows, now=NOW)["flat"][0]
    assert v["opening_message"] == "//image-cacher investigate why this 404s"


def test_view_opening_message_defaults_empty_when_absent():
    # GRACEFUL DEGRADATION: a pre-v5 row (OPTIONAL_COLUMNS didn't select it) has no key -> "".
    row = _row(slug="s")
    assert "opening_message" not in row
    v = viewer.build_model([row], now=NOW)["flat"][0]
    assert v["opening_message"] == ""


def test_opening_message_is_an_optional_column():
    # It's OPTIONAL so an un-migrated store (view/table without the column) degrades cleanly.
    assert "opening_message" in viewer.OPTIONAL_COLUMNS


def test_card_renders_start_line_and_css_present():
    # The SPA card renders a `start ›` line from v.opening_message, with matching CSS.
    js = viewer._JS
    assert "v.opening_message" in js
    assert "start ›" in js
    css = viewer._CSS
    assert ".start" in css and ".start .lbl" in css


def test_render_html_embeds_opening_message_in_json_island():
    rows = [_row(slug="s", opening_message="//the origin ask")]
    html = viewer.render_html(viewer.build_model(rows, now=NOW))
    assert '"opening_message"' in html
    assert "the origin ask" in html


# --------------------------------------------------------------------------- #
# search_text — the SEARCH-ONLY full-text index (v6): fed to matchQ, NEVER rendered.
# --------------------------------------------------------------------------- #
def test_view_carries_search_text():
    rows = [_row(slug="s",
                 search_text="//open\nit's an announcement image, 404ing is a big blast radius")]
    v = viewer.build_model(rows, now=NOW)["flat"][0]
    assert "announcement" in v["search_text"]


def test_view_search_text_defaults_empty_when_absent():
    # GRACEFUL DEGRADATION: a pre-v6 row (OPTIONAL_COLUMNS didn't select it) has no key -> "".
    row = _row(slug="s")
    assert "search_text" not in row
    v = viewer.build_model([row], now=NOW)["flat"][0]
    assert v["search_text"] == ""


def test_search_text_is_an_optional_column():
    # OPTIONAL so an un-migrated store (view/table without the column) degrades cleanly.
    assert "search_text" in viewer.OPTIONAL_COLUMNS


def test_search_text_fed_to_match_blob_but_not_rendered_on_card():
    # It feeds the client-side search predicate...
    assert "v.search_text" in viewer._MATCH_JS
    # ...and its ONLY reference in the whole page bundle is the matcher — the card DOM-builder
    # never touches it, so the full session text is searched but never displayed on the card.
    assert viewer._JS.count("v.search_text") == 1
    # It reaches the client (JSON island) so matchQ can use it, but nothing renders it: the
    # card-building code (`function card(`) does not mention search_text between its bounds.
    js = viewer._JS
    ci = js.index("function card(")
    card_body = js[ci:ci + 6000]
    assert "search_text" not in card_body


def _node_match(views, q):
    """Eval `viewer._MATCH_JS` (the pure card-search predicate) under node against `views`
    with query `q`; return the slugs that match. Skips if node isn't on PATH — same pattern
    as `_node_partition`, so the ACTUAL page predicate is exercised (not a Python replica).

    matchQ's contract is that `q` arrives ALREADY trimmed+lowercased (the page does
    `state.q.trim().toLowerCase()` before calling it), so this helper applies the SAME
    normalization — exercising the real query pipeline (normalize → predicate)."""
    node = _shutil.which("node")
    if not node:
        import pytest
        pytest.skip("node not on PATH — search matchQ JS untested this run")
    body = (
        "var VIEWS = " + json.dumps(views) + ";\n"
        "var Q = (" + json.dumps(q) + ").trim().toLowerCase();\n"
        "console.log(JSON.stringify(VIEWS.filter(function(v){ return matchQ(v, Q); })"
        ".map(function(v){ return v.slug; })));"
    )
    out = _subprocess.run([node, "-e", viewer._MATCH_JS + "\n" + body],
                          capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_js_matchq_empty_query_matches_all():
    got = _node_match([{"slug": "a"}, {"slug": "b"}], "")
    assert got == ["a", "b"]


def test_js_matchq_filters_on_title_summary_and_opening_and_latest():
    views = [
        {"slug": "cacher", "title": "Web delete flow dispatch",
         "opening_message": "//image-cacher investigate why this 404s",
         "recent_messages": [{"text": "check the delete flow"}]},
        {"slug": "mail", "title": "Mail automation", "summary": "invoice archiver",
         "opening_message": "", "recent_messages": [{"text": "ship the extractor"}]},
        {"slug": "clawgate", "next_step": "cut a release", "repo_name": "devrc",
         "recent_messages": []},
    ]
    # matches the ORIGIN prompt of the first card even though its TITLE drifted away from it
    assert _node_match(views, "image-cacher") == ["cacher"]
    assert _node_match(views, "404s") == ["cacher"]
    # matches a LATEST message text
    assert _node_match(views, "extractor") == ["mail"]
    # matches summary / title / next_step / repo_name
    assert _node_match(views, "invoice") == ["mail"]
    assert _node_match(views, "automation") == ["mail"]
    assert _node_match(views, "release") == ["clawgate"]
    assert _node_match(views, "devrc") == ["clawgate"]
    # case-insensitive + no match
    assert _node_match(views, "MAIL") == ["mail"]
    assert _node_match(views, "nonexistent-token") == []


# --------------------------------------------------------------------------- #
# matchQ — FUZZY search over the full-text blob (v6). A strict SUPERSET of the old
# substring test, node-eval'd so the ACTUAL page predicate runs (not a Python replica).
# --------------------------------------------------------------------------- #
def _fuzzy_views():
    # Card A: the announcement keyword lives ONLY in search_text (a mid-session turn), NOT in
    # the opening/title/latest — exactly the miss the old substring-over-last-5 blob had.
    return [
        {"slug": "img-cacher", "title": "image cacher", "repo_name": "civit/datapacket-talos",
         "opening_message": "//image-cacher investigate why this 404s",
         "recent_messages": [{"text": "dispatch a fix"}, {"text": "check the canary"}],
         "search_text": ("//image-cacher investigate why this 404s\n"
                         "it's an announcement image, 404ing is a large blast radius\n"
                         "dispatch to scope a solution for the announcement pointer issue")},
        {"slug": "mail", "title": "Mail automation", "summary": "invoice archiver",
         "opening_message": "//kick off mail", "recent_messages": [{"text": "ship extractor"}],
         "search_text": "ship the extractor and archive the invoices"},
    ]


def test_js_matchq_substring_fast_path_preserved():
    # The old exact/substring behaviour is intact (regression guard for "404"/"img").
    v = _fuzzy_views()
    assert _node_match(v, "404") == ["img-cacher"]        # appears in search_text + opening
    assert _node_match(v, "img") == ["img-cacher"]        # substring of "image"/slug
    assert _node_match(v, "invoices") == ["mail"]
    assert _node_match(v, "MAIL") == ["mail"]             # case-insensitive


def test_js_matchq_indexes_mid_session_keyword_via_search_text():
    # THE FIX: a keyword typed in a MIDDLE turn (only in search_text) now matches, where the
    # old opening+last-5 blob missed it.
    v = _fuzzy_views()
    assert _node_match(v, "announcement") == ["img-cacher"]


def test_js_matchq_fuzzy_typo_tolerance():
    v = _fuzzy_views()
    # single-deletion typo (edit distance 1) still finds "announcement"
    assert _node_match(v, "annoucement") == ["img-cacher"]
    # transposition-ish / substitution within the len-based cap
    assert _node_match(v, "anouncement") == ["img-cacher"]


def test_js_matchq_subsequence_partial_typing():
    v = _fuzzy_views()
    # "annce" is an ordered subsequence of "announcement" (partial typing)
    assert _node_match(v, "annce") == ["img-cacher"]


def test_js_matchq_4char_subsequence_does_not_overmatch():
    # Subsequence tolerance is gated at >=5 chars: a 4-char token must NOT match via
    # subsequence (it over-matches noisily, e.g. "test" ⊂ "greatest"). "rdis" is an ordered
    # subsequence of "radius" (in the blob) but NOT a substring and edit-distance 2 > cap 1
    # for a 4-char token — so under the tightened rule it matches NOTHING.
    v = _fuzzy_views()
    assert _node_match(v, "rdis") == []
    # A 5-char subsequence of the same word still matches (partial-typing recall preserved).
    assert _node_match(v, "rdius") == ["img-cacher"]


def test_js_matchq_short_tokens_do_not_overmatch():
    # Tokens <4 chars are substring-only — no fuzzy noise. "xyz" is nowhere; "an" (<4) must
    # NOT fuzzy-explode onto the announcement card (it isn't a substring of that blob token
    # boundary-free... it IS a substring "an" appears — so use a genuinely absent short token).
    v = _fuzzy_views()
    assert _node_match(v, "zzq") == []          # 3 chars, absent -> no match, no fuzzy
    assert _node_match(v, "qxz") == []


def test_js_matchq_multi_token_and_semantics():
    v = _fuzzy_views()
    # BOTH tokens must match SOME card for it to qualify (AND). "announcement" is only on
    # img-cacher, "invoices" only on mail -> no single card has both.
    assert _node_match(v, "announcement invoices") == []
    # both tokens on the SAME card -> matches
    assert _node_match(v, "announcement pointer") == ["img-cacher"]
    # one exact + one fuzzy token, same card
    assert _node_match(v, "404 annoucement") == ["img-cacher"]


def test_js_matchq_empty_and_whitespace_query_matches_all():
    v = _fuzzy_views()
    assert set(_node_match(v, "")) == {"img-cacher", "mail"}
    assert set(_node_match(v, "   ")) == {"img-cacher", "mail"}


def test_js_matchq_diacritic_insensitive():
    v = [{"slug": "café", "title": "Café résumé pipeline", "search_text": ""}]
    assert _node_match(v, "cafe") == ["café"]       # query without accents matches accented blob
    assert _node_match(v, "resume") == ["café"]


def test_js_matchq_is_wired_and_inlined_once():
    js = viewer._JS
    assert "__MATCH_JS__" not in js                 # snippet inlined
    assert "function matchQ" in js                  # the predicate is present in the page
    assert js.count("function matchQ") == 1         # exactly once (not the old inline copy too)
    # the blob now includes the origin prompt + the parsed next-step + the full-text index
    assert "v.opening_message" in viewer._MATCH_JS
    assert "v.next_step" in viewer._MATCH_JS
    assert "v.search_text" in viewer._MATCH_JS
    # the search input is debounced and a "N shown / M" count is wired
    assert "setTimeout(function(){ state.q = searchInput.value; render(); }, 150)" in js
    assert "shown / " in js
    assert "function updateSearchCount" in js


def test_js_grouping_and_inline_emerging_wired():
    # Phase-1: the standalone Emerging lane + the doc/emerging partition are RETIRED. Undocumented
    # cards render inline in their repo group with an "emerging" badge; grouping is client-side,
    # collapsible, and the search/triage filters compose. These markers assert the DOM wiring
    # stays intact (the pure helpers groupByRepo/matchState are node-tested below).
    js = viewer._JS
    assert "__GROUP_JS__" not in js and "__STATEFILTER_JS__" not in js   # snippets inlined
    assert "function groupByRepo" in js and "function matchState" in js
    # grouped (default) view builds collapsible sections from the flat stream, client-side.
    assert "groupByRepo(all)" in js
    assert "repo collapsible" in js
    assert "REPO_COLLAPSE_KEY = 'initiatives-repo-collapsed'" in js
    # the standalone lane + partition are gone; undocumented cards get an inline badge instead.
    assert "partitionInitiatives" not in js
    assert "renderEmerging" not in js
    assert "emerging-badge" in js
    assert "v.undocumented) row1.appendChild" in js
    # flat + recency render the full FILTERED stream (no documented/emerging split anymore).
    assert "all.forEach(function(v){ if(visible(v))" in js
    assert "rviews = all.filter(visible)" in js
    # search + triage compose (AND) via the single `visible` predicate.
    assert "matchQ(v, q) && matchState(v, sf)" in js


def test_render_html_embeds_inline_emerging_badge_not_lane():
    # A mixed doc/session snapshot renders session-only cards INLINE with an "emerging" badge —
    # the standalone "Emerging / undocumented" lane is gone.
    rows = [_row(slug="doc"), _row(slug="emg", undocumented=True, source="session")]
    html = viewer.render_html(viewer.build_model(rows, now=NOW))
    assert "emerging-badge" in html                 # the inline badge (CSS + JS)
    assert "Emerging / undocumented" not in html    # the retired lane header is gone
    assert "auto-detected, may include one-offs" not in html
    assert ".emerging{" not in html                 # the retired lane CSS block is gone


# --------------------------------------------------------------------------- #
# Read-only Q&A sidebar — POST /api/ask + the chat pane render (Phase 1 assistant)
# --------------------------------------------------------------------------- #
def test_parse_ask_question_variants():
    assert viewer._parse_ask_question(b'{"question": "what is blocked?"}') == "what is blocked?"
    assert viewer._parse_ask_question(b'{"question": "  padded  "}') == "padded"
    assert viewer._parse_ask_question(b'{"nope": 1}') == ""       # missing key
    assert viewer._parse_ask_question(b'not json') == ""          # unparseable
    assert viewer._parse_ask_question(b'{"question": 5}') == ""   # non-string
    assert viewer._parse_ask_question(b"") == ""                  # empty body
    assert viewer._parse_ask_question(None) == ""


def test_route_ask_calls_asker_and_returns_answer():
    seen = {}

    def asker(q):
        seen["q"] = q
        return {"ok": True, "intent": "blocked_on_me",
                "answer": "clawgate-agent-loop awaits you.",
                "sources": [{"slug": "clawgate-agent-loop", "repo": "devrc"}]}

    status, ctype, body = viewer.route_request(
        "/api/ask", _FakeProvider(), method="POST",
        body=b'{"question": "what is blocked on me?"}', asker=asker)
    assert status == 200
    assert "application/json" in ctype
    payload = json.loads(body)
    assert payload["ok"] is True
    assert payload["answer"] == "clawgate-agent-loop awaits you."
    assert payload["sources"][0]["slug"] == "clawgate-agent-loop"
    assert payload["intent"] == "blocked_on_me"
    assert seen["q"] == "what is blocked on me?"


def test_route_ask_missing_question_400():
    status, _ctype, body = viewer.route_request(
        "/api/ask", _FakeProvider(), method="POST", body=b'{}', asker=lambda q: {})
    assert status == 400
    assert json.loads(body)["ok"] is False


def test_route_ask_no_asker_503():
    status, _ctype, body = viewer.route_request(
        "/api/ask", _FakeProvider(), method="POST",
        body=b'{"question": "x"}', asker=None)
    assert status == 503
    assert json.loads(body)["ok"] is False


def test_route_ask_asker_error_is_caught_200():
    def boom(q):
        raise RuntimeError("assistant exploded")

    status, _ctype, body = viewer.route_request(
        "/api/ask", _FakeProvider(), method="POST",
        body=b'{"question": "x"}', asker=boom)
    assert status == 200                       # never 500s the request
    payload = json.loads(body)
    assert payload["ok"] is False
    assert payload["sources"] == []


def test_route_ask_is_get_safe_404():
    # /api/ask is POST-only; a GET falls through to the 404 (no read side effect).
    status, _ctype, _body = viewer.route_request("/api/ask", _FakeProvider(), method="GET")
    assert status == 404


def test_default_ask_errors_when_store_unreachable():
    # default_ask must degrade (not raise) when the provider snapshot has no model.
    res = viewer.default_ask("what's blocked?", _FakeProvider(error="db down"))
    assert res["ok"] is False
    assert res["sources"] == []


def test_default_ask_passes_provider_views_to_assistant(monkeypatch):
    # default_ask reuses the provider's CACHED snapshot (no second DB read) and hands the
    # flat views + live_unmatched straight to assistant.ask.
    model = viewer.build_model([_row(slug="a-slug")], now=NOW)
    captured = {}

    class _StubAssistant:
        def ask(self, question, *, views=None, unmatched=None):
            captured["question"] = question
            captured["views"] = views
            captured["unmatched"] = unmatched
            return {"ok": True, "answer": "ok", "sources": [], "intent": "overview"}

    monkeypatch.setattr(viewer, "_assistant", lambda: _StubAssistant())
    res = viewer.default_ask("what's up", _FakeProvider(model=model))
    assert res["answer"] == "ok"
    assert captured["question"] == "what's up"
    assert [v["slug"] for v in captured["views"]] == ["a-slug"]


def test_render_html_includes_chat_pane_and_ask_wiring():
    model = viewer.build_model([_row(slug="s")], now=NOW)
    html = viewer.render_html(model, None)
    # the toggle button + the sidebar element + the input/form are present
    assert 'id="ask-toggle"' in html
    assert 'id="chat"' in html and 'class="chat"' in html
    assert 'id="chat-input"' in html and 'id="chat-form"' in html
    # the client posts to the read-only endpoint and renders sources
    assert "/api/ask" in viewer._JS
    assert "renderSources" in viewer._JS
    # the input is disabled while a request is in flight (debounce/lockout)
    assert "chatInput.disabled = true" in viewer._JS
    assert "askInFlight" in viewer._JS


def test_render_html_chat_answer_rendered_via_xss_safe_markdown():
    # The agent answer now renders MARKDOWN via mdToHtml (which HTML-ESCAPES first), written
    # through innerHTML — NOT raw model text. The question stays plain (createTextNode).
    assert "aEl.innerHTML = mdToHtml(answer)" in viewer._JS
    # mdToHtml escapes before transforming, so no raw model text can reach innerHTML.
    assert "function mdToHtml(" in viewer._JS
    assert "function mdEscape(" in viewer._JS
    # the streaming client posts to the SSE endpoint.
    assert "/api/ask/stream" in viewer._JS


def test_render_html_chat_streams_and_renders_sources():
    # streaming client: reads the response body, parses SSE frames, renders sources on done.
    assert "res.body.getReader()" in viewer._JS
    assert "TextDecoder" in viewer._JS
    assert "renderSources(block, msg.sources)" in viewer._JS


def test_chat_pane_absent_from_error_page():
    # the graceful error page has no JS/model island — and no chat pane wiring.
    html = viewer.render_html(None, "OperationalError")
    assert 'id="chat"' not in html
    assert "store unreachable" in html


# --------------------------------------------------------------------------- #
# recommend_next_step — build_model attaches a grounded recommendation per view.
# --------------------------------------------------------------------------- #
def test_build_model_attaches_recommended_next_step_from_handoff():
    # _row has next_step="wire the systemd unit" → basis handoff (priority 1).
    model = viewer.build_model([_row(slug="rec-doc")], now=NOW)
    v = model["flat"][0]
    assert v["recommended_next_step"] == {"text": "wire the systemd unit", "basis": "handoff"}


def test_build_model_recommended_next_step_falls_to_open_pr_without_next_step():
    # No parsed next_step, but an open PR → an inferred open-pr recommendation (the Emerging gap).
    model = viewer.build_model(
        [_row(slug="rec-pr", next_step="", open_investigations=[],
              open_prs=[{"number": 9, "title": "feat: thing"}])], now=NOW)
    rec = model["flat"][0]["recommended_next_step"]
    assert rec["basis"] == "open-pr"
    assert "#9" in rec["text"]


def test_build_model_recommended_next_step_none_when_no_signal():
    model = viewer.build_model(
        [_row(slug="rec-none", next_step="", open_prs=[], open_investigations=[],
              summary="", momentum="active")], now=NOW)
    assert model["flat"][0]["recommended_next_step"] is None


def test_api_json_carries_recommended_next_step():
    model = viewer.build_model([_row(slug="rec-json")], now=NOW)
    payload = viewer.model_to_json(model, None)
    assert payload["flat"][0]["recommended_next_step"]["basis"] == "handoff"


# --------------------------------------------------------------------------- #
# POST /api/dispatch — create a clawgate Task (injected dispatcher; no network).
# --------------------------------------------------------------------------- #
def _dispatch_provider(slug="disp-slug"):
    model = viewer.build_model([_row(slug=slug)], now=NOW)
    return _FakeProvider(model=model), model


def _dispatch_body(repo, slug):
    return json.dumps({"repo": repo, "slug": slug}).encode("utf-8")


def test_route_dispatch_happy_path_200_with_injected_dispatcher():
    prov, model = _dispatch_provider()
    seen = {}

    def dispatcher(view):
        seen["slug"] = view["slug"]
        return {"ok": True, "task_id": 77, "error": None}

    status, ctype, body = viewer.route_request(
        "/api/dispatch", prov, method="POST",
        body=_dispatch_body("/home/zach/workspace/devrc", "disp-slug"),
        dispatcher=dispatcher)
    assert status == 200
    payload = json.loads(body)
    assert payload == {"ok": True, "task_id": 77}
    assert seen["slug"] == "disp-slug"  # the resolved view was handed to the dispatcher


def test_route_dispatch_matches_on_repo_name_too():
    prov, _ = _dispatch_provider()
    status, _c, body = viewer.route_request(
        "/api/dispatch", prov, method="POST",
        body=_dispatch_body("devrc", "disp-slug"),  # repo_name, not the full path
        dispatcher=lambda v: {"ok": True, "task_id": 1, "error": None})
    assert status == 200
    assert json.loads(body)["ok"] is True


def test_route_dispatch_400_on_missing_fields():
    prov, _ = _dispatch_provider()
    status, _c, body = viewer.route_request(
        "/api/dispatch", prov, method="POST", body=_dispatch_body("", ""),
        dispatcher=lambda v: {"ok": True, "task_id": 1, "error": None})
    assert status == 400
    assert json.loads(body)["ok"] is False


def test_route_dispatch_404_on_unknown_slug():
    prov, _ = _dispatch_provider()
    called = {"n": 0}

    def dispatcher(view):
        called["n"] += 1
        return {"ok": True, "task_id": 1, "error": None}

    status, _c, body = viewer.route_request(
        "/api/dispatch", prov, method="POST",
        body=_dispatch_body("devrc", "no-such-slug"), dispatcher=dispatcher)
    assert status == 404
    assert json.loads(body)["ok"] is False
    assert called["n"] == 0  # never dispatched for an unknown initiative


def test_route_dispatch_502_on_dispatch_failure():
    prov, _ = _dispatch_provider()
    status, _c, body = viewer.route_request(
        "/api/dispatch", prov, method="POST",
        body=_dispatch_body("devrc", "disp-slug"),
        dispatcher=lambda v: {"ok": False, "task_id": None,
                              "error": "clawgate unreachable"})
    assert status == 502
    payload = json.loads(body)
    assert payload["ok"] is False
    assert payload["error"] == "clawgate unreachable"


def test_route_dispatch_502_when_dispatcher_raises():
    prov, _ = _dispatch_provider()

    def boom(view):
        raise RuntimeError("kaboom")

    status, _c, body = viewer.route_request(
        "/api/dispatch", prov, method="POST",
        body=_dispatch_body("devrc", "disp-slug"), dispatcher=boom)
    assert status == 502  # never a 500 — wrapped like /api/ask
    assert json.loads(body)["ok"] is False


def test_route_dispatch_lazy_load_path_used_when_no_dispatcher(monkeypatch):
    """With NO injected dispatcher (the LIVE serve() path), the route lazily resolves the
    sibling `dispatch.py` via `_dispatch().dispatch_initiative`. Exercises that real path."""
    prov, _ = _dispatch_provider()
    seen = {}

    class _FakeDispatchMod:
        @staticmethod
        def dispatch_initiative(view):
            seen["slug"] = view["slug"]
            return {"ok": True, "task_id": 5, "error": None}

    monkeypatch.setattr(viewer, "_dispatch", lambda: _FakeDispatchMod)
    status, _c, body = viewer.route_request(
        "/api/dispatch", prov, method="POST",
        body=_dispatch_body("devrc", "disp-slug"))  # dispatcher defaults to None → lazy load
    assert status == 200
    assert json.loads(body) == {"ok": True, "task_id": 5}
    assert seen["slug"] == "disp-slug"


def test_route_dispatch_502_when_lazy_load_import_fails(monkeypatch):
    """A dispatch.py import failure must degrade to a graceful 502, NOT the outer handler's
    caught-500 — the resolution sits INSIDE the try (regression guard for that fix)."""
    prov, _ = _dispatch_provider()

    def _boom_import():
        raise ImportError("cannot load dispatch.py")

    monkeypatch.setattr(viewer, "_dispatch", _boom_import)
    status, _c, body = viewer.route_request(
        "/api/dispatch", prov, method="POST",
        body=_dispatch_body("devrc", "disp-slug"))  # no dispatcher → lazy load raises
    assert status == 502  # NOT 500 — the lazy resolution is wrapped
    assert json.loads(body)["ok"] is False


# =========================================================================== #
# Phase-1 board redesign — derived triage state, two-line cards, repo grouping,
# the sticky triage bar, and inline emerging cards.
# =========================================================================== #

# --- derive_state: the pure classifier (state + line2). No DB. ---------------- #
def _view(**over):
    """A minimal view dict (the shape derive_state consumes)."""
    v = {"slug": "s", "status": "", "next_step": "", "summary": "", "momentum": "active",
         "age": "3h", "live_task": "", "live_tasks": [], "tmux_sessions": [],
         "face_message": None, "recent_messages": [], "recommended_next_step": None}
    v.update(over)
    return v


def test_derive_state_needs_you_from_blocked_marker():
    st, line2 = viewer.derive_state(_view(status="awaiting your review before merge"))
    assert st == "needs_you"
    assert line2 == "awaiting your review before merge"


def test_derive_state_needs_you_beats_stalled():
    # needs_you is top precedence: a stalled card that ALSO has a blocker is needs_you (v2).
    v = _view(status="blocked on your decision", momentum="stalled",
              live_tasks=["running thing"], tmux_sessions=["Pool-1"])
    st, line2 = viewer.derive_state(v)
    assert st == "needs_you"
    assert "blocked on your decision" in line2
    assert viewer.derive_live(v) is True   # live is an independent overlay, not the state


def test_derive_state_stalled_beats_slowing_beats_active():
    # v2 precedence among the momentum states: stalled > slowing > active.
    assert viewer.derive_state(_view(momentum="stalled"))[0] == "stalled"
    assert viewer.derive_state(_view(momentum="slowing"))[0] == "slowing"
    assert viewer.derive_state(_view(momentum="active"))[0] == "active"
    assert viewer.derive_state(_view(momentum=None))[0] == "active"   # unknown → active


def test_derive_state_needs_you_line2_prefers_field_with_marker():
    # the blocker line is the field that actually tripped a marker (next_step here, not status).
    v = _view(status="just a status note", next_step="pending your go-ahead")
    st, line2 = viewer.derive_state(v)
    assert st == "needs_you"
    assert line2 == "pending your go-ahead"


def test_derive_state_live_is_a_badge_not_a_state():
    # v2: `live` NO LONGER overrides the state. A stalled card that's also live classifies as
    # stalled, carries the live badge, and its line2 stays the actionable next-step (not "agent
    # live: …"). This is the whole point of the decouple (19 agents no longer hijack the board).
    v = _view(momentum="stalled", next_step="finish the canary rollout",
              live_task="canary rollout", live_tasks=["canary rollout"])
    st, line2 = viewer.derive_state(v)
    assert st == "stalled"
    assert viewer.derive_live(v) is True
    assert line2 == "finish the canary rollout"   # actionable, NOT "agent live: …"


def test_derive_live_from_tmux_sessions_or_tasks_only():
    assert viewer.derive_live(_view(tmux_sessions=["main:8-1"])) is True
    assert viewer.derive_live(_view(live_tasks=["a task"])) is True
    assert viewer.derive_live(_view()) is False   # no live signal → no badge


def test_derive_state_stalled_line2_uses_last_activity_when_no_action():
    # v2: with no rec/next_step, a stalled card's line2 is "last: <face/last prompt>" (the age is
    # on line 1 now, so line2 stays actionable/informative, not a "stalled <age>" restatement).
    v = _view(momentum="stalled", age="3w",
              face_message={"text": "pick up the migration", "ts": 1.0})
    st, line2 = viewer.derive_state(v)
    assert st == "stalled"
    assert line2 == "last: pick up the migration"


def test_derive_state_slowing_line2_uses_last_activity():
    v = _view(momentum="slowing", face_message={"text": "cooling off here", "ts": 1.0})
    st, line2 = viewer.derive_state(v)
    assert st == "slowing"
    assert line2 == "last: cooling off here"


def test_derive_state_stalled_line2_prefers_actionable_next_step():
    # a stalled card WITH a next-step shows the ACTION (not "last: …").
    v = _view(momentum="stalled", next_step="resume the migration",
              face_message={"text": "old prompt", "ts": 1.0})
    assert viewer.derive_state(v) == ("stalled", "resume the migration")


def test_derive_state_stalled_falls_back_to_recent_message():
    v = _view(momentum="stalled", age="2w", face_message=None,
              recent_messages=[{"text": "the last thing I said", "ts": 1.0}])
    st, line2 = viewer.derive_state(v)
    assert st == "stalled"
    assert "the last thing I said" in line2


def test_derive_state_stalled_no_signal_line2_empty():
    # v2: a stalled card with no rec/next_step/face/status/summary → line2 is "" (the age is on
    # line 1; nothing actionable to show).
    v = _view(momentum="stalled", age="5w")
    st, line2 = viewer.derive_state(v)
    assert st == "stalled" and line2 == ""


def test_derive_state_active_uses_recommended_then_next_step_then_status_then_summary():
    assert viewer.derive_state(
        _view(recommended_next_step={"text": "wire the unit", "basis": "handoff"})) == (
        "active", "wire the unit")
    assert viewer.derive_state(_view(next_step="do the thing")) == ("active", "do the thing")
    assert viewer.derive_state(_view(status="in progress")) == ("active", "in progress")
    assert viewer.derive_state(_view(summary="a summary line")) == ("active", "a summary line")


def test_derive_state_line2_trimmed_to_bound():
    _st, line2 = viewer.derive_state(_view(next_step="x" * 500))
    assert len(line2) <= viewer.LINE2_TRIM
    assert line2.endswith("…")


def test_derive_state_fallback_when_assistant_unavailable(monkeypatch):
    # assistant sibling can't load → derive_state still classifies needs_you via the local copy.
    def boom():
        raise ImportError("assistant unavailable")

    monkeypatch.setattr(viewer, "_assistant", boom)
    st, line2 = viewer.derive_state(_view(next_step="pending your go-ahead"))
    assert st == "needs_you"
    assert "pending your go-ahead" in line2
    # a non-blocked view still classifies (active) on the fallback path.
    assert viewer.derive_state(_view(next_step="ship it"))[0] == "active"


def test_fallback_blocking_hits_parity_with_assistant():
    # PARITY: the local fallback marker set + fields are a VERBATIM copy of assistant's, pinned
    # so they can never drift (assistant._blocking_hits is the single source of truth).
    assistant = viewer._assistant()
    assert viewer._FALLBACK_BLOCKED_MARKERS == assistant.BLOCKED_MARKERS
    assert viewer._FALLBACK_BLOCKED_FIELDS == assistant._BLOCKED_FIELDS


def test_fallback_blocking_hits_matches_assistant_on_samples():
    # Belt-and-suspenders: fallback hits equal assistant._blocking_hits on real views.
    assistant = viewer._assistant()
    for v in [_view(status="awaiting your review"), _view(next_step="ship it"),
              _view(status="blocked on you", next_step="pending your call")]:
        assert viewer._fallback_blocking_hits(v) == assistant._blocking_hits(v)


def test_blocking_hits_for_uses_assistant_as_single_source(monkeypatch):
    # _blocking_hits_for delegates to assistant._blocking_hits (not a re-hardcoded copy).
    seen = {}

    class _StubAssistant:
        @staticmethod
        def _blocking_hits(v):
            seen["called"] = True
            return ["sentinel-marker"]

    monkeypatch.setattr(viewer, "_assistant", lambda: _StubAssistant)
    assert viewer._blocking_hits_for(_view()) == ["sentinel-marker"]
    assert seen["called"] is True


# --- _initiative_view / build_model attach state + line2 --------------------- #
def test_build_model_attaches_state_and_line2():
    v = viewer.build_model([_row(slug="s")], now=NOW)["flat"][0]
    assert v["state"] == "active"                    # default _row: active + a documented next_step
    assert v["line2"] == "wire the systemd unit"     # recommended_next_step.text


def test_build_model_state_needs_you_from_blocked_status():
    # a blocked status → needs_you; line2 is the actionable next-step (default _row's next_step).
    v = viewer.build_model([_row(slug="s", status="awaiting your sign-off")], now=NOW)["flat"][0]
    assert v["state"] == "needs_you"
    assert v["line2"] == "wire the systemd unit"


def test_build_model_state_needs_you_line2_blocker_when_no_action():
    # with no next_step/rec, a needs_you card's line2 falls back to the blocker text.
    v = viewer.build_model(
        [_row(slug="s", status="awaiting your sign-off", next_step="",
              open_investigations=[], open_prs=[])], now=NOW)["flat"][0]
    assert v["state"] == "needs_you"
    assert "awaiting your sign-off" in v["line2"]


def test_build_model_live_is_badge_state_unchanged():
    # v2: a live tmux task sets the `live` overlay badge but does NOT change the state (active
    # here, from momentum). line2 stays the actionable next-step.
    rows = [_row(slug="s")]
    rows[0]["tmux_tasks"] = ["a live task"]
    v = viewer.build_model(rows, now=NOW)["flat"][0]
    assert v["state"] == "active"
    assert v["live"] is True
    assert v["line2"] == "wire the systemd unit"


def test_build_model_state_slowing_is_distinct():
    # "slowing" is now its OWN state (not collapsed into active).
    v = viewer.build_model([_row(slug="s", momentum="slowing")], now=NOW)["flat"][0]
    assert v["state"] == "slowing"


def test_build_model_state_stalled():
    # Full pipeline: a stalled card with a recent prompt → state stalled, no live badge, and an
    # ACTIONABLE line2 (the grounded "Continue where you left off" recommendation from nextstep,
    # which outranks the bare "last:" fallback — line2 is always actionable in v2).
    v = viewer.build_model(
        [_row(slug="s", momentum="stalled", next_step="", open_investigations=[], open_prs=[],
              last_touch=NOW - timedelta(days=21),
              recent_messages=[{"text": "the last thing", "ts": 1.0}])], now=NOW)["flat"][0]
    assert v["state"] == "stalled"
    assert v["live"] is False
    assert v["line2"] == "Continue where you left off: the last thing"


def test_build_model_state_stalled_signalless_gets_resume_or_drop_line2():
    # A stalled card with NO next_step/pr/investigation/face → nextstep's "stalled" resume-or-drop
    # recommendation grounds line2 (still actionable). Age is on line 1.
    v = viewer.build_model(
        [_row(slug="s", momentum="stalled", next_step="", open_investigations=[], open_prs=[],
              summary="", recent_messages=[], last_touch=NOW - timedelta(days=21))],
        now=NOW)["flat"][0]
    assert v["state"] == "stalled"
    assert "resume or drop" in v["line2"]


def test_build_model_state_graceful_with_missing_fields():
    # a minimal row (no status/next_step/momentum) still classifies + never raises.
    v = viewer.build_model([_row(slug="s", momentum=None, next_step="", summary="")],
                           now=NOW)["flat"][0]
    assert v["state"] in ("active", "needs_you", "live", "stalled")
    assert isinstance(v["line2"], str)


def test_model_to_json_flat_includes_state_line2_and_live():
    rows = [_row(slug="a")]
    rows[0]["tmux_tasks"] = ["running"]
    j = viewer.model_to_json(viewer.build_model(rows, now=NOW), None)
    assert j["flat"][0]["state"] == "active"
    assert j["flat"][0]["line2"] == "wire the systemd unit"
    assert j["flat"][0]["live"] is True   # the overlay badge flag reaches the client


def test_build_model_derive_state_per_row_isolation(monkeypatch):
    # Part D: a bad row must degrade ONE card (safe active/empty/no-live default), not break the
    # whole render — mirroring the recommend_next_step guard.
    real = viewer.derive_state

    def flaky(view):
        if view.get("slug") == "boom":
            raise RuntimeError("bad row")
        return real(view)

    monkeypatch.setattr(viewer, "derive_state", flaky)
    flat = viewer.build_model([_row(slug="boom"), _row(slug="ok")], now=NOW)["flat"]
    by = {v["slug"]: v for v in flat}
    assert by["boom"]["state"] == "active" and by["boom"]["line2"] == "" and by["boom"]["live"] is False
    assert by["ok"]["state"] == "active"   # the healthy row is unaffected


def test_state_counts_over_fixture_set():
    # the summary-header counts are a tally over the derived per-card state (mutually exclusive)
    # + the `live` overlay (which OVERLAPS the states). Assert the data side over a mixed fixture.
    from collections import Counter
    rows = [
        _row(slug="a", status="awaiting your review"),                     # needs_you
        _row(slug="b"),                                                     # active
        _row(slug="c", momentum="slowing"),                                # slowing (cooling)
        _row(slug="e", momentum="stalled", next_step="",
             last_touch=NOW - timedelta(days=30)),                         # stalled
    ]
    live = _row(slug="d")
    live["tmux_tasks"] = ["running"]                                       # active + LIVE badge
    rows.append(live)
    flat = viewer.build_model(rows, now=NOW)["flat"]
    counts = Counter(v["state"] for v in flat)
    assert counts["needs_you"] == 1
    assert counts["slowing"] == 1
    assert counts["stalled"] == 1
    assert counts["active"] == 2                       # b and d (d is live but still active)
    # live is an OVERLAP, counted by the badge (only d here), independent of state.
    assert sum(1 for v in flat if v["live"]) == 1


# --- triage state-filter predicate (node-eval matchState) -------------------- #
def _node_statefilter(views, sf):
    node = _shutil.which("node")
    if not node:
        import pytest
        pytest.skip("node not on PATH — triage matchState JS untested this run")
    body = (
        "var VIEWS = " + json.dumps(views) + ";\n"
        "var SF = " + json.dumps(sf) + ";\n"
        "console.log(JSON.stringify(VIEWS.filter(function(v){ return matchState(v, SF); })"
        ".map(function(v){ return v.slug; })));"
    )
    out = _subprocess.run([node, "-e", viewer._STATEFILTER_JS + "\n" + body],
                          capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_js_matchstate_filters_each_chip():
    # v2: needs_you/stalled/slowing/active filter by STATE; `live` filters by the overlay BADGE
    # (regardless of state). Here `a` is active AND live, `s` is stalled AND live.
    views = [{"slug": "n", "state": "needs_you", "live": False},
             {"slug": "s", "state": "stalled", "live": True},
             {"slug": "w", "state": "slowing", "live": False},
             {"slug": "a", "state": "active", "live": True}]
    assert _node_statefilter(views, "needs_you") == ["n"]
    assert _node_statefilter(views, "stalled") == ["s"]
    assert _node_statefilter(views, "slowing") == ["w"]
    assert _node_statefilter(views, "active") == ["a"]
    # Live filters by badge → BOTH the stalled+live and active+live cards, across states.
    assert _node_statefilter(views, "live") == ["s", "a"]


def test_js_matchstate_all_and_empty_show_everything():
    views = [{"slug": "n", "state": "needs_you", "live": False},
             {"slug": "a", "state": "active", "live": True}]
    assert _node_statefilter(views, "") == ["n", "a"]
    assert _node_statefilter(views, "all") == ["n", "a"]


def test_js_triage_and_search_compose_and():
    # the page's `visible` predicate is matchQ(v,q) && matchState(v,sf); node-eval BOTH to prove
    # they AND: only the card that is needs_you AND matches the query survives.
    node = _shutil.which("node")
    if not node:
        import pytest
        pytest.skip("node not on PATH — compose test untested this run")
    views = [{"slug": "clawgate", "state": "needs_you", "title": "clawgate release"},
             {"slug": "mail", "state": "needs_you", "title": "mail automation"},
             {"slug": "civ", "state": "active", "title": "clawgate adjacent"}]
    body = (
        "var VIEWS = " + json.dumps(views) + ";\n"
        "var Q = 'clawgate'; var SF = 'needs_you';\n"
        "function visible(v){ return matchQ(v, Q) && matchState(v, SF); }\n"
        "console.log(JSON.stringify(VIEWS.filter(visible).map(function(v){return v.slug;})));"
    )
    src = viewer._MATCH_JS + "\n" + viewer._STATEFILTER_JS + "\n" + body
    out = _subprocess.run([node, "-e", src], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == ["clawgate"]   # civ matches Q but not SF; mail vice-versa


# --- repo grouping (node-eval groupByRepo) ----------------------------------- #
def _node_group(views):
    node = _shutil.which("node")
    if not node:
        import pytest
        pytest.skip("node not on PATH — groupByRepo JS untested this run")
    body = (
        "var VIEWS = " + json.dumps(views) + ";\n"
        "console.log(JSON.stringify(groupByRepo(VIEWS).map(function(g){"
        "return {name:g.name, needs:g.needs, slugs:g.items.map(function(v){return v.slug;})};})));"
    )
    out = _subprocess.run([node, "-e", viewer._GROUP_JS + "\n" + body],
                          capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_js_group_by_repo_sections_counts_and_needs_ordering():
    views = [
        {"slug": "d-active", "repo_name": "devrc", "state": "active"},
        {"slug": "d-needs",  "repo_name": "devrc", "state": "needs_you"},
        {"slug": "h-active", "repo_name": "homelab", "state": "active"},
    ]
    got = _node_group(views)
    # devrc has a needs_you → sorts ahead of homelab (needs-count DESC).
    assert [g["name"] for g in got] == ["devrc", "homelab"]
    devrc = next(g for g in got if g["name"] == "devrc")
    assert devrc["needs"] == 1
    # within a repo, needs_you sorts before active (state precedence).
    assert devrc["slugs"] == ["d-needs", "d-active"]


def test_js_group_repo_order_by_recency_when_no_needs():
    # no needs_you anywhere → repos order by most-recent activity = first appearance in the DESC
    # flat stream (hot before cold).
    views = [
        {"slug": "h1", "repo_name": "hot", "state": "active"},
        {"slug": "c1", "repo_name": "cold", "state": "active"},
        {"slug": "h2", "repo_name": "hot", "state": "stalled"},
    ]
    got = _node_group(views)
    assert [g["name"] for g in got] == ["hot", "cold"]


def test_js_group_within_repo_full_state_precedence_then_recency():
    # v2 precedence: needs_you → stalled → slowing → active (recency preserved within a state).
    views = [
        {"slug": "active1", "repo_name": "r", "state": "active"},
        {"slug": "slowing1", "repo_name": "r", "state": "slowing"},
        {"slug": "stalled1", "repo_name": "r", "state": "stalled"},
        {"slug": "needs1", "repo_name": "r", "state": "needs_you"},
        {"slug": "active2", "repo_name": "r", "state": "active"},
    ]
    got = _node_group(views)
    assert got[0]["slugs"] == ["needs1", "stalled1", "slowing1", "active1", "active2"]


def test_js_group_undocumented_not_segregated():
    # Undocumented cards group with their repo (no separate lane) — ordinary cards here.
    views = [
        {"slug": "doc", "repo_name": "r", "state": "active", "undocumented": False},
        {"slug": "emg", "repo_name": "r", "state": "active", "undocumented": True},
    ]
    got = _node_group(views)
    assert len(got) == 1
    assert set(got[0]["slugs"]) == {"doc", "emg"}


def test_js_group_unknown_repo_name_bucketed():
    got = _node_group([{"slug": "x", "state": "active"}])   # no repo_name
    assert got[0]["name"] == "(unknown repo)"


# --- two-line collapsed card render (source markers) ------------------------- #
def _card_body():
    js = viewer._JS
    ci = js.index("function card(")
    return js[ci:js.index("function renderLiveNow(")]


def test_js_card_is_two_line_with_badge_slug_age_and_emerging():
    body = _card_body()
    # LINE 1: state badge glyph + slug + age (+ emerging badge when undocumented).
    assert "sbadge state-" in body and "STATE_GLYPH[st]" in body
    assert "el('span', 'slug', v.slug)" in body
    assert "el('span', 'age', v.age)" in body
    assert "if(v.undocumented) row1.appendChild(el('span', 'emerging-badge', 'emerging'))" in body
    # LINE 2: the single state line, textContent-only.
    assert "'line2 state-'" in body and "v.line2" in body
    assert "createTextNode(v.line2)" in body


def test_js_card_moved_lines_not_on_collapsed_card():
    # the current/start/you/live + identity lines are NOT on the collapsed card (moved to detail).
    body = _card_body()
    assert "v.opening_message" not in body    # start -> detail
    assert "v.face_message" not in body       # you -> detail
    assert "live-task" not in body            # live -> detail
    assert "v.identity" not in body           # identity/summary primary -> detail
    assert "'current" not in body             # status current -> detail
    assert "v.tmux_sessions" not in body      # tmux tags -> gone from the card


def test_js_card_dispatch_only_when_recommendation_exists():
    body = _card_body()
    # Phase-3: the action row is driven by the pure cardActions(v) map; the dispatch/resume button
    # is emitted ONLY as a kind:'dispatch' descriptor (cardActions emits it iff a grounded rec
    # exists), reusing dispatchNextStep + the label VERBATIM. (The gating itself is proven by the
    # node-eval cardActions tests below — this pins the DOM wiring.)
    assert "cardActions(v).forEach(" in body
    assert "a.kind === 'dispatch'" in body
    assert "el('button', 'dispatch-btn', a.label)" in body
    assert "dispatchNextStep(v, btn, dstat)" in body
    assert "ev.stopPropagation()" in body     # the button click doesn't toggle expand
    assert "if(ev.target.closest('a')) return;" in body   # link-guard kept


def test_render_html_two_line_card_and_detail_moved_lines():
    # a rendered page ships the two-line card markers AND the moved lines living in the detail.
    html = viewer.render_html(viewer.build_model([_row(slug="s")], now=NOW))
    assert "STATE_GLYPH" in html and "v.line2" in html            # two-line card
    assert "d.identity || (v && v.identity) || d.summary" in html  # identity in detail
    assert "start" in html and "you" in html                       # start/you labels in detail


# --- collapse / auto-expand-on-match wiring ---------------------------------- #
def test_js_repo_sections_collapsible_and_auto_expand():
    js = viewer._JS
    assert "REPO_COLLAPSE_KEY = 'initiatives-repo-collapsed'" in js
    assert "function isRepoCollapsed(" in js and "function setRepoCollapsed(" in js
    assert "repo collapsible" in js                          # collapsible section
    assert "isRepoCollapsed(g.name)" in js                   # remembers per-repo state
    # a filter force-opens a collapsed section that has matches (NOT persisted).
    assert "!collapsed || (filtering && vis.length > 0)" in js
    # a section with zero matches under a filter is hidden entirely.
    assert "if(!vis.length) return;" in js
    assert "var filtering = !!(q || sf);" in js              # search OR triage = filtering


# --- sticky triage bar (markers + smoke) ------------------------------------- #
def test_js_triage_bar_wired():
    js = viewer._JS
    assert "function renderTriage(" in js
    assert "state.triage" in js
    # v3: FOUR chips — Needs you / Stalled / Cooling / All. The old [● Live N] chip is RETIRED
    # (the pinned Live-now strip replaces it).
    assert "label:'Needs you'" in js and "label:'Stalled'" in js
    assert "label:'Cooling'" in js and "label:'All'" in js
    assert "label:'Live'" not in js                       # the Live triage chip is gone
    # the Cooling chip filters the `slowing` state.
    assert "k:'slowing'" in js
    # counts come from stateCounts; a chip click sets state.triage and re-renders.
    assert "stateCounts.needs_you" in js and "stateCounts.slowing" in js
    assert "state.triage = ch.k" in js


def test_js_live_is_badge_not_state():
    js = viewer._JS
    # `live` is decoupled: it's an overlay BADGE keyed off v.live, never a state value.
    assert "STATE_GLYPH = {needs_you:'⚠', stalled:'◑', slowing:'~', active:'→'}" in js
    assert "LIVE_GLYPH = '●'" in js
    # the card renders the badge from v.live (independent of state).
    body = _card_body()
    assert "if(v.live){" in body
    assert "live-badge" in body
    assert "LIVE_GLYPH + ' live'" in body
    # a live card carries an `is-live` class but its state class is still the momentum state.
    assert "'ini state-' + st + (v.live ? ' is-live' : '')" in body


def test_js_slowing_state_has_distinct_glyph_and_color():
    # the restored "cooling"/slowing cue: distinct glyph (~) + yellow, in both JS + CSS.
    js, css = viewer._JS, viewer._CSS
    assert "slowing:'~'" in js
    assert "slowing:'cooling'" in js       # STATE_LABEL
    assert ".sbadge.state-slowing{color:var(--yellow)}" in css
    assert ".ini.state-slowing{border-left-color:var(--yellow)}" in css


def test_css_state_colors_match_momentum_semantics():
    css = viewer._CSS
    # needs_you=orange, stalled=gray, slowing=yellow, active=blue; live badge=green.
    assert ".ini.state-needs_you{border-left-color:var(--orange)}" in css
    assert ".ini.state-stalled{border-left-color:var(--gray)}" in css
    assert ".ini.state-active{border-left-color:var(--blue)}" in css
    assert ".live-badge{" in css and "color:var(--green)" in css
    # the retired live-as-state CSS is gone.
    assert ".ini.state-live{" not in css
    assert ".sbadge.state-live{" not in css


def test_render_html_live_card_shows_badge_and_state_class():
    # a live+active card renders with the state-active class AND the live badge in the payload.
    rows = [_row(slug="s")]
    rows[0]["tmux_tasks"] = ["running now"]
    m = viewer.build_model(rows, now=NOW)
    assert m["flat"][0]["live"] is True and m["flat"][0]["state"] == "active"
    html = viewer.render_html(m)
    assert '"live": true' in html or '"live":true' in html   # the badge flag in the JSON island
    assert "LIVE_GLYPH" in html                               # the badge renderer ships


def test_render_html_has_sticky_triage_bar_container():
    html = viewer.render_html(viewer.build_model([_row(slug="s")], now=NOW))
    assert 'id="triage"' in html and 'class="triage"' in html
    assert ".triage{position:sticky" in html                 # the sticky CSS


def test_render_html_summary_header_shows_state_counts():
    html = viewer.render_html(viewer.build_model([_row(slug="s")], now=NOW))
    # the header count text is the state tally (client-side, from stateCounts): v3 order is
    # need you · stalled · cooling · active, plus a "N live now" pane count that rides along.
    assert "' need you · '" in html and "' stalled · '" in html
    assert "' cooling · '" in html
    assert "' active'" in html
    assert "' live · '" not in html            # the old card-badge "N live" stat is retired
    assert "' live now'" in html               # replaced by the Live-now pane union count


# --- render smoke: no exceptions; page has the key Phase-1 surfaces ---------- #
def test_render_smoke_triage_repo_section_and_state_badge():
    rows = [_row(slug="a", status="awaiting your review"),   # needs_you
            _row(slug="b")]                                   # active
    html = viewer.render_html(viewer.build_model(rows, now=NOW), None)
    assert html.startswith("<!doctype html>")
    assert 'id="triage"' in html                              # the sticky triage bar container
    assert "groupByRepo(all)" in html                         # repo-section rendering
    assert "repo collapsible" in html                         # collapsible section
    assert "⚠" in html and "◑" in html              # the needs_you / stalled glyphs
    assert "Needs you" in html and "Stalled" in html          # triage chip labels


# --- regressions: preserved endpoints/behaviours ----------------------------- #
def test_regression_dispatch_button_still_posts_to_api_dispatch():
    assert "fetch('/api/dispatch'" in viewer._JS
    assert "function dispatchNextStep(" in viewer._JS


def test_regression_ask_and_store_error_paths_intact():
    assert "/api/ask" in viewer._JS
    err = viewer.render_html(None, "OperationalError: connection refused")
    assert "store unreachable" in err
    assert 'id="triage"' not in err   # the error page has no triage bar / SPA


# =========================================================================== #
# Phase 2 — the archive / done lifecycle (manual done/drop + resurface + Done view).
# =========================================================================== #

# --- build_model: suppression + resurface + the Done-view list -------------- #
def _archived_row(slug, archived_at, *, repo="/home/zach/workspace/devrc",
                  title="", reason="done"):
    return {"repo": repo, "slug": slug, "title": title, "reason": reason,
            "archived_at": archived_at}


def test_build_model_suppresses_archived_with_no_new_activity():
    # 'gone' was archived AFTER its last activity → last_touch <= archived_at → hidden from the
    # board, the flat stream, and the counts. 'live' is untouched.
    rows = [_row(slug="live", last_touch=NOW - timedelta(hours=1)),
            _row(slug="gone", last_touch=NOW - timedelta(days=3))]
    archived = [_archived_row("gone", NOW - timedelta(days=1), title="Gone")]
    model = viewer.build_model(rows, now=NOW, archived=archived)
    slugs = [v["slug"] for v in model["flat"]]
    assert slugs == ["live"]                      # 'gone' suppressed
    assert model["total"] == 1                    # count excludes the suppressed card
    # and it is absent from every repo group too
    grouped = [v["slug"] for g in model["repos"] for v in g["initiatives"]]
    assert "gone" not in grouped


def test_build_model_resurfaces_archived_card_on_new_activity():
    # 'back' has activity STRICTLY newer than when it was archived → it resurfaces on the board.
    rows = [_row(slug="back", last_touch=NOW - timedelta(hours=1))]
    archived = [_archived_row("back", NOW - timedelta(days=1))]
    model = viewer.build_model(rows, now=NOW, archived=archived)
    assert [v["slug"] for v in model["flat"]] == ["back"]
    assert model["total"] == 1


def test_build_model_boundary_last_touch_equals_archived_at_is_suppressed():
    # last_touch == archived_at → NOT strictly newer → still suppressed (resurface is '>').
    at = NOW - timedelta(days=1)
    rows = [_row(slug="edge", last_touch=at)]
    model = viewer.build_model(rows, now=NOW, archived=[_archived_row("edge", at)])
    assert model["flat"] == []


def test_build_model_store_unreachable_suppresses_nothing():
    rows = [_row(slug="a"), _row(slug="b")]
    # archived=None (the read failed / store had nothing) → nothing hidden, board renders.
    model = viewer.build_model(rows, now=NOW, archived=None)
    assert {v["slug"] for v in model["flat"]} == {"a", "b"}
    assert model["archived"] == []


def test_build_model_archived_view_includes_aged_out_card_newest_first():
    # The Done view carries the FULL archived set — including 'aged' which is NOT in `latest`
    # (so its title is sourced from the archived row) — sorted newest archived_at first.
    rows = [_row(slug="present", last_touch=NOW - timedelta(days=5))]
    archived = [
        _archived_row("present", NOW - timedelta(days=2), title="Present", reason="done"),
        _archived_row("aged", NOW - timedelta(days=1), title="Aged out", reason="dropped"),
    ]
    model = viewer.build_model(rows, now=NOW, archived=archived)
    done = model["archived"]
    assert [a["slug"] for a in done] == ["aged", "present"]   # newest archived_at first
    aged = done[0]
    assert aged["title"] == "Aged out" and aged["reason"] == "dropped"
    assert aged["repo_name"] == "devrc" and aged["archived_age"]   # rendered label present
    assert model["total"] == 0                                    # 'present' is suppressed


def test_model_to_json_carries_archived_list():
    rows = [_row(slug="x", last_touch=NOW - timedelta(days=5))]
    archived = [_archived_row("x", NOW - timedelta(days=1), title="X")]
    payload = viewer.model_to_json(viewer.build_model(rows, now=NOW, archived=archived), None)
    assert payload["ok"] is True
    assert [a["slug"] for a in payload["archived"]] == ["x"]
    assert payload["flat"] == []                               # suppressed off the board
    # the error payload still has an archived key (so the client never reads undefined)
    assert viewer.model_to_json(None, "boom")["archived"] == []


# --- DataProvider normalizes the (rows, archived) loader shape --------------- #
def test_provider_threads_archived_from_tuple_loader():
    rows = [_row(slug="p", last_touch=NOW - timedelta(days=5))]
    archived = [_archived_row("p", NOW - timedelta(days=1), title="P")]
    prov = viewer.DataProvider(ttl=60, loader=lambda: (rows, archived),
                               tmux=lambda r: True, now_fn=lambda: NOW)
    model, err = prov.snapshot()
    assert err is None
    assert model["flat"] == []                                # suppressed
    assert [a["slug"] for a in model["archived"]] == ["p"]


def test_provider_legacy_list_loader_still_works():
    # A loader that returns just rows (no archived) → archived=[] → nothing suppressed.
    prov = viewer.DataProvider(ttl=60, loader=lambda: [_row(slug="q")],
                               tmux=lambda r: True, now_fn=lambda: NOW)
    model, err = prov.snapshot()
    assert err is None and [v["slug"] for v in model["flat"]] == ["q"]
    assert model["archived"] == []


# --- _parse_archive_body ----------------------------------------------------- #
def test_parse_archive_body_extracts_fields():
    body = json.dumps({"repo": " /r ", "slug": " s ", "reason": " dropped "}).encode()
    assert viewer._parse_archive_body(body) == ("/r", "s", "dropped")


def test_parse_archive_body_missing_and_bad():
    assert viewer._parse_archive_body(None) == ("", "", "")
    assert viewer._parse_archive_body(b"not json") == ("", "", "")
    assert viewer._parse_archive_body(json.dumps({"repo": "/r"}).encode()) == ("/r", "", "")


# --- POST /api/archive + /api/unarchive routes (injected archiver) ----------- #
def _archive_provider(slug="arch-slug"):
    model = viewer.build_model([_row(slug=slug)], now=NOW)
    return _FakeProviderWithInvalidate(model=model)


def _archive_body(repo, slug, reason=None):
    b = {"repo": repo, "slug": slug}
    if reason is not None:
        b["reason"] = reason
    return json.dumps(b).encode("utf-8")


def test_route_archive_happy_path_200_and_invalidates():
    prov = _archive_provider()
    seen = {}

    def archiver(repo, slug, title, reason):
        seen.update(repo=repo, slug=slug, title=title, reason=reason)
        return {"ok": True}

    status, ctype, body = viewer.route_request(
        "/api/archive", prov, method="POST",
        body=_archive_body("/home/zach/workspace/devrc", "arch-slug", "done"),
        archiver=archiver)
    assert status == 200 and json.loads(body) == {"ok": True}
    assert prov.invalidated == 1                       # the card drops from the board at once
    assert seen["slug"] == "arch-slug"
    assert seen["reason"] == "done"
    assert seen["title"] == "Initiatives consolidation Phase 3"   # resolved from the snapshot


def test_route_archive_resolves_title_and_defaults_reason_to_done():
    prov = _archive_provider()
    seen = {}

    def archiver(repo, slug, title, reason):
        seen["reason"] = reason
        return {"ok": True}

    # no reason in the body → the route substitutes "done"
    viewer.route_request("/api/archive", prov, method="POST",
                         body=_archive_body("devrc", "arch-slug"), archiver=archiver)
    assert seen["reason"] == "done"


def test_route_archive_drop_passes_reason_dropped():
    prov = _archive_provider()
    seen = {}
    viewer.route_request("/api/archive", prov, method="POST",
                         body=_archive_body("devrc", "arch-slug", "dropped"),
                         archiver=lambda r, s, t, reason: seen.update(reason=reason) or {"ok": True})
    assert seen["reason"] == "dropped"


def test_route_archive_matches_on_repo_name_too():
    prov = _archive_provider()
    status, _c, body = viewer.route_request(
        "/api/archive", prov, method="POST",
        body=_archive_body("devrc", "arch-slug", "done"),   # short repo_name, not full path
        archiver=lambda r, s, t, reason: {"ok": True})
    assert status == 200 and json.loads(body)["ok"] is True


def test_route_archive_400_on_missing_fields():
    prov = _archive_provider()
    status, _c, body = viewer.route_request(
        "/api/archive", prov, method="POST", body=_archive_body("", ""),
        archiver=lambda r, s, t, reason: {"ok": True})
    assert status == 400 and json.loads(body)["ok"] is False
    assert prov.invalidated == 0


def test_route_archive_502_on_store_failure():
    prov = _archive_provider()
    status, _c, body = viewer.route_request(
        "/api/archive", prov, method="POST",
        body=_archive_body("devrc", "arch-slug"),
        archiver=lambda r, s, t, reason: {"ok": False, "error": "OperationalError"})
    assert status == 502 and json.loads(body)["error"] == "OperationalError"
    assert prov.invalidated == 0                       # nothing changed → don't drop the cache


def test_route_archive_502_when_archiver_raises():
    prov = _archive_provider()

    def boom(repo, slug, title, reason):
        raise RuntimeError("kaboom")

    status, _c, body = viewer.route_request(
        "/api/archive", prov, method="POST",
        body=_archive_body("devrc", "arch-slug"), archiver=boom)
    assert status == 502                               # never a 500 — wrapped like /api/dispatch
    assert json.loads(body)["ok"] is False


def test_route_archive_works_even_when_title_not_in_snapshot():
    # archiving a slug not present in the current snapshot → title resolves to "" (no crash).
    prov = _archive_provider(slug="something-else")
    seen = {}
    status, _c, _b = viewer.route_request(
        "/api/archive", prov, method="POST",
        body=_archive_body("devrc", "arch-slug"),
        archiver=lambda r, s, t, reason: seen.update(title=t) or {"ok": True})
    assert status == 200 and seen["title"] == ""


def test_route_archive_lazy_load_used_when_no_archiver(monkeypatch):
    prov = _archive_provider()
    seen = {}

    class _FakeArchiveMod:
        @staticmethod
        def archive(repo, slug, title, reason):
            seen.update(slug=slug, reason=reason)
            return {"ok": True}

    monkeypatch.setattr(viewer, "_archive", lambda: _FakeArchiveMod)
    status, _c, body = viewer.route_request(
        "/api/archive", prov, method="POST",
        body=_archive_body("devrc", "arch-slug", "dropped"))   # archiver defaults None → lazy
    assert status == 200 and json.loads(body) == {"ok": True}
    assert seen == {"slug": "arch-slug", "reason": "dropped"}


def test_route_unarchive_happy_path_200_and_invalidates():
    prov = _archive_provider()
    seen = {}
    status, _c, body = viewer.route_request(
        "/api/unarchive", prov, method="POST",
        body=_archive_body("devrc", "arch-slug"),
        unarchiver=lambda r, s: seen.update(repo=r, slug=s) or {"ok": True})
    assert status == 200 and json.loads(body) == {"ok": True}
    assert prov.invalidated == 1 and seen["slug"] == "arch-slug"


def test_route_unarchive_400_on_missing_fields():
    prov = _archive_provider()
    status, _c, body = viewer.route_request(
        "/api/unarchive", prov, method="POST", body=_archive_body("", ""),
        unarchiver=lambda r, s: {"ok": True})
    assert status == 400 and json.loads(body)["ok"] is False


def test_route_unarchive_502_on_failure_and_never_500_on_raise():
    prov = _archive_provider()
    status, _c, _b = viewer.route_request(
        "/api/unarchive", prov, method="POST",
        body=_archive_body("devrc", "arch-slug"),
        unarchiver=lambda r, s: {"ok": False, "error": "boom"})
    assert status == 502

    def raiser(r, s):
        raise RuntimeError("x")
    status2, _c2, body2 = viewer.route_request(
        "/api/unarchive", prov, method="POST",
        body=_archive_body("devrc", "arch-slug"), unarchiver=raiser)
    assert status2 == 502 and json.loads(body2)["ok"] is False


# --- JS: node-eval the Phase-2 pure predicates (dropEligible + matchArchived) - #
def _node_drop_eligible(views):
    node = _shutil.which("node")
    if not node:
        import pytest
        pytest.skip("node not on PATH — dropEligible JS untested this run")
    body = (
        "var VIEWS = " + json.dumps(views) + ";\n"
        "console.log(JSON.stringify(VIEWS.filter(dropEligible)"
        ".map(function(v){ return v.slug; })));"
    )
    out = _subprocess.run([node, "-e", viewer._ARCHIVE_JS + "\n" + body],
                          capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_js_drop_eligible_only_stalled_and_slowing():
    views = [{"slug": "n", "state": "needs_you"}, {"slug": "s", "state": "stalled"},
             {"slug": "w", "state": "slowing"}, {"slug": "a", "state": "active"},
             {"slug": "u"}]   # no state → not drop-eligible
    assert _node_drop_eligible(views) == ["s", "w"]


def _node_match_archived(rows, q):
    node = _shutil.which("node")
    if not node:
        import pytest
        pytest.skip("node not on PATH — matchArchived JS untested this run")
    body = (
        "var ROWS = " + json.dumps(rows) + ";\n"
        "var Q = (" + json.dumps(q) + ").trim().toLowerCase();\n"
        "console.log(JSON.stringify(ROWS.filter(function(a){ return matchArchived(a, Q); })"
        ".map(function(a){ return a.slug; })));"
    )
    out = _subprocess.run([node, "-e", viewer._ARCHIVE_JS + "\n" + body],
                          capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_js_match_archived_filters_done_view():
    rows = [{"slug": "clawgate", "title": "release cut", "reason": "done",
             "repo_name": "devrc"},
            {"slug": "mail", "title": "invoice archiver", "reason": "dropped",
             "repo_name": "devrc"}]
    assert _node_match_archived(rows, "") == ["clawgate", "mail"]     # empty → all
    assert _node_match_archived(rows, "release") == ["clawgate"]      # title
    assert _node_match_archived(rows, "dropped") == ["mail"]          # reason
    assert _node_match_archived(rows, "MAIL") == ["mail"]             # case-insensitive slug
    assert _node_match_archived(rows, "nope") == []


# --- JS wiring markers (the DOM code that isn't a pure node-eval predicate) --- #
def test_js_archive_actions_and_snippet_wired():
    js = viewer._JS
    assert "__ARCHIVE_JS__" not in js                              # snippet inlined
    assert js.count("function dropEligible") == 1
    assert js.count("function matchArchived") == 1
    # every card gets [✓ done]; stalled/cooling ALSO get [drop] via dropEligible(v)
    assert "'archive-btn done', '✓ done'" in js
    assert "if(dropEligible(v)){" in js
    assert "'archive-btn drop', 'drop'" in js
    assert "archiveCard(v, doneBtn, astat, 'done', c)" in js
    assert "archiveCard(v, dropBtn, astat, 'dropped', c)" in js
    # the write endpoints + never-clobber-on-failure retry
    assert "fetch('/api/archive'" in js
    assert "fetch('/api/unarchive'" in js
    assert "function archiveCard(" in js and "function unarchiveCard(" in js
    # the dispatch button is preserved alongside the new actions
    assert "fetch('/api/dispatch'" in js


def test_js_done_chip_and_view_wired():
    js = viewer._JS
    assert "state.doneMode" in js
    assert "'✓ Done ' + archivedN" in js                           # the [✓ Done N] chip
    assert "chip state-done" in js
    assert "function renderDoneView(" in js
    assert "if(state.doneMode){ renderDoneView(q)" in js           # render branches into it
    assert "data.archived" in js
    assert "'↺ unarchive'" in js                                   # the Done-view restore button
    assert "N archived" not in js and "' archived'" in js          # the header stat


# --- render smoke: an archived+suppressed card is absent, resurfaced present -- #
def test_render_smoke_archive_suppression_and_done_chip_count():
    rows = [_row(slug="visible-active", last_touch=NOW - timedelta(hours=1)),
            _row(slug="suppressed-done", last_touch=NOW - timedelta(days=4)),
            _row(slug="resurfaced-one", last_touch=NOW - timedelta(hours=2))]
    archived = [
        # suppressed: archived AFTER its last activity → hidden
        _archived_row("suppressed-done", NOW - timedelta(days=1), title="Suppressed"),
        # resurfaced: archived BEFORE its (newer) last activity → shows on the board
        _archived_row("resurfaced-one", NOW - timedelta(days=1), title="Resurfaced"),
    ]
    model = viewer.build_model(rows, now=NOW, archived=archived)
    html = viewer.render_html(model, None)
    assert html.startswith("<!doctype html>")                      # rendered, no exception
    # the JSON island (the board data) omits the suppressed card and keeps the resurfaced one
    island = html.split('id="idata" type="application/json">')[1].split("</script>")[0]
    payload = json.loads(island.replace("\\u003c", "<").replace("\\u003e", ">")
                         .replace("\\u0026", "&").replace("\\u2028", " ").replace("\\u2029", " "))
    board_slugs = {v["slug"] for v in payload["flat"]}
    assert "suppressed-done" not in board_slugs                    # suppressed absent from board
    assert "resurfaced-one" in board_slugs                         # resurfaced present on board
    assert "visible-active" in board_slugs
    assert payload["total"] == 2                                   # counts exclude the suppressed
    # The Done view is the FULL archived set (list_archived) — the resurfaced card keeps its
    # harmless stale row (never auto-deleted), so it appears in BOTH the board and Done; the
    # chip count is len(archived). Sorted newest archived_at first (slug tiebreak on equal ts).
    assert [a["slug"] for a in payload["archived"]] == ["resurfaced-one", "suppressed-done"]


# --------------------------------------------------------------------------- #
# Phase 3 — STATE-MATCHED card actions (dispatch.py state-aware body is covered
# in test_dispatch.py; here: the pure JS action MAP + the [resolve] ask helpers,
# node-eval'd so the ACTUAL page code runs — plus DOM-wiring markers + smoke).
# --------------------------------------------------------------------------- #
def _node_card_actions(views):
    """Eval `viewer._ARCHIVE_JS + viewer._ACTIONS_JS` (cardActions depends on dropEligible)
    against `views`; return, per view, the ordered list of `kind`/`label` action descriptors.
    Skips if node isn't on PATH — same pattern as the other node-eval predicates, so the ACTUAL
    page mapping is exercised (not a Python replica)."""
    node = _shutil.which("node")
    if not node:
        import pytest
        pytest.skip("node not on PATH — cardActions JS untested this run")
    body = (
        "var VIEWS = " + json.dumps(views) + ";\n"
        "console.log(JSON.stringify(VIEWS.map(function(v){ return cardActions(v); })));"
    )
    src = viewer._ARCHIVE_JS + "\n" + viewer._ACTIONS_JS + "\n" + body
    out = _subprocess.run([node, "-e", src], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def _kinds(actions):
    return [a["kind"] for a in actions]


_REC = {"text": "wire it", "basis": "handoff"}


def test_js_card_actions_needs_you_has_resolve_and_conditional_dispatch():
    # needs_you WITH a grounded rec → [resolve, dispatch, done]; WITHOUT → [resolve, done].
    with_rec, without = _node_card_actions([
        {"slug": "n1", "state": "needs_you", "recommended_next_step": _REC},
        {"slug": "n2", "state": "needs_you", "recommended_next_step": None},
    ])
    assert _kinds(with_rec) == ["resolve", "dispatch", "done"]
    assert _kinds(without) == ["resolve", "done"]
    # the needs_you dispatch keeps the plain '⤴ dispatch' label (some blocks are agent-fixable).
    assert with_rec[1]["label"] == "⤴ dispatch"


def test_js_card_actions_stalled_and_slowing_relabel_resume_plus_drop():
    # stalled/slowing → [resume?, drop, done]; the dispatch button is relabeled '⤴ resume'.
    stalled, slowing, no_rec = _node_card_actions([
        {"slug": "s", "state": "stalled", "recommended_next_step": _REC},
        {"slug": "w", "state": "slowing", "recommended_next_step": _REC},
        {"slug": "s2", "state": "stalled", "recommended_next_step": None},
    ])
    assert _kinds(stalled) == ["dispatch", "drop", "done"]
    assert _kinds(slowing) == ["dispatch", "drop", "done"]
    assert stalled[0]["label"] == "⤴ resume"
    assert slowing[0]["label"] == "⤴ resume"
    # no rec → the resume button is dropped, but [drop] + [done] remain.
    assert _kinds(no_rec) == ["drop", "done"]


def test_js_card_actions_active_is_dispatch_and_done_no_resolve_no_drop():
    active, no_rec, legacy = _node_card_actions([
        {"slug": "a", "state": "active", "recommended_next_step": _REC},
        {"slug": "a2", "state": "active", "recommended_next_step": None},
        {"slug": "u"},   # no state → treated as active (legacy/unknown)
    ])
    assert _kinds(active) == ["dispatch", "done"]
    assert active[0]["label"] == "⤴ dispatch"
    assert _kinds(no_rec) == ["done"]
    assert _kinds(legacy) == ["done"]
    # [resolve] is ONLY on needs_you; [drop] ONLY on stalled/slowing.
    for acts in (active, no_rec, legacy):
        assert "resolve" not in _kinds(acts)
        assert "drop" not in _kinds(acts)


def test_js_resolve_absent_on_non_needs_you_cards():
    rows = _node_card_actions([
        {"slug": "n", "state": "needs_you", "recommended_next_step": _REC},
        {"slug": "s", "state": "stalled", "recommended_next_step": _REC},
        {"slug": "w", "state": "slowing", "recommended_next_step": _REC},
        {"slug": "a", "state": "active", "recommended_next_step": _REC},
    ])
    has_resolve = ["resolve" in _kinds(acts) for acts in rows]
    assert has_resolve == [True, False, False, False]


def _node_resolve_question(slug):
    node = _shutil.which("node")
    if not node:
        import pytest
        pytest.skip("node not on PATH — resolveQuestion JS untested this run")
    body = ("console.log(JSON.stringify(resolveQuestion(" + json.dumps(slug) + ")));")
    out = _subprocess.run([node, "-e", viewer._ACTIONS_JS + "\n" + body],
                          capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_js_resolve_question_is_grounded_prefill_from_slug():
    assert _node_resolve_question("clawgate-agent-loop") == \
        "What's blocking clawgate-agent-loop and what should I do to resolve it?"
    # missing/empty slug degrades cleanly (no "undefined" in the string).
    assert _node_resolve_question("") == "What's blocking  and what should I do to resolve it?"


def test_js_ask_resolve_opens_sidebar_and_submits_the_prefilled_question():
    # askResolve reuses the EXISTING ask flow: it opens the sidebar (openChat), prefills the
    # chat input, and submits VIA submitQuestion — the same path the form submit uses. We stub
    # those closures in module scope and assert the composed question reaches submit.
    node = _shutil.which("node")
    if not node:
        import pytest
        pytest.skip("node not on PATH — askResolve JS untested this run")
    harness = (
        "var opened = false; var submitted = []; var chatInput = {};\n"
        "function openChat(){ opened = true; }\n"
        "function submitQuestion(q){ submitted.push(q); }\n"
        "askResolve({slug: 'clawgate-agent-loop'});\n"
        "console.log(JSON.stringify({opened: opened, submitted: submitted,"
        " inputValue: chatInput.value}));"
    )
    out = _subprocess.run([node, "-e", viewer._ACTIONS_JS + "\n" + harness],
                          capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    res = json.loads(out.stdout)
    q = "What's blocking clawgate-agent-loop and what should I do to resolve it?"
    assert res["opened"] is True                 # sidebar opened (reused open path)
    assert res["submitted"] == [q]               # submitted through the existing chat-submit path
    assert res["inputValue"] == q                # the input was prefilled with the same question


def test_js_actions_snippet_inlined_and_wired():
    js = viewer._JS
    assert "__ACTIONS_JS__" not in js                              # snippet inlined
    assert js.count("function cardActions") == 1
    assert js.count("function resolveQuestion") == 1
    assert js.count("function askResolve") == 1
    # the card action row is driven by the pure map + the [resolve] ask helper.
    assert "cardActions(v).forEach(" in js
    assert "el('button', 'resolve-btn', 'resolve')" in js          # the [resolve] button
    assert "askResolve(v)" in js                                   # wired to the ask flow
    assert "el('button', 'dispatch-btn', a.label)" in js           # dispatch/resume share wiring
    # askResolve reuses the EXISTING sidebar open + submit (no new endpoint).
    assert "openChat();" in viewer._ACTIONS_JS
    assert "submitQuestion(q);" in viewer._ACTIONS_JS
    # every action button stops propagation so a click never toggles the card expand.
    cbody = _card_body()
    assert cbody.count("ev.stopPropagation()") >= 4                # resolve + dispatch + done + drop


def test_css_resolve_button_styled():
    css = viewer._CSS
    assert ".resolve-btn{" in css
    assert ".resolve-btn:hover:not(:disabled){border-color:var(--yellow)}" in css


def test_js_resolve_no_server_route_added():
    # [resolve] is a CLIENT-SIDE convenience over the EXISTING ask flow — it must reuse
    # /api/ask/stream (via submitQuestion), NOT introduce a new endpoint.
    js = viewer._JS
    assert "/api/resolve" not in js
    assert "fetch('/api/ask/stream'" in js                         # the reused ask endpoint


# --- render smoke: one card per state → render_html has the state-matched wiring --- #
def test_render_smoke_one_card_per_state_has_resolve_resume_dispatch():
    rows = [
        _row(slug="needsyou-card", status="awaiting your review",       # needs_you
             last_touch=NOW - timedelta(hours=1)),
        _row(slug="stalled-card", momentum="stalled",                   # stalled
             last_touch=NOW - timedelta(days=9)),
        _row(slug="slowing-card", momentum="slowing",                   # slowing/cooling
             last_touch=NOW - timedelta(days=3)),
        _row(slug="active-card", last_touch=NOW - timedelta(hours=2)),  # active
    ]
    model = viewer.build_model(rows, now=NOW)
    # the states derived as expected (drives the client action map).
    by_slug = {v["slug"]: v for v in model["flat"]}
    assert by_slug["needsyou-card"]["state"] == "needs_you"
    assert by_slug["stalled-card"]["state"] == "stalled"
    assert by_slug["slowing-card"]["state"] == "slowing"
    assert by_slug["active-card"]["state"] == "active"

    html = viewer.render_html(model, None)
    assert html.startswith("<!doctype html>")                       # rendered, no exception
    # the state-matched action wiring ships on the page: the [resolve] button, the shared
    # dispatch/resume button, and the pure map that decides which appears per state.
    assert "el('button', 'resolve-btn', 'resolve')" in html
    assert "el('button', 'dispatch-btn', a.label)" in html
    assert "'⤴ resume'" in html and "'⤴ dispatch'" in html
    assert "function cardActions" in html


def test_regression_ask_sidebar_still_opens_and_submits_from_its_own_button():
    # The [resolve] reuse must NOT regress the sidebar's own toggle/submit wiring.
    js = viewer._JS
    assert "askToggle.addEventListener('click'" in js               # 💬 ask toggle intact
    assert "if(chat.hidden) openChat(); else closeChat();" in js
    assert "chatForm.addEventListener('submit'" in js               # the form submit intact
    assert "submitQuestion((chatInput.value || '').trim())" in js


# --------------------------------------------------------------------------- #
# SESSIONS-ONLY board: the pinned "Live now" strip (buildLiveNow union+dedup),
# retiring the collapsed catch-all + the ● Live triage chip. buildLiveNow is
# node-eval'd (the ACTUAL page builder, not a Python replica) like groupByRepo/matchQ.
# --------------------------------------------------------------------------- #
def _node_livenow(flat, unmatched, now_ms=None):
    node = _shutil.which("node")
    if not node:
        import pytest
        pytest.skip("node not on PATH — buildLiveNow JS untested this run")
    now_arg = "null" if now_ms is None else str(int(now_ms))
    body = (
        "var FLAT = " + json.dumps(flat) + ";\n"
        "var UM = " + json.dumps(unmatched) + ";\n"
        "console.log(JSON.stringify(buildLiveNow(FLAT, UM, " + now_arg + ")));"
    )
    out = _subprocess.run([node, "-e", viewer._LIVENOW_JS + "\n" + body],
                          capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_js_livenow_unions_every_live_pane_count_is_total():
    # EVERY live pane shows: matched cards' live_tasks + the unmatched (below-floor) panes.
    flat = [
        {"slug": "aa", "repo_name": "devrc", "live_tasks": ["task one", "task two"]},
        {"slug": "bb", "repo_name": "civitai", "live_tasks": ["task three"]},
        {"slug": "cc", "repo_name": "homelab", "live_tasks": []},   # a card with no live pane
    ]
    unmatched = [
        {"id": "Pool-6", "title": "below-floor work", "repo_name": "devrc"},
        {"id": "x-1", "title": "brand new thread", "repo_name": "homelab"},
    ]
    rows = _node_livenow(flat, unmatched)
    # 3 matched tasks + 2 unmatched panes = 5 rows == total live panes.
    assert len(rows) == 5
    tasks = sorted(r["task"] for r in rows)
    assert tasks == sorted(["task one", "task two", "task three",
                            "below-floor work", "brand new thread"])
    # matched rows carry their initiative slug; unmatched rows do not.
    by_task = {r["task"]: r for r in rows}
    assert by_task["task one"]["slug"] == "aa" and by_task["task one"]["matched"] is True
    assert by_task["task three"]["slug"] == "bb"
    assert by_task["below-floor work"]["slug"] == "" and by_task["below-floor work"]["matched"] is False
    assert by_task["below-floor work"]["id"] == "Pool-6"


def test_js_livenow_below_floor_unmatched_session_appears():
    # A brand-new / below-the-initiative-floor session (only in live_unmatched, no card) is
    # still shown in Live-now — the whole point of the pinned strip.
    rows = _node_livenow([], [{"id": "z-1", "title": "short new session", "repo_name": "devrc"}])
    assert len(rows) == 1
    assert rows[0]["task"] == "short new session"
    assert rows[0]["matched"] is False and rows[0]["slug"] == ""


def test_js_livenow_matched_rows_show_slug():
    rows = _node_livenow(
        [{"slug": "mycard", "repo_name": "devrc", "repo": "/home/zach/workspace/devrc",
          "live_tasks": ["do the thing"]}], [])
    # A pre-meta (strings-only) payload → activity_ts null, age "" (no freshness signal). The
    # matched row also carries the FULL repo path (so focusCard can rebuild the card's key()).
    assert rows == [{"task": "do the thing", "repo_name": "devrc",
                     "repo": "/home/zach/workspace/devrc", "slug": "mycard",
                     "matched": True, "id": "", "activity_ts": None, "age": ""}]


def test_js_livenow_matched_row_carries_full_repo_path_for_focus():
    # A matched row threads the card's FULL `repo` path (not the short repo_name) so focusCard can
    # rebuild key()=repo+'::'+slug. Meta + pre-meta payloads both carry it; unmatched rows carry ''.
    rows = _node_livenow(
        [{"slug": "mycard", "repo_name": "devrc", "repo": "/home/zach/workspace/devrc",
          "live_tasks_meta": [{"task": "meta task", "activity_ts": None}]}],
        [{"id": "z-1", "title": "orphan", "repo_name": "civitai", "repo": "/home/zach/workspace/civitai"}])
    by_task = {r["task"]: r for r in rows}
    assert by_task["meta task"]["repo"] == "/home/zach/workspace/devrc"
    assert by_task["meta task"]["slug"] == "mycard" and by_task["meta task"]["matched"] is True
    # unmatched carries its repo path too but no slug → focusCard won't treat it as clickable.
    assert by_task["orphan"]["repo"] == "/home/zach/workspace/civitai"
    assert by_task["orphan"]["slug"] == ""


def test_js_livenow_dedups_same_task_and_repo_matched_wins():
    # A task echoed on a card AND as unmatched (same task+repo) collapses to ONE row — the
    # matched, slug-tagged one (matched rows are pushed first). Repeated tasks on a card dedup too.
    flat = [{"slug": "aa", "repo_name": "devrc", "live_tasks": ["dup task", "dup task"]}]
    unmatched = [{"id": "P-1", "title": "dup task", "repo_name": "devrc"}]
    rows = _node_livenow(flat, unmatched)
    assert len(rows) == 1
    assert rows[0]["slug"] == "aa" and rows[0]["matched"] is True
    # same task text but a DIFFERENT repo is a distinct row (dedup is task+repo, not task alone).
    rows2 = _node_livenow(
        [{"slug": "aa", "repo_name": "devrc", "live_tasks": ["shared task"]}],
        [{"id": "P-1", "title": "shared task", "repo_name": "civitai"}])
    assert len(rows2) == 2


def test_js_livenow_sorted_by_repo_then_matched_then_task():
    flat = [{"slug": "z", "repo_name": "devrc", "live_tasks": ["zeta"]}]
    unmatched = [{"id": "a-1", "title": "alpha", "repo_name": "devrc"},
                 {"id": "c-1", "title": "gamma", "repo_name": "civitai"}]
    rows = _node_livenow(flat, unmatched)
    # civitai (repo) sorts before devrc; within devrc the matched row (zeta) precedes the
    # unmatched (alpha) despite alpha sorting first alphabetically.
    assert [(r["repo_name"], r["task"]) for r in rows] == [
        ("civitai", "gamma"), ("devrc", "zeta"), ("devrc", "alpha")]


def test_js_livenow_non_array_and_empty_inputs_are_safe():
    assert _node_livenow([], []) == []
    # a task with no text is not a row.
    assert _node_livenow([{"slug": "a", "repo_name": "devrc", "live_tasks": ["", "  "]}], []) == []


def test_js_livenow_null_inputs_return_empty():
    # buildLiveNow(null,null) / (true,3) → [] (a fake tmux hook returning junk can't crash it).
    node = _shutil.which("node")
    if not node:
        import pytest
        pytest.skip("node not on PATH — buildLiveNow JS untested this run")
    body = "console.log(JSON.stringify([buildLiveNow(null,null), buildLiveNow(true,3)]));"
    out = _subprocess.run([node, "-e", viewer._LIVENOW_JS + "\n" + body],
                          capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == [[], []]


# --- activity-sort + freshness age (buildLiveNow / liveAgeStr, node-eval'd) --- #
_NOW_SEC = 1_722_000_000
_NOW_MS = _NOW_SEC * 1000


def _node_live_age(pairs):
    """Eval liveAgeStr over [(ts, now_ms), …] → the list of age strings (the ACTUAL page fn)."""
    node = _shutil.which("node")
    if not node:
        import pytest
        pytest.skip("node not on PATH — liveAgeStr JS untested this run")
    body = ("var P = " + json.dumps(pairs) + ";\n"
            "console.log(JSON.stringify(P.map(function(p){ return liveAgeStr(p[0], p[1]); })));")
    out = _subprocess.run([node, "-e", viewer._LIVENOW_JS + "\n" + body],
                          capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_js_live_age_formatter_m_h_d():
    ages = _node_live_age([
        [_NOW_SEC - 10, _NOW_MS],            # <60s → now
        [_NOW_SEC - 4 * 60, _NOW_MS],        # 4m
        [_NOW_SEC - 2 * 3600, _NOW_MS],      # 2h
        [_NOW_SEC - 34 * 3600, _NOW_MS],     # 34h (hours don't roll to days at 24h)
        [_NOW_SEC - 5 * 86400, _NOW_MS],     # 5d
        [_NOW_SEC - 3 * 7 * 86400, _NOW_MS], # 3w
        [None, _NOW_MS],                     # no activity → ''
        [_NOW_SEC + 999, _NOW_MS],           # future (clock skew) → clamps to now
    ])
    assert ages == ["now", "4m", "2h", "34h", "5d", "3w", "", "now"]


def test_js_livenow_sorts_by_activity_desc_null_last_with_ages():
    # A 4m-old row precedes a 34h-old row; a null-activity row sorts LAST. Each row carries its age.
    flat = [
        {"slug": "stale", "repo_name": "devrc",
         "live_tasks_meta": [{"task": "idle 34h", "activity_ts": _NOW_SEC - 34 * 3600}]},
        {"slug": "hot", "repo_name": "devrc",
         "live_tasks_meta": [{"task": "fresh 4m", "activity_ts": _NOW_SEC - 4 * 60}]},
        {"slug": "mid", "repo_name": "civitai",
         "live_tasks_meta": [{"task": "warm 2h", "activity_ts": _NOW_SEC - 2 * 3600}]},
    ]
    unmatched = [{"id": "z-1", "title": "no activity", "repo_name": "homelab", "activity_ts": None}]
    rows = _node_livenow(flat, unmatched, now_ms=_NOW_MS)
    assert [r["task"] for r in rows] == ["fresh 4m", "warm 2h", "idle 34h", "no activity"]
    ages = {r["task"]: r["age"] for r in rows}
    assert ages == {"fresh 4m": "4m", "warm 2h": "2h", "idle 34h": "34h", "no activity": ""}


def test_js_livenow_null_activity_falls_to_repo_task_tiebreak():
    # With NO activity anywhere (all null) the sort degrades to the stable (repo, matched, task)
    # tiebreak — the pre-activity ordering, so a pre-meta payload still reads sensibly.
    flat = [{"slug": "z", "repo_name": "devrc", "live_tasks": ["zeta"]}]
    unmatched = [{"id": "a-1", "title": "alpha", "repo_name": "devrc"},
                 {"id": "c-1", "title": "gamma", "repo_name": "civitai"}]
    rows = _node_livenow(flat, unmatched, now_ms=_NOW_MS)
    assert [(r["repo_name"], r["task"]) for r in rows] == [
        ("civitai", "gamma"), ("devrc", "zeta"), ("devrc", "alpha")]


def test_js_livenow_dedup_matched_wins_with_activity():
    # dedup (task+repo) still keeps the matched, slug-tagged row (pushed first) even when the
    # unmatched twin carries its own activity_ts.
    flat = [{"slug": "aa", "repo_name": "devrc",
             "live_tasks_meta": [{"task": "dup", "activity_ts": _NOW_SEC - 60}]}]
    unmatched = [{"id": "P-1", "title": "dup", "repo_name": "devrc", "activity_ts": _NOW_SEC - 5}]
    rows = _node_livenow(flat, unmatched, now_ms=_NOW_MS)
    assert len(rows) == 1
    assert rows[0]["slug"] == "aa" and rows[0]["matched"] is True


# --- renderLiveNow: collapse to top-N + expand/collapse toggle (DOM-shim eval) - #
def _node_render_livenow(flat, unmatched, clicks=0):
    """Eval the ACTUAL renderLiveNow (+ its collapse helpers) under a tiny DOM/localStorage shim,
    optionally clicking the "＋N more" toggle `clicks` times, and return a per-render summary
    [{display, rowCount, count, more}] plus the persisted expand flag. Exercises the real page
    code (like _node_group), not a Python replica."""
    node = _shutil.which("node")
    if not node:
        import pytest
        pytest.skip("node not on PATH — renderLiveNow untested this run")
    js = viewer._JS
    slice_ = js[js.index("var LIVE_PREVIEW_N"):js.index("function renderTriage(")]
    shim = r"""
var _store = {};
var localStorage = {
  getItem: function(k){ return (k in _store) ? _store[k] : null; },
  setItem: function(k, v){ _store[k] = String(v); },
  removeItem: function(k){ delete _store[k]; }
};
function _mk(tag){
  var e = {tag: tag, className: '', textContent: '', type: '', style: {}, _kids: [], _h: {}};
  Object.defineProperty(e, 'innerHTML', {set: function(v){ if(v === '') e._kids = []; }, get: function(){ return ''; }});
  Object.defineProperty(e, 'children', {get: function(){ return e._kids; }});
  e.appendChild = function(c){ e._kids.push(c); return c; };
  e.addEventListener = function(ev, fn){ e._h[ev] = fn; };
  return e;
}
var _live = _mk('section');
var document = {
  createElement: function(t){ return _mk(t); },
  createTextNode: function(t){ return {text: String(t), _text: true, className: undefined}; },
  getElementById: function(id){ return id === 'livenow' ? _live : null; }
};
var LIVE_GLYPH = '●';
function el(tag, cls, txt){ var e = document.createElement(tag); if(cls) e.className = cls; if(txt != null) e.textContent = txt; return e; }
var liveNowEl = document.getElementById('livenow');
var data = {flat: [], live_unmatched: []};   // populated by the driver (FLAT/UM defined there)
function _summ(){
  var body = null, more = null, header = null;
  liveNowEl.children.forEach(function(k){
    if(k.className === 'livenow-body') body = k;
    else if(k.className === 'ln-more') more = k;
    else if(k.tag === 'h2') header = k;
  });
  var rowCount = 0;
  if(body){ body.children.forEach(function(c){ if(String(c.className || '').indexOf('ln-row') === 0) rowCount++; }); }
  var count = '';
  if(header){ header.children.forEach(function(c){ if(c.className === 'count') count = c.textContent; }); }
  return {display: liveNowEl.style.display, rowCount: rowCount, count: count, more: more ? more.textContent : null};
}
function _clickMore(){
  var kids = liveNowEl.children, i;
  for(i = 0; i < kids.length; i++){ if(kids[i].className === 'ln-more'){ kids[i]._h.click(); return true; } }
  return false;
}
"""
    driver = (
        "var FLAT = " + json.dumps(flat) + ";\n"
        "var UM = " + json.dumps(unmatched) + ";\n"
        "data.flat = FLAT; data.live_unmatched = UM;\n"
        "var OUT = [];\n"
        "renderLiveNow(); OUT.push(_summ());\n"
        "for(var i = 0; i < " + str(int(clicks)) + "; i++){ _clickMore(); OUT.push(_summ()); }\n"
        "console.log(JSON.stringify({renders: OUT, stored: _store['initiatives-livenow-expanded'] || null}));"
    )
    program = shim + "\n" + viewer._LIVENOW_JS + "\n" + slice_ + "\n" + driver
    out = _subprocess.run([node, "-e", program], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def _many_unmatched(n):
    # n live rows with DESCENDING activity so the sort order is deterministic (row i is the i-th
    # freshest); enough to exceed LIVE_PREVIEW_N (6).
    return [{"id": "s-" + str(i), "title": "task " + str(i), "repo_name": "devrc",
             "activity_ts": _NOW_SEC - i} for i in range(n)]


def test_render_livenow_collapses_to_top6_by_default():
    res = _node_render_livenow([], _many_unmatched(9))
    r0 = res["renders"][0]
    assert r0["display"] == "block"
    assert r0["rowCount"] == 6                       # only the top-6 render by default
    assert r0["count"] == "(9)"                      # header N = TOTAL live, not the shown slice
    assert r0["more"] == "＋3 more ▸"       # ＋3 more ▸  (9 − 6)
    assert res["stored"] is None                     # default COLLAPSED → nothing persisted


def test_render_livenow_expand_shows_all_then_collapses_and_persists():
    res = _node_render_livenow([], _many_unmatched(9), clicks=2)
    first, expanded, collapsed = res["renders"]
    assert first["rowCount"] == 6                     # collapsed
    assert expanded["rowCount"] == 9                  # click 1 → all rows
    assert expanded["more"] == "▾ show fewer"    # ▾ show fewer
    assert collapsed["rowCount"] == 6                 # click 2 → back to top-6
    assert collapsed["more"] == "＋3 more ▸"
    # the FINAL state is collapsed → the localStorage flag was cleared (removeItem), not left '1'.
    assert res["stored"] is None


def test_render_livenow_no_toggle_when_at_or_below_six():
    res = _node_render_livenow([], _many_unmatched(6))
    r0 = res["renders"][0]
    assert r0["rowCount"] == 6
    assert r0["more"] is None                         # exactly 6 → no "more" affordance


def test_render_livenow_hidden_when_empty():
    res = _node_render_livenow([], [])
    assert res["renders"][0]["display"] == "none"
    assert res["renders"][0]["rowCount"] == 0


def test_render_livenow_untrusted_text_is_textcontent_not_parsed():
    # A task carrying markup is stored as textContent verbatim (el() uses textContent, never
    # innerHTML) — the shim proves the string is not parsed into child nodes.
    res = _node_render_livenow(
        [], [{"id": "x-1", "title": "<script>alert(1)</script>", "repo_name": "devrc",
              "activity_ts": _NOW_SEC - 30}])
    assert res["renders"][0]["rowCount"] == 1         # the markup became ONE text row, not DOM


def test_render_livenow_row_has_age_and_meta_but_no_session_id():
    # Row structure: ln-task + ln-age (·) + ln-meta(ln-repo[+ln-slug]); the cryptic tmux codename
    # (ln-id) is DROPPED for unmatched rows. Pinned via a JS-source check on the render fn.
    js = viewer._JS
    r = js[js.index("function renderLiveNow("):js.index("function renderTriage(")]
    assert "el('span', 'ln-age', '· ' + r.age)" in r     # small muted "· <age>"
    assert "el('span', 'ln-meta')" in r                        # repo/slug grouped together
    assert "el('span', 'ln-repo', r.repo_name)" in r
    assert "el('span', 'ln-slug', r.slug)" in r
    assert "ln-id" not in r                                     # session codename dropped


def test_js_old_unmatched_catchall_is_removed():
    js = viewer._JS
    assert "renderUnmatched" not in js            # the collapsed catch-all fn is gone
    assert "matchUnmatched" not in js             # its search predicate is gone
    assert "Live sessions — not tied to an initiative" not in js
    assert "function renderLiveNow(" in js        # replaced by the pinned strip
    assert "function buildLiveNow(" in js


def test_render_smoke_livenow_matched_unmatched_and_no_doc_card():
    # A sessions-only board: a standalone session card + a doc-anchored `both` card that has a
    # LIVE pane, plus a below-floor unmatched live session. render must not raise; the pinned
    # #livenow strip is present ABOVE the board; the old catch-all + Live chip are gone; the
    # JSON island carries NO source=="doc" card (the store is sessions-only now).
    both = _row(slug="mail-automation", source="both",
                tmux_tasks=["Ship the mail extractor"])          # a live pane on a `both` card
    session = _row(slug="comfyui-pipeline", source="session", repo="/home/zach/workspace/civitai",
                   title="ComfyUI pipeline", tmux_tasks=[])       # a session-only card, no live pane
    model = viewer.build_model([both, session], now=NOW,
                               unmatched=[_um("Pool-9", "below-floor exploration")])
    html = viewer.render_html(model, None)
    assert html.startswith("<!doctype html>")                    # rendered, no exception
    assert 'id="livenow"' in html
    assert html.index('id="livenow"') < html.index('id="triage"')  # pinned above the triage bar
    assert "renderUnmatched" not in html                         # old catch-all gone
    assert "label:'Live'" not in html                            # ● Live triage chip gone
    # the sessions-only island carries session/both sources but NO pure doc card.
    assert '"source": "doc"' not in html and '"source":"doc"' not in html
    assert '"source": "both"' in html and '"source": "session"' in html
    # the live pane + the unmatched session both reach the client (island) for buildLiveNow.
    assert "Ship the mail extractor" in html                     # a matched live_task
    assert "below-floor exploration" in html                     # an unmatched live session


def test_render_smoke_livenow_mixed_activity_ts_serializes_cleanly():
    # A board with MIXED freshness — a fresh matched pane, a 34h-idle matched pane, and a
    # null-activity unmatched pane — must render without raising and carry the activity_ts on
    # BOTH the matched (live_tasks_meta) and the unmatched island rows so buildLiveNow can sort.
    now_sec = int(NOW.timestamp())
    fresh = _row(slug="clawgate", repo="/home/zach/workspace/devrc")
    fresh["tmux_tasks"] = ["fresh clawgate soak"]
    fresh["tmux_task_activity"] = {"fresh clawgate soak": now_sec - 240}          # 4m
    stale = _row(slug="grafana", repo="/home/zach/workspace/civitai", title="grafana")
    stale["tmux_tasks"] = ["idle grafana build"]
    stale["tmux_task_activity"] = {"idle grafana build": now_sec - 34 * 3600}     # 34h
    model = viewer.build_model([fresh, stale], now=NOW, unmatched=[
        _um("Pool-9", "brand new thread", activity_ts=None)])                     # null activity
    html = viewer.render_html(model, None)                                        # must not raise
    assert html.startswith("<!doctype html>")
    j = viewer.model_to_json(model, None)
    metas = {m["task"]: m["activity_ts"]
             for v in j["flat"] for m in v.get("live_tasks_meta", [])}
    assert metas["fresh clawgate soak"] == now_sec - 240
    assert metas["idle grafana build"] == now_sec - 34 * 3600
    assert j["live_unmatched"][0]["activity_ts"] is None


def test_build_model_is_source_agnostic_and_tolerant_of_legacy_doc_row():
    # The store is sessions-only, but the viewer must stay TOLERANT: a stray legacy source="doc"
    # row (or a row with no source key) still builds a card without crashing (no filtering here —
    # sessions-only is enforced upstream in the scan, not re-litigated in the pure render layer).
    rows = [_row(slug="legacy", source="doc"), _row(slug="nosrc")]
    rows[1].pop("source", None)
    model = viewer.build_model(rows, now=NOW)
    slugs = {v["slug"] for v in model["flat"]}
    assert slugs == {"legacy", "nosrc"}
    assert next(v for v in model["flat"] if v["slug"] == "nosrc")["source"] == ""


# --------------------------------------------------------------------------- #
# EVERY SESSION A CLICKABLE CARD: the Live-now strip's matched rows jump to +
# open their card via focusCard(repo, slug). Both the REAL focusCard and the REAL
# renderLiveNow row-wiring are node-eval'd under a richer DOM shim (querySelector/
# closest/classList/dataset) so the ACTUAL page code is exercised, not a replica.
# --------------------------------------------------------------------------- #
# A small DOM shim with parent-tracking + simple selector matching (.cls / .a.b / tag /
# [name="v"]) so closest()/querySelector(All)/classList/dataset behave enough to drive
# focusCard + the clickable row wiring. setTimeout STORES (never fires) so the ~1s `.flash`
# is still present when we inspect. Shared by the two harnesses below.
_DOM_SHIM = r"""
var _timeouts = [];
function setTimeout(fn, ms){ _timeouts.push(fn); return _timeouts.length; }
var _store = {};
var localStorage = {
  getItem: function(k){ return (k in _store) ? _store[k] : null; },
  setItem: function(k, v){ _store[k] = String(v); },
  removeItem: function(k){ delete _store[k]; }
};
function _classSet(node){ return String(node.className == null ? '' : node.className).split(/\s+/).filter(Boolean); }
function _matchSel(node, sel){
  sel = String(sel).trim();
  if(sel.charAt(0) === '['){
    var m = sel.match(/^\[([^=\]]+)(?:="?([^"\]]*)"?)?\]$/);
    if(!m) return false;
    return node.getAttribute && node.getAttribute(m[1]) === (m[2] == null ? '' : m[2]);
  }
  var parts = sel.split('.');
  var tag = parts[0], classes = [];
  for(var i = 1; i < parts.length; i++){ if(parts[i]) classes.push(parts[i]); }
  if(tag && String(node.tag).toLowerCase() !== tag.toLowerCase()) return false;
  var cs = _classSet(node);
  for(var j = 0; j < classes.length; j++){ if(cs.indexOf(classes[j]) < 0) return false; }
  return true;
}
function _mk(tag){
  var e = {tag: tag, className: '', textContent: '', title: '', type: '',
           style: {}, dataset: {}, _attrs: {}, _kids: [], _h: {}, parent: null, _scrolled: false};
  Object.defineProperty(e, 'innerHTML', {set: function(v){ if(v === '') e._kids = []; }, get: function(){ return ''; }});
  Object.defineProperty(e, 'children', {get: function(){ return e._kids; }});
  e.appendChild = function(c){ c.parent = e; e._kids.push(c); return c; };
  e.addEventListener = function(ev, fn){ e._h[ev] = fn; };
  e.setAttribute = function(k, v){ e._attrs[k] = String(v); if(k === 'data-repo') e.dataset.repo = String(v); if(k === 'data-key') e._attrs['data-key'] = String(v); };
  e.getAttribute = function(k){ return (k in e._attrs) ? e._attrs[k] : null; };
  e.classList = {
    add: function(c){ var s = _classSet(e); if(s.indexOf(c) < 0){ s.push(c); e.className = s.join(' '); } },
    remove: function(c){ e.className = _classSet(e).filter(function(x){ return x !== c; }).join(' '); },
    contains: function(c){ return _classSet(e).indexOf(c) >= 0; }
  };
  e.scrollIntoView = function(){ e._scrolled = true; };
  e.querySelectorAll = function(sel){ var out = []; (function walk(n){ (n._kids || []).forEach(function(k){ if(_matchSel(k, sel)) out.push(k); walk(k); }); })(e); return out; };
  e.querySelector = function(sel){ var r = e.querySelectorAll(sel); return r.length ? r[0] : null; };
  e.closest = function(sel){ var n = e; while(n){ if(_matchSel(n, sel)) return n; n = n.parent; } return null; };
  return e;
}
var _root = _mk('body');
var _live = _mk('section');
var document = {
  createElement: function(t){ return _mk(t); },
  createTextNode: function(t){ return {text: String(t), _text: true, className: undefined, tag: undefined, _kids: []}; },
  querySelectorAll: function(sel){ return _root.querySelectorAll(sel); },
  querySelector: function(sel){ return _root.querySelector(sel); },
  getElementById: function(id){ return id === 'livenow' ? _live : null; }
};
var LIVE_GLYPH = '●';
function el(tag, cls, txt){ var e = document.createElement(tag); if(cls) e.className = cls; if(txt != null) e.textContent = txt; return e; }
"""


def _js_slice(a, b):
    js = viewer._JS
    return js[js.index(a):js.index(b)]


def _node_focus_card(view, key, *, collapsed=True, target_key=None, in_flat=True):
    """Build a fake board (a collapsed grouped repo-section > .repo-body > .ini card > .detail),
    then eval the REAL key() + REAL focusCard() and call focusCard(view.repo, view.slug). Returns a
    summary of every side effect. `target_key` is the data-key stamped on the card (defaults to the
    same key so it matches); pass a different one to exercise the not-found path."""
    node = _shutil.which("node")
    if not node:
        import pytest
        pytest.skip("node not on PATH — focusCard untested this run")
    tkey = key if target_key is None else target_key
    src = (_js_slice("function key(v)", "function el(tag") +
           "\n" + _js_slice("function focusCard(", "function card("))
    flat = [view] if in_flat else []
    driver = (
        "var expanded = {}, detailCache = {};\n"
        "var data = {flat: " + json.dumps(flat) + "};\n"
        "var _loadCalls = [], _setCollapse = [];\n"
        "function loadDetail(v, det){ _loadCalls.push({slug: v && v.slug, repo: v && v.repo, isDetail: det === _det}); }\n"
        "function setRepoCollapsed(name, c){ _setCollapse.push([name, c]); }\n"
        "var _sec = _mk('section'); _sec.className = 'repo collapsible'; _root.appendChild(_sec);\n"
        "var _h2 = _mk('h2'); _h2.dataset.repo = " + json.dumps(view.get("repo_name") or "") + "; _sec.appendChild(_h2);\n"
        "var _chev = _mk('span'); _chev.className = 'chev'; _chev.textContent = " + ("'\\u25b8'" if collapsed else "'\\u25be'") + "; _h2.appendChild(_chev);\n"
        "var _body = _mk('div'); _body.className = 'repo-body'; _body.style.display = " + ("'none'" if collapsed else "'block'") + "; _sec.appendChild(_body);\n"
        "var _card = _mk('div'); _card.className = 'ini state-active'; _card.setAttribute('data-key', " + json.dumps(tkey) + "); _body.appendChild(_card);\n"
        "var _det = _mk('div'); _det.className = 'detail'; _det.style.display = 'none'; _card.appendChild(_det);\n"
        "var _ret = focusCard(" + json.dumps(view["repo"]) + ", " + json.dumps(view["slug"]) + ");\n"
        "console.log(JSON.stringify({ret: _ret, key: key(" + json.dumps({"repo": view["repo"], "slug": view["slug"]}) + "),"
        " bodyDisplay: _body.style.display, chev: _chev.textContent, expandedSet: !!expanded[" + json.dumps(key) + "],"
        " detailDisplay: _det.style.display, cardOpen: _card.classList.contains('open'),"
        " flash: _card.classList.contains('flash'), scrolled: _card._scrolled,"
        " loadCalls: _loadCalls, setCollapse: _setCollapse}));\n"
    )
    out = _subprocess.run([node, "-e", _DOM_SHIM + "\n" + src + "\n" + driver],
                          capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


_FVIEW = {"repo": "/home/zach/workspace/devrc", "repo_name": "devrc", "slug": "clawgate-release",
          "title": "Clawgate release", "summary": "cut a release"}
_FKEY = "/home/zach/workspace/devrc::clawgate-release"


def test_focus_card_expands_section_opens_detail_and_scrolls():
    r = _node_focus_card(_FVIEW, _FKEY, collapsed=True)
    assert r["ret"] is True
    assert r["key"] == _FKEY                       # focusCard reused the SAME key(repo::slug)
    assert r["bodyDisplay"] == "block"             # collapsed section was expanded first
    assert r["chev"] == "▾"                   # chevron flipped ▸ -> ▾
    assert r["setCollapse"] == [["devrc", False]]  # persisted the section as expanded
    assert r["expandedSet"] is True                # the card-expand path set expanded[key]
    assert r["detailDisplay"] == "block"           # .detail shown
    assert r["cardOpen"] is True                   # 'open' class added (same as a card click)
    assert r["scrolled"] is True                   # scrollIntoView called
    assert r["flash"] is True                      # brief highlight applied (setTimeout not fired)
    # loadDetail was called VERBATIM with the real view from data.flat + the card's own .detail.
    assert len(r["loadCalls"]) == 1
    assert r["loadCalls"][0]["slug"] == "clawgate-release"
    assert r["loadCalls"][0]["repo"] == "/home/zach/workspace/devrc"
    assert r["loadCalls"][0]["isDetail"] is True


def test_focus_card_already_expanded_section_still_opens_detail():
    # Section already open → focusCard doesn't touch the chevron/persist, but still opens the card.
    r = _node_focus_card(_FVIEW, _FKEY, collapsed=False)
    assert r["ret"] is True
    assert r["bodyDisplay"] == "block"
    assert r["setCollapse"] == []                  # not collapsed → no re-persist
    assert r["expandedSet"] is True and r["detailDisplay"] == "block"


def test_focus_card_no_matching_card_is_a_safe_noop():
    # A row whose card can't be found (e.g. the two untitled sessions with no card) → false, no throw.
    r = _node_focus_card(_FVIEW, _FKEY, target_key="/other/repo::nope")
    assert r["ret"] is False
    assert r["expandedSet"] is False and r["setCollapse"] == [] and r["scrolled"] is False


def _node_livenow_click(flat, unmatched):
    """Eval the REAL renderLiveNow (with a stubbed focusCard capturing its args) under the DOM
    shim, render, then report each row's clickability and fire the first clickable row's click +
    keydown handlers. Exercises the actual row-wiring, not a replica."""
    node = _shutil.which("node")
    if not node:
        import pytest
        pytest.skip("node not on PATH — renderLiveNow click-wiring untested this run")
    slice_ = _js_slice("var LIVE_PREVIEW_N", "function renderTriage(")
    driver = (
        "var _focus = [];\n"
        "function focusCard(repo, slug){ _focus.push([repo, slug]); }\n"
        "var liveNowEl = document.getElementById('livenow');\n"
        "var data = {flat: " + json.dumps(flat) + ", live_unmatched: " + json.dumps(unmatched) + "};\n"
        "renderLiveNow();\n"
        "var rows = liveNowEl.querySelectorAll('.ln-row');\n"
        "var summ = rows.map(function(row){\n"
        "  var task = ''; (row._kids || []).forEach(function(k){ if(k.className === 'ln-task') task = k.textContent; });\n"
        "  return {task: task, clickable: row.classList.contains('clickable'), role: row.getAttribute('role'),\n"
        "          tabindex: row.getAttribute('tabindex'), title: row.title,\n"
        "          hasClick: !!row._h.click, hasKeydown: !!row._h.keydown};\n"
        "});\n"
        "var clickable = null; for(var i = 0; i < rows.length; i++){ if(rows[i].classList.contains('clickable')){ clickable = rows[i]; break; } }\n"
        "if(clickable){\n"
        "  clickable._h.click();\n"
        "  var prevented = false;\n"
        "  clickable._h.keydown({key: 'Enter', preventDefault: function(){ prevented = true; }});\n"
        "  clickable._h.keydown({key: ' ', preventDefault: function(){}});\n"          # Space also activates
        "  clickable._h.keydown({key: 'a', preventDefault: function(){}});\n"          # other key: no-op
        "  var afterOther = _focus.length;\n"
        "  console.log(JSON.stringify({rows: summ, focus: _focus, prevented: prevented, afterOther: afterOther}));\n"
        "} else {\n"
        "  console.log(JSON.stringify({rows: summ, focus: _focus, prevented: null, afterOther: 0}));\n"
        "}\n"
    )
    out = _subprocess.run([node, "-e", _DOM_SHIM + "\n" + viewer._LIVENOW_JS + "\n" + slice_ + "\n" + driver],
                          capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_render_livenow_matched_row_is_clickable_and_wired_to_focus_card():
    flat = [{"slug": "clawgate-release", "repo": "/home/zach/workspace/devrc", "repo_name": "devrc",
             "live_tasks_meta": [{"task": "cut the release", "activity_ts": None}]}]
    res = _node_livenow_click(flat, [])
    assert len(res["rows"]) == 1
    row = res["rows"][0]
    assert row["clickable"] is True and row["role"] == "button" and row["tabindex"] == "0"
    assert row["hasClick"] is True and row["hasKeydown"] is True and row["title"]
    # click → focusCard(full repo path, slug); Enter + Space each activate too; 'a' does not.
    # Enter is preventDefault'd (so Space doesn't scroll the page).
    assert res["focus"][0] == ["/home/zach/workspace/devrc", "clawgate-release"]
    assert len(res["focus"]) == 3               # click + Enter + Space (NOT the 'a' key)
    assert res["afterOther"] == 3               # the non-activation key added nothing
    assert res["prevented"] is True
    assert all(f == ["/home/zach/workspace/devrc", "clawgate-release"] for f in res["focus"])


def test_render_livenow_unmatched_row_is_not_clickable():
    # An unmatched row (no slug → no card to open) is NOT clickable/focusable — no focusCard wiring.
    res = _node_livenow_click([], [{"id": "z-1", "title": "untitled session",
                                    "repo_name": "civitai", "repo": "/home/zach/workspace/civitai"}])
    assert len(res["rows"]) == 1
    row = res["rows"][0]
    assert row["clickable"] is False and row["role"] is None and row["tabindex"] is None
    assert row["hasClick"] is False and row["hasKeydown"] is False
    assert res["focus"] == []


def test_render_livenow_clickable_row_text_is_textcontent_only():
    # XSS: a matched row carrying markup keeps it as textContent (el() never innerHTML) even though
    # the row is now an interactive element.
    flat = [{"slug": "x", "repo": "/r", "repo_name": "r",
             "live_tasks_meta": [{"task": "<img src=x onerror=alert(1)>", "activity_ts": None}]}]
    res = _node_livenow_click(flat, [])
    assert res["rows"][0]["task"] == "<img src=x onerror=alert(1)>"   # verbatim string, not parsed
    assert res["rows"][0]["clickable"] is True


def test_focus_card_and_clickable_wiring_present_in_js_source():
    js = viewer._JS
    assert "function focusCard(repo, slug)" in js
    # focusCard reuses the shared key() and the card-expand path (loadDetail) VERBATIM.
    assert "key({repo: repo, slug: slug})" in js
    assert "loadDetail(v || {repo: repo, slug: slug}, det)" in js
    assert "expanded[k] = true" in js and ".detail" in js
    # renderLiveNow wires matched rows to focusCard(repo, slug) with role/tabindex + keyboard.
    r = _js_slice("function renderLiveNow(", "function renderTriage(")
    assert "focusCard(repo, slug)" in r
    assert "'role', 'button'" in r and "'tabindex', '0'" in r
    assert "keydown" in r and "clickable" in r


def test_render_smoke_matched_live_card_carries_click_target_in_island():
    # End-to-end render: a live matched card sits in the JSON island with its repo+slug (the
    # click target focusCard rebuilds key() from) and its live task; render_html doesn't raise and
    # ships the focusCard + clickable wiring. The card exists to jump to.
    card = _row(slug="clawgate-release", repo="/home/zach/workspace/devrc",
                tmux_tasks=["cut the clawgate release"])
    model = viewer.build_model([card], now=NOW)
    html = viewer.render_html(model, None)
    assert html.startswith("<!doctype html>")
    j = viewer.model_to_json(model, None)
    v = next(x for x in j["flat"] if x["slug"] == "clawgate-release")
    assert v["repo"] == "/home/zach/workspace/devrc"                 # the key() target lives on the card
    assert any(m["task"] == "cut the clawgate release" for m in v.get("live_tasks_meta", []))
    assert "function focusCard(repo, slug)" in html
    assert "'role', 'button'" in html                                # clickable row wiring shipped
