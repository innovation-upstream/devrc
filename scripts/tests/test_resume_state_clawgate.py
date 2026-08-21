"""The clawgate<->handoff seam: `scripts/lib/clawgate_handoff.sh` + the CLAWGATE
block it gives `scripts/resume-state.sh`.

WHAT THIS GATES
---------------
`/handoff` records the clawgate task a session's work belongs to as YAML front
matter (`clawgate-task: <id>`) at the top of the handoff doc; `/resume` reads it
back and reconciles it against the LIVE board. Three failure shapes are what the
suite is built around, and each is the same disease this repo keeps meeting:

  1. 🔴 A SILENT ZERO. clawgate unreachable, unauthorised, missing from PATH, or
     answering without a status must ALL print a `!` gap in DRIFT. The `/resume`
     convention is explicit that an empty DRIFT means nothing unless something
     was actually reconciled, and a `!` line is how a source says it did not
     answer. `test_a_dead_clawgate_*` are the load-bearing tests here.
  2. 🔴 AN EMPTY ARRAY READ AS A CLEAN RESULT. `GET /api/sessions/{id}/tasks`
     answers `200 {"tasks":[]}` for an UNKNOWN session — not 404 — so an empty
     result cannot distinguish "this session touched no task" from "the id is
     wrong" (claude/RULES.md: an empty result cannot distinguish two
     mechanisms). `resolve` must say so rather than report a clean resolution.
  3. 🔴 A CONSTANT THAT IS NEVER UNDER TEST. The session id comes from
     `CLAUDE_CODE_SESSION_ID`; there is no `CLAUDE_SESSION_ID`, and reading the
     name that does not exist ships a feature that is INERT and indistinguishable
     from a working one. Both names are written as LITERALS in this file and
     never imported from the subject, and the negative control sets the wrong
     one and watches the tool refuse.

     ⚠ THE HARNESS ITSELF NEARLY WALKED THAT CONTROL. `CLAUDE_CODE_SESSION_ID`
     is set in the environment of any real Claude Code session, so a subprocess
     that inherits `os.environ` resolves a task no matter which name the code
     reads — measured: the first run of the negative control passed while the
     wrong variable was set, because the RIGHT one was inherited. `_base_env`
     pops both, and `test_the_harness_carries_neither_session_variable` pins it.

HERMETIC. No test reaches the network: `curl`, `gh` and `kubectl` are tripwire
stubs on the front of $PATH that log and fail, `clawgatectl` is a stub whose
answer each test supplies, and every fixture repo is a throwaway `git init`
under tmp_path with no remote and no prod-kubeconfig. `conftest.py`'s
no-launcher policy is unaffected — nothing here launches a real binary.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from testlib.mockbin import write_exec  # noqa: E402

LIB = REPO_ROOT / "scripts" / "lib" / "clawgate_handoff.sh"
RESUME = REPO_ROOT / "scripts" / "resume-state.sh"
HANDOFF_SKILL = REPO_ROOT / "claude" / "skills" / "handoff" / "SKILL.md"
RESUME_SKILL = REPO_ROOT / "claude" / "skills" / "resume" / "SKILL.md"
HANDOFF_DOC_TOOL = REPO_ROOT / "scripts" / "lib" / "handoff_doc.py"

# 🔴 LITERALS, written out here and NEVER read from the subject. A harness that
# derives the variable name from the code it is testing cannot see the code
# reading the wrong name — that is exactly how the feature this follows shipped
# inert. `WRONG_SESSION_VAR` is a real, plausible spelling that does not exist.
SESSION_VAR = "CLAUDE_CODE_SESSION_ID"
WRONG_SESSION_VAR = "CLAUDE_SESSION_ID"

# A token value that appears nowhere else, so a leak into argv or into output is
# unambiguous rather than a substring coincidence.
SENTINEL_TOKEN = "tok-SENTINEL-9f2b41"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None
    or shutil.which("git") is None
    or shutil.which("jq") is None,
    reason="needs bash + git + jq on PATH",
)


# --------------------------------------------------------------------------- #
# harness
# --------------------------------------------------------------------------- #
def _base_env() -> dict:
    """os.environ MINUS anything that could answer a question for the subject.

    🔴 Both session-id spellings are popped. See the module docstring: the real
    one is present in every Claude Code session, and inheriting it made the
    negative control pass while the code read the wrong name.
    """
    env = dict(os.environ)
    env.pop(SESSION_VAR, None)
    env.pop(WRONG_SESSION_VAR, None)
    return env


def call_fn(fn: str, *args: str, env: dict | None = None):
    """Source the lib and call one PURE function on fixture text.

    🔴 `$0` is deliberately NOT the lib's own path. The lib ends with the
    standard `[[ "${BASH_SOURCE[0]}" == "${0}" ]]` main guard, so passing the
    path as `$0` would EXECUTE it instead of sourcing it, and every pure-function
    assertion would silently be measuring the CLI. Pinned by
    `test_sourcing_the_lib_executes_nothing`.

    🔴 A FAILED SOURCE IS AN ERROR, NOT AN EMPTY ANSWER. Half the assertions
    here are of the form "this input yields NOTHING", and a lib that could not
    be sourced yields nothing too — MEASURED against the pre-change tree, where
    five such tests passed with the file absent. Exit 97 is unreachable from any
    function in the lib, so it can only mean the source itself failed.
    """
    r = subprocess.run(
        [
            "bash",
            "-c",
            f'set -uo pipefail; source "$1" || exit 97; shift 1; {fn} "$@"',
            "harness",
            str(LIB),
            *args,
        ],
        capture_output=True,
        text=True,
        timeout=30,
        env=env or _base_env(),
    )
    assert r.returncode != 97 and "No such file" not in r.stderr, (
        f"the lib could not be SOURCED — every 'yields nothing' assertion below "
        f"would pass vacuously.\n{r.stderr}"
    )
    return r


def fm(value: str | None = "193", *, before: str = "", key: str = "clawgate-task") -> str:
    """A handoff doc, optionally carrying front matter."""
    block = "" if value is None else f"---\n{key}: {value}\n---\n"
    return f"{before}{block}# Handoff: sample — 2026-08-19\n\n## Goal\nkeep the widget queue draining.\n"


# --------------------------------------------------------------------------- #
# §1 front-matter parsing — pure
# --------------------------------------------------------------------------- #
class TestFrontMatterParsing:
    def test_a_recorded_task_id_is_read_back(self):
        """POSITIVE CONTROL for every negative case below: the parser can, in
        fact, read a real id out of a real doc."""
        r = call_fn("clawgate_task_field", fm("193"))
        assert r.stdout.strip() == "193"
        assert r.returncode == 0

    def test_a_doc_with_no_front_matter_yields_nothing(self):
        r = call_fn("clawgate_task_field", fm(None))
        assert r.stdout.strip() == ""
        assert r.returncode == 1

    def test_a_doc_with_no_front_matter_is_not_reported_as_PRESENT(self):
        assert call_fn("clawgate_field_present", fm(None)).returncode == 1

    def test_a_malformed_value_yields_no_id(self):
        """`clawgate-task: TBD` names no task. Printing it would send `TBD` to
        `clawgatectl task get`, producing a confident "did not answer" about a
        task nobody ever named."""
        r = call_fn("clawgate_task_field", fm("TBD"))
        assert r.stdout.strip() == ""
        assert r.returncode == 1

    def test_a_malformed_value_IS_still_reported_PRESENT(self):
        """🔴 The two questions have different answers here, and that is the
        whole reason `clawgate_field_present` exists: the WRITER must not append
        a second field beside an unreadable one, while the READER must not
        pretend the unreadable one names a task."""
        assert call_fn("clawgate_field_present", fm("TBD")).returncode == 0

    def test_an_empty_value_is_present_but_unreadable(self):
        assert call_fn("clawgate_field_present", fm("")).returncode == 0
        assert call_fn("clawgate_task_field", fm("")).returncode == 1

    def test_front_matter_that_is_not_at_the_top_is_not_front_matter(self):
        """A `---` further down a markdown doc is a horizontal rule. If one
        could open a front-matter block, arbitrary body prose would mint a task
        id — and /resume would reconcile against it."""
        r = call_fn("clawgate_task_field", fm("193", before="# Handoff\n\nsome prose\n\n"))
        assert r.stdout.strip() == ""
        assert r.returncode == 1

    def test_a_horizontal_rule_in_prose_cannot_mint_a_task_id(self):
        """ADVERSARIAL, and not the same case as the one above: here the doc
        does start with `---`… but only after a blank line, which is the shape a
        loosened `^---` scan would accept."""
        r = call_fn("clawgate_task_field", "\n---\nclawgate-task: 999\n---\n")
        assert r.stdout.strip() == ""

    def test_a_quoted_value_is_read(self):
        assert call_fn("clawgate_task_field", fm('"193"')).stdout.strip() == "193"

    def test_the_first_key_wins_and_the_scan_stops_at_the_closing_delimiter(self):
        """GUARD (not regression coverage — no bug produced this). A duplicated
        key is malformed YAML; picking the LAST one would make which task you
        reconcile depend on append order, which is not a choice anybody made."""
        text = "---\nclawgate-task: 11\nclawgate-task: 22\n---\nclawgate-task: 33\n"
        assert call_fn("clawgate_task_field", text).stdout.strip() == "11"

    def test_a_key_after_the_closing_delimiter_is_invisible(self):
        text = "---\ntitle: x\n---\nclawgate-task: 44\n"
        assert call_fn("clawgate_task_field", text).returncode == 1

    def test_an_unclosed_front_matter_block_yields_nothing_when_the_key_is_absent(self):
        assert call_fn("clawgate_task_field", "---\ntitle: x\nno closing delimiter\n").returncode == 1

    def test_a_similarly_named_key_is_not_the_field(self):
        """NEGATIVE CONTROL on the key match: `clawgate-task-notes` is not
        `clawgate-task`."""
        text = "---\nclawgate-task-notes: 55\n---\n"
        assert call_fn("clawgate_task_field", text).returncode == 1

    def test_other_front_matter_keys_do_not_hide_the_field(self):
        text = "---\ntitle: something\nclawgate-task: 7\ntags: [a, b]\n---\n"
        assert call_fn("clawgate_task_field", text).stdout.strip() == "7"

    def test_sourcing_the_lib_executes_nothing(self):
        """GUARD on the HARNESS. If `source` ran the CLI, every pure-function
        assertion in this class would be measuring the CLI's output instead."""
        r = call_fn("true")
        assert r.stdout.strip() == "", r.stdout
        assert "usage:" not in r.stderr


class TestFieldVerb:
    """The `field <doc>` CLI — what /handoff uses to avoid double-adding."""

    def _run(self, tmp_path: Path, text: str):
        doc = tmp_path / "handoff-x.md"
        doc.write_text(text, encoding="utf-8")
        return subprocess.run(
            ["bash", str(LIB), "field", str(doc)],
            capture_output=True, text=True, timeout=30, env=_base_env(),
        )

    def test_a_readable_field_exits_0_and_prints_the_id(self, tmp_path):
        r = self._run(tmp_path, fm("193"))
        assert (r.returncode, r.stdout.strip()) == (0, "193")

    def test_no_field_exits_1(self, tmp_path):
        assert self._run(tmp_path, fm(None)).returncode == 1

    def test_an_unreadable_field_exits_2_and_says_so(self, tmp_path):
        r = self._run(tmp_path, fm("TBD"))
        assert r.returncode == 2
        assert "UNREADABLE" in r.stderr

    def test_a_missing_doc_is_a_usage_error_not_an_absent_field(self, tmp_path):
        r = subprocess.run(
            ["bash", str(LIB), "field", str(tmp_path / "nope.md")],
            capture_output=True, text=True, timeout=30, env=_base_env(),
        )
        assert r.returncode == 2


# --------------------------------------------------------------------------- #
# §2 comment counting — pure
# --------------------------------------------------------------------------- #
CUT = 1_600_000_000  # 2020-09-13T12:26:40Z


def counts(task: dict, cutoff: int = CUT) -> str:
    return call_fn("clawgate_new_comments", json.dumps(task), str(cutoff)).stdout.strip()


class TestCommentCounting:
    def test_comments_after_the_cutoff_are_counted(self):
        task = {"comments": [
            {"createdAt": "2026-08-19T23:34:18.640843Z"},
            {"createdAt": "2026-08-18T10:00:00Z"},
            {"createdAt": "2019-01-01T00:00:00Z"},
        ]}
        assert counts(task) == "2 0 3"

    def test_no_comments_at_all_is_a_real_zero(self):
        assert counts({"comments": []}) == "0 0 0"

    def test_a_nanosecond_fraction_still_parses(self):
        """Go can emit more fractional digits than a naive parse accepts. A
        timestamp we refuse to read becomes an unreadable count, i.e. a gap —
        worth not manufacturing over a digit count."""
        assert counts({"comments": [{"createdAt": "2026-08-19T23:34:18.640843123Z"}]}) == "1 0 1"

    def test_an_unparseable_timestamp_is_counted_SEPARATELY_not_as_old(self):
        """🔴 Folding it into "not newer" would report a floor as a measurement.
        The caller turns this third-of-three number into a `!` gap."""
        assert counts({"comments": [
            {"createdAt": "2026-08-19T23:34:18Z"},
            {"createdAt": "not a timestamp"},
            {"createdAt": "2026-08-19T23:34:18+00:00"},
        ]}) == "1 2 3"

    def test_an_ABSENT_comments_key_is_a_real_zero(self):
        """⚠ MEASURED, NOT ASSUMED — and the first cut had it the other way.
        clawgate's `comments` field is `omitempty`, so a task with no comments
        simply has no key. Measured live 0.7.98 on 2026-08-21: task #306
        `has("comments") == false` with zero comments, #299 an array of 5. The
        first implementation called absence a gap, and the live board promptly
        emitted that gap for a perfectly healthy task — an alarm that fires on
        the majority of tasks is the permanently-red gate, not sensitivity."""
        assert counts({"status": "open"}) == "0 0 0"
        assert counts({"comments": None}) == "0 0 0"

    @pytest.mark.parametrize("bad", [{"oops": 1}, "x", 3])
    def test_a_comments_value_that_is_present_and_NOT_an_array_is_MINUS_ONE(self, bad):
        """🔴 THE SILENT-ZERO CASE, kept for the shape that absence cannot be
        confused with: a schema change, or an error object that happens to be
        JSON, must not render as "0 new comments"."""
        assert counts({"comments": bad}) == "0 0 -1"

    def test_a_payload_that_is_not_an_object_at_all_is_MINUS_ONE(self):
        """NEGATIVE CONTROL on the zero above: the absent-key rule must not
        extend to a response that is not a task object."""
        assert counts([1, 2]) == "0 0 -1"

    def test_junk_input_does_not_crash_the_counter(self):
        r = call_fn("clawgate_new_comments", "not json at all", str(CUT))
        assert r.stdout.strip() == "0 0 -1"


# --------------------------------------------------------------------------- #
# §3 drift computation — pure
# --------------------------------------------------------------------------- #
def drift(status: str, newer: str = "0", task_id: str = "193") -> list[str]:
    r = call_fn("clawgate_drift_lines", task_id, status, newer)
    assert r.returncode == 0, r.stderr
    return [ln for ln in r.stdout.splitlines() if ln.strip()]


class TestDriftComputation:
    def test_complete_on_the_board_is_drift(self):
        lines = drift("complete")
        assert len(lines) == 1
        assert "COMPLETE" in lines[0] and "#193" in lines[0]

    def test_ready_for_review_is_drift(self):
        lines = drift("ready_for_review")
        assert len(lines) == 1
        assert "READY_FOR_REVIEW" in lines[0]

    @pytest.mark.parametrize("status", ["open", "in_progress"])
    def test_a_task_still_in_flight_is_NOT_drift(self, status):
        assert drift(status) == []

    def test_an_unknown_status_produces_no_drift_line(self):
        """GUARD. A status the parser could not read is the CALLER's gap to
        report — inventing a finding from it would be fabrication, which the
        digest treats as worse than silence."""
        assert drift("") == []
        assert drift("some-future-status") == []

    def test_comments_newer_than_the_doc_are_drift(self):
        lines = drift("open", "3")
        assert len(lines) == 1
        assert "3 comment(s) POSTDATING" in lines[0]

    def test_zero_new_comments_produce_no_line(self):
        assert drift("open", "0") == []

    def test_both_reasons_produce_BOTH_lines(self):
        """They are collected, not short-circuited: a reader sees every reason
        that applies, the same contract `stuck_reasons` carries."""
        lines = drift("complete", "2")
        assert len(lines) == 2

    def test_a_non_numeric_comment_count_produces_no_comment_line(self):
        """GUARD: `[ x -gt 0 ]` on a non-number is a shell error, and the block
        must degrade rather than emit a broken line."""
        assert drift("open", "?") == []


# --------------------------------------------------------------------------- #
# §4 the CLAWGATE block inside resume-state.sh — end to end
# --------------------------------------------------------------------------- #
@pytest.fixture
def stubs(tmp_path_factory):
    """`clawgatectl` (answerable) plus `gh`/`kubectl`/`curl` tripwires."""
    d = tmp_path_factory.mktemp("clawgate-stubbin")
    log = d / "invocations.log"
    for name in ("gh", "kubectl", "curl"):
        write_exec(d / name, f'printf "{name} %s\\n" "$*" >> "$STUB_LOG"\nexit 1\n')
    write_exec(
        d / "clawgatectl",
        'printf "clawgatectl %s\\n" "$*" >> "$STUB_LOG"\n'
        'if [ "${STUB_CG_RC:-0}" != 0 ]; then exit "$STUB_CG_RC"; fi\n'
        'cat "$STUB_CG_JSON"\n',
    )
    return d, log


def make_repo(tmp_path: Path, doc_text: str | None, *, mtime: int = 1_700_000_000) -> Path:
    repo = tmp_path / "fixture-repo"
    (repo / "claudedocs").mkdir(parents=True)
    env = _git_env(repo)
    git = ["git", "-C", str(repo)]
    subprocess.run([*git, "init", "-q"], check=True, env=env)
    (repo / "README.md").write_text("seed\n")
    subprocess.run([*git, "add", "README.md"], check=True, env=env)
    subprocess.run([*git, "commit", "-qm", "seed"], check=True, env=env)
    if doc_text is not None:
        p = repo / "claudedocs" / "handoff-sample.md"
        p.write_text(doc_text, encoding="utf-8")
        os.utime(p, (mtime, mtime))
    return repo


def _git_env(repo: Path) -> dict:
    env = _base_env()
    env.update({
        "GIT_CONFIG_GLOBAL": str(repo.parent / "gitconfig-global"),
        "GIT_CONFIG_SYSTEM": str(repo.parent / "gitconfig-system"),
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
    })
    return env


# The binaries resume-state.sh and its shell actually reach for. Used to build
# a CURATED $PATH for the "clawgatectl is not installed" case — see below.
# 🔴 ENUMERATED, and the enumeration is what justifies this test's PATH
# replacement in test_no_real_launchers.py's PINNED_PATH_CLOBBERS. Nothing in
# HAZARD_VOCABULARY is reachable from a directory holding only these: no
# systemd-run, systemctl, notify-send, rofi, xdotool, i3-msg, openrgb, espanso,
# home-manager or nixos-rebuild. `curl`/`gh`/`kubectl` are deliberately ABSENT
# — the tripwire stubs supply those, and they sit ahead of this directory.
_SANDBOX_TOOLS = (
    "bash", "sh", "git", "jq", "cat", "stat", "sed", "grep", "awk", "date",
    "ls", "head", "tail", "sort", "tr", "cut", "wc", "dirname", "basename",
    "realpath", "mktemp", "rm", "cp", "chmod", "env", "uname", "true", "false",
    "sleep",
)


def _sandbox_bin(root: Path) -> Path:
    """A $PATH directory holding ONLY the tools named above.

    🔴 THIS IS NOT TIDINESS. `clawgatectl` is installed on the dev host, so
    "drop the stub and see what happens" left the REAL binary reachable — the
    test then measured a live call to the real board instead of the
    not-installed path, and would have gone green for the wrong reason on this
    host while the nix sandbox (which has no clawgatectl) measured the intended
    one. Two tiers, opposite blind spots. A curated PATH makes both tiers run
    the same case.
    """
    d = root / "sandbox-bin"
    if d.exists():
        return d
    d.mkdir()
    for name in _SANDBOX_TOOLS:
        p = shutil.which(name)
        if p:
            (d / name).symlink_to(p)
    # A LIVE invariant rather than prose: the directory's contents are asserted
    # to be a subset of the enumerated list, and clawgatectl unreachable from
    # it. This is the justification recorded in PINNED_PATH_CLOBBERS.
    assert set(p.name for p in d.iterdir()) <= set(_SANDBOX_TOOLS)
    assert not shutil.which("clawgatectl", path=str(d)), "the sandbox bin leaked clawgatectl"
    return d


def run_resume(repo: Path, stubs, *, task: dict | None = None, cg_rc: int = 0,
               drop_clawgatectl: bool = False) -> str:
    d, log = stubs
    env = _git_env(repo)
    if drop_clawgatectl:
        # Stubs FIRST (gh/kubectl/curl tripwires still apply), then a curated
        # bin with no clawgatectl in it and nothing else on PATH at all.
        nocg = repo.parent / "bin-no-clawgatectl"
        nocg.mkdir(exist_ok=True)
        for f in d.iterdir():
            if f.name != "clawgatectl" and f.is_file():
                shutil.copy2(f, nocg / f.name)
        env["PATH"] = f"{nocg}{os.pathsep}{_sandbox_bin(repo.parent)}"
    else:
        env["PATH"] = f"{d}{os.pathsep}{env['PATH']}"
    env["STUB_LOG"] = str(log)
    env["STUB_CG_RC"] = str(cg_rc)
    tf = repo.parent / "task.json"
    tf.write_text(json.dumps(task if task is not None else {}), encoding="utf-8")
    env["STUB_CG_JSON"] = str(tf)
    out = subprocess.run(["bash", str(RESUME)], cwd=str(repo), capture_output=True,
                         text=True, timeout=60, env=env)
    assert out.returncode == 0, f"rc={out.returncode}\n{out.stderr}"
    return out.stdout


def block(stdout: str, name: str) -> list[str]:
    lines = stdout.splitlines()
    i = lines.index(name)
    body = []
    for ln in lines[i + 1:]:
        if ln and not ln.startswith(" "):
            break
        if ln.strip():
            body.append(ln.strip())
    return body


def drift_lines(stdout: str) -> list[str]:
    lines = stdout.splitlines()
    return [ln.strip() for ln in lines[lines.index("DRIFT") + 1:] if ln.strip()]


def findings(stdout: str) -> list[str]:
    return [ln[2:] for ln in drift_lines(stdout) if ln.startswith("- ")]


def gaps(stdout: str) -> list[str]:
    return [ln[2:] for ln in drift_lines(stdout) if ln.startswith("! ")]


CLEAN_BILL = "(none detected — live state matches the handoff's claims)"

IN_FLIGHT = {"id": 193, "status": "in_progress", "comments": [
    {"createdAt": "2020-01-01T00:00:00Z"},
]}


class TestClawgateBlock:
    def test_the_block_reports_status_and_comment_counts(self, tmp_path, stubs):
        """POSITIVE CONTROL on the whole seam: the block can read a field, fetch
        a task and print what it found. Every negative case below is otherwise
        satisfiable by a block that does nothing at all."""
        out = run_resume(tmp_path and make_repo(tmp_path, fm("193")), stubs, task={
            "id": 193, "status": "in_progress",
            "comments": [{"createdAt": "2026-01-01T00:00:00Z"},
                         {"createdAt": "2015-01-01T00:00:00Z"}],
        })
        body = block(out, "CLAWGATE")
        assert body == ["task #193  status=in_progress  comments=2 (1 newer than the doc)"], body

    def test_complete_on_the_board_reaches_DRIFT(self, tmp_path, stubs):
        out = run_resume(make_repo(tmp_path, fm("193")), stubs,
                         task={"id": 193, "status": "complete", "comments": []})
        assert any("COMPLETE" in f for f in findings(out)), drift_lines(out)
        assert CLEAN_BILL not in "\n".join(drift_lines(out))

    def test_comments_after_the_doc_reach_DRIFT(self, tmp_path, stubs):
        out = run_resume(make_repo(tmp_path, fm("193")), stubs, task={
            "id": 193, "status": "open",
            "comments": [{"createdAt": "2026-08-19T00:00:00Z"},
                         {"createdAt": "2026-08-18T00:00:00Z"}],
        })
        assert any("2 comment(s) POSTDATING" in f for f in findings(out)), drift_lines(out)

    def test_an_in_sync_task_produces_no_clawgate_DRIFT(self, tmp_path, stubs):
        """🔴 The "no drift" half is only meaningful beside evidence that the
        block RAN — a digest with no CLAUDE block at all reports the same clean
        bill (measured: this assertion alone passed against the pre-change
        tree). So the reconciliation is asserted first, the silence second."""
        out = run_resume(make_repo(tmp_path, fm("193")), stubs, task=IN_FLIGHT)
        assert block(out, "CLAWGATE") == [
            "task #193  status=in_progress  comments=1 (0 newer than the doc)"
        ], block(out, "CLAWGATE")
        assert drift_lines(out) == [CLEAN_BILL], drift_lines(out)

    # ----------------------------------------------------------------- gaps --
    def test_a_dead_clawgatectl_emits_a_GAP_and_never_a_clean_bill(self, tmp_path, stubs):
        """🔴 THE LOAD-BEARING TEST. A source that did not answer must print a
        `!` line, and the digest must stop claiming live state matches the doc.
        A silent zero here is exactly what the gap convention exists to
        prevent."""
        out = run_resume(make_repo(tmp_path, fm("193")), stubs, cg_rc=6)
        assert gaps(out), drift_lines(out)
        assert any("#193" in g and "UNKNOWN" in g for g in gaps(out)), gaps(out)
        assert CLEAN_BILL not in "\n".join(drift_lines(out))
        assert "NOT a clean bill of health" in "\n".join(drift_lines(out))

    @pytest.mark.parametrize("rc", [1, 3, 4, 6, 8])
    def test_every_clawgatectl_failure_code_is_a_gap(self, tmp_path, stubs, rc):
        """Per-code, so a failure names the exit status that leaked through.
        3=auth, 4=no such task, 6=unreachable, 8=non-JSON — none of them is a
        reading, and they must not be classified into silence."""
        out = run_resume(make_repo(tmp_path, fm("193")), stubs, cg_rc=rc)
        assert gaps(out), f"exit {rc} produced no gap: {drift_lines(out)}"

    def test_clawgatectl_missing_from_PATH_is_a_gap(self, tmp_path, stubs):
        """🔴 The nix sandbox and any host without it land here. "The tool is not
        installed" is the case most likely to be read as "nothing to report"."""
        out = run_resume(make_repo(tmp_path, fm("193")), stubs, drop_clawgatectl=True)
        assert any("not on PATH" in g for g in gaps(out)), drift_lines(out)
        assert CLEAN_BILL not in "\n".join(drift_lines(out))

    def test_an_answer_with_no_readable_status_is_a_gap(self, tmp_path, stubs):
        out = run_resume(make_repo(tmp_path, fm("193")), stubs, task={"id": 193})
        assert any("no readable status" in g for g in gaps(out)), drift_lines(out)

    def test_a_task_with_no_comments_reports_zero_and_is_NOT_a_gap(self, tmp_path, stubs):
        """⚠ THE LIVE SHAPE. `comments` is `omitempty`, so a task with none has
        no key at all (measured on live 0.7.98, task #306). This must read as a
        real zero — the version that gapped on it fired the alarm on a healthy
        task the first time it met the real board."""
        out = run_resume(make_repo(tmp_path, fm("193")), stubs,
                         task={"id": 193, "status": "open"})
        assert "comments=0 (0 newer than the doc)" in " ".join(block(out, "CLAWGATE"))
        assert gaps(out) == [], drift_lines(out)

    def test_a_comments_field_of_the_WRONG_TYPE_is_still_a_gap(self, tmp_path, stubs):
        """The distinguishable half of the case above: a schema break cannot be
        mistaken for `omitempty`, so it keeps its alarm."""
        out = run_resume(make_repo(tmp_path, fm("193")), stubs,
                         task={"id": 193, "status": "open", "comments": {"oops": 1}})
        assert any("no comments array" in g for g in gaps(out)), drift_lines(out)
        assert "comments=(unreadable)" in " ".join(block(out, "CLAWGATE"))

    def test_unparseable_comment_timestamps_make_the_count_a_declared_FLOOR(self, tmp_path, stubs):
        out = run_resume(make_repo(tmp_path, fm("193")), stubs, task={
            "id": 193, "status": "open",
            "comments": [{"createdAt": "2026-08-19T00:00:00Z"}, {"createdAt": "???"}],
        })
        assert any("FLOOR" in g for g in gaps(out)), drift_lines(out)

    def test_an_unreadable_field_IS_a_gap(self, tmp_path, stubs):
        """The doc meant to name a task and we could not read which — clawgate
        was never asked, so its state is unknown."""
        out = run_resume(make_repo(tmp_path, fm("TBD")), stubs)
        assert any("unreadable clawgate-task" in g for g in gaps(out)), drift_lines(out)
        assert CLEAN_BILL not in "\n".join(drift_lines(out))

    # ------------------------------------------------- the NOT-a-gap case ----
    def test_a_doc_with_no_field_says_so_and_is_NOT_a_gap(self, tmp_path, stubs):
        """Nothing asked clawgate anything, so its silence costs no coverage —
        the same rule the PR block applies to a handoff that references no PRs.
        But the block still has to SAY it, because "this doc names no task" and
        "the task is fine" are different statements."""
        out = run_resume(make_repo(tmp_path, fm(None)), stubs)
        body = " ".join(block(out, "CLAWGATE"))
        assert "no clawgate-task: field" in body
        assert "says NOTHING about the board" in body
        assert gaps(out) == []

    def test_a_repo_with_no_handoff_at_all_reconciles_nothing(self, tmp_path, stubs):
        out = run_resume(make_repo(tmp_path, None), stubs)
        assert "nothing to reconcile" in " ".join(block(out, "CLAWGATE"))

    def test_a_missing_shared_parser_is_a_GAP_not_a_missing_field(self, tmp_path, stubs):
        """🔴 THE DEPLOY FAILURE, and it fails in the reassuring direction if
        nothing guards it. resume-state.sh runs without `set -e`, so an absent
        lib leaves every `clawgate_*` call exiting 127 — which the block reads
        as "the parser found no field", i.e. an absent tool renders as a doc
        that names no task. Measured by running a COPY of the script from a
        directory with no lib/ beside it, which is exactly what an untracked
        file deploys as."""
        repo = make_repo(tmp_path, fm("193"))
        lone = tmp_path / "no-lib"
        lone.mkdir()
        shutil.copy2(RESUME, lone / "resume-state.sh")
        d, log = stubs
        env = _git_env(repo)
        env["PATH"] = f"{d}{os.pathsep}{env['PATH']}"
        env["STUB_LOG"] = str(log)
        env["STUB_CG_RC"] = "0"
        tf = repo.parent / "task.json"
        tf.write_text(json.dumps(IN_FLIGHT))
        env["STUB_CG_JSON"] = str(tf)
        out = subprocess.run(["bash", str(lone / "resume-state.sh")], cwd=str(repo),
                             capture_output=True, text=True, timeout=60, env=env)
        assert out.returncode == 0, out.stderr
        assert any("could not be sourced" in g for g in gaps(out.stdout)), out.stdout
        assert CLEAN_BILL not in out.stdout

    # ------------------------------------------------------------ tripwire ---
    def test_no_network_tool_is_ever_invoked(self, tmp_path, stubs):
        _d, log = stubs
        if log.exists():
            log.unlink()
        run_resume(make_repo(tmp_path, fm("193")), stubs, task=IN_FLIGHT)
        text = log.read_text() if log.exists() else ""
        assert not re.search(r"^(gh|kubectl|curl) ", text, re.M), text

    def test_the_tripwire_can_observe_an_invocation(self, tmp_path, stubs):
        """POSITIVE CONTROL on the assertion above: a counter wired to nothing
        reports the same zero as a clean run. The subject DOES invoke
        `clawgatectl` through the same log, so the log is proven writable by the
        subject itself, not merely by a direct exec."""
        _d, log = stubs
        if log.exists():
            log.unlink()
        run_resume(make_repo(tmp_path, fm("193")), stubs, task=IN_FLIGHT)
        assert re.search(r"^clawgatectl task get 193$", log.read_text(), re.M), log.read_text()


# --------------------------------------------------------------------------- #
# §5 the `resolve` verb
# --------------------------------------------------------------------------- #
@pytest.fixture
def resolver(tmp_path):
    """A fake HOME with a clawgate.env, and a curl stub whose body each test
    supplies. Returns a callable that runs `resolve` and hands back the result.
    """
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "clawgate.env").write_text(
        f"# comment\nCLAWGATE_API_URL=http://clawgate.invalid:1\n"
        f"CLAWGATE_HOOK_TOKEN={SENTINEL_TOKEN}\n",
        encoding="utf-8",
    )
    binp = tmp_path / "bin"
    binp.mkdir()
    argv_log = tmp_path / "curl-argv.log"
    write_exec(binp / "curl", (
        'out=""\n'
        'while [ $# -gt 0 ]; do\n'
        '  case "$1" in\n'
        '    -o) out="$2"; shift 2 ;;\n'
        '    *) printf "%s\\n" "$1" >> "$STUB_ARGV"; shift ;;\n'
        '  esac\n'
        'done\n'
        '[ -n "$out" ] && [ -n "${STUB_BODY:-}" ] && cp "$STUB_BODY" "$out"\n'
        'printf "%s" "${STUB_CODE:-200}"\n'
        'exit "${STUB_RC:-0}"\n'
    ))

    def go(payload=None, *, session=SESSION_VAR, session_id="sess-abc-123",
           code="200", rc="0", env_file: str | None = None):
        env = _base_env()
        env["HOME"] = str(home)
        env["PATH"] = f"{binp}{os.pathsep}{env['PATH']}"
        env["STUB_ARGV"] = str(argv_log)
        env["STUB_CODE"] = code
        env["STUB_RC"] = rc
        if payload is not None:
            body = tmp_path / "body.json"
            body.write_text(payload if isinstance(payload, str) else json.dumps(payload))
            env["STUB_BODY"] = str(body)
        if session is not None:
            env[session] = session_id
        if env_file is not None:
            (home / ".claude" / "clawgate.env").write_text(env_file, encoding="utf-8")
        return subprocess.run(["bash", str(LIB), "resolve"], capture_output=True,
                              text=True, timeout=30, env=env)

    go.argv_log = argv_log  # type: ignore[attr-defined]
    return go


ONE = {"sessionId": "sess-abc-123", "tasks": [{"id": 193, "status": "open", "title": "drain the queue"}]}
NONE = {"sessionId": "sess-abc-123", "tasks": []}
TWO = {"sessionId": "s", "tasks": [{"id": 12, "status": "open", "title": "a"},
                                   {"id": 34, "status": "in_progress", "title": "b"}]}


class TestResolve:
    def test_one_task_resolves_and_names_the_field_to_write(self, resolver):
        """POSITIVE CONTROL: the resolver can reach a 200 and read an id."""
        r = resolver(ONE)
        assert r.returncode == 0, r.stdout + r.stderr
        assert "#193" in r.stdout
        assert "clawgate-task: 193" in r.stdout

    def test_an_empty_tasks_array_is_NOTHING_RESOLVED_not_a_clean_result(self, resolver):
        """🔴 An unknown session answers 200 with `[]`, so this observable is
        shared by "touched no task" and "wrong id" and identifies neither. The
        output must refuse to pick one, and must forbid inventing a task."""
        r = resolver(NONE)
        assert r.returncode == 5
        assert "NOTHING RESOLVED" in r.stdout
        assert "EMPTY ARRAY" in r.stdout
        assert "never create a task" in r.stdout
        assert "clawgate-task:" not in r.stdout.replace("Write NO clawgate-task: field", "")

    def test_several_tasks_ask_rather_than_guess(self, resolver):
        r = resolver(TWO)
        assert r.returncode == 6
        assert "ASK which one" in r.stdout
        assert "#12" in r.stdout and "#34" in r.stdout

    # ------------------------------------------- the variable name itself ----
    def test_the_session_id_is_read_from_the_exact_variable_name(self, resolver):
        """🔴 The literal is written in THIS file (`SESSION_VAR`), never read
        from the subject."""
        assert resolver(ONE, session=SESSION_VAR).returncode == 0

    def test_a_plausible_WRONG_variable_name_resolves_nothing(self, resolver):
        """🔴 NEGATIVE CONTROL on the constant's VALUE. `CLAUDE_SESSION_ID` does
        not exist; a subject reading it would answer identically to a working
        one whenever no task exists, which is how the feature this follows
        shipped inert. Both names are popped from the harness env
        (`_base_env`) so the RIGHT one cannot answer for the wrong one."""
        r = resolver(ONE, session=WRONG_SESSION_VAR)
        assert r.returncode == 3, r.stdout
        assert "NO SESSION ID" in r.stdout
        assert "NOT 'no task'" in r.stdout

    def test_no_session_variable_at_all_is_reported_as_its_own_outcome(self, resolver):
        r = resolver(ONE, session=None)
        assert r.returncode == 3
        assert "NO SESSION ID" in r.stdout

    def test_the_harness_carries_neither_session_variable(self):
        """🔴 GUARD ON THE HARNESS, and it is not hypothetical: the real
        `CLAUDE_CODE_SESSION_ID` is set in every Claude Code session, and the
        negative control above passed for the wrong reason until `_base_env`
        popped it."""
        env = _base_env()
        assert SESSION_VAR not in env
        assert WRONG_SESSION_VAR not in env

    def test_a_session_id_that_could_steer_the_url_is_refused(self, resolver):
        """GUARD. The id is interpolated into a URL PATH; `../` or a query
        string would send the read somewhere nobody asked for."""
        r = resolver(ONE, session_id="../../api/tasks")
        assert r.returncode == 3
        assert "REFUSED" in r.stdout

    # ------------------------------------------------------------- gaps ------
    def test_a_non_200_is_a_gap(self, resolver):
        r = resolver(ONE, code="401")
        assert r.returncode == 4
        assert "DID NOT ANSWER" in r.stdout and "401" in r.stdout
        assert "UNKNOWN, not empty" in r.stdout

    def test_a_curl_failure_is_a_gap(self, resolver):
        r = resolver(ONE, rc="7")
        assert r.returncode == 4
        assert "curl exit 7" in r.stdout

    def test_a_missing_token_is_a_gap_not_an_empty_result(self, resolver):
        r = resolver(ONE, env_file="CLAWGATE_API_URL=http://clawgate.invalid:1\n")
        assert r.returncode == 4
        assert "DID NOT ANSWER" in r.stdout

    def test_a_200_without_a_tasks_array_is_a_gap(self, resolver):
        r = resolver({"error": "nope"})
        assert r.returncode == 4
        assert "no `tasks` array" in r.stdout

    def test_the_token_never_reaches_argv_or_output(self, resolver):
        """🔴 `/proc/<pid>/cmdline` is world-readable — which is why clawgatectl
        refuses a token positional too. The header goes in a 0600 config file."""
        r = resolver(ONE)
        assert SENTINEL_TOKEN not in r.stdout
        assert SENTINEL_TOKEN not in r.stderr
        argv = resolver.argv_log.read_text() if resolver.argv_log.exists() else ""  # type: ignore[attr-defined]
        assert argv, "the argv log is empty — the control cannot see a leak"
        assert SENTINEL_TOKEN not in argv, argv

    def test_the_config_file_is_deleted_afterwards(self, resolver, tmp_path):
        """The token file must not outlive the call. Measured by counting the
        files the run leaves in TMPDIR."""
        env_tmp = tmp_path / "tmpdir"
        env_tmp.mkdir()
        before = set(env_tmp.iterdir())
        env = _base_env()
        r = resolver(ONE)
        assert r.returncode == 0
        assert set(env_tmp.iterdir()) == before


# --------------------------------------------------------------------------- #
# §6 the skills and the code must not drift apart
# --------------------------------------------------------------------------- #
HANDOFF_PINS: list[tuple[str, str]] = [
    ("scripts/lib/clawgate_handoff.sh", "the step invokes the tool that owns the resolution"),
    (SESSION_VAR, "🔴 the EXACT session variable reaches the executor"),
    ("clawgate-task: 193", "the front-matter SHAPE is shown, not described"),
    ("NEVER create a task", "🔴 a task is never minted to fill a blank field"),
    ("ASK the user which one", "several resolved => a question, not a guess"),
    ("EMPTY ARRAY", "🔴 the 200-with-[] caveat reaches the executor"),
    ("field <doc>", "the no-double-add check is spelled out"),
]

RESUME_PINS: list[tuple[str, str]] = [
    ("CLAWGATE", "the block is named in the digest the step describes"),
    ("clawgate-task:", "the reader knows which field it reconciles"),
    ("clawgatectl task get", "how the task is fetched, so a failure is legible"),
    ("UNKNOWN", "🔴 a dead clawgate is stated as unknown, never as no drift"),
]


class TestSkillsAndCodeAgree:
    @pytest.mark.parametrize("phrase,why", HANDOFF_PINS, ids=[w for _, w in HANDOFF_PINS])
    def test_handoff_skill_pins(self, phrase, why):
        doc = HANDOFF_SKILL.read_text(encoding="utf-8")
        assert phrase in doc, f"claude/skills/handoff/SKILL.md no longer pins {why}: {phrase!r}"

    @pytest.mark.parametrize("phrase,why", RESUME_PINS, ids=[w for _, w in RESUME_PINS])
    def test_resume_skill_pins(self, phrase, why):
        doc = RESUME_SKILL.read_text(encoding="utf-8")
        assert phrase in doc, f"claude/skills/resume/SKILL.md no longer pins {why}: {phrase!r}"

    def test_the_wrong_variable_name_appears_in_neither_skill_nor_the_code(self):
        """🔴 NEGATIVE CONTROL on the pin above — a phrase check that can only
        pass is not a check. `CLAUDE_SESSION_ID` must never be written as a
        variable an executor could export or read."""
        pat = re.compile(r"(?<![A-Z_])" + WRONG_SESSION_VAR)
        denial = re.compile(r"there is no `?" + WRONG_SESSION_VAR, re.I)
        seen = 0
        for path in (HANDOFF_SKILL, RESUME_SKILL, LIB, RESUME):
            # Wherever the non-existent name appears at all it must carry its
            # own denial ON THE SAME LINE — a bare mention is one copy-paste
            # away from becoming the name something reads.
            for line in path.read_text(encoding="utf-8").splitlines():
                if pat.search(line):
                    seen += 1
                    assert denial.search(line), (
                        f"{path.name} names {WRONG_SESSION_VAR} without denying it: {line}"
                    )
        assert seen >= 1, (
            "POSITIVE CONTROL: the scanner found the wrong name NOWHERE, so it "
            "cannot have been looking — the skill is supposed to warn about it."
        )

    def test_the_pin_can_report_absence(self):
        """NEGATIVE CONTROL on the instrument itself."""
        doc = HANDOFF_SKILL.read_text(encoding="utf-8")
        assert "create a clawgate task if none resolves" not in doc

    def test_the_lib_is_tracked_by_git(self):
        """A new file the flake never sees deploys as an absence, silently —
        and resume-state.sh SOURCES this one, so its absence breaks /resume
        entirely rather than just skipping a block. Asserted in both tiers: in
        the nix sandbox the flake source holds only tracked files, so presence
        is the evidence; on the dev host `git ls-files` is asked."""
        rel = "scripts/lib/clawgate_handoff.sh"
        assert LIB.exists(), f"{LIB} is missing from this tree"
        if not (REPO_ROOT / ".git").exists():
            return
        out = subprocess.run(["git", "-C", str(REPO_ROOT), "ls-files", "--", rel],
                             capture_output=True, text=True, env=_base_env())
        assert out.stdout.strip() == rel, f"{rel} is untracked; the flake will omit it"

    def test_resume_state_sources_the_shared_parser_rather_than_copying_it(self):
        """🔴 ONE RULE, ONE PLACE. The writer's "is a field already there?" and
        the reader's "what does it say?" must agree by construction; a second
        copy of the parser is how /handoff double-adds a field /resume then
        reads twice."""
        text = RESUME.read_text(encoding="utf-8")
        assert "lib/clawgate_handoff.sh" in text
        assert "clawgate_task_field(){" not in text, "the parser was re-implemented"


# --------------------------------------------------------------------------- #
# §7 the field has to SURVIVE a /handoff update
# --------------------------------------------------------------------------- #
def _handoff_doc_module():
    spec = importlib.util.spec_from_file_location("handoff_doc_fm", HANDOFF_DOC_TOOL)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


hd = _handoff_doc_module()

BASE_DOC = (
    "---\nclawgate-task: 193\n---\n"
    "# Handoff: sample — 2026-08-19\n\n"
    "## State now\n- Branch: feat/x\n\n"
    "## Gotchas / decisions / dead-ends\n- the first gotcha\n"
)


class TestFrontMatterSurvivesTheMerge:
    """🔴 THE DEFECT THIS PINS (measured on the pre-change module): front matter
    is not a section, so it lands in `split_sections`'s PREAMBLE — and `merge`
    takes the UPDATE's preamble whenever it has one. A delta file whose first
    line was prose rather than a `## ` heading therefore DELETED the doc's front
    matter silently, and /resume then read the doc as naming no task at all: a
    durable field that was durable only while every writer remembered to start
    with a heading.
    """

    def test_a_delta_with_a_preamble_does_not_delete_the_field(self):
        upd = "a stray line of prose\n\n## State now\n- Branch: feat/y\n"
        merged = hd.merge(BASE_DOC, upd)
        assert merged.startswith("---\nclawgate-task: 193\n---\n"), merged

    def test_a_heading_only_delta_keeps_the_field_too(self):
        merged = hd.merge(BASE_DOC, "## State now\n- Branch: feat/y\n")
        assert merged.startswith("---\nclawgate-task: 193\n---\n"), merged

    def test_the_field_is_not_duplicated(self):
        merged = hd.merge(BASE_DOC, "## State now\n- Branch: feat/y\n")
        assert merged.count("clawgate-task:") == 1, merged

    def test_the_rest_of_the_doc_is_undisturbed(self):
        """The field surviving is worth nothing if the merge's own contract
        broke: the appended gotcha must still append and the old one survive."""
        merged = hd.merge(BASE_DOC, "## Gotchas\n- the second gotcha\n")
        assert "the first gotcha" in merged and "the second gotcha" in merged
        assert "Branch: feat/x" in merged

    def test_an_explicit_front_matter_in_the_delta_wins(self):
        merged = hd.merge(BASE_DOC, "---\nclawgate-task: 200\n---\n## State now\n- x\n")
        assert merged.startswith("---\nclawgate-task: 200\n---\n"), merged
        assert merged.count("clawgate-task:") == 1

    def test_a_doc_with_no_front_matter_merges_exactly_as_before(self):
        """GUARD against a regression in the untouched majority: no front
        matter, so the preamble rule must behave identically to before."""
        base = "# Handoff: x\n\n## State now\n- Branch: feat/x\n"
        merged = hd.merge(base, "## State now\n- Branch: feat/y\n")
        assert merged.startswith("# Handoff: x\n")
        assert "feat/y" in merged and "feat/x" not in merged

    def test_the_parser_and_the_merge_agree_on_what_front_matter_IS(self):
        """🔴 A SEAM TEST, not a component one. The bash reader and the python
        merger each decide independently where front matter starts and stops; if
        they disagree, the merge preserves a block the reader will not read, or
        preserves nothing while the reader still finds one. Both must reject a
        block that does not start at line 1."""
        late = "# Handoff\n\n---\nclawgate-task: 193\n---\n\n## State now\n- x\n"
        assert hd.split_front_matter(late)[0] == ""
        assert call_fn("clawgate_task_field", late).returncode == 1
        early = BASE_DOC
        assert hd.split_front_matter(early)[0] == "---\nclawgate-task: 193\n---\n"
        assert call_fn("clawgate_task_field", early).stdout.strip() == "193"
