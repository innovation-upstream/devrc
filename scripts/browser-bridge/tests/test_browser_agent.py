"""Tests for the `browser agent` wrapper (browser-agent) and its guardrail shim
(browser-agent-guard).

Fully HEADLESS — NO live model, NO Brave. A fake `opencode` (emits a canned
`--format json` JSONL stream, modelled on the verified real 1.18.4 envelope) and
a fake `browser` CLI (records every op it is asked to run, hands out a fixed
tabId on `open`) stand in for the real binaries via env seams
(BROWSER_AGENT_OPENCODE / BROWSER_AGENT_BROWSER_BIN). The guard shim is the REAL
committed script; the agent-md template + parser are the REAL committed files.

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
GUARD = BB / "browser-agent-guard"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("python3") is None,
    reason="bash + python3 required to drive the wrapper/guard")

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
    """A stand-in for the real `browser` CLI.

    - `[--instance K] open [url]`     → prints {result:{data:{tabId:FRB_TABID}}}
      (or exits 1 if FRB_OPEN_FAIL=1).
    - `[--instance K] --tab N <op> …` → prints a canned success envelope.
    Every invocation is appended (one JSON line) to $FRB_LOG for assertions.
    """
    return _write_exec(path, '''#!/usr/bin/env python3
import json, os, sys
argv = sys.argv[1:]
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
    $FAKE_OC_LOG so tests can prove the retry count. It emits a REAL-shaped
    `--format json` JSONL stream on stdout."""
    return _write_exec(path, r'''#!/usr/bin/env python3
import json, os, re, subprocess, sys, time
argv = sys.argv[1:]
cont = "--continue" in argv
# The task message is the last positional (after all flags/values we know).
msg = argv[-1] if argv else ""
log = os.environ.get("FAKE_OC_LOG")
if log:
    with open(log, "a") as f:
        f.write(json.dumps({"continue": cont, "argv": argv}) + "\n")

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

def tab_from_msg():
    m = re.search(r"tab id is (\d+)", msg)
    return m.group(1) if m else None

mode = os.environ.get("FAKE_OC_MODE", "ok")
if mode == "slow":
    time.sleep(float(os.environ.get("FAKE_OC_SLEEP", "30")))
    emit_text(schema()); sys.exit(0)
if mode == "nonzero":
    sys.stderr.write("fake opencode error\n"); sys.exit(3)
if mode == "malformed":
    emit_text("I tried but here is no JSON at all."); sys.exit(0)
if mode == "malformed_then_ok":
    if cont: emit_text(schema())
    else: emit_text("no json yet");
    sys.exit(0)
if mode == "partial":
    emit_text(schema(status="partial")); sys.exit(0)
if mode in ("drive_ok", "drive_wrongtab", "drive_denydomain", "drive_dryrun"):
    tab = tab_from_msg()
    def run_browser(*a):
        subprocess.run(["browser", *a], capture_output=True, text=True)
    if mode == "drive_ok":
        run_browser("--tab", tab, "text")
        emit_text(schema(status="ok")); sys.exit(0)
    if mode == "drive_wrongtab":
        run_browser("--tab", "999", "nav", "https://evil.example.com")
        emit_text(schema(status="partial")); sys.exit(0)
    if mode == "drive_denydomain":
        run_browser("--tab", tab, "nav", "https://evil.example.com")
        emit_text(schema(status="partial")); sys.exit(0)
    if mode == "drive_dryrun":
        run_browser("--tab", tab, "nav", "https://good.example.com")
        emit_text(schema(status="ok")); sys.exit(0)
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
    scratch_root = tmp_path / "scratch"; scratch_root.mkdir()

    def run(args, mode="ok", extra_env=None, timeout=60, open_fail=False,
            tabid=TAB_ID):
        env = dict(os.environ)
        env.update(
            BROWSER_AGENT_OPENCODE=str(focode),
            BROWSER_AGENT_BROWSER_BIN=str(fbrowser),
            FAKE_OC_MODE=mode, FAKE_OC_LOG=str(oc_log),
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


def _agent_md(scratch_root: Path):
    hits = list(scratch_root.glob("browser-agent.*/.opencode/agents/browser-agent.md"))
    assert hits, "no per-run agent md was written"
    return hits[0].read_text()


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


def test_flags_parsed_and_threaded(rig):
    """A successful run with --steps N templates that budget into the per-run
    agent md, and locks the bash permission to the OWNED tab."""
    r = rig.run(["read the page", "--steps", "7"], mode="ok")
    assert r.returncode == 0, r.stderr
    md = _agent_md(rig.scratch_root)
    assert "steps: 7" in md, "the --steps budget must be templated into the agent"
    assert f"browser --tab {TAB_ID} *" in md, \
        "the bash permission must be locked to the owned tab"
    assert "__TAB_ID__" not in md and "__STEPS__" not in md    # fully templated


# --------------------------------------------------------------------------- #
# Own-tab lifecycle: open → capture tabId → inject → close on EVERY exit path
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
    # --timeout 2 hard-kills the slow (30s) opencode well before its sleep ends.
    r = rig.run(["do it", "--timeout", "2"], mode="slow", timeout=30,
                extra_env={"FAKE_OC_SLEEP": "30"})
    elapsed = time.time() - t0
    assert elapsed < 20, f"wrapper did not hard-kill in time ({elapsed:.1f}s)"
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
# Integration/smoke: fake opencode DRIVES the guard shim (`browser` on PATH) →
# assert ops route to the OWNED tab only, the tab is opened+closed once, and the
# active tab is never targeted.
# --------------------------------------------------------------------------- #
def test_integration_ops_route_to_owned_tab_only(rig):
    r = rig.run(["read the page"], mode="drive_ok")
    assert r.returncode == 0, r.stderr
    calls = _browser_calls(rig.frb_log)
    ops = [c for c in calls if c["op"] not in ("open", "close")]
    assert ops, "the agent never actually drove the browser"
    # EVERY driven op targeted the owned tab — never the active tab (no --tab).
    assert all(c["tab"] == str(TAB_ID) for c in ops), \
        f"an op targeted a non-owned tab: {ops}"
    assert any(c["op"] == "text" for c in ops), "expected a `text` read"
    assert len([c for c in calls if c["op"] == "open"]) == 1
    assert len([c for c in calls if c["op"] == "close"]) == 1
    assert json.loads(r.stdout.strip())["status"] == "ok"


def test_integration_wrong_tab_is_blocked_by_guard(rig):
    """When the model tries to touch a DIFFERENT tab, the guard refuses it — the
    fake browser never receives an op on tab 999, so the active tab is safe."""
    r = rig.run(["do it"], mode="drive_wrongtab")
    calls = _browser_calls(rig.frb_log)
    # No op ever reached the real browser on the forbidden tab.
    assert not any(c["tab"] == "999" for c in calls), \
        "the guard let an op through to a non-owned tab"
    # The owned tab was still opened + closed cleanly.
    assert len([c for c in calls if c["op"] == "open"]) == 1
    assert len([c for c in calls if c["op"] == "close"]) == 1


def test_integration_deny_domain_blocked_by_guard(rig):
    r = rig.run(["do it", "--deny-domains", "evil.example.com"],
                mode="drive_denydomain")
    calls = _browser_calls(rig.frb_log)
    # The nav to the denied domain never reached the real browser.
    navs = [c for c in calls if c["op"] == "nav"]
    assert navs == [], f"a denied-domain nav reached the browser: {navs}"


def test_integration_dry_run_intercepts_nav(rig):
    r = rig.run(["do it", "--dry-run"], mode="drive_dryrun")
    calls = _browser_calls(rig.frb_log)
    navs = [c for c in calls if c["op"] == "nav"]
    assert navs == [], "dry-run must intercept the nav (never execute it)"


# --------------------------------------------------------------------------- #
# The guardrail shim (browser-agent-guard) — direct unit coverage.
# --------------------------------------------------------------------------- #
@pytest.fixture
def guard_rig(tmp_path):
    bind = tmp_path / "bin"; bind.mkdir()
    fbrowser = _fake_browser(bind / "real-browser")
    frb_log = tmp_path / "frb.log"
    audit = tmp_path / "audit.jsonl"

    def run(args, env=None):
        e = dict(os.environ)
        e.update(BAG_TAB=str(TAB_ID), BAG_REAL_BROWSER=str(fbrowser),
                 BAG_TRANSCRIPT=str(audit), FRB_LOG=str(frb_log),
                 FRB_TABID=str(TAB_ID))
        if env:
            e.update(env)
        r = subprocess.run([str(GUARD), *args], env=e,
                           capture_output=True, text=True, timeout=20)
        return r

    guard_rig.run = run
    guard_rig.frb_log = frb_log
    guard_rig.audit = audit
    return guard_rig


def _guard_browser_calls(frb_log):
    return _browser_calls(frb_log)


def test_guard_passes_owned_tab_op_through(guard_rig):
    r = guard_rig.run(["--tab", str(TAB_ID), "text"])
    assert r.returncode == 0, r.stderr
    calls = _guard_browser_calls(guard_rig.frb_log)
    assert len(calls) == 1 and calls[0]["op"] == "text"
    assert calls[0]["tab"] == str(TAB_ID)


def test_guard_refuses_wrong_tab(guard_rig):
    r = guard_rig.run(["--tab", "999", "text"])
    assert r.returncode != 0
    assert "guard_refused" in r.stdout
    assert _guard_browser_calls(guard_rig.frb_log) == [], "wrong-tab op must not exec"


def test_guard_refuses_missing_tab(guard_rig):
    r = guard_rig.run(["text"])
    assert r.returncode != 0
    assert "missing_--tab" in r.stdout or "missing" in r.stdout


def test_guard_refuses_disallowed_op(guard_rig):
    for op in ("open", "close", "tabs", "release"):
        r = guard_rig.run(["--tab", str(TAB_ID), op])
        assert r.returncode != 0, f"op {op} should be refused"
    assert _guard_browser_calls(guard_rig.frb_log) == []


def test_guard_blocks_denied_domain_nav(guard_rig):
    r = guard_rig.run(["--tab", str(TAB_ID), "nav", "https://sub.evil.example.com/x"],
                      env={"BAG_DENY_DOMAINS": "evil.example.com"})
    assert r.returncode != 0
    assert "domain_blocked" in r.stdout
    assert _guard_browser_calls(guard_rig.frb_log) == []


def test_guard_allows_non_denied_nav(guard_rig):
    r = guard_rig.run(["--tab", str(TAB_ID), "nav", "https://good.example.com"],
                      env={"BAG_DENY_DOMAINS": "evil.example.com"})
    assert r.returncode == 0, r.stderr
    calls = _guard_browser_calls(guard_rig.frb_log)
    assert len(calls) == 1 and calls[0]["op"] == "nav"


def test_guard_allowlist_blocks_non_allowed(guard_rig):
    r = guard_rig.run(["--tab", str(TAB_ID), "nav", "https://other.example.com"],
                      env={"BAG_ALLOW_DOMAINS": "wikipedia.org"})
    assert r.returncode != 0
    assert "domain_blocked" in r.stdout


def test_guard_allowlist_permits_allowed(guard_rig):
    r = guard_rig.run(["--tab", str(TAB_ID), "nav", "https://en.wikipedia.org/wiki/X"],
                      env={"BAG_ALLOW_DOMAINS": "wikipedia.org"})
    assert r.returncode == 0, r.stderr


def test_guard_dry_run_intercepts_nav_and_eval(guard_rig):
    for op_args in (["nav", "https://good.example.com"], ["eval", "1+1"]):
        r = guard_rig.run(["--tab", str(TAB_ID), *op_args],
                          env={"BAG_DRY_RUN": "1"})
        assert r.returncode == 0, r.stderr
        assert "dryRun" in r.stdout
    assert _guard_browser_calls(guard_rig.frb_log) == [], \
        "dry-run must never exec the real browser"
    # The audit trail recorded the dry-run decisions.
    audit = guard_rig.audit.read_text()
    assert "dry_run" in audit


def test_guard_eval_referencing_denied_domain_blocked(guard_rig):
    r = guard_rig.run(["--tab", str(TAB_ID), "eval",
                       "location.href='https://evil.example.com'"],
                      env={"BAG_DENY_DOMAINS": "evil.example.com"})
    assert r.returncode != 0
    assert "eval_references_blocked" in r.stdout
