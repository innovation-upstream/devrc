#!/usr/bin/env python3
"""Reduce a `session-manager scan --json` report to the ONE fact drift-check
needs: how many rows still take their `age_secs` from fuzzyclaw ALONE.

WHY THIS IS THE FACT
--------------------
The fuzzyclaw readers are being removed in phases (`session-manager`,
`tmux-claude-counters.sh`, `verify-agent-work`,
`validation/{reconcile,refsources}.py` are what remain). Phase 2 — deleting the
readers — is safe exactly when no row depends on fuzzyclaw for a fact the agent
ledger cannot supply. `age_source` names the writer that actually answered, so
`summary.age_sources["fuzzyclaw"]` IS that count: those rows are pre-deploy
sessions the ledger has no record of, and the number decays as they restart.

Measured on the workbench 2026-08-15: 7 fuzzyclaw-sourced of 47 rows examined
(local host only). The same 7 appears in a two-host scan of 75 rows, because
fuzzyclaw task files are LOCAL state — `gather()` passes `task_index` only for
`host == local_host`, so a remote row structurally cannot carry a fuzzyclaw
age. That is why drift-check scans the local host alone: same numerator, no ssh.

WHY A SEPARATE FILE AND NOT A `python3 -c` STRING IN drift-check.sh
------------------------------------------------------------------
🔴 `scripts/tests/test_drift_check.py` walks every line of drift-check.sh with a
shell tokenizer and demands that each word in COMMAND POSITION be accounted for
in `UNIT_PATH_REQUIREMENTS`. Python source pasted into that file sprays dozens of
false command words at it (`_walk` splits on `(` and `{`, so `summ = rep.get(
"summary") or {}` presents `summ` as a command). Widening the prose ledger to
absorb them would blunt the guard that found `dirname` and `bash`. Kept out of
the bash file, drift-check.sh gains exactly one new command word: `python3`.

READ-ONLY BY CONSTRUCTION: stdin in, one line out. It opens no file, imports
nothing but `json`/`sys`, and cannot reach the network. Pinned by
`test_the_phase2_reader_is_read_only`.

OUTPUT CONTRACT — one line, three space-separated fields:

    <token> <rows_examined> <fuzzyclaw_only>

`<token>` is `ok` when the two counts are a real measurement, and otherwise a
reason token explaining why they are not. The counts are `-1` when unknown.
🔴 The caller must branch on the TOKEN, never on the counts alone: every failure
mode here would otherwise present as `0 0`, which is indistinguishable from a
clean host and would read as "phase 2 is ready".
"""
import json
import sys


def emit(token, rows=-1, fuzzy=-1):
    print(token, rows, fuzzy)
    raise SystemExit(0)


def main(argv):
    want = argv[1] if len(argv) > 1 else ""
    try:
        rep = json.load(sys.stdin)
    except Exception as exc:  # noqa: BLE001 — any parse/IO failure is one fact
        emit("no-json:%s" % type(exc).__name__)
    if not isinstance(rep, dict):
        emit("no-json:not-an-object")

    summ = rep.get("summary") or {}
    rows = summ.get("total_sessions")
    fuzzy = (summ.get("age_sources") or {}).get("fuzzyclaw", 0)
    # `bool` is an `int` in Python; a True here would print as a count.
    if not isinstance(rows, int) or isinstance(rows, bool):
        emit("no-counts")
    if not isinstance(fuzzy, int) or isinstance(fuzzy, bool):
        emit("no-counts")

    # 🔴 THE HOST GUARD, and it is not paranoia. `session-manager` decides which
    # host is LOCAL from `ACTIVITY_HOST` / the collector env file, while
    # drift-check decides from `lib/host-role.sh` and the machine IPs. Two
    # predicates, two sources. If they disagree, `--host <role>` names the
    # REMOTE machine and the scan ssh's there — and a remote row can never carry
    # a fuzzyclaw age, so the count comes back 0 and the gate declares phase 2
    # READY off a scan of the wrong machine. Surfaced as a reason token instead.
    #
    # Checked AFTER the counts are extracted, deliberately: the numbers exist
    # and are real — they are just about the wrong machine — so they ride back
    # with the reason and the caller can print the finding in full.
    got = rep.get("local_host")
    if want and got != want:
        emit("host-mismatch:%s" % (got,), rows, fuzzy)

    # 🔴 THE POSITIVE-CONTROL GUARD, IN TWO PARTS, because `status == "ok"` is
    # NOT sufficient and I proved it by accident. fuzzyclaw is OPT-IN
    # (`--fuzzyclaw`), so with the flag off no row can have a fuzzyclaw age —
    # a guaranteed 0 from a reader wired to nothing, which is exactly the "phase
    # 2 is ready" answer. That is the `status` half.
    #
    # The second half: a scan whose `$HOME` has no `~/.tmux/tasks` at all
    # reports `status: "ok"` with `files_seen: 0` — an empty directory is a
    # legitimate successful read. MEASURED: the drift-check suite runs with a
    # fixture HOME, and the first version of this gate declared "READY — 0 of 48
    # rows" off it, on a machine where the true count was 7. Zero task files
    # means fuzzyclaw supplied nothing to ANY row, so the zero is structural
    # rather than a measurement of readiness.
    #
    # Both counts are reported ALONGSIDE the reason, so the finding stays
    # legible without being acted on.
    fz = rep.get("fuzzyclaw") or {}
    status = fz.get("status")
    if status != "ok":
        emit("fuzzyclaw-%s" % (status,), rows, fuzzy)
    seen = fz.get("files_seen")
    if not isinstance(seen, int) or isinstance(seen, bool) or seen <= 0:
        emit("fuzzyclaw-no-task-files", rows, fuzzy)

    emit("ok", rows, fuzzy)


if __name__ == "__main__":
    main(sys.argv)
