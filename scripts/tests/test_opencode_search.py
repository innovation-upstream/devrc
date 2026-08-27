#!/usr/bin/env python3
"""Tests for `scripts/lib/opencode_search.py` — the opencode-session half of find-session.

🔴 WHICH TESTS ARE REGRESSION COVERAGE, AND AGAINST WHICH BASE.

  `RED_AT_CF4ABDE9` — watched FAIL against cf4abde9, this branch's own first head, where
                      `REMOTE_HOST` was a single hardcoded `zach@10.42.0.30`. MEASURED on
                      2026-08-26 from both machines before the fix:

                        term         laptop DB   workbench DB   found FROM laptop   FROM workbench
                        sensei               0             46                  46               46
                        verify-117           3              0                   3                0

                      The last cell is the bug. From the workbench the SSH leg was a
                      self-connection ("Permission denied"), `except Exception: pass`
                      swallowed it, and the laptop's 377 sessions were permanently
                      invisible with no diagnostic on stderr.

Everything not in that list is an invariant guard — it pins something worth keeping, but
is NOT evidence that a bug was fixed.
"""
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import opencode_search as ocs  # noqa: E402

RED_AT_CF4ABDE9 = {
    "test_the_host_we_are_running_on_is_never_an_ssh_target",
    "test_every_peer_that_is_not_us_is_queried",
    "test_an_unreachable_peer_warns_instead_of_returning_a_silent_empty",
}


@pytest.fixture
def spy(monkeypatch):
    """Record which peers went down the local path and which down the SSH path."""
    calls = {"local": [], "ssh": []}

    def fake_query_db(db_path, terms, patterns, **kw):
        calls["local"].append(kw.get("label"))
        return []

    def fake_query_remote(ssh_target, label, terms, patterns, **kw):
        calls["ssh"].append((label, ssh_target))
        return []

    monkeypatch.setattr(ocs, "_query_db", fake_query_db)
    monkeypatch.setattr(ocs, "_query_remote", fake_query_remote)
    return calls


# --- the regression: symmetry across hosts -----------------------------------------

@pytest.mark.parametrize("own_addr,expected_local,expected_ssh", [
    ("10.42.0.30", "workbench", ("laptop", "zach@10.42.0.100")),
    ("10.42.0.100", "laptop", ("workbench", "zach@10.42.0.30")),
])
def test_the_host_we_are_running_on_is_never_an_ssh_target(
        monkeypatch, spy, own_addr, expected_local, expected_ssh):
    """Self-SSH is the whole bug. Whichever peer we ARE is read off local disk.

    Parameterised over BOTH hosts deliberately: the pre-fix code was correct from
    the laptop and broken from the workbench, so a single-host measurement would
    have passed while the defect was live.
    """
    monkeypatch.setattr(ocs, "_own_addresses", lambda: {own_addr})
    ocs.search_opencode(["anything"])

    assert spy["local"] == [expected_local]
    assert spy["ssh"] == [expected_ssh]
    assert own_addr not in [t for _, t in spy["ssh"]], \
        f"{own_addr} is this host — it must never be an SSH target"


def test_every_peer_that_is_not_us_is_queried(monkeypatch, spy):
    """The peer set is covered exactly once: no host silently dropped, none doubled."""
    monkeypatch.setattr(ocs, "_own_addresses", lambda: {"10.42.0.30"})
    ocs.search_opencode(["anything"])

    covered = set(spy["local"]) | {label for label, _ in spy["ssh"]}
    assert covered == {label for label, _, _ in ocs.PEERS}
    assert len(spy["local"]) + len(spy["ssh"]) == len(ocs.PEERS), "a peer was queried twice"


def test_a_host_outside_the_peer_table_still_searches_its_own_db(monkeypatch, spy):
    """Address detection failing must not mean we skip our OWN sessions."""
    monkeypatch.setattr(ocs, "_own_addresses", set)
    ocs.search_opencode(["anything"])

    assert spy["local"] == ["local"], "local DB must be searched even when we match no peer"
    assert len(spy["ssh"]) == len(ocs.PEERS), "all peers are remote when none is us"


# --- the regression: a degraded leg must be audible --------------------------------

def test_an_unreachable_peer_warns_instead_of_returning_a_silent_empty(
        monkeypatch, capsys):
    """An unreachable peer and a peer with no matches must not print the same thing.

    Pre-fix, `except Exception: pass` made an incomplete result set indistinguishable
    from an honest zero — which is exactly how the workbench gap went unnoticed.
    """
    monkeypatch.setattr(ocs, "_own_addresses", lambda: {"10.42.0.30"})
    monkeypatch.setattr(ocs, "_query_db", lambda *a, **k: [])

    def refuse(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=[], returncode=255, stdout=b"",
            stderr=b"zach@10.42.0.100: Permission denied (publickey).")

    monkeypatch.setattr(ocs.subprocess, "run", refuse)
    results = ocs.search_opencode(["anything"])

    err = capsys.readouterr().err
    assert results == []
    assert "laptop" in err, "the warning must name WHICH peer is missing"
    assert "NOT in these results" in err, \
        "the warning must say the result set is incomplete, not merely that something failed"


def test_a_missing_local_database_warns(monkeypatch, capsys):
    """A host with no opencode DB is a legitimate state — but it is still a gap."""
    monkeypatch.setattr(ocs, "_own_addresses", lambda: {"10.42.0.30"})
    monkeypatch.setattr(ocs, "_query_remote", lambda *a, **k: [])
    monkeypatch.setattr(ocs, "LOCAL_DB", Path("/nonexistent/opencode-stable.db"))
    ocs.search_opencode(["anything"])

    assert "no database at" in capsys.readouterr().err


# --- invariant guards ---------------------------------------------------------------

def test_results_are_tagged_with_the_peer_they_came_from(monkeypatch):
    """`path` must identify the HOST, so a merged result set stays attributable.

    Pre-fix the remote script hardcoded `opencode:workbench` for whatever host it ran
    on, which would mislabel every laptop row the moment a second peer was added.
    """
    monkeypatch.setattr(ocs, "_own_addresses", lambda: {"10.42.0.30"})
    assert '"opencode:" + label' in ocs._REMOTE_SEARCH_SCRIPT, \
        "the remote script must tag rows with the peer label it was given"
    assert "opencode:workbench" not in ocs._REMOTE_SEARCH_SCRIPT, \
        "a hardcoded host label mislabels every other peer's rows"


def test_the_remote_script_receives_the_label_it_must_tag_with():
    """Seam guard: the caller sends `label`, the remote script reads `label`."""
    assert '"label": label' in _source_of(ocs._query_remote)
    assert 'payload.get("label"' in ocs._REMOTE_SEARCH_SCRIPT


def test_the_red_at_base_ledger_names_real_tests():
    """A ledger naming a test that does not exist reads as coverage while providing none."""
    here = set(globals())
    missing = RED_AT_CF4ABDE9 - here
    assert not missing, f"ledger names tests that do not exist: {sorted(missing)}"


def _source_of(fn):
    import inspect
    return inspect.getsource(fn)
