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


def check_env(env_file: Path | None, extra=None, timeout=30):
    """Run `airvpn-updown check-env` and return (rc, stdout, stderr)."""
    env = dict(os.environ)
    env.pop("NEBULA_LIGHTHOUSE", None)
    if env_file is not None:
        env["AIRVPN_UPDOWN_ENV"] = str(env_file)
    env.update(extra or {})
    r = subprocess.run([_bash(), str(UPDOWN), "check-env"],
                       capture_output=True, text=True, env=env, timeout=timeout)
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


@pytest.mark.parametrize("v6", [
    # 🔴 Every v6 here is either a non-address or inside 2001:db8::/32. An
    # arbitrary 8-group literal is GLOBALLY ROUTABLE and this repo is public;
    # one slipped in here and `test_no_public_ips.py` caught it in the SANDBOX
    # tier only, because running this file alone never loads that gate. Then the
    # COMMENT explaining the mistake re-spelled the same literal and was caught
    # again — hence the deliberately value-free wording.
    GOOD_V6, "::", ":::::", "0::0::0", "2001:db8:0:0:0:0:0:1", "fe80::1",
])
def test_an_IPv6_value_is_REJECTED_because_the_ruleset_cannot_express_it(tmp_path, v6):
    """🔴 REVERSED from the previous revision, which asserted v6 RESOLVES.

    That test blessed an outage. The consumers are `ip daddr <v>` (an IPv4-family
    nft match) and `ip route replace <v> via <IPv4 gateway>`. A v6 value passes a
    naive "is this an address" check, then fails the nft LOAD — which used to
    flush the table first and fall through to `arm_failclosed`, a ruleset with no
    `meta mark` accept, so wg's own encrypted packets are dropped and THE TUNNEL
    DIES. `::`, `:::::` and `0::0::0` were all accepted too.

    Validate against what the ruleset can EXPRESS, not against what looks like an
    address. If v6 lighthouses are ever needed, the emitter has to grow an
    `ip6 daddr` branch and a v6 route first — and this test is where that starts.
    """
    rc, out, err = check_env(write_env(tmp_path, f"NEBULA_LIGHTHOUSE={v6}\n"))
    assert rc == 1, (rc, out, err)
    assert "lighthouse=UNSET" in out and "state=rejected-or-empty" in out, out
    assert "not a canonical IPv4 address" in err, err


@pytest.mark.parametrize("v4", [
    "010.0.0.1", "00.0.0.1", "203.0.113.09", "0203.0.113.9", "203.00.113.9",
])
def test_a_LEADING_ZERO_octet_is_REJECTED(tmp_path, v4):
    """🔴 Two consumers disagree about what a zero-prefixed octet means:
    `getent`/inet_aton read it as OCTAL, nft reads it as decimal. Accepting one
    means the route and the firewall rule name DIFFERENT hosts. (The resolved
    octal value is deliberately not written here — it is a routable address and
    this repo is public; `test_no_public_ips.py` flagged it when it was.)

    A zero-prefixed octet like `…09` was previously rejected only by ACCIDENT —
    bash raised 'value too great for base' inside the arithmetic, which is a
    crash, not a decision.

    🔴 ATTRIBUTION, corrected after an audit measured it (M5): what rejects
    these is the LEADING-ZERO CHECK, which fires first. `10#` is currently
    UNREACHABLE for zero-prefixed input and makes no behavioural difference —
    an earlier version of this docstring credited it with the protection, which
    would have led a maintainer to delete the check that actually works. The
    assertion below pins the OUTCOME (rejected by a decision, not by a crash),
    which is true whichever of the two guards fires; it does not attribute.
    """
    rc, out, err = check_env(write_env(tmp_path, f"NEBULA_LIGHTHOUSE={v4}\n"))
    assert rc == 1, (rc, out, err)
    assert "lighthouse=UNSET" in out, out
    assert "not a canonical IPv4 address" in err, err
    assert "value too great" not in err, (
        "rejected by a bash arithmetic CRASH, not by the validator: " + err)


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
    fallback fail to parse, and `up` flushed the table BEFORE loading — so a
    re-arm with any of them removed a working killswitch and installed nothing,
    exit 0. UNSET is the fail-SAFE outcome, and it must be announced."""
    rc, out, err = check_env(write_env(tmp_path, f"NEBULA_LIGHTHOUSE={value}\n"))
    assert rc == 1, (rc, out, err)
    assert "lighthouse=UNSET" in out, out
    assert "not a canonical IPv4 address" in err, err
    # 🔴 The rejected text must NEVER be echoed back — it is attacker-controlled
    # and the log line goes to syslog.
    assert value not in out and value not in err, (out, err)


# --- F3: the shape A-2 is NAMED for was the one shape the guard was silent on --

@pytest.mark.parametrize("body,expect_ok", [
    (f"NEBULA_LIGHTHOUSE={GOOD_V4}", True),
    (f"# note\nNEBULA_LIGHTHOUSE={GOOD_V4}", True),
    (f"NEBULA_LIGHTHOUSE={OTHER_V4}\nNEBULA_LIGHTHOUSE={GOOD_V4}", True),
])
def test_a_file_with_NO_TRAILING_NEWLINE_still_resolves(tmp_path, body, expect_ok):
    """🔴 F3 — THE regression this whole guard is named after.

    `read` returns non-zero on a final line with no trailing newline, so the
    plain `while IFS= read -r line` loop DROPPED it. A file whose write was cut
    short is precisely a file with no final newline, so
    `NEBULA_LIGHTHOUSE=<ip>` with no `\\n` resolved to EMPTY and fell into the
    "key simply absent" branch — **UNSET with no log line at all**, while the
    PR body claimed "an unparseable value is treated as UNSET, loudly… a
    partially-written env file was enough".

    Mutant X4 (drop the final line) survived all 44 tests of the previous
    revision. This is the coverage that kills it.
    """
    p = tmp_path / "nonl.env"
    p.write_text(body, encoding="utf-8")  # deliberately no trailing newline
    p.chmod(0o600)
    assert not p.read_bytes().endswith(b"\n"), "fixture must have no final newline"
    rc, out, err = check_env(p)
    assert rc == 0, (rc, out, err)
    assert f"lighthouse={GOOD_V4}" in out, out


@pytest.mark.parametrize("tail", [b"\r", b"\r\n"])
def test_CRLF_resolves_rather_than_silently_UNSETTING(tmp_path, tail):
    """CRLF shares F3's root cause (a `\\r`-only tail is also "no final LF"), and
    the audit flagged it alongside.

    MEASURED, and the outcome is better than I first assumed: the rtrim already
    strips `\\r` (it is `[:space:]`), so a CRLF file — with or without a final
    LF — resolves CORRECTLY rather than being rejected. Both tails are pinned so
    that stays true; the failure being fixed is SILENCE, and neither of these is
    silent.
    """
    p = tmp_path / "crlf.env"
    p.write_bytes(f"NEBULA_LIGHTHOUSE={GOOD_V4}".encode() + tail)
    p.chmod(0o600)
    rc, out, err = check_env(p)
    assert rc == 0, (rc, out, err)
    assert f"lighthouse={GOOD_V4} state=ok" in out, out


def test_an_absent_key_says_so_rather_than_saying_nothing(tmp_path):
    """Distinct from a rejected value: 'you did not set it' vs 'you set it to
    garbage'. Same safe outcome, different signal — and after F3 BOTH are
    announced, because a silent UNSET is indistinguishable from a truncated
    file, which is exactly how F3 hid."""
    rc, out, err = check_env(write_env(tmp_path, "SOMETHING_ELSE=1\n"))
    assert rc == 1 and "lighthouse=UNSET" in out
    assert "state=rejected-or-empty" in out, out
    assert "has no NEBULA_LIGHTHOUSE= line" in err, err
    assert "not a canonical IPv4 address" not in err, err


def test_a_missing_file_is_UNSET_and_says_ABSENT(tmp_path):
    rc, out, err = check_env(tmp_path / "does-not-exist.env")
    assert rc == 1 and "lighthouse=UNSET" in out, out
    assert "state=absent" in out, out


# --- G4: a non-regular file must not HANG or emit a raw shell error ------------

def test_a_FIFO_at_the_env_path_does_not_hang(tmp_path):
    """🔴 G4. The read runs at TOP LEVEL, which in production is inside
    wg-quick's PostUp as root. A FIFO there blocked FOREVER (measured
    unbounded), so `wg-quick up` stalled with the interface UP and the
    killswitch NOT YET ARMED — an indefinite fail-OPEN window, and a NEW
    failure mode: the pre-env-file script read no file at all.

    The timeout here is the assertion. If this test ever hangs, it has found
    the bug back.
    """
    fifo = tmp_path / "fifo.env"
    os.mkfifo(fifo, 0o600)
    try:
        # A short timeout on purpose: the correct path returns in milliseconds,
        # and the bug is UNBOUNDED, so waiting longer buys nothing.
        rc, out, err = check_env(fifo, timeout=5)
    except subprocess.TimeoutExpired:  # pragma: no cover - the bug itself
        pytest.fail("reading the env path BLOCKED — G4 has regressed")
    finally:
        # 🔴 UNBLOCK ANY SURVIVOR, ALWAYS. `subprocess.run` kills the direct
        # child on timeout, but a shell blocked in `open()` on a FIFO with no
        # writer can outlive it — MEASURED IN THE WILD: 30 `check-env`
        # processes from this suite were found still blocked, the oldest for 77
        # minutes, after a mutation run that removed the `-f` gate. Opening the
        # write end non-blocking releases the reader; unlinking stops anything
        # later from blocking on it again. This runs on the GREEN path too,
        # where it is a no-op — a cleanup that only fires on failure is a
        # cleanup that is never exercised.
        try:
            os.close(os.open(fifo, os.O_WRONLY | os.O_NONBLOCK))
        except OSError:
            pass
        fifo.unlink(missing_ok=True)
    assert rc == 1, (rc, out, err)
    assert "state=not-a-regular-file" in out, out
    assert "not a regular file" in err, err


def test_a_DIRECTORY_at_the_env_path_is_rejected_cleanly(tmp_path):
    """It used to emit a raw `read: 0: read error: Is a directory` and then
    silently UNSET — a shell error message is not a diagnosis."""
    d = tmp_path / "dir.env"
    d.mkdir()
    rc, out, err = check_env(d)
    assert rc == 1, (rc, out, err)
    assert "state=not-a-regular-file" in out, out
    assert "read error" not in err, ("raw shell error leaked: " + err)


# --- G3: unreadable is NOT the same as absent ---------------------------------

def test_an_UNREADABLE_file_says_so_rather_than_looking_UNSET(tmp_path):
    """🔴 G3. `check-env` is the ONE diagnostic the operator protocol tells you
    to trust. Running it WITHOUT sudo on a correct 0600 root:root host is the
    likeliest way to see UNSET, and reporting that identically to 'you never
    created the file' sends the operator to fix the wrong thing."""
    if os.geteuid() == 0:  # pragma: no cover - the sandbox runs unprivileged
        pytest.skip("root can read anything; the distinction is unobservable")
    p = write_env(tmp_path, f"NEBULA_LIGHTHOUSE={GOOD_V4}\n", mode=0o000)
    rc, out, err = check_env(p)
    assert rc == 1, (rc, out, err)
    assert "state=unreadable-by-uid-" in out, out
    assert "not readable" in err and "run as root" in err, err


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


# --- the RENDER harness: what the kernel would actually have been handed -------
#
# 🔴 WHY THIS EXISTS. The previous revision asserted the fallback "carries no
# lighthouse" by grepping the function body for the string `NEBULA_LIGHTHOUSE`.
# That is a SPELLED guard, and the audit's mutant Y1 walked straight past it:
# assign `_FB_LH="${NEBULA_LIGHTHOUSE}"` OUTSIDE the function and interpolate
# `${_FB_LH}` inside — the token is gone, the hazard is fully restored, the test
# is green. RULES.md: "can the guard pass while the hazard exists in a different
# shape?" It could.
#
# So assert the PROPERTY instead: render the rulesets with the lighthouse set to
# a distinctive sentinel and require that the fallback's actual TEXT does not
# contain it, whatever route it took to get there.
#
# Nothing real is touched. `nft`, `ip`, `wg` and `logger` are all stubs; the stub
# `nft` records every invocation's argv and stdin, and NEVER commits anything.

_SENTINEL = "198.51.100.77"   # TEST-NET-2, distinctive, and a valid IPv4


def _up_harness(tmp_path, nft_fail="", ks_present=True, lighthouse=_SENTINEL,
                extra_path=None):
    """Run `airvpn-updown up airvpn` with every external command stubbed.

    Returns (rc, stdout, syslog_text, [captured nft calls]).

    🔴 `syslog_text`, not stderr. `log()` runs `logger … 2>/dev/null`, so a stub
    that writes to stderr is DISCARDED by the code under test — which is correct
    in production (syslog is the sink) and made every arm-line assertion here
    silently unfalsifiable until it was noticed. The stub writes to a file, the
    way `journalctl -t airvpn-updown` is what the operator actually reads.
    """
    # `parents=True`: callers pass a per-case SUBDIRECTORY of tmp_path so several
    # harness runs in one test cannot share (and overwrite) each other's capture.
    tmp_path.mkdir(parents=True, exist_ok=True)
    bindir = tmp_path / "upbin"
    bindir.mkdir(parents=True, exist_ok=True)
    rec = tmp_path / "nftcalls"
    rec.mkdir(parents=True, exist_ok=True)

    # nft stub: append argv + stdin to a numbered file, then honour the
    # requested failure mode. `list table` answers the ks_present probe.
    mockbin.write_exec(bindir / "nft", f'''
n=$(ls "{rec}" | wc -l)
f="{rec}/$n"
printf 'ARGV: %s\\n' "$*" > "$f"
case "$1" in
  list) [ -n "$KS_PRESENT" ] && exit 0 || exit 1 ;;
esac
printf 'STDIN:\\n' >> "$f"
cat >> "$f"
case "$NFT_FAIL" in
  all) exit 1 ;;
  first) [ "$n" = "0" ] && exit 1 || exit 0 ;;
  *) exit 0 ;;
esac
''')
    # ip: no default route -> the bypass-route block is skipped entirely, so
    # nothing is written to /run and no route is ever touched.
    mockbin.write_exec(bindir / "ip", "exit 0\n")
    mockbin.write_exec(bindir / "wg", "exit 1\n")
    syslog = tmp_path / "syslog.txt"
    mockbin.write_exec(bindir / "logger", f'printf "%s\\n" "$*" >> "{syslog}"\n')

    env = write_env(tmp_path, f"NEBULA_LIGHTHOUSE={lighthouse}\n")
    e = dict(os.environ,
             AIRVPN_UPDOWN_ENV=str(env),
             # `extra_path` FIRST so a case-specific `ip`/`wg` stub overrides the
             # default no-op ones, letting a test drive what the producers see.
             PATH=(f"{extra_path}:" if extra_path else "")
                  + f"{bindir}:{os.environ['PATH']}",
             NFT_FAIL=nft_fail,
             KS_PRESENT="1" if ks_present else "")
    e.pop("NEBULA_LIGHTHOUSE", None)
    r = subprocess.run([_bash(), str(UPDOWN), "up", "airvpn"],
                       capture_output=True, text=True, env=e, timeout=30)
    calls = []
    for i in sorted(rec.iterdir(), key=lambda p: int(p.name)):
        calls.append(i.read_text())
    log_text = syslog.read_text(encoding="utf-8") if syslog.exists() else ""
    return r.returncode, r.stdout, log_text, calls


def test_the_syslog_stub_is_load_bearing(tmp_path):
    """🔴 POSITIVE CONTROL for every arm-line assertion below. They all read
    `syslog_text`; if the stub never captured anything they would all be
    assertions against an empty string — which is exactly what happened when the
    stub wrote to stderr and `log()`'s `2>/dev/null` ate it. Require a non-zero
    capture before believing any of them."""
    _rc, _o, syslog, _calls = _up_harness(tmp_path)
    assert syslog.strip(), "the logger stub captured NOTHING"
    assert "up: killswitch" in syslog, syslog


def test_the_harness_observes_a_real_ruleset(tmp_path):
    """🔴 POSITIVE CONTROL for everything below. Every assertion that follows is
    of the form "the sentinel is NOT in the fallback" or "there is no flush" —
    all satisfiable by a harness that captured nothing at all. Require a
    NON-ZERO observation first: the primary ruleset must be captured, and it
    must contain the sentinel (which is the whole point of the primary)."""
    _rc, _o, _e, calls = _up_harness(tmp_path)
    assert calls, "the nft stub captured NOTHING — the harness is wired to nothing"
    body = "\n".join(calls)
    assert _SENTINEL in body, (
        "the primary ruleset does not contain the lighthouse the env file set — "
        "the harness is not reaching the real code path")
    assert "chain out" in body and "drop" in body, body[:400]


def test_the_FALLBACK_ruleset_contains_no_lighthouse_derived_value(tmp_path):
    """🔴 A-2, asserted as a PROPERTY of the rendered text (kills mutant Y1).

    Force the primary load to fail so `arm_failclosed` runs, then require that
    the text the fallback handed to nft contains the sentinel NOWHERE — no
    matter what variable name it travelled under.
    """
    _rc, _o, _e, calls = _up_harness(tmp_path, nft_fail="first")
    # Select the fallback by CONTENT, not by index. Indexing on `calls[1]`
    # happened to be right for the current implementation and wrong for the
    # previous one (which issued `add` and `flush` as separate nft calls), so it
    # went red for an indexing reason rather than for the property — red for the
    # wrong reason is as misleading as green for the wrong reason.
    loads = [c for c in calls if "chain out" in c]
    assert len(loads) >= 2, f"the fallback never rendered a ruleset: {calls}"
    fallback = loads[-1]
    assert _SENTINEL not in fallback, (
        "the FALLBACK ruleset interpolates a lighthouse-derived value — the "
        "value that can break the primary parse must not be able to break the "
        "fallback too:\n" + fallback)
    # …and it must still be a real fallback, not an empty one.
    for required in ("meta skuid", "chain out", "drop"):
        assert required in fallback, f"the fallback lost its {required} rule"


def test_the_load_is_ONE_atomic_transaction(tmp_path):
    """🔴 F4. The old sequence was `nft add table` → `nft flush table` → `nft -f`:
    three transactions, the first two of which DESTROY the working killswitch
    before the third is known to parse. Any failure then left the uplink flushed
    and empty at exit 0 — the A-2 outcome by a different door (and reachable
    without the env file at all, e.g. a `$NEBULA_USER` nft cannot resolve).

    Mutant Y6 (delete the flush entirely) survived all 44 tests of the previous
    revision, because nothing looked at HOW the ruleset was loaded.

    🔴 BOTH CALLERS, NOT JUST THE PRIMARY (audit 🟡3, mutant M12). This drove
    `_up_harness` with NO failure injected, so it only ever inspected the primary
    path — and replacing `arm_failclosed`'s `nft_load_atomic` with the old
    `add`+`flush`+`load` sequence SURVIVED all 83 tests. That is the
    "second caller open-codes the old sequence" shape, and it de-atomises
    precisely the path that runs when things are already going wrong.
    """
    for label, kwargs in (("primary", {}), ("fallback", {"nft_fail": "first"})):
        _rc, _o, _e, calls = _up_harness(tmp_path / label, **kwargs)
        argvs = [c.splitlines()[0] for c in calls]
        assert not any("flush" in a for a in argvs), (
            f"[{label}] a separate `nft flush` is back — the load is no longer "
            f"atomic: {argvs}")
        assert not any(a.startswith("ARGV: add table") for a in argvs), (
            f"[{label}] a separate `nft add table` is back: {argvs}")

        loads = [c for c in calls if "chain out" in c]
        assert loads, f"[{label}] no ruleset was loaded via stdin: {argvs}"
        # EVERY load must be self-contained, not merely the first.
        for i, payload in enumerate(loads):
            assert "delete table inet airvpn_ks" in payload, (
                f"[{label}] load #{i} is not self-contained — without the "
                f"in-transaction delete this ADDS to the old table instead of "
                f"replacing it:\n{payload}")
            assert payload.index("delete table") < payload.index("chain out"), (
                f"[{label}] load #{i}: the delete must precede the new "
                f"definition in the same transaction")
    # and the fallback path must genuinely have produced a SECOND load, or the
    # loop above asserted twice about the same thing.
    _rc, _o, _e, calls = _up_harness(tmp_path / "fb-count", nft_fail="first")
    assert len([c for c in calls if "chain out" in c]) >= 2, (
        "the fallback never rendered a ruleset — the fallback half of this test "
        "was vacuous")


@pytest.mark.parametrize("gw_out,label", [
    ("default dev eth0 \n", "gateway-less default route -> awk yields 'eth0'"),
    ("default via not-an-ip dev eth0 \n", "junk in the gateway field"),
    ("default via 2001:db8::1 dev eth0 \n", "IPv6 gateway"),
])
def test_a_NON_ADDRESS_gateway_never_reaches_the_ruleset(tmp_path, gw_out, label):
    """🔴 AUDIT 🟡4. `$GW` is interpolated as `ip daddr ${GW} accept`, exactly
    like the lighthouse — and it was the only one of the three with NO check.

    On a gateway-less default route (`default dev eth0`) the awk yields the
    literal `eth0`; nft then tries to DNS-RESOLVE that token, as root, inside
    wg-quick's PostUp, and FAILS the primary load — which drops into the blanket
    fallback, which has no `meta mark` accept, which kills the tunnel. This is
    G1's exact class, one variable over.
    """
    bindir = tmp_path / "gwbin"
    bindir.mkdir(parents=True)
    mockbin.write_exec(bindir / "ip", f'''
case "$*" in
  *"route show default"*) printf '%s' '{gw_out}' ;;
esac
exit 0
''')
    _rc, _o, syslog, calls = _up_harness(tmp_path, extra_path=bindir)
    loads = [c for c in calls if "chain out" in c]
    assert loads, f"no ruleset rendered ({label})"
    for tok in ("eth0", "not-an-ip", "2001:db8::1"):
        assert f"ip daddr {tok} accept" not in loads[0], (
            f"{label}: a non-address reached the ruleset as `ip daddr {tok}`:\n"
            + loads[0])
    assert "gw=none" in syslog, syslog


def test_a_NON_ADDRESS_endpoint_never_reaches_the_ruleset(tmp_path):
    """🔴 AUDIT 🟡4, the other producer. `wg show <if> endpoints` prints the
    literal `(none)` for a peer that has no endpoint, and an IPv6 endpoint
    survives the `sed` as a mangled hextet run. Both were interpolated straight
    into `ip daddr` — IPv4 family — and failed the load.

    🔴 THE `ip` STUB IS NOT OPTIONAL HERE. `EP` is only computed inside the
    `if [[ -n "$GW" && -n "$PHYS" ]]` branch, so with the harness's default
    no-op `ip` this test never reached `endpoint_ip` at all and passed
    vacuously — measured: mutant G4b (delete the endpoint validation) SURVIVED
    it. A real default route is what makes the assertion reachable.
    """
    bindir = tmp_path / "epbin"
    bindir.mkdir(parents=True)
    mockbin.write_exec(bindir / "ip", '''
case "$*" in
  *"route show default"*) printf 'default via 192.0.2.1 dev eth0 \\n' ;;
esac
exit 0
''')
    mockbin.write_exec(bindir / "wg", '''
case "$1" in
  show) case "$3" in
          endpoints) printf 'PEERKEY\\t(none)\\n' ;;
          fwmark) printf '0xca6c\\n' ;;
        esac ;;
esac
exit 0
''')
    _rc, _o, syslog, calls = _up_harness(tmp_path, extra_path=bindir)
    loads = [c for c in calls if "chain out" in c]
    assert loads, "no ruleset rendered"
    # reachability control: the gateway DID land, so this run really did take
    # the branch where the endpoint is computed.
    assert "ip daddr 192.0.2.1 accept" in loads[0], (
        "the run never reached the branch that computes EP — this test would "
        "pass vacuously:\n" + loads[0])
    assert "(none)" not in loads[0], (
        "wg's literal `(none)` reached the ruleset:\n" + loads[0])
    assert "ep=?" in syslog or "ep=none" in syslog, syslog


def test_positive_control_a_VALID_gateway_and_endpoint_DO_reach_the_ruleset(tmp_path):
    """🔴 The complement, and the control that keeps the two tests above from
    passing because nothing was rendered at all. A validator that rejects
    EVERYTHING is not a validator — feed it good values and require they land."""
    bindir = tmp_path / "okbin"
    bindir.mkdir(parents=True)
    mockbin.write_exec(bindir / "ip", '''
case "$*" in
  *"route show default"*) printf 'default via 192.0.2.1 dev eth0 \\n' ;;
esac
exit 0
''')
    mockbin.write_exec(bindir / "wg", '''
case "$1" in
  show) case "$3" in
          endpoints) printf 'PEERKEY\\t198.51.100.9:1637\\n' ;;
          fwmark) printf '0xca6c\\n' ;;
        esac ;;
esac
exit 0
''')
    _rc, _o, syslog, calls = _up_harness(tmp_path, extra_path=bindir)
    loads = [c for c in calls if "chain out" in c]
    assert loads, "no ruleset rendered"
    assert "ip daddr 192.0.2.1 accept" in loads[0], loads[0]
    assert "ip daddr 198.51.100.9 accept" in loads[0], loads[0]
    assert "meta mark 0xca6c accept" in loads[0], loads[0]


def test_the_comment_describing_the_fallback_matches_the_code():
    """🔴 A comment is a claim. The previous one said the fallback had 'no
    dynamic … substitution', which this change made FALSE — and a maintainer
    reading a stale safety comment is how a guard gets deleted."""
    src = UPDOWN.read_text(encoding="utf-8")
    head = src[:src.index("arm_failclosed() {")]
    tail = head[head.rindex("# Blanket FAIL-CLOSED"):]
    assert "NO OPERATOR-SUPPLIED TEXT" in tail, (
        "the fallback's comment no longer states what the code does")


# --- G6: the arm line must not claim ARMED when nothing is armed --------------

def test_the_arm_line_reports_NOT_ARMED_when_both_loads_failed(tmp_path):
    """🔴 G6. The old arm line was UNCONDITIONAL — it printed `killswitch armed`
    even when the primary load AND `arm_failclosed` had both failed, i.e. exactly
    when the uplink was unfiltered. This PR adds the `lighthouse=` field to that
    same line and tells the operator to read it, so the line carrying the new
    signal was the one that lied. Mutant Y5 (always report a lighthouse) survived
    the previous revision.

    Driven with both loads failing AND the kernel reporting the table absent.
    """
    _rc, _o, err, _calls = _up_harness(tmp_path, nft_fail="all", ks_present=False)
    assert "NOT-ARMED" in err, err
    assert "UNFILTERED" in err, err
    assert "killswitch ARMED" not in err, (
        "the arm line claims ARMED after both loads failed: " + err)


def test_the_arm_line_reports_ARMED_when_the_kernel_says_the_table_is_there(tmp_path):
    """The complement. A guard that only ever says NOT-ARMED is useless too —
    measure both ends, not just the failure side."""
    _rc, _o, err, _calls = _up_harness(tmp_path, ks_present=True)
    assert "killswitch ARMED(primary)" in err, err
    assert "NOT-ARMED" not in err, err
    assert f"lighthouse={_SENTINEL}" in err, err


def test_the_arm_line_names_the_FALLBACK_when_that_is_what_is_installed(tmp_path):
    """Three states, not two: primary / fallback / none. Collapsing fallback into
    'armed' hides that the uplink is running a degraded ruleset with no
    `meta mark` accept."""
    _rc, _o, err, _calls = _up_harness(tmp_path, nft_fail="first", ks_present=True)
    assert "killswitch ARMED(fallback)" in err, err


def test_both_loads_failing_with_a_SURVIVING_table_reports_STALE(tmp_path):
    """🔴 THE FOURTH STATE — one my own F4 created, and no test covered it.

    Before the atomic load, "both loads failed" implied the table had already
    been flushed, so NOT-ARMED was the only reachable outcome. After F4 a failed
    load changes nothing, so the PREVIOUS table survives and `ks_present` is
    TRUE while `KS_RULESET` is `none` — which reported as `ARMED(none)`,
    directly contradicting the `FALLBACK … ALSO FAILED` line immediately above
    it. Worse than meaningless: the surviving ruleset was built for a DIFFERENT
    endpoint and fwmark, so it can read as armed while the tunnel is dead.
    """
    _rc, _o, syslog, _calls = _up_harness(tmp_path, nft_fail="all", ks_present=True)
    assert "STALE(previous)" in syslog, syslog
    assert "ARMED(none)" not in syslog, (
        "the meaningless fourth state is back: " + syslog)
    # the two lines must not contradict each other any more
    assert "uplink is NOT filtered" not in syslog, (
        "the fallback still claims the uplink is unfiltered while a table "
        "survives: " + syslog)
    assert "OLDER table" in syslog and "does NOT match" in syslog, syslog


def test_the_four_states_are_pairwise_distinct(tmp_path):
    """A four-way verdict is only useful if the four are distinguishable. Drive
    every corner of (load outcome × kernel answer) and require four different
    strings — a collapse to three is how `ARMED(none)` hid in the first place."""
    seen = {}
    for label, kwargs in (
        ("primary",  {}),
        ("fallback", {"nft_fail": "first"}),
        ("stale",    {"nft_fail": "all", "ks_present": True}),
        ("gone",     {"nft_fail": "all", "ks_present": False}),
    ):
        _rc, _o, syslog, _c = _up_harness(tmp_path / label, **kwargs)
        # the stub replays logger's full argv, so the tag precedes the message
        line = [l for l in syslog.splitlines() if "up: killswitch " in l]
        assert len(line) == 1, (label, syslog)
        seen[label] = line[0].split("up: killswitch ", 1)[1].split()[0]
    assert len(set(seen.values())) == 4, f"states collapsed: {seen}"
    assert seen == {"primary": "ARMED(primary)", "fallback": "ARMED(fallback)",
                    "stale": "STALE(previous)", "gone": "NOT-ARMED"}, seen


def test_the_ks_present_probe_is_load_bearing(tmp_path):
    """POSITIVE CONTROL for the three above: flipping ONLY the kernel's answer,
    with the loads succeeding, must flip the verdict. If it does not, the arm
    line is still being written from the load's exit status."""
    _r1, _o1, ok_err, _c1 = _up_harness(tmp_path, ks_present=True)
    _r2, _o2, no_err, _c2 = _up_harness(tmp_path, ks_present=False)
    assert "ARMED(primary)" in ok_err and "NOT-ARMED" in no_err, (ok_err, no_err)


# --- G9a: check-env is read-only, asserted as a PROPERTY -----------------------

def test_check_env_performs_no_filesystem_writes(tmp_path):
    """🔴 BEHAVIOURAL half of the read-only guard.

    The previous version forbade a list of TOKENS in the branch text. Mutants X2
    (`echo x >/tmp/canary`, no space after `>`) and X3 (`touch /tmp/canary`) both
    walked past it. Snapshot every byte of a sandbox — including the env file
    itself, HOME and TMPDIR — and require it identical afterwards.
    """
    home = tmp_path / "home"; home.mkdir()
    tmp = tmp_path / "tmp"; tmp.mkdir()
    env = write_env(tmp_path, f"NEBULA_LIGHTHOUSE={GOOD_V4}\n")

    def snapshot():
        out = {}
        for p in sorted(tmp_path.rglob("*")):
            if p.is_file():
                st = p.stat()
                out[str(p.relative_to(tmp_path))] = (st.st_size, st.st_mode,
                                                     p.read_bytes())
            elif p.is_dir():
                out[str(p.relative_to(tmp_path)) + "/"] = None
        return out

    before = snapshot()
    rc, _out, _err = check_env(env, extra={"HOME": str(home), "TMPDIR": str(tmp)})
    assert rc == 0
    assert snapshot() == before, "check-env WROTE to the filesystem"


def test_check_env_invokes_no_external_command_beyond_a_read_only_set(tmp_path):
    """🔴 STRUCTURAL half — a WHITELIST, not a blacklist.

    A blacklist is what X2/X3 defeated: any command not thought of passes. Run
    `check-env` with a PATH whose ONLY entries are recording stubs for a small
    read-only set, plus a catch-all that FAILS LOUDLY for anything else, and
    require the run to still succeed. A mutant adding `touch`, `cp`, `tee`,
    `dd`, `install`, `nft` … cannot find it and the recorded log names it.

    Scoped honestly: this observes commands resolved through PATH. A bash
    BUILTIN redirection is caught by the behavioural test above instead — the
    two together cover both shapes.
    """
    bindir = tmp_path / "robin"; bindir.mkdir()
    log = tmp_path / "cmds.log"
    # the read-only set check-env is allowed to reach
    for allowed in ("id", "logger", "stat"):
        real = __import__("shutil").which(allowed) or "/bin/true"
        mockbin.write_exec(bindir / allowed,
                           f'printf "%s\\n" "{allowed}" >> "{log}"\nexec {real} "$@"\n')
    env = write_env(tmp_path, f"NEBULA_LIGHTHOUSE={GOOD_V4}\n")
    # PATH is ONLY the stub dir: anything else is "command not found".
    rc, out, err = check_env(env, extra={"PATH": str(bindir)})
    assert rc == 0, (rc, out, err)
    assert "command not found" not in err, (
        "check-env reached a command outside the read-only set: " + err)
    assert f"lighthouse={GOOD_V4}" in out, out


def _check_env_code() -> str:
    src = UPDOWN.read_text(encoding="utf-8")
    start = src.index("    check-env)")
    return "\n".join(l for l in src[start:src.index("        ;;", start)].splitlines()
                     if not l.strip().startswith("#"))


def _write_redirections(code: str) -> list[str]:
    """Every output redirection in `code` that targets a PATH rather than an fd.

    EXHAUSTIVE over the syntax class, which is what makes this different from
    the token blacklist it replaces: bash has exactly one way to spell "send
    output somewhere" (`>` / `>>`), and the only benign form here is a
    duplication onto an existing descriptor (`>&N`). So this is a whitelist over
    a closed set, not a guess-list of commands somebody might use.
    """
    out = []
    for line in code.splitlines():
        i = 0
        while (i := line.find(">", i)) != -1:
            rest = line[i + 1:].lstrip(">")
            if not rest.startswith("&"):
                out.append(line.strip())
                break
            i += 1
    return out


def test_check_env_contains_no_output_redirection_to_a_path():
    """🔴 THE X2 GAP. The audit's mutant X2 — `echo x >/tmp/canary`, no space
    after the `>` — survived BOTH behavioural guards above: the sandbox snapshot
    only sees paths under `tmp_path`, and a bash builtin redirection reaches no
    command through PATH. A write to an absolute path outside the sandbox is
    invisible to both.

    Three complementary properties now cover `check-env`:
      1. it writes nothing into a sandbox           (behavioural)
      2. it reaches no command outside a read-only set (behavioural, PATH)
      3. it contains no output redirection to a path   (syntactic, THIS)
    """
    offenders = _write_redirections(_check_env_code())
    assert not offenders, (
        "check-env writes to a path — it must stay read-only:\n  "
        + "\n  ".join(offenders))


@pytest.mark.parametrize("planted", [
    'echo x >/tmp/canary',          # X2 exactly: no space after >
    'echo x > /tmp/canary',         # with a space
    'echo x >>/tmp/canary',         # append
    'printf y > "$SITE_ENV"',       # clobbering the input
])
def test_positive_control_the_redirection_check_catches_each_write_shape(planted):
    """🔴 A zero-shaped assertion needs a non-zero control. Feed the checker the
    exact mutant shapes and require a NON-EMPTY result — otherwise "no
    offenders" is indistinguishable from a checker that can never find one."""
    found = _write_redirections(_check_env_code() + "\n        " + planted)
    assert found, f"the redirection check cannot see {planted!r}"


def test_positive_control_the_redirection_check_allows_fd_duplication():
    """The complement: it must not fire on `>&2`, or the guard is unusable and
    gets deleted the first time someone legitimately writes to stderr."""
    assert not _write_redirections('        echo "x" >&2')


def test_the_read_only_harness_would_catch_a_side_effect(tmp_path):
    """🔴 POSITIVE CONTROL for both tests above. Neither is worth anything unless
    a planted side effect actually trips them — a snapshot that compares two
    empty dicts, or a PATH that was never narrowed, would pass identically.

    Runs a deliberately-mutated COPY of the script, so the real one is untouched.
    """
    mutant = tmp_path / "airvpn-updown-mutant"
    src = UPDOWN.read_text(encoding="utf-8")
    canary = tmp_path / "canary"
    # X3's shape: a real external command with a real side effect.
    src = src.replace('        echo "lighthouse=${NEBULA_LIGHTHOUSE:-UNSET}',
                      f'        touch "{canary}"\n        echo "lighthouse=${{NEBULA_LIGHTHOUSE:-UNSET}}')
    mutant.write_text(src, encoding="utf-8")
    env = write_env(tmp_path, f"NEBULA_LIGHTHOUSE={GOOD_V4}\n")
    e = dict(os.environ, AIRVPN_UPDOWN_ENV=str(env))
    e.pop("NEBULA_LIGHTHOUSE", None)
    subprocess.run([_bash(), str(mutant), "check-env"],
                   capture_output=True, text=True, env=e, timeout=30)
    assert canary.exists(), (
        "the planted side effect did not happen — this control proves nothing "
        "about the two tests above")


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
    # 🔴 EXACT BYTES, NOT `.strip()`ed (audit X11). The audit's real X11 mutant
    # changed this `printf` from `'NEBULA_LIGHTHOUSE=%s\n'` to `'%s'` — dropping
    # the trailing newline — and SURVIVED, because `.strip()` erases the very
    # byte F3 is about. A file with no final newline is the canonical
    # partially-written shape; a fixture that cannot see it is a fixture that
    # cannot see F3's regression either.
    assert f.read_bytes() == f"NEBULA_LIGHTHOUSE={GOOD_V4}\n".encode(), (
        f"exact bytes differ: {f.read_bytes()!r}")
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
    requirement used to be a trailing sentence AFTER the install command.

    🔴 ANCHORED ON THE 🔴 BLOCK'S OWN TEXT (audit X15). The previous version
    anchored on the substring `/etc/airvpn-updown.env`, which Procedure step 1's
    own command ALSO contains — so the audit's real X15 mutant, moving the whole
    prerequisite block to the END of the file, SURVIVED. A guard that can be
    satisfied by a different occurrence of the same substring is pinning
    spelling, not order.
    """
    doc = SKILL_DOC.read_text(encoding="utf-8")
    prereq = doc.index("is a PREREQUISITE, not an\nafterthought")
    install = doc.index("sudo install -m 0755 -o root -g root")
    assert prereq < install, (
        "airvpn.md introduces the env file only after telling the operator to "
        "install and re-arm")
    # …and the block must still precede the Procedures it is a prerequisite FOR.
    assert prereq < doc.index("## Procedures"), (
        "the prerequisite block has been moved out from in front of Procedures")


# --- G7: the DURABLE artifact, not just the PR body ---------------------------
#
# 🔴 A PR body is read once, by one person, and then it is gone. The file a
# future session loads is `claude/skills/bar/airvpn.md`. The previous revision
# renumbered the Procedures correctly and left the *re-test protocol* untouched —
# so after merge the durable doc described a protocol that could not detect this
# PR's own failure mode. These pin the protocol itself.

SKILL_DOC = REPO / "claude" / "skills" / "bar" / "airvpn.md"


def _protocol_section() -> str:
    doc = SKILL_DOC.read_text(encoding="utf-8")
    start = doc.index("## 🔴 Mandatory re-test protocol")
    return doc[start:doc.index("\n## ", start + 1)]


def test_the_retest_protocol_states_the_tunnel_must_ALREADY_BE_UP():
    """🔴 F2. MEASURED on the workbench: the `airvpn` interface does not
    currently exist, so the host is in exactly the state where following this
    protocol literally blackholes its own egress. Arming with the tunnel down
    yields a chain with no `meta mark` accept and no endpoint accept, ending in
    `drop`.

    The precondition and the bail must both be IN the protocol, not inferable
    from it.
    """
    sec = _protocol_section()
    assert "PRECONDITION" in sec and "TUNNEL MUST ALREADY BE UP" in sec.upper(), sec
    assert "ip link show airvpn" in sec, "no way to CHECK the precondition"
    assert "nft delete table inet airvpn_ks" in sec, "no bail in the protocol"


def test_the_retest_protocol_installs_the_edited_helper():
    """🔴 G8. MEASURED: the deployed `/etc/nixos/i3blocks-scripts/airvpn-updown`
    is byte-identical to `origin/main` — the OLD script, with no `check-env`
    action. A protocol that says "run check-env" without first saying "install
    the thing you edited" prints usage and exits 1."""
    sec = _protocol_section()
    install = sec.index("sudo install -m 0755")
    check = sec.index("check-env")
    assert install < check, (
        "the protocol runs check-env before installing the edited helper — it "
        "would be exercising the OLD script")


def test_the_retest_protocol_observes_the_slash32_route_directly():
    """🔴 G8. `ip route get <lighthouse>` is the ONLY step that observes A-1.
    The off-LAN ssh check cannot substitute: with a healthy tunnel, lighthouse
    traffic routed INTO the tunnel still arrives, so ssh succeeds whether or not
    the /32 exists."""
    sec = _protocol_section()
    assert "ip route get" in sec, "the protocol never observes the /32 bypass"
    assert "dev airvpn" in sec, "no stated FAILING reading for the route check"
    route = sec.index("ip route get")
    ssh = sec.index("ssh zach@")
    assert route < ssh, "the route check must precede the checks it is not implied by"


def test_the_off_LAN_step_names_the_SOURCE_host_and_is_not_a_self_ssh():
    """🔴 P1 — the step the entire do-not-merge banner exists for.

    `10.42.0.30` is the WORKBENCH'S OWN nebula address, and step 1 puts the
    operator on the LAN. `ssh zach@10.42.0.30` run there is a SELF-SSH; from
    another LAN host it is a same-LAN hop. Neither exercises the nebula
    DIRECT-PUNCH path that this section's opening line says a LAN-only test
    cannot reach — the path that locked the host out in #118. The step carrying
    the whole "off-LAN" claim never said which host to run it FROM, so followed
    literally it passed unconditionally.
    """
    sec = _protocol_section()
    tail = sec[sec.index("11."):]
    # AND, not OR: an earlier version accepted either phrase, so a mutant that
    # changed "ON THE LAPTOP" to "ON THIS HOST" — i.e. re-created the self-ssh —
    # SURVIVED on the other half of the disjunction.
    assert "ON THE LAPTOP" in tail, (
        "the off-LAN step no longer names the SOURCE host to run it from")
    assert "off-LAN host" in tail, (
        "the off-LAN step no longer says the source must be off-LAN")
    assert "self-ssh" in tail, (
        "the step no longer explains WHY running it on the workbench is void")
    # it must tell the operator how to notice they are still on the LAN
    assert "192" in tail and "STILL ON THE LAN" in tail, (
        "no guard against running the off-LAN step from the LAN")
    # …and what a pass and a fail each look like
    for verdict in ("**PASS**", "**FAIL**", "**INVALID**"):
        assert verdict in tail, f"the off-LAN step does not define {verdict}"
    assert "cannot be completed" in tail, (
        "no instruction for the case where no off-LAN host exists — that is "
        "when the self-ssh gets substituted")


def test_the_precondition_checks_UP_ness_not_mere_existence():
    """🟢 P3. `ip link show airvpn` succeeds for an interface left behind by a
    HALF-FAILED `wg-quick up` — a state in which `wg show … fwmark` still fails
    and arming still blackholes the host. Existence is not up-ness."""
    sec = _protocol_section()
    assert "EXISTENCE IS NOT UP-NESS" in sec, sec[:600]
    assert "grep -qw UP" in sec, "the check does not require the link to be UP"
    assert "latest-handshakes" in sec, (
        "the check does not require a peer that has actually answered")


def test_the_install_step_tells_the_operator_to_confirm_the_branch():
    """🟢 P4. G8 exists precisely because the DEPLOYED helper was found
    byte-identical to `main` while the PR was open. Installing from a checkout
    without confirming which branch is in it reproduces that."""
    sec = _protocol_section()
    install = sec.index("sudo install -m 0755")
    head = sec[:install]
    assert "status -sb" in head, (
        "the install step does not tell the operator to confirm the branch")


def test_the_protocol_says_to_BAIL_on_a_degraded_ruleset():
    """🟡 P5. `ARMED(fallback)` was described as merely 'degraded', while the
    script's own comment says that state drops wg's encrypted packets and kills
    the tunnel. With no instruction to bail, the operator walks into step 10's
    `curl` and gets a confusing failure instead of a diagnosed one."""
    sec = _protocol_section()
    six = sec[sec.index("6. `journalctl"):sec.index("7. 🔴")]
    for state in ("ARMED(fallback)", "STALE(previous)", "NOT-ARMED"):
        assert state in six, f"step 6 does not mention {state}"
    assert six.count("BAIL NOW") >= 2, (
        "step 6 does not tell the operator to bail on a degraded/stale ruleset:\n"
        + six)


def test_the_retest_protocol_reads_the_new_arm_line_fields():
    """The protocol must name the fields this PR added, or it cannot detect the
    failure modes this PR introduces."""
    sec = _protocol_section()
    for token in ("lighthouse=UNSET", "ARMED(primary)", "ARMED(fallback)",
                  "STALE(previous)", "NOT-ARMED", "state=ok"):
        assert token in sec, f"the protocol never mentions {token}"


def test_the_debug_section_shows_the_CURRENT_arm_line_format():
    """A stale example is a claim too. The Debug/bail block still showed the
    pre-`lighthouse=` format, so an operator comparing against it would see a
    field they had no reason to expect and no reason to check."""
    doc = SKILL_DOC.read_text(encoding="utf-8")
    dbg = doc[doc.index("## Debug / bail"):]
    assert "lighthouse=" in dbg, "the arm-line example predates this PR"
    assert "killswitch armed (fwmark" not in dbg, (
        "the Debug section still shows the OLD unconditional `armed` wording")
    for state in ("ARMED(primary)", "ARMED(fallback)", "STALE(previous)",
                  "NOT-ARMED"):
        assert state in dbg, f"Debug/bail does not explain {state}"
    assert "FOUR states" in dbg, (
        "Debug/bail still says THREE states — `STALE(previous)` became "
        "reachable when the load was made atomic")


# --- the seam ------------------------------------------------------------------

def test_check_env_still_does_the_one_thing_it_exists_for():
    """The read-only PROPERTY is asserted behaviourally and by PATH-whitelist
    above (`test_check_env_performs_no_filesystem_writes`,
    `…_invokes_no_external_command_beyond_a_read_only_set`), both with a positive
    control. The token-blacklist that used to live here was a SPELLED guard and
    the audit's mutants X2/X3 walked past it — it is deleted rather than kept as
    reassurance.

    What remains here is the complement: a read-only branch that reads nothing
    would also pass every test above.
    """
    src = UPDOWN.read_text(encoding="utf-8")
    start = src.index("    check-env)")
    code = "\n".join(l for l in src[start:src.index("        ;;", start)].splitlines()
                     if not l.strip().startswith("#"))
    assert "NEBULA_LIGHTHOUSE" in code and "echo " in code, code
    assert "state=" in code, "check-env no longer reports WHY it is UNSET (G3)"


def test_the_lighthouse_is_never_a_literal_in_this_repo():
    """The reason the env file exists at all. `scripts/tests/test_no_public_ips.py`
    is the repo-wide gate; this is the local seam assertion, so a change that
    re-inlines the value fails in the file that owns the decision too."""
    sys.path.insert(0, str(REPO / "scripts"))
    sys.path.insert(0, str(REPO / "scripts" / "claude-hooks"))
    from testlib import public_ip_scan as S  # noqa: E402
    hits = [h for h in S.scan_file(UPDOWN)]
    assert not hits, f"airvpn-updown carries a routable IP literal again: {hits}"
