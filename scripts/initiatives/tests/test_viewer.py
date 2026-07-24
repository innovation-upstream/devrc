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


# --- Phase B: LLM recap as the primary "what this is" line ------------------- #
def test_view_carries_recap_and_defaults_empty():
    v = viewer.build_model([_row(slug="s", recap="A one-line plain recap.")],
                           now=NOW)["flat"][0]
    assert v["recap"] == "A one-line plain recap."
    # absent recap normalizes to "" (so the JS `v.recap || v.summary` falls back cleanly)
    v2 = viewer.build_model([_row(slug="s2")], now=NOW)["flat"][0]
    assert v2["recap"] == ""


def test_model_to_json_flat_includes_recap():
    j = viewer.model_to_json(
        viewer.build_model([_row(slug="a", recap="the recap")], now=NOW), None)
    assert j["flat"][0]["recap"] == "the recap"


def test_js_uses_recap_as_primary_line_with_summary_fallback():
    # The card FACE prefers the recap and falls back to the deterministic summary.
    assert "v.recap || v.summary" in viewer._JS


def test_js_stat_strip_is_removed():
    # The numeric stat strip (N commits · N merged · N sess · N ev) is gone from the card.
    assert "tag stat" not in viewer._JS
    assert " commits · " not in viewer._JS
    assert " merged · " not in viewer._JS
    assert " sess · " not in viewer._JS
    # …and its dead CSS rule is removed too.
    assert ".tag.stat" not in viewer._CSS


def test_render_html_embeds_recap_when_present():
    rows = [_row(slug="s", recap="A homelab-served recap of where this stands.",
                 summary="the deterministic summary")]
    html = viewer.render_html(viewer.build_model(rows, now=NOW))
    assert "A homelab-served recap of where this stands." in html   # recap in the payload
    # the JS selection (recap-over-summary) is present in the page
    assert "v.recap || v.summary" in html


def test_render_html_falls_back_to_summary_when_no_recap():
    rows = [_row(slug="s", summary="deterministic summary fallback line")]
    html = viewer.render_html(viewer.build_model(rows, now=NOW))
    assert "deterministic summary fallback line" in html   # summary still embedded
    # recap key present-but-empty in the payload (never breaks the fallback)
    j = viewer.model_to_json(viewer.build_model(rows, now=NOW), None)
    assert j["flat"][0]["recap"] == ""


def test_render_html_neutralizes_untrusted_recap_text():
    rows = [_row(slug="s", recap="<script>alert('recap')</script>")]
    html = viewer.render_html(viewer.build_model(rows, now=NOW))
    assert "<script>alert('recap')</script>" not in html   # never raw
    assert "u003cscript" in html                            # neutralized as <


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


def test_attach_recaps_joins_by_repo_slug():
    rows = [{"repo": "/r", "slug": "a"}, {"repo": "/r", "slug": "b"}]
    conn = _RecapConn(recap_rows=[
        {"repo": "/r", "slug": "a", "recap": "recap for a"},
        {"repo": "/r", "slug": "b", "recap": None},   # a row with no recap yet
    ])
    ok = viewer.attach_recaps(conn, rows)
    assert ok is True
    assert rows[0]["recap"] == "recap for a"
    assert rows[1]["recap"] is None   # NULL recap → stays None (card falls back to summary)


def test_attach_recaps_absent_table_leaves_recap_none():
    rows = [{"repo": "/r", "slug": "a"}]
    conn = _RecapConn(regclass=None)   # to_regclass → NULL (table not created yet)
    ok = viewer.attach_recaps(conn, rows)
    assert ok is False
    assert rows[0]["recap"] is None    # never blank/missing → fallback to summary works


def test_attach_recaps_db_error_rolls_back_and_is_fail_soft():
    rows = [{"repo": "/r", "slug": "a"}]
    conn = _RecapConn(raise_on="SELECT repo, slug, recap")
    ok = viewer.attach_recaps(conn, rows)
    assert ok is False
    assert conn.rollbacks == 1
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
def _um(id_, title="some work", repo="/home/zach/workspace/devrc"):
    """One `match_tmux_to_initiatives` unmatched pane (id/title/repo)."""
    return {"id": id_, "title": title, "repo": repo}


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
         "repo_name": "devrc"}]


def test_build_model_carries_live_unmatched():
    model = viewer.build_model([_row(slug="s")], now=NOW,
                               unmatched=[_um("Pool-6", "uncovered thread")])
    assert model["live_unmatched"] == [
        {"id": "Pool-6", "title": "uncovered thread",
         "repo": "/home/zach/workspace/devrc", "repo_name": "devrc"}]


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
         "repo_name": "devrc"}]
    # error branch always carries an empty list so the JS never sees undefined.
    assert viewer.model_to_json(None, "store down")["live_unmatched"] == []


def test_render_html_renders_live_unmatched_section_and_escapes():
    model = viewer.build_model([_row(slug="s")], now=NOW, unmatched=[
        _um("Pool-6", "<script>alert('u')</script>", repo="/home/zach/workspace/civitai")])
    html = viewer.render_html(model)
    # the section + its data ride the JSON island + JS (rendered client-side)
    assert "Live sessions — not tied to an initiative" in html
    assert "renderUnmatched" in html
    assert '"live_unmatched"' in html
    assert "Pool-6" in html                              # session id in the payload
    assert "<script>alert('u')</script>" not in html     # untrusted title never raw
    assert "u003cscript" in html                         # neutralized in the island


def test_render_html_no_section_when_live_unmatched_empty():
    # empty list → the JSON island carries [] and the JS early-returns (no section rows).
    model = viewer.build_model([_row(slug="s")], now=NOW, unmatched=[])
    j = viewer.model_to_json(model, None)
    assert j["live_unmatched"] == []
    html = viewer.render_html(model)
    assert '"live_unmatched": []' in html or '"live_unmatched":[]' in html
    # the renderer guards on length (no rows → no <section>)
    assert "if(!rows.length) return;" in viewer._JS


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


def test_js_card_renders_all_live_tasks():
    # the card iterates the full live_tasks list (one line per session), not a single line.
    assert "v.live_tasks" in viewer._JS
    assert "ltasks.forEach" in viewer._JS


def test_build_detail_carries_live_tasks():
    rows = [_row(slug="s")]
    rows[0]["tmux_tasks"] = ["task one", "task two"]
    model = viewer.build_model(rows, now=NOW)
    d = viewer.build_detail(model, None, "/home/zach/workspace/devrc", "s",
                            doc_reader=lambda repo, doc: None)
    assert d["live_tasks"] == ["task one", "task two"]


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
         "repo": "/home/zach/workspace/devrc", "repo_name": "devrc"}]


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


def test_js_recency_is_default_and_storage_key_bumped():
    js = viewer._JS
    # The resolved default view is now 'recency' (was 'flat'); an unknown/legacy stored value
    # falls back via the VALID_VIEWS allowlist to recency.
    assert "VALID_VIEWS" in js and "VALID_VIEWS[storedView] ? storedView : 'recency'" in js
    assert "VALID_VIEWS[storedView] ? storedView : 'flat'" not in js
    # The storage key was bumped to a v2 name: a browser that persisted the OLD default ('flat')
    # under the v1 key reads NOTHING under the v2 key (localStorage is per-key), so storedView is
    # null and it falls to the recency default rather than being pinned to the stale 'flat'.
    assert "VIEW_KEY = 'initiatives-view-v2'" in js
    assert "VIEW_KEY = 'initiatives-view'" not in js   # the v1 key is fully gone (no stale read)
    # the choice is still persisted, and flat/grouped stay selectable + sticky.
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
