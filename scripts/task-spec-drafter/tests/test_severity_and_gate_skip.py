"""Behavioral tests for the two digest-content changes (2026-07-29):

CHANGE 1 — deterministic SEVERITY/URGENCY detector + digest ordering:
  (c) incident-language tickets are tagged URGENT and the digest floats them to
      the top with a 🔥 marker + a top-of-digest callout;
  (d) a normal ticket stays non-urgent.

CHANGE 2 — SKIP the safety-gate override for NON-DISPATCHING classifications:
  (a) an ALREADY-DONE / VERIFY ticket carrying risk keywords is NOT relabeled to
      NEEDS-DECISION and carries NO safety flag;
  (b) a TASK carrying risk keywords STILL gets gate-escalated (unchanged).

These EXERCISE the real bash functions (severity_tag / safety_gate /
build_summary) by sourcing drafter.sh with DRAFTER_LIB_ONLY=1 (a test hook that
returns before the live ClickUp/claude pipeline runs). Pure-text structural
assertions live alongside these in test_shadow_wiring.py.

Skips cleanly when bash/jq are unavailable (e.g. a minimal nix sandbox) so the
hermetic flake check never goes red on tooling; on the dev host (where
run-tests.sh runs) bash+jq are present and the tests execute for real.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_DRAFTER = _HERE.parent / "drafter.sh"

_BASH = shutil.which("bash")
_JQ = shutil.which("jq")
_GREP = shutil.which("grep")

pytestmark = pytest.mark.skipif(
    not (_BASH and _JQ and _GREP),
    reason="behavioral tests need bash + jq + grep on PATH",
)


def _run_lib(snippet: str, tmp_path: Path) -> str:
    """Source drafter.sh (lib-only) then run `snippet`; return stdout."""
    out_dir = tmp_path / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    script = textwrap.dedent(
        f"""
        set -uo pipefail
        export DRAFTER_LIB_ONLY=1
        export DRAFTER_OUT_DIR={out_dir!s}
        export DRAFTER_LOG_FILE={out_dir!s}/log
        source {_DRAFTER!s}
        {snippet}
        """
    )
    res = subprocess.run(
        [_BASH, "-c", script],
        capture_output=True,
        text=True,
        cwd=str(tmp_path),
    )
    assert res.returncode == 0, f"lib snippet failed rc={res.returncode}\nstderr:\n{res.stderr}"
    return res.stdout


def _write(p: Path, text: str) -> Path:
    p.write_text(text, encoding="utf-8")
    return p


def _gate(tmp_path: Path, ticket_text: str, record: dict) -> dict:
    """Run safety_gate over (ticket_text, record) and return the JSON result."""
    txt = _write(tmp_path / "ticket.txt", ticket_text)
    rec = _write(tmp_path / "rec.json", json.dumps(record))
    out = _run_lib(f'safety_gate "{txt}" "$(cat "{rec}")"', tmp_path)
    return json.loads(out.strip().splitlines()[-1])


# --- CHANGE 2: gate skip for non-dispatching classes --------------------------

def test_a_already_done_with_risk_keywords_not_relabeled(tmp_path):
    """(a) ALREADY-DONE carrying prod/migration risk keywords must keep its class
    and carry NO safety flag — a close-recommendation never dispatches, so gating
    it is pure noise."""
    rec = {
        "ticket_id": "ccc333",
        "title": "Prod migration for user table",
        "classification": "ALREADY-DONE",
        "confidence": "high",
        "verification": "merged in PR 2811, deployed",
        "recommendation": "close it — already shipped",
        "correlations": [],
        "spec": {"goal": "", "done": "", "owner": "", "autonomy": ""},
        "safety_flag": "",
    }
    out = _gate(tmp_path, "Close this — the prod migration is already merged and deployed", rec)
    assert out["classification"] == "ALREADY-DONE", "must NOT be relabeled to NEEDS-DECISION"
    assert out["gate_fired"] is False
    assert out["safety_flag"] == "", "no safety flag on a non-dispatching class"
    assert out["recommendation"] == "close it — already shipped", "model recommendation preserved"
    # audit trail: record that the gate WOULD have matched, for transparency
    assert out.get("gate_exempt_class") is True
    assert "destructive/prod-mutation" in out.get("gate_would_have_categories", [])


def test_a_verify_with_money_keywords_not_relabeled(tmp_path):
    """(a, sibling) VERIFY carrying money-category keywords is likewise exempt."""
    rec = {
        "ticket_id": "vvv444",
        "title": "Refund path sanity check",
        "classification": "VERIFY",
        "confidence": "medium",
        "verification": "looks shipped",
        "recommendation": "manual confirm the refund flow",
        "correlations": [],
        "spec": {"goal": "", "done": "", "owner": "", "autonomy": ""},
        "safety_flag": "",
    }
    out = _gate(tmp_path, "Confirm the payment refund path works", rec)
    assert out["classification"] == "VERIFY"
    assert out["gate_fired"] is False
    assert out["safety_flag"] == ""


def test_all_nondispatching_classes_are_exempt(tmp_path):
    """Every non-dispatching class token the code exempts stays put under a risk
    match (ALREADY-DONE / STALE-close / STALE / VERIFY / FYI / DUPLICATE)."""
    for cls in ("ALREADY-DONE", "STALE-close", "STALE", "VERIFY", "FYI", "DUPLICATE"):
        rec = {
            "ticket_id": "x",
            "title": "prod delete migration",
            "classification": cls,
            "confidence": "high",
            "verification": "v",
            "recommendation": "r",
            "correlations": [],
            "spec": {"goal": "", "done": "", "owner": "", "autonomy": ""},
            "safety_flag": "",
        }
        out = _gate(tmp_path, "delete the prod table via migration", rec)
        assert out["classification"] == cls, f"{cls} must not be relabeled"
        assert out["gate_fired"] is False, f"{cls} must not fire the gate"
        assert out["safety_flag"] == "", f"{cls} must carry no safety flag"


# --- CHANGE 2 (unchanged half): TASK still escalates --------------------------

def test_b_task_with_risk_keywords_still_escalates(tmp_path):
    """(b) A TASK (dispatchable) carrying risk keywords MUST still be force-
    escalated to NEEDS-DECISION with a safety flag + needs-Zach — the gate's core
    dispatch-blocking job is unchanged."""
    rec = {
        "ticket_id": "t1",
        "title": "Rebuild search index in production",
        "classification": "TASK",
        "confidence": "high",
        "verification": "checked",
        "recommendation": "do it",
        "correlations": [],
        "spec": {"goal": "rebuild", "done": "green", "owner": "zach", "autonomy": "auto"},
        "safety_flag": "",
    }
    out = _gate(tmp_path, "Delete the prod meilisearch index and rebuild", rec)
    assert out["classification"] == "NEEDS-DECISION", "TASK with risk must escalate"
    assert out["gate_fired"] is True
    assert len(out["safety_flag"]) > 0
    assert out["spec"]["autonomy"] == "needs-Zach"
    assert out["gate_override_from"] == "TASK"
    assert out["spec"]["goal"] == "", "dispatchable spec blanked"


def test_task_without_risk_keywords_passes_through(tmp_path):
    """A clean TASK is untouched (gate_fired=false, spec intact)."""
    rec = {
        "ticket_id": "t2",
        "title": "Improve onboarding copy",
        "classification": "TASK",
        "confidence": "medium",
        "verification": "no prior art",
        "recommendation": "",
        "correlations": [],
        "spec": {"goal": "tidy copy", "done": "reviewed", "owner": "zach", "autonomy": "auto"},
        "safety_flag": "",
    }
    out = _gate(tmp_path, "Please tidy up the onboarding copy on the about page", rec)
    assert out["classification"] == "TASK"
    assert out["gate_fired"] is False
    assert out["spec"]["goal"] == "tidy copy"


# --- CHANGE 1: severity detector ----------------------------------------------

def _severity(tmp_path: Path, ticket_text: str) -> str:
    txt = _write(tmp_path / "ticket.txt", ticket_text)
    return _run_lib(f'severity_tag "{txt}"', tmp_path).strip()


@pytest.mark.parametrize(
    "text",
    [
        "Image scanning DOWN since restart, 0 images reaching Scanned",
        "Upload pipeline is stuck, nothing processing for 2 hours",
        "prod is broken — 500s on every request",
        "P1: error rate spiking, data loss suspected",
        "URGENT please fix ASAP the scanner crashloop",
    ],
)
def test_c_incident_language_is_urgent(tmp_path, text):
    """(c) incident/high-urgency language → URGENT."""
    assert _severity(tmp_path, text) == "URGENT"


@pytest.mark.parametrize(
    "text",
    [
        "Improve onboarding copy on the about page when you get a chance",
        "Add a tooltip to the settings gear icon",
        "Consider renaming the 'models' tab to 'checkpoints' someday",
    ],
)
def test_d_normal_language_stays_normal(tmp_path, text):
    """(d) a vague/backlog ticket stays non-urgent."""
    assert _severity(tmp_path, text) == "normal"


def test_severity_is_word_anchored_no_false_positive_on_2500(tmp_path):
    """`\\b500s?\\b` must not fire on '2500' (documented false-positive guard)."""
    assert _severity(tmp_path, "Bump the batch size to 2500 items per page") == "normal"


# --- CHANGE 1: digest ordering + callout (via build_summary) ------------------

def _build_summary(tmp_path: Path, records: list[dict]) -> str:
    q = tmp_path / "queue.jsonl"
    q.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    snippet = (
        'RUN_TS=T CLICKUP_VIEW_ID=v TOTAL=9 PROCESSED=3 SKIPPED=6 BASELINED=0 '
        'GATE_HITS=0 DRAFTER_MODEL=haiku DRAFTER_MODE=shadow; '
        f'build_summary "{q}"'
    )
    return _run_lib(snippet, tmp_path)


def test_c_digest_floats_urgent_first_with_marker_and_callout(tmp_path):
    """(c) URGENT action-worthy items sort FIRST, wear a 🔥 URGENT marker, and a
    top-of-digest callout names the incident."""
    records = [
        # normal TASK appears FIRST in the file — must be pushed BELOW the incident
        {"ticket_id": "aaa111", "title": "Improve onboarding copy",
         "classification": "TASK", "confidence": "medium", "age_days": 40, "status": "open",
         "verification": "no prior art", "recommendation": "", "correlations": [],
         "spec": {"goal": "copy", "done": "reviewed", "owner": "zach", "autonomy": "auto"},
         "safety_flag": "", "gate_fired": False, "severity": "normal"},
        # the active incident — mid-file, must float to the top
        {"ticket_id": "868kfwm3j",
         "title": "Image scanning DOWN since restart, 0 images reaching Scanned",
         "classification": "NEEDS-DECISION", "confidence": "medium", "age_days": 0, "status": "open",
         "verification": "scanner pod crashlooping in prod",
         "recommendation": "needs Zach — active incident", "correlations": [],
         "spec": {"goal": "", "done": "", "owner": "", "autonomy": "needs-Zach"},
         "safety_flag": "", "gate_fired": False, "severity": "URGENT"},
    ]
    out = _build_summary(tmp_path, records)

    # top-of-digest callout, before the "Source:" meta line
    assert "🔥 1 URGENT — active incident: 868kfwm3j Image scanning DOWN" in out
    assert out.index("🔥 1 URGENT") < out.index("Source: ClickUp"), "callout must be at the very top"
    assert "· urgent: 1" in out

    # ordering: the urgent heading appears before the normal TASK heading
    urgent_hd = out.index("### 🔥 URGENT · [NEEDS-DECISION] 868kfwm3j")
    normal_hd = out.index("### [TASK] aaa111")
    assert urgent_hd < normal_hd, "URGENT item must render before the normal item"
    # the normal item carries NO 🔥 marker on its heading
    assert "🔥 URGENT · [TASK] aaa111" not in out


def test_d_no_callout_when_nothing_urgent(tmp_path):
    """No urgent items → no 🔥 callout, urgent count 0, normal ordering intact."""
    records = [
        {"ticket_id": "aaa111", "title": "Improve onboarding copy",
         "classification": "TASK", "confidence": "medium", "age_days": 40, "status": "open",
         "verification": "no prior art", "recommendation": "", "correlations": [],
         "spec": {"goal": "copy", "done": "reviewed", "owner": "zach", "autonomy": "auto"},
         "safety_flag": "", "gate_fired": False, "severity": "normal"},
    ]
    out = _build_summary(tmp_path, records)
    assert "🔥" not in out
    assert "· urgent: 0" in out
    assert "### [TASK] aaa111" in out


def test_c_already_done_urgent_words_not_in_callout(tmp_path):
    """An ALREADY-DONE that merely mentions incident words is NOT an open
    incident, so it never reaches the callout or the action-worthy reordering —
    it stays in Suppressed."""
    records = [
        {"ticket_id": "ddd555", "title": "Scanner was DOWN last week",
         "classification": "ALREADY-DONE", "confidence": "high", "age_days": 7, "status": "closed",
         "verification": "fixed + deployed in PR 900", "recommendation": "close — resolved",
         "correlations": [], "spec": {"goal": "", "done": "", "owner": "", "autonomy": ""},
         "safety_flag": "", "gate_fired": False, "severity": "URGENT"},
    ]
    out = _build_summary(tmp_path, records)
    assert "🔥" not in out, "a resolved incident must not raise the callout"
    assert "· urgent: 0" in out
    assert "## Suppressed" in out
    assert "[ALREADY-DONE] ddd555" in out
