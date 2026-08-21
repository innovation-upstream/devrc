"""The ONE clawgate hook-token resolver — `scripts/lib/clawgate_env.py`.

WHY (clawgate task #307)
-----------------------
Both clawgate producers read the token from `os.environ` alone and, finding
nothing, posted nothing and said nothing. The token is not in the process
environment on this host — it is in `~/.claude/clawgate.env`, which is where
`clawgatectl` already looks. A real Signal draft was stored with no card and no
trace of the skip.

WHAT IS PINNED HERE, AND WHY EACH ONE
-------------------------------------
* THE FILE TIER EXISTS. Without it there is no fix at all — this is the
  behaviour the defect is.
* THE PRECEDENCE IS FILE-THEN-ENVIRONMENT. Every other test in this file passes
  under the INVERTED order too, because the two orders differ in exactly one
  case: both tiers set, disagreeing. That case has its own test, and it is the
  mandatory mutation for this module.
* AN EMPTY VALUE IS NOT A VALUE, at either tier — `config.go`'s `set` helper
  only assigns non-empty, so `export CLAWGATE_HOOK_TOKEN=` must not blank out a
  good file value.
* NOTHING RAISES. A producer's contract is that it degrades to "no card" and
  never takes the durable record down with it.
* THE WARNING NAMES THE SKIP AND NEVER THE SECRET.

The module is deliberately dependency-free (stdlib only, no psycopg2, no
requests), so this suite runs in the plain gate environment.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import io
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
LIB_REL = "scripts/lib/clawgate_env.py"


def _load():
    """Load the module by explicit path — the same way both producers do."""
    path = REPO / LIB_REL
    loader = importlib.machinery.SourceFileLoader("_test_clawgate_env", str(path))
    spec = importlib.util.spec_from_file_location("_test_clawgate_env", str(path),
                                                  loader=loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


CE = _load()

TOKEN = "cg-file-token-AAA"
OTHER = "cg-env-token-BBB"


def _env_file(tmp_path: Path, body: str) -> str:
    p = tmp_path / "clawgate.env"
    p.write_text(body, encoding="utf-8")
    return str(p)


# =========================================================================== #
# HARNESS CONTROLS — read these before believing anything below
# =========================================================================== #
def test_the_module_under_test_is_the_repo_one_and_it_loaded():
    assert (REPO / LIB_REL).is_file(), "%s is missing from this tree" % LIB_REL
    assert CE.TOKEN_VAR == "CLAWGATE_HOOK_TOKEN"
    # The default path is the one clawgatectl documents (config.go:15). Spelled
    # as a comparison rather than restated, so a rename of the constant cannot
    # quietly make this vacuous.
    assert CE.DEFAULT_ENV_PATH.replace("\\", "/").endswith(".claude/clawgate.env")


def test_the_fixture_builder_produces_a_file_the_parser_can_read():
    """POSITIVE CONTROL for the fixture itself. Every "resolved from the file"
    assertion below is worthless if `_env_file` writes something the parser
    silently drops — a zero from a parser wired to nothing is indistinguishable
    from a zero from a correct one."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        path = _env_file(Path(d), "CLAWGATE_HOOK_TOKEN=%s\n" % TOKEN)
        assert CE.parse_env_file(path) == {"CLAWGATE_HOOK_TOKEN": TOKEN}


# =========================================================================== #
# parse_env_file — the shapes ~/.claude/clawgate.env actually has
# =========================================================================== #
@pytest.mark.parametrize("body,expected", [
    ("CLAWGATE_HOOK_TOKEN=%s\n" % TOKEN, TOKEN),
    ("export CLAWGATE_HOOK_TOKEN=%s\n" % TOKEN, TOKEN),              # export prefix
    ('CLAWGATE_HOOK_TOKEN="%s"\n' % TOKEN, TOKEN),                   # double quotes
    ("CLAWGATE_HOOK_TOKEN='%s'\n" % TOKEN, TOKEN),                   # single quotes
    ("  CLAWGATE_HOOK_TOKEN =  %s  \n" % TOKEN, TOKEN),              # padding
    ("# a comment\n\nCLAWGATE_HOOK_TOKEN=%s\n" % TOKEN, TOKEN),      # comments/blanks
    ("CLAWGATE_API_URL=http://x\nCLAWGATE_HOOK_TOKEN=%s\n" % TOKEN, TOKEN),
    ("CLAWGATE_HOOK_TOKEN=a=b=c\n", "a=b=c"),                        # '=' in the value
])
def test_parse_env_file_reads_the_shapes_the_real_file_has(tmp_path, body, expected):
    assert CE.parse_env_file(_env_file(tmp_path, body))["CLAWGATE_HOOK_TOKEN"] \
        == expected


def test_parse_env_file_skips_junk_without_raising(tmp_path):
    got = CE.parse_env_file(_env_file(
        tmp_path, "not-a-kv-line\n=novalue\n#c\nCLAWGATE_HOOK_TOKEN=%s\n" % TOKEN))
    assert got == {"CLAWGATE_HOOK_TOKEN": TOKEN}


def test_parse_env_file_of_a_missing_path_is_empty_not_an_error(tmp_path):
    assert CE.parse_env_file(str(tmp_path / "nope.env")) == {}


def test_parse_env_file_of_an_unreadable_path_is_empty_not_an_error(tmp_path):
    # A directory where a file was expected: `open()` raises IsADirectoryError,
    # an OSError. A producer must degrade, never traceback four frames up.
    (tmp_path / "dir.env").mkdir()
    assert CE.parse_env_file(str(tmp_path / "dir.env")) == {}


# =========================================================================== #
# resolve_token — THE PRECEDENCE
# =========================================================================== #
def test_the_file_tier_resolves_when_the_environment_has_nothing(tmp_path):
    """🔴 THE DEFECT ITSELF. This is the case that produced a draft with no card:
    token in the file, nothing exported."""
    path = _env_file(tmp_path, "CLAWGATE_HOOK_TOKEN=%s\n" % TOKEN)
    assert CE.resolve_token(env_path=path, environ={}) == TOKEN


def test_the_environment_tier_resolves_when_the_file_is_missing(tmp_path):
    assert CE.resolve_token(env_path=str(tmp_path / "absent.env"),
                            environ={"CLAWGATE_HOOK_TOKEN": OTHER}) == OTHER


def test_the_environment_OVERRIDES_the_file_when_both_are_set(tmp_path):
    """🔴 THE PRECEDENCE TEST, and the only one in this file that can tell the
    documented order from its inverse. `clawgatectl` `resolveConfig`
    (config.go:94) applies the file FIRST and lets the environment override it,
    so an operator exporting a token for one command gets THAT token. Swap the
    two assignments in `resolve_token` and every other test here still passes;
    this one fails, naming both values."""
    path = _env_file(tmp_path, "CLAWGATE_HOOK_TOKEN=%s\n" % TOKEN)
    got = CE.resolve_token(env_path=path, environ={"CLAWGATE_HOOK_TOKEN": OTHER})
    assert got == OTHER, (
        "precedence is inverted: with the file holding %r and the environment "
        "holding %r the resolver returned %r. clawgatectl's chain is "
        "file -> environment -> flag, later overriding earlier." % (TOKEN, OTHER, got))


def test_an_empty_exported_value_does_not_blank_out_the_file(tmp_path):
    path = _env_file(tmp_path, "CLAWGATE_HOOK_TOKEN=%s\n" % TOKEN)
    assert CE.resolve_token(env_path=path, environ={"CLAWGATE_HOOK_TOKEN": ""}) == TOKEN


def test_an_empty_file_value_falls_through_to_the_environment(tmp_path):
    path = _env_file(tmp_path, "CLAWGATE_HOOK_TOKEN=\n")
    assert CE.resolve_token(env_path=path,
                            environ={"CLAWGATE_HOOK_TOKEN": OTHER}) == OTHER


def test_nothing_anywhere_resolves_to_None(tmp_path):
    assert CE.resolve_token(env_path=str(tmp_path / "absent.env"), environ={}) is None


def test_an_empty_value_at_BOTH_tiers_is_None_not_an_empty_string(tmp_path):
    path = _env_file(tmp_path, "CLAWGATE_HOOK_TOKEN=\n")
    assert CE.resolve_token(env_path=path, environ={"CLAWGATE_HOOK_TOKEN": ""}) is None


def test_the_default_env_path_is_used_when_none_is_given(monkeypatch, tmp_path):
    """The producers pass no path, so the DEFAULT must be the thing that works.
    `$HOME` is moved rather than the constant patched, so this exercises the real
    `~/.claude/clawgate.env` construction end to end."""
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "clawgate.env").write_text(
        "CLAWGATE_HOOK_TOKEN=%s\n" % TOKEN, encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    assert CE.resolve_token(environ={}) == TOKEN


def test_resolve_token_reads_os_environ_when_no_environ_is_passed(monkeypatch,
                                                                  tmp_path):
    monkeypatch.setenv("CLAWGATE_HOOK_TOKEN", OTHER)
    assert CE.resolve_token(env_path=str(tmp_path / "absent.env")) == OTHER


# =========================================================================== #
# resolve_hook_token — the AUDIBLE skip
# =========================================================================== #
def test_a_resolved_token_is_returned_and_NOTHING_is_written(tmp_path):
    path = _env_file(tmp_path, "CLAWGATE_HOOK_TOKEN=%s\n" % TOKEN)
    buf = io.StringIO()
    assert CE.resolve_hook_token("the card for draft #1", env_path=path,
                                 environ={}, stream=buf) == TOKEN
    assert buf.getvalue() == "", (
        "a successful resolve must be silent, or the warning becomes noise "
        "everyone filters out")


def test_an_unresolvable_token_warns_ONCE_and_returns_None(tmp_path):
    path = str(tmp_path / "absent.env")
    buf = io.StringIO()
    assert CE.resolve_hook_token("the card for draft #17", env_path=path,
                                 environ={}, stream=buf) is None
    out = buf.getvalue()
    assert out.count("\n") == 1, "expected exactly ONE line, got %r" % out
    # It names WHAT was skipped…
    assert "the card for draft #17" in out
    # …and BOTH places it looked, so "nothing happened" is no longer the
    # observable shared by "no token provisioned" and "the producer cannot see
    # one".
    assert path in out
    assert "environment" in out
    assert CE.TOKEN_VAR in out


def test_the_warning_never_carries_the_token(tmp_path):
    """🔴 stderr is captured into run logs. The warning fires only when there is
    no token, but a future edit that prints "found %r" would leak one — so pin it
    against a file that HAS a token, resolved out from under the environment."""
    path = _env_file(tmp_path, "CLAWGATE_HOOK_TOKEN=%s\n" % TOKEN)
    buf = io.StringIO()
    # Force the None branch while a real token sits in the parsed file: an
    # environ that is not consulted cannot help, so this is the parse-then-warn
    # path with a secret in hand.
    CE.resolve_hook_token("a card", env_path=path, environ={}, stream=buf)
    # (this call RESOLVES, so nothing is printed — the assertion that matters is
    # the negative one, checked on both branches)
    assert TOKEN not in buf.getvalue()
    buf2 = io.StringIO()
    CE.resolve_hook_token("a card", env_path=str(tmp_path / "absent.env"),
                          environ={}, stream=buf2)
    assert TOKEN not in buf2.getvalue()
    assert OTHER not in buf2.getvalue()


def test_resolve_hook_token_never_raises_on_a_hostile_path(tmp_path):
    (tmp_path / "dir.env").mkdir()
    buf = io.StringIO()
    assert CE.resolve_hook_token("a card", env_path=str(tmp_path / "dir.env"),
                                 environ={}, stream=buf) is None
