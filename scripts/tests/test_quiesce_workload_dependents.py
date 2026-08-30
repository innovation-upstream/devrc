"""quiesce-workload.sh must not suspend a kustomization that others dependsOn.

WHY THIS EXISTS (measured, not hypothetical). 2026-08-30, workbench cluster:
`quiesce-workload.sh workbench media-stack stash-sense` suspended `stash-sense`
while `aggregator` declared `dependsOn: [media-stack, stash-sense]`. A suspended
kustomization can never advance its lastAppliedRevision, so `aggregator` went
`Ready=False DependencyNotReady: revision is not up to date`, pinned at
trunk@8cb6ff3a while the source had moved to trunk@f93935de, retrying every 30s
indefinitely. It was invisible: 0 of the 70 commits in that range touched
aggregator's own path, so nothing had been dropped and no symptom existed. The
NEXT aggregator commit would simply not have applied.

The guard is a pre-flight, so what these tests pin is a RELATIONSHIP, not a
message: `flux suspend` must not run at all when dependents exist or cannot be
determined. Asserting the warning text would pass while the suspend still fired.

The fakes on PATH are what make this hermetic AND what make it a real exercise
of the script -- the assertion reads the sentinel files the fakes write, so a
guard that printed the right words and suspended anyway fails here.

RED/GREEN MATRIX, measured by checking out `origin/main:scripts/quiesce-workload.sh`
over the fixed copy and re-running this file:
  pre-change (origin/main @ bc0809f6):  7 failed, 2 passed
  post-change (HEAD):                   9 passed

The 2 that pass on BOTH sides are INVARIANT GUARDS, not regression coverage --
labelled as such on each. They pin behaviour the bug never violated (the happy
path still suspending; a same-named kustomization in another namespace not
counting), and they exist to stop the FIX from over-firing, which is the way a
guard becomes noise and then gets reflexively --force'd.
"""

import json
import os
import stat
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
QUIESCE = REPO / "scripts" / "quiesce-workload.sh"


def _ks(name, namespace="flux-system", depends_on=None):
    spec = {}
    if depends_on is not None:
        spec["dependsOn"] = depends_on
    return {"metadata": {"name": name, "namespace": namespace}, "spec": spec}


def _fake_bin(bindir: Path, name: str, body: str) -> None:
    p = bindir / name
    p.write_text("#!/usr/bin/env bash\n" + body)
    p.chmod(p.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _run(tmp_path, items, *args, kubectl_rc=0):
    """Run quiesce-workload.sh against fake kubectl/flux.

    Returns (CompletedProcess, suspend_sentinel, scale_sentinel).
    """
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    listing = tmp_path / "kustomizations.json"
    listing.write_text(json.dumps({"items": items}))
    suspend_log = tmp_path / "suspend.called"
    scale_log = tmp_path / "scale.called"

    # kubectl: serve the kustomization list; record scale; empty pod list.
    _fake_bin(
        bindir,
        "kubectl",
        f"""
if [[ "$*" == *"get kustomizations"* ]]; then
  if [[ {kubectl_rc} -ne 0 ]]; then
    echo "error: the server could not find the requested resource" >&2
    exit {kubectl_rc}
  fi
  cat {listing}
  exit 0
fi
if [[ "$*" == *"scale"* ]]; then
  echo "$*" >> {scale_log}
  exit 0
fi
exit 0
""",
    )
    # flux: record every suspend. Its existence is the whole assertion.
    _fake_bin(bindir, "flux", f'echo "$*" >> {suspend_log}\nexit 0\n')

    env = dict(os.environ)
    env["PATH"] = f"{bindir}:{env['PATH']}"
    env["KC_WORKBENCH"] = str(tmp_path / "kubeconfig")
    (tmp_path / "kubeconfig").write_text("fake")

    proc = subprocess.run(
        ["bash", str(QUIESCE), *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    return proc, suspend_log, scale_log


def test_refuses_to_suspend_a_kustomization_that_others_depend_on(tmp_path):
    """The measured incident: aggregator dependsOn stash-sense."""
    items = [
        _ks("stash-sense"),
        _ks("aggregator", depends_on=[{"name": "media-stack"}, {"name": "stash-sense"}]),
        _ks("unrelated", depends_on=[{"name": "media-stack"}]),
    ]
    proc, suspend_log, scale_log = _run(
        tmp_path, items, "workbench", "media-stack", "stash-sense"
    )

    assert proc.returncode == 5, (
        f"expected refusal (5), got {proc.returncode}\n"
        f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
    )
    # The relationship, not the prose: nothing was suspended, nothing scaled.
    assert not suspend_log.exists(), (
        "flux suspend RAN despite the dependent — the guard printed but did not "
        f"guard. suspend log: {suspend_log.read_text()}"
    )
    assert not scale_log.exists(), "deployment was scaled despite the refusal"
    # The operator must be able to see WHICH dependent, or the refusal is unactionable.
    assert "aggregator" in proc.stderr


def test_names_every_dependent_not_merely_the_first(tmp_path):
    items = [
        _ks("stash-sense"),
        _ks("aggregator", depends_on=[{"name": "stash-sense"}]),
        _ks("second-dependent", depends_on=[{"name": "stash-sense"}]),
    ]
    proc, _, _ = _run(tmp_path, items, "workbench", "media-stack", "stash-sense")
    assert proc.returncode == 5
    assert "aggregator" in proc.stderr
    assert "second-dependent" in proc.stderr


def test_suspends_normally_when_nothing_depends_on_it(tmp_path):
    """INVARIANT GUARD (passes pre- and post-change; not regression coverage).

    The guard must not block the ordinary case -- otherwise it gets --force'd
    reflexively and stops being read at all."""
    items = [
        _ks("stash-sense"),
        _ks("aggregator", depends_on=[{"name": "media-stack"}]),
    ]
    proc, suspend_log, scale_log = _run(
        tmp_path, items, "workbench", "media-stack", "stash-sense"
    )
    assert proc.returncode == 0, f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
    assert suspend_log.exists(), "the happy path must still suspend"
    assert "suspend kustomization stash-sense" in suspend_log.read_text()
    assert scale_log.exists(), "the happy path must still scale to 0"


def test_force_overrides_the_refusal(tmp_path):
    items = [
        _ks("stash-sense"),
        _ks("aggregator", depends_on=[{"name": "stash-sense"}]),
    ]
    proc, suspend_log, _ = _run(
        tmp_path, items, "--force", "workbench", "media-stack", "stash-sense"
    )
    assert proc.returncode == 0, f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
    assert suspend_log.exists(), "--force must actually suspend"
    # It still has to say what it overrode.
    assert "aggregator" in proc.stderr


def test_an_unanswerable_dependent_query_refuses_rather_than_reading_as_none(tmp_path):
    """An empty answer from a FAILED query is indistinguishable from a genuine
    'no dependents'. Treating it as the all-clear is the exact shape that let
    the incident through, so it gets its own exit code."""
    proc, suspend_log, _ = _run(
        tmp_path, [], "workbench", "media-stack", "stash-sense", kubectl_rc=1
    )
    assert proc.returncode == 6, (
        f"expected could-not-measure (6), got {proc.returncode}\n"
        f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
    )
    assert not suspend_log.exists(), "suspended despite not knowing the dependents"


def test_force_overrides_the_could_not_measure_refusal(tmp_path):
    proc, suspend_log, _ = _run(
        tmp_path, [], "--force", "workbench", "media-stack", "stash-sense", kubectl_rc=1
    )
    assert proc.returncode == 0, f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
    assert suspend_log.exists()


def test_a_dependsOn_in_a_different_namespace_is_not_this_kustomization(tmp_path):
    """INVARIANT GUARD (passes pre- and post-change; not regression coverage).

    dependsOn[].namespace defaults to the DEPENDENT's own namespace. A
    same-named kustomization in another namespace is a different object, and
    counting it would make the guard fire on unrelated names -- the way a guard
    becomes noise and then gets bypassed."""
    items = [
        _ks("stash-sense"),
        # dependent lives elsewhere and names its own namespace implicitly
        _ks("other-ns-dependent", namespace="other", depends_on=[{"name": "stash-sense"}]),
    ]
    proc, suspend_log, _ = _run(
        tmp_path, items, "workbench", "media-stack", "stash-sense"
    )
    assert proc.returncode == 0, f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
    assert suspend_log.exists()


def test_an_explicit_cross_namespace_dependsOn_IS_counted(tmp_path):
    """The mirror of the previous case: an explicit namespace pointing AT
    flux-system does depend on this one, and must be caught."""
    items = [
        _ks("stash-sense"),
        _ks(
            "other-ns-dependent",
            namespace="other",
            depends_on=[{"name": "stash-sense", "namespace": "flux-system"}],
        ),
    ]
    proc, suspend_log, _ = _run(
        tmp_path, items, "workbench", "media-stack", "stash-sense"
    )
    assert proc.returncode == 5, f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
    assert not suspend_log.exists()
    assert "other-ns-dependent" in proc.stderr


def test_usage_still_exits_1_and_mentions_force(tmp_path):
    proc, _, _ = _run(tmp_path, [], "workbench")
    assert proc.returncode == 1
    assert "--force" in proc.stderr
