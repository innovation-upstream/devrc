"""Static contract tests for the SHADOW safety invariant + the home-manager wiring.

These encode the non-negotiables in code so a future edit can't silently break
them:
  * SHADOW is the default and NOTHING is POSTed to clawgate / dispatched in it —
    proven structurally: the only clawgate `/api/send` POST lives strictly AFTER
    the shadow branch's `exit 0`, so a shadow run can never reach it.
  * the deterministic safety gate force-escalates risk tickets to NEEDS-DECISION.
  * the drafter is wired into home-manager: workbench-only (serverMode-gated),
    daily 08:00, OnFailure toast, and the env is seeded shadow-by-default.

Pure text assertions over the committed sources — hermetic, no execution.
"""
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_DRAFTER = _HERE.parent / "drafter.sh"
_PROMPT = _HERE.parent / "drafter-prompt.md"
_ENV_EXAMPLE = _HERE.parent / "task-spec-drafter.env.example"
_HOME_NIX = _HERE.parents[2] / "nix" / "home.nix"

# The `git -C` path is PINNED (2026-07-28 RCE fix) — a wildcard there let an
# injected ticket insert git's GLOBAL options (`-c diff.external=<cmd>`) between
# the path and the verb. See test_allowlist_covers_prompt_shapes.py.
_PINNED_PATHS = (
    "/home/zach/workspace/civit/civitai",
    "/home/zach/workspace/civit/datapacket-talos",
    "/home/zach/workspace/homelab-talos",
)


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


# --- drafter.sh: SHADOW is the default + nothing dispatches in shadow ----------

def test_drafter_defaults_to_shadow():
    assert 'DRAFTER_MODE="${DRAFTER_MODE:-shadow}"' in _read(_DRAFTER)


def test_drafter_defaults_model_haiku():
    assert 'DRAFTER_MODEL="${DRAFTER_MODEL:-haiku}"' in _read(_DRAFTER)


def test_no_clawgate_post_reachable_in_shadow():
    """The clawgate POST must sit AFTER the shadow branch's `exit 0`, so a shadow
    run structurally cannot reach it."""
    src = _read(_DRAFTER)
    shadow_exit = src.index('run $RUN_TS done (shadow, nothing sent)')
    post_idx = src.index('/api/send')
    assert shadow_exit < post_idx, "clawgate POST appears before the shadow exit — shadow could dispatch!"
    # and the POST is guarded by mode=on, not shadow
    assert 'on: actually notify clawgate' in src


def test_shadow_branch_exits_before_send():
    src = _read(_DRAFTER)
    assert 'if [ "$DRAFTER_MODE" = "shadow" ]; then' in src
    # the shadow block ends in exit 0
    seg = src.split('if [ "$DRAFTER_MODE" = "shadow" ]; then', 1)[1]
    assert 'exit 0' in seg.split('# on: actually notify clawgate', 1)[0]


# --- read-only allowlist (no write-capable verbs reachable under injection) ----

def _allowlist(src: str) -> str:
    """Extract the DRAFTER_ALLOWED_TOOLS default value."""
    line = next(l for l in src.splitlines() if l.startswith("DRAFTER_ALLOWED_TOOLS="))
    return line


def test_allowlist_has_no_write_capable_verbs():
    """The headless pass reasons over untrusted client ticket text with no plan
    mode, so a write-capable allowlist entry is an injection→mutation path. These
    must NOT be present."""
    al = _allowlist(_read(_DRAFTER))
    assert "gh api" not in al, "gh api* allows `gh api -X POST/PATCH/DELETE`"
    assert "curl" not in al, "curl -s* allows `curl -X POST -d …`"
    assert "Bash(env" not in al, "env* dumps the inherited CLAWGATE_HOOK_TOKEN"
    # The merge/ref-status verbs added for the headless-runtime fix must be
    # NARROWED to their read-only shape — the broad globs would also match the
    # write forms of the same command, so those broad globs must be ABSENT.
    # Checked against BOTH the retired wildcard form and every pinned path, so the
    # guard cannot go vacuous now that the `-C` path is pinned.
    for prefix in ("git -C *",) + tuple(f"git -C {p}" for p in _PINNED_PATHS):
        assert f"Bash({prefix} branch*)" not in al, (
            f"broad `{prefix} branch*` glob matches `git branch <name>`/`-d/-D` "
            "(writes); only the read-only `branch --contains*` form is allowed"
        )
        assert f"Bash({prefix} symbolic-ref*)" not in al, (
            f"broad `{prefix} symbolic-ref*` glob matches 2-arg "
            "`symbolic-ref HEAD <ref>` (repoints HEAD, a write); only "
            "`symbolic-ref --short*` is allowed"
        )
    # sanity: no obviously-mutating git verbs anywhere in the allowlist.
    for bad in ("push", "commit", "git -C * apply", "git -C * reset",
                "git -C * checkout", "git -C * tag", "git -C * update-ref"):
        assert bad not in al, f"write-capable verb leaked into allowlist: {bad}"


def test_allowlist_keeps_readonly_verification_verbs():
    al = _allowlist(_read(_DRAFTER))
    for verb in (
        "Bash(gh pr list*)", "Bash(gh pr view*)",
        # kubectl moved behind the fixed `kubectl-ro` wrapper — a `Bash(kubectl
        # <verb>:*)` rule is a PREFIX rule and kubectl takes its global flags
        # BEFORE the verb, so `kubectl -n prod delete …` walked past every one of
        # them. Direct kubectl is now denied wholesale.
        "Bash($SELF_DIR/kubectl-ro *)",
        # PINNED to the literal script path — `Bash(node *query.mjs get*)` was the
        # same mid-glob RCE as `git -C *`: `node -e '<js>' query.mjs get 1` matched
        # it and node ran the `-e` payload (verified end-to-end via `claude -p`).
        "Bash(node /home/zach/.claude/skills/clickup/query.mjs get*)",
    ):
        assert verb in al, f"missing read-only verification verb {verb}"
    assert "Bash(node *query.mjs" not in al, (
        "the clickup CLI entry must pin the literal script path — a wildcard "
        "before it admits `node -e '<arbitrary JS>' query.mjs get 1`"
    )
    # git reads survive the RCE fix — in their PINNED form, for every repo the
    # drafter is allowed to touch.
    for path in _PINNED_PATHS:
        for verb in (f"Bash(git -C {path} log*)", f"Bash(git -C {path} show*)"):
            assert verb in al, f"missing read-only verification verb {verb}"


def test_allowlist_adds_merge_status_readonly_verbs():
    """The headless-runtime fix: the drafter kept burning calls on "is X merged /
    on trunk?" because the answer verbs weren't allowlisted → "requires approval".
    These read-only merge/ref verbs (each in its narrowest safe shape) must now be
    present so the check runs non-interactively."""
    al = _allowlist(_read(_DRAFTER))
    assert "Bash(gh pr checks*)" in al, "missing read-only merge/ref verb gh pr checks*"
    for path in _PINNED_PATHS:
        for suffix in (
            "branch --contains*", "rev-parse*", "rev-list*", "merge-base*",
            "for-each-ref*", "symbolic-ref --short*", "ls-files*", "cat-file*",
        ):
            verb = f"Bash(git -C {path} {suffix})"
            assert verb in al, f"missing read-only merge/ref verb {verb}"


def test_allowlist_has_no_arbitrary_command_flag_verbs():
    """RCE guard (security audit, 2026-07). `git ls-remote --upload-pack=<cmd>`
    (also `-o <cmd>`) EXECUTES <cmd> locally before the transport resolves; the
    trailing-`*` glob permits that suffix with no $VAR/&&/;/|/redirect, so it
    slips past the harness filters AND matches the glob AND auto-executes (no
    plan mode) over untrusted client ticket text. A prefix glob CANNOT forbid the
    exec flags, so the whole verb is unsafe to allowlist — same class that keeps
    `gh api` out. Block re-adding any git subcommand that accepts a
    remote-helper / exec flag (`--upload-pack`/`--receive-pack`/`--exec`/`-o`)."""
    al = _allowlist(_read(_DRAFTER))
    # The specific verb the audit proved, plus its exec-flag siblings — none of
    # these git subcommands may appear as an allowlist entry. `grep` joined the
    # list on 2026-07-28: `git grep -O<cmd>` / `--open-files-in-pager=<cmd>` runs
    # <cmd>, the flag sits AFTER the subcommand (so PINNING the -C path does not
    # help), and git grep accepts the abbreviated `--open=<cmd>` too — verified by
    # execution on git 2.54.0. Checked against the retired wildcard form AND every
    # pinned path so the guard cannot go vacuous.
    for prefix in ("git -C *",) + tuple(f"git -C {p}" for p in _PINNED_PATHS):
        for verb in ("ls-remote", "fetch", "clone", "pull", "push", "daemon",
                     "archive", "remote", "submodule", "grep"):
            assert f"{prefix} {verb}" not in al, (
                f"git {verb}* accepts an arbitrary-command flag "
                "(--upload-pack/--receive-pack/--exec/-O) — a prefix glob can't "
                "forbid it; RCE"
            )
    # And no raw exec-flag token should ever be baked into an allowlist entry.
    for flag in ("--upload-pack", "--receive-pack", "--exec"):
        assert flag not in al, f"exec flag {flag} must never appear in the allowlist"


# --- prompt: command-shape contract (headless calls kept getting rejected) ----

def _norm(s: str) -> str:
    """Collapse all runs of whitespace to single spaces so phrase assertions are
    robust to markdown line-wrapping."""
    return " ".join(s.split())


def test_prompt_has_command_shape_contract():
    """The harness rejects the shapes the model defaults to ($VAR expansion,
    cd, compound &&/pipes). The prompt must carry a loud contract forbidding them
    and mandating a single `git -C <abspath>` verb per call."""
    p = _norm(_read(_PROMPT))
    assert "COMMAND-SHAPE CONTRACT" in p
    # forbids cd
    assert "NEVER `cd`" in p or "NEVER `cd repo" in p
    # forbids shell-variable expansion (with the concrete $CIVITAI example)
    assert "$CIVITAI" in p
    assert "simple_expansion" in p, "should name the exact rejection the harness emits"
    # forbids compound / pipes, mandates ONE verb per call
    assert "&&" in p and "pipes" in p
    assert "ONE verb per Bash call" in p or "ONE read verb per" in p
    # mandates the git -C <abspath> single-verb shape
    assert "git -C <ABSOLUTE-PATH>" in p
    # a concrete REJECTED→DO-THIS table exists
    assert "REJECTED" in p and "INSTEAD" in p


def test_prompt_has_anticonfab_gate():
    """One drafter run emitted a "nothing found" verdict having run ZERO tools —
    a fabricated verification. The prompt must hard-require ≥1 successful read
    before any factual verdict, and route the no-read case to a low-confidence
    NEEDS-DECISION instead of asserting a negative finding."""
    p = _norm(_read(_PROMPT))
    assert "ANTI-CONFABULATION GATE" in p
    assert "at least ONE successful read" in p or "at least one successful read" in p.lower()
    # the honest no-read path is NEEDS-DECISION + low confidence, not a false negative
    assert "NEEDS-DECISION" in p
    assert 'confidence: "low"' in p or "confidence: low" in p
    assert "no source was reachable" in p or "all reads failed" in p


def test_prompt_still_forbids_gh_api_and_curl():
    """The expanded read verbs must not have quietly re-permitted the mutation
    paths the design excludes."""
    p = _norm(_read(_PROMPT))
    assert "No `gh api` and no `curl`" in p


# --- deterministic safety gate ------------------------------------------------

def test_safety_gate_forces_needs_decision():
    src = _read(_DRAFTER)
    assert 'safety_gate()' in src
    assert '.classification = "NEEDS-DECISION"' in src
    for cat in ("GATE_RE_SECURITY", "GATE_RE_MONEY", "GATE_RE_DESTRUCTIVE"):
        assert cat in src


# --- daily email digest (the review surface) ----------------------------------

def test_digest_email_step_present_and_default_on():
    src = _read(_DRAFTER)
    assert 'DRAFTER_EMAIL="${DRAFTER_EMAIL:-on}"' in src
    assert "send_digest.py" in src or "$SEND_HELPER" in src
    assert "task-drafter $MODE_TAG digest" in src


def test_env_example_is_shadow_and_haiku():
    env = _read(_ENV_EXAMPLE)
    assert "DRAFTER_MODE=shadow" in env
    assert "DRAFTER_MODEL=haiku" in env
    assert "CLICKUP_VIEW_ID=6-901111220963-1" in env


# --- home-manager wiring ------------------------------------------------------

def test_hm_unit_is_servermode_gated():
    nix = _read(_HOME_NIX)
    assert "systemd.user.services.task-spec-drafter = lib.mkIf serverMode" in nix
    assert "systemd.user.timers.task-spec-drafter = lib.mkIf serverMode" in nix


def test_hm_timer_is_daily_0800():
    nix = _read(_HOME_NIX)
    # the drafter timer block carries the 08:00 calendar
    block = nix.split("systemd.user.timers.task-spec-drafter", 1)[1]
    assert 'OnCalendar = "*-*-* 08:00:00"' in block
    assert "Persistent = true" in block


def test_hm_unit_has_onfailure_and_execstart():
    nix = _read(_HOME_NIX)
    block = nix.split("systemd.user.services.task-spec-drafter", 1)[1].split(
        "systemd.user.timers.task-spec-drafter", 1)[0]
    assert 'OnFailure = [ "notify-failure@%n.service" ]' in block
    assert "scripts/task-spec-drafter/drafter.sh" in block
    assert "REPO_COS_PROD_KUBECONFIG" in block


def test_hm_unit_timeout_covers_first_run():
    """TimeoutStartSec must clear the worst-case first run
    (DRAFTER_MAX_TICKETS 25 × DRAFTER_TIMEOUT 240s = 6000s) so it isn't SIGTERM'd
    mid-loop."""
    nix = _read(_HOME_NIX)
    block = nix.split("systemd.user.services.task-spec-drafter", 1)[1].split(
        "systemd.user.timers.task-spec-drafter", 1)[0]
    assert "TimeoutStartSec = 7200" in block


def test_hm_seeds_shadow_env():
    nix = _read(_HOME_NIX)
    assert "home.activation.taskSpecDrafterEnv" in nix
    assert "task-spec-drafter.env" in nix
    assert "chmod 600" in nix
