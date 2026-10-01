"""devrc #686 — a run that NEVER REACHED a host must not end on a per-host code.

🔴 WHY A NEW FILE RATHER THAN A CASE IN `test_ship_ssh_probe_wiring.py`.

That file is the probe's existing guard and it passed throughout the #686
incident, start to finish. It is not weak — it is STRUCTURALLY BLIND, and the
reason is one line of its own harness: every ship.sh case in it runs

    bash ship.sh --print-remote-target

and that flag `exit 0`s inside the address-selection block, roughly 1,500 lines
before the verdict. So the whole file can only ever observe:

  * WHICH target the selection resolved  (stdout), and
  * the announcement lines the selection printed  (stderr).

It cannot observe the exit status of a real run, the legs, the pre-flight, or
the verdict — the four surfaces where "one host was never contacted" either
survives into the operator's conclusion or does not. `test_ship_falls_back_to_
nebula_and_SAYS_SO` and `test_ship_names_every_address_when_none_answers` both
assert the right thing about the right line, and both stay green while the run
those lines belong to goes on to print a local `✅ VERIFIED` and exit on the
LOCAL host's code. That is the isolation seam: the selection half was correct,
the verdict half was correct about the hosts it heard from, and the pair was
wrong.

So these cases deliberately run ship.sh to COMPLETION and read the END of it.

🔴 HERMETIC, and the sandbox is IMPORTED not re-implemented. These runs leave
`$REMOTE_SSH` unset on purpose — that is the whole point, because an explicit
target is a one-element candidate list and skips the probe — which means the
derived default is the OPERATOR'S REAL LAPTOP, `zach@192.168.50.155`. The
refusing `ssh`/`home-manager` pair from `test_ship_converge` is therefore
load-bearing here, not hygiene: it is the only thing between this test and a
real connection. `$SHIP_REPO` names a path that does not exist, so the local leg
halts at rc 3 having converged nothing — chosen because it makes the override
visible: rc 3 is a real per-host code that the old build would have reported as
the verdict.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
SHIP = SCRIPTS / "ship.sh"
LIB = SCRIPTS / "lib" / "host-role.sh"

sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

# write_exec owns the shebang: `#!/usr/bin/env bash` runs on the dev host and
# ENOENTs in the nix build sandbox, which is the two-tier hazard
# test_runtime_shebangs.py forbids.
from testlib.mockbin import write_exec  # noqa: E402

# The refusing ssh/home-manager pair — see the module docstring.
from test_ship_converge import _write_sandbox_bin  # noqa: E402

pytestmark = pytest.mark.skipif(
    not (SHIP.is_file() and LIB.is_file()),
    reason="needs ship.sh and lib/host-role.sh",
)

#: The laptop's two derived candidates, typed out rather than read from the lib.
#: A test that imports the constants it asserts on cannot see them change.
LAN = "zach@192.168.50.155"
NEBULA = "zach@10.42.0.100"

#: A distinctive stderr the probe stub prints. Deliberately NOT any real ssh
#: wording and NOT a substring of any ship.sh message, so finding it in the
#: output proves it travelled from the probe's stderr to the operator's screen
#: and was not synthesised by the script.
STUB_REASON = "zz-probe-stub-refused-on-purpose-686"


def _probe(tmp_path, answers=None):
    """A $SSH_PROBE_CMD that answers only for `answers`, else fails LOUDLY.

    It prints to STDERR, which is where real ssh prints and where
    `first_reachable_ssh` captures from (`2>&1 >/dev/null`).
    """
    p = tmp_path / "probe-stub"
    ok = answers or ""
    write_exec(
        p,
        f'if [ "$1" = "{ok}" ]; then exit 0; fi\n'
        f'echo "{STUB_REASON}: $1" >&2\n'
        "exit 255\n",
    )
    return str(p)


def _ship(tmp_path, *args, **env_extra):
    """Run ship.sh to COMPLETION with both hosts in scope. Returns (rc, out)."""
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    denybin = _write_sandbox_bin(tmp_path / "denybin")
    env = dict(os.environ)
    env.update(
        HOME=str(home),
        GIT_CONFIG_SYSTEM="/dev/null",
        GIT_TERMINAL_PROMPT="0",
        SHIP_ROLE="workbench",          # no `ip` in the sandbox
        SHIP_REPO=str(tmp_path / "no-such-devrc"),
        SHIP_NO_SWITCH="1",             # never run a real home-manager switch
    )
    env.pop("XDG_STATE_HOME", None)
    # 🔴 BOTH, and POPPED not blanked. Either one set makes the candidate list a
    # single element, which SKIPS the probe — the exact way this whole surface
    # was invisible to the existing suite.
    env.pop("REMOTE_SSH", None)
    env.pop("LAPTOP_SSH", None)
    env.pop("SHIP_SKIP_SSH_PROBE", None)
    env.pop("SHIP_SELF_GEN", None)
    env.update(env_extra)
    assert "PATH" not in env_extra, (
        "do not pass PATH= — it would place a stub AHEAD of the refusing "
        "ssh/home-manager shims and re-open the path to the real laptop."
    )
    env["PATH"] = os.pathsep.join([str(denybin), os.environ["PATH"]])
    # 🔴 MERGED, NOT CONCATENATED, and that is load-bearing for the tail cases.
    # `proc.stdout + proc.stderr` is the obvious spelling and it DESTROYS the
    # ordering: ship.sh writes its verdict to stdout and its probe diagnostics to
    # stderr, so gluing the captures together puts the probe's lines — printed
    # FIRST by the run — at the very end of the string. A "what does the last line
    # say" assertion then reads the top of the run and fails for a reason that has
    # nothing to do with the code. `stderr=STDOUT` reproduces the operator's own
    # documented recipe, `bash scripts/ship.sh > out 2>&1`, which is the artifact
    # the criterion is about. Every `echo` here is one write(2), so the interleave
    # is the real chronology and not a buffering accident.
    proc = subprocess.run(
        ["bash", str(SHIP), *args],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, env=env, timeout=180,
    )
    return proc.returncode, proc.stdout


def _tail(out, n=6):
    """The last `n` non-blank lines — what a `| tail` read actually lands on."""
    return "\n".join([ln for ln in out.splitlines() if ln.strip()][-n:])


# --------------------------------------------------------------------------- #
# rc 27: the verdict
# --------------------------------------------------------------------------- #

def test_a_run_that_reached_NO_candidate_exits_27_not_the_local_hosts_code(tmp_path):
    """🔴 THE #686 REGRESSION. RED at 6d996aea: that build kept the FIRST non-zero
    code, so this run exited on the LOCAL leg's rc 3 and the verdict read
    `ship: incomplete (rc=3)` — a sentence about the host that DID answer, while
    the laptop had never been contacted at all.
    """
    rc, out = _ship(tmp_path, SSH_PROBE_CMD=_probe(tmp_path))

    assert "NO candidate address answered for laptop" in out, (
        "the probe did not reach the no-candidate branch, so this test is not "
        f"exercising the surface it exists to pin:\n{out[-3000:]}"
    )
    assert rc == 27, (
        f"a run that never contacted the laptop exited {rc}. Every code other "
        "than 27 is a claim about a host; this run observed only ONE of two, and "
        f"the exit status has to say that:\n{out[-3000:]}"
    )


def test_the_local_hosts_own_code_is_still_reported_not_swallowed(tmp_path):
    """rc 27 outranks the per-host code; it must not DELETE it.

    The local leg's finding is still the actionable advice for the local host,
    so the headline names it explicitly. A fix that simply overwrote `rc` and
    said nothing would trade one lost fact for another.
    """
    rc, out = _ship(tmp_path, SSH_PROBE_CMD=_probe(tmp_path))
    assert rc == 27
    assert "rc=3" in out, (
        "the local leg's own code vanished from the output when rc 27 took over "
        f"the verdict:\n{out[-3000:]}"
    )


def test_the_TAIL_of_the_run_names_the_host_that_was_never_reached(tmp_path):
    """🔴 CRITERION: the failure must stop reading as success.

    `ship.sh`'s exit status does not survive a pipe — `ship.sh | tail -40; echo
    $?` prints tail's 0 over a real non-zero — so the words at the END of the
    output are, for a piped reader, the ONLY verdict. The no-candidate diagnosis
    is printed at the TOP of the run, ten-plus lines above a local `✅ VERIFIED`;
    at 6d996aea nothing restated it at the bottom.
    """
    _rc, out = _ship(tmp_path, SSH_PROBE_CMD=_probe(tmp_path))
    tail = _tail(out)
    assert "NEVER REACHED" in tail, (
        "the last lines of the run do not say that a host was never contacted, "
        f"so a piped read cannot tell this from a pass:\n--- tail ---\n{tail}"
    )
    assert "laptop" in tail, f"the tail does not name WHICH host:\n{tail}"
    assert "rc=27" in tail, (
        f"the tail does not carry the code a caller would act on:\n{tail}"
    )


def test_no_verdict_line_claims_converged_or_verified(tmp_path):
    """The word that must never appear in this run's verdict.

    Asserted on the VERDICT lines (`ship: …`), not the whole transcript: the
    per-host `[nixos] ✅ VERIFIED` line is legitimate and is exactly what the
    operator's eye lands on, which is why the bottom of the run has to contradict
    it rather than echo it.
    """
    _rc, out = _ship(tmp_path, SSH_PROBE_CMD=_probe(tmp_path))
    verdicts = [ln for ln in out.splitlines() if ln.startswith("ship: ")]
    assert verdicts, out[-2000:]
    offenders = [ln for ln in verdicts if "converged + verified" in ln]
    assert not offenders, (
        f"a run that contacted one of two hosts claimed convergence: {offenders}"
    )


# --------------------------------------------------------------------------- #
# The POSITIVE CONTROL on the guard itself
# --------------------------------------------------------------------------- #

def test_rc27_does_NOT_fire_when_a_candidate_DOES_answer(tmp_path):
    """🔴 Without this, every assertion above passes against a guard wired ON.

    Same run, same fixture, one thing changed: the probe answers for the nebula
    address. rc 27 must disappear and the local leg's own code must come back —
    so the three tests above are reading a CONDITIONAL branch, not a constant.
    """
    rc, out = _ship(tmp_path, SSH_PROBE_CMD=_probe(tmp_path, answers=NEBULA))
    assert "NO candidate address answered" not in out, out[-2000:]
    assert "NEVER REACHED" not in out, (
        f"the never-reached headline fired on a run that DID reach a host:\n{out[-2000:]}"
    )
    assert rc != 27, (
        f"rc 27 fired although {NEBULA} answered — the guard is unconditional, "
        f"so nothing above is evidence:\n{out[-2000:]}"
    )
    assert rc == 3, (
        f"expected the local leg's own rc 3 (missing $SHIP_REPO) once the remote "
        f"host is reachable, got {rc}:\n{out[-2000:]}"
    )


def test_an_operator_scoped_one_host_run_is_not_rc27(tmp_path):
    """`--no-remote` is a one-host run the operator CHOSE. rc 27 is for a scope
    that was halved without anyone asking. Conflating them would make the new
    code fire on the ordinary local-only workflow.
    """
    rc, out = _ship(tmp_path, "--no-remote", SSH_PROBE_CMD=_probe(tmp_path))
    assert rc != 27, f"--no-remote reported a host as never reached:\n{out[-2000:]}"
    assert "NEVER REACHED" not in out, out[-2000:]


# --------------------------------------------------------------------------- #
# The reason line: the signal the two rival mechanisms disagree about
# --------------------------------------------------------------------------- #

def test_the_probe_surfaces_the_UNDERLYING_reason_each_address_failed(tmp_path):
    """🔴 The measured diagnostic gap. `first_reachable_ssh` captured ssh's stderr
    into `$err` and used it for ONE bit — host-key or not — then discarded it. So
    every remaining mechanism printed the same sentence, `did not answer
    (unreachable)`: a refused port, a timed-out path, and a CLIENT-side ssh
    failure are indistinguishable. During #686 that is precisely why two rival
    mechanisms could not be separated after the fact.

    RED at 6d996aea: the stub's stderr appears nowhere in the output.
    """
    _rc, out = _ship(tmp_path, SSH_PROBE_CMD=_probe(tmp_path))
    assert out.count(STUB_REASON) >= 2, (
        "the probe's own stderr did not reach the operator for both candidates, "
        f"so a failing probe is still undiagnosable:\n{out[-3000:]}"
    )
    for target in (LAN, NEBULA):
        assert f"{STUB_REASON}: {target}" in out, (
            f"no reason reported for {target}; a per-address reason is the point "
            f"— one address can refuse while the other times out:\n{out[-3000:]}"
        )


def test_the_reason_is_ONE_line_per_candidate_and_carries_the_callers_prefix(tmp_path):
    """Two properties one assertion cannot be traded for the other.

    ONE LINE: `drift-check.sh` writes a journal whose every line must open with
    `[`, `===`, `drift-check: ` or two spaces, and a multi-line ssh refusal
    pasted raw would break it mid-message.

    THE CALLER'S PREFIX: the lib is shared, so an unprefixed reason line both
    fails that hygiene rule and attributes the message to whichever program the
    default names rather than the one running.
    """
    _rc, out = _ship(tmp_path, SSH_PROBE_CMD=_probe(tmp_path))
    reasons = [ln for ln in out.splitlines() if STUB_REASON in ln]
    assert len(reasons) == 2, (
        f"expected exactly one reason line per candidate, got {reasons}"
    )
    for ln in reasons:
        assert ln.startswith("ship:"), (
            f"reason line {ln!r} carries no caller prefix — it would fail "
            "drift-check's journal hygiene and name the wrong program."
        )


def test_a_probe_that_says_NOTHING_does_not_print_an_empty_reason_line(tmp_path):
    """A silent failure must not become a content-free `reason:` line.

    An empty reason is worse than none: it reads as "ssh explained itself" while
    carrying nothing, and it is the shape a careless implementation produces.
    """
    silent = tmp_path / "silent-probe"
    write_exec(silent, "exit 255\n")
    _rc, out = _ship(tmp_path, SSH_PROBE_CMD=str(silent))
    assert "NO candidate address answered" in out, out[-2000:]
    bare = [ln for ln in out.splitlines() if ln.rstrip().endswith("reason:")]
    assert not bare, f"an empty reason line was printed: {bare}"
