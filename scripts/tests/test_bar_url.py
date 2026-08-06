#!/usr/bin/env python3
"""`scripts/bar-url` — the resolver that keeps a host-specific URL OUT of this
public repo without turning the bar button that opens it into a dead button.

🔴 THESE ARE NOT REGRESSION TESTS. `bar-url` is new in the same commit, so every
case here is green at the base ref too — they are contract tests for a new
component, and are labelled as such rather than counted as regression coverage
(RULES.md → "a test you have not watched FAIL proves nothing").

The REGRESSION guard for the class this component exists to serve is
`test_no_client_hostnames.py`, which IS red at the base ref.

The seam that matters is covered in `test_bar_url_seam`: the bar block's
left-click (`nix/graphical.nix`) and the poller's toast action
(`scripts/bar-status-poll`) must both route through this one resolver, or the
hostname comes back in the file nobody re-checked.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from testlib import mockbin  # noqa: E402

BAR_URL = REPO / "scripts" / "bar-url"

#: A fictional host under a reserved TLD. Deliberately NOT the real value — the
#: whole point of the component under test is that the real value is not here.
FIXTURE_URL = "https://grafana.example.invalid/d/abc"


def _load():
    loader = importlib.machinery.SourceFileLoader("_bar_url", str(BAR_URL))
    spec = importlib.util.spec_from_loader("_bar_url", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


@pytest.fixture
def mod():
    return _load()


@pytest.fixture
def envfile(tmp_path, monkeypatch):
    p = tmp_path / "urls.env"
    p.write_text(
        "# bar urls\n"
        f"civitai_grafana={FIXTURE_URL}\n"
        "\n"
        "spaced_key  =  https://spaced.example.invalid  \n"
        "not-a-line-without-equals\n",
        encoding="utf-8")
    monkeypatch.setenv("BAR_URLS_ENV", str(p))
    return p


# --- resolution ----------------------------------------------------------------

def test_resolves_a_named_url(mod, envfile):
    assert mod.resolve("civitai_grafana") == FIXTURE_URL


def test_whitespace_around_key_and_value_is_stripped(mod, envfile):
    assert mod.resolve("spaced_key") == "https://spaced.example.invalid"


def test_comments_blank_lines_and_malformed_lines_are_ignored(mod, envfile):
    loaded = mod.load()
    assert set(loaded) == {"civitai_grafana", "spaced_key"}, loaded


def test_an_unset_name_resolves_to_none_not_an_exception(mod, envfile):
    assert mod.resolve("nope") is None


@pytest.mark.parametrize("blob", [
    b"civitai_grafana=https://ok.example.invalid/\xff\n",   # one stray byte
    b"\xff\xfe\x00c\x00i\x00v\x00",                          # UTF-16 BOM + text
    b"\x80\x81\x82",                                          # bare continuation bytes
], ids=["stray-byte", "utf16-bom", "invalid-continuation"])
def test_an_UNDECODABLE_file_is_an_empty_mapping_not_a_traceback(mod, tmp_path,
                                                                 monkeypatch, blob):
    """🔴 MEASURED REGRESSION. `UnicodeDecodeError` is NOT an `OSError`, so
    catching only `OSError` let it escape as an unhandled traceback with exit 1
    — contradicting this module's own 🔴 contract ("FAILS LOUD… exit 3 with a
    message naming the file and the key").

    Why it matters more than it looks: from a bar click **stderr goes nowhere**.
    A traceback is not "loud", it is invisible — so one stray byte in a
    hand-maintained file restored exactly the silent-dead-button failure this
    component exists to prevent.
    """
    p = tmp_path / "urls.env"
    p.write_bytes(blob)
    monkeypatch.setenv("BAR_URLS_ENV", str(p))
    assert mod.load() == {}
    assert mod.resolve("civitai_grafana") is None


@pytest.mark.parametrize("blob", [
    b"civitai_grafana=https://ok.example.invalid/\xff\n",
    b"\x80\x81\x82",
], ids=["stray-byte", "invalid-continuation"])
def test_cli_on_an_UNDECODABLE_file_exits_3_not_1(tmp_path, blob):
    """The end-to-end half of the case above: the CLI's exit code and message
    are the contract, and exit 1 with a traceback is a different contract.
    Distinguishing 3 from 1 is the whole point — 1 is "it crashed", 3 is "here
    is the key and the file to fix"."""
    p = tmp_path / "urls.env"
    p.write_bytes(blob)
    r = _run(["civitai_grafana"], p)
    assert r.returncode == 3, (r.returncode, r.stdout, r.stderr)
    assert "civitai_grafana" in r.stderr and str(p) in r.stderr
    assert "Traceback" not in r.stderr, r.stderr


def test_a_missing_file_is_an_empty_mapping_not_a_crash(mod, tmp_path, monkeypatch):
    """A host that has never created the file must reach the SAME loud per-name
    error as a host missing one key — not an unhandled traceback out of a
    left-click handler, which the bar swallows."""
    monkeypatch.setenv("BAR_URLS_ENV", str(tmp_path / "absent.env"))
    assert mod.load() == {}
    assert mod.resolve("civitai_grafana") is None


def test_the_env_var_overrides_the_default_path(mod, envfile, monkeypatch):
    assert mod.env_path() == str(envfile)
    monkeypatch.delenv("BAR_URLS_ENV")
    assert mod.env_path().endswith("/.config/bar/urls.env")


# --- the CLI contract (what graphical.nix and bar-status-poll invoke) ----------

def _run(args, env_path, extra_env=None):
    env = dict(os.environ, BAR_URLS_ENV=str(env_path))
    env.update(extra_env or {})
    return subprocess.run([sys.executable, str(BAR_URL), *args],
                          capture_output=True, text=True, env=env, timeout=20)


def test_cli_prints_the_url_and_exits_zero(envfile):
    r = _run(["civitai_grafana"], envfile)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == FIXTURE_URL


def test_cli_fails_LOUD_on_an_unset_name(envfile):
    """🔴 The failure mode this component exists to avoid is a button that
    silently does nothing. Exit 3, and the message must name BOTH the key and
    the file, so the fix is legible without reading the source."""
    r = _run(["civitai_grafana_typo"], envfile)
    assert r.returncode == 3, (r.returncode, r.stdout, r.stderr)
    assert "civitai_grafana_typo" in r.stderr
    assert str(envfile) in r.stderr


def test_cli_usage_error_is_distinct_from_an_unset_name(envfile):
    """Two different failures must not share an exit code, or the operator
    cannot tell 'you typo'd the key' from 'you typo'd the command'."""
    assert _run([], envfile).returncode == 2
    assert _run(["a", "b"], envfile).returncode == 2


def test_cli_open_does_not_go_through_a_shell(tmp_path, monkeypatch):
    """🔴 The URL is argv[1] to xdg-open, never a shell word. A value carrying
    `;` or backticks must reach xdg-open INTACT rather than being executed.

    Driven with a fake `xdg-open` on PATH that records its argv — a control that
    would go red if the implementation ever switched to `shell=True`.
    """
    bindir = tmp_path / "bin"
    bindir.mkdir()
    recorded = tmp_path / "argv.txt"
    # mockbin owns the shebang: `/usr/bin/env` does not exist in the nix build
    # sandbox, so a hand-written one is green here and red on the gating tier
    # (scripts/testlib/mockbin.py; scripts/tests/test_runtime_shebangs.py).
    mockbin.write_exec(bindir / "xdg-open", 'printf "%s\\n" "$@" > "$REC"\n')

    nasty = "https://ok.example.invalid/a;touch /tmp/pwned`id`"
    env = tmp_path / "urls.env"
    env.write_text(f"k={nasty}\n", encoding="utf-8")

    r = _run(["--open", "k"], env,
             extra_env={"PATH": f"{bindir}:{os.environ['PATH']}",
                        "REC": str(recorded)})
    assert r.returncode == 0, r.stderr
    assert recorded.read_text().strip() == nasty, (
        "xdg-open did not receive the URL as ONE intact argv element — it went "
        "through a shell")


# --- the seam ------------------------------------------------------------------

def test_bar_url_seam():
    """🔴 SEAM GUARD. Two surfaces open the client dashboard — the bar block's
    left-click and the poller's rising-edge toast. Both must resolve through
    THIS script, and the literal must appear in NEITHER. Pinning the
    RELATIONSHIP is the point: each file is clean in isolation, and the bug this
    forbids is one of them quietly re-inlining the URL.

    🔴 THE STRUCTURAL HALF IS NOT ENOUGH — see
    `test_the_toast_action_is_an_executable_bar_url_invocation` below. Asserting
    that a call APPEARS is a check on spelling; it type-checks past a wrong
    argument. Both surfaces need a behavioural assertion, and only the nix side
    is genuinely text-only (it is a nix string literal, not runnable here).
    """
    nix = (REPO / "nix" / "graphical.nix").read_text(encoding="utf-8")
    poll = (REPO / "scripts" / "bar-status-poll").read_text(encoding="utf-8")

    assert "bar-url --open civitai_grafana" in nix, (
        "the civitai block's left-click no longer routes through bar-url")
    assert "_bar_url_action(\"civitai_grafana\")" in poll, (
        "the civitai toast action no longer routes through bar-url")

    # STRUCTURAL, not spelled: no `xdg-open https://…` on a civitai line in
    # either file. Asserting the absence of one hostname would pass the moment
    # somebody used a different one.
    for name, text in (("graphical.nix", nix), ("bar-status-poll", poll)):
        for i, line in enumerate(text.splitlines(), 1):
            if "civitai" not in line.lower():
                continue
            assert "xdg-open http" not in line, (
                f"{name}:{i} inlines a URL on a civitai line again: {line.strip()}")


def _load_poller(monkeypatch):
    """Load `scripts/bar-status-poll` by explicit path, the way the repo's other
    tests load it — it has no `.py` suffix, so it is not importable.

    🔴 `DEVRC_DIR` is pinned at THIS checkout. The poller reads it at import time
    and defaults to `~/workspace/devrc`, so without this the tests would assert
    against whatever the developer's base clone happens to contain — green or
    red for reasons that have nothing to do with the change under test. (It is
    also how this asymmetry became visible: the toast resolves `bar-url` out of
    the GIT CHECKOUT, while the bar block's left-click uses the NIX-DEPLOYED
    copy under `~/.config/i3status-rust/scripts`. Two copies of one script, and
    only one of them is updated by `home-manager switch`. Reported, not changed
    here — it is a behaviour change to a live bar component.)
    """
    import importlib.machinery
    import importlib.util
    monkeypatch.setenv("DEVRC_DIR", str(REPO))
    path = REPO / "scripts" / "bar-status-poll"
    loader = importlib.machinery.SourceFileLoader("_bar_status_poll", str(path))
    spec = importlib.util.spec_from_loader("_bar_status_poll", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def test_the_toast_action_resolves_bar_url_from_the_repo_root_handle(monkeypatch):
    """Pin WHERE the toast looks, because it is not where the bar block looks.

    INVARIANT GUARD, labelled as such: it is green at the base ref too. It
    exists so the two-copies asymmetry above is visible in the suite rather than
    only in a report — and so a future change to the resolution root is a
    decision someone makes on purpose.
    """
    poll = _load_poller(monkeypatch)
    action = poll._bar_url_action("civitai_grafana")
    assert action.split()[0] == str(REPO / "scripts" / "bar-url"), action


def test_the_toast_action_is_an_executable_bar_url_invocation(monkeypatch):
    """🔴 THE TOAST HALF, asserted on what the function RETURNS.

    The seam guard above only checked that the literal `_bar_url_action("…")`
    APPEARS in the poller. MEASURED: mutating that function's body to
    `"--openn " + name + "_typo"` left 150 tests passing and 0 failing, and
    repointing it at a non-existent `scripts/bar-urls` left 135 passing — both
    make the toast COMPLETELY INERT. A structural check type-checks past a wrong
    argument (RULES.md → "isolation-seam").

    The toast is the half with no other coverage: `test_bar_status.py` (122
    tests) asserts nothing about the civitai action. And its stated purpose is
    that it can never be silently dead — a dunst action that fails produces no
    output anywhere a human looks.
    """
    poll = _load_poller(monkeypatch)
    action = poll._bar_url_action("civitai_grafana")

    parts = action.split()
    assert len(parts) == 3, f"the action is not a 3-token invocation: {action!r}"
    target, flag, key = parts
    assert flag == "--open", f"wrong flag: {flag!r} (the toast would not open)"
    assert key == "civitai_grafana", f"wrong key: {key!r}"

    # 🔴 The target must EXIST and be RUNNABLE. A path that is merely
    # well-formed is what the `scripts/bar-urls` mutant produced.
    t = Path(target)
    assert t.name == "bar-url", f"the action does not invoke bar-url: {target!r}"
    assert t.is_file(), f"the toast action points at a missing file: {target}"
    assert os.access(t, os.X_OK), f"the toast action target is not executable: {target}"


def _stub_xdg_open(tmp_path):
    """🔴 MANDATORY for any test that runs the `--open` path.

    The toast action ends in `--open`, and `--open` execs the REAL `xdg-open`.
    The first version of the test below did exactly that and **launched a
    browser tab on the developer's desktop** — a test suite must not touch the
    user's session. The stub records argv instead, which is also the only way to
    assert the URL reached xdg-open intact.
    """
    bindir = tmp_path / "xdgbin"
    bindir.mkdir(exist_ok=True)
    rec = tmp_path / "xdg-argv.txt"
    mockbin.write_exec(bindir / "xdg-open", f'printf "%s\\n" "$@" > "{rec}"\n')
    return bindir, rec


def test_the_toast_action_actually_resolves_a_url_end_to_end(tmp_path, monkeypatch):
    """The complement: the invocation the toast builds must, when RUN, print the
    URL, exit 0, and hand that URL to xdg-open. Everything above is about the
    shape of a string; this exercises the shape as a COMMAND, which is the only
    thing dunst does with it."""
    poll = _load_poller(monkeypatch)
    env_file = tmp_path / "urls.env"
    env_file.write_text(f"civitai_grafana={FIXTURE_URL}\n", encoding="utf-8")
    bindir, rec = _stub_xdg_open(tmp_path)

    action = poll._bar_url_action("civitai_grafana")
    r = subprocess.run([sys.executable, *action.split()],
                       capture_output=True, text=True, timeout=20,
                       env=dict(os.environ, BAR_URLS_ENV=str(env_file),
                                PATH=f"{bindir}:{os.environ['PATH']}"))
    assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
    assert r.stdout.strip() == FIXTURE_URL, r.stdout
    assert rec.exists(), "the toast action never reached xdg-open"
    assert rec.read_text().strip() == FIXTURE_URL, rec.read_text()


def test_the_toast_action_fails_loud_on_an_unset_key(tmp_path, monkeypatch):
    """And the failure path, through the same constructed command: exit 3 with
    the key and the file named. A toast whose action exits 0 having done nothing
    is the silent-dead-button this whole indirection exists to prevent."""
    poll = _load_poller(monkeypatch)
    env_file = tmp_path / "urls.env"
    env_file.write_text("something_else=https://x.example.invalid/\n", encoding="utf-8")

    action = poll._bar_url_action("civitai_grafana")
    r = subprocess.run([sys.executable, *action.split()],
                       capture_output=True, text=True, timeout=20,
                       env=dict(os.environ, BAR_URLS_ENV=str(env_file)))
    assert r.returncode == 3, (r.returncode, r.stdout, r.stderr)
    assert "civitai_grafana" in r.stderr and str(env_file) in r.stderr


def test_bar_url_is_deployed_by_home_manager():
    """A NEW file that is not wired into `home.file` is silently absent after a
    switch (CLAUDE.md), which would leave the button pointing at nothing — the
    exact outcome this change exists to prevent."""
    nix = (REPO / "nix" / "graphical.nix").read_text(encoding="utf-8")
    assert 'i3status-rust/scripts/bar-url"' in nix
    assert "../scripts/bar-url" in nix


def test_bar_url_is_executable():
    assert os.access(BAR_URL, os.X_OK), "home.file executable=true is not enough"
