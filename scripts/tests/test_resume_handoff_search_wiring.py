"""The `/resume` skill must actually CALL `scripts/lib/handoff_search.py`.

WHY THIS EXISTS
---------------
The section index shipped (`devrc#1209`) and for the whole of its life NOTHING
called it: `git grep handoff_search` found the module itself, its sibling writer,
`scripts/README.md` and a shebang test. An index with no reader is a cost with no
benefit, and the failure is silent -- every `/resume` simply went on re-deriving
findings the corpus already held.

So this module pins the WIRING, not the existence of a file. Three claims, and
each is derived from the tool rather than restated:

  1. the prescribed command is pinned as a WHOLE NORMALISED STRING, so a future
     edit that drops `--offline` -- which would silently start requiring a live
     Postgres that has never once been exercised -- goes red instead of shipping;
  2. every flag the skill prescribes is a flag the tool's parser ACCEPTS, probed
     behaviourally by running `main()` in-process, so a rename in the tool breaks
     the test rather than leaving the skill prescribing a dead command;
  3. every non-answer exit code the module can return is NAMED in the block, with
     the ledger DERIVED from `handoff_search.EXIT_CODES` /
     `SCOPE_REASON_EXIT_CODES`. A consumer that swallows the difference between
     "the corpus is silent" and "the index is empty" reintroduces the exact
     silent zero the tool was built to prevent, and a hand-copied list of codes
     would go stale the first time a sixth zero is carved out.

🔴 WHY THE PROSE IS PINNED WHOLE AND NOT BY KEYWORD. `claude/RULES.md`: "when the
artifact under test IS prose, a guard on WORDS is walkable by REWORDING -- pin
the WHOLE normalised string." A substring pin on `--offline` is satisfied by the
flag appearing anywhere in a 40 KB file, including in a sentence saying not to
use it. So the command line is compared in full after whitespace normalisation,
and every contract assertion runs against a SLICE of the file delimited by two
pinned sentinels -- a hit elsewhere in the skill cannot satisfy them.

⚠ THE COST IS EXPLICIT: a cosmetic reword of this block fails this test. That is
the trade `RULES.md` names -- pay it, for a machine-readable claim.

This module lives in `scripts/tests`, which is in `HERMETIC_TARGETS` in
`scripts/run-tests.sh`, so it runs in `nix build .#checks.x86_64-linux.pytests`.
It opens no database, reaches no network and shells out to nothing.
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
RESUME_SKILL = REPO_ROOT / "claude" / "skills" / "resume" / "SKILL.md"
SEARCH_MODULE = REPO_ROOT / "scripts" / "lib" / "handoff_search.py"

# The two sentinels that delimit the wiring block inside the skill body.
#
# 🔴 THESE EXIST TO SCOPE THE CONTRACT ASSERTIONS, not as decoration. Without
# them every "the skill names rc 4" check is satisfied by the words appearing
# anywhere in a 40 KB file -- and this file already discusses exit code 4 twice,
# for `cairn` and for `subsystem_recall.py`, in a completely different sense.
# `claude/RULES.md`: "verified in isolation is the new vacuous green -- the
# defect lives in the SEAM nobody owns."
BLOCK_OPEN = "**And ask whether ANOTHER doc already ruled it out**"
BLOCK_CLOSE = "carry on with the item."

# 🔴 THE COMMAND, PINNED WHOLE. Normalised only for internal whitespace runs, so
# a reflow across lines is tolerated and a changed flag is not.
EXPECTED_COMMAND = (
    "python3 ~/workspace/devrc/scripts/lib/handoff_search.py --offline "
    '--query "<the open item, in its own words>" --limit 3'
)


def _load_search_module():
    """Import `handoff_search` by path -- `scripts/lib` is not a package."""
    spec = importlib.util.spec_from_file_location("handoff_search", SEARCH_MODULE)
    assert spec and spec.loader, f"cannot load {SEARCH_MODULE}"
    mod = importlib.util.module_from_spec(spec)
    # `handoff_search` imports its sibling `handoff_index` by bare name.
    lib = str(SEARCH_MODULE.parent)
    if lib not in sys.path:
        sys.path.insert(0, lib)
    sys.modules["handoff_search"] = mod
    spec.loader.exec_module(mod)
    return mod


def _skill_text() -> str:
    return RESUME_SKILL.read_text(encoding="utf-8")


def _normalise(text: str) -> str:
    """Collapse every whitespace run to one space, and strip."""
    return re.sub(r"\s+", " ", text).strip()


def wiring_block(text: str) -> str:
    """The slice of the skill between the two sentinels, sentinels included.

    Raises with a message naming which sentinel is missing -- an unscoped
    assertion is how the contract checks below would go vacuous.
    """
    start = text.find(BLOCK_OPEN)
    assert start != -1, (
        f"{RESUME_SKILL} no longer contains the wiring block's opening sentinel "
        f"{BLOCK_OPEN!r}. `scripts/lib/handoff_search.py` has no other caller in "
        "the repo, so removing this block un-wires the index entirely. If the "
        "block moved or was reworded, update BLOCK_OPEN here in the SAME commit."
    )
    end = text.find(BLOCK_CLOSE, start)
    assert end != -1, (
        f"{RESUME_SKILL} contains the wiring block's opening sentinel but not its "
        f"closing one ({BLOCK_CLOSE!r}) after it. That sentence is the "
        "NON-BLOCKING instruction -- without it a caller that gets a non-zero "
        "exit has no rule telling it to print the stderr line and carry on."
    )
    return text[start : end + len(BLOCK_CLOSE)]


def _block() -> str:
    return wiring_block(_skill_text())


def test_the_resume_skill_is_where_it_is_expected():
    """Guard the guard: a moved SKILL.md must not silently pass every test."""
    assert RESUME_SKILL.is_file(), (
        f"{RESUME_SKILL} not found -- every assertion below would error rather "
        "than report the wiring. If the resume skill moved, update RESUME_SKILL."
    )
    assert SEARCH_MODULE.is_file(), (
        f"{SEARCH_MODULE} not found -- the skill would be prescribing a command "
        "that cannot run. If the tool moved, update SEARCH_MODULE and the "
        "command pinned in the skill, in the same commit."
    )


def test_the_skill_prescribes_the_offline_command_verbatim():
    """🔴 THE SHAPE PIN, and `--offline` is the load-bearing token in it.

    Without `--offline` the tool falls through to the Postgres backend, and that
    would turn a working retrieval step into a connection error at the worst
    moment in a session. A substring check for `handoff_search.py` would not see
    that edit; a whole-string comparison does.

    🔴 THE REASON CHANGED ON 2026-09-04 AND IS NOW SHARPER, NOT WEAKER. The old
    rationale was "no test has ever exercised that path, and its timer ships
    disarmed (`enableHandoffIndexSync = false`)". The timer is now ARMED and the
    live path HAS been exercised by hand (4647 rows written, `backend=postgres`
    answering), so that half is retired. What replaces it is stronger, because it
    does not depend on the index being empty: reaching Postgres needs a DSN read
    via `kubectl`, and this repo leaves `KUBECONFIG` UNSET on purpose so a bare
    `kubectl` cannot hit prod. A `/resume` session does not export it, so the
    non-offline path raises `CalledProcessError` before it can answer -- measured,
    not argued. `--offline` needs no cluster, no port-forward and no secret.
    """
    block = _block()
    normalised = _normalise(block)
    assert _normalise(EXPECTED_COMMAND) in normalised, (
        "the /resume wiring block no longer prescribes the exact command.\n"
        f"  expected: {_normalise(EXPECTED_COMMAND)}\n"
        f"  block:    {normalised}\n"
        "🔴 If you removed `--offline`, STOP: the database path has never been "
        "executed by any test and the sync unit ships disarmed. If you changed "
        "the command deliberately, update EXPECTED_COMMAND in the same commit."
    )


def test_every_flag_the_skill_prescribes_is_one_the_TOOL_accepts():
    """🔴 THE SEAM. The skill and the tool are two artifacts; each is green alone.

    Pinning the command string proves the skill says `--offline`; it proves
    nothing about whether `handoff_search.py` still has such a flag. So probe the
    parser BEHAVIOURALLY -- run `main()` with the prescribed flags plus a
    `--limit` the module rejects AFTER parsing, and read the usage exit code 2.
    Reaching that return means argparse accepted every flag; an unknown flag
    would `SystemExit` out of `parse_args` first.

    Nothing is searched: `--limit 0` is refused before any store is built, so no
    repo is walked, no database is opened and no network is touched.
    """
    mod = _load_search_module()
    flags = sorted(set(re.findall(r"--[a-z][a-z-]+", EXPECTED_COMMAND)))
    # 🔴 The argv below is written out by hand, so this equality is what keeps it
    # honest: add a flag to the prescribed command and this fails HERE, naming
    # the flag, instead of the probe quietly continuing to test the old three.
    assert flags == ["--limit", "--offline", "--query"], (
        f"the prescribed command's flags changed to {flags}. Extend the argv "
        "built below to pass the new flag, so the probe still proves the parser "
        "accepts everything /resume tells a reader to type."
    )

    argv = ["--query", "x", "--offline", "--limit", "0"]
    rc = mod.main(argv)
    assert rc == 2, (
        f"`handoff_search.main({argv})` returned {rc}, not the usage code 2 the "
        "`--limit` bound is documented to return. Either a flag the /resume "
        "block prescribes no longer exists, or the bound moved -- read the "
        "block and the module together before changing either."
    )

    # 🔴 NEGATIVE CONTROL for the probe itself: a flag the parser does NOT know
    # must blow up, otherwise `rc == 2` above would be evidence about nothing.
    # argparse exits 2 for an unknown option, which is the SAME number -- so the
    # control asserts the SystemExit, not the code.
    with pytest.raises(SystemExit):
        mod.main(["--query", "x", "--offline", "--no-such-flag"])


def test_the_block_names_every_NON_ANSWER_exit_code_the_tool_can_return():
    """🔴 THE SILENT-ZERO CONTRACT, DERIVED -- not a hand-copied list.

    `handoff_search` exists because a zero has five causes and only one of them
    is an answer. A consumer that prints the hits and drops the distinction hands
    the reader "the corpus does not say that" when the truth was "the index is
    empty" or "your filter selected no rows". So the block must NAME each
    non-zero code, and the ledger is read out of the module: carve out a sixth
    zero and this goes red until `/resume` is taught about it.
    """
    mod = _load_search_module()
    codes = set(mod.EXIT_CODES.values()) | set(mod.SCOPE_REASON_EXIT_CODES.values())
    assert codes, "the module declares no non-answer exit codes -- ledger moved?"
    block = _block()
    missing = sorted(c for c in codes if f"**{c}**" not in block)
    assert not missing, (
        f"the /resume wiring block does not name exit code(s) {missing}.\n"
        f"  the module can return: {sorted(codes)}\n"
        "Each is a NON-answer -- an empty index, an empty filter scope, a corpus "
        "that could not be measured, or repos that derived zero docs. A block "
        "that lists only some of them teaches the reader to read the rest as "
        "'the corpus is silent', which is the silent zero this tool exists to "
        "prevent. Name the new code in claude/skills/resume/SKILL.md."
    )


def test_the_block_carries_the_recall_posture_and_the_scope_LITERAL():
    """The two things the tool prints on EVERY response must survive the wiring.

    `indexed_docs=` is derived from the module, not restated: it is the literal
    the renderer emits beside every outcome, and it is what makes a zero
    readable. The recall posture is the same claim `cairn recall` carries four
    steps earlier -- one provenance rule, so a caller cannot learn it for one
    surface and lose it for the other.
    """
    source = SEARCH_MODULE.read_text(encoding="utf-8")
    assert "indexed_docs=" in source, (
        "`handoff_search.py` no longer emits `indexed_docs=` -- the block below "
        "would be pinning a literal the tool does not print"
    )
    block = _block()
    for literal, why in (
        ("indexed_docs=", "the scope literal that makes a zero readable"),
        ("POINTER TO VERIFY", "the recall posture -- results are not live readings"),
        ("--offline", "the flag that keeps this working with no database"),
        ("Non-blocking", "the rule that a non-zero must not stop the resume"),
    ):
        assert literal in block, (
            f"the /resume wiring block no longer carries {literal!r} ({why}). "
            "Do not drop it to save bytes -- claude/skills/resume/SKILL.md has "
            "no byte ceiling, and this is the honesty half of the wiring."
        )


def test_the_sentinels_can_report_a_missing_block(tmp_path):
    """🔴 NEGATIVE CONTROL for `wiring_block` itself.

    On a wired tree the extractor never takes its failure branches, so nothing
    exercises them: the contract tests above would pass identically if
    `wiring_block` returned the WHOLE FILE, which is precisely the vacuous scope
    this module's docstring warns about. Drive both arms directly.
    """
    with pytest.raises(AssertionError) as no_open:
        wiring_block("a skill body that never mentions the tool")
    assert "opening sentinel" in str(no_open.value)

    with pytest.raises(AssertionError) as no_close:
        wiring_block(f"... {BLOCK_OPEN} ... and then nothing closes it")
    assert "closing one" in str(no_close.value)

    # POSITIVE HALF: a synthetic body holding both sentinels yields EXACTLY the
    # span between them -- not the surrounding text. A fixture whose prefix and
    # suffix are distinct from the block is what makes that checkable.
    body = f"BEFORE {BLOCK_OPEN} middle {BLOCK_CLOSE} AFTER"
    got = wiring_block(body)
    assert got == f"{BLOCK_OPEN} middle {BLOCK_CLOSE}", got
    assert "BEFORE" not in got and "AFTER" not in got
