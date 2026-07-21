"""Tests for send_digest.py — the task-spec-drafter digest email helper.

Hermetic: the dry-run path touches no network; the real-send path is exercised
only as far as the relay's local `PROD_KUBECONFIG.exists()` guard (a missing
kubeconfig raises before any port-forward / SMTP), which proves BOTH that the
default send mode is the repo-cos relay AND that a send failure degrades to a
logged non-zero exit rather than raising into the drafter.
"""
import os
import subprocess
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_HELPER = _HERE.parent / "send_digest.py"


def _run(args, env_extra=None):
    env = dict(os.environ)
    env.pop("DRAFTER_EMAIL_DRYRUN", None)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(_HELPER), *args],
        capture_output=True, text=True, env=env, timeout=60,
    )


def test_helper_exists_and_is_executable():
    assert _HELPER.exists()
    assert os.access(_HELPER, os.X_OK)


def test_dry_run_renders_to_file_and_sends_nothing(tmp_path):
    body = tmp_path / "digest.md"
    body.write_text("# triage\n[TASK] abc — do the thing\n", encoding="utf-8")
    out = tmp_path / "rendered.txt"
    r = _run([
        "--subject", "task-drafter SHADOW digest — 1 would-dispatch, 2 need-decision",
        "--body-file", str(body),
        "--to", "someone@example.com",
        "--dry-run", "--out", str(out),
    ])
    assert r.returncode == 0, r.stderr
    rendered = out.read_text(encoding="utf-8")
    assert "To: someone@example.com" in rendered
    assert "Subject: task-drafter SHADOW digest" in rendered
    assert "do the thing" in rendered  # the body is carried verbatim


def test_dry_run_via_env_flag_to_stdout(tmp_path):
    body = tmp_path / "digest.md"
    body.write_text("BODYMARKER\n", encoding="utf-8")
    r = _run(
        ["--subject", "s", "--body-file", str(body)],
        env_extra={"DRAFTER_EMAIL_DRYRUN": "1"},
    )
    assert r.returncode == 0, r.stderr
    assert "BODYMARKER" in r.stdout


def test_real_send_missing_kubeconfig_is_best_effort(tmp_path):
    """Default mode is the repo-cos relay; a missing prod kubeconfig fails the
    relay's local existence guard BEFORE any network, and send_digest catches it
    -> exit 1 + a logged 'FAILED', never an unhandled exception."""
    body = tmp_path / "digest.md"
    body.write_text("x\n", encoding="utf-8")
    r = _run(
        ["--subject", "s", "--body-file", str(body), "--to", "z@example.com"],
        env_extra={"REPO_COS_PROD_KUBECONFIG": str(tmp_path / "nope-kubeconfig")},
    )
    assert r.returncode == 1
    assert "FAILED" in r.stderr
    # no traceback leaked
    assert "Traceback" not in r.stderr
