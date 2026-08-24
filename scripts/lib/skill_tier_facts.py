#!/usr/bin/env python3
"""Project the skill-listing tier ledger into one machine-readable line.

`scripts/drift-check.sh` needs to know what `skillOverrides` the ledger asks a
host to carry. It could sed the JSON — and that is exactly the trap
`claude/RULES.md` names under "PARSING output makes its FORMAT a dependency you
did not pin": a pattern that stops matching returns a confident empty set, and an
empty expectation makes every host look compliant, in silence.

So the ledger is read by the ONE parser that owns it (`lib/skill_tiers.py`, the
same one `sync-skill-tiers.py` writes from), and this prints:

    ok name=value name=value ...      # sorted; `ok` alone means the ledger
                                      # asks for no overrides at all
    err <token>                       # on stderr, exit 1 — the caller reports
                                      # COULD NOT MEASURE and sets no rc

Optional argv[1] overrides the ledger path, so the suite can drive every branch
against a fixture instead of the live file.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def main(argv) -> int:
    try:
        import skill_tiers
    except Exception:
        print("err reader-unimportable", file=sys.stderr)
        return 1
    path = Path(argv[1]) if len(argv) > 1 and argv[1] else skill_tiers.LEDGER_PATH
    try:
        ledger = skill_tiers.load_ledger(path)
    except FileNotFoundError:
        print("err ledger-absent", file=sys.stderr)
        return 1
    except ValueError:
        print("err ledger-malformed", file=sys.stderr)
        return 1
    except OSError:
        print("err ledger-unreadable", file=sys.stderr)
        return 1
    want = skill_tiers.expected_overrides(ledger)
    print(" ".join(["ok"] + [f"{n}={v}" for n, v in sorted(want.items())]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
