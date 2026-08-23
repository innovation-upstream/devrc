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

import datetime
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

# The four task states, taken from THIS REPO'S SINGLE DEFINITION rather than
# spelled again here — `test_clawgate_predicate_single_source.py` walks every
# python file's AST and fails on any second copy of the state set, and it caught
# the literal this replaces. `complete` is the one member that module has no
# name for (it owns the OPERATOR-PENDING predicate, and a complete task is not
# pending), so it is named once, here, and nowhere else.
def _clawgate_tasks_module():
    spec = importlib.util.spec_from_file_location(
        "clawgate_tasks_for_status",
        Path(__file__).resolve().parents[2] / "scripts" / "lib" / "clawgate_tasks.py",
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_ct = _clawgate_tasks_module()
BOARD_STATUSES = tuple(sorted(set(_ct.PENDING_TASK_STATES) | {_ct.IN_PROGRESS, "complete"}))

# A token value that appears nowhere else, so a leak into argv or into output is
# unambiguous rather than a substring coincidence.
SENTINEL_TOKEN = "tok-SENTINEL-9f2b41"

#: A bash function definition, used by the structural guards below.
fn_re_top = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*\(\)\s*\{")

#: A command that opens a positional ARGUMENT for its content.
_ARG_CONTENT_READ = re.compile(
    r'(?:^|[;&|(]|\$\()\s*(?:cat|grep|head|tail|sed|awk|wc|jq)\b[^\n]*"\$[0-9]"'
    r'|<\s*"\$[0-9]"'
)


def laundered_readers(src: list[str]) -> list[tuple[str, list[str]]]:
    """Helpers handed `"$HANDOFF"` that read their argument's CONTENT.

    Factored out of the guard so it can be run on a DECOY as a positive control:
    a scan wired to nothing returns the same empty list as a clean file, and
    this scan has already been wrong once in exactly that way (see the comment
    on the per-defined-function loop).
    """
    defined = {m.group(1) for ln in src if (m := fn_re_top.match(ln))}
    called_with_handoff = set()
    for line in src:
        if line.lstrip().startswith("#") or '"$HANDOFF"' not in line:
            continue
        for name in defined:
            if re.search(r"\b" + re.escape(name) + r"\b\s", line):
                called_with_handoff.add(name)
    out: list[tuple[str, list[str]]] = []
    for name in sorted(called_with_handoff):
        body, inside = [], False
        for ln in src:
            m = fn_re_top.match(ln)
            if m:
                inside = m.group(1) == name
                continue
            if inside:
                if ln.startswith("}"):
                    inside = False
                else:
                    body.append(ln)
        hits = [ln.strip() for ln in body
                if not ln.lstrip().startswith("#") and _ARG_CONTENT_READ.search(ln)]
        if hits:
            out.append((name, hits))
    return out

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

    def test_an_unclosed_block_CARRYING_the_key_yields_no_id(self):
        """🔴 THE SEAM BUG. This side used to return the value the moment it saw
        the key, while `handoff_doc._FRONT_MATTER` requires a closing `---` — so
        an unterminated block (an ordinary LLM slip) made the shell print `193`
        while `merge` treated those lines as BODY and dropped them: the silent
        data loss this whole change exists to fix, reintroduced at the seam."""
        r = call_fn("clawgate_task_field", "---\nclawgate-task: 193\n# Handoff\n")
        assert r.stdout.strip() == ""
        assert r.returncode == 1

    def test_an_unclosed_block_CARRYING_the_key_is_still_PRESENT(self):
        """…and it must not read as "no field": the doc tried to name a task, so
        the caller owes a `!` gap, not silence."""
        assert call_fn("clawgate_field_present",
                       "---\nclawgate-task: 193\n# Handoff\n").returncode == 0

    def test_the_raw_reader_reports_the_unterminated_case_as_its_OWN_code(self):
        """rc 2 is what lets `field` name the right repair — putting the `---`
        back, not editing the value."""
        assert call_fn("clawgate_task_field_raw",
                       "---\nclawgate-task: 193\n# Handoff\n").returncode == 2
        assert call_fn("clawgate_task_field_raw",
                       "---\ntitle: x\n# Handoff\n").returncode == 1
        assert call_fn("clawgate_task_field_raw",
                       "---\nclawgate-task: 193\n---\n").returncode == 0

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

    def test_an_unterminated_block_exits_2_and_names_THAT_cause(self, tmp_path):
        """Two different repairs, so they must not share a message: a bad VALUE
        is edited in place, an unclosed BLOCK needs its `---` back."""
        r = self._run(tmp_path, "---\nclawgate-task: 193\n# Handoff\n")
        assert r.returncode == 2
        assert "NEVER CLOSED" in r.stderr

    def test_a_NONEXISTENT_doc_is_its_own_code_not_a_broken_field(self, tmp_path):
        """🔴 Finding 8. Exit 2 used to mean "missing verb" OR "mistyped path" OR
        "present-but-unreadable field", while the skill documented only the last
        — so a typo in the path read to the executor as "this doc has a broken
        clawgate-task: field", and the repair it would attempt is to a file that
        does not exist."""
        r = subprocess.run(
            ["bash", str(LIB), "field", str(tmp_path / "nope.md")],
            capture_output=True, text=True, timeout=30, env=_base_env(),
        )
        assert r.returncode == 66, r.stderr
        assert "cannot read" in r.stderr
        assert "says NOTHING about any field" in r.stderr

    def test_a_missing_path_is_a_USAGE_error(self, tmp_path):
        r = subprocess.run(["bash", str(LIB), "field"], capture_output=True,
                           text=True, timeout=30, env=_base_env())
        assert r.returncode == 64
        assert "usage:" in r.stderr

    def test_an_unknown_verb_is_a_USAGE_error(self):
        r = subprocess.run(["bash", str(LIB), "frobnicate"], capture_output=True,
                           text=True, timeout=30, env=_base_env())
        assert r.returncode == 64

    def test_the_four_field_codes_are_pairwise_DISTINCT(self, tmp_path):
        """A guard on the RELATIONSHIP rather than on any one code: the whole
        point is that the executor can tell these four cases apart."""
        doc_ok = self._run(tmp_path, fm("193")).returncode
        doc_none = self._run(tmp_path, fm(None)).returncode
        doc_bad = self._run(tmp_path, fm("TBD")).returncode
        no_file = subprocess.run(["bash", str(LIB), "field", str(tmp_path / "x.md")],
                                 capture_output=True, text=True, env=_base_env()).returncode
        codes = [doc_ok, doc_none, doc_bad, no_file]
        assert len(set(codes)) == 4, codes


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

    def test_an_unknown_status_produces_no_drift_line_AND_is_not_known(self):
        """🔴 INVERTED FROM ITS FIRST VERSION, which pinned the silence alone and
        so pinned the bug: `drift_lines` staying quiet is only safe if something
        else refuses to call an unknown state healthy. Inventing a finding here
        would be fabrication, so the silence stays — but the vocabulary check
        that the caller gates on is asserted in the SAME test, and the e2e gap is
        `test_a_status_outside_the_vocabulary_is_a_gap`."""
        assert drift("") == []
        assert drift("some-future-status") == []
        assert call_fn("clawgate_known_status", "some-future-status").returncode == 1
        assert call_fn("clawgate_known_status", "").returncode == 1

    @pytest.mark.parametrize("status", BOARD_STATUSES)
    def test_every_status_the_board_can_return_is_KNOWN(self, status):
        """POSITIVE CONTROL on the vocabulary: a membership check that says NO to
        everything would satisfy the test above for free.

        🔴 THE STATUSES ARE NOT SPELLED HERE. They come from
        `scripts/lib/clawgate_tasks.py`, this repo's single definition of the
        clawgate task states — enforced by
        `test_clawgate_predicate_single_source.py`, which failed on the literal
        this replaces. That makes the test a CROSS-SOURCE pin (does the shell
        vocabulary cover what python already knows?) instead of a second copy of
        the set that would drift with neither."""
        assert call_fn("clawgate_known_status", status).returncode == 0

    def test_the_shell_vocabulary_covers_everything_PYTHON_knows_about(self):
        """🔴 A SEAM PIN, and it fails when either side GROWS. A status added to
        `clawgate_tasks.py` (the bar/session-manager surfaces) and not here means
        /resume calls it unknown; the reverse means this file invented one."""
        src = LIB.read_text(encoding="utf-8")
        m = re.search(r'^CLAWGATE_TASK_STATUSES="([^"]+)"', src, re.M)
        assert m, "CLAWGATE_TASK_STATUSES is no longer a plain assignment"
        shell = set(m.group(1).split())
        assert shell == set(BOARD_STATUSES), (shell, BOARD_STATUSES)

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
    for name in ("kubectl", "curl"):
        write_exec(d / name, f'printf "{name} %s\\n" "$*" >> "$STUB_LOG"\nexit 1\n')
    # gh answers with $STUB_GH_JSON when set (the convention
    # test_resume_state_handoff_resolution.py already uses), so the PR-framing
    # path can be driven without a network. Unset it stays a pure tripwire.
    write_exec(d / "gh", (
        'printf "gh %s\\n" "$*" >> "$STUB_LOG"\n'
        'if [ -n "${STUB_GH_JSON:-}" ]; then printf "%s\\n" "$STUB_GH_JSON"; exit 0; fi\n'
        'exit 1\n'
    ))
    write_exec(
        d / "clawgatectl",
        'printf "clawgatectl %s\\n" "$*" >> "$STUB_LOG"\n'
        'if [ "${STUB_CG_RC:-0}" != 0 ]; then exit "$STUB_CG_RC"; fi\n'
        'cat "$STUB_CG_JSON"\n',
    )
    return d, log


def make_repo(
    tmp_path: Path,
    doc_text: str | None,
    *,
    mtime: int = 1_700_000_000,
    commit_date: str | None = None,
) -> Path:
    """A throwaway repo whose claudedocs/ holds one handoff.

    `commit_date` COMMITS the doc at that timestamp — which is what makes the
    two clocks disagree. A fresh `git worktree add` / `clone` stamps every file
    at checkout, so `mtime` and the commit date are independent in reality and
    must be independent in the fixture.
    """
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
        if commit_date is not None:
            cenv = dict(env, GIT_AUTHOR_DATE=commit_date, GIT_COMMITTER_DATE=commit_date)
            subprocess.run([*git, "add", "claudedocs/handoff-sample.md"],
                           check=True, env=cenv)
            subprocess.run([*git, "commit", "-qm", "handoff"], check=True, env=cenv)
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
        assert body == ["task #193  status=in_progress  comments=2 (1 newer than the doc, by file mtime)"], body

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
        repo = make_repo(tmp_path, fm("193"), commit_date="2026-08-01T00:00:00 +0000")
        out = run_resume(repo, stubs, task=IN_FLIGHT)
        assert block(out, "CLAWGATE") == [
            "task #193  status=in_progress  comments=1 (0 newer than the doc, by last commit)"
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

    def test_a_status_outside_the_vocabulary_is_a_gap(self, tmp_path, stubs):
        """🔴 FIXTURE-DERIVED. No other fixture supplies a status outside the four
        the board defines, and `clawgate_drift_lines` is silent on anything it
        does not recognise — so a FIFTH status rendered exactly like a healthy
        one: `DRIFT (none detected — live state matches the handoff's claims)`.
        Unreachable from today's server, and clawgate's own `taskstatus.go`
        header records an incident where adding a constant left a suite green."""
        out = run_resume(make_repo(tmp_path, fm("193")), stubs,
                         task={"id": 193, "status": "blocked", "comments": []})
        assert any("does not know" in g for g in gaps(out)), drift_lines(out)
        assert any("blocked" in g for g in gaps(out)), gaps(out)
        assert CLEAN_BILL not in "\n".join(drift_lines(out))

    def test_an_answer_with_no_readable_status_is_a_gap(self, tmp_path, stubs):
        out = run_resume(make_repo(tmp_path, fm("193")), stubs, task={"id": 193})
        assert any("no readable status" in g for g in gaps(out)), drift_lines(out)

    def test_a_task_with_no_comments_reports_zero_and_is_NOT_a_gap(self, tmp_path, stubs):
        """⚠ THE LIVE SHAPE. `comments` is `omitempty`, so a task with none has
        no key at all (measured on live 0.7.98, task #306). This must read as a
        real zero — the version that gapped on it fired the alarm on a healthy
        task the first time it met the real board."""
        repo = make_repo(tmp_path, fm("193"), commit_date="2026-08-01T00:00:00 +0000")
        out = run_resume(repo, stubs, task={"id": 193, "status": "open"})
        assert "comments=0 (0 newer than the doc, by " in " ".join(block(out, "CLAWGATE"))
        assert gaps(out) == [], drift_lines(out)

    def test_a_comments_field_of_the_WRONG_TYPE_is_still_a_gap(self, tmp_path, stubs):
        """The distinguishable half of the case above: a schema break cannot be
        mistaken for `omitempty`, so it keeps its alarm."""
        out = run_resume(make_repo(tmp_path, fm("193")), stubs,
                         task={"id": 193, "status": "open", "comments": {"oops": 1}})
        assert any("no comments array" in g for g in gaps(out)), drift_lines(out)
        assert "comments=(unreadable)" in " ".join(block(out, "CLAWGATE"))

    # --------------------------------- WHICH COPY of the doc gets read -------
    def _stale_clone(
        self,
        tmp_path: Path,
        local_fm: str | None,
        origin_fm: str | None,
        local_body: str = "",
        origin_body: str = "\n## Extra\n- the last session's real work\n",
        v1_date: str = "2026-08-05T00:00:00 +0000",
        v2_date: str = "2026-08-15T00:00:00 +0000",
    ):
        """A clone whose checked-out handoff differs from origin's.

        🔴 NEITHER SIDE'S SUITE MADE THE TWO COPIES DIFFER — which is why a block
        reading the wrong one survived a full audit round. Built the way it
        actually happens: clone A commits v1 and pushes, clone B pushes v2, A is
        never updated. A's `origin/main` ref is stale too until the script
        fetches, so the fetch path is exercised for real against a local bare
        repo (no network).
        """
        env = _git_env(tmp_path)
        origin = tmp_path / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)],
                       check=True, env=env)
        a, b = tmp_path / "cloneA", tmp_path / "cloneB"
        for dest in (a, b):
            subprocess.run(["git", "clone", "-q", str(origin), str(dest)], check=True, env=env)
        doc = "claudedocs/handoff-stale.md"
        # 🔴 THE TWO COMMITS CARRY DIFFERENT DATES, and that is load-bearing, not
        # tidiness. Without `GIT_AUTHOR_DATE` both land in the same SECOND, so
        # the fixture cannot express the difference the clock assertion is
        # supposed to detect — and a mutant that kept the `on origin/main` LABEL
        # while taking the VALUE from the discarded local copy was green
        # 141/141. A fixture that cannot produce two different dates makes every
        # date assertion over it vacuous.
        (a / "claudedocs").mkdir(parents=True)
        (a / doc).write_text(fm(local_fm) + local_body, encoding="utf-8")
        env_a = dict(_git_env(a), GIT_AUTHOR_DATE=v1_date, GIT_COMMITTER_DATE=v1_date)
        for args in (["add", doc], ["commit", "-qm", "v1"], ["push", "-q", "origin", "main"]):
            subprocess.run(["git", "-C", str(a), *args], check=True, env=env_a)
        subprocess.run(["git", "-C", str(b), "pull", "-q", "origin", "main"],
                       check=True, env=_git_env(b))
        (b / doc).write_text(fm(origin_fm) + origin_body, encoding="utf-8")
        env_b = dict(_git_env(b), GIT_AUTHOR_DATE=v2_date, GIT_COMMITTER_DATE=v2_date)
        for args in (["add", doc], ["commit", "-qm", "v2"], ["push", "-q", "origin", "main"]):
            subprocess.run(["git", "-C", str(b), *args], check=True, env=env_b)
        return a

    def _run_in(self, repo: Path, stubs, task: dict, gh_json: str | None = None) -> str:
        d, log = stubs
        env = _git_env(repo)
        env["PATH"] = f"{d}{os.pathsep}{env['PATH']}"
        env["STUB_LOG"] = str(log)
        env["STUB_CG_RC"] = "0"
        env["RESUME_STATE_SKIP_FETCH"] = ""      # let the real fetch run
        if gh_json is not None:
            env["STUB_GH_JSON"] = gh_json
        tf = repo.parent / "task.json"
        tf.write_text(json.dumps(task))
        env["STUB_CG_JSON"] = str(tf)
        out = subprocess.run(["bash", str(RESUME)], cwd=str(repo), capture_output=True,
                             text=True, timeout=90, env=env)
        assert out.returncode == 0, out.stderr
        return out.stdout

    def test_the_field_is_read_from_the_copy_handoff_freshness_CHOSE(self, tmp_path, stubs):
        """🔴 THE REGRESSION. Every other block reads `$HANDOFF_TEXT` — the copy
        the freshness check selected — and this one read the working tree.

        The two differ exactly when it matters: here the STALE local copy carries
        NO field while the origin copy (the one the digest announces it read)
        carries `clawgate-task: 193`, and the board has that task `complete`. The
        pre-fix digest printed `handoff-read: 🔴 origin/main copy (…STALE…)` and
        then `(no clawgate-task: field …)` with ZERO gaps — because "no field" is
        the one case deliberately exempt from gapping. A false clean, reached
        through the block built to prevent false cleans."""
        repo = self._stale_clone(tmp_path, local_fm=None, origin_fm="193")
        out = self._run_in(repo, stubs, {"id": 193, "status": "complete", "comments": []})
        assert "STALE" in " ".join(
            ln for ln in out.splitlines() if "handoff-read:" in ln), out
        body = " ".join(block(out, "CLAWGATE"))
        assert "task #193" in body, f"the block read the stale local copy:\n{out}"
        assert any("COMPLETE" in f for f in findings(out)), drift_lines(out)

    def test_the_two_copies_naming_DIFFERENT_tasks_reconciles_the_chosen_one(
        self, tmp_path, stubs
    ):
        """NEGATIVE CONTROL on the test above: "no field locally" could be
        satisfied by a block that ignores the doc entirely and always fetches.
        Here BOTH copies name a task and they name DIFFERENT ones, so only
        reading the right copy produces the right id."""
        repo = self._stale_clone(tmp_path, local_fm="777", origin_fm="193")
        out = self._run_in(repo, stubs, {"id": 193, "status": "open", "comments": []})
        body = " ".join(block(out, "CLAWGATE"))
        assert "task #193" in body, out
        assert "777" not in body, f"the block reconciled the stale copy's task:\n{out}"

    def test_the_clock_dates_the_REF_copy_when_that_is_the_one_read(self, tmp_path, stubs):
        """…and the date must come from the same copy as the text. The local
        branch's `git log -1 -- <path>` describes the file that was discarded, so
        the printed clock names the ref instead.

        🔴 THE VALUE, NOT ONLY THE LABEL — the label alone is a SPELLED guard and
        was walkable: keeping `clock="last commit on $HANDOFF_REF"` while taking
        `mt` from the local copy stayed green. The two commits are 10 days apart
        (v1 2026-08-05 local, v2 2026-08-15 origin) and the comment sits BETWEEN
        them, so the two clocks give different ANSWERS: on the ref's date the
        comment is older (0 newer), on the discarded copy's it is newer (1)."""
        repo = self._stale_clone(tmp_path, local_fm="193", origin_fm="193")
        out = self._run_in(repo, stubs, {
            "id": 193, "status": "open",
            "comments": [{"createdAt": "2026-08-10T00:00:00Z"}],
        })
        body = " ".join(block(out, "CLAWGATE"))
        assert "by last commit on origin/main" in body, out
        assert "comments=1 (0 newer than the doc" in body, (
            f"the count was measured against the DISCARDED local copy:\n{out}"
        )

    def test_that_clock_fixture_really_produces_two_different_dates(self, tmp_path, stubs):
        """POSITIVE CONTROL on the fixture, because the previous one could not
        express the difference at all (both commits landed in the same second).
        Same repo, a comment AFTER both dates: now the answer must move."""
        repo = self._stale_clone(tmp_path, local_fm="193", origin_fm="193")
        out = self._run_in(repo, stubs, {
            "id": 193, "status": "open",
            "comments": [{"createdAt": "2026-08-20T00:00:00Z"}],
        })
        assert "comments=1 (1 newer than the doc" in " ".join(block(out, "CLAWGATE")), out

    # ------------------------------------ the LAUNDERED read (PR framing) ----
    GH_MERGED = json.dumps({"state": "MERGED", "mergeable": "MERGEABLE",
                            "mergedAt": "2026-08-19T00:00:00Z", "statusCheckRollup": []})

    def test_PR_framing_is_read_from_the_chosen_copy_too(self, tmp_path, stubs):
        """🔴 THE THIRD INSTANCE, laundered through a path-taking helper.

        `git_pr_block` extracts the PR refs from `$HANDOFF_TEXT` and then asked
        `handoff_says_inflight` — which took a PATH and grepped the FILE —
        whether the doc frames them as in-flight. Different copies.

        FALSE-CLEAN DIRECTION: the origin copy (the one the digest says it read)
        frames #999 as open; the stale local copy never mentions it. gh says
        MERGED. The drift line must fire; before the fix it did not."""
        repo = self._stale_clone(
            tmp_path, local_fm="193", origin_fm="193",
            local_body="\n## State now\n- nothing about that PR here\n",
            origin_body="\n## State now\n- #999 is OPEN and awaiting merge\n",
        )
        out = self._run_in(repo, stubs, {"id": 193, "status": "open", "comments": []},
                           gh_json=self.GH_MERGED)
        assert any("#999 MERGED but handoff frames it as open" in f for f in findings(out)), (
            f"the framing was read from the stale local copy:\n{out}"
        )

    def test_PR_framing_does_not_FABRICATE_from_the_discarded_copy(self, tmp_path, stubs):
        """The other direction, and the file's own comment calls it the worse
        one: the STALE copy frames #999 as open while the copy actually read
        says the follow-on is already done. No drift line may fire."""
        repo = self._stale_clone(
            tmp_path, local_fm="193", origin_fm="193",
            local_body="\n## State now\n- #999 is OPEN and awaiting merge\n",
            origin_body="\n## State now\n- #999 LANDED; the follow-on is already done\n",
        )
        out = self._run_in(repo, stubs, {"id": 193, "status": "open", "comments": []},
                           gh_json=self.GH_MERGED)
        assert not any("#999 MERGED but handoff frames it" in f for f in findings(out)), (
            f"a drift line was fabricated from the discarded copy:\n{out}"
        )

    # ------------------------------------------------- which CLOCK counts ----
    def test_a_FRESH_CHECKOUT_does_not_silence_the_comment_count(self, tmp_path, stubs):
        """🔴 THE REGRESSION, and it lands on this repo's own standard workflow.
        `git worktree add` / `git clone` stamp every checked-out file at CHECKOUT
        time, so on the mtime clock every comment predates the doc and the count
        is a silent zero — in the fresh worktree CLAUDE.md mandates for
        commit-bound work.

        Fixture: the doc is COMMITTED at 2026-08-01 and its mtime is then set to
        2026-08-20, exactly what a checkout produces. The comment sits between
        the two, so the two clocks give different answers and the test can tell
        them apart."""
        repo = make_repo(
            tmp_path, fm("193"),
            commit_date="2026-08-01T00:00:00 +0000",
            mtime=int(datetime.datetime(2026, 8, 20, tzinfo=datetime.timezone.utc).timestamp()),
        )
        out = run_resume(repo, stubs, task={
            "id": 193, "status": "open",
            "comments": [{"createdAt": "2026-08-10T00:00:00Z"}],
        })
        body = " ".join(block(out, "CLAWGATE"))
        assert "1 newer than the doc, by last commit" in body, body
        assert any("POSTDATING" in f for f in findings(out)), drift_lines(out)
        assert gaps(out) == [], drift_lines(out)

    def test_the_git_clock_is_not_used_when_it_disagrees_with_nothing(self, tmp_path, stubs):
        """NEGATIVE CONTROL on the clock above: a comment OLDER than the commit
        must still read as old, or "prefer the commit date" would just be a way
        of always saying "newer"."""
        repo = make_repo(
            tmp_path, fm("193"),
            commit_date="2026-08-01T00:00:00 +0000",
            mtime=int(datetime.datetime(2026, 8, 20, tzinfo=datetime.timezone.utc).timestamp()),
        )
        out = run_resume(repo, stubs, task={
            "id": 193, "status": "open",
            "comments": [{"createdAt": "2026-07-01T00:00:00Z"}],
        })
        assert "0 newer than the doc, by last commit" in " ".join(block(out, "CLAWGATE"))
        assert findings(out) == []

    def test_it_works_in_a_real_git_WORKTREE(self, tmp_path, stubs):
        """🔴 FOUND BY WRITING THE FIXTURE, NOT BY RUNNING THE CODE. In a git
        WORKTREE `.git` is a FILE holding `gitdir: …`, not a directory — so a
        `[ -d "$REPO/.git" ]` precondition is false in exactly the checkout the
        commit-date clock exists for, and it would fall straight back to the
        mtime a checkout just reset.

        MEASURED on the real thing: running the pre-fix script inside
        /home/zach/workspace/devrc-clawgate-task (a worktree) printed
        `GIT/PR (not a git repo: …)` and reconciled NOTHING — that `-d` was
        pre-existing in git_pr_block and is fixed in the same pass.

        This fixture builds a REAL worktree with `git worktree add`, so it is
        the file-vs-directory `.git` that is under test, not a simulation."""
        repo = make_repo(tmp_path, fm("193"), commit_date="2026-08-01T00:00:00 +0000")
        env = _git_env(repo)
        wt = tmp_path / "wt"
        subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", "--detach",
                        str(wt)], check=True, env=env)
        assert (wt / ".git").is_file(), "the fixture did not produce a real worktree"
        doc = wt / "claudedocs" / "handoff-sample.md"
        # what a checkout does: every file stamped NOW
        now = int(datetime.datetime(2026, 8, 20, tzinfo=datetime.timezone.utc).timestamp())
        os.utime(doc, (now, now))
        d, log = stubs
        env["PATH"] = f"{d}{os.pathsep}{env['PATH']}"
        env["STUB_LOG"] = str(log)
        env["STUB_CG_RC"] = "0"
        tf = tmp_path / "task.json"
        tf.write_text(json.dumps({"id": 193, "status": "open",
                                  "comments": [{"createdAt": "2026-08-10T00:00:00Z"}]}))
        env["STUB_CG_JSON"] = str(tf)
        out = subprocess.run(["bash", str(RESUME)], cwd=str(wt), capture_output=True,
                             text=True, timeout=60, env=env)
        assert out.returncode == 0, out.stderr
        assert "not a git repo" not in out.stdout, out.stdout
        assert "1 newer than the doc, by last commit" in " ".join(
            block(out.stdout, "CLAWGATE")), out.stdout

    def test_an_UNDATABLE_ref_copy_counts_every_comment_rather_than_dating_the_discarded_one(
        self, tmp_path, stubs
    ):
        """🔴 NEW-5. When the text came from a ref but that ref carries no commit
        for the path (a shallow or grafted clone), the mtime on disk belongs to
        the DISCARDED copy and is typically NEWER than the ref's commit — so
        falling back to it SILENCES comments, in the one case where the code
        already knows it is reading someone else's copy. Cutoff 0 instead: every
        comment counted as newer, with a gap saying why.

        Driven at the FUNCTION level — the globals `handoff_freshness` publishes
        are set directly and `clawgate_block` is called — because the branch
        needs a ref that carries no commit for the path, and a fixture that
        merely *looks* shallow is not guaranteed to produce that (measured: a
        `.git/shallow` marker did not; the test would then SKIP, and a skip in
        this suite fails the gate's pinned skip set, which is the correct
        pressure to build the deterministic drive instead)."""
        repo = make_repo(tmp_path, fm("193"), commit_date="2026-08-01T00:00:00 +0000")
        doc = repo / "claudedocs" / "handoff-sample.md"
        # the mtime that WOULD silence the comment if the fallback used it
        now = int(datetime.datetime(2026, 8, 20, tzinfo=datetime.timezone.utc).timestamp())
        os.utime(doc, (now, now))
        d, log = stubs
        env = _git_env(repo)
        env["PATH"] = f"{d}{os.pathsep}{env['PATH']}"
        env["STUB_LOG"] = str(log)
        env["STUB_CG_RC"] = "0"
        tf = tmp_path / "task.json"
        tf.write_text(json.dumps({"id": 193, "status": "open",
                                  "comments": [{"createdAt": "2026-08-10T00:00:00Z"}]}))
        env["STUB_CG_JSON"] = str(tf)
        script = (
            'source "$1"\n'
            'REPO="$2"; HANDOFF="$3"; HANDOFF_TEXT=$(cat "$3")\n'
            # a ref that exists nowhere: `git log` on it answers nothing at all
            'HANDOFF_REF="origin/no-such-ref"; HANDOFF_REL="claudedocs/handoff-sample.md"\n'
            'DRIFT=(); UNRECONCILED=()\n'
            'clawgate_block\n'
            'printf "GAP %s\\n" "${UNRECONCILED[@]}"\n'
        )
        r = subprocess.run(
            ["bash", "-c", script, "harness", str(RESUME), str(repo), str(doc)],
            capture_output=True, text=True, timeout=60, env=env, cwd=str(repo),
        )
        assert r.returncode == 0, r.stderr
        assert "UNDATED" in r.stdout, r.stdout
        assert "1 newer than the doc" in r.stdout, (
            f"the count was dated off the DISCARDED local copy, silencing it:\n{r.stdout}"
        )
        assert "carries no commit date" in r.stdout, r.stdout

    def test_that_undated_drive_would_SILENCE_the_comment_on_the_mtime_clock(
        self, tmp_path, stubs
    ):
        """POSITIVE CONTROL on the fixture above: prove the mtime really is the
        newer date, so "1 newer" is a fact about the FIX and not about a comment
        that would have counted either way. Same doc, same comment, but with no
        ref set — the mtime path — and the answer must flip to 0."""
        repo = make_repo(tmp_path, fm("193"))          # untracked: mtime clock
        doc = repo / "claudedocs" / "handoff-sample.md"
        now = int(datetime.datetime(2026, 8, 20, tzinfo=datetime.timezone.utc).timestamp())
        os.utime(doc, (now, now))
        out = run_resume(repo, stubs, task={
            "id": 193, "status": "open",
            "comments": [{"createdAt": "2026-08-10T00:00:00Z"}],
        })
        assert "comments=1 (0 newer than the doc, by file mtime)" in " ".join(
            block(out, "CLAWGATE")), out

    def test_an_UNCOMMITTED_doc_falls_back_to_mtime_AND_says_so(self, tmp_path, stubs):
        """The fallback is honest rather than silent: an untracked doc has no
        commit date, mtime is all there is, and a `!` gap names the clock —
        because that clock is the one a checkout, copy or rsync resets."""
        out = run_resume(make_repo(tmp_path, fm("193")), stubs, task={
            "id": 193, "status": "open", "comments": [],
        })
        assert "by file mtime" in " ".join(block(out, "CLAWGATE"))
        assert any("FILE MTIME" in g for g in gaps(out)), drift_lines(out)

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
    cfg_copy = tmp_path / "curl-config.copy"
    cfg_paths = tmp_path / "curl-config.paths"
    tmpdir = tmp_path / "tmpdir"
    tmpdir.mkdir()
    # 🔴 The stub COPIES the --config file aside. Without that, the only thing
    # the suite could say about the token is that it is not in argv — and the
    # file it IS in, its escaping, and whether it is unlinked afterwards were
    # all unmeasured.
    write_exec(binp / "curl", (
        'out=""\n'
        'while [ $# -gt 0 ]; do\n'
        '  case "$1" in\n'
        '    -o) out="$2"; shift 2 ;;\n'
        # 🔴 The PATH is logged as well as the contents. Copying the file proves
        # a config carried the token; only the path proves WHERE it lived — and
        # without that join a compound mutant (write to /tmp, drop the `rm`,
        # drop it from the trap) left the suite green with a real token file
        # persisting while the fixture's TMPDIR sat empty.
        '    --config) printf "%s\\n" "$2" >> "$STUB_CFG_PATHS"; cp "$2" "$STUB_CFG_COPY"; shift 2 ;;\n'
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
        env["STUB_CFG_COPY"] = str(cfg_copy)
        env["STUB_CFG_PATHS"] = str(cfg_paths)
        # 🔴 LOAD-BEARING, AND IT WAS MISSING. `mktemp` honours TMPDIR, so
        # without this the subject wrote its token file into the ambient /tmp
        # and the cleanup test compared an empty directory against itself —
        # green with `rm -f` deleted. See test_the_config_file_is_deleted.
        env["TMPDIR"] = str(tmpdir)
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
    go.cfg_copy = cfg_copy  # type: ignore[attr-defined]
    go.cfg_paths = cfg_paths  # type: ignore[attr-defined]
    go.tmpdir = tmpdir  # type: ignore[attr-defined]
    return go


ONE = {"sessionId": "sess-abc-123", "tasks": [{"id": 193, "status": "open", "title": "drain the queue"}]}
NONE = {"sessionId": "sess-abc-123", "tasks": []}
TWO = {"sessionId": "s", "tasks": [{"id": 12, "status": "open", "title": "a"},
                                   {"id": 34, "status": "in_progress", "title": "b"}]}

#: 🔴 THE LINK-ROLE VOCABULARY, spelled HERE so the ledger below is a real
#: two-way check rather than a mirror of whatever the subject happens to say.
#: The authority is clawgate's own `internal/notes/threads.go` (`RoleRead` /
#: `RoleWorked` / `RoleCreated`), in a different repository — see the lib's own
#: header for what sets each, and for why `worked` is a strong signal (the
#: pickup flow's comment/status write-back is mandated and hook-enforced) while
#: `created` is TERMINAL and outranks it.
ROLE_VOCAB = ("read", "worked", "created")

#: 🔴 IDS CHOSEN SO THEY CANNOT COLLIDE WITH A COUNT. Every verdict line in the
#: subject prints numbers — "1 WORKED task of 3 link(s) (2 created, 0 read)" —
#: and an assertion looking for an id among them is only meaningful if no id can
#: be mistaken for one. Three digits each, pairwise distinct, none of them a
#: plausible tally. `claude/RULES.md` -> "a fixture that can only ever produce
#: the constant's own value cannot see a mutant that hardcodes the literal".
WORKED_ID, WORKED_ID2 = 771, 772
CREATED_ID, CREATED_ID2 = 552, 663
READ_ID = 884


def rrow(task_id, role=None, *, status="open", title="t"):
    """One task row. `role=None` means the KEY IS ABSENT — not `null`, and not
    an empty string: an older server simply does not emit it, and that is the
    case the fallback exists for."""
    row = {"id": task_id, "status": status, "title": title}
    if role is not None:
        row["role"] = role
    return row


def rpayload(*rows):
    return {"sessionId": "sess-abc-123", "tasks": list(rows)}


def recorded(stdout: str) -> list[str]:
    """Every id the subject told the writer to record. The one thing that must
    never be wrong, read structurally rather than by eyeballing a phrase."""
    return re.findall(r"^clawgate: .*\bclawgate-task: (\S+)\s*$", stdout, re.M)


def role_order(stdout: str) -> list[str]:
    """The `role=` annotations, in the order the candidate rows were printed."""
    return re.findall(r"^\s+#\S+ role=(\S+) ", stdout, re.M)


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

    # ---------------------------------------------------- ranking by role ----
    # 🔴 WHY THESE EXIST. The endpoint has always returned `role` beside
    # `id`/`status`/`title`; `resolve` discarded it and counted links, so every
    # link weighed the same. Measured on the live board 2026-08-22 (138 tasks,
    # 43 links, 12 linked sessions): 6 of 12 sessions were multi-task and got
    # "ASK which one", while only 1 had more than one WORKED task — five of the
    # six questions had an answer the response already carried. Worst case was a
    # session linked to 19 tasks, 18 `created` + 1 `read`, where asking is not
    # merely noisy: EVERY answer is wrong, because that session did nobody's
    # work. Each test below drives one verdict of the replacement rule.

    def test_one_WORKED_task_among_many_created_resolves_to_the_WORKED_one(self, resolver):
        """🔴 THE POINT OF THE CHANGE, and the one assertion that must never be
        wrong: the id handed to the writer is the WORKED row's, not a created
        one's and not "the only one" — there are three."""
        r = resolver(rpayload(rrow(CREATED_ID, "created"),
                              rrow(WORKED_ID, "worked"),
                              rrow(CREATED_ID2, "created")))
        assert r.returncode == 0, r.stdout + r.stderr
        assert recorded(r.stdout) == [str(WORKED_ID)], r.stdout
        assert "1 WORKED task" in r.stdout
        # the other two are still SHOWN — the operator can see what was passed over
        assert f"#{CREATED_ID}" in r.stdout and f"#{CREATED_ID2}" in r.stdout

    def test_two_WORKED_tasks_are_genuinely_ambiguous_and_are_ASKED_about(self, resolver):
        r = resolver(rpayload(rrow(WORKED_ID, "worked"),
                              rrow(WORKED_ID2, "worked"),
                              rrow(CREATED_ID, "created")))
        assert r.returncode == 6, r.stdout
        assert "2 WORKED tasks" in r.stdout
        assert "ASK which one" in r.stdout
        assert recorded(r.stdout) == [], r.stdout

    def test_zero_WORKED_beside_several_created_says_it_belongs_to_NONE(self, resolver):
        """🔴 The rc 6 message has to say WHICH rc 6 this is. "several tasks —
        pick one" is actively wrong here: the honest report is that none of them
        is this document's work, so every answer to that question is wrong."""
        r = resolver(rpayload(rrow(CREATED_ID, "created"),
                              rrow(CREATED_ID2, "created"),
                              rrow(READ_ID, "read")))
        assert r.returncode == 6, r.stdout
        assert "NONE of them WORKED" in r.stdout
        assert "(2 created, 1 read)" in r.stdout
        assert "belongs to NONE of them" in r.stdout
        assert recorded(r.stdout) == [], r.stdout

    def test_a_LONE_created_row_is_ASKED_about_rather_than_recorded(self, resolver):
        """🔴 THE DELIBERATE BEHAVIOUR CHANGE. The old count rule returned 0 for
        this — one row, one id, record it. "I filed this ticket" and "this
        document describes that work" are different claims, and recording the
        first as the second gives the doc a task it is then reconciled against
        for its whole life."""
        r = resolver(rpayload(rrow(CREATED_ID, "created")))
        assert r.returncode == 6, r.stdout
        assert recorded(r.stdout) == [], r.stdout
        assert "NONE of them WORKED" in r.stdout
        assert "(1 created, 0 read)" in r.stdout

    def test_a_lone_READ_row_is_also_asked_about(self, resolver):
        """`read` is `task-pickup.md` step 1 firing the write-back guard — the
        session looked at the board, nothing more."""
        r = resolver(rpayload(rrow(READ_ID, "read")))
        assert r.returncode == 6, r.stdout
        assert recorded(r.stdout) == [], r.stdout
        assert "NONE of them WORKED" in r.stdout

    def test_a_lone_WORKED_row_still_resolves(self, resolver):
        """POSITIVE CONTROL on the rule's cheapest case — without it the three
        rc 6 tests above are equally satisfied by a subject that never returns
        0 at all."""
        r = resolver(rpayload(rrow(WORKED_ID, "worked")))
        assert r.returncode == 0, r.stdout
        assert recorded(r.stdout) == [str(WORKED_ID)], r.stdout

    def test_the_nineteen_row_real_world_shape_asks_and_names_the_split(self, resolver):
        """The measured worst case, at its measured size: a backlog-filling
        session linked to 19 tasks, 18 `created` + 1 `read`. The old rule said
        "19 tasks resolved — ASK which one"; the answer to that question does
        not exist."""
        rows = [rrow(400 + i, "created", title=f"filed {i}") for i in range(18)]
        rows.append(rrow(READ_ID, "read", title="glanced at"))
        r = resolver(rpayload(*rows))
        assert r.returncode == 6, r.stdout
        assert "19 task(s) linked to this session" in r.stdout
        assert "(18 created, 1 read)" in r.stdout
        assert recorded(r.stdout) == [], r.stdout

    def test_the_candidates_are_printed_WORKED_FIRST(self, resolver):
        """The rc 6 question has to be answerable from THIS output — a second
        command to find out which row was which defeats the point. Ordering is
        asserted from a fixture whose input order is the REVERSE of the expected
        one, so a subject that merely echoes the server's order fails."""
        r = resolver(rpayload(rrow(READ_ID, "read"),
                              rrow(CREATED_ID, "created"),
                              rrow(WORKED_ID, "worked"),
                              rrow(WORKED_ID2, "worked")))
        assert role_order(r.stdout) == ["worked", "worked", "created", "read"], r.stdout

    def test_roles_absent_on_EVERY_row_falls_back_to_counting_and_says_so(self, resolver):
        """🔴 A MISSING FIELD MUST NOT BECOME "ZERO WORKED". An older server, or
        the field being dropped, makes every count 0 — and the role rule would
        then answer "none of them worked" about a board that was never asked.
        One row falls back to the old rc 0, and the output says the ranking did
        not happen."""
        r = resolver(rpayload(rrow(WORKED_ID)))
        assert r.returncode == 0, r.stdout
        assert recorded(r.stdout) == [str(WORKED_ID)], r.stdout
        assert "ROLES UNAVAILABLE" in r.stdout
        assert "WORKED" not in r.stdout.replace("ROLES UNAVAILABLE", "")

    def test_roles_absent_on_every_row_keeps_the_old_MULTI_task_question(self, resolver):
        r = resolver(rpayload(rrow(CREATED_ID), rrow(WORKED_ID)))
        assert r.returncode == 6, r.stdout
        assert "ROLES UNAVAILABLE" in r.stdout
        assert "2 tasks resolved" in r.stdout and "ASK which one" in r.stdout
        assert recorded(r.stdout) == [], r.stdout

    def test_a_NULL_role_counts_as_absent_not_as_unrecognised(self, resolver):
        """`role: null` and no `role` key at all are the same claim — the server
        emits neither today, but `omitempty` and an explicit null are one field
        change apart, and treating null as a strange VALUE would fire the
        unrecognised-role alarm on a healthy board."""
        r = resolver({"tasks": [{"id": READ_ID, "status": "open", "title": "a", "role": None}]})
        assert r.returncode == 0, r.stdout
        assert "ROLES UNAVAILABLE" in r.stdout
        assert "UNRECOGNISED" not in r.stdout

    def test_PARTIAL_role_absence_is_data_not_a_fallback(self, resolver):
        """🔴 THE BOUNDARY BETWEEN THE TWO RULES, and it is not "any absence".
        One row carrying a role means the board CAN answer, so the ranking runs;
        the row that carried none is reported and counts as not worked. A
        subject that fell back here would record the wrong id whenever the
        absent row was the only other one."""
        r = resolver(rpayload(rrow(CREATED_ID), rrow(WORKED_ID, "worked")))
        assert r.returncode == 0, r.stdout
        assert recorded(r.stdout) == [str(WORKED_ID)], r.stdout
        assert "ROLES UNAVAILABLE" not in r.stdout
        assert "1 of 2 row(s) carried no `role`" in r.stdout

    def test_an_UNRECOGNISED_role_is_REPORTED_never_folded_into_not_worked(self, resolver):
        """🔴 The same reasoning as `clawgate_known_status`: a vocabulary this
        tool has never heard of must not render like a healthy one. It is not a
        permanently-red gate — the server's CHECK constraint means today's board
        cannot emit one — which is exactly why a silent verdict here would be
        wrong on the one day it fires."""
        r = resolver(rpayload(rrow(WORKED_ID, "worked"),
                              rrow(CREATED_ID, "reviewed")))
        assert r.returncode == 6, r.stdout
        assert "UNRECOGNISED ROLE" in r.stdout
        assert "reviewed" in r.stdout, "the value itself has to be named"
        assert recorded(r.stdout) == [], r.stdout

    def test_an_unrecognised_role_is_NOT_silently_treated_as_worked(self, resolver):
        """NEGATIVE CONTROL on the one guess that would look right: a lone
        unknown role must not become the recorded task."""
        r = resolver(rpayload(rrow(CREATED_ID, "definitely-worked-on-it")))
        assert r.returncode == 6, r.stdout
        assert recorded(r.stdout) == [], r.stdout
        assert "UNRECOGNISED ROLE" in r.stdout

    @pytest.mark.parametrize("bad_role", [7, 1.5, True, ["worked"], {"role": "worked"}])
    def test_no_shape_of_non_string_role_is_read_as_a_role(self, resolver, bad_role):
        r = resolver(rpayload(rrow(WORKED_ID, bad_role)))
        assert r.returncode == 6, r.stdout
        assert "UNRECOGNISED ROLE" in r.stdout
        assert recorded(r.stdout) == [], r.stdout

    def test_a_row_with_no_usable_id_still_wins_over_the_role_verdict(self, resolver):
        """ORDERING GUARD. A payload can be BOTH id-broken and role-rich; the
        gap has to win, or a schema break gets a confident resolution."""
        r = resolver({"tasks": [{"status": "open", "title": "no id", "role": "worked"}]})
        assert r.returncode == 4, r.stdout
        assert "only 0 carry a usable id" in r.stdout
        assert recorded(r.stdout) == [], r.stdout

    def test_the_role_vocabulary_is_exactly_what_this_file_drives(self):
        """🔴 LEDGER, both directions — the same discipline as the preflight
        list below. A role added to the lib and not driven here fails, and one
        dropped from the lib fails too. It cannot check the CROSS-REPO half
        (clawgate's `threads.go` is in another repository and no test here can
        see it); it only keeps this file and the subject honest about the same
        three, which is why the lib's header records the hand verification."""
        src = LIB.read_text(encoding="utf-8")
        m = re.search(r'^CLAWGATE_TASK_ROLES="([a-z ]+)"\s*$', src, re.M)
        assert m, "CLAWGATE_TASK_ROLES is no longer a plain space-separated literal"
        assert tuple(m.group(1).split()) == ROLE_VOCAB, m.group(1)

    def test_the_ranking_lives_above_the_IO_line(self):
        """🔴 THE PURE/IO CONTRACT the file's header states, checked rather than
        trusted. New logic on the wrong side of that line is untestable without
        a server, which is how the whole verdict became one 50-line block inside
        the curl call in the first place."""
        src = LIB.read_text(encoding="utf-8")
        io_line = src.index("# I/O — everything below this line")
        assert src.index("clawgate_rank_rows(){") < io_line
        assert src.index("clawgate_resolve(){") > io_line

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

    def test_the_config_file_carries_the_token_at_all(self, resolver):
        """🔴 POSITIVE CONTROL FOR THE TEST BELOW, and it is the half that was
        missing. "TMPDIR is empty afterwards" is satisfied by a run that never
        wrote a token file at all — including a run pointed at the wrong TMPDIR,
        which is exactly what the previous version did. This proves the file
        exists, is the one curl was handed, and contains the credential; the
        next test then proves it is gone."""
        r = resolver(ONE)
        assert r.returncode == 0
        cfg = resolver.cfg_copy  # type: ignore[attr-defined]
        assert cfg.exists(), "curl was never handed a --config file"
        assert SENTINEL_TOKEN in cfg.read_text()

    def test_the_token_file_LIVES_where_the_deletion_test_looks(self, resolver):
        """🔴 THE JOIN, and its absence made the pair below walkable. The control
        above proves *a* config carried the token — but it reads the stub's COPY,
        so it says nothing about where the original lived; the deletion test
        inspects a directory the subject may never have used. A compound mutant
        (`cfg=$(TMPDIR=/tmp mktemp)`, no `rm`, not in the trap) therefore left
        328/328 green with a real `-rw------- … Authorization: Bearer …`
        persisting outside the fixture. Asserting the PATH is what ties the two
        halves to the same file."""
        r = resolver(ONE)
        assert r.returncode == 0
        paths = resolver.cfg_paths.read_text().split()  # type: ignore[attr-defined]
        assert len(paths) == 1, f"expected exactly one --config, got {paths}"
        tmpdir = resolver.tmpdir  # type: ignore[attr-defined]
        assert Path(paths[0]).parent == tmpdir, (
            f"the token file was written to {paths[0]}, outside the TMPDIR the "
            f"deletion test inspects ({tmpdir}) — the two halves are measuring "
            f"different files"
        )

    def test_the_config_file_is_deleted_afterwards(self, resolver):
        """🔴 THE GUARD ON A LIVE BEARER TOKEN, and it was VACUOUS until now.

        The first version created its own `env_tmp`, never put it in the
        subprocess environment, and compared that empty directory against
        itself — so `mktemp` wrote into the ambient /tmp and the assertion could
        not see it. An auditor deleted `rm -f "$cfg"` and 95/95 stayed green,
        then found a real `tmp.XXXX` holding `Authorization: Bearer …`.

        The fixture now exports TMPDIR, so `mktemp` writes HERE, and the control
        above proves a file really does appear. Watched to fail with the `rm`
        removed and to pass with it restored."""
        tmpdir = resolver.tmpdir  # type: ignore[attr-defined]
        assert not list(tmpdir.iterdir()), "the fixture TMPDIR did not start empty"
        r = resolver(ONE)
        assert r.returncode == 0
        left = list(tmpdir.iterdir())
        assert left == [], (
            "resolve left a file in TMPDIR — the curl config holds a live bearer "
            f"token: {[p.name for p in left]}"
        )

    def test_the_token_is_ESCAPED_for_curls_config_parser(self, resolver):
        """curl processes backslash escapes inside a double-quoted config value,
        so a token containing `"` or `\\` is sent mangled — and arrives as a 401
        that reads like a wrong token rather than a quoting bug. The fixture's
        token carries BOTH characters; the written file must carry them escaped
        and must not terminate the quoted value early."""
        weird = 'tok-"quote"-and\\slash'
        r = resolver(ONE, env_file=(
            "CLAWGATE_API_URL=http://clawgate.invalid:1\n"
            f"CLAWGATE_HOOK_TOKEN={weird}\n"
        ))
        assert r.returncode == 0
        written = resolver.cfg_copy.read_text()  # type: ignore[attr-defined]
        assert written == (
            'header = "Authorization: Bearer tok-\\"quote\\"-and\\\\slash"\n'
        ), repr(written)

    def test_a_title_carrying_a_NEWLINE_is_still_one_task(self, resolver):
        """🔴 FIXTURE-DERIVED, not subject-derived: no other fixture supplies a
        title with an interior newline, and the count used to be `grep -c .` over
        the RENDERED rows — so one task reported "2 tasks resolved — ASK which
        one". The server trims and rune-caps titles; it does not strip interior
        newlines."""
        r = resolver({"tasks": [{"id": 193, "status": "open",
                                 "title": "line one\nline two"}]})
        assert r.returncode == 0, r.stdout
        assert "clawgate-task: 193" in r.stdout
        assert "tasks resolved" not in r.stdout

    def test_a_row_with_NO_id_is_a_gap_not_an_empty_field(self, resolver):
        """🔴 FIXTURE-DERIVED. Every other fixture always carries an id, so the
        `sed` that extracted it could match ZERO digits and nothing noticed: the
        writer was told to record `clawgate-task:` with no value, which the
        reader classifies as present-and-unreadable — a permanent `!` gap on
        every future /resume of that doc."""
        r = resolver({"tasks": [{"status": "open", "title": "no id here"}]})
        assert r.returncode == 4, r.stdout
        assert "only 0 carry a usable id" in r.stdout
        assert "record nothing" in r.stdout
        assert not re.search(r"clawgate-task:\s*$", r.stdout, re.M), r.stdout

    @pytest.mark.parametrize("bad_id", [None, "abc", -1, 1.5, {"n": 1}])
    def test_no_shape_of_unusable_id_ever_reaches_the_writer(self, resolver, bad_id):
        r = resolver({"tasks": [{"id": bad_id, "status": "open", "title": "t"}]})
        assert r.returncode == 4, r.stdout
        assert "record nothing" in r.stdout

    # The tools `resolve` preflights. Spelled here so the ledger below fails
    # when the list in the lib grows or shrinks without this test moving.
    PREFLIGHTED = ("curl", "jq", "mktemp")

    def test_the_preflight_list_is_exactly_what_this_test_drives(self):
        """🔴 LEDGER. Removing `mktemp` from the lib's list survived the whole
        suite, because only `jq`'s absence was ever exercised — one driven
        member reads as a driven list. Both directions: a tool added to the
        preflight and not driven here fails, and one dropped from the lib fails
        too."""
        src = LIB.read_text(encoding="utf-8")
        m = re.search(r"^\s*for need in ([a-z ]+); do", src, re.M)
        assert m, "the preflight loop is no longer a plain `for need in …`"
        assert tuple(m.group(1).split()) == self.PREFLIGHTED, m.group(1)

    @pytest.mark.parametrize("missing", PREFLIGHTED)
    def test_every_preflighted_tool_is_individually_driven(self, tmp_path, missing):
        """Each tool removed IN TURN, with the other two present — the shape a
        ledger alone cannot check."""
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        (home / ".claude" / "clawgate.env").write_text(
            f"CLAWGATE_API_URL=http://clawgate.invalid:1\n"
            f"CLAWGATE_HOOK_TOKEN={SENTINEL_TOKEN}\n", encoding="utf-8")
        binp = tmp_path / f"bin-no-{missing}"
        binp.mkdir()
        for name in ("bash", "sh", "cat", "sed", "chmod", "rm", "cp", "curl", "jq", "mktemp"):
            if name == missing:
                continue
            p = shutil.which(name)
            if p:
                (binp / name).symlink_to(p)
        assert not shutil.which(missing, path=str(binp)), f"{missing} leaked into the bin"
        env = _base_env()
        env["HOME"] = str(home)
        env["PATH"] = str(binp)
        env[SESSION_VAR] = "sess-abc-123"
        out = subprocess.run(["bash", str(LIB), "resolve"], capture_output=True,
                             text=True, timeout=30, env=env)
        assert out.returncode == 4, out.stdout + out.stderr
        assert f"`{missing}` is not on PATH" in out.stdout, out.stdout
        assert "UNKNOWN, not empty" in out.stdout

    def test_a_missing_TOOL_is_a_gap_naming_the_tool(self, resolver, tmp_path):
        """🔴 Finding 9's class. The preflight checked `curl` and `jq` while the
        count used `grep`, so a host without grep turned a real resolution into
        "NOTHING RESOLVED" (exit 5) — a tool absence rendering as a fact about
        the board. Every external command is now named up front; this drives the
        one that is easiest to remove."""
        r = resolver(ONE, env_file=(
            "CLAWGATE_API_URL=http://clawgate.invalid:1\n"
            f"CLAWGATE_HOOK_TOKEN={SENTINEL_TOKEN}\n"
        ), )
        assert r.returncode == 0, "control: it resolves with every tool present"
        # now the same call with a PATH holding only the curl stub (no jq)
        env = _base_env()
        env["HOME"] = str(tmp_path / "home")
        onlycurl = tmp_path / "only-curl"
        onlycurl.mkdir()
        for name in ("bash", "sh", "cat", "sed", "mktemp", "chmod", "rm", "cp"):
            p = shutil.which(name)
            if p:
                (onlycurl / name).symlink_to(p)
        env["PATH"] = str(onlycurl)
        env[SESSION_VAR] = "sess-abc-123"
        out = subprocess.run(["bash", str(LIB), "resolve"], capture_output=True,
                             text=True, timeout=30, env=env)
        assert out.returncode == 4, out.stdout + out.stderr
        assert "is not on PATH" in out.stdout
        assert "UNKNOWN, not empty" in out.stdout


# --------------------------------------------------------------------------- #
# §6 the skills and the code must not drift apart
# --------------------------------------------------------------------------- #
HANDOFF_PINS: list[tuple[str, str]] = [
    ("scripts/lib/clawgate_handoff.sh", "the step invokes the tool that owns the resolution"),
    (SESSION_VAR, "🔴 the EXACT session variable reaches the executor"),
    ("clawgate-task: 193", "the front-matter SHAPE is shown, not described"),
    ("NEVER create a task", "🔴 a task is never minted to fill a blank field"),
    ("ASK the user which one", "several resolved => a question, not a guess"),
    ("one WORKED task", "🔴 rc 0 means ONE WORKED task now, never just one link"),
    ("no worked task at all", "🔴 the rc 6 the executor must NOT answer by picking"),
    ("ROLES UNAVAILABLE", "the roles-missing fallback is named, so it is legible"),
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

    def test_every_CLOCK_the_script_can_print_is_documented(self):
        """🔴 DERIVED FROM THE SCRIPT, not restated. The skill enumerated two
        clock names; the branch that added `on <ref>` did not update it, and the
        one that added `UNDATED` would not have either — a reader told there are
        two, shown a third, has to guess what it means. Same shape as
        test_handoff_doc.py's exit-code sweep: scrape what the code can emit and
        require the doc to mention each."""
        src = RESUME.read_text(encoding="utf-8")
        # Every `clock="…"` assignment ANYWHERE on a line (they are not all at
        # the start of one), truncated at the first interpolation — the STATIC
        # prefix is what a skill can carry.
        emitted = {re.split(r"\$", m)[0].strip().rstrip("(").strip()
                   for m in re.findall(r'clock="([^"]*)"', src)}
        emitted.discard("")
        assert len(emitted) >= 3, f"the scraper found too few clocks: {emitted}"
        doc = RESUME_SKILL.read_text(encoding="utf-8")
        for clock in sorted(emitted):
            assert clock in doc, (
                f"resume-state.sh can print the clock {clock!r} and "
                f"claude/skills/resume/SKILL.md never mentions it."
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

    def test_only_handoff_freshness_READS_the_working_tree_copy(self):
        """🔴 THE CLASS, NOT THE INSTANCE — and this exists because the class was
        identified, one instance was fixed, and a second sat three functions away
        in the same file for a whole review round.

        `handoff_freshness` decides WHICH copy of the doc is authoritative (the
        working tree, or `origin/<default>` when the branch is behind) and
        publishes it as `$HANDOFF_TEXT`. Any other function that opens
        `$HANDOFF` for its CONTENT silently opts out of that decision — and the
        two copies differ exactly when it matters.

        Structural, not spelled: it walks every use of `$HANDOFF` in the script,
        attributes it to its enclosing function, and classifies it as a CONTENT
        read or a PATH use. Content reads outside `handoff_freshness` fail. Path
        uses are LEDGERED, so a new one has to be classified here rather than
        appearing unreviewed — the ledger fails when it grows *or* shrinks."""
        src = RESUME.read_text(encoding="utf-8").splitlines()
        # commands that open a file for its CONTENT (vs. ones that take a path)
        content_re = re.compile(
            r'(?:^|[;&|(]|\$\()\s*(?:cat|grep|head|tail|sed|awk|wc|jq)\b[^\n]*"\$HANDOFF"'
            r'|<\s*"\$HANDOFF"'
        )
        # 🔴 …AND THE LAUNDERED FORM, which is how the THIRD instance hid from
        # the first version of this guard: `handoff_says_inflight "$num"
        # "$HANDOFF"` reads the file inside a helper that merely *takes a path*,
        # so no content-reading command appears on the line at all — and it sat
        # inside `git_pr_block`, a function already in the path ledger, whose
        # comment described only its OTHER `$HANDOFF` use. Green 141/141.
        launderers = laundered_readers(src)
        assert not launderers, (
            "a helper is handed $HANDOFF (a PATH) and reads its CONTENT — so the "
            "caller's text and this helper's text can be different copies of the "
            "document:\n"
            + "\n".join(f"  {n}: {h}" for n, hs in launderers for h in hs)
            + "\n  Give the helper the TEXT instead; that removes the class from "
              "it permanently."
        )
        # `[ -n "$HANDOFF" ]` asks whether a doc resolved AT ALL. It opens
        # nothing, so it is neither a content read nor a file access, and every
        # block legitimately does it.
        presence_re = re.compile(r'\[\s*-[nz]\s+"\$HANDOFF"\s*\]')
        fn_re = fn_re_top
        fn = "(top level)"
        content_hits, path_hits = [], []
        for i, line in enumerate(src, 1):
            m = fn_re.match(line)
            if m:
                fn = m.group(1)
            if line.lstrip().startswith("#") or '"$HANDOFF"' not in line:
                continue
            if content_re.search(line):
                content_hits.append((fn, i, line.strip()))
            elif not presence_re.search(line):
                path_hits.append((fn, i, line.strip()))

        offenders = [h for h in content_hits if h[0] != "handoff_freshness"]
        assert not offenders, (
            "a block reads the working-tree copy directly instead of "
            "$HANDOFF_TEXT — the copy handoff_freshness chose:\n"
            + "\n".join(f"  {f}:{n}: {ln}" for f, n, ln in offenders)
        )
        assert content_hits, (
            "POSITIVE CONTROL: the scanner found NO content read at all, so it "
            "cannot have been looking — handoff_freshness itself does one."
        )
        # The ledger: every PATH use, by function. Each is a use of the NAME,
        # not of the bytes, so it is compatible with the rule above — but a new
        # one must be added here deliberately.
        #
        # 🔴 COUNTED PER FUNCTION, NOT JUST NAMED. A set of function names let a
        # SECOND use appear inside a function already on the list — which is
        # exactly how the laundered read hid: `git_pr_block` was ledgered for its
        # `basename` use, and its other `$HANDOFF` use (the one handed to a
        # helper) inherited that approval. A count makes every new use its own
        # review.
        counts: dict[str, int] = {}
        for f, _n, _l in path_hits:
            counts[f] = counts.get(f, 0) + 1
        assert counts == {
            # dates the doc: `git log … -- "$HANDOFF"` (local branch, only when
            # the working-tree copy IS the chosen one) and the `stat -c %Y`
            # fallback beneath it
            "clawgate_block": 2,
            # prints `handoff: $(basename …)`
            "git_pr_block": 1,
            # resolves `rel` for the ref comparison
            "handoff_freshness": 1,
            # derives $REPO from the path when an explicit doc is passed (twice:
            # `git -C $(dirname …)` and the plain-dirname fallback), plus the
            # `basename` naming the doc it FELL BACK to in the #684 gap line —
            # a use of the name only, and it must stay one: that message is
            # emitted from resolve(), before handoff_freshness has run, so
            # $HANDOFF_TEXT does not exist yet and any CONTENT read here would
            # necessarily be of the unchosen copy.
            "resolve": 3,
        }, sorted(path_hits)

    def test_the_laundered_read_scan_can_actually_find_one(self):
        """🔴 POSITIVE CONTROL, and it is not decoration: the FIRST version of
        this scan returned an empty list for the very shape it was written for
        (`re.finditer` returns non-overlapping matches, so on
        `if [ … ] && helper "$num" "$HANDOFF"` the only match it found began at
        `if`, which the keyword filter then discarded). It passed the mutant.
        A scan wired to nothing is indistinguishable from a clean file, so it is
        run here against a DECOY that must trip it, and against one that must
        not."""
        decoy = [
            'reader(){',
            '  grep -q "x" "$1" || return 1',
            '}',
            'caller(){',
            '  if [ "$state" = MERGED ] && reader "$HANDOFF" "$n"; then :; fi',
            '}',
        ]
        assert [n for n, _ in laundered_readers(decoy)] == ["reader"], decoy
        # …and a helper handed the TEXT is not a launderer, however it reads it
        clean = [
            'reader(){',
            '  printf %s "$2" | grep -q x || return 1',
            '}',
            'caller(){',
            '  reader "$n" "$text"  # $HANDOFF is not passed at all',
            '}',
        ]
        assert laundered_readers(clean) == []

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

    def test_an_explicit_front_matter_that_DROPS_the_field_is_reported(self):
        """🔴 THE SIBLING HOLE. `(upd_fm or base_fm)` discards the base's block
        wholesale, so an update bringing its own front matter deletes the
        recorded task with `dropped == ()` — while the *preamble* sibling had
        been given a warning. "An explicit one wins" stays the rule; it is prose
        in a skill, and prose is not a guard.

        REACHABLE BY ACCIDENT, which is why it matters: a delta whose first line
        is `---` used as a horizontal rule, with another `---` further down, is a
        well-formed front-matter block to `split_front_matter`."""
        report = hd.merge_report(BASE_DOC, "---\ntitle: something else\n---\n## State now\n- x\n")
        assert "clawgate-task" not in report.text, report.text
        hits = [d for d in report.dropped if "clawgate-task" in d.line]
        assert len(hits) == 1, report.dropped
        assert hits[0].line_no == 2, hits[0]
        assert BASE_DOC.splitlines()[hits[0].line_no - 1] == hits[0].line

    def test_replacing_the_field_with_ANOTHER_task_is_still_reported(self):
        """Changing which task a doc names is a legitimate act — and still a
        deletion of the old one, which the author should see named."""
        report = hd.merge_report(BASE_DOC, "---\nclawgate-task: 200\n---\n## State now\n- x\n")
        assert [d.line for d in report.dropped] == ["---\nclawgate-task: 193\n---".splitlines()[1]]

    def test_an_IDENTICAL_front_matter_in_the_delta_reports_nothing(self):
        """NEGATIVE CONTROL: a warning that fires when the field SURVIVES is
        noise, and rule (f) is a warn-on-loss rule."""
        report = hd.merge_report(BASE_DOC, "---\nclawgate-task: 193\n---\n## State now\n- x\n")
        assert report.dropped == (), report.dropped
        assert "clawgate-task: 193" in report.text

    def test_a_doc_with_no_front_matter_merges_exactly_as_before(self):
        """GUARD against a regression in the untouched majority: no front
        matter, so the preamble rule must behave identically to before."""
        base = "# Handoff: x\n\n## State now\n- Branch: feat/x\n"
        merged = hd.merge(base, "## State now\n- Branch: feat/y\n")
        assert merged.startswith("# Handoff: x\n")
        assert "feat/y" in merged and "feat/x" not in merged

    def test_rule_f_line_numbers_COUNT_the_front_matter(self):
        """🔴 THE SEMANTIC MERGE CONFLICT, and a clean textual resolution hides it.

        `main` added rule (f), whose warning names a BASE LINE NUMBER computed by
        `_body_start_lines` from the preamble's newline count. This branch strips
        front matter off `base_text` BEFORE `split_sections` ever sees it — so on
        the merged tree every line number for a doc carrying `clawgate-task:` was
        short by the height of that block, and a line number is the entire value
        of that warning. Neither side's tests could see it: main's fixture has no
        front matter, and this branch's had no rule (f).

        Asserted the way main's own CLI test does — by OPENING the base doc at
        every number printed and checking the line found there is the one that
        was flagged."""
        base = (
            "---\nclawgate-task: 193\n---\n"
            "# Handoff: sample — 2026-08-19\n\n"
            "## State now\n"
            "- MEASURED 2026-08-18: the widget queue drains at 3/s, not 30/s\n"
            "- Branch: feat/x\n"
        )
        report = hd.merge_report(base, "## State now\n- Branch: feat/y\n")
        assert report.dropped, "the fixture produced no durable-drop warning"
        base_lines = base.splitlines()
        for d in report.dropped:
            assert base_lines[d.line_no - 1].rstrip() == d.line, (
                f"line {d.line_no} of the base doc is "
                f"{base_lines[d.line_no - 1]!r}, but the warning quoted {d.line!r}"
            )
        assert [d.line_no for d in report.dropped] == [7], report.dropped

    def test_rule_f_line_numbers_are_right_WITHOUT_front_matter_too(self):
        """NEGATIVE CONTROL on the test above — the same doc minus the block must
        report a number 3 lower. A fixture that could not move cannot detect an
        offset."""
        base = (
            "# Handoff: sample — 2026-08-19\n\n"
            "## State now\n"
            "- MEASURED 2026-08-18: the widget queue drains at 3/s, not 30/s\n"
            "- Branch: feat/x\n"
        )
        report = hd.merge_report(base, "## State now\n- Branch: feat/y\n")
        assert [d.line_no for d in report.dropped] == [4], report.dropped

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

    def test_the_two_sides_agree_that_an_UNTERMINATED_block_is_not_front_matter(self):
        """🔴 THE OTHER DIRECTION, which the seam test above never exercised —
        and where the two sides genuinely DISAGREED. The late-block case is the
        one where they happened to agree already, so pinning only that proved
        nothing about the seam.

        python requires a closing `---`; the shell used to hand back the id on
        sight of the key. Result: the reader reconciled task 193 while `merge`
        treated the block as body and DROPPED it on the next update — the data
        loss this change exists to prevent, at the seam between its two halves.
        BOTH must now refuse."""
        unterminated = "---\nclawgate-task: 193\n# Handoff: sample\n\n## State now\n- x\n"
        assert hd.split_front_matter(unterminated)[0] == ""
        assert call_fn("clawgate_task_field", unterminated).returncode == 1
        # …and it is not silently reclassified as "this doc names no task"
        assert call_fn("clawgate_field_present", unterminated).returncode == 0

    def test_an_unterminated_block_dropped_by_a_merge_is_REPORTED(self):
        """🔴 FOUND BY THIS TEST, NOT BY THE AUDIT — the front-matter fix left a
        hole one line below itself. An unterminated block is preamble, and the
        preamble is replaced wholesale whenever the update brings its own, so a
        writer who forgets the closing `---` still loses the field on the next
        update.

        It cannot be "kept" without inventing a semantic the reader does not
        share (both sides refuse to call it front matter, deliberately). So it
        is REPORTED, under rule (f)'s existing warn-never-refuse contract, with
        an address the author can open."""
        base = "---\nclawgate-task: 193\n# Handoff\n\n## State now\n- Branch: feat/x\n"
        report = hd.merge_report(base, "prose first\n\n## State now\n- Branch: feat/y\n")
        assert "clawgate-task: 193" not in report.text, report.text
        hits = [d for d in report.dropped if "clawgate-task" in d.line]
        assert len(hits) == 1, report.dropped
        assert hits[0].line_no == 2, hits[0]
        assert base.splitlines()[hits[0].line_no - 1] == hits[0].line

    def test_the_report_is_SILENT_when_the_preamble_keeps_the_line(self):
        """NEGATIVE CONTROL: a warning that fires whether or not the line
        survives is noise, and rule (f) is explicitly a warn-on-loss rule."""
        base = "---\nclawgate-task: 193\n# Handoff\n\n## State now\n- x\n"
        report = hd.merge_report(base, "## State now\n- y\n")     # no new preamble
        assert [d for d in report.dropped if "clawgate-task" in d.line] == []
        assert "clawgate-task: 193" in report.text

    def test_an_ORDINARY_preamble_replacement_warns_about_nothing(self):
        """🔴 The reason this is narrow to the KEY rather than `durable_reason`.
        A handoff preamble is normally `# Handoff: <topic> — <date>`, which
        carries a date; running the general predicate here would fire rule (f)
        on every preamble-replacing update in the corpus — a warning on the
        ordinary case, which is the failure rule (f)'s own header forbids."""
        base = "# Handoff: sample — 2026-08-19\n\n## State now\n- x\n"
        report = hd.merge_report(base, "# Handoff: sample — 2026-08-21\n\n## State now\n- y\n")
        assert report.dropped == (), report.dropped

    def test_the_drop_report_does_not_tell_the_author_to_DISABLE_the_field(self):
        """🔴 NEW-3. The standing rule (f) remedy is "move them under an APPEND
        heading" — and `clawgate_task_field` only parses a block whose `---` is
        LINE 1. An author who obeys it moves the field somewhere no reader
        parses, and /resume then reports the doc as naming no task: the silent
        disable this whole change exists to prevent, arrived at by following the
        tool's own advice. The address was right; the label and the advice were
        not, so this class gets its own heading and its own remedy."""
        report = hd.dropped_durable_report(
            hd.merge_report(BASE_DOC, "---\ntitle: other\n---\n## State now\n- x\n").dropped
        )
        assert "RESTORE IT AT LINE 1" in report, report
        assert "Move them under an APPEND heading" not in report, report
        assert "recorded clawgate task" in report, report
        assert "(front matter):2:" in report, report

    def test_BOTH_report_blocks_render_when_both_classes_are_dropped(self):
        """…and splitting them must not lose either. An ordinary durable line
        and the field, dropped in the same update, produce both blocks with
        their own remedies."""
        base = (
            "---\nclawgate-task: 193\n---\n# H\n\n"
            "## State now\n- MEASURED 2026-08-18: the queue drains at 3/s\n- x\n"
        )
        report = hd.dropped_durable_report(
            hd.merge_report(base, "---\ntitle: other\n---\n## State now\n- y\n").dropped
        )
        assert "RESTORE IT AT LINE 1" in report
        assert "Move them under an APPEND heading" in report
        assert "MEASURED 2026-08-18" in report
        assert report.count("🔴") == 2, report

    def test_an_ordinary_durable_drop_keeps_the_ORIGINAL_remedy(self):
        """NEGATIVE CONTROL: the new heading must not swallow the old class."""
        base = "# H\n\n## State now\n- MEASURED 2026-08-18: the queue drains at 3/s\n"
        report = hd.dropped_durable_report(
            hd.merge_report(base, "## State now\n- y\n").dropped
        )
        assert "Move them under an APPEND heading" in report
        assert "RESTORE IT AT LINE 1" not in report

    def test_the_two_languages_spell_the_key_identically(self):
        """🔴 A SEAM PIN ACROSS LANGUAGES. The python merger and the bash reader
        each carry the key as their own constant; a rename on one side is
        invisible to the other and silently turns the durable field into an
        ordinary body line. Read from the SHELL SOURCE, not from a literal in
        this file, so the two constants are compared to each other."""
        src = LIB.read_text(encoding="utf-8")
        m = re.search(r'^CLAWGATE_FIELD_KEY="([^"]+)"', src, re.M)
        assert m, "CLAWGATE_FIELD_KEY is no longer a plain assignment in the lib"
        assert m.group(1) == hd.CLAWGATE_TASK_KEY == "clawgate-task"
