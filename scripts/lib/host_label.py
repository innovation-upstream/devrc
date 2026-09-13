"""host_label — THE one rule for "which box is this".

🔴 IT EXISTS BECAUSE THE RULE WAS OPEN-CODED TWICE AND THE TWO COPIES DISAGREED,
AND BECAUSE A LATER CHANGE MADE THAT DISAGREEMENT LOAD-BEARING.

Both halves of the transcript feed write the SAME row's `host` column:

  * `transcript-push.sh` had `HOST_NAME="${TRANSCRIPT_PUSH_HOST:-${ACTIVITY_HOST:-$(uname -n)}}"`.
    Its own comment said ACTIVITY_HOST is "the fleet's existing per-host handle …
    so reusing it keeps one answer to 'which box is this' rather than minting a
    second" — but ACTIVITY_HOST lives in a FILE that the unit does not source and
    the shell never reads, so it fell through to `uname -n`, which is **"nixos"
    on BOTH machines**. Measured on the deployed server: every stored transcript
    row said `host: nixos`, i.e. the column was useless.
  * `tmux-reply-agent` read that file, lowercased, validated against the two real
    names, and produced "workbench" / "laptop".

That mismatch cost nothing while `host` was display-only. Then the delta stream
made it a CORRECTNESS predicate (an append whose host differs from the stored
row's is refused, because the stored offset is a position in the other machine's
file) — and the two feeders' disagreement turned into a permanent refusal: every
5-minute bulk tick stamped `nixos` back onto every row, and every 5-second stream
poll then reseeded up to 48 KiB per session because "the host changed". Caught by
an adversarial audit BEFORE deploy, with both values measured side by side.

So the rule lives here now, in one module, and both feeders read it.

🔴 AND IT NO LONGER GUESSES. `local_host_label()` used to end
`return DEFAULT_LOCAL_HOST` — the literal `"workbench"` — whenever neither
`ACTIVITY_HOST` nor the collector's env file supplied a valid label. On the
LAPTOP in that state every consumer stamped `workbench` on the laptop's data,
silently, exit 0, no error. For `scripts/peer-host` — a ROUTING tool — that is
not a degraded answer, it is a WRONG one: the local leg reads the laptop's
registry and files it under `workbench`, while the `laptop` leg becomes an SSH
connection to itself. Work then gets sent to the wrong machine, which is the
exact failure that tool was built to prevent.

The default is gone. In its place, in order:

  1. `ACTIVITY_HOST` in the environment (unchanged, still wins),
  2. `ACTIVITY_HOST=` in the collector env file (unchanged, still second),
  3. 🔴 NEW — DERIVE it from an address this machine actually HOLDS. Both hosts
     report hostname `nixos`, but each holds addresses only it holds, and
     `scripts/lib/host-role.sh` already owns that table (see HOST_ROLE_SH).
  4. 🔴 NEW — nothing determined it -> `HostLabelUnresolved`. Not `"workbench"`.

and (3) is also a CROSS-CHECK on (1)/(2): when the stated label and the
address-derived one disagree, that is `HostLabelConflict`, for the same reason
`ssh_target()` raises on an unknown label — there is no safe answer, so there is
none. See `local_host_label` for why an exception rather than a sentinel.
"""
from __future__ import annotations

import os
import re
import socket
import sys

#: The host vocabulary the read model, the termwrite queue and the activity
#: collector all use. `hostname` is "nixos" on BOTH machines, which is precisely
#: why it cannot be the source of truth.
HOST_NAMES = ("workbench", "laptop")


class HostLabelError(RuntimeError):
    """Base class: this module refuses to name the local machine."""


class HostLabelUnresolved(HostLabelError):
    """Nothing positively identified this machine."""


class HostLabelConflict(HostLabelError):
    """Two signals named DIFFERENT machines, so neither can be trusted."""

#: 🔴 EVERY host in HOST_NAMES with the Nebula address and user an SSH leg to it
#: needs — `(label, addr, user)`, in HOST_NAMES order.
#:
#: `10.42.0.100` is the LAPTOP; `10.42.0.10` is the homelab GATEWAY. Getting that
#: wrong DOES NOT FAIL LOUDLY: SSH succeeds against a real host and reports the
#: gateway's state as the laptop's. That is why the address belongs next to the
#: label vocabulary it is meant to agree with, rather than being retyped per tool.
#:
#: 🔴 THE GUARD'S SCOPE, STATED ONCE. NOT A LIST OF EVERY SPELLING.
#:
#: Two earlier drafts of this comment tried to ENUMERATE where these addresses
#: appear. Both were incomplete when written — the first listed 3 of 5, the
#: second 5 of 11+ — and each was caught by the next audit round. An enumeration
#: of places a guard CANNOT see is unbounded and rots silently, and an
#: over-claiming ledger is worse than none: it is what the next reader trusts
#: INSTEAD of looking. So this states the SCOPE, which is checkable and stable,
#: and stops pretending to a census.
#:
#: ENFORCED — `test_peer_host.py::test_no_module_redeclares_a_peer_address_literal`:
#:     no string constant EQUAL to a peer's `user@addr`, in a file under
#:     `scripts/` that is not inside a `tests/` directory and does not end in
#:     `.md`, outside this module.
#:
#: 🔴 EVERY CLAUSE OF THAT WAS MEASURED AGAINST THE SCANNER, not inferred from
#: its name. It does NOT catch a constant that merely CONTAINS the address
#: (`"ssh zach@… uptime"`), an f-string, a bare unquoted address in a `.sh`
#: file, or anything in a `.md` — `scripts/browser-bridge/README.md` spells one
#: today. This is the THIRD attempt at this paragraph; the first two described
#: the guard more widely than it works, which is the same defect in a new place.
#:
#: OUT OF SCOPE, and therefore NOT enumerated anywhere: a target COMPOSED at
#: runtime from parts (shell does this); a BARE IP; and anything outside
#: `scripts/` — `nix/` in particular carries several, in espanso snippets, a
#: systemd `ExecStart` and shell under `nix/system/`. To find them, GREP; do not
#: trust a list here.
#:
#: The two in-scope-but-unreachable cases worth knowing by name, because each is
#: itself a claimed single source of truth:
#:   * `scripts/lib/host-role.sh` — composes `zach@<ip>` at runtime from bare IP
#:     constants. Live (sourced by `ship.sh` and `drift-check.sh`), and
#:     `scripts/README.md` calls it "the ONE host-identity predicate". 🔴 IT IS
#:     NO LONGER A SECOND, UNRELATED CLAIM: this module now READS its IP table
#:     (see `HOST_ROLE_SH` / `host_addrs()`) instead of restating one, so the
#:     shell file OWNS the addresses and this module owns the label vocabulary
#:     and the Python answer. The two are pinned together by
#:     `test_host_label_identity.py::test_the_address_table_is_host_role_shs_own`.
#:     Folding the shell into Python is still a design change, not a rename, and
#:     is still not done.
#:   * `scripts/browser-bridge/server.py` — bare IPs in `_HOST_IP_ORDER`. It
#:     CANNOT import this module: home-manager deploys it as a lone flattened
#:     symlink at `~/.config/browser-bridge/server.py`, with no `lib/` sibling.
#:     So it keeps its copy and
#:     `test_host_label_identity.py::test_browser_bridges_ip_table_agrees` fails
#:     if the two ever disagree.
#:
#: 🔴 NO LINE NUMBERS, deliberately — `nix/home.nix` states the rule this repo
#: already learned: "a line number is a claim that rots silently". An earlier
#: draft of this comment carried four, and one had already gone stale within the
#: same PR.
#:
#: These modules DERIVE from this table (three of the four were literals;
#: `scripts/peer-host` is new and never carried one):
#:   `scripts/peer-host`, `scripts/lib/opencode_search.py`,
#:   `scripts/session-manager`, `scripts/session-analysis/espanso-usage.py`.
#:
#: Every value agrees today; this is a duplication hazard, not a live defect.
PEER_SSH = (
    ("workbench", "10.42.0.30", "zach"),
    ("laptop", "10.42.0.100", "zach"),
)


def ssh_target(label: str) -> str:
    """`user@addr` for a host label. Raises on an unknown label.

    Raising is deliberate. A miss that returned None or "" would be spliced into
    an `ssh` argv and produce a connection to the LOCAL machine or to whatever
    the caller's default host is — i.e. the wrong machine's answer, reported as
    that label's. There is no safe fallback here, so there is none.
    """
    for lbl, addr, user in PEER_SSH:
        if lbl == label:
            return f"{user}@{addr}"
    raise KeyError(
        "no SSH target for host label %r — known labels: %s"
        % (label, ", ".join(l for l, _, _ in PEER_SSH)))


#: Where the collector records this machine's label.
#:
#: `HOST_LABEL_ENV_FILE` redirects it. That override exists so a test can point
#: BOTH feeders at one fixture and assert they agree — which is the only way to
#: pin the property that matters here; pinning each side to a literal separately
#: is exactly what let them drift.
ACTIVITY_ENV = os.environ.get("HOST_LABEL_ENV_FILE") or os.path.expanduser(
    "~/.config/activity-collector/env")


# =========================================================================== #
# THE THIRD SIGNAL: an address this machine actually holds
# =========================================================================== #
#: 🔴 THE ADDRESS TABLE IS NOT DECLARED HERE. It is READ from
#: `scripts/lib/host-role.sh`, which already owns it and is already live
#: (`ship.sh`, `drift-check.sh` source it). This module exists BECAUSE a
#: duplicated rule drifted; answering that by typing the same four addresses a
#: second time would have been the same mistake in a new file.
#:
#: Read, not executed: a `bash` + `ip` fork per call would be both slow on a
#: hot-ish path and IMPOSSIBLE in the units that call this — `nix/home.nix`
#: deliberately drops `iproute2` from the transcript-push and tmux-reply-agent
#: PATHs, and that omission is itself pinned by a test. Nothing here shells out.
HOST_ROLE_SH = (
    os.path.join(os.path.dirname(os.path.abspath(globals()["__file__"])),
                 "host-role.sh")
    if globals().get("__file__") else None)

#: `WORKBENCH_IP_PRIMARY="192.168.50.250"` and its three siblings. The host part
#: is captured NON-GREEDILY so `WORKBENCH_IP_PRIMARY` yields `WORKBENCH`, and the
#: address is matched as four real octets so a commented-out or templated line
#: cannot be read as a host address.
_IP_ASSIGN_RE = re.compile(
    r'^[ \t]*([A-Z][A-Z0-9_]*?)_IP_(PRIMARY|SECONDARY)[ \t]*=[ \t]*'
    r'"?((?:25[0-5]|2[0-4][0-9]|1[0-9][0-9]|[1-9]?[0-9])'
    r'(?:\.(?:25[0-5]|2[0-4][0-9]|1[0-9][0-9]|[1-9]?[0-9])){3})"?[ \t]*$',
    re.MULTILINE)

#: host-role.sh's own precedence: every PRIMARY (the stable 192.168.50.x LAN
#: address) before any SECONDARY (the 10.42.0.x nebula one).
_ADDR_RANKS = ("PRIMARY", "SECONDARY")


def parse_host_addrs(source: str, host_names=HOST_NAMES) -> tuple:
    """`((label, addr), …)` parsed out of host-role.sh, in ITS precedence order.

    🔴 FAILS CLOSED, and that is the whole safety argument for reading a foreign
    file at runtime. If the parse does not yield at least one address for EVERY
    host in `host_names` — the file moved, was reformatted, lost a constant — it
    returns `()`, i.e. "no address signal", and `local_host_label()` then refuses
    rather than answering from a half-read table. A partial table is the one
    outcome that could mislabel a machine, so it is the one outcome forbidden.
    """
    found = {}
    for name, rank, addr in _IP_ASSIGN_RE.findall(source or ""):
        label = name.lower()
        if label in host_names:
            found.setdefault((label, rank), addr)
    for host in host_names:
        if not any((host, rank) in found for rank in _ADDR_RANKS):
            return ()
    out = []
    for rank in _ADDR_RANKS:
        for host in host_names:
            addr = found.get((host, rank))
            if addr:
                out.append((host, addr))
    return tuple(out)


def _peer_ssh_addrs() -> tuple:
    """The nebula half of the table, from this module's OWN `PEER_SSH`.

    🔴 A FALLBACK, NOT A SECOND TABLE, AND IT IS A STRICT SUBSET BY TEST.
    `test_host_label_identity.py::test_peer_ssh_addresses_are_in_the_shared_table`
    fails if any `PEER_SSH` address is absent from host-role.sh's, so this can
    never DISAGREE with the file — it can only know less (no LAN addresses).

    It exists because `scripts/peer-host` SPLICES this module's source into a
    program it pipes to `python3 -` on the far host. There `__file__` is
    `'<stdin>'`, so host-role.sh cannot be located — and that leg is exactly the
    one whose independent self-identification catches a wrong SSH address. It
    reaches the far host over nebula, so the nebula addresses are the ones that
    can possibly match there anyway.
    """
    return tuple((label, addr) for label, addr, _user in PEER_SSH)


_HOST_ADDRS_MEMO = {}


def host_addrs(path=None) -> tuple:
    """The `(label, addr)` table, memoised per path. Never raises."""
    key = HOST_ROLE_SH if path is None else path
    if key in _HOST_ADDRS_MEMO:
        return _HOST_ADDRS_MEMO[key]
    table = ()
    if key:
        try:
            with open(key, "r", encoding="utf-8", errors="replace") as fh:
                table = parse_host_addrs(fh.read())
        except OSError:
            table = ()
    if not table:
        table = _peer_ssh_addrs()
    _HOST_ADDRS_MEMO[key] = table
    return table


def _reset_host_addrs_cache():
    """Test seam: drop the memo so a fixture path can be re-read."""
    _HOST_ADDRS_MEMO.clear()


#: Test/ops seam for the address probe, read from the PROCESS environment the
#: same way `HOST_LABEL_ENV_FILE` is: a space- or comma-separated list of the
#: addresses this machine is to be treated as holding. Set-but-EMPTY means "holds
#: none of them", which is how a test makes the third signal deterministic
#: without depending on which of the two real machines it is running on.
HOST_LABEL_ADDRS_ENV = "HOST_LABEL_ADDRS"


def _bind_holds_address(addr: str) -> bool:
    """True when THIS machine holds `addr` on some interface.

    🔴 A BIND, NOT AN ENUMERATION AND NOT A CONNECT — three properties matter.
    (a) It cannot block: binding a UDP socket to a local address sends no packet
    and contacts nothing, so there is no timeout to get wrong on a hot path
    (measured 2026-09-12: ~100 µs for five candidates on workbench, ~75 µs on the
    laptop). (b) It needs no external binary, which the calling systemd units
    require — see HOST_ROLE_SH. (c) It sees addresses a default-route probe
    cannot: measured the same day, the laptop was on `192.168.1.4`, NOT on the
    192.168.50.0/24 LAN at all, so only its nebula address identified it. A
    `connect()`-to-a-public-resolver source-address trick — what
    `browser-bridge/server.py` does — would have reported neither. (The address
    is described rather than spelled: `test_no_public_ips.py` refuses a routable
    public IP literal anywhere in this repo, and rightly.)

    An address this machine does not hold gives `EADDRNOTAVAIL`. The one way a
    bind can succeed for a foreign address is `net.ipv4.ip_nonlocal_bind=1`
    (measured `0` on both hosts) — and that case cannot mislabel anything, since
    it would make BOTH hosts' addresses match and `address_host_label()` refuses
    on a multi-host match rather than picking one.
    """
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    except OSError:
        return False
    try:
        sock.bind((addr, 0))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def _holds_from_env(raw: str):
    held = frozenset(part for part in raw.replace(",", " ").split() if part)
    return lambda addr: addr in held


def _default_holds():
    raw = os.environ.get(HOST_LABEL_ADDRS_ENV)
    return _bind_holds_address if raw is None else _holds_from_env(raw)


def address_host_label(holds=None, addrs=None):
    """The host label this machine's OWN addresses imply, or None.

    Raises `HostLabelConflict` when addresses of MORE THAN ONE host are present.
    🔴 THIS IS A DELIBERATE DIVERGENCE FROM `host-role.sh::detect_role`, which
    documents that a list carrying both hosts' primaries resolves to `workbench`.
    Its caller is a CONVERGER, where picking one and proceeding beats stopping;
    this module's callers include a ROUTING tool, where a wrong answer sends work
    to the wrong machine. Same table, different tie-break, both written down.
    """
    table = host_addrs() if addrs is None else tuple(addrs)
    probe = _default_holds() if holds is None else holds
    hits = []
    for label, addr in table:
        # 🔴 NOT AN OPTIMISATION — CORRECTNESS. Every host has TWO addresses in
        # this table and normally holds BOTH (workbench: 192.168.50.250 and
        # 10.42.0.30). Without this skip the same label lands in `hits` twice and
        # the multi-host refusal below fires on a perfectly ordinary machine.
        if label in hits:
            continue
        try:
            if probe(addr):
                hits.append(label)
        except OSError:
            continue
    if len(hits) > 1:
        raise HostLabelConflict(
            "this machine holds addresses belonging to more than one host (%s)"
            " — refusing to name it" % ", ".join(hits))
    return hits[0] if hits else None


# =========================================================================== #
# THE ANSWER
# =========================================================================== #
def _file_stated_label(text) -> str:
    """The valid `ACTIVITY_HOST=` label in an env-file body, or ""."""
    for line in (text or "").splitlines():
        line = line.strip()
        if line.startswith("ACTIVITY_HOST="):
            val = line[len("ACTIVITY_HOST="):].strip()
            if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
                val = val[1:-1]
            val = val.strip().lower()
            if val in HOST_NAMES:
                return val
    return ""


def stated_host_label(env=None, env_file=None) -> tuple:
    """`(source, label)` from the two DECLARED feeders, or `("", "")`.

    🔴 `env_file=None` MEANS `ACTIVITY_ENV`, RESOLVED AT CALL TIME — it is NOT
    the same as spelling the constant as the default. A default argument is
    bound once, when the `def` executes, so `monkeypatch.setattr(host_label,
    "ACTIVITY_ENV", …)` left the old path baked in and the patch was INERT:
    `test_peer_host.py`'s hermeticity fixture did exactly that and this function
    kept reading the operator's REAL collector config. It went unnoticed because
    the real answer and the fixture's happened to agree. Measured 2026-09-12.

    The environment wins over the file. A value that is not one of HOST_NAMES is
    ignored rather than passed through: a typo would otherwise mint a third host
    that nothing else in the fleet knows about, and — since the termwrite queue is
    keyed on this label — an agent computing one would poll for a host nobody
    enqueues to and deliver nothing, silently.
    """
    env_file = ACTIVITY_ENV if env_file is None else env_file
    e = os.environ if env is None else env
    v = (e.get("ACTIVITY_HOST") or "").strip().lower()
    if v in HOST_NAMES:
        return ("ACTIVITY_HOST", v)
    try:
        with open(env_file, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        text = ""
    label = _file_stated_label(text)
    return ((env_file, label) if label else ("", ""))


def local_host_label(env=None, env_file=None, holds=None, addrs=None) -> str:
    """This machine's ACTIVITY_HOST label. RAISES rather than guessing.

    `env_file=None` means `ACTIVITY_ENV`, resolved at CALL time — see
    `stated_host_label`, where spelling the constant as the default silently
    disarmed a test fixture.

    Precedence is unchanged where it was already right — `ACTIVITY_HOST` in the
    environment, then the collector's env file — and the machine's OWN ADDRESSES
    are the fallback AND the cross-check, never the primary.

    🔴 AN EXCEPTION, NOT A SENTINEL, AND THE REASON IS THIS MODULE'S OWN HISTORY.
    A returned `None`/`""` is only a guard if every consumer BRANCHES on it, and
    there are seven-plus of them; the first one that does not would splice the
    sentinel into a queue key, a ClickHouse row or an `ssh` argv and produce a
    new silent wrong answer in place of the old one. `ssh_target()` above already
    made this call for the same reason ("there is no safe fallback here, so there
    is none"), and the shell entry point below turns the exception back into the
    empty-stdout-plus-nonzero pair `transcript-push.sh` already treats as fatal.
    So: ONE failure mode, loud, and no consumer has to remember anything.

    ⚠ WHAT THIS COSTS: an operator who sets `ACTIVITY_HOST=laptop` on the
    workbench now gets `HostLabelConflict` instead of `laptop`. That is the
    mandate — the class of defect being closed is "a machine that believes it is
    the other machine" — but it does mean `ACTIVITY_HOST` is NOT an override.
    The declared seams are `HOST_LABEL_ADDRS` (below), the `holds`/`addrs`
    parameters, and, for the transcript feeder specifically, its own
    `TRANSCRIPT_PUSH_HOST`, which bypasses this module entirely.
    """
    env_file = ACTIVITY_ENV if env_file is None else env_file
    source, stated = stated_host_label(env=env, env_file=env_file)
    derived = address_host_label(holds=holds, addrs=addrs)
    if stated and derived and stated != derived:
        raise HostLabelConflict(
            "%s says this machine is %r, but the address it holds says %r — "
            "refusing to name it" % (source, stated, derived))
    if stated:
        return stated
    if derived:
        return derived
    raise HostLabelUnresolved(
        "cannot identify this machine: ACTIVITY_HOST is unset or invalid, %r "
        "supplies no valid ACTIVITY_HOST, and none of the known host addresses "
        "(%s) is held here. `hostname` is 'nixos' on both hosts, so there is "
        "nothing left to derive from — refusing to guess."
        % (env_file, ", ".join("%s=%s" % (l, a)
                               for l, a in (host_addrs() if addrs is None
                                            else tuple(addrs))) or "none"))


if __name__ == "__main__":
    # 🔴 THE SHELL ENTRY POINT. transcript-push.sh calls `python3 host_label.py`
    # rather than re-deriving the rule in shell — which is what it used to do, and
    # what got the two feeders out of step. Prints nothing but the label.
    #
    # 🔴 ON A REFUSAL, STDOUT STAYS EMPTY AND THE EXIT IS NON-ZERO — the two
    # signals `transcript-push.sh` already checks together ("A FAILURE HERE IS
    # FATAL, NOT A FALLBACK TO `uname -n`"). A traceback on stdout would be
    # captured as the host NAME by `HOST_NAME="$(python3 …)"`, so the message
    # goes to stderr and nothing else is printed.
    try:
        print(local_host_label())
    except HostLabelError as exc:
        sys.stderr.write("host_label: %s\n" % exc)
        raise SystemExit(3)
