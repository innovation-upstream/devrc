#!/usr/bin/env python3
"""GUARD 9b's predicate, pinned against REAL marker lines.

🔴 WHY THIS FILE EXISTS. The first version of this gate built an ERE containing
the marker text `gitenv(session)`. Those PARENTHESES are an ERE group, so the
pattern matched the literal string "gitenvsession", never hit, and reported a
co-tenant count of ZERO — "nothing else writes to this repo" — on a box whose
sessions had just printed `unattributable=1` with four named processes. The gate
then fired on a healthy target and made the whole run red.

That failure is invisible to every kind of testing except this one: the pipeline
ran, exited 0, and produced a plausible number. Only feeding it a line that MUST
produce a non-zero count, and watching the number move, distinguishes "no
co-tenants" from "my pattern cannot match".

So each case below carries BOTH halves — a fixture that must trip the gate and
one that must not — and the fixtures are copied from real run output rather than
hand-idealised, because the whole bug was in the shape of the real text.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
RUN_TESTS = REPO_ROOT / "scripts" / "run-tests.sh"

# Copied verbatim from a real gate run, parentheses and all — the exact shape
# the broken pattern could not match.
SESSION_CLEAN = ("gitenv(session) stripped=none protected-git-dirs=2 "
                 "mode=enforce(auto) unattributable=0")
SESSION_COTENANT = ("gitenv(session) stripped=none protected-git-dirs=2 "
                    "mode=report(auto) unattributable=1")
COTENANT_DETAIL = ("gitenv(session)   cannot attribute: live processes are "
                   "sitting inside a protected repository (cwd), and none of "
                   "them is ours: 97043:nvidia-smi, 143880:.claude-wrapped")
SUMMARY_QUIET = ("gitenv(session) summary: attributed-violations=0 "
                 "unattributed-observations=0 mode=enforce(auto)")
SUMMARY_OBSERVED = ("gitenv(session) summary: attributed-violations=0 "
                    "unattributed-observations=2 mode=report(auto)")


def _extract(name: str) -> str:
    src = RUN_TESTS.read_text(encoding="utf-8")
    m = re.search(rf"^{re.escape(name)}\(\) \{{\n(.*?)^\}}\n", src,
                  re.MULTILINE | re.DOTALL)
    assert m, f"could not find `{name}()` in {RUN_TESTS} — update the extractor"
    return f"{name}() {{\n{m.group(1)}}}\n"


def _verdict(tmp_path: Path, lines: list[str]) -> str:
    log = tmp_path / "target.log"
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    script = (
        "set -u\n"
        'GITENV_SESSION_MARKER="gitenv(session)"\n'
        + _extract("_gitenv_unattributed_verdict")
        + f'_gitenv_unattributed_verdict "{log}"\n'
    )
    out = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                         timeout=30)
    assert out.returncode == 0, out.stderr
    return out.stdout.strip().splitlines()[-1]


def test_clean_session_with_no_observations_passes(tmp_path: Path) -> None:
    assert _verdict(tmp_path, [SESSION_CLEAN, SUMMARY_QUIET]) == "OK"


def test_clean_session_WITH_an_observation_fails(tmp_path: Path) -> None:
    """The state the gate exists for: the detector saw a change and could not
    pin it on a test, with no co-tenant to excuse the downgrade."""
    assert _verdict(tmp_path, [SESSION_CLEAN, SUMMARY_OBSERVED]) == "FAIL 2"


def test_a_PROVEN_cotenant_excuses_the_observation(tmp_path: Path) -> None:
    """🔴 THE REGRESSION CASE. This is the one the broken ERE got wrong, and
    getting it wrong makes the gate permanently red on any developer box."""
    verdict = _verdict(tmp_path,
                       [SESSION_COTENANT, COTENANT_DETAIL, SUMMARY_OBSERVED])
    assert verdict == "OK", (
        "a session that PROVED another writer must not be failed for an "
        "observation it already explained — this is the pattern-cannot-match "
        "bug that shipped a red gate"
    )


def test_one_cotenant_session_among_several_still_excuses(tmp_path: Path) -> None:
    """Under xdist each worker prints its own line; any one proving a co-tenant
    means the repo demonstrably has another writer for the whole target."""
    assert _verdict(tmp_path, [SESSION_CLEAN, SESSION_COTENANT,
                               SUMMARY_OBSERVED, SUMMARY_OBSERVED]) == "OK"


def test_observations_are_SUMMED_across_worker_sessions(tmp_path: Path) -> None:
    assert _verdict(tmp_path, [SESSION_CLEAN, SESSION_CLEAN,
                               SUMMARY_OBSERVED, SUMMARY_OBSERVED]) == "FAIL 4"


def test_the_marker_is_matched_LITERALLY_not_as_a_regex(tmp_path: Path) -> None:
    """The positive control for the actual defect, stated as its own case.

    `gitenv(session)` read as an ERE matches "gitenvsession". If the
    implementation ever goes back to `grep -E` on the marker, the co-tenant line
    below stops being seen, the count silently becomes 0, and this returns FAIL.
    """
    assert _verdict(tmp_path, [SESSION_COTENANT, SUMMARY_OBSERVED]) == "OK"
    # ...and the same log with the parens removed must NOT be read as a session
    # line, which is what proves the match is literal rather than incidental.
    assert _verdict(tmp_path, [SESSION_COTENANT.replace("gitenv(session)",
                                                        "gitenvsession"),
                               SUMMARY_OBSERVED]) == "FAIL 2"


@pytest.mark.parametrize("n", [1, 2, 7])
def test_the_reported_count_is_the_real_one(tmp_path: Path, n: int) -> None:
    summary = SUMMARY_OBSERVED.replace("unattributed-observations=2",
                                       f"unattributed-observations={n}")
    assert _verdict(tmp_path, [SESSION_CLEAN, summary]) == f"FAIL {n}"
