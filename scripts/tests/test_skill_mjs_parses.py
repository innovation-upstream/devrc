"""Every `.mjs` under `claude/skills/` must PARSE.

WHY THIS EXISTS
---------------
PR #768 shipped a `claude/skills/clickup/query.mjs` that did not parse. The
ClickUp skill's entire CLI was dead on `main` -- every invocation died with
`SyntaxError: missing ) after argument list` before running a single line.

The cause is worth stating, because it is a whole class and not a typo: the
help text is a TEMPLATE LITERAL (`console.error(`Usage: ...`)`), and the PR
added a line containing a backtick-quoted word -- ``manual`` -- inside it. The
inner backtick TERMINATED the literal early; everything after it re-parsed as
code, and the error surfaced ~14 lines EARLIER than the edit, at an unrelated
`${...}` on line 331. So the reported location did not point at the defect.

🔴 WHY THE EXISTING GATES DID NOT CATCH IT. The devrc node suite passed 1,179
tests on that PR. It runs the `.test.mjs` files; it never IMPORTS or parses
`query.mjs`, because nothing tests the CLI entry point. A suite can be fully
green and structurally blind to a file being syntactically invalid.

WHY `node --check` AND NOT AN IMPORT
------------------------------------
Importing needs the package's dependencies resolved. In a bare git worktree
there is no `node_modules`, so an import test fails with ERR_MODULE_NOT_FOUND
for reasons that have nothing to do with the code -- measured while fixing this
(`Cannot find package 'unified'`). `node --check` validates syntax with no
dependency resolution at all, so it is green in a worktree, in CI, and on a
deployed store path alike. It checks LESS than an import, and what it checks it
checks everywhere.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SKILLS = REPO_ROOT / "claude" / "skills"

# Floor, so an empty or mis-rooted glob cannot pass vacuously. There were far
# more than this when the test was written; it is a tripwire, not a target.
MIN_FILES = 10


def _mjs_files() -> list[Path]:
    return sorted(p for p in SKILLS.rglob("*.mjs") if p.is_file())


def test_node_is_available():
    """If node is absent every check below would skip and read as coverage."""
    assert shutil.which("node"), (
        "node is not on PATH, so the parse checks below cannot run. "
        "This test exists so that reads as a FAILURE, never as a silent pass."
    )


def test_the_glob_finds_files():
    """Positive control. A zero-length list makes the parametrised test vacuous."""
    found = _mjs_files()
    assert len(found) >= MIN_FILES, (
        f"only {len(found)} .mjs files found under {SKILLS} (floor {MIN_FILES}) -- "
        "the search root is probably wrong, which would make the parse check "
        "pass while checking nothing"
    )


@pytest.mark.parametrize("path", _mjs_files(), ids=lambda p: p.name)
def test_mjs_parses(path: Path):
    proc = subprocess.run(
        ["node", "--check", str(path)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, (
        f"\n\n{path.relative_to(REPO_ROOT)} does not parse.\n\n"
        f"{proc.stderr.strip()}\n\n"
        "🔴 If the reported line looks unrelated to your edit, suspect an "
        "unbalanced delimiter EARLIER in the file -- a backtick inside a "
        "template literal is the way this has actually broken before, and it "
        "reports the error well after the real cause."
    )
