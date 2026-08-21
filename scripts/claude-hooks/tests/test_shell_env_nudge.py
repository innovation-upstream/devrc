#!/usr/bin/env python3
"""Unit tests for shell-env-nudge.analyze() + the hook's IO contract.
Run: python3 scripts/claude-hooks/tests/test_shell_env_nudge.py"""
import os, sys, json, atexit, shutil, tempfile, subprocess, importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(HERE, "..", "shell-env-nudge.py")

# 🔴 PRIVATE HOME, set BEFORE the hook is imported — same defect and same fix as
# test_search_tool_nudge.py. The hook's dedupe cache is `~/.cache/claude-shell-env-nudge/
# <session id>` and the session id below is a fixed literal, so the cache file was a
# host-global resource keyed by a constant: a second copy of this file removing it
# between this copy's two calls makes "io dedupe silent" fire, and a second copy WRITING
# it before this copy's first call makes "io first-fire emits" silent. That is not
# hypothetical — run-tests.sh runs this file, scripts/tests/test_run_tests_floors.py
# nests a second run-tests.sh that runs it again, and concurrent checkouts run their own
# gates. MEASURED pre-fix, two concurrent copies: 40/80 copies red.
SANDBOX_HOME = tempfile.mkdtemp(prefix="shell-env-nudge-home-")
os.environ["HOME"] = SANDBOX_HOME
atexit.register(shutil.rmtree, SANDBOX_HOME, True)
HOME = os.path.expanduser("~")
assert HOME == SANDBOX_HOME, "the sandbox HOME did not take effect"

spec = importlib.util.spec_from_file_location("shell_env_nudge", HOOK)
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
analyze = mod.analyze

fails = []
def check(name, got, want):
    if got != want:
        fails.append(f"{name}: got {got!r} want {want!r}")

def vars_of(cmd):
    return sorted(v for v, _ in analyze(cmd))

# --- repo roots via cd / assignment ---
check("cd datapacket", vars_of(f"cd {HOME}/workspace/civit/datapacket-talos && git status"), ["DATAPACKET"])
check("REPO= assignment", vars_of(f"REPO={HOME}/workspace/civit/civitai && cd $REPO"), ["CIVITAI"])
check("civitai-cli new handle", vars_of(f"CLI={HOME}/workspace/civit/civitai-cli"), ["CIVITAI_CLI"])
check("homelab cd", vars_of(f"cd {HOME}/workspace/homelab-talos"), ["HOMELAB"])

# --- kubeconfig absolute + relative + inline ---
check("export KUBECONFIG abs", vars_of(f"export KUBECONFIG={HOME}/workspace/civit/datapacket-talos/prod-kubeconfig"), ["KC_DPPROD"])
check("inline KUBECONFIG rel", vars_of("KUBECONFIG=./prod-kubeconfig kubectl get pods"), ["KC_DPPROD"])
check("homelab kubeconfig abs", vars_of(f"KUBECONFIG={HOME}/workspace/homelab-talos/homelab-kubeconfig kubectl get ns"), ["KC_HOMELAB"])

# --- must NOT fire ---
check("already using $DATAPACKET", vars_of("git -C $DATAPACKET status"), [])
check("already using $KC_DPPROD", vars_of("KUBECONFIG=$KC_DPPROD kubectl get pods"), [])
check("incidental path (ls, no cd/=)", vars_of(f"ls {HOME}/workspace/civit/datapacket-talos/clusters"), [])
check("unrelated command", vars_of("git status && npm test"), [])
check("unknown kubeconfig", vars_of("export KUBECONFIG=/tmp/random.yaml"), [])

# --- combined: both a repo and a kubeconfig in one call ---
check("combined repo+kc",
      vars_of(f"cd {HOME}/workspace/civit/datapacket-talos && KUBECONFIG=./prod-kubeconfig kubectl get po"),
      ["DATAPACKET", "KC_DPPROD"])

# --- IO contract: real subprocess, Bash event, emits additionalContext exactly once ---
def run(payload):
    p = subprocess.run([sys.executable, HOOK], input=json.dumps(payload),
                       capture_output=True, text=True)
    return p.returncode, p.stdout.strip()

sid = "test-session-io-DO-NOT-COLLIDE"
cache = os.path.join(HOME, ".cache", "claude-shell-env-nudge",
                     "".join(c if c.isalnum() or c in "_.-" else "_" for c in sid))
if os.path.exists(cache):
    os.remove(cache)

rc, out = run({"tool_name": "Bash", "session_id": sid,
               "tool_input": {"command": f"cd {HOME}/workspace/civit/datapacket-talos"}})
check("io rc", rc, 0)
emitted = bool(out) and "DATAPACKET" in out and "additionalContext" in out
check("io first-fire emits", emitted, True)

# second identical call in same session -> deduped, silent
rc2, out2 = run({"tool_name": "Bash", "session_id": sid,
                 "tool_input": {"command": f"cd {HOME}/workspace/civit/datapacket-talos"}})
check("io dedupe silent", out2, "")

# non-Bash tool -> silent, rc 0
rc3, out3 = run({"tool_name": "Read", "tool_input": {"file_path": "/x"}})
check("io non-bash silent", (rc3, out3), (0, ""))

# malformed stdin -> never crashes
p = subprocess.run([sys.executable, HOOK], input="not json", capture_output=True, text=True)
check("io malformed rc", p.returncode, 0)

if os.path.exists(cache):
    os.remove(cache)

if fails:
    print("FAIL:")
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("all shell-env-nudge tests passed")
