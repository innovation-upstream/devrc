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
BLOCK_OPEN = "**Surface what past sessions already recorded — TWO recall surfaces"
BLOCK_CLOSE = "carry on with the item."

# 🔴 THE COMMAND, PINNED WHOLE. Normalised only for internal whitespace runs, so
# a reflow across lines is tolerated and a changed flag is not.
#
# 🔴 THE QUERY IS THE TOPIC, NOT AN OPEN ITEM, AND THAT IS THE WHOLE POINT OF THE
# 2026-09-06 MOVE. Keyed on an open item the step can only run when one exists,
# which is the conditional that measured 1/14. Every resume has a topic.
EXPECTED_COMMAND = (
    "python3 ~/workspace/devrc/scripts/lib/handoff_search.py --offline "
    "--query \"<this handoff's topic, in plain words>\" --limit 3 "
    "--exclude-slug \"<the handoff: basename from step 2>\""
)

# The step that was MEASURED to fire 5/6, and whose company this command was
# moved into. Pinned as the first command of that same numbered step.
CO_LOCATED_COMMAND = "cairn recall --repo"

# Opens the paragraph that warns the reader the corpus query is NOT repo-scoped.
# The handle pin is scoped to THIS paragraph; widened to the whole block, its
# `DEVRC` arm cannot fail (the prescribed command's own path contains "devrc").
SCOPE_WARNING_SENTINEL = "THE TWO SURFACES ARE NOT SCOPED ALIKE"


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
    assert flags == ["--exclude-slug", "--limit", "--offline", "--query"], (
        f"the prescribed command's flags changed to {flags}. Extend the argv "
        "built below to pass the new flag, so the probe still proves the parser "
        "accepts everything /resume tells a reader to type."
    )

    argv = ["--query", "x", "--offline", "--limit", "0",
            "--exclude-slug", "claudedocs/handoff-anything.md"]
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
    readable. The recall posture is the same claim `cairn recall` carries in the
    SAME fence, a few lines above -- one provenance rule, so a caller cannot
    learn it for one surface and lose it for the other. (It read "four steps
    earlier" until 2026-09-06, which was wrong in both directions: `cairn recall`
    was one step LATER before the move, and is co-located after it.)
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


def numbered_steps(text: str) -> list[tuple[int, str]]:
    """Split the skill body into its top-level numbered steps.

    Returns [(step_number, body), ...]. A step starts at a line matching
    `^<n>. ` at column 0 and runs to the next such line, so the body includes
    every indented continuation and fenced block that belongs to it.
    """
    starts = [
        (int(m.group(1)), m.start())
        for m in re.finditer(r"(?m)^(\d+)\.\s", text)
    ]
    out = []
    for i, (num, pos) in enumerate(starts):
        end = starts[i + 1][1] if i + 1 < len(starts) else len(text)
        out.append((num, text[pos:end]))
    return out


def fenced_blocks(text: str) -> list[str]:
    """Every ``` fenced block in the document, bodies only."""
    return re.findall(r"(?ms)^\s*```[a-z]*\n(.*?)^\s*```", text)


def test_the_query_shares_a_FENCE_with_cairn_recall():
    """🔴 THE REGRESSION GUARD FOR THE 2026-09-06 MOVE — structural, not spelled.

    🔴 READ WHAT THIS DOES **NOT** PROVE FIRST. It does not prove the command is
    unconditional; it proves CO-LOCATION, which is a PROXY for it. An audit
    (2026-09-06) built four isolated mutants against an earlier version of this
    guard that asserted only a shared step DIGIT, and three survived a fully
    green suite:

      * M1  command moved back into step 3                     -> KILLED
      * M2  a gating sentence inserted above the fence          -> SURVIVED
      * M3  a "only when working an open item" comment INSIDE   -> SURVIVED
      * M4  a SECOND list item also labelled `4.`, gated        -> SURVIVED

    M4 is now killed too, by comparing the FENCE rather than the digit: a second
    `4.` block is a different fence.

    A round-2 audit then built a FIFTH mutant the round-1 residual list did not
    anticipate, and it was strictly worse than M2/M3:

      * M5  the command left in the fence but COMMENTED OUT     -> now KILLED

    M5 mattered because every check here is a substring test, and a commented
    `# python3 ...handoff_search.py ...` still CONTAINS the pinned string -- so
    the string pin, the step check and the fence check all went green over DEAD
    wiring, which is the exact failure this module was written to prevent. It is
    killed below by requiring the fence line carrying the command to be live.

    🔴 **M2 and M3 STILL SURVIVE and are covered by nothing.** They are
    prose-shaped -- a sentence or comment that re-introduces a condition without
    moving the command -- and no structural check here sees them.
    ⚠ **THIS LIST IS NOT CLOSED.** M5 was found by an auditor after round 1 had
    already called the residual settled; treat these five as the mutants somebody
    happened to think of, never as the space of ways to break the wiring.
    `claude/RULES.md`: a guard on WORDS is walkable by REWORDING, so this asserts
    placement and states its own residual rather than claiming a coverage it does
    not have.

    MEASURED over the 34 h after `#1295` merged: the query lived as a
    CONDITIONAL inside step 3 ("before working any open item…") and **1 of 14**
    `/resume` runs across both hosts ever invoked it -- and that one was reading
    `origin/main` off the staleness alarm, not firing the step. The trigger was
    met. The workbench half was 8 runs = 1 that fired + 6 analysed as non-firing
    + 1 that was the measuring session itself, excluded as the instrument; of
    those 6, all ran `claim-work`, five made edits, and five resumed a doc
    carrying an `## Open investigations` section. The discriminator was
    PLACEMENT: step 3's sibling `git log --since` check -- same trigger, same
    block, an ordinary command -- fired 0/6, while step 4 (`cairn recall`,
    numbered, unconditional, fenced) fired 5/6.

    ⚠ WHAT THIS DOES NOT CLAIM: that co-location reproduces 5/6. That is a
    prediction, and the residual is stated in the skill -- the 5/6 was measured
    with ONE command in the step. Re-measure; do not read this green as adoption.
    """
    steps = numbered_steps(_skill_text())
    assert steps, "no numbered steps parsed out of the resume skill -- format moved?"

    holding = [n for n, body in steps if _normalise(EXPECTED_COMMAND) in _normalise(body)]
    assert len(holding) == 1, (
        f"the prescribed query appears in {len(holding)} numbered step(s) "
        f"{holding}, expected exactly 1. Two copies mean two contracts that can "
        "drift apart; zero means the wiring left the numbered steps entirely."
    )

    co_located = [n for n, body in steps if CO_LOCATED_COMMAND in body]
    assert len(co_located) == 1, (
        f"`{CO_LOCATED_COMMAND}` appears in {len(co_located)} numbered step(s) "
        f"{co_located}, expected exactly 1 -- the anchor this guard measures "
        "against is itself ambiguous, so the assertion below would be meaningless."
    )

    assert holding[0] == co_located[0], (
        f"the handoff-corpus query is in step {holding[0]} but `cairn recall` is "
        f"in step {co_located[0]}.\n"
        "🔴 The query was MOVED into `cairn recall`'s step on 2026-09-06 because, "
        "as a conditional in step 3, it was invoked by 1 of 14 real /resume runs. "
        "Moving it back out re-creates a step that only fires when a session "
        "happens to be working an open item -- measured at ~7% of runs.\n"
        "If you are deliberately restructuring, re-measure adoption FIRST and put "
        "the number in the commit message.\n"
        "🔴 A shared step DIGIT is not enough on its own -- see the fence check "
        "below, which is what kills a second list item re-labelled `4.`."
    )

    # 🔴 THE FENCE CHECK -- this is the half that kills mutant M4. A second list
    # item re-labelled `4.` shares the digit, so the assertion above passes while
    # the query sits in its own separately-gated block. Sharing a FENCE cannot be
    # faked that way: one ```bash block is one thing a reader runs together.
    fences = fenced_blocks(_skill_text())
    with_query = [f for f in fences if _normalise(EXPECTED_COMMAND) in _normalise(f)]
    assert len(with_query) == 1, (
        f"the prescribed query appears in {len(with_query)} fenced block(s), "
        "expected exactly 1."
    )
    assert CO_LOCATED_COMMAND in with_query[0], (
        "the handoff-corpus query is no longer in the SAME fenced block as "
        f"`{CO_LOCATED_COMMAND}`.\n"
        "🔴 Sharing a step NUMBER is not sharing a step: a second list item "
        "re-labelled with the same digit passes the check above while gating the "
        "query behind its own condition -- that exact mutant SURVIVED this guard "
        "until 2026-09-06. The two commands must sit in one fence, so a reader "
        "runs them together.\n"
        f"  fence containing the query:\n{with_query[0].rstrip()}"
    )

    # 🔴 M5 -- THE COMMAND MUST BE LIVE, NOT COMMENTED. Every check above is a
    # substring test, so `# python3 ...handoff_search.py ...` satisfies all of
    # them while the wiring is DEAD. That is this module's founding failure (see
    # the file docstring: the index shipped and nothing called it), so it must
    # not be reachable through a one-character edit.
    live = [
        ln for ln in with_query[0].splitlines()
        if "handoff_search.py" in ln and not ln.lstrip().startswith("#")
    ]
    assert live, (
        "the prescribed query is present in the fence but every line carrying it "
        "is COMMENTED OUT -- the wiring is dead and every substring check above "
        "still passes.\n"
        "🔴 This module exists because the index shipped with no caller at all. A "
        "commented command is that same state, reached by one character, with a "
        "green suite vouching for it.\n"
        f"  fence:\n{with_query[0].rstrip()}"
    )


def test_the_corpus_SCOPE_warning_names_the_repos_the_TOOL_actually_searches():
    """🔴 DERIVED FROM THE MODULE, because the sensitivity warning rests on it.

    Step 4's block warns that the query is corpus-wide over four repos, two of
    them client repos, while its co-located twin `cairn recall` is repo-scoped.
    That warning is only true while the skill's list matches the tool's. Add a
    fifth handle -- plausibly another client repo -- and the prose, and the "two
    of which are client repos" count the warning rests on, go stale with nothing
    red. The module's own docstring says every claim is derived from the tool
    rather than restated; this is the missing derivation.

    🔴 SCOPED TO THE WARNING PARAGRAPH, NOT THE WHOLE BLOCK -- and that is the
    difference between a guard and a decoration. Searching the whole wiring block
    made the `DEVRC` arm STRUCTURALLY UNREACHABLE: `EXPECTED_COMMAND` contains
    `~/workspace/devrc/scripts/lib/handoff_search.py`, and the pin above requires
    that exact string in the same block, so `"devrc" in block` was guaranteed true
    on every green run. Deleting `devrc` from the warning left the suite at 8
    passed. The round-2 sweep chose `DATAPACKET` -- a fixture that could only die
    -- and read the kill as proof. `claude/RULES.md`: prove a guard REACHABLE, not
    merely breakable, and pick fixtures that are distinct from any constant the
    assertion already names. A whole-block search was also satisfied by a handle
    mentioned in ANY unrelated sentence in the block.

    ⚠ Scope: this pins the LABELS, not the client/non-client split, which is not
    a fact any module here holds. If a handle is added, re-read the sentence --
    the count in it is a human judgement and this test cannot check it.
    """
    mod = _load_search_module()
    index = sys.modules.get("handoff_index")
    handles = getattr(index, "REPO_ENV_HANDLES", None) or getattr(
        mod, "REPO_ENV_HANDLES", None
    )
    assert handles, (
        "could not read REPO_ENV_HANDLES off the tool -- if it moved or was "
        "renamed, update this test and the skill's scope warning together."
    )
    paras = [p for p in _block().split("\n\n") if SCOPE_WARNING_SENTINEL in p]
    assert len(paras) == 1, (
        f"expected exactly 1 paragraph carrying {SCOPE_WARNING_SENTINEL!r} in the "
        f"wiring block, found {len(paras)}. Without a unique paragraph this check "
        "silently widens back to the whole block, where the DEVRC arm cannot fail."
    )
    # 🔴 MATCH BACKTICKED TOKENS, NOT THE PROSE. Narrowing to the paragraph was
    # NOT enough: the paragraph itself says "a devrc-topic query" and "above the
    # devrc ones", so a bare substring search still could not see `devrc` being
    # deleted from the enumerated list. Only the list is backticked, so requiring
    # a CODE-SPAN carrying the handle makes every arm reachable -- measured: with
    # prose matching, dropping `devrc` left 8 passed; with this, it dies.
    block = paras[0]
    tokens = [t.lower() for t in re.findall(r"`([^`]+)`", block)]
    missing = [h for h in handles if not any(h.lower() in t for t in tokens)]
    assert not missing, (
        f"the /resume step-4 scope warning does not name repo handle(s) {missing}.\n"
        f"  the tool searches: {list(handles)}\n"
        "🔴 That paragraph tells the reader the query is corpus-wide and that "
        "hits may carry another client's content. A handle it does not name is a "
        "repo the reader is not warned about. Name it in "
        "claude/skills/resume/SKILL.md, and re-check the 'two of which are "
        "client repos' count in the same edit -- this test cannot check that."
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
