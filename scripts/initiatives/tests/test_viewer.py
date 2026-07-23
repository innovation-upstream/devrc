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


# --- HTML render ------------------------------------------------------------ #
def test_render_html_contains_slug_badge_tmux_and_footer():
    rows = [_row(slug="initiatives-viewer")]
    rows[0]["tmux_sessions"] = {"Vapor-2"}
    model = viewer.build_model(rows, now=NOW)
    html = viewer.render_html(model)
    assert "initiatives-viewer" in html            # a slug
    assert "●" in html and "active" in html         # a momentum badge glyph + label
    assert "[tmux:Vapor-2]" in html                 # a tmux tag
    assert "wire the systemd unit" in html          # the next-step
    assert "#138" in html                           # the open PR
    assert "hourly sync" in html                    # the footer
    assert "2026-07-22 11:54 UTC" in html           # captured_at in the footer
    assert 'http-equiv="refresh"' in html           # auto-refresh wired
    assert html.startswith("<!doctype html>")


def test_render_html_escapes_untrusted_text():
    model = viewer.build_model([_row(title="<script>alert(1)</script>")], now=NOW)
    html = viewer.render_html(model)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_render_html_error_page_when_store_unreachable():
    html = viewer.render_html(None, error="OperationalError: connection refused")
    assert "store unreachable" in html
    assert "connection refused" in html
    assert html.startswith("<!doctype html>")


def test_render_html_empty_model_shows_empty_message():
    model = viewer.build_model([], now=NOW)
    html = viewer.render_html(model)
    assert "No initiatives" in html


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
    # must swallow it, return False, and leave the rows untouched (no crash, overlay absent).
    def boom():
        raise RuntimeError("scan import failed")

    monkeypatch.setattr(viewer, "_scan", boom)
    rows = [_row(slug="ok")]
    assert viewer.attach_tmux(rows) is False
    assert "tmux_sessions" not in rows[0]  # untouched


def test_attach_tmux_absent_when_no_tmux_server(monkeypatch):
    # No panes = no tmux server on this host → overlay absent (False), not an error.
    class _FakeScan:
        collect_tmux_panes = staticmethod(lambda: [])

    monkeypatch.setattr(viewer, "_scan", lambda: _FakeScan)
    assert viewer.attach_tmux([_row(slug="ok")]) is False
