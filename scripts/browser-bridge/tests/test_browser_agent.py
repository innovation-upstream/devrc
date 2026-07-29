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
    path.write_text(content)
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return path


def _fake_browser(path: Path) -> Path:
    """A stand-in for the real `browser` CLI (used ONLY for open/close/print-session-id
    by the wrapper — the agent's own reads go through the custom tool, not this).

    - `--print-session-id`           → prints a stable fake id.
    - `[--instance K] open [url]`     → prints {result:{data:{tabId:FRB_TABID}}}
      (or exits 1 if FRB_OPEN_FAIL=1).
    - `[--instance K] --tab N <op> …` → prints a canned success envelope.
    Every invocation is appended (one JSON line) to $FRB_LOG for assertions.
    """
    return _write_exec(path, '''#!/usr/bin/env python3
import json, os, sys
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
if op == "open":
    if os.environ.get("FRB_OPEN_FAIL") == "1":
        sys.stderr.write("fake open failed\\n"); sys.exit(1)
    tabid = int(os.environ.get("FRB_TABID", "4242"))
    print(json.dumps({"ok": True, "result": {"id": "x", "ok": True,
          "data": {"tabId": tabid, "url": "about:blank"}}}))
    sys.exit(0)
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
    gate = os.environ.get("FAKE_OC_GATE_MODE", "ok")
    if gate == "fail":               # `debug agent` itself errors (bad/unsupported)
        sys.stderr.write("fake debug agent failed\n"); sys.exit(1)
    if gate == "unparseable":        # output is not JSON → gate must fail closed
        print("<not json>"); sys.exit(0)
    tools = {"bash": False, "read": False, "edit": False, "write": False,
             "webfetch": False, "glob": False, "grep": False, "task": False,
             "browser": True}
    if gate == "hosttool":           # a host tool leaked back on → must refuse
        tools["bash"] = True
    elif gate == "nobrowser":        # custom tool absent (unsupported ver) → refuse
        del tools["browser"]
    elif gate == "missing_hosttool": # bash omitted → can't confirm → refuse
        del tools["bash"]
    print(json.dumps({"name": "browser-agent", "tools": tools}))
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
    keep = {k: v for k, v in os.environ.items()
            if k.startswith("BROWSER_AGENT_") or k.startswith("BROWSER_BRIDGE_")}
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


@pytest.fixture
def rig(tmp_path):
    """Assemble the fake binaries + a scratch TMPDIR and return a runner."""
    bind = tmp_path / "bin"; bind.mkdir()
    fbrowser = _fake_browser(bind / "browser-fake")
    focode = _fake_opencode(bind / "opencode")
    frb_log = tmp_path / "frb.log"
    oc_log = tmp_path / "oc.log"
    oc_env = tmp_path / "oc_env.log"
    scratch_root = tmp_path / "scratch"; scratch_root.mkdir()

    def run(args, mode="ok", extra_env=None, timeout=60, open_fail=False,
            tabid=TAB_ID):
        env = dict(os.environ)
        env.update(
            BROWSER_AGENT_OPENCODE=str(focode),
            BROWSER_AGENT_BROWSER_BIN=str(fbrowser),
            FAKE_OC_MODE=mode, FAKE_OC_LOG=str(oc_log), FAKE_OC_ENV=str(oc_env),
            FRB_LOG=str(frb_log), FRB_TABID=str(tabid),
            TMPDIR=str(scratch_root),
            BROWSER_AGENT_KEEP_SCRATCH="1",
            PATH=f"{bind}:{env.get('PATH','')}",
        )
        if open_fail:
            env["FRB_OPEN_FAIL"] = "1"
        if extra_env:
            env.update(extra_env)
        r = subprocess.run([str(WRAPPER), *args], env=env,
                           capture_output=True, text=True, timeout=timeout)
        return r

    rig.frb_log = frb_log
    rig.oc_log = oc_log
    rig.oc_env = oc_env
    rig.scratch_root = scratch_root
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
    t0 = time.time()
    # --timeout 2 process-group-kills the slow (30s) opencode well before its sleep.
    r = rig.run(["do it", "--timeout", "2"], mode="slow", timeout=30,
                extra_env={"FAKE_OC_SLEEP": "30"})
    elapsed = time.time() - t0
    assert elapsed < 20, f"wrapper did not kill in time ({elapsed:.1f}s)"
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
# Process-group kill on timeout — no orphaned child survives.
# --------------------------------------------------------------------------- #
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
    # Give the group-kill a moment to propagate, then assert the child is gone and
    # never wrote its post-sleep 'survived' marker.
    deadline = time.time() + 8
    alive = True
    while time.time() < deadline:
        try:
            os.kill(child_pid, 0)
            time.sleep(0.2)
        except ProcessLookupError:
            alive = False
            break
        except PermissionError:
            alive = False
            break
    assert not alive, f"orphaned child {child_pid} survived the timeout kill"
    assert not survived.exists(), "the straggler outlived the timeout (wrote 'survived')"


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
