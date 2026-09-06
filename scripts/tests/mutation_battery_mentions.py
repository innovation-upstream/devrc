#!/usr/bin/env python3
"""Mutation battery for the MENTION pipeline — scanner, tailer and hint handler.

    python3 scripts/tests/mutation_battery_mentions.py

🔴 NOT COLLECTED BY THE GATE, ON PURPOSE — and the filename is the mechanism:
`scripts/run-tests.sh` collects `test_*.py` only. This is a MANUAL instrument,
run when the mention pipeline changes: it rewrites tracked source in place, one
mutant at a time, and runs the three mention suites after each. A gate that edits
tracked source is a gate nobody can run concurrently.

WHY IT IS COMMITTED. PR #1313's round-1 sweep lived in /tmp, so the auditor could
not re-run it and built their own — which found four guards the first sweep had
not imagined, including a `profile="telemetry"` mutant at the click surface that
the whole 313-test suite tolerated. A mutation result quoted from an instrument
the reader cannot run is a claim, not evidence. This makes it evidence.

🔴 IT SPANS FOUR FILES, which is what this battery adds over its two siblings.
The defect class it exists for is a SEAM: `mention_scan.py` decides what may be
detected, `session-tailer.py` decides what is recorded, `mention-open.py` decides
what is clicked, and `nix/programs/alacritty/default.nix` decides what the
terminal underlines AND what is on the handler's PATH — and every round-1
survivor lived at one of those joins, not inside one file. The nix file used to
be collected but not mutated; K36 mutates it, because "its suite is collected"
was a claim nothing checked. So `TARGETS` names a file per mutant and
`scripts/tests/test_mutation_battery_anchors.py` (which IS collected) reads it,
so a row whose anchor stops occurring exactly once fails the push rather than
scoring a silent SURVIVED for whoever next runs this by hand.

READ BEFORE TRUSTING A VERDICT:

  * The CONTROL runs first and aborts on a red OR EMPTY baseline. A zero is
    indistinguishable from a probe wired to nothing until something makes the
    number move.
  * `P1` is the POSITIVE CONTROL — a mutant that MUST die, breaking a URL every
    suite reads. A run in which P1 survives is a broken battery, not a clean
    sweep, and the final line says so.
  * Every run sets `PYTHONDONTWRITEBYTECODE=1`. CPython validates a cached module
    on mtime-in-whole-SECONDS plus size, so a same-length edit landing inside one
    second of the last import is invisible: the suite would import the ORIGINAL
    bytecode and the mutant would be scored SURVIVED without ever executing.
    Several rows here ARE same-length-ish edits.
  * A mutant whose pattern is NOT FOUND is reported as such and counted as a
    problem. Silent non-application is how a battery reports a clean sweep of
    mutations it never made.
  * 🔴 A MUTANT THAT COLLECTED NOTHING CANNOT SCORE `SURVIVED`. This was a real
    hole in this instrument: the `npass < 200` sanity check ran on the CONTROL
    only, and the per-mutant verdict was `if not nfail: SURVIVED`. A mutant that
    makes a module UNIMPORTABLE produces `0 failed, 0 passed` and a collection
    ERROR — pytest's summary then says `N errors`, which the `(\\d+) failed`
    regex does not see — so the row was reported as "no test can see this
    change" when in truth no test RAN. `classify()` now answers `NOT-OBSERVED`
    for that, counted with the problems, and the floor is derived from the
    control's own reading rather than from a literal.
  * SURVIVED does not mean "the code is wrong". It means "no test can see this
    change" — usually a missing test, occasionally genuinely-equivalent code.
  * THREE KILL VERDICTS. `KILLED` is "the suite went red". A row carrying an
    `expected` phrase reports `KILLED(attributed)` only when that phrase appears
    in pytest's `E ` lines, and `KILLED-WRONG-REASON` otherwise — the latter is a
    FAILURE of this battery, counted with the survivors, because the row's named
    assertion is not the one that went red. Nearly every row here carries one:
    these mutants redden broad swathes of the suite, so a bare kill would not
    show that the guard the row is about is the guard that fired.
  * Sources are restored in a `finally` AND the restore is verified by hash, so
    an abort mid-run cannot leave a mutated tree behind unreported.
"""
from __future__ import annotations

import hashlib
import os
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]

SCAN = ROOT / "scripts/collector/mention_scan.py"
TAILER = ROOT / "scripts/collector/claude/session-tailer.py"
OPEN_ = ROOT / "scripts/mention-open.py"
ALACRITTY = ROOT / "nix/programs/alacritty/default.nix"

# The primary target, for the shared anchor checker's single-file affordances.
SCRIPT = SCAN

SUITES = (
    "scripts/tests/test_mention_scan.py",
    "scripts/tests/test_mention_open.py",
    "scripts/collector/claude/tests/test_session_tailer.py",
    # 🔴 NOT A MUTATION TARGET — A SUITE, and it is here because a guard was
    # scored KILLED-WRONG-REASON without it. The seam between the Alacritty hint
    # regex's digit bound and the handler's `_OFFER_NUM_RE` is asserted in THIS
    # file, so widening `_OFFER_NUM_RE` (K20) reddened only an incidental
    # parametrized case while the test that names the hazard never ran. A battery
    # that does not collect a guard's own suite reports on mutations that guard
    # cannot see.
    "scripts/tests/test_alacritty_hints.py",
)

# (id, shape, description, old, new[, expected]) — `old` must occur EXACTLY once
# in that row's TARGETS file. Same contract as the two sibling batteries: `old`
# and `new` may be TUPLES of equal length, applied in order, each half still
# required to occur exactly once. The only addition here is that the FILE is
# per-row rather than module-wide (see `TARGETS` below the table).
MUTANTS: list[tuple] = [
    # ---- POSITIVE CONTROL --------------------------------------------------
    ("P1", "control", "break a URL every suite reads — MUST be killed",
     'CLAWGATE_TASKS_URL = "https://clawgate.zacx.dev/tasks"',
     'CLAWGATE_TASKS_URL = "https://example.invalid/tasks"'),

    # ---- F1: the dedupe identity discards the attribution -------------------
    ("K1", "deletion", "the key drops the repo again (the round-1 defect): two "
                       "repositories referencing one number collapse to one row",
     '    return f"{m[\'platform\']}:{m[\'raw\']}@{repo}" if repo else f"{m[\'platform\']}:{m[\'raw\']}"\n',
     '    return f"{m[\'platform\']}:{m[\'raw\']}"\n',
     "dropped a second repository's reference"),
    ("K2", "widening", "the suffix becomes UNCONDITIONAL, re-keying every "
                       "already-emitted mention on both hosts",
     '    return f"{m[\'platform\']}:{m[\'raw\']}@{repo}" if repo else f"{m[\'platform\']}:{m[\'raw\']}"\n',
     '    return f"{m[\'platform\']}:{m[\'raw\']}@{repo}"\n',
     "the unattributed key format MOVED"),
    ("K3", "operand swap", "the key uses the bare id instead of the raw text",
     '    return f"{m[\'platform\']}:{m[\'raw\']}@{repo}" if repo else f"{m[\'platform\']}:{m[\'raw\']}"\n',
     '    return f"{m[\'platform\']}:{m[\'id\']}@{repo}" if repo else f"{m[\'platform\']}:{m[\'id\']}"\n',
     "keyed on the id, not the raw text"),

    # ---- F2: `explicit` reported for an owner nobody wrote ------------------
    ("K4", "deletion", "the mapping-resolved path is labelled `explicit` again",
     "            source = SOURCE_EXPLICIT if owner else SOURCE_MAPPED\n",
     "            source = SOURCE_EXPLICIT\n",
     "a MAPPING-resolved repo is reported as if the text stated it"),
    ("K5", "widening", "`mapped` is spelled `explicit`, so the two collapse "
                       "again one level down",
     'SOURCE_MAPPED = "mapped"\n', 'SOURCE_MAPPED = "explicit"\n',
     "a MAPPING-resolved repo is reported as if the text stated it"),
    ("K6", "deletion", "drop the `source if repo else SOURCE_NONE` guard, so an "
                       "UNRESOLVED `repo#N` claims `repo_source=mapped`",
     '        "repo_source": source if repo else SOURCE_NONE,\n',
     '        "repo_source": source,\n',
     "claims a resolution that did not happen"),

    # ---- F3: the profile split, at all four enforcement sites ---------------
    ("K7", "deletion", "the ADJACENT attribution route runs in the terminal "
                       "profile too",
     '        adjacent = _adjacent_repo(text, start, repos) if "REPO_BEFORE_RE" in on else ""\n',
     "        adjacent = _adjacent_repo(text, start, repos)\n",
     "the adjacent attribution route answered in the TERMINAL profile"),
    ("K8", "deletion", "the URL attribution route runs in the terminal profile",
     '    url_repo = _sole_repo_named_by(GITHUB_URL_RE, text) if "GITHUB_URL_RE" in on else ""\n',
     "    url_repo = _sole_repo_named_by(GITHUB_URL_RE, text)\n",
     "the url attribution route answered in the TERMINAL profile"),
    ("K9", "deletion", "the --repo FLAG attribution route runs in the terminal "
                       "profile",
     '    flag_repo = _sole_repo_named_by(REPO_FLAG_RE, text) if "REPO_FLAG_RE" in on else ""\n',
     "    flag_repo = _sole_repo_named_by(REPO_FLAG_RE, text)\n",
     "the flag attribution route answered in the TERMINAL profile"),
    ("K10", "widening", "the CLICK handler scans at the telemetry profile — the "
                        "wide surface becomes clickable",
     "    spans = scan_mention_spans(text, repos=repos, default_repo=default_repo)\n",
     "    spans = scan_mention_spans(text, repos=repos, default_repo=default_repo,\n"
     '                               profile="telemetry")\n',
     "reached the CLICK surface"),

    # ---- F4: the disclosure guard's two blind paths -------------------------
    ("K11", "disclosure", "the REFUSAL path offers the universe as a hint — the "
                          "path `--print` reaches and the picker does not",
     "        return refuse(span, text, args)\n",
     '        notify("cannot resolve it", ", ".join(repo_universe(discovered)))\n'
     "        return refuse(span, text, args)\n",
     "REFUSAL-PATH DISCLOSURE"),
    ("K12", "disclosure", "the picker path logs the universe to stdout",
     "        candidates = universe\n",
     '        print("universe:", repo_universe(discovered))\n'
     "        candidates = universe\n",
     "PICKER-PATH DISCLOSURE"),
    ("K13", "disclosure", "the emit line ships the WHOLE mapping beside the one "
                          "repo the mention was attributed to",
     "        f\"b64:repo={m.get('repo', '')}\",\n",
     "        f\"b64:repo={m.get('repo', '')}\",\n"
     '        f"b64:known_repos={sorted(load_mention_repos().values())}",\n',
     "SPOOL DISCLOSURE (attributed run)"),
    ("K14", "disclosure", "the mapping rides along in `context`, on a mention "
                          "with NO attribution at all",
     "        f\"b64:context={m['context']}\",\n",
     "        f\"b64:context={m['context']} {sorted(load_mention_repos())}\",\n",
     "SPOOL DISCLOSURE (unattributed run)"),

    # ---- F3, the TWO-SITE shape: one pattern, gated in two places -----------
    #
    # 🔴 `GITHUB_URL_RE` IS GATED TWICE — once as an ATTRIBUTION source and once
    # as a DETECT pattern — and K8 removes only the first. A maintainer
    # "simplifying the profile gating for this one pattern" removes both, and
    # that is a strictly worse defect than either half: the URL shape becomes
    # clickable AND starts attributing on the terminal profile. Expressing only
    # one half would put a narrower label on the row than the hazard deserves.
    # This is also the battery's multi-site row, which is what lets
    # `test_the_PAIR_check_goes_RED_on_a_real_battery_COPY` run instead of skip.
    ("K16", "widening", "BOTH `GITHUB_URL_RE` profile gates are removed, so the "
                        "URL shape both attributes AND is clickable in terminal",
     ('    url_repo = _sole_repo_named_by(GITHUB_URL_RE, text) if "GITHUB_URL_RE" in on else ""\n',
      '    if "GITHUB_URL_RE" in on:\n'),
     ("    url_repo = _sole_repo_named_by(GITHUB_URL_RE, text)\n",
      "    if True:\n"),
     "GITHUB_URL_RE: a telemetry-only shape reached the CLICK surface"),

    # ---- the nit list: live, reachable, previously untested ------------------
    ("K15", "deletion", "TASK_ANCHOR_RE loses its `(?<![&#])` left guard, so an "
                        "HTML entity and a `##` run both parse as the anchor",
     'TASK_ANCHOR_RE = re.compile(rf"(?<![&#])#task-(?P<num>{_NUM})" + _NUM_END)\n',
     'TASK_ANCHOR_RE = re.compile(rf"#task-(?P<num>{_NUM})" + _NUM_END)\n',
     "the legacy-anchor left guard is gone"),

    # ---- F5: the click path must make NO NETWORK CALL ----------------------
    #
    # 🔴 The rows below exist because a GitHub-WIDE namesake search used to live
    # in this handler and cost 4.3s per click, answering with strangers' repos.
    # It is deleted; these are what stop it — or any replacement — coming back.
    ("K17", "widening", "a network call is re-added to the resolution path: the "
                        "deleted GitHub-wide namesake search, verbatim",
     "        discovered = discover_repos()\n",
     '        subprocess.run(["gh", "api", "search/repositories", "--method",\n'
     '                        "GET", "-f", "q=x in:name"],\n'
     "                       capture_output=True, text=True, timeout=5)\n"
     "        discovered = discover_repos()\n",
     "the resolution path's command ledger MOVED"),
    ("K18", "widening", "the same call wearing a DIFFERENT name, which a ban on "
                        "the word `gh` alone would not see",
     "    discovered: dict = {}\n",
     "    discovered: dict = {}\n"
     '    subprocess.run(["git", "ls-remote", "https://github.com/x/y"],\n'
     "                   capture_output=True, text=True, timeout=5)\n",
     "the resolution path's command ledger MOVED"),

    # ---- F6: OFFERED is not RESOLVED ---------------------------------------
    ("K19", "deletion", "a universe row becomes auto-openable again, so a click "
                        "on a hex colour opens an unrelated repo's issue",
     "        offered_universe = True\n", "        offered_universe = False\n",
     "bypassing the picker"),
    ("K20", "widening", "`_OFFER_NUM_RE` loses its bound and drifts away from "
                        "the Alacritty hint regex it mirrors",
     '_OFFER_NUM_RE = re.compile(r"#(?P<num>[0-9]{1,6})")\n',
     '_OFFER_NUM_RE = re.compile(r"#(?P<num>[0-9]{1,9})")\n',
     "the handler offers up to"),
    ("K21", "deletion", "the `span is None` arm of the measurement pass is "
                        "reverted, so a six-digit click dead-ends again",
     '    unresolved = span is None or span["ambiguous"] or not candidates\n',
     '    unresolved = span is not None and (span["ambiguous"] or not candidates)\n',
     "DEAD-ENDED instead of offering the picker"),
    ("K22", "deletion", "`--print` is allowed to offer the universe, so a "
                        "non-interactive consumer is answered with a question — "
                        "and private repo names reach stdout",
     "    may_offer_universe = (not args.print_only and not args.no_discovery\n"
     "                          and not colour)\n",
     "    may_offer_universe = (not args.no_discovery\n"
     "                          and not colour)\n",
     "a refusal must print no URL at all"),

    # ---- F7: the refusal must say WHICH empty it is ------------------------
    ("K23", "deletion", "`universe_reason` stops distinguishing a mapping that "
                        "PARSED but holds nothing usable from one that is fine",
     "    if not clean_repo_map(raw):\n", "    if False:\n",
     "no usable rows"),
    ("K24", "operand swap", "the refusal SUBSTITUTES the cause for the advice — "
                            "exactly the regression that happened once before",
     '    notify(f"cannot resolve {subject}", f"{why} — {advice}")\n',
     '    notify(f"cannot resolve {subject}", why)\n',
     "the actionable advice was dropped"),

    # ---- F8: the 16-thread fan-out, which had ZERO coverage ----------------
    #
    # 🔴 EVERY WORKSPACE FIXTURE IN THE SUITE USED TO HOLD ONE CHECKOUT, so both
    # rows below SURVIVED the whole 352-test suite: with a single entry,
    # `zip(entries, results)` and `zip(entries, reversed(results))` are the same
    # function, and there is no second row for an empty answer to overwrite.
    ("K25", "operand swap", "the fan-out pairs each checkout with its NEIGHBOUR's"
                            " owner — `~/workspace/devrc` then answers "
                            "`devrc#1291` with another org's issue 1291",
     "        resolved = _fan_out(entries)\n",
     "        resolved = _fan_out(entries)[::-1]\n",
     "paired a checkout with a NEIGHBOUR's owner"),
    ("K26", "deletion", "a checkout whose `git remote` failed writes \"\" OVER a "
                        "good row the mapping supplied, un-resolving a name the "
                        "host could answer a moment ago",
     "        if full:\n            out[entry.name] = full\n",
     "        out[entry.name] = full\n",
     "ERASED the mapping's answer"),
    ("K33", "deletion", "the fan-out stops degrading to serial, so a box at its "
                        "thread limit gets an exception out of a DETACHED "
                        "process and a click that does nothing",
     "    except (ImportError, OSError, RuntimeError):\n"
     "        resolved = [repo_of_checkout(p) for p in entries]\n",
     "    except (ImportError, OSError, RuntimeError):\n"
     "        resolved = []\n",
     "did not DEGRADE to a serial fan-out"),

    # ---- F9: the no-network ledger was one word short of its own claim -----
    ("K27", "widening", "a `git remote update` — a fetch per remote — is added "
                        "to the measurement leg. Under the OLD two-word ledger "
                        "this survived all 352 tests",
     '    return parse_owner_repo(_git(["remote", "get-url", "origin"], cwd=str(path)))\n',
     '    _git(["remote", "update"], cwd=str(path))\n'
     '    return parse_owner_repo(_git(["remote", "get-url", "origin"], cwd=str(path)))\n',
     "the resolution path's command ledger MOVED"),

    # ---- F10: a refusal that shows nothing ---------------------------------
    ("K28", "deletion", "`notify-send` loses its `--`, so every refusal body "
                        "beginning with a flag name is parsed as an option and "
                        "NO toast appears (exit 1, swallowed by check=False)",
     '        subprocess.run(["notify-send", "-a", "mention-open", "--", summary, body],\n',
     '        subprocess.run(["notify-send", "-a", "mention-open", summary, body],\n',
     "notify-send got a body starting with `--`"),
    ("K34", "deletion", "the entry point stops going through `guarded_main`, so "
                        "an unexpected exception is a silent click again",
     "    raise SystemExit(guarded_main())\n",
     "    raise SystemExit(main())\n",
     "does not call guarded_main()"),

    # ---- F11: six digits is answered, not asked about ----------------------
    ("K29", "deletion", "a six-digit colour literal raises the several-hundred-"
                        "row picker again, every row of which 404s",
     # Anchored with the following line: `refuse()` calls the same helper, and
     # mutating THAT one changes only the refusal's wording, not whether the
     # picker is raised — a different mutant under this row's name.
     "    colour = colour_literal_offer(span, text)\n\n    # PASS 2",
     '    colour = ""\n\n    # PASS 2',
     "raised the repository picker for a colour literal"),
    ("K30", "widening", "`colour_literal_offer` stops asking for `span is None`, "
                        "so a six-digit number the SCANNER ACCEPTED — from a URL "
                        "shape — would be dismissed as a colour",
     '    num = offer_number(text) if span is None else ""\n',
     "    num = offer_number(text)\n",
     "a colour literal BESIDE a real reference suppressed the reference"),

    # ---- F12: the picker must explain itself, the refusal must be actionable
    ("K31", "deletion", "the fuzzy picker is raised with NO note, so a dismissal "
                        "and a no-match are indistinguishable to the operator",
     "    url = pick(candidates, mesg=mesg)\n",
     "    url = pick(candidates)\n",
     "raised with NO explanation"),
    ("K32", "widening", "the staleness threshold is pushed past any real age, so "
                        "a mapping nothing regenerates never reports as old",
     "STALE_MAPPING_DAYS = 7\n", "STALE_MAPPING_DAYS = 99999\n",
     "never named the mapping's age"),
    ("K35", "deletion", "`--print` goes back to blaming the FLAG alone on a host "
                        "with no mapping, dropping the only cause the operator "
                        "can act on",
     "            if extra := (reason or staleness_note()):\n"
     '                why = f"{why}; also {extra}"\n',
     "            pass\n",
     "blamed the FLAG and never named the mapping"),
    ("K36", "deletion", "the alacritty wrapper drops `pkgs.git` from the hint's "
                        "PATH: `git` is then absent under the display manager's "
                        "environment, FileNotFoundError is caught as OSError, "
                        "and discovery is INERT in production",
     "      pkgs.python312 pkgs.git pkgs.tmux pkgs.xdg-utils pkgs.libnotify\n",
     "      pkgs.python312 pkgs.tmux pkgs.xdg-utils pkgs.libnotify\n",
     "the wrapper's PATH is MISSING"),
]

TARGETS: dict[str, pathlib.Path] = {
    "P1": SCAN,
    "K1": TAILER, "K2": TAILER, "K3": TAILER,
    "K4": SCAN, "K5": SCAN, "K6": SCAN,
    "K7": SCAN, "K8": SCAN, "K9": SCAN, "K10": OPEN_,
    "K11": OPEN_, "K12": OPEN_, "K13": TAILER, "K14": TAILER,
    "K15": SCAN, "K16": SCAN,
    "K17": OPEN_, "K18": OPEN_, "K19": OPEN_, "K20": OPEN_,
    "K21": OPEN_, "K22": OPEN_, "K23": OPEN_, "K24": OPEN_,
    "K25": OPEN_, "K26": OPEN_, "K27": OPEN_, "K28": OPEN_,
    "K29": OPEN_, "K30": OPEN_, "K31": OPEN_, "K32": OPEN_,
    "K33": OPEN_, "K34": OPEN_, "K35": OPEN_,
    # 🔴 A FOURTH FILE, AND A NIX ONE. The wrapper's PATH is a seam between two
    # files in two languages that agree only by coincidence, and both directions
    # of disagreement are silent — see the test named in K36's `expected`. It is
    # mutated rather than merely collected because the header of this module
    # called it "not a mutation target", and that sentence was true only for as
    # long as nothing here read it.
    "K36": ALACRITTY,
}


def _digest(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def classify(nfail: int, npass: int, nerror: int, floor: int,
             expected: str | None, msgs: str) -> str:
    """The verdict for ONE mutant run. Pure, so it is unit-tested by
    `test_mutation_battery_anchors.py` rather than only exercised by running the
    whole battery for half an hour.

    🔴 `NOT-OBSERVED` COMES FIRST, AND IT IS THE FIX FOR THIS INSTRUMENT'S OWN
    BLIND SPOT. `SURVIVED` is a claim that every test RAN and none of them saw
    the change; a run that collected nothing satisfies `nfail == 0` just as
    well, and reports the strongest possible finding — "no test covers this" —
    from a suite that never executed. A mutant that makes a module unimportable
    is exactly that shape: pytest reports `N errors`, not `N failed`, and the
    old code read the absence of failures as survival.

    Two independent detectors, because either alone can be walked past: `nerror`
    catches a collection error by name, and the FLOOR catches a run that
    silently collected a fraction of the suite (a mutant inside a `conftest`, an
    import that hangs a whole file) without pytest calling it an error.
    """
    if nerror or (npass + nfail) < floor:
        return "NOT-OBSERVED"
    if not nfail:
        return "SURVIVED"
    if expected is None:
        return "KILLED"
    return "KILLED(attributed)" if expected in msgs else "KILLED-WRONG-REASON"


def run_suite(messages: bool = False) -> tuple[int, int, int, list[str], str]:
    """Run the mention suites once. Returns (failed, passed, errors, killers, msgs).

    🔴 ONLY THE `E ` LINES COUNT AS "THE MESSAGE". Under `--tb=short` pytest also
    echoes the SOURCE of the failing statement, which for an assert carrying an
    f-string message contains that message's literal text — so matching the whole
    output would report a right-reason kill for a test that never evaluated the
    assertion. pytest prefixes rendered assertion lines with `E `.
    """
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    r = subprocess.run(
        [sys.executable, "-m", "pytest", *SUITES, "-p", "no:cacheprovider",
         "--tb=short" if messages else "--tb=no", "-q"],
        cwd=ROOT, capture_output=True, text=True, env=env, timeout=1800,
    )
    out = r.stdout
    killers = sorted({ln.split("::")[1].split("[")[0]
                      for ln in out.splitlines()
                      if ln.startswith("FAILED") and "::" in ln})
    nfail = int(m.group(1)) if (m := re.search(r"(\d+) failed", out)) else 0
    npass = int(m.group(1)) if (m := re.search(r"(\d+) passed", out)) else 0
    # 🔴 `error` IS NOT `failed`, AND PYTEST SAYS SO IN A DIFFERENT WORD. A
    # mutant that breaks an import produces `N errors` and ZERO of both counts
    # above — which read as a clean survival until this line existed.
    nerr = int(m.group(1)) if (m := re.search(r"(\d+) errors?\b", out)) else 0
    msgs = "\n".join(ln for ln in out.splitlines() if ln.startswith("E "))
    return nfail, npass, nerr, killers, msgs


def main() -> int:
    files = sorted({p for p in TARGETS.values()}, key=str)
    orig = {p: p.read_text(encoding="utf-8") for p in files}
    before = {p: _digest(p) for p in files}
    try:
        nf, np_, nerr, _, _ = run_suite()
        print(f"CONTROL (pristine): {np_} passed, {nf} failed, {nerr} errors")
        # 🔴 BOTH halves. A green-looking zero is what a battery wired to
        # nothing reports.
        if nf or nerr or np_ < 200:
            print("ABORT — baseline is red or collected nothing; no verdict "
                  "below would mean anything")
            return 1
        # 🔴 THE FLOOR EVERY MUTANT IS MEASURED AGAINST, derived from what the
        # CONTROL actually collected rather than from a literal that drifts with
        # the suite. Half, not 90%: a legitimate mutant turns passes into
        # failures without changing the total, so the only thing this can catch
        # is a run that lost whole FILES — which is what an unimportable module
        # does. See `classify`.
        floor = np_ // 2
        print(f"observation floor for each mutant: {floor} tests must RUN")

        problems: list[str] = []
        for row in MUTANTS:
            mid, shape, desc, old, new = row[:5]
            expected = row[5] if len(row) > 5 else None
            target = TARGETS[mid]
            text = orig[target]
            # One edit or several: a tuple means every pair is applied in order,
            # and EACH is still required to occur exactly once. A multi-site
            # mutant whose second half silently did not apply would be scored on
            # the first half alone — a DIFFERENT mutation than the row names,
            # reported under the row's id.
            pairs = list(zip(old, new)) if isinstance(old, tuple) else [(old, new)]
            counts = [text.count(o) for o, _ in pairs]
            if any(n != 1 for n in counts):
                shown_n = counts[0] if len(counts) == 1 else counts
                print(f"{mid:4} {shape:12} !! PATTERN OCCURS {shown_n}x in "
                      f"{target.name} — NOT APPLIED — {desc}")
                problems.append(mid)
                continue
            mutated = text
            for o, nw in pairs:
                mutated = mutated.replace(o, nw)
            target.write_text(mutated, encoding="utf-8")
            try:
                nf, _np, _nerr, killers, msgs = run_suite(
                    messages=expected is not None)
            finally:
                target.write_text(text, encoding="utf-8")
            verdict = classify(nf, _np, _nerr, floor, expected, msgs)
            if verdict in ("SURVIVED", "KILLED-WRONG-REASON", "NOT-OBSERVED"):
                problems.append(mid)
            shown = ", ".join(k[:52] for k in killers[:3])
            extra = f" (+{len(killers) - 3} more)" if len(killers) > 3 else ""
            print(f"{mid:4} {shape:12} {verdict:19} f={nf:<3} p={_np:<5} "
                  f"[{target.name}] {desc}")
            if verdict == "KILLED-WRONG-REASON":
                print(f"     expected {expected!r} in the `E ` lines; not found")
            if verdict == "NOT-OBSERVED":
                print(f"     🔴 the suite did not RUN: {_np} passed + {nf} "
                      f"failed is below the floor of {floor}, or it reported "
                      f"{_nerr} collection error(s). This is NOT a survival — "
                      f"the mutant probably broke an import.")
            if killers:
                print(f"     killers: {shown}{extra}")

        # 🔴 THE POSITIVE CONTROL IS READ AS A VERDICT ON THE BATTERY. A run in
        # which P1 survives observed nothing, whatever the other rows say.
        pc_ok = "P1" not in problems
        print(f"\npositive control P1: {'KILLED — the battery can observe' if pc_ok else 'SURVIVED — THIS BATTERY IS BROKEN'}")
        print(f"{len(MUTANTS) - len(problems)}/{len(MUTANTS)} killed for the "
              f"stated reason; problems: {problems or 'none'}")
        return 0 if (pc_ok and not problems) else 1
    finally:
        for p in files:
            p.write_text(orig[p], encoding="utf-8")
        after = {p: _digest(p) for p in files}
        drifted = [p.name for p in files if before[p] != after[p]]
        print("restore: " + ("OK — " + " ".join(f"{p.name}={before[p]}" for p in files)
                             if not drifted else f"🔴 DRIFTED: {drifted}"))


if __name__ == "__main__":
    sys.exit(main())
