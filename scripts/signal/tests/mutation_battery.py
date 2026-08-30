#!/usr/bin/env python3
"""The Signal suite's mutation battery — the mutants, and a runner for them.

    python3 scripts/signal/tests/mutation_battery.py            # run them all
    python3 scripts/signal/tests/mutation_battery.py --list      # show the ledger
    python3 scripts/signal/tests/mutation_battery.py --only A1 M4

Exit codes: 0 every mutant reached its EXPECTED verdict · 1 at least one did not
(SURVIVED, ANCHOR-MISS, KILLED-WRONG-REASON — or, for an `equivalent=True` row,
KILLED) · 2 refused to start (dirty tree, an unreadable `git status`, a red
baseline, a named killer that did not run) · 3 a mutant was left in the tree, or
that could not be determined.

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

MEN = "scripts/signal/_mentions.py"
SKILL = "claude/skills/signal/SKILL.md"

SUITE_EXCL = "scripts/signal/tests/test_group_exclusions.py"
SUITE_MENT = "scripts/signal/tests/test_mentions.py"
SUITE_SKILL = "scripts/signal/tests/test_skill_doc.py"
SUITE_IMAGE = "scripts/signal/tests/test_image_deps.py"
SUITE_LIVE = "scripts/signal/tests/test_liveness.py"


class Mutant:
    """One way to break the code, and the ONE test that must notice.

    `equivalent=True` INVERTS the expected verdict: the mutation is argued to be
    incapable of changing any observable behaviour, so SURVIVED is the pass and
    KILLED is the finding — it would mean the argument is wrong and the two
    forms are not interchangeable after all.

    🔴 WHY THESE ARE RECORDED RATHER THAN DELETED. An equivalent mutant is the
    one row a reader is most likely to re-derive from scratch next round, and
    the argument for equivalence is usually a premise about the code around it
    ("`re` folds one code point to one"). Keeping the row keeps the premise
    checkable: if that premise ever stops holding, this stops SURVIVING and the
    runner says so. Deleting the row loses both the finding and the reason.
    🔴 AND IT MUST NOT MAKE THE RUNNER PERMANENTLY RED — a gate that is always
    failing is one everybody learns to click through, so `equivalent` is what
    keeps a legitimate survivor out of the failure set instead of parking a
    known-red row in it.
    """

    def __init__(self, mid, why, path, old, new, killer, suite, *,
                 equivalent: bool = False):
        self.id, self.why, self.path = mid, why, path
        self.old, self.new, self.killer, self.suite = old, new, killer, suite
        self.equivalent = equivalent

    @property
    def expected(self) -> str:
        return "SURVIVED" if self.equivalent else "KILLED"


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
           'want_choices="approve conversations draft drafts health mute muted reconcile run search send unapprove unmute "',
           'want_choices="approve conversations draft drafts health reconcile run search send "',
           "test_the_build_control_lists_EXACTLY_the_CLI_subcommands", SUITE_IMAGE),

    # ------------------------------------------------------------------ #
    # Outbound GROUP drafting (#686). Before this, `draft_message()` sent every
    # recipient through `upsert_contact()`: a group draft stored `group_id`
    # NULL — invisible to `not_excluded()`, which keys on it — and minted a
    # PHANTOM CONTACT whose phone_number was the group address.
    # ------------------------------------------------------------------ #
    Mutant("GD1", "the shipped defect, in its narrowest form: the INSERT stops carrying "
                  "`group_id`. The draft still resolves the group, so nothing about the "
                  "address looks wrong — the row is simply unlinked, and every read that "
                  "believes it is filtering returns a MUTED group's draft in full.",
           DB,
           "                (ts, source_id, dest_id, group_row_id, body, STATE_PENDING,",
           "                (ts, source_id, dest_id, None, body, STATE_PENDING,",
           "test_a_muted_group_draft_is_hidden_from_every_filtered_read", SUITE_EXCL),

    Mutant("GD2", "the phantom contact returns: a group recipient ALSO resolved through "
                  "`upsert_contact`. `group_id` is still set, so the mute keeps working "
                  "and the mute test stays GREEN — only the contacts table shows it. A "
                  "battery that checked mute alone would score this SURVIVED.",
           DB,
           "            gid = _group_address_to_id(recipient)",
           "            dest_id = self.upsert_contact(phone_number=recipient)\n"
           "            gid = _group_address_to_id(recipient)",
           "test_a_group_draft_is_LINKED_to_the_group_row_not_a_contact", SUITE_EXCL),

    Mutant("GD3", "`get_draft` stops deriving the recipient from the group row. The "
                  "phantom contact was LOAD-BEARING — it was where the send address was "
                  "read from — so removing it without this rewiring addresses every "
                  "group send to None.",
           DB,
           "            if group_signal_id is not None:", "            if False:",
           "test_the_SEND_recipient_is_derived_from_the_group_row_not_a_contact",
           SUITE_EXCL),

    Mutant("GD4", "the group address SINGLE-encodes. `/v2/send` takes `group.` + "
                  "base64(base64(raw)) — a DOUBLE encoding — and the single form is "
                  "well-formed base64 that decodes to something plausible, so only a "
                  "literal anchor can tell them apart.",
           DB,
           "    return GROUP_ADDRESS_PREFIX + base64.b64encode(\n"
           "        base64.b64encode(bytes(raw))).decode()",
           "    return GROUP_ADDRESS_PREFIX + base64.b64encode(bytes(raw)).decode()",
           "test_the_group_address_encoding_round_trips_against_a_LITERAL", SUITE_EXCL),

    Mutant("GD5", "the group-address decoder drops the STRICT reader, so a display name "
                  "or a truncated paste resolves to some other bytes and "
                  "`upsert_group` CREATES that group — a send into the void, reported "
                  "as success.",
           DB,
           "    return _decode_internal_id(internal)",
           "    return base64.b64decode(internal)",
           "test_a_MALFORMED_group_address_is_refused_and_stores_nothing", SUITE_EXCL),

    Mutant("GD6", "🔴 [audit] a BARE `internal_id` — the form `mute` takes — is accepted "
                  "instead of refused, falling through to `upsert_contact()`. This is "
                  "the phantom-contact defect by the back door, and SKILL.md documented "
                  "the opposite. Found by a delta re-audit AFTER the fix was called "
                  "complete.",
           DB,
           "        elif _looks_like_bare_group_internal_id(recipient):",
           "        elif False:",
           "test_a_BARE_internal_id_is_REFUSED_not_turned_into_a_phantom_contact",
           SUITE_EXCL),

    Mutant("GD7", "the bare-internal-id refusal WIDENED to swallow real recipients — the "
                  "risk the refusal itself carries. Dropping the length check makes "
                  "`Team` (3 bytes) and any short base64 a 'group id', so ordinary "
                  "contacts stop being draftable.",
           CON,
           "    if len(raw) not in (16, 32):",
           "    if False:",
           "test_no_REAL_recipient_shape_is_mistaken_for_a_group_id", SUITE_EXCL),

    Mutant("GD8", "`draft` mints a previously-unseen group SILENTLY. A canonically "
                  "encoded but WRONG id decodes perfectly and cannot be rejected, so the "
                  "stderr warning is the ONLY signal that the message is going nowhere — "
                  "the silent-zero shape `mute` was hardened against.",
           DB,
           "            group_created = not self.group_exists(gid)",
           "            group_created = False",
           "test_drafting_to_an_UNSEEN_group_WARNS_loudly_on_stderr", SUITE_EXCL),

    Mutant("GD9", "the same warning fired UNCONDITIONALLY. A warning on every draft is "
                  "one an operator learns to ignore, which is indistinguishable from no "
                  "warning at all — so the SILENT case needs its own mutant.",
           DB,
           "            group_created = not self.group_exists(gid)",
           "            group_created = True",
           "test_drafting_to_a_KNOWN_group_is_SILENT", SUITE_EXCL),

    Mutant("GD10", "the `draft` CLI loses its refusal branch, so a bad `--to` escapes as "
                   "an uncaught traceback and exit 1 — indistinguishable, to a caller, "
                   "from the interpreter dying for an unrelated reason. `mute` and "
                   "`send` both exit 3.",
            CON,
            # 🔴 Anchored on TWO lines: `except ValueError as exc:` alone now
            # matches twice (the `mute` branch has one too), and the battery's
            # own exactly-once guard caught it. Keep the comment line in the
            # anchor, or this silently re-ambiguates the day another branch
            # grows a ValueError handler.
            "            except ValueError as exc:\n"
            "                # 🔴 EXIT 3, like every sibling.",
            "            except ZeroDivisionError as exc:\n"
            "                # 🔴 EXIT 3, like every sibling.",
            "test_draft_REFUSES_a_bad_recipient_with_exit_3_like_its_siblings",
            SUITE_EXCL),

    # ------------------------------------------------------------------ #
    # The mute LEDGER and its behavioural probes (#686). The ledger says which
    # reads filter; these check the ledger cannot drift from the code.
    # ------------------------------------------------------------------ #
    Mutant("LP1", "a read method that is in NEITHER ledger. The seam this whole ledger "
                  "exists for: a new read surface added without the filter.",
           DB,
           "    def commit(self) -> None:",
           "    def list_archived_messages(self):\n"
           "        with self._c.cursor() as cur:\n"
           '            cur.execute("SELECT m.id, m.body FROM signal.messages m")\n'
           "            return cur.fetchall()\n\n"
           "    def commit(self) -> None:",
           "test_the_two_ledgers_PARTITION_every_read_of_signal_messages", SUITE_EXCL),

    Mutant("LP2", "🔴 `get_message` declared EXEMPT while it is still FILTERED, so the "
                  "ledger now says the mute does not cover the id route. The two sets' "
                  "UNION is IDENTICAL after this — the union check that preceded the "
                  "partition scored exactly this class GREEN. Only a DISJOINTNESS check, "
                  "plus comparing the ledger against what the CODE does, can see it.",
           SUITE_EXCL,
           "EXEMPT_READS = {\n",
           'EXEMPT_READS = {\n    "get_message": "declared exempt while still calling the '
           'predicate — the ledger and the code now disagree",\n',
           "test_the_two_ledgers_PARTITION_every_read_of_signal_messages", SUITE_EXCL),

    Mutant("LP7", "the mute filter removed from `list_conversations` — scored here against "
                  "the LEDGER rather than the behavioural test. Same edit as M2, "
                  "deliberately: M2 proves a muted group reappears, this proves the "
                  "ledger notices the code drifted away from what it claims. A "
                  "structural check that only ever agreed with the behavioural one would "
                  "be decorative, and nothing else in the file distinguishes them.",
           DB,
           "                    WHERE {not_excluded('m')}\n", "",
           "test_the_two_ledgers_PARTITION_every_read_of_signal_messages", SUITE_EXCL),

    Mutant("LP3", "the predicate detector reverted to a SUBSTRING over source text. "
                  "`get_draft` does NOT filter but its docstring EXPLAINS the mute "
                  "predicate, so the substring form certified it as filtering — a guard "
                  "satisfiable by PROSE. Measured on the shipped code, not imagined.",
           SUITE_EXCL,
           "    return any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)\n"
           '               and n.func.id == "not_excluded" for n in ast.walk(node))',
           '    return "not_excluded(" in inspect.getsource(fn)',
           "test_the_predicate_detector_is_NOT_walked_by_a_DOCSTRING_mention", SUITE_EXCL),

    Mutant("LP4", "`get_draft` loses `send_state IS NOT NULL`, which is the PREMISE its "
                  "mute exemption rests on: without it the method also returns "
                  "device-sync ECHOES, which carry OTHER PEOPLE's bodies from muted "
                  "groups. The exemption silently becomes a real leak.",
           DB,
           "WHERE m.id = %s AND m.is_outbound AND m.send_state IS NOT NULL",
           "WHERE m.id = %s AND m.is_outbound",
           "test_the_draft_exemptions_PREMISE_is_still_in_the_code", SUITE_EXCL),

    Mutant("LP5", "🔴 [audit] two behavioural probes SWAPPED — `get_message` given the "
                  "`search` probe's body. The probe KEY set is unchanged, so "
                  "`set(_MUTE_PROBES) == FILTERED_READS` cannot see it and the suite "
                  "stayed fully green while `get_message` had NO behavioural coverage of "
                  "a group draft. Found by an audit; the same set-invariant-under-a-swap "
                  "shape as LP2.",
           SUITE_EXCL,
           '    "get_message": lambda db, draft, body: [\n'
           '        r for r in [db.get_message(draft["id"])] if r],\n',
           '    "get_message": lambda db, draft, body: [\n'
           '        r for r in db.search(body) if r["id"] == draft["id"]],\n',
           "test_each_MUTE_PROBE_actually_calls_the_method_it_is_keyed_under", SUITE_EXCL),

    Mutant("LP6", "🔴 [audit] the recording proxy BLINDED — it still delegates, so every "
                  "probe returns real rows and every downstream assertion passes, while "
                  "`called` stays empty. This is what the old, vacuous negative control "
                  "could not see: it PASSED under this mutant.",
           SUITE_EXCL,
           "            def recording(*a, **kw):\n"
           "                self.called.append(name)\n"
           "                return attr(*a, **kw)\n"
           "            return recording",
           "            return attr",
           "test_each_MUTE_PROBE_actually_calls_the_method_it_is_keyed_under", SUITE_EXCL),

    # ------------------------------------------------------------------ #
    # MENTIONS (#1121, round-3 and round-4 delta audits). Every mutant here is a
    # way the fixes revert or over-correct. Two shapes recur and both are
    # represented deliberately: a SECOND matching rule that agrees with the
    # first only on ASCII (MEN1/MEN2), and an identity check that is either
    # too narrow (MEN5/MEN7/MEN11) or too wide (MEN6/MEN10). The fixture pairs
    # are chosen so ASCII CANNOT SEE the bug — `ß`/`ss` and `İstanbul`/
    # `istanbul` — which is exactly why the round-2 fix passed its own battery.
    #
    # 🔴 MEN10 IS THE ROW TO READ FIRST. Every other mutant here breaks a
    # REFUSAL; MEN10 is the round-3 fix as shipped, and it SENDS. An identity
    # rule has two failure directions and only one of them is loud — which is
    # why the too-narrow and too-wide mutants come in pairs whose killers cannot
    # see each other's defect.
    # ------------------------------------------------------------------ #
    Mutant("MEN1", "the cursor's equivalence goes back to `casefold()` — a SECOND "
                   "matching rule. Agrees with `re.IGNORECASE` on ASCII and disagrees "
                   "in BOTH directions on unicode, which is how round-2's fix shipped "
                   "with both arms live.",
           MEN,
           "    return _whole(a, b) and _whole(b, a)",
           "    return a.casefold() == b.casefold()",
           "test_the_cursor_does_NOT_merge_two_needles_the_MATCHER_keeps_APART",
           SUITE_MENT),

    Mutant("MEN2", "the equivalence goes RAW — exact string identity. The OPPOSITE "
                   "over-correction to MEN1: nothing is ever merged, so `@İstanbul` "
                   "and `@istanbul` both start at 0 and claim the same span. Needed "
                   "separately because MEN1's killer stays GREEN under this.",
           MEN,
           "    return _whole(a, b) and _whole(b, a)",
           "    return a == b",
           "test_the_cursor_DOES_merge_two_needles_the_MATCHER_treats_as_ONE",
           SUITE_MENT),

    Mutant("MEN3", "🔴 EQUIVALENT MUTANT, recorded rather than counted as a kill. "
                   "`_whole()` drops its end-of-string check. It cannot change any "
                   "answer: `re.escape` folds one code point to one, so a match "
                   "consumes exactly `len(pattern)`, and BOTH directions matching "
                   "already forces the two needles to be the same length. Kept in the "
                   "ledger so the next round does not re-derive it — and so that if "
                   "the matcher ever grows a variable-width element, this row turns "
                   "into a real mutant and its SURVIVED verdict becomes a finding.",
           MEN,
           "        return match is not None and match.end() == len(text)",
           "        return match is not None",
           "test_the_cursor_DOES_merge_two_needles_the_MATCHER_treats_as_ONE",
           SUITE_MENT, equivalent=True),

    Mutant("MEN4", "the cursor stops ADVANCING past the match it just claimed. Every "
                   "repeat of one needle then re-claims the first occurrence, which "
                   "the overlap guard refuses — the round-2 defect by another route.",
           MEN,
           "        slot[1] = idx + len(needle)",
           "        slot[1] = idx",
           "test_the_ASCII_case_the_round_2_fix_was_written_for_still_works",
           SUITE_MENT),

    Mutant("MEN5", "the collision rule reverts to comparing RAW author strings, so one "
                   "person holding a real row AND a durable phone-only placeholder "
                   "reads as two people and their own longer name vetoes their own "
                   "ping. The shipped round-2 defect, in its narrowest form.",
           MEN,
           "            if _norm_member(_contact_author(contact)) not in mine",
           "            if _contact_author(contact) != author",
           "test_a_person_in_TWO_contact_rows_does_not_veto_their_OWN_ping",
           SUITE_MENT),

    Mutant("MEN6", "the identity check goes VETO-BLIND — every other member's longer "
                   "name is dropped from `avoid`. The over-correction MEN5's killer "
                   "cannot see: it passes under this, while round-2's F2 silently "
                   "reopens and `@Ann` lands on `@Ann Smith` again.",
           MEN,
           "            if _norm_member(_contact_author(contact)) not in mine",
           "            if False",
           "test_a_DIFFERENT_persons_longer_name_still_vetoes", SUITE_MENT),

    Mutant("MEN7", "`_identity_groups()` unions NOTHING, so every row is its own "
                   "person. Same observable as MEN5 from a different SITE — the "
                   "builder rather than the lookup — because a union that silently "
                   "stops merging is not visible at the call site at all.",
           MEN,
           "            if not (row_a.get(\"is_placeholder\") or row_b.get(\"is_placeholder\")):\n"
           "                continue",
           "            if True:\n"
           "                continue",
           "test_a_person_in_TWO_contact_rows_does_not_veto_their_OWN_ping",
           SUITE_MENT),

    Mutant("MEN10", "🔴 [round-4 audit F-A] the identity union DROPS its "
                    "`is_placeholder` gate, so any two rows sharing an identifier "
                    "merge — which is what round-3's own fix shipped. `phone_number` "
                    "is TEXT with no UNIQUE constraint and `_promote_placeholder()` "
                    "only touches placeholder rows, so two REAL rows can durably hold "
                    "one number (number recycling / a number change). Merging them "
                    "dropped person B's veto and PINGED person A on a body reading "
                    "@Ann Smith — the first WRONG SEND in four audit rounds, where "
                    "every other defect here was a refusal. The OPPOSITE "
                    "over-correction to MEN7, which MEN7's killer cannot see: it "
                    "passes under this.",
           MEN,
           "            if not (row_a.get(\"is_placeholder\") or row_b.get(\"is_placeholder\")):\n"
           "                continue",
           "            if False:\n"
           "                continue",
           "test_two_REAL_rows_sharing_a_number_are_TWO_people_not_one",
           SUITE_MENT),

    Mutant("MEN11", "🔴 [round-4 audit F-B] `_resolve_one()` de-duplicates by the "
                    "ROW's author string again instead of by identity — the second "
                    "site of the predicate `_identity_groups()` owns. One person "
                    "holding a real row and a durable placeholder row is then refused "
                    "as an AMBIGUITY, and the remedy the message prints is half wrong: "
                    "one of the two ids is the synthetic placeholder uuid, which is "
                    "not in `member_set`, so following the advice hits "
                    "MentionNotAMember.",
           MEN,
           "        group = identity.get(key) or frozenset({key})",
           "        group = frozenset({key})",
           "test_one_person_with_a_real_AND_a_placeholder_row_is_NOT_ambiguous",
           SUITE_MENT),

    Mutant("MEN12", "`NAME_HINT_MAX` shrinks 5 -> 4. Found SURVIVING all 908 tests by "
                    "the round-4 audit (F-C): the only assertion on the truncation "
                    "derived its expectation from `_mentions.NAME_HINT_MAX` itself, so "
                    "it agreed with any value. A cap on how much of a group roster a "
                    "token-free `draft` refusal will enumerate is a privacy constant; "
                    "an uncovered one drifts in the other direction just as quietly.",
           MEN,
           "NAME_HINT_MAX = 5",
           "NAME_HINT_MAX = 4",
           "test_the_name_hint_cap_is_a_LITERAL_five_not_whatever_the_module_says",
           SUITE_MENT),

    Mutant("MEN13", "🔴 [round-5 audit F-A] the GROUP-LEVEL invariant is deleted, "
                    "leaving only round-4's per-pair `is_placeholder` gate. That gate "
                    "cannot survive transitivity: the union-find joins PATHS, so with "
                    "three rows on one number — real A, placeholder P, real C — the "
                    "direct edge A—C is blocked and A—P—C unions anyway. Two real "
                    "people are one identity again and `--mention Ann` pings A under "
                    "a body reading @Ann Smith. This is the shipped 707412e6 "
                    "behaviour, and the reason the fix is a check on the FORMED "
                    "GROUP rather than a third revision of the pair condition. MEN10's "
                    "killer cannot see it — that fixture has only two rows.",
           MEN,
           "        if len(members_) > 1:",
           "        if False:",
           "test_a_PLACEHOLDER_bridging_TWO_REAL_rows_is_REFUSED_not_guessed",
           SUITE_MENT),

    Mutant("MEN14", "the group invariant's BOUNDARY moves 1 -> 2, so a group holding "
                    "exactly two real rows — the whole failure mode — passes while a "
                    "three-real-row group still refuses. The narrowest expression that "
                    "can be wrong here, and the one MEN13 cannot distinguish: a guard "
                    "deleted and a guard off by one look identical from the call site, "
                    "and only this one proves the threshold is where the harm starts "
                    "rather than one past it.",
           MEN,
           "        if len(members_) > 1:",
           "        if len(members_) > 2:",
           "test_a_PLACEHOLDER_bridging_TWO_REAL_rows_is_REFUSED_not_guessed",
           SUITE_MENT),

    Mutant("MEN8", "`utf16_span()` stops forwarding `avoid` — the exported wrapper "
                   "silently implements only the `(?!\\w)` half while its docstring "
                   "promises one matching rule. It has NO production caller, so only "
                   "a direct test can see it; the next caller inherits the gap.",
           MEN,
           "    _, start, length = find_span(body, needle, from_index=from_index, avoid=avoid)",
           "    _, start, length = find_span(body, needle, from_index=from_index)",
           "test_utf16_span_FORWARDS_avoid_so_it_really_is_one_matching_rule",
           SUITE_MENT),

    Mutant("MEN9", "🔴 the drain runbook's steps SWAPPED — the SELECT before the "
                   "deploy, i.e. exactly the unexecutable order the test is named "
                   "for. Measured: this SURVIVED the pre-round-3 assertion, which "
                   "indexed `deploy` over the whole section and found it in the "
                   "explanatory paragraph above the list. The mutant is what proves "
                   "the surviving ordering guard is REACHABLE now that the walkable "
                   "phrase assertion beside it is gone.",
           SKILL,
           "   1. Deploy, and let the consumer start (that is what runs `ensure_schema()`\n"
           "      and adds the column).\n"
           "   2. Find them:\n"
           "\n"
           "      ```sql\n"
           "      SELECT id, send_state, approval_ref FROM signal.messages\n"
           "       WHERE send_state = 'approved' AND approved_digest IS NULL;\n"
           "      ```\n",
           "   1. Find them:\n"
           "\n"
           "      ```sql\n"
           "      SELECT id, send_state, approval_ref FROM signal.messages\n"
           "       WHERE send_state = 'approved' AND approved_digest IS NULL;\n"
           "      ```\n"
           "\n"
           "   2. Deploy, and let the consumer start (that is what runs `ensure_schema()`\n"
           "      and adds the column).\n",
           "test_the_pre_digest_drain_procedure_is_ORDERED_so_it_can_be_RUN",
           SUITE_SKILL),
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


def headline(results) -> str:
    """The `N/M killed` line. PURE — extracted so it is table-testable.

    🔴 IT USED TO DOUBLE-SUBTRACT (round-4 audit F-D). The count was
    `len(results) - len(bad) - len(equiv)`, and a row that is BOTH `equivalent`
    AND bad — an "equivalent" mutant that got KILLED, i.e. precisely the finding
    the flag exists to surface — was charged to both subtrahends. Observed live
    as `-1/0 killed`. The exit code and the `!!` lines were correct throughout;
    only the number a reader scans FIRST was wrong, which is the worst place for
    one, because a negative headline reads as a glitch in the tool rather than
    as a finding about the ledger.

    Counted, not subtracted: the numerator is the real mutants that met their
    expectation, the denominator the real mutants. Equivalent rows are reported
    separately and never enter either.
    """
    real = [r for r in results if not r[0].equivalent]
    equiv = [r for r in results if r[0].equivalent]
    killed = [r for r in real if r[1] == r[0].expected]
    return (f"{len(killed)}/{len(real)} killed by their NAMED test"
            + (f"  ({len(equiv)} EQUIVALENT, expected to survive)"
               if equiv else ""))


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
            tag = "  [EQUIVALENT — expected to SURVIVE]" if m.equivalent else ""
            print(f"{m.id:4} {m.path:30} -> {m.killer}{flag}{tag}\n     {m.why}\n")
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
    # 🔴 COMPARED AGAINST EACH MUTANT'S OWN EXPECTED VERDICT, not against the
    # constant "KILLED": an `equivalent=True` row PASSES by surviving, and FAILS
    # loudly if something kills it, because that would disprove the equivalence
    # argument recorded in its `why`.
    bad = [r for r in results if r[1] != r[0].expected]
    equiv = [r for r in results if r[0].equivalent]
    print("\n" + headline(results))
    for m, verdict, detail in bad:
        expected = f"expected {m.expected}" if m.equivalent else ""
        print(f"  !! {verdict}: {m.id} — {detail} {expected}".rstrip(),
              file=sys.stderr)

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
