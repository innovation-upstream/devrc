#!/usr/bin/env python3
"""End-to-end tests for the OpenCode plugin's tool-call capture.

WHY THIS FILE EXISTS SEPARATELY FROM test_plugin.py
---------------------------------------------------
`test_plugin.py` tests a COPY of `emitEvent` re-typed inside the test string,
and builds emit's argv itself with `subprocess.run([...])`. That structurally
cannot observe either of the two bugs this file pins, because neither the real
`emitEvent` nor the real `tool.execute.after` handler is ever executed:

  BUG 1 (name lost).  `tool.execute.after` read `input.tool.name`, but the
    OpenCode plugin contract (@opencode-ai/plugin dist/index.d.ts) passes
    `input.tool` as a STRING. `undefined || undefined || "unknown"` → every row
    landed with text='unknown'. Measured 2026-08-02: 2,699 / 2,699 rows
    (source='opencode', kind='tool-call', 30d, host=workbench) were 'unknown'.

  BUG 2 (payload mangled).  `emitEvent` shelled out with
    `execSync(\\`${emit} ${args.join(" ")}\\`)`. A shell then word-split every
    value on spaces and performed quote REMOVAL, so the JSON payload arrived as
    `{duration_ms:0,success:true,args_summary:{"name":"x"}}` — not valid JSON —
    and any value containing a space was torn into several bogus argv entries.

Every test here drives the REAL exported functions / the REAL hook handlers via
node, through a mock `emit` that records argv ONE ENTRY PER LINE (so argv
splitting is directly observable rather than re-joined and hidden).

HARNESS VALIDATION (RULES: "validate the harness against a known-bad state").
`test_harness_negative_control_*` run the same harness against a vendored
reproduction of the two pre-fix mechanisms and assert it goes RED. Until those
pass, a green result from the tests below would be a fact about the harness.

POSITIVE CONTROL (RULES: "where the reassuring answer is a zero").
`corrupted_arg_count()` returns 0 for the fixed code. `test_positive_control_*`
proves that counter can be non-zero by running it against the buggy emitter.

Environment: needs `node` on PATH — same hard dependency as test_plugin.py
(no skipif; the pytest gate pins the skip set, so a missing node must go RED).
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
import collector as C  # noqa: E402
import _mockbin as M  # noqa: E402

SCRIPT_DIR = Path(__file__).resolve().parent.parent
PLUGIN_JS = SCRIPT_DIR / "activity-plugin.js"
EMIT = SCRIPT_DIR.parent / "emit"

NAME_CAPTURE_FAILED = "__name_capture_failed__"

# `#!` and the quoted forms the shebang guard scans for, assembled from char
# codes so this module's own source never contains the pattern it searches for.
_HB = chr(35) + chr(33)
_NEEDLES = (chr(34) + _HB, chr(39) + _HB)


# --------------------------------------------------------------------------- #
# Harness
# --------------------------------------------------------------------------- #
def write_argv_recorder(path: Path, log: Path) -> None:
    """A mock `emit` that records each argv entry on its OWN line.

    One-arg-per-line is load-bearing: `echo "$@"` re-joins the arguments with
    spaces, which is exactly the corruption we are trying to detect, so a
    space-joined log cannot tell a mangled call from a clean one.

    The shebang is owned by `_mockbin.write_exec` — see that module for why a
    `#!/usr/bin/env` stub is green on the dev host and RED in the nix sandbox.
    """
    M.write_exec(
        path,
        f'for a in "$@"; do printf "%s\\n" "$a" >> "{log}"; done\n'
        f'printf -- "--END--\\n" >> "{log}"\n'
        "exit 0\n",
    )


def read_calls(log: Path) -> list[list[str]]:
    """Parse the recorder log into a list of argv lists."""
    if not log.exists():
        return []
    calls, cur = [], []
    for line in log.read_text(encoding="utf-8").split("\n")[:-1]:
        if line == "--END--":
            calls.append(cur)
            cur = []
        else:
            cur.append(line)
    return calls


def kv(argv: list[str]) -> dict[str, str]:
    """argv → {key: value}, base64-decoding any `b64:` key (as emit would)."""
    out: dict[str, str] = {}
    for a in argv:
        if "=" not in a:
            continue
        k, v = a.split("=", 1)
        if k.startswith("b64:"):
            out[k[4:]] = v
        else:
            out[k] = v
    return out


def corrupted_arg_count(argv: list[str]) -> int:
    """Count argv entries that are NOT a well-formed `key=value` pair.

    Shell word-splitting turns one `b64:payload={"a":"b c"}` argument into
    several fragments, only the first of which contains an `=`. So the number of
    `=`-less entries is a direct, unambiguous measure of the corruption.
    """
    return sum(1 for a in argv if "=" not in a)


def run_node(code: str, *, env: dict | None = None) -> subprocess.CompletedProcess:
    base = dict(os.environ)
    if env:
        base.update(env)
    return subprocess.run(
        ["node", "--input-type=module", "-e", code],
        capture_output=True, text=True, env=base, timeout=30,
    )


TOOL_CALL_DRIVER = """
import {{ ActivityPlugin }} from "{plugin}";
const hooks = await ActivityPlugin({{ project: {{ name: "my proj" }},
                                      directory: "/tmp/dir with space" }});
const input = {input};
if (hooks["tool.execute.before"]) {{
  await hooks["tool.execute.before"]({{ tool: input.tool, sessionID: input.sessionID,
                                        callID: input.callID }}, {{ args: input.args }});
}}
await hooks["tool.execute.after"](input, {{ title: "t", output: "o", metadata: {{}} }});
"""


def drive_tool_call(tmp_path: Path, hook_input: dict) -> list[str]:
    """Run the REAL plugin's tool.execute.{before,after} and return emit's argv."""
    log = tmp_path / "emit.log"
    mock = tmp_path / "emit"
    write_argv_recorder(mock, log)
    code = TOOL_CALL_DRIVER.format(plugin=PLUGIN_JS, input=json.dumps(hook_input))
    r = run_node(code, env={"EMIT_PATH": str(mock),
                            "XDG_STATE_HOME": str(tmp_path / "state")})
    assert r.returncode == 0, r.stderr
    calls = read_calls(log)
    # 🔴 An empty log is AMBIGUOUS and must not be read as "the plugin chose not
    # to emit". `emitEvent` swallows every error by design, so node still exits
    # 0 when the stub cannot be exec'd at all — which is precisely how a
    # `#!/usr/bin/env` shebang failed silently in the nix sandbox while the dev
    # host stayed green. Name the rival mechanism instead of guessing.
    assert calls, (
        "the mock emit produced NO output. Either the plugin did not emit, or "
        f"the stub could not be exec'd. Interpreter {M.SH} executable: "
        f"{M.interpreter_is_executable()}; stub first line: "
        f"{mock.read_text().splitlines()[0]!r}")
    assert len(calls) == 1, f"expected exactly 1 emit call, got {len(calls)}: {calls}"
    return calls[0]


# --------------------------------------------------------------------------- #
# TIER PRECONDITION — every test below depends on a runtime-written stub being
# EXECUTABLE. That is environment-dependent, and it differs between the dev host
# and the nix build sandbox, so it gets its own named check rather than being
# discovered as 14 confusing downstream failures.
# --------------------------------------------------------------------------- #
def test_the_mock_helper_can_actually_exec(tmp_path):
    """Runs in BOTH tiers and must never skip.

    MEASURED 2026-08-02: with `#!/usr/bin/env bash` this suite was 166/166 green
    under `nix-shell` on the laptop and 152 passed / 14 FAILED under
    `nix build .#checks.x86_64-linux.pytests`, because the sandbox has no
    /usr/bin/env. The dev host structurally could not observe it.
    """
    assert M.interpreter_is_executable(), f"{M.SH} is not executable"
    out = tmp_path / "out"
    stub = M.write_exec(tmp_path / "stub", f'printf ran >> "{out}"\nexit 0\n')
    rc = subprocess.run([str(stub)], capture_output=True, text=True, timeout=10)
    assert rc.returncode == 0, f"stub failed to exec: {rc.stderr}"
    assert out.read_text() == "ran"


def test_brace_expansion_probe_agrees_with_the_shell_node_actually_uses():
    """The probe decides which corruption shape the negative controls assert, so
    it must describe the SAME shell node's `execSync` runs — not a guess.

    MEASURED 2026-08-02: dev host (/bin/sh → bash-interactive) expands; the nix
    build sandbox does not. Both are green because both are asserted.
    """
    probe = M.shell_does_brace_expansion()
    r = run_node('import { execSync } from "child_process";'
                 'process.stdout.write(execSync(\'printf "%s\\\\n" {a,b}\').toString());')
    assert r.returncode == 0, r.stderr
    assert (r.stdout.split() == ["a", "b"]) is probe, (
        f"probe says {probe} but node's shell produced {r.stdout!r}")


def test_no_runtime_written_shebang_uses_usr_bin_env():
    """STRUCTURAL GUARD — the deterministic half of the fix.

    A quoted `#!` anywhere in this suite's sources means a call site is writing
    its own shebang instead of going through `_mockbin.write_exec`, which is how
    `/usr/bin/env` came back the first time. `_mockbin` itself is exempt: it
    owns the one shebang, and its docstring names the trap.
    """
    # ⚠ The needles are BUILT AT RUNTIME, character by character — including the
    # `#!` itself. Written as literals they appear in this function's own source
    # and the scan reports ITSELF, the same self-match that makes
    # `pgrep -f <pattern>` find its own shell. (Measured: the first version of
    # this guard failed on its own two source lines.)
    needles = _NEEDLES
    tests_dir = Path(__file__).resolve().parent
    offenders = []
    for py in sorted(tests_dir.glob("test_*.py")):
        for i, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
            if i == 1:
                continue          # the module's own shebang; pytest imports, never execs
            if any(n in line for n in needles):
                offenders.append(f"{py.name}:{i}: {line.strip()}")
    assert not offenders, (
        "a test writes its own shebang — use _mockbin.write_exec instead:\n  "
        + "\n  ".join(offenders))


def test_positive_control_the_shebang_guard_can_detect_an_offender(tmp_path):
    """POSITIVE CONTROL for the `not offenders` (== empty) assertion above.

    An empty offender list is indistinguishable from a scan wired to nothing.
    Feed the same matcher a file that MUST be flagged and watch the count move.
    """
    bad = tmp_path / "test_bad.py"
    # Assembled at runtime for the same self-match reason as the guard itself.
    offending = 'p.write_text(' + chr(34) + _HB + '/usr/bin/env bash' + chr(34) + ')'
    bad.write_text("x = 1\n" + offending + "\n", encoding="utf-8")
    hits = [i for i, line in enumerate(bad.read_text().splitlines(), 1)
            if i != 1 and any(n in line for n in _NEEDLES)]
    assert hits == [2], f"guard is wired to nothing — no hit on {offending!r}"


# --------------------------------------------------------------------------- #
# HARNESS NEGATIVE CONTROLS — the harness must go RED on the known-bad code.
# --------------------------------------------------------------------------- #
BUGGY_EMITTER = """
import {{ execSync }} from "child_process";
const EMIT = "{mock}";
// Verbatim reproduction of the PRE-FIX emitEvent + name extraction.
function emitEvent({{ kind, text, project, cwd, payload }}) {{
  const args = ["source=opencode", `kind=${{kind}}`];
  if (text != null) args.push(`b64:text=${{String(text)}}`);
  if (project != null) args.push(`b64:project=${{String(project)}}`);
  if (cwd != null) args.push(`b64:cwd=${{String(cwd)}}`);
  if (payload != null) {{
    const p = typeof payload === "string" ? payload : JSON.stringify(payload);
    args.push(`b64:payload=${{p}}`);
  }}
  execSync(`${{EMIT}} ${{args.join(" ")}}`, {{ stdio: "ignore" }});
}}
const input = {input};
const toolName = input?.tool?.name || input?.name || "unknown";
const argsStr = input?.args ? JSON.stringify(input.args).slice(0, 200) : "";
emitEvent({{ kind: "tool-call", text: toolName,
             project: "my proj", cwd: "/tmp/dir with space",
             payload: {{ duration_ms: 0, success: true, args_summary: argsStr }} }});
"""

KNOWN_BAD_INPUT = {"tool": "bash", "sessionID": "s1", "callID": "c1",
                   "args": {"name": "customize-opencode"}}


def _drive_buggy(tmp_path: Path) -> list[str]:
    log = tmp_path / "emit.log"
    mock = tmp_path / "emit"
    write_argv_recorder(mock, log)
    r = run_node(BUGGY_EMITTER.format(mock=mock, input=json.dumps(KNOWN_BAD_INPUT)))
    assert r.returncode == 0, r.stderr
    calls = read_calls(log)
    assert len(calls) == 1
    return calls[0]


def test_harness_negative_control_sees_the_lost_tool_name(tmp_path):
    """KNOWN-BAD: the pre-fix extraction must be observed as 'unknown'.

    If this asserts something else, the harness is not reading `text` and every
    green name assertion below is meaningless.
    """
    fields = kv(_drive_buggy(tmp_path))
    assert fields["text"] == "unknown", (
        "harness cannot observe the tool name — it did not reproduce the known bug")


def test_harness_negative_control_sees_the_mangled_payload(tmp_path):
    """KNOWN-BAD: the pre-fix shell join must produce UNPARSEABLE payload JSON."""
    fields = kv(_drive_buggy(tmp_path))
    raw = fields["payload"]
    try:
        json.loads(raw)
    except json.JSONDecodeError:
        pass
    else:
        raise AssertionError(
            f"harness cannot observe the payload corruption — {raw!r} parsed as JSON")
    # UNIVERSAL half — quote removal happens in every POSIX shell, both tiers.
    assert 'args_summary:{"name":"customize-opencode"}' in raw, raw
    # BASH-ONLY half — brace expansion split the object into three
    # `b64:payload=` args and emit kept the LAST, which is the exact value the
    # 2,699 live rows carry. The workbench runs bash; the nix sandbox's /bin/sh
    # does not brace-expand, so this is gated on a MEASURED probe, not assumed.
    if M.shell_does_brace_expansion():
        assert raw == 'args_summary:{"name":"customize-opencode"}', raw
    else:
        assert raw.startswith("{duration_ms:0,success:true,"), raw


def test_positive_control_corrupted_arg_count_can_be_nonzero(tmp_path):
    """POSITIVE CONTROL for the zero asserted in test_no_argv_corruption.

    A `0` from corrupted_arg_count() is only evidence if the counter can move.
    Drive it against the buggy shell path and require a NON-ZERO count.

    ⚠ The FIRST version of this control asserted on a spacey value inside
    `args_summary` and measured 0 — because JSON.stringify wraps string values
    in `"`, so the shell keeps those spaces in one word. The splitting hits the
    fields the plugin does NOT wrap in quotes: `project` and `cwd`. Had this
    control been omitted, `test_no_argv_corruption`'s 0 would have been read as
    proof while the counter was blind to the case it was built for.
    """
    log = tmp_path / "emit.log"
    mock = tmp_path / "emit"
    write_argv_recorder(mock, log)
    r = run_node(BUGGY_EMITTER.format(mock=mock, input=json.dumps(KNOWN_BAD_INPUT)))
    assert r.returncode == 0, r.stderr
    argv = read_calls(log)[0]
    n = corrupted_arg_count(argv)
    # `b64:project=my proj` → +1, `b64:cwd=/tmp/dir with space` → +2.
    assert n == 3, f"positive control expected 3 corrupted entries, got {n}: {argv}"

    # The exact live signature: bash BRACE-EXPANDS the unquoted JSON object
    # `{"duration_ms":0,"success":true,"args_summary":"…"}` into three separate
    # `b64:payload=` arguments. `emit` takes the LAST one, which is why every
    # stored payload read `args_summary:{"name":"…"}` — a fragment, not JSON.
    #
    # Brace expansion is bash-only, and `/bin/sh` is bash on the workbench (where
    # the live rows were produced) but not in the nix build sandbox. So PROBE it
    # rather than assuming, and assert the shape that shell actually produces.
    payloads = [a for a in argv if a.startswith("b64:payload=")]
    if M.shell_does_brace_expansion():
        assert len(payloads) == 3, payloads
        assert payloads[-1] == 'b64:payload=args_summary:{"name":"customize-opencode"}', (
            "this is the literal value the 2,699 live rows carried")
    else:
        assert len(payloads) == 1, payloads
        assert payloads[0].startswith("b64:payload={duration_ms:0,success:true,"), payloads
    # Either way the payload is NOT valid JSON — that is the tier-independent
    # claim, and it is what makes the rows unreadable.
    for p in payloads:
        with pytest.raises(json.JSONDecodeError):
            json.loads(p.split("=", 1)[1])


# --------------------------------------------------------------------------- #
# BUG 1 — the tool name
# --------------------------------------------------------------------------- #
def test_tool_name_captured_from_string_input(tmp_path):
    """RED at origin/main (text='unknown'), GREEN here.

    `input.tool` is a STRING per the OpenCode plugin contract.
    """
    fields = kv(drive_tool_call(tmp_path, KNOWN_BAD_INPUT))
    assert fields["text"] == "bash"
    assert json.loads(fields["payload"])["name_captured"] is True


def test_tool_name_captured_from_object_form(tmp_path):
    """Tolerate a future/legacy `{name}` object shape without losing the name."""
    fields = kv(drive_tool_call(
        tmp_path, {"tool": {"name": "webfetch"}, "sessionID": "s", "callID": "c"}))
    assert fields["text"] == "webfetch"
    assert json.loads(fields["payload"])["name_captured"] is True


def test_capture_failure_is_distinguishable_from_a_real_tool(tmp_path):
    """A genuine capture failure must NOT look like a tool called 'unknown'.

    This is the rule the original violated: `unknown` is a plausible tool name,
    so a 100%-broken capture was indistinguishable from real data.
    """
    fields = kv(drive_tool_call(
        tmp_path, {"sessionID": "s", "callID": "c", "args": {}}))
    assert fields["text"] == NAME_CAPTURE_FAILED
    p = json.loads(fields["payload"])
    assert p["name_captured"] is False
    assert p["name_capture_shape"] == "undefined"


def test_a_tool_literally_named_unknown_is_not_a_failure(tmp_path):
    """The discriminator works in the other direction too."""
    fields = kv(drive_tool_call(
        tmp_path, {"tool": "unknown", "sessionID": "s", "callID": "c"}))
    assert fields["text"] == "unknown"
    assert json.loads(fields["payload"])["name_captured"] is True


# --------------------------------------------------------------------------- #
# BUG 2 — argv / payload integrity
# --------------------------------------------------------------------------- #
def test_no_argv_corruption(tmp_path):
    """RED at origin/main. Zero paired with the positive control above."""
    argv = drive_tool_call(tmp_path, {
        "tool": "bash", "sessionID": "s", "callID": "c",
        "args": {"command": "echo hello there world", "description": "a b c"}})
    assert corrupted_arg_count(argv) == 0, argv
    # one argv entry per emit key — nothing was split
    assert len(argv) == len(kv(argv))


def test_payload_is_valid_json_with_spaces_and_quotes(tmp_path):
    """RED at origin/main (payload was unparseable)."""
    fields = kv(drive_tool_call(tmp_path, {
        "tool": "bash", "sessionID": "s1", "callID": "c1",
        "args": {"command": 'grep -n "foo bar" *.py', "cwd": "/tmp/dir with space"}}))
    p = json.loads(fields["payload"])          # must not raise
    assert json.loads(p["args_summary"]) == {
        "command": 'grep -n "foo bar" *.py', "cwd": "/tmp/dir with space"}
    assert p["args_truncated"] is False


def test_free_text_fields_with_spaces_survive(tmp_path):
    """`project`/`cwd` contained spaces in the driver — they must arrive intact."""
    fields = kv(drive_tool_call(tmp_path, KNOWN_BAD_INPUT))
    assert fields["project"] == "my proj"
    assert fields["cwd"] == "/tmp/dir with space"
    assert fields["session"] == "s1"           # from input.sessionID, not null


def test_args_truncation_keeps_payload_parseable(tmp_path):
    """Over-budget args must degrade to a STRUCTURED marker, not a torn JSON slice."""
    fields = kv(drive_tool_call(tmp_path, {
        "tool": "write", "sessionID": "s", "callID": "c",
        "args": {"content": "x" * 5000, "path": "/tmp/f"}}))
    p = json.loads(fields["payload"])
    assert p["args_truncated"] is True
    marker = json.loads(p["args_summary"])     # must not raise
    assert marker["_truncated"] is True
    assert sorted(marker["keys"]) == ["content", "path"]
    assert marker["bytes"] > 5000


def test_duration_is_measured_not_hardcoded_zero(tmp_path):
    """The old payload always said duration_ms=0 / success=true — both fabricated.

    `tool.execute.after`'s `output` is {title, output, metadata}: it carries
    neither a duration nor an error, so those were unmeasured claims.
    """
    fields = kv(drive_tool_call(tmp_path, KNOWN_BAD_INPUT))
    p = json.loads(fields["payload"])
    assert "duration_ms" in p and isinstance(p["duration_ms"], int)
    assert "success" not in p, "success:true was never observable from this hook"
    assert p["outcome"] == "completed"
    assert p["call_id"] == "c1"


def test_duration_absent_when_before_hook_never_fired(tmp_path):
    """No `.before` → no start time → duration must be ABSENT, never 0."""
    log = tmp_path / "emit.log"
    mock = tmp_path / "emit"
    write_argv_recorder(mock, log)
    code = f'''
import {{ ActivityPlugin }} from "{PLUGIN_JS}";
const hooks = await ActivityPlugin({{ project: {{ name: "p" }}, directory: "/tmp" }});
await hooks["tool.execute.after"](
  {{ tool: "bash", sessionID: "s", callID: "orphan" }},
  {{ title: "t", output: "o", metadata: {{}} }});
'''
    r = run_node(code, env={"EMIT_PATH": str(mock),
                            "XDG_STATE_HOME": str(tmp_path / "state")})
    assert r.returncode == 0, r.stderr
    p = json.loads(kv(read_calls(log)[0])["payload"])
    assert "duration_ms" not in p


# --------------------------------------------------------------------------- #
# Full round-trip through the REAL emit → collector.parse_line
# --------------------------------------------------------------------------- #
def test_roundtrip_through_real_emit_and_collector(tmp_path):
    """The whole chain: real plugin → real emit → real collector.parse_line.

    This is the path the 2,699 bad rows took. RED at origin/main: parse_line
    yields text='unknown' and an unparseable payload.
    """
    assert EMIT.exists(), "emit script missing — this test must not silently pass"
    spool = tmp_path / "spool"
    spool.mkdir()
    code = TOOL_CALL_DRIVER.format(plugin=PLUGIN_JS, input=json.dumps({
        "tool": "bash", "sessionID": "sess-9", "callID": "c9",
        "args": {"command": 'echo "hi there" && ls -la', "timeout": 5000}}))
    r = run_node(code, env={"EMIT_PATH": str(EMIT),
                            "ACTIVITY_SPOOL_DIR": str(spool),
                            "XDG_STATE_HOME": str(tmp_path / "state")})
    assert r.returncode == 0, r.stderr

    lines = [l for l in (spool / "current.log").read_text().splitlines() if l]
    assert len(lines) == 1, lines
    ev = C.parse_line(lines[0])
    assert ev is not None
    assert ev["source"] == "opencode"
    assert ev["kind"] == "tool-call"
    assert ev["text"] == "bash"               # was 'unknown' for all 2,699 rows
    assert ev["session"] == "sess-9"
    p = json.loads(ev["payload"])             # was unparseable
    assert json.loads(p["args_summary"])["command"] == 'echo "hi there" && ls -la'
    assert p["name_captured"] is True


def test_b64_values_are_real_base64(tmp_path):
    """Guard the emit contract itself: `b64:` keys must decode.

    INVARIANT GUARD (not a regression test) — this held before the fix too. It
    is here because execFileSync bypasses the shell, and a future refactor that
    pre-encodes on the JS side would double-encode silently.
    """
    spool = tmp_path / "spool"
    spool.mkdir()
    code = TOOL_CALL_DRIVER.format(plugin=PLUGIN_JS, input=json.dumps(
        {"tool": "bash", "sessionID": "s", "callID": "c", "args": {"a": 1}}))
    r = run_node(code, env={"EMIT_PATH": str(EMIT),
                            "ACTIVITY_SPOOL_DIR": str(spool),
                            "XDG_STATE_HOME": str(tmp_path / "state")})
    assert r.returncode == 0, r.stderr
    line = (spool / "current.log").read_text().splitlines()[0]
    for field in line.split("\t"):
        if field.startswith("b64:"):
            base64.b64decode(field.split("=", 1)[1], validate=True)
