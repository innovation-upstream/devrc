r"""🔴 ENGINE-EXERCISING tests for the opencode configuration.

WHY THIS FILE EXISTS (it is the complement of test_opencode_config.py, not a
duplicate of it).

  test_opencode_config.py is entirely STATIC. It parses opencode.jsonc, parses
  the agent frontmatter, greps nix/home.nix, and REIMPLEMENTS opencode's
  resolver in Python (`wildcard_match` + `effective_bash_action`). Its only
  subprocess is `nix-instantiate --eval`. It never runs `opencode`.

  That is a real gap, and it is exactly the gap the version pin exists to cover:
  a config whose keys are unchanged can have its RESOLVED MEANING changed by the
  binary underneath it. opencode.jsonc's header documents a large set of
  behaviours annotated "measured on v1.18.16 — do not re-derive" — last-match-
  wins ordering, hidden agents inheriting the global permission block, the exact
  tool set. A static test cannot see any of those change. This file runs the
  real engine and checks them.

HOW. `opencode debug agent <name> --pure` prints the AUTHORITATIVE flat, ordered
permission array and the resolved tool map as JSON. It is read-only — it does
NOT execute anything.

  🔴 Deliberately NOT `--tool bash --params '{"command": ...}'`, even though that
  performs a genuine permission check: for anything that resolves allow it also
  RUNS the command. A test matrix containing `rm -rf /` must never be one
  permission regression away from executing it. `debug agent` gives the same
  ground truth with no execution path at all.

  Each test seeds a THROWAWAY OPENCODE_CONFIG_DIR from the REPO's
  scripts/opencode/{opencode.jsonc,agent/} and runs with cwd in an empty temp
  project. So this asserts about the source of truth in this repo, never about
  whatever happens to be deployed in ~/.config/opencode — a live probe against a
  deployed artifact is evidence about the deploy, not about the commit.

  MEASURED 2026-08-02: this needs no network, materialises no node_modules
  (62 MB, and offline it HANGS — see scripts/browser-bridge/browser-agent), and
  takes ~1 s per invocation. It was verified to behave identically under a
  scrubbed env carrying only PATH/HOME/TMPDIR, which is why it can be gated in
  the nix sandbox rather than deferred to a dev host.

🔴 NO SKIPS. `opencode` is in flake.nix `checks.pytests` nativeBuildInputs and in
run-tests.sh's REQUIRED_TOOLS, so its absence is a NAMED PRECONDITION FAILURE up
front, not a silent skip deep in the run. `_opencode_bin()` below calls
pytest.fail(), never pytest.skip() — the same choice, for the same reason, as
`nix_eval()` in test_opencode_config.py. This file adds 22 tests and 0 skips
(MEASURED 2026-08-11, `pytest -q`: "22 passed", ~7 s, in BOTH tiers — the
dev-host nix-shell and `nix build .#checks.x86_64-linux.pytests`).

🔴 STDOUT IS READ FROM A FILE, NEVER A PIPE. opencode loses the tail of a large
stdout write when stdout is a pipe, silently and with exit 0. That cost this file
17 failures on every dev host while the nix check stayed green. The full
measurement, the ruled-out rival mechanisms and the pins are at `_run_opencode`
below — read that before touching any subprocess call here.

🔴 WHAT THIS FILE CANNOT SEE. Its conformance tests compare the ENGINE against
the MODEL, and both read the same config — so a bad CONFIG makes them agree and
stay green. That is test_opencode_config.py's job. This file's job is the
complement: a bad or drifted BINARY under an unchanged config. Do not read a
green here as "the config is fine".

    run:  python -m pytest scripts/tests/test_opencode_engine.py -q
"""

import ast
import json
import re
import os
import shutil
import subprocess
import sys
import tempfile
from functools import lru_cache
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# One place owns the shebang of every runtime-written stub — see its docstring
# for the two-tier trap that made this a shared helper.
from testlib.mockbin import write_exec  # noqa: E402

# The resolver MODEL and the command matrix live in one place — the sibling
# file. Re-declaring either here would give us two copies to drift apart, and
# the point of this file is to check the model against reality, not to fork it.
import test_opencode_config as model  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
OC_DIR = ROOT / "scripts" / "opencode"
TOOLS_NIX = ROOT / "nix" / "pkgs" / "tools" / "default.nix"

# 🔴 THE PIN. Every "measured on v1.18.16" claim in opencode.jsonc's header, in
# scripts/opencode/README.md and in test_opencode_config.py's docstrings is keyed
# to this exact version. It is pinned declaratively by nix/pkgs/tools/default.nix
# resolving `pkgs.opencode` out of flake.lock's nixpkgs.
#
# 🔴 RE-DERIVED 1.18.4 -> 1.18.16 (2026-08-13), not merely re-spelled. What was
# actually measured, and how, so a future bump can repeat it rather than trust it:
#
#   * `debug agent <a> --pure` dumped for ALL SEVEN agents (the 4 primaries plus
#     the 3 hidden ones) under BOTH binaries, from a config dir seeded out of this
#     repo. Compared order-insensitively: IDENTICAL — permissions as a set, the
#     resolved tool map, and the model, on every agent.
#   * This whole file run against the OLD binary with the NEW pin: 1 failed
#     (exactly the version assertion below), 21 passed. So every other engine
#     claim holds on both, and the harness is shown to DISCRIMINATE rather than
#     to be green by default.
#
# 🔴 THE DUMPS ARE NOT DETERMINISTIC, and a naive diff of them is worthless. The
# `external_directory` rules are generated by walking ~/.claude/skills and
# ~/.config/opencode/skills, so their ORDER follows readdir: two runs of the SAME
# binary differed on 434 lines. That noise reads exactly like a resolver-ordering
# change. Canonicalise (sort every array) before comparing, and validate the
# canonicalisation with a same-binary control — two runs must collapse to
# identical — before believing a cross-version verdict. Ordering SEMANTICS are
# still pinned, behaviourally, by the engine-vs-model conformance tests below;
# they are what survives sorting, and they pass on both binaries.
#
# NOT covered by that re-derivation, and deliberately still keyed to 1.18.4: the
# hook-behaviour claims (`permission.ask` never fires, `tool.execute.before` does)
# in scripts/opencode/README.md and scripts/opencode/plugin/guard.js. Those need
# a running hook to observe and were not re-measured here.
PINNED_VERSION = "1.18.16"

# MEASURED via `opencode debug agent nav --pure` at 1.18.16. This is the cost AND
# blast-radius pin that test_opencode_config.py's `test_nav_is_kept_lean` only
# asserts about the CONFIG KEYS; here it is read off the engine's resolved tool
# map. `skill` alone injects the ~3,730-token catalogue on every request.
NAV_EXPECTED_TOOLS = {"glob", "grep", "invalid", "read"}

# The engine name of the default primary agent, and the model's spelling for
# "global config only, no agent block".
ENGINE_AGENTS = [("build", None), ("nav", "nav"), ("k8s", "k8s"),
                 ("review", "review")]

HIDDEN_AGENTS = ["title", "summary", "compaction"]


# --------------------------------------------------------------------------- #
# harness
# --------------------------------------------------------------------------- #
def _opencode_bin() -> str:
    """🔴 fail(), NOT skip().

    A skip here would report safety while testing nothing — and this is the file
    whose entire job is to notice that the binary changed. `opencode` is asserted
    on PATH by run-tests.sh's REQUIRED_TOOLS, so in every tier that gates a merge
    this cannot fire; if it does, the caller's inputs are wrong, not the test.
    """
    exe = shutil.which("opencode")
    if not exe:
        pytest.fail(
            "opencode is not on PATH. This file exercises the REAL engine and "
            "must not be skipped — a skip is how a resolver-semantics change "
            "ships unnoticed. Add pkgs.opencode to flake.nix "
            "checks.pytests.nativeBuildInputs (it is already in "
            "run-tests.sh REQUIRED_TOOLS), or run under `nix-shell -p opencode`."
        )
    return exe


def _seed_config_dir(tmp: Path, mutate=None) -> Path:
    """A throwaway OPENCODE_CONFIG_DIR seeded from THIS REPO's sources.

    `mutate`, if given, takes the parsed config dict and returns the dict to
    write instead — used by the harness's positive control below.
    """
    cfg = tmp / "cfg"
    cfg.mkdir(parents=True)
    if mutate is None:
        shutil.copy(OC_DIR / "opencode.jsonc", cfg / "opencode.jsonc")
    else:
        # Plain JSON is valid JSONC, so writing the mutated dict back out is a
        # faithful substitute for the commented source.
        data = mutate(model.load_config())
        (cfg / "opencode.jsonc").write_text(json.dumps(data, indent=2))
    shutil.copytree(OC_DIR / "agent", cfg / "agent")
    return cfg


# 🔴 THE ONE PLACE THIS FILE READS opencode's STDOUT.
#
# CAPTURE TO A FILE — NEVER A PIPE. opencode (a Bun single-file binary) does not
# reliably drain stdout before exiting when stdout is a PIPE, so a
# `capture_output=True` / `subprocess.PIPE` read returns a TRUNCATED PREFIX with
# **returncode 0 and an EMPTY stderr** — every signal that would let you notice
# says the run succeeded. The only symptom is a JSONDecodeError halfway through
# the document, which reads as "opencode's output format changed".
#
# MEASURED 2026-08-11 on the workbench, opencode 1.18.4
# (/nix/store/64n428w29sra24db9d6h6clzdh0vy9hk-opencode-1.18.4 — the SAME store
# path the nix check uses), for all 7 agents this file resolves:
#     stdout=PIPE  -> exactly 8192 B every time, rc=0, stderr 0 B, BAD JSON
#     stdout=FILE  -> 9214 / 11733 / 12927 / 16960 / 11475 / 9912 / 10086 B,
#                     rc=0, stderr 0 B, VALID JSON
# 20/20 consecutive piped runs of `debug agent build` returned exactly 8192 B.
# It is the WRITER, not the reader: a shell `… | wc -c` truncates identically,
# and a raw `os.read()` loop truncates identically, while a SOCKETPAIR (also not
# a tty, also not seekable) delivers all 9214 B — and so does the same command
# slowed down under strace. Not pipe capacity either: the default pipe holds
# 65536 B, eight times what arrives.
#
# This is the SAME defect scripts/browser-bridge/browser-agent already fixed for
# its tool-set gate (see its "CAPTURE TO A FILE — NEVER `$(...)`" block, and
# scripts/browser-bridge/tests/test_browser_agent.py::
# test_gate_reads_from_a_file_not_a_pipe). That guard reads only that ONE
# wrapper's source, so it was structurally unable to see this file reintroduce
# the identical bug through Python's `subprocess.PIPE` — the seam nobody owned.
# The pin here is behavioural (see test_debug_dump_survives_a_pipe_truncating_
# opencode below) plus the call-site ledger, so it cannot be satisfied by
# spelling.
#
# 🔴 WHY THE MERGE GATE COULD NOT SEE IT. Inside the nix sandbox the same store
# path delivers the whole document through a pipe (5/5, MEASURED via a
# runCommand probe). So `nix build .#checks.x86_64-linux.pytests` was GREEN on
# the exact tree where the dev-host tier had 17 failures. Do not "fix" a repeat
# of this by re-measuring in the sandbox — the sandbox is the tier that is blind
# to it.
def _run_opencode(exe: str, args: list, tmp: Path, env=None, cwd=None):
    """🔴 THE ONLY `subprocess.run` IN THIS MODULE. Returns (rc, stdout, stderr).

    stdout AND stderr go to real FILES. Pinned by
    test_this_file_never_pipe_captures_opencode below, which asserts this is the
    single call site — so the rule lives in one place and a second reader cannot
    quietly reintroduce the race.
    """
    out_path = tmp / "oc-stdout"
    err_path = tmp / "oc-stderr"
    with open(out_path, "wb") as fout, open(err_path, "wb") as ferr:
        p = subprocess.run([exe, *args], stdout=fout, stderr=ferr, env=env,
                           cwd=cwd, timeout=180)
    return (p.returncode,
            out_path.read_text(errors="replace"),
            err_path.read_text(errors="replace"))


def _capture_debug_agent(exe: str, name: str, cfg: Path, proj: Path,
                         home: Path, tmp: Path) -> dict:
    """Run `opencode debug agent <name> --pure`, capturing stdout to a FILE."""
    # A MINIMAL env, not os.environ: the operator's real ~/.config/opencode,
    # their OPENCODE_* overrides and their credentials must not be able to
    # change this verdict. Verified to work under exactly this env.
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(home),
        "TMPDIR": str(tmp),
        "OPENCODE_CONFIG_DIR": str(cfg),
        "OPENCODE_DISABLE_AUTOUPDATE": "true",
        "OPENCODE_DISABLE_MODELS_FETCH": "true",
    }
    rc, out, err = _run_opencode(
        exe, ["debug", "agent", name, "--pure"], tmp, env=env, cwd=str(proj))

    assert rc == 0, (
        f"`opencode debug agent {name} --pure` exited {rc}\n"
        f"STDERR:\n{err[:2000]}"
    )
    assert out.strip(), (
        f"`opencode debug agent {name}` produced NO stdout. Do not read this "
        f"as 'nothing to check' — the output format is a dependency this "
        f"test did not pin, and an empty parse is indistinguishable from a "
        f"changed CLI. STDERR:\n{err[:2000]}"
    )
    try:
        return json.loads(out)
    except json.JSONDecodeError as exc:
        # Never let a raw JSONDecodeError out of here: for two tiers running it
        # meant "opencode's output format changed" when it meant "the document
        # was cut short". Say which, and say what was NOT the cause.
        raise AssertionError(
            f"`opencode debug agent {name} --pure` returned {len(out)} bytes "
            f"that do not parse as JSON ({exc}).\n"
            f"  returncode={rc}, stderr={len(err)} bytes — so the process was "
            f"NOT killed and wrote nothing to stderr.\n"
            f"  FIRST rule out the flush race documented above: this harness is "
            f"supposed to capture stdout to a real FILE, and "
            f"test_this_file_never_pipe_captures_opencode is what proves it "
            f"still does. If that test is ALSO red, believe it and fix the "
            f"capture — the format did not change, the document was cut short.\n"
            f"  Tail of what arrived:\n{out[-400:]!r}"
        ) from None


def _run_debug_agent(name: str, mutate=None, exe: str | None = None) -> dict:
    exe = exe or _opencode_bin()
    tmp = Path(tempfile.mkdtemp(prefix="oc-engine-test-"))
    try:
        cfg = _seed_config_dir(tmp, mutate)
        proj = tmp / "proj"
        proj.mkdir()
        home = tmp / "home"
        home.mkdir()
        return _capture_debug_agent(exe, name, cfg, proj, home, tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@lru_cache(maxsize=None)
def engine_agent(name: str) -> str:
    """Cached raw JSON for the UNMUTATED config (each call is a ~1 s process)."""
    return json.dumps(_run_debug_agent(name))


def engine_bash_rules(name: str, mutate=None) -> list:
    """The engine's flat, ordered (pattern, action) list for the bash tool.

    THIS IS THE AUTHORITY. opencode.jsonc's header says so explicitly: "`opencode
    debug agent <name>` prints that array — it is the authority, not this file."
    """
    d = json.loads(engine_agent(name)) if mutate is None else _run_debug_agent(name, mutate)
    return [(r["pattern"], r["action"]) for r in d["permission"]
            if r.get("permission") == "bash"]


def resolve(rules, command: str) -> str:
    """LAST-match-wins over the engine's own array, using the model's matcher.

    Pairing the ENGINE's ruleset with the MODEL's matcher is deliberate: it
    isolates ruleset CONSTRUCTION (merge order, where an agent block is appended,
    whether a new built-in rule got inserted after ours) from glob MATCHING,
    which test_opencode_config.py pins separately row-by-row.
    """
    action = "ask"
    for pattern, act in rules:
        if model.wildcard_match(command, pattern):
            action = act
    return action


def _dedupe_consecutive(rules: list) -> list:
    out = []
    for r in rules:
        if not out or out[-1] != r:
            out.append(r)
    return out


# --------------------------------------------------------------------------- #
# 🔴 harness self-validation — the POSITIVE control
#
# Everything below reports a reassuring result: "0 mismatches", "engine agrees".
# A zero is indistinguishable from a harness wired to nothing, so before reading
# any of those verdicts, feed this harness a config it MUST reject and watch the
# number move.
# --------------------------------------------------------------------------- #
def test_harness_observes_a_deliberately_broken_config():
    """🔴 POSITIVE CONTROL — report the pair, never the zero alone.

    Append a trailing `"git *": "allow"` to the bash block. This is the exact
    mutation that beat the OLD key-order-only test (see test_opencode_config.py's
    module docstring: "82 passed"), and under last-match-wins it resurrects every
    git deny above it.

    Assert BOTH halves in one test so the pair is reported together:
      * unmutated config  -> `git stash` resolves 'deny'   (the reassuring zero)
      * mutated config    -> `git stash` resolves 'allow'  (the number moving)

    If the mutated half ever reports 'deny', this harness cannot see a broken
    config and every green below is a fact about the harness, not the engine.
    """
    clean = resolve(engine_bash_rules("build"), "git stash")

    def add_trailing_git_allow(cfg):
        cfg["permission"]["bash"]["git *"] = "allow"
        return cfg

    broken = resolve(
        engine_bash_rules("build", mutate=add_trailing_git_allow), "git stash"
    )

    assert (clean, broken) == ("deny", "allow"), (
        f"positive control FAILED: clean={clean!r}, broken={broken!r}, expected "
        f"('deny', 'allow'). Either the engine is no longer LAST-MATCH-WINS (in "
        f"which case opencode.jsonc's entire ordering rationale is void), or "
        f"this harness is not actually reaching the engine — and every other "
        f"assertion in this file is then meaningless."
    )


def test_harness_reaches_the_engine_and_not_the_deployed_config():
    """Negative control on the SEED: the array must come from this repo.

    A harness that silently fell back to ~/.config/opencode would still look
    green — and would be testing the deployed artifact, not the commit. Removing
    a rule from the seeded config must change the engine's answer.
    """
    def drop_the_stash_denies(cfg):
        cfg["permission"]["bash"] = {
            k: v for k, v in cfg["permission"]["bash"].items()
            if "stash" not in k
        }
        return cfg

    got = resolve(engine_bash_rules("build", mutate=drop_the_stash_denies),
                  "git stash")
    assert got != "deny", (
        "dropping every `*stash*` rule from the SEEDED config did not change the "
        "engine's verdict for `git stash` — the harness is reading some other "
        "config (most likely the deployed ~/.config/opencode), so nothing here "
        "is evidence about this repo."
    )


# --------------------------------------------------------------------------- #
# 🔴 the flush-race pin — a PIPE-truncating opencode must not be able to make
# this harness read a partial document
#
# The fixture below reproduces the measured defect EXACTLY: full document to a
# regular file, truncated prefix to a pipe, rc 0 and empty stderr in BOTH cases.
# It is built from THIS REPO's real bash permission block, not a textbook blob,
# so the cut lands mid-structure the way the real one does.
# --------------------------------------------------------------------------- #
PIPE_CUT = 8192   # the prefix the real binary delivered on 20/20 piped runs


def _realistic_debug_dump() -> str:
    """The REAL `debug agent build --pure` document, re-indented as the CLI emits it.

    Not a hand-written blob: a scanner or a fixture built from a tidy synthetic
    example is exactly what sails past its own canonical case. This is the
    engine's own output for the agent these tests actually resolve, so the
    PIPE_CUT lands mid-structure the way the measured one did (the real cut fell
    inside a `"pattern"` string, producing "Unterminated string").
    """
    return json.dumps(json.loads(engine_agent("build")), indent=2)


def _pipe_truncating_opencode(tmp: Path) -> str:
    """Write a stand-in `opencode` that mimics the flush race, return its path.

    🔴 The executable is written by `testlib.mockbin.write_exec`, which owns the
    shebang, and the logic sits in a plain .py file it execs. A hand-written
    `#!/usr/bin/env …` (or any runtime-written shebang) is dead in the nix
    sandbox — the repo-wide scanner in test_runtime_shebangs.py caught exactly
    that in the first draft of this fixture, in the tier a per-file dev-host run
    cannot see.
    """
    doc_path = tmp / "dump.json"
    doc = _realistic_debug_dump()
    # If the repo's bash block ever shrinks below the cut the fixture would stop
    # reproducing anything and every assertion below would pass vacuously.
    assert len(doc.encode()) > PIPE_CUT + 512, (
        f"the fixture document is only {len(doc.encode())} bytes, which a "
        f"{PIPE_CUT}-byte cut barely truncates — this fixture can no longer "
        f"reproduce the race it exists for. Pad it, do not lower PIPE_CUT."
    )
    doc_path.write_text(doc)

    # The behaviour, as a plain module — no shebang, never executed directly.
    logic = tmp / "fake_opencode_logic.py"
    logic.write_text(
        "import os, stat, sys\n"
        f"payload = open({str(doc_path)!r}, 'rb').read()\n"
        "# The real binary loses the tail only when fd 1 is a FIFO; to a regular\n"
        "# file it writes everything. Exit 0 and say nothing on stderr either\n"
        "# way — that silence is the whole reason the bug was hard to see.\n"
        "if stat.S_ISFIFO(os.fstat(1).st_mode):\n"
        f"    payload = payload[:{PIPE_CUT}]\n"
        "os.write(1, payload)\n"
        "sys.exit(0)\n"
    )
    fake = write_exec(tmp / "fake-opencode",
                      f'exec {sys.executable} {logic} "$@"\n')
    return str(fake)


def test_the_pipe_truncating_fixture_really_truncates():
    """🔴 NEGATIVE CONTROL on the fixture, reported as a PAIR.

    Before believing the regression pin below, watch the fixture produce the bad
    case — and confirm it carries the real defect's full signature, because that
    signature is why nothing upstream catches it: rc 0, empty stderr, non-empty
    stdout, unparseable JSON. A fixture that failed loudly instead would make the
    pin below green for the wrong reason.
    """
    with tempfile.TemporaryDirectory(prefix="oc-fixture-") as td:
        tmp = Path(td)
        fake = _pipe_truncating_opencode(tmp)
        full = (tmp / "dump.json").read_bytes()

        piped = subprocess.run([fake], capture_output=True, timeout=60)
        to_file = tmp / "viafile"
        with open(to_file, "wb") as fh:
            filed = subprocess.run([fake], stdout=fh, stderr=subprocess.DEVNULL,
                                   timeout=60)
        via_file = to_file.read_bytes()

    assert (piped.returncode, len(piped.stderr)) == (0, 0), (
        "the fixture must reproduce the SILENT shape of the defect — a non-zero "
        "exit or a stderr message would be caught by the harness's existing "
        f"asserts, got rc={piped.returncode} stderr={piped.stderr[:200]!r}"
    )
    assert len(piped.stdout) == PIPE_CUT < len(full), (
        f"piped capture returned {len(piped.stdout)} B of a {len(full)} B "
        f"document; expected exactly {PIPE_CUT}"
    )
    with pytest.raises(json.JSONDecodeError):
        json.loads(piped.stdout)
    # …and the same fixture through a FILE is whole. Report the pair, never the
    # truncation alone: "it truncated" and "a file gets everything" are two
    # claims, and the fix rests on the second.
    assert filed.returncode == 0 and via_file == full, (
        f"the fixture must deliver the WHOLE {len(full)} B document to a regular "
        f"file (got {len(via_file)} B, rc={filed.returncode}) — otherwise it is "
        f"not modelling the defect, it is just broken."
    )


def test_debug_dump_survives_a_pipe_truncating_opencode():
    """🔴 THE REGRESSION PIN for the 17 dev-host failures.

    RED before this change (`capture_output=True` -> JSONDecodeError on every
    call), GREEN after. This exercises the REAL harness function the 19 engine
    tests go through, so it cannot be satisfied by a comment or a spelling.
    """
    with tempfile.TemporaryDirectory(prefix="oc-pin-") as td:
        tmp = Path(td)
        fake = _pipe_truncating_opencode(tmp)
        expected = json.loads((tmp / "dump.json").read_text())
        got = _run_debug_agent("build", exe=fake)

    assert got == expected, (
        "the harness did not read the WHOLE document from an opencode that "
        "truncates on a pipe. That is the flush race: capture stdout to a real "
        "file, never `capture_output=True` / `subprocess.PIPE`."
    )


def test_this_file_never_pipe_captures_opencode():
    """The CALL-SITE LEDGER — fails when the set of readers grows OR shrinks.

    The behavioural pin above covers the one helper it calls. This covers the
    shape that pin structurally cannot see: a SECOND reader added later that
    pipes. `scripts/browser-bridge/tests/test_browser_agent.py::
    test_gate_source_has_no_command_substitution_capture` is the same ledger for
    the browser-agent wrapper — the repo's other `opencode debug` consumer, and
    the only one that existed when this defect was first diagnosed.
    """
    # Parsed with `ast`, NOT grepped. A textual check is satisfied or defeated by
    # spelling: it counts its own error messages, misses a `capture_output=True`
    # that sits on a continuation line (which is exactly where the real one was),
    # and cannot tell code from a docstring.
    tree = ast.parse(Path(__file__).read_text())
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute) and n.func.attr == "run"
             and isinstance(n.func.value, ast.Name) and n.func.value.id == "subprocess"]

    # Three, and the ledger fails if that GROWS or SHRINKS: `_run_opencode`
    # (files), plus the fixture's two deliberate calls against the FAKE binary —
    # one of which pipes on purpose, to prove the fixture reproduces the defect.
    described = [f"line {n.lineno}: {ast.unparse(n)[:90]}" for n in calls]
    assert len(calls) == 3, (
        f"expected exactly 3 `subprocess.run` calls in this file; found "
        f"{len(calls)}:\n" + "\n".join(f"  {d}" for d in described)
        + "\n  A new reader of the REAL binary must go through `_run_opencode`, "
          "which captures to a FILE — see the flush-race block above."
    )

    for n in calls:
        kwargs = {kw.arg for kw in n.keywords}
        target = ast.unparse(n.args[0]) if n.args else ""
        against_fake = "fake" in target
        piped = "capture_output" in kwargs or any(
            ast.unparse(kw.value) == "subprocess.PIPE" for kw in n.keywords)
        if against_fake:
            continue          # the fixture may pipe; that IS the negative control
        assert not piped, (
            f"line {n.lineno} pipe-captures the REAL opencode "
            f"({ast.unparse(n)[:120]}). That is the flush race: rc 0, empty "
            f"stderr, a truncated prefix. Capture to a file via `_run_opencode`."
        )
        assert "stdout" in kwargs, (
            f"line {n.lineno} runs the real opencode without redirecting stdout "
            f"to a file: {ast.unparse(n)[:120]}"
        )


# --------------------------------------------------------------------------- #
# 1. 🔴 the version pin
# --------------------------------------------------------------------------- #
def test_engine_is_the_version_every_measurement_is_keyed_to():
    """🔴 The whole point of making opencode declarative.

    ENVIRONMENT-DEPENDENT BY DESIGN, and that is the feature: in the nix sandbox
    PATH carries flake.lock's pinned `pkgs.opencode`, so this pins CI to the
    measured version. On a dev host it reads whatever opencode is on PATH — so
    it goes RED on a host still carrying the old imperative
    `nix profile install nixpkgs#opencode` entry (MEASURED 2026-08-02: workbench
    1.18.9 vs laptop 1.18.4, drifting independently). That red is the intended
    signal, and it clears with the documented one-time per-host prerequisite:

        nix profile remove opencode   # then home-manager switch / ship.sh
    """
    exe = _opencode_bin()
    # Through `_run_opencode` like everything else. `--version` is ~7 bytes and
    # would survive a pipe today — but "small enough to be safe" is a claim about
    # the CURRENT output, and routing it here means this module has exactly ONE
    # rule about reading opencode's stdout instead of one rule and an exception.
    with tempfile.TemporaryDirectory(prefix="oc-version-") as td:
        rc, out, _err = _run_opencode(exe, ["--version"], Path(td))
    assert rc == 0, f"`opencode --version` exited {rc}"
    got = out.strip()
    assert got == PINNED_VERSION, (
        f"opencode on PATH is {got!r}, but every 'measured on v{PINNED_VERSION}' "
        f"claim in scripts/opencode/opencode.jsonc, scripts/opencode/README.md "
        f"and scripts/tests/test_opencode_config.py is keyed to "
        f"{PINNED_VERSION!r}.\n"
        f"  * On a DEV HOST this usually means the old imperative profile entry "
        f"is still winning PATH. Fix: `nix profile remove opencode`, then "
        f"`home-manager switch --flake ~/workspace/devrc --impure`.\n"
        f"  * If flake.lock genuinely moved opencode, do NOT just bump "
        f"PINNED_VERSION: re-derive the header's measurements against the new "
        f"binary first (the rest of this file tells you which ones changed), "
        f"then update both together."
    )


# --------------------------------------------------------------------------- #
# 🔴 THE PIN'S SURFACE — every file that spells a version-keyed claim.
#
# WHY THIS EXISTS. The 1.18.4 -> 1.18.16 bump updated 23 claim lines and MISSED
# four files, because the author grepped `scripts/opencode/` and the two test
# modules and stopped there. The misses were not cosmetic: nix/pkgs/tools/
# default.nix and flake.nix are the files the pin names as its OWN mechanism,
# and both asserted in the PRESENT TENSE that `pkgs.opencode` *is* 1.18.4 at a
# nixpkgs rev the lock had already left behind. claude/opencode-addendum.md is
# concatenated into ~/.config/opencode/AGENTS.md, so the agent was being told
# the old version while the repo said the new one. An adversarial reviewer found
# all four; nothing in the suite could.
#
# `test_engine_is_the_version_every_measurement_is_keyed_to` pins the BINARY
# against PINNED_VERSION. It cannot see a doc that disagrees with either. This
# closes that gap: within the pin's surface, a version literal is either
# PINNED_VERSION or an ENUMERATED historical exception.
#
# 🔴 The allowlist is a LEDGER, not a filter: a stale entry is a failure too
# (see the second half of the test). It is keyed on version-FREE snippets on
# purpose — an allowlist quoting `1.18.4` would match itself when this very file
# is scanned.
PIN_SURFACE = (
    "scripts/opencode/README.md",
    "scripts/opencode/opencode.jsonc",
    "scripts/opencode/agent/k8s.md",
    "scripts/opencode/agent/review.md",
    "scripts/opencode/plugin/guard.js",
    "scripts/tests/test_opencode_config.py",
    "scripts/tests/test_opencode_engine.py",
    "nix/pkgs/tools/default.nix",
    "nix/home.nix",
    "flake.nix",
    "claude/opencode-addendum.md",
)

# 🔴 SCOPED TO opencode's VERSION SERIES, and it has to be. `nix/home.nix` and
# `flake.nix` also carry dunst 1.13.2 and playwright 1.57.0/1.61.1; a bare
# `1\.\d+\.\d+` sweep flags all of them, and an allowlist full of unrelated
# packages would go red on every unrelated bump — a gate nobody can keep green
# gets switched off. Note the lookbehind admits a leading `v`: `\b` does NOT
# match between `v` and `1` (both word characters), so an anchored pattern
# silently skips every `v1.18.4` — which is how the FIRST draft of this test
# passed while `README.md:229` was stale.
#
# WHEN opencode CROSSES A MINOR (1.19.x), append the old series here rather than
# replacing it — otherwise every surviving 1.18.x claim becomes invisible on the
# same day the pin stops matching it.
OPENCODE_SERIES = ("1.18",)
_VERSION_RE = re.compile(
    r"(?<![\d.])(?:" + "|".join(re.escape(s) + r"\.\d+" for s in OPENCODE_SERIES) + r")(?![\d.])"
)

# (path, snippet on the SAME LINE as the version, why it does not track the pin)
# 🔴 Same line, not same paragraph: a snippet one line above its version matches
# nothing and reads as an orphan. Snippets carry no version literal of their own,
# or they would match themselves when this file is scanned.
HISTORICAL_VERSION_CLAIMS = (
    ("nix/pkgs/tools/default.nix", "which drifted: MEASURED",
     "the per-host drift that motivated pinning; a dated fact"),
    ("nix/pkgs/tools/default.nix", "when this pin was introduced",
     "states what the pin resolved to when it was created"),
    ("nix/home.nix", "and not `permission.ask`: MEASURED on",
     "hook-firing claim — needs a running hook to observe, not re-derived"),
    ("nix/home.nix", "opencode has NO `env` config key (verified on v",
     "plugin/env claim — not re-derived at the new version"),
    ("nix/home.nix", "CORRECTION (measured 2026-08-02, opencode",
     "dated correction record"),
    ("nix/home.nix", 'DEPLOYMENT CONSTRAINTS (measured on v',
     "plugin-loading deployment claim — not re-derived"),
    ("nix/home.nix", "before removing it, against a",
     "dated record of a removal decision"),
    ("nix/home.nix", "reads BOTH (measured: it",
     "plugin-glob claim — not re-derived"),
    ("scripts/opencode/plugin/guard.js", ", this host, 2026-08-02 — and STILL",
     "hook-firing claim — needs a running hook to observe"),
    ("scripts/opencode/plugin/guard.js", "see PINNED_VERSION in scripts/tests",
     "the in-place marker saying that claim is deliberately not re-derived"),
    ("scripts/opencode/README.md", ", this host, 2026-08-02. \U0001f534 Still",
     "heads the hook-behaviour block; deliberately not re-derived"),
    ("scripts/opencode/README.md", 'startup files" is **FALSE on this host**',
     "zsh-startup-files claim — not re-derived"),
    ("scripts/tests/test_opencode_engine.py", "RE-DERIVED",
     "names the transition itself"),
    ("scripts/tests/test_opencode_engine.py", "deliberately still keyed to",
     "the pin's own note naming the not-re-derived subset"),
    ("scripts/tests/test_opencode_engine.py", "MEASURED 2026-08-11 on the workbench, opencode",
     "the pipe-truncation incident record; history"),
    ("scripts/tests/test_opencode_engine.py", "the SAME store",
     "the store path the incident was measured on"),
    ("scripts/tests/test_opencode_engine.py", "drifting independently",
     "the cross-host drift example in the version test's docstring"),
    ("scripts/tests/test_opencode_engine.py", "claim lines and MISSED",
     "this test's own account of the bump that motivated it"),
    ("scripts/tests/test_opencode_engine.py", "in the PRESENT TENSE",
     "same account"),
    ("scripts/tests/test_opencode_engine.py", "an allowlist quoting",
     "the note explaining why snippets carry no version literal"),
    ("scripts/tests/test_opencode_engine.py", "silently skips every",
     "the worked example of the `v`-prefix regex trap"),
    ("scripts/tests/test_opencode_engine.py", "The binary assertion cannot see this class",
     "this test's own docstring, naming the bump it was written for"),
)


def test_no_file_in_the_pins_surface_still_claims_an_OLD_version():
    """\U0001f534 A doc that names a superseded version is a FALSE CLAIM, not a typo.

    The binary assertion cannot see this class at all, and the 1.18.4 -> 1.18.16
    bump shipped four instances of it — two in the very files that implement the
    pin, one in a file concatenated into the agent's own AGENTS.md.
    """
    stale = []
    for rel in PIN_SURFACE:
        path = ROOT / rel
        assert path.exists(), f"{rel} is in PIN_SURFACE but does not exist — the ledger rotted"
        allowed = [s for p, s, _ in HISTORICAL_VERSION_CLAIMS if p == rel]
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            hits = [v for v in _VERSION_RE.findall(line) if v != PINNED_VERSION]
            if hits and not any(snip in line for snip in allowed):
                stale.append(f"  {rel}:{n}: {hits} :: {line.strip()[:100]}")
    assert not stale, (
        f"{len(stale)} version claim(s) in the pin's surface disagree with "
        f"PINNED_VERSION={PINNED_VERSION!r} and are not enumerated as historical:\n"
        + "\n".join(stale)
        + "\n\nEither re-derive the claim against the pinned binary and update the "
          "number, or add it to HISTORICAL_VERSION_CLAIMS with the reason it does "
          "not track the pin. Do NOT allowlist it just to go green — a claim keyed "
          "to a version nothing runs is exactly what this test exists to catch."
    )


def test_every_historical_version_claim_still_exists():
    """The allowlist is a LEDGER: it must fail when the set SHRINKS too.

    A stale entry silently grants an exemption to a line that no longer exists,
    and the next edit to that file inherits the hole.
    """
    orphans = []
    for rel, snippet, reason in HISTORICAL_VERSION_CLAIMS:
        text = (ROOT / rel).read_text(encoding="utf-8")
        hits = [
            ln for ln in text.splitlines()
            if snippet in ln
            and any(v != PINNED_VERSION for v in _VERSION_RE.findall(ln))
        ]
        if not hits:
            orphans.append(f"  {rel}: {snippet!r} ({reason})")
    assert not orphans, (
        "HISTORICAL_VERSION_CLAIMS entries match no old-version line any more — "
        "the claim was updated or deleted, so drop the entry:\n" + "\n".join(orphans)
    )


def test_opencode_is_declared_in_the_nix_package_set():
    """🔴 A version pin that is not deployed pins nothing.

    The binary must come from flake.lock via home.packages, not from an
    imperative `nix profile install`, which each host can move independently and
    silently.
    """
    src = TOOLS_NIX.read_text()
    lines = [ln.strip() for ln in src.split("\n")]
    assert "opencode" in lines, (
        f"`opencode` is not a bare entry in {TOOLS_NIX.relative_to(ROOT)}. It "
        f"must be declared there so flake.lock pins the version; an imperative "
        f"`nix profile install nixpkgs#opencode` drifts per host."
    )


# --------------------------------------------------------------------------- #
# 2. 🔴 the engine vs the Python resolver model
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("engine_name,model_agent", ENGINE_AGENTS)
def test_engine_bash_ruleset_matches_the_python_model(engine_name, model_agent):
    """🔴 test_opencode_config.py's `bash_ruleset()` must equal what opencode
    actually builds — otherwise every effective-action assertion in that file is
    a fiction about a merge order that no longer holds.

    Compared after collapsing CONSECUTIVE DUPLICATES: the model prepends the
    built-in `("*", "allow")` and then re-reads the config's own leading
    `("*", "allow")`, so it carries one redundant copy the engine collapses.
    That duplicate is idempotent under last-match-wins (MEASURED: 0 verdict
    mismatches across the whole command matrix either way), so collapsing it is
    the right normalisation — it is NOT a licence to ignore other differences.
    """
    engine = engine_bash_rules(engine_name)
    predicted = _dedupe_consecutive(model.bash_ruleset(model_agent))
    assert engine == predicted, (
        f"agent={engine_name}: the engine's bash rule array differs from "
        f"test_opencode_config.py's model.\n"
        f"  engine {len(engine)} rules, model {len(predicted)} rules\n"
        f"  first difference: "
        f"{next((f'@{i} engine={e} model={m}' for i, (e, m) in enumerate(zip(engine, predicted)) if e != m), 'length only')}\n"
        f"  The model is what every permission assertion in this repo rests on."
    )


@pytest.mark.parametrize("engine_name,model_agent", ENGINE_AGENTS)
def test_engine_and_model_agree_on_every_pinned_command(engine_name, model_agent):
    """The outcome-level check, over the SAME matrix test_opencode_config.py
    pins statically. Structural equality above should imply this; asserting it
    anyway is cheap and catches a normalisation that quietly hid a real change.

    NOTE this compares the GLOB LAYER only — guard_core.py (layer 2) is a
    separate control with its own tests, and is not what a version bump moves.
    """
    rules = engine_bash_rules(engine_name)
    commands = model.MUST_DENY + model.MUST_ASK + model.MUST_ALLOW + model.GLOB_BLIND_SPOTS
    mismatches = [
        (c, resolve(rules, c), model.effective_bash_action(c, model_agent))
        for c in commands
        if resolve(rules, c) != model.effective_bash_action(c, model_agent)
    ]
    assert not mismatches, (
        f"agent={engine_name}: {len(mismatches)}/{len(commands)} commands "
        f"resolve differently against the REAL engine's rule array than against "
        f"the Python model (command, engine, model): {mismatches[:5]}"
    )


# --------------------------------------------------------------------------- #
# 3. 🔴 resolved TOOL SETS — claims no static test can check
# --------------------------------------------------------------------------- #
def test_engine_resolves_navs_tool_set_to_exactly_the_pinned_four():
    """test_opencode_config.py's `test_nav_is_kept_lean` docstring says "VERIFIED
    against `opencode debug agent nav` on 1.18.16: the resolved tool set is
    exactly {glob, grep, read} (+ the internal `invalid`)" — but that file
    asserts only the CONFIG KEYS, so the verification was a one-off nobody
    re-ran. This re-runs it every gate.
    """
    d = json.loads(engine_agent("nav"))
    enabled = {k for k, v in d["tools"].items() if v}
    assert enabled == NAV_EXPECTED_TOOLS, (
        f"nav resolves tools {sorted(enabled)}, expected "
        f"{sorted(NAV_EXPECTED_TOOLS)}. nav is the cheap, high-frequency "
        f"navigator; `skill` alone injects the ~3,730-token catalogue on EVERY "
        f"request, and `task` lets it recurse."
    )


@pytest.mark.parametrize("name", HIDDEN_AGENTS)
def test_engine_resolves_no_tools_for_the_hidden_agents(name):
    """🔴 Cost AND blast radius, checked at the engine.

    Every entry in the global `permission` block is appended to the hidden
    agents' resolved lists too, which once re-enabled bash, write, edit, task and
    skill on all three. `compaction` runs automatically on every context
    overflow, i.e. it handed a shell and a writer to the cheap model on a path
    nobody watches. test_opencode_config.py pins the CONFIG fix (`'*': 'deny'`);
    only the engine can confirm the fix still WORKS.
    """
    d = json.loads(engine_agent(name))
    enabled = sorted(k for k, v in d["tools"].items() if v)
    assert enabled == [], (
        f"hidden agent {name!r} resolves tools {enabled} — expected none. It "
        f"runs unattended on the cheap model, so any tool here is both a cost "
        f"and a blast-radius regression."
    )


@pytest.mark.parametrize("name", HIDDEN_AGENTS)
def test_engine_resolves_the_cheap_model_for_the_hidden_agents(name):
    d = json.loads(engine_agent(name))
    got = (d.get("model") or {}).get("modelID", "")
    assert "flash" in got, (
        f"hidden agent {name!r} resolves model {got!r}, which is not the cheap "
        f"one. These run on every title generation and every compaction."
    )
