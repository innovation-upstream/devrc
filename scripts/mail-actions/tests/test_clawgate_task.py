"""Tests for `clawgate.emit_task` / `build_task_payload`.

Regression guard for the silently-dropped-title bug: clawgate's `POST /api/tasks`
handler reads `directory` (which it renders as the card title) and IGNORES any
`title` key. These tests assert the built payload carries the action title in
`directory` (NOT `title`), and that `emit_task` POSTs exactly that body with the
bearer token — with the HTTP layer mocked (no live clawgate)."""
import ast
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


# --------------------------------------------------------------------------- #
# 🔴 THE TOKEN NOW HAS TWO SOURCES (clawgate task #307), so every test below
# pins BOTH — and pins $HOME. `emit_task` resolves through
# `scripts/lib/clawgate_env`, whose file tier is `~/.claude/clawgate.env`. A test
# that only unset the environment variable would read the OPERATOR'S REAL TOKEN
# on a dev host: green in the nix sandbox (no such file) and posting a live card
# off the workbench. Same isolation helper as
# scripts/signal/tests/test_approval_gate.py — the two producers share the
# resolver, so they share the hazard.
# --------------------------------------------------------------------------- #
def _isolate_clawgate_env(monkeypatch, tmp_path, *, file_token=None):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True, exist_ok=True)
    if file_token is not None:
        (home / ".claude" / "clawgate.env").write_text(
            "CLAWGATE_HOOK_TOKEN=%s\n" % file_token, encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CLAWGATE_HOOK_TOKEN", raising=False)


def test_emit_task_noop_without_token(monkeypatch, tmp_path, capsys):
    _isolate_clawgate_env(monkeypatch, tmp_path)     # no file, no env var
    fake = _install_fake_requests(monkeypatch)
    assert clawgate.emit_task(
        who="X", ask="a", deadline=None, amount=None, source_ref="mail#1 a@b.com",
    ) is False
    assert fake.calls == []  # graceful no-op — nothing posted
    # 🔴 Graceful, but NOT silent: the action item is already stored, so this
    # costs a notification — and it says so, naming the skip and both places it
    # looked.
    err = capsys.readouterr().err
    assert err.count("\n") == 1, "expected exactly ONE stderr line, got %r" % err
    assert "mail#1 a@b.com" in err and "CLAWGATE_HOOK_TOKEN" in err


def test_emit_task_posts_when_the_token_is_ONLY_in_the_env_FILE(monkeypatch,
                                                                tmp_path, capsys):
    """🔴 THE DEFECT, on this producer. Identical to the signal one, which is the
    point: the old `os.environ.get` was open-coded at both sites and wrong at
    both, so fixing one would have left this half failing unnoticed."""
    _isolate_clawgate_env(monkeypatch, tmp_path, file_token="tok-from-file")
    fake = _install_fake_requests(monkeypatch)
    assert clawgate.emit_task(
        who="Acme", ask="a", deadline=None, amount=None, source_ref="mail#2 a@b.com",
    ) is True
    assert len(fake.calls) == 1
    assert fake.calls[0]["headers"]["Authorization"] == "Bearer tok-from-file"
    assert capsys.readouterr().err == "", "a successful post must be silent"


def test_emit_task_prefers_the_ENVIRONMENT_over_the_file(monkeypatch, tmp_path):
    """`clawgatectl`'s chain is file -> environment, later overriding earlier."""
    _isolate_clawgate_env(monkeypatch, tmp_path, file_token="tok-from-file")
    monkeypatch.setenv("CLAWGATE_HOOK_TOKEN", "tok-from-environ")
    fake = _install_fake_requests(monkeypatch)
    assert clawgate.emit_task(
        who="Acme", ask="a", deadline=None, amount=None, source_ref="mail#3 a@b.com",
    ) is True
    assert fake.calls[0]["headers"]["Authorization"] == "Bearer tok-from-environ", (
        "precedence is inverted: the exported token must override the file's")


def test_emit_task_reads_the_token_ONLY_through_the_shared_resolver():
    """🔴 THE SEAM, structurally — see the twin in the signal suite."""
    src = Path(clawgate.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    prose = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", None) or []
        if (body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            prose.add(id(body[0].value))
    for node in ast.walk(tree):
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and id(node) not in prose):
            assert "CLAWGATE_HOOK_TOKEN" not in node.value, (
                "scripts/mail-actions/clawgate.py names the token variable in "
                "code at line %s — it must resolve through "
                "scripts/lib/clawgate_env.resolve_hook_token, which is the ONE "
                "place either producer reads it" % getattr(node, "lineno", "?"))
    assert "resolve_hook_token(" in src, (
        "the producer no longer calls the shared resolver")


def test_both_producers_load_the_SAME_resolver_file():
    """🔴 THE SEAM BETWEEN THE TWO PRODUCERS, and a positive control for both
    loaders. Each producer resolves the module by explicit path, so "shared" is a
    claim about two paths landing on one file — not something either suite can
    see alone. Asserted by RESOLVING both, so a loader that never finds anything
    (its ImportError branch also returns False) cannot pass this."""
    import importlib.machinery
    import importlib.util
    mail_mod = clawgate._clawgate_env()
    repo = Path(clawgate.__file__).resolve().parents[2]
    sig_path = repo / "scripts" / "signal" / "clawgate.py"
    loader = importlib.machinery.SourceFileLoader("_sig_clawgate", str(sig_path))
    spec = importlib.util.spec_from_file_location("_sig_clawgate", str(sig_path),
                                                  loader=loader)
    sig = importlib.util.module_from_spec(spec)
    loader.exec_module(sig)
    assert Path(mail_mod.__file__).resolve() \
        == Path(sig._clawgate_env().__file__).resolve() \
        == (repo / "scripts" / "lib" / "clawgate_env.py").resolve()


def test_emit_task_posts_directory_payload_with_bearer(monkeypatch, tmp_path):
    _isolate_clawgate_env(monkeypatch, tmp_path)
    monkeypatch.setenv("CLAWGATE_HOOK_TOKEN", "tok-123")
    fake = _install_fake_requests(monkeypatch)

    ok = clawgate.emit_task(
        who="Acme", ask="Pay invoice", deadline="2026-08-01", amount="$5",
        source_ref="mail#9 x@acme.com",
    )
    assert ok is True
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["url"] == clawgate.ENDPOINT
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
