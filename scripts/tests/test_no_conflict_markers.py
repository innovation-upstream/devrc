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


def _tracked_text_files() -> list[Path]:
    out = subprocess.run(
        ["git", "-C", str(REPO), "ls-files", "-z"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [REPO / p for p in out.split("\0") if p]


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
