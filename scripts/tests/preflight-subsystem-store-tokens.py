#!/usr/bin/env python3
"""Pre-flight the criterion-10-step-2 token-file states against the DEPLOYED server.py.

Synthetic tokens only — the real secret never leaves the pod. What this proves is
LOADER SEMANTICS for the two shapes step 2 produces, plus that the loader can go red.
"""
import importlib.util
import os
import secrets
import sys
import tempfile
import traceback

# The tree must be the one EXTRACTED FROM THE RUNNING POD, never `main`:
#   kubectl -n subsystem-store exec $POD -- tar cf - -C /app scripts | tar xf - -C <dir>
#   kubectl -n subsystem-store exec $POD -- sha256sum /app/scripts/subsystem-store-api/server.py
# and confirm that sha equals the extracted copy's before believing anything below.
if len(sys.argv) < 2:
    sys.exit("usage: preflight-subsystem-store-tokens.py <dir containing scripts/ from the pod>")
_ROOT = os.path.join(os.path.abspath(sys.argv[1]), "scripts")
SERVER = os.path.join(_ROOT, "subsystem-store-api", "server.py")
sys.path.insert(0, os.path.join(_ROOT, "lib"))

# --- load the DEPLOYED module (sys.modules BEFORE exec_module; see handoff) -----------
spec = importlib.util.spec_from_file_location("deployed_store_server", SERVER)
assert spec is not None and spec.loader is not None
mod = importlib.util.module_from_spec(spec)
sys.modules["deployed_store_server"] = mod
spec.loader.exec_module(mod)
print(f"loaded deployed server.py  MIN_TOKEN_CHARS={mod.MIN_TOKEN_CHARS}")

SCOPES = ("civitai,civitai-app-model-benchmarking,civitai-app-sensei,civitai-app-starters,"
          "civitai-orchestration,civitai-spine-controller,claude-pool,cli,datapacket-talos,"
          "devrc,flipt-state,homelab-infra,homelab-talos,kubeclaw,storage-resolver")

TOK_A = secrets.token_urlsafe(43)   # 58 chars, stands in for the legacy bare token
TOK_B = secrets.token_urlsafe(43)   # 58 chars, stands in for zach's mapped token
assert len(TOK_A) == 58 and len(TOK_B) == 58


def run(name, body, *, expect):
    warnings = []
    with tempfile.NamedTemporaryFile("w", suffix=".token", delete=False) as fh:
        fh.write(body)
        path = fh.name
    try:
        recs = mod.load_tokens(path, {}, warn=warnings.append)
        got = "LOADED"
        detail = "; ".join(
            f"{r.identity}/{r.fingerprint}/scopes="
            + ("None(UNRESTRICTED)" if r.scopes is None else str(len(r.scopes)))
            for r in recs
        )
    except SystemExit as e:
        got, detail = "SystemExit", f"code={e.code}"
    except BaseException as e:  # noqa: BLE001 - we want the class name for the ladder
        got, detail = "RAISED", f"{type(e).__name__}: {e}"
    finally:
        os.unlink(path)

    ok = "PASS" if got == expect else "🔴 UNEXPECTED"
    print(f"\n[{ok}] {name}\n    outcome={got} (expected {expect})\n    {detail}")
    for w in warnings:
        print(f"    warn: {w[:200]}")
    return got == expect, warnings


results = []

# ---- the two states step 2 actually produces ----------------------------------------
results.append(run(
    "C1 rollback state — bare legacy row ALONE (what (b) restarts the pod on)",
    f"{TOK_A}\n", expect="LOADED"))

results.append(run(
    "C2 FINAL state — mapped zach row ALONE, no bare row (what (c) leaves behind)",
    f"{TOK_B} zach {SCOPES}\n", expect="LOADED"))

# ---- the five negative controls: the loader MUST be able to go red -------------------
results.append(run(
    "N1 space after a scope comma (parses as 4 fields)",
    f"{TOK_B} zach devrc, cli\n", expect="RAISED"))

results.append(run(
    "N2 mapped row claiming the reserved identity 'legacy'",
    f"{TOK_B} legacy {SCOPES}\n", expect="RAISED"))

results.append(run(
    "N3 guard 11 — the SAME token bare AND mapped",
    f"{TOK_A}\n{TOK_A} zach {SCOPES}\n", expect="RAISED"))

results.append(run(
    "N4 guard 12 — two rows claiming one identity",
    f"{TOK_A} zach {SCOPES}\n{TOK_B} zach {SCOPES}\n", expect="RAISED"))

results.append(run(
    "N5 short token (< MIN_TOKEN_CHARS)",
    f"{'x' * 20} zach {SCOPES}\n", expect="RAISED"))

results.append(run(
    "N6 empty file (no rows at all)",
    "\n", expect="RAISED"))

passed = sum(1 for ok, _ in results if ok)
print(f"\nRESULT: {passed}/{len(results)} as expected")
sys.exit(0 if passed == len(results) else 1)
