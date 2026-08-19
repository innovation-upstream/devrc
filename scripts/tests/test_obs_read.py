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
# Local-port TOCTOU — bounded retry on a BIND COLLISION only
#
# _free_port() closes its probe socket before kubectl binds the number, so a
# concurrent obs-read can steal it. __enter__ re-picks and relaunches, but ONLY
# for a collision: a genuine failure (RBAC, missing svc, backend never ready)
# must surface on the FIRST attempt, unretried and unrewritten.
# =========================================================================== #
# Verbatim kubectl stderr for a local-port collision (it prints BOTH lines;
# _drain_stderr keeps only the LAST non-empty one).
KUBECTL_COLLISION_STDERR = (
    "Unable to listen on port 45011: Listeners failed to create with the "
    "following errors: [unable to create listener: Error listen tcp4 "
    "127.0.0.1:45011: bind: address already in use]\n"
    "error: unable to listen on any of the requested ports: [{45011 9090}]\n"
)
# A genuine, NON-retryable failure.
KUBECTL_RBAC_STDERR = (
    'error: services "kube-prometheus-stack-prometheus" is forbidden: User '
    '"system:serviceaccount:default:default" cannot get resource "services" '
    'in API group "" in the namespace "monitoring"\n'
)


class _PFAttempts:
    """popen stub recording one FakeProc per attempt.

    `stderrs[i]` scripts attempt i to die early with that kubectl stderr; any
    attempt past the end of the list gets a LIVE proc (i.e. succeeds).
    """

    def __init__(self, stderrs):
        self.stderrs = list(stderrs)
        self.ports = []          # local port used by each attempt, in order
        self.procs = []          # the FakeProc handed to each attempt

    def popen(self, argv, **kw):
        # argv's last element is the "<local>:<remote>" port mapping
        self.ports.append(int(argv[-1].split(":")[0]))
        i = len(self.procs)
        if i < len(self.stderrs):
            proc = FakeProc(alive=False, stderr_text=self.stderrs[i])
        else:
            proc = FakeProc()    # alive -> ready
        self.procs.append(proc)
        return proc


# Deliberately LONGER than PF_ATTEMPTS (8 ports for a bound of 3): a mutant that
# raises the retry bound must run out of ASSERTION, not out of fixture. A short
# list makes such a mutant die on StopIteration from the _free_port iterator —
# green for the wrong reason, and blind to the bound it claims to pin.
_SPARE_PORTS = [45011, 45013, 45017, 45021, 45023, 45027, 45031, 45037]


def _wait_ready_faithful(port, ready_path, timeout, proc=None):
    """Stand-in for _wait_ready that routes an early-exited proc through the
    REAL _wait_ready — so the error TEXT the retry discriminates on is produced
    by production code, not by the test. A live proc is treated as ready."""
    if proc is not None and proc.poll() is not None:
        obs._wait_ready(port, ready_path, 0.5, proc)   # raises RuntimeError
    return None


def _pf_with(handler, ports, monkeypatch, backend="prometheus"):
    it = iter(ports)
    monkeypatch.setattr(obs, "_free_port", lambda: next(it))
    return obs.PortForward("/kc", obs.BACKENDS[backend],
                           popen=handler.popen,
                           wait_ready=_wait_ready_faithful)


def test_is_port_collision_discriminates(monkeypatch):
    # both shapes kubectl can leave as the last stderr line
    assert obs._is_port_collision(RuntimeError(
        "kubectl port-forward exited early: error: unable to listen on any of "
        "the requested ports: [{45011 9090}]")) is True
    assert obs._is_port_collision(RuntimeError(
        "kubectl port-forward exited early: Unable to listen on port 45011: "
        "... bind: address already in use]")) is True
    # everything that a different port cannot fix
    for other in (
        'kubectl port-forward exited early: Error from server (NotFound): '
        'services "pyroscope" not found',
        "kubectl port-forward exited early: " + KUBECTL_RBAC_STDERR.strip(),
        "backend not ready on 127.0.0.1:45011/ready in 10s (timed out)",
        "kubectl port-forward exited early",
    ):
        assert obs._is_port_collision(RuntimeError(other)) is False
    assert obs._is_port_collision(TimeoutError("never became ready")) is False


def test_is_port_collision_ignores_a_client_side_address_in_use():
    """A readiness TIMEOUT is not a bind collision even when its text carries
    'address already in use'.

    _wait_ready interpolates the LAST client-side connect error into its own
    message, so a host out of ephemeral ports produces exactly this string. It
    matches a collision marker; only the `exited early` prefix test keeps it out
    of the retry path. Retrying it would burn 3 x startup_timeout and report
    'local port collision on all 3 attempts' for an honest not-ready backend.
    """
    assert obs._is_port_collision(TimeoutError(
        "backend not ready on 127.0.0.1:45011/-/ready in 10s "
        "([Errno 98] Address already in use)")) is False
    # same trap in the other marker's wording
    assert obs._is_port_collision(TimeoutError(
        "backend not ready on 127.0.0.1:45011/ready in 10s (unable to listen "
        "on any of the requested ports)")) is False


def test_is_port_collision_marker_is_specific_not_just_listen():
    """The marker's SPECIFICITY is the property the prefix test rests on.

    kubectl's per-port diagnostic (`Unable to listen on port N: Listeners failed
    to create ...`) names listening for failures a different port cannot fix —
    EADDRNOTAVAIL, EACCES. A predicate broadened to any mention of 'listen'
    would retry those three times instead of surfacing them.
    """
    for not_a_collision in (
        "kubectl port-forward exited early: Unable to listen on port 45011: "
        "Listeners failed to create with the following errors: [unable to "
        "create listener: Error listen tcp6 [::1]:45011: bind: cannot assign "
        "requested address]",
        "kubectl port-forward exited early: Unable to listen on port 443: "
        "Listeners failed to create with the following errors: [unable to "
        "create listener: Error listen tcp4 127.0.0.1:443: bind: permission "
        "denied]",
    ):
        assert obs._is_port_collision(RuntimeError(not_a_collision)) is False, (
            "a mention of listening is not a bind collision")


def test_is_port_collision_is_case_insensitive():
    """The `.lower()` normalisation is load-bearing: the marker text is not
    always emitted lowercase (older kubectl capitalises the sentence, and a
    Python-side OSError renders 'Address already in use')."""
    assert obs._is_port_collision(RuntimeError(
        "kubectl port-forward exited early: error: Unable to listen on any of "
        "the requested ports: [{45011 9090}]")) is True
    assert obs._is_port_collision(RuntimeError(
        "kubectl port-forward exited early: Unable to listen on port 45011: "
        "[unable to create listener: Error listen tcp4 127.0.0.1:45011: bind: "
        "Address already in use]")) is True


def test_port_forward_retries_collision_on_a_different_port(monkeypatch):
    # attempt 1 loses the race; attempt 2 must use a DIFFERENT port and win.
    h = _PFAttempts([KUBECTL_COLLISION_STDERR])
    pf = _pf_with(h, [45011, 45013, 45017], monkeypatch)
    with pf as port:
        assert port == 45013
    assert h.ports == [45011, 45013]              # re-picked, not reused
    assert len(set(h.ports)) == len(h.ports)      # pairwise distinct
    assert h.procs[0].terminated is True          # failed attempt reaped
    assert h.procs[1].terminated is True          # success torn down on exit


def test_port_forward_does_not_retry_a_non_collision_failure(monkeypatch):
    # An RBAC denial must fail on attempt 1 with kubectl's own message intact —
    # retrying would mask a real error and triple the time-to-error.
    h = _PFAttempts([KUBECTL_RBAC_STDERR] * 5)
    pf = _pf_with(h, [45011, 45013, 45017, 45021], monkeypatch)
    with pytest.raises(RuntimeError) as ei:
        pf.__enter__()
    assert len(h.procs) == 1                      # EXACTLY one attempt
    assert h.ports == [45011]
    # original error preserved verbatim — not rewritten into a retry message
    assert str(ei.value) == ("kubectl port-forward exited early: "
                             + KUBECTL_RBAC_STDERR.strip())
    assert h.procs[0].terminated is True


def test_port_forward_does_not_retry_a_readiness_timeout(monkeypatch):
    # The backend never answering is not a port problem either.
    calls = []
    it = iter([45011, 45013, 45017])
    monkeypatch.setattr(obs, "_free_port", lambda: next(it))

    def never_ready(*a, **k):
        calls.append(1)
        raise TimeoutError("backend not ready on 127.0.0.1:45011/ready in 10s")

    proc = FakeProc()
    pf = obs.PortForward("/kc", obs.BACKENDS["loki"],
                         popen=lambda *a, **k: proc, wait_ready=never_ready)
    with pytest.raises(TimeoutError) as ei:
        pf.__enter__()
    assert len(calls) == 1
    assert "backend not ready" in str(ei.value)
    assert proc.terminated is True


def test_port_forward_does_not_retry_a_timeout_naming_address_in_use():
    # The behavioural half of test_is_port_collision_ignores_a_client_side_
    # address_in_use: on a host out of ephemeral ports the readiness TimeoutError
    # carries 'Address already in use' from the client side. It must still fail
    # on attempt 1, with the message verbatim — not 3 x 10s and a bogus
    # 'local port collision on all 3 attempts'.
    msg = ("backend not ready on 127.0.0.1:45011/-/ready in 10s "
           "([Errno 98] Address already in use)")
    calls = []

    def never_ready(*a, **k):
        calls.append(1)
        raise TimeoutError(msg)

    procs = []

    def popen(*a, **k):
        procs.append(FakeProc())
        return procs[-1]

    pf = obs.PortForward("/kc", obs.BACKENDS["prometheus"],
                         popen=popen, wait_ready=never_ready)
    with pytest.raises(TimeoutError) as ei:
        pf.__enter__()
    assert len(calls) == 1, "a readiness timeout must not be retried"
    assert len(procs) == 1
    assert str(ei.value) == msg              # original error, not rewritten
    assert procs[0].terminated is True


def test_port_forward_gives_up_after_exhausting_collision_retries(monkeypatch):
    h = _PFAttempts([KUBECTL_COLLISION_STDERR] * len(_SPARE_PORTS))
    pf = _pf_with(h, _SPARE_PORTS, monkeypatch)
    with pytest.raises(RuntimeError) as ei:
        pf.__enter__()
    msg = str(ei.value)
    # the retry BOUND, asserted first and against literals: a PF_ATTEMPTS>3
    # mutant has spare ports left (see _SPARE_PORTS) so it dies HERE, on the
    # observed launch sequence, not on the fixture running dry.
    assert h.ports == [45011, 45013, 45017]       # bounded: exactly 3 launches
    assert obs.PF_ATTEMPTS == 3
    assert len(h.procs) == obs.PF_ATTEMPTS
    # actionable: names the exhausted budget AND carries kubectl's last error
    assert ("local port collision on all %d attempts" % obs.PF_ATTEMPTS) in msg
    assert "unable to listen on any of the requested ports" in msg


def test_port_forward_leaks_no_process_across_retries(monkeypatch):
    # every failed attempt's kubectl is terminated AND reaped before the next
    h = _PFAttempts([KUBECTL_COLLISION_STDERR] * len(_SPARE_PORTS))
    pf = _pf_with(h, _SPARE_PORTS, monkeypatch)
    with pytest.raises(RuntimeError):
        pf.__enter__()
    assert len(h.procs) == obs.PF_ATTEMPTS
    assert all(p.terminated for p in h.procs)
    assert all(p.waited for p in h.procs)
    assert pf.proc is None                        # no handle left dangling



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
# adoption telemetry (Part A instrumentation)
# =========================================================================== #
def _read_last_event(spool_dir):
    coll = SCRIPTS / "collector"
    sys.path.insert(0, str(coll))
    import collector as C  # noqa: PLC0415
    text = (Path(spool_dir) / "current.log").read_text().strip()
    line = text.splitlines()[-1]
    return C.parse_line(line), line


def test_invocation_outcome_mapping():
    assert obs.invocation_outcome(False, False) == "ok"
    assert obs.invocation_outcome(True, False) == "matched-nothing"
    # expected-absence preset: empty is healthy -> ok, not a caught silent-zero
    assert obs.invocation_outcome(True, True) == "ok"


def test_main_emits_ok_invocation(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("ACTIVITY_SPOOL_DIR", str(tmp_path))
    kc = tmp_path / "kc"
    kc.write_text("x")

    def fake_http(url, timeout=15.0):
        return prom_vector([({"code": "200"}, "10")])

    rc = obs.main(["--cluster", "dpprod", "--preset", "dp-code-breakdown"],
                  pf_factory=_fake_pf(None), http_get=fake_http,
                  env={"KC_DPPROD": str(kc)})
    assert rc == 0
    ev, _ = _read_last_event(tmp_path)
    assert ev["source"] == "tool" and ev["text"] == "obs-read"
    p = json.loads(ev["payload"])
    assert p["outcome"] == "ok"
    assert p["cluster"] == "dpprod" and p["backend"] == "prometheus"
    assert p["preset"] == "dp-code-breakdown"


def test_main_emits_matched_nothing(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("ACTIVITY_SPOOL_DIR", str(tmp_path))
    kc = tmp_path / "kc"
    kc.write_text("x")

    def fake_http(url, timeout=15.0):
        return prom_empty()

    rc = obs.main(["--cluster", "dpprod", "--preset", "dp-5xx-rate"],
                  pf_factory=_fake_pf(None), http_get=fake_http,
                  env={"KC_DPPROD": str(kc)})
    assert rc == 0
    ev, _ = _read_last_event(tmp_path)
    p = json.loads(ev["payload"])
    assert p["outcome"] == "matched-nothing"


def test_main_emits_error_on_transport_failure(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("ACTIVITY_SPOOL_DIR", str(tmp_path))
    kc = tmp_path / "kc"
    kc.write_text("x")

    def boom(url, timeout=15.0):
        raise RuntimeError("kubectl exited early")

    rc = obs.main(["--cluster", "dpprod", "--preset", "dp-5xx-rate"],
                  pf_factory=_fake_pf(None), http_get=boom,
                  env={"KC_DPPROD": str(kc)})
    assert rc == 1
    ev, _ = _read_last_event(tmp_path)
    p = json.loads(ev["payload"])
    assert p["outcome"] == "error"


def test_privacy_error_path_leaks_neither_query_nor_exception(capsys, tmp_path,
                                                              monkeypatch):
    """SECURITY (error path): a secret-bearing --query AND a transport exception
    whose message carries a secret must NOT reach the emitted event — the error
    path records ONLY {tool,outcome:error,cluster,backend,preset:adhoc}."""
    import base64 as _b64
    monkeypatch.setenv("ACTIVITY_SPOOL_DIR", str(tmp_path))
    kc = tmp_path / "kc"
    kc.write_text("x")

    def boom(url, timeout=15.0):
        raise RuntimeError("connect failed: secret_token=hunter2")

    rc = obs.main(["--cluster", "homelab", "--backend", "prometheus",
                   "--query", 'super_secret_metric{apikey="AKIA_LEAK"}'],
                  pf_factory=_fake_pf(None), http_get=boom,
                  env={"KC_HOMELAB": str(kc)})
    assert rc == 1
    ev, raw = _read_last_event(tmp_path)
    p = json.loads(ev["payload"])
    assert p["outcome"] == "error" and p["preset"] == "adhoc"
    assert p["cluster"] == "homelab" and p["backend"] == "prometheus"
    decoded = _b64.b64decode(raw.split("b64:payload=")[1].split("\t")[0])
    blob = json.dumps(ev)
    for secret in ("super_secret_metric", "AKIA_LEAK", "hunter2", "secret_token"):
        assert secret not in blob, f"{secret} leaked into decoded event"
        assert secret not in raw, f"{secret} leaked into raw spool line"
        assert secret.encode() not in decoded, f"{secret} leaked into payload"


def test_privacy_preset_query_text_not_leaked(capsys, tmp_path, monkeypatch):
    """SECURITY: the emitted event must carry the preset NAME, never the PromQL."""
    monkeypatch.setenv("ACTIVITY_SPOOL_DIR", str(tmp_path))
    kc = tmp_path / "kc"
    kc.write_text("x")

    def fake_http(url, timeout=15.0):
        return prom_vector([({"code": "500"}, "1")])

    rc = obs.main(["--cluster", "dpprod", "--preset", "dp-5xx-rate"],
                  pf_factory=_fake_pf(None), http_get=fake_http,
                  env={"KC_DPPROD": str(kc)})
    assert rc == 0
    ev, raw_line = _read_last_event(tmp_path)
    # The preset's PromQL contains this metric; it must NOT appear anywhere in
    # the emitted (decoded) event OR the raw spool line.
    assert "traefik_service_requests_total" not in json.dumps(ev)
    import base64 as _b64
    decoded = _b64.b64decode(raw_line.split("b64:payload=")[1].split("\t")[0])
    assert b"traefik_service_requests_total" not in decoded
    assert json.loads(ev["payload"])["preset"] == "dp-5xx-rate"


def test_privacy_adhoc_query_text_not_leaked(capsys, tmp_path, monkeypatch):
    """SECURITY: a raw --query must be recorded as 'adhoc', never verbatim."""
    monkeypatch.setenv("ACTIVITY_SPOOL_DIR", str(tmp_path))
    kc = tmp_path / "kc"
    kc.write_text("x")

    def fake_http(url, timeout=15.0):
        return prom_vector([({}, "1")])

    rc = obs.main(["--cluster", "homelab", "--backend", "prometheus",
                   "--query", 'super_secret_metric{token="hunter2"}'],
                  pf_factory=_fake_pf(None), http_get=fake_http,
                  env={"KC_HOMELAB": str(kc)})
    assert rc == 0
    ev, _ = _read_last_event(tmp_path)
    p = json.loads(ev["payload"])
    assert p["preset"] == "adhoc"
    blob = json.dumps(ev)
    assert "super_secret_metric" not in blob and "hunter2" not in blob


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
