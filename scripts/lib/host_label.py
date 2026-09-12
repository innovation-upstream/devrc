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
"""
from __future__ import annotations

import os

#: The host vocabulary the read model, the termwrite queue and the activity
#: collector all use. `hostname` is "nixos" on BOTH machines, which is precisely
#: why it cannot be the source of truth.
HOST_NAMES = ("workbench", "laptop")
DEFAULT_LOCAL_HOST = "workbench"

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
#:     no `user@addr` STRING CONSTANT for a peer, in any non-test file under
#:     `scripts/`, outside this module.
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
#:     `scripts/README.md` calls it "the ONE host-identity predicate", so two
#:     modules each claim to be the single home. It answers a different question
#:     (which role am I, from a list of interface addresses) and it is shell, so
#:     folding it in is a design change, not a rename.
#:   * `scripts/browser-bridge/server.py` — bare IPs in `_HOST_IP_ORDER`.
#:
#: 🔴 NO LINE NUMBERS, deliberately — `nix/home.nix` states the rule this repo
#: already learned: "a line number is a claim that rots silently". An earlier
#: draft of this comment carried four, and one had already gone stale within the
#: same PR.
#:
#: These modules DERIVE from this table (all were literals):
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


def local_host_label(env=None, env_file: str = ACTIVITY_ENV) -> str:
    """This machine's ACTIVITY_HOST label.

    The ENVIRONMENT wins over the file, then the file, then the default. A value
    that is not one of HOST_NAMES is ignored rather than passed through: a typo
    would otherwise mint a third host that nothing else in the fleet knows about,
    and — since the termwrite queue is keyed on this label — an agent computing
    one would poll for a host nobody enqueues to and deliver nothing, silently.
    """
    e = os.environ if env is None else env
    v = (e.get("ACTIVITY_HOST") or "").strip().lower()
    if v in HOST_NAMES:
        return v
    try:
        with open(env_file, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        text = ""
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("ACTIVITY_HOST="):
            val = line[len("ACTIVITY_HOST="):].strip()
            if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
                val = val[1:-1]
            val = val.strip().lower()
            if val in HOST_NAMES:
                return val
    return DEFAULT_LOCAL_HOST


if __name__ == "__main__":
    # 🔴 THE SHELL ENTRY POINT. transcript-push.sh calls `python3 host_label.py`
    # rather than re-deriving the rule in shell — which is what it used to do, and
    # what got the two feeders out of step. Prints nothing but the label.
    print(local_host_label())
