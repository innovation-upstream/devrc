"""Tests for `clawgate.emit_task` / `build_task_payload`.

Regression guard for the silently-dropped-title bug: clawgate's `POST /api/tasks`
handler reads `directory` (which it renders as the card title) and IGNORES any
`title` key. These tests assert the built payload carries the action title in
`directory` (NOT `title`), and that `emit_task` POSTs exactly that body with the
bearer token — with the HTTP layer mocked (no live clawgate)."""
import ast
import json as json_mod
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import clawgate  # noqa: E402


# -- pure payload builder ------------------------------------------------------

def test_payload_puts_title_in_directory_not_title():
    body = clawgate.build_task_payload(
        who="Acme Billing", ask="Pay invoice", deadline=None, amount=None,
        source_ref="mail#42 billing@acme.com",
    )
    # The server renders `directory` as the card title; `title` is dropped.
    assert "title" not in body
    assert set(body) == {"directory", "body"}
    assert body["directory"].endswith("Acme Billing")
    assert "action-required" in body["directory"]


def test_payload_body_includes_ask_deadline_amount_source():
    body = clawgate.build_task_payload(
        who="Acme", ask="  Approve the PO  ", deadline="2026-08-01", amount="$1,200",
        source_ref="mail#7 po@acme.com",
    )
    lines = body["body"].splitlines()
    assert lines[0] == "Approve the PO"  # ask, stripped
    assert "Deadline: 2026-08-01" in lines
    assert "Amount: $1,200" in lines
    assert "Source: mail#7 po@acme.com" in lines


def test_payload_omits_absent_deadline_and_amount():
    body = clawgate.build_task_payload(
        who="X", ask="do thing", deadline=None, amount=None, source_ref="mail#1 a@b.com",
    )
    assert body["body"] == "do thing\nSource: mail#1 a@b.com"


def test_directory_title_is_length_capped():
    body = clawgate.build_task_payload(
        who="W" * 500, ask="a", deadline=None, amount=None, source_ref="mail#1 a@b.com",
    )
    assert len(body["directory"]) <= clawgate.TITLE_MAX


# -- emit_task (HTTP mocked) ---------------------------------------------------

class _FakeResponse:
    def __init__(self):
        self.raised = False

    def raise_for_status(self):
        self.raised = True


class _FakeRequests:
    """Minimal stand-in for the `requests` module emit_task imports lazily."""

    def __init__(self):
        self.calls = []
        self._resp = _FakeResponse()

    def post(self, url, headers=None, json=None, timeout=None):
        self.calls.append(
            {"url": url, "headers": headers, "json": json, "timeout": timeout}
        )
        return self._resp


def _install_fake_requests(monkeypatch):
    fake = _FakeRequests()
    mod = types.ModuleType("requests")
    mod.post = fake.post
    monkeypatch.setitem(sys.modules, "requests", mod)
    return fake


def test_emit_task_noop_without_token(monkeypatch, tmp_path):
    """🔴 `path=` IS NOT DECORATION HERE. Deleting the variable from the process
    environment stopped being enough the moment the token started resolving
    through ~/.claude/clawgate.env as well: without an absent env-file path this
    test read the DEVELOPER'S real file, found a real token, and posted a card.
    It failed exactly that way against the fix and is the reason the fix is not
    vacuous — the file layer is genuinely being read."""
    monkeypatch.delenv("CLAWGATE_HOOK_TOKEN", raising=False)
    fake = _install_fake_requests(monkeypatch)
    assert clawgate.emit_task(
        who="X", ask="a", deadline=None, amount=None, source_ref="mail#1 a@b.com",
        path=str(tmp_path / "absent" / "clawgate.env"),
    ) is False
    assert fake.calls == []  # graceful no-op — nothing posted


# --------------------------------------------------------------------------- #
# THE TASK BASE URL IS CONFIGURATION, NOT A LITERAL
#
# 🔴 RED AT the pre-rework tip: this module held
# `ENDPOINT = "http://192.168.50.250:30302/api/tasks"` — and then, briefly, a
# resolver that read `os.environ` ONLY. Both are wrong in the same direction on
# the host this actually runs on: `--emit-clawgate` is manual/on-demand, no unit
# exports any `CLAWGATE_*`, and the configuration lives in
# ~/.claude/clawgate.env. So the file is the layer that carries the answer and
# the process environment is the override.
#
# 🔴 EVERY TEST BELOW MUST CALL `_env_file`. `task_endpoint()` reads the REAL
# ~/.claude/clawgate.env by default; a test that does not redirect it asserts
# against whatever the developer's own host happens to carry — green or red for
# reasons that have nothing to do with this code. (The operator host that runs
# this producer does have that file, and it sets CLAWGATE_TASK_API_URL.)
#
# Fixture hosts are PAIRWISE DISTINCT and distinct from `DEFAULT_BASE`, so a
# mutant that collapses the precedence onto any other key, onto the other layer,
# or onto the default cannot pass by returning a coincidentally equal value.
# --------------------------------------------------------------------------- #
FILE_TASK_BASE = "http://file-task.example:18081"
FILE_ROUTER_BASE = "http://file-router.example:27443"
ENV_TASK_BASE = "http://env-task.example:34567"
ENV_ROUTER_BASE = "http://env-router.example:41213"
#: The shared module's fallback, spelled as a LITERAL. Deriving it from the
#: module under test would assert nothing about it.
DEFAULT_BASE = "http://192.168.50.250:30302"
TASKS = "/api/tasks"


def _env_file(monkeypatch, tmp_path, contents=None):
    """Redirect ~/.claude/clawgate.env into `tmp_path`; return its path.

    `contents=None` writes NO file — the "this host has no clawgate.env" state,
    which must resolve rather than raise.
    """
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    path = home / ".claude" / "clawgate.env"
    if contents is not None:
        path.write_text(contents, encoding="utf-8")
    return path


def _clear_env(monkeypatch):
    monkeypatch.delenv("CLAWGATE_TASK_API_URL", raising=False)
    monkeypatch.delenv("CLAWGATE_API_URL", raising=False)


def test_the_env_file_redirect_actually_takes_effect(monkeypatch, tmp_path):
    """🔴 HARNESS CONTROL for every test below. If `HOME` were not what
    `~/.claude/clawgate.env` expands to, the redirect would be inert: every
    "the file wins" assertion would be measuring the absence of a file, and
    every "no file" assertion would be reading the developer's real one. Prove
    the file layer is READ (a value only the file can supply comes back) and
    that removing it changes the answer."""
    _clear_env(monkeypatch)
    path = _env_file(monkeypatch, tmp_path,
                     "CLAWGATE_TASK_API_URL=%s\n" % FILE_TASK_BASE)
    assert clawgate.task_endpoint() == FILE_TASK_BASE + TASKS, (
        "the env-file layer was not read — HOME redirect inert, so every "
        "file-layer assertion in this module is vacuous")
    path.unlink()
    assert clawgate.task_endpoint() == DEFAULT_BASE + TASKS, (
        "deleting the redirected file changed nothing — the resolver is not "
        "reading the path this harness controls")


def test_the_process_environment_OVERRIDES_the_env_file(monkeypatch, tmp_path):
    """The file says one host, a one-off `CLAWGATE_TASK_API_URL=… cmd` says
    another. The override wins — that is the whole point of keeping a process
    layer at all."""
    _env_file(monkeypatch, tmp_path,
              "CLAWGATE_TASK_API_URL=%s\nCLAWGATE_API_URL=%s\n"
              % (FILE_TASK_BASE, FILE_ROUTER_BASE))
    monkeypatch.setenv("CLAWGATE_TASK_API_URL", ENV_TASK_BASE)
    monkeypatch.delenv("CLAWGATE_API_URL", raising=False)
    assert clawgate.task_endpoint() == ENV_TASK_BASE + TASKS, (
        "the process environment must OVERRIDE the env file: expected %s, and "
        "%s would mean the file won" % (ENV_TASK_BASE, FILE_TASK_BASE))


def test_the_env_file_alone_decides_when_the_process_has_nothing(monkeypatch,
                                                                 tmp_path):
    """🔴 THE DEFECT THIS FIX CLOSES. This is the live configuration of the host
    that runs `--emit-clawgate`: the file names the task service, the process
    environment names nothing. A resolver reading `os.environ` only answers
    `DEFAULT_BASE` here and posts every card at the permission router."""
    _clear_env(monkeypatch)
    _env_file(monkeypatch, tmp_path,
              "CLAWGATE_HOOK_TOKEN=unused\n"
              "CLAWGATE_TASK_API_URL=%s\nCLAWGATE_API_URL=%s\n"
              % (FILE_TASK_BASE, FILE_ROUTER_BASE))
    assert clawgate.task_endpoint() == FILE_TASK_BASE + TASKS, (
        "with nothing in the process environment the env file's "
        "CLAWGATE_TASK_API_URL (%s) must decide; %s means the file was not "
        "read at all and %s means the router key won"
        % (FILE_TASK_BASE, DEFAULT_BASE, FILE_ROUTER_BASE))


def test_the_file_TASK_key_outranks_a_process_ROUTER_key(monkeypatch, tmp_path):
    """🔴 THE LAYERS ARE MERGED BEFORE THE LEDGER IS APPLIED. A process layer
    that won WHOLESALE would let a bare `CLAWGATE_API_URL` exported in a shell
    beat the file's `CLAWGATE_TASK_API_URL` and silently un-split the two
    services again. The SPECIFIC key must outrank the general one across both
    layers."""
    _env_file(monkeypatch, tmp_path,
              "CLAWGATE_TASK_API_URL=%s\n" % FILE_TASK_BASE)
    monkeypatch.delenv("CLAWGATE_TASK_API_URL", raising=False)
    monkeypatch.setenv("CLAWGATE_API_URL", ENV_ROUTER_BASE)
    assert clawgate.task_endpoint() == FILE_TASK_BASE + TASKS, (
        "a process-environment CLAWGATE_API_URL (%s) must NOT beat the file's "
        "CLAWGATE_TASK_API_URL (%s) — that un-splits the services"
        % (ENV_ROUTER_BASE, FILE_TASK_BASE))


def test_no_task_key_anywhere_falls_back_to_the_router_key(monkeypatch,
                                                           tmp_path):
    """No `CLAWGATE_TASK_API_URL` in either layer: the router key decides, so a
    host that never heard of the split keeps resolving exactly as before."""
    _clear_env(monkeypatch)
    _env_file(monkeypatch, tmp_path,
              "CLAWGATE_API_URL=%s\n" % FILE_ROUTER_BASE)
    assert clawgate.task_endpoint() == FILE_ROUTER_BASE + TASKS, (
        "with no task key anywhere the endpoint must fall back to "
        "CLAWGATE_API_URL (%s)" % FILE_ROUTER_BASE)


def test_an_empty_process_value_does_not_MASK_the_file(monkeypatch, tmp_path):
    """Empty means UNSET everywhere (`${A:-$B}`), so `CLAWGATE_TASK_API_URL= cmd`
    falls through TO THE FILE rather than erasing it."""
    _env_file(monkeypatch, tmp_path,
              "CLAWGATE_TASK_API_URL=%s\n" % FILE_TASK_BASE)
    monkeypatch.setenv("CLAWGATE_TASK_API_URL", "")
    monkeypatch.delenv("CLAWGATE_API_URL", raising=False)
    assert clawgate.task_endpoint() == FILE_TASK_BASE + TASKS, (
        "an empty process variable masked the file's value")


def test_a_trailing_slash_does_not_double_the_separator(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    _env_file(monkeypatch, tmp_path,
              "CLAWGATE_TASK_API_URL=%s/\n" % FILE_TASK_BASE)
    assert clawgate.task_endpoint() == FILE_TASK_BASE + TASKS


def test_with_no_configuration_at_all_the_endpoint_is_unchanged(monkeypatch,
                                                                tmp_path):
    """🔴 AN INVARIANT GUARD, NOT REGRESSION COVERAGE — counted as neither.

    It pins what this change must NOT move: on a host with no env file and no
    `CLAWGATE_*` exported, the endpoint is byte for byte what the old literal
    was. It goes red at the pre-rework tip only because the layering does not
    exist there; the VALUE it asserts was already what that tree produced."""
    _clear_env(monkeypatch)
    _env_file(monkeypatch, tmp_path)          # no file written
    assert clawgate.task_endpoint() == DEFAULT_BASE + TASKS


def test_an_UNOPENABLE_env_file_resolves_rather_than_raising(monkeypatch,
                                                             tmp_path):
    """A base URL has a defined answer without the file, so a file that cannot
    be opened is a STATE, not an error. (`read_clawgate_task_env` keeps the
    raising read — it must also produce a token, which no file genuinely
    defeats.)

    🔴 A DIRECTORY, not `chmod 000`. A mode-based test is decided by the uid the
    suite runs under — root opens an 0o000 file happily — so it would assert one
    thing on a developer box and another in a sandbox. `IsADirectoryError` is
    the same `OSError` family and is uid-independent.
    """
    _clear_env(monkeypatch)
    path = _env_file(monkeypatch, tmp_path)   # no file written
    path.mkdir()
    assert clawgate.task_endpoint() == DEFAULT_BASE + TASKS


# -- the POST itself ---------------------------------------------------------- #

def test_emit_task_posts_directory_payload_with_bearer(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAWGATE_HOOK_TOKEN", "tok-123")
    _clear_env(monkeypatch)
    _env_file(monkeypatch, tmp_path)          # no file — assert the default
    fake = _install_fake_requests(monkeypatch)

    ok = clawgate.emit_task(
        who="Acme", ask="Pay invoice", deadline="2026-08-01", amount="$5",
        source_ref="mail#9 x@acme.com",
    )
    assert ok is True
    assert len(fake.calls) == 1
    call = fake.calls[0]
    # A LITERAL, not `clawgate.<whatever the module computes>` — an expectation
    # derived from the implementation under test asserts nothing about it.
    assert call["url"] == DEFAULT_BASE + TASKS
    assert call["headers"]["Authorization"] == "Bearer tok-123"
    # The POSTed body carries the title in `directory`, never `title`.
    posted = call["json"]
    assert "title" not in posted
    assert posted["directory"].endswith("Acme")
    assert posted == clawgate.build_task_payload(
        who="Acme", ask="Pay invoice", deadline="2026-08-01", amount="$5",
        source_ref="mail#9 x@acme.com",
    )
    assert fake._resp.raised is True  # raise_for_status() was called


def test_emit_task_posts_to_the_task_service_NAMED_BY_THE_ENV_FILE(monkeypatch,
                                                                   tmp_path):
    """End to end, in the live configuration: the file names the task service
    and the POST goes there. This is the assertion that fails with the old
    hardcoded endpoint AND with an `os.environ`-only resolver."""
    monkeypatch.setenv("CLAWGATE_HOOK_TOKEN", "tok-cfg")
    _clear_env(monkeypatch)
    _env_file(monkeypatch, tmp_path,
              "CLAWGATE_TASK_API_URL=%s\nCLAWGATE_API_URL=%s\n"
              % (FILE_TASK_BASE, FILE_ROUTER_BASE))
    fake = _install_fake_requests(monkeypatch)

    assert clawgate.emit_task(
        who="Acme", ask="a", deadline=None, amount=None,
        source_ref="mail#1 a@b.com") is True
    assert fake.calls[0]["url"] == FILE_TASK_BASE + TASKS, (
        "emit_task posted to %r; it must follow CLAWGATE_TASK_API_URL from "
        "~/.claude/clawgate.env (%s)" % (fake.calls[0]["url"], FILE_TASK_BASE))


def test_emit_task_without_a_token_resolves_NO_ENDPOINT(monkeypatch, tmp_path):
    """The no-op must not resolve a base URL it has no credential to use.

    🔴 THIS DOCSTRING USED TO CLAIM MORE THAN THE CODE NOW DOES, and the weaker
    claim is the honest one. It read "no shared-module load, no env-file read",
    which held only while the token came from `os.environ`. The token lives in
    ~/.claude/clawgate.env, so "is there a token" cannot be answered without
    reading that file through that module — the load and the read moved AHEAD of
    the token check deliberately (see `emit_task`). What survives, and is what
    this asserts, is that the ENDPOINT is not resolved on a path that posts
    nothing.
    """
    monkeypatch.delenv("CLAWGATE_HOOK_TOKEN", raising=False)
    _env_file(monkeypatch, tmp_path)
    fake = _install_fake_requests(monkeypatch)

    def explode(*a, **k):  # pragma: no cover - fails the test if reached
        raise AssertionError("emit_task resolved the endpoint with no token set")

    monkeypatch.setattr(clawgate, "task_endpoint", explode)
    assert clawgate.emit_task(
        who="X", ask="a", deadline=None, amount=None,
        source_ref="mail#1 a@b.com") is False
    assert fake.calls == []


# -- the seam: this module must not grow its own copy of the rule ------------- #

def test_the_precedence_is_the_SHARED_one_not_a_local_respelling(monkeypatch,
                                                                 tmp_path):
    """🔴 THE SEAM. Everything above would also pass over a private copy of the
    rule living in this module — the exact regrowth the shared definition exists
    to prevent. This pins that the module RESOLVES THROUGH the shared ledger:
    feed the shared module's OWN ledger names and watch this module's resolver
    follow them, and confirm it is the shared default it falls back to."""
    _clear_env(monkeypatch)
    no_file = str(tmp_path / "absent" / "clawgate.env")
    cg = clawgate._load_clawgate_tasks()
    assert cg.TASK_API_URL_VARS == ("CLAWGATE_TASK_API_URL", "CLAWGATE_API_URL")
    specific, general = cg.TASK_API_URL_VARS
    assert clawgate.task_endpoint({specific: ENV_TASK_BASE,
                                   general: ENV_ROUTER_BASE},
                                  no_file) == ENV_TASK_BASE + TASKS
    assert clawgate.task_endpoint({general: ENV_ROUTER_BASE},
                                  no_file) == ENV_ROUTER_BASE + TASKS
    assert clawgate.task_endpoint({}, no_file) == cg.DEFAULT_API_URL + TASKS


# --------------------------------------------------------------------------- #
# 🔴 THE ORIGINAL DEFECT, pinned so it cannot come back.
#
# This lives here rather than in a repo-wide seam file because it guards THIS
# module's file only. Its Signal twin (`scripts/signal/clawgate.py`) carried the
# same literal until 2026-09-29 and now carries the same guard, next to itself,
# in `scripts/signal/tests/test_approval_gate.py` — each producer's scan reads
# the file it is about. The repo-wide statement that neither of them re-grows a
# private copy of the PRECEDENCE is `scripts/tests/
# test_clawgate_predicate_single_source.py`'s two-way importer ledger, which
# both are on.
# --------------------------------------------------------------------------- #
def _reintroduced_endpoint_constant(line: str) -> bool:
    """A base URL and the tasks path welded together in one module constant.
    The bare default constant in the shared module is fine and is not this."""
    stripped = line.strip()
    if stripped.startswith("#") or stripped.startswith("*"):
        return False              # a comment ABOUT the old literal is fine
    return stripped.startswith("ENDPOINT") and "=" in stripped


def test_the_producer_does_not_hardcode_a_task_endpoint_again():
    text = Path(clawgate.__file__).read_text(encoding="utf-8")
    for i, line in enumerate(text.splitlines(), 1):
        assert not _reintroduced_endpoint_constant(line), (
            "%s:%d re-introduced a hardcoded endpoint constant: %s\n"
            "Resolve the base from configuration instead (task_endpoint)."
            % (clawgate.__file__, i, line.strip()))


def test_the_hardcoded_endpoint_needle_can_actually_fire():
    """POSITIVE CONTROL for the scan above: a guard reporting zero on a clean
    tree is indistinguishable from one wired to nothing."""
    assert _reintroduced_endpoint_constant('ENDPOINT = "http://host:1/api/tasks"')
    assert _reintroduced_endpoint_constant("    ENDPOINT='http://host:1/api/tasks'")
    assert not _reintroduced_endpoint_constant(
        '# ENDPOINT = "http://host:1/api/tasks" (removed)')
    assert not _reintroduced_endpoint_constant('TASKS_PATH = "/api/tasks"')


# --------------------------------------------------------------------------- #
# 🔴 THE HOOK TOKEN IS CONFIGURATION TOO — task #307
#
# The base URL was fixed in #1878 and the token was left behind, explicitly:
# this module's own header carried "⚠ THE TOKEN IS STILL TAKEN FROM `os.environ`
# ONLY … the subject of its own open PR". That asymmetry WAS the remaining
# defect. `--emit-clawgate` is manual/on-demand, no unit exports any
# `CLAWGATE_*`, and the host it runs on keeps the token in
# ~/.claude/clawgate.env — so the module resolved the right base URL and then
# skipped every card for want of a credential it was not looking in the right
# place for, and said nothing.
#
# The precedence is `clawgatectl`'s, LOWEST to HIGHEST
# (`containers/clawgate/cmd/clawgatectl/config.go` `resolveConfig`):
#
#     ~/.claude/clawgate.env  ->  process environment
#
# ...with a later source overriding an earlier one ONLY when it supplies a
# value. Both directions are pinned below: the file alone decides (the defect),
# and the process environment wins when it has something (the precedence).
#
# 🔴 EVERY TEST BELOW PINS THE ENV-FILE PATH, by `path=` or by the HOME
# redirect. `hook_token()` reads the real ~/.claude/clawgate.env by default and
# this host HAS one, carrying a real token — a test that does not pin the path
# is green or red for reasons that have nothing to do with this code. That is
# not hypothetical: `test_emit_task_noop_without_token` above failed exactly
# that way against the fix until it was given an absent `path=`.
#
# Fixture tokens are PAIRWISE DISTINCT and distinct from every other literal in
# this module, so a mutant that collapses the two layers onto each other, or
# onto a URL key, cannot pass by returning a coincidentally equal value.
# --------------------------------------------------------------------------- #
FILE_TOKEN = "tok-env-file-9fd1"
ENV_TOKEN = "tok-process-env-4c7e"


def _absent(tmp_path):
    """An env-file path that cannot exist, so a result is decided by `env` alone."""
    return str(tmp_path / "absent-dir" / "clawgate.env")


def _token_file(tmp_path, token=FILE_TOKEN, extra=""):
    """Write an env file carrying `token`; return its path. No HOME involved."""
    path = tmp_path / "clawgate.env"
    body = "" if token is None else "CLAWGATE_HOOK_TOKEN=%s\n" % token
    path.write_text(body + extra, encoding="utf-8")
    return str(path)


# -- the resolver, both layers ------------------------------------------------ #

def test_the_env_file_ALONE_supplies_the_token(monkeypatch, tmp_path):
    """🔴 THE DEFECT THIS FIX CLOSES, in the live configuration of the host that
    runs `--emit-clawgate`: the token is in the file and the process environment
    has none. An `os.environ`-only read answers `None` here and the card is
    skipped in silence."""
    assert clawgate.hook_token({}, _token_file(tmp_path)) == FILE_TOKEN, (
        "the env-file layer did not supply the token: ~/.claude/clawgate.env "
        "carries CLAWGATE_HOOK_TOKEN and nothing is exported, which is the "
        "exact state in which every card was being skipped")


def test_the_process_environment_OVERRIDES_the_env_file_token(monkeypatch,
                                                              tmp_path):
    """🔴 THE PRECEDENCE, NOT JUST THE SOURCE. Both layers carry a token and the
    process one wins — `clawgatectl` applies the file FIRST and the environment
    ON TOP (`resolveConfig`), so a one-off `CLAWGATE_HOOK_TOKEN=… cmd` must beat
    a stale value in the file. A resolver with the two layers SWAPPED passes
    every file-alone assertion above and fails here."""
    got = clawgate.hook_token({"CLAWGATE_HOOK_TOKEN": ENV_TOKEN},
                              _token_file(tmp_path))
    assert got == ENV_TOKEN, (
        "the process environment must OVERRIDE the env file: expected %r, got "
        "%r — and %r would mean the two layers are applied in the wrong order"
        % (ENV_TOKEN, got, FILE_TOKEN))


def test_an_empty_process_token_does_not_MASK_the_file(tmp_path):
    """Empty means UNSET everywhere, matching the Go side's "only overrides when
    it actually supplies a value". `CLAWGATE_HOOK_TOKEN= cmd` must fall THROUGH
    to the file rather than erase it."""
    got = clawgate.hook_token({"CLAWGATE_HOOK_TOKEN": ""},
                              _token_file(tmp_path))
    assert got == FILE_TOKEN, (
        "an empty process variable masked the file's token (got %r)" % (got,))


def test_an_empty_token_in_the_FILE_is_unset_not_a_bare_bearer(tmp_path):
    """An empty value is "no token" on the file layer too — otherwise the header
    goes out as `Bearer ` and the server answers 401 instead of the producer
    saying it had no credential."""
    assert clawgate.hook_token({}, _token_file(tmp_path, token="")) is None


def test_no_token_in_either_layer_is_None(tmp_path):
    assert clawgate.hook_token({}, _absent(tmp_path)) is None
    assert clawgate.hook_token({}, _token_file(tmp_path, token=None)) is None


def test_an_UNOPENABLE_env_file_resolves_to_None_rather_than_raising(tmp_path):
    """A producer must degrade, never raise, on a file it cannot read — a
    missing token degrades notification and never the record.

    🔴 A DIRECTORY, not `chmod 000`: a mode-based test is decided by the uid the
    suite runs under (root opens an 0o000 file happily), while
    `IsADirectoryError` is the same `OSError` family and uid-independent."""
    path = tmp_path / "clawgate.env"
    path.mkdir()
    assert clawgate.hook_token({}, str(path)) is None


def test_the_DEFAULT_env_file_path_is_the_one_under_HOME(monkeypatch, tmp_path):
    """🔴 HARNESS CONTROL for the `path=`-less default. Everything above pins the
    path explicitly, which proves the LAYERING but not that the layer it reads
    by default is `~/.claude/clawgate.env`. Prove the default path is the HOME
    one — a value only this file can supply comes back — and that removing the
    file changes the answer."""
    _clear_env(monkeypatch)
    monkeypatch.delenv("CLAWGATE_HOOK_TOKEN", raising=False)
    path = _env_file(monkeypatch, tmp_path,
                     "CLAWGATE_HOOK_TOKEN=%s\n" % FILE_TOKEN)
    assert clawgate.hook_token() == FILE_TOKEN, (
        "hook_token() did not read ~/.claude/clawgate.env by default — HOME "
        "redirect inert, so this assertion would be vacuous")
    path.unlink()
    assert clawgate.hook_token() is None, (
        "deleting the redirected file changed nothing — the resolver is not "
        "reading the path this harness controls")


# -- criterion 2: the card actually posts off the file-only token ------------- #

def test_emit_task_POSTS_when_the_TOKEN_IS_ONLY_IN_THE_ENV_FILE(monkeypatch,
                                                                tmp_path):
    """🔴 THE HEADLINE ASSERTION. The exact condition that failed: token present
    only in ~/.claude/clawgate.env, absent from the process environment. The
    card must post, with that token as the bearer."""
    monkeypatch.delenv("CLAWGATE_HOOK_TOKEN", raising=False)
    fake = _install_fake_requests(monkeypatch)
    path = _token_file(tmp_path,
                       extra="CLAWGATE_TASK_API_URL=%s\n" % FILE_TASK_BASE)

    ok = clawgate.emit_task(who="Acme", ask="Pay invoice", deadline=None,
                            amount=None, source_ref="mail#307 x@acme.com",
                            env={}, path=path)
    assert ok is True, (
        "the card was NOT posted with the token present in the env file and "
        "absent from the environment — that is the defect, unfixed")
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["headers"]["Authorization"] == "Bearer " + FILE_TOKEN, (
        "the bearer was %r; the env file's token (%r) must be the credential"
        % (call["headers"]["Authorization"], FILE_TOKEN))
    assert call["url"] == FILE_TASK_BASE + TASKS


# -- criterion 3: one clear stderr line, no raise, record intact -------------- #

def test_no_token_anywhere_WARNS_ON_STDERR_and_returns_False(monkeypatch,
                                                             tmp_path, capsys):
    """One line, on stderr, naming WHAT was skipped and WHERE it looked — and no
    raise. Silence was the actual operator-visible symptom: a draft stored, no
    card, and nothing to read."""
    monkeypatch.delenv("CLAWGATE_HOOK_TOKEN", raising=False)
    fake = _install_fake_requests(monkeypatch)
    path = _absent(tmp_path)

    assert clawgate.emit_task(who="X", ask="a", deadline=None, amount=None,
                              source_ref="mail#1 a@b.com",
                              env={}, path=path) is False
    assert fake.calls == []
    err = capsys.readouterr().err
    lines = [ln for ln in err.strip().splitlines() if ln.strip()]
    assert len(lines) == 1, (
        "expected exactly ONE stderr line, got %d:\n%s" % (len(lines), err))
    line = lines[0]
    assert "CLAWGATE_HOOK_TOKEN" in line, (
        "the warning must NAME the variable; got %r" % line)
    assert path in line, (
        "the warning must name WHERE it looked (%s); got %r" % (path, line))
    assert "mail#1 a@b.com" in line, (
        "the warning must name WHAT was skipped; got %r" % line)


def test_the_warning_never_carries_the_TOKEN_itself(monkeypatch, tmp_path,
                                                    capsys):
    """🔴 CRITERION 7, driven rather than asserted about. A warning built by
    formatting the resolved config would leak the credential into a log the
    moment one layer had a token and the resolver still refused. Feed a token
    the resolver MUST reject — empty on top of empty — and confirm no secret
    reaches either stream."""
    monkeypatch.delenv("CLAWGATE_HOOK_TOKEN", raising=False)
    _install_fake_requests(monkeypatch)
    path = _token_file(tmp_path, token="")       # present but EMPTY => unset

    assert clawgate.emit_task(who="X", ask="a", deadline=None, amount=None,
                              source_ref="mail#2 a@b.com",
                              env={"CLAWGATE_HOOK_TOKEN": ""},
                              path=path) is False
    cap = capsys.readouterr()
    for stream, text in (("stderr", cap.err), ("stdout", cap.out)):
        assert FILE_TOKEN not in text and ENV_TOKEN not in text, \
            "a token reached %s: %r" % (stream, text)


def test_the_POSTED_card_carries_the_token_ONLY_in_the_bearer_header(
        monkeypatch, tmp_path, capsys):
    """🔴 CRITERION 7 on the SUCCESS path. The token goes in one place: the
    Authorization header. Never in the URL, never in the JSON body, never on
    stdout or stderr."""
    monkeypatch.delenv("CLAWGATE_HOOK_TOKEN", raising=False)
    fake = _install_fake_requests(monkeypatch)

    assert clawgate.emit_task(who="Acme", ask="a", deadline=None, amount=None,
                              source_ref="mail#3 a@b.com",
                              env={}, path=_token_file(tmp_path)) is True
    call = fake.calls[0]
    assert FILE_TOKEN not in call["url"], "the token is in the URL: %r" % call["url"]
    assert FILE_TOKEN not in json_mod.dumps(call["json"]), \
        "the token is in the POSTed body"
    assert call["headers"]["Authorization"] == "Bearer " + FILE_TOKEN
    cap = capsys.readouterr()
    assert FILE_TOKEN not in cap.err and FILE_TOKEN not in cap.out


# -- criterion 1: ONE definition, and this module is not a second one --------- #

def test_the_token_precedence_is_the_SHARED_one_not_a_local_respelling(tmp_path):
    """🔴 THE SEAM. Every test above would also pass over a private copy of the
    rule living in this module — the exact regrowth the shared definition exists
    to prevent. This pins that the module RESOLVES THROUGH the shared module:
    the variable name comes from the shared constant, and this producer's
    resolver and the shared one agree on the same inputs."""
    cg = clawgate._load_clawgate_tasks()
    assert cg.HOOK_TOKEN_VAR == "CLAWGATE_HOOK_TOKEN"
    path = _token_file(tmp_path)
    assert clawgate.hook_token({}, path) == cg.hook_token({}, path)
    assert clawgate.hook_token({cg.HOOK_TOKEN_VAR: ENV_TOKEN}, path) \
        == cg.hook_token({cg.HOOK_TOKEN_VAR: ENV_TOKEN}, path) == ENV_TOKEN
    assert clawgate.hook_token({}, _absent(tmp_path)) \
        is cg.hook_token({}, _absent(tmp_path)) is None


def _os_environ_token_reads(src: str) -> list:
    """Line numbers where `src` reads the hook token straight out of the process
    environment — `os.environ[...]`, `os.environ.get(...)` or `os.getenv(...)`.

    🔴 AN AST WALK, NOT A TEXTUAL NEEDLE, and the difference is load-bearing:
    every docstring in these producers QUOTES
    `os.environ.get("CLAWGATE_HOOK_TOKEN")` to record why it is gone, so a grep
    fires on the prose that documents the fix and the guard becomes noise
    somebody deletes. A parse sees a Call or a Subscript, or nothing.
    """
    hits = []
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Call):
            target = node.func
            names = [a.value for a in node.args
                     if isinstance(a, ast.Constant) and isinstance(a.value, str)]
        elif isinstance(node, ast.Subscript):
            target = node.value
            s = node.slice
            names = ([s.value] if isinstance(s, ast.Constant)
                     and isinstance(s.value, str) else [])
        else:
            continue
        if "CLAWGATE_HOOK_TOKEN" not in names:
            continue
        dumped = ast.dump(target)
        if "environ" in dumped or "getenv" in dumped:
            hits.append(node.lineno)
    return hits


def test_the_producer_reads_the_token_through_the_SHARED_resolver_ONLY():
    """🔴 CRITERION 1, structurally. One shared helper is the ONLY place this
    producer reads the token — so a re-grown `os.environ.get(...)` beside it,
    which would pass every behavioural test above by shadowing nothing, fails
    here instead."""
    src = Path(clawgate.__file__).read_text(encoding="utf-8")
    hits = _os_environ_token_reads(src)
    assert hits == [], (
        "%s reads CLAWGATE_HOOK_TOKEN out of the process environment at line(s) "
        "%s. Resolve it through scripts/lib/clawgate_tasks.hook_token instead — "
        "an environment-only read is blind to ~/.claude/clawgate.env, which is "
        "where the token actually lives." % (clawgate.__file__, hits))


def test_the_token_read_needle_can_actually_fire():
    """POSITIVE AND NEGATIVE CONTROL for the scan above. A guard reporting zero
    on a clean tree is indistinguishable from one wired to nothing — and this
    one in particular must NOT fire on the three near-misses, or it reports a
    finding on its own documentation."""
    assert _os_environ_token_reads(
        'import os\nt = os.environ.get("CLAWGATE_HOOK_TOKEN")\n') == [2]
    assert _os_environ_token_reads(
        'import os\nt = os.environ["CLAWGATE_HOOK_TOKEN"]\n') == [2]
    assert _os_environ_token_reads(
        'import os\nt = os.getenv("CLAWGATE_HOOK_TOKEN")\n') == [2]
    # the prose that documents the fix is NOT a finding
    assert _os_environ_token_reads(
        '"""Not os.environ.get("CLAWGATE_HOOK_TOKEN") any more."""\n') == []
    # a DIFFERENT variable read from the environment is not this guard's business
    assert _os_environ_token_reads(
        'import os\nu = os.environ.get("CLAWGATE_API_URL")\n') == []
    # the shared resolver, reached by name, is the thing we WANT
    assert _os_environ_token_reads(
        'x = cg.hook_token(env, path)  # CLAWGATE_HOOK_TOKEN\n') == []


# -- criterion 6: the record survives a missing token ------------------------- #

def test_extract_counts_a_tokenless_card_as_NOT_EMITTED_without_raising(
        monkeypatch, tmp_path, capsys):
    """🔴 D3 FOR THIS PRODUCER. `_emit_clawgate` must come back 0 — not an
    exception, not a failed run — when no token resolves. The mail row and its
    extraction are already stored; a missing token degrades NOTIFICATION, never
    the record."""
    import extract

    monkeypatch.delenv("CLAWGATE_HOOK_TOKEN", raising=False)
    _env_file(monkeypatch, tmp_path)                 # no file under HOME either
    _clear_env(monkeypatch)
    fake = _install_fake_requests(monkeypatch)

    ex = types.SimpleNamespace(who="Acme", ask="Pay invoice", deadline=None,
                               amount=None)
    assert extract._emit_clawgate({"id": 42, "from_addr": "x@acme.com"}, ex) == 0
    assert fake.calls == []
    err = capsys.readouterr().err
    assert "CLAWGATE_HOOK_TOKEN" in err, (
        "the skip must be NAMED on stderr, not silent; got %r" % err)
    assert "clawgate emit failed" not in err, (
        "a missing token must not be reported as a FAILURE — it is a graceful "
        "no-op: %r" % err)
