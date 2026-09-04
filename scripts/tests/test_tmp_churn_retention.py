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
LATE_RULE = '"e /tmp/nix-shell.* - - - mM:7d"'


def _script_text() -> str:
    return SCRIPT.read_text()


def _extract_inserter() -> str:
    """The body of the `python3 - "$CFG" "${TMPFILES_RULES[@]}" <<'PY' … PY` heredoc.

    The ledger moved OUT of this heredoc on 2026-09-01 and is now a bash array
    passed on argv, so the inserter and the post-write verifier read one
    definition. This regex therefore tolerates arguments after "$CFG" — but see
    `test_the_inserter_and_the_verifier_read_ONE_ledger`, which pins that they
    both actually reference it.
    """
    text = _script_text()
    m = re.search(r"^python3 - \"\$CFG\"[^\n]*<<'PY'\n(.*?)^PY$", text, re.M | re.S)
    assert m, "could not find the python heredoc — has the script's shape changed?"
    body = m.group(1)
    assert "systemd.tmpfiles.rules" in body, "extracted the wrong block"
    return body


def _bash_ledger() -> list[str]:
    """The single TMPFILES_RULES ledger, as the shipped script defines it."""
    text = _script_text()
    m = re.search(r"^TMPFILES_RULES=\(\n(.*?)^\)$", text, re.M | re.S)
    assert m, "could not find the TMPFILES_RULES ledger — has the script's shape changed?"
    return re.findall(r"^\s*'(e /tmp/[^']*)'", m.group(1), re.M)


def _python_rules() -> list[str]:
    """The ledger in the quoted form the inserter writes into configuration.nix.

    Reads the SAME array the script passes on argv, so there is still no second
    copy of the rules here to drift out of sync.
    """
    return [f'"{r}"' for r in _bash_ledger()]


def _verifier_iterates_the_ledger() -> bool:
    """Does the post-write verification loop iterate the ONE ledger?

    It used to carry its own hardcoded copy of the rules, which this module
    compared against the inserter's. That comparison is obsolete now that there is
    a single array — but the property it protected is not, so it is pinned
    structurally instead: a verifier that stopped reading TMPFILES_RULES would
    once again be able to "verify" a rule set it never looked at.
    """
    return re.search(
        r'^for _rule in "\$\{TMPFILES_RULES\[@\]\}"; do$', _script_text(), re.M
    ) is not None


def _run_inserter(tmp_path: Path, config_text: str) -> tuple[int, str, str, str]:
    """Run the extracted inserter against a fixture config. Returns rc/out/err/text."""
    cfg = tmp_path / "configuration.nix"
    cfg.write_text(config_text)
    src = tmp_path / "inserter.py"
    src.write_text(_extract_inserter())
    proc = subprocess.run(
        [sys.executable, str(src), str(cfg), *_bash_ledger()],
        capture_output=True,
        text=True,
    )
    return proc.returncode, proc.stdout, proc.stderr, cfg.read_text()


# ---------------------------------------------------------------- the ledger --


def test_the_inserter_and_the_verifier_read_ONE_ledger():
    """A rule written but never re-read off disk is unverified; a rule verified but
    never written is a permanently-failing check.

    Until 2026-09-01 those were two hardcoded lists and this test compared them as
    SETS. They are now one bash array, so the property is pinned at its source
    instead: the ledger exists and is non-empty, the inserter is handed it on
    argv, and the verifier iterates that same array. Any of the three breaking
    re-opens the divergence this test has always been about.
    """
    ledger = _bash_ledger()
    assert ledger, "the TMPFILES_RULES ledger parsed empty — the extractor is broken"

    assert re.search(
        r'^python3 - "\$CFG" "\$\{TMPFILES_RULES\[@\]\}" <<\'PY\'$',
        _script_text(),
        re.M,
    ), "the inserter is no longer handed TMPFILES_RULES on argv"

    assert _verifier_iterates_the_ledger(), (
        "the post-write verification loop no longer iterates TMPFILES_RULES — it "
        "can now 'verify' a rule set it never looked at"
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


# ------------------------------------------------------- the eviction block --
# 🔴 These exist because an audit mutated the WHOLE eviction block away and all
# 11 tests still passed: the PR's central new payload — code that DELETES LINES
# FROM /etc/nixos — had no coverage at all. Each test below kills that mutant.


def _seed(rules: list[str], *, user_list: str = "", trailing_comment: bool = False) -> str:
    body = "".join(
        f"    {r}" + ("  # added 2026-08-15\n" if trailing_comment else "\n") for r in rules
    )
    user = f"  systemd.user.tmpfiles.rules = [\n{user_list}  ];\n" if user_list else ""
    return f"{{ ... }}:\n{{\n{user}{ANCHOR}{body}    \"d /var/tmp 1777 root root 30d\"\n  ];\n}}\n"


def test_a_stale_variant_of_a_ledger_rule_is_evicted(tmp_path):
    """Without eviction the old line stays ABOVE the new one, and systemd takes the
    FIRST line for a path — so the corrected rule is inert while reading as applied."""
    stale = '"e /tmp/nix-shell.* - - - m:7d"'
    rc, out, err, text = _run_inserter(tmp_path, _seed([stale]))

    assert rc == 0, err
    assert stale not in text, "the stale m:7d line survived — the new rule is inert"
    assert LATE_RULE in text
    assert "evicted" in out


def test_eviction_does_not_reach_outside_systemd_tmpfiles_rules(tmp_path):
    """MEASURED defect: the regex ran over the whole file, so a rule for a ledger
    path in `systemd.user.tmpfiles.rules` — a different unit's list this script has
    nothing to do with — was deleted."""
    user_rule = '    "e /tmp/go-build* - - - 30d"\n'
    rc, _out, err, text = _run_inserter(
        tmp_path, _seed(['"e /tmp/go-build* - - - m:7d"'], user_list=user_rule)
    )

    assert rc == 0, err
    assert user_rule.strip() in text, (
        "a rule in systemd.user.tmpfiles.rules was evicted — eviction is not scoped"
    )
    assert '"e /tmp/go-build* - - - m:7d"' not in text, "the system-list stale rule survived"


def test_a_trailing_comment_does_not_defeat_eviction(tmp_path):
    """The first regex ended `.*"\\n`, so a comment after the closing quote made the
    line unmatchable and the config ended carrying BOTH spellings for one path."""
    rc, _out, err, text = _run_inserter(
        tmp_path, _seed(['"e /tmp/go-build* - - - m:7d"'], trailing_comment=True)
    )

    assert rc == 0, err
    assert "m:7d" not in text.replace("mM:7d", ""), "a commented stale line survived eviction"


def test_eviction_leaves_unrelated_rules_alone(tmp_path):
    rc, _out, err, text = _run_inserter(tmp_path, _seed(['"e /tmp/nix-shell.* - - - m:7d"']))

    assert rc == 0, err
    assert '"d /var/tmp 1777 root root 30d"' in text, "an unrelated rule was evicted"


def test_every_ledger_glob_names_a_directory_form(tmp_path):
    """`e` acts on a directory's CONTENTS and silently ignores a plain file, so a
    glob whose real matches are files is a DEAD RULE. `homelab-talos-prs-*` was one
    for its whole life — 821 matches, 0 directories — and the coverage table hid it
    by labelling a file count 'dirs'. This pins the withdrawal."""
    globs = [r.strip('"').split()[1] for r in _python_rules()]
    assert not any("homelab-talos-prs" in g for g in globs), (
        "homelab-talos-prs-* is back in the ledger; it matches only plain files"
    )
