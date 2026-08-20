#!/usr/bin/env python3
"""The `session-write` MUTATION SWEEP — committed, so the claim is reproducible.

🔴 WHY THIS FILE IS IN THE REPO
---------------------------------
The first version of PR #582 claimed "36 deliberate mutations, 36 KILLED" and
shipped no harness. An auditor could not reproduce it, noticed the printed
matrix skipped number 11 with no explanation while the body said 36, and then
found — with an independent 21-mutant sweep — SEVEN survivors of a fully green
117-test suite, including the deploy-blocking `type` shell-exec bypass.

A sweep is an INSTRUMENT, and RULES.md is explicit that an instrument's
self-report is a claim about the instrument until its controls have been
watched to work. An unreproducible sweep is not evidence of anything. So:

  * the numbering here is generated from the table, so it CANNOT skip;
  * two controls run in every batch, and BOTH must behave or the run is void:
      PC  a mutation known to be caught  -> must be KILLED
      NC  a semantically NULL edit       -> must SURVIVE
    PC alone cannot distinguish a working harness from one that reports
    everything as killed (a `pytest` that fails to start would score a clean
    100%); NC is what closes that.
  * `PYTHONDONTWRITEBYTECODE=1` on every spawned process AND `__pycache__`
    purged before every mutant. CPython validates cached bytecode on
    whole-second mtime + size, so a SAME-LENGTH edit landing in the same second
    as the last import is invisible: the test imports the ORIGINAL bytecode and
    the mutant is scored SURVIVED without ever having executed.
  * every patch site is asserted to occur EXACTLY ONCE before it is applied, so
    a `count=1` replace cannot hit an occurrence nobody pictured;
  * the original bytes are restored in a `finally` and SHA-256 byte-identity is
    asserted after every mutant and again at the end;
  * the FULL test file runs for each mutant — no `-k` filter, which is how a
    sweep reports SURVIVED because it excluded the killing test;
  * a mutant is KILLED only when the NAMED test is in the FAILED set. "Something
    went red" is never accepted: a different guard's error killing your test is
    green for the wrong reason and stays green with your guard deleted;
  * a mutant that fails MORE than `BLAST_LIMIT` of the suite is reported as
    NOT ISOLATED — it probably broke the import rather than the guard, and a
    kill attributed to it is unearned.

🔴 THE MUTATION CLASSES ARE SEMANTIC, NOT JUST DELETIONS. A sweep built only
from "remove the guard" has a blind spot the audit already walked into: operand
swaps, branch inversions, comment-outs (text still present, clause dead), stale
rebinds, off-by-one and constant renumbering all survive a deletions-only sweep.
Each mutant below carries its CLASS so the coverage of the classes is legible.

    usage:  python3 scripts/session-write-harness/mutation_sweep.py [-k SUBSTR]
    exit:   0 = every mutant killed and both controls behaved
            1 = a survivor, an unearned kill, or a broken control
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, "..", ".."))
_SRC = os.path.join(_ROOT, "scripts", "session-write")
_TESTS = os.path.join(_ROOT, "scripts", "tests", "test_session_write.py")

#: A mutant failing more of the suite than this did not isolate anything.
BLAST_LIMIT = 0.5

# Classes, so the sweep's own coverage of MUTATION SHAPES is readable.
DEL = "deletion"            # a guard removed outright
INV = "branch-inversion"    # a condition flipped or forced
SWP = "operand-swap"        # two correct values exchanged
CMT = "comment-out"         # the text stays, the clause dies
REB = "stale-rebind"        # a value recomputed/overwritten with a stale one
OBO = "off-by-one"          # a bound or slice narrowed by one
NUM = "renumber"            # a constant given a different value
WID = "allowlist-widening"  # a permitted set grown


class M:
    def __init__(self, guard, cls, desc, find, repl, killer, wide=False):
        self.guard, self.cls, self.desc = guard, cls, desc
        self.find, self.repl, self.killer = find, repl, killer
        #: 🔴 DECLARED, never inferred. `BLAST_LIMIT` exists to catch a mutant
        #: that broke the IMPORT rather than the guard — a kill attributed to
        #: such a mutant is unearned, because every test died for one reason
        #: that has nothing to do with the guard. But a few mutations really do
        #: have a wide blast: hardcoding the local host makes G2 refuse EVERY
        #: local target, and nearly every test in the file resolves one. Those
        #: must say so HERE, so the exemption is a reviewable claim about one
        #: mutant rather than a threshold quietly raised for all of them.
        self.wide = wide


# --------------------------------------------------------------------------- #
# THE TABLE. Numbering is derived from position, so it cannot develop a gap.
# --------------------------------------------------------------------------- #
MUTANTS = [
    # ---- G13, the --text ALLOWLIST: the audited deploy blocker -------------
    M("G13", DEL, "the allowlist predicate always permits",
      "    return ch in TEXT_EXTRA_ALLOWED or ch.isprintable()",
      "    return True",
      "test_a_character_outside_the_allowlist_is_refused_by_codepoint"),
    M("G13", WID, "TEXT_EXTRA_ALLOWED widened to admit Ctrl-O — the fix that "
                  "was explicitly REJECTED, re-applied as a mutant",
      'TEXT_EXTRA_ALLOWED = ("\\t",)', 'TEXT_EXTRA_ALLOWED = ("\\t", "\\x0f")',
      "test_tab_is_the_ONE_control_character_re_permitted_and_it_is_explicit"),
    M("G13", INV, "the scan permits every character it examines",
      "        if TEXT_IS_ALLOWED(ch):\n            continue",
      "        if True:\n            continue",
      "test_a_character_outside_the_allowlist_is_refused_by_codepoint"),
    M("G13", SWP, "isprintable swapped for isascii — admits every control byte "
                  "below 0x80 while still refusing something, so a test that "
                  "only checks 'some character is refused' cannot see it",
      "ch.isprintable()", "ch.isascii()",
      "test_a_character_outside_the_allowlist_is_refused_by_codepoint"),
    M("G13", OBO, "only the FIRST character is scanned",
      "    for idx, ch in enumerate(text):",
      "    for idx, ch in enumerate(text[:1]):",
      "test_a_character_outside_the_allowlist_is_refused_by_codepoint"),
    M("G13", NUM, "the reported codepoint hardcoded",
      'codepoint=f"{ord(ch):04X}"', 'codepoint="0000"',
      "test_the_refusal_names_the_COMPUTED_codepoint_not_the_one_in_its_own_prose"),
    M("G13", NUM, "the reported offset hardcoded to 0 — the fixture never puts "
                  "the character first, so this is visible",
      "                                          offset=idx),",
      "                                          offset=0),",
      "test_a_character_outside_the_allowlist_is_refused_by_codepoint"),
    M("G13", CMT, "the trailing-separator refusal made dead code",
      "    if text.endswith(TMUX_ARGV_SEPARATOR):",
      "    if False and text.endswith(TMUX_ARGV_SEPARATOR):",
      "test_text_ending_in_the_tmux_separator_is_refused"),
    M("G13", SWP, "trailing-separator test widened to ANY separator, which "
                  "breaks a legitimate shell payload",
      "    if text.endswith(TMUX_ARGV_SEPARATOR):",
      "    if TMUX_ARGV_SEPARATOR in text:",
      "test_a_separator_in_the_MIDDLE_of_the_text_is_allowed"),

    # ---- G14, the log path confinement ------------------------------------
    M("G14", DEL, "validate_log_path never refuses",
      "    resolved = os.path.realpath(path)",
      "    return None\n    resolved = os.path.realpath(path)",
      "test_a_log_path_outside_the_root_is_REFUSED_before_anything_resolves"),
    M("G14", SWP, "commonpath swapped for startswith — a sibling directory "
                  "sharing the root's PREFIX becomes 'inside'",
      "        inside = os.path.commonpath([resolved, real_root]) == real_root",
      "        inside = resolved.startswith(real_root)",
      "test_a_sibling_directory_sharing_the_roots_PREFIX_is_outside_it"),
    M("G14", SWP, "realpath swapped for abspath — normalises `..` but follows "
                  "no symlink, so a link planted inside the root escapes",
      "    resolved = os.path.realpath(path)",
      "    resolved = os.path.abspath(path)",
      "test_a_symlink_planted_inside_the_root_cannot_point_out_of_it"),
    M("G14", DEL, "the root itself accepted as a log path",
      "    if inside and resolved != real_root:",
      "    if inside:",
      "test_the_root_ITSELF_is_not_a_legal_log_path"),
    M("G14", CMT, "the G14 call in `run` made dead code",
      "    refusal = validate_log_path(ws.requested_log_path(), "
      "ws.resolved_log_root())\n    if refusal:",
      "    refusal = validate_log_path(ws.requested_log_path(), "
      "ws.resolved_log_root())\n    if False and refusal:",
      "test_a_log_path_outside_the_root_is_REFUSED_before_anything_resolves"),
    M("G14", REB, "resolved_log_path honours the REFUSED request anyway, so "
                  "the refusal is journalled to the path it just refused",
      "        if validate_log_path(requested, root) is None:\n"
      "            return requested",
      "        if True:\n            return requested",
      "test_a_refused_log_path_is_still_JOURNALLED_but_never_to_that_path"),

    # ---- G5, the screen gate (now covering dismiss) ------------------------
    M("G5", DEL, "dismiss dropped from the screen-verb ledger — the exact gap "
                 "the audit found",
      '                "dismiss": MSG_DISMISS_NEEDS_FLAG}',
      '                }',
      "test_dismiss_refuses_without_the_flag_and_touches_nothing"),
    M("G5", SWP, "the two screen refusals share focus's wording, which is how a "
                 "gate moves between verbs invisibly",
      '                "dismiss": MSG_DISMISS_NEEDS_FLAG}',
      '                "dismiss": MSG_FOCUS_NEEDS_FLAG}',
      "test_dismiss_refuses_without_the_flag_and_touches_nothing"),
    M("G5", INV, "the keyboard acknowledgement is never required",
      "    if args.verb in SCREEN_VERBS and not args.at_keyboard:",
      "    if False:",
      "test_focus_refuses_without_the_flag_and_touches_nothing"),

    # ---- G6 / focus's mirror assertion -------------------------------------
    M("G6", DEL, "the base-terminal guard never refuses",
      '    if client.get("base_terminal"):', "    if False:",
      "test_dismiss_refuses_to_detach_a_base_terminal"),
    M("G6", DEL, "focus's mirror assertion never refuses — `focus --client "
                 "<popup tty>` steers an overlay again",
      '    if client.get("popup"):', "    if False:",
      "test_focus_refuses_an_explicit_client_that_is_a_POPUP"),
    M("G6", DEL, "the explicit --client path skips the base-terminal assertion "
                 "entirely, which is precisely the audited bug",
      "        refusal = _assert_is_base_terminal(client)\n"
      "        return (None, refusal) if refusal else (client, None)",
      "        return client, None",
      "test_focus_refuses_an_explicit_client_that_is_a_POPUP"),
    M("G6", INV, "pick_named_client searches only popups, making the "
                 "base-terminal guard UNREACHABLE. The exit CODE is unchanged, "
                 "so only a whole-string message assertion can see it",
      "    for c in clients:\n        if c.get(\"tty\") == tty:",
      "    for c in clients:\n        if c.get(\"popup\") and "
      "c.get(\"tty\") == tty:",
      "test_dismiss_refuses_to_detach_a_base_terminal"),

    # ---- G9, the unsent-prompt gate (now covering bare send) ---------------
    M("G9", DEL, "the unsent-prompt gate disabled",
      "    prompt = target.get(\"unsent_prompt\")\n    if not prompt:",
      "    prompt = target.get(\"unsent_prompt\")\n    if True:",
      "test_writing_onto_unsent_operator_text_is_refused"),
    M("G9", INV, "bare send exempted from the gate again — the audited gap, "
                 "restored exactly as it was",
      "    refusal = gate_unsent_prompt(target, args.append,\n"
      "                                 submitting_only=args.text is None)",
      "    refusal = (gate_unsent_prompt(target, args.append)\n"
      "               if args.text is not None else None)",
      "test_bare_send_IS_unsent_gated_and_says_it_would_SUBMIT"),
    M("G9", SWP, "bare send refuses with the CONCATENATE wording, which "
                 "misdescribes what the operator is authorising",
      "                                 submitting_only=args.text is None)",
      "                                 submitting_only=False)",
      "test_bare_send_IS_unsent_gated_and_says_it_would_SUBMIT"),
    M("G9", DEL, "the refusal reports the prompt's CONTENT, not its length. "
                 "🔴 This repo is PUBLIC and that string is captured text",
      "template.format(address=target.get(\"address\"), length=len(prompt)),",
      "template.format(address=target.get(\"address\"), length=prompt),",
      "test_the_unsent_prompt_CONTENT_never_reaches_the_output"),

    # ---- G8, the shell-exec gate -------------------------------------------
    M("G8", DEL, "the agent allowlist shrunk to one runtime — invisible to a "
                 "single-runtime test",
      'AGENT_RUNTIMES = ("claude", "opencode")', 'AGENT_RUNTIMES = ("claude",)',
      "test_send_to_an_agent_runtime_is_not_gated"),
    M("G8", SWP, "the gate keyed on `runtime is not None`. 🔴 Every end-to-end "
                 "test SURVIVES this: the shell fixture's runtime IS None and "
                 "both spellings refuse it. Only the unit test feeding \"bash\" "
                 "— a value the constant CANNOT equal — kills it",
      "    if runtime in AGENT_RUNTIMES:", "    if runtime is not None:",
      "test_gate_shell_exec_reads_the_runtime_and_nothing_else"),
    M("G8", INV, "the shell gate skipped when --text is absent, so the more "
                 "dangerous shape is the unguarded one",
      "    refusal = gate_shell_exec(target, args.allow_shell_exec)",
      "    refusal = (gate_shell_exec(target, args.allow_shell_exec)\n"
      "               if args.text is not None else None)",
      "test_bare_send_to_an_unrecorded_runtime_is_also_refused"),
    M("G8", INV, "the shell gate ALSO applied to `type`, which would break the "
                 "verb rather than protect it",
      "    refusal = validate_text(args.text)\n    if refusal:\n"
      "        return refusal\n    refusal = gate_unsent_prompt(target, "
      "args.append)",
      "    refusal = validate_text(args.text)\n    if refusal:\n"
      "        return refusal\n    refusal = gate_shell_exec(target, False)\n"
      "    if refusal:\n        return refusal\n"
      "    refusal = gate_unsent_prompt(target, args.append)",
      "test_type_is_ungated_ONLY_because_the_allowlist_makes_it_safe"),

    # ---- G2, local host only -----------------------------------------------
    M("G2", INV, "the remote-host comparison forced false",
      "    if target_host != local_host:", "    if False:",
      "test_a_remote_target_is_refused_by_name"),
    M("G2", NUM, "the local host hardcoded to a production name — the fixture "
                 "host is deliberately NOT one, so the LOCAL case fails too",
      '        "local_host")', '        "local_host") and "workbench"',
      "test_a_local_target_is_not_refused",
      # WIDE, and legitimately so: with the local host forced to a name no
      # fixture uses, G2 refuses every local target and ~half the suite goes
      # red. Measured 83/158. The kill is still attributed to the named test.
      wide=True),
    M("G2", DEL, "the unknown-local-host branch removed: locality that cannot "
                 "be proven is treated as proven",
      "    if not local_host or local_host == sr.LOCAL_HOST_UNKNOWN:",
      "    if False:",
      "test_an_unknown_local_host_is_refused_rather_than_assumed"),

    # ---- G3, re-verify at write time ---------------------------------------
    M("G3", INV, "the pane-existence half of the re-verify never runs",
      "    if need_pane:", "    if False:",
      "test_a_pane_that_vanished_between_resolve_and_write_is_refused"),
    M("G3", INV, "every verb re-verifies the pane, including the client verbs "
                 "that have none",
      "need_pane=args.verb in PANE_VERBS", "need_pane=True",
      "test_a_client_verb_does_not_require_the_pane_to_survive"),

    # ---- G4, the subprocess seam -------------------------------------------
    M("G4", WID, "a destructive verb added to the write allowlist",
      'TMUX_WRITE_SUBCOMMANDS = (', 'TMUX_WRITE_SUBCOMMANDS = ("kill-pane",',
      "test_the_seam_refuses_a_destructive_verb"),
    M("G4", DEL, "the seam stops after the FIRST command, so a `;`-welded "
                 "second one is never checked",
      "            break       # the rest of THIS segment is that command's "
      "arguments",
      "            return",
      "test_the_seam_checks_every_command_not_just_the_first"),
    M("G4", CMT, "run_tmux stops asserting the allowlist",
      "    argv = list(argv)\n    _assert_allowed(argv)",
      "    argv = list(argv)\n    pass  # _assert_allowed(argv)",
      "test_run_tmux_asserts_the_allowlist_even_on_a_dry_run"),

    # ---- focus: record / raise / restore -----------------------------------
    M("focus", REB, "pick_base_terminal returns the FIRST base terminal rather "
                    "than the focused one. The fixture puts the unfocused one "
                    "first, deliberately",
      "    if len(base) == 1:\n        return base[0], None",
      "    if len(base) >= 1:\n        return base[0], None",
      "test_focus_picks_the_focused_base_terminal_not_the_first_one"),
    M("focus", DEL, "the restore never runs",
      "    if args.stay and raise_failure is None:",
      "    if raise_failure is None:",
      "test_focus_records_and_restores_both_tmux_dimensions"),

    # ---- G1, re-resolve at write time --------------------------------------
    M("G1", INV, "an AMBIGUOUS resolution treated as a normal one",
      "    if refusal:\n        return refusal\n\n    fresh, refusal = "
      "reverify(",
      "    if False:\n        return refusal\n\n    fresh, refusal = reverify(",
      "test_an_ambiguous_selector_is_refused_with_session_resolves_exit_code"),

    # ---- the audit log ------------------------------------------------------
    M("G10", CMT, "log_record made a no-op",
      "    path = ws.resolved_log_path()",
      "    return None\n    path = ws.resolved_log_path()",
      "test_every_run_writes_exactly_one_log_line"),
    M("G10", NUM, "the on-disk artifact RENAMED — invisible to every "
                  "behavioural test, which only ever reads the path back",
      'DEFAULT_LOG_PATH = os.path.join(LOG_ROOT, "session-write.log")',
      'DEFAULT_LOG_PATH = os.path.join(LOG_ROOT, "sessionwrite.log")',
      "test_the_log_path_is_pinned"),
    M("G10", INV, "a dry run logged as a real write",
      '    if args.dry_run and str(record.get("outcome", "")).startswith('
      '"written:"):',
      "    if False:",
      "test_a_dry_run_is_never_logged_as_a_write"),

    # ---- the client summary: the three the AUDIT found surviving -----------
    M("summary", SWP, "tty and session swapped in the client summary. 🔴 A "
                      "SURVIVOR of the original sweep — the expectations were "
                      "built by calling this same function, so both sides "
                      "swapped together",
      "    return \", \".join(f\"{c['tty']} ({c['session']}, \"",
      "    return \", \".join(f\"{c['session']} ({c['tty']}, \"",
      "test_dismiss_refuses_an_unattached_client"),
    M("summary", INV, "the popup/base label inverted. 🔴 Also a SURVIVOR, for "
                      "the same reason",
      "                     f\"{'popup' if c['popup'] else 'base'})\"",
      "                     f\"{'base' if c['popup'] else 'popup'})\"",
      "test_dismiss_refuses_an_unattached_client"),
    M("summary", DEL, "every client rendered as the empty-list fallback. 🔴 The "
                      "third SURVIVOR — `f(x) == f(x)` is true for any f",
      '                     for c in clients) or "none"',
      '                     for c in clients) and "none"',
      "test_dismiss_refuses_an_unattached_client"),

    # ---- exit codes and the outcome-token ledger ---------------------------
    M("codes", NUM, "EXIT_REMOTE_HOST renumbered. 🔴 Every other test compares "
                    "`outcome.code == sw.EXIT_REMOTE_HOST` — both sides move "
                    "together. Only the test pinning the LITERAL can see it",
      "EXIT_REMOTE_HOST = 4", "EXIT_REMOTE_HOST = 40",
      "test_exit_codes_are_pairwise_distinct_and_reuse_session_resolves"),
    M("codes", NUM, "EXIT_BAD_LOG_PATH renumbered onto an existing code, which "
                    "also breaks the pairwise-distinct claim",
      "EXIT_BAD_LOG_PATH = 13", "EXIT_BAD_LOG_PATH = 11",
      "test_exit_codes_are_pairwise_distinct_and_reuse_session_resolves"),
    M("codes", DEL, "a gate's outcome token dropped from the ledger, so a "
                    "caller branching on it silently stops matching",
      '    "refused:bad-log-path",\n', "",
      "test_the_outcome_token_ledger_matches_what_the_SOURCE_actually_emits"),

    # ---- type / send argv ---------------------------------------------------
    M("type", DEL, "the `-l` literal flag dropped, so tmux interprets the "
                   "payload as KEY NAMES instead of text",
      '    failed = _issue(ws, ["tmux", "send-keys", "-t", pane, "-l", "--",\n'
      "                         args.text], args.dry_run)",
      '    failed = _issue(ws, ["tmux", "send-keys", "-t", pane, "--",\n'
      "                         args.text], args.dry_run)",
      "test_type_sends_literal_text_and_no_enter"),
    M("type", DEL, "an Enter appended after the literal text, so `type` submits",
      '                   record={"outcome": "written:type", "message": msg,',
      '                   record={"outcome": "written:type", "message": msg,',
      None),   # placeholder replaced below; see _ENTER_MUTANT
]

#: 🔴 Built separately because it INSERTS rather than substitutes, and an insert
#: whose anchor also appears in the replacement is the shape that silently
#: applies twice.
_ENTER_MUTANT = M(
    "type", DEL, "an Enter appended after the literal text, so `type` submits "
                 "— the exact contract violation the verb is named against",
    '    msg = (f"typed: {len(args.text)} character(s) into pane {pane} "',
    '    _issue(ws, ["tmux", "send-keys", "-t", pane, "Enter"], args.dry_run)\n'
    '    msg = (f"typed: {len(args.text)} character(s) into pane {pane} "',
    "test_type_sends_literal_text_and_no_enter")
MUTANTS[-1] = _ENTER_MUTANT

# --------------------------------------------------------------------------- #
# The two CONTROLS. Both must behave or the whole run is void.
# --------------------------------------------------------------------------- #
PC = M("CONTROL", INV,
       "POSITIVE CONTROL — a mutation known to be caught. If this is ever "
       "reported SURVIVED, the harness is broken, not the code",
       'MSG_FOCUS_NEEDS_FLAG = (\n    "REFUSED: focus changes your screen.\\n"',
       'MSG_FOCUS_NEEDS_FLAG = (\n    "REFUSED: focus changes the screen.\\n"',
       "test_the_focus_refusal_is_exactly_the_agreed_wording")

NC = M("CONTROL", "null",
       "NEGATIVE CONTROL — a semantically NULL edit that MUST survive. Without "
       "it, a harness that scored every mutant KILLED (a pytest that cannot "
       "start, a parse that treats any output as a failure) would report a "
       "flawless sweep",
       "# The verbs\n", "# The verbs (comment touched by the sweep's control)\n",
       None)


# --------------------------------------------------------------------------- #
def sha256(path) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def purge_pycache() -> None:
    """🔴 Before EVERY mutant. CPython validates a cached module on source
    mtime-in-whole-SECONDS + size, so a same-length edit in the same second is
    invisible and the mutant is scored SURVIVED without ever executing."""
    for base, dirs, _files in os.walk(os.path.join(_ROOT, "scripts")):
        for d in list(dirs):
            if d == "__pycache__":
                shutil.rmtree(os.path.join(base, d), ignore_errors=True)
                dirs.remove(d)


def run_suite():
    """-> (failed_names, total, rc). Reads the CONTENT, never the exit code."""
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--tb=no", "-p",
         "no:cacheprovider", _TESTS],
        capture_output=True, text=True, cwd=_ROOT, env=env, timeout=900)
    out = proc.stdout + proc.stderr
    failed = set(re.findall(r"^FAILED [^:]+::(.+?)(?: - |\s*$)", out,
                            re.MULTILINE))
    m = re.search(r"(\d+) (?:passed|failed)", out)
    total = 0
    for n, _w in re.findall(r"(\d+) (passed|failed|error[s]?)", out):
        total += int(n)
    if "panic: test timed out" in out or "INTERNALERROR" in out:
        raise RuntimeError(f"harness fault:\n{out[-3000:]}")
    return failed, total, proc.returncode, out


def apply_mutant(mut) -> None:
    src = open(_SRC, encoding="utf-8").read()
    n = src.count(mut.find)
    if n != 1:
        raise AssertionError(
            f"patch site occurs {n} times, expected exactly 1:\n"
            f"  {mut.find[:120]!r}")
    open(_SRC, "w", encoding="utf-8").write(src.replace(mut.find, mut.repl, 1))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-k", dest="filter", default=None,
                    help="only mutants whose guard/description matches")
    args = ap.parse_args()

    original = open(_SRC, encoding="utf-8").read()
    baseline_sha = sha256(_SRC)

    print(f"source      : {_SRC}")
    print(f"sha256      : {baseline_sha}")
    print()

    # ---- BASELINE. An unmutated red suite makes every kill meaningless. -----
    purge_pycache()
    failed, total, _rc, out = run_suite()
    if failed:
        print(f"🔴 BASELINE IS NOT GREEN — {len(failed)} failing before any "
              f"mutation. Every 'KILLED' below would be unearned.")
        for f in sorted(failed):
            print(f"     {f}")
        return 1
    print(f"baseline    : {total} tests, all green")
    print()

    batch = [PC, NC] + [m for m in MUTANTS
                        if not args.filter
                        or args.filter.lower() in (m.guard + m.desc).lower()]

    rows, problems = [], []
    try:
        for i, mut in enumerate(batch):
            if mut is PC:
                num = "PC"
            elif mut is NC:
                num = "NC"
            else:
                num = str(i - 1)     # PC and NC occupy 0 and 1

            t0 = time.time()
            try:
                purge_pycache()
                apply_mutant(mut)
                failed, total, _rc, out = run_suite()
            finally:
                open(_SRC, "w", encoding="utf-8").write(original)
                assert sha256(_SRC) == baseline_sha, (
                    "🔴 RESTORE FAILED — the source is not byte-identical")
            purge_pycache()

            killed_by = sorted(f for f in failed if mut.killer and
                               mut.killer in f)
            blast = (len(failed) / total) if total else 1.0
            must_survive = mut is NC

            if must_survive:
                ok = not failed
                verdict = "SURVIVED (as required)" if ok else \
                          f"🔴 KILLED — a NULL edit must not fail anything"
            elif not failed:
                ok, verdict = False, "🔴 SURVIVED"
            elif not killed_by:
                ok = False
                verdict = (f"🔴 KILLED BY THE WRONG TEST ({len(failed)} red, "
                           f"none named {mut.killer})")
            elif blast > BLAST_LIMIT and not mut.wide:
                ok = False
                verdict = (f"🔴 NOT ISOLATED — {len(failed)}/{total} of the "
                           f"suite went red; the kill is unearned")
            else:
                ok = True
                verdict = f"KILLED by {killed_by[0]}"
                if mut.wide:
                    verdict += (f" (declared-wide blast: {len(failed)}/{total})")

            rows.append((num, mut.guard, mut.cls, mut.desc, verdict,
                         len(failed), total, time.time() - t0))
            if not ok:
                problems.append(num)
            print(f"[{num:>3}] {mut.guard:<8} {mut.cls:<18} {verdict}")
            if not ok:
                print(f"        {mut.desc}")
                if failed and not must_survive:
                    print(f"        red: {sorted(failed)[:6]}")
    finally:
        open(_SRC, "w", encoding="utf-8").write(original)
        final = sha256(_SRC)
        print()
        print(f"restored    : sha256 {final} "
              f"({'IDENTICAL' if final == baseline_sha else '🔴 DIFFERENT'})")
        assert final == baseline_sha

    print()
    print(f"{'#':>3}  {'guard':<8} {'class':<18} verdict")
    print("-" * 100)
    for num, guard, cls, desc, verdict, nf, total, secs in rows:
        print(f"{num:>3}  {guard:<8} {cls:<18} {verdict}")
        print(f"     {desc}")

    by_class = {}
    for _n, _g, cls, *_ in rows:
        by_class[cls] = by_class.get(cls, 0) + 1
    print()
    print("mutation CLASSES exercised: " +
          ", ".join(f"{k}={v}" for k, v in sorted(by_class.items())))
    print(f"mutants (excluding the two controls): {len(rows) - 2}")

    if problems:
        print(f"\n🔴 {len(problems)} PROBLEM(S): {problems}")
        return 1
    print("\nALL MUTANTS KILLED BY THEIR NAMED TEST; BOTH CONTROLS BEHAVED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
