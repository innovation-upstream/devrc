#!/usr/bin/env python3
"""The backgrounded-command capture log — ClickUp 868ktvqf9.

WHAT EACH TEST HERE IS, because "it passes" is not a category:

  * POSITIVE CONTROL   drives the REAL hook as a subprocess with the REAL
    payload shape and asserts the recorded line. `test_the_hook_records_a_real_
    trailing_semicolon_after_a_redirect_verbatim` is the one the ticket asks
    for; without it this file would be a claim about functions nobody has
    watched write to a disk.
  * NEVER-THROWS       21 malformed / empty / huge / hostile inputs, each
    asserting `rc == 0` AND empty stdout AND empty stderr. This hook fires
    before and after every Bash call on the host; a raise breaks the shell and
    a stray byte on stdout is read as a PreToolUse permission verdict.
    `test_the_never_throws_harness_can_go_red` is its negative control — the
    battery is otherwise indistinguishable from one wired to nothing.
  * MARKER LEDGER      the scanner, pinned in BOTH directions. A marker test
    that only asserts hits is satisfied by a scanner that marks everything;
    the non-matches (`&&`, a bare trailing `;`, a heredoc body, a quoted `;`)
    are the load-bearing half: 14 of the 28 rows expect NO marker.
  * INSTRUMENT CONTROL `parse_completions` is pinned on BOTH spellings of the
    notification — the escaped one that is actually on disk and the bare one —
    because the first implementation matched only the bare form, returned `{}`
    against a real transcript, and `report()` reported `announced_exit_codes:
    []` for a record whose exit code was in the file. An empty result that
    means "my pattern is wrong" reading as "the harness never announced one"
    is the exact defect class this instrument exists to expose, reproduced
    inside the instrument. It is red on a mutant that drops either spelling.
  * ON-DISK NAMES      the COMPLETE set of relative paths the real writer
    creates under a throwaway $HOME, compared to a literal list, in the shape
    tests/test_on_disk_artifact_names.py established. Fails when the set grows
    as well as when it shrinks.
  * BOUND              rotation, measured against a cap the fixture cannot
    reach by accident.

NOT regression coverage: nothing here was red before this branch, because
nothing here existed. Every test is a specification pin or a control, and the
file says so rather than letting a green run imply otherwise.

run:  python -m pytest scripts/claude-hooks/tests/test_bg_command_capture.py -q
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
HOOKS = os.path.abspath(os.path.join(HERE, os.pardir))
ROOT = os.path.abspath(os.path.join(HOOKS, os.pardir, os.pardir))
LIB = os.path.join(ROOT, "scripts", "lib")
HOOK = os.path.join(HOOKS, "bg-command-capture.py")
MODULE = os.path.join(LIB, "bg_command_capture.py")

# Fixture values, pairwise distinct and distinct from every constant the module
# names (SCHEMA, LOG_NAME, DEFAULT_MAX_BYTES, the marker strings). A fixture that
# can only ever produce a constant's own value cannot see a mutant that hardcodes
# that constant.
SESSION = "sess-bgcap-4f2"
TOOL_USE = "toolu_bgcap_9c7"
TASK_ID = "bq7zk3m1x"
DESCRIPTION = "Run the queued unit suite off-thread"

# The shape from the ticket: a redirect, then a `;`-chained command. The reported
# status is `echo`'s, not the suite's.
MASKING_COMMAND = (
    "node .claude/skills/dev-server/cli.mjs test run --suite unit"
    " > /tmp/unit-4f2.log 2>&1; echo \"finished rc=$?\""
)


def load_module():
    """Import the library by path, INSIDE a test — after $HOME is redirected.

    `state_dir()` resolves `~` per call precisely so this is safe, but importing
    at collection time would still be the wrong habit to establish here: the
    module beside it in this directory (`agent_ledger`) does bake `~` at import,
    and a reader copying this file should not learn the unsafe pattern.
    """
    loader = importlib.machinery.SourceFileLoader("_bgcap_under_test", MODULE)
    spec = importlib.util.spec_from_file_location("_bgcap_under_test", MODULE, loader=loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


@pytest.fixture()
def capture_dir(tmp_path, monkeypatch):
    """A throwaway state dir AND a throwaway $HOME.

    Both, not either: `$CLAUDE_BG_CAPTURE_DIR` is the documented override and is
    what the hook subprocess uses, but redirecting `$HOME` as well means a defect
    that ignores the override writes into the sandbox rather than into the
    developer's real log — a test that pollutes the live evidence file would be a
    poor guardian of an evidence file.
    """
    d = tmp_path / "state"
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("CLAUDE_BG_CAPTURE_DIR", str(d))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CLAUDE_BG_CAPTURE_DISABLE", raising=False)
    monkeypatch.delenv("CLAUDE_BG_CAPTURE_MAX_BYTES", raising=False)
    return d


def pre_payload(command=MASKING_COMMAND, background=True, **over):
    p = {
        "session_id": SESSION,
        "transcript_path": "/nonexistent/transcript.jsonl",
        "cwd": "/tmp",
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_use_id": TOOL_USE,
        "tool_input": {
            "command": command,
            "description": DESCRIPTION,
            "timeout": 1800000,
            "run_in_background": background,
        },
    }
    p.update(over)
    return p


def post_payload(**over):
    p = pre_payload(**over)
    p["hook_event_name"] = "PostToolUse"
    # The MEASURED shape of a backgrounded Bash tool_response on this host: no
    # exit code, empty output, a task id. Written out in full rather than
    # summarised, because the point of the PostToolUse record is that this is
    # everything the harness gives back.
    p["tool_response"] = {
        "stdout": "", "stderr": "", "interrupted": False, "isImage": False,
        "noOutputExpected": False, "backgroundTaskId": TASK_ID,
    }
    return p


def run_hook(payload, env=None, script=HOOK):
    """Drive the REAL hook as a subprocess and return (rc, stdout, stderr)."""
    proc = subprocess.run(
        [sys.executable, script],
        input=payload if isinstance(payload, str) else json.dumps(payload),
        capture_output=True, text=True, timeout=120,
        env={**os.environ, **(env or {})},
    )
    return proc.returncode, proc.stdout, proc.stderr


# --------------------------------------------------------------------------- #
# POSITIVE CONTROL — the artifact the ticket needs
# --------------------------------------------------------------------------- #

def test_the_hook_records_a_real_trailing_semicolon_after_a_redirect_verbatim(capture_dir):
    """🔴 THE POSITIVE CONTROL. Real hook, real payload shape, real disk.

    Asserts the recorded command is byte-identical to the input — not that it
    "contains" it. A `in` check would pass on a normalised, re-quoted or
    shell-expanded record, and a normalised record cannot answer the question
    this log exists to answer.
    """
    rc, out, err = run_hook(pre_payload())
    assert (rc, out, err) == (0, "", ""), (rc, out, err)

    log = capture_dir / "commands.jsonl"
    assert log.exists(), f"the hook wrote nothing to {log}"
    lines = [ln for ln in log.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 1, lines
    rec = json.loads(lines[0])

    assert rec["command"] == MASKING_COMMAND, "the command was not stored VERBATIM"
    assert rec["background"] is True
    assert rec["description"] == DESCRIPTION, "the join key to the completion notification"
    assert rec["session_id"] == SESSION
    assert rec["tool_use_id"] == TOOL_USE
    assert rec["event"] == "PreToolUse"
    assert rec["schema"] == "BG_COMMAND_CAPTURE_V1"
    # The grep target the ticket asks for.
    assert "REDIRECT_THEN_SEMI" in rec["markers"]


def test_the_post_event_records_the_background_task_id_and_no_invented_exit_code(capture_dir):
    """The task id names the output file the operator finds at 0 bytes.

    The second assertion is the honest half: `tool_response` on a backgrounded
    call carries NO terminal status, and the record must say so with a null
    rather than manufacturing a plausible zero — which is the very failure under
    investigation. `response_keys` is the asserted ledger beside it: it fails
    visibly if the harness's key set ever changes, in either direction.
    """
    rc, out, err = run_hook(post_payload())
    assert (rc, out, err) == (0, "", ""), (rc, out, err)
    rec = json.loads((capture_dir / "commands.jsonl").read_text().splitlines()[0])
    assert rec["background_task_id"] == TASK_ID
    assert rec["harness_exit_field"] is None
    assert rec["response_keys"] == sorted(
        ["stdout", "stderr", "interrupted", "isImage", "noOutputExpected", "backgroundTaskId"]
    )
    assert rec["stdout_len"] == 0 and rec["stderr_len"] == 0


def test_a_harness_that_starts_supplying_an_exit_field_is_recorded_not_ignored(capture_dir):
    """Forward guard, and the positive control for `harness_exit_field`.

    Without it the null asserted above is indistinguishable from a field that is
    hardcoded to null — a mutant returning a constant `None` survives the test
    above and would silently discard the very thing a future harness might
    finally give us.
    """
    payload = post_payload()
    payload["tool_response"]["exitCode"] = 1
    rc, _, _ = run_hook(payload)
    assert rc == 0
    rec = json.loads((capture_dir / "commands.jsonl").read_text().splitlines()[0])
    assert rec["harness_exit_field"] == {"key": "exitCode", "value": 1}


def test_the_module_selftest_passes_and_is_a_real_round_trip(capture_dir):
    """The shipped `--selftest` must PASS, and must actually have written."""
    proc = subprocess.run([sys.executable, HOOK, "--selftest"],
                          capture_output=True, text=True, timeout=120,
                          env={**os.environ})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "positive control: 1 expected, 1 observed -> PASS" in proc.stdout
    assert "verbatim round-trip: True" in proc.stdout


# --------------------------------------------------------------------------- #
# NEVER-THROWS
# --------------------------------------------------------------------------- #

HOSTILE = [
    ("empty", ""),
    ("whitespace", "   \n\t "),
    ("not-json", "this is not json at all }{"),
    ("json-null", "null"),
    ("json-array", "[1,2,3]"),
    ("json-string", '"hello"'),
    ("json-number", "42"),
    ("truncated", '{"hook_event_name":"PreToolUse","tool_in'),
    ("no-event", json.dumps({"tool_name": "Bash", "tool_input": {"command": "x"}})),
    ("unknown-event", json.dumps({"hook_event_name": "Frobnicate", "tool_name": "Bash",
                                  "tool_input": {"command": "x"}})),
    ("event-not-string", json.dumps({"hook_event_name": 7, "tool_name": "Bash",
                                     "tool_input": {"command": "x"}})),
    ("tool-input-null", json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Bash",
                                    "tool_input": None})),
    ("tool-input-list", json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Bash",
                                    "tool_input": [1, 2]})),
    ("command-int", json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Bash",
                                "tool_input": {"command": 123, "run_in_background": True}})),
    ("command-empty", json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Bash",
                                  "tool_input": {"command": "", "run_in_background": True}})),
    ("not-bash", json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Edit",
                             "tool_input": {"command": "a > f; echo x"}})),
    ("response-string", json.dumps({"hook_event_name": "PostToolUse", "tool_name": "Bash",
                                    "tool_input": {"command": "a > f; echo x",
                                                   "run_in_background": True},
                                    "tool_response": "Error: refused"})),
    ("deep-nesting", "[" * 2000 + "]" * 2000),
    ("unterminated-quote", json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Bash",
                                       "tool_input": {"command": "echo 'oops > f; echo hi",
                                                      "run_in_background": True}})),
    ("unterminated-heredoc", json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Bash",
                                         "tool_input": {"command": "python3 - <<PY\nx=1; y=2\n> f",
                                                        "run_in_background": True}})),
    ("nul-and-unicode", json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Bash",
                                    "tool_input": {"command": "echo \"日本語 \U0001f525\""
                                                              " > /tmp/u; echo ok",
                                                   "run_in_background": True}})),
]


@pytest.mark.parametrize("name, payload", HOSTILE, ids=[n for n, _ in HOSTILE])
def test_the_hook_never_throws_and_never_speaks(name, payload, capture_dir):
    """rc 0, empty stdout, empty stderr — all three, on every hostile input.

    All three, and stdout is not the incidental one. This is registered on
    PreToolUse, where stdout is parsed as a permission verdict: a traceback there
    is not merely noise, it is a malformed verdict on the operator's command.
    """
    rc, out, err = run_hook(payload)
    assert rc == 0, f"{name}: rc={rc}\nstdout={out}\nstderr={err}"
    assert out == "", f"{name}: wrote to stdout: {out!r}"
    assert err == "", f"{name}: wrote to stderr: {err!r}"


def test_the_never_throws_harness_can_go_red(tmp_path):
    """🔴 NEGATIVE CONTROL on the battery above.

    Every assertion in `test_the_hook_never_throws_and_never_speaks` is of the
    form "nothing happened". A harness that cannot observe something happening
    passes them all against a hook that does not exist. This plants a script that
    raises and exits non-zero and asserts the same three checks FAIL.
    """
    broken = tmp_path / "broken-hook.py"
    broken.write_text("import sys\nsys.stderr.write('boom\\n')\nsys.exit(3)\n")
    rc, out, err = run_hook("{}", script=str(broken))
    assert rc == 3 and "boom" in err, (rc, out, err)


def test_an_unwritable_state_directory_is_silent_not_fatal(tmp_path, monkeypatch):
    """The disk can be full, read-only, or owned by someone else.

    Degrading to "no record" is correct; that is the status quo this replaces, so
    a silent failure is strictly no worse than not having the instrument. A raise
    would be strictly worse than not having it.
    """
    ro = tmp_path / "ro"
    ro.mkdir()
    ro.chmod(0o500)
    try:
        rc, out, err = run_hook(pre_payload(), env={"CLAUDE_BG_CAPTURE_DIR": str(ro / "sub")})
        assert (rc, out, err) == (0, "", ""), (rc, out, err)
        assert not (ro / "sub").exists()
    finally:
        ro.chmod(0o700)


def test_the_hook_is_inert_without_its_library(tmp_path, capture_dir):
    """Copied away from its sibling module, the hook must do nothing, quietly.

    A partial `home-manager switch` produces exactly this state. bash-guard fails
    CLOSED here because it is a guard with a verdict to give; this one has no
    verdict, so failing OPEN and silent is the correct direction — and it must be
    a DECISION visible in a test, not an accident of an exception handler.
    """
    orphan = tmp_path / "orphan-hook.py"
    orphan.write_text(open(HOOK, encoding="utf-8").read())
    rc, out, err = run_hook(pre_payload(), script=str(orphan))
    assert (rc, out, err) == (0, "", ""), (rc, out, err)
    assert not (capture_dir / "commands.jsonl").exists()


def test_the_disable_switch_turns_it_off(capture_dir):
    rc, out, err = run_hook(pre_payload(), env={"CLAUDE_BG_CAPTURE_DISABLE": "1"})
    assert (rc, out, err) == (0, "", "")
    assert not (capture_dir / "commands.jsonl").exists()


# --------------------------------------------------------------------------- #
# MARKER LEDGER — both directions
# --------------------------------------------------------------------------- #

MARKS = [
    # ---- REDIRECT_THEN_SEMI: the shape the ticket is about ----
    ("a > f 2>&1; echo done", ["TRAILING_SEMI", "REDIRECT_THEN_SEMI"]),
    ("run >> out.log; tail -5 out.log", ["TRAILING_SEMI", "REDIRECT_THEN_SEMI"]),
    ("(x > f 2>&1; echo $?) ; echo end", ["TRAILING_SEMI"]),
    ("run > f\necho done", ["TRAILING_SEMI", "REDIRECT_THEN_SEMI"]),
    ("run 2> err.txt; ls", ["TRAILING_SEMI", "REDIRECT_THEN_SEMI"]),
    ("run &> all.log; true", ["TRAILING_SEMI", "REDIRECT_THEN_SEMI"]),
    # ---- masking WITHOUT a redirect ----
    ("make test; echo done", ["TRAILING_SEMI"]),
    ("pytest -q | tail -3", ["TRAILING_PIPE"]),
    ("a > f 2>&1; b | tail -1",
     ["TRAILING_SEMI", "REDIRECT_THEN_SEMI", "TRAILING_PIPE"]),
    # ---- NON-MATCHES: the load-bearing half ----
    ("ls -la", []),
    ("kubectl get pods -o wide", []),
    # && short-circuits, so the status is the failing command's — NOT masking.
    ("run > f && echo done", []),
    ("run > f || echo failed", []),
    # a trailing `;` with nothing after it runs nothing and masks nothing.
    ("run > f;", []),
    ("run > f ;   ", []),
    # a trailing comment is not a command.
    ("run > f; # note", []),
    # `;` inside quotes is data.
    ("echo 'a > f; echo x'", []),
    ('echo "a > f; echo x"', []),
    # `;` inside a heredoc BODY is the false positive that would swamp this host.
    ("python3 - <<'PY'\nx = 1; y = 2\nprint(x)\nPY", []),
    ("python3 - <<PY > out.log\na; b\nPY", []),
    ("cat <<-EOF > f\n\ta; b\n\tEOF", []),
    # ...but a heredoc followed by a REAL trailing command still marks.
    ("python3 - <<'PY' > out.log\nx = 1; y = 2\nPY\necho done",
     ["TRAILING_SEMI", "REDIRECT_THEN_SEMI"]),
    # a here-STRING is not a heredoc; the rest of the command must stay visible.
    ("grep x <<< \"$data\" > f; echo done",
     ["TRAILING_SEMI", "REDIRECT_THEN_SEMI"]),
    # `;;` is a case terminator.
    ("case $x in a) run > f;; esac", []),
    # `||` is not a pipe.
    ("run | grep x || true", ["TRAILING_PIPE"]),
    # a `#` that is not a comment.
    ("echo ${#arr[@]} > f; echo done", ["TRAILING_SEMI", "REDIRECT_THEN_SEMI"]),
    # `$( ... ; ... )` is a subshell, not a top-level separator.
    ("x=$(a > f; b); echo $x", ["TRAILING_SEMI"]),
    ("echo $(a; b) > f", []),
]


@pytest.mark.parametrize("command, expected", MARKS, ids=[c[:44] for c, _ in MARKS])
def test_the_marker_scanner_agrees_with_its_ledger(command, expected):
    """The COMPLETE marker set, compared as a set, in both directions.

    Comparing the complete set rather than asserting membership means a scanner
    that marks everything fails here — 14 of these 28 rows expect NO marker, an
    even split that is deliberate. A `REDIRECT_THEN_SEMI in marks` style assertion
    is satisfied by exactly the mutant this population exists to kill.
    """
    mod = load_module()
    assert sorted(mod.markers(command)) == sorted(expected), mod.markers(command)


def test_the_marker_scanner_terminates_on_pathological_input():
    """No marker is worth a hang on the hot path of every Bash call."""
    mod = load_module()
    for cmd in ("'" * 5000, '"' * 5000, "<" * 5000, "(" * 5000, "`" * 5000,
                "<<" * 2000, ";" * 5000, "\\" * 5001, "a" * 200000 + "; b"):
        assert isinstance(mod.markers(cmd), list)


# --------------------------------------------------------------------------- #
# INSTRUMENT CONTROL — the notification parser
# --------------------------------------------------------------------------- #

def test_the_completion_parser_reads_BOTH_spellings_of_the_notification():
    """🔴 POSITIVE CONTROL, and the reason it exists is a measured failure.

    The notification lives inside a JSON string in the transcript, so ON DISK its
    quotes are backslash-escaped. The first pattern required a bare `"`, matched
    nothing against a real transcript, and `report()` returned an empty
    `announced_exit_codes` — indistinguishable from "the harness never announced
    one". Both spellings are pinned, with a distinct exit code each, so a mutant
    that handles only one is red and cannot be green for the other's reason.
    """
    mod = load_module()
    escaped = (r'{"content":"<summary>Background command \"' + DESCRIPTION
               + r'\" completed (exit code 1)</summary>"}')
    bare = ('<summary>Background command "' + DESCRIPTION
            + '" completed (exit code 7)</summary>')
    assert mod.parse_completions(escaped) == {DESCRIPTION: [1]}
    assert mod.parse_completions(bare) == {DESCRIPTION: [7]}


def test_the_completion_parser_has_a_negative_control():
    """It must be capable of finding NOTHING, or the test above proves little."""
    mod = load_module()
    assert mod.parse_completions("no notification here") == {}
    assert mod.parse_completions('Background command "x" completed (exit code)') == {}
    assert mod.parse_completions(None) == {}


def test_a_repeated_description_keeps_every_exit_code():
    """The notification is keyed on the description, which is not unique.

    Returning the LIST keeps that ambiguity visible. Collapsing to the last value
    would resolve, by write order, exactly the question an investigator is here
    to answer.
    """
    mod = load_module()
    text = ('Background command "dup" completed (exit code 0)\n'
            'Background command "dup" completed (exit code 1)\n')
    assert mod.parse_completions(text) == {"dup": [0, 1]}


def test_report_puts_the_verbatim_command_and_the_announced_exit_code_on_one_row(capture_dir):
    """END-TO-END: the artifact that settles 868ktvqf9.

    Writes through the real hook, then joins through the real `report()` against
    a transcript carrying the real (escaped) notification bytes, and asserts the
    disagreeing pair land together: a command marked `REDIRECT_THEN_SEMI` next to
    an announced exit code of 0.
    """
    mod = load_module()
    rc, _, _ = run_hook(post_payload())
    assert rc == 0
    transcript = (r'{"content":"<summary>Background command \"' + DESCRIPTION
                  + r'\" completed (exit code 0)</summary>"}')
    res = mod.report(path=str(capture_dir / "commands.jsonl"),
                     transcript_reader=lambda _p: transcript)
    assert res["unparseable"] == 0
    assert len(res["rows"]) == 1
    row = res["rows"][0]
    assert row["command"] == MASKING_COMMAND
    assert row["announced_exit_codes"] == [0]
    assert "REDIRECT_THEN_SEMI" in row["markers"]
    assert row["background_task_id"] == TASK_ID


def test_report_counts_unreadable_transcripts_instead_of_reporting_a_clean_zero(capture_dir):
    """A transcript that cannot be read must be COUNTED, never absorbed.

    Otherwise "no exit code was announced" and "I could not open the file that
    holds it" render identically — which is the shape of the bug under
    investigation, one layer down.
    """
    mod = load_module()
    assert run_hook(pre_payload())[0] == 0

    def boom(_p):
        raise OSError("nope")

    res = mod.report(path=str(capture_dir / "commands.jsonl"), transcript_reader=boom)
    assert res["transcripts_unreadable"] == 1
    assert res["rows"][0]["announced_exit_codes"] == []


def test_read_records_counts_corrupt_lines_instead_of_dropping_them(capture_dir):
    mod = load_module()
    assert run_hook(pre_payload())[0] == 0
    log = capture_dir / "commands.jsonl"
    with open(log, "a", encoding="utf-8") as fh:
        fh.write("{not json at all\n")
        fh.write(json.dumps({"schema": "SOMETHING_ELSE"}) + "\n")
    recs, bad = mod.read_records(str(log))
    assert len(recs) == 1
    assert bad == 2


# --------------------------------------------------------------------------- #
# BOUND, and FIDELITY under load
# --------------------------------------------------------------------------- #

def test_the_log_is_bounded_at_two_generations(capture_dir, monkeypatch):
    """Rotation, measured against a cap the fixture overshoots rather than
    lands on.

    The cap is 4000 and each record is ~700 bytes of a 512-byte command — no
    integer multiple of the record size equals the cap, so the rotation cannot
    fire exactly on a boundary and pass for the wrong reason.

    Asserts exactly two generations exist, never three, and that BOTH exist after
    the final write: rotating after the append instead of before would leave no
    live file in the window after a rotation, which reads to an investigator as
    "the instrument never ran". The ceiling is `2 * (cap + one record)` — records
    are never truncated, so a generation can overshoot by at most one of them.
    """
    mod = load_module()
    log = capture_dir / "commands.jsonl"
    os.makedirs(capture_dir, exist_ok=True)
    cap = 4000
    for i in range(60):
        rec = mod.build_record("PreToolUse", pre_payload(
            command="x" * 512 + " > /tmp/f%d 2>&1; echo %d" % (i, i)))
        assert rec is not None
        mod.append_record(rec, path=str(log), cap=cap)
    files = sorted(p for p in os.listdir(capture_dir) if p.endswith(".jsonl"))
    assert files == ["commands.1.jsonl", "commands.jsonl"], files
    total = sum(os.path.getsize(capture_dir / f) for f in files)
    assert total <= 2 * cap + 4096, total


def test_a_huge_command_is_stored_verbatim_and_never_truncated(capture_dir):
    """🔴 The bound is on the FILE, never on the record.

    A truncated command is precisely the evidence that would be missing, so a
    record larger than the whole cap must still round-trip byte-for-byte. Uses a
    1 MiB command against the 4000-byte cap below it — the record is 260x the
    cap, so any policy that bounded the record instead of the file is red here.
    """
    mod = load_module()
    log = capture_dir / "commands.jsonl"
    os.makedirs(capture_dir, exist_ok=True)
    huge = "b" * (1024 * 1024) + " > /tmp/huge 2>&1; echo done"
    rec = mod.build_record("PreToolUse", pre_payload(command=huge))
    out = mod.append_record(rec, path=str(log), cap=4000)
    assert out["written"] is True and out["error"] is None
    # It rotated immediately (it is far over the cap), so read both generations.
    recs, bad = mod.read_records(str(log))
    assert bad == 0
    assert len(recs) == 1
    assert recs[0]["command"] == huge
    assert len(recs[0]["command"]) == len(huge)


def test_multibyte_and_control_characters_survive_the_round_trip(capture_dir):
    mod = load_module()
    log = capture_dir / "commands.jsonl"
    os.makedirs(capture_dir, exist_ok=True)
    cmd = "echo '日本語 \U0001f525\ttab' > /tmp/u; printf 'a\\nb'"
    rec = mod.build_record("PreToolUse", pre_payload(command=cmd))
    mod.append_record(rec, path=str(log))
    recs, bad = mod.read_records(str(log))
    assert bad == 0 and recs[0]["command"] == cmd


# --------------------------------------------------------------------------- #
# THE WRITE PREDICATE — a ledger, both directions
# --------------------------------------------------------------------------- #

PREDICATE = [
    ("ls -la", False, False),
    ("kubectl get pods", False, False),
    ("run > f && echo ok", False, False),
    ("python3 - <<'PY'\na; b\nPY", False, False),
    # backgrounded, no marker: still recorded — the masking shape may be one
    # nobody has thought of, which is the open question.
    ("sleep 30", True, True),
    ("pnpm test", True, True),
    # foreground, marked: recorded — the self-inflicted `| tail` case was one.
    ("pytest | tail -3", False, True),
    ("run > f 2>&1; echo done", False, True),
]


@pytest.mark.parametrize("command, background, expected", PREDICATE,
                         ids=[f"{c[:24]}|bg={b}" for c, b, _ in PREDICATE])
def test_the_write_predicate_is_pinned_in_both_directions(command, background, expected):
    """Neither half subsumes the other, and a one-way test would not show it.

    A test that only pins hits is satisfied by "record everything", which would
    turn a 16 MiB bound into hours of history instead of months. A test that only
    pins misses is satisfied by "record nothing", which is the instrument being
    absent. Four rows each way.
    """
    mod = load_module()
    got = mod.should_record({"command": command, "run_in_background": background})
    assert got is expected, mod.markers(command)


# --------------------------------------------------------------------------- #
# ON-DISK ARTIFACT NAMES
# --------------------------------------------------------------------------- #

def test_the_on_disk_artifact_names_are_pinned_as_whole_paths(tmp_path, monkeypatch):
    """The COMPLETE set of paths the real writer creates under a throwaway $HOME.

    Same form, and for the same reason, as
    `scripts/claude-hooks/tests/test_on_disk_artifact_names.py`: writer and
    reader here share one constant, so a rename is self-consistent and invisible
    to every behavioural test above — while `home-manager switch` replaces this
    file underneath live sessions, orphaning whatever the previous deploy wrote.
    This log is EVIDENCE for an open ticket; orphaning it is how the next hit
    gets captured into a file nobody looks at.

    Compares the whole set, so it fails when the set GROWS as well as when it
    shrinks: a future artifact arrives as a red test naming the new path.
    """
    mod = load_module()
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CLAUDE_BG_CAPTURE_DIR", raising=False)
    monkeypatch.delenv("CLAUDE_BG_CAPTURE_DISABLE", raising=False)
    monkeypatch.delenv("CLAUDE_BG_CAPTURE_MAX_BYTES", raising=False)

    rec = mod.build_record("PreToolUse", pre_payload())
    # Twice, with a cap small enough to force the rotated generation to exist.
    mod.append_record(rec, cap=1)
    mod.append_record(rec, cap=1)

    found = sorted(
        os.path.relpath(os.path.join(dirpath, name), home)
        for dirpath, _dirs, names in os.walk(home) for name in names
    )
    assert found == [
        ".local/state/claude-bg-command-capture/commands.1.jsonl",
        ".local/state/claude-bg-command-capture/commands.jsonl",
    ], found


def test_the_state_dir_is_resolved_per_call_not_baked_at_import(tmp_path, monkeypatch):
    """A module constant resolved at import bakes the developer's real home into
    every test — and, worse, into any long-lived process that imports it before
    the environment is set."""
    mod = load_module()
    monkeypatch.setenv("HOME", str(tmp_path / "one"))
    first = mod.state_dir()
    monkeypatch.setenv("HOME", str(tmp_path / "two"))
    assert mod.state_dir() != first


# --------------------------------------------------------------------------- #
# THE DELIVERY SEAM — deployed and registered, or it is inert
# --------------------------------------------------------------------------- #

def test_home_manager_deploys_both_files_beside_each_other():
    """The hook imports its library as a SIBLING in `~/.claude/hooks/`.

    Deploying one without the other is a green switch and an inert hook — the
    #452 shape this repo has already paid for once. Pins both declarations.
    """
    nix = open(os.path.join(ROOT, "nix", "home.nix"), encoding="utf-8").read()
    assert '".claude/hooks/bg-command-capture.py"' in nix
    assert '".claude/hooks/bg_command_capture.py"' in nix
    assert "../scripts/claude-hooks/bg-command-capture.py" in nix
    assert "../scripts/lib/bg_command_capture.py" in nix


def test_the_registrar_registers_it_on_both_bash_events():
    """A script in `~/.claude/hooks/` registers NOTHING by itself.

    Both events, because they capture different halves: PreToolUse has the
    verbatim command and the background flag, PostToolUse has the task id.
    Registering one is half an instrument.
    """
    src = open(os.path.join(HOOKS, "register-nudge-hook.py"), encoding="utf-8").read()
    pre = src.split("PRE_BASH_CMDS = [", 1)[1].split("]", 1)[0]
    post = src.split("POST_BASH_CMDS = [", 1)[1].split("]", 1)[0]
    assert "bg-command-capture.py" in pre, "not registered on PreToolUse"
    assert "bg-command-capture.py" in post, "not registered on PostToolUse"


def test_the_gate_runs_this_file():
    """This suite must be named by run-tests.sh, or nothing runs it.

    The #276 shape: a suite that exists, passes locally, and no gate collects.
    """
    src = open(os.path.join(ROOT, "scripts", "run-tests.sh"), encoding="utf-8").read()
    target = "scripts/claude-hooks/tests/test_bg_command_capture.py"
    assert target in src.split("HERMETIC_TARGETS=(", 1)[1].split("\n)", 1)[0]
    assert f'"{target}|' in src, "no TARGET_FLOORS entry"
