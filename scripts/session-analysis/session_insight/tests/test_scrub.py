"""scrub — every SECRET_PATTERN redacted + labelled + counted; private-key block
redacted; internal IPs survive; public IPs redacted only when enabled."""
import ast
from pathlib import Path

import scrub


# One synthetic sample per pattern → its expected label slug.
SAMPLES = {
    "aws-key": "AKIA" + "A" * 16,
    "aws-temp-key": "ASIA" + "B" * 16,
    "github-token": "ghp_" + "a" * 36,
    "github-pat": "github_pat_" + "b" * 42,
    "gitlab-token": "glpat-" + "c" * 24,
    "anthropic-key": "sk-ant-" + "d" * 24,
    "openrouter-key": "sk-or-v1-" + "e" * 24,
    "openai-key": "sk-proj-" + "f" * 24,
    "slack-token": "xoxb-" + "1234567890abc",
    "google-key": "AIza" + "g" * 35,
}


def test_each_secret_pattern_redacted_labelled_counted():
    for label, sample in SAMPLES.items():
        text = f"here is a key: {sample} end"
        clean, counts = scrub.scrub(text)
        assert sample not in clean, f"{label} not redacted"
        assert f"<REDACTED:{label}>" in clean, f"{label} label missing"
        assert counts.get(label) == 1, f"{label} count wrong: {counts}"


def test_multiple_matches_counted():
    text = f"{SAMPLES['aws-key']} and {'AKIA' + 'Z' * 16}"
    clean, counts = scrub.scrub(text)
    assert counts["aws-key"] == 2
    assert "AKIA" not in clean


def test_private_key_block_redacted():
    block = ("-----BEGIN RSA PRIVATE KEY-----\n"
             "MIIEpAIBAAKCAQEA0000fakekeymaterial00000\n"
             "-----END RSA PRIVATE KEY-----")
    clean, counts = scrub.scrub(f"secret:\n{block}\ndone")
    assert "PRIVATE KEY" not in clean
    assert "<REDACTED:private-key>" in clean
    assert counts["private-key"] == 1


def test_internal_ips_survive_by_default():
    text = "nebula 10.42.0.100, LAN 192.168.50.94, loopback 127.0.0.1, NodePort 172.16.0.5"
    clean, counts = scrub.scrub(text)
    assert "10.42.0.100" in clean
    assert "192.168.50.94" in clean
    assert "127.0.0.1" in clean
    assert "172.16.0.5" in clean
    assert "public-ip" not in counts


def test_public_ip_redacted_only_when_enabled():
    text = "external 8.8.8.8 vs internal 192.168.1.1"
    clean_off, counts_off = scrub.scrub(text, redact_public_ips=False)
    assert "8.8.8.8" in clean_off and "public-ip" not in counts_off

    clean_on, counts_on = scrub.scrub(text, redact_public_ips=True)
    assert "8.8.8.8" not in clean_on
    assert "<REDACTED:public-ip>" in clean_on
    assert counts_on["public-ip"] == 1
    assert "192.168.1.1" in clean_on   # internal survives even when enabled


def test_version_string_not_treated_as_ip():
    clean, counts = scrub.scrub("version 999.999.999.999", redact_public_ips=True)
    assert "999.999.999.999" in clean
    assert "public-ip" not in counts


def test_empty_text():
    assert scrub.scrub("") == ("", {})


# --------------------------------------------------------------------------- #
# FIX 4 — drift guard against the source of truth.
# --------------------------------------------------------------------------- #
# 🔴 This points at the REPO file, not at ~/.claude/hooks/. Two reasons, and the
# second is the one that bit:
#
#   1. CORRECT REFERENT. The deployed copy is *generated from* this file by
#      home-manager. Comparing against the deployed copy tested whichever build
#      the host last switched to — i.e. it answered a question about the machine,
#      not about the commit. The drift that matters is between two files that
#      live side by side IN THIS REPO.
#
#   2. IT COULD NOT FAIL IN CI. Keyed on $HOME, the test SKIPPED in the nix
#      sandbox (synthetic HOME, no hook) and RAN only on a switched dev host —
#      so the hermetic gate structurally could not observe it. #276 then moved
#      SECRET_PATTERNS out of bash-guard.py into guard_core.py; the parser
#      returned [] and this test failed on every dev host while the gate stayed
#      green and silent. See claude/RULES.md, "A suite that runs in TWO TIERS
#      must be green in BOTH".
#
# Both files are tracked, so this now runs EVERYWHERE and never skips.
_GUARD_CORE = (
    Path(__file__).resolve().parents[3] / "claude-hooks" / "guard_core.py"
)


def _guard_core_patterns():
    """Extract the regex strings from guard_core.py's SECRET_PATTERNS literal by
    parsing the module (never executing it)."""
    tree = ast.parse(_GUARD_CORE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign)
                and any(getattr(t, "id", None) == "SECRET_PATTERNS"
                        for t in node.targets)
                and isinstance(node.value, ast.List)):
            pats = []
            for elt in node.value.elts:            # each elt is (regex, label)
                if isinstance(elt, ast.Tuple) and elt.elts:
                    lit = ast.literal_eval(elt.elts[0])
                    if isinstance(lit, str):
                        pats.append(lit)
            return pats
    return []


def test_patterns_cover_guard_core():
    """scrub.py's pattern set must be a SUPERSET of guard_core's SECRET_PATTERNS.

    Never skips: both files are tracked in this repo, so every tier runs it.
    A NEVER-SKIPPING drift guard is the entire point — the previous version
    keyed on $HOME and was therefore unobservable in the sandbox.
    """
    assert _GUARD_CORE.is_file(), (
        f"{_GUARD_CORE} not found — this guard would be vacuous. guard_core.py "
        "is the source of truth for the Bash hook's SECRET_PATTERNS; if it "
        "moved, update _GUARD_CORE rather than deleting the check."
    )
    theirs = _guard_core_patterns()
    assert theirs, (
        "could not parse SECRET_PATTERNS from guard_core.py. That is a PARSER "
        "failure, not a pass — the literal may have changed shape (it must stay "
        "a list of (regex, label) tuples for the ast walk to see it). This exact "
        "silence is how the check sat broken after #276 moved the patterns."
    )
    ours = {rx.pattern for rx, _label in scrub.SECRET_PATTERNS}
    missing = [p for p in theirs if p not in ours]
    assert not missing, (
        "scrub.py drifted from guard_core.py — these SECRET_PATTERNS are no "
        f"longer covered: {missing}")
