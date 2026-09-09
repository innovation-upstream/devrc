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
