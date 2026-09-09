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
import subprocess
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

    🔴 SCOPE, because the earlier version of this docstring claimed more than
    the body checked: this asserts the SHAPE of the selection flags only. That
    the pattern actually MATCHES a tmux server is a different claim, made by
    `test_the_pgrep_pattern_matches_a_REALISTIC_comm` below — and it is a claim
    this test previously certified the OPPOSITE of, by pinning `-x` while the
    shipped pattern was `-x tmux`, which matches nothing.
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


# 🔴 REALISTIC, not tidied. MEASURED on the workbench 2026-09-09:
#     cat /proc/1111077/comm  -> tmux: server
#     cat /proc/1292943/comm  -> tmux: client
# The tmux server renames itself; its `comm` is not `tmux`. A fixture that said
# `tmux` would be a fixture the bug cannot fail against — the whole point of
# this pair is that the shipped selector must work on the string the KERNEL
# reports, not on the one the process is called after.
_REAL_SERVER_COMM = "tmux: server"
_REAL_CLIENT_COMM = "tmux: client"


def _pgrep_patterns(text: str) -> list[str]:
    """Every `-x` pattern the script hands pgrep, quoted or bare."""
    code = _code_only(text)
    return re.findall(r"pgrep\b[^\n]*?\s-x\s+(?:'([^']+)'|\"([^\"]+)\"|(\S+))", code)


def test_the_pgrep_pattern_matches_a_REALISTIC_comm(text: str):
    """🔴 THE BLOCKER THIS FILE EXISTED AND DID NOT CATCH.

    The first draft shipped `pgrep -u 1000 -x tmux`. `-x` is an anchored,
    exact match against /proc/<pid>/comm, and the tmux SERVER's comm is
    `tmux: server` — so it matched NOTHING. The loop body never ran, the unit
    printed `adjusted 0`, exited 0, and systemd reported `active (exited)` every
    two minutes forever. The only detector was a human reading the journal.

    Measured on the workbench, and reproducible:
        pgrep -u 1000 -x tmux             -> rc=1, no output
        pgrep -u 1000 -x 'tmux: server'   -> 1111077, rc=0

    It was invisible to the whole battery: mutating the selector to
    `-x zzz-no-such-proc` scored SURVIVED, because nothing here evaluated the
    pattern against a process name at all. This test does, against the value the
    kernel actually reports.
    """
    patterns = [next(g for g in groups if g) for groups in _pgrep_patterns(text)]
    assert patterns, "no `pgrep -x <pattern>` found — has the script changed shape?"
    for pat in patterns:
        assert re.fullmatch(pat, _REAL_SERVER_COMM), (
            f"`pgrep -x {pat!r}` does NOT match the real server comm "
            f"{_REAL_SERVER_COMM!r} — the unit would select nothing and report "
            "success forever"
        )
        assert not re.fullmatch(pat, _REAL_CLIENT_COMM), (
            f"`pgrep -x {pat!r}` also matches a tmux CLIENT ({_REAL_CLIENT_COMM!r}); "
            "the design lowers the SERVER's badness and touches nothing else"
        )


def test_the_realistic_comm_fixture_can_actually_fail_the_selector_check():
    """🔴 NEGATIVE CONTROL on the test above — it must be able to go red.

    A fixture that any plausible pattern matches would make the assertion
    vacuous. The exact pattern that shipped and was inert is the control: it
    must FAIL against the realistic comm, and it must pass against the tidied
    one nobody should write. If this ever goes green for `tmux`, the fixture has
    been "cleaned up" and the guard above has stopped testing anything.
    """
    assert not re.fullmatch("tmux", _REAL_SERVER_COMM), (
        "the realistic fixture now matches the broken pattern — it has been tidied")
    assert re.fullmatch("tmux", "tmux"), "sanity: fullmatch works as assumed"
    assert re.fullmatch("tmux: server", _REAL_SERVER_COMM)


def test_the_comm_recheck_in_the_loop_uses_the_same_realistic_value(text: str):
    """One rule, one place: the pgrep pattern and the /proc re-read must agree.

    The re-read exists because a pid can be recycled between `pgrep` and the
    write. It is only a guard if it names the same identity — the version this
    replaces re-checked a `tmux*` PREFIX, which also accepts `tmux: client`.
    """
    code = _code_only(text)
    assert _REAL_SERVER_COMM in code, (
        f"the loop body no longer compares comm against {_REAL_SERVER_COMM!r}")
    assert 'comm" != "tmux: server"' in code or "comm\" != \"tmux: server\"" in code, (
        "the /proc/<pid>/comm re-read must be an EXACT comparison, not a prefix")


def test_an_empty_match_set_is_not_allowed_to_be_the_silent_path(text: str):
    """🔴 The runtime half of the blocker: `found 0` must not be able to hide.

    Two mechanisms, and BOTH are needed because neither covers the other:

      * APPLY TIME — the script runs the selector itself, under sudo, at the one
        moment a human is watching and the server is certainly up, and refuses
        to install when it matches nothing. This is the positive control the
        every-two-minutes unit structurally cannot do.
      * RUN TIME — the unit counts what it FOUND separately from what it
        ADJUSTED and exits non-zero when it found servers it could not lower.
        `found 0` is deliberately NOT escalated there: after a clean shutdown
        there is genuinely no server, and failing on that would toast four times
        an hour forever — a permanently-red gate being worse than no gate.
    """
    code = _code_only(text)
    assert re.search(r"if\s*!\s*pgrep\b", code), (
        "the apply step must run the selector as a pre-flight positive control "
        "and refuse to install an inert unit")
    assert "found=0" in code and "adjusted=0" in code, (
        "the unit must count FOUND separately from ADJUSTED — a single counter "
        "cannot tell 'no server running' from 'selector broken'")
    assert re.search(r'"\$found"\s*-gt\s*0', code) and re.search(r"exit 1", code), (
        "found-but-not-adjusted must exit non-zero")


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


# --------------------------------------------------------------------------- #
# 🔴 BLOCKER 2 — the import wiring, and the script it is borrowed from.
# --------------------------------------------------------------------------- #

AIRVPN = REPO / "nix/system/apply-airvpn-host.sh"

# The two shapes an `imports =` assignment takes in a NixOS config. The second
# is what /etc/nixos/configuration.nix ACTUALLY uses on the only host this
# script targets — measured 2026-09-09 at lines 23-24:
#     imports =
#       [
#         ./agent-sudo.nix …
# The first draft's awk required `[` on the SAME line as `imports =`, so it
# matched nothing, inserted nothing, and exited 1 at its own grep guard. Right
# direction (closed, loud) — but it could not complete anywhere it would be run.
_IMPORTS_SPLIT = "{\n  imports =\n    [\n      ./a.nix\n    ];\n}\n"
_IMPORTS_ONE_LINE = "{\n  imports = [ ./a.nix ];\n}\n"


def _extract_awk(text: str) -> str:
    """The awk program between `awk '` and the closing `'`."""
    m = re.search(r"awk '\n(.*?)\n\s*' ", text, re.S)
    assert m, "could not find the import-wiring awk program"
    return m.group(1)


def test_the_import_wiring_splits_on_the_BRACKET_not_the_line(text: str):
    """🔴 The blocker, pinned as the property rather than as a comment.

    Splitting on the `[` CHARACTER keeps the new entry inside the list for BOTH
    spellings; arming on the whole `imports = [` line handles only one of them,
    and not the one on disk here.
    """
    awk = _extract_awk(text)
    assert 'index($0, "[")' in awk, (
        "the wiring must find the list-opener `[` as a CHARACTER — a regex "
        "requiring it on the `imports =` line does not match this host")
    assert "imports[[:space:]]*=" in awk, "must still arm on the imports assignment"


def test_the_import_wiring_is_the_SAME_logic_as_the_script_it_was_taken_from():
    """🔴 ONE RULE, ONE PLACE — enforced across two standalone sudo scripts.

    These scripts cannot import from each other: each is run as
    `sudo bash nix/system/apply-<x>.sh` on a live host, sometimes from outside
    the repo, so a shared library would be one more thing that has to be there.
    The duplication is deliberate; what is NOT acceptable is the two copies
    silently diverging, which is exactly how this PR shipped a broken one beside
    a working one that had solved the same problem months earlier.

    So: pin that both carry the same load-bearing pieces — the `[`-character
    split AND the multiple-`imports =` guard the first draft dropped. Identity of
    BEHAVIOUR, checked structurally; a fix to one is now visible from the other.

    🔴 EACH FRAGMENT IS A SEPARATE CLAIM, DELIBERATELY. An earlier version of
    this list asked only for the token `n_imports`, and a mutation that DELETED
    the counting line entirely SURVIVED — the token still appeared in the `if`
    that compared it, so the guard was gone while the test read as covering it.
    A guard on a word is walkable; these name the count, the comparison and the
    refusal separately, so removing any one of the three goes red.
    """
    ours = SCRIPT.read_text()
    theirs = AIRVPN.read_text()
    for name, needle in [
        ("the `[`-character split", 'index($0, "[")'),
        ("the awk arm on the imports assignment", "imports[[:space:]]*="),
        ("the imports-assignment COUNT",
         "grep -cE '^[[:space:]]*imports[[:space:]]*='"),
        ("the COMPARISON that makes the count a guard", '"${n_imports}" != "1"'),
        ("the REFUSAL when the count is not 1", "Refusing to guess"),
        ("a non-zero exit on that refusal", "exit 1"),
        ("inode-preserving overwrite", "cat "),
    ]:
        assert needle in theirs, f"apply-airvpn-host.sh no longer has {name} — re-derive"
        assert needle in ours, (
            f"apply-tmux-oom-protection.sh is missing {name}, which "
            f"apply-airvpn-host.sh has. Do not reimplement it — copy it.")


@pytest.mark.parametrize("config,label", [
    (_IMPORTS_SPLIT, "opener on its own line (THIS HOST)"),
    (_IMPORTS_ONE_LINE, "opener on the imports line"),
])
def test_the_wiring_awk_actually_inserts_in_both_config_shapes(tmp_path, config, label):
    """🔴 RUN the awk, do not read it. MEASURED at two points, per RULES.md.

    A substring check on the program text says the right characters are present.
    Executing it against both spellings says it works — and the split shape is
    the one on disk, so this is a real reproduction of the blocker rather than a
    tidied stand-in.
    """
    src = tmp_path / "configuration.nix"
    src.write_text(config)
    out = subprocess.run(["awk", _extract_awk(SCRIPT.read_text()), str(src)],
                         capture_output=True, text=True, check=True).stdout
    assert "./tmux-oom-protection.nix" in out, f"no insertion for {label}: {out!r}"
    # inside the list, not after the closing `];`
    assert out.index("./tmux-oom-protection.nix") < out.index("];"), (
        f"inserted OUTSIDE the imports list for {label}: {out!r}")


def test_the_OLD_wiring_regex_is_the_negative_control(tmp_path):
    """🔴 The mutation that proves the test above is not vacuous.

    The shipped-and-broken program, run against the shape that is actually on
    disk, must produce NO insertion. If this ever passes, the fixture has been
    tidied into a shape the bug could not fail against.
    """
    broken = ('!done && /imports[[:space:]]*=[[:space:]]*\\[/ {\n'
              '  print; print "      ./tmux-oom-protection.nix"; done=1; next\n'
              '}\n{ print }')
    src = tmp_path / "configuration.nix"
    src.write_text(_IMPORTS_SPLIT)
    out = subprocess.run(["awk", broken, str(src)],
                         capture_output=True, text=True, check=True).stdout
    assert "./tmux-oom-protection.nix" not in out, (
        "the OLD awk inserted something on the split shape — the fixture no "
        "longer reproduces the blocker")


def test_the_module_is_written_only_after_the_wiring_succeeds(text: str):
    """A refused wiring must not leave an orphan /etc/nixos module behind.

    The first draft wrote $MODULE first, so every failure path left an inert
    tmux-oom-protection.nix in /etc/nixos that the next reader had to work out
    was unreferenced.
    """
    code = _code_only(text)
    wiring = code.index("n_imports")
    module_write = code.index('cat > "$MODULE"')
    assert wiring < module_write, (
        "the module is written before the import wiring is attempted — a refused "
        "wiring leaves an orphan file in /etc/nixos")


def test_the_backup_does_not_accumulate_one_file_per_run(text: str):
    """/etc/nixos already holds 18 `configuration.nix.bak*` files.

    A script whose header advertises it as idempotent must not add a
    nineteenth, a twentieth and a twenty-first. A FIXED backup name (the choice
    apply-airvpn-host.sh made) is overwritten on re-run; a timestamped one is
    not. Also: the backup is taken only on the branch that actually edits the
    file, so a no-op re-run writes nothing at all.
    """
    code = _code_only(text)
    assert not re.search(r"date\s+\+%Y", code), (
        "the backup name is timestamped — every run leaves another "
        "configuration.nix.bak-* in /etc/nixos")
    assert 'BACKUP="${CFG}.bak.tmux-oom"' in code, "expected a single fixed backup name"


def test_the_config_is_overwritten_in_place_not_replaced_by_mv(text: str):
    """`mv` swaps the inode and carries the temp file's ownership and mode.

    /etc/nixos/configuration.nix is 0644 root:root and other things watch it.
    apply-airvpn-host.sh:114-115 uses `cat >` for exactly this reason.
    """
    code = _code_only(text)
    assert not re.search(r'\bmv\s+"\$CFG\.new"', code), (
        "`mv` replaces the inode and the mode — use `cat \"$CFG.new\" > \"$CFG\"`")
    assert 'cat "$CFG.new" > "$CFG"' in code


def test_restore_does_not_claim_to_restore_a_backup_that_does_not_exist(text: str):
    """Cosmetic, but it is a false statement read under stress.

    The ERR trap is armed before the backup is taken (it has to be — the backup
    is inside the branch that edits), so on the already-wired path there is
    nothing to restore. Saying "restoring" there and cp'ing a missing file
    tells an operator their config was rolled back when it was never touched.
    """
    code = _code_only(text)
    restore = code[code.index("restore()"):code.index("trap restore ERR")]
    assert '-f "$BACKUP"' in restore, (
        "restore() must check the backup exists before claiming to use it")
    assert "nothing to restore" in restore, (
        "the no-backup branch must say plainly that nothing was rolled back")


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
