"""Unit tests for ship.sh's remote-address selection (LAN -> nebula fallback).

WHY THIS EXISTS, measured 2026-09-09: `ship.sh` reached for the laptop's LAN
address `192.168.50.155`, ssh timed out after the connect timeout, and the leg
exited 255 with `converge exited 255`. The laptop was UP and answering on its
nebula address `10.42.0.100` the entire time. Two things were wrong and only one
of them is cosmetic:

  * the deploy did not happen -- the laptop silently stopped receiving every
    future change while the run merely looked like it had hit a dead host;
  * nothing in the output distinguished "this host is off" from "this host is
    not on that network", so the fix is not discoverable from the failure.

The selection is split so it can be tested without a network:
`remote_ssh_candidates_of` is PURE (prints the targets to try, contacts
nothing), and `first_reachable_ssh` does the probing behind `$SSH_PROBE_CMD`,
which these tests replace with a stub.

🔴 The contract that matters most is the NEGATIVE one: an explicit
`$REMOTE_SSH` / `$LAPTOP_SSH` must be a ONE-ELEMENT list, so a run an operator
addressed by hand can never be silently redirected to the other address. A
fallback that also "helpfully" retries an explicit target is a converge landing
on a machine nobody named.
"""

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

# 🔴 write_exec owns the shebang. `#!/usr/bin/env bash` here is forbidden by
# `test_runtime_shebangs.py` because `env` is absent from the nix build sandbox,
# so a stub written that way cannot run in the tier the merge is gated on.
from testlib.mockbin import write_exec  # noqa: E402

LIB = Path(__file__).resolve().parents[1] / "lib" / "host-role.sh"

# The canonical addresses, restated here ON PURPOSE: if someone renumbers a host
# in the lib, these tests must FAIL rather than silently re-derive the new value
# and keep passing. A test that reads its expectation from the code under test
# asserts nothing.
LAPTOP_LAN = "zach@192.168.50.155"
LAPTOP_NEBULA = "zach@10.42.0.100"
WORKBENCH_LAN = "zach@192.168.50.250"
WORKBENCH_NEBULA = "zach@10.42.0.30"


def _run(snippet: str, env: dict | None = None) -> str:
    """Source the lib and run a snippet, returning stdout (stderr dropped)."""
    script = f'set -euo pipefail\nsource "{LIB}"\n{textwrap.dedent(snippet)}'
    full_env = {**os.environ, **(env or {})}
    # Unset the real overrides unless the case sets them, so a developer with
    # REMOTE_SSH exported in their shell cannot turn these green by accident.
    for var in ("REMOTE_SSH", "LAPTOP_SSH"):
        if not (env or {}).get(var):
            full_env.pop(var, None)
    out = subprocess.run(
        ["bash", "-c", script],
        capture_output=True, text=True, check=True, env=full_env,
    )
    return out.stdout.strip()


def candidates(role: str, env: dict | None = None) -> list[str]:
    raw = _run(f'remote_ssh_candidates_of {role}', env)
    return [line for line in raw.splitlines() if line.strip()]


# --------------------------------------------------------------------------- #
# The candidate list -- pure, no network
# --------------------------------------------------------------------------- #

def test_from_the_workbench_the_laptops_lan_address_comes_first():
    assert candidates("workbench") == [LAPTOP_LAN, LAPTOP_NEBULA]


def test_from_the_laptop_the_workbenchs_lan_address_comes_first():
    assert candidates("laptop") == [WORKBENCH_LAN, WORKBENCH_NEBULA]


def test_an_unknown_role_offers_nothing():
    assert candidates("unknown") == []


@pytest.mark.parametrize("override", ["REMOTE_SSH", "LAPTOP_SSH"])
def test_an_explicit_target_is_the_WHOLE_list(override):
    """🔴 The negative contract: no fallback behind an operator's own address.

    A second candidate here would let a run addressed at one machine converge a
    DIFFERENT one whenever the named host happened not to answer.
    """
    assert candidates("workbench", {override: "zach@10.0.0.9"}) == ["zach@10.0.0.9"]


def test_LAPTOP_SSH_does_not_leak_into_the_laptops_own_candidates():
    """From the laptop, the remote is the WORKBENCH -- LAPTOP_SSH would be self."""
    assert candidates("laptop", {"LAPTOP_SSH": "zach@10.0.0.9"}) == [
        WORKBENCH_LAN,
        WORKBENCH_NEBULA,
    ]


# --------------------------------------------------------------------------- #
# The probe -- selection driven with a stubbed prober
# --------------------------------------------------------------------------- #

def _probe_stub(tmp_path: Path, reachable: str) -> str:
    """A prober that succeeds only for `reachable` (empty = nothing answers)."""
    return str(write_exec(tmp_path / "probe.sh",
                          '[ "$1" = "%s" ]\n' % reachable))


def test_the_lan_address_wins_when_it_answers(tmp_path):
    stub = _probe_stub(tmp_path, LAPTOP_LAN)
    got = _run(
        f'first_reachable_ssh {LAPTOP_LAN} {LAPTOP_NEBULA}',
        {"SSH_PROBE_CMD": stub},
    )
    assert got == LAPTOP_LAN


def test_it_falls_back_to_nebula_when_the_lan_address_is_silent(tmp_path):
    """THE MEASURED CASE: laptop off-LAN, up on nebula."""
    stub = _probe_stub(tmp_path, LAPTOP_NEBULA)
    got = _run(
        f'first_reachable_ssh {LAPTOP_LAN} {LAPTOP_NEBULA}',
        {"SSH_PROBE_CMD": stub},
    )
    assert got == LAPTOP_NEBULA


def test_nothing_reachable_yields_nothing_and_a_nonzero_status(tmp_path):
    """🔴 It must NOT fall through to the first candidate.

    Returning a target nobody could reach would turn a reachability fact into a
    converge failure 200 lines later, which is the failure this whole change
    exists to make legible.
    """
    stub = _probe_stub(tmp_path, "zach@nothing-answers")
    script = (
        f'set -uo pipefail\nsource "{LIB}"\n'
        f'out="$(first_reachable_ssh {LAPTOP_LAN} {LAPTOP_NEBULA})" && rc=0 || rc=$?\n'
        'echo "rc=$rc out=[$out]"\n'
    )
    env = {**os.environ, "SSH_PROBE_CMD": stub}
    env.pop("REMOTE_SSH", None)
    res = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, check=True, env=env
    )
    assert res.stdout.strip() == "rc=1 out=[]"


def test_the_probe_reports_each_address_that_did_not_answer(tmp_path):
    """The operator has to be able to see WHICH addresses were tried."""
    stub = _probe_stub(tmp_path, LAPTOP_NEBULA)
    env = {**os.environ, "SSH_PROBE_CMD": stub}
    env.pop("REMOTE_SSH", None)
    res = subprocess.run(
        ["bash", "-c",
         f'set -uo pipefail\nsource "{LIB}"\nfirst_reachable_ssh {LAPTOP_LAN} {LAPTOP_NEBULA}'],
        capture_output=True, text=True, check=True, env=env,
    )
    assert LAPTOP_LAN in res.stderr
    assert "did not answer" in res.stderr
    # ...and the one that DID answer is not reported as silent.
    assert f"{LAPTOP_NEBULA} did not answer" not in res.stderr


def test_the_secondary_targets_are_derived_from_the_ip_constants(tmp_path):
    """One address, one place -- the nebula targets must not be a second copy.

    Renumbering `LAPTOP_IP_SECONDARY` alone must move the ssh target with it; if
    someone re-spells the address in a `*_SSH_SECONDARY` literal, this fails.

    ⚠ The renumber is done in a COPY of the lib, not by exporting the variable:
    the lib assigns its constants unconditionally, so an injected value is
    overwritten the moment it is sourced. A first draft of this test did exactly
    that and asserted nothing -- it read the unmodified default back and failed,
    which at least failed LOUDLY rather than passing vacuously.
    """
    copy = tmp_path / "host-role.sh"
    src = LIB.read_text()
    old = 'LAPTOP_IP_SECONDARY="10.42.0.100"'
    assert old in src, (
        f"expected {old!r} in {LIB}; if the constant was renamed or respelled, "
        "re-point this test -- it cannot prove derivation without it."
    )
    copy.write_text(src.replace(old, 'LAPTOP_IP_SECONDARY="10.99.99.99"'))

    out = subprocess.run(
        ["bash", "-c", f'set -euo pipefail\nsource "{copy}"\nremote_ssh_candidates_of workbench'],
        capture_output=True, text=True, check=True,
        env={k: v for k, v in os.environ.items() if k not in ("REMOTE_SSH", "LAPTOP_SSH")},
    )
    lines = out.stdout.split()
    assert "zach@10.99.99.99" in lines, (
        f"renumbering LAPTOP_IP_SECONDARY did not move the ssh target: {lines}. "
        "The nebula target is a duplicated literal rather than derived."
    )
    assert LAPTOP_NEBULA not in lines, (
        "the ORIGINAL nebula address survived a renumber, so it is spelled "
        f"somewhere else too: {lines}"
    )


# --------------------------------------------------------------------------- #
# The FOUR failure states -- ported from the closed PR #1287 (`scripts/workhost`)
#
# 🔴 WHY: until this landed, every failing target got the SAME line, "did not
# answer". A powered-off host and a host whose key CHANGED (a reinstall, or a
# squatter on the LAN address) were reported identically, so the second read as
# the first. An empty result cannot distinguish the mechanisms that produce it;
# these tests pin that each one is NAMED.
# --------------------------------------------------------------------------- #

def _probe_saying(tmp_path: Path, text: str, stream: str = "stderr") -> str:
    """A prober that always FAILS, emitting `text` on the named stream.

    ssh exits 255 for a refused host key and for a dead host alike, so the state
    is carried by the message, not the status -- which is exactly why the stub
    signals the same way rather than inventing a private exit code.
    """
    redirect = ">&2" if stream == "stderr" else ""
    return str(write_exec(tmp_path / f"probe_{stream}.sh",
                          'printf "%%s\\n" %s %s\nexit 255\n'
                          % (repr(text).replace("'", '"'), redirect)))


def _probe_stderr_of(tmp_path: Path, stub: str, targets: str) -> str:
    env = {**os.environ, "SSH_PROBE_CMD": stub}
    env.pop("REMOTE_SSH", None)
    res = subprocess.run(
        ["bash", "-c",
         f'set -uo pipefail\nsource "{LIB}"\nfirst_reachable_ssh {targets}'],
        capture_output=True, text=True, env=env,
    )
    return res.stderr


def test_a_refused_host_key_is_reported_as_untrusted_key_not_as_silence(tmp_path):
    """🔴 THE POINT OF THE PORT. `accept-new` refuses a CHANGED key by design,
    and that refusal used to be indistinguishable from the host being off."""
    stub = _probe_saying(tmp_path, "Host key verification failed.")
    err = _probe_stderr_of(tmp_path, stub, LAPTOP_LAN)
    assert "untrusted-key" in err, (
        f"a refused host key was not named as such: {err!r}. This is the state "
        "that reads as a network fault and sends the operator to the wrong place."
    )
    assert "did not answer" not in err, (
        f"the refused key was ALSO reported as silence, so the states are still "
        f"collapsed rather than distinguished: {err!r}"
    )


def test_a_changed_identification_banner_is_also_untrusted_key(tmp_path):
    """The other spelling ssh uses -- a reinstalled host, not an unknown one."""
    stub = _probe_saying(
        tmp_path, "WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!")
    err = _probe_stderr_of(tmp_path, stub, LAPTOP_LAN)
    assert "untrusted-key" in err, err


def test_a_silent_address_is_still_named_unreachable(tmp_path):
    """The common case keeps its wording AND gains its state token."""
    stub = _probe_saying(tmp_path, "")
    err = _probe_stderr_of(tmp_path, stub, LAPTOP_LAN)
    assert "did not answer" in err, err
    assert "unreachable" in err, (
        f"the silent case lost its state token, so the four states are no longer "
        f"greppable as a set: {err!r}"
    )
    assert "untrusted-key" not in err, (
        f"a plain timeout was classified as a key problem: {err!r}"
    )


def test_an_unconfigured_path_is_NAMED_rather_than_skipped_in_silence(tmp_path):
    """🔴 `not-configured` was previously a bare `continue`.

    A path with no address produced no output at all, so "we never tried" and
    "we tried and it was down" looked the same from the log.
    """
    stub = _probe_saying(tmp_path, "")
    err = _probe_stderr_of(tmp_path, stub, '"" ')
    assert "not-configured" in err, (
        f"an empty target was skipped silently: {err!r}"
    )


def test_the_classification_reads_STDERR_and_not_stdout(tmp_path):
    """🔴 PINS THE REDIRECTION ORDER. `2>&1 >/dev/null` captures stderr alone;
    reversed, it captures stdout and classification reads the wrong stream.

    A probe shouting the host-key banner on STDOUT is NOT a key problem -- ssh
    writes that diagnostic to stderr. If this goes red with `untrusted-key`, the
    redirection was flipped and every classification is now reading stdout.
    """
    stub = _probe_saying(
        tmp_path, "Host key verification failed.", stream="stdout")
    err = _probe_stderr_of(tmp_path, stub, LAPTOP_LAN)
    assert "unreachable" in err, (
        f"stdout was classified as if it were ssh's diagnostic: {err!r}"
    )
    assert "untrusted-key" not in err, (
        f"the classifier read STDOUT -- the `2>&1 >/dev/null` order is flipped, "
        f"so a target that merely prints on stdout is reported as a key "
        f"refusal: {err!r}"
    )


def test_a_reachable_target_still_wins_and_says_nothing(tmp_path):
    """The success path must stay quiet -- stdout is the winner, alone."""
    stub = str(write_exec(tmp_path / "ok.sh", '[ "$1" = "%s" ]\n' % LAPTOP_NEBULA))
    env = {**os.environ, "SSH_PROBE_CMD": stub}
    env.pop("REMOTE_SSH", None)
    res = subprocess.run(
        ["bash", "-c",
         f'set -uo pipefail\nsource "{LIB}"\nfirst_reachable_ssh {LAPTOP_LAN} {LAPTOP_NEBULA}'],
        capture_output=True, text=True, check=True, env=env,
    )
    assert res.stdout.strip() == LAPTOP_NEBULA
    assert LAPTOP_NEBULA not in res.stderr, (
        f"the winning target was also reported as a failure: {res.stderr!r}"
    )
