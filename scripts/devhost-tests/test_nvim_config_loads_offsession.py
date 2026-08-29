"""BEHAVIOURAL: the nvim config must survive a session with no $DEVRC_DIR.

Dev-host tier -- drives a real nvim, which the nix sandbox has not got.

The bug this pins was found over real ssh, but it reproduces LOCALLY by simply
unsetting the variable, which is what makes it testable at all:

    env -u DEVRC_DIR nvim -c 'source .config/nvim/init.vim'
    -> E484: Can't open file /.config/nvim/config/native.vim   (x9)

🔴 THE FIXTURE MUST NOT SUPPLY DEVRC_DIR. The clipboard suite that shipped just
before this one set it in every subprocess env, which manufactured the one
precondition that does not hold in production -- so a correct fix shipped inert
and the tests stayed green. `_env_without_session()` exists to make that
mistake loud rather than repeatable.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
INIT_VIM = REPO / ".config" / "nvim" / "init.vim"


def _env_without_session() -> dict:
    """The environment an ssh session actually has: no DEVRC_DIR, no DISPLAY."""
    env = dict(os.environ)
    env.pop("DEVRC_DIR", None)
    env.pop("DISPLAY", None)
    return env


def _source_count_e484(init_path: Path) -> int:
    """Source `init_path` in a bare nvim and count E484s."""
    res = subprocess.run(
        ["nvim", "--clean", "--headless", "-c", f"source {init_path}", "-c", "qa!"],
        env=_env_without_session(), capture_output=True, text=True, timeout=120,
    )
    return (res.stdout + res.stderr).count("E484")


def test_the_unsubstituted_init_vim_still_reproduces_the_bug():
    """🔴 RED CONTROL. If this stops failing, the green half proves nothing.

    The repo's init.vim carries the literal `$DEVRC_DIR` token on purpose --
    nix replaces it at build time. Sourced RAW with the variable unset, it must
    still break, or the fixture is no longer reproducing the reported symptom.
    """
    n = _source_count_e484(INIT_VIM)
    assert n > 0, (
        "sourcing the raw init.vim with DEVRC_DIR unset produced no E484. "
        "Either the token was removed from init.vim (then delete this test and "
        "the substitution together) or the reproduction has drifted -- in which "
        "case the green half below is measuring nothing."
    )


def test_the_build_substituted_init_vim_loads_with_no_session_env():
    """GREEN. Exactly what nix ships: the token replaced with the repo path.

    Reproduces `builtins.replaceStrings ["$DEVRC_DIR"] [<repo>]` rather than
    importing it, because the nix evaluation is not available here -- the
    hermetic suite pins that the module still performs that substitution.
    """
    with tempfile.TemporaryDirectory() as td:
        substituted = Path(td) / "init.vim"
        substituted.write_text(INIT_VIM.read_text().replace("$DEVRC_DIR", str(REPO)))
        n = _source_count_e484(substituted)

    assert n == 0, (
        f"the substituted init.vim still raised {n} E484(s) with DEVRC_DIR "
        "unset -- something under .config/nvim resolves a path through the "
        "environment rather than through the substituted root"
    )


def test_the_tools_this_tier_needs_are_present():
    """Fail loudly rather than skip -- an unpinned skip is what moved this file
    out of the hermetic target in the first place."""
    assert shutil.which("nvim"), "nvim missing on the dev-host tier"
