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
_ENV_EXAMPLE = _HERE.parent / "task-spec-drafter.env.example"
_HOME_NIX = _HERE.parents[2] / "nix" / "home.nix"


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


def test_hm_seeds_shadow_env():
    nix = _read(_HOME_NIX)
    assert "home.activation.taskSpecDrafterEnv" in nix
    assert "task-spec-drafter.env" in nix
    assert "chmod 600" in nix
