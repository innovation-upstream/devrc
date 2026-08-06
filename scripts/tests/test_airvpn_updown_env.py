#!/usr/bin/env python3
"""`scripts/airvpn-updown` — the site-config resolution that decides whether the
LIVE killswitch keeps its nebula-lighthouse bypass.

WHAT IS AND IS NOT COVERED HERE — read this before trusting a green run
----------------------------------------------------------------------
🔴 These tests exercise the `check-env` path ONLY. `check-env` is read-only: it
touches no nft table, no route, no file. Nothing here arms, disarms or loads a
ruleset, and **nothing here is a substitute for the `bar` skill's mandatory
re-test protocol** (LAN session held open → k3s healthy → 4 nebula nodes → exit
IP is the tunnel → off-LAN ssh survives). That protocol needs a physically
reachable host and has NOT been run for this change.

So: green here means "the value resolves and the rejections fire". It does not
mean the killswitch works.

WHAT THESE ARE REGRESSION TESTS FOR
-----------------------------------
Two audit findings against the first version of the env-file change:

  * **A-2, FAIL-OPEN.** A malformed/truncated value (a partially-written env
    file) was interpolated straight into the nft ruleset, making it a SYNTAX
    ERROR — and because `up` does `nft add table` + `nft flush table` BEFORE
    loading, re-arming with a bad value DELETED a working killswitch and
    installed nothing, then exited 0. The blanket fallback carried the same
    value, so it failed identically.
  * **A-4, ROOT-SOURCED CONFIG.** The file was `.`-sourced as root with no
    owner/mode check and no constraint on what it could set, so a stray
    `LAN_SUBNET=` / `IFACE=` / `NEBULA_USER=` line rewrote the killswitch's
    constants, and any other shell in the file simply ran.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from testlib import mockbin  # noqa: E402

UPDOWN = REPO / "scripts" / "airvpn-updown"
APPLY = REPO / "nix" / "system" / "apply-airvpn-host.sh"

#: TEST-NET-1/3 (RFC 5737). Reserved for documentation, so these can be written
#: down in a public repo and are still real, parseable addresses — the point of
#: a control built from realistic rather than degenerate data.
GOOD_V4 = "203.0.113.9"
OTHER_V4 = "192.0.2.7"
GOOD_V6 = "2001:db8::1"


def _bash():
    import shutil
    return shutil.which("bash") or "/bin/sh"


def check_env(env_file: Path | None, extra=None):
    """Run `airvpn-updown check-env` and return (rc, stdout, stderr)."""
    env = dict(os.environ)
    env.pop("NEBULA_LIGHTHOUSE", None)
    if env_file is not None:
        env["AIRVPN_UPDOWN_ENV"] = str(env_file)
    env.update(extra or {})
    r = subprocess.run([_bash(), str(UPDOWN), "check-env"],
                       capture_output=True, text=True, env=env, timeout=30)
    return r.returncode, r.stdout, r.stderr


def write_env(tmp_path, text, mode=0o600, name="airvpn-updown.env") -> Path:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    p.chmod(mode)
    return p


# --- the happy path ------------------------------------------------------------

def test_a_valid_ipv4_resolves(tmp_path):
    rc, out, _ = check_env(write_env(tmp_path, f"NEBULA_LIGHTHOUSE={GOOD_V4}\n"))
    assert rc == 0, out
    assert f"lighthouse={GOOD_V4}" in out


def test_a_valid_ipv6_resolves(tmp_path):
    rc, out, _ = check_env(write_env(tmp_path, f"NEBULA_LIGHTHOUSE={GOOD_V6}\n"))
    assert rc == 0, out
    assert f"lighthouse={GOOD_V6}" in out


@pytest.mark.parametrize("body", [
    'NEBULA_LIGHTHOUSE="%s"\n' % GOOD_V4,
    "NEBULA_LIGHTHOUSE='%s'\n" % GOOD_V4,
    "  NEBULA_LIGHTHOUSE  =  %s  \n" % GOOD_V4,
    "# a comment\n\nNEBULA_LIGHTHOUSE=%s   # trailing\n" % GOOD_V4,
])
def test_ordinary_spellings_all_resolve(tmp_path, body):
    """Sourcing accepted these, so parsing must too — otherwise the change
    silently degrades a host whose file was written by hand."""
    rc, out, _ = check_env(write_env(tmp_path, body))
    assert rc == 0 and f"lighthouse={GOOD_V4}" in out, (rc, out)


def test_the_last_assignment_wins(tmp_path):
    """Matching what sourcing did — a second line is an override, not an error."""
    rc, out, _ = check_env(write_env(
        tmp_path, f"NEBULA_LIGHTHOUSE={OTHER_V4}\nNEBULA_LIGHTHOUSE={GOOD_V4}\n"))
    assert rc == 0 and f"lighthouse={GOOD_V4}" in out, out


# --- A-2: a malformed value must be UNSET, never interpolated ------------------

@pytest.mark.parametrize("value,why", [
    # 🔴 TEST-NET-3, deliberately. An earlier revision used the REAL lighthouse's
    # first three octets here — byte-identical to the prefix of the literal this
    # PR deletes — which would have narrowed it from "somewhere in Hetzner" to a
    # /24 in a PUBLIC repo, on merge. Neither gate can catch that: a 3-octet
    # prefix is not an address, so `is_reportable()` is False by construction.
    # Every literal in this file must be TEST-NET.
    ("203.0.113", "truncated quad — a partially-written file"),
    ("203.0.113.", "trailing dot"),
    ("203.0.113.999", "octet > 255"),
    ("$(id)", "command substitution"),
    ("`id`", "backticks"),
    ("203.0.113.9 accept; drop", "an nft rule smuggled in as the value"),
    ("203.0.113.9 203.0.113.10", "two values"),
    ("not-an-ip", "prose"),
    ("203.0.113.9/32", "a CIDR, not a host"),
    ("2001:db8::1%eth0", "an IPv6 zone/scope suffix"),
    ('"', "one stray quote — the truncation shape that started this"),
])
def test_a_malformed_value_is_treated_as_UNSET_and_says_why(tmp_path, value, why):
    """🔴 A-2. Every one of these made BOTH the primary ruleset and the blanket
    fallback fail to parse, and `up` flushes the table BEFORE loading — so a
    re-arm with any of them removed a working killswitch and installed nothing,
    exit 0. UNSET is the fail-SAFE outcome, and it must be announced."""
    rc, out, err = check_env(write_env(tmp_path, f"NEBULA_LIGHTHOUSE={value}\n"))
    assert rc == 1, (rc, out, err)
    assert "lighthouse=UNSET" in out, out
    assert "not an IP address" in err, err
    # 🔴 The rejected text must NEVER be echoed back — it is attacker-controlled
    # and the log line goes to syslog.
    assert value not in out and value not in err, (out, err)


def test_an_absent_key_is_UNSET_but_quiet(tmp_path):
    """Distinct from the case above: 'you did not set it' is the documented
    default, 'you set it to garbage' is an error. Same safe outcome, different
    signal — otherwise the operator cannot tell which one they are in."""
    rc, out, err = check_env(write_env(tmp_path, "SOMETHING_ELSE=1\n"))
    assert rc == 1 and "lighthouse=UNSET" in out
    assert "not an IP address" not in err, err


def test_a_missing_file_is_UNSET_not_a_crash(tmp_path):
    rc, out, _ = check_env(tmp_path / "does-not-exist.env")
    assert rc == 1 and "lighthouse=UNSET" in out, out


# --- A-4: the file is parsed, not sourced --------------------------------------

def test_the_file_cannot_clobber_the_killswitchs_own_constants(tmp_path):
    """🔴 A-4. Sourcing this as root rewrote LAN_SUBNET/IFACE/NEBULA_USER — i.e.
    rewrote the killswitch. Parsing means only NEBULA_LIGHTHOUSE can be read."""
    body = ("LAN_SUBNET=0.0.0.0/0\n"
            "IFACE=eth0\n"
            "NEBULA_USER=root\n"
            "LAN_ROUTER=203.0.113.254\n"
            f"NEBULA_LIGHTHOUSE={GOOD_V4}\n")
    rc, out, err = check_env(write_env(tmp_path, body))
    assert rc == 0 and f"lighthouse={GOOD_V4}" in out, (out, err)
    # none of the other keys leaked into the output the script produces
    assert "0.0.0.0/0" not in out and "eth0" not in out, out


def test_the_file_is_not_executed(tmp_path):
    """The strongest form: put a side effect in the file. Sourcing runs it;
    parsing cannot. Driven by a real filesystem effect, not by inspecting the
    source for a `.` character."""
    canary = tmp_path / "canary"
    body = (f"touch {canary}\n"
            f"NEBULA_LIGHTHOUSE={GOOD_V4}\n")
    rc, out, _ = check_env(write_env(tmp_path, body))
    assert rc == 0 and f"lighthouse={GOOD_V4}" in out, out
    assert not canary.exists(), (
        "the env file was EXECUTED — it is being sourced, not parsed")


@pytest.mark.parametrize("mode", [0o666, 0o622, 0o660, 0o620])
def test_a_group_or_other_writable_file_is_IGNORED(tmp_path, mode):
    """Whoever can write this file names a destination that gets BOTH a
    main-table bypass route AND an nft accept — a hole through the killswitch."""
    rc, out, err = check_env(
        write_env(tmp_path, f"NEBULA_LIGHTHOUSE={GOOD_V4}\n", mode=mode))
    assert rc == 1, (rc, out, err)
    assert "lighthouse=UNSET" in out, out
    assert "writable" in err, err


@pytest.mark.parametrize("mode", [0o600, 0o400, 0o640, 0o644])
def test_a_non_writable_mode_is_accepted(tmp_path, mode):
    """The complement — the check must not be so strict it rejects the modes an
    operator actually creates. Measured at both ends of the range rather than at
    the single value the docs suggest."""
    rc, out, err = check_env(
        write_env(tmp_path, f"NEBULA_LIGHTHOUSE={GOOD_V4}\n", mode=mode))
    assert rc == 0, (mode, out, err)


def _stat_stub(tmp_path, uid, mode):
    """A `stat` on PATH that reports a chosen owner/mode.

    🔴 WHY A STUB HERE AND REAL FILES ABOVE. The OWNER branches cannot be built
    from the filesystem: a test cannot chown, and the two tiers disagree about
    what exists — the first version of this test walked /etc for a root-owned
    file, passed on the dev host, and FAILED in the nix build sandbox, which has
    no /etc/passwd and where everything is owned by the build user. That is the
    two-tier hazard in RULES.md, found by running both tiers.

    So the decomposition is: real files cover MODE parsing (above), and this
    stub covers the OWNER comparison — including the foreign-uid branch, which
    no real filesystem a test controls can produce at all.
    """
    bindir = tmp_path / "stubbin"
    bindir.mkdir(exist_ok=True)
    mockbin.write_exec(bindir / "stat", f'printf "%s\\n" "{uid} {mode}"\n')
    return bindir


def test_a_root_owned_file_is_ACCEPTED(tmp_path):
    """The production case: uid 0 owns the file and the caller is not root."""
    f = write_env(tmp_path, f"NEBULA_LIGHTHOUSE={GOOD_V4}\n")
    bindir = _stat_stub(tmp_path, 0, 600)
    rc, out, err = check_env(f, extra={"PATH": f"{bindir}:{os.environ['PATH']}"})
    assert rc == 0, (rc, out, err)
    assert f"lighthouse={GOOD_V4}" in out, out
    assert "IGNORING" not in err, err


def test_a_file_owned_by_a_THIRD_party_is_IGNORED(tmp_path):
    """🔴 The branch that matters most and is hardest to build: the file is
    owned by neither root nor us. Whoever that is gets to name a destination
    that receives BOTH a main-table bypass route and an nft `accept`."""
    f = write_env(tmp_path, f"NEBULA_LIGHTHOUSE={GOOD_V4}\n")
    stranger = 4242 if os.geteuid() != 4242 else 4243
    bindir = _stat_stub(tmp_path, stranger, 600)
    rc, out, err = check_env(f, extra={"PATH": f"{bindir}:{os.environ['PATH']}"})
    assert rc == 1, (rc, out, err)
    assert "lighthouse=UNSET" in out, out
    assert "not root" in err and str(stranger) in err, err


def test_the_stat_stub_itself_is_load_bearing(tmp_path):
    """POSITIVE CONTROL for the two tests above. If the stub were not on PATH —
    a typo, a PATH that does not take effect — both would exercise the REAL
    stat and the self-owned accept, and both would still pass for the wrong
    reason. Drive it with a uid that must produce the OPPOSITE verdict from the
    file's real owner."""
    f = write_env(tmp_path, f"NEBULA_LIGHTHOUSE={GOOD_V4}\n")
    assert f.stat().st_uid == os.geteuid(), "fixture is not self-owned"
    # real stat would ACCEPT (self-owned 0600); the stub must make it REJECT
    bindir = _stat_stub(tmp_path, 4242, 600)
    rc, _out, err = check_env(f, extra={"PATH": f"{bindir}:{os.environ['PATH']}"})
    assert rc == 1 and "not root" in err, (
        "the stat stub is not being used — the owner tests prove nothing")


# --- the fallback ruleset ------------------------------------------------------

def test_the_blanket_fallback_carries_no_lighthouse():
    """🔴 A-2, structural half. `arm_failclosed` is the fallback for 'the primary
    ruleset did not parse'. Any value that could have broken that parse must not
    appear in it too, or both fail together and the uplink ends up with NO table.

    Asserted on the function BODY, not on the file — a match anywhere else in a
    600-line script would pass while the fallback stayed broken.
    """
    src = UPDOWN.read_text(encoding="utf-8")
    start = src.index("arm_failclosed() {")
    end = src.index("\n}\n", src.index("FB\n", start))
    body = src[start:end]
    assert "NEBULA_LIGHTHOUSE" not in body, (
        "the fallback ruleset interpolates the lighthouse again:\n" + body)
    # and it must still be a real fallback, not an empty one
    for required in ("meta skuid", "LAN_SUBNET", "drop"):
        assert required in body, f"the fallback lost its {required} rule"


def test_the_comment_describing_the_fallback_matches_the_code():
    """🔴 A comment is a claim. The previous one said the fallback had 'no
    dynamic … substitution', which this change made FALSE — and a maintainer
    reading a stale safety comment is how a guard gets deleted."""
    src = UPDOWN.read_text(encoding="utf-8")
    head = src[:src.index("arm_failclosed() {")]
    tail = head[head.rindex("# Blanket FAIL-CLOSED"):]
    assert "NO OPERATOR-SUPPLIED TEXT" in tail, (
        "the fallback's comment no longer states what the code does")


# --- the apply script (A-3) ----------------------------------------------------

def apply_lib(tmp_path, site_env: Path, env=None):
    """Source apply-airvpn-host.sh as a LIBRARY and call ensure_site_env."""
    e = dict(os.environ, AIRVPN_APPLY_LIB="1", AIRVPN_SITE_ENV=str(site_env))
    e.pop("NEBULA_LIGHTHOUSE", None)
    e.pop("ALLOW_NO_LIGHTHOUSE", None)
    e.update(env or {})
    r = subprocess.run(
        [_bash(), "-c", f'. "{APPLY}"; ensure_site_env'],
        capture_output=True, text=True, env=e, cwd=str(tmp_path), timeout=30)
    return r.returncode, r.stdout, r.stderr


def test_apply_refuses_when_the_site_config_is_absent(tmp_path):
    """🔴 A-3. A fresh-host apply used to install the helper and never create,
    chmod or check this file — shipping a silently degraded killswitch."""
    rc, out, err = apply_lib(tmp_path, tmp_path / "absent.env")
    assert rc == 1, (rc, out, err)
    assert "not found" in err and "NEBULA_LIGHTHOUSE=" in err, err


def test_apply_creates_the_file_0600_root_owned_from_the_env_var(tmp_path):
    f = tmp_path / "new.env"
    rc, out, err = apply_lib(tmp_path, f, env={"NEBULA_LIGHTHOUSE": GOOD_V4})
    assert rc == 0, (out, err)
    assert f.read_text().strip() == f"NEBULA_LIGHTHOUSE={GOOD_V4}"
    assert oct(f.stat().st_mode & 0o777) == "0o600", oct(f.stat().st_mode)


def test_apply_tightens_the_mode_of_an_existing_file(tmp_path):
    """The wg conf gets an explicit chown+chmod at step 1; this file got neither.
    An existing world-readable one must be fixed, not accepted."""
    f = write_env(tmp_path, f"NEBULA_LIGHTHOUSE={GOOD_V4}\n", mode=0o666)
    rc, out, err = apply_lib(tmp_path, f)
    assert rc == 0, (out, err)
    assert oct(f.stat().st_mode & 0o777) == "0o600", oct(f.stat().st_mode)


def test_apply_warns_when_an_existing_file_has_no_usable_value(tmp_path):
    f = write_env(tmp_path, "# empty\n")
    rc, out, err = apply_lib(tmp_path, f)
    assert rc == 0, (out, err)
    assert "WARNING" in err and "lighthouse=UNSET" in err, err


def test_apply_opt_out_is_explicit_and_says_what_is_lost(tmp_path):
    rc, out, err = apply_lib(tmp_path, tmp_path / "absent.env",
                             env={"ALLOW_NO_LIGHTHOUSE": "1"})
    assert rc == 0, (out, err)
    assert "lighthouse=UNSET" in err and "NOT preserved" in err, err


def test_the_apply_script_provisions_the_env_BEFORE_installing_the_helper():
    """🔴 A-3, the ORDERING half — and the actual defect. Installing the helper
    first and mentioning the file afterwards is how an operator following the
    script top-to-bottom re-arms before the file exists.

    An ordering guard, not a presence guard: swapping the two lines keeps every
    token in the file and would pass a `grep`-shaped assertion.
    """
    src = APPLY.read_text(encoding="utf-8")
    call = src.index("\nensure_site_env\n")
    install = src.index('install -m 0755 -o root -g root "${REPO}/scripts/airvpn-updown"')
    assert call < install, (
        "ensure_site_env runs AFTER airvpn-updown is installed — a fresh host "
        "gets a degraded killswitch")


def test_the_skill_documents_the_prerequisite_BEFORE_the_install_command():
    """The same ordering defect in the doc an operator actually follows: the
    requirement used to be a trailing sentence AFTER the install command."""
    doc = (REPO / "claude" / "skills" / "bar" / "airvpn.md").read_text(encoding="utf-8")
    prereq = doc.index("/etc/airvpn-updown.env")
    install = doc.index("sudo install -m 0755 -o root -g root")
    assert prereq < install, (
        "airvpn.md introduces the env file only after telling the operator to "
        "install and re-arm")


# --- the seam ------------------------------------------------------------------

def test_check_env_is_read_only():
    """`check-env` exists so this file can test the resolution without arming.
    If it ever grew a side effect, every test above would be running against a
    live killswitch path. Pin the SHAPE of the branch, structurally."""
    src = UPDOWN.read_text(encoding="utf-8")
    start = src.index("    check-env)")
    end = src.index("        ;;", start)
    # CODE only — a comment explaining "touches no nft table" would otherwise
    # satisfy a naive substring search for `nft `, which is the spelled-vs-
    # structural trap (RULES.md).
    code = "\n".join(l for l in src[start:end].splitlines()
                     if not l.strip().startswith("#"))
    for forbidden in ("nft ", "ip route", "ip rule", "rm -f", "install ",
                      "> ", ">>"):
        assert forbidden not in code, (
            f"check-env is no longer read-only ({forbidden!r}):\n{code}")
    # …and it must still actually do the one thing it exists for.
    assert "NEBULA_LIGHTHOUSE" in code and "echo " in code, code


def test_the_lighthouse_is_never_a_literal_in_this_repo():
    """The reason the env file exists at all. `scripts/tests/test_no_public_ips.py`
    is the repo-wide gate; this is the local seam assertion, so a change that
    re-inlines the value fails in the file that owns the decision too."""
    sys.path.insert(0, str(REPO / "scripts"))
    sys.path.insert(0, str(REPO / "scripts" / "claude-hooks"))
    from testlib import public_ip_scan as S  # noqa: E402
    hits = [h for h in S.scan_file(UPDOWN)]
    assert not hits, f"airvpn-updown carries a routable IP literal again: {hits}"
