#!/usr/bin/env python3
"""Tests for the CLAUDE-corpus peer leg (`transcript_search.search_peers`).

🔴 WHY IT EXISTS. `opencode_search` has searched every host since 2026-08-26
while this module walked `~/.claude/projects` on the LOCAL machine only — and
the shipped `find-session` description said "searches both runtimes on **both
hosts**" the whole time. True of one corpus, false of the other. An
investigation asked "was the signal skill ever used operationally?", ran on the
workbench, got zero, and answered "never"; five sessions were sitting in the
laptop's Claude transcripts.

🔴 EVERY TEST HERE IS HERMETIC BY CONSTRUCTION, not by SSH being absent. SSH and
the peer hosts ARE reachable on the dev host and absent in the nix sandbox, so a
test that relied on absence would pass in one tier and fail in the other — the
exact failure `opencode_search.configured_peers`'s docstring records. The peer
list is emptied or replaced through `DEVRC_OPENCODE_PEERS`, and the subprocess
seam is patched.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "lib"))

import transcript_search as ts  # noqa: E402


@pytest.fixture(autouse=True)
def _no_real_peers(monkeypatch):
    """Nothing in this module may contact a real host."""
    monkeypatch.setenv("DEVRC_OPENCODE_PEERS", "")


def _fake_run(stdout=b"", stderr=b"", rc=0, boom=None):
    def run(*a, **k):
        if boom:
            raise boom
        return subprocess.CompletedProcess(a[0], rc, stdout, stderr)
    return run


def _one_peer(monkeypatch):
    monkeypatch.setenv("DEVRC_OPENCODE_PEERS", "laptop:10.42.0.100:zach")
    # Never "us", so the leg always attempts the remote call.
    import opencode_search
    monkeypatch.setattr(opencode_search, "_own_addresses", lambda: set())


class TestHermeticByConstruction:
    def test_no_peers_configured_means_no_rows_and_no_subprocess(self, monkeypatch):
        called = []
        monkeypatch.setattr(subprocess, "run",
                            lambda *a, **k: called.append(a) or _fake_run()(*a, **k))
        assert ts.search_peers(["x"]) == []
        assert called == [], "an empty peer list must not shell out at all"


class TestAPeerThatCannotAnswerIsNAMEDNotSilent:
    """🔴 THE WHOLE POINT. An unreachable peer and a peer with no matches are
    different facts; returning `[]` for both is the silent zero that produced
    the original wrong answer."""

    @pytest.mark.parametrize("kind,kwargs", [
        ("ssh raised", {"boom": OSError("no route to host")}),
        ("non-zero rc", {"rc": 255, "stderr": b"Permission denied"}),
        ("unparseable", {"stdout": b"not json at all"}),
        ("remote error", {"stdout": json.dumps(
            {"error": "peer is running an older transcript_search"}).encode()}),
    ])
    def test_it_warns_on_stderr_and_returns_no_rows(self, monkeypatch, capsys,
                                                    kind, kwargs):
        _one_peer(monkeypatch)
        monkeypatch.setattr(subprocess, "run", _fake_run(**kwargs))
        rows = ts.search_peers(["x"])
        err = capsys.readouterr().err
        assert rows == []
        assert "laptop" in err and "NOT in these results" in err, (
            f"{kind}: a peer that could not answer was not named on stderr")


class TestAPeerThatDOESAnswer:
    """The positive control. A leg that only ever warns is indistinguishable
    from one wired to nothing — measured live: the workbench holds ZERO vetr
    sessions and `--claude-only` returned two tagged `[claude-remote]`."""

    def _payload(self, session_id="remote1"):
        return json.dumps({"rows": [{
            "session_id": session_id, "cwd": "/home/zach/workspace/scratch/vetr",
            "branch": "main", "first": "2026-08-28T22:09:00+00:00",
            "last": "2026-08-28T22:10:00+00:00", "genesis": "hi",
            "matched_terms": ["x"], "total_hits": 6, "snippets": {},
            "path": "/home/zach/.claude/projects/-p/remote1.jsonl",
            "project_dir": "-p", "last_local": "2026-08-28T22:10:00",
        }]}).encode()

    def test_rows_come_back_tagged_with_their_host(self, monkeypatch, capsys):
        _one_peer(monkeypatch)
        monkeypatch.setattr(subprocess, "run", _fake_run(stdout=self._payload()))
        rows = ts.search_peers(["x"])
        assert len(rows) == 1
        assert rows[0]["host"] == "laptop"
        assert rows[0]["source"] == "claude-remote"
        assert capsys.readouterr().err == "", "a peer that answered must not warn"

    def test_last_local_is_a_datetime_so_the_merged_set_can_RANK(self, monkeypatch):
        """The merged list sorts on `last_local`; a string would raise or, worse,
        order lexically against real datetimes."""
        from datetime import datetime
        _one_peer(monkeypatch)
        monkeypatch.setattr(subprocess, "run", _fake_run(stdout=self._payload()))
        assert isinstance(ts.search_peers(["x"])[0]["last_local"], datetime)

    def test_a_peer_that_IS_us_is_skipped(self, monkeypatch):
        """The local walk already covers it; querying it again would duplicate
        every local hit."""
        monkeypatch.setenv("DEVRC_OPENCODE_PEERS", "laptop:10.42.0.100:zach")
        import opencode_search
        monkeypatch.setattr(opencode_search, "_own_addresses",
                            lambda: {"10.42.0.100"})
        called = []
        monkeypatch.setattr(subprocess, "run",
                            lambda *a, **k: called.append(a) or _fake_run()(*a, **k))
        assert ts.search_peers(["x"]) == []
        assert called == []


class TestTheCapabilityProbeIsInTheREMOTESCRIPT:
    """🔴 A peer that has not been shipped has no `--skill` support, and
    searching it WITHOUT the filter would return every term match as though it
    used the skill — a wrong answer dressed as a complete one. The probe lives
    in the remote script, so it is checked on the host that would answer."""

    def test_the_remote_script_refuses_skill_without_support(self):
        src = ts._REMOTE_SCAN
        assert "canonical_skill_name" in src, (
            "the remote script no longer probes for --skill support")
        assert "no --skill support" in src
        # and it must refuse rather than fall through to an unfiltered search
        probe = src.index("canonical_skill_name")
        assert "SystemExit" in src[probe:probe + 400]

    def test_the_remote_script_only_passes_skill_when_asked(self):
        assert 'if req.get("skill")' in ts._REMOTE_SCAN
