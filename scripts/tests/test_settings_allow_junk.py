"""Deterministic junk gate for `~/.claude/settings.json` -> `permissions.allow`.

WHY THIS EXISTS
---------------
The allow-list grows by ACCRETION: every "yes, allow this" at a permission prompt
appends whatever text was in the prompt. When the text was not a command -- a
pasted YAML manifest, a heredoc body, a `curl -u user:pass` one-liner -- the
entry is captured verbatim anyway, and nothing ever removes it.

Measured on the workbench 2026-08-10, BEFORE the cleanup that motivated this
module: 248 entries, of which 38 were captured junk --

    33  YAML manifest fragments   `Bash(apiVersion: v1)`, `Bash(hostPath:)`, ...
     1  heredoc terminator        `Bash(EOF)`
     1  multi-line entry          a whole `git commit -m "$(cat <<EOF ...)"` body
     3  credential-bearing        two `curl -u <user>:<pass>` + one `<Password>` sed
     1  stale framework command   a `SlashCommand(/sc:...)` from a removed install

Both settings.json backups from 2026-06-05 and 2026-06-11 already contained all
38, so the junk had been accumulating for at least two months with nothing
looking at it.

Most of it is inert noise. The credential class is not: an allow-list is a
plaintext file that gets read, copied into backups, and quoted into bug reports,
so a password captured in a permission rule is a password published to every one
of those. Deleting the entries treats the symptom; THIS module is the structural
part, because the next pasted manifest will do it again.

🔴 WHY THIS DOES NOT `pytest.skip()` WHEN THE FILE IS ABSENT
------------------------------------------------------------
`~/.claude/settings.json` is outside the repo and does not exist in the nix
sandbox, so the obvious shape -- skip when `Path.home()` has no settings.json --
is a `$HOME`-keyed conditional skip. `scripts/run-tests.sh` GUARD 2 forbids
exactly that, twice, by name ("do not re-add ... Fix that instead"): such a skip
means one thing on a dev host and nothing at all in the tier that gates merges,
and it left the gate RED on main for two days the last time it was tried.

So the split is deliberate:

  * `test_detects_*` / `test_accepts_*` run against fixtures TRACKED IN THIS
    REPO. They exercise the detector itself, run in EVERY tier, and never skip.
    They are also the positive control: they prove the detector CAN fire, so a
    clean live result below is a measurement rather than a wiring accident.

  * `test_live_settings_*` reads the real file. When it is absent the test
    PASSES -- it is a legitimate "nothing to check here", not a skip, and it
    emits a note saying so.

⚠ SCOPE, STATED HONESTLY: in the hermetic tier the live check is vacuous, because
the file is never there. Its real tier is a dev host (`scripts/run-tests.sh`
locally, or the pre-push hook). The fixture tests are what carries this module in
CI. Do not read a green CI run as "the workbench allow-list is clean".

The patterns below are the SINGLE source of truth. Any other mention of them must
cross-reference this module rather than restate the literal.
"""
import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "settings_allow_junk"
LIVE_SETTINGS = Path.home() / ".claude" / "settings.json"

# --- the junk shapes ---------------------------------------------------------
#
# Each pattern is written to be SPELLING-INDEPENDENT where it can be: it pins the
# structural shape of the capture, not a specific word a future paste might not
# use. Every one was validated against all 248 live entries on 2026-08-10 and
# matched only the 38 junk ones -- zero false positives on the real list.

# A YAML mapping key captured as a command.
#
# 🔴 The load-bearing detail is the SPACE after the colon (or nothing at all).
# A permission rule for a real command is `Bash(mkdir:*)` / `Bash(git commit:*)`
# -- colon then the argument wildcard, never colon-space. A YAML key is
# `key:` or `key: value`. Without that distinction this pattern eats the entire
# legitimate allow-list. The character class carries `.` and `/` so that a
# label-shaped key (`kubernetes.io/hostname: node-a`) is caught too.
YAML_KEY = re.compile(r"^Bash\([A-Za-z_][A-Za-z0-9_./-]*:(\s+[^)]*)?\)$")

# A credential captured inline. Two shapes seen in the wild: curl's `-u user:pass`
# and a config-file password element carried through a `sed` expression.
#
# 🔴 THE FLAG IS NOT ALWAYS SPELLED `-u`. An earlier version of this pattern
# required a literal `-u` followed by whitespace, and therefore MISSED
# `curl -su admin:… `, `curl -sSu admin:… ` and `curl --user admin:… ` -- all
# ordinary curl, and all exactly what a permission prompt for a real command
# would show. Bundling short options is the common form, so the miss was in the
# most likely shape, in the one class where a miss actually costs something.
# Hence `-{1,2}[A-Za-z]*u(?:ser)?`: any short-flag bundle ending in `u`, or the
# long `--user`.
_CREDENTIAL_FLAG = re.compile(
    r"""(?<![\w-])              # start of a real token, not mid-word
        -{1,2}[A-Za-z]*u(?:ser)?  # -u | -su | -sSu | -Lu | --user
        [=\s]+['"]?
        (?P<cred>[^\s):'"]+:[^\s)'"]+)
    """,
    re.VERBOSE,
)

# ...but `-u <uid>:<gid>` is NOT a credential. `docker run -u 1000:1000` is a
# perfectly ordinary rule, and flagging it would be the expensive kind of false
# positive: it deletes a working permission. A purely numeric pair is the only
# carve-out -- `-u admin:1234` still counts, because the USER half is what makes
# it a login.
_UID_GID = re.compile(r"^\d+:\d+$")

# A password carried through a config-file edit (seen via a `sed` expression).
_PASSWORD_ELEMENT = re.compile(r"<[Pp]assword>[^<]*</[Pp]assword>")


def _credential_matches(entry: str) -> list[re.Match]:
    """Every inline-credential match in `entry`, minus the uid:gid carve-out."""
    out = [m for m in _PASSWORD_ELEMENT.finditer(entry)]
    out += [
        m
        for m in _CREDENTIAL_FLAG.finditer(entry)
        if not _UID_GID.match(m.group("cred"))
    ]
    return out

# A heredoc terminator that got its own rule. Matched as an exact literal rather
# than a pattern: `EOF` is a plausible substring of a real command, so anything
# looser would be a spelled guard rather than a structural one.
HEREDOC_TERMINATORS = frozenset({"Bash(EOF)", "Bash(EOT)"})


def classify(entry: str) -> str | None:
    """Return the junk class of `entry`, or None if it looks like a real rule.

    Ordering matters: the multi-line and credential checks run FIRST, because a
    captured heredoc body can also contain a YAML key, and the class a maintainer
    needs to see is the one that determines urgency.
    """
    if "\n" in entry or "\r" in entry:
        return "multi-line entry (a captured heredoc/command body)"
    if _credential_matches(entry):
        return "credential-bearing entry"
    if YAML_KEY.match(entry):
        return "YAML manifest fragment"
    if entry.strip() in HEREDOC_TERMINATORS:
        return "heredoc terminator"
    return None


def _allow_entries(path: Path) -> list[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("permissions", {}).get("allow", []) or []


def _findings(entries) -> list[tuple[str, str]]:
    out = []
    for e in entries:
        cls = classify(e)
        if cls:
            out.append((cls, e))
    return out


def _redact(entry: str) -> str:
    """Never echo a captured secret into test output or CI logs.

    Redacts by SPAN rather than by re-running a `sub` with the same pattern, so
    the uid:gid carve-out cannot cause a real credential to be printed: whatever
    `_credential_matches` decided was a credential is exactly what gets masked.
    """
    for m in sorted(_credential_matches(entry), key=lambda m: m.start(), reverse=True):
        entry = entry[: m.start()] + "<REDACTED-CREDENTIAL>" + entry[m.end() :]
    entry = entry.replace("\n", "\\n").replace("\r", "\\r")
    return entry[:120] + ("..." if len(entry) > 120 else "")


def _playbook(findings) -> str:
    listed = "\n".join(f"      [{cls}] {_redact(e)}" for cls, e in findings)
    return f"""
  {len(findings)} junk entr{'y' if len(findings) == 1 else 'ies'} in permissions.allow:

{listed}

  WHAT HAPPENED
    A permission prompt was answered "allow" while the Bash input held something
    that was not a command -- a pasted YAML manifest, a heredoc body, or a
    one-liner carrying a credential. Claude Code stores the prompt text verbatim,
    so the paste became a permanent rule. These entries grant nothing useful (no
    real command matches `Bash(metadata:)`), but they never expire either.

  HOW TO FIX
    1. Edit ~/.claude/settings.json by PARSING and RE-EMITTING the JSON --
       `json.dumps(data, indent=2, ensure_ascii=False) + "\\n"` reproduces the
       file byte-for-byte, so the diff is exactly the entries you removed.
       Do NOT hand-edit: this is the live config of every running Claude Code
       session, and a syntax error takes them all down.
    2. Copy the file aside first. Delete ONLY the flagged entries; leave every
       other key (hooks, statusLine, enabledPlugins, ...) untouched.
    3. 🔴 A credential-bearing finding means a secret was written to a plaintext
       config -- and to every backup and bug report that ever copied it. Removing
       the entry does not unpublish it. ROTATE the credential.
    4. Re-run this test.

  HOW TO AVOID IT
    At a permission prompt for a pasted manifest or heredoc, decline the rule and
    allow the single call instead -- the "don't ask again" answer is what writes
    the paste to disk. Patterns live in scripts/tests/test_settings_allow_junk.py.
"""


# --- guard the guard ---------------------------------------------------------


def test_fixtures_exist():
    """A moved/renamed fixture must not silently make the detector tests vacuous."""
    for name in ("settings_with_junk.json", "settings_clean.json"):
        assert (FIXTURES / name).is_file(), (
            f"{FIXTURES / name} not found -- the detector tests below would be "
            "testing nothing. If the fixtures moved, update FIXTURES in this module."
        )


def test_detects_every_junk_class_in_the_fixture():
    """POSITIVE CONTROL: the detector must fire, and fire on every class.

    Asserting a non-zero count is not enough -- one over-broad pattern could
    account for every hit while another is wired to nothing. So this pins the SET
    of classes, which fails if any single pattern stops matching.
    """
    findings = _findings(_allow_entries(FIXTURES / "settings_with_junk.json"))
    classes = {cls for cls, _ in findings}
    assert classes == {
        "multi-line entry (a captured heredoc/command body)",
        "credential-bearing entry",
        "YAML manifest fragment",
        "heredoc terminator",
    }, f"detector did not report every junk class; got {sorted(classes)}"
    # 8 YAML keys + 1 terminator + 1 multi-line + 3 credential-bearing.
    assert len(findings) == 13, (
        f"expected 13 junk entries in the fixture, got {len(findings)}: "
        f"{[_redact(e) for _, e in findings]}"
    )


def test_accepts_the_real_shapes_of_a_legitimate_allow_list():
    """NEGATIVE CONTROL, and the one that actually matters.

    The fixture is a sample of Zach's REAL allow-list shapes -- `Bash(mkdir:*)`,
    `Bash(git commit:*)`, `Bash(dig :*)`, `KUBECONFIG=... kubectl get:*`,
    `Read(//path/**)`. A pattern that eats any of these would delete working
    permissions, which is far worse than leaving noise in place.
    """
    findings = _findings(_allow_entries(FIXTURES / "settings_clean.json"))
    # Redacted before the assert, for the same reason as the live check below.
    redacted = [f"[{cls}] {_redact(e)}" for cls, e in findings]
    assert not redacted, (
        "FALSE POSITIVE -- these are legitimate permission rules:\n"
        + "\n".join("  " + r for r in redacted)
    )


def test_credential_detected_across_curl_flag_spellings():
    """REGRESSION. The credential class must not be keyed to one spelling of `-u`.

    Found by an independent mutation sweep built differently from the fixtures
    above: the original pattern required a literal `-u` + whitespace, so every
    BUNDLED short-flag form walked straight past it. `curl -sSu user:pass` is not
    an exotic invocation -- it is the form most people actually type.

    Expectations are literal, written from what curl accepts, NOT derived from
    the regex they test.
    """
    for cmd in (
        "curl -u admin:s3cret https://registry.example.invalid/v2/",
        "curl -su admin:s3cret https://registry.example.invalid/v2/",
        "curl -sSu admin:s3cret https://registry.example.invalid/v2/",
        "curl -k -s -u admin:s3cret https://registry.example.invalid/v2/",
        "curl --user admin:s3cret https://registry.example.invalid/v2/",
        'curl -u "admin:s3cret" https://registry.example.invalid/v2/',
    ):
        entry = f"Bash({cmd})"
        assert classify(entry) == "credential-bearing entry", (
            f"MISSED an inline credential: {cmd}"
        )
        assert "s3cret" not in _redact(entry), f"secret leaked into output: {cmd}"


def test_uid_gid_pair_is_not_treated_as_a_credential():
    """The other direction, and the more expensive one to get wrong.

    `docker run -u 1000:1000` is a real rule. Flagging it would tell a maintainer
    to delete a working permission and rotate a credential that does not exist.
    A purely numeric pair is the carve-out; a named user is still a credential.
    """
    for cmd in (
        "docker run -u 1000:1000 alpine:*",
        "docker run --user 1000:1000 alpine:*",
        "podman run -u 0:0 alpine:*",
    ):
        assert classify(f"Bash({cmd})") is None, f"FALSE POSITIVE on a uid:gid: {cmd}"
    # ...but the carve-out must not become a bypass.
    assert classify("Bash(curl -u admin:1234 https://x.example.invalid/)") == (
        "credential-bearing entry"
    ), "a named user with a numeric password is still a credential"


def test_credential_findings_are_redacted_in_output():
    """The failure message must not republish the secret it is complaining about.

    REGRESSION, and it covers BOTH output surfaces, because a sweep found the
    second one leaking while the first was clean:

      1. `_playbook(...)` -- the human-readable message.
      2. the value the ASSERTION binds. pytest prints the repr of an assert's
         operand, so `assert not findings` would dump the raw entry (secret and
         all) into the CI log directly underneath a playbook line that says
         `<REDACTED-CREDENTIAL>`. Both call sites bind a redacted list instead.
    """
    entry = "Bash(curl -s -u someuser:somesecret https://example.invalid/v2/)"
    assert classify(entry) == "credential-bearing entry"

    findings = [(classify(entry), entry)]
    assert "somesecret" not in _playbook(findings)

    # the exact expression each assert binds
    redacted = [f"[{cls}] {_redact(e)}" for cls, e in findings]
    assert "somesecret" not in repr(redacted), (
        "the assertion operand would leak the credential into pytest's output"
    )
    assert "<REDACTED-CREDENTIAL>" in repr(redacted)


# --- the live check ----------------------------------------------------------


def test_live_settings_allow_has_no_captured_junk():
    """Read the deployed ~/.claude/settings.json and fail on captured junk.

    Absent file == pass, deliberately and NOT via pytest.skip -- see the module
    docstring for why a $HOME-keyed skip is forbidden here. In the nix sandbox
    this branch is always the one taken, which is precisely why the fixture tests
    above exist.
    """
    if not LIVE_SETTINGS.is_file():
        print(f"note: {LIVE_SETTINGS} absent (expected in the hermetic tier) -- nothing to check")
        return

    try:
        entries = _allow_entries(LIVE_SETTINGS)
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"{LIVE_SETTINGS} is not valid JSON ({exc}). That file is the live "
            "config of every running Claude Code session -- repair it before "
            "anything else."
        ) from None

    findings = _findings(entries)
    # 🔴 Assert on the REDACTED list, never on `findings` itself.
    #
    # pytest rewrites `assert not findings` and prints the repr of the operand --
    # so asserting on the raw list republishes the very credential this module
    # redacts, right past `_playbook`, into the CI log. MEASURED: a sweep with a
    # bundled-flag credential mutant found the plaintext password in pytest's
    # output while the playbook line above it read `<REDACTED-CREDENTIAL>`.
    # Binding the redacted strings first makes the introspected operand safe.
    #
    # NB: deliberately no example password in this comment -- a literal here
    # shows up in pytest's echoed source and defeats any leak check grepping
    # the run output.
    redacted = [f"[{cls}] {_redact(e)}" for cls, e in findings]
    assert not redacted, (
        f"\n\n{LIVE_SETTINGS} has captured junk in permissions.allow.\n"
        f"  entries: {len(entries)}\n"
        f"{_playbook(findings)}"
    )
