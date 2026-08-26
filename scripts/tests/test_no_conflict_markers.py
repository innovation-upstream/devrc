"""No tracked file may carry a git conflict marker.

🔴 WHY THIS EXISTS, measured 2026-08-21: `scripts/tests/test_subsystem_recall.py`
carried a stray `<` * 7 + ` HEAD` line on `main` for several commits (introduced by
`54ebf951`, #645) and NOTHING caught it. It landed INSIDE a docstring, so Python
parsed it as string content, the module imported, the test passed, and a full green
gate said nothing. That is the whole hazard: a botched conflict resolution is only
loud when it lands in code: in a docstring, a markdown file, or a heredoc it is
silent, and the surrounding prose is then two sessions' text concatenated at a seam
nobody reviewed.

This repo has no automated merge gate and several concurrent sessions resolving
conflicts, so "we would notice" is not available as a control.

🔴 THE MARKERS ARE BUILT AT RUNTIME, NEVER WRITTEN AS LITERALS. A guard that spells
the thing it forbids matches ITSELF and then either fails forever or gets an
exemption that also blinds it to the real case. Building them from `"<" * 7` keeps
this file scannable by its own predicate — asserted below by
`test_this_file_is_itself_clean`, which would be vacuous if the literals were here.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

# Built, not spelled — see the module docstring.
_OURS = "<" * 7
_THEIRS = ">" * 7
_SPLIT = "=" * 7


def _marker_lines(text: str) -> list[tuple[int, str]]:
    """Lines that open or close a conflict hunk.

    Only `<<<<<<<` and `>>>>>>>` are flagged. A bare `=======` is deliberately NOT
    flagged on its own: markdown's setext heading underline is a run of `=`, so it
    occurs legitimately and would make this guard permanently red — and a
    permanently-red gate is worse than no gate. Every real conflict carries an
    opening marker anyway, so the hunk is still caught by its first line.
    """
    hits: list[tuple[int, str]] = []
    for n, line in enumerate(text.splitlines(), start=1):
        for marker in (_OURS, _THEIRS):
            if line.startswith(marker) and (len(line) == 7 or line[7] == " "):
                hits.append((n, line[:60]))
    return hits


# Directories a filesystem walk must never descend into. Only reachable on the
# fallback path below; `git ls-files` already excludes every one of them.
_WALK_SKIP_DIRS = frozenset(
    {".git", "__pycache__", ".pytest_cache", "node_modules", "result"}
)


def _tracked_text_files() -> list[Path]:
    """Every tracked text file — by `git ls-files` where that works, else a walk.

    🔴 THE FALLBACK IS NOT A DEGRADATION, it is what makes this guard exist in
    the tier that matters. MEASURED 2026-08-21: this ran `git ls-files` with
    `check=True`, and the nix check sandbox builds from `/build/src` — a COPY of
    the source, not a clone — so git exited 128 and the test ERRORED. This guard
    was therefore structurally inert in the ONLY tier that gates a merge, which
    is precisely the failure it was written to prevent (module docstring: a green
    gate that cannot see the thing it forbids).

    The fallback is faithful exactly where it fires: nix populates the flake
    source from the git tree, so what is on disk in the sandbox IS the tracked
    set. Where git answers we still prefer it, because a dev-host checkout also
    carries ignored and untracked files that are none of this guard's business.

    Either way the caller asserts the denominator, so a fallback that walked
    nothing still cannot pass as a clean tree.
    """
    out = subprocess.run(
        ["git", "-C", str(REPO), "ls-files", "-z"],
        capture_output=True,
        text=True,
        check=False,
    )
    if out.returncode == 0:
        return [REPO / p for p in out.stdout.split("\0") if p]

    found: list[Path] = []
    for path in REPO.rglob("*"):
        if not path.is_file():
            continue
        if any(part in _WALK_SKIP_DIRS for part in path.relative_to(REPO).parts):
            continue
        found.append(path)
    return found


def test_no_tracked_file_carries_a_conflict_marker() -> None:
    offenders: list[str] = []
    examined = 0
    for path in _tracked_text_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
            continue  # binary or a broken symlink — not our business
        examined += 1
        for n, snippet in _marker_lines(text):
            offenders.append(f"{path.relative_to(REPO)}:{n}: {snippet}")

    # 🔴 Print the DENOMINATOR. A zero from a scan that walked nothing is
    # indistinguishable from a clean tree, and this guard's whole value is the
    # zero it reports.
    assert examined > 500, (
        f"the scan only examined {examined} tracked text files, which is far below "
        f"this repo's size — `git ls-files` failed or the filter is wrong, so a "
        f"clean result here would mean nothing"
    )
    assert not offenders, (
        f"{len(offenders)} conflict marker(s) in tracked files "
        f"(of {examined} examined):\n  " + "\n  ".join(offenders)
    )


def test_it_still_scans_the_tree_when_git_cannot_answer(monkeypatch) -> None:
    """🔴 REGRESSION, measured 2026-08-21: this guard ERRORED in the nix check.

    `git ls-files` ran with `check=True`, and the check sandbox builds from
    `/build/src` — a COPY of the source, not a clone — so git exited 128 and
    raised. The guard was therefore dead in the ONE tier that gates a merge,
    while passing on every dev host, which is the same shape of blindness the
    module docstring exists to describe.

    Simulates git refusing, then asserts the walk actually produced this repo's
    files — not merely that nothing raised.
    """
    calls: list[list[str]] = []

    def fake_run(cmd, *args, **kwargs):
        calls.append(list(cmd))
        if kwargs.get("check"):
            # What the real subprocess.run does on a non-zero exit, which is the
            # pre-fix behaviour this test must be able to see.
            raise subprocess.CalledProcessError(128, cmd, output="", stderr="")  # pragma: no cover - no case in this suite drives the fake with check=True
        return subprocess.CompletedProcess(
            cmd, 128, stdout="", stderr="fatal: not a git repository"
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    files = _tracked_text_files()

    assert calls, "premise gone: it no longer asks git first"
    assert len(files) > 500, (
        f"the fallback walk produced only {len(files)} file(s) — it walked "
        f"nothing useful, so the guard would report a vacuous clean tree"
    )
    assert REPO / "flake.nix" in files, "the walk did not reach this repo's root"
    assert not [p for p in files if "__pycache__" in p.parts], (
        "the walk descended into a skip directory"
    )


def test_the_predicate_actually_fires() -> None:
    """Positive control: the scan above reports 0, so prove 0 is a measurement.

    Without this, a predicate that never matched anything would produce the same
    reassuring zero as a genuinely clean tree.
    """
    conflicted = "\n".join(
        [
            "some prose",
            f"{_OURS} HEAD",
            "our side",
            _SPLIT,
            "their side",
            f"{_THEIRS} feature/branch",
        ]
    )
    hits = _marker_lines(conflicted)
    assert [n for n, _ in hits] == [2, 6], hits

    # And it does not fire on text that merely mentions markers inline, or on a
    # markdown setext underline.
    benign = "\n".join(
        [
            f"it emits NO `{_OURS}` markers, so grepping for them finds nothing",
            "A Heading",
            _SPLIT,
            f"  {_OURS} indented, not a marker at column 0",
        ]
    )
    assert _marker_lines(benign) == []


def test_this_file_is_itself_clean() -> None:
    """The guard must be scannable by its own predicate.

    This is why the markers are built rather than spelled. If someone "clarifies"
    this file by writing the literals out, this test goes red rather than the file
    quietly becoming the one place the guard cannot look.
    """
    assert _marker_lines(Path(__file__).read_text(encoding="utf-8")) == []
