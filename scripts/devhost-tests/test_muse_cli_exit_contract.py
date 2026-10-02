"""Binds muse-cli's exit-code contract, which `scripts/muse/muse` depends on.
Dev-host tier.

`cmd_send_cli` skips the pacing stamp on exactly ONE muse-cli exit code, because
that code means the send provably never left this machine. That is a fact about a
third-party binary, not about this repo, and until #1976 round 4 nothing read it —
so a renumbering in muse-cli 0.4.x would silently turn the carve-out into "skip the
gate after a possible real send", which is the exact hazard the wrapper exists to
prevent, with the suite fully green.

The history is why this is not paranoia: #1976 round 2 carved the WRONG code, taking
it from the wrapper's own stale header legend instead of from muse-cli, and no test
objected for two rounds.

🔴 WHY IT IS HERE AND NOT IN `scripts/tests`. It needs the `muse-cli` BINARY to
locate its installed source. The `nix build` sandbox tier has no uv tools, so the
test would skip there — and a skip in a hermetic target is an UNPINNED SKIP that
`run-tests.sh` GUARD 2 rejects with `ERROR: n UNPINNED skip group(s) — coverage
silently collapsed` / `RESULT: FAIL`. The dev-host tier would stay GREEN throughout,
because muse-cli is on the workbench's PATH: the two-tier trap exactly.
Round 4 shipped it into `scripts/tests` with a commit message asserting the skip kept
the sandbox "hermetic". That was backwards for this runner, and #1976 round 5 caught
it. The reasoning is `run-tests.sh`'s own (see its DEVHOST_TARGETS note, and the
identical fzf incident recorded there on 2026-09-09); this file applies it.

⚠ It reads muse-cli's installed SOURCE and never executes it. Executing muse-cli can
contact Meta.
"""

from __future__ import annotations

import pathlib
import re
import shutil

import pytest

# Each handler's `sys.exit` sits ~66-72 chars from its `except`, so {0,200} has ~3x
# headroom. Both residual blind spots — an `except` inside an intervening string, or
# a handler longer than the bound — fail LOUD (no match) rather than green.
CONTRACT = (("AuthError", 2), ("GatewayError", 3), ("TimeoutError", 4))


def _installed_cli_py() -> pathlib.Path:
    bin_path = shutil.which("muse-cli")
    if not bin_path:
        pytest.skip("muse-cli not installed; nothing to bind")
    root = pathlib.Path(bin_path).resolve().parent.parent
    cli = next(root.glob("lib/python*/site-packages/muse_cli/cli.py"), None)
    if cli is None:
        pytest.skip(f"muse-cli present at {bin_path} but cli.py not found under {root}")
    return cli


@pytest.mark.parametrize("exc,code", CONTRACT)
def test_muse_cli_still_maps_the_exception_to_the_code_the_wrapper_assumes(exc, code):
    """scripts/muse/muse carves ONLY rc 2 out of the pacing stamp."""
    cli = _installed_cli_py()
    flat = " ".join(cli.read_text().split())
    # 🔴 `(?:(?!except\b).)` — the span must NOT cross into the next `except`.
    # With a plain `.{0,200}?` the AuthError->3 pattern MATCHED, by running past
    # AuthError's own handler into GatewayError's `sys.exit(3)`: the guard was
    # blind to precisely the renumbering it exists to catch. Found by the
    # NEGATIVE control below, never by the positive one, which was green
    # throughout. (#1976 round 4.)
    pat = rf"except {exc} as \w+:(?:(?!except\b).){{0,200}}?sys\.exit\({code}\)"
    assert re.search(pat, flat), (
        f"muse-cli no longer maps {exc} -> exit {code}. scripts/muse/muse carves "
        f"ONLY rc 2 out of the pacing stamp, on the grounds that it is AuthError "
        f"and therefore a provable non-send. Re-derive the mapping from {cli} and "
        f"update cmd_send_cli, the header legend and SKILL.md TOGETHER — all three "
        f"have been wrong before, in both directions."
    )


def test_the_contract_regex_rejects_every_wrong_mapping():
    """NEGATIVE CONTROL, and the only reason the guard above is trustworthy.

    A guard that only ever asserts the TRUE mapping cannot distinguish "the
    contract holds" from "my pattern matches anything". All six wrong pairings
    must fail to match. This is the control that caught the `.{0,200}?` span bug
    while the positive assertions were green.
    """
    cli = _installed_cli_py()
    flat = " ".join(cli.read_text().split())
    codes = [c for _, c in CONTRACT]
    bad = []
    for exc, right in CONTRACT:
        for code in codes:
            if code == right:
                continue
            pat = rf"except {exc} as \w+:(?:(?!except\b).){{0,200}}?sys\.exit\({code}\)"
            if re.search(pat, flat):
                bad.append(f"{exc}->{code}")
    assert not bad, (
        f"the contract pattern matches wrong mappings {bad} — it would not "
        "notice a renumbering, which is the only thing it is for"
    )
