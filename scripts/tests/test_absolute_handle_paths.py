"""Handle gate: a doc may not spell out a checkout path that a handle already names.

WHY THIS EXISTS
---------------
`claude/skills/<name>/SKILL.md` is loaded VERBATIM into an agent's context as
authoritative operating instructions, and `nix/agent-handles.nix` already exports
`$DEVRC`, `$HOMELAB`, `$DATAPACKET`, `$CIVITAI`, `$CIVITAI_CLI` and the `$KC_*`
kubeconfigs into EVERY agent shell -- Claude Code's `zsh -c` (via
`nix/programs/zsh/default.nix`) and opencode's bash tool (via the generated
`~/.config/opencode/plugin/env.js`), both derived from that ONE file and both
existence-guarded.

Writing `python3 /home/zach/workspace/devrc/scripts/memory-audit.py` into a skill
therefore duplicates a value the runtime already holds, at a site nothing checks.
The duplicate is wrong the moment the user, the home directory or the checkout
location differs -- and it is wrong SILENTLY: the command does not warn, it just
names a file that is not there, or (worse, for a kubeconfig) a cluster that is
not the one meant. Measured by running THIS module against `22ddd8dc`, the
commit before it existed: 39 occurrences across 17 files (37 across 16 net of
the ignore list), all of them inside runnable commands or file pointers. The
gate's two halves do not contribute equally, and the split matters below: 30 of
those 39, across 14 files, are the ABSOLUTE spelling (28 across 13 net); the
remaining 9, across 4 files, are the `~`-spelled kubeconfig assignments the
later half armed.
⚠ This sentence read "21 occurrences across 9 files" until it was re-derived;
that pair reproduces under no framing tried -- not the parent commit with this
module, not the parent with the module as first committed (30/14), not the set
`98aa68b7` actually fixed (28/13). ⚠ Those two are NOT a third and fourth
quantity that coincidentally match the split above: the module AS FIRST
COMMITTED had only the absolute half, so running it over that tree measures
exactly the absolute subset. One set, reachable three ways. The figures above
are stated against a NAMED ref precisely so the next reader can re-run rather
than inherit.

🔴 THIS IS A SPELLING GATE, NOT A RESOLUTION GATE -- and that is deliberate
------------------------------------------------------------------------------
`test_doc_path_rot.py` -- the sibling gate over the SAME corpus -- explicitly
declines to look at absolute paths:

    no `$HOME`-EXPANDED spelling is mapped. A row keyed on `Path.home()` would
    make whether a token is even COUNTED depend on which user runs the gate [...]
    Absolute paths are never claims this repo can settle

That reasoning is correct, and it is exactly why the ABSOLUTE-spelled sites in
the census above -- the only spelling it speaks to -- survived a gate running
over the very same files. They did survive it: `test_doc_path_rot`'s own suite
is green over that tree. The `~`-spelled sites in the same census are NOT
covered by this argument; they survive that gate for reasons of its own, which
is why the census counts the two spellings separately rather than as one number.

So this module never resolves, stats or expands anything: it matches a TEXTUAL
PATTERN, which is identical on every host, in every clone, for every user.
`/home/zach/workspace/devrc/x`,
`/home/alice/workspace/devrc/x` and `/root/workspace/devrc/x` are all flagged, and
the verdict cannot depend on the machine because nothing about the machine is
read.

THE HANDLE TABLE IS PARSED FROM `nix/agent-handles.nix`, NEVER RESTATED
-----------------------------------------------------------------------
`agent-handles.nix` is the single source of truth and derives every value from
`home`, so the only host-independent part of a handle is its path SUFFIX
(`workspace/devrc`). This module parses those suffixes out of the nix file and
turns each into `<any absolute prefix>/<suffix>`. Two consequences worth stating:

  * adding a handle there arms this gate for it with no edit here -- but ONLY
    for a suffix of TWO OR MORE segments. A one-segment suffix turns the pattern
    into a wildcard over every parent directory, so it is refused by
    `test_the_handle_table_is_parsed_from_the_nix_source`; see the
    MINIMUM SPECIFICITY note on `_ABS_PREFIX`. Auto-arming is a convenience
    inside that bound, not an unbounded promise;
  * the remedy printed on failure is the handle's OWN name, so the failure line
    is the fix (`-> use `$DEVRC/scripts/memory-audit.py``).

⚠ A SECOND COPY OF THIS TABLE EXISTS and is deliberately NOT consulted:
`scripts/claude-hooks/shell-env-nudge.py` keeps its own `REPO_VARS`/`KC_VARS`
dicts. Reading the nix file is what makes this gate DRY against the SOURCE rather
than against another copy — that stays true, and the hook is still not consulted
here.

What changed: that copy is no longer UNPINNED. This paragraph used to end "unifying
the hook with it is separate work", and while it said so the hook's `KC_VARS` was
missing `KC_PROD` — from 2026-08-02, when that handle was declared, to 2026-09-14
(~6 weeks), with nothing able to report it.
`scripts/tests/test_shell_env_nudge_handles.py` now pins both of the hook's dicts
to `agent-handles.nix` in BOTH directions, importing `_handle_table()` /
`_kubeconfig_table()` from THIS module rather than writing a third regex over the
same file. So a handle added to the nix source arms this gate automatically and
reds that ledger until the hook is updated too.

LONGEST MATCH WINS
------------------
`/home/zach/workspace/homelab-talos/workbench-kubeconfig` is matched by BOTH
`$HOMELAB` and `$KC_WORKBENCH`. The more specific handle is the better remedy --
`$KC_WORKBENCH` is guarded on the FILE existing, `$HOMELAB` only on the directory
-- so matches are resolved longest-suffix-first. Pinned by
`test_the_longest_handle_wins`.

BOUNDARY, NOT SUBSTRING
-----------------------
A match must end at a path boundary, or `workspace/devrc` would claim a sibling
`workspace/devrc-scratch` -- a DIFFERENT directory, reported under `$DEVRC` with
a remedy naming the wrong tree. Measured, by deleting `_RIGHT_BOUND`:
`/home/zach/workspace/devrc-scratch/notes.md` becomes a `$DEVRC` finding.

⚠ `workspace/civit/civitai` vs `workspace/civit/civitai-cli` READS like the same
mechanism and is NOT one; this note asserted it was until it was planted. Both
patterns start at the SAME offset, and the table is longest-suffix-first, so
`$CIVITAI_CLI` claims it with or without `_RIGHT_BOUND` -- deleting the boundary
changes that verdict not at all. That pair is protected by LONGEST MATCH WINS
above. The boundary's job is the sibling NO handle names. Both cases are
asserted by `test_a_sibling_directory_is_not_a_match`; only the `devrc-scratch`
arm grades the boundary.

TWO TIERS -- THIS MODULE MUST RUN WHERE IT ACTUALLY GATES
----------------------------------------------------------
`scripts/tests` is in `scripts/run-tests.sh`'s `HERMETIC_TARGETS`, and
`flake.nix` `checks.pytests` runs that set over a COPY OF THE FLAKE SOURCE --
tracked files only, and NO `.git`. An unguarded `git ls-files` exits 128 there,
so a gate that reads the index without a fallback is dev-host-only in the tier
that actually blocks a merge. The corpus builders are IMPORTED from
`test_doc_path_rot`, which already solves this by falling back to a filesystem
walk -- one definition, so the two gates cannot disagree about what they cover.
`test_the_corpus_is_the_doc_rot_gates_corpus` pins that seam.

A RUN THAT CHECKS ZERO FILES IS AN ERROR
-----------------------------------------
A reassuring zero is indistinguishable from a gate wired to nothing, so the
corpus size is floored (`test_corpus_is_not_empty`) and the matcher is driven
against a planted violation on every run (`test_planted_absolute_path_is_caught`).
Both halves are needed: a floor proves the corpus is there, the plant proves the
matcher can still see.

🔴 `~/<suffix>` IS AN ACCEPTED SPELLING FOR THE **REPO** HANDLES, TODAY
------------------------------------------------------------------------
Read the title of this module as narrower than it sounds. `~/workspace/devrc/x`
names the same checkout as `$DEVRC/x` and this gate does **not** flag it: the
`~` character is in `_LEFT_BOUND`'s exclusion set, so a tilde-spelled path is
invisible to every absolute pattern.

THE CENSUS, measured on `f4241883` -- the commit the `~` half was armed against:

  * **183** `~/workspace/…` tokens across the corpus;
  * **10** of those 183 name a file a `$KC_*` handle also names;
  * a further **3** name a `$KC_*` file in the `~/.kube/…` spelling. 🔴 Those
    are NOT `~/workspace/…` tokens and are NOT among the 183 -- the **13**
    tilde-spelled `$KC_*` files are the two groups ADDED, never a subset of the
    183. (This line read "13 of which" until it was re-derived; conflating the
    two spellings is what made one quantity carry two figures.)
  * **9** of the 13 sit in a `KUBECONFIG=` assignment -- 6 spelled
    `~/workspace/…`, 3 spelled `~/.kube/…`. Those 9 are fixed and gated below,
    along with one bare `~/workspace/…` pointer fixed beside them.

🔴 ONE NAME PER QUANTITY, AND IT IS STATED ONCE -- HERE, AGAINST A NAMED REF.
Everywhere else in this module these tokens are referred to in WORDS. A total
kept beside the thing it counts drifts the moment anyone edits a doc (this
census is already stale against HEAD, by construction -- the commit it describes
is what moved it), and two figures for one quantity is how a reader learns to
trust neither. If you need the current number, derive it; do not add a second
copy here.

That is deliberate, for two reasons and one deferral:

  * `~` IS THE CORRECT SPELLING FOR A READ-TOOL TARGET. A doc pointing an agent
    at `~/.claude/skills/<name>/reference/<topic>.md` is naming a file to be
    opened with the Read tool, where `$VAR` does not expand -- it is the
    spelling this gate's own failure message recommends. Arming `~` wholesale
    would make the gate unsatisfiable on such pointers, and `claude/RULES.md`
    calls a permanently-red gate worse than no gate. ⚠ That example is the
    SHAPE, not a site a wholesale arming would flag: NO handle names `.claude`,
    so arming `~` for every handle reports ZERO `~/.claude/…` findings --
    measured. The pointers it actually breaks are the `~/workspace/…` ones: the
    same experiment flags most of the corpus's `~/workspace/…` tokens, among
    them `~/workspace/homelab-talos/containers/clawgate/HANDOFF.md`, which
    `test_a_correct_spelling_stays_green` requires to stay green;
  * `~` IS ALSO THE CORRECT SPELLING WHERE THE PER-HOST SPLIT IS THE POINT.
    `claude/skills/clawgate/SKILL.md` and its `reference/troubleshooting.md`
    tabulate BOTH `~/workspace/homelab-talos/workbench-kubeconfig` and
    `~/workspace/homelab-infra/workbench-kubeconfig` precisely to teach the
    reader to `ls` both and take the one that exists. A handle cannot express
    that -- `$KC_WORKBENCH` names only the `homelab-talos` spelling, so it is
    EMPTY on exactly the host where `homelab-infra` is right;
  * and the remaining `~/workspace/…` repo-handle sweep is DEFERRED work, not a
    claim that those sites are fine. Until it happens, THIS GATE'S TITLE
    OVERSTATES ITS SCOPE:
    it rejects the `/home/<user>/…` spelling everywhere, and the `~/…` spelling
    for one shape only.

WHAT IS ARMED FOR `~`: A KUBECONFIG ASSIGNMENT, AND NOTHING ELSE
-----------------------------------------------------------------
One narrow shape is rejected in the `~` spelling: `KUBECONFIG=~/<suffix>` (with
or without a leading `export`) where `<suffix>` is a path the `kubeconfigs`
block of `agent-handles.nix` already names. That block is parsed out of the nix
file separately from `repos` -- see `_kubeconfig_table` -- so the two halves
cannot be armed by accident together.

The `KUBECONFIG=` prefix IS the shell-command discriminator, and it is the whole
restriction: a bare `~/workspace/homelab-talos/homelab-kubeconfig` cited as a
pointer stays green. Pinned in both directions by
`test_a_tilde_kubeconfig_outside_a_shell_assignment_stays_green` and
`test_planted_tilde_kubeconfig_assignment_is_caught`.

That shape is not invented here. `scripts/claude-hooks/shell-env-nudge.py` --
the RUNTIME nudge, the second copy of the handle table this module deliberately
does not consult -- already keys on `KUBECONFIG=<path>`, already expands `~`
before matching, and already declines to nudge a repo path used as a file
reference. So the gate rejects in a doc exactly what the hook nudges in a live
command. The TABLES stay separate (reading the nix source is what makes this
gate DRY against the SOURCE); the DISCRIMINATOR agreeing is the point.

⚠ MEASURED, AND IT CORRECTS THE FOLK JUSTIFICATION FOR THIS RULE
------------------------------------------------------------------
The reason usually given -- "an absent `~` path yields an empty `KUBECONFIG=`,
which falls back to `~/.kube/config`" -- is BACKWARDS. `~` is expanded by the
shell whether or not the file exists, so an ABSENT path is never an EMPTY
variable, and the two arms fail differently:

    KUBECONFIG=<an absent file>  kubectl config current-context
        -> `error: current-context is not set`, rc 1   (LOUD; no fallback)
    KUBECONFIG=                  kubectl config current-context
        -> the context from `~/.kube/config`, rc 0     (SILENT; wrong cluster)

🔴 THE SECOND LINE CARRIES A PRECONDITION, AND IT DOES NOT HOLD ON THIS HOST.
The silent arm requires the DEFAULT kubeconfig to carry a NON-EMPTY
`current-context`; it is reached only after someone has run `kubectl config
use-context`. Re-measured 2026-09-12, kubectl v1.36.3: `~/.kube/config` exists
here (8,750 B, 3 contexts, mtime 2026-07-24 -- so this was already true when the
block above was first written) but carries `current-context: ""`. BOTH arms
therefore return `error: current-context is not set`, rc 1 -- loud, and
character-for-character identical. As written the transcript did not reproduce.

The MECHANISM is real; only its reachability is host state. Isolated with a
control that varies nothing else -- same kubectl, same command, a `HOME` whose
`.kube/config` sets `current-context: sentinel-ctx`: the empty arm returns
`sentinel-ctx` rc 0 while the absent-file arm still fails rc 1. That is the
difference the two lines above claim, observed, with the precondition supplied.

The silent arm is the EXISTENCE-GUARDED HANDLE's failure mode, not the tilde's:
a `$KC_*` that declines to export leaves `KUBECONFIG=` empty. So substituting
the handle trades a loud failure for a quiet one on a host where the checkout is
absent AND a default context is set. It is still the right trade here -- this is
a SPELLING gate, and the duplicated literal is wrong on any host whose checkout
differs (the `homelab-talos` / `homelab-infra` split above is that host, live)
-- but do not repeat the fallback sentence as the mechanism, and do not quote
the transcript without its precondition. Neither is what happens unconditionally.

THE ESCAPE HATCH
----------------
`IGNORE_FILE`, keyed on `(doc, literal)` -- NOT on the literal alone, so an
exemption cannot silently license the same spelling in a doc that copies it. 🔴
EVERY entry must carry a `#` reason on its own line, enforced by
`test_every_ignore_entry_carries_a_reason`: an ignore list of unexplained lines
stops being an exception and becomes the coverage mechanism.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

# ONE corpus definition, shared with the sibling gate over the same files. See
# the TWO TIERS note above: these builders already carry the no-index fallback.
from test_doc_path_rot import (  # noqa: E402
    CORPUS_DIRS,
    REPO_ROOT,
    _corpus_docs,
    _corpus_from_filesystem,
    _corpus_from_index,
    _has_index,
)
# 🔴 The SEAM is this module's corpus against the one the OTHER GATE ACTUALLY
# ITERATES -- `test_doc_path_rot._corpus`, the zero-argument function its own
# gate calls. Comparing against `_corpus_docs` instead cannot fail, because the
# local `_corpus` is defined as a call to it; see
# `test_the_corpus_is_the_doc_rot_gates_corpus`.
from test_doc_path_rot import _corpus as _doc_rot_corpus  # noqa: E402

HERE = Path(__file__).resolve().parent
HANDLES_NIX = REPO_ROOT / "nix/agent-handles.nix"
# 🔴 NOT a `.txt`: `test_no_captured_markup.py` gates every tracked `.txt` for
# pasted prose, and a header explaining an exception is exactly what trips it.
# The suffix says what the file is -- a list of entries.
IGNORE_FILE = HERE / "absolute-handle-path-ignore.list"

# `NAME = "${home}/workspace/devrc";` -- the only shape `agent-handles.nix` uses,
# and the only one that is host-independent. A future entry built any other way
# would go unparsed, which `test_the_handle_table_is_parsed_from_the_nix_source`
# catches via the floor and the named-handle assertions.
_NIX_ENTRY = re.compile(r'^\s*([A-Z][A-Z0-9_]*)\s*=\s*"\$\{home\}/([^"]+)"\s*;', re.M)

# The `kubeconfigs = { ... };` attrset, isolated from `repos = { ... };`. Only
# the kubeconfig half is armed for the `~` spelling (see the docstring), so the
# two must be parsed SEPARATELY rather than filtered by a `KC_` name prefix --
# a naming convention is a spelling, and a handle added to `kubeconfigs` under
# some other name would silently go unarmed.
# ⚠ `_NIX_SECTION` lived here, hardcoded to `kubeconfigs`. It is now `nix_block(name)`
# — same pattern, parameterised — because a second consumer needed the `repos` block
# and started by writing its own copy. Removed rather than left beside its successor:
# an unused-but-plausible parser reads as authoritative to whoever greps for one next,
# and a fourth copy of this predicate is exactly what the consolidation was for.

# The `${home}` half, as a pattern: ANY absolute prefix of one or more segments.
# This is what makes the gate host-independent -- `/home/zach`, `/home/alice` and
# `/root` are all matched, so the verdict never depends on who runs it.
#
# 🔴 MINIMUM SPECIFICITY: THIS WILDCARD IS ONLY SAFE AGAINST A SUFFIX OF TWO OR
# MORE SEGMENTS. The prefix deliberately matches anything, so ALL the specificity
# lives in the suffix it is glued to. `workspace/devrc` names one checkout under
# any home. A ONE-segment suffix names that segment under ANY parent -- and the
# gate would then print a remedy for it, naming a DIFFERENT FILE. That bound is
# asserted in `test_the_handle_table_is_parsed_from_the_nix_source`, not here,
# because it is a property of the TABLE parsed out of the nix source (which is
# free to change without this file changing) rather than of this pattern.
_ABS_PREFIX = r"/(?:[A-Za-z0-9._+-]+/)*"
# A match may not START mid-path and may not END mid-segment (see BOUNDARY, NOT
# SUBSTRING above).
#
# 🔴 `/` IS IN THE LEFT-HAND EXCLUSION SET, and it is what keeps URLs out.
# `https://example.dev/home/zach/workspace/devrc/scripts/x.py` offers the engine
# a start at `/example.dev/…` whose preceding character is the second `/` of the
# scheme; every other candidate start inside it is preceded by a word character.
# Without this the gate reports a finding on any documentation LINK whose path
# happens to contain a handle suffix, and prints a remedy that would corrupt the
# URL. Pinned in `test_a_correct_spelling_stays_green`.
_LEFT_BOUND = r"(?<![A-Za-z0-9._+~/-])"
_RIGHT_BOUND = r"(?![A-Za-z0-9._+-])"
# The TAIL: the rest of the path after the handle's own suffix. Captured so the
# failure line can print the WHOLE literal and the remedy can keep it verbatim --
# `$DEVRC/scripts/memory-audit.py`, not a bare `$DEVRC` the reader must re-derive
# the rest of. A trailing `/` is allowed, punctuation that ends a sentence is
# not.
#
# ⚠ `@` and `%` are in the class DEFENSIVELY -- for the shapes that carry them
# (an npm scope, a URL-escaped segment) -- NOT because this corpus has any.
# This comment asserted the corpus as the reason and that was not a fact:
# measured at `22ddd8dc`, at `f4241883` and at HEAD, no literal this gate
# matches carries either character, and dropping both from the class changes no
# verdict. Keeping them is free; the justification had to be corrected.
_TAIL = r"(?:/[A-Za-z0-9._+@%-]+)*/?"

# THE `~` HALF -- kubeconfigs only, and only inside a shell assignment.
#
# `KUBECONFIG=` is the shell-command discriminator. It is what separates a
# RUNNABLE command (where `$KC_*` expands and is the correct spelling) from a
# doc POINTER or a Read-tool target (where `$VAR` does NOT expand, so `~` is
# correct and must stay legal). Widening this to bare `~/<suffix>` flags those
# pointers; `test_a_tilde_kubeconfig_outside_a_shell_assignment_stays_green`
# is the assertion that catches the widening.
#
# The left lookbehind keeps `MY_KUBECONFIG=`-style suffixed names out, so the
# match is an assignment to the real variable rather than to anything ending in
# those letters. There is no `_TAIL`: a kubeconfig handle names a FILE.
_KUBECONFIG_ASSIGN = r"(?<![A-Za-z0-9_])KUBECONFIG="


def _handle_table() -> list[tuple[str, str]]:
    """`[(HANDLE_NAME, home-relative-suffix), ...]`, LONGEST SUFFIX FIRST.

    Parsed from `nix/agent-handles.nix` rather than restated, so the gate is DRY
    against the single source of truth and a new handle arms it automatically.
    The ordering is the longest-match rule: see the module docstring.
    """
    text = HANDLES_NIX.read_text(encoding="utf-8")
    entries = [(name, rel.rstrip("/")) for name, rel in _NIX_ENTRY.findall(text)]
    return sorted(entries, key=lambda e: (-len(e[1]), e[0]))


def nix_block(name: str, why: str) -> list[tuple[str, str]]:
    """One named attrset of `agent-handles.nix`, LONGEST SUFFIX FIRST.

    🔴 PARAMETERISED AND PUBLIC ON PURPOSE. `test_shell_env_nudge_handles` needs
    the `repos` block the same way this module needs `kubeconfigs`, and it began
    by adding a SECOND block regex of its own — at which point three copies of
    this predicate existed over one file and they DISAGREED: on an inline
    `repos = { A = "..."; };` the private copy over-captured into the next block
    while `test_repo_path_guard`'s `split("repos = {")` copy silently dropped an
    entry. `claude/RULES.md`: one rule, one place — a predicate open-coded at N
    sites is wrong at N−1 of them.

    🔴 A THIRD COPY SURVIVES AND IS NOT CONSOLIDATED HERE — named so this is not
    read as finished. `scripts/tests/test_repo_path_guard.py` parses the same
    block as `handles_nix.split("repos = {", 1)[1].split("};", 1)[0]`, and on the
    inline shape above it drops an entry where this one over-captures. It is
    pre-existing and belongs to another gate. **Closes when** that call site uses
    `nix_block("repos", …)` and its suite is green.

    ⚠ KNOWN LIMIT, stated rather than implied: `^\\s*\\};` requires the closing
    brace to start its own line, which is the only shape `agent-handles.nix`
    uses. An INLINE one-line attrset is not matched here and the search runs on
    to the next block's closer. That is not fixed — it is named, so the next
    reader does not have to re-derive it, and so nobody reads this as robust to
    a reformat. The handle-name floors are what catch the consequence.

    `why` is folded into the refusal so each caller says what ITS absence costs;
    an empty return disarms that caller while every other assertion stays green.
    """
    text = HANDLES_NIX.read_text(encoding="utf-8")
    m = re.compile(rf"^\s*{re.escape(name)}\s*=\s*\{{(.*?)^\s*\}};", re.M | re.S).search(text)
    if not m:
        raise AssertionError(
            f"no `{name} = {{ ... }};` block found in {HANDLES_NIX}. {why}"
        )
    entries = [(n, rel.rstrip("/")) for n, rel in _NIX_ENTRY.findall(m.group(1))]
    return sorted(entries, key=lambda e: (-len(e[1]), e[0]))


def _kubeconfig_table() -> list[tuple[str, str]]:
    """The `kubeconfigs` half of `agent-handles.nix`, LONGEST SUFFIX FIRST.

    Parsed out of its own attrset, not filtered by name. An empty return here
    disarms the whole `~` half while every other assertion stays green, so it is
    floored as a ledger by `test_the_kubeconfig_table_is_its_own_nix_section`.
    """
    return nix_block(
        "kubeconfigs",
        "The `~` half of this gate is built from that block; without it every "
        "tilde-spelled kubeconfig assignment in the corpus goes UNCHECKED "
        "while this module still reports PASS.",
    )


def _patterns() -> list[tuple[str, re.Pattern[str]]]:
    """`[(HANDLE_NAME, compiled), ...]`. Group 1 of every pattern is the LITERAL.

    Two families, and they cannot collide: an absolute match starts at `/`, a
    tilde match at `~`, so no offset is ever claimed by both. Group 1 is what
    makes them uniform -- the `~` patterns must not report their `KUBECONFIG=`
    prefix as part of the path.
    """
    abs_pats = [
        (name, re.compile(
            _LEFT_BOUND + "(" + _ABS_PREFIX + re.escape(rel) + _TAIL + ")" + _RIGHT_BOUND
        ))
        for name, rel in _handle_table()
    ]
    tilde_pats = [
        (name, re.compile(_KUBECONFIG_ASSIGN + "(~/" + re.escape(rel) + ")" + _RIGHT_BOUND))
        for name, rel in _kubeconfig_table()
    ]
    return abs_pats + tilde_pats


def _violations(docs: dict[str, str]) -> list[tuple[str, int, str, str]]:
    """`[(doc, lineno, matched-literal, HANDLE_NAME), ...]` for `{path: text}`.

    Longest-suffix-first with a per-offset claim, so one literal yields exactly
    one finding and it names the MOST SPECIFIC handle. Grading by equality in the
    probes below then catches a mutation that ADDS a finding as well as one that
    drops it.
    """
    pats = _patterns()
    out: list[tuple[str, int, str, str]] = []
    for doc in sorted(docs):
        for lineno, line in enumerate(docs[doc].splitlines(), start=1):
            claimed: dict[int, tuple[str, str]] = {}
            for name, pat in pats:
                for m in pat.finditer(line):
                    # First writer wins: `pats` is longest-suffix-first, so the
                    # most specific handle claims the offset. Group 1 is the
                    # literal -- for a `~` match the `KUBECONFIG=` prefix that
                    # anchored it is NOT part of the path being reported.
                    claimed.setdefault(m.start(1), (m.group(1), name))
            for start in sorted(claimed):
                literal, name = claimed[start]
                out.append((doc, lineno, literal, name))
    return out


def _remedy(literal: str, name: str) -> str:
    """`$HANDLE/<tail>` -- the literal with its handle-named prefix replaced.

    🔴 `find`, not `rfind`. The regex's `${home}` half is GREEDY, so on a path
    that repeats the suffix (`…/workspace/devrc/x/workspace/devrc/y.py`) the
    engine's own split lands on the LAST occurrence -- and a remedy built from
    that silently deletes the middle of the path while still reading as a correct
    instruction. The checkout root is the FIRST occurrence. Pinned by
    `test_the_remedy_names_the_handle_and_keeps_the_tail`.
    """
    rel = dict((n, r) for n, r in _handle_table())[name]
    idx = literal.find("/" + rel)
    tail = literal[idx + len(rel) + 1:]
    return f"${name}{tail}"


# --- the ignore list ---------------------------------------------------------


def _read_ignore() -> tuple[set[tuple[str, str]], list[str]]:
    """`({(doc, literal), ...}, [raw non-comment lines])`.

    LOUD on a missing file, never silent: without it every exempt site is
    reclassified as a violation, which is a false red, and a false red is how a
    gate gets bypassed.
    """
    if not IGNORE_FILE.is_file():
        raise AssertionError(
            f"{IGNORE_FILE} is missing. This gate's verdict is meaningless "
            "without it; restore it from git rather than letting the run "
            "continue."
        )
    pairs: set[tuple[str, str]] = set()
    raws: list[str] = []
    for line in IGNORE_FILE.read_text(encoding="utf-8").splitlines():
        body = line.split("#", 1)[0].strip()
        if not body:
            continue
        raws.append(line)
        doc, sep, literal = body.partition("\t")
        assert sep and literal.strip(), (
            f"malformed ignore line (want '<doc>\\t<literal>  # why'): {line!r}"
        )
        pairs.add((doc.strip(), literal.strip()))
    return pairs, raws


def _unexplained(raws: list[str]) -> list[str]:
    """Ignore-file lines carrying no `#` reason.

    Extracted so it can be driven against a SYNTHETIC list. The real file's one
    entry is explained, so a `return []` mutant here is invisible when graded
    only against the tree -- it SURVIVED a full battery before this probe
    existed. The guard is worth nothing unless it has been watched to fire.
    """
    return [r for r in raws if "#" not in r or not r.split("#", 1)[1].strip()]


def _corpus() -> list[str]:
    return _corpus_docs(REPO_ROOT)


def _corpus_text() -> dict[str, str]:
    return {d: (REPO_ROOT / d).read_text(encoding="utf-8") for d in _corpus()}


def _filter(violations, ignored) -> list[tuple[str, int, str, str]]:
    """Violations the ignore list does not license.

    🔴 ONE implementation, called by THE GATE and by
    `test_an_ignore_entry_licenses_only_the_doc_it_names` alike. Open-coding the
    filter in the probe would leave the gate's own copy ungraded: the corpus is
    clean, so `return []` here reports a perfect PASS and every planted-violation
    probe stays green, because those grade `_violations` and never reach this.
    Routing both through one function is what makes that mutant die.

    🔴 The key is `(doc, literal)`, never the literal alone -- see the probe.
    """
    return [v for v in violations if (v[0], v[2]) not in ignored]


def _new_violations() -> list[tuple[str, int, str, str]]:
    ignored, _ = _read_ignore()
    return _filter(_violations(_corpus_text()), ignored)


# --- positive controls -------------------------------------------------------

# WELL UNDER HALF the measured corpus, by the same rule `test_doc_path_rot.py`
# states for its own floors: these are COLLAPSE detectors, not a census. This
# repo's headline workflow is PRUNING documentation, so a floor sitting just
# under the measurement reds on ordinary work and sends the reader hunting a bug
# that is not there.
#
# ⚠ This read "HALF" until the arithmetic was checked: 40 against a corpus of 99
# is nearer two-fifths. The value is the sibling gate's own floor, which ITS
# comment records as half of the 80 it measured; this corpus has grown since and
# the floor has not, which only makes it slacker -- the safe direction for a
# collapse detector, but not "half".
CORPUS_DOC_FLOOR = 40  # measured 99 (re-derived via `_corpus()`; read "88" before)


def test_corpus_is_not_empty():
    """🔴 A RUN THAT CHECKS ZERO FILES IS AN ERROR, NOT A PASS.

    Every other assertion here is "no violations found". That sentence is true of
    a gate wired to nothing, and true in exactly the same words, so the corpus
    size has to be asserted or the green means nothing.
    """
    docs = _corpus()
    assert len(docs) >= CORPUS_DOC_FLOOR, (
        f"only {len(docs)} corpus doc(s) found under {CORPUS_DIRS} -- 99 were "
        "measured. The corpus builder has broken, and every PASS from this "
        "module is a claim about the builder rather than about the docs."
    )


def test_the_corpus_is_the_doc_rot_gates_corpus():
    """SEAM GUARD. Two gates over 'the same corpus' is a claim, and a claim that
    nobody owns drifts: if one of them narrows, the other's green stops covering
    what its docstring says it covers.

    🔴 GRADED AGAINST THE OTHER GATE'S OWN ENTRY POINT, `test_doc_path_rot
    ._corpus` -- the zero-argument function that module's gate actually iterates
    -- NOT against the shared builder `_corpus_docs`. That distinction is the
    whole guard: the local `_corpus` is *defined* as `_corpus_docs(REPO_ROOT)`,
    so `assert _corpus() == _corpus_docs(REPO_ROOT)` restates its own definition
    and cannot fail for the reason the docstring gives. It stayed green through
    a narrowing of `test_doc_path_rot._corpus`, which is the exact seam it
    claims to watch. Reading as coverage while providing none is worse than
    providing none, because it stops anyone looking."""
    assert _corpus() == _doc_rot_corpus(), (
        "this gate's corpus is no longer the doc-rot gate's corpus. A narrowed "
        "corpus is the silent failure: every doc it dropped is UNCHECKED and "
        "UNCOUNTED, and the gate still reports PASS."
    )
    if _has_index(REPO_ROOT):
        assert set(_corpus_from_index(REPO_ROOT)) == set(
            _corpus_from_filesystem(REPO_ROOT)
        ), (
            "the index and the filesystem disagree about the corpus, so the dev "
            "host and the nix-sandbox tier check different sets of docs."
        )


# Every handle name this module is known to arm. Named individually rather than
# counted, because a COUNT survives a rename: the parser could pick up five
# entries with one of them silently spelled differently and the floor would not
# move. The `$KC_*` rows matter most -- a wrong kubeconfig is the failure that
# looks like success.
KNOWN_HANDLES = frozenset(
    {
        "DEVRC", "HOMELAB", "DATAPACKET", "CIVITAI", "CIVITAI_CLI",
        "KC_HOMELAB", "KC_WORKBENCH", "KC_PROD", "KC_DPPROD", "KC_NEBULA",
    }
)


def test_the_handle_table_is_parsed_from_the_nix_source():
    """POSITIVE CONTROL on the parser, and a LEDGER over `agent-handles.nix`.

    The table is the whole gate: an empty or half-read table makes every
    assertion below vacuously true while the module still reports PASS. Graded as
    a ledger that fails when the set SHRINKS (a handle stopped being armed) --
    growth is allowed, because adding a handle upstream is meant to arm this gate
    with no edit here.
    """
    table = _handle_table()
    names = {n for n, _ in table}
    assert not (KNOWN_HANDLES - names), (
        f"handles no longer parsed out of nix/agent-handles.nix: "
        f"{sorted(KNOWN_HANDLES - names)}\nEither they were removed upstream (in "
        "which case delete them from KNOWN_HANDLES and say why) or `_NIX_ENTRY` "
        "no longer matches the shape they are written in -- and in that second "
        "case this gate is silently not checking them."
    )
    # The suffixes are what the patterns are built from; an empty one would match
    # every absolute path in the corpus.
    #
    # 🔴 AND SO WOULD A ONE-SEGMENT ONE, WHICH IS THE HAZARD THE DOCSTRING'S
    # "adding a handle there arms this gate with no edit here" WOULD OTHERWISE
    # LEAVE UNBOUNDED. `_ABS_PREFIX` matches ANY absolute prefix by design, so a
    # short suffix is a wildcard and the remedy the gate prints for it names a
    # DIFFERENT FILE than the one flagged. Reproduced: adding
    # `NIXCFG = "${home}/nixos";` to `nix/agent-handles.nix`, with no edit to
    # this module, makes the gate report 22 corpus sites spelling `/etc/nixos/…`
    # and emit `-> use `$NIXCFG/configuration.nix`` -- i.e.
    # `~/nixos/configuration.nix`, which is not `/etc/nixos/configuration.nix`.
    # A remedy that reads as correct and silently redirects the reader is worse
    # than no finding at all.
    #
    # One assertion covers BOTH halves of the gate: `_handle_table()` parses the
    # WHOLE nix file, so a `kubeconfigs` entry with a one-segment suffix is
    # caught here too and needs no second copy in the kubeconfig ledger.
    for name, rel in table:
        assert rel and not rel.startswith("/") and "${" not in rel, (
            f"{name} parsed to a suffix this gate cannot use: {rel!r}"
        )
        assert "/" in rel, (
            f'{name} = "${{home}}/{rel}" is a ONE-SEGMENT handle suffix, which '
            f"this gate cannot arm safely. Its absolute pattern is <any absolute "
            f"prefix>/{rel}, so {rel!r} matches under ANY parent -- /etc/{rel}, "
            f"/usr/local/{rel}, /var/lib/{rel} -- not only under a home. Each of "
            f"those is a DIFFERENT FILE from the one ${name} names, and the gate "
            f"would print `-> use `${name}/<tail>`` for it: a wrong-file remedy "
            f"that reads as a correct instruction. Give the handle a suffix of "
            f"two or more segments. If it genuinely names a one-segment "
            f"directory, there is NO exclusion hatch to reach for: "
            f"{IGNORE_FILE.name} is keyed on (doc, literal) and filters "
            f"VIOLATIONS, so it cannot exempt a table entry -- and silencing "
            f"this assert by hand leaves `_patterns` arming the wildcard "
            f"anyway. Excluding a handle means teaching `_handle_table` to skip "
            f"it, which is a code change and needs its own test."
        )


KNOWN_KUBECONFIG_HANDLES = frozenset(
    {"KC_HOMELAB", "KC_WORKBENCH", "KC_PROD", "KC_DPPROD", "KC_NEBULA"}
)
KNOWN_REPO_HANDLES = KNOWN_HANDLES - KNOWN_KUBECONFIG_HANDLES


def test_the_kubeconfig_table_is_its_own_nix_section():
    """LEDGER + NEGATIVE LEDGER on the `~` half's table.

    🔴 BOTH DIRECTIONS MATTER AND THEY FAIL DIFFERENTLY. An EMPTY or shrunken
    table disarms the `~` check while every other assertion in this module stays
    green -- the reassuring zero. The `leaked` half is a SCOPE LEDGER: what `~`
    covers is a deliberate decision, and this is where it is written down.

    🔴 THE HAZARD IS THE ANCHOR, NOT THE TABLE -- so do not re-derive a
    catastrophe for this assert. This docstring claimed a grown table "would arm
    `~` for EVERY `~/workspace/…` site in the corpus" and "make the gate
    unsatisfiable on Read-tool pointers". MEASURED, by planting all five repo
    handles into the `kubeconfigs` block of a throwaway de-gitted copy and
    re-running: NOT ONE corpus site turns red, and every Read-tool-pointer
    fixture in `test_a_tilde_kubeconfig_outside_a_shell_assignment_stays_green`
    stays green. The `~` family is anchored on `KUBECONFIG=`, so a leaked handle
    can only fire inside an assignment naming that checkout -- and the corpus
    has none. What actually arms `~` broadly is DROPPING THE ANCHOR: that is
    stated and guarded where the anchor is defined (`_KUBECONFIG_ASSIGN`), and
    graded by that same probe. With the anchor gone AND the table grown it does
    reach most of the corpus's `~/workspace/…` sites -- the deferred sweep --
    but neither change gets there alone.

    ⚠ A GROWN TABLE IS NOT HARMLESS EITHER; IT FAILS IN A DIFFERENT WAY. The
    same experiment turns the `repo-handle-tilde` and `sibling-file` fixtures
    red, on `KUBECONFIG=~/workspace/homelab-talos/<file>`, and the gate prints
    `KUBECONFIG=$HOMELAB` -- silently dropping the filename. The `~` family
    carries no `_TAIL` because a kubeconfig handle names a FILE; a repo handle
    names a DIRECTORY, so the reported literal stops at the checkout root and
    the remedy names the wrong file. Same class as the one-segment-suffix hazard
    above: a remedy that reads as a correct instruction and is not.

    (An earlier draft of this docstring carried a count of its own, disagreeing
    with the module docstring's census of the same quantity. The census is
    stated ONCE, there, against a named ref; the argument here does not need a
    figure, so it still has none.) So the kubeconfig names are floored as a set
    AND the repo names are asserted ABSENT.
    """
    names = {n for n, _ in _kubeconfig_table()}
    assert not (KNOWN_KUBECONFIG_HANDLES - names), (
        "kubeconfig handles no longer parsed out of the `kubeconfigs` block of "
        f"nix/agent-handles.nix: {sorted(KNOWN_KUBECONFIG_HANDLES - names)}. "
        "The `~` half of this gate is built from that block, so every tilde-"
        "spelled assignment naming them is now UNCHECKED."
    )
    leaked = names & KNOWN_REPO_HANDLES
    assert not leaked, (
        f"repo handles leaked into the kubeconfig table: {sorted(leaked)}. That "
        "arms the `~` spelling for repo checkouts, which this module's docstring "
        "records as an ACCEPTED spelling (Read-tool targets need it) and as "
        "deferred work. Widening it here is a silent scope change."
    )
    for name, rel in _kubeconfig_table():
        assert rel and not rel.startswith("/") and "${" not in rel, (
            f"{name} parsed to a suffix this gate cannot use: {rel!r}"
        )


def test_the_table_is_longest_suffix_first():
    """The ordering IS the longest-match rule -- it is not cosmetic. Sorted
    wrongly, `$HOMELAB` claims every `$KC_*` path under it and the printed remedy
    silently degrades to the weaker handle."""
    lens = [len(rel) for _, rel in _handle_table()]
    assert lens == sorted(lens, reverse=True)


# --- THE GATE ----------------------------------------------------------------


def test_no_absolute_checkout_paths_in_agent_docs():
    """THE GATE."""
    new = _new_violations()
    assert not new, (
        f"{len(new)} doc site(s) spell out a checkout path that a handle already "
        "names:\n"
        + "\n".join(
            f"  {d}:{n}  {lit}  ->  use `{_remedy(lit, h)}`"
            for d, n, lit, h in new
        )
        + "\n\nThese files are loaded VERBATIM as authoritative agent "
        "instructions, and `nix/agent-handles.nix` already exports the handle "
        "into every agent shell (zsh + opencode), existence-guarded. A spelled-"
        "out path is a second copy of that value which nothing reconciles.\n"
        "  * in a shell command   -> use the handle: `$DEVRC/scripts/x.py`\n"
        "  * in PYTHON            -> `os.environ[\"DEVRC\"]` ($VAR does not "
        "expand there)\n"
        "  * a doc POINTER to another devrc skill -> the deployed spelling, "
        "`~/.claude/skills/<name>/reference/<topic>.md`\n"
        "  * a path being CREATED, or one with no handle -> derive it from a "
        "handle (`$(dirname $HOMELAB)/homelab-trunk`)\n"
        f"Genuinely correct as a literal (a sample API payload, a per-host "
        f"probe) -> scripts/tests/{IGNORE_FILE.name}, WITH A REASON."
    )


def test_every_ignore_entry_carries_a_reason():
    """🔴 An ignore list of unexplained lines stops being an exception list and
    becomes the coverage mechanism -- silently, because it reads as maintained.

    So the reason is STRUCTURAL, not a convention: an entry without a `#` comment
    on its own line fails here. Reviewing a one-line justification is the entire
    cost of the hatch, and it is what keeps the list small."""
    _, raws = _read_ignore()
    unexplained = _unexplained(raws)
    assert not unexplained, (
        "ignore entries with no written reason:\n"
        + "\n".join(f"  {r}" for r in unexplained)
        + f"\n\nAppend `  # <why this literal is correct>` to each. An entry "
        "nobody can justify in one line belongs in the docs as a FIX, not here."
    )


def test_the_reason_check_can_actually_fire():
    """POSITIVE CONTROL on the reason check.

    🔴 The test above reports a reassuring zero, and the real file's single entry
    IS explained -- so `_unexplained` never executes its predicate on a bad line
    and a `return []` mutant is invisible. Measured: it SURVIVED the whole
    battery until this probe existed. Driven here against synthetic lines whose
    answer must be non-zero, with the explained forms as the other arm so the
    check is not simply refusing everything.

    Fixture lines are pairwise distinct and name paths that appear nowhere in the
    corpus, so none of them can be satisfied by a constant this module already
    holds.
    """
    bad = [
        "claude/skills/aa/SKILL.md\t/home/ada/workspace/devrc/x.py",
        "claude/skills/bb/SKILL.md\t/srv/eve/workspace/devrc/y.py   #",
        "claude/skills/cc/SKILL.md\t/home/ida/.kube/homelab-nebula.yaml #    ",
    ]
    good = [
        "claude/skills/dd/SKILL.md\t/home/kay/workspace/devrc/z.py  # recorded wire payload",
        "claude/skills/ee/SKILL.md\t/opt/lee/workspace/civit/civitai # per-host probe, handle absent",
    ]
    assert _unexplained(bad) == bad, (
        "the reason check did not fire on lines that carry no reason -- every "
        "zero it reports on the real file means nothing."
    )
    assert _unexplained(good) == []
    assert _unexplained(bad + good) == bad


def test_no_stale_ignore_entries():
    """An entry that no longer names a live violation must be DELETED.

    Without this the list is a ratchet with no pawl: the doc gets fixed, the line
    stays, and the stale entry silently re-licenses the same spelling when
    someone reintroduces it."""
    live = {(d, lit) for d, _, lit, _ in _violations(_corpus_text())}
    ignored, _ = _read_ignore()
    stale = sorted(ignored - live)
    assert not stale, (
        f"{len(stale)} ignore entry/entries no longer name a live site:\n"
        + "\n".join(f"  {d}\t{lit}" for d, lit in stale)
        + f"\n\nThe doc was fixed or moved. Delete these lines from "
        f"scripts/tests/{IGNORE_FILE.name} -- it may only shrink."
    )


# =============================================================================
# PROBES -- the gate's own regression coverage. Every case is planted into
# SYNTHETIC text and graded by the REAL `_violations`, so a mutation anywhere in
# the pipeline (the nix parser, the ordering, the boundary lookarounds, the
# per-offset claim, the remedy builder) moves a verdict here.
#
# 🔴 FIXTURE VALUES ARE PAIRWISE DISTINCT AND DISTINCT FROM EVERY CONSTANT THE
# ASSERTIONS NAME: the planted users are `alice`, `bob`, `root` and `zach`, and
# the tails differ per case. A fixture that could only ever produce the table's
# own value cannot see a mutant that hardcodes that value.
# =============================================================================

PROBE_DOC = "claude/skills/prune-memory/SKILL.md"


def test_probe_doc_is_a_real_corpus_doc():
    """The probes feed synthetic text at PROBE_DOC, so a PROBE_DOC that stopped
    existing would keep them all green while describing nothing in the corpus."""
    assert PROBE_DOC in _corpus(), f"{PROBE_DOC} is not a tracked corpus doc"


def _found(text: str, doc: str = PROBE_DOC):
    return [(lit, h) for _, _, lit, h in _violations({doc: text})]


@pytest.mark.parametrize(
    "text,literal,handle",
    [
        # The exact shape that motivated this module.
        ("Run `python3 /home/zach/workspace/devrc/scripts/memory-audit.py` first.",
         "/home/zach/workspace/devrc/scripts/memory-audit.py", "DEVRC"),
        # 🔴 ANOTHER USER. The whole point of the `${home}` pattern: the gate may
        # not be a fact about whose laptop it runs on.
        ("`cd /home/alice/workspace/homelab-talos && git fetch`",
         "/home/alice/workspace/homelab-talos", "HOMELAB"),
        # 🔴 AND A NON-`/home` ROOT -- a container or a root shell.
        ("`/root/workspace/civit/datapacket-talos/prod-kubeconfig`",
         "/root/workspace/civit/datapacket-talos/prod-kubeconfig", "KC_DPPROD"),
        # A deeper home, to prove the prefix is multi-segment.
        ("`/var/lib/agents/bob/workspace/civit/civitai/src/app.ts`",
         "/var/lib/agents/bob/workspace/civit/civitai/src/app.ts", "CIVITAI"),
        # The bare directory, with nothing after it.
        ("`DEVRC=/home/zach/workspace/devrc`", "/home/zach/workspace/devrc", "DEVRC"),
        # Inside PYTHON, where `$VAR` would not expand -- the site this gate
        # exists to route to `os.environ[...]`.
        ('sys.path.insert(0, "/home/zach/workspace/devrc/scripts/task-spec-drafter")',
         "/home/zach/workspace/devrc/scripts/task-spec-drafter", "DEVRC"),
        # A dot-directory tail: `.kube/homelab-nebula.yaml` is a handle too.
        ("`KUBECONFIG=/home/bob/.kube/homelab-nebula.yaml kubectl get nodes`",
         "/home/bob/.kube/homelab-nebula.yaml", "KC_NEBULA"),
        # Plain prose, no backticks: still a spelled-out path in a file an agent
        # reads as instructions.
        ("State lives in /home/zach/workspace/homelab-talos/containers/x/HANDOFF.md today.",
         "/home/zach/workspace/homelab-talos/containers/x/HANDOFF.md", "HOMELAB"),
    ],
    ids=["devrc-script", "other-user", "root-home", "deep-home", "bare-dir",
         "python-literal", "dotdir-kubeconfig", "bare-prose"],
)
def test_planted_absolute_path_is_caught(text, literal, handle):
    """NEGATIVE CONTROL: the gate must go red, name the literal, AND name the
    handle. A gate that stays green on a planted violation is testing nothing."""
    assert _found(text) == [(literal, handle)], (
        f"planting {text!r} did not produce exactly [({literal!r}, {handle!r})]."
    )


@pytest.mark.parametrize(
    "text,literal,handle",
    [
        # The shape measured in the corpus: a one-shot env prefix on a command.
        ("`KUBECONFIG=~/workspace/homelab-talos/homelab-kubeconfig kubectl get pods`",
         "~/workspace/homelab-talos/homelab-kubeconfig", "KC_HOMELAB"),
        # `export`, inside a fenced block.
        ("export KUBECONFIG=~/workspace/homelab-talos/production-kubeconfig",
         "~/workspace/homelab-talos/production-kubeconfig", "KC_PROD"),
        # The dot-directory kubeconfig, which lives nowhere near a checkout.
        ("`KUBECONFIG=~/.kube/homelab-nebula.yaml kubectl -n activity get pods`",
         "~/.kube/homelab-nebula.yaml", "KC_NEBULA"),
        # A DIFFERENT repo's kubeconfig, to prove the table is not one row.
        ("KUBECONFIG=~/workspace/civit/datapacket-talos/prod-kubeconfig kubectl top nodes",
         "~/workspace/civit/datapacket-talos/prod-kubeconfig", "KC_DPPROD"),
    ],
    ids=["homelab-inline", "prod-export", "nebula-dotdir", "dpprod"],
)
def test_planted_tilde_kubeconfig_assignment_is_caught(text, literal, handle):
    """NEGATIVE CONTROL on the `~` half.

    The reported literal must be the PATH, not the `KUBECONFIG=` prefix that
    anchored it -- otherwise the printed remedy is unusable and the ignore-list
    key names a string no reader would search for."""
    assert _found(text) == [(literal, handle)], (
        f"planting {text!r} did not produce exactly [({literal!r}, {handle!r})]."
    )
    assert _remedy(literal, handle) == f"${handle}"


@pytest.mark.parametrize(
    "text",
    [
        # 🔴 THE SHELL-CONTEXT RESTRICTION, GRADED -- but these fixtures do not
        # all grade the same guard, so read the ids before quoting this block.
        # MEASURED by dropping the `KUBECONFIG=` anchor and re-running: FOUR go
        # red -- `read-tool-target`, `table-cell`, `per-host-split`, `other-var`.
        # Those are the ones naming a file a `$KC_*` handle also names, in the
        # `~` spelling, and they are what the anchor is FOR.
        #
        # The other three stay GREEN under that mutation and grade other things:
        # `the-remedy` carries no `~` at all, and `repo-handle-tilde` /
        # `sibling-file` name files NO handle covers -- those two are the ones
        # that go red if a REPO handle leaks into the kubeconfig table, which is
        # `test_the_kubeconfig_table_is_its_own_nix_section`'s ledger. "Turns
        # every one of them red" was asserted here and is false: 4 of 7.
        #
        # A Read-tool target: `$VAR` does not expand there, so `~` is CORRECT.
        "Open `~/workspace/homelab-talos/homelab-kubeconfig` to read the context list.",
        # A table cell naming the file as a fact, not running anything.
        "| Kubeconfig | `~/workspace/homelab-talos/workbench-kubeconfig` | absent |",
        # 🔴 THE PER-HOST SPLIT, where the handle is not equivalent: the laptop
        # checkout is `homelab-infra`, which NO handle names, so the doc must be
        # free to write both spellings side by side.
        "workbench -> `~/workspace/homelab-talos/workbench-kubeconfig`; "
        "laptop -> `~/workspace/homelab-infra/workbench-kubeconfig`",
        # An assignment to a DIFFERENT variable that merely ends in the letters.
        "`MY_KUBECONFIG=~/.kube/homelab-nebula.yaml`",
        # The remedy itself, which must stay satisfiable.
        "`KUBECONFIG=$KC_HOMELAB kubectl get pods -A`",
        # A `~` path under a REPO handle: the accepted spelling, deferred sweep.
        "`KUBECONFIG=~/workspace/homelab-talos/some-other-file`",
        # A kubeconfig-shaped sibling that no handle names.
        "`KUBECONFIG=~/workspace/homelab-talos/homelab-kubeconfig-old`",
    ],
    ids=["read-tool-target", "table-cell", "per-host-split", "other-var",
         "the-remedy", "repo-handle-tilde", "sibling-file"],
)
def test_a_tilde_kubeconfig_outside_a_shell_assignment_stays_green(text):
    assert _found(text) == [], (
        f"planting {text!r} turned the gate red. The `~` half is armed ONLY for "
        "a `KUBECONFIG=` assignment; anything wider makes Read-tool pointers and "
        "the per-host `homelab-infra` table unsatisfiable."
    )


def test_the_longest_handle_wins():
    """🔴 `$KC_WORKBENCH` sits UNDER `$HOMELAB`, and the more specific handle is
    the better remedy -- it is guarded on the FILE existing, not merely the
    directory. Graded on BOTH halves (the handle named AND the remedy string), so
    a mutant that reverses the ordering cannot pass by still reporting *a*
    finding."""
    text = "`KUBECONFIG=/home/zach/workspace/homelab-talos/workbench-kubeconfig kubectl get pods`"
    found = _found(text)
    assert found == [
        ("/home/zach/workspace/homelab-talos/workbench-kubeconfig", "KC_WORKBENCH")
    ], f"expected the most specific handle, got {found}"
    assert _remedy(*found[0]) == "$KC_WORKBENCH"


def test_a_sibling_directory_is_not_a_match():
    """BOUNDARY, NOT SUBSTRING -- and the arms below grade DIFFERENT mechanisms.

    🔴 THE `civitai-cli` ARM DOES NOT GRADE THE BOUNDARY, and this docstring said
    it did. `workspace/civit/civitai` is a character prefix of
    `workspace/civit/civitai-cli`, but both patterns start at the same offset and
    the table is longest-suffix-first, so `$CIVITAI_CLI` claims it with or
    without `_RIGHT_BOUND` -- measured by deleting the boundary and re-running
    this input. It is kept because asserting that the MORE SPECIFIC handle is the
    one reported is worth an assertion; the mechanism it grades is LONGEST MATCH
    WINS, not this one.

    🔴 NOR DOES THE `homelab-trunk` ARM -- checked, because the obvious pairing
    is wrong twice over. No handle suffix is a character PREFIX of
    `workspace/homelab-trunk` (`homelab-talos` diverges at the fourth letter),
    so it is green with `_RIGHT_BOUND` and green without it. It grades a weaker
    and still worthwhile claim: a path under home that no handle covers is not
    flagged.

    `devrc-scratch` is the ONLY arm here that grades `_RIGHT_BOUND`, and it does
    so squarely: `workspace/devrc` IS a character prefix of it, so with the
    boundary deleted `$DEVRC` claims it and the gate prints a remedy naming the
    wrong tree -- measured."""
    assert _found("`/home/zach/workspace/civit/civitai-cli/main.go`") == [
        ("/home/zach/workspace/civit/civitai-cli/main.go", "CIVITAI_CLI")
    ]
    # ...and a genuine sibling with no handle at all is not matched by the
    # shorter one it merely resembles.
    assert _found("`/home/zach/workspace/devrc-scratch/notes.md`") == []
    assert _found("`/home/zach/workspace/homelab-trunk/containers/x`") == []


@pytest.mark.parametrize(
    "text",
    [
        # THE REMEDIES. Each must stay green or the gate is unsatisfiable, which
        # is the state `claude/RULES.md` calls worse than no gate at all.
        "`python3 $DEVRC/scripts/memory-audit.py`",
        "`KUBECONFIG=$KC_WORKBENCH kubectl get pods -A`",
        '`sys.path.insert(0, os.environ["DEVRC"] + "/scripts/x")`',
        "`~/.claude/skills/clawgate/reference/internals.md`",
        "`~/workspace/homelab-talos/containers/clawgate/HANDOFF.md`",
        "`WT=$(dirname $HOMELAB)/homelab-trunk`",
        # A RELATIVE path that happens to end in a handle suffix: not absolute,
        # so not a spelled-out checkout path.
        "`../workspace/devrc/scripts/x.py`",
        "`./workspace/devrc/scripts/x.py`",
        # A URL whose path resembles a handle suffix. The left boundary is what
        # keeps this out; without it every doc link would be a finding.
        "https://example.dev/home/zach/workspace/devrc/scripts/x.py",
        # Prose merely NAMING a handle.
        "The `$DEVRC` handle is exported by nix/agent-handles.nix.",
        # A path under home that no handle covers.
        "`/home/zach/.config/opencode/plugin/env.js`",
        "`/home/zach/workspace/civit/civitai-orchestration/README.md`",
        # An absolute path that is not under a home at all.
        "`/nix/store/abc-devrc/bin/x`",
        "`/etc/nixos/configuration.nix`",
    ],
    ids=lambda v: v[:52],
)
def test_a_correct_spelling_stays_green(text):
    assert _found(text) == [], f"planting {text!r} turned the gate red on input it must not flag."


def test_an_ignore_entry_licenses_only_the_doc_it_names():
    """🔴 The key is `(doc, literal)`, not the literal alone.

    Otherwise one justified exemption -- a sample API payload showing a real
    `cwd` -- silently exempts the identical string in every doc that copies it,
    which is precisely how a spelled-out path spreads through a skill corpus.
    Both arms graded: the named doc keeps its licence, any other doc does not."""
    ignored, _ = _read_ignore()
    assert ignored, (
        "the ignore list is empty, so this probe is vacuous. If the list is "
        "legitimately empty, keep the probe but plant a synthetic pair instead."
    )
    doc_a, literal = sorted(ignored)[0]
    assert doc_a != PROBE_DOC

    licensed = _filter(_violations({doc_a: literal}), ignored)
    assert licensed == [], (
        f"{doc_a}\t{literal} is on the ignore list but was still reported in its "
        "own doc -- the hatch does not work."
    )
    elsewhere = _filter(_violations({PROBE_DOC: literal}), ignored)
    assert len(elsewhere) == 1 and elsewhere[0][0] == PROBE_DOC, (
        f"an ignore entry for {doc_a} licensed the same literal cited from "
        f"{PROBE_DOC}. The key must be (doc, literal), not literal."
    )


def test_the_remedy_names_the_handle_and_keeps_the_tail():
    """The failure line IS the fix, so the tail must survive verbatim. A remedy
    that dropped it would read as a correct instruction and send the reader to
    the wrong file."""
    assert _remedy("/home/zach/workspace/devrc/scripts/lib/handoff_doc.py", "DEVRC") == (
        "$DEVRC/scripts/lib/handoff_doc.py"
    )
    assert _remedy("/root/workspace/devrc", "DEVRC") == "$DEVRC"
    assert _remedy("/home/alice/.kube/homelab-nebula.yaml", "KC_NEBULA") == "$KC_NEBULA"
    # 🔴 The repeated-suffix case, where `rfind` would delete the middle of the
    # path and still print something that reads like a correct instruction.
    assert _remedy(
        "/home/ada/workspace/devrc/t/workspace/devrc/q.py", "DEVRC"
    ) == "$DEVRC/t/workspace/devrc/q.py"


def test_a_missing_ignore_file_raises_LOUDLY_rather_than_reading_as_empty(monkeypatch,
                                                                          tmp_path):
    """POSITIVE CONTROL on the missing-file guard.

    The file exists in every real checkout, so that branch never executes on a
    healthy tree -- indistinguishable from a guard wired to nothing. 🔴 Pin the
    TYPE and the MESSAGE: deleting the guard still raises, just a different
    exception meaning "the run crashed" instead of "the verdict is meaningless"."""
    monkeypatch.setattr(
        "test_absolute_handle_paths.IGNORE_FILE", tmp_path / "absent.list"
    )
    with pytest.raises(AssertionError, match=r"is missing\. This gate's verdict"):
        _read_ignore()
