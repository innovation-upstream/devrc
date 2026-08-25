#!/usr/bin/env python3
"""The mutation battery for this skill's decision logic, as DATA plus a runner.

    PYTHONDONTWRITEBYTECODE=1 python3 tests/mutation_sweep.py          # everything
    PYTHONDONTWRITEBYTECODE=1 python3 tests/mutation_sweep.py M17 N7   # a subset
    python3 tests/mutation_sweep.py --list                             # ids and notes

🔴 WHY THIS IS IN THE REPO. Earlier rounds each ran a sweep, reported "N mutants, 0 survived",
and threw the driver away. That number was then unreproducible from the repo — a reviewer had
to take it on trust, and the next round re-invented a list from scratch and re-discovered the
same sites. A blind re-audit of one such "51 mutants, 0 non-killed" spot-checked the delta and
found SEVEN more at the same sites, including one (`M-SCOPE`) that reverted that round's
headline fix. That is not a criticism of the sweep; it is the standing lesson: **a green sweep
is a claim about the mutants somebody imagined**, so the list must be extendable rather than
re-derived. Add to it; do not start a new one.

HOW TO READ A RESULT
  * `KILLED` names the test that failed. 🔴 **Read the name.** A mutant that dies to a
    DIFFERENT guard's assertion is a green you cannot spend — it says nothing about the guard
    you meant to exercise.
  * `SURVIVED` is a hole. Not always in the code: three of one round's four survivors were
    defects in the MUTANT or in a vacuous TEST. Establish which before writing code.
  * `NOT APPLIED` means the pattern no longer matches — the code moved. It is NOT a pass, and
    an unnoticed one reads exactly like one. `--check` fails the run if any mutant is stale.

Each mutant runs against a FRESH copy of the skill (never `cp -a`: it preserves mtimes, and
CPython validates a cached module on whole-second mtime + size, so a same-length edit inside
the same second imports the ORIGINAL bytecode and scores SURVIVED without ever running).
`run_all.py` purges `__pycache__` itself; `PYTHONDONTWRITEBYTECODE=1` is set on the child too.

⚠️ LAYOUT. Upstream keeps the scripts in `<skill>/scripts/` and the suite in `<skill>/test/`;
here the scripts sit at the skill root and the suite in `tests/`. `run_one` below is written
against THIS layout — if you copy a mutant across, copy the paths too.

🔴 N1-N5 (the scan-less announcement and the `"mentions_found" in r` gate that goes with it)
were deliberately ABSENT while this tool always scanned. The `--transcripts` opt-in landed
2026-08-22, which is the restore condition that omission named, and they are back — together
with T1-T9 for the opt-in's own parsing, its scan-less record, its three renderings and its
CALL SITES in `main()`, and U1-U3 for the retracted false-premise repo message.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
REPO = SKILL.parents[1]
RC = "recent-comments.py"
CA = "check-addressed.py"
CC = "check-completion.py"
# Relative to the mutant copy's skill dir — resolves into the `lib/` sibling that run_one
# reproduces beside it.
TS = "../lib/transcript_search.py"

# 🔴 REMOVED FROM EVERY MUTANT COPY, and this is the instrument's own correctness, not tidiness.
# `test_no_real_identifiers.py` is a REPO-HYGIENE gate: it walks `<repo>/scripts/…` and
# `<repo>/claude/skills/…` via `Path(__file__).resolve().parents[3]`, and asserts loudly when a
# scan root is missing — correctly, since a scan that walks nothing is not a clean scan. Inside
# the temp copy those roots do not exist, so all four of its tests fail on EVERY run.
#
# Measured on the first pass of this port: all 59 mutants reported KILLED, and each one's killer
# list opened with `test_the_scan_actually_walks_files_and_can_see_an_id`. That is a
# fully-green sweep in which a mutant changing NOTHING would also have scored KILLED — the exact
# "dying to a different guard's assertion is a green you cannot spend" failure this file's own
# HOW TO READ A RESULT section warns about, hit by the file that warns about it.
#
# No code mutant can ever be caught by this gate (it reads the tree, not the logic), so dropping
# it costs no coverage. `NULL-CONTROL` below is what proves the removal was sufficient.
EXCLUDED_FROM_MUTANT_RUNS = ("test_no_real_identifiers.py",)

# Modules from `scripts/lib/` these scripts import. Copied into the mutant tree beside the
# skill so the relative `../lib` import resolves there exactly as it does in the repo.
SHARED_MODULES = ("transcript_search.py",)

# 🔴 REPO-ROOT-RELATIVE DATA A TEST FILE READS AT IMPORT TIME, copied at the same relative
# path so `Path(__file__).resolve().parents[3] / <path>` resolves in the copy as it does in
# the repo. Measured 2026-08-25 on the merge of this branch with main: without the entry
# below, `test_awaiting_contract.py` raised at import (it reads its shared contract table
# from `claude/skills/clickup/test/`), the whole file scored FAILED TO IMPORT, and the
# NULL-CONTROL ABORTED the sweep — the "green for the wrong reason" failure this file's own
# EXCLUDED_FROM_MUTANT_RUNS block was written about, recurring in a new shape. Copying is
# the right fix rather than excluding the file: the fixture is DATA the mutants can be
# scored against, so excluding it would drop real coverage.
REPO_FIXTURES = ("claude/skills/clickup/test/awaiting-contract.fixtures.json",)

# (id, file, old, new, note). `old` must occur EXACTLY ONCE or the mutant is NOT APPLIED.
MUTANTS = [
    # ---------- round 7 change 1: the UNIDENTIFIED sentinel on an unreadable own date
    ("M1", RC, "        if ts is None:\n            return UNIDENTIFIED",
     "        if ts is None:\n            continue", "revert to round 6's `continue`"),
    ("M2", RC, "        if best is None or ts > best:", "        if best is None or ts < best:",
     "rank my OLDEST comment as newest"),
    ("M3", RC, "        if best is None or ts > best:", "        if best is None:",
     "first-wins instead of newest-wins"),
    ("M4", RC, "    if ts is None or ts is UNIDENTIFIED:\n        return ts\n    return format_date(ts)",
     "    return format_date(ts)", "format the sentinel into a date-looking string"),
    ("M5", RC, "    if ts is None or ts is UNIDENTIFIED:", "    if ts is None:",
     "pass None through but format the sentinel"),
    ("M14", RC, "    except (ValueError, TypeError):\n        return None\n\n\ndef latest_reply_ts_by",
     "    except (ValueError, TypeError):\n        return 0\n\n\ndef latest_reply_ts_by",
     "unreadable epoch-ms becomes 1970 instead of absent"),

    # ---------- round 7 change 2, producer side
    ("M6", RC, '    if date_ms is not None:\n        rec["date_ms"] = date_ms',
     '    if date_ms is not None:\n        pass', "drop the comment's raw ms"),
    ("M7", RC, '    if date_ms is not None:\n        rec["date_ms"] = date_ms',
     '    if True:\n        rec["date_ms"] = date_ms', "emit an absent ms as None"),
    ("M8", RC, '    date_ms = _epoch_ms(comment.get("date", ""))',
     '    date_ms = _epoch_ms(comment.get("date", "")) or 0', "emit an unreadable ms as 0"),
    ("M9", RC, '        if isinstance(my_latest_reply_ms, int) and not isinstance(my_latest_reply_ms, bool):\n            rec["my_latest_reply_ms"] = my_latest_reply_ms',
     '        if isinstance(my_latest_reply_ms, int) and not isinstance(my_latest_reply_ms, bool):\n            pass',
     "drop my reply's raw ms"),
    ("M10", RC, "        if isinstance(my_latest_reply_ms, int) and not isinstance(my_latest_reply_ms, bool):",
     "        if my_latest_reply_ms is not None:", "accept a non-int ms"),
    ("M11", RC, '        if isinstance(my_latest_reply_ms, int) and not isinstance(my_latest_reply_ms, bool):\n            rec["my_latest_reply_ms"] = my_latest_reply_ms\n    return rec',
     '        pass\n    if isinstance(my_latest_reply_ms, int) and not isinstance(my_latest_reply_ms, bool):\n        rec["my_latest_reply_ms"] = my_latest_reply_ms\n    return rec',
     "un-nest: an ms without the formatted date it refines"),
    ("M12", RC, "            my_reply_ts = latest_reply_ts_by(comments, my_id)",
     "            my_reply_ts = latest_reply_ts_by([], my_id)",
     "compute the reply over an empty list (round 6's inert-fix shape)"),
    ("M13", RC, "                build_record(tid, tname, task, c, text, my_reply, my_reply_ts))",
     "                build_record(tid, tname, task, c, text, my_reply, None))",
     "drop the ms at the _collect join"),

    # ---------- round 7 change 2, consumer side
    ("M15", CA, "    if isinstance(value, bool) or not isinstance(value, int):",
     "    if not isinstance(value, int):", "`_ms` accepts a bool: a JSON `true` compares as 1ms"),
    ("M17", CA, "        return mine_ms >= theirs_ms", "        return mine_ms > theirs_ms",
     "a same-millisecond tie is no longer an answer"),
    ("M18", CA, "        return mine_ms >= theirs_ms", "        return mine_ms <= theirs_ms",
     "invert the ms comparison"),
    ("M19", CA, "        return mine_ms >= theirs_ms", "        return True",
     "any reported reply answers, at ms resolution"),
    ("M20", CA, "    if mine_ms is not None and theirs_ms is not None:",
     "    if mine_ms is not None or theirs_ms is not None:",
     "compare a raw instant against a rounded one"),
    ("M21", CA, "    if mine_ms is not None and theirs_ms is not None:\n        return mine_ms >= theirs_ms",
     "    if False:\n        return mine_ms >= theirs_ms", "delete the ms branch"),
    ("M22", CA, "    if mine is None or theirs is None:\n        return None\n    # Smaller age = more recent.\n    return mine <= theirs",
     "    return None", "delete the age fallback"),
    ("M23", CA, "    return mine <= theirs", "    return mine < theirs",
     "round 6's own minute boundary, flipped"),

    # ---------- round 7 change 3: the suppression note
    ("M24", CA, "            and _reply_answers_the_comment(nc, now) is True):",
     "            and _reply_answers_the_comment(nc, now) is not None):",
     "undecidable counts as answered"),
    ("M25", CA, "    if (not reply_unreported and bound_age is not None",
     "    if (bound_age is not None", "drop the reported-vs-absent guard on the answered branch"),
    ("M26", CA, "        return ANSWERED, (", "        return None, None or (",
     "suppress silently again"),
    ("M27", CA, "            f\"older, so the WAITING flag is SUPPRESSED. If that reply was an agent's, @{who} \"\n            f\"is still waiting for a human — read the thread before treating this as handled.\")",
     "            f\"older, so the WAITING flag is SUPPRESSED.\")",
     "drop the caveat's per-line consequence"),
    ("M28", CA, "        if kind != ANSWERED:\n            continue", "        if kind != WAITING:\n            continue",
     "wrong bind in suppressed_notes"),
    ("M29", CA, "    now = now or datetime.now(timezone.utc)\n    out = []\n    for r in results:\n        kind, line = _waiting_verdict(r, now)\n        if kind != ANSWERED:",
     "    now = now or datetime.now(timezone.utc)\n    out = []\n    for r in []:\n        kind, line = _waiting_verdict(r, now)\n        if kind != ANSWERED:",
     "suppressed_notes over an empty list"),
    ("M30", CA, "    kind, line = _waiting_verdict(r, now)\n    return line if kind == WAITING else None",
     "    kind, line = _waiting_verdict(r, now)\n    return line", "notes leak into Needs a decision"),
    ("M31", CA, "    return WAITING, head", "    return ANSWERED, head",
     "route the waiting flag into the notes block"),
    ("M32", CA, "    notes = suppressed_notes(results, now)\n    if notes:",
     "    notes = suppressed_notes(results, now)\n    if False:", "drop the notes block"),
    ("M33", CA, '    for key in ("date_ms", "my_latest_reply_ms"):', '    for key in ("date_ms",):',
     "hand-off drops my reply's ms"),
    ("M34", CA, '    for key in ("date_ms", "my_latest_reply_ms"):', '    for key in ("my_latest_reply_ms",):',
     "hand-off drops the comment's ms"),
    ("M35", CA, "    for key in (\"date_ms\", \"my_latest_reply_ms\"):\n        if key in meta:\n            nc[key] = meta[key]",
     "    for key in (\"date_ms\", \"my_latest_reply_ms\"):\n        nc[key] = meta.get(key)",
     "hand-off flattens absent into null"),
    ("M36", CA, "MOVE_THE_RECENCY_BOUND", "", "move the recency bound AFTER the answered branch"),
    ("M37", CA, "        blocks.append((DECISION_HEADING, [f\"⚠️  {f}\" for f in flags]))",
     "        blocks.insert(99, (DECISION_HEADING, [f\"⚠️  {f}\" for f in flags]))\n"
     "    notes = suppressed_notes(results, now)\n"
     "    if notes:\n"
     "        blocks.insert(0, (ANSWERED_HEADING, [f\"🔴 {BOT_IDENTITY_CAVEAT}\"] + [f\"ℹ️  {n}\" for n in notes]))",
     "print the no-action block first"),
    ("M38", CA, "        waiting = _waiting_on_a_human(r, now)\n        if waiting:",
     "        waiting = _waiting_on_a_human(r, now)\n        if False:", "drop the waiting flag"),
    ("M39", CA, "        seen = f\"{age:.0f}d ago\" if age is not None else f\"at {nc.get('date')!r} (unreadable)\"",
     "        seen = f\"{age:.0f}d ago\"", "assume the note's age is always readable"),
    ("M40", CA, "    if r.get(\"mentions_found\") != 0:\n        return None, None",
     "    if r.get(\"mentions_found\", 0) != 0:\n        return None, None",
     "absent mention count read as a reported zero"),

    # ---------- round 7b: the audit fixes
    #
    # ⚠️ MEASURED WHILE PORTING, and worth knowing before you move a test: **M45 and M47 are
    # killed by NOTHING in `test_own_reply_answers.py`.** Running each of the three files
    # alone against those two mutants: the round-7 hand-written file goes 0 red, the corpus
    # goes 4 red, and `test_bounds_and_parsing.py` goes 1 and 2 red respectively. Both mutants
    # disable the `bound_age` fallback to the raw ms, and the only fixtures that combine an
    # unreadable DISPLAY date with a usable `date_ms` live in the corpus and the round-8 file.
    # All three ship together, so coverage is real — but delete either of those files and
    # these two mutants survive a suite that still looks comprehensive.
    ("M45", CA, "    bound_age = age if age is not None else _age_days_from_ms(_ms(nc.get(\"date_ms\")), now)",
     "    bound_age = age", "recency bound dies when the display date is unreadable"),
    ("M46", CA, "    except (ValueError, TypeError, OSError, OverflowError):\n        return None\n    return (now - dt).total_seconds() / 86400.0",
     "    except (ValueError, TypeError, OSError, OverflowError):\n        return 0\n    return (now - dt).total_seconds() / 86400.0",
     "an unusable raw ms reads as age 0 (always fresh)"),
    ("M47", CA, "    if ms is None:\n        return None\n    try:\n        dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)",
     "    if True:\n        return None\n    try:\n        dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)",
     "the ms-based bound never answers"),
    ("M48", CA, "        if others:\n            line +=", "        if not others:\n            line +=",
     "invert the other-coverage branch"),
    # ⚠️ M49 and M-SCOPE were re-anchored 2026-08-23: the run-level-announcement filter
    # rewrote this line, and BOTH went `NOT APPLIED` — which `--check` caught and failed on,
    # exactly as its docstring promises. M-SCOPE is round 8's headline mutant; a silent
    # NOT APPLIED there would have read like every other green row.
    ("M49", CA, "        others = [f for f in disagreements([r], now) if not f.startswith(SCANLESS_LEAD)]",
     "        others = []", "always claim the report is silent about the ticket"),
    ("M50", CA, "                       [f\"🔴 {BOT_IDENTITY_CAVEAT}\"] + [f\"ℹ️  {n}\" for n in notes]))",
     "                       [f\"ℹ️  {n}\" for n in notes]))", "drop the caveat preamble"),
    ("M51", CA, "                       [f\"🔴 {BOT_IDENTITY_CAVEAT}\"] + [f\"ℹ️  {n}\" for n in notes]))",
     "                       [f\"ℹ️  {n}\" for n in notes] + [f\"🔴 {BOT_IDENTITY_CAVEAT}\"]))",
     "the caveat trails the block instead of leading it"),
    ("M52", CA, "        for heading, lines in report_blocks(results):\n            print(f\"\\n{heading}\\n\")\n            for line in lines:\n                print(line)",
     "        pass", "delete main()'s entire print loop"),
    ("M53", CA, "        for heading, lines in report_blocks(results):",
     "        for heading, lines in report_blocks(results)[:1]:", "print only the first block"),
    ("M54", CA, "    flags = disagreements(results, now)", "    flags = disagreements(results)",
     "report_blocks drops `now` on the flags producer"),
    ("M55", CA, "    notes = suppressed_notes(results, now)", "    notes = suppressed_notes(results)",
     "report_blocks drops `now` on the notes producer"),
    ("M56", CA, "    if (not reply_unreported and bound_age is not None\n            and _reply_answers_the_comment(nc, now) is True):",
     "    if (not reply_unreported\n            and _reply_answers_the_comment(nc, now) is True):",
     "emit a note whose recency bound could not be evaluated at all"),

    # ---------- round 8: the invariants nothing pinned. M-SCOPE is the one a blind re-audit
    # found surviving a green sweep — it reverts round 7b's headline fix, and the guard
    # written for that fix could not see it because every fixture passed a single-record list.
    ("M-SCOPE", CA, "if not f.startswith(SCANLESS_LEAD)]\n        if others:",
     "if not f.startswith(SCANLESS_LEAD)]\n        others = disagreements(results, now)\n        if others:",
     "the other-coverage sentence stops being scoped to its own ticket"),
    ("N7", CA, "        seen = f\"{age:.0f}d ago\" if age is not None else f\"at {nc.get('date')!r} (unreadable)\"",
     "        seen = f\"{bound_age:.0f}d ago\" if bound_age is not None else f\"at {nc.get('date')!r} (unreadable)\"",
     "the NOTE displays an age derived from the raw ms"),
    ("N8", CA, "    if age is None:\n        head += (f\" (comment date {nc.get('date')!r} was unreadable, so its age is unknown \"",
     "    if bound_age is None:\n        head += (f\" (comment date {nc.get('date')!r} was unreadable, so its age is unknown \"",
     "the FLAG head displays an age derived from the raw ms"),
    ("N9", CA, "    if bound_age is not None and bound_age > UNANSWERED_COMMENT_DAYS:",
     "    if bound_age is not None and bound_age >= UNANSWERED_COMMENT_DAYS:",
     "the recency bound becomes inclusive"),
    ("N10", CA, "UNANSWERED_COMMENT_DAYS = 14", "UNANSWERED_COMMENT_DAYS = 28",
     "silently double the recency window"),
    ("N11", CA, "    bound_age = age if age is not None else _age_days_from_ms(_ms(nc.get(\"date_ms\")), now)",
     "    bound_age = age if age is not None else _age_days_from_ms(nc.get(\"date_ms\"), now)",
     "drop the `_ms` type gate on the bound fallback"),
    ("N12", CA, "    except (ValueError, TypeError, OSError, OverflowError):",
     "    except (ValueError, OSError, OverflowError):",
     "a string raw-ms raises an uncaught TypeError"),

    # ---------- round 7/8: the transcript opt-in, its scan-less record, and the announcement
    # + state gate that only became reachable once the opt-in existed. N1-N5 are upstream's,
    # restored per the condition their omission named; T1-T9 are this port's own, and half of
    # them target CALL SITES rather than function bodies, because a correct scan-less record
    # that `main()` never builds is inert with a fully green suite — this skill's own headline
    # defect class.
    ("N1", CA, "    if any(r.get(\"status\") == \"not_scanned\" for r in results):",
     "    if False:", "delete the scan-less announcement"),
    ("N2", CA, "    if any(r.get(\"status\") == \"not_scanned\" for r in results):",
     "    if any(\"mentions_found\" not in r for r in results):",
     "announce from a RECORD-level absence instead of the run's own declaration"),
    ("N3", CA, "    if any(r.get(\"status\") == \"not_scanned\" for r in results):",
     "    for _r in results:\n     if _r.get(\"status\") == \"not_scanned\":",
     "announce once per TICKET instead of once per run"),
    ("N4", CA, "        if (\"mentions_found\" in r and r[\"status\"] in (\"open\", \"no_mentions_found\")",
     "        if ((True) and r[\"status\"] in (\"open\", \"no_mentions_found\")",
     "open-signals flag stops requiring a SEARCHED zero"),
    ("N5", CA, "                \"status\": \"not_scanned\",", "                \"status\": \"no_mentions_found\",",
     "rename the scan-less sentinel (SURVIVED the round-7c suite upstream)"),
    ("T1", CA, '        "transcripts": False,', '        "transcripts": True,',
     "the scan is on by default again"),
    ("T2", CA, "        elif a == \"--transcripts\":\n            opts[\"transcripts\"] = True; i += 1",
     "        elif a == \"--transcripts\":\n            opts[\"transcripts\"] = False; i += 1",
     "`--transcripts` parses to a no-op — the opt-in cannot be taken"),
    ("T3", CA, "        elif a == \"--no-transcripts\":\n            opts[\"transcripts\"] = False; i += 1",
     "        elif a == \"--no-transcripts\":\n            opts[\"transcripts\"] = True; i += 1",
     "`--no-transcripts` turns the scan ON"),
    ("T4", CA, "    transcripts = opts[\"transcripts\"]", "    transcripts = False",
     "CALL SITE: main() ignores the parsed flag and never scans"),
    ("T5", CA, "        if not transcripts:", "        if False:",
     "CALL SITE: main() always scans, whatever the flag said"),
    ("T6", CA, "                \"status\": \"not_scanned\",\n                \"completion\": [], \"open\": [], \"sessions\": [],",
     "                \"status\": \"not_scanned\", \"mentions_found\": 0,\n                \"completion\": [], \"open\": [], \"sessions\": [],",
     "the scan-less record emits an UNSEARCHED zero as a searched one"),
    ("T7", CA, "        if transcripts:\n            print(f\"{MARKER_LEGEND}\\n\")",
     "        if True:\n            print(f\"{MARKER_LEGEND}\\n\")",
     "the evidence-marker legend heads a report with no evidence"),
    ("T8", CA, "        if transcripts:\n            print(f\"## Summary: {addressed} addressed",
     "        if True:\n            print(f\"## Summary: {addressed} addressed",
     "a tally of verdicts nobody computed — four zeroes that read as a finding"),
    ("T9", CA, "            if r[\"status\"] == \"not_scanned\":", "            if False:",
     "a scan-less record prints a searched-sessions/mentions count"),

    # ---------- the scan-less record's CONTENT, and the announcement's LEDGER. Z1-Z3 are the
    # three that survived a 220-green suite before `attach_clickup_meta` collapsed the two
    # record writers into one: T4-T9 cover whether the branch runs and what its counts say,
    # and nothing covered what the record CARRIES. Z1 alone deletes the highest-value line in
    # the report on the default path. They stay in the list even though the duplicate writer
    # is gone — a mutant that can no longer be expressed at two sites is still worth pinning
    # at the one.
    ("Z1", CA, "    record[\"newest_comment\"] = build_newest_comment(meta)",
     "    record[\"newest_comment\"] = {}",
     "the record carries no newest comment: the RESOLVED-on-open flag silently disappears"),
    ("Z2", CA, "    record[\"clickup_status\"] = meta.get(\"task_status\")",
     "    record[\"clickup_status\"] = None",
     "the record carries no ClickUp status: every status-gated rule goes to 'unknown'"),
    ("Z3", CA, "            }, meta))", "            }, {}))",
     "CALL SITE: the scan-less builder is handed an empty meta"),
    ("Z4", CA, "    named = \"; \".join(f\"'{name}'\" for name, _tail in SCAN_ONLY_RULES)",
     "    named = \"; \".join(f\"'{name}'\" for name, _tail in SCAN_ONLY_RULES[:2])",
     "the announcement names a SUBSET again — the defect the ledger replaced"),
    ("Z5", CA, "        others = [f for f in disagreements([r], now) if not f.startswith(SCANLESS_LEAD)]",
     "        others = disagreements([r], now)",
     "the RUN-level announcement counts as a per-TICKET flag in the note"),
    # 🔴 The LEDGER shrinking, which is the half a ledger-derived assertion cannot see —
    # both of the obvious checks are computed FROM `SCAN_ONLY_RULES`, so dropping an entry
    # keeps them consistent with each other and silently stops announcing a rule that still
    # goes quiet. Only the flags-that-actually-disappeared diff catches this.
    ("Z6", CA, "    (\"a PR the transcripts cited is still open\", \"the signal that quoted it reads as done\"),\n)",
     ")", "drop a rule from the ledger: it goes quiet and nothing announces it"),

    # ---------- round 7: the retracted false-premise message in `_unresolved_state`. Round 5
    # replaced one vague failure with three precise ones; measured on live input the precise
    # version was wrong MORE often, because the token on the '#' is whatever word precedes it.
    ("U1", CC, "    return (\"unresolved (could not determine the repo; if one is named here, add it to \"",
     "    if kind == \"unknown\":\n        return f\"unresolved (repo {detail!r} is not in KNOWN_REPOS)\"\n"
     "    return (\"unresolved (could not determine the repo; if one is named here, add it to \"",
     "reinstate the false premise: quote the captured token back as a repo"),
    ("U2", CC, "            \"KNOWN_REPOS in check-completion.py)\")",
     "            \"the repo table in check-completion.py)\")",
     "drop the affordance — the reader is told nothing to do"),
    ("U3", CC, "    if kind == \"ambiguous\":", "    if False:",
     "collapse the one failure whose diagnosis is TRUE into the generic message"),

    # ---------- the shared transcript walk (scripts/lib/transcript_search.py, 2026-08-24)
    # These prove this suite can still SEE a break in code that no longer lives in this
    # directory. Consolidating the walk out of `search-sessions.py` and `check-completion.py`
    # would otherwise have moved it out from under every mutant here — coverage lost with a
    # fully green sweep to show for it.
    # ⚠ This targets `is_corpus_member`, NOT `iter_transcripts`. An earlier spelling aimed at
    # the walk's own body and, once the predicate was factored out, scored NOT APPLIED — a
    # silent coverage hole that reads like a row rather than an absence. Re-check the anchor
    # when transcript_search.py moves.
    ("TS1", TS, "    if any(p in EXCLUDED_DIR_NAMES for p in parents):\n        return False\n",
     "", "walk the subagents/ tier — 4,776 transcripts that are not sessions"),
    ("TS2", TS, "            except Exception:\n                continue\n",
     "            except Exception:\n                raise\n",
     "let one malformed JSONL line abort the read again"),

    # ---------- POSITIVE CONTROL. A mutant round 6 proved is caught; if this ever reports
    # SURVIVED the harness is broken, not the code.
    ("PC", CA, '    reply_unreported = "my_latest_reply" not in nc', "    reply_unreported = False",
     "POSITIVE CONTROL — flatten absent vs reported"),
]

# M36 is a MOVE, not a substitution, so it is applied by hand below rather than by pattern.
# The tuple above carries a sentinel `old` that never matches; `run` special-cases the id.
_BOUND = ("    bound_age = age if age is not None else _age_days_from_ms(_ms(nc.get(\"date_ms\")), now)\n"
          "    if bound_age is not None and bound_age > UNANSWERED_COMMENT_DAYS:\n"
          "        return None, None\n")
_AFTER_NOTE = ("            f\"is still waiting for a human — read the thread before treating "
               "this as handled.\")\n")


def _apply(body, mid, old, new):
    """Return the mutated source, or None if the mutant no longer applies."""
    if mid == "NULL-CONTROL":
        return body
    if mid == "M36":
        if body.count(_BOUND) != 1 or body.count(_AFTER_NOTE) != 1:
            return None
        body = body.replace(_BOUND, "")
        return body.replace(_AFTER_NOTE, _AFTER_NOTE + "\n" + _BOUND)
    if body.count(old) != 1:
        return None
    return body.replace(old, new)


def materialize(root):
    """Build a runnable copy of the skill under `root`, and return its skill dir.

    🔴 THE MUTANT COPY REPRODUCES THE REPO's `scripts/` LAYOUT, not just this directory.
    The scripts import `scripts/lib/transcript_search.py` — the single transcript-corpus
    walker they share with `scripts/find-session.py` — as `_HERE.parent / "lib"`, and
    `tests/test_awaiting_contract.py` reads a shared contract table via
    `parents[3] / "claude/skills/..."`. Copying only `SKILL` into a bare temp dir leaves
    both unresolvable, every test file scores FAILED TO IMPORT, and the NULL-CONTROL
    aborts the sweep. Nesting the copy under `<root>/scripts/` keeps ONE path rule — the
    real repo's — true in the copy too, rather than teaching the code a second candidate
    path that only ever fires inside a test harness.

    A FUNCTION so the property is testable without running a sweep: the ledger test in
    `tests/test_shared_walk.py` calls exactly this and then imports every test module.
    """
    root = Path(root)
    dst = root / "scripts" / SKILL.name
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(SKILL, dst, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    shared = dst.parent / "lib"
    shared.mkdir(parents=True, exist_ok=True)
    for mod in SHARED_MODULES:
        shutil.copy2(SKILL.parent / "lib" / mod, shared / mod)
    for rel in REPO_FIXTURES:              # `root` IS the copy's repo root — parents[3]
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO / rel, target)
    return dst


def run_one(mid, fname, old, new, note, workdir):
    dst = materialize(Path(workdir) / mid)
    for name in EXCLUDED_FROM_MUTANT_RUNS:
        (dst / "tests" / name).unlink(missing_ok=True)
    target = dst / fname
    mutated = _apply(target.read_text(), mid, old, new)
    if mutated is None:
        return "NOT APPLIED", []
    target.write_text(mutated)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    proc = subprocess.run([sys.executable, str(dst / "tests" / "run_all.py")],
                          capture_output=True, text=True, env=env)
    killers = re.findall(r"^  ✗ (\S+?):", proc.stdout, re.M)
    total = re.search(r"Total: (\d+) passed, (\d+) failed", proc.stdout)
    if not total:
        # No verdict line at all is a DEAD RUN, never a pass — the runner's own docstring
        # says so, and an absent line reads as empty output through any filter.
        return "NO VERDICT", killers
    return ("KILLED" if int(total.group(2)) else "SURVIVED"), killers


def _assert_ids_unique():
    """🔴 A DUPLICATE MUTANT ID IS A SILENT HARNESS BREAK, and it happened once.

    `run_one` builds its work directory as `<workdir>/<id>`, and `--check`/selection match
    on the id — so two rows sharing one id collide in the same temp tree and a selection
    naming it picks up both, while `len(selected) != len(set(wanted))` rejects the run with
    a bare "unknown mutant id". Adding TS1/TS2 as `T1`/`T2` produced exactly that. Fail
    with the offending id instead.
    """
    seen, dupes = set(), []
    for row in MUTANTS:
        if row[0] in seen:
            dupes.append(row[0])
        seen.add(row[0])
    if dupes:
        raise SystemExit(f"duplicate mutant id(s) in MUTANTS: {sorted(set(dupes))}")


def main(argv):
    _assert_ids_unique()
    if "--list" in argv:
        for mid, fname, _o, _n, note in MUTANTS:
            print(f"{mid:8s} {fname:20s} {note}")
        return 0
    strict = "--check" in argv
    wanted = [a for a in argv if not a.startswith("--")]
    selected = [m for m in MUTANTS if not wanted or m[0] in wanted]
    if wanted and len(selected) != len(set(wanted)):
        print(f"unknown mutant id in {wanted}", file=sys.stderr)
        return 2

    bad = []
    with tempfile.TemporaryDirectory(prefix="ccua-sweep-") as workdir:
        # 🔴 NEGATIVE CONTROL ON THE HARNESS, run FIRST and fatal. It copies the skill and
        # mutates NOTHING, so the only honest verdict is SURVIVED. A `KILLED` here means the
        # suite is red inside the temp copy for a reason that has nothing to do with any
        # mutant — and then every row below reads KILLED for free, which is a fully green
        # sweep that proves nothing. That is not hypothetical: it is what the first run of
        # this file did, because a repo-hygiene gate cannot resolve the repo from a copy.
        # See EXCLUDED_FROM_MUTANT_RUNS.
        verdict, killers = run_one("NULL-CONTROL", CA, "", "", "no mutation", workdir)
        print(f"{'NULL-CTL':8s} {verdict:11s} no mutation — MUST be SURVIVED")
        if verdict != "SURVIVED":
            for k in killers[:5]:
                print(f"          red without any mutation: {k}")
            print("\nABORTED: the suite is not green inside a fresh copy, so every mutant "
                  "below would score KILLED whatever it did. Fix the harness, not the code.",
                  file=sys.stderr)
            return 2

        for mid, fname, old, new, note in selected:
            verdict, killers = run_one(mid, fname, old, new, note, workdir)
            if verdict != "KILLED":
                bad.append((mid, verdict))
            print(f"{mid:8s} {verdict:11s} {note}")
            for k in killers[:3]:
                print(f"          killed by: {k}")
            if len(killers) > 3:
                print(f"          ... and {len(killers) - 3} more")
            sys.stdout.flush()

    print(f"\n{len(selected)} mutant(s); non-KILLED: {len(bad)}")
    for mid, verdict in bad:
        print(f"  {mid}: {verdict}")
    # `NOT APPLIED` fails under --check because a stale mutant is not a pass: the code moved
    # out from under it, and nobody notices a verdict that looks like every other one.
    return 1 if (bad and strict) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
