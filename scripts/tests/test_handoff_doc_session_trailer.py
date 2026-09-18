"""`handoff_doc.py` stamps its OWN commit with `Claude-Session-Id:`.

Why the tool does this rather than leaning on `prepare-commit-msg`: that hook is
installed PER CLONE (`install-session-stamp.sh` writes into the clone's common
git dir), so a clone without it produces unstamped commits and those writers drop
out of the arc silently. This arc's own originating commit is unstamped for
exactly that reason.

🔴 THE HOOK-PRESENT CASE IS THE DEFAULT, NOT THE EDGE CASE, and that is measured
rather than assumed: a real `--confirm --push` run from a worktree of
`~/workspace/devrc` on 2026-09-18 produced a commit already carrying the trailer,
because every worktree shares the base clone's common git dir. So the
double-stamp test below is the one guarding real behaviour; the no-hook test is
the one guarding the reason this code exists at all.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

from lib import handoff_doc as hd  # noqa: E402
from lib import session_trailer as st  # noqa: E402
from testlib.hermetic_git import hermetic_git_env  # noqa: E402
from testlib.mockbin import write_exec  # noqa: E402

SID = "aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa"
OTHER = "bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb"


class TestResolveSessionId:
    def test_opencode_wins_over_claude_code(self):
        """Precedence copied from `clawgate_handoff.sh`, and it is not cosmetic:
        a `CLAUDE_CODE_SESSION_ID` visible inside opencode may be an ANCESTOR's,
        so reading it first misattributes the commit."""
        env = {"OPENCODE_SESSION_ID": SID, "CLAUDE_CODE_SESSION_ID": OTHER}
        assert hd.resolve_session_id(env) == SID

    def test_claude_code_is_used_when_opencode_is_absent(self):
        assert hd.resolve_session_id({"CLAUDE_CODE_SESSION_ID": SID}) == SID

    def test_an_empty_value_is_not_an_id(self):
        env = {"OPENCODE_SESSION_ID": "  ", "CLAUDE_CODE_SESSION_ID": SID}
        assert hd.resolve_session_id(env) == SID

    def test_the_env_order_ledger_matches_what_the_resolver_reads(self):
        """Two-way: the documented order IS the order, so the constant cannot
        drift from the behaviour it claims to describe."""
        assert hd.SESSION_ID_ENV_ORDER == (
            "OPENCODE_SESSION_ID", "CLAUDE_CODE_SESSION_ID")
        env = {name: f"{i}1111111-1111-4111-8111-111111111111"
               for i, name in enumerate(hd.SESSION_ID_ENV_ORDER, start=1)}
        assert hd.resolve_session_id(env) == env[hd.SESSION_ID_ENV_ORDER[0]]

    def test_a_name_that_does_not_exist_is_not_read(self):
        """There is no `CLAUDE_SESSION_ID`. Reading a name that does not exist is
        how a resolver returns '' forever while looking correct."""
        assert "CLAUDE_SESSION_ID" not in hd.SESSION_ID_ENV_ORDER


class TestCommitMessage:
    def test_a_known_id_becomes_a_trailer(self):
        msg = hd.commit_message("docs(handoff): a subject", session_id=SID)
        assert msg.splitlines()[0] == "docs(handoff): a subject"
        assert f"{st.TRAILER_KEY}: {SID}" in msg

    def test_NO_id_means_NO_trailer_and_never_a_placeholder(self):
        """🔴 A placeholder would be worse than nothing: the arc reader counts a
        stamped commit as attributable, so every unresolvable commit across every
        repo would resolve to one imaginary session."""
        msg = hd.commit_message("docs(handoff): a subject", session_id="")
        assert msg == "docs(handoff): a subject"
        assert st.TRAILER_KEY not in msg

    def test_stamping_twice_yields_exactly_ONE_trailer(self):
        once = hd.commit_message("docs(handoff): s", session_id=SID)
        twice = hd.commit_message(once, session_id=SID)
        assert twice.count(f"{st.TRAILER_KEY}:") == 1

    def test_the_subject_is_not_truncated_by_the_trailer(self):
        long_subject = "docs(handoff): " + "x" * 80
        msg = hd.commit_message(long_subject, session_id=SID)
        assert msg.splitlines()[0] == long_subject
        assert SID in msg


def _sh(*args: str, cwd: Path, env=None) -> str:
    res = subprocess.run(args, cwd=str(cwd), capture_output=True, text=True,
                         env=env or hermetic_git_env(), timeout=60)
    assert res.returncode == 0, f"{args} failed: {res.stderr}"
    return res.stdout


def _repo_with_hook(tmp_path: Path, hook_body: str) -> Path:
    work = tmp_path / "w"
    work.mkdir()
    _sh("git", "init", "-q", "-b", "main", cwd=work)
    write_exec(work / ".git" / "hooks" / "prepare-commit-msg", hook_body)
    return work


class TestTheTwoWritersCompose:
    """The seam: this tool AND the hook may both run on one commit."""

    def test_with_a_hook_ALSO_stamping_there_is_exactly_ONE_trailer(self, tmp_path):
        """🔴 THE DEFAULT STATE ON A DEVELOPED CLONE, measured, not imagined.

        ⚠ READ WHAT THIS DOES AND DOES NOT COVER. The stand-in hook below is
        idempotent — it checks for the key and exits — because the REAL hook is
        (`prepare_commit_msg.py` routes through `session_trailer.append_trailer`).
        So this test pins the realistic seam: our writer plus a well-behaved hook
        yields one trailer. It does NOT catch a naive appender inside
        `commit_message`; a mutation making that blind append was measured to
        leave this test GREEN and was killed only by
        `test_stamping_twice_yields_exactly_ONE_trailer`. That unit test is the
        guard for the appender; this one is the guard for the composition.

        An unconditional stand-in hook is deliberately NOT used: nothing in this
        module could make such a hook produce one trailer, so the test would pin
        a guarantee the code cannot offer.
        """
        # 🔴 NO SHEBANG HERE — `mockbin.write_exec` owns it, and a repo guard
        # (`test_runtime_shebangs.py`) fails any test that writes its own. That
        # guard caught this exact line on the merged-tree gate run.
        hook = (
            f"grep -q '^{st.TRAILER_KEY}:' \"$1\" && exit 0\n"
            f"printf '\\n{st.TRAILER_KEY}: {SID}\\n' >> \"$1\"\n"
            "exit 0\n"
        )
        work = _repo_with_hook(tmp_path, hook)
        (work / "f.md").write_text("x\n", encoding="utf-8")
        _sh("git", "add", "--", "f.md", cwd=work)
        _sh("git", "commit", "-m", hd.commit_message("subject", session_id=SID),
            "--", "f.md", cwd=work)
        body = _sh("git", "log", "-1", "--format=%B", cwd=work)
        assert body.count(f"{st.TRAILER_KEY}:") == 1, (
            f"expected exactly one trailer, message was:\n{body}")

    def test_with_NO_hook_the_tool_still_stamps(self, tmp_path):
        """The clones this code exists for. Without it these commits are
        invisible to the arc reader forever."""
        work = tmp_path / "nohook"
        work.mkdir()
        _sh("git", "init", "-q", "-b", "main", cwd=work)
        (work / "f.md").write_text("x\n", encoding="utf-8")
        _sh("git", "add", "--", "f.md", cwd=work)
        _sh("git", "commit", "-m", hd.commit_message("subject", session_id=SID),
            "--", "f.md", cwd=work)
        body = _sh("git", "log", "-1", "--format=%B", cwd=work)
        assert body.count(f"{st.TRAILER_KEY}: {SID}") == 1

    def test_the_arc_reader_can_read_back_what_this_tool_wrote(self, tmp_path):
        """🔴 THE SEAM ASSERTION: writer and reader are separate modules and were
        tested separately; this is the only test that builds the combined state.
        A trailer written in a form the reader cannot parse is a defect neither
        module's own suite can see."""
        from lib import handoff_arc as ha

        work = tmp_path / "seam"
        work.mkdir()
        _sh("git", "init", "-q", "-b", "main", cwd=work)
        (work / "claudedocs").mkdir()
        doc = "claudedocs/handoff-seam.md"
        (work / doc).write_text("x\n", encoding="utf-8")
        _sh("git", "add", "--", doc, cwd=work)
        _sh("git", "commit", "-m", hd.commit_message("docs(handoff): seam",
                                                     session_id=SID),
            "--", doc, cwd=work)

        report = ha.resolve_arc(str(work), doc, readers_measured=True)
        assert [m.session_id for m in report.members] == [SID]
        assert report.unstamped_commits == 0
        assert report.total_commits == 1
