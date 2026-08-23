"""`nix/system/apply-tmp-churn-retention.sh` — the tmpfiles rule ledger.

The script itself cannot be executed here: it refuses a non-root EUID and writes a
hardcoded `/etc/nixos/configuration.nix`. What CAN be exercised, and is the part
that decides what lands on disk, is the `python3 … <<'PY'` heredoc it runs. These
tests extract that heredoc verbatim from the shipped file and run it against
fixture configs, so a change to the script changes what is under test — there is
no second copy of the rules here to drift out of sync.

Two properties are pinned that prose alone got wrong once already:

  1. **A re-run on an ALREADY-APPLIED host still receives a newly-added rule.**
     The original script gated the whole edit on `grep -qF "$MARKER"`, i.e. on the
     presence of a *comment*. Any rule added later was therefore unreachable on
     every host that had already run it: the run printed "already present" and
     exited 0 over a config missing the new glob. That is the shape of a guard
     that reads as coverage while providing none.

  2. **`nix-shell-*` cannot match `nix-shell.<mktemp>`.** A hyphen is a literal.
     The two spellings are two globs and nix-shell emits both.
"""

from __future__ import annotations

import fnmatch
import re
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[2] / "nix" / "system" / "apply-tmp-churn-retention.sh"
)

ANCHOR = "  systemd.tmpfiles.rules = [\n"

# A config shaped like the real one, reduced to what the inserter reads.
FRESH_CONFIG = f"""{{ config, pkgs, ... }}:
{{
  networking.hostName = "workbench";

{ANCHOR}    "d /run/example 0755 root root -"
  ];
}}
"""

# The glob added after the script had already shipped. Case 1 above is the reason
# this constant is spelled out separately rather than read from the ledger: the
# test must know which rule is the LATE one.
LATE_RULE = '"e /tmp/nix-shell.* - - - m:7d"'


def _script_text() -> str:
    return SCRIPT.read_text()


def _extract_inserter() -> str:
    """The body of the `python3 - "$CFG" <<'PY' … PY` heredoc, verbatim."""
    text = _script_text()
    m = re.search(r"^python3 - \"\$CFG\" <<'PY'\n(.*?)^PY$", text, re.M | re.S)
    assert m, "could not find the python heredoc — has the script's shape changed?"
    body = m.group(1)
    assert "systemd.tmpfiles.rules" in body, "extracted the wrong block"
    return body


def _python_rules() -> list[str]:
    """The RULES ledger as the inserter itself defines it."""
    body = _extract_inserter()
    m = re.search(r"^RULES = \[\n(.*?)^\]$", body, re.M | re.S)
    assert m, "could not find the RULES ledger in the inserter"
    return re.findall(r"'\s*(\"e /tmp/.*?\")\s*'", m.group(1))


def _shell_verified_rules() -> list[str]:
    """The rules the script re-reads off disk after writing, in its `for` loop."""
    text = _script_text()
    m = re.search(r"^for _rule in \\\n(.*?)^do$", text, re.M | re.S)
    assert m, "could not find the post-write verification loop"
    return re.findall(r"'(e /tmp/.*?)'", m.group(1))


def _run_inserter(tmp_path: Path, config_text: str) -> tuple[int, str, str, str]:
    """Run the extracted inserter against a fixture config. Returns rc/out/err/text."""
    cfg = tmp_path / "configuration.nix"
    cfg.write_text(config_text)
    src = tmp_path / "inserter.py"
    src.write_text(_extract_inserter())
    proc = subprocess.run(
        [sys.executable, str(src), str(cfg)],
        capture_output=True,
        text=True,
    )
    return proc.returncode, proc.stdout, proc.stderr, cfg.read_text()


# ---------------------------------------------------------------- the ledger --


def test_the_shell_verification_ledger_equals_the_python_rule_ledger():
    """A rule written but never re-read off disk is unverified; a rule verified but
    never written is a permanently-failing check. The two lists must be the SAME
    set — this fails when either grows or shrinks, not merely when one is empty."""
    written = {r.strip('"') for r in _python_rules()}
    verified = set(_shell_verified_rules())

    assert written, "the python RULES ledger parsed empty — the extractor is broken"
    assert verified, "the shell verification loop parsed empty — the extractor is broken"
    assert written == verified, (
        "the rules the script WRITES and the rules it VERIFIES have diverged.\n"
        f"  written but not verified: {sorted(written - verified)}\n"
        f"  verified but not written: {sorted(verified - written)}"
    )


def test_the_late_rule_is_actually_in_the_ledger():
    """Positive control for the two tests below: they are only meaningful if the
    rule they are about is present at all."""
    assert LATE_RULE in _python_rules()


# ------------------------------------------------------- hyphen is a literal --


def test_the_hyphen_glob_cannot_match_the_dot_form():
    """Why a second nix-shell rule has to exist. `fnmatch` here stands in for
    systemd-tmpfiles' own glob(3) matching, which shares the semantics that
    matter: `-` is a literal, `*` does not cross the boundary backwards."""
    dot_form = "/tmp/nix-shell.QcVn1oNwEG"
    hyphen_form = "/tmp/nix-shell-3304470-3498967483"

    assert not fnmatch.fnmatchcase(dot_form, "/tmp/nix-shell-*"), (
        "if this ever passes, the hyphen rule covers the dot form and the second "
        "rule is dead — delete it rather than leaving two rules for one case"
    )
    assert fnmatch.fnmatchcase(dot_form, "/tmp/nix-shell.*")
    assert fnmatch.fnmatchcase(hyphen_form, "/tmp/nix-shell-*")


def test_both_nix_shell_spellings_are_covered_by_the_shipped_ledger():
    """The behavioural form of the test above, against the real ledger rather than
    against two literals: every spelling nix-shell emits must be matched by SOME
    shipped rule. Deleting either rule fails this."""
    globs = [r.strip('"').split()[1] for r in _python_rules()]
    for path in ("/tmp/nix-shell.QcVn1oNwEG", "/tmp/nix-shell-3304470-3498967483"):
        assert any(fnmatch.fnmatchcase(path, g) for g in globs), (
            f"{path} is matched by no shipped rule; globs = {globs}"
        )


# ------------------------------------------------------------- the inserter --


def test_a_fresh_config_receives_every_rule_and_the_comment_header(tmp_path):
    rc, out, err, text = _run_inserter(tmp_path, FRESH_CONFIG)

    assert rc == 0, f"inserter failed: {err}"
    for rule in _python_rules():
        assert rule in text, f"{rule} missing from the written config"
    assert "/tmp churn retention" in text, "comment header not written"
    assert f"inserted {len(_python_rules())} of {len(_python_rules())}" in out


def test_a_second_run_changes_nothing(tmp_path):
    """Idempotence, asserted on the BYTES rather than on the message."""
    rc, _, err, once = _run_inserter(tmp_path, FRESH_CONFIG)
    assert rc == 0, err

    rc2, out2, err2, twice = _run_inserter(tmp_path, once)
    assert rc2 == 0, err2
    assert twice == once, "a second run modified the config"
    assert "nothing to insert" in out2


def test_an_already_applied_host_still_receives_a_newly_added_rule(tmp_path):
    """🔴 THE REGRESSION TEST for defect 1 in this module's docstring.

    Fixture = a config that already carries the comment header and every rule
    EXCEPT the late one, i.e. exactly the state of a host that ran the script
    before that rule existed. The marker-gated version of this script skipped the
    edit entirely on such a host and reported success.
    """
    seeded = FRESH_CONFIG.replace(
        ANCHOR,
        ANCHOR
        + "    # /tmp churn retention (2026-08-15). mtime-ONLY ageing (`m:`), because\n"
        + "".join(
            f"    {r}\n" for r in _python_rules() if r != LATE_RULE
        ),
        1,
    )
    assert LATE_RULE not in seeded, "fixture built wrong — the late rule is present"
    assert "/tmp churn retention" in seeded, "fixture built wrong — no marker"

    rc, out, err, text = _run_inserter(tmp_path, seeded)

    assert rc == 0, f"inserter failed: {err}"
    assert LATE_RULE in text, (
        "an already-applied host did NOT receive the newly-added rule — the skip "
        "is keyed on the comment header again"
    )
    assert f"inserted 1 of {len(_python_rules())}" in out, out
    assert text.count("/tmp churn retention (2026-08-15)") == 1, (
        "the comment header was duplicated onto a config that already had it"
    )


def test_a_config_without_the_anchor_is_refused_not_silently_skipped(tmp_path):
    rc, _out, err, text = _run_inserter(tmp_path, FRESH_CONFIG.replace(ANCHOR, ""))

    assert rc != 0, "a config with no rules list was accepted"
    assert "systemd.tmpfiles.rules" in err
    assert "e /tmp/nix-shell" not in text, "the config was mutated on the error path"


@pytest.mark.parametrize(
    "protected",
    [
        "/tmp/wt-apps-ui-3497",
        "/tmp/claude-1000",
        "/tmp/nix-shellish-not-really",
    ],
)
def test_no_shipped_rule_matches_a_path_that_must_never_be_reaped(protected):
    """The script's own header promises live worktrees and Claude scratchpads are
    out of scope. That promise is only as good as the globs — a rule broadened by
    one character could take them, and nothing else would notice."""
    globs = [r.strip('"').split()[1] for r in _python_rules()]
    hits = [g for g in globs if fnmatch.fnmatchcase(protected, g)]
    assert not hits, f"{protected} would be reaped by {hits}"
