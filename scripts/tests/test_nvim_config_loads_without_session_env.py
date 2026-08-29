"""neovim's config must load with NO graphical session. Fully hermetic.

🔴 MEASURED 2026-08-29, over real ssh to the laptop, against the DEPLOYED copy:

    Error in /home/zach/.config/nvim/init.lua:
    E484: Can't open file /.config/nvim/config/native.vim
    clipboard: No provider. Try ":checkhealth" or ":h clipboard".

`$DEVRC_DIR` is set in exactly ONE place -- a systemd user service's
`Environment=` block in nix/graphical.nix -- so it exists only inside a
graphical session. Unlike `$DEVRC` and `$HOMELAB` it is NOT in .zshenv.
init.vim sources every other config file through it, so with the variable unset
the first source expands to `/.config/nvim/config/native.vim`, raises E484 and
ABORTS THE WHOLE CONFIG: no options, no leader mappings, no lua half, no
plugin config. neovim had been running unconfigured in every non-graphical
context -- ssh, a bare TTY, a systemd unit, cron -- and it looked healthy
because the only place anyone reads a config error is the terminal in front of
them, which is the one place the variable IS set.

🔴 WHY THIS GUARD EXISTS RATHER THAN A BEHAVIOURAL TEST ALONE. The OSC 52
clipboard fix that shipped just before this one was verified by a red/green
harness that SET `DEVRC_DIR` itself. That manufactured the exact precondition
which does not hold in production, so the harness could not observe this class
at all, and a correct fix shipped INERT in the situation it was written for.
The lesson generalises past this variable: a test that supplies an environment
cannot see the environment being absent. These assertions therefore pin the
RELATIONSHIP -- nothing under .config/nvim may read $DEVRC_DIR at runtime, and
the one remaining mention must be the token nix substitutes at build time.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
NVIM_DIR = REPO / ".config" / "nvim"
INIT_VIM = NVIM_DIR / "init.vim"
NEOVIM_NIX = REPO / "nix" / "programs" / "neovim" / "default.nix"
PROGRAMS_NIX = REPO / "nix" / "programs" / "default.nix"


def _strip_comments(text: str, path: Path) -> str:
    """Drop comment lines so a guard cannot fire on prose ABOUT the hazard.

    🔴 Not cosmetic. The fix for this bug puts an explanatory comment naming
    $DEVRC_DIR at the top of the very files that no longer read it, so a raw
    substring check reports every fixed file as an offender -- and the obvious
    "fix" is to delete the explanation that stops the next person reintroducing
    it. Strip the comments; assert on the code.
    """
    marker = '"' if path.suffix == ".vim" else "--"
    out = []
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith(marker):
            continue
        out.append(line)
    return "\n".join(out)


def _config_files() -> list[Path]:
    files = sorted(p for p in NVIM_DIR.rglob("*") if p.suffix in {".vim", ".lua"})
    assert len(files) >= 10, (
        f"only {len(files)} nvim config files found under {NVIM_DIR} -- the "
        "enumeration is broken, and an empty list would pass every assertion "
        "below vacuously"
    )
    return files


def test_nix_substitutes_the_repo_path_into_init_vim_at_build_time():
    """The build must resolve $DEVRC_DIR so the runtime never has to."""
    src = NEOVIM_NIX.read_text()
    assert "builtins.replaceStrings" in src, (
        f"{NEOVIM_NIX.relative_to(REPO)} no longer substitutes the repo path; "
        "init.vim would again depend on $DEVRC_DIR being set at runtime"
    )
    assert '"$DEVRC_DIR"' in src, "the substitution no longer names $DEVRC_DIR"
    assert "config.home.homeDirectory" in src, (
        "the substituted path is not derived from home.homeDirectory"
    )
    # The module cannot use `config` unless it is passed one.
    assert re.search(r"import\s+\./neovim\s*\{[^}]*config\s*=\s*config",
                     PROGRAMS_NIX.read_text()), (
        f"{PROGRAMS_NIX.relative_to(REPO)} does not pass `config` to the neovim "
        "module, so config.home.homeDirectory cannot resolve"
    )


def test_no_nvim_config_file_reads_DEVRC_DIR_at_runtime():
    """🔴 THE RELATIONSHIP, across the whole tree -- not one file.

    init.vim is exempt: its `$DEVRC_DIR` occurrences are the substitution TOKEN,
    replaced at build time before neovim ever sees them. Every other file is
    read at runtime from the working tree, where no substitution happens, so a
    reference there is a live dependency on a graphical session.
    """
    offenders = []
    for f in _config_files():
        if f == INIT_VIM:
            continue
        if "DEVRC_DIR" in _strip_comments(f.read_text(), f):
            offenders.append(str(f.relative_to(REPO)))
    assert offenders == [], (
        "these nvim config files read $DEVRC_DIR at runtime, so they load only "
        "inside a graphical session and are silently absent over ssh:\n  "
        + "\n  ".join(offenders)
    )


def test_init_lua_derives_its_directory_from_its_own_path():
    """The positive half: it must locate itself, not merely avoid the env var.

    Asserting only the absence above would be satisfied by a file that hardcodes
    a path, or by one that computes nothing at all.
    """
    raw = (NVIM_DIR / "init.lua").read_text()
    src = _strip_comments(raw, NVIM_DIR / "init.lua")
    assert "debug.getinfo" in src, (
        "init.lua no longer derives its directory from its own path"
    )
    assert "os.getenv" not in src, (
        "init.lua reads the environment again; string.format('%s', nil) yields "
        'the literal "nil" and every source() silently resolves under "nil/"'
    )


def test_init_vim_still_carries_the_token_the_build_replaces():
    """Both halves of the substitution must agree, or the build is a no-op.

    A silently-unmatched replaceStrings leaves init.vim exactly as written and
    the config goes back to depending on the environment -- with the nix side
    still looking correct.
    """
    assert "$DEVRC_DIR" in INIT_VIM.read_text(), (
        "init.vim no longer contains $DEVRC_DIR, so the replaceStrings in "
        f"{NEOVIM_NIX.relative_to(REPO)} matches nothing and silently does "
        "nothing. If the path was hardcoded instead, drop the substitution too."
    )
