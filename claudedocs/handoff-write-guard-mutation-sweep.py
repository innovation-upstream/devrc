#!/usr/bin/env python3
"""Mutation sweep for handoff-write-guard.py -- the evidence behind this hook's
"N/N killed" claim, committed rather than left in a scratchpad.

Run it against a CLEAN worktree of the branch, never against the primary clone:

    git worktree add --detach /tmp/hwg <ref>
    HWG_TREE=/tmp/hwg python3 claudedocs/handoff-write-guard-mutation-sweep.py

It EDITS the hook in place and restores it in a `finally`, so a tree with other
uncommitted work in that file is not a safe place to run it.

Each mutant must (a) go RED, and (b) go red on the SPECIFIC test named for it — a
mutant that dies to a neighbour proves nothing about the guard it targets.

PYTHONDONTWRITEBYTECODE=1 throughout: a same-length edit landing in the same whole
second as the last import is invisible to CPython's mtime+size cache, and the mutant
would be scored SURVIVED without ever executing.
"""
import os
import subprocess
import sys

WT = os.environ.get("HWG_TREE", "/tmp/wt-hwg")
HOOK = os.path.join(WT, "scripts/claude-hooks/handoff-write-guard.py")
TEST = os.path.join(WT, "scripts/claude-hooks/tests/test_handoff_write_guard.py")

# (label, old, new, the test that MUST fail)
MUTANTS = [
    ("wrote>= -> wrote>",
     "if wrote_at is not None and wrote_at >= read_at:",
     "if wrote_at is not None and wrote_at > read_at:",
     "test_a_write_in_the_SAME_tool_call_as_the_read_satisfies"),
    ("drop the mtime satisfaction route",
     'if isinstance(doc, str) and doc and os.path.getmtime(doc) >= read_at:',
     'if isinstance(doc, str) and doc and os.path.getmtime(doc) < read_at and False:',
     "test_the_docs_own_mtime_satisfies_even_with_no_observation"),
    ("mtime route ignores the anchor",
     'if isinstance(doc, str) and doc and os.path.getmtime(doc) >= read_at:',
     'if isinstance(doc, str) and doc and os.path.getmtime(doc) >= 0:',
     "test_an_mtime_BEFORE_the_read_does_not_satisfy"),
    ("drop the wrote satisfaction route",
     "if wrote_at is not None and wrote_at >= read_at:",
     "if wrote_at is not None and wrote_at >= read_at and False:",
     "test_a_handoff_doc_py_run_satisfies"),
    ("unreadable read stamp -> written instead of unknown",
     '    if read_at is None:\n        # 🔴 CANNOT MEASURE',
     '    if read_at is None:\n        return "written"\n        # 🔴 CANNOT MEASURE',
     "test_an_unreadable_read_stamp_is_a_notice_and_never_blocks"),
    ("drop the work gate at Stop",
     "    if not work_happened(state_dir):",
     "    if not work_happened(state_dir) and False:",
     "test_read_with_no_work_is_silent_and_bumps_NOTHING"),
    ("Stop stops refusing a subagent",
     '    if d.get("agent_id"):\n        return ("silent", "")',
     '    if d.get("agent_id") and False:\n        return ("silent", "")',
     "test_these_stop_shaped_payloads_are_refused"),
    ("Stop stops refusing SubagentStop",
     '    if d.get("hook_event_name") not in (None, "Stop"):',
     '    if d.get("hook_event_name") not in (None, "Stop", "SubagentStop"):',
     "test_these_stop_shaped_payloads_are_refused"),
    ("record_read stops consulting the tombstone",
     "    if is_dismissed(state_dir, key):\n        return False",
     "    if is_dismissed(state_dir, key) and False:\n        return False",
     "test_dismiss_SURVIVES_a_later_read_of_the_same_doc"),
    ("MAX_DOCS census removed",
     'if len([n for n in os.listdir(state_dir) if n.startswith("read-")]) >= MAX_DOCS:',
     'if len([n for n in os.listdir(state_dir) if n.startswith("read-")]) >= 99:',
     "test_at_most_three_docs_are_tracked_per_session"),
    ("record_read stops being idempotent",
     "    if os.path.exists(path):\n        return False",
     "    if os.path.exists(path) and False:\n        return False",
     "test_a_re_read_never_moves_the_first_read_timestamp"),
    ("arming drops the handoff-basename filter",
     "        if not _is_handoff_basename(raw):\n            continue",
     "        if not _is_handoff_basename(raw) and False:\n            continue",
     "test_these_bash_commands_do_NOT_arm"),
    ("arming drops the comment strip",
     '        stripped = re.sub(COMMENT_PAT, " ", cmd)',
     '        stripped = cmd',
     "test_a_path_after_a_hash_is_a_comment_not_a_read"),
    ("arming resolves without requiring the directory",
     "        if os.path.isdir(os.path.dirname(c)):\n            return c",
     "        if True:\n            return c",
     "test_a_path_whose_directory_does_not_exist_does_NOT_arm"),
    ("Read arm loses cwd as a base",
     '        bases = _bases("", d.get("cwd"))',
     '        bases = []',
     "test_a_RELATIVE_Read_path_resolves_against_cwd"),
    ("arming ignores -C and uses cwd only",
     "    out = []\n    for m in DASH_C_RX.finditer(cmd or \"\"):",
     "    out = []\n    for m in DASH_C_RX.finditer(\"\"):",
     "test_a_git_show_off_a_ref_arms_and_resolves_through_the_dash_C"),
    ("arming admits a Write as a read",
     '    if tool == "Read":',
     '    if tool in ("Read", "Write", "Edit"):',
     "test_a_WRITE_of_a_handoff_doc_does_NOT_arm"),
    ("arming accepts a subagent's read",
     "    docs = [] if agent else handoff_read_docs(data)",
     "    docs = handoff_read_docs(data)",
     "test_a_subagents_read_does_NOT_arm_the_parent"),
    ("subagent handoff WRITE stops satisfying the parent",
     "    if is_handoff_write(data):",
     "    if is_handoff_write(data) and not agent:",
     "test_a_subagents_handoff_WRITE_also_counts"),
    ("subagent work stops counting",
     "    if is_work(data):",
     "    if is_work(data) and not agent:",
     "test_a_subagents_WORK_does_count_once_the_parent_has_read"),
    ("ladder: one more block",
     "MAX_BLOCKS = 2",
     "MAX_BLOCKS = 3",
     "test_the_ladder_moves_block_block_notice_silent"),
    ("ladder: never relents",
     "MAX_FIRES = 3",
     "MAX_FIRES = 99",
     "test_the_ladder_moves_block_block_notice_silent"),
    ("unknown spends the BLOCK budget",
     'if escalate(bump_fires(state_dir, key, "unknown")) != "silent":',
     'if escalate(bump_fires(state_dir, key)) != "silent":',
     "test_an_unreadable_read_stamp_is_a_notice_and_never_blocks"),
    ("notice becomes additionalContext (the channel that does NOT relent)",
     '    elif kind == "notice":\n        json.dump({"systemMessage": text}, sys.stdout)',
     '    elif kind == "notice":\n        json.dump({"hookSpecificOutput": {"additionalContext": text}}, sys.stdout)',
     "test_a_notice_alone_never_forces_a_continuation"),
    ("the doc key becomes the full path",
     '    return _sanitize(os.path.basename(doc_path))',
     '    return _sanitize(str(doc_path))',
     "test_the_key_is_the_basename_so_one_doc_read_three_ways_books_one_slot"),
    ("dismiss stops writing the tombstone",
     "    write_dismissal_tombstone(state_dir, key, doc=doc, now=now)",
     "    pass",
     "test_dismiss_SURVIVES_a_later_read_of_the_same_doc"),
    ("the fast path is taken AFTER the work write",
     "    if not tracked and not docs:",
     "    if False:",
     "test_the_fast_path_does_exactly_one_stat_and_nothing_else"),
    ("state root shared with the precedent",
     '"claude-handoff-write", "s")',
     '"claude-clawgate-writeback", "s")',
     "test_the_handoff_guards_cache_root_is_its_OWN"),
]

# POSITIVE CONTROL: a mutant that must be caught, proving the harness can go red at all.
CONTROL = ("POSITIVE CONTROL: emit() never writes",
           '    if kind == "block":',
           '    if False:',
           "test_the_real_process_blocks_end_to_end")

ONDISK = os.path.join(WT, "scripts/claude-hooks/tests/test_on_disk_artifact_names.py")


def run(target_test, extra_file=None):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    files = [TEST] + ([extra_file] if extra_file else [])
    p = subprocess.run([sys.executable, "-m", "pytest", *files,
                        "-q", "-p", "no:cacheprovider", "--no-header", "-x",
                        "-k", target_test.split("[")[0]],
                       cwd=WT, capture_output=True, text=True, env=env, timeout=600)
    return p


def main():
    original = open(HOOK).read()
    results = []
    for label, old, new, test in [CONTROL] + MUTANTS:
        if old not in original:
            results.append((label, "PATTERN-NOT-FOUND", ""))
            continue
        if original.count(old) != 1:
            results.append((label, "PATTERN-AMBIGUOUS(%d)" % original.count(old), ""))
            continue
        extra = ONDISK if "cache_root_is_its_OWN" in test else None
        try:
            open(HOOK, "w").write(original.replace(old, new, 1))
            p = run(test, extra)
            # Count the runner's OWN result lines rather than reading an exit code.
            tail = [ln for ln in p.stdout.splitlines() if " failed" in ln or " passed" in ln]
            killed = "failed" in (tail[-1] if tail else "")
            named = test in p.stdout
            results.append((label, "KILLED" if killed else "SURVIVED",
                            "%s | named=%s" % (tail[-1] if tail else "NO SUMMARY", named)))
        finally:
            open(HOOK, "w").write(original)
    for label, verdict, detail in results:
        print("%-9s %-62s %s" % (verdict, label, detail))
    bad = [r for r in results if r[1] != "KILLED"]
    print("\n%d/%d killed" % (len(results) - len(bad), len(results)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
