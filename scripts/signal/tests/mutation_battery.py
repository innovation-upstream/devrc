#!/usr/bin/env python3
"""The Signal suite's mutation battery — the mutants, and a runner for them.

    python3 scripts/signal/tests/mutation_battery.py            # run them all
    python3 scripts/signal/tests/mutation_battery.py --list      # show the ledger
    python3 scripts/signal/tests/mutation_battery.py --only A1 M4

Exit codes: 0 every mutant killed by its named test · 1 at least one SURVIVED,
ANCHOR-MISSed or was KILLED-WRONG-REASON · 2 refused to start (dirty tree, an
unreadable `git status`, a red baseline, a named killer that did not run) ·
3 a mutant was left in the tree, or that could not be determined.

🔴 WHY THIS FILE EXISTS AT ALL. Eight mutation batteries have been run against
this module across #514, #537, #540, #546 and #573. Every one of them lived in a
scratchpad directory that no longer exists. The batteries were the most expensive
artefact produced in those sessions and the only one not kept: each encodes a
specific way this code can be broken *without any test noticing*, which is
knowledge that does not survive in anyone's head and cannot be re-derived by
reading the code — it was found by breaking the code and watching what stayed
green.

🔴 WHAT A GREEN RUN HERE DOES AND DOES NOT MEAN. It means: every mutant BELOW is
killed by the test NAMED beside it. It does NOT mean the suite is adequate — a
battery only ever covers the failure modes whoever wrote it imagined. The
strongest evidence in this file is the four mutants marked `[audit]`: they were
found by an INDEPENDENTLY-CONSTRUCTED battery during a pre-merge audit, and every
one of them SURVIVED the battery its author had just called complete. So when you
extend this: vary how the battery is BUILT, not just how many mutants it holds.

Two disciplines this runner enforces mechanically, because both have produced
confident wrong answers here before:

  * **A kill must be BY THE NAMED TEST.** "Some test failed" is not a kill: a
    different guard's error is green for the wrong reason and stays green with
    the guard under test deleted. A mutant killed by an unexpected test is
    reported `KILLED-WRONG-REASON`, which is a finding, not a pass.
  * **`PYTHONDONTWRITEBYTECODE=1`, always.** CPython validates a cached module on
    source mtime-in-whole-SECONDS plus size, so a same-LENGTH edit landing in the
    same second as the last import is invisible: the test imports the ORIGINAL
    bytecode and the mutant is scored SURVIVED without ever having executed.

And one it enforces for safety: it edits files in the working tree and restores
them from a byte copy, so it REFUSES TO RUN ON A DIRTY TREE. A crash mid-run
against uncommitted work would destroy it, and this repo is a shared checkout
where the dirty files usually belong to somebody else.
"""
from __future__ import annotations

import argparse
import os
import shutil
import signal as _signal
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
DB = "scripts/signal/_signal_db.py"
CON = "scripts/signal/consumer.py"
BP = "scripts/signal/build-push.sh"

SUITE_EXCL = "scripts/signal/tests/test_group_exclusions.py"
SUITE_IMAGE = "scripts/signal/tests/test_image_deps.py"
SUITE_LIVE = "scripts/signal/tests/test_liveness.py"


class Mutant:
    """One way to break the code, and the ONE test that must notice."""

    def __init__(self, mid, why, path, old, new, killer, suite):
        self.id, self.why, self.path = mid, why, path
        self.old, self.new, self.killer, self.suite = old, new, killer, suite


MUTANTS: list[Mutant] = [
    # ------------------------------------------------------------------ #
    # [audit] — found by an INDEPENDENT battery; every one SURVIVED the
    # battery its author had just certified as complete. These are the
    # highest-value rows in this file. Do not delete one because it looks
    # redundant with a neighbour; each names a distinct blind spot.
    # ------------------------------------------------------------------ #
    Mutant("A1", "[audit] the group-name COALESCE was DEAD CODE reading as protection: "
                 "the bind is `name or \"\"`, so EXCLUDED.name is never NULL. A later "
                 "nameless envelope wiped a stored name back to ''.",
           DB,
           "                    name = COALESCE(NULLIF(EXCLUDED.name, ''), groups.name),",
           "                    name = EXCLUDED.name,",
           "test_a_LATER_nameless_envelope_does_not_WIPE_a_stored_group_name", SUITE_EXCL),

    Mutant("A2", "[audit] `not_excluded()` validated its alias and then hardcoded `m`. "
                 "Every call site passes `m`, so nothing could tell the two apart and the "
                 "parameter silently became decorative.",
           DB,
           '        f"WHERE gx.id = {alias}.group_id)"',
           '        "WHERE gx.id = m.group_id)"',
           "test_the_predicate_actually_USES_the_alias_it_is_given", SUITE_EXCL),

    Mutant("A3", "[audit] `_fmt_group_id` emitting the urlsafe alphabet. Survived because "
                 "every fixture reaching it was a repeated byte whose base64 contains no "
                 "`+` or `/` — a fixture structurally unable to see the bug.",
           CON,
           "    return base64.b64encode(bytes(raw)).decode()",
           "    return base64.urlsafe_b64encode(bytes(raw)).decode()",
           "test_fmt_group_id_emits_the_STANDARD_alphabet_not_urlsafe", SUITE_EXCL),

    Mutant("A4", "[audit] widening the bytes guard to accept `str`. The test still passed — "
                 "on the TypeError from `bytes(\"…\")` further down, not on the guard. Green "
                 "for the wrong reason, and still green with the guard deleted.",
           DB,
           "        if not isinstance(group_id, (bytes, bytearray, memoryview)):",
           "        if not isinstance(group_id, (bytes, bytearray, memoryview, str)):",
           "test_a_str_group_id_is_refused", SUITE_EXCL),

    # ------------------------------------------------------------------ #
    # The mute list
    # ------------------------------------------------------------------ #
    Mutant("M1", "the mute filter removed from `search`", DB,
           "                  AND {not_excluded('m')}\n", "",
           "test_search_hides_a_muted_group", SUITE_EXCL),

    Mutant("M2", "the mute filter removed from `list_conversations`", DB,
           "                    WHERE {not_excluded('m')}\n", "",
           "test_conversations_hides_a_muted_group_and_shows_it_again_after_unmute",
           SUITE_EXCL),

    Mutant("M3", "the mute filter removed from `get_message` — the id route", DB,
           "f\"FROM signal.messages m WHERE m.id = %s AND {not_excluded('m')}\"",
           '"FROM signal.messages m WHERE m.id = %s"',
           "test_get_message_hides_a_muted_message_by_id", SUITE_EXCL),

    Mutant("M4", "the predicate INVERTED — NOT EXISTS becomes EXISTS, so the mute list "
                 "becomes an allowlist and everything else disappears", DB,
           '        "NOT EXISTS (SELECT 1 FROM signal.excluded_groups x "',
           '        "EXISTS (SELECT 1 FROM signal.excluded_groups x "',
           "test_the_predicate_is_composable_with_AND", SUITE_EXCL),

    Mutant("M5", "the SQL-alias whitelist accepts anything — the alias is interpolated, "
                 "so this is the injection surface", DB,
           '_SAFE_ALIAS = re.compile(r"^[a-z][a-z0-9_]{0,15}$")',
           '_SAFE_ALIAS = re.compile(r"")',
           "test_the_predicate_refuses_an_unsafe_alias", SUITE_EXCL),

    Mutant("M6", "`unmute` deletes nothing — the rollback story silently stops working",
           DB,
           '            cur.execute("DELETE FROM signal.excluded_groups WHERE group_id = %s",',
           '            cur.execute("DELETE FROM signal.excluded_groups WHERE group_id = %s AND 1=0",',
           "test_conversations_hides_a_muted_group_and_shows_it_again_after_unmute",
           SUITE_EXCL),

    Mutant("M7", "an empty group_id is accepted — it would mute nothing, silently", DB,
           '        if not bytes(group_id):\n'
           '            raise ValueError("group_id is empty — that would mute nothing, silently")',
           '        if False:\n'
           '            raise ValueError("group_id is empty — that would mute nothing, silently")',
           "test_an_empty_group_id_is_refused", SUITE_EXCL),

    Mutant("M8", "a note-less re-mute WIPES the recorded reason — `mute <id>` is the "
                 "natural way to re-issue and it destroyed the only record of why", DB,
           '                "note = COALESCE(EXCLUDED.note, excluded_groups.note)",',
           '                "note = EXCLUDED.note",',
           "test_re_muting_WITHOUT_a_note_keeps_the_recorded_reason", SUITE_EXCL),

    Mutant("M9", "`get_draft` loses `send_state IS NOT NULL`, so `is_outbound` alone also "
                 "matches device-sync ECHOES — which carry a group_id, leaking a muted "
                 "group's body through the draft surface", DB,
           "                WHERE m.id = %s AND m.is_outbound AND m.send_state IS NOT NULL",
           "                WHERE m.id = %s AND m.is_outbound",
           "test_get_draft_refuses_a_device_sync_ECHO_not_just_a_draft", SUITE_EXCL),

    Mutant("M10", "a NAME column ADDED to the mute table — the shape a name-keyed mute "
                  "list would take, which would have matched nothing because no group "
                  "name was stored for months. The killer pins the COLUMN LIST, not the "
                  "primary key, so that is what this breaks", DB,
           "        group_id BYTEA PRIMARY KEY,\n        note TEXT,",
           "        group_id BYTEA PRIMARY KEY,\n        name TEXT,\n        note TEXT,",
           "test_the_mute_table_is_keyed_on_the_binary_id_not_the_name", SUITE_EXCL),

    # ------------------------------------------------------------------ #
    # Operator input decoding
    # ------------------------------------------------------------------ #
    Mutant("D1", "the operator decoder loses its ROUND-TRIP check. 🔴 This mutant SURVIVED "
                 "the round that ADDED the length check below — the new guard swallowed "
                 "every input that used to reach the round trip, so the round trip became "
                 "unreachable and its removal went unnoticed. A fix round resets the gate.",
           CON,
           "    if not raw or base64.b64encode(raw).decode() != s:",
           "    if not raw and False:",
           "test_a_NON_CANONICAL_32_byte_encoding_is_refused", SUITE_EXCL),

    Mutant("D2", "the operator decoder loses its LENGTH check — `Team` (3 bytes) and "
                 "`deadbeef` (6) were accepted, muting nothing while printing success",
           CON,
           "    if len(raw) not in (16, 32):",
           "    if False:",
           "test_decode_internal_id_refuses_anything_non_canonical", SUITE_EXCL),

    Mutant("D3", "the length check rejects GroupV2 — off-by-one in the accepted set", CON,
           "    if len(raw) not in (16, 32):",
           "    if len(raw) not in (16,):",
           "test_decode_internal_id_round_trips_a_canonical_id", SUITE_EXCL),

    # ------------------------------------------------------------------ #
    # The group-name parse (#573)
    # ------------------------------------------------------------------ #
    Mutant("G1", "reverting to the pre-fix field: `groupInfo.name`, which real envelopes "
                 "never carry (34 of 34 measured)", CON,
           '"group_name": group.get("groupName") or group.get("name"),',
           '"group_name": group.get("name"),',
           "test_the_parser_reads_groupName_which_is_what_real_envelopes_carry", SUITE_EXCL),

    Mutant("G2", "OPERAND ORDER swapped. Only killable because the two fixtures hold "
                 "DISTINCT values — two operand-order mutants once survived 416 tests here "
                 "because every fixture set both fields to the same string", CON,
           '"group_name": group.get("groupName") or group.get("name"),',
           '"group_name": group.get("name") or group.get("groupName"),',
           "test_groupName_WINS_over_the_legacy_spelling", SUITE_EXCL),

    # ------------------------------------------------------------------ #
    # The heartbeat counters (#618). `test_the_thread_SURVIVES_a_beat_that_
    # fails_AFTER_it_started` used a wall-clock settle-and-poll and lost its
    # race 1 run in 3; it was rewritten to synchronise on the beat itself. A
    # rewrite that makes a test deterministic by no longer exercising the
    # failure path is worse than the flake it removed, so these three pin that
    # the REWRITTEN test still notices all three ways its subject can break.
    # Every one of them is also caught by the thread-free sibling
    # `test_a_FAILED_write_counts_an_ATTEMPT_but_NOT_a_TICK`.
    # ------------------------------------------------------------------ #
    Mutant("H1", "a FAILED write counts as a successful beat — `ticks += 1` hoisted "
                 "above the I/O. `ticks` is what says the file on disk actually "
                 "moved; incremented before the write, a consumer writing nowhere "
                 "reports a healthy climbing tick count forever.",
           CON,
           "        write_heartbeat_file(hb, self._path)\n        self.ticks += 1",
           "        self.ticks += 1\n        write_heartbeat_file(hb, self._path)",
           "test_the_thread_SURVIVES_a_beat_that_fails_AFTER_it_started", SUITE_LIVE),

    Mutant("H2", "`attempts` counted AFTER the I/O, so a failing sink increments "
                 "nothing. attempts-vs-ticks is the only thing that separates "
                 "'the thread is wedged' from 'the thread is trying and the disk "
                 "is refusing'; counted after the write, both read identically.",
           CON,
           "        self.attempts += 1\n        hb = self.payload()\n"
           "        write_heartbeat_file(hb, self._path)",
           "        hb = self.payload()\n        write_heartbeat_file(hb, self._path)\n"
           "        self.attempts += 1",
           "test_the_thread_SURVIVES_a_beat_that_fails_AFTER_it_started", SUITE_LIVE),

    Mutant("H3", "the file loop loses the except that lets it outlive a bad beat — "
                 "the thread then dies on the first transient, i.e. the liveness "
                 "signal reports death for exactly the fault it exists to ride out",
           CON,
           "            try:\n                self.tick()\n"
           "            except Exception as exc:  # noqa: BLE001 — the thread must outlive a bad beat\n"
           '                print(f"signal-consumer: heartbeat failed ({exc})", file=sys.stderr)\n',
           "            self.tick()\n",
           "test_the_thread_SURVIVES_a_beat_that_fails_AFTER_it_started", SUITE_LIVE),

    # ------------------------------------------------------------------ #
    # The build gate
    # ------------------------------------------------------------------ #
    Mutant("B1", "`build-push.sh`'s subcommand pin left stale — the control that refuses "
                 "to push an image whose CLI grew a subcommand nobody decided on", BP,
           'want_choices="approve conversations draft drafts health mute muted reconcile run search send unmute "',
           'want_choices="approve conversations draft drafts health reconcile run search send "',
           "test_the_build_control_lists_EXACTLY_the_CLI_subcommands", SUITE_IMAGE),
]


def anchor_report() -> list[tuple[Mutant, int]]:
    """Each mutant's anchor and how many times it occurs. Exactly 1 is required.

    0 → the code moved and the mutant would never land (`ANCHOR-MISS`, which is
    neither a kill nor a survival — it is the battery silently testing nothing).
    2+ → `str.replace(..., 1)` would hit whichever came first, which is not the
    site the mutant describes. A real mutant once reported ANCHOR-MISS because
    its anchor also matched an unrelated branch.
    """
    out = []
    cache: dict[str, str] = {}
    for m in MUTANTS:
        if m.path not in cache:
            cache[m.path] = (REPO / m.path).read_text(encoding="utf-8")
        out.append((m, cache[m.path].count(m.old)))
    return out


def _run(suite: str, *, verbose: bool = False) -> tuple[int, str]:
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    p = subprocess.run(
        [sys.executable, "-m", "pytest", suite, "-v" if verbose else "-q",
         "--no-header", "-p", "no:cacheprovider"],
        cwd=REPO, env=env, capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr


def _failed(out: str) -> set[str]:
    return {ln.split()[1].split("::")[-1].split("[")[0]
            for ln in out.splitlines() if ln.startswith(("FAILED ", "ERROR "))}


def _verdict(rc: int, failures: set[str], killer: str) -> tuple[str, str]:
    """(verdict, detail) for one mutant. PURE — no I/O, so it is table-testable.

    Extracted from `main` on an audit finding: while this logic was inline, the
    only guard on it was a test asserting the STRING `"m.killer in failures"`
    appeared in the source. That is satisfied by `elif m.killer in failures or
    True:` — which scores every mutant KILLED regardless of which test fired,
    i.e. exactly the green-for-the-wrong-reason this battery exists to prevent,
    with the guard's own words still present. A guard on WORDS is walkable by
    rewording; the fix is to make the behaviour reachable from a test.
    """
    if rc == 0:
        return "SURVIVED", "the suite stayed GREEN"
    if killer in failures:
        return "KILLED", f"by {killer} (+{len(failures) - 1} other)"
    return "KILLED-WRONG-REASON", f"expected {killer}, got {sorted(failures)}"


def _git_status(repo: Path) -> tuple[int, str]:
    """`git status --porcelain`, returning the STATUS as well as the output.

    🔴 FAIL CLOSED. Both callers used to read `.stdout` and ignore the return
    code, so any git failure produced an empty string — which reads as "clean".
    Measured: with `git status` broken (rc 128, empty stdout) the runner did not
    refuse, mutated a module in a tree holding uncommitted work, and finished by
    printing `tree restored: clean` — a positive claim about a check that never
    ran. Real ways to get rc≠0 with empty stdout: git's `safe.directory`
    ownership refusal, a concurrent `index.lock`, a `cp -a` copy of a worktree
    (whose `.git` is a FILE — a manoeuvre this repo's rules tell agents to make),
    an extracted tarball, a misconfigured `GIT_DIR`.
    """
    p = subprocess.run(["git", "-C", str(repo), "status", "--porcelain"],
                       capture_output=True, text=True)
    return p.returncode, p.stdout.strip()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--list", action="store_true", help="print the ledger and exit")
    ap.add_argument("--only", nargs="+", metavar="ID", help="run just these mutant ids")
    args = ap.parse_args(argv)

    if args.list:
        for m, n in anchor_report():
            flag = "" if n == 1 else f"  🔴 ANCHOR MATCHES {n}x"
            print(f"{m.id:4} {m.path:30} -> {m.killer}{flag}\n     {m.why}\n")
        return 0

    # 🔴 A SIGTERM used to leave a mutant in the tree. `finally` covers exceptions
    # and Ctrl-C (KeyboardInterrupt) but NOT a default-handled SIGTERM, which
    # kills the process outright. Measured: `timeout -s TERM` left
    # `_signal_db.py` modified; `timeout -s INT` restored cleanly. In a shared
    # checkout that silently hands the next session a mutated production module.
    # Turning it into SystemExit lets the `finally` run.
    _signal.signal(_signal.SIGTERM, lambda *_: sys.exit(1))

    # 🔴 REFUSE A DIRTY TREE. This mutates files in place. A crash mid-run would
    # take uncommitted work with it, and in this shared checkout that work is
    # usually somebody else's. Fails CLOSED — see `_git_status`.
    rc_git, dirty = _git_status(REPO)
    if rc_git != 0:
        print(f"REFUSING: `git status` exited {rc_git} in {REPO}, so the "
              "dirty-tree check COULD NOT MEASURE. An unreadable answer is not "
              "a clean one, and this battery edits files in place.",
              file=sys.stderr)
        return 2
    if dirty:
        print("REFUSING: the tree is dirty and this battery edits files in place.\n"
              "Commit, or run it in a clean worktree:\n"
              "  git -C <repo> worktree add ../devrc-mutants HEAD\n"
              f"dirty paths:\n{dirty}", file=sys.stderr)
        return 2

    selected = [m for m in MUTANTS if not args.only or m.id in args.only]
    if not selected:
        print(f"no mutants matched {args.only}", file=sys.stderr)
        return 2

    # Baseline. Without it a "kill" is unattributable — it might have been red
    # before the mutant landed.
    #
    # 🔴 AND IT PROVES EVERY NAMED KILLER ACTUALLY RAN AND PASSED. A killer that
    # merely EXISTS is not enough: mark one `@pytest.mark.skip` and it is still
    # collected, still greps as `def <name>`, and its mutant is then scored
    # SURVIVED — inverting the meaning of this tool's own output. Verified by
    # doing exactly that. Existence is checked statically in the gate; that it
    # RAN can only be observed here, in the run that happens anyway.
    baseline_passed: set[str] = set()
    for suite in {m.suite for m in selected}:
        rc, out = _run(suite, verbose=True)
        summary = [ln for ln in out.strip().splitlines() if " passed" in ln or " failed" in ln]
        print(f"BASELINE {suite}: rc={rc}  {summary[-1] if summary else '(no verdict)'}")
        if rc != 0:
            print("  !! baseline RED — aborting; nothing below would be attributable",
                  file=sys.stderr)
            return 2
        for ln in out.splitlines():
            if " PASSED" in ln and "::" in ln:
                baseline_passed.add(ln.split("::")[-1].split()[0].split("[")[0])

    unrun = sorted({m.killer for m in selected} - baseline_passed)
    if unrun:
        print("REFUSING: these named killer tests did not PASS in the baseline, so "
              "any mutant they guard would be scored SURVIVED for the wrong "
              f"reason (skipped? renamed? deselected?): {unrun}", file=sys.stderr)
        return 2

    results = []
    for m in selected:
        target = REPO / m.path
        original = target.read_text(encoding="utf-8")
        hits = original.count(m.old)
        if hits != 1:
            results.append((m, "ANCHOR-MISS", f"anchor matched {hits}x, need exactly 1"))
            print(f"{m.id}: ANCHOR-MISS ({hits}x) — the mutant never landed")
            continue
        backup = tempfile.mkstemp(prefix="mutant-")[1]
        shutil.copyfile(target, backup)
        try:
            target.write_text(original.replace(m.old, m.new, 1), encoding="utf-8")
            rc, out = _run(m.suite)
            verdict, detail = _verdict(rc, _failed(out), m.killer)
            results.append((m, verdict, detail))
            print(f"{m.id}: {verdict} — {detail}")
        finally:
            shutil.copyfile(backup, target)
            os.unlink(backup)

    print("\n================ SUMMARY ================")
    for m, verdict, detail in results:
        print(f"  {verdict:20} {m.id}  {m.killer}")
    bad = [r for r in results if r[1] != "KILLED"]
    print(f"\n{len(results) - len(bad)}/{len(results)} killed by their NAMED test")
    for m, verdict, detail in bad:
        print(f"  !! {verdict}: {m.id} — {detail}", file=sys.stderr)

    rc_git, after = _git_status(REPO)
    if rc_git != 0:
        print(f"\n🔴 COULD NOT MEASURE whether a mutant was left behind — "
              f"`git status` exited {rc_git}. Check the tree by hand; this is "
              "NOT a clean report.", file=sys.stderr)
        return 3
    if after:
        print(f"\n🔴 THE TREE IS DIRTY AFTER THE RUN — a mutant was left behind:\n{after}",
              file=sys.stderr)
        return 3
    print("tree restored: clean")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
