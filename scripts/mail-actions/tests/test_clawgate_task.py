"""Tests for `clawgate.emit_task` / `build_task_payload`.

Regression guard for the silently-dropped-title bug: clawgate's `POST /api/tasks`
handler reads `directory` (which it renders as the card title) and IGNORES any
`title` key. These tests assert the built payload carries the action title in
`directory` (NOT `title`), and that `emit_task` POSTs exactly that body with the
bearer token — with the HTTP layer mocked (no live clawgate)."""
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


def test_emit_task_noop_without_token(monkeypatch):
    monkeypatch.delenv("CLAWGATE_HOOK_TOKEN", raising=False)
    fake = _install_fake_requests(monkeypatch)
    assert clawgate.emit_task(
        who="X", ask="a", deadline=None, amount=None, source_ref="mail#1 a@b.com",
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


def test_emit_task_without_a_token_reads_NOTHING(monkeypatch, tmp_path):
    """The no-op must stay a no-op: no shared-module load, no env-file read.

    Pinned because resolving the endpoint before the token check would turn a
    benign "no token configured" into an ImportError on any host whose lib/ is
    not where this module expects it.
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
# This lives here rather than in a repo-wide seam file because there is exactly
# ONE producer under guard. Its Signal twin (`scripts/signal/clawgate.py`) still
# holds the literal on purpose — its only caller is the `draft` CLI subcommand,
# which the deployed pod never runs — so a scan over "both producers" would be
# asserting something false about the repo.
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
