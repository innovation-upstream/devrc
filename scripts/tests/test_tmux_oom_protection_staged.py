#!/usr/bin/env python3
"""Guards on the STAGED system change `nix/system/apply-tmux-oom-protection.sh`.

WHY A TEST FOR A SCRIPT NOBODY HAS RUN

This file is staged-not-applied by design (CLAUDE.md: Claude cannot
`sudo nixos-rebuild`), so nothing exercises it until the operator does — under
sudo, on the live box, at a moment when they are probably not reading it
closely. That is precisely when a latent hazard costs something. These pin the
two properties that would be expensive to get wrong and are invisible on a
casual read, plus the honesty of the header.

🔴 THESE ARE STRUCTURAL GUARDS ON A SHELL SCRIPT, NOT A TEST OF ITS BEHAVIOUR.
Applying it requires root and a `nixos-rebuild`, so its runtime effect is not
reachable from the suite. Stated here so nobody reads a green run as "the OOM
protection works" — it means "the script does not contain the two mistakes we
know to look for". The claim it CANNOT make is the whole reason the PR body
labels this arm unverified.
"""
from __future__ import annotations

import re
import stat
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "nix/system/apply-tmux-oom-protection.sh"


@pytest.fixture(scope="module")
def text() -> str:
    return SCRIPT.read_text()


def _code_only(text: str) -> str:
    """The script with whole-line `#` comments removed.

    🔴 STRIPPING IS THE POINT, exactly as `scripts/testlib/nix_units.py` argues
    for nix/home.nix: a substring search over this file answers questions about
    its PROSE, not about what it executes, and this script is mostly prose. It
    is not hypothetical — `test_every_oom_score_adj_write_is_NEGATIVE` was
    written against the raw text and FAILED on its first run, on the header's
    own measurement line

        #   $ sh -c 'echo 500 > /proc/self/oom_score_adj'  # rc=0 — RAISING is fine

    which documents the EPERM finding and executes nothing. A guard that cannot
    tell "the script raises a score" from "the script explains that raising a
    score is what an unprivileged process is limited to" is reporting on the
    wrong artifact — and it would have failed OPEN just as easily, had the
    example been a negative number beside a positive write.

    Whole-line only: no line in this script puts a `#` mid-command, and a reader
    that guessed at trailing comments inside shell strings would corrupt the
    `2>/dev/null` and `#{pid}`-style tokens it is trying to read.
    """
    return "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))


def test_the_comment_stripper_can_tell_code_from_prose():
    """Negative + positive control on the reader the guards below depend on.

    Without this, a `_code_only` that returned "" would make every guard below
    pass vacuously — the "reassuring zero from a harness wired to nothing" this
    repo's rules name explicitly.
    """
    sample = "# echo 500 > /proc/self/oom_score_adj\necho -500 > /proc/1/oom_score_adj\n"
    stripped = _code_only(sample)
    assert "echo -500" in stripped, "stripped away real code"
    assert "echo 500" not in stripped, "kept a commented-out line"


def test_the_staged_script_exists_and_is_executable():
    """A staged script the operator cannot run is not staged.

    🔴 DELIBERATELY DOES NOT ASSERT THE SHEBANG. The obvious extra assertion —
    that the file opens with the repo's `/usr/bin/env bash` convention — was
    written here and REMOVED, because it made `scripts/tests/test_runtime_shebangs.py`
    fail: that repo-wide guard scans every `test_*.py` for the literal
    `/usr/bin/env` string, since a shebang a test writes at RUNTIME is invisible
    on the dev-host tier and red only in the nix sandbox, where `/usr/bin/env`
    does not exist.

    This site was a false positive of that scan — the string was being ASSERTED
    about a file that only ever runs under `sudo` on a real NixOS host, never in
    the sandbox. It is still not worth keeping, and the two available workarounds
    are both worse than dropping it:

      * an ALLOWLIST entry — forbidden by that guard's own docstring, twice over:
        "Do not add an ALLOWLIST entry to get green" and 🔴 "Nothing here may be
        `/usr/bin/env`";
      * splitting the literal so the scan cannot see it — the "spelled guard"
        evasion `claude/RULES.md` names, which defeats a guard protecting a real
        hazard in order to keep a cosmetic assertion.

    The executable bit is the property that actually matters for a staged script
    and is checked below. Shebang correctness is left to the reader and to
    `patchShebangs`.
    """
    assert SCRIPT.is_file(), f"{SCRIPT} is missing"
    assert stat.S_IMODE(SCRIPT.stat().st_mode) & 0o111, f"{SCRIPT} is not executable"


def test_every_oom_score_adj_write_is_NEGATIVE(text: str):
    """🔴 THE DISPLACEMENT DECISION, pinned as a value and not as a sentence.

    `oom_score_adj` is a RANKING: tmux can be moved up the survival order either
    by LOWERING its own score or by RAISING everything else's. The rejected
    design did the latter — `OOMScoreAdjust=+N` on the `tmux-spawn-*.scope`
    units — which reaches the same ordering by making the operator's live Claude
    CONVERSATIONS more killable. That is the trade this whole change exists to
    avoid, and it is one plus sign away from the shipped one.

    So: assert the SIGN of every value the script writes. A positive write is a
    silent inversion of the design — the script would still run, still report
    success, and quietly make the panes the preferred victims.
    """
    writes = re.findall(r"echo\s+(-?\d+)\s*>\s*/proc/", _code_only(text))
    assert writes, "found no oom_score_adj write at all — has the script changed shape?"
    for value in writes:
        assert int(value) < 0, (
            f"script writes a NON-NEGATIVE oom_score_adj ({value}). Raising a score makes "
            "the process MORE likely to be killed; the design lowers tmux's own and "
            "deliberately touches nothing else."
        )


def test_the_floor_leaves_tmux_KILLABLE(text: str):
    """-1000 means 'never kill this', and that is not what was argued for.

    The header commits to leaving the server reclaimable in case it is ever
    itself the runaway (unbounded scrollback). -1000 disables OOM killing for
    the process entirely and would turn a fat tmux server into an unkillable
    one — a different, worse failure than the one being prevented.
    """
    writes = [int(v) for v in re.findall(r"echo\s+(-?\d+)\s*>\s*/proc/", _code_only(text))]
    for value in writes:
        assert value > -1000, (
            f"oom_score_adj={value} makes the process OOM-IMMUNE. The design "
            "disfavours it; it does not exempt it."
        )


def test_process_selection_never_uses_a_full_commandline_pattern(text: str):
    """🔴 RULES.md: "Never let a `-f` pattern reach `pkill`" — and `pgrep -f`
    matches the caller's own shell.

    This script iterates processes and writes to /proc, on a box that routinely
    has 40+ agents and sibling worktrees running. A `-f` pattern here would
    select by full command line and could match an unrelated process — or the
    script's own shell — and a `pkill` would make that fatal rather than a no-op.
    The shipped selection is `pgrep -u 1000 -x tmux`: an EXACT process-NAME
    match, re-confirmed against /proc/<pid>/comm before each write.
    """
    code = _code_only(text)
    assert "pkill" not in code, "this script must never invoke pkill"
    assert re.search(r"pgrep\b[^\n]*\s-x\s", code), (
        "process selection must use `pgrep -x` (exact NAME match)"
    )
    for match in re.finditer(r"pgrep\b([^\n]*)", code):
        flags = match.group(1)
        assert not re.search(r"(?<![-\w])-\w*f", flags), (
            f"`pgrep -f` selects on the FULL command line and matches the caller's own "
            f"shell: {match.group(0).strip()!r}"
        )


def test_the_header_states_plainly_that_the_incident_was_NOT_an_oom_kill(text: str):
    """🔴 The reason this file could mislead, pinned so a later edit cannot quietly
    drop it.

    The script's own name promises OOM protection, and it was commissioned under
    a hypothesis the evidence then falsified. An operator who applies it while
    believing it addresses the 2026-09-07 outage has been given false assurance
    about a still-open risk, and would have no reason to look at the guard that
    IS the fix. The header must therefore keep carrying: the falsified verdict,
    the instrument-validated zero behind it, and the pointer to the real fix.
    """
    head = text[: text.index("set -euo pipefail")]
    assert "DOES NOT ADDRESS" in head
    assert "THAT HYPOTHESIS IS FALSE" in head
    assert "TMUX_TMPDIR" in head, "must name the actual cause"
    assert "check_tmux_kill_shared_server" in head, "must point at the real fix"
    assert "2026-08-28" in head, (
        "must keep the POSITIVE CONTROL that makes the zero readable — the date of "
        "the real kernel OOM kills the same grep does match"
    )


def test_the_header_states_why_home_manager_cannot_do_this(text: str):
    """The next reader's most likely 'simplification' is moving this into
    nix/home.nix, where it would silently do nothing: an unprivileged process
    cannot LOWER oom_score_adj (EPERM without CAP_SYS_RESOURCE), and the tmux
    server is not in a home-manager unit at all — it sits in a per-login
    `session-N.scope`. Both measurements must stay in the file that would be
    deleted by that change.
    """
    head = text[: text.index("set -euo pipefail")]
    assert "CAP_SYS_RESOURCE" in head
    assert "Permission denied" in head
    assert "session-3.scope" in head or "session-N.scope" in head
