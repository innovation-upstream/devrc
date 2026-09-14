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
    """The `repos` half: every parsed entry that is not a kubeconfig.

    Derived rather than parsed again. The two nix blocks are disjoint by handle
    name (asserted in `test_the_nix_source_parses_to_a_usable_table`), so the
    subtraction is exact.
    """
    kc = {name for name, _ in _kubeconfig_table()}
    return [(name, rel) for name, rel in _handle_table() if name not in kc]


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

    # The two blocks must stay disjoint, or `_repos()`'s subtraction silently
    # drops a repo handle that happens to share a name with a kubeconfig one.
    assert not (repo_names & kc_names), (
        f"{sorted(repo_names & kc_names)} appears in BOTH the `repos` and "
        f"`kubeconfigs` blocks of {HANDLES_NIX}. `_repos()` derives the repo half "
        f"by subtracting the kubeconfig names, so an overlap would silently hide "
        f"a repo handle from this gate."
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
        f"shell does not export expands to EMPTY, and `KUBECONFIG= kubectl …` "
        f"falls back to the DEFAULT context — i.e. the nudge would route a "
        f"command at whatever cluster is default rather than the named one. "
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

    This is a live hazard rather than a hypothetical — `homelab-kubeconfig`,
    `workbench-kubeconfig`, `prod-kubeconfig` and `production-kubeconfig` are
    four similar names across two repos, and nothing stops a fifth repeating one.
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


def test_the_hook_file_is_where_this_module_thinks_it_is():
    """Ledger on the path this module resolves, derived from `HANDLES_NIX`.

    Every assertion here imports the hook by path. If the hook MOVED, the import
    would raise rather than pass, so this is not about a false green — it is so
    the failure names the relocation instead of an opaque loader error.
    """
    assert HOOK.is_file(), (
        f"{HOOK} does not exist. shell-env-nudge.py moved or was removed; this "
        f"ledger, and the handle-table guarantee it provides, moved with it."
    )
