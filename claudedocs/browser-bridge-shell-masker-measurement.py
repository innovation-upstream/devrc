#!/usr/bin/env python3
"""Re-runnable evidence for deleting `mask_shell_noncode` from
`scripts/browser-bridge/tests/test_surface_parity.py`.

WHY THIS FILE EXISTS
--------------------
That module used to carry a 129-line hand-rolled shell lexer (CODE/SQ/DQ/BQ
states, a `$(` suspend stack, a heredoc queue) whose only job was to answer "is
this byte inside code?" before the `cmd_op <op>` dispatch regex ran. Successive
audit rounds kept finding real over-strips in it; each fix added a branch and
exposed another. The defect rate never fell, because the thing being patched was
a hand-rolled shell lexer.

It was deleted on MEASUREMENT. This script IS that measurement, so the next
round that is tempted to reinstate quote/heredoc tracking can re-derive the
numbers instead of re-litigating them.

RUN IT
------
    nix develop ~/workspace/devrc -c \\
      python3 claudedocs/browser-bridge-shell-masker-measurement.py

Add `--base <ref>` to also load the PRE-DELETION module out of git and run the
full 2x2 (masker x anchor). Any ref whose tree still contains
`mask_shell_noncode` works; the numbers below were taken against `cdfd14ab`,
the base this deletion branched from.

WHAT IT PRINTS, AND WHAT WAS MEASURED 2026-09-10 at that ref
------------------------------------------------------------
Corpus: `scripts/browser-bridge/browser`, 144,273 bytes, 53 `cmd_op`
occurrences of which 25 are prose (comments / strings / an embedded `python3 -c`
program body).

    masker  anchor    ops parsed
    ------  ------    ----------
    on      on        19   <- what shipped
    OFF     on        19   IDENTICAL, same 19 names
    on      OFF       19   IDENTICAL, same 19 names
    OFF     OFF       27   8 junk words: OP, already, can, dispatches,
                           in, inside, runs, splices

So each defence was independently sufficient on this corpus, and the lexer
detected NOTHING the command-position anchor had not already rejected. Its
entire marginal contribution was ONE line -- `browser`'s own comment

    # substitution (`resp="$(cmd_op nav …)"`), and a subshell cannot write the

which the anchor accepts (a `$(` precedes the mention) and which is harmless
only because `nav` happens to be a real op. That single case is now rejected by
a whole-line comment filter, one regex with no state and no over-strip failure
mode: a shell dispatch can never begin with `#`, so dropping such a line can
remove a MENTION and never a call.

CONSTRUCT CENSUS on the same corpus (why no replacement lexer was written)
-------------------------------------------------------------------------
    heredoc openers `<<WORD`            0
    here-strings `<<<`                  1  (inside a single-quoted jq program)
    process substitutions `<(` `>(`     0
    `$( (` subshell-in-cmdsubst         0
    ANSI-C quoting `$'…'`              12  (all `$'\\n'` inside `${…}` in a
                                            double-quoted word -- never around a
                                            `cmd_op` mention)
    backticks, total                  980
    backticks outside comments/strings  0

TWO REPLACEMENTS WERE TRIED AND REFUTED, both by measurement
------------------------------------------------------------
1. **A real tokeniser.** stdlib `shlex` (posix=True, punctuation_chars, newline
   as a separator) parsed only 11 of the 19 real dispatches: a double-quoted
   span is ONE token to it, so it cannot see `resp="$(cmd_op screenshot …)"` at
   all, which is how 8 of the 19 are spelled. `shfmt` and `shellcheck` -- the
   only real shell parsers that would work -- are NOT in this repo's devShell,
   so using one means a new input to the hermetic pre-merge check for one test.
2. **A whole-line-shape whitelist** (dispatch must match `^cmd_op <op>` or
   `^<var>="?$( cmd_op <op>`). Measured against the rig: it leaks exactly the
   same 5 mentions as no masking at all (a prose line at column 0 inside a
   multi-line quoted string or a heredoc body matches `^cmd_op <word>`), so it
   buys nothing over the anchor while adding a second grammar to maintain.

THE RESIDUAL BLIND SPOT, stated rather than implied
---------------------------------------------------
The surviving parser cannot see a `cmd_op <word>` mention (a) at the start of a
line inside a multi-line quoted string or a heredoc body, or (b) in a TRAILING
comment behind a `(`/`;`/`|`/`&`. Neither exists in `browser` today (measured
above). If one appears, the result is a PHANTOM op, and
`test_the_classification_matches_what_the_cli_actually_dispatches` and
`test_the_dispatch_parser_did_not_over_tighten_on_the_real_cli` both report it
loudly, by name, as `only-in-CLI=[<word>]`. It is a false RED, not a silent
hole, and the remedy is to reword the mention -- `browser`'s own `classify()`
docstring documents that discipline.
"""
from __future__ import annotations

import argparse
import importlib.util
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BB = REPO / "scripts" / "browser-bridge"
CLI = BB / "browser"
RIG = BB / "tests" / "fixtures" / "cmd_op_parse_rig.sh"
REL = "scripts/browser-bridge/tests/test_surface_parity.py"


def _load(name: str, path: Path):
    sys.path.insert(0, str(BB))
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _census(src: str) -> list[tuple[str, int]]:
    return [
        ("heredoc openers  <<WORD    ",
         len(re.findall(r"""<<-?\s*['"]?[A-Za-z_][A-Za-z0-9_]*""", src))),
        ("here-strings     <<<       ", src.count("<<<")),
        ("process subst    <( or >(  ", len(re.findall(r"[<>]\(", src))),
        ("$( (  subshell-in-cmdsubst ", len(re.findall(r"\$\(\s*\(", src))),
        ("ANSI-C quoting   $'        ", src.count("$" + "'")),
        ("backticks, total           ", src.count("`")),
        ("cmd_op occurrences         ", len(re.findall(r"cmd_op", src))),
    ]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", help="git ref whose tree still has mask_shell_noncode")
    args = ap.parse_args()

    now = _load("tsp_now", BB / "tests" / "test_surface_parity.py")
    src = CLI.read_text(encoding="utf-8")

    anchored = now._CMD_OP_CALL
    bare = re.compile(r"\bcmd_op\s+([A-Za-z]+)")
    ident = (lambda s: s)

    print(f"corpus: {CLI}  ({len(src)} bytes)")
    print("\n--- construct census ---")
    for label, n in _census(src):
        print(f"  {label}: {n}")

    print("\n--- 2x2 over the SURVIVING defences (comment filter x anchor) ---")
    for cf, cf_lbl in ((now._drop_comment_lines, "on "), (ident, "OFF")):
        for rx, rx_lbl in ((anchored, "on "), (bare, "OFF")):
            got = set(rx.findall(cf(src)))
            print(f"  comment-filter={cf_lbl} anchor={rx_lbl} -> n={len(got):3d} "
                  f"{sorted(got)}")

    print("\n--- rig ---")
    rig_src = RIG.read_text(encoding="utf-8")
    got = now.parse_dispatched_ops(RIG)
    leaked = sorted(o for o in got if o.startswith("phantom"))
    print(f"  parsed n={len(got)}  phantoms leaked={leaked}")

    if not args.base:
        print("\n(pass --base <ref> to add the pre-deletion masker rows)")
        return 0

    out = subprocess.run(["git", "-C", str(REPO), "show", f"{args.base}:{REL}"],
                         capture_output=True, text=True)
    if out.returncode:
        print(f"\ncannot read {REL} at {args.base}: {out.stderr.strip()}")
        return 1
    tmp = Path(tempfile.mkdtemp()) / "before_surface_parity.py"
    tmp.write_text(out.stdout, encoding="utf-8")
    before = _load("tsp_before", tmp)
    if not hasattr(before, "mask_shell_noncode"):
        print(f"\n{args.base} has no mask_shell_noncode -- pick an earlier ref")
        return 1

    print(f"\n--- 2x2 over the ORIGINAL defences (masker x anchor) at {args.base} ---")
    for mk, mk_lbl in ((before.mask_shell_noncode, "on "), (ident, "OFF")):
        for rx, rx_lbl in ((anchored, "on "), (bare, "OFF")):
            got = set(rx.findall(mk(src)))
            print(f"  masker={mk_lbl} anchor={rx_lbl} -> n={len(got):3d} {sorted(got)}")

    b = before.parse_dispatched_ops(CLI)
    a = now.parse_dispatched_ops(CLI)
    print(f"\n  BEFORE parse_dispatched_ops n={len(b)}")
    print(f"  AFTER  parse_dispatched_ops n={len(a)}")
    print(f"  IDENTICAL ? {a == b}   before-only={sorted(b - a)} "
          f"after-only={sorted(a - b)}")
    print(f"  (positive control: the two parsers DO differ on the pre-deletion "
          f"rig -- see the module docstring)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
