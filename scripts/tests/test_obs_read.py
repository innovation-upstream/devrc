"""Unit tests for scripts/obs-read — the cluster-aware observability query tool.

Fully HERMETIC: no kubectl, no port-forward, no live cluster, no HTTP. The
transport (PortForward + http_get) is injected, and the PURE parse/guard/render
functions are exercised directly against fixture payloads. Mirrors the injection
style of test_bar_status.py / test_disk_detail.py.

Highest-value coverage = the SILENT-ZERO GUARD: an empty vector/matrix/stream
MUST trip `matched_nothing`, while a matched series whose value is actually 0
must NOT. Also covers cluster->kubeconfig mapping (incl. missing handle -> clear
error), preset resolution, URL building, table/JSON shape, and port-forward
cleanup-on-error.

    run:  pytest scripts/tests/test_obs_read.py
"""
import importlib.machinery
import importlib.util
import io
import json
import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]


def _load(name, modname):
    loader = importlib.machinery.SourceFileLoader(modname, str(SCRIPTS / name))
    spec = importlib.util.spec_from_loader(modname, loader)
    mod = importlib.util.module_from_spec(spec)
    # Register BEFORE exec: @dataclass on py3.14 resolves annotations via
    # sys.modules.get(cls.__module__), which is None for an unregistered module.
    sys.modules[modname] = mod
    loader.exec_module(mod)
    return mod


obs = _load("obs-read", "obs_read")


# --------------------------------------------------------------------------- #
# Fixtures — realistic backend payloads
# --------------------------------------------------------------------------- #
def prom_vector(pairs):
    """pairs = [(labels_dict, value_str), ...] -> a Prometheus vector payload."""
    return {"status": "success", "data": {"resultType": "vector", "result": [
        {"metric": m, "value": [1700000000, v]} for m, v in pairs]}}


def prom_empty():
    return {"status": "success",
            "data": {"resultType": "vector", "result": []}}


def prom_matrix(series):
    """series = [(labels, [(ts,val),...]), ...] -> a Prometheus matrix payload."""
    return {"status": "success", "data": {"resultType": "matrix", "result": [
        {"metric": m, "values": [[ts, v] for ts, v in vals]} for m, vals in series]}}


def loki_streams(streams):
    """streams = [(labels, [(ts,line),...]), ...] -> a Loki streams payload."""
    return {"status": "success", "data": {"resultType": "streams", "result": [
        {"stream": s, "values": [[ts, line] for ts, line in vals]}
        for s, vals in streams]}}


def loki_empty():
    return {"status": "success",
            "data": {"resultType": "streams", "result": []}}


def loki_matrix(series):
    return {"status": "success", "data": {"resultType": "matrix", "result": [
        {"metric": m, "values": [[ts, v] for ts, v in vals]} for m, vals in series]}}


def pyro_profile(names, levels, num_ticks):
    return {"flamebearer": {"names": names, "levels": levels,
                            "numTicks": num_ticks, "maxSelf": 0}}


# =========================================================================== #
# SILENT-ZERO GUARD — the highest-value tests
# =========================================================================== #
def test_prometheus_empty_vector_trips_guard():
    qr = obs.parse_prometheus(prom_empty())
    assert qr.matched_nothing is True
    assert qr.rows == []


def test_prometheus_value_actually_zero_does_NOT_trip_guard():
    # a REAL series whose value is 0 — must be treated as a genuine zero
    qr = obs.parse_prometheus(prom_vector([({"code": "5xx"}, "0")]))
    assert qr.matched_nothing is False
    assert qr.rows[0]["value"] == 0
    assert "REAL zero" in qr.detail


def test_prometheus_nonzero_value_no_zero_note():
    qr = obs.parse_prometheus(prom_vector([({"code": "200"}, "12.5")]))
    assert qr.matched_nothing is False
    assert qr.rows[0]["value"] == 12.5
    assert qr.detail == ""


def test_prometheus_empty_matrix_trips_guard():
    qr = obs.parse_prometheus(
        {"status": "success", "data": {"resultType": "matrix", "result": []}})
    assert qr.matched_nothing is True


def test_prometheus_matrix_with_values_ok():
    qr = obs.parse_prometheus(
        prom_matrix([({"pod": "api-0"}, [(1, "1"), (2, "3")])]))
    assert qr.matched_nothing is False
    assert qr.rows[0]["value"] == 3       # last point
    assert qr.rows[0]["points"] == 2


def test_loki_empty_streams_trips_guard():
    qr = obs.parse_loki(loki_empty())
    assert qr.matched_nothing is True


def test_loki_streams_present_zero_lines_trips_guard():
    # a stream object but with no log lines -> still "matched nothing"
    qr = obs.parse_loki(loki_streams([({"app": "x"}, [])]))
    assert qr.matched_nothing is True


def test_loki_streams_with_lines_ok():
    qr = obs.parse_loki(loki_streams(
        [({"namespace": "civitai-dp-prod"}, [(1, '{"code":"NOT_FOUND"}')])]))
    assert qr.matched_nothing is False
    assert qr.rows[0]["lines"] == 1
    assert qr.extra["total_lines"] == 1


def test_loki_empty_matrix_trips_guard():
    qr = obs.parse_loki(
        {"status": "success", "data": {"resultType": "matrix", "result": []}})
    assert qr.matched_nothing is True


def test_loki_matrix_value_zero_is_real_zero():
    qr = obs.parse_loki(loki_matrix([({"code": "500"}, [(1, "0")])]))
    assert qr.matched_nothing is False
    assert "REAL zero" in qr.detail


def test_pyroscope_empty_profile_trips_guard():
    qr = obs.parse_pyroscope(pyro_profile(["total"], [[0, 0, 0, 0]], 0))
    assert qr.matched_nothing is True


def test_pyroscope_zero_ticks_trips_guard_even_with_names():
    qr = obs.parse_pyroscope(pyro_profile(["total", "foo"], [], 0))
    assert qr.matched_nothing is True


def test_pyroscope_with_samples_ranks_frames():
    # names[0]=total (root), foo self=30, bar self=70
    payload = pyro_profile(
        ["total", "foo", "bar"],
        [[0, 100, 0, 0], [0, 30, 30, 1], [30, 70, 70, 2]],
        100)
    qr = obs.parse_pyroscope(payload)
    assert qr.matched_nothing is False
    assert qr.rows[0]["function"] == "bar"     # highest self first
    assert qr.rows[0]["self_pct"] == 70.0
    assert all(r["function"] != "total" for r in qr.rows)


# =========================================================================== #
# Cluster -> kubeconfig mapping
# =========================================================================== #
def test_resolve_kubeconfig_maps_each_cluster(tmp_path):
    kc = tmp_path / "kubeconfig"
    kc.write_text("x")
    env = {"KC_HOMELAB": str(kc), "KC_WORKBENCH": str(kc),
           "KC_DPPROD": str(kc), "KC_NEBULA": str(kc)}
    for cluster in ("homelab", "workbench", "dpprod", "nebula"):
        assert obs.resolve_kubeconfig(cluster, env=env) == str(kc)


def test_resolve_kubeconfig_missing_handle_is_clear_error():
    # KC_NEBULA unset/empty -> must refuse, NOT silently pick another cluster
    env = {"KC_HOMELAB": "/some/path"}
    with pytest.raises(ValueError) as ei:
        obs.resolve_kubeconfig("nebula", env=env, check_exists=False)
    msg = str(ei.value)
    assert "KC_NEBULA" in msg and "guess" in msg.lower()


def test_resolve_kubeconfig_unknown_cluster_errors():
    with pytest.raises(ValueError):
        obs.resolve_kubeconfig("prod", env={}, check_exists=False)


def test_resolve_kubeconfig_nonexistent_path_errors(tmp_path):
    env = {"KC_HOMELAB": str(tmp_path / "nope")}
    with pytest.raises(ValueError) as ei:
        obs.resolve_kubeconfig("homelab", env=env, check_exists=True)
    assert "not found" in str(ei.value)


# =========================================================================== #
# Preset resolution
# =========================================================================== #
class Args:
    def __init__(self, **kw):
        self.preset = kw.get("preset")
        self.query = kw.get("query")
        self.backend = kw.get("backend")
        self.kind = kw.get("kind")


def test_preset_resolves_backend_and_query():
    b, q, k = obs.resolve_query(Args(preset="dp-5xx-rate"))
    assert b == "prometheus"
    assert "traefik_service_requests_total" in q
    assert k == "instant"


def test_unknown_preset_errors():
    with pytest.raises(ValueError):
        obs.resolve_query(Args(preset="does-not-exist"))


def test_raw_query_requires_backend():
    with pytest.raises(ValueError):
        obs.resolve_query(Args(query="up"))


def test_raw_query_with_backend_ok():
    b, q, k = obs.resolve_query(Args(query="up", backend="prometheus"))
    assert (b, q, k) == ("prometheus", "up", "instant")


def test_every_preset_has_valid_backend_and_source():
    for p in obs.PRESETS:
        assert p.backend in obs.BACKENDS
        assert p.source            # honesty: every preset names a source
        assert p.kind in ("instant", "range", "profile")


def test_validated_presets_reference_a_file_source():
    for p in obs.PRESETS:
        if p.validated:
            assert ":" in p.source  # a file:line reference


def test_traefik_500_preset_groups_by_path_matching_source():
    # regression: the source (investigate-dp-errors:271) groups `by (path)` to
    # find WHICH endpoint 500s; a `by (code)` variant collapses to ~1 bucket and
    # is less diagnostic. Keep it verbatim-to-source while tagged validated.
    p = obs.PRESETS_BY_NAME["dp-traefik-500-by-path"]
    assert "sum by (path)" in p.query
    assert "by (code)" not in p.query
    assert p.validated is True


# =========================================================================== #
# URL building (pure)
# =========================================================================== #
def test_build_url_prometheus_instant():
    url = obs.build_url("prometheus", 9090, "up", "instant", 1800, now=1000)
    assert url.startswith("http://127.0.0.1:9090/api/v1/query?")
    assert "query=up" in url
    assert "query_range" not in url


def test_build_url_prometheus_range_has_window():
    url = obs.build_url("prometheus", 9090, "up", "range", 1800, now=1000)
    assert "/api/v1/query_range?" in url
    assert "start=" in url and "end=1000" in url


def test_build_url_loki_uses_ns_timestamps():
    url = obs.build_url("loki", 3100, '{app="x"}', "range", 60, now=1000)
    assert "/loki/api/v1/query_range?" in url
    # end = now * 1e9
    assert "end=1000000000000" in url
    assert "start=940000000000" in url


def test_build_url_pyroscope_render():
    url = obs.build_url("pyroscope", 4040, '{service_name="x"}', "profile",
                        1800, now=1000)
    assert "/pyroscope/render?" in url
    assert "from=now-1800s" in url and "until=now" in url


# =========================================================================== #
# Duration parsing
# =========================================================================== #
def test_parse_duration_units():
    assert obs.parse_duration("30m") == 1800
    assert obs.parse_duration("2h") == 7200
    assert obs.parse_duration("1d") == 86400
    assert obs.parse_duration("90s") == 90
    assert obs.parse_duration("45") == 45


def test_parse_duration_bad_raises():
    with pytest.raises(ValueError):
        obs.parse_duration("banana")


# =========================================================================== #
# Rendering — table + JSON + the loud warning
# =========================================================================== #
def test_render_table_prometheus_vector():
    qr = obs.parse_prometheus(prom_vector(
        [({"code": "200"}, "10"), ({"code": "500"}, "2")]))
    out, err = obs.render(qr, False, "q", "dpprod", "prometheus")
    assert err == ""
    assert "METRIC" in out and "VALUE" in out
    assert "code=200" in out and "code=500" in out


def test_render_empty_emits_loud_warning_to_stderr():
    qr = obs.parse_prometheus(prom_empty())
    out, err = obs.render(qr, False, "q", "dpprod", "prometheus")
    assert "MATCHED NOTHING" in err
    assert "NOT a" in err            # "NOT a confirmed zero"
    # stdout must NOT render a clean 0/table
    assert "0" not in out or "no series" in out


def test_render_json_shape_and_warning_flag():
    qr = obs.parse_prometheus(prom_empty())
    out, err = obs.render(qr, True, "q", "dpprod", "prometheus")
    doc = json.loads(out)
    assert doc["matched_nothing"] is True
    assert "warning" in doc
    assert doc["cluster"] == "dpprod" and doc["backend"] == "prometheus"


def test_render_json_real_zero_no_warning():
    qr = obs.parse_prometheus(prom_vector([({"code": "5xx"}, "0")]))
    out, _ = obs.render(qr, True, "q", "dpprod", "prometheus")
    doc = json.loads(out)
    assert doc["matched_nothing"] is False
    assert "warning" not in doc
    assert doc["row_count"] == 1


# =========================================================================== #
# Port-forward lifecycle — cleanup on success AND on error
# =========================================================================== #
class FakeProc:
    def __init__(self, alive=True, pid=None, stderr_text=None):
        self._alive = alive
        self.terminated = False
        self.waited = False
        self.pid = pid                      # None -> _kill_process_group no-ops
        self.stderr = (io.StringIO(stderr_text) if stderr_text is not None
                       else None)

    def poll(self):
        return None if self._alive else 0

    def terminate(self):
        self.terminated = True
        self._alive = False

    def wait(self, timeout=None):
        self.waited = True
        return 0


def test_port_forward_terminates_on_success():
    proc = FakeProc()
    pf = obs.PortForward(
        "/kc", obs.BACKENDS["prometheus"],
        popen=lambda *a, **k: proc,
        wait_ready=lambda *a, **k: None)   # ready immediately
    with pf as port:
        assert isinstance(port, int) and port > 0
        assert proc.terminated is False    # still up inside the block
    assert proc.terminated is True         # torn down on exit
    assert proc.waited is True


def test_port_forward_terminates_on_wait_ready_error():
    # THE cleanup-on-error case: readiness fails -> forward must be killed
    proc = FakeProc()

    def boom(*a, **k):
        raise TimeoutError("never became ready")

    pf = obs.PortForward("/kc", obs.BACKENDS["loki"],
                         popen=lambda *a, **k: proc, wait_ready=boom)
    with pytest.raises(TimeoutError):
        pf.__enter__()
    assert proc.terminated is True         # cleaned up despite the error


def test_query_backend_injected_transport_no_cluster():
    # end-to-end through query_backend with a fake port-forward + http_get:
    # no kubectl, no network.
    captured = {}

    class FakePF:
        def __init__(self, kubeconfig, backend, **kw):
            captured["ns"] = backend.namespace
            captured["svc"] = backend.service

        def __enter__(self):
            return 12345

        def __exit__(self, *exc):
            return False

    def fake_http(url, timeout=15.0):
        captured["url"] = url
        return prom_vector([({"code": "200"}, "5")])

    payload = obs.query_backend("/kc", "prometheus", "up", "instant", 60,
                                pf_factory=FakePF, http_get=fake_http)
    assert captured["svc"] == "kube-prometheus-stack-prometheus"
    assert "127.0.0.1:12345" in captured["url"]
    qr = obs.parse_prometheus(payload)
    assert qr.rows[0]["value"] == 5


# =========================================================================== #
# KUBECONFIG override — the #1-priority "can't hit ambient/wrong cluster"
# invariant (locks the env= actually handed to the kubectl child)
# =========================================================================== #
def test_port_forward_forces_named_kubeconfig_and_overrides_ambient(monkeypatch):
    # a hostile ambient KUBECONFIG that must NOT leak through
    monkeypatch.setenv("KUBECONFIG", "/ambient/WRONG/cluster")
    captured = {}

    def fake_popen(argv, **kw):
        captured["argv"] = argv
        captured["env"] = kw.get("env")
        captured["start_new_session"] = kw.get("start_new_session")
        captured["stderr"] = kw.get("stderr")
        return FakeProc()

    pf = obs.PortForward("/named/homelab-kubeconfig", obs.BACKENDS["prometheus"],
                         popen=fake_popen, wait_ready=lambda *a, **k: None)
    with pf:
        pass
    # (a) the named cluster's kubeconfig is forced onto the child
    assert captured["env"]["KUBECONFIG"] == "/named/homelab-kubeconfig"
    # (b) the ambient KUBECONFIG is OVERRIDDEN, not inherited
    assert captured["env"]["KUBECONFIG"] != "/ambient/WRONG/cluster"
    # (c) child is in its own session so a signal can killpg the group
    assert captured["start_new_session"] is True
    # kubectl is actually the argv, forwarding the right svc
    assert captured["argv"][0] == "kubectl"
    assert "svc/kube-prometheus-stack-prometheus" in captured["argv"]


# =========================================================================== #
# Signal-safe teardown — SIGTERM must run __exit__ / killpg (no leaked tunnel)
# =========================================================================== #
def test_sigterm_raises_systemexit_and_restores_handler():
    prev = signal.getsignal(signal.SIGTERM)
    with pytest.raises(SystemExit):
        with obs._sigterm_raises():
            # inside the block SIGTERM is converted to SystemExit (so enclosing
            # context managers unwind) instead of the default silent kill
            os.kill(os.getpid(), signal.SIGTERM)
    # handler restored to whatever it was before the block
    assert signal.getsignal(signal.SIGTERM) == prev


def test_port_forward_torn_down_on_sigterm():
    # THE headline claim: a SIGTERM mid-query must tear the forward down.
    proc = FakeProc()
    pf = obs.PortForward("/kc", obs.BACKENDS["prometheus"],
                         popen=lambda *a, **k: proc,
                         wait_ready=lambda *a, **k: None)
    with pytest.raises(SystemExit):
        with obs._sigterm_raises():
            with pf as port:
                assert port > 0
                os.kill(os.getpid(), signal.SIGTERM)
    assert proc.terminated is True             # __exit__ ran despite SIGTERM


def test_terminate_kills_process_group(monkeypatch):
    # with a real pid, _terminate must attempt a process-GROUP kill (reaps the
    # kubectl child + any grandchild), not just proc.terminate().
    killed = {}
    monkeypatch.setattr(obs, "_kill_process_group",
                        lambda proc: killed.setdefault("pid", proc.pid))
    proc = FakeProc(pid=4242)
    pf = obs.PortForward("/kc", obs.BACKENDS["prometheus"],
                         popen=lambda *a, **k: proc,
                         wait_ready=lambda *a, **k: None)
    with pf:
        pass
    assert killed.get("pid") == 4242
    assert proc.terminated is True             # belt-and-braces child kill too


def test_kill_process_group_noop_without_pid():
    # a fake proc with pid=None must NOT blow up (no os.killpg on a None pid)
    obs._kill_process_group(FakeProc(pid=None))   # should simply return


# =========================================================================== #
# kubectl stderr surfaced on early exit (#4)
# =========================================================================== #
def test_wait_ready_surfaces_kubectl_stderr():
    err = 'Error from server (NotFound): services "pyroscope" not found'
    proc = FakeProc(alive=False, stderr_text=err + "\n")
    with pytest.raises(RuntimeError) as ei:
        obs._wait_ready(12345, "/ready", 1.0, proc=proc)
    assert "NotFound" in str(ei.value)
    assert "exited early" in str(ei.value)


def test_wait_ready_early_exit_without_stderr_still_raises():
    proc = FakeProc(alive=False)               # no stderr stream
    with pytest.raises(RuntimeError) as ei:
        obs._wait_ready(12345, "/ready", 1.0, proc=proc)
    assert "exited early" in str(ei.value)


# =========================================================================== #
# HTTP error body surfaced (#6) — malformed PromQL -> 400 + JSON error
# =========================================================================== #
def test_http_get_surfaces_error_body(monkeypatch):
    import urllib.error

    def boom(req, timeout=None):
        raise urllib.error.HTTPError(
            req.full_url, 400, "Bad Request", {},
            io.BytesIO(b'{"status":"error","error":"unexpected end of query"}'))

    monkeypatch.setattr(obs.urllib.request, "urlopen", boom)
    with pytest.raises(RuntimeError) as ei:
        obs._http_get("http://127.0.0.1:9090/api/v1/query?query=sum(")
    msg = str(ei.value)
    assert "400" in msg
    assert "unexpected end of query" in msg     # the BODY, not a bare code


# =========================================================================== #
# Expected-absence preset (#5) — empty renders calm OK, not the ⚠ banner
# =========================================================================== #
def test_absence_ok_empty_renders_calm_ok_not_warning():
    qr = obs.parse_prometheus(prom_empty())
    out, err = obs.render(qr, False, "q", "homelab", "prometheus",
                          absence_ok=True)
    assert err == ""                            # NO scary stderr banner
    assert "OK" in out and "MATCHED NOTHING" not in out


def test_absence_ok_json_marks_ok_absent_not_warning():
    qr = obs.parse_prometheus(prom_empty())
    out, _ = obs.render(qr, True, "q", "homelab", "prometheus", absence_ok=True)
    doc = json.loads(out)
    assert doc["matched_nothing"] is True
    assert doc.get("status") == "ok-absent"
    assert "warning" not in doc


def test_absence_ok_off_still_warns():
    qr = obs.parse_prometheus(prom_empty())
    _, err = obs.render(qr, False, "q", "homelab", "prometheus",
                        absence_ok=False)
    assert "MATCHED NOTHING" in err


def test_homelab_alerts_preset_is_absence_ok():
    p = obs.PRESETS_BY_NAME["homelab-alerts-firing"]
    assert p.absence_ok is True


# =========================================================================== #
# main() end-to-end with injected transport (no cluster, no network)
# =========================================================================== #
def _fake_pf(payload_holder):
    class FakePF:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return 5555

        def __exit__(self, *exc):
            return False
    return FakePF


def test_main_requires_cluster(capsys):
    rc = obs.main(["--preset", "dp-5xx-rate"], check_exists=False)
    assert rc == 2
    err = capsys.readouterr().err
    assert "--cluster is REQUIRED" in err


def test_main_missing_handle_exits_clean(capsys):
    rc = obs.main(["--cluster", "nebula", "--preset", "dp-5xx-rate"],
                  env={}, check_exists=False)
    assert rc == 2
    assert "KC_NEBULA" in capsys.readouterr().err


def test_main_happy_path_table(capsys, tmp_path):
    kc = tmp_path / "kc"
    kc.write_text("x")
    env = {"KC_DPPROD": str(kc)}

    def fake_http(url, timeout=15.0):
        return prom_vector([({"code": "200"}, "10")])

    rc = obs.main(["--cluster", "dpprod", "--preset", "dp-code-breakdown"],
                  pf_factory=_fake_pf(None), http_get=fake_http, env=env)
    out = capsys.readouterr()
    assert rc == 0
    assert "code=200" in out.out


def test_main_silent_zero_warns_and_exit0(capsys, tmp_path):
    kc = tmp_path / "kc"
    kc.write_text("x")
    env = {"KC_DPPROD": str(kc)}

    def fake_http(url, timeout=15.0):
        return prom_empty()

    rc = obs.main(["--cluster", "dpprod", "--preset", "dp-5xx-rate"],
                  pf_factory=_fake_pf(None), http_get=fake_http, env=env)
    cap = capsys.readouterr()
    assert rc == 0
    assert "MATCHED NOTHING" in cap.err     # loud on stderr
    assert "0" not in cap.out or "no series" in cap.out


def test_main_absence_ok_preset_empty_renders_calm_ok(capsys, tmp_path):
    # homelab-alerts-firing with nothing firing -> empty result -> calm OK on
    # stdout, NOT the ⚠ banner on stderr (guard keeps its credibility).
    kc = tmp_path / "kc"
    kc.write_text("x")
    env = {"KC_HOMELAB": str(kc)}

    def fake_http(url, timeout=15.0):
        return prom_empty()

    rc = obs.main(["--cluster", "homelab", "--preset", "homelab-alerts-firing"],
                  pf_factory=_fake_pf(None), http_get=fake_http, env=env)
    cap = capsys.readouterr()
    assert rc == 0
    assert "OK" in cap.out
    assert "MATCHED NOTHING" not in cap.err


def test_main_list_presets(capsys):
    rc = obs.main(["--list-presets"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "dp-5xx-rate" in out
    assert "UNVALIDATED" in out             # honesty tag is surfaced


# =========================================================================== #
# CLI smoke via subprocess (offline paths only)
# =========================================================================== #
def test_cli_list_presets_subprocess():
    r = subprocess.run([sys.executable, str(SCRIPTS / "obs-read"),
                        "--list-presets"],
                       stdout=subprocess.PIPE, text=True, timeout=15)
    assert r.returncode == 0
    assert "dp-trpc-errors" in r.stdout


def test_cli_no_cluster_subprocess():
    r = subprocess.run([sys.executable, str(SCRIPTS / "obs-read"),
                        "--preset", "dp-5xx-rate"],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       text=True, timeout=15)
    assert r.returncode == 2
    assert "--cluster is REQUIRED" in r.stderr
