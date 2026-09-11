"""`scripts/break-glass-merge.sh` must CLOSE the window it opens, and prove it.

The script automates the escape hatch CLAUDE.md documents in prose: delete
`main`'s `required_status_checks`, merge, put protection back. The prose version
failed twice in real use on 2026-08-29/30 -- `PATCH` cannot recreate a deleted
sub-resource (it 404s `Required status checks not enabled`), and a PARTIAL `PUT`
returns 200 while silently dropping every key it omits. One of those failures
happened INSIDE an EXIT trap that fired exactly as designed, because the
untested command was in the safety net.

So the properties worth pinning are behavioural, not textual:

  * a capture that is empty / malformed / missing a key / already-open must
    REFUSE, before anything is deleted (rc 3);
  * a failed restore must be LOUD and rc 6, never a quiet success;
  * a restore that lands a PARTIAL object must be caught by the READ-BACK and
    reported key-by-key (rc 7) -- this is the 200-means-nothing hazard;
  * the happy path must exit 0 only when the read-back matches key-by-key.

🔴 The `gh` stub here SHELLS OUT TO REAL `jq` for every `--jq` filter, and the
filter it runs is extracted from the script itself. A stubbed binary whose
`--jq` never runs would leave the capture projection -- the thing that decides
which keys survive a restore -- exercised by NOTHING. That trap is documented in
this repo and is the reason for `test_the_capture_projection_runs_under_real_jq`.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
SCRIPT = REPO / "scripts" / "break-glass-merge.sh"

# The raw shape `GET /branches/<b>/protection` returns: booleans arrive wrapped
# in `{"enabled": ...}`, which is exactly why the projection exists.
RAW_PROTECTION = {
    "required_status_checks": {
        "strict": False,
        "checks": [
            {"context": "tekton/devrc-pytests", "app_id": 4320115},
            {"context": "tekton/devrc-nodetests", "app_id": 4320115},
        ],
    },
    "enforce_admins": {"enabled": True},
    "required_pull_request_reviews": None,
    "restrictions": None,
    "required_linear_history": {"enabled": False},
    "allow_force_pushes": {"enabled": False},
    "allow_deletions": {"enabled": False},
    "block_creations": {"enabled": False},
    "required_conversation_resolution": {"enabled": False},
    "lock_branch": {"enabled": False},
    "allow_fork_syncing": {"enabled": False},
}

ELEVEN_KEYS = sorted(RAW_PROTECTION)

GH_STUB = r'''#!/usr/bin/env python3
"""A `gh` good enough to exercise the script's real control flow.

State lives in $BG_STATE/protection.json in the RAW api shape. `--jq` filters
are handed to the REAL jq, so the script's own projection is what runs.
"""
import json, os, subprocess, sys

state = os.environ["BG_STATE"]
mode = os.environ.get("BG_STUB_MODE", "ok")
prot = os.path.join(state, "protection.json")
log = os.path.join(state, "calls.log")

argv = sys.argv[1:]
with open(log, "a") as fh:
    fh.write(" ".join(argv) + "\n")


def load():
    with open(prot) as fh:
        return json.load(fh)


def run_jq(filt, obj):
    p = subprocess.run(["jq", "-c", filt], input=json.dumps(obj),
                       capture_output=True, text=True)
    if p.returncode != 0:
        sys.stderr.write(p.stderr)
        sys.exit(1)
    sys.stdout.write(p.stdout)


def wrap(body):
    """PUT body (projected) -> RAW api shape, as GitHub would store it."""
    out = {"required_status_checks": body.get("required_status_checks"),
           "required_pull_request_reviews": body.get("required_pull_request_reviews"),
           "restrictions": body.get("restrictions")}
    for k in ("enforce_admins", "required_linear_history", "allow_force_pushes",
              "allow_deletions", "block_creations",
              "required_conversation_resolution", "lock_branch",
              "allow_fork_syncing"):
        if k in body:
            out[k] = {"enabled": body[k]}
    return out


if argv[:1] == ["pr"]:
    sys.exit(1 if mode == "merge_fails" else 0)

if argv[0] != "api":
    sys.exit(2)

rest = argv[1:]
method = "GET"
if "-X" in rest:
    i = rest.index("-X")
    method = rest[i + 1]
    del rest[i:i + 2]

inp = None
if "--input" in rest:
    i = rest.index("--input")
    inp = rest[i + 1]
    del rest[i:i + 2]

filt = None
if "--jq" in rest:
    i = rest.index("--jq")
    filt = rest[i + 1]
    del rest[i:i + 2]

path = rest[0]

if method == "GET":
    if not os.path.exists(prot):
        sys.stderr.write("Branch not protected\n")
        sys.exit(1)
    obj = load()
    run_jq(filt, obj) if filt else sys.stdout.write(json.dumps(obj))
    sys.exit(0)

if method == "DELETE":
    if mode == "delete_fails":
        sys.stderr.write("could not delete\n")
        sys.exit(1)
    obj = load()
    obj.pop("required_status_checks", None)
    with open(prot, "w") as fh:
        json.dump(obj, fh)
    sys.exit(0)

if method == "PUT":
    if mode == "put_fails":
        sys.stderr.write("PUT rejected\n")
        sys.exit(1)
    with open(inp) as fh:
        body = json.load(fh)
    if mode == "partial_put":
        # The documented hazard: 200, but a key silently absent.
        body.pop("enforce_admins", None)
    with open(prot, "w") as fh:
        json.dump(wrap(body), fh)
    sys.exit(0)

sys.exit(2)
'''


@pytest.fixture
def env(tmp_path):
    """A PATH whose `gh` is the stub, plus a state dir the test can inspect."""
    if shutil.which("jq") is None:
        pytest.skip("jq not on PATH")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    gh = bindir / "gh"
    gh.write_text(GH_STUB)
    gh.chmod(0o755)

    state = tmp_path / "state"
    state.mkdir()
    (state / "protection.json").write_text(json.dumps(RAW_PROTECTION))

    class _Runner:
        """Callable so tests read `env(...)`, with `.state` for inspection."""

        state = None

        def __call__(self, *args, mode="ok", raw=None):
            if raw is not None:
                (state / "protection.json").write_text(json.dumps(raw))
            e = dict(os.environ)
            e["PATH"] = f"{bindir}{os.pathsep}" + e["PATH"]
            e["BG_STATE"] = str(state)
            e["BG_STUB_MODE"] = mode
            return subprocess.run(
                [str(SCRIPT), "--yes", "--workdir", str(tmp_path / "wd"), *args],
                capture_output=True, text=True, env=e, timeout=120,
            )

    runner = _Runner()
    runner.state = state
    return runner


def _protection(env):
    return json.loads((env.state / "protection.json").read_text())


# --------------------------------------------------------------- happy path


def test_the_self_test_opens_the_window_and_closes_it(env):
    r = env()
    assert r.returncode == 0, r.stderr
    assert "read-back FAITHFUL" in r.stderr, r.stderr
    # The window is SHUT again, with both contexts and their app_id pinning.
    after = _protection(env)
    assert [c["context"] for c in after["required_status_checks"]["checks"]] == [
        "tekton/devrc-pytests",
        "tekton/devrc-nodetests",
    ]
    assert {c["app_id"] for c in after["required_status_checks"]["checks"]} == {4320115}
    assert after["enforce_admins"] == {"enabled": True}


def test_the_window_really_was_open_in_between(env):
    """🔴 POSITIVE CONTROL. Every other assertion here is about the state
    AFTERWARDS, which a script that did nothing at all would also satisfy. This
    one proves the DELETE actually landed, so the restore is restoring
    something."""
    r = env()
    assert r.returncode == 0, r.stderr
    calls = (env.state / "calls.log").read_text()
    assert "-X DELETE" in calls and "required_status_checks" in calls, calls
    assert "-X PUT" in calls, calls
    # ...and the DELETE preceded the PUT.
    assert calls.index("-X DELETE") < calls.index("-X PUT"), calls


def test_it_never_PATCHes_protection(env):
    """PATCH 404s once the sub-resource is deleted. It must not be reachable."""
    env()
    assert "-X PATCH" not in (env.state / "calls.log").read_text()
    assert "-X PATCH" not in SCRIPT.read_text()


# ------------------------------------------------- refusals, before any harm


@pytest.mark.parametrize(
    "raw,because",
    [
        ({}, "an empty object has none of the eleven keys"),
        (
            {**RAW_PROTECTION, "required_status_checks": {"strict": False, "checks": []}},
            "zero required checks is an ALREADY-OPEN window, not a capture",
        ),
        (
            {
                **RAW_PROTECTION,
                "required_status_checks": {
                    "strict": False,
                    "checks": [{"context": "tekton/devrc-pytests", "app_id": None}],
                },
            },
            "a null app_id would rebind the context to ANY app that can post it",
        ),
        (
            # 🔴 REACHES THE REQUIRED_KEYS LOOP AND NOTHING ELSE. Every other
            # case here is ALSO caught by the zero-checks guard, which runs
            # later -- so with only those, deleting the loop entirely left the
            # suite green (measured: mutant `required-keys-loop-removed`
            # SURVIVED). This capture has two app_id-pinned checks, so it walks
            # past every other guard; it is missing exactly one of the other ten
            # keys. That is the production hazard: a capture PUT back without
            # `allow_force_pushes` silently turns force-pushes on.
            {k: v for k, v in RAW_PROTECTION.items() if k != "allow_force_pushes"},
            "a capture missing one key would silently drop it on the restore",
        ),
    ],
)
def test_a_bad_capture_REFUSES_before_deleting_anything(env, raw, because):
    r = env(raw=raw)
    assert r.returncode == 3, f"{because}: {r.stderr}"
    calls = (env.state / "calls.log").read_text()
    assert "-X DELETE" not in calls, f"{because} -- but it deleted anyway: {calls}"


def test_a_failed_DELETE_touches_nothing(env):
    r = env(mode="delete_fails")
    assert r.returncode == 4, r.stderr
    assert "-X PUT" not in (env.state / "calls.log").read_text()
    assert _protection(env)["required_status_checks"]["checks"], "checks were lost"


# ------------------------------------------------------- the loud failures


def test_a_failed_restore_is_rc6_and_says_main_may_be_unprotected(env):
    r = env(mode="put_fails")
    assert r.returncode == 6, r.stderr
    assert "RESTORE FAILED" in r.stderr, r.stderr
    # It must hand over the exact manual command, with the capture path.
    assert "-X PUT" in r.stderr and "--input" in r.stderr, r.stderr
    assert "MAY BE UNPROTECTED" in r.stderr, r.stderr


def test_a_PARTIAL_put_is_caught_by_the_readback_not_by_the_200(env):
    """🔴 THE HEADLINE HAZARD. The PUT 'succeeds'; a key is silently gone. Only
    the read-back can tell, and it must name WHICH key."""
    r = env(mode="partial_put")
    assert r.returncode == 7, r.stderr
    assert "MISMATCH" in r.stderr, r.stderr
    assert "DIFF  enforce_admins" in r.stderr, r.stderr


def test_a_failed_merge_still_closes_the_window(env):
    r = env("--pr", "123", mode="merge_fails")
    assert r.returncode == 5, r.stderr
    assert "read-back FAITHFUL" in r.stderr, r.stderr
    assert _protection(env)["required_status_checks"]["checks"], "window left open"


# --------------------------------------------- the projection, under real jq


def _capture_jq() -> str:
    """The filter the script actually uses -- ONE definition, two consumers."""
    src = SCRIPT.read_text()
    m = re.search(
        r"# --- BEGIN CAPTURE_JQ ---(.*?)# --- END CAPTURE_JQ ---", src, re.S
    )
    assert m, "the CAPTURE_JQ markers are gone; this guard would silently test nothing"
    body = m.group(1)
    m2 = re.search(r"CAPTURE_JQ='(.*?)'", body, re.S)
    assert m2, "CAPTURE_JQ is no longer a single-quoted assignment"
    return m2.group(1)


def test_the_capture_projection_runs_under_real_jq():
    """A stub's `--jq` never running is a documented way to leave the projection
    untested. This runs the script's own filter through real jq."""
    if shutil.which("jq") is None:
        pytest.skip("jq not on PATH")
    p = subprocess.run(
        ["jq", "-c", _capture_jq()],
        input=json.dumps(RAW_PROTECTION), capture_output=True, text=True,
    )
    assert p.returncode == 0, p.stderr
    got = json.loads(p.stdout)

    assert sorted(got) == ELEVEN_KEYS, "the PUT body must carry all eleven keys"
    # Presence, not truthiness: these two are legitimately null and REQUIRED.
    assert got["required_pull_request_reviews"] is None
    assert got["restrictions"] is None
    # Booleans must be UNWRAPPED from {"enabled": ...} for the PUT body.
    assert got["enforce_admins"] is True
    assert got["allow_force_pushes"] is False
    # app_id pinning survives, or a restored context binds to any app.
    assert got["required_status_checks"]["checks"] == [
        {"context": "tekton/devrc-pytests", "app_id": 4320115},
        {"context": "tekton/devrc-nodetests", "app_id": 4320115},
    ]


def test_the_projection_guard_can_actually_FAIL():
    """🔴 VACUITY CONTROL. If the assertion above passes against a filter that
    DROPS enforce_admins, it is asserting nothing -- which is the exact defect
    (a silently-dropped key) the whole script exists to catch."""
    if shutil.which("jq") is None:
        pytest.skip("jq not on PATH")
    mutant = _capture_jq().replace("enforce_admins:.enforce_admins.enabled,", "")
    assert mutant != _capture_jq(), "the mutation did not apply; control is inert"
    p = subprocess.run(
        ["jq", "-c", mutant],
        input=json.dumps(RAW_PROTECTION), capture_output=True, text=True,
    )
    assert p.returncode == 0, p.stderr
    assert sorted(json.loads(p.stdout)) != ELEVEN_KEYS, (
        "a filter missing enforce_admins still produced all eleven keys -- "
        "the projection test cannot detect a dropped key"
    )
