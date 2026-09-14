"""LEDGER: `shell-env-nudge.py`'s handle tables against `nix/agent-handles.nix`.

THE DEFECT THIS CLOSES (measured at `06287019`)
-----------------------------------------------
`scripts/claude-hooks/shell-env-nudge.py` hand-maintains two dicts — `REPO_VARS`
and `KC_VARS` — that restate `nix/agent-handles.nix`, the single source of truth
every other consumer parses. Nothing read them: `grep -rn 'KC_VARS\\|REPO_VARS'`
over the whole tree returned the hook and nothing else, so the copy had no ledger
of any kind and drifted in silence.

It HAD drifted. `KC_VARS` carried 4 of the 5 kubeconfig handles — **`KC_PROD` was
missing** — because the hook landed in one commit (`15233e3c`, #50, 2026-07-02)
and was never touched again, while `KC_PROD` entered `agent-handles.nix` a month
later (`e21985a0`, #276, 2026-08-02) as part of the opencode consolidation. The
gap therefore ran 2026-08-02 → 2026-09-14, about six weeks. Nobody reconciled the
two, and no gate could say so.

⚠ `REPO_VARS` was NOT drifted, and the difference is worth recording because a
sibling table being fine is not evidence this one was. The rank-26 brief this
closes also called `handoff_index.REPO_ENV_HANDLES` drifted for omitting
`CIVITAI_CLI`; it is not — that omission is a deliberate exclusion already pinned
BOTH WAYS, with its reason in source, by
`test_handoff_index.py::TestTheUnitEnvironmentMatchesTheHandlesTheIndexerReads`.
Three hand-maintained tables, two already pinned, one not: this file is the one.

WHY THE FAILURE IS QUIET, WHICH IS WHY IT NEEDS A GATE
------------------------------------------------------
The hook's whole job is to notice an agent re-typing a literal path and answer
with the handle. A handle ABSENT from these tables is not an error — it is a
`.get()` that returns `None`, one `if var:` that does not fire, and a nudge that
is simply never emitted. The hook keeps working, reports nothing, and the operator
sees a slightly less helpful assistant rather than a bug. There is no symptom to
notice and no log line to grep, so the only thing that can catch it is an
assertion that the table IS the source.

WHAT EACH TEST HERE IS (labelled, because "it passes" is not a category)
------------------------------------------------------------------------
  * test_the_nix_source_parses_to_a_usable_table
        POSITIVE CONTROL for every assertion below. Each one compares the hook's
        dict against a set parsed out of the nix file; a parser that returned
        `{}` would make all of them pass against ANY hook table, including an
        empty one. This requires the parse to be non-empty, to hold both blocks,
        and to name handles by value rather than by count — a count survives a
        rename.
  * test_the_repo_table_is_exactly_the_nix_repos_block
        REGRESSION + LEDGER. Both directions.
  * test_the_kubeconfig_table_is_exactly_the_nix_kubeconfigs_block
        REGRESSION. This is the one that is RED at `06287019` on `KC_PROD`.
  * test_no_kubeconfig_basename_shadows_another
        REGRESSION-ADJACENT, and it guards a hazard THIS change enlarges.
        `KC_BASENAMES` is `{basename(path): handle}`, so two kubeconfigs sharing
        a file name collapse into one entry and the loser becomes unreachable
        through the relative-path arm — silently, last-writer-wins. Adding a
        fifth kubeconfig (`production-kubeconfig`) makes a future collision more
        likely, not less.

🔴 BOTH DIRECTIONS, DELIBERATELY. A one-way check ("every hook entry is real")
stays green forever against a table that is missing half the handles — which is
exactly the state this file was written to end. `claude/RULES.md`: a guard's
DESCRIPTION claims coverage, so the body has to be as wide as the sentence.

🔴 WHAT THIS PINS IS THE DECLARATION, AND A DECLARATION IS NOT AN EXPORT — SO
THIS LEDGER CANNOT SEE, AND CURRENTLY BLESSES, A HANDLE THE SHELL NEVER SETS.
`agent-handles.nix` DECLARES handles; both consumers then EXISTENCE-GUARD before
exporting (`nix/programs/zsh/default.nix`, `exportIf "-d"` / `exportIf "-f"`), so
a handle whose directory or file is absent on a host is declared and NOT
exported. The hook's job is to name a handle the shell actually has, and pinning
it to the declaration mandates the opposite.

MEASURED on this host, and it is not hypothetical: `~/.kube/homelab-nebula.yaml`
does not exist, `$KC_NEBULA` is UNSET — and the hook nudges `KUBECONFIG=$KC_NEBULA`
anyway, which is precisely the failure
`test_the_kubeconfig_table_is_exactly_the_nix_kubeconfigs_block` describes in its
own `extra`-arm message ("expands to EMPTY"). ⚠ That message used to continue
"… falls back to the DEFAULT context"; it does not, unconditionally — see the
retraction in `test_absolute_handle_paths.py` and the corrected wording there.
That is PRE-EXISTING — `KC_NEBULA` has been in `KC_VARS` since the hook landed —
but this ledger now makes its presence MANDATORY in both directions, so it is
recorded here rather than left to be rediscovered.

**The better design, named so it is not re-derived:** keep a hand-maintained list
of handle NAMES and take the PATHS from `os.environ` —
`{p: n for n in REPO_NAMES if (p := os.environ.get(n))}` — which is free at
import, needs no file at runtime, is correct per-host by construction, and
STRUCTURALLY cannot nudge an unexported handle. Deriving from the nix file at
import is NOT the fix: the deployed hook is a `home.file` store copy, so reaching
that file needs `$DEVRC` — the very mechanism it is trying to teach.
**Closes when** the hook resolves paths from the environment and a test shows it
emitting NO suggestion for a declared-but-unexported handle, RED before and GREEN
after. Until then this ledger is the weaker invariant, knowingly.

🔴 THE TABLE IS PARSED, NEVER RESTATED — and the parser is IMPORTED, not
re-written. `test_absolute_handle_paths` already parses `agent-handles.nix` for
its own gate. Writing a second regex here would make THREE copies of the thing
whose duplication is the defect under test (`claude/RULES.md`: one rule, one
place — a predicate open-coded at N sites is wrong at N−1 of them). Importing it
also means this module inherits that module's own ledger assertions about the
parse.

⚠ THIS FILE NEVER WRITES TO `$HOME`. It imports the hook module, which computes
`HOME = os.path.expanduser("~")` and a `CACHE_DIR` string at import — no IO, no
`makedirs`, no glob, no delete. `main()` is under `if __name__ == "__main__"` and
is never called here. The suite that DOES drive the hook is
`scripts/claude-hooks/tests/test_shell_env_nudge.py`, which redirects `$HOME`
first; `test_hook_suites_do_not_touch_the_inherited_home.py` is what polices that.
Expectations here are built from the hook's OWN `mod.HOME`, so the verdict does
not depend on which user or host runs it.
"""
import importlib.util
import os

# The nix parser, imported rather than re-implemented — see the module docstring.
# `_handle_table()` returns EVERY `NAME = "${home}/…";` entry in the file (both
# blocks); `_kubeconfig_table()` returns the `kubeconfigs = { … };` block alone.
# The repos half is therefore a DERIVATION of the two, which is what keeps this
# module from adding a third regex over the same file.
from test_absolute_handle_paths import (  # noqa: E402
    HANDLES_NIX,
    _handle_table,
    _kubeconfig_table,
    nix_block,
)

HOOK = HANDLES_NIX.parent.parent / "scripts/claude-hooks/shell-env-nudge.py"


def _hook():
    """The hook module, imported by path (its file name is not an identifier)."""
    spec = importlib.util.spec_from_file_location("shell_env_nudge", HOOK)
    assert spec and spec.loader, f"could not load {HOOK}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _expected(entries, home):
    """`{absolute path: HANDLE}` — the nix table rendered against `home`.

    The hook stores absolute paths because it matches literal command text, so
    the comparison has to be against paths and not merely against handle names:
    a table whose names are all correct and whose PATHS have drifted matches no
    command and nudges nobody, which is the same silent nothing.
    """
    return {f"{home}/{rel}": name for name, rel in entries}


def _repos():
    """The `repos` block of `agent-handles.nix`, LONGEST SUFFIX FIRST.

    🔴 PARSED VIA THE IMPORTED `nix_block`, NOT A LOCAL REGEX — and it took two
    tries to get that right. This began as "the whole-file parse MINUS the
    kubeconfig names", justified as exact because the two blocks are disjoint.
    Disjointness gives `repos ∩ kubeconfigs = ∅`; exactness ALSO needs
    `_handle_table() ⊆ repos ∪ kubeconfigs`, which nothing asserted. Measured:
    a third block — `caches = { CACHE_X = "${home}/.cache/agent-x"; };` — was
    swept into the repo half, so
    `test_the_repo_table_is_exactly_the_nix_repos_block` failed telling the
    reader to ADD a never-exported, non-repo handle to `REPO_VARS`. Loud, but an
    actively wrong remedy.

    The first fix replaced the subtraction with a LOCAL block regex, which made
    this module's own docstring false ("the parser is IMPORTED, not re-written")
    and put a THIRD copy of the predicate over one file — copies that disagreed:
    on an inline `repos = { A = "..."; };` the local one over-captured into the
    next block while `test_repo_path_guard`'s `split("repos = {")` copy silently
    dropped an entry. `nix_block` is that predicate, parameterised where it
    already lived.
    """
    return nix_block(
        "repos",
        "Every repo assertion in this module compares against that block; "
        "without it they compare against an empty set and pass vacuously.",
    )


def test_the_nix_source_parses_to_a_usable_table():
    """POSITIVE CONTROL: prove the instrument can see something before reading it.

    Every other assertion in this file is `hook_dict == parsed_nix`. If the parse
    silently yielded nothing — a renamed block, a changed quoting style, a moved
    file — those comparisons would be `{} == {}` for the repos half and would
    pass against a hook table in ANY state, including the drifted one this module
    exists to catch. A reassuring zero is indistinguishable from a probe wired to
    nothing, so the zero is refused here first.

    Named handles, not just a count: a count survives a rename, and a rename is
    precisely the drift that would make the hook's table stale again.
    """
    kubeconfigs = _kubeconfig_table()
    repos = _repos()

    assert repos, (
        f"no repo handles parsed out of {HANDLES_NIX}. Every assertion in this "
        f"module compares a hook dict against this parse, so an empty one makes "
        f"them all vacuous — they would pass against ANY table."
    )
    assert kubeconfigs, (
        f"no kubeconfig handles parsed out of the `kubeconfigs` block of "
        f"{HANDLES_NIX}. Same vacuity as above, for the KC_VARS half."
    )

    repo_names = {name for name, _ in repos}
    kc_names = {name for name, _ in kubeconfigs}

    # 🔴 THE DUPLICATE CHECK MUST READ THE RAW PARSE, NOT THE SUBTRACTED SET.
    # An earlier draft asserted `not (repo_names & kc_names)` and was VACUOUS BY
    # CONSTRUCTION: `_repos()` builds its half by SUBTRACTING the kubeconfig
    # names, so that intersection is empty by set-difference identity whatever
    # the nix file says. Measured on a planted tree — `SHARED` added to BOTH nix
    # blocks with the hook carrying only the kubeconfig one — the module reported
    # **5 passed** while `agent-handles.nix` declared a repo handle `REPO_VARS`
    # did not carry, i.e. exactly the silent half
    # `test_the_repo_table_is_exactly_the_nix_repos_block` says is undetectable
    # from the hook's behaviour. `claude/RULES.md`: a guard's DESCRIPTION claims
    # coverage, so check the body is as wide as the sentence.
    #
    # `_handle_table()` is the whole-file parse, so a name declared twice comes
    # back as TWO entries — which is the observation the subtracted set has
    # already destroyed. Same planted tree, this assertion: RED.
    names = [name for name, _ in _handle_table()]
    dupes = sorted({n for n in names if names.count(n) > 1})
    assert not dupes, (
        f"{dupes} is declared more than once in {HANDLES_NIX} — in both the "
        f"`repos` and `kubeconfigs` blocks, or twice in one. The two blocks are "
        f"pinned to two different consumer dicts, so a name in both makes this "
        f"module's verdict depend on which block it reads first."
    )

    # 🔴 THE TWO BLOCKS MUST PARTITION THE WHOLE-FILE PARSE. `_handle_table()`
    # matches every `NAME = "${home}/…";` in the file, wherever it sits. A THIRD
    # attrset — `caches = { … };`, say — is parsed by it and belongs to neither
    # consumer dict, so without this assertion it is simply invisible to this
    # module: not a repo handle, not a kubeconfig handle, silently unpinned.
    # This is the half that makes "disjoint" into "exhaustive", and it is the
    # one the old subtraction got wrong in the opposite direction (it swept such
    # a handle INTO the repo half and demanded `REPO_VARS` carry it).
    known = {n for n, _ in _repos()} | {n for n, _ in kubeconfigs}
    orphans = sorted(set(names) - known)
    assert not orphans, (
        f"{orphans} is declared in {HANDLES_NIX} but sits in NEITHER the `repos` "
        f"nor the `kubeconfigs` block. This module pins those two blocks to the "
        f"hook's two dicts; a handle outside both is exported by nix and checked "
        f"by nothing here. Put it in one of the two blocks, or extend this module "
        f"to cover the new one — do NOT add it to a consumer dict just to go "
        f"green, which is what the previous derivation would have told you to do."
    )

    # Anchors by NAME. These two are the handles this repo's own tooling is
    # written against; if either stops parsing, the parse has broken in a way a
    # non-empty check cannot see.
    assert "DEVRC" in repo_names, (
        f"`DEVRC` did not parse out of the `repos` block of {HANDLES_NIX} "
        f"(parsed: {sorted(repo_names)})."
    )
    assert "KC_PROD" in kc_names, (
        f"`KC_PROD` did not parse out of the `kubeconfigs` block of "
        f"{HANDLES_NIX} (parsed: {sorted(kc_names)})."
    )


def test_the_repo_table_is_exactly_the_nix_repos_block():
    """`REPO_VARS` IS the `repos` block — both directions.

    An entry the nix file does not declare is a handle the hook will suggest and
    the shell never exported: the agent is told to use `$FOO`, `$FOO` is empty,
    and the resulting command runs against `/` or against the cwd. An entry the
    nix file declares and the hook lacks is the silent half — no nudge, ever.
    Neither direction is detectable from the hook's own behaviour.
    """
    mod = _hook()
    want = _expected(_repos(), mod.HOME)
    got = dict(mod.REPO_VARS)

    missing = {p: n for p, n in want.items() if p not in got}
    extra = {p: n for p, n in got.items() if p not in want}

    assert not missing, (
        f"nix/agent-handles.nix declares repo handle(s) that "
        f"scripts/claude-hooks/shell-env-nudge.py's REPO_VARS does not carry: "
        f"{sorted(missing.values())}. The hook will never nudge for them — it "
        f"fails by staying SILENT, so nothing else can report this. Add "
        f"{sorted((n, p) for p, n in missing.items())} to REPO_VARS."
    )
    assert not extra, (
        f"REPO_VARS carries repo handle(s) nix/agent-handles.nix does not "
        f"declare: {sorted(extra.values())}. The hook would advise an agent to "
        f"use a handle the shell never exports, which expands to an EMPTY string "
        f"— `git -C $GONE …` runs against the cwd. Remove them, or declare them "
        f"in nix/agent-handles.nix."
    )
    # Belt and braces: the two dicts must be equal as MAPPINGS, not merely have
    # the same keys — a key present under the wrong handle name matches no
    # command correctly and is caught by neither set difference above.
    assert got == want


def test_the_kubeconfig_table_is_exactly_the_nix_kubeconfigs_block():
    """`KC_VARS` IS the `kubeconfigs` block — both directions.

    🔴 RED at `06287019`: `KC_PROD` is declared in nix and absent from `KC_VARS`,
    so `KUBECONFIG=<…>/production-kubeconfig` — a real, existing kubeconfig — was
    never recognised and never nudged.
    """
    mod = _hook()
    want = _expected(_kubeconfig_table(), mod.HOME)
    got = dict(mod.KC_VARS)

    missing = {p: n for p, n in want.items() if p not in got}
    extra = {p: n for p, n in got.items() if p not in want}

    assert not missing, (
        f"nix/agent-handles.nix declares kubeconfig handle(s) that "
        f"scripts/claude-hooks/shell-env-nudge.py's KC_VARS does not carry: "
        f"{sorted(missing.values())}. Both arms of the kubeconfig nudge are "
        f"built from KC_VARS — the absolute-path lookup AND the KC_BASENAMES "
        f"relative-path one — so the handle is unreachable by either. Add "
        f"{sorted((n, p) for p, n in missing.items())} to KC_VARS."
    )
    assert not extra, (
        f"KC_VARS carries kubeconfig handle(s) nix/agent-handles.nix does not "
        f"declare: {sorted(extra.values())}. 🔴 A kubeconfig handle that the "
        f"shell does not export expands to EMPTY. (`KUBECONFIG= kubectl` reaches "
        f"the default context only where the default kubeconfig carries a "
        f"non-empty `current-context`; that precondition is RETRACTED as "
        f"unconditional in test_absolute_handle_paths.py — do not restate it.) "
        f"Remove them, or declare them in nix/agent-handles.nix."
    )
    assert got == want


def test_no_kubeconfig_basename_shadows_another():
    """`KC_BASENAMES` is keyed on file name, so a duplicate silently loses.

    `KC_BASENAMES = {os.path.basename(p): v for p, v in KC_VARS.items()}` — a
    dict comprehension, so two kubeconfigs sharing a basename collapse to ONE
    entry and the later one wins. The relative-path arm (`KUBECONFIG=./x`, the
    spelling datapacket's own CLAUDE.md uses) would then resolve to the wrong
    handle and name the wrong CLUSTER, which is the worst outcome this hook can
    produce: not a missing hint, an actively misleading one.

    ⚠ THIS IS A FORWARD GUARD, NOT A LIVE DEFECT — an earlier draft of this
    docstring said "a live hazard rather than a hypothetical" and that was FALSE.
    Measured: the five basenames (`homelab-kubeconfig`, `workbench-kubeconfig`,
    `production-kubeconfig`, `prod-kubeconfig`, `homelab-nebula.yaml`) are
    pairwise DISTINCT, and were before this change too. What is true is the
    likelihood argument — four similar names across two repos, and nothing stops
    a fifth repeating one — and likelihood is not liveness. The distinction
    matters because this sentence is the guard's whole justification and is the
    decision input for anyone later asking whether to keep it.
    """
    mod = _hook()
    seen = {}
    for path, handle in mod.KC_VARS.items():
        seen.setdefault(os.path.basename(path), []).append((handle, path))

    clashes = {base: v for base, v in seen.items() if len(v) > 1}
    assert not clashes, (
        f"two kubeconfig handles share a file name, so KC_BASENAMES keeps only "
        f"the last and the other is unreachable through the relative-path arm: "
        f"{clashes}. A `KUBECONFIG=./<name>` command would be nudged toward the "
        f"WRONG cluster. Rename one of the files, or drop the relative-path arm "
        f"for the ambiguous basename."
    )
    assert len(mod.KC_BASENAMES) == len(mod.KC_VARS), (
        f"KC_BASENAMES has {len(mod.KC_BASENAMES)} entries against KC_VARS' "
        f"{len(mod.KC_VARS)} — entries were lost building it, which is the "
        f"collision above by another route."
    )


def test_an_absolute_path_is_not_matched_by_BASENAME_alone():
    """REGRESSION. The basename fallback must fire for RELATIVE refs only.

    `KC_BASENAMES` is `{basename(path): handle}` and the lookup site used to
    consult it for ANY path whose exact match missed — absolute paths included.
    An absolute path that merely ENDS in a known kubeconfig name is a DIFFERENT
    FILE, so the nudge named the wrong cluster. Because `$KC_*` is
    existence-guarded in nix, on a host where that handle is unset the suggestion
    expands to EMPTY.

    ⚠ DO NOT say the empty case "silently falls back to the default context".
    `test_absolute_handle_paths.py` RETRACTED that sentence and forbids repeating
    it without its precondition: the silent arm needs the default kubeconfig to
    carry a NON-EMPTY `current-context`, and on this host `~/.kube/config` has
    `current-context: ""`, so BOTH arms return `error: current-context is not
    set`, rc 1 — loud and character-identical (re-measured). The worse case is
    the one that does not need a precondition: where the wrongly-named handle IS
    exported, the command runs against the WRONG CLUSTER with no error at all.

    🔴 MEASURED BEFORE THE FIX, and the realistic case is the damaging one:
    `claude/skills/auditloop/SKILL.md` documents the laptop's kubeconfigs at
    `~/workspace/homelab-infra/{workbench,homelab,production}-kubeconfig`, while
    `$KC_PROD` is guarded on the `homelab-talos` spelling. So an absolute
    `.../homelab-infra/production-kubeconfig` was nudged as `$KC_PROD`.
    Pre-existing for `KC_HOMELAB`/`KC_WORKBENCH`; adding `KC_PROD` completed it
    to three of three, which is why it is fixed here rather than filed.

    The positive control is in the same test on purpose: a bare zero from the
    negative cases is indistinguishable from `analyze()` wired to nothing.
    """
    mod = _hook()
    home = mod.HOME

    # POSITIVE CONTROL — the relative spelling the fallback exists for STILL fires.
    assert [v for v, _ in mod.analyze("KUBECONFIG=./prod-kubeconfig kubectl get pods")] == [
        "KC_DPPROD"
    ], "the relative-reference fallback stopped working — this fix went too far"

    # And an EXACT absolute path still resolves, via KC_VARS rather than basename.
    exact = f"{home}/workspace/homelab-talos/production-kubeconfig"
    assert [v for v, _ in mod.analyze(f"KUBECONFIG={exact} kubectl get ns")] == ["KC_PROD"]

    # NEGATIVE — absolute paths that only share a basename must NOT be claimed.
    for bad in (
        "/tmp/production-kubeconfig",
        f"{home}/workspace/homelab-infra/production-kubeconfig",
        f"{home}/workspace/homelab-infra/homelab-kubeconfig",
        "/tmp/prod-kubeconfig",
    ):
        got = [v for v, _ in mod.analyze(f"KUBECONFIG={bad} kubectl get pods")]
        assert got == [], (
            f"{bad!r} shares a basename with a known kubeconfig but is a "
            f"DIFFERENT FILE, and was nudged as {got}. Where that handle IS "
            f"exported the command runs against the WRONG CLUSTER; where it "
            f"declines to export the suggestion expands to empty. (Do not write "
            f"that the empty case silently takes the default context — "
            f"test_absolute_handle_paths.py retracted that without its "
            f"precondition; here both arms are loud.)"
        )


# ⚠ DELETED, on measurement: `test_the_hook_file_is_where_this_module_thinks_it_is`.
# It asserted `HOOK.is_file()` and justified itself as making a relocation legible
# "instead of an opaque loader error". Measured by moving the hook aside and
# running this module: the loader already raises
# `FileNotFoundError: [Errno 2] No such file or directory: '<...>/scripts/claude-hooks/shell-env-nudge.py'`
# — it names the exact path. The test added one sentence of prose and a fourth red
# line, and by its own docstring was never guarding against a false green.
