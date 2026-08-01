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
    probe_count = tmp_path / "probe.count"
    scratch_root = tmp_path / "scratch"; scratch_root.mkdir()

    def run(args, mode="ok", extra_env=None, timeout=60, open_fail=False,
            tabid=TAB_ID, probe_fail=0):
        env = dict(os.environ)
        env.update(
            BROWSER_AGENT_OPENCODE=str(focode),
            BROWSER_AGENT_BROWSER_BIN=str(fbrowser),
            FAKE_OC_MODE=mode, FAKE_OC_LOG=str(oc_log), FAKE_OC_ENV=str(oc_env),
            FRB_LOG=str(frb_log), FRB_TABID=str(tabid),
            FRB_PROBE_FAIL_TIMES=str(probe_fail), FRB_PROBE_COUNT=str(probe_count),
            TMPDIR=str(scratch_root),
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
