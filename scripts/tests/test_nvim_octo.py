"""`nvim-octo` — the review TUI a clicked GitHub mention opens in.

🔴 WHAT THIS FILE CAN AND CANNOT SEE, STATED FIRST SO A GREEN RUN IS NOT READ
AS MORE THAN IT IS.

It asserts two things, both from files in this repo:

  * the octo.nvim configuration the wrapper ships — parsed STRUCTURALLY, so the
    claim is about the table that will be handed to `setup()`, not about words
    in a comment;
  * the wrapper's argument validation — by RUNNING the shell text with `nvim`
    stubbed, so a rejection is watched rather than reasoned about.

It cannot see, and does not claim: that alacritty maps a window; that neovim
starts; that `setup()` ran; that `Octo <N> <owner/repo>` resolved the right
buffer KIND (a live GraphQL call); or that the merge mappings are absent from a
LIVE PR buffer. A config file saying a mapping is gone is a claim about the
file — only a running editor proves the buffer. Those were measured by hand on
the built derivation and recorded in the PR; they are deliberately NOT asserted
here, because the measurement needs a nix build and a headless editor, and a
test that skips itself when it cannot get one is worse than no test.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PKG = ROOT / "nix" / "pkgs" / "tools" / "nvim-octo"
INIT_LUA = PKG / "octo-init.lua"
WRAPPER_SH = PKG / "nvim-octo.sh"
PKG_NIX = PKG / "default.nix"

# 🔴 THE MERGE FAMILY, READ OFF octo 2026-08-28's OWN `get_default_values()`.
# Four of these are keymaps that merge IMMEDIATELY — octo's `pr merge` calls
# `gh.pr.merge(opts)` with no confirmation of any kind — and three queue a merge.
# The brief named four; the other three were found by reading the defaults, and
# they are here for the same reason the first four are.
MERGE_MAPPINGS = (
    "merge_pr",
    "squash_and_merge_pr",
    "rebase_and_merge_pr",
    "merge_pr_stack",
    "merge_pr_queue",
    "squash_and_merge_queue",
    "rebase_and_merge_queue",
)

# `pr_options` is `<CR>` — ONE keystroke — and its menu (octo's `mappings.lua`)
# offers "Merge PR", "Squash and Merge PR" and "Delete Branch". Stripping every
# merge KEYMAP while re-declaring this would be a guard that reads as coverage
# and provides none, so it is treated as part of the same family.
MERGE_MENUS = ("pr_options",)


# --------------------------------------------------------------------------- #
# A STRUCTURAL LUA TABLE READER
#
# 🔴 NOT A GREP, AND THE DIFFERENCE IS THE WHOLE POINT. A `grep` for
# `mappings_disable_default = true` is walkable two ways: by rewording, and —
# far worse — by a later `mappings.pull_request` table re-adding `merge_pr`
# UNDERNEATH it, which the grep would never see. The reader below answers "which
# keys does this table declare", which is the state that decides what is bound.
# --------------------------------------------------------------------------- #
def _strip_comments(source: str) -> str:
    """Lua source with every `--` line comment removed, strings left intact.

    🔴 THIS IS NOT TIDINESS — WITHOUT IT EVERY READER BELOW IS WRONG, AND IT WAS.
    MEASURED on the first run of this file: `_scalar(lua, "picker")` returned
    `-- "fzf-lua"`: MEASURED against octo 2026-08-28` — a sentence out of the
    comment that EXPLAINS the setting, not the setting. The comments here are
    prose ABOUT the config and name every value they discuss, so reading them as
    config makes a passing test a fact about documentation. The alacritty
    `makeBinPath` ledger in test_mention_open.py strips comments for exactly
    this reason, and hit exactly this bug.

    ⚠ A `--` INSIDE A STRING IS NOT A COMMENT, so this tracks quoting rather
    than cutting at the first `--` on a line. Nothing in the config uses that
    today; a `desc = "foo -- bar"` added tomorrow would silently truncate the
    line and drop whatever followed it on that line from every reader.
    Long-bracket comments (`--[[ … ]]`) are NOT handled and do not occur here —
    if one is added, this returns its body as code and the control test below is
    what should be extended first.
    """
    out: list[str] = []
    for line in source.split("\n"):
        quote = None
        i = 0
        cut = len(line)
        while i < len(line):
            ch = line[i]
            if quote:
                if ch == "\\":
                    i += 2
                    continue
                if ch == quote:
                    quote = None
            elif ch in ('"', "'"):
                quote = ch
            elif ch == "-" and line[i + 1:i + 2] == "-":
                cut = i
                break
            i += 1
        out.append(line[:cut])
    return "\n".join(out)


def _table_body(source: str, key: str, *, start: int = 0) -> tuple[str, int]:
    """The brace-balanced body of `key = { … }`, and the index just past it.

    Raises rather than returning a sentinel: a table this cannot find means the
    config was restructured, and an empty string would make every "the merge
    keys are absent" assertion below pass VACUOUSLY. That is the exact shape of
    a reassuring zero, so it fails loudly instead.
    """
    m = re.search(r"(?<![\w.])" + re.escape(key) + r"\s*=\s*\{", source[start:])
    if not m:
        raise AssertionError(
            f"no `{key} = {{` table in the config — it was restructured, and "
            f"every assertion about its contents would now pass vacuously")
    open_at = start + m.end() - 1
    depth = 0
    for i in range(open_at, len(source)):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[open_at + 1:i], i + 1
    raise AssertionError(f"unbalanced braces after `{key} = {{`")


def _top_level_keys(body: str) -> set[str]:
    """The `name =` keys declared at depth 0 of a table body.

    Depth-tracked, so a nested `{ lhs = …, desc = … }` contributes nothing —
    `lhs` and `desc` appear under every single entry and would otherwise swamp
    the answer.
    """
    keys: set[str] = set()
    depth = 0
    i = 0
    while i < len(body):
        ch = body[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        elif depth == 0:
            m = re.match(r"([A-Za-z_][A-Za-z0-9_]*)\s*=", body[i:])
            if m and (i == 0 or not re.match(r"[\w.]", body[i - 1])):
                keys.add(m.group(1))
                i += m.end()
                continue
        i += 1
    return keys


def _scalar(source: str, key: str) -> str:
    """The literal a top-level `key = <value>` is set to, comments stripped."""
    m = re.search(r"(?<![\w.])" + re.escape(key) + r"\s*=\s*([^,\n]+)", source)
    assert m, f"`{key}` is not set in {INIT_LUA.name} — it takes the upstream default"
    return m.group(1).strip().strip('"').strip("'")


def test_the_lua_table_reader_can_actually_fire():
    """🔴 POSITIVE **AND** NEGATIVE CONTROL ON THE INSTRUMENT, before any verdict
    it produces is believed. A reader that matched nothing would report an empty
    key set forever, and every "no merge key is declared" assertion below would
    be a fact about the reader rather than about the config."""
    sample = """
    mappings = {
      pull_request = {
        checkout_pr = { lhs = "<localleader>po", desc = "checkout PR" },
        merge_pr = { lhs = "<localleader>pm", desc = "merge commit PR" },
      },
      issue = {
        close_issue = { lhs = "<localleader>ic", desc = "close issue" },
      },
    }
    """
    body, _ = _table_body(sample, "pull_request")
    keys = _top_level_keys(body)
    # POSITIVE: it finds the keys that ARE there…
    assert keys == {"checkout_pr", "merge_pr"}, keys
    # …and does NOT report the nested `lhs`/`desc` of every entry.
    assert "lhs" not in keys and "desc" not in keys
    # NEGATIVE: it can see a merge key when one is present. Without this, "no
    # merge key found" would be indistinguishable from a reader wired to
    # nothing — the reassuring zero this repo keeps paying for.
    assert MERGE_MAPPINGS[0] in keys
    # It also does not leak into the SIBLING table.
    assert "close_issue" not in keys
    # A table that is not there is an ERROR, never an empty set.
    with pytest.raises(AssertionError):
        _table_body(sample, "no_such_table")


def test_the_comment_stripper_can_actually_fire():
    """🔴 THE CONTROL FOR THE BUG THIS FILE ALREADY HIT ONCE. Before the
    stripper existed, `_scalar(lua, "picker")` read a value out of the comment
    that explains the setting. Both directions are asserted: prose goes, code
    stays, and a `--` inside a STRING is not a comment."""
    src = '\n'.join([
        '-- picker = "telescope" is what upstream defaults to',
        'picker = "fzf-lua",           -- and this is the real one',
        'desc = "a -- b",              -- a double dash inside a string',
    ])
    out = _strip_comments(src)
    # The prose value is gone…
    assert "telescope" not in out, out
    # …the real one survives, with its trailing comment removed…
    assert _scalar(out, "picker") == "fzf-lua"
    assert "the real one" not in out, out
    # …and a `--` inside a string is NOT treated as a comment start.
    assert 'desc = "a -- b"' in out, out
    # NEGATIVE CONTROL: on source with no comments it changes nothing, so a
    # stripper that simply deleted lines could not pass this pair.
    assert _strip_comments('picker = "fzf-lua",') == 'picker = "fzf-lua",'


# --------------------------------------------------------------------------- #
# THE CONFIG — asserted as STATE
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def lua() -> str:
    assert INIT_LUA.exists(), f"{INIT_LUA} is missing — was it `git add`ed?"
    raw = INIT_LUA.read_text(encoding="utf-8")
    stripped = _strip_comments(raw)
    # 🔴 A CHECK ON THE STRIPPER ITSELF, AGAINST THE REAL FILE RATHER THAN A
    # FIXTURE. The comments in this config quote every value they discuss, so a
    # stripper that silently stopped working would hand every reader below the
    # prose again — and the readers would happily find the right answers in the
    # wrong place. The file is heavily commented by design, so "something was
    # removed" is a safe and meaningful invariant.
    assert len(stripped) < len(raw) * 0.75, (
        "the comment stripper removed almost nothing — every reader below is "
        "now at risk of reading prose as configuration")
    return stripped


@pytest.fixture(scope="module")
def pr_keys(lua: str) -> set[str]:
    # Anchored INSIDE the `mappings = {` table, so a future top-level key
    # called `pull_request` somewhere else cannot be read instead.
    mappings_body, _ = _table_body(lua, "mappings")
    pr_body, _ = _table_body(mappings_body, "pull_request")
    return _top_level_keys(pr_body)


def test_the_pull_request_table_is_NOT_empty(pr_keys: set[str]):
    """🔴 THE POSITIVE CONTROL FOR EVERY ABSENCE ASSERTED BELOW. "No merge key
    is declared" is trivially true of a table that declares nothing at all —
    which would also mean reviewing is impossible, and would pass every other
    test in this section. Reviewing is the point of the feature, so the table
    must be substantial AND must carry the keys a review actually needs."""
    assert len(pr_keys) > 30, sorted(pr_keys)
    for needed in ("review_start", "review_resume", "approve_pr",
                   "add_comment", "list_changed_files", "show_pr_diff",
                   "toggle_checks", "resolve_thread"):
        assert needed in pr_keys, (
            f"`{needed}` is not declared, so the review surface this feature "
            f"exists for is incomplete: {sorted(pr_keys)}")


@pytest.mark.parametrize("key", MERGE_MAPPINGS + MERGE_MENUS)
def test_no_merge_keystroke_is_declared_for_a_pull_request_buffer(
        pr_keys: set[str], key: str):
    """🔴 THE OPERATOR REQUIREMENT, ASSERTED AS STATE: merging must require
    TYPING `:Octo pr merge`, never a keystroke.

    Upstream binds four immediate-merge keymaps and three merge-queue ones, and
    puts "Merge PR" / "Squash and Merge PR" / "Delete Branch" behind `<CR>` via
    `pr_options`. `mappings_disable_default = true` clears the whole default
    table, so what this file declares is the complete set — and none of it may
    be one of these.
    """
    assert key not in pr_keys, (
        f"`{key}` is declared in mappings.pull_request, which puts a merge "
        f"back one keystroke away. Merging must require typing "
        f"`:Octo pr merge`.")


def test_the_default_mappings_are_cleared_before_ours_are_merged(lua: str):
    """Without this flag `vim.tbl_deep_extend` MERGES our table into upstream's,
    so every merge keymap survives and the test above would be asserting the
    absence of something that is nonetheless bound. Structural: the flag and the
    declared-key set are two halves of one guarantee, and neither alone is it."""
    assert _scalar(lua, "mappings_disable_default") == "true"


def test_the_merge_method_is_squash(lua: str):
    """Upstream defaults to `"merge"` — a merge commit. These repositories
    squash, so left at the default the FIRST merge made from this TUI would
    produce the wrong commit shape."""
    assert _scalar(lua, "default_merge_method") == "squash"


def test_the_branch_is_NOT_deleted_on_merge(lua: str):
    """Also upstream's default, and kept EXPLICIT because it is load-bearing
    here rather than incidental: these repos set `delete_branch_on_merge`, and
    deleting a STACKED PARENT's branch makes GitHub auto-close the child PR and
    then refuse to reopen it — the branch is restorable, the PR object is
    not."""
    assert _scalar(lua, "default_delete_branch") == "false"


def test_the_review_never_wants_a_local_checkout(lua: str):
    """octo offers to check a PR out only when this is true. A review opened
    from a clicked link must never touch a working tree — other sessions share
    these checkouts."""
    assert _scalar(lua, "use_local_fs") == "false"


def test_the_picker_is_fzf_lua_which_is_a_SAFETY_setting(lua: str):
    """🔴 A SECOND, SEPARATE MERGE PATH THAT `mappings_disable_default` DOES NOT
    TOUCH. `picker_config.mappings` lives OUTSIDE the `mappings` table that flag
    clears, so upstream's `merge_pr = { lhs = "<C-r>" }` for the picker survives
    it — MEASURED on the built derivation: after setup,
    `picker_config.mappings.merge_pr.lhs` is still `<C-r>`.

    What closes it is the picker CHOICE: measured against octo 2026-08-28, the
    whole `lua/octo/pickers/fzf-lua/` tree contains no occurrence of "merge" at
    all, while `pickers/telescope/provider.lua` wires
    `cfg.picker_config.mappings.merge_pr.lhs` to a merge action in two places.
    So this line is the guard, and switching pickers silently re-opens the
    path."""
    assert _scalar(lua, "picker") == "fzf-lua"


def test_every_other_mapping_table_the_flag_cleared_is_re_declared(lua: str):
    """`mappings_disable_default` empties NINE tables, not just
    `pull_request`. A table left undeclared is a surface with no keymaps at
    all — `submit_win` is how a review is approved or has changes requested, and
    an empty one would make the feature's stated scope unreachable while every
    merge-safety test above stayed green."""
    mappings_body, _ = _table_body(lua, "mappings")
    declared = _top_level_keys(mappings_body)
    for table in ("pull_request", "issue", "review_thread", "submit_win",
                  "review_diff", "file_panel", "repo", "release",
                  "discussion"):
        assert table in declared, (
            f"`{table}` is cleared by mappings_disable_default and never "
            f"re-declared, so it has NO keymaps at all")
    submit, _ = _table_body(mappings_body, "submit_win")
    submit_keys = _top_level_keys(submit)
    assert {"approve_review", "request_changes"} <= submit_keys, submit_keys


def test_the_config_is_referenced_by_the_derivation(lua: str):
    """The seam between the two files. A config nothing loads is a config that
    describes no editor — and the failure would be silent, because every
    assertion above reads the FILE."""
    nix = PKG_NIX.read_text(encoding="utf-8")
    assert "./octo-init.lua" in nix, nix
    assert "luafile" in nix, (
        "the derivation no longer sources the init file, so the merge-safety "
        "settings asserted above reach no editor")
    assert "octo-nvim" in nix and "fzf-lua" in nix, (
        "octo.nvim declares NO runtime dependencies (a bare buildVimPlugin), "
        "so each plugin must be listed explicitly or the config errors at "
        "startup")


# --------------------------------------------------------------------------- #
# THE WRAPPER — driven, not read
# --------------------------------------------------------------------------- #
def _run_wrapper(tmp_path: Path, *args: str):
    """Run the wrapper's shell text with `nvim` STUBBED.

    🔴 THE STUB IS WHAT MAKES THIS SAFE AND WHAT MAKES IT A MEASUREMENT. The
    real binary would take over a terminal and reach GitHub; the stub records
    its argv into a file, so the ex-command the wrapper composes is observable.
    `bash -euo pipefail` reproduces the preamble `writeShellApplication` adds —
    verified against the built derivation, whose first four lines are the
    shebang plus exactly those three `set -o` lines.

    🔴 THE EXISTENCE CHECK IS NOT DEFENSIVE — IT CLOSES A MEASURED VACUOUS
    GREEN. Run against a tree where `nvim-octo.sh` does not exist (the base
    commit of the branch that added it), `bash` exits non-zero with an empty
    argv log, which satisfied every "rejected, and nvim was never reached"
    assertion below exactly as a correct wrapper does: 18 of them passed on a
    tree with NO WRAPPER AT ALL. That is why the callers now assert the
    wrapper's OWN exit code and OWN message rather than "non-zero".
    """
    assert WRAPPER_SH.exists(), (
        f"{WRAPPER_SH} is missing — was it `git add`ed? Without this check a "
        f"missing wrapper makes every rejection test below pass vacuously")
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    log = tmp_path / "nvim-argv"
    stub = bindir / "nvim"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f'printf "%s\\n" "$@" > {log}\n'
        "exit 0\n")
    stub.chmod(0o755)
    env = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}")
    proc = subprocess.run(
        ["bash", "-euo", "pipefail", str(WRAPPER_SH), *args],
        capture_output=True, text=True, env=env, timeout=60)
    argv = log.read_text().splitlines() if log.exists() else []
    return proc, argv


def test_a_good_invocation_composes_the_NUMBER_FIRST_ex_command(tmp_path):
    """🔴 THE ORDER IS THE CONTRACT, AND IT IS INVERTED FROM THE WRAPPER'S OWN
    ARGUMENTS. The wrapper takes `<owner/repo> <number>` because that is the
    order `mention-open.py` has them in, and it must emit `Octo <number>
    <owner/repo>` because octo's `M.octo(object, action, …)` does
    `tonumber(object)` and treats the SECOND word as the repository. Get it
    backwards and octo takes an entirely different branch.

    The fixtures are pairwise distinct and distinct from every constant the
    assertion names, so a mutant hardcoding a literal cannot survive.
    """
    proc, argv = _run_wrapper(tmp_path, "gardenersguild/trowelcast", "1559")
    assert proc.returncode == 0, proc.stderr
    assert argv == ["-c", "Octo 1559 gardenersguild/trowelcast"], argv


def test_a_number_is_passed_rather_than_a_url(tmp_path):
    """A URL would assert the reference KIND, which the handler cannot know —
    `mention-open.py` builds `/pull/{id}` for every mention and lets github.com
    redirect.

    ⚠ THE POSITIVE ASSERTION COMES FIRST, AND IT HAS TO. An earlier version of
    this test asserted only that `http` and `/pull/` were ABSENT from the argv,
    which an EMPTY argv satisfies — and an empty argv is what a missing wrapper
    produces. Pin what was passed, then observe what it is not."""
    proc, argv = _run_wrapper(tmp_path, "rivalorg/spadeworks", "42")
    assert proc.returncode == 0, proc.stderr
    assert argv == ["-c", "Octo 42 rivalorg/spadeworks"], argv
    assert "http" not in " ".join(argv), argv
    assert "/pull/" not in " ".join(argv), argv


# The wrapper's own exit codes. Asserted by VALUE rather than as "non-zero",
# because non-zero is also what a missing file, a syntax error and an unbound
# variable produce — see `_run_wrapper`'s note on the 18 tests that passed
# against a tree with no wrapper at all.
RC_USAGE = 64
RC_BAD_REPO = 65
RC_BAD_NUM = 66


@pytest.mark.parametrize("repo", [
    "notarepo",                     # no slash at all
    "too/many/slashes",
    "/leadingslash",
    "trailing/",
    "../../etc/passwd",             # traversal, and it HAS a slash
    "owner/repo;rm -rf /",          # shell metacharacters
    "owner/repo with space",
    "owner/$(whoami)",
    "",
])
def test_a_bad_repository_is_REJECTED_and_nvim_is_never_reached(tmp_path, repo):
    """Three halves, and the first is what makes the other two mean anything:
    THIS guard's own exit code, THIS guard's own message, and no editor. An exit
    code alone would be satisfied by a wrapper that launched first and
    complained after — or by no wrapper at all."""
    proc, argv = _run_wrapper(tmp_path, repo, "1559")
    assert proc.returncode == RC_BAD_REPO, (proc.returncode, proc.stderr)
    assert "not an owner/repo" in proc.stderr, proc.stderr
    assert argv == [], argv


@pytest.mark.parametrize("num", ["", "abc", "12a", "-1", "1.5", "1 2",
                                 "$(id)", "42;ls"])
def test_a_bad_number_is_REJECTED_and_nvim_is_never_reached(tmp_path, num):
    """🔴 A DIFFERENT EXIT CODE FROM THE REPOSITORY GUARD, ON PURPOSE. A mutation
    test that broke the repository check and watched "a test fail" would
    otherwise be green for the wrong reason if the NUMBER guard was the one that
    fired. Distinct codes plus distinct messages make each guard's kill
    attributable to itself."""
    proc, argv = _run_wrapper(tmp_path, "gardenersguild/trowelcast", num)
    assert proc.returncode == RC_BAD_NUM, (proc.returncode, proc.stderr)
    assert "not a reference number" in proc.stderr, proc.stderr
    assert argv == [], argv


@pytest.mark.parametrize("args", [(), ("only-one",),
                                  ("a/b", "1", "extra")])
def test_the_wrong_number_of_arguments_is_REJECTED(tmp_path, args):
    """🔴 `set -u` MAKES THE ARITY CHECK LOAD-BEARING RATHER THAN POLITE. Without
    it, `"$1"` on a zero-argument call is an unbound-variable error whose message
    says nothing about usage — and with `set -e` it is still non-zero, so a test
    asserting only the exit code would pass while the operator got
    `nvim-octo.sh: line 23: $1: unbound variable`."""
    proc, argv = _run_wrapper(tmp_path, *args)
    assert proc.returncode == RC_USAGE, (proc.returncode, proc.stderr)
    assert argv == []
    assert "usage:" in proc.stderr, proc.stderr


@pytest.mark.parametrize("repo", [
    "gardenersguild/trowelcast",
    "civitai/talos-infra",
    "ZacxDev/naida-ai",
    "innovation-upstream/devrc",
    "a-b.c/d_e.f",
])
def test_real_shaped_repository_names_are_ACCEPTED(tmp_path, repo):
    """🔴 THE NEGATIVE CONTROL ON THE VALIDATOR, built from REALISTIC data. A
    rejector that refused everything would pass every rejection test above —
    which is the instrument-validation trap RULES.md names. Dots, dashes,
    underscores and mixed case all occur in these owners' real repository
    names."""
    proc, argv = _run_wrapper(tmp_path, repo, "7")
    assert proc.returncode == 0, proc.stderr
    assert argv == ["-c", f"Octo 7 {repo}"], argv


def test_the_wrapper_text_carries_no_shebang_and_no_set_line():
    """`writeShellApplication` prepends both. A second copy here would be
    harmless in production and would make `_run_wrapper`'s `bash -euo pipefail`
    a claim about a file that sets its own options — i.e. the harness would stop
    reproducing the deployed preamble."""
    text = WRAPPER_SH.read_text(encoding="utf-8")
    assert not text.startswith("#!"), text.splitlines()[:1]
    assert not re.search(r"^\s*set\s+-", text, re.M), text
