#!/usr/bin/env python3
"""`scripts/cairn-receipt.sh` — the PATH `cairn` wrapper that records one row.

🔴 WHICH OF THESE ARE REGRESSION TESTS, STATED HONESTLY — because most are NOT,
and counting them as such would overstate what this file proves
(`claude/RULES.md`: a guard's description claims coverage).

  REGRESSION (watched RED on the pre-fix script, for its OWN reason):
    `test_a_SUBSTITUTED_wrapper_does_not_refuse`
  The first cut of the placeholder guard spelled `@CAIRN_REAL@` literally in its
  own `case` pattern. `substitute` rewrites EVERY occurrence, so the deployed
  file's pattern became the real store path — which the value also was — and the
  guard matched itself. MEASURED on the built derivation: exit **70** on a
  correctly substituted wrapper, i.e. `cairn` broken outright for every caller.
  🔴 THE SMOKE TEST THAT PRECEDED IT COULD NOT SEE THIS, and that is the reusable
  part: it exercised the file IN THE CHECKOUT, where the placeholder is still a
  placeholder and the guard works. Only the SUBSTITUTED artifact is the thing that
  ships. This test therefore performs the substitution itself.

  NEW-FEATURE TESTS (everything else). On pre-change code they fail by the script
  not existing, which is one reason for every one of them and therefore
  discriminates between none — deliberately not described as a red-at-base matrix.

  INVARIANT GUARDS (they pin behaviour this change must not alter):
    `test_stdout_is_byte_identical_to_the_client`
    `test_stderr_is_byte_identical_to_the_client`
    `test_the_exit_code_is_the_clients`

⚠ WHAT THIS FILE CANNOT SEE. It never runs the real cairn client and never writes
to the real spool: every case points `CAIRN_RECEIPT_REAL_BIN` at a stub and
`ACTIVITY_SPOOL_DIR` at a tmp dir. So it says nothing about the client's own
behaviour, and nothing about whether the collector daemon ships the row — that is
the daemon's contract, out of band. The end-to-end claim (built derivation, real
client, real `emit`) was made by hand and is recorded in the PR, not here.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "cairn-receipt.sh"
NIX_HOME = REPO / "nix" / "home.nix"

#: The literal the deploy substitutes. Assembled from two pieces for the SAME
#: reason the script itself does it: a test file is not substituted, but spelling
#: it whole here invites a copy-paste into a file that is.
PLACEHOLDER = "@CAIRN" + "_REAL@"

STUB = """#!/usr/bin/env bash
printf 'STDOUT:%s\\n' "$*"
printf 'STDERR-line\\n' >&2
exit "${STUB_RC:-0}"
"""

#: A fake `emit` that records its own argv, so a test can read the fields the
#: wrapper passed WITHOUT re-implementing the v1 line format.
EMIT_STUB = """#!/usr/bin/env bash
printf '%s\\n' "$@" >> "$EMIT_LOG"
exit 0
"""


def _write_exec(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)
    return path


@pytest.fixture
def world(tmp_path):
    """A stub client, a stub emit, a tmp spool, and BOTH forms of the wrapper:
    the checkout file and the SUBSTITUTED one the deploy actually ships."""
    stub = _write_exec(tmp_path / "stub-cairn", STUB)
    emit = _write_exec(tmp_path / "stub-emit", EMIT_STUB)
    emit_log = tmp_path / "emit.log"

    raw = SCRIPT.read_text(encoding="utf-8")
    assert PLACEHOLDER in raw, (
        f"{SCRIPT} no longer carries the {PLACEHOLDER} placeholder, so "
        "nix/home.nix's substitution has nothing to replace and the deployed "
        "wrapper would refuse at runtime")
    substituted = _write_exec(
        tmp_path / "cairn-substituted", raw.replace(PLACEHOLDER, str(stub)))

    env = dict(os.environ)
    env.update({
        "ACTIVITY_SPOOL_DIR": str(tmp_path / "spool"),
        "CAIRN_RECEIPT_EMIT_BIN": str(emit),
        "EMIT_LOG": str(emit_log),
    })
    env.pop("CAIRN_RECEIPT_DISABLE", None)
    return {
        "stub": stub, "emit_log": emit_log, "env": env,
        "substituted": substituted, "checkout": SCRIPT, "tmp": tmp_path,
    }


def _run(script, args, env, rc=None):
    e = dict(env)
    if rc is not None:
        e["STUB_RC"] = str(rc)
    return subprocess.run([str(script), *args], capture_output=True, env=e, timeout=60)


def _emitted(world):
    """The argv `emit` was called with, as a dict, or {} if it was never called.

    🔴 VALUES ARE PLAINTEXT, AND THAT IS THE CORRECT BOUNDARY. `scripts/collector/
    emit` base64-encodes `b64:` values ITSELF (verified by decoding a real spool
    line), so what the wrapper hands it — and therefore what the stub records — is
    the raw string. Decoding here would be testing emit's contract, which emit's
    own consumers already pin; this file's boundary is what the WRAPPER passes.
    """
    if not world["emit_log"].exists():
        return {}
    out = {}
    for line in world["emit_log"].read_text(encoding="utf-8").splitlines():
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        if k.startswith("b64:"):
            out[k[4:]] = v
        else:
            out[k] = v
    return out


# --------------------------------------------------------------------------- #
# The regression
# --------------------------------------------------------------------------- #
def test_a_SUBSTITUTED_wrapper_does_not_refuse(world):
    """🔴 THE ONE REGRESSION HERE. The guard that recognises an unsubstituted
    placeholder must not recognise a SUBSTITUTED one. Pre-fix this exited 70 with
    `the real-client path was never substituted`, naming the correct store path in
    the same breath — because `substitute` had rewritten the guard's own pattern.
    """
    p = _run(world["substituted"], ["recall", "--scope", "devrc"], world["env"])
    assert p.returncode == 0, (
        "the substituted wrapper refused to run the client.\n"
        f"rc={p.returncode}\nstderr={p.stderr.decode()!r}")
    assert b"never substituted" not in p.stderr, p.stderr
    assert p.stdout.startswith(b"STDOUT:"), p.stdout


def test_an_UNSUBSTITUTED_wrapper_still_REFUSES(world):
    """The other half of the same guard, and the reason it exists: run from the
    checkout there is no client to run, and a silent fallback to `cairn` on PATH
    would make the wrapper invoke ITSELF — unbounded recursion, not a degraded
    mode. Env is scrubbed of the override so the placeholder is reached."""
    env = dict(world["env"])
    env.pop("CAIRN_RECEIPT_REAL_BIN", None)
    p = _run(world["checkout"], ["recall"], env)
    assert p.returncode == 70, (p.returncode, p.stderr.decode())
    assert b"never substituted" in p.stderr
    assert p.stdout == b"", "a refusal must put nothing on stdout"


def test_a_real_bin_that_is_not_executable_REFUSES_rather_than_half_running(world):
    """A deleted or non-executable client is a refusal, not a confusing exec
    failure part-way through. Same exit code: both mean "no client ran"."""
    env = dict(world["env"])
    env["CAIRN_RECEIPT_REAL_BIN"] = str(world["tmp"] / "does-not-exist")
    p = _run(world["checkout"], ["recall"], env)
    assert p.returncode == 70, (p.returncode, p.stderr.decode())
    assert p.stdout == b""


# --------------------------------------------------------------------------- #
# Contract 1 — the client's streams and status are untouched
# --------------------------------------------------------------------------- #
def test_stdout_is_byte_identical_to_the_client(world):
    """`read.sh`'s header pins "STDOUT IS THE CLIENT'S, BYTE FOR BYTE" because
    /resume, /handoff and /analyze-service all diff recall output against prior
    captures. Compared against the stub run DIRECTLY, not against a literal."""
    args = ["recall", "--repo", "/tmp/x", "--scope", "devrc"]
    via = _run(world["substituted"], args, world["env"])
    direct = subprocess.run([str(world["stub"]), *args],
                            capture_output=True, env=world["env"], timeout=60)
    assert via.stdout == direct.stdout, (via.stdout, direct.stdout)


def test_stderr_is_byte_identical_to_the_client(world):
    args = ["recall", "--scope", "devrc"]
    via = _run(world["substituted"], args, world["env"])
    direct = subprocess.run([str(world["stub"]), *args],
                            capture_output=True, env=world["env"], timeout=60)
    assert via.stderr == direct.stderr, (via.stderr, direct.stderr)


@pytest.mark.parametrize("rc", [0, 1, 2, 20, 70, 126])
def test_the_exit_code_is_the_clients(world, rc):
    """Every documented cairn/read.sh code must survive, 70 and 126 included —
    those collide with the wrapper's OWN refusal code and with a shell exec
    failure, so a wrapper that invented a status would be indistinguishable here.
    """
    p = _run(world["substituted"], ["recall"], world["env"], rc=rc)
    assert p.returncode == rc, (rc, p.returncode, p.stderr.decode())


# --------------------------------------------------------------------------- #
# Contract 4 — no free text, enforced by charset
# --------------------------------------------------------------------------- #
def test_a_search_QUERY_never_reaches_the_telemetry(world):
    """🔴 THE SAFETY PROPERTY, WITH ITS POSITIVE CONTROL IN THE SAME TEST. A zero
    here is otherwise indistinguishable from a reader wired to nothing: the
    control asserts the SAME haystack-building code CAN see the secret."""
    secret = "hunter2-SUPERSECRET-querytext"
    _run(world["substituted"], ["search", secret, "--scope", "devrc"], world["env"])
    fields = _emitted(world)
    assert fields, "emit was never called, so this test proves nothing"

    blob = "\n".join(fields.values())
    assert secret not in blob, f"the query reached telemetry: {fields!r}"

    # POSITIVE CONTROL: the same assembly over a field set that DOES carry the
    # secret must see it. Without this, a `fields` dict that silently lost its
    # values would pass the assertion above while proving nothing.
    control = "\n".join({**fields, "payload": f'{{"q":"{secret}"}}'}.values())
    assert secret in control, "the control cannot see the secret — the check above is vacuous"


def test_the_verb_comes_from_argv1_ONLY(world):
    """So a query can never be promoted to the verb. `cairn --scope devrc recall`
    records an EMPTY verb rather than scanning forward for the first non-flag,
    which is what would make `search <query>`'s operand reachable."""
    _run(world["substituted"], ["--scope", "devrc", "recall"], world["env"])
    fields = _emitted(world)
    payload = fields["payload"]
    assert '"verb":""' in payload, payload


def test_the_scope_is_recorded_but_stripped_to_its_charset(world):
    """The expectation is DERIVED FROM THE CHARSET RULE, not read off the output.
    `_safe` keeps `[A-Za-z0-9._/-]`, so `dev rc;rm -rf /` keeps `dev`, `rc`, `rm`,
    `-rf` and `/` and drops the two spaces and the `;` -> `devrcrm-rf/`.
    ⚠ My first draft of this test asserted `devrc-rf/`, silently dropping `rm`;
    the script was right and the guess was wrong. Recomputing from the rule is
    what caught it — reading the actual output and pasting it back would have
    pinned whatever the code happened to do.
    """
    _run(world["substituted"], ["recall", "--scope", "dev rc;rm -rf /"], world["env"])
    payload = _emitted(world)["payload"]
    assert '"scope":"devrcrm-rf/"' in payload, payload


# --------------------------------------------------------------------------- #
# Contract 2 — fail-open
# --------------------------------------------------------------------------- #
def test_a_MISSING_emit_is_a_silent_noop(world):
    """Telemetry must never be able to break `cairn`. An absent emit leaves the
    client's streams and status exactly as they were."""
    env = dict(world["env"])
    env["CAIRN_RECEIPT_EMIT_BIN"] = str(world["tmp"] / "no-such-emit")
    p = _run(world["substituted"], ["recall"], env)
    assert p.returncode == 0
    assert p.stdout.startswith(b"STDOUT:")
    assert b"no-such-emit" not in p.stderr, "telemetry leaked a diagnostic to stderr"


def test_a_DIRECTORY_at_the_emit_path_is_also_a_silent_noop(world):
    """`-x` is true for a directory, so this is the case a `-f`/`-x` mix-up would
    turn into a failed exec on every cairn call."""
    d = world["tmp"] / "emit-is-a-dir"
    d.mkdir()
    env = dict(world["env"])
    env["CAIRN_RECEIPT_EMIT_BIN"] = str(d)
    p = _run(world["substituted"], ["recall"], env)
    assert p.returncode == 0, p.stderr.decode()
    assert p.stderr == b"STDERR-line\n", p.stderr


# --------------------------------------------------------------------------- #
# The row's shape, and the deploy
# --------------------------------------------------------------------------- #
def test_the_row_is_source_tool_kind_invocation_with_cairn_in_text(world):
    """`source='tool' kind='invocation'` is the EXISTING adoption signal read by
    `scripts/session-analysis/adoption-scan.py`; `text` carries the tool name as
    well as the payload so a consumer can group without parsing JSON, which is
    `scripts/collector/invocation.py::build_fields`'s own choice."""
    _run(world["substituted"], ["recall", "--scope", "devrc"], world["env"])
    f = _emitted(world)
    assert f.get("source") == "tool", f
    assert f.get("kind") == "invocation", f
    assert f["text"] == "cairn", f
    assert f.get("exit_code") == "0", f


def test_the_outcome_is_error_on_a_nonzero_client(world):
    _run(world["substituted"], ["recall"], world["env"], rc=3)
    payload = _emitted(world)["payload"]
    assert '"outcome":"error"' in payload, payload
    assert _emitted(world)["exit_code"] == "3"


def test_home_nix_deploys_the_wrapper_and_asserts_its_placeholder():
    """The deploy must (a) point `.local/bin/cairn` at the wrapper derivation and
    (b) FAIL THE BUILD when the placeholder is gone — `--subst-var-by` does not
    fail on a missing placeholder, so without that grep a rename would ship a
    wrapper that refuses at runtime on every call."""
    nix = NIX_HOME.read_text(encoding="utf-8")
    assert 'home.file.".local/bin/cairn".source = cairnWithReceipt;' in nix
    assert "cairn-receipt.sh" in nix
    assert f"grep -q '{PLACEHOLDER}'" in nix, (
        "the derivation no longer asserts the placeholder exists before "
        "substituting it")
