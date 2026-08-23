"""Tests for the `browser agent` wrapper (browser-agent).

Fully HEADLESS — NO live model, NO Brave, NO bridge. A fake `opencode` (emits a
canned `--format json` JSONL stream, modelled on the verified real 1.18.4
envelope) and a fake `browser` CLI (records ops, hands out a fixed tabId on
`open`) stand in for the real binaries via env seams (BROWSER_AGENT_OPENCODE /
BROWSER_AGENT_BROWSER_BIN). The agent-md template, the committed TYPED custom tool
(opencode/tools/browser.js + browser_tool_impl.mjs), and the parser are the REAL
committed files.

The TYPED-tool enforcement itself (op allowlist / forced tab / domain deny /
request shape) is unit-tested in `browser_tool.test.mjs` (node) — the model's only
action surface is that tool, and it has NO shell. These wrapper tests cover the
lifecycle + wiring around it: arg parsing, own-tab open→close on EVERY exit path,
the agent def denying bash / allowing only the tool, the env the wrapper forces on
the tool, schema parse + one-retry, and the process-group kill on timeout.

Run: nix-shell -p python312Packages.pytest --run "pytest scripts/browser-bridge/tests/test_browser_agent.py"
"""
import json
import os
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

BB = Path(__file__).resolve().parent.parent            # scripts/browser-bridge
WRAPPER = BB / "browser-agent"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("python3") is None,
    reason="bash + python3 required to drive the wrapper")

TAB_ID = 4242
SCHEMA_OK = {"answer": "The top 3 stories are A, B, C.",
             "evidence": ["A — 500", "B — 400", "C — 300"],
             "steps_used": 2, "status": "ok"}


# --------------------------------------------------------------------------- #
# Fixture builders — write tiny executable stand-ins into a tmp bin dir.
# --------------------------------------------------------------------------- #
def _write_exec(path: Path, content: str):
    # The fake stdlib-only python stubs carry a `#!/usr/bin/env python3` shebang,
    # but the nix build sandbox has no /usr/bin/env — point them at the running
    # interpreter so they exec both in the sandbox AND on the dev host.
    content = content.replace(
        "#!/usr/bin/env python3\n", f"#!{sys.executable}\n", 1)
    path.write_text(content)
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return path


# A distinctive string that appears ONLY in the fake `browser tabs` listing — used
# to prove the probe's output never reaches the wrapper's own stdout. (In the real
# world that listing is the operator's entire tab list: every url + title.)
PROBE_MARKER = "PROBE-ONLY-TAB-TITLE-9f3ac1"


def _fake_browser(path: Path) -> Path:
    """A stand-in for the real `browser` CLI (used ONLY for open/close/tabs/
    print-session-id by the wrapper — the agent's own reads go through the custom
    tool, not this).

    - `--print-session-id`            → prints a stable fake id.
    - `[--instance K] open [url]`     → prints {result:{data:{tabId:FRB_TABID}}}
      (or exits 1 if FRB_OPEN_FAIL=1). The tab is opened at **about:blank**, as
      the real wrapper does.
    - `[--instance K] tabs`           → the wrapper's readiness PROBE: a
      chrome.tabs-style listing (id/url/title/active/windowId), always including a
      decoy tab titled PROBE_MARKER. Modes:
        * FRB_PROBE_FAIL_TIMES>0 — the FIRST that-many probes OMIT the owned tab
          from the listing (the tab-gone shape → wrapper must re-open); the
          counter is persisted in FRB_PROBE_COUNT so retries advance it.
        * FRB_PROBE_HARD_FAIL=1  — a bridge/transport error (stderr + exit 1).
        * FRB_PROBE_UNPARSEABLE=1 — exits 0 with output that carries no tab list.
    - `[--instance K] --tab N eval …` → mimics REAL Chrome on an about:blank tab:
      chrome.scripting cannot inject there (no host permission covers about:blank),
      so it FAILS. This is the regression pin — a wrapper that probes with `eval`
      can never start.
    - `[--instance K] --tab N <op> …` → prints a canned success envelope.
    Every invocation is appended (one JSON line) to $FRB_LOG for assertions.
    """
    return _write_exec(path, '''#!/usr/bin/env python3
import json, os, sys
MARKER = "PROBE-ONLY-TAB-TITLE-9f3ac1"
argv = sys.argv[1:]
if argv and argv[0] == "--print-session-id":
    print("claude:fake-session"); sys.exit(0)
inst = None; tab = None; op = None; rest = []
i = 0
while i < len(argv):
    a = argv[i]
    if a == "--instance": i += 1; inst = argv[i]
    elif a.startswith("--instance="): inst = a.split("=",1)[1]
    elif a == "--tab": i += 1; tab = argv[i]
    elif a.startswith("--tab="): tab = a.split("=",1)[1]
    elif op is None: op = a
    else: rest.append(a)
    i += 1
log = os.environ.get("FRB_LOG")
if log:
    with open(log, "a") as f:
        f.write(json.dumps({"op": op, "tab": tab, "instance": inst,
                            "rest": rest}) + "\\n")
tabid = int(os.environ.get("FRB_TABID", "4242"))
if op == "open":
    if os.environ.get("FRB_OPEN_FAIL") == "1":
        sys.stderr.write("fake open failed\\n"); sys.exit(1)
    print(json.dumps({"ok": True, "result": {"id": "x", "ok": True,
          "data": {"tabId": tabid, "url": "about:blank"}}}))
    sys.exit(0)
if op == "tabs":
    # The wrapper's NON-INJECTING readiness probe.
    if os.environ.get("FRB_PROBE_HARD_FAIL") == "1":
        sys.stderr.write("browser: no extension connected " + MARKER + "\\n")
        sys.exit(1)
    fail_times = int(os.environ.get("FRB_PROBE_FAIL_TIMES", "0"))
    cf = os.environ.get("FRB_PROBE_COUNT")
    cur = 0
    if cf and os.path.exists(cf):
        try: cur = int(open(cf).read().strip() or "0")
        except ValueError: cur = 0
    if cf:
        open(cf, "w").write(str(cur + 1))
    if os.environ.get("FRB_PROBE_UNPARSEABLE") == "1":
        print("<not json, no tab list> " + MARKER); sys.exit(0)
    # A decoy tab is ALWAYS present; the owned tab is omitted while "gone".
    tabs = [{"id": 11, "url": "https://decoy.test/", "title": MARKER,
             "active": True, "windowId": 1}]
    if cur >= fail_times:
        tabs.append({"id": tabid, "url": "about:blank", "title": "",
                     "active": False, "windowId": 1})
    print(json.dumps({"ok": True, "result": {"id": "x", "ok": True,
          "data": {"tabs": tabs}}}))
    sys.exit(0)
if op == "eval":
    # REAL Chrome behaviour on the agent's about:blank tab: chrome.scripting has
    # no host permission for about:blank, so injection is refused. Any wrapper
    # that uses `eval` as its readiness probe dies here, before the model runs.
    sys.stderr.write(
        "browser: op 'eval' failed in the browser: Cannot access contents of url "
        "\\"about:blank\\". Extension manifest must request permission to access "
        "this host.\\n")
    sys.exit(1)
print(json.dumps({"ok": True, "result": {"id": "x", "ok": True,
      "data": {"url": "https://x.test", "title": "X", "text": "page text"}}}))
sys.exit(0)
''')


def _fake_opencode(path: Path) -> Path:
    """A stand-in for `opencode`. Behaviour is driven by env FAKE_OC_MODE; every
    invocation appends a line (with whether `--continue` was present) to
    $FAKE_OC_LOG, and dumps the BROWSER_AGENT_* env it received to $FAKE_OC_ENV so
    tests can prove the wrapper forced the tab/instance/domain policy. It emits a
    REAL-shaped `--format json` JSONL stream on stdout."""
    return _write_exec(path, r'''#!/usr/bin/env python3
import json, os, sys, time
argv = sys.argv[1:]

# `opencode debug agent browser-agent` — the wrapper's fail-closed tool-set gate.
# Answer with a resolved `tools` map (model-free); NEVER touch FAKE_OC_LOG /
# FAKE_OC_ENV (those count/inspect only the real `run`). FAKE_OC_GATE_MODE drives
# the shape so tests can prove the gate fails closed.
if argv[:2] == ["debug", "agent"]:
    import stat as _stat
    gate = os.environ.get("FAKE_OC_GATE_MODE", "ok")
    if gate == "fail":               # `debug agent` itself errors (bad/unsupported)
        sys.stderr.write("fake debug agent failed\n"); sys.exit(1)
    if gate == "unparseable":        # output is not JSON → gate must fail closed
        print("<not json>"); sys.exit(0)
    if gate == "empty":              # exits 0 but writes NOTHING → must fail closed
        sys.exit(0)
    tools = {"bash": False, "read": False, "edit": False, "write": False,
             "webfetch": False, "glob": False, "grep": False, "task": False,
             "browser": True}
    if gate == "hosttool":           # a host tool leaked back on → must refuse
        tools["bash"] = True
    elif gate == "nobrowser":        # custom tool absent (unsupported ver) → refuse
        del tools["browser"]
    elif gate == "missing_hosttool": # bash omitted → can't confirm → refuse
        del tools["bash"]
    # Pad the dump so a truncated prefix is unmistakably a PREFIX, mirroring the
    # real dumps (10s-100s of KB) that exposed opencode's stdout flush race.
    full = json.dumps({"name": "browser-agent", "tools": tools,
                       "_pad": "x" * 200000})
    if gate == "truncated":          # a flush-race-style truncated prefix
        sys.stdout.write(full[:4096]); sys.exit(0)
    if gate == "pipe_truncates":
        # THE REGRESSION PIN for the stdout flush race. Emit the FULL, valid dump
        # only when stdout is a REGULAR FILE; through a PIPE (what `$(...)` gives
        # you) emit a truncated prefix — exactly how the real opencode behaves when
        # it exits without flushing a piped stdout. So this mode passes the gate
        # IFF the wrapper redirects the debug dump to a file.
        is_file = _stat.S_ISREG(os.fstat(1).st_mode)
        sys.stdout.write(full if is_file else full[:4096])
        sys.exit(0)
    sys.stdout.write(full)
    sys.exit(0)

cont = "--continue" in argv
# The task message is the last positional (after all flags/values we know).
msg = argv[-1] if argv else ""
log = os.environ.get("FAKE_OC_LOG")
if log:
    with open(log, "a") as f:
        f.write(json.dumps({"continue": cont, "argv": argv}) + "\n")
envlog = os.environ.get("FAKE_OC_ENV")
if envlog:
    # OPENCODE_CONFIG_DIR is kept alongside the BROWSER_* seams because it is the
    # ONLY observable that says whether the wrapper ran under its ISOLATED config
    # dir or silently fell back to the operator's global one (~7.7k extra input
    # tokens per request). Without it the fallback was indistinguishable from the
    # real thing, and every test in this file ran in the fallback for months.
    keep = {k: v for k, v in os.environ.items()
            if k.startswith("BROWSER_AGENT_") or k.startswith("BROWSER_BRIDGE_")
            or k == "OPENCODE_CONFIG_DIR"}
    with open(envlog, "a") as f:
        f.write(json.dumps(keep) + "\n")

def emit_text(s):
    print(json.dumps({"type": "step_start", "part": {"type": "step-start"}}))
    print(json.dumps({"type": "text",
                      "part": {"type": "text", "text": s}}))
    print(json.dumps({"type": "step_finish",
                      "part": {"type": "step-finish", "reason": "stop",
                               "tokens": {"total": 10}, "cost": 0.001}}))

def schema(status="ok", steps=2, answer="The top 3 stories are A, B, C.",
           evidence=None):
    return json.dumps({"answer": answer,
                       "evidence": evidence or ["A — 500", "B — 400", "C — 300"],
                       "steps_used": steps, "status": status})

mode = os.environ.get("FAKE_OC_MODE", "ok")
if mode == "slow":
    time.sleep(float(os.environ.get("FAKE_OC_SLEEP", "30")))
    emit_text(schema()); sys.exit(0)
if mode == "straggler":
    # Fork a child that INHERITS this process's group (mimics an opencode helper
    # child) and would OUTLIVE a naive kill of only the direct pid. It writes its
    # pid immediately, sleeps long, then writes a "survived" marker. A correct
    # process-GROUP kill on timeout reaps it before it can write "survived".
    pid = os.fork()
    if pid == 0:
        with open(os.environ["STRAGGLER_PID"], "w") as f:
            f.write(str(os.getpid()))
        time.sleep(float(os.environ.get("STRAGGLER_SLEEP", "30")))
        with open(os.environ["STRAGGLER_SURVIVED"], "w") as f:
            f.write("survived")
        os._exit(0)
    time.sleep(float(os.environ.get("FAKE_OC_SLEEP", "30")))
    emit_text(schema()); sys.exit(0)
if mode == "nonzero":
    sys.stderr.write("fake opencode error\n"); sys.exit(3)
if mode == "malformed":
    emit_text("I tried but here is no JSON at all."); sys.exit(0)
if mode == "malformed_then_ok":
    if cont: emit_text(schema())
    else: emit_text("no json yet")
    sys.exit(0)
if mode == "partial":
    emit_text(schema(status="partial")); sys.exit(0)
# default: ok
emit_text(schema()); sys.exit(0)
''')


# --------------------------------------------------------------------------- #
# Wall-clock budget. READ THIS BEFORE CHANGING A NUMBER HERE.
#
# Every test in this file drives the wrapper as a SUBPROCESS, so each one needs
# some deadline or a hang would wedge the suite forever. That deadline is a
# HANG-NET, never a performance assertion: the happy path is ~0.25 s (measured
# 2026-08-13, n=30, median 0.24 s), essentially all of it process-spawn latency
# across ~10 execs, with no single dominant step.
#
# 🔴 It used to be a bare `timeout=60`, which was SHORTER than three budgets the
# wrapper is allowed to spend inside it: the warm step (BROWSER_AGENT_WARM_TIMEOUT,
# default 90 s), the warm lock's bounded wait (~120 s), and the opencode watchdog
# (`--timeout`, default 120 s). A deadline below the budget of the code it wraps
# cannot report anything useful — whichever of those paths fires, pytest prints
# `subprocess.TimeoutExpired`, and the only other thing in the captured output is
# the wrapper's "could not warm … re-run ONCE with network available" warning,
# which pytest echoes *because the test failed*, not because it caused anything.
# That is exactly how one flake on 2026-08-13 sent the diagnosis at network
# availability instead of at the wall clock.
#
# So the budget is now coherent from BOTH ends:
#   * the rig PINS the wrapper's internal budgets below its own (WARM_TIMEOUT
#     below, `--timeout` per-test), so no legitimate wrapper path can outlive it;
#   * and — the part that actually removes the wall-clock dependency — the rig
#     waits on the DETERMINISTIC condition (the process exited) and consults the
#     clock only to decide whether "still running" is evidence of a HANG. On each
#     expiry it re-measures how long THIS machine currently takes to spawn ten
#     trivial processes: normal ⇒ the wrapper really is stuck, fail; inflated ⇒
#     the MACHINE is stalled, keep waiting. Nothing is ever re-executed, so this
#     is not a retry; the run in flight simply is not judged by a clock that has
#     stopped meaning anything.
#
# 🔴 That distinction is not theoretical. MEASURED 2026-08-13 while deliberately
# reproducing the original conditions (four concurrent devrc-pytests nix builds
# + CPU hogs, loadavg 65): ONE wrapper invocation whose idle cost is 0.25 s took
# 125.4 s — a ~400x stall, with the wrapper itself doing nothing unusual. That is
# the same order as the 60 s that started this, and it is why a fixed number here
# — 60, 180, anything — is the wrong instrument. A stalled box can outrun any
# constant you pick; only a measurement taken DURING the stall can tell the two
# apart.
#
# HARD_CAP is the real hang-net: a genuinely wedged wrapper on a genuinely
# stalled box still fails, just slowly.
RUN_TIMEOUT = float(os.environ.get("BROWSER_AGENT_TEST_TIMEOUT", "60"))
RUN_HARD_CAP = float(os.environ.get("BROWSER_AGENT_TEST_HARD_CAP", "900"))
_SPAWN_PROBE_N = 10
# 10 trivial spawns cost 0.085-0.105 s on this idle 24-core box (n=5, 2026-08-13).
# 8x that is comfortably outside the idle spread and far below what a real stall
# produces, so it does not read a busy-but-healthy box as stalled.
#
# 🔴 That was ONE host in ONE tier, and this constant decides every hung-vs-
# stalled verdict in the file — so the scope it carries matters. Set it too low
# and a healthy-but-busy box extends forever up to RUN_HARD_CAP instead of
# reporting a real hang. Re-measured 2026-08-15 at the two points the first
# measurement could not see (same probe, n=3-5 each):
#   * inside a NIX BUILD SANDBOX — the tier the authoritative gate runs in, and
#     the one the 2026-08-13 flake actually happened in: 0.099-0.113 s, taken
#     while a devrc-pytests build was in flight (loadavg 15.5).
#   * the LAPTOP host (8-core i7-1165G7, idle): 0.113-0.117 s.
# So 0.10 holds across both hosts and both tiers, with ~7x headroom to the
# 0.80 s threshold. For reference at the other end: 6 concurrent full runs of
# this file plus 12 CPU hogs on the 24-core box only reached 0.14-0.22 s — a
# busy box is nowhere near the threshold, which is the point.
_SPAWN_IDLE_REF = 0.10
_SPAWN_STALL_FACTOR = 8.0


def _stall_extends(waited: float, cap):
    """ONE rule for every wall-clock decision in this file: may we keep waiting?

    Returns (extend?, measured_baseline). Used by BOTH the subprocess wait and the
    condition polls below — the discipline is the point, and a second copy of it
    would be the second copy that drifts.
    """
    base = _spawn_baseline()
    cap = RUN_HARD_CAP if cap is None else cap
    return (base > _SPAWN_IDLE_REF * _SPAWN_STALL_FACTOR and waited < cap), base


def _proc_state(pid):
    """The single-letter process state from /proc, or "" if unreadable."""
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split(None, 1)[0]
    except (OSError, IndexError, ValueError):
        return ""


def _process_gone(pid):
    """Has `pid` stopped running? A ZOMBIE counts as gone.

    🔴 `os.kill(pid, 0)` IS NOT THIS CHECK, and the difference is invisible on a
    dev host. A killed process that its parent has not `wait()`ed for stays in
    the table as a zombie, and `kill(pid, 0)` SUCCEEDS on a zombie — so the naive
    check reports a corpse as a live process, forever.

    On the workbench nothing notices: the straggler below is orphaned when its
    parent dies, PID 1 is systemd, and systemd reaps an orphan within
    milliseconds. Inside a CI step container PID 1 is the step's own entrypoint,
    which does not reap, so the zombie persists for the life of the pod.

    MEASURED 2026-08-22: this made `test_timeout_reaps_the_whole_process_group`
    the sole failure in every `devrc-ci` PipelineRun — 5 of 5, across unrelated
    revisions, on a test whose file none of those changes touched. The gate had
    never once been green, and a gate that has never passed teaches everyone to
    click through it.

    The state is read from `/proc/<pid>/stat`, whose second field is a
    parenthesised comm that may itself contain spaces and `)`, so the split is on
    the LAST `") "` — not `.split()[2]`, which a process named `foo bar` breaks.

    The remaining rival for "killed but kill(0) still succeeds" is PID REUSE. It
    is not silently folded in: a reused pid is some other live process, reads as
    R/S here, and is reported STILL RUNNING — the conservative direction, and a
    different failure with a different diagnosis.
    """
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return True
    try:
        stat_line = Path(f"/proc/{pid}/stat").read_text()
    except (OSError, ValueError):
        # No procfs (not Linux, or the entry vanished between the two reads —
        # which is itself "gone", but say so only for the vanish case).
        return False
    try:
        state = stat_line.rsplit(") ", 1)[1].split(None, 1)[0]
    except IndexError:
        return False
    return state == "Z"


def _await(predicate, what, slice_s=8.0, stall_cap=None, poll=0.05):
    """Wait for a DETERMINISTIC condition, consulting the clock only to judge
    hung-vs-stalled. Same discipline as the subprocess wait — see RUN_TIMEOUT.

    🔴 `slice_s` must EXCEED the longest time the condition can legitimately take,
    or the first slice expires while the answer is still coming and the caller
    gets "it never happened" instead of its own assertion. The inner poll returns
    the instant the predicate holds, so a generous slice costs nothing on green.

    Worst case is `RUN_HARD_CAP` on top of whatever `rig.run` already spent — up
    to ~30 min on a persistently stalled box, against ~38 s for the fixed
    deadlines this replaced. That is a deliberate trade: slow-and-correct beats
    fast-and-wrong about which component failed.
    """
    waited, stalls = 0.0, []
    while True:
        end = time.monotonic() + slice_s
        while time.monotonic() < end:
            if predicate():
                return
            time.sleep(poll)
        waited += slice_s
        extend, base = _stall_extends(waited, stall_cap)
        if extend:
            stalls.append(f"{waited:.0f}s:{base:.2f}s")
            continue
        thresh = _SPAWN_IDLE_REF * _SPAWN_STALL_FACTOR
        # Say WHICH term ended the wait. Reporting "the machine is not the
        # explanation" after the HARD CAP fired on a demonstrably stalled machine
        # is the same misattribution this whole file exists to stop.
        verdict = ("so the machine is not the explanation"
                   if base <= thresh else
                   "the machine IS stalled, but the hard cap is spent")
        pytest.fail(
            f"waited {waited:.0f}s for {what} and it never happened.\n"
            f"  Spawning {_SPAWN_PROBE_N} trivial processes on this machine just "
            f"now took {base:.2f}s (idle reference {_SPAWN_IDLE_REF:.2f}s; stall "
            f"threshold {thresh:.2f}s), {verdict}.\n"
            f"  Stall extensions granted: {stalls or 'none'}.")


def _spawn_baseline(n: int = _SPAWN_PROBE_N) -> float:
    """Wall time to spawn `n` trivial processes RIGHT NOW — the same currency the
    wrapper's happy path is denominated in (its 0.25 s is ~10 execs and little
    else). 0.085-0.105 s on this idle 24-core box."""
    t0 = time.monotonic()
    for _ in range(n):
        subprocess.run([sys.executable, "-c", ""], capture_output=True)
    return time.monotonic() - t0


def _prewarm_oc_config(oc_config_dir: Path, opencode_bin: Path) -> str:
    """Pre-populate the isolated opencode config dir so the wrapper's warm/
    bootstrap step is a NO-OP at test time — the fixture equivalent of shipping
    the warmed tree as a build input.

    The wrapper considers the dir warm when `node_modules/@opencode-ai/plugin`
    exists AND `.devrc-warm-stamp` matches the identity it derives for the
    opencode binary in play (`<realpath>|<size>:<mtime>` — see `oc_warm_version`
    in the wrapper). Both are cheap, purely local facts, so we can state them
    directly instead of making 51 tests each drive a bootstrap that, with a FAKE
    opencode, is guaranteed to fail and fall back anyway.

    What this buys, per test: two fewer process spawns (`timeout` + a second fake
    opencode invocation), no `mkdir` lock dance, no `rm -rf`, no `mktemp -d`, no
    two-file copy — and, crucially, it removes the only code paths in the wrapper
    whose own budgets (90 s warm, ~120 s lock wait) exceed this suite's deadline.
    It also means the tests now run under the ISOLATED config dir that production
    uses, instead of the degraded global-config fallback they used to.
    """
    (oc_config_dir / "node_modules" / "@opencode-ai" / "plugin").mkdir(parents=True,
                                                                      exist_ok=True)
    st = opencode_bin.stat()
    stamp = f"{os.path.realpath(opencode_bin)}|{st.st_size}:{int(st.st_mtime)}\n"
    (oc_config_dir / ".devrc-warm-stamp").write_text(stamp)
    return stamp


@pytest.fixture
def rig(tmp_path):
    """Assemble the fake binaries + a scratch TMPDIR and return a runner."""
    bind = tmp_path / "bin"; bind.mkdir()
    fbrowser = _fake_browser(bind / "browser-fake")
    focode = _fake_opencode(bind / "opencode")
    frb_log = tmp_path / "frb.log"
    oc_log = tmp_path / "oc.log"
    oc_env = tmp_path / "oc_env.log"
    probe_count = tmp_path / "probe.count"
    scratch_root = tmp_path / "scratch"; scratch_root.mkdir()
    # 🔴 Isolate the isolated-config-dir. Without this the wrapper falls back to
    # the REAL ~/.cache/browser-agent-opencode-config, and its warm/bootstrap step
    # would see a stamp written by the real opencode, judge the dir stale (the
    # fake binary has a different identity), and `rm -rf` the operator's warmed
    # 62 MB node_modules as a side effect of running the test suite.
    oc_config_dir = tmp_path / "oc-config"
    # Warm it up front — see `_prewarm_oc_config`. A test that wants the COLD /
    # degraded path deletes `rig.oc_config_dir` first.
    _prewarm_oc_config(oc_config_dir, focode)

    def run(args, mode="ok", extra_env=None, timeout=RUN_TIMEOUT, open_fail=False,
            tabid=TAB_ID, probe_fail=0, stall_cap=None):
        env = dict(os.environ)
        env.update(
            BROWSER_AGENT_OPENCODE=str(focode),
            BROWSER_AGENT_BROWSER_BIN=str(fbrowser),
            FAKE_OC_MODE=mode, FAKE_OC_LOG=str(oc_log), FAKE_OC_ENV=str(oc_env),
            FRB_LOG=str(frb_log), FRB_TABID=str(tabid),
            FRB_PROBE_FAIL_TIMES=str(probe_fail), FRB_PROBE_COUNT=str(probe_count),
            TMPDIR=str(scratch_root),
            BROWSER_AGENT_OPENCODE_CONFIG_DIR=str(oc_config_dir),
            # Pin the wrapper's own warm budget BELOW this rig's deadline. With
            # the pre-warm above nothing should reach the warm step at all; if a
            # test deletes the config dir on purpose, this keeps the degrade
            # inside a budget the rig can actually observe and report on.
            BROWSER_AGENT_WARM_TIMEOUT="10",
            BROWSER_AGENT_KEEP_SCRATCH="1",
            # Keep the open->readiness-retry backoff near-zero so the retry tests
            # don't sleep (the real default is ~0.4s to let the SW settle).
            BROWSER_AGENT_READY_BACKOFF="0.01",
            PATH=f"{bind}:{env.get('PATH','')}",
        )
        if open_fail:
            env["FRB_OPEN_FAIL"] = "1"
        if extra_env:
            env.update(extra_env)
        # Wait on the DETERMINISTIC condition — the process exited — and use the
        # clock only to decide whether "still running" means "hung". `communicate`
        # (unlike `subprocess.run`) does NOT kill the child on expiry, so we can
        # look at the machine and go back to waiting without re-executing
        # anything. See the RUN_TIMEOUT block above for why a constant cannot do
        # this job.
        proc = subprocess.Popen([str(WRAPPER), *args], env=env, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        waited, stalls = 0.0, []
        while True:
            try:
                out, err = proc.communicate(timeout=timeout)
                break
            except subprocess.TimeoutExpired:
                waited += timeout
                extend, base = _stall_extends(waited, stall_cap)
                if extend:
                    stalls.append(f"{waited:.0f}s:{base:.2f}s")
                    continue                     # the MACHINE is stalled — wait on
                proc.kill()                      # the wrapper, not on the clock
                out, err = proc.communicate()
                thresh = _SPAWN_IDLE_REF * _SPAWN_STALL_FACTOR
                verdict = ("so the MACHINE is not the explanation and the "
                           "wrapper genuinely hung"
                           if base <= thresh else
                           "so the MACHINE is stalled — but the stall budget "
                           "for this run is exhausted")
                pytest.fail(
                    f"the wrapper did not exit within {waited:.0f}s.\n"
                    f"  This deadline is a HANG-NET, not a performance assertion: "
                    f"the happy path is ~0.25s of process-spawn latency.\n"
                    f"  Spawning {_SPAWN_PROBE_N} trivial processes on this "
                    f"machine just now took {base:.2f}s (idle reference "
                    f"{_SPAWN_IDLE_REF:.2f}s; stall threshold {thresh:.2f}s), "
                    f"{verdict} — its state is under {scratch_root}.\n"
                    f"  Stall extensions granted: {stalls or 'none'}.\n"
                    f"  Wrapper stderr tail (a captured artifact of the failure, "
                    f"not evidence of its cause):\n{(err or '')[-1500:]}")
        return subprocess.CompletedProcess([str(WRAPPER), *args],
                                           proc.returncode, out, err)

    rig.frb_log = frb_log
    rig.oc_log = oc_log
    rig.oc_env = oc_env
    rig.scratch_root = scratch_root
    rig.oc_config_dir = oc_config_dir
    rig.opencode_bin = focode
    rig.run = run
    return rig


def _browser_calls(frb_log: Path):
    if not frb_log.exists():
        return []
    return [json.loads(ln) for ln in frb_log.read_text().splitlines() if ln.strip()]


def _oc_calls(oc_log: Path):
    if not oc_log.exists():
        return []
    return [json.loads(ln) for ln in oc_log.read_text().splitlines() if ln.strip()]


def _oc_env(oc_env: Path):
    if not oc_env.exists():
        return []
    return [json.loads(ln) for ln in oc_env.read_text().splitlines() if ln.strip()]


def _scratch_dir(scratch_root: Path):
    hits = list(scratch_root.glob("browser-agent.*"))
    assert hits, "no per-run scratch dir was created"
    return hits[0]


def _agent_md(scratch_root: Path):
    return (_scratch_dir(scratch_root) / ".opencode/agents/browser-agent.md").read_text()


# --------------------------------------------------------------------------- #
# Arg parsing (fails BEFORE opening any tab — no fake needed to reach these)
# --------------------------------------------------------------------------- #
def test_goal_required(rig):
    r = rig.run([])
    assert r.returncode == 2
    assert "goal is required" in r.stderr.lower()
    assert _browser_calls(rig.frb_log) == []          # no tab opened


def test_a_second_goal_after_the_separator_is_rejected_not_dropped(rig):
    """`--` used to take only the FIRST remaining arg and silently DROP the rest,
    so `browser agent -- g1 g2` ran with goal "g1" and never said so — the same
    drop-after-`--` shape fixed in the `browser` CLI's nine loops."""
    r = rig.run(["--", "goal one", "goal two"])
    assert r.returncode == 2
    assert "only one goal" in r.stderr.lower()
    assert _browser_calls(rig.frb_log) == []          # no tab opened


def test_an_empty_first_goal_is_not_silently_overwritten(rig):
    """GOAL_SEEN, not `[ -z "$GOAL" ]`: an empty first goal must still occupy the
    slot rather than being overwritten by the next arg."""
    r = rig.run(["--", "", "sneaky second"])
    assert r.returncode == 2
    assert "only one goal" in r.stderr.lower()
    assert _browser_calls(rig.frb_log) == []


def test_a_goal_beginning_with_a_dash_is_reachable_via_the_separator(rig):
    """The point of keeping `--`: a goal that looks like a flag still parses. It
    gets past arg-parsing (no "unknown flag"), which is all this asserts."""
    r = rig.run(["--", "--not-a-flag"])
    assert "unknown flag" not in r.stderr.lower()
    assert "goal is required" not in r.stderr.lower()


def test_bad_steps_rejected(rig):
    r = rig.run(["do a thing", "--steps", "abc"])
    assert r.returncode == 2
    assert "steps" in r.stderr.lower()


def test_bad_timeout_rejected(rig):
    r = rig.run(["do a thing", "--timeout", "-5"])
    assert r.returncode == 2
    assert "timeout" in r.stderr.lower()


def test_unknown_flag_rejected(rig):
    r = rig.run(["do a thing", "--nope"])
    assert r.returncode == 2
    assert "unknown flag" in r.stderr.lower()


# --------------------------------------------------------------------------- #
# The security contract: the agent def DENIES bash + only the custom tool is
# allowed, and NO shell-string surface remains anywhere in the per-run def/tool.
# --------------------------------------------------------------------------- #
def test_agent_def_denies_bash_allows_only_custom_tool(rig):
    """The per-run agent def must deny EVERY built-in tool (bash included) and
    allow ONLY the typed `browser` custom tool — the crux of the PR #180 RCE fix
    (no bash → no shell → no redirect/metacharacter surface)."""
    r = rig.run(["read the page", "--steps", "7"], mode="ok")
    assert r.returncode == 0, r.stderr
    md = _agent_md(rig.scratch_root)
    # The permission block denies everything then re-allows only `browser`.
    assert '"*": deny' in md, "the agent def must deny all tools by default"
    assert "browser: allow" in md, "only the custom browser tool may be allowed"
    # bash must NOT be granted anywhere (no `bash:` allow, no `browser --tab … *`).
    assert "browser --tab" not in md, "no bash/shell command permission may remain"
    assert "bash:" not in md, "the old bash permission block must be gone"
    # The step budget is templated; the tab is NOT in the def (forced via env).
    assert "steps: 7" in md
    assert str(TAB_ID) not in md, "the tab must not be baked into the def (env-forced)"
    assert "__STEPS__" not in md and "__MODEL__" not in md    # fully templated


def test_custom_tool_copied_into_scratch_project(rig):
    """opencode loads a project's `.opencode/tools/*.js`; the wrapper must copy the
    committed typed tool (+ its pure-logic sibling) in, so the model's ONLY tool is
    `browser`."""
    r = rig.run(["read the page"], mode="ok")
    assert r.returncode == 0, r.stderr
    sd = _scratch_dir(rig.scratch_root)
    assert (sd / ".opencode/tools/browser.js").exists()
    assert (sd / ".opencode/tools/browser_tool_impl.mjs").exists()


def test_agent_def_does_not_offer_upload_to_the_model(rig):
    """`upload` is operator-only: it takes a caller-chosen ABSOLUTE path with no
    allowlist and the model is pointed at untrusted, prompt-injecting pages. The
    def handed to the model must not advertise a capability the enforcement layer
    refuses (`op_not_allowed:upload`) — the old three-way drift told it otherwise."""
    r = rig.run(["read the page"], mode="ok")
    assert r.returncode == 0, r.stderr
    md = _agent_md(rig.scratch_root)
    assert 'op="upload"' not in md, "the agent def must not offer an upload op"
    assert "setFileInputFiles" not in md and "input type=file" not in md
    assert "There is no `upload`" in md, "the def should say upload is unavailable"


def test_wrapper_forces_tab_and_domain_policy_via_env(rig):
    """The model cannot choose the tab/instance/domain policy — the wrapper FORCES
    them on the tool via env. Assert the exact env opencode (and thus the tool)
    received."""
    r = rig.run(["read the page", "--instance", "work",
                 "--deny-domains", "evil.com,tracker.io", "--allow-domains", "wikipedia.org"],
                mode="ok")
    assert r.returncode == 0, r.stderr
    envs = _oc_env(rig.oc_env)
    assert envs, "fake opencode never recorded its env"
    e = envs[0]
    assert e.get("BROWSER_AGENT_TAB") == str(TAB_ID)
    assert e.get("BROWSER_AGENT_INSTANCE") == "work"
    assert e.get("BROWSER_AGENT_DENY_DOMAINS") == "evil.com tracker.io"
    assert e.get("BROWSER_AGENT_ALLOW_DOMAINS") == "wikipedia.org"
    assert e.get("BROWSER_AGENT_DRY_RUN") == "0"


def test_no_shell_string_path_remains(rig):
    """Belt-and-suspenders: the wrapper must not put a `browser` shim on PATH nor
    hand the agent any shell command string. The scratch dir carries NO `bin/`
    shim, and the task message never tells the model to run a `browser --tab …`
    shell command."""
    r = rig.run(["read the page"], mode="ok")
    assert r.returncode == 0, r.stderr
    sd = _scratch_dir(rig.scratch_root)
    assert not (sd / "bin").exists(), "no PATH-shadow shim dir may exist"
    # The task message (last opencode positional) must describe the TYPED tool,
    # not a shell command line.
    argv = _oc_calls(rig.oc_log)[0]["argv"]
    msg = argv[-1]
    assert "browser --tab" not in msg, "the model must not be given a shell command"
    assert "op=" in msg, "the model must be pointed at the typed tool"


# --------------------------------------------------------------------------- #
# Fail-closed tool-set gate: BEFORE opening a tab or spending a model token, the
# wrapper runs `opencode debug agent` and refuses to run unless the resolved tool
# set is browser-ONLY. This is what makes an un-upgraded / other opencode version
# SAFE — it refuses rather than running the model unconfined.
# --------------------------------------------------------------------------- #
def test_gate_passes_when_browser_only(rig):
    """Default gate output (browser:true, all host tools false) → the run proceeds
    normally (tab opened, opencode `run` invoked)."""
    r = rig.run(["read the page"], mode="ok")           # FAKE_OC_GATE_MODE defaults ok
    assert r.returncode == 0, r.stderr
    assert len(_oc_calls(rig.oc_log)) >= 1, "the model run must proceed when the gate passes"
    opens = [c for c in _browser_calls(rig.frb_log) if c["op"] == "open"]
    assert len(opens) == 1


@pytest.mark.parametrize("gate_mode,needle", [
    ("hosttool", "tool-set gate"),          # a host tool (bash) still enabled
    ("nobrowser", "tool-set gate"),         # the custom browser tool is absent
    ("unparseable", "tool-set gate"),       # debug output is not parseable
    ("truncated", "tool-set gate"),         # a truncated JSON prefix (flush race)
    ("empty", "tool-set gate"),             # exited 0 with NO output at all
    ("missing_hosttool", "tool-set gate"),  # bash omitted → can't confirm disabled
    ("fail", "tool-set gate"),              # `opencode debug agent` itself failed
])
def test_gate_fails_closed(rig, gate_mode, needle):
    """On ANY tool set the gate can't positively confirm as browser-only, the
    wrapper `die`s (rc=2), NEVER invokes the model, and — because the gate runs
    BEFORE the tab is opened — leaks NO tab (nothing to orphan)."""
    r = rig.run(["read the page"], mode="ok",
                extra_env={"FAKE_OC_GATE_MODE": gate_mode})
    assert r.returncode == 2, r.stdout + r.stderr
    assert needle in r.stderr.lower() or needle in r.stderr, r.stderr
    assert _oc_calls(rig.oc_log) == [], "the model must NEVER run when the gate fails"
    assert _browser_calls(rig.frb_log) == [], "no tab may be opened when the gate fails"


@pytest.mark.parametrize("gate_mode,phrase", [
    # A truncation used to surface as the SAME message as a version problem, which
    # is precisely what misdirected the diagnosis. Each failure class must name
    # itself distinctly so the operator can tell them apart from stderr alone.
    ("fail", "failed to RUN"),
    ("empty", "produced NO output"),
    ("unparseable", "UNPARSEABLE"),
    ("truncated", "UNPARSEABLE"),
    ("hosttool", "browser-ONLY tool set"),
    ("nobrowser", "browser-ONLY tool set"),
    ("missing_hosttool", "browser-ONLY tool set"),
])
def test_gate_failure_messages_are_distinct(rig, gate_mode, phrase):
    r = rig.run(["read the page"], mode="ok",
                extra_env={"FAKE_OC_GATE_MODE": gate_mode})
    assert r.returncode == 2, r.stdout + r.stderr
    assert phrase in r.stderr, f"{gate_mode}: expected {phrase!r} in stderr:\n{r.stderr}"
    # A run-failure / no-output / unparseable refusal must NOT claim the tool set
    # was resolved-but-wrong, and vice versa — that conflation is the bug.
    if phrase != "browser-ONLY tool set":
        assert "did not resolve browser-agent" not in r.stderr, (
            f"{gate_mode} must not masquerade as a not-browser-only tool set")


# --------------------------------------------------------------------------- #
# Regression pin: the gate must capture `opencode debug agent` to a FILE, never a
# PIPE (`$(...)`). opencode does not reliably flush stdout before exiting into a
# pipe, so a command-substitution capture can return a TRUNCATED prefix — which is
# unparseable, so the gate fails closed (correct) with a message that looks like an
# opencode-version problem (wrong diagnosis). Measured on these hosts: `debug
# skill` 65536 B piped vs 293329 B to a file; `debug v2` 55276/55276/6103 B across
# three identical piped runs — a flush RACE, not a fixed buffer cap.
# --------------------------------------------------------------------------- #
def test_gate_reads_from_a_file_not_a_pipe(rig):
    """The fake opencode emits a FULL valid dump only when its stdout is a regular
    FILE; through a pipe it emits a truncated prefix (mimicking the real flush
    race). The gate therefore passes IFF the wrapper redirected to a file."""
    r = rig.run(["read the page"], mode="ok",
                extra_env={"FAKE_OC_GATE_MODE": "pipe_truncates"})
    assert r.returncode == 0, (
        "the gate must capture the debug dump to a FILE — a `$(...)` pipe capture "
        f"truncates and fails closed spuriously:\n{r.stderr}")
    assert len(_oc_calls(rig.oc_log)) >= 1, "the model run must proceed"


def test_gate_dump_is_materialized_in_the_scratch_dir(rig):
    """The dump lands as a real file inside the per-run $SCRATCH (which is already
    mktemp -d'd and rm -rf'd on exit), and it holds the FULL untruncated JSON."""
    r = rig.run(["read the page"], mode="ok")
    assert r.returncode == 0, r.stderr
    gate_json = _scratch_dir(rig.scratch_root) / "gate.json"
    assert gate_json.exists(), "the gate must materialize its debug dump as a file"
    parsed = json.loads(gate_json.read_text())   # full document, not a prefix
    assert parsed["tools"]["browser"] is True
    assert parsed["tools"]["bash"] is False


def test_gate_source_has_no_command_substitution_capture():
    """Static guard: no `$(...)`/backtick/pipe capture of `opencode debug agent`
    may creep back into the wrapper — that is the flush-race bug itself."""
    src = WRAPPER.read_text()
    code = [ln for ln in src.splitlines() if not ln.lstrip().startswith("#")]
    # The line(s) that actually INVOKE the dump (a die/help message may *mention*
    # `opencode debug agent` in prose — that is documentation, not a capture).
    invocations = [ln for ln in code if '"$OPENCODE_BIN" debug agent' in ln]
    assert invocations, "the wrapper must still invoke `opencode debug agent`"
    for ln in invocations:
        assert "$(" not in ln, f"command-substitution capture of the gate dump: {ln}"
        assert "`" not in ln, f"backtick capture of the gate dump: {ln}"
        # A single `|` is a pipe; `||` is the die-on-failure guard (fine).
        assert "|" not in ln.replace("||", ""), f"piped capture of the gate dump: {ln}"
        assert ">" in ln, f"the gate dump must be redirected to a file: {ln}"
    # No line anywhere may command-substitute the debug dump.
    for ln in code:
        assert not ("$(" in ln and "debug agent" in ln), \
            f"command-substitution capture of the gate dump: {ln}"
    assert any('>"$GATE_OUT"' in ln or '> "$GATE_OUT"' in ln for ln in invocations), \
        "the gate dump must be redirected to $GATE_OUT (a file in $SCRATCH)"


# --------------------------------------------------------------------------- #
# Own-tab lifecycle: open → capture tabId → close on EVERY exit path
# --------------------------------------------------------------------------- #
def test_happy_path_opens_and_closes_exactly_once(rig):
    r = rig.run(["find the top stories"], mode="ok")
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout.strip())
    assert out == SCHEMA_OK
    calls = _browser_calls(rig.frb_log)
    opens = [c for c in calls if c["op"] == "open"]
    closes = [c for c in calls if c["op"] == "close"]
    assert len(opens) == 1, "must open exactly one tab"
    assert len(closes) == 1, "must close exactly one tab"
    assert closes[0]["tab"] == str(TAB_ID), "must close the tab it opened"


def test_tab_closed_on_opencode_error(rig):
    r = rig.run(["do it"], mode="nonzero")
    assert r.returncode != 0
    closes = [c for c in _browser_calls(rig.frb_log) if c["op"] == "close"]
    assert len(closes) == 1, "the owned tab must be closed even on opencode error"


def test_tab_closed_on_timeout(rig):
    # --timeout 2 process-group-kills the slow (30s) opencode well before its sleep.
    #
    # 🔴 An `elapsed < 20` belt lived here and is GONE. It asserted the wall clock
    # to prove a kill, and the status assertion below already proves it
    # DETERMINISTICALLY: a wrapper that failed to kill would collect the fake's
    # post-sleep output and report status "ok", never "blocked … timed out". The
    # clock added nothing but a way to go red on a stalled box — measured at 125.4s
    # for a 0.25s run under four concurrent gate builds, which would have blamed
    # the wrapper for the machine, the exact misdiagnosis this file now guards.
    # The slice is raised above the fake's 30s sleep on purpose so a broken
    # wrapper RUNS TO COMPLETION and fails on the assertion that means something.
    r = rig.run(["do it", "--timeout", "2"], mode="slow", timeout=60,
                extra_env={"FAKE_OC_SLEEP": "30"})
    out = json.loads(r.stdout.strip())
    assert out["status"] == "blocked"
    assert "timed out" in out["answer"].lower()
    closes = [c for c in _browser_calls(rig.frb_log) if c["op"] == "close"]
    assert len(closes) == 1, "the owned tab must be closed on a timeout kill"


def test_opencode_missing_no_orphan_tab(rig):
    """A missing opencode binary is a clean error and — crucially — opens NO tab
    (the availability check runs before `open`)."""
    r = rig.run(["do it"], extra_env={"BROWSER_AGENT_OPENCODE": "definitely-not-a-real-binary-xyz"})
    assert r.returncode != 0
    assert "opencode not found" in r.stderr.lower()
    assert _browser_calls(rig.frb_log) == [], "no tab may be opened if opencode is missing"


def test_open_failure_clean_error(rig):
    r = rig.run(["do it"], mode="ok", open_fail=True)
    assert r.returncode != 0
    assert "failed to open a tab" in r.stderr.lower()
    # A failed open leaves nothing to close (only the open attempt was logged).
    assert [c for c in _browser_calls(rig.frb_log) if c["op"] == "close"] == []


# --------------------------------------------------------------------------- #
# Pre-flight tab-readiness retry: after opening its own tab, the wrapper PROBES
# it (a NON-INJECTING `browser tabs` listing, no model tokens) to confirm the tab
# is live/owned before invoking opencode. Right after an extension reload the
# service worker's tab tracking isn't settled, so the just-opened tab can vanish;
# the wrapper must re-open (bounded retry) so the model never gets a doomed tabId.
# --------------------------------------------------------------------------- #
def test_readiness_probe_passes_first_try_single_open(rig):
    """Happy path: the probe passes on the first attempt → EXACTLY one open, one
    probe, no needless extra opens, then opencode is invoked."""
    r = rig.run(["read the page"], mode="ok")           # probe_fail defaults 0
    assert r.returncode == 0, r.stderr
    calls = _browser_calls(rig.frb_log)
    opens = [c for c in calls if c["op"] == "open"]
    probes = [c for c in calls if c["op"] == "tabs"]
    assert len(opens) == 1, f"expected exactly one open, got {len(opens)}"
    assert len(probes) == 1, f"expected exactly one readiness probe, got {len(probes)}"
    assert len(_oc_calls(rig.oc_log)) == 1, "opencode must be invoked once the probe passes"


def test_readiness_reopens_on_transient_tab_gone(rig):
    """First probe reports the tab is gone (post-reload transient), the retry's
    probe passes → the wrapper re-opens, the probe passes, and opencode IS
    invoked and the run proceeds to a normal result."""
    r = rig.run(["read the page"], mode="ok", probe_fail=1)
    assert r.returncode == 0, r.stdout + r.stderr
    assert json.loads(r.stdout.strip()) == SCHEMA_OK
    calls = _browser_calls(rig.frb_log)
    opens = [c for c in calls if c["op"] == "open"]
    probes = [c for c in calls if c["op"] == "tabs"]
    assert len(opens) == 2, f"expected a re-open after the transient, got {len(opens)} opens"
    assert len(probes) == 2, f"expected two probes (fail then pass), got {len(probes)}"
    # The stale (gone) tab is closed best-effort before re-opening.
    closes = [c for c in calls if c["op"] == "close"]
    assert len(closes) >= 1, "the stale tab must be closed best-effort before re-opening"
    assert len(_oc_calls(rig.oc_log)) == 1, "opencode must run once the tab is ready"


def test_readiness_all_attempts_fail_no_token_spend(rig):
    """Every probe reports tab-gone → the wrapper dies NON-ZERO with a clear
    message, NEVER invokes opencode (no token spend), and leaves NO tab open
    (cleanup closed the last owned tab)."""
    r = rig.run(["read the page"], mode="ok", probe_fail=99)
    assert r.returncode != 0, r.stdout + r.stderr
    assert "not ready after" in r.stderr.lower()
    # The clear error must NOT be a raw op-error dump on stdout, and no JSON result
    # (a doomed run must not print a fake schema).
    assert "owned_tab_gone" not in r.stdout, "the probe's op-error must not leak to stdout"
    assert r.stdout.strip() == "", "a readiness failure must not print a schema result"
    assert _oc_calls(rig.oc_log) == [], "opencode must NEVER run when the tab never gets ready"
    calls = _browser_calls(rig.frb_log)
    assert len([c for c in calls if c["op"] == "open"]) == 3, "should try open 3 times (bounded)"
    # No orphan: every opened tab was closed (2 stale closes in-loop + 1 on cleanup).
    opens = [c for c in calls if c["op"] == "open"]
    closes = [c for c in calls if c["op"] == "close"]
    assert len(closes) >= len(opens), "every opened tab must be closed (no orphan)"


def test_readiness_failure_still_closes_tab_no_orphan(rig):
    """A readiness-failure exit path must still run cleanup — the last owned tab
    is closed, so nothing is orphaned, and the error is human-readable."""
    r = rig.run(["read the page"], mode="ok", probe_fail=99)
    assert r.returncode != 0
    calls = _browser_calls(rig.frb_log)
    # The last-owned tab (TAB_ID) must appear in a close call (cleanup trap).
    assert any(c["op"] == "close" and c["tab"] == str(TAB_ID) for c in calls), \
        "cleanup must close the last owned tab on a readiness-failure exit"
    assert "extension loaded/connected" in r.stderr.lower(), "the error must be actionable"


# --------------------------------------------------------------------------- #
# REGRESSION (the second of two independent blockers that kept `browser agent`
# from EVER running): the readiness probe must not need PAGE-CONTENT access.
#
# The agent's tab is deliberately opened at about:blank, and chrome.scripting
# cannot inject into about:blank — it isn't covered by the manifest's <all_urls>
# host permission. The old probe ran `eval '1'` there, so EVERY run died with
#   readiness probe failed unexpectedly on tab N: browser: op 'eval' failed in
#   the browser: Cannot access contents of url "about:blank". …
# before a single model token was spent. The fake `browser` above reproduces that
# Chrome behaviour faithfully, so these tests FAIL on the pre-fix wrapper.
# --------------------------------------------------------------------------- #
def test_readiness_probe_passes_on_an_about_blank_tab(rig):
    """THE regression: the agent's own tab starts at about:blank and the probe
    must still pass. (Pre-fix this died rc=2 with Chrome's host-permission error.)"""
    r = rig.run(["report the page title"], mode="ok")
    assert r.returncode == 0, (
        "the readiness probe must pass on an about:blank tab — page-content "
        f"access is not required to check that a tab exists:\n{r.stderr}")
    assert "Cannot access contents of url" not in r.stderr
    assert json.loads(r.stdout.strip()) == SCHEMA_OK
    assert len(_oc_calls(rig.oc_log)) == 1, "the model must actually be reached"


def test_readiness_probe_does_not_inject_into_the_page(rig):
    """Structural pin: the probe must use a NON-INJECTING op. No `eval` (nor any
    other content-script op) may be issued against the agent's tab by the wrapper
    — the only pre-model browser calls are `open` and the `tabs` probe."""
    r = rig.run(["read the page"], mode="ok")
    assert r.returncode == 0, r.stderr
    ops = [c["op"] for c in _browser_calls(rig.frb_log)]
    assert "eval" not in ops, "the readiness probe must not inject into the page"
    for injecting in ("html", "getHtml", "text", "screenshot", "nav"):
        assert injecting not in ops, f"the wrapper must not issue `{injecting}` itself"
    assert ops.count("tabs") == 1, f"expected exactly one `tabs` probe, got {ops}"


def test_probe_source_does_not_use_eval():
    """Static guard: `eval` must not creep back into the probe. The wrapper may
    still MENTION it in the explanatory comment (that is the bug's postmortem),
    so only non-comment lines are checked."""
    src = WRAPPER.read_text()
    code = [ln for ln in src.splitlines() if not ln.lstrip().startswith("#")]
    probe_calls = [ln for ln in code if '"$BROWSER_BIN"' in ln and " eval " in ln]
    assert probe_calls == [], f"the wrapper must not run a `browser eval`: {probe_calls}"
    assert any('"$BROWSER_BIN"' in ln and " tabs" in ln for ln in code), \
        "the readiness probe must use the non-injecting `tabs` op"


def test_probe_hard_failure_dies_rc2_without_running_the_model(rig):
    """A genuine (non-tab-gone) probe failure is still HARD: rc=2, the error is
    surfaced on stderr, the model is never invoked, and no retry loop spins."""
    r = rig.run(["read the page"], mode="ok",
                extra_env={"FRB_PROBE_HARD_FAIL": "1"})
    assert r.returncode == 2, r.stdout + r.stderr
    assert "readiness probe failed unexpectedly" in r.stderr
    assert _oc_calls(rig.oc_log) == [], "the model must NEVER run on a hard probe failure"
    # rc=2 is terminal — exactly one open + one probe, no bounded-retry spin.
    ops = [c["op"] for c in _browser_calls(rig.frb_log)]
    assert ops.count("open") == 1, f"a hard probe failure must not retry the open: {ops}"
    assert ops.count("tabs") == 1, f"a hard probe failure must not re-probe: {ops}"
    assert r.stdout.strip() == "", "a hard probe failure must not print a schema result"


def test_probe_unparseable_listing_is_a_hard_failure(rig):
    """A 0-exit `tabs` whose output carries no tab list is rc=2 (fail closed) — an
    unreadable answer must never be treated as 'ready'."""
    r = rig.run(["read the page"], mode="ok",
                extra_env={"FRB_PROBE_UNPARSEABLE": "1"})
    assert r.returncode == 2, r.stdout + r.stderr
    assert "readiness probe failed unexpectedly" in r.stderr
    assert _oc_calls(rig.oc_log) == [], "the model must NEVER run on an unreadable probe"


def test_probe_output_never_reaches_stdout(rig):
    """PROBE_OUT captures stdout+stderr and must NEVER reach the wrapper's own
    stdout — in production the `tabs` listing is the operator's ENTIRE tab list
    (every url + title), and any of it on stdout would corrupt the JSON result."""
    # Happy path: the listing (incl. the decoy tab's title) must not leak.
    r = rig.run(["read the page"], mode="ok")
    assert r.returncode == 0, r.stderr
    assert PROBE_MARKER not in r.stdout, "the probe's tab listing leaked to stdout"
    assert PROBE_MARKER not in r.stderr, "the probe's tab listing leaked to stderr"
    assert json.loads(r.stdout.strip()) == SCHEMA_OK   # stdout is EXACTLY the schema
    # Tab-gone retry path: still nothing on stdout.
    r2 = rig.run(["read the page"], mode="ok", probe_fail=1)
    assert r2.returncode == 0, r2.stderr
    assert PROBE_MARKER not in r2.stdout
    assert json.loads(r2.stdout.strip()) == SCHEMA_OK
    # Hard-failure path: the error goes to STDERR, stdout stays empty.
    r3 = rig.run(["read the page"], mode="ok",
                 extra_env={"FRB_PROBE_HARD_FAIL": "1"})
    assert r3.returncode == 2
    assert r3.stdout.strip() == "", "an op-error dump must never reach stdout"


def test_unparseable_probe_error_does_not_dump_the_whole_listing(rig):
    """Privacy: when the listing can't be parsed, the stderr diagnostic is capped
    — it must not spray the operator's full tab list into the terminal/logs."""
    r = rig.run(["read the page"], mode="ok",
                extra_env={"FRB_PROBE_UNPARSEABLE": "1"})
    assert r.returncode == 2
    assert "could not find a tab list" in r.stderr
    assert "first 200 bytes" in r.stderr, "the raw response must be truncated"


# --------------------------------------------------------------------------- #
# Process-group kill on timeout — no orphaned child survives.
# --------------------------------------------------------------------------- #
def test_a_zombie_counts_as_gone_and_the_naive_check_disagrees():
    """🔴 The reap check must not mistake a corpse for a live process.

    Builds a REAL zombie: spawn a child, SIGKILL it, never `wait()` it. The
    control is the second assertion — `os.kill(pid, 0)` succeeding is the whole
    defect, so a version of `_process_gone` that reverts to it fails HERE rather
    than 35 seconds into the straggler test with "it never happened".

    Why this was invisible: on this host PID 1 is systemd and reaps an orphan in
    milliseconds, so the naive check is right almost always. In a CI step
    container PID 1 does not reap, and it is wrong every time — 5 of 5
    `devrc-ci` runs, on revisions with nothing to do with this file.
    """
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"])
    try:
        child.kill()
        _await(lambda: _proc_state(child.pid) == "Z",
               what=f"child {child.pid} to become a zombie", slice_s=10.0)

        assert _process_gone(child.pid), (
            f"a zombie ({child.pid}) must count as GONE — it has been killed; "
            f"only its parent has not reaped it yet")

        # The control. If this ever passes, the assertion above proves nothing,
        # because the naive check would satisfy it too.
        naive_says_alive = True
        try:
            os.kill(child.pid, 0)
        except (ProcessLookupError, PermissionError):
            naive_says_alive = False
        assert naive_says_alive, (
            "premise gone: `os.kill(pid, 0)` no longer succeeds on a zombie on "
            "this platform, so this test can no longer see the defect it pins")
    finally:
        child.wait()

    # And once reaped it is gone by BOTH readings — the pid is out of the table.
    assert _process_gone(child.pid)


def test_the_reap_check_reports_a_live_process_as_running():
    """The other direction, so `_process_gone` cannot be trivially `return True`."""
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"])
    try:
        assert _proc_state(child.pid) in ("R", "S", "D")
        assert not _process_gone(child.pid), "a running process reported as gone"
    finally:
        child.kill()
        child.wait()


def test_timeout_reaps_the_whole_process_group(rig, tmp_path):
    """opencode may spawn helper children in its group; a naive per-pid `timeout`
    kill would orphan them. The wrapper runs opencode under `setsid` and kills the
    WHOLE group on timeout, so an inherited-group child is reaped before it can
    write its 'survived' marker."""
    pid_file = tmp_path / "straggler.pid"
    survived = tmp_path / "straggler.survived"
    r = rig.run(["do it", "--timeout", "2"], mode="straggler", timeout=30,
                extra_env={"FAKE_OC_SLEEP": "30", "STRAGGLER_SLEEP": "30",
                           "STRAGGLER_PID": str(pid_file),
                           "STRAGGLER_SURVIVED": str(survived)})
    assert json.loads(r.stdout.strip())["status"] == "blocked"
    assert pid_file.exists(), "the straggler child never started (test rig issue)"
    child_pid = int(pid_file.read_text().strip())

    # 🔴 `_process_gone`, NOT a local `os.kill(pid, 0)` — read its docstring. The
    # straggler is orphaned by design here, so whether it disappears or lingers
    # as a zombie is decided by whatever PID 1 happens to be, and that differs
    # between this dev host and a CI step container.
    _gone = _process_gone

    # 🔴 This was `deadline = time.time() + 8` — a wall clock, and the same defect
    # class as the flake this file now guards: on a stalled box the kill can take
    # longer than 8s to propagate and the test reds against the wrapper for the
    # machine's sin. The wait is now on a TERMINAL condition: either the
    # group-kill worked and the child is gone, or it did not and the child
    # finishes its 30s sleep and writes 'survived'. Exactly one of those
    # eventually holds — but the SLOW one takes ~30s, so the slice must exceed it
    # or a broken wrapper reports "it never happened" (an `_await` diagnostic)
    # instead of "the straggler outlived the timeout" (this test's own point).
    # The inner poll returns the moment the predicate holds, so 35s costs nothing
    # on green: this test runs in ~2s.
    _await(lambda: _gone(child_pid) or survived.exists(),
           what=f"the straggler ({child_pid}) to be reaped or write its marker",
           slice_s=35.0)
    assert not survived.exists(), "the straggler outlived the timeout (wrote 'survived')"
    assert _gone(child_pid), f"orphaned child {child_pid} survived the timeout kill"


# --------------------------------------------------------------------------- #
# Schema parse + EXACTLY ONE retry, then blocked (no infinite retry)
# --------------------------------------------------------------------------- #
def test_malformed_then_blocked_after_one_retry(rig):
    r = rig.run(["do it"], mode="malformed")
    assert r.returncode != 0
    out = json.loads(r.stdout.strip())
    assert out["status"] == "blocked"
    calls = _oc_calls(rig.oc_log)
    assert len(calls) == 2, f"expected EXACTLY 2 opencode calls (1 retry), got {len(calls)}"
    assert calls[0]["continue"] is False
    assert calls[1]["continue"] is True, "the retry must use --continue"


def test_malformed_then_recovers_on_retry(rig):
    r = rig.run(["do it"], mode="malformed_then_ok")
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout.strip())
    assert out["status"] == "ok"
    calls = _oc_calls(rig.oc_log)
    assert len(calls) == 2
    assert calls[1]["continue"] is True


def test_nonzero_exit_no_retry(rig):
    """A non-zero opencode exit is surfaced as blocked WITHOUT a retry (the retry
    is only for a completed-but-malformed run)."""
    r = rig.run(["do it"], mode="nonzero")
    assert r.returncode != 0
    out = json.loads(r.stdout.strip())
    assert out["status"] == "blocked"
    assert len(_oc_calls(rig.oc_log)) == 1, "a hard opencode error must NOT retry"


def test_partial_status_is_success_exit(rig):
    r = rig.run(["do it"], mode="partial")
    assert r.returncode == 0
    assert json.loads(r.stdout.strip())["status"] == "partial"


# --------------------------------------------------------------------------- #
# The isolated opencode CONFIG dir: warm / cold / unusable.
#
# 🔴 This section exists because these three paths ran on EVERY test in this file
# and were asserted by NONE of them. The wrapper's warm step could only ever FAIL
# here (it "warms" by making the fake opencode materialise node_modules, which a
# 40-line python stub does not do), so all 51 tests silently ran in the degraded
# global-config fallback — the opposite of production — while paying a doomed
# bootstrap each time, and printing a warning that then read as a cause whenever
# any test failed for any reason. The rig now pre-warms (see `_prewarm_oc_config`),
# so the bootstrap is gone from the other tests and is pinned HERE instead.
# --------------------------------------------------------------------------- #
def test_a_warm_config_dir_is_used_and_skips_the_bootstrap(rig):
    """The positive control for the pre-warm: with a warm dir the wrapper must
    (a) not warn, (b) not run the bootstrap at all, and (c) actually export the
    ISOLATED config dir to opencode. (c) is the load-bearing one — without it the
    pre-warm could silently stop matching the stamp and every test would drift
    back into the fallback with nothing going red."""
    r = rig.run(["read the page"], mode="ok")
    assert r.returncode == 0, r.stderr
    assert "could not warm" not in r.stderr
    # `opencode.jsonc` is written by `_oc_bootstrap_locked` and by nothing else,
    # so its ABSENCE is a structural proof the bootstrap never ran — no timing,
    # no log parsing.
    assert not (rig.oc_config_dir / "opencode.jsonc").exists(), \
        "the bootstrap ran despite a warm dir (it wrote opencode.jsonc)"
    assert not Path(str(rig.oc_config_dir) + ".lock").exists(), \
        "the warm lock was taken (or leaked) despite a warm dir"
    envs = _oc_env(rig.oc_env)
    assert envs, "fake opencode never recorded its env"
    assert envs[0].get("OPENCODE_CONFIG_DIR") == str(rig.oc_config_dir), \
        "the wrapper did not run under the ISOLATED config dir"


def test_a_cold_config_dir_degrades_loudly_and_still_runs(rig):
    """The documented contract for a config dir that cannot be warmed: DEGRADE,
    LOUDLY — warn on stderr, do NOT export the isolated dir, and still complete
    the run. A browser-agent that works and is expensive beats one that dies."""
    shutil.rmtree(rig.oc_config_dir)
    r = rig.run(["read the page"], mode="ok")
    assert r.returncode == 0, r.stderr
    assert "could not warm" in r.stderr, "the fallback must be LOUD"
    envs = _oc_env(rig.oc_env)
    assert envs, "fake opencode never recorded its env"
    assert "OPENCODE_CONFIG_DIR" not in envs[0], \
        "a failed warm must NOT leave the isolated config dir exported"


def test_an_unusable_warm_lock_degrades_instead_of_stalling(rig):
    """🔴 REGRESSION (the 2026-08-13 flake's mechanism class). `_oc_lock` treated
    EVERY `mkdir` failure as contention and slept on it — up to
    (WARM_TIMEOUT + 30) x 2 x 0.5 s, i.e. ~120 s on production defaults — then
    "stole" a lock that had never existed and bootstrapped UNSERIALISED anyway.
    A regular file at the lock path (or ENOSPC, EACCES, EIO — sleeping fixes none
    of them) is permanent, so that wait is pure stall, and it is LONGER than the
    deadline any caller of this script allows.

    The discriminator asserted here is STRUCTURAL, never temporal: post-fix the
    wrapper refuses the lock and so never reaches `_oc_bootstrap_locked`, the one
    and only writer of `opencode.jsonc`. Confirmed RED at the parent commit on
    exactly that assertion, after a measured 120.32 s stall.

    🔴 An `elapsed < 20` belt was tried here and REMOVED. It failed inside a
    deliberate reproduction of the original conditions (four concurrent
    devrc-pytests builds, loadavg 65) at `elapsed = 125.4 s` — with the
    structural assertion passing, i.e. the lock WAS refused and the machine
    simply stalled a 0.25 s run by ~400x. Re-asserting the wall clock to guard
    against a wall-clock bug just relocates the flake; the structural check
    catches the same regression and cannot be stalled into a false red.

    Runs with the PRODUCTION warm timeout, not the rig's pinned-down one, so the
    guarded constant is the one that actually ships."""
    shutil.rmtree(rig.oc_config_dir)
    rig.oc_config_dir.mkdir()
    lock = Path(str(rig.oc_config_dir) + ".lock")
    lock.write_text("not a directory")          # mkdir() here can never succeed
    # The deadline is raised ABOVE the guarded stall on purpose. A regression
    # test has to let the buggy path run to completion, or the generic hang-net
    # fires first and the specific assertion below never gets to speak: with the
    # default 60 s slice a mutated wrapper failed at 60 s with "the wrapper
    # genuinely hung" — true, red, and the wrong message.
    r = rig.run(["read the page"], mode="ok", timeout=200,
                extra_env={"BROWSER_AGENT_WARM_TIMEOUT": "90"})
    assert r.returncode == 0, r.stderr
    assert "could not warm" in r.stderr, "an unusable lock must degrade LOUDLY"
    # 🔴 The bug's two halves need two DIFFERENT observables, and neither is a
    # clock. WHICH branch refused is otherwise unobservable — both end identically
    # — so the wrapper emits a DECLARED TOKEN per branch from one writer, and the
    # pair below pins that it took the fast one. The absence of `opencode.jsonc`
    # (written by `_oc_bootstrap_locked` and by nothing else) pins that it did not
    # BOOTSTRAP unserialised. Both lines exist because a mutation restoring only
    # the sleep passed everything weaker: first the jsonc check alone stayed
    # green, then a single shared message did too (the post-wait branch emits it
    # as well). Prose in these messages may be reworded; a token may not.
    assert "warm-lock-refused:unwaitable" in r.stderr, \
        "the wrapper did not take the immediate-refusal branch"
    assert "warm-lock-refused:timeout" not in r.stderr, \
        ("the wrapper WAITED on a lock path that can never become available — "
         "only EEXIST-on-a-directory is contention, everything else is permanent")
    # A FACT, not a wording: the operator has to be told WHICH path to go fix.
    # Pinning that the message names the path is not pinning how it reads.
    assert str(lock) in r.stderr, "the refusal must name the offending lock path"
    assert not (rig.oc_config_dir / "opencode.jsonc").exists(), \
        ("the bootstrap ran without holding the lock — `_oc_lock`'s result was "
         "discarded, or it stole a lock that was never there")
    assert lock.is_file(), "the stray file at the lock path must be left alone"


def test_a_lock_a_holder_never_frees_refuses_rather_than_bootstrapping_unlocked(rig):
    """🔴 The CONTENTION branch — genuinely held lock, legitimately waited for,
    steal fails, refuse. It shipped with NO test reaching it at all: every other
    case takes the immediate-refusal branch, so a mutation deleting this branch's
    `return 1` survived the whole 56-test file while reintroducing exactly the
    defect this PR removes (the caller bootstrapping WITHOUT the lock).

    A NON-EMPTY directory at the lock path is what makes the branch reachable:
    `mkdir` fails (EEXIST, and it IS a directory ⇒ real contention, so waiting is
    correct here), and after the budget the stale-lock steal fails too because
    `rmdir` cannot remove a non-empty directory.

    The guard is structural — `opencode.jsonc` is written by
    `_oc_bootstrap_locked` and by nothing else, so its absence proves the
    bootstrap never ran — plus the declared token naming which branch refused.
    The budget is shortened via its seam so the branch is reachable in ~1 s
    instead of the ~120 s that kept it untested.

    🔴 SCOPE, stated at what is actually measured: this proves the branch is
    REACHED and REFUSES CORRECTLY. It does NOT prove the wait was applied — the
    budget's use site (`deadline=$(( SECONDS + OC_LOCK_BUDGET ))`) is unguarded,
    and mutating it to `SECONDS + 0` (steal instantly, never wait) is green here
    and across `test_opencode_config.py`. Separating "waited" from "did not wait"
    needs a clock, which is exactly the instrument this PR removes, and the
    obvious clock-free design — a helper that releases the lock mid-wait — races
    the wrapper's own startup on a stalled box, so it would buy the guard back at
    the price of the flake. The derivation test in `test_opencode_config.py` pins
    the budget's VALUE; nothing pins its application, and nothing did before
    either."""
    shutil.rmtree(rig.oc_config_dir)
    rig.oc_config_dir.mkdir()
    lock = Path(str(rig.oc_config_dir) + ".lock")
    (lock / "held-by-another-run").mkdir(parents=True)
    r = rig.run(["read the page"], mode="ok",
                extra_env={"BROWSER_AGENT_LOCK_BUDGET": "1"})
    assert r.returncode == 0, r.stderr
    assert "could not warm" in r.stderr, "the fallback must still be LOUD"
    # Structural first, on purpose: it names the DEFECT, and a mutant that both
    # bootstraps unlocked and drops the token should report the former.
    assert not (rig.oc_config_dir / "opencode.jsonc").exists(), \
        ("the bootstrap ran WITHOUT the lock — `_oc_lock` returned success after "
         "failing to take it, which is the defect this PR exists to remove")
    assert "warm-lock-refused:timeout" in r.stderr, \
        "a lock held to the end of the budget must refuse with the timeout token"
    assert str(lock) in r.stderr, "the refusal must name the offending lock path"
    assert (lock / "held-by-another-run").is_dir(), \
        "a lock it could not take must be left exactly as found"


def test_the_rigs_deadline_reports_a_machine_baseline_not_a_bare_timeout(rig):
    """The negative control for this file's OWN hang-net (see RUN_TIMEOUT). A
    bare `subprocess.TimeoutExpired` says nothing about WHY, and the only other
    thing in the captured output is the wrapper's warm warning — which pytest
    echoes because the test failed, not because it caused anything. That
    misreading is the whole reason this section exists, so the diagnostic itself
    gets a test rather than being assumed to work.

    Driven with a deliberately unmeetable 2 s deadline against a wrapper told to
    take ~5 s; the fake's sleep is kept short so the setsid'd child a hard kill
    leaves behind reaps itself within seconds. `stall_cap=0` forbids the
    stall-extension branch, so the outcome does not depend on how loaded the box
    happens to be while this test runs."""
    with pytest.raises(BaseException) as ei:                 # pytest.fail -> Failed
        rig.run(["do it", "--timeout", "5"], mode="slow",
                extra_env={"FAKE_OC_SLEEP": "6"}, timeout=2, stall_cap=0)
    msg = str(ei.value)
    assert "did not exit within 2s" in msg
    assert "HANG-NET" in msg, "the failure must say the deadline is not a perf assertion"
    assert "trivial processes on this machine" in msg, \
        "the failure must carry a FRESH machine baseline, so a stall is legible"


def test_a_stalled_machine_extends_the_wait_instead_of_failing(rig, monkeypatch):
    """The POSITIVE control for the stall-extension branch — the one piece of new
    logic that actually removes the wall-clock dependency, and the one a
    passing-on-an-idle-box suite would never execute. The machine probe is forced
    to report a stalled box, so a wrapper that legitimately outruns the deadline
    several times over must still be WAITED for and its result returned, not
    killed. Delete the extension branch and this goes red at the first slice."""
    monkeypatch.setattr(sys.modules[__name__], "_spawn_baseline",
                        lambda n=_SPAWN_PROBE_N: 99.0)
    r = rig.run(["do it", "--timeout", "20"], mode="slow",
                extra_env={"FAKE_OC_SLEEP": "3"}, timeout=1)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout.strip())["status"] == "ok", \
        "a run that outlived several deadline slices must still be READ, not killed"


def test_the_hard_cap_ends_the_wait_even_on_a_stalled_machine(rig, monkeypatch):
    """The stall extension has TWO terms and only one was exercised. With the
    machine probe forced to "stalled" the extension is granted forever unless the
    hard cap stops it — so the cap is the actual hang-net, and it needs its own
    control. `stall_cap=0` spends the budget on the first slice."""
    monkeypatch.setattr(sys.modules[__name__], "_spawn_baseline",
                        lambda n=_SPAWN_PROBE_N: 99.0)
    with pytest.raises(BaseException) as ei:                 # pytest.fail -> Failed
        rig.run(["do it", "--timeout", "5"], mode="slow",
                extra_env={"FAKE_OC_SLEEP": "6"}, timeout=1, stall_cap=0)
    msg = str(ei.value)
    assert "stall budget for this run is exhausted" in msg, \
        "a stalled machine past the hard cap must say the CAP ended it, not the probe"


def test_a_non_numeric_seam_dies_with_its_own_name(rig):
    """The lock budget is derived by arithmetic at TOP LEVEL now, not inside the
    cold path, so a non-numeric warm timeout would spray a raw bash arithmetic
    error over every run — warm ones included, which never evaluated it before —
    and then die on an unbound `deadline` two steps from the cause. Validate at
    the seam, in the wrapper's normal error contract."""
    r = rig.run(["do it"], extra_env={"BROWSER_AGENT_WARM_TIMEOUT": "9x"})
    assert r.returncode == 2, r.stderr
    assert "BROWSER_AGENT_WARM_TIMEOUT" in r.stderr, \
        "the error must name the seam the operator set, not an internal variable"
    assert "arithmetic" not in r.stderr and "unbound" not in r.stderr, \
        "a misconfiguration must not surface as a raw shell error"


def test_the_condition_poll_also_extends_on_a_stalled_machine(monkeypatch):
    """`_await` is the second consumer of the same stall rule (the straggler-reap
    poll uses it). Its extension branch needs its own positive control — sharing
    `_stall_extends` means a break there breaks both, but only a test that
    actually ENTERS the branch proves it is reachable from here."""
    monkeypatch.setattr(sys.modules[__name__], "_spawn_baseline",
                        lambda n=_SPAWN_PROBE_N: 99.0)
    ready = time.monotonic() + 0.6
    _await(lambda: time.monotonic() >= ready,
           what="a condition that resolves after several slices",
           slice_s=0.1, poll=0.01)
