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
  behaviours annotated "measured on v1.18.4 — do not re-derive" — last-match-
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
`nix_eval()` in test_opencode_config.py. This file adds 19 tests and 0 skips
(MEASURED, `pytest -q`: "19 passed", ~10 s).

🔴 WHAT THIS FILE CANNOT SEE. Its conformance tests compare the ENGINE against
the MODEL, and both read the same config — so a bad CONFIG makes them agree and
stay green. That is test_opencode_config.py's job. This file's job is the
complement: a bad or drifted BINARY under an unchanged config. Do not read a
green here as "the config is fine".

    run:  python -m pytest scripts/tests/test_opencode_engine.py -q
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from functools import lru_cache
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

# The resolver MODEL and the command matrix live in one place — the sibling
# file. Re-declaring either here would give us two copies to drift apart, and
# the point of this file is to check the model against reality, not to fork it.
import test_opencode_config as model  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
OC_DIR = ROOT / "scripts" / "opencode"
TOOLS_NIX = ROOT / "nix" / "pkgs" / "tools" / "default.nix"

# 🔴 THE PIN. Every "measured on v1.18.4" claim in opencode.jsonc's header, in
# scripts/opencode/README.md and in test_opencode_config.py's docstrings is keyed
# to this exact version. It is pinned declaratively by nix/pkgs/tools/default.nix
# resolving `pkgs.opencode` out of flake.lock's nixpkgs.
PINNED_VERSION = "1.18.4"

# MEASURED via `opencode debug agent nav --pure` at 1.18.4. This is the cost AND
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


def _run_debug_agent(name: str, mutate=None) -> dict:
    exe = _opencode_bin()
    tmp = Path(tempfile.mkdtemp(prefix="oc-engine-test-"))
    try:
        cfg = _seed_config_dir(tmp, mutate)
        proj = tmp / "proj"
        proj.mkdir()
        home = tmp / "home"
        home.mkdir()
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
        p = subprocess.run(
            [exe, "debug", "agent", name, "--pure"],
            capture_output=True, text=True, env=env, cwd=str(proj), timeout=180,
        )
        assert p.returncode == 0, (
            f"`opencode debug agent {name} --pure` exited {p.returncode}\n"
            f"STDERR:\n{p.stderr[:2000]}"
        )
        assert p.stdout.strip(), (
            f"`opencode debug agent {name}` produced NO stdout. Do not read this "
            f"as 'nothing to check' — the output format is a dependency this "
            f"test did not pin, and an empty parse is indistinguishable from a "
            f"changed CLI. STDERR:\n{p.stderr[:2000]}"
        )
        return json.loads(p.stdout)
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
    p = subprocess.run([exe, "--version"], capture_output=True, text=True,
                       timeout=120)
    assert p.returncode == 0, f"`opencode --version` exited {p.returncode}"
    got = p.stdout.strip()
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
    against `opencode debug agent nav` on 1.18.4: the resolved tool set is
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
